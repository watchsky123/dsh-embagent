"""P63 验收 —— B 档：选型字段 / 网络类 / 块实例 / 变更图 / 引脚分配。

    python -m dsh_embagent.scripts.check_p63

## B 档五件事

| 编号 | 内容 | 关键判据 |
|---|---|---|
| B1 | `Part` 加 mpn/footprint/alternates/cost/supply/derating | 往返不丢；空字段不写；非法值报错 |
| B2 | `Net` 的 class/constraints 进 validate | 未知 class 报错；重复引脚报错 |
| B3 | `design.blocks_list`（层次元数据）| assemble 写入；引用存在；一个件只属一个块 |
| B4 | 变更图 `graph` | 四种节点；impact 两跳；目标不存在 → not_ready |
| B5 | 引脚分配器 `pin-assign` | 按 AF 表；不重复用脚；分不出来进 unassigned |
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import assemble_blocks as assemble_module  # noqa: E402
from dsh_embagent.design import graph as graph_module  # noqa: E402
from dsh_embagent.design import pin_assign as pin_module  # noqa: E402
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


def _req(**kw):
    from dsh_embagent.design import generate  # noqa: PLC0415
    base = dict(id="p63", title="t", roles=["mcu", "power_in"], interfaces=[],
                constraints={}, numbers={"power_w": 30}, notes="")
    base.update(kw)
    return generate.Requirement(**base)


def _design(pid: str = "p63") -> DesignModel:
    board = Board(kind="custom", name="b", id="PCB#main", size_mm={"w": 60, "h": 40},
                  outline=[(0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0)])
    return DesignModel(id=pid, board=board, boards=[board])


# ---------------------------------------------------------------------------
# B1 选型字段
# ---------------------------------------------------------------------------


def part_selection() -> None:
    full = Part(ref="U1", kind="stm32f103c8t6", size_mm={"w": 7, "h": 7, "d": 1.4},
                pins=["1"], mpn="STM32F103C8T6", footprint="LQFP-48",
                alternates=[{"mpn": "GD32F103C8", "note": "pin2pin"}],
                cost={"unit_cny": 8.5, "qty": 1},
                supply={"stock": 12000, "lead_time_days": 3},
                derating={"voltage_pct": 0.8, "current_pct": 0.7})
    data = full.to_dict()
    back = Part.from_dict(data)
    check("B1-1. 六个选型字段**往返不丢**（mpn/封装/替代料/成本/供货/降额）",
          back.mpn == full.mpn and back.footprint == full.footprint
          and back.alternates == full.alternates and back.cost == full.cost
          and back.supply == full.supply and back.derating == full.derating,
          json.dumps({k: data.get(k) for k in ("mpn", "footprint", "cost")},
                     ensure_ascii=False))

    bare = Part(ref="R1", kind="resistor", size_mm={"w": 1.6, "h": 0.8, "d": 0.45})
    check("B1-2. 空字段**不写进 design.json**（旧文件往返不变）",
          not any(key in bare.to_dict() for key in
                  ("mpn", "footprint", "alternates", "cost", "supply", "derating")),
          sorted(bare.to_dict()))

    bad = _design()
    bad.parts = [Part(ref="U2", kind="mcu", size_mm={"w": 7, "h": 7, "d": 1.4},
                      pins=["1"], cost={"unit_cny": -1},
                      derating={"voltage_pct": 1.5})]
    problems = bad.validate()
    check("B1-3. 非法值报错：负成本 / 降额 1.5（必须在 (0,1]）",
          any("cost.unit_cny" in p for p in problems)
          and any("derating.voltage_pct" in p for p in problems),
          str(problems[:3]))


# ---------------------------------------------------------------------------
# B2 网络类
# ---------------------------------------------------------------------------


def part_net_classes() -> None:
    good = _design()
    good.parts = [Part(ref="U1", kind="mcu", size_mm={"w": 7, "h": 7, "d": 1.4},
                       pins=["1", "2"])]
    good.nets = [{"net": "3V3", "class": "power", "constraints": ["去耦 100nF"],
                  "pins": [{"ref": "U1", "pin": "1"}]},
                 {"net": "I2C1_SCL", "class": "i2c",
                  "pins": [{"ref": "U1", "pin": "2"}]}]
    check("B2-1. 合法 class（power/i2c）与 constraints 数组 → 无问题",
          not [p for p in good.validate() if "class" in p or "constraints" in p],
          str(good.validate()[:2]))

    bad = _design()
    bad.parts = [Part(ref="U1", kind="mcu", size_mm={"w": 7, "h": 7, "d": 1.4},
                      pins=["1", "2"])]
    bad.nets = [
        {"net": "X", "class": "wat", "pins": [{"ref": "U1", "pin": "1"}]},
        {"net": "Y", "constraints": "去耦", "pins": [{"ref": "U1", "pin": "2"}]},
        {"net": "Z", "pins": [{"ref": "U1", "pin": "2"}, {"ref": "U1", "pin": "2"}]},
    ]
    problems = bad.validate()
    check("B2-2. 未知 class / constraints 不是数组 / 同一网重复引脚 → 三条都报",
          any("未知 class" in p for p in problems)
          and any("constraints 必须是数组" in p for p in problems)
          and any("引脚重复" in p for p in problems),
          str(problems[:4]))


# ---------------------------------------------------------------------------
# B3 块实例
# ---------------------------------------------------------------------------


def part_blocks_list() -> None:
    result = assemble_module.assemble(_req())
    design = result["design"]
    rows = design.blocks_list
    check("B3-1. assemble 写入 blocks_list（3 块：MCU/PWR_DCDC/PWR_LDO）",
          len(rows) == 3 and {row["block"] for row in rows}
          == {"mcu_support", "power_dcdc_5v", "power_ldo_3v3"},
          str([(row["instance"], row["block"], len(row["parts"])) for row in rows]))
    data = design.to_dict()
    back = DesignModel.from_dict(data)
    check("B3-2. blocks_list 往返不丢；validate 通过（引用都存在）",
          back.blocks_list == design.blocks_list and not design.validate(),
          f"blocks={len(back.blocks_list)}")

    bad = _design()
    bad.parts = [Part(ref="U1", kind="mcu", size_mm={"w": 7, "h": 7, "d": 1.4},
                      pins=["1"])]
    bad.blocks_list = [
        {"instance": "A", "parts": ["U1", "U9"]},
        {"instance": "B", "parts": ["U1"]},
    ]
    problems = bad.validate()
    check("B3-3. 块引用不存在的件 / 同一件属两个块 → 报错",
          any("不存在的元件 U9" in p for p in problems)
          and any("同时属于块" in p for p in problems),
          str(problems[:3]))


# ---------------------------------------------------------------------------
# B4 变更图
# ---------------------------------------------------------------------------


def part_graph() -> None:
    design = assemble_module.assemble(_req())["design"]
    design.firmware = {"pinmap": {"3V3": {"pin": "PA9", "function": "power"}}}
    graph = graph_module.build(design)
    kinds = set(graph["counts"]) - {"edges"}
    check("B4-1. 四种节点都在（part/net/block/firmware）",
          {"part", "net", "block", "firmware"} <= kinds,
          str(graph["counts"]))
    edge_kinds = {edge["kind"] for edge in graph["edges"]}
    check("B4-2. 边覆盖 pin / block_part / block_net / firmware",
          {"pin", "block_part", "block_net", "firmware"} <= edge_kinds,
          str(sorted(edge_kinds)))

    hit = graph_module.impact(design, ["U1"])
    check("B4-3. impact(U1)：两跳内受影响零件/网/块/固件都列出来",
          hit["status"] == "ok" and hit["counts"]["parts"] > 0
          and hit["counts"]["nets"] > 0 and hit["counts"]["blocks"] > 0
          and hit["counts"]["firmware"] > 0,
          str(hit["counts"]))

    miss = graph_module.impact(design, ["NOT_A_REF"])
    check("B4-4. 目标不存在 → **not_ready**（不是“没有影响”）",
          miss["status"] == "not_ready" and "不存在" in str(miss.get("reason")),
          str(miss.get("reason")))


# ---------------------------------------------------------------------------
# B5 引脚分配器
# ---------------------------------------------------------------------------


def part_pin_assign() -> None:
    needs = ["USART1_TX", "USART1_RX", "I2C1_SCL", "I2C1_SDA"]
    got = pin_module.assign("stm32f103c8t6", needs)
    pins = list(got["assignment"].values())
    functions_ok = all(
        any(row.get("signal") == need for row in
            (pin_module.afmap.find("stm32f103c8t6")["pins"][pin]["functions"]))
        for need, pin in got["assignment"].items())
    check("B5-1. 4 个功能都分到脚、**互不重用**、功能名与 AF 表一致",
          got["status"] == "ok" and len(set(pins)) == 4 and functions_ok,
          str(got["assignment"]))

    blocked = pin_module.assign("stm32f103c8t6", needs,
                                avoid={"PA9"}, used={"PB6", "PB7"})
    check("B5-2. 候选被 avoid/used 占完 → 进 unassigned（不硬塞），其余照分",
          blocked["status"] == "partial"
          and [row["need"] for row in blocked["unassigned"]] == ["USART1_TX"]
          and len(blocked["assignment"]) == 3
          and len(set(blocked["assignment"].values())) == 3,
          str(blocked["unassigned"]))

    fallback = pin_module.assign_protocol("stm32f103c8t6", "uart",
                                          avoid={"PA9", "PB6"})
    check("B5-3. USART1 分不出来 → **换组**（USART2）",
          fallback["status"] == "ok" and fallback.get("group") == "USART2",
          str(fallback.get("assignment")))

    impossible = pin_module.assign_protocol(
        "stm32f103c8t6", "uart",
        avoid={"PA9", "PB6", "PA2", "PA3", "PB10", "PB11"})
    check("B5-4. 所有组都被 avoid → **not_ready** 并写原因",
          impossible["status"] == "not_ready" and impossible.get("reason"),
          str(impossible.get("reason")))

    none = pin_module.assign("esp32s3", ["I2C1_SCL"])
    check("B5-5. 没有 AF 表的型号 → not_ready（不猜）",
          none["status"] == "not_ready", str(none.get("reason")))


def main() -> int:
    print("=== P63 B 档：选型字段 / 网络类 / 块实例 / 变更图 / 引脚分配 ===")
    part_selection()
    part_net_classes()
    part_blocks_list()
    part_graph()
    part_pin_assign()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())