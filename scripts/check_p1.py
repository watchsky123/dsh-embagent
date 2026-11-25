"""P1 验收：finding 可操作化。

    python -m dsh_embagent.scripts.check_p1

P1 只做一件事：**每条能动手的 finding，带一个能点的动作**。

所以这 14 条验四件事：

1. **映射表是唯一一处**，且源码里出现的每个 `fix.need` 都在表里
   （漏一个 = 那条 finding 悄悄变回自由文本，正是要修的病）
2. **没有的动作不编**：表里没有 → `None`；命令缺参数 → 降级成 `external`
3. **数据链路能通**：`fix` → handoff item → verdict.next → 快照 finding
4. **前端真的接上了**：按钮存在、已渲染、字段聚焦有去处
   （tsc 抓不到"只定义不接线"，所以读源码断言）

## 为什么第 1 条要扫源码

`fix.need` 是**在十几个地方写出来的字符串**。新加一个 need 而忘了进表，
运行时表现是"这条 finding 没有按钮"——**不报错、不失败、只是没了**。
这和 AGENTS §1.2 的闸门是同一个形状，所以它必须有一条**正面**断言。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import handoff, nextstep, verdict  # noqa: E402
from dsh_embagent.design.store import DesignStore  # noqa: E402

PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []
REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "dsh_embagent"


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": str(detail)[:900]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {str(detail)[:900]}")
    return ok


_PROJECT = "示例-多板"


def _repo() -> DesignStore:
    return DesignStore(_PROJECT)


def _items() -> list[dict[str, Any]]:
    return handoff.build(_repo()).to_dict()["items"]


# ---------------------------------------------------------------------------
# 1. 映射表：唯一一处，且不漏
# ---------------------------------------------------------------------------


def _needs_in_source() -> set[str]:
    """扫源码里所有 `"need": "xxx"` —— 包括 f-string 之外的写字面量处。"""
    found: set[str] = set()
    for path in sorted(PKG.glob("design/*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'"need"\s*:\s*"([a-z_]+)"', text):
            found.add(match.group(1))
    return found


def check_table() -> None:
    used = _needs_in_source()
    missing = sorted(need for need in used if need not in nextstep.NEEDS)
    check("1a. 源码里出现的每个 `fix.need` 都在映射表里（漏一个 = 那条悄悄变回文字）",
          not missing,
          f"源码 {len(used)} 个，表 {len(nextstep.NEEDS)} 条；缺：{missing}")

    kinds = {spec["kind"] for spec in nextstep.NEEDS.values()}
    check("1b. 表里只用四类动作，且**没有 auto_fix**（改设计是人的决定）",
          kinds <= set(nextstep.KINDS) and "auto_fix" not in kinds,
          f"用到的类={sorted(kinds)}")

    # 每条都得有 label，否则按钮下面没字
    bad = [need for need, spec in nextstep.NEEDS.items() if not spec.get("label")]
    check("1c. 每条映射都有人能读的 label", not bad, f"缺 label：{bad}")

    # edit_field / run_command 必须带 target，否则点了不知道去哪
    no_target = [need for need, spec in nextstep.NEEDS.items()
                 if spec["kind"] == "edit_field" and not spec.get("field")]
    no_target += [need for need, spec in nextstep.NEEDS.items()
                  if spec["kind"] == "run_command" and not spec.get("command")]
    check("1d. edit_field 都有 field、run_command 都有 command（否则点了不知去哪）",
          not no_target, f"缺 target：{no_target}")


# ---------------------------------------------------------------------------
# 2. 不编的动作
# ---------------------------------------------------------------------------


def check_honest() -> None:
    check("2a. 表里没有的 need → None（不临时编一个）",
          nextstep.build({"need": "no_such_need"}) is None
          and nextstep.build({}) is None
          and nextstep.build(None) is None,
          "未知 / 空 / None 都返回 None")

    # 命令模板缺参数 → 降级成 external，且**话说清楚**
    downgraded = nextstep.build({"need": "lookup_part", "refs": ["U3"]}, project="p")
    check("2b. 命令缺 lcsc → 降级 external，且 label 不含空的占位符",
          downgraded is not None and downgraded["kind"] == "external"
          and "lcsc" in downgraded["label"] and "查  " not in downgraded["label"],
          f"{downgraded}")

    with_lcsc = nextstep.build({"need": "lookup_part", "refs": ["U3"]},
                               project="p", lcsc="C1523")
    check("2c. 参数齐了 → 是 run_command，命令里带真实编号",
          with_lcsc is not None and with_lcsc["kind"] == "run_command"
          and "C1523" in str(with_lcsc.get("command")),
          str(with_lcsc.get("command"))[:120])

    # refs 展开：两个位号要都出现，而不是只第一个
    apart = nextstep.build({"need": "move_apart", "refs": ["C146", "Lora1"]})
    check("2d. refs 全部展开进 label（不是只取第一个）",
          apart is not None and "C146" in apart["label"] and "Lora1" in apart["label"]
          and apart.get("refs") == ["C146", "Lora1"],
          str(apart))


# ---------------------------------------------------------------------------
# 3. 数据链路通
# ---------------------------------------------------------------------------


def check_pipeline() -> None:
    items = _items()
    with_next = [i for i in items if isinstance(i.get("next"), dict)]
    check("3a. 交接单条目带 `next`（真工程上不是 0 条）",
          len(with_next) >= 10,
          f"{len(with_next)}/{len(items)} 条带动作；"
          f"类={sorted({i['next']['kind'] for i in with_next})}")

    # blocking 现在**一 finding 一条**，且每条都能动手
    blocking = [i for i in items if i.get("category") == "blocking" and i.get("status") == "fail"]
    no_action = [i["title"] for i in blocking if not isinstance(i.get("next"), dict)]
    check("3b. 每条 blocking 的 fail 条目都带动作（没有孤零零的一句话）",
          bool(blocking) and not no_action,
          f"blocking/fail {len(blocking)} 条，没动作的：{no_action[:3]}")

    # 层级信息没丢：标题前缀还在
    prefixed = [i for i in blocking if str(i.get("title")).startswith("V")]
    check("3c. 拆成一条条之后**层名仍可读**（标题带 V2/V0 前缀）",
          bool(prefixed), f"带前缀 {len(prefixed)}/{len(blocking)}")

    # 具体三条必须可操作（这是 P1 的原始诉求）
    link = next((i for i in items if "板间链路一条都没声明" in str(i.get("title"))), None)
    elec = next((i for i in items if "申报电气参数" in str(i.get("title"))), None)
    est = next((i for i in items if "估算值" in str(i.get("title"))), None)

    check("4a. 「板间链路一条都没声明」→ run_command，命令是 links --infer --apply",
          link is not None and isinstance(link.get("next"), dict)
          and link["next"]["kind"] == "run_command"
          and "links" in str(link["next"].get("command"))
          and "--apply" in str(link["next"].get("command")),
          str((link or {}).get("next", {}).get("command"))[:120])

    check("4b. 「申报电气参数」→ edit_field 指到 electrical.power_w（Inspector 里那个框）",
          elec is not None and isinstance(elec.get("next"), dict)
          and elec["next"]["kind"] == "edit_field"
          and elec["next"].get("field") == "electrical.power_w",
          str((elec or {}).get("next", {}).get("field")))

    # 「整个设计都没申报」没有具体靶子 → **不给 refs**（挑一个件就是猜）
    check("4b2. 那一条**不**带 refs（全设计范围，挑一个件就是猜）",
          elec is not None and not (elec.get("next") or {}).get("refs"),
          f"refs={((elec or {}).get('next') or {}).get('refs')}")

    # 4c 在 001-blink 的快照 finding 上验：那个工程**真的**有一个估算尺寸的件
    # （btn1）。示例-多板 没有估算件，拿它验这条等于验空气。
    code_b, out_b = _run([sys.executable, "-m", "dsh_embagent.design_cli", "snapshot",
                          "001-blink"])
    try:
        snap_b = json.loads(out_b.strip().splitlines()[-1])
    except (ValueError, IndexError):
        snap_b = {}
    est_finding = next((f for f in (snap_b.get("checks") or {}).get("findings") or []
                        if "估算值" in str(f.get("message"))), None)
    est_next = (est_finding or {}).get("next") or {}
    check("4c. 「尺寸是估算值」→ external（查手册不是本工具能做）+ **带具体位号**",
          est_finding is not None and est_next.get("kind") == "external"
          and bool(est_next.get("refs")),
          f"refs={est_next.get('refs')} label={str(est_next.get('label'))[:60]}")


def check_verdict_and_snapshot() -> None:
    payload = verdict.compose(_repo())
    rows = payload.get("next") or []
    with_step = [r for r in rows if isinstance(r.get("step"), dict)]
    check("5a. `verdict.next` 的条目带 `step`（首屏第一行就能点）",
          bool(rows) and bool(with_step),
          f"{len(with_step)}/{len(rows)} 条带 step")

    # 快照：失败的 finding 才有 next，通过的没有
    code, out = _run([sys.executable, "-m", "dsh_embagent.design_cli", "snapshot",
                      "001-blink"])
    try:
        snapshot = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        snapshot = {}
    findings = (snapshot.get("checks") or {}).get("findings") or []
    failed = [f for f in findings if f.get("ok") is not True]
    passed = [f for f in findings if f.get("ok") is True]
    check("5b. 快照里**失败的** finding 带 `next`（通过的**不带**）",
          all(isinstance(f.get("next"), dict) for f in failed) if failed else True
          and not any("next" in f for f in passed),
          f"失败 {len(failed)} 条（exit={code}），通过 {len(passed)} 条"
          f"其中带 next 的通过项={sum(1 for f in passed if 'next' in f)}")

    # 通过的 finding 带 next 是**噪音**：OK 的东西不需要"下一步"
    check("5c. 通过的 finding 一条 next 都没有（OK 的不给按钮）",
          not any("next" in f for f in passed),
          f"通过 {len(passed)} 条")


# ---------------------------------------------------------------------------
# 4. 前端接上了
# ---------------------------------------------------------------------------


def check_frontend() -> None:
    panels = (PKG / "src" / "client" / "panels.tsx").read_text(encoding="utf-8")
    view = (PKG / "src" / "client" / "HwIdeView.tsx").read_text(encoding="utf-8")
    store = (PKG / "src" / "client" / "store.ts").read_text(encoding="utf-8")
    inspector = (PKG / "src" / "client" / "Inspector.tsx").read_text(encoding="utf-8")
    types = (PKG / "src" / "shared" / "types.ts").read_text(encoding="utf-8")

    check("6a. 类型：NextStep 存在，且 Finding/VerdictNext 都带 next/step",
          "export interface NextStep" in types
          and "next?: NextStep" in types and "step?: NextStep" in types,
          "types.ts")

    check("6b. `StepButton` 存在，且**判定行与结论行都渲染它**",
          "function StepButton" in panels
          and panels.count("<StepButton") >= 2,
          f"<StepButton 出现 {panels.count('<StepButton')} 次")

    check("6c. 复制失败**不假装成功**（catch 分支把 copied 置回 false）",
          "setCopied(false)" in panels and "navigator.clipboard" in panels,
          "有 clipboard 调用与失败回退")

    check("6d. edit_field 走 `onSelectField` 一路到 Inspector 的 focusField",
          "onSelectField" in panels and "onSelectField" in view
          and "focusField" in store and "focusField" in inspector
          and "selectField(" in store,
          "panels → view → store.selectField → Inspector.focusField")

    check("6e. Inspector 真的用 focusField **聚焦那个框**（不是只存着不用）",
          "focusField !== 'electrical.power_w'" in inspector
          and "powerRef" in inspector and "scrollIntoView" in inspector,
          "有 useEffect 聚焦 + scrollIntoView")


# ---------------------------------------------------------------------------
# 5. 退化
# ---------------------------------------------------------------------------


def check_degradation() -> None:
    saved = dict(nextstep.NEEDS)
    before = [i for i in _items() if isinstance(i.get("next"), dict)]
    try:
        # 退化：把 move_apart 从表里拿掉
        nextstep.NEEDS.pop("move_apart", None)
        after = [i for i in _items() if isinstance(i.get("next"), dict)]
    finally:
        nextstep.NEEDS.clear()
        nextstep.NEEDS.update(saved)

    check("7. 退化验证：从表里拿掉 move_apart → 那些条目真的失去 `next`（3a/3b 会红）",
          len(after) < len(before),
          f"拿掉前 {len(before)} 条带动作，拿掉后 {len(after)} 条")

    restored = [i for i in _items() if isinstance(i.get("next"), dict)]
    check("7b. 恢复后条数回到原值（表中招可逆，测试自己不留副作用）",
          len(restored) == len(before), f"{len(restored)} vs {len(before)}")


def _run(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(  # noqa: S603 - argv 由本脚本拼
        argv, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=600)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P1 验收：finding 可操作化 ===\n")
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p1-"))
    try:
        if only in ("", "table"):
            check_table()
        if only in ("", "honest"):
            check_honest()
        if only in ("", "pipeline"):
            check_pipeline()
            check_verdict_and_snapshot()
        if only in ("", "frontend"):
            check_frontend()
        if only in ("", "degrade"):
            check_degradation()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for r in _RESULTS if r["status"] == PASS)
    failed = sum(1 for r in _RESULTS if r["status"] == FAIL)
    skipped = sum(1 for r in _RESULTS if r["status"] not in (PASS, FAIL))
    status = "ok" if failed == 0 and skipped == 0 else ("failed" if failed else "not_ready")
    print(f"\n=== {passed} 通过 / {failed} 失败 / {skipped} 未做到 ===")
    payload = {"status": status, "check": "P1", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())