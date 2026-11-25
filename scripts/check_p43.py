"""P43 验收 —— 编排器（`scaffold`）：一句需求 → 一份半成品。

    python -m dsh_embagent.scripts.check_p43

## 这一段补的是什么

之前从需求到一个"能装能编能看 3D"的半成品要人按顺序跑七八个命令，顺序错了
就静默出一份"看着有东西、其实没接上"的工程。P43 把那条链固定下来：

```
req → 选路(synth/generate/roles) → design.json → pinmap → joints 提案
    → fw 骨架 → MJCF → [可选] 立创发射
```

两条最容易被写错的判据：

1. **缺件不中止**（用户要求：所有项目都是半成品）。缺 `.ioc` 走网表、
   缺关节走提案；它们进 `open_items.md`，**不阻塞产出**。
2. **发射失败不拖累其它段**（`--to pcb` 是外层加的，不是编排的一部分）。

## 判据

| 项 | 期望 |
|---|---|
| 段齐全 | `requirement/design/pinmap/joints/firmware/mechanical` 都在 |
| 缺引脚表 | `pinmap=not_ready` 有原因，**但后面的段照样跑**（firmware 段仍在） |
| 确定性 | 同输入跑两次，`scaffold.json` **逐字节一致**（无时间戳） |
| happy path | 手搓 ATtiny85 设计 → pinmap ok、固件骨架落盘 |
| 发射隔离 | `HWV_LCEDA` 指向不存在的路径 → `pcb=not_ready`，`design` 仍 ok |
| 退化 | 关掉 `CONTINUE_ON_GAP` → "后面的段照样跑"必须变红 |
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent import design_cli  # noqa: E402
from dsh_embagent.design import scaffold as scaffold_module  # noqa: E402
from dsh_embagent.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from dsh_embagent.design.store import DesignStore  # noqa: E402

PROJECT = "p43-fixture"
PROJECT_B = "p43-happy"

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


def _stages(report: dict) -> dict[str, dict]:
    return {str(s.get("stage")): s for s in (report.get("stages") or [])}


# ---------------------------------------------------------------------------
# 夹具：一个**有已知 MCU** 的设计（ATtiny85 在引脚表里，能推出引脚）
# ---------------------------------------------------------------------------


def _mcu_design(project_id: str = PROJECT_B) -> DesignModel:
    d = DesignModel(
        id=project_id, title="p43 happy",
        board=Board(kind="generic", name="b",
                    outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                    size_mm={"w": 40, "h": 30}, thickness_mm=1.6),
    )
    d.add_part(Part(ref="U1", kind="mcu", label="ATtiny85",
                    size_mm={"w": 10, "h": 10, "d": 2},
                    physical_source="catalog",
                    placement=Placement(x_mm=10, y_mm=10, side="top"),
                    pins=["1", "2", "3", "4", "5", "6", "7", "8"]))
    d.add_part(Part(ref="D1", kind="led", label="LED",
                    size_mm={"w": 2, "h": 1, "d": 1},
                    physical_source="catalog",
                    placement=Placement(x_mm=25, y_mm=10, side="top"),
                    pins=["A", "K"]))
    d.connect("LED", ("U1", "5"), ("D1", "A"))
    d.connect("LED_K", ("D1", "K"), ("U1", "6"))
    return d


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def part_chain() -> None:
    store = DesignStore(PROJECT)
    try:
        got = design_cli.cmd_scaffold([
            "--project=" + PROJECT, "--route=synth", "--template=blink",
        ])
        stages = _stages(got)
        check("1a. 六段齐全（requirement/design/pinmap/joints/firmware/mechanical）",
              set(stages) >= {"requirement", "design", "pinmap", "joints",
                              "firmware", "mechanical"},
              f"stages={sorted(stages)}")
        check("1b. design 段 ok，且工程真的落盘了（design.json 在）",
              stages.get("design", {}).get("status") == "ok"
              and store.path("design.json").is_file(),
              f"design={stages.get('design')}")
        check("1b2. 缺引脚表时**不中止**：pinmap 不 ok，但 firmware 段仍然出现",
              stages.get("pinmap", {}).get("status") != "ok"
              and "firmware" in stages,
              f"pinmap={stages.get('pinmap', {}).get('status')} "
              f"firmware={stages.get('firmware', {}).get('status')}")
        check("1c. `open_items.md` 与 `scaffold.json` 都写出来了",
              store.path("open_items.md").is_file()
              and store.path("scaffold.json").is_file())
        text = store.path("open_items.md").read_text(encoding="utf-8")
        check("1d. open_items 是**人读清单**（有标题、有待办表），不是闸门",
              "待办清单" in text and "|" in text
              and "不阻塞" in text,
              f"chars={len(text)}")

        first = json.loads(store.path("scaffold.json").read_text(encoding="utf-8"))
        design_cli.cmd_scaffold(["--project=" + PROJECT, "--route=synth",
                                 "--template=blink"])
        second = json.loads(store.path("scaffold.json").read_text(encoding="utf-8"))
        check("1e. 同输入跑两次：`scaffold.json` **逐字节一致**（不带时间戳）",
              first == second,
              f"差异字段={sorted(set(first) ^ set(second)) or 'none'}")
    finally:
        store.delete()


def part_happy_path() -> None:
    store = DesignStore(PROJECT_B)
    store.save(_mcu_design())
    try:
        got = scaffold_module.build(
            project_id=PROJECT_B, store=store, design=store.load(), route="prebuilt",
        )
        stages = _stages(got)
        check("2a. 有已知 MCU → pinmap ok（从网表推，不需要 .ioc）",
              stages.get("pinmap", {}).get("status") == "ok",
              f"pinmap={stages.get('pinmap')}")
        check("2b. 引脚表写进 design.firmware.pinmap，且标 proposed",
              (store.load().firmware or {}).get("pinmap")
              and all((v or {}).get("source") == "proposed"
                      for v in (store.load().firmware or {}).get("pinmap", {}).values()),
              f"pinmap={(store.load().firmware or {}).get('pinmap')}")
        fw = stages.get("firmware", {})
        check("2c. 固件骨架落盘（ATtiny85 → avr target）",
              fw.get("status") == "ok"
              and (fw.get("counts") or {}).get("target") == "avr"
              and any((store.dir / "firmware").rglob("*.h")),
              f"firmware={fw}")
        mech = stages.get("mechanical", {})
        check("2d. 没有关节 → mechanical 如实 not_ready（静几何不是可动结构）",
              mech.get("status") == "not_ready"
              and (mech.get("counts") or {}).get("load") == "ok",
              f"mechanical={mech.get('note')}")
    finally:
        store.delete()


def part_emit_isolation() -> None:
    """`--to pcb` 走外层；发射挂掉时**前面的段必须还在**。"""
    old = os.environ.get("HWV_LCEDA")
    os.environ["HWV_LCEDA"] = str(Path("/nonexistent/lceda-pro.exe"))
    store = DesignStore(PROJECT)
    try:
        got = design_cli.cmd_scaffold([
            "--project=" + PROJECT, "--route=synth", "--template=blink", "--to=pcb",
        ])
        stages = _stages(got)
        check("3a. 没有 lceda 时 `pcb=not_ready`，但 `design` 仍 ok",
              stages.get("design", {}).get("status") == "ok"
              and stages.get("pcb", {}).get("status") != "ok",
              f"design={stages.get('design', {}).get('status')} "
              f"pcb={stages.get('pcb', {}).get('status')}")
    finally:
        store.delete()
        if old is None:
            os.environ.pop("HWV_LCEDA", None)
        else:
            os.environ["HWV_LCEDA"] = old


def part_degeneration() -> None:
    """关掉 CONTINUE_ON_GAP → "后面的段照样跑"必须变红。"""
    store = DesignStore(PROJECT)
    try:
        baseline = _stages(design_cli.cmd_scaffold([
            "--project=" + PROJECT, "--route=synth", "--template=blink"]))
        original = scaffold_module.CONTINUE_ON_GAP
        scaffold_module.CONTINUE_ON_GAP = False  # type: ignore[assignment]
        try:
            degenerated = _stages(scaffold_module.build(
                project_id=PROJECT, store=store, route="synth",
                template="blink"))
        finally:
            scaffold_module.CONTINUE_ON_GAP = original  # type: ignore[assignment]
        check("4. 退化验证：关掉 CONTINUE_ON_GAP → 缺引脚表后面的段消失（1b2 会红）",
              "firmware" in baseline and "firmware" not in degenerated,
              f"baseline={sorted(baseline)} degenerated={sorted(degenerated)}")
    finally:
        store.delete()


def main() -> int:
    print("=== P43 编排器（scaffold） ===")
    part_chain()
    part_happy_path()
    part_emit_isolation()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())