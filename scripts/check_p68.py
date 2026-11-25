"""P68 验收 —— 块引脚用符号名 + 预检查引脚是否存在。

    python -m dsh_embagent.scripts.check_p68

## 背景（实测）

第一版块里连接器写的是逻辑名（`GND/VCC/PWM`），而立创符号是**数字焊盘**
（`1/2/3`）。结果原理图有 27 个 `wire:pin_not_found`，PCB parity 缺 27。

## 判据

| 项 | 期望 |
|---|---|
| `pin_map` | 块里逻辑名 → 数字；实例化后 Part.pins 与网表都用数字 |
| 表来源 | `symbol_pins.json` 来自**真实 PCB 网表**，不是手编 |
| 预检 | 设计引脚不在表里 → `not_ready`（挡在 emit 前）|
| 预检 | 编号没有表项 → `unverified_pins`（warning，不假装知道）|
| 预检 | 全对 → `ok`，`pin_missing=0` |
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import blocks as blocks_module  # noqa: E402
from dsh_embagent.design import emit_preflight  # noqa: E402
from dsh_embagent.design.model import Board, DesignModel, Part, Placement  # noqa: E402

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


def _design(pins: list[str], lcsc: str = "C1") -> DesignModel:
    board = Board(kind="custom", id="PCB#main", size_mm={"w": 30, "h": 20},
                  outline=[(0.0, 0.0), (30.0, 0.0), (30.0, 20.0), (0.0, 20.0)])
    design = DesignModel(id="p68", board=board, boards=[board])
    design.parts = [Part(ref="U1", kind="mcu", physical_source="datasheet",
                         size_mm={"w": 5, "h": 5, "d": 1}, pins=list(pins),
                         lcsc=lcsc,
                         placement=Placement(x_mm=5, y_mm=5, board_id="PCB#main"))]
    design.nets = [{"net": "3V3", "class": "power",
                    "pins": [{"ref": "U1", "pin": pins[0]}]}]
    return design


def _rows(_codes) -> list[dict]:
    return [{"lcsc": "C1", "ok": True, "footprint": True, "name": "C1"}]


def part_pin_map() -> None:
    got = blocks_module.instantiate(blocks_module.get("sensor_i2c"), "S1",
                                    max_x_mm=60, max_y_mm=40)
    header = next(p for p in got["parts"] if p.kind.startswith("header"))
    check("A1. `pin_map`：实例化后连接器引脚是**数字焊盘**",
          header.pins == ["1", "2", "3", "4"],
          str(header.pins))

    scl = next(n for n in got["nets"] if n["net"].endswith("I2C_SCL"))
    pin = next(p for p in scl["pins"] if p["ref"] == header.ref)["pin"]
    check("A2. 网表也映射了（SCL 逻辑名 → 焊盘 3）",
          pin == "3", f"{scl['net']} -> {pin}")
    check("A3. 块结构校验仍用**逻辑层**（pin_map 不破坏 validate）",
          not blocks_module.validate(blocks_module.get("sensor_i2c")))


def part_table() -> None:
    table = emit_preflight.load_pin_table()
    check("B1. 引脚表来自真实网表：23 个编号、全数字名",
          len(table) >= 22 and all(
              all(str(x).isdigit() for x in names)
              for names in table.values()),
          f"codes={len(table)}")
    check("B2. C9135（1x4/2x4 排针）有 8 个焊盘",
          len(table.get("C9135") or []) == 8, str(table.get("C9135")))


def part_preflight() -> None:
    bad = emit_preflight.check(_design(["1", "2"]), lookup=_rows,
                               pin_table={"C1": ["1"]})
    check("C1. 设计引脚不在符号里 → **not_ready**（挡在 emit 前）",
          bad["status"] == "not_ready"
          and bad["pin_missing"] and bad["pin_missing"][0]["missing"] == ["2"],
          str(bad.get("pin_missing")))

    good = emit_preflight.check(_design(["1"]), lookup=_rows,
                                pin_table={"C1": ["1", "2"]})
    check("C2. 引脚都在表里 → ok，pin_missing=0",
          good["status"] == "ok" and good["counts"]["pin_missing"] == 0,
          good.get("reason"))

    unknown = emit_preflight.check(_design(["1"]), lookup=_rows,
                                   pin_table={})
    check("C3. 编号没有表项 → `unverified_pins`（warning，不假装知道）",
          unknown["status"] == "ok"
          and unknown["counts"]["unverified_pins"] == 1,
          str(unknown.get("unverified_pins")))


def main() -> int:
    print("=== P68 块引脚符号名 + 预检引脚存在性 ===")
    part_pin_map()
    part_table()
    part_preflight()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())