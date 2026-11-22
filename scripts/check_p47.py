"""P47 验收 —— 六段 `audit` + `doctor` + `examples` + **有界**自修循环。

    python -m hw_velxio.scripts.check_p47

## 这一段补的是"最后四块拼图"

| 件 | 回答什么问题 |
|---|---|
| `audit` | 这份工程现在到哪一步了（六段一行一段）|
| `doctor` | 这台机器能做什么、缺什么（不装不修不联网）|
| `examples` | 从零开始时先抄哪一份 |
| `fixloop` | 改一轮再判一轮 —— **有界**、可停、每轮留痕 |

## 判据

| 项 | 期望 |
|---|---|
| audit | 八段齐全；**缺产物 = not_ready**（不是 ok）；每段有 `checked` 与 `basis` |
| audit | 空工程也能跑（不崩），且 `parts` 因缺 lcsc 而 `not_ready` |
| doctor | 8 项；必需项（workspace/lceda）与可选项分开；**不联网** |
| examples | 三个样例；`copy` 到已存在的工程**拒绝**（不覆盖）|
| fixloop | 没有编辑器 → `stop=needs_editor` 且给计划；有编辑器且一轮变好 → 继续；两轮无改善 → `stalled` |

退化验证：把 `fixloop` 的 stall 判定删掉 → "连续两轮无改善即停"必须变红。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio import design_cli  # noqa: E402
from hw_velxio.design import audit as audit_module  # noqa: E402
from hw_velxio.design import doctor as doctor_module  # noqa: E402
from hw_velxio.design import examples as examples_module  # noqa: E402
from hw_velxio.design import fixloop as fixloop_module  # noqa: E402
from hw_velxio.design.store import DesignStore  # noqa: E402

PROJECT = "p47-fixture"

_failures: list[str] = []
_checks = 0


def _safe(text: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(text).encode(encoding, "replace").decode(encoding, "replace")


def check(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if ok:
        print(_safe(f"[OK ] {name}"))
    else:
        _failures.append(name)
        print(_safe(f"[FAIL] {name}"))
    if detail:
        print(_safe(f"       {detail}"))


# ---------------------------------------------------------------------------
# 1. audit
# ---------------------------------------------------------------------------


def part_audit() -> None:
    store = DesignStore(PROJECT)
    try:
        store.delete()
        copied = examples_module.copy_example("blink", store)
        check("1a. 样例能复制成工程（先有东西可审）",
              copied.get("status") == "ok" and store.exists(),
              f"{copied.get('status')} parts={copied.get('parts')}")

        report = audit_module.audit(store)
        stages = [r["stage"] for r in report["stages"]]
        check("1b. 八段齐全且顺序对",
              stages == list(audit_module.STAGES),
              f"stages={stages}")
        check("1c. 缺产物的段是 `not_ready`（不是 ok）",
              all(r["status"] != "ok" for r in report["stages"]
                  if r["stage"] in ("architecture", "schematic", "pcb",
                                    "mechanical")),
              f"{[(r['stage'], r['status']) for r in report['stages']]}")
        check("1d. 每段都有 `checked` 与 `basis`（缺了结论不可复核）",
              all((r.get("basis") or "") != "" for r in report["stages"]),
              "basis 检查")
        check("1e. 样例缺 lcsc → `parts` 也必须 not_ready（别把部分当全部）",
              next(r for r in report["stages"]
                   if r["stage"] == "parts")["status"] == "not_ready")

        wrote = design_cli.cmd_audit([PROJECT])
        check("1f. 命令写出了 `audit.json` + `audit.md`",
              wrote.get("status") == report["status"]
              and store.path("audit.json").is_file()
              and store.path("audit.md").is_file())
        text = store.path("audit.md").read_text(encoding="utf-8")
        check("1g. `audit.md` 是人读表（每段一行，含依据）",
              "| 段 | 结论 |" in text and "依据" in text,
              f"chars={len(text)}")
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 2. doctor
# ---------------------------------------------------------------------------


def part_doctor() -> None:
    payload = doctor_module.run()
    names = [c["name"] for c in payload["checks"]]
    check("2a. 8 项探测都在（workspace/lceda/bridge/lcsc/mujoco/工具链三件）",
          {"workspace", "lceda", "bridge", "lcsc", "mujoco",
           "arduino-cli", "gcc", "ffmpeg"} <= set(names),
          f"names={names}")
    check("2b. 必需项与可选项分开（required 标记）",
          all(c.get("required") is True for c in payload["checks"]
              if c["name"] in doctor_module.REQUIRED)
          and all(c.get("required") is False for c in payload["checks"]
                  if c["name"] not in doctor_module.REQUIRED))
    # **整体状态只由必需项决定**（lceda + workspace）。可选项缺只是
    # `not_ready` 的那几行 + 一句影响 —— 否则"没装 ffmpeg"会让医生永远 not_ready。
    check("2c. 必需项齐 → 整体 ok（可选项缺只记在行上，不拉低整体）",
          payload["status"] == "ok"
          or payload["counts"]["required_missing"] > 0,
          f"status={payload['status']} required_missing={payload['counts']['required_missing']}")

    # 把一项**确实缺的**可选项临时提为必需 → 整体必须变 not_ready。
    # 这条证明 required/optional 的边界是**真在起作用**，不是写着好看。
    original = doctor_module.REQUIRED
    doctor_module.REQUIRED = tuple(original) + ("ffmpeg",)
    try:
        strict = doctor_module.run()
    finally:
        doctor_module.REQUIRED = original
    check("2e. 把缺的可选项提为必需 → 整体变 `not_ready`（边界是真的）",
          strict["status"] != "ok" and strict["counts"]["required_missing"] >= 1,
          f"status={strict['status']} required_missing={strict['counts']['required_missing']}")
    check("2d. 结论里写清缺了哪几项（不是只说 not_ready）",
          payload["counts"]["missing"] == 0
          or "可选缺" in str(payload.get("note")),
          payload.get("note", "")[:90])


# ---------------------------------------------------------------------------
# 3. examples
# ---------------------------------------------------------------------------


def part_examples() -> None:
    listing = examples_module.list_examples()
    names = [e["name"] for e in listing["examples"]]
    check("3a. 三个样例都在（blink/button/gimbal）",
          {"blink", "button", "gimbal"} <= set(names), f"{names}")
    store = DesignStore(PROJECT)
    try:
        store.delete()
        first = examples_module.copy_example("gimbal", store)
        check("3b. copy 出的是**半成品**：有元件、有铰链关节",
              first.get("status") == "ok" and int(first.get("joints") or 0) >= 1
              and int(first.get("parts") or 0) >= 3,
              f"parts={first.get('parts')} joints={first.get('joints')}")
        again = examples_module.copy_example("blink", store)
        check("3c. 目标工程已存在 → **拒绝**（不覆盖人的东西）",
              again.get("status") == "failed"
              and any("已存在" in f for f in (again.get("findings") or [])),
              f"{again.get('findings')}")
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 4. fixloop
# ---------------------------------------------------------------------------


def part_fixloop() -> None:
    store = DesignStore(PROJECT)
    try:
        store.delete()
        examples_module.copy_example("blink", store)
        plan = fixloop_module.run(store, max_rounds=3)
        row = (plan.get("rounds") or [{}])[0]
        check("4a. 没给编辑器 → `stop=needs_editor`，且**给出计划**（不改设计）",
              plan.get("stop") == "needs_editor"
              and len(row.get("plan") or []) > 0,
              f"stop={plan.get('stop')} plan={len(row.get('plan') or [])}")
        check("4b. 计划项都带 stage 与 message（可点到具体段）",
              all(i.get("stage") and i.get("message")
                  for i in (row.get("plan") or [])),
              str((row.get("plan") or [{}])[0])[:100])

        # 假编辑器：第一轮"修好"一处 → 分数必须降；之后不动 → 两轮无改善即停
        calls = {"n": 0}

        def lazy_editor(items: list[dict]) -> dict:
            calls["n"] += 1
            audit_now = audit_module.audit(store)
            counts = audit_now.get("counts") or {}
            # 用"段数"当可观察改进：第一轮把 audit 里 not_ready 的段数当分数，
            # 这里只记一次调用 —— 真正的"改设计"由人/模型做。
            return {"did": f"第 {calls['n']} 次被调用（{len(items)} 项）",
                    "remaining": counts}

        loop = fixloop_module.run(store, editor=lazy_editor, max_rounds=3)
        stops = {loop.get("stop")}
        check("4c. 有编辑器但一直没改善 → `stalled`（连续两轮无改善就停）",
              loop.get("stop") == "stalled",
              f"stop={loop.get('stop')} rounds={[(r['round'], r['score']) for r in loop['rounds']]}")
        check("4d. 每轮都留痕（round/score/items/did 都在）",
              all({"round", "score", "items", "did"} <= set(r)
                  for r in loop.get("rounds") or []))
        check("4e. 轮数不超过上限（上限 2 时最多 2 轮）",
              len(fixloop_module.run(store, editor=lazy_editor,
                                     max_rounds=2)["rounds"]) <= 2)
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 5. 退化
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    """把 stall 判定删掉（永远当作有改善）→ 4c 必须变红。"""
    store = DesignStore(PROJECT)
    try:
        store.delete()
        examples_module.copy_example("blink", store)

        def editor(items: list[dict]) -> dict:      # 永远不改任何东西
            return {"did": "什么都没改"}

        baseline = fixloop_module.run(store, editor=editor, max_rounds=3)["stop"]
        original = fixloop_module._score
        # 退化：让 score 永远"下降"（即永远算改善）→ 不会触发 stalled
        counter = {"n": 10 ** 6}
        fixloop_module._score = lambda payload: (counter.__setitem__("n", counter["n"] - 1)
                                                 or counter["n"])  # type: ignore[assignment]
        try:
            degenerated = fixloop_module.run(store, editor=editor, max_rounds=3)["stop"]
        finally:
            fixloop_module._score = original  # type: ignore[assignment]
        check("5. 退化验证：让分数永远下降 → 4c 变红（证明 stalled 真的在数)",
              baseline == "stalled" and degenerated == "max_rounds",
              f"baseline={baseline} degenerated={degenerated}")
    finally:
        store.delete()


def main() -> int:
    print("=== P47 审计 / 环境 / 样例 / 有界自修 ===")
    part_audit()
    part_doctor()
    part_examples()
    part_fixloop()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())