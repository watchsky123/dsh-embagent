"""P65 验收 —— 块布局按尺寸排 + 预算读 spec.constraints。

    python -m dsh_embagent.scripts.check_p65

## 两个修正

| 问题 | 之前 | 现在 |
|---|---|---|
| 块布局 | 固定 6 mm 栅格 → 7×7 的 MCU 与晶振/排针大面积重叠（实测 77 处）| 按零件实际 `w/h` 做行式排布；**跨块全局排一次**；放不下时板框按内容扩 |
| 功耗上限 | 只读 `req.numbers.power_w` | 依次读 `req.numbers` → `req.constraints` → `spec.constraints` |

## 判据

* 组合后**任意两件不重叠**；元件都在板内（`validate` 通过）
* 板框不够时**自动扩**并如实标 `board_auto_sized`（不是静默越界）
* 预算上限从三个来源都能拿到，并注明 `limit_from`
* 三处都没有 → `unknown`（不是 pass）
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import assemble_blocks as assemble_module  # noqa: E402
from dsh_embagent.design import blocks as blocks_module  # noqa: E402
from dsh_embagent.design import constraints as constraints_module  # noqa: E402
from dsh_embagent.design import generate  # noqa: E402
from dsh_embagent.design import spec as spec_module  # noqa: E402

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


Req = generate.Requirement
PLAN = [{"instance": "PWR1", "block": "power_dcdc_5v"},
        {"instance": "PWR2", "block": "power_ldo_3v3"},
        {"instance": "MCU1", "block": "mcu_support"},
        {"instance": "USB1", "block": "usb_device"},
        {"instance": "CAN1", "block": "can_transceiver"},
        {"instance": "DE1", "block": "decoupling_bank"}]


def _overlaps(parts: list) -> list[tuple[str, str]]:
    bad: list[tuple[str, str]] = []
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, c = parts[i].footprint_mm(), parts[j].footprint_mm()
            if (min(a[2], c[2]) - max(a[0], c[0]) > 0.01
                    and min(a[3], c[3]) - max(a[1], c[1]) > 0.01):
                bad.append((parts[i].ref, parts[j].ref))
    return bad


def part_layout() -> None:
    composed = blocks_module.compose(PLAN, max_x_mm=60.0, max_y_mm=40.0)
    parts = composed["parts"]
    bad = _overlaps(parts)
    check("A1. 6 块组合：33 件**两两不重叠**",
          len(parts) == 33 and not bad,
          f"parts={len(parts)} overlaps={bad[:4]}")

    # 板框不够时 assemble 自动扩，扩完必须过 validate
    req = Req(id="p65", title="t",
              roles=["mcu", "power_in", "radio", "sensor", "camera", "motor"],
              interfaces=[{"a": "mcu", "b": "radio", "protocol": "can"}],
              constraints={"io_voltage_v": 3.3}, numbers={}, notes="")
    result = assemble_module.assemble(req, board_size=(60.0, 40.0))
    counts = result.get("counts") or {}
    design = result.get("design")
    check("A2. 放不下时**板框按内容扩**，扩完过 validate",
          result.get("status") in ("ok", "partial")
          and counts.get("board_auto_sized") is True
          and counts.get("board_mm", {}).get("h", 0) > 40
          and not design.validate(),
          f"board={counts.get('board_mm')} fits={counts.get('layout_fits')} "
          f"problems={design.validate()[:2] if design else None}")

    one = blocks_module.instantiate(blocks_module.get("power_ldo_3v3"), "P1",
                                    max_x_mm=60.0, max_y_mm=40.0)
    check("A3. 单块实例化不重叠（回归：不是只有组合才修）",
          len(one["parts"]) == 4 and not _overlaps(one["parts"]),
          str([(p.ref, p.footprint_mm()) for p in one["parts"]][:2]))


def _budget(req, spec=None) -> dict:
    payload = constraints_module.derive(_small_design(), spec=spec, req=req)
    return next(row for row in payload["results"] if row["id"] == "budget.power")


def _small_design():
    from dsh_embagent.design.model import Board, DesignModel, Part, Placement  # noqa: PLC0415
    board = Board(kind="custom", id="PCB#main", size_mm={"w": 40, "h": 30},
                  outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)])
    design = DesignModel(id="p65", board=board, boards=[board])
    design.parts = [Part(ref="U1", kind="mcu", physical_source="datasheet",
                         size_mm={"w": 7, "h": 7, "d": 1.4}, pins=["1"],
                         placement=Placement(x_mm=5, y_mm=5, board_id="PCB#main"))]
    design.nets = [{"net": "3V3", "class": "power",
                    "pins": [{"ref": "U1", "pin": "1"}]}]
    return design


def part_budget() -> None:
    spec = spec_module.build(Req(
        id="p65", title="t", roles=["mcu", "power_in"], interfaces=[],
        constraints={"power_w": 30}, numbers={}, notes=""))
    high = _budget(Req(id="p65", title="t", roles=["mcu"], interfaces=[],
                       constraints={"power_w": 1000}, numbers={}, notes=""),
                   spec=spec)
    low = _budget(Req(id="p65", title="t", roles=["mcu"], interfaces=[],
                      constraints={"power_w": 1}, numbers={}, notes=""),
                  spec=spec)
    check("B1. 上限只在 `req.constraints` 里也能读到（limit_from 标注）",
          high["status"] == "pass" and low["status"] == "fail"
          and high["checked"].get("limit_from") == "req.constraints",
          f"{high['detail']} / {low['detail']}")

    from_spec = _budget(Req(id="p65", title="t", roles=["mcu"], interfaces=[],
                            constraints={}, numbers={}, notes=""), spec=spec)
    check("B2. 上限只在 `spec.constraints` 里也能读到",
          from_spec["checked"].get("limit_from") == "spec.constraints"
          and from_spec["status"] in ("pass", "fail"),
          str(from_spec["checked"]))

    # "三处都没有"必须用**不含 power_w** 的 spec（否则 spec.constraints 兜住了）
    spec_none = spec_module.build(Req(
        id="p65", title="t", roles=["mcu", "power_in"], interfaces=[],
        constraints={"io_voltage_v": 3.3}, numbers={"mass_g": 100}, notes=""))
    none = _budget(Req(id="p65", title="t", roles=["mcu"], interfaces=[],
                       constraints={}, numbers={}, notes=""), spec=spec_none)
    check("B3. 三处都没有 → **unknown**（不是 pass）",
          none["status"] == "unknown", none["detail"])


def main() -> int:
    print("=== P65 块布局 + 预算上限 ===")
    part_layout()
    part_budget()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())