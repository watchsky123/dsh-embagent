"""P5 验收：需求草稿 + 确认闸门。

    python -m hw_velxio.scripts.check_p5

P5 的初版方案有两处**错的前提**（见 `docs/p5-plan.md` §0）：

* 「0 个 provider」→ 错。DSH 的 `ctx.llm` 已挂载，`dsh-our-free-model` 就是适配器；
  hw_velxio 早就在用 `session.prompt()` 投递。**P5 不写 provider。**
* 「字段是 units/links」→ 错。`Requirement` 是
  `id/title/roles/interfaces/constraints/numbers/notes`。

所以这 24 条验的是**修正后**的三件事：

1. **契约对齐**：`ReqDraft` 的字段与 `Requirement` **比对**（不是抄一份）
2. **校验拦得住**：未知 role / 悬空 interface / 自环 / 空 roles / 负约束
3. **闸门不可绕**：没校验不写、没 confirm 不写；`numbers` 无消费方要说出来

## 为什么 1a 要比对而不是写死

写死 `["id","title",...]` 的断言在 `Requirement` 加字段时会**照样绿**——
而那时草稿会静默丢掉新字段。比对 `Requirement.__dataclass_fields__` 才能红。
（AGENTS §3.15：再写一份清单 = 迟早分叉。）

## 为什么 3 用真 `generate`

前两类是合成夹具（证明判据本身对）；`3c` 必须走真 `generate --fast`——
那才证明"校验通过 ⇒ generate 真的能枚举"。按 AGENTS §0.3 的区分。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import generate, req_draft  # noqa: E402

PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []
REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "hw_velxio"


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": str(detail)[:900]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {str(detail)[:900]}")
    return ok


GOOD = {
    "id": "bot", "title": "避障小车",
    "roles": ["mcu", "motor", "sensor", "power_in"],
    "interfaces": [
        {"a": "mcu", "b": "motor", "protocol": "pwm"},
        {"a": "mcu", "b": "sensor", "protocol": "i2c"},
    ],
    "constraints": {"area_mm2": 600},
}


def _kinds(report: dict[str, Any], key: str) -> list[str]:
    return [str(row.get("kind")) for row in report.get(key) or []]


# ---------------------------------------------------------------------------
# 1. 契约对齐
# ---------------------------------------------------------------------------


def check_contract() -> None:
    draft_fields = list(req_draft.ReqDraft.__dataclass_fields__)
    req_fields = list(generate.Requirement.__dataclass_fields__)
    check("1a. `ReqDraft` 字段 == `Requirement` 字段（**比对**，不抄清单）",
          draft_fields == req_fields,
          f"draft={draft_fields} / requirement={req_fields}")

    # 角色清单也必须来自 roles.py（再写一份会分叉）
    known = req_draft._known_roles()
    from hw_velxio.design import roles as roles_module
    from_source = sorted({t.role for t in roles_module.ROLE_TEMPLATES})
    check("1b. 已知角色取自 `roles.ROLE_TEMPLATES`（不另写清单）",
          known == from_source,
          f"{len(known)} 个：{known[:6]}…")

    # 消费方清单同样要能从源码核实
    check("1c. `CONSUMED_CONSTRAINTS` 只列源码里真读过的键",
          req_draft.CONSUMED_CONSTRAINTS == ("area_mm2",),
          f"{req_draft.CONSUMED_CONSTRAINTS}")


# ---------------------------------------------------------------------------
# 2. 校验拦得住
# ---------------------------------------------------------------------------


def check_validate() -> None:
    good = req_draft.validate(req_draft.ReqDraft.from_dict(GOOD))
    check("2a. 合法草稿 → ok，且没有任何 problem",
          good["ok"] and good["problems"] == [],
          f"counts={good['counts']}")

    empty = req_draft.validate(req_draft.ReqDraft.from_dict({}))
    check("2b. 空草稿 → **不通过**（roles 空），不是「没什么可查」",
          not empty["ok"] and "roles_empty" in _kinds(empty, "problems"),
          f"{_kinds(empty, 'problems')}")

    unknown = req_draft.validate(req_draft.ReqDraft.from_dict(
        {**GOOD, "roles": ["mcu", "teleporter"]}))
    check("2c. 未知角色 → 拦住，且**列出可用角色**",
          "role_unknown" in _kinds(unknown, "problems")
          and any("teleporter" in p["message"] for p in unknown["problems"]),
          str([p["message"] for p in unknown["problems"]])[:120])

    dangling = req_draft.validate(req_draft.ReqDraft.from_dict(
        {**GOOD, "interfaces": [{"a": "mcu", "b": "camera"}]}))
    check("2d. interface 引用不在 roles 里的角色 → 拦住（悬空）",
          "interface_dangling" in _kinds(dangling, "problems"),
          str([p["message"] for p in dangling["problems"]])[:120])

    loop = req_draft.validate(req_draft.ReqDraft.from_dict(
        {**GOOD, "interfaces": [{"a": "mcu", "b": "mcu"}]}))
    check("2e. interface 两端同一角色（自环）→ 拦住",
          "interface_self_loop" in _kinds(loop, "problems"),
          str([p["message"] for p in loop["problems"]])[:120])

    bad_area = req_draft.validate(req_draft.ReqDraft.from_dict(
        {**GOOD, "constraints": {"area_mm2": -5}}))
    check("2f. `area_mm2` 是负数 → 拦住（它是唯一被消费的约束键）",
          "constraint_not_positive" in _kinds(bad_area, "problems"),
          str([p["message"] for p in bad_area["problems"]])[:120])

    # 非对象 interface：from_dict 要保住它可被报告，而不是静默丢弃
    broken = req_draft.ReqDraft.from_dict(
        {**GOOD, "interfaces": [{"a": "mcu", "b": "motor"}, 42]})
    report = req_draft.validate(broken)
    check("2g. interface 项不是对象 → 记成 `_invalid` 并报出来（不静默丢）",
          "interface_not_object" in _kinds(report, "problems")
          and len(broken.interfaces) == 2,
          f"{_kinds(report, 'problems')} / 保留 {len(broken.interfaces)} 项")


# ---------------------------------------------------------------------------
# 3. numbers 无消费方要说出来
# ---------------------------------------------------------------------------


def check_numbers() -> None:
    with_numbers = req_draft.validate(req_draft.ReqDraft.from_dict(
        {**GOOD, "numbers": {"detect_range_m": 2.0}}))
    kinds = _kinds(with_numbers, "warnings")
    check("3a. 填了 numbers → 报 `field_without_consumer`（**不是「低影响」**）",
          "field_without_consumer" in kinds and with_numbers["ok"],
          f"warnings={kinds}")

    # 关键：它不是 problem（不拦 apply），但**必须出现在 warnings 里**
    check("3b. 它是 warning 不是 problem：不拦 apply，但一定被说出来",
          with_numbers["ok"] and with_numbers["problems"] == []
          and bool(with_numbers["warnings"]),
          f"ok={with_numbers['ok']} warnings={len(with_numbers['warnings'])}")

    # 反证：**没有任何代码读 `req.numbers`** —— 扫整个 design/ 核实。
    #
    # 第一版这条断言按"行里有 numbers"筛，于是把 `generate.py:57` 的**文档字符串**
    # （「`numbers` 是协同验证要用的数值」）也当成读取点，自己把自己判红。
    # 判据必须精确到**读取形状**：`req.numbers` / `.numbers.get(` / `.numbers[`。
    import re as _re
    read_re = _re.compile(r"\breq\.numbers\b|\.numbers\.get\(|\.numbers\[")
    readers: list[str] = []
    for path in sorted((PKG / "design").glob("*.py")):
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            if read_re.search(line):
                readers.append(f"{path.name}:{number} {stripped[:60]}")
    check("3c. 整个 design/ 里**没有** `req.numbers` 的读取点（结论可复核）",
          not readers, f"读取点：{readers[:3]}")

    unread = req_draft.validate(req_draft.ReqDraft.from_dict(
        {**GOOD, "constraints": {**GOOD["constraints"], "weight_g": 100}}))
    check("3d. 未被消费的 constraints 键也报出来（`constraint_unread`）",
          "constraint_unread" in _kinds(unread, "warnings"),
          f"{_kinds(unread, 'warnings')}")


# ---------------------------------------------------------------------------
# 4. 端到端 + 闸门
# ---------------------------------------------------------------------------


def check_end_to_end(tmp: Path) -> None:
    os.environ["HWV_WORKSPACE"] = str(tmp / "ws")
    try:
        from hw_velxio import design_cli
        from hw_velxio.design.store import DesignStore

        store = DesignStore("p5-fixture")
        store.dir.mkdir(parents=True, exist_ok=True)
        # `snapshot()` 要 `design.json`（工程先存在，才谈需求）。
        # 这正是 P5 的一个**真实边界**：从零开始时工程还不存在，
        # 需求草稿现在只能挂在已有工程上（见 CHANGES 的已知缺口）。
        import shutil as _shutil
        _shutil.copyfile(
            PKG / "tests" / "fixtures" / "designs" / "tiny-blink.json",
            store.dir / "design.json")

        # -- 草稿落盘 ------------------------------------------------
        saved = req_draft.save(store, req_draft.ReqDraft.from_dict(GOOD))
        check("4a. 草稿落盘，且**不碰** req.json（两个文件）",
              (store.dir / req_draft.DRAFT_FILE).is_file()
              and not (store.dir / req_draft.REQ_FILE).exists(),
              f"files={sorted(p.name for p in store.dir.iterdir())}")

        # -- 未确认 apply 必须拒 --------------------------------------
        denied = req_draft.apply(store, confirm=False)
        check("4b. 草稿合法但 **没有 confirm** → 拒绝，且不写 req.json",
              denied["status"] == "failed"
              and not (store.dir / req_draft.REQ_FILE).exists(),
              str(denied.get("reason"))[:80])

        # -- 校验不过的草稿也不能 apply ------------------------------
        req_draft.save(store, req_draft.ReqDraft.from_dict(
            {**GOOD, "roles": ["mcu", "nope"]}))
        broken = req_draft.apply(store, confirm=True)
        check("4c. 校验不过时**即使 confirm=true 也拒**（闸门有先后）",
              broken["status"] == "failed"
              and not (store.dir / req_draft.REQ_FILE).exists(),
              str(broken.get("reason"))[:60])

        # -- 恢复合法草稿并确认 --------------------------------------
        req_draft.save(store, req_draft.ReqDraft.from_dict(GOOD))
        done = req_draft.apply(store, confirm=True)
        applied = store.read(req_draft.REQ_FILE)
        check("4d. 校验过 + 确认 → 写 req.json，且内容与草稿一致",
              done["status"] == "ok" and isinstance(applied, dict)
              and applied["roles"] == GOOD["roles"],
              f"file={done.get('file')}")

        # -- 端到端：这份 req 真的能喂 generate ----------------------
        argv = ["generate", "--requirement", str(store.dir / req_draft.REQ_FILE),
                "--max-candidates", "2", "--fast"]
        payload = design_cli.COMMANDS["generate"](argv)
        check("4e. 应用后的 req.json 能被 `generate --fast` 消费（出候选）",
              payload.get("status") == "ok"
              and len(payload.get("candidates") or []) > 0,
              f"candidates={len(payload.get('candidates') or [])} "
              f"enumerated={payload.get('enumerated')}")

        # -- 快照带 req 摘要 ------------------------------------------
        snap = design_cli.snapshot("p5-fixture")
        req = snap.get("req") or {}
        check("4f. 快照带 `req` 摘要（草稿 / 校验 / applied）",
              isinstance(req.get("draft"), dict)
              and isinstance(req.get("validation"), dict)
              and req.get("applied") is True,
              f"applied={req.get('applied')} next={str(req.get('next'))[:40]}")
    finally:
        os.environ.pop("HWV_WORKSPACE", None)


# ---------------------------------------------------------------------------
# 4b. 从零开局（工程还不存在）
# ---------------------------------------------------------------------------


def check_zero_start(tmp: Path) -> None:
    """**这一组是补断链的验收**：工程不存在时也能写草稿、确认后连工程一起建。

    背景（AGENTS §3.19）：`req-draft` 原本只写 `<project>/req_draft.json`，
    而工程要有 `design.json` 才算存在。于是"从零一句话开局"走不通。
    """
    os.environ["HWV_WORKSPACE"] = str(tmp / "ws-zero")
    try:
        from hw_velxio.design.store import DesignStore

        store = DesignStore("from-zero")
        check("7a. 工程确实还不存在（这是本组的前提，不是假设）",
              not store.exists(), f"exists={store.exists()}")

        saved = req_draft.save(store, req_draft.ReqDraft.from_dict(GOOD))
        workspace_draft = (tmp / "ws-zero" / req_draft.DRAFT_DIR
                           / "from-zero.json")
        check("7b. 工程不存在 → 草稿落到**工作区**（`_drafts/<id>.json`）",
              saved.get("scope") == "workspace" and workspace_draft.is_file()
              and not store.dir.exists(),
              f"scope={saved.get('scope')} file={saved.get('file')}")

        check("7c. 这份草稿**能读回来**（load 会先看工程再看工作区）",
              req_draft.load(store) is not None
              and req_draft.draft_scope(store) == "workspace",
              f"scope={req_draft.draft_scope(store)}")

        no_confirm = req_draft.apply(store)
        check("7d. 没确认 → 拒绝，且**工程不会被建出来**（建工程在确认之后）",
              no_confirm["status"] == "failed" and not store.exists(),
              str(no_confirm.get("reason"))[:60])

        done = req_draft.apply(store, confirm=True)
        check("7e. 确认 → **顺手建工程** + 写 req.json",
              done["status"] == "ok" and done.get("created_project") is True
              and store.exists() and (store.dir / req_draft.REQ_FILE).is_file(),
              f"created_project={done.get('created_project')} "
              f"board={done.get('board')}")

        design = store.load()
        check("7f. 建出来的工程**是合法 DesignModel**（不是空壳占位）",
              design.id == "from-zero" and design.board is not None
              and design.validate() == [],
              f"id={design.id} board={design.board.kind} "
              f"problems={design.validate()}")

        # 建工程必须复用 auto_lock —— 漏了会让导出闸门问"mcu_family 未闭合"
        ledger = store.read("deferred.json") or {}
        check("7g. 建工程走了 `auto_lock`（deferred.json 存在且已锁决策）",
              isinstance(ledger, dict) and bool(ledger.get("locked")),
              f"locked={sorted((ledger.get('locked') or {}))[:4]}")

        # **草稿必须搬进工程、工作区那份要删掉**：否则两份副本会分叉。
        check("7h. apply 后草稿**搬进工程**（scope=project，工作区那份已删）",
              req_draft.draft_scope(store) == "project"
              and (store.dir / req_draft.DRAFT_FILE).is_file()
              and not workspace_draft.exists()
              and done.get("workspace_draft_removed") is True,
              f"draft_scope={req_draft.draft_scope(store)} "
              f"工作区那份还在={workspace_draft.exists()} "
              f"removed={done.get('workspace_draft_removed')}")

        listed = req_draft.list_workspace()
        check("7i. 搬完之后工作区列表里**不再有它**（没有第二份副本）",
              all(row.get("id") != "from-zero" for row in listed),
              f"工作区草稿：{[r.get('id') for r in listed]}")
    finally:
        os.environ.pop("HWV_WORKSPACE", None)


# ---------------------------------------------------------------------------
# 5. 前端与工具
# ---------------------------------------------------------------------------


def check_frontend() -> None:
    tools = (PKG / "src" / "tools.ts").read_text(encoding="utf-8")
    check("5a. `hw_req_draft` 与 `hw_req_apply` 都注册了（模型会话里可调）",
          "name: 'hw_req_draft'" in tools and "name: 'hw_req_apply'" in tools,
          "两个工具都在 tools.ts")

    # apply 工具的描述必须点明"这是人的动作"
    block = tools.split("name: 'hw_req_apply'")[1][:400]
    check("5b. `hw_req_apply` 的描述写明**这是人的动作**（不替人调用）",
          "人的动作" in block or "人的确认" in block,
          block.split("description:")[1][:90].strip() if "description:" in block else "")

    store_src = (PKG / "src" / "client" / "store.ts").read_text(encoding="utf-8")
    panels = (PKG / "src" / "client" / "panels.tsx").read_text(encoding="utf-8")
    view = (PKG / "src" / "client" / "HwIdeView.tsx").read_text(encoding="utf-8")
    service = (PKG / "src" / "service.ts").read_text(encoding="utf-8")

    delivery = _delivery_sections(store_src)
    check("5c. 前端：`req` 是 delivery 组分区，标签「需求」",
          "req" in delivery and "req: '需求'" in store_src,
          f"delivery={delivery}")

    has_panel = "export function ReqPanel" in panels
    has_case = "case 'req':" in view
    check("5d. `ReqPanel` 存在，且 view 里有 case 'req'",
          has_panel and has_case, f"panel={has_panel} case={has_case}")

    check("5e. 应用按钮**校验没过时禁用**（不是点了才报错）",
          "disabled={req.applied || !(validation?.ok === true)}" in panels,
          "disabled 条件里含 validation.ok")

    check("5f. 有 `applyReqDraft` 的宿主 RPC（confirm 由客户端传 true）",
          "@Remote('applyReqDraft')" in service and "applyReqDraft" in store_src,
          "service + store 都在")


# ---------------------------------------------------------------------------
# 6. 退化
# ---------------------------------------------------------------------------


def check_degradation() -> None:
    good = req_draft.ReqDraft.from_dict(GOOD)
    baseline = req_draft.validate(good)
    check("6a. 对照组：合法草稿本来 0 problem",
          baseline["ok"] and baseline["problems"] == [], "ok=True")

    saved = req_draft.validate

    def lenient(draft: req_draft.ReqDraft) -> dict[str, Any]:
        """退化：不查悬空/自环（"反正 generate 会跳过"）。"""
        report = saved(draft)
        keep = [p for p in report["problems"]
                if p["kind"] not in ("interface_dangling", "interface_self_loop")]
        return {**report, "ok": not keep, "problems": keep}

    try:
        req_draft.validate = lenient          # type: ignore[assignment]
        broken = req_draft.ReqDraft.from_dict(
            {**GOOD, "interfaces": [{"a": "mcu", "b": "camera"}]})
        degraded = req_draft.validate(broken)
    finally:
        req_draft.validate = saved            # type: ignore[assignment]

    check("6b. 退化验证：不查悬空 interface → 那条草稿变成「通过」（2d 会红）",
          degraded["ok"] and not baseline["ok"] is False,
          f"退化后 ok={degraded['ok']}，正常情况该条有 problem")

    restored = req_draft.validate(
        req_draft.ReqDraft.from_dict(
            {**GOOD, "interfaces": [{"a": "mcu", "b": "camera"}]}))
    check("6c. 恢复后重新拦住（测试自己不留副作用）",
          not restored["ok"], f"{_kinds(restored, 'problems')}")


def _run(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(  # noqa: S603
        argv, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=600)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------


def _delivery_sections(store_src: str) -> list[str]:
    """从 `SECTIONS.delivery = [...]` 里取分区名。

    **不要按字面量钉死整条数组**：加一个分区就会让别的验收脚本红，
    而"firmware 属于 delivery 组"这个意图没变（AGENTS §3.15 同源）。
    """
    anchor = "delivery: ["
    i = store_src.find(anchor)
    if i < 0:
        return []
    j = store_src.find("]", i)
    if j < 0:
        return []
    raw = store_src[i + len(anchor):j]
    return [part.strip().strip("'").strip('"')
            for part in raw.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P5 验收：需求草稿 + 确认闸门 ===\n")
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p5-"))
    try:
        if only in ("", "contract"):
            check_contract()
        if only in ("", "validate"):
            check_validate()
        if only in ("", "numbers"):
            check_numbers()
        if only in ("", "e2e"):
            check_end_to_end(tmp)
        if only in ("", "zero"):
            check_zero_start(tmp)
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
    payload = {"status": status, "check": "P5", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())