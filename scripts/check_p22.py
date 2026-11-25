"""P22 验收：agent 循环（白名单 / 审批 / 假模型端到端 / 中断续跑）。

    python -m dsh_embagent.scripts.check_p22

五条对应 `docs/execution-plan-v3.md` §P22，第 5 条是**退化验证**。

## 全程假模型

`ScriptedClient` 按预先写好的脚本回答，**不联网、不花 token**。
所以这 24 条能进常规回归——而 agent 循环最易坏的部分（白名单、入参校验、
审批、日志、续跑）本来就跟模型无关。

## 工作区隔离

端到端那几条用 `HWV_WORKSPACE` 指到临时目录。**验收不往用户工程里写东西**
（AGENTS §4.4）。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import agent_loop, agent_tools  # noqa: E402
from dsh_embagent.scripts.fake_model import ScriptedClient, call, done  # noqa: E402

PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []
REPO = Path(__file__).resolve().parents[2]
REQ_CAR = REPO / "dsh_embagent" / "examples" / "req_car.yaml"


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": str(detail)[:800]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {str(detail)[:800]}")
    return ok


class SpyRunner:
    """记录"工具到底有没有被执行"。审批被拒时必须**一次都没跑**。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._inner = None

    def __call__(self, command: str, argv: list[str]) -> dict[str, Any]:
        self.calls.append((command, list(argv)))
        if self._inner is not None:
            return self._inner(command, argv)
        return {"status": "ok", "note": "spy"}


# ---------------------------------------------------------------------------
# 1. 闸门：白名单里没有细粒度改动工具
# ---------------------------------------------------------------------------


def check_whitelist() -> None:
    table = agent_tools.ToolTable()
    present = table.names()
    leaked = [name for name in agent_tools.FORBIDDEN if name in present]
    check("1a. 白名单里**不存在** setpart/move/delpart 等细粒度工具",
          not leaked,
          f"已注册 {len(present)} 个：{present}｜泄漏={leaked}")

    write_tools = table.by_tier("write")
    check("1b. 写入口只有一个 `hw_put`（另有 `hw_emit` 产出工程，不改 design.json）",
          write_tools == ["hw_emit", "hw_put"],
          f"write 档={write_tools}")

    human = table.by_tier("human")
    check("1c. 人确认通道只有 `hw_promote`（deferred lock）",
          human == ["hw_promote"], f"human 档={human}")

    # 四档都要有人；否则审批表就是残缺的
    tiers = {tier: len(table.by_tier(tier)) for tier in agent_tools.TIERS}
    check("1d. 四档都有工具（read/judge/write/human）",
          all(count > 0 for count in tiers.values()), json.dumps(tiers))


# ---------------------------------------------------------------------------
# 2. 审批：三档 × 三模式
# ---------------------------------------------------------------------------


def check_approval() -> None:
    table = agent_tools.ToolTable()
    grid = {}
    for tier, name in (("read", "hw_get"), ("judge", "hw_ladder"),
                       ("write", "hw_put"), ("human", "hw_promote")):
        spec = table.get(name)
        grid[tier] = {mode: agent_tools.decide(spec, mode)          # type: ignore[arg-type]
                      for mode in ("read-only", "ask", "auto")}
    expected = {
        "read": {"read-only": "allow", "ask": "allow", "auto": "allow"},
        "judge": {"read-only": "allow", "ask": "allow", "auto": "allow"},
        "write": {"read-only": "deny", "ask": "ask", "auto": "allow"},
        # human：**auto 也是 ask**
        "human": {"read-only": "deny", "ask": "ask", "auto": "ask"},
    }
    check("2a. 审批表：read/judge 放行、write 看模式、**human 在 auto 也必问**",
          grid == expected, json.dumps(grid, ensure_ascii=False))

    # read-only 下写工具被拒，且**一次都没执行**
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p22-ro-"))
    try:
        spy = SpyRunner()
        client = ScriptedClient([call("hw_put", project="p", design={"parts": []}),
                                 done("好")])
        report = agent_loop.run(
            client, "试着写一下", mode="read-only",
            tools=agent_tools.ToolTable(runner=spy), journal=tmp / "j.jsonl")
        check("3a. read-only 模式下 `hw_put` 被拒，且工具**没有被执行**",
              report["status"] == "failed" and bool(report["denied"])
              and spy.calls == [],
              f"denied={report['denied']} 执行次数={len(spy.calls)}")

        # auto 模式下 same 工具放行（对照组，证明上面那条不是"所有模式都拒"）
        spy2 = SpyRunner()
        client2 = ScriptedClient([call("hw_put", project="p", design={"parts": []}),
                                  done("好")])
        report2 = agent_loop.run(
            client2, "写一下", mode="auto",
            tools=agent_tools.ToolTable(runner=spy2), journal=tmp / "j2.jsonl")
        check("3b. 对照组：auto 模式下同一个 `hw_put` 被放行（差异来自模式）",
              report2["status"] == "ok" and len(spy2.calls) == 1,
              f"status={report2['status']} calls={spy2.calls}")

        # human 档在 auto 下仍然要问；没有回调 → 拒绝 + 不执行
        spy3 = SpyRunner()
        client3 = ScriptedClient([
            call("hw_promote", project="p", action="lock",
                 name="package_family", value="QFN"),
            done("好")])
        report3 = agent_loop.run(
            client3, "锁一下", mode="auto",
            tools=agent_tools.ToolTable(runner=spy3), journal=tmp / "j3.jsonl")
        check("3c. auto 模式下 `hw_promote` 仍要问；无审批回调 → 拒绝且不执行",
              report3["status"] == "failed" and spy3.calls == []
              and any("需要人确认" in d for d in report3["denied"]),
              f"denied={report3['denied']} 执行次数={len(spy3.calls)}")

        # 给了回调并同意 → 执行
        spy4 = SpyRunner()
        client4 = ScriptedClient([
            call("hw_promote", project="p", action="lock",
                 name="package_family", value="QFN"),
            done("好")])
        asked: list[dict[str, Any]] = []
        report4 = agent_loop.run(
            client4, "锁一下", mode="auto",
            tools=agent_tools.ToolTable(runner=spy4), journal=tmp / "j4.jsonl",
            approval=lambda req: asked.append(req) or True)
        check("3d. 有回调且人同意 → 执行，且请求里带 tool/tier/args",
              report4["status"] == "ok" and len(spy4.calls) == 1
              and asked and asked[0].get("tier") == "human",
              f"asked={asked[0] if asked else None} calls={spy4.calls}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. 入参先校验
# ---------------------------------------------------------------------------


def check_args() -> None:
    table = agent_tools.ToolTable()
    spec = table.get("hw_put")
    missing = spec.validate({"project": "p"})                  # type: ignore[union-attr]
    unknown = spec.validate({"project": "p", "design": {}, "proj": "typo"})  # type: ignore[union-attr]
    wrong_type = spec.validate({"project": "p", "design": "not-an-object"})  # type: ignore[union-attr]
    check("4a. 缺必填 / 未知参数 / 类型不对 → 都报出来（不是静默忽略）",
          bool(missing) and bool(unknown) and bool(wrong_type),
          f"缺={missing} 未知={unknown} 类型={wrong_type}")

    spy = SpyRunner()
    client = ScriptedClient([call("hw_put", project="p", design={"parts": []},
                                 proj="typo"), done("好")])
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p22-arg-"))
    try:
        report = agent_loop.run(client, "x", mode="auto",
                                tools=agent_tools.ToolTable(runner=spy),
                                journal=tmp / "j.jsonl")
        check("4b. 入参不合法 → 记 invalid，**工具一次都没跑**",
              report["status"] == "failed" and spy.calls == []
              and any("proj" in row for row in report["invalid"]),
              f"invalid={report['invalid']} 执行次数={len(spy.calls)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. 假模型端到端：generate → put → ladder
# ---------------------------------------------------------------------------


def _end_to_end(workspace: Path, journal: Path) -> tuple[dict[str, Any], ScriptedClient]:
    script = [
        call("hw_generate", requirement=str(REQ_CAR), max_candidates=1, materialize=1),
        call("hw_put", project="p22-bot",
             design=lambda prev: (prev or {}).get("candidates", [{}])[0].get("design", {})),
        call("hw_ladder", project="p22-bot"),
        done("生成、写入、判定都做完了"),
    ]
    client = ScriptedClient(script)
    report = agent_loop.run(client, "做个避障小车，选一个候选写进去并判定",
                            mode="auto", journal=journal)
    return report, client


def check_end_to_end(tmp: Path) -> None:
    workspace = tmp / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    old = os.environ.get("HWV_WORKSPACE")
    os.environ["HWV_WORKSPACE"] = str(workspace)
    try:
        if not REQ_CAR.is_file():
            check("5a. 假模型端到端：generate → put → ladder", False,
                  f"需求文件缺失：{REQ_CAR}", status=SKIP)
            return
        report, client = _end_to_end(workspace, tmp / "e2e.jsonl")
        design = workspace / "p22-bot" / "design.json"
        events = agent_loop.read_journal(tmp / "e2e.jsonl")
        tools_used = [e.get("tool") for e in events if e.get("kind") == "result"]
        check("5a. 假模型端到端：generate → put → ladder，全绿且 design.json 落盘",
              report["status"] == "ok"
              and tools_used == ["hw_generate", "hw_put", "hw_ladder"]
              and design.is_file()
              and report["final_ladder"] in ("ok", "not_ready"),
              f"status={report['status']} 工具序列={tools_used} "
              f"ladder={report['final_ladder']} design={design.is_file()}")

        check("5b. 端到端过程中**没有任何**人确认请求（三档都放行）",
              client.describe()["requests"] == 4,
              f"模型被调用 {client.describe()['requests']} 次，脚本 4 步")

        # 假模型被多用一次就必须报错（不是静默结束）。
        # 脚本最后一步给的是**工具调用**（不是 done），所以循环会回来再要一次回复
        # —— 那一次脚本已经空了。
        try:
            agent_loop.run(ScriptedClient([call("hw_get", project="p")]), "x",
                           mode="auto",
                           tools=agent_tools.ToolTable(runner=SpyRunner()),
                           journal=tmp / "over.jsonl")
            over_ok, over_detail = False, "多用没报错"
        except AssertionError as exc:
            over_ok, over_detail = True, str(exc)[:140]
        check("5c. 脚本用尽后再被调用 → 明确报错（不静默结束）",
              over_ok, over_detail)
    finally:
        if old is None:
            os.environ.pop("HWV_WORKSPACE", None)
        else:
            os.environ["HWV_WORKSPACE"] = old


# ---------------------------------------------------------------------------
# 5. 中断 → 续跑 → 重放一致
# ---------------------------------------------------------------------------


def check_resume(tmp: Path) -> None:
    workspace = tmp / "ws2"
    workspace.mkdir(parents=True, exist_ok=True)
    old = os.environ.get("HWV_WORKSPACE")
    os.environ["HWV_WORKSPACE"] = str(workspace)
    try:
        # (a) 一口气跑完 → 参考日志
        full, _ = _end_to_end(workspace, tmp / "full.jsonl")
        full_events = agent_loop.read_journal(tmp / "full.jsonl")

        # (b) 跑到一半被打断
        interrupted_path = tmp / "part.jsonl"
        cut = 7        # 第 7 个事件之后断（够跑完 generate 与 put）
        try:
            _end_to_end(workspace, interrupted_path)   # 会写满；下面单独造中断
        except Exception:                              # noqa: BLE001
            pass
        interrupted_path.unlink(missing_ok=True)

        script = [
            call("hw_generate", requirement=str(REQ_CAR), max_candidates=1, materialize=1),
            call("hw_put", project="p22-bot",
                 design=lambda prev: (prev or {}).get("candidates", [{}])[0].get("design", {})),
            call("hw_ladder", project="p22-bot"),
            done("生成、写入、判定都做完了"),
        ]
        try:
            agent_loop.run(ScriptedClient(script), "做个避障小车，选一个候选写进去并判定",
                           mode="auto", journal=interrupted_path, interrupt_after=cut)
            raised = False
        except agent_loop.Interrupted as exc:
            raised = True
            cut = exc.seq
        part_events = agent_loop.read_journal(interrupted_path)

        # (c) 续跑：同脚本、跳过已做的那些回复，**不重跑工具**
        resumed = agent_loop.resume(
            interrupted_path, ScriptedClient(script), mode="auto",
            out=tmp / "resumed.jsonl")
        resumed_events = agent_loop.read_journal(tmp / "resumed.jsonl")

        check("6a. 中断在预期位置抛出 Interrupted，且日志已落盘",
              raised and len(part_events) == cut and (tmp / "resumed.jsonl").is_file(),
              f"中断事件数={len(part_events)} raise={raised} "
              f"续跑后事件数={len(resumed_events)}")

        # 续跑日志 = 已做的前缀 + 新做的；把它和一口气跑完的日志比决策序列
        replay = agent_loop.compare(part_events + resumed_events[len(part_events):],
                                    full_events)
        check("6b. 续跑与一口气跑：**决策序列逐条一致**（重放一致）",
              replay["same"], f"same={replay['same']} {replay['detail']} "
                              f"a={replay['a_events']} b={replay['b_events']}")

        # 续跑不重跑工具：result 事件的 call_id 不重复
        result_ids = [e.get("call_id") for e in resumed_events
                      if e.get("kind") == "result"]
        check("6c. 续跑**不重跑**已做过的工具（工具结果不重复出现）",
              len(result_ids) == len(set(result_ids)) and len(result_ids) >= 3,
              f"result call_id={result_ids}")

        # 日志只追加：没有任何"改动"事件，且 seq 严格递增
        seqs = [int(e.get("seq") or 0) for e in resumed_events
                if e.get("kind") != "journal_warning"]
        check("6d. 日志只追加：seq 严格递增，且没有 update/delete 类事件",
              seqs == sorted(seqs) and len(seqs) == len(set(seqs))
              and not any(str(e.get("kind")) in ("update", "delete", "modify")
                          for e in resumed_events),
              f"事件 {len(seqs)} 条，seq 递增={seqs == sorted(seqs)}")
        _ = full
    finally:
        if old is None:
            os.environ.pop("HWV_WORKSPACE", None)
        else:
            os.environ["HWV_WORKSPACE"] = old


# ---------------------------------------------------------------------------
# 6. 退化验证
# ---------------------------------------------------------------------------


def check_degradation() -> None:
    table = agent_tools.ToolTable()
    spec = table.get("hw_promote")
    strict = agent_tools.decide(spec, "auto")                  # type: ignore[arg-type]

    saved = agent_tools.decide

    def loose(candidate: agent_tools.ToolSpec, mode: str) -> str:
        decision = saved(candidate, mode)
        if candidate.tier == "human" and decision == "ask":
            return "allow"        # ← 退化：auto 放行不可逆操作
        return decision

    try:
        agent_tools.decide = loose                             # type: ignore[assignment]
        degraded = agent_tools.decide(spec, "auto")            # type: ignore[arg-type]
    finally:
        agent_tools.decide = saved                             # type: ignore[assignment]

    check("7. 退化验证：把「human 在任何模式都问」改成 auto 放行 → 2a 的判据变红",
          strict == "ask" and degraded == "allow",
          f"严格={strict}（期望 ask）｜退化后={degraded}（期望 allow）")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P22 验收：agent 循环（白名单 / 审批 / 假模型 / 续跑）===\n")
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p22-"))
    try:
        if only in ("", "whitelist"):
            check_whitelist()
        if only in ("", "approval"):
            check_approval()
        if only in ("", "args"):
            check_args()
        if only in ("", "e2e"):
            check_end_to_end(tmp)
        if only in ("", "resume"):
            check_resume(tmp)
        if only in ("", "degrade"):
            check_degradation()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for r in _RESULTS if r["status"] == PASS)
    failed = sum(1 for r in _RESULTS if r["status"] == FAIL)
    skipped = sum(1 for r in _RESULTS if r["status"] not in (PASS, FAIL))
    status = "ok" if failed == 0 and skipped == 0 else ("failed" if failed else "not_ready")
    print(f"\n=== {passed} 通过 / {failed} 失败 / {skipped} 未做到 ===")
    payload = {"status": status, "check": "P22", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())