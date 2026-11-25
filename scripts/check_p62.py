"""P62 验收 —— A4 装配（spec+块→设计）+ A5 约束账本。

    python -m dsh_embagent.scripts.check_p62

## A4 判据

| 项 | 期望 |
|---|---|
| 装配 | 需求 → 23 件 / 17 网 / 4 块；**块外角色不造占位件**（sensor/i2c 进 uncovered）|
| 合法性 | 装配结果过 `validate()`；V0 阶梯 pass |
| 写入 | `--write` 只写空工程；已有元件 → 失败并说明 |
| 回归 | 旧的 `generate` 枚举路径仍然出候选、网表非空 |

## A5 判据（**反着判**最要紧）

| 项 | 期望 |
|---|---|
| 上拉 | I2C 网没有上拉电阻 → **fail**；有两颗上拉到 3V3 → pass |
| 终端 | CANH–CANL 之间没有 120Ω → **fail**；有 → pass |
| 去耦 | 电源轨上 0 颗电容且板上也没有电容 → `unknown`；有电容但不在该轨 → fail |
| 预算 | 需求 power_w 太小 → fail；没给 power_w → **unknown**（不是 pass）|
| 总状态 | 有 fail → failed；一条都求不出 → not_ready |
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import assemble_blocks as assemble_module  # noqa: E402
from dsh_embagent.design import blocks as blocks_module  # noqa: E402
from dsh_embagent.design import constraints as constraints_module  # noqa: E402
from dsh_embagent.design import generate  # noqa: E402
from dsh_embagent.design import ladder  # noqa: E402
from dsh_embagent.design import spec as spec_module  # noqa: E402
from dsh_embagent.design.model import Board, DesignModel, Part, Placement  # noqa: E402
from dsh_embagent.design.store import DesignStore  # noqa: E402

Req = generate.Requirement
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


def _req(**kw) -> object:
    base = dict(id="p62", title="t", roles=["mcu", "power_in", "sensor"],
                interfaces=[{"a": "mcu", "b": "sensor", "protocol": "i2c"},
                            {"a": "mcu", "b": "power_in", "protocol": "usb"}],
                constraints={"io_voltage_v": 3.3},
                numbers={"power_w": 30, "mass_g": 200}, notes="")
    base.update(kw)
    return Req(**base)


def _board(w: float = 60.0, h: float = 40.0) -> Board:
    return Board(kind="custom", name="b", id="PCB#main", size_mm={"w": w, "h": h},
                 outline=[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)])


def _fixture_i2c(*, pullups: bool) -> DesignModel:
    board = _board()
    design = DesignModel(id="i2c", board=board, boards=[board])
    parts = [
        Part(ref="U1", kind="mcu", size_mm={"w": 7, "h": 7, "d": 1.4},
             pins=["SCL", "SDA", "3V3", "GND"], placement=Placement(board_id="PCB#main")),
    ]
    nets = [
        {"net": "I2C1_SCL", "class": "i2c",
         "pins": [{"ref": "U1", "pin": "SCL"}]},
        {"net": "I2C1_SDA", "class": "i2c",
         "pins": [{"ref": "U1", "pin": "SDA"}]},
        {"net": "3V3", "class": "power", "pins": [{"ref": "U1", "pin": "3V3"}]},
        {"net": "GND", "class": "power", "pins": [{"ref": "U1", "pin": "GND"}]},
    ]
    if pullups:
        parts += [
            Part(ref="R1", kind="resistor", label="4k7",
                 size_mm={"w": 1.6, "h": 0.8, "d": 0.45}, pins=["1", "2"],
                 placement=Placement(board_id="PCB#main")),
            Part(ref="R2", kind="resistor", label="4k7",
                 size_mm={"w": 1.6, "h": 0.8, "d": 0.45}, pins=["1", "2"],
                 placement=Placement(board_id="PCB#main")),
        ]
        nets[0]["pins"].append({"ref": "R1", "pin": "1"})
        nets[1]["pins"].append({"ref": "R2", "pin": "1"})
        nets[2]["pins"] += [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "2"}]
    design.parts = parts
    design.nets = nets
    return design


def _fixture_can(*, terminator: bool) -> DesignModel:
    board = _board()
    design = DesignModel(id="can", board=board, boards=[board])
    design.parts = [
        Part(ref="U1", kind="tja1050", size_mm={"w": 4.9, "h": 3.9, "d": 1.75},
             pins=["TXD", "GND", "VCC", "RXD", "CANL", "CANH"],
             placement=Placement(board_id="PCB#main")),
    ] + ([Part(ref="R1", kind="resistor", label="120R",
               size_mm={"w": 3.2, "h": 1.6, "d": 0.55}, pins=["1", "2"],
               placement=Placement(board_id="PCB#main"))] if terminator else [])
    design.nets = [
        {"net": "CANH", "class": "can", "pins": [{"ref": "U1", "pin": "CANH"}]},
        {"net": "CANL", "class": "can", "pins": [{"ref": "U1", "pin": "CANL"}]},
    ]
    if terminator:
        design.nets[0]["pins"].append({"ref": "R1", "pin": "1"})
        design.nets[1]["pins"].append({"ref": "R1", "pin": "2"})
    return design


# ---------------------------------------------------------------------------
# A4
# ---------------------------------------------------------------------------


def part_assemble() -> None:
    req = _req()
    result = assemble_module.assemble(req)
    counts = result.get("counts") or {}
    uncovered = {(row.get("role") or row.get("protocol")) for row in result.get("uncovered") or []}
    check("A4-1. 装配：27 件 / 19 网 / 5 块（mcu + dcdc + ldo + sensor + usb）",
          result["status"] == "partial" and counts.get("parts") == 27
          and counts.get("nets") == 19 and counts.get("blocks") == 5,
          str(counts))
    check("A4-2. **块外角色不造占位件**：i2c 接口进 uncovered（sensor 已有块）",
          "i2c" in uncovered,
          str(result.get("uncovered")))
    design = result["design"]
    check("A4-3. 装配结果过 validate()，V0 阶梯 pass",
          not design.validate() and ladder.level_v0(design).status == "pass",
          f"problems={design.validate()[:3]}")

    no_usb = _req(interfaces=[{"a": "mcu", "b": "sensor", "protocol": "i2c"}])
    smaller = assemble_module.assemble(no_usb)
    check("A4-4. **件数随块增长**：去掉 usb 接口 → 22 件（mcu+dcdc+ldo+sensor）",
          (smaller.get("counts") or {}).get("parts") == 22,
          str((smaller.get("counts") or {}).get("parts")))

    legacy_req = _req(roles=["mcu", "sensor"],
                      interfaces=[{"a": "mcu", "b": "sensor", "protocol": "i2c"}])
    legacy = generate.generate(legacy_req, max_candidates=2, run_ladder=False)
    cands = legacy.get("candidates") or []
    materialized = generate.materialize(cands[0], legacy_req) if cands else None
    check("A4-5. 回归：旧 `generate` 枚举路径仍出候选、materialize 出网表",
          bool(cands) and bool(cands[0].get("links"))
          and materialized is not None and len(materialized.nets) > 0,
          f"candidates={len(cands)} nets={len(materialized.nets) if materialized else 0}")


def part_assemble_cli() -> None:
    sandbox = Path(tempfile.mkdtemp(prefix="p62-ws-"))
    old = os.environ.get("HWV_WORKSPACE")
    os.environ["HWV_WORKSPACE"] = str(sandbox)
    try:
        from dsh_embagent import design_cli  # noqa: PLC0415
        store = DesignStore("p62-cli")
        store.delete()
        store.save(DesignModel(id="p62-cli", board=_board()))
        store.write("req.json", {"id": "p62-cli", "title": "t",
                                 "roles": ["mcu", "power_in"],
                                 "interfaces": [{"a": "mcu", "b": "power_in",
                                                 "protocol": "usb"}],
                                 "constraints": {}, "numbers": {"power_w": 30},
                                 "notes": ""})
        first = design_cli.cmd_assemble(["p62-cli", "--write"])
        second = design_cli.cmd_assemble(["p62-cli", "--write"])
        check("A4-6. `--write` 写进空工程；**再写一次被拒**（不覆盖已有元件）",
              first["status"] in ("ok", "partial") and first.get("written") is True
              and second["status"] == "failed" and "已有元件" in str(second.get("reason")),
              f"first={first.get('counts')} second={second.get('reason')}")
        store.delete()
    finally:
        if old is None:
            os.environ.pop("HWV_WORKSPACE", None)
        else:
            os.environ["HWV_WORKSPACE"] = old
        shutil.rmtree(sandbox, ignore_errors=True)


# ---------------------------------------------------------------------------
# A5
# ---------------------------------------------------------------------------


def part_constraints() -> None:
    result = assemble_module.assemble(_req())
    design = result["design"]
    # 用没有对应网表的接口（uart）验 unknown；i2c 现在有 sensor 块
    uart_req = _req(interfaces=[{"a": "mcu", "b": "radio", "protocol": "uart"}])
    spec = spec_module.build(uart_req)
    payload = constraints_module.derive(design, spec=spec, req=uart_req)
    counts = payload.get("counts") or {}
    check("A5-1. 约束账本：规则 ≥ 网表数，求值覆盖去耦/预算/接口",
          counts.get("rules", 0) >= len(design.nets)
          and counts.get("evaluated", 0) >= 5 and payload["status"] == "ok",
          str(counts))
    check("A5-2. 接口未知：uart 契约在 spec 里、网表里没有 → **unknown 不是 pass**",
          any(row["id"].startswith("iface.") and row["status"] == "unknown"
              for row in payload["results"]),
          str([row["id"] for row in payload["results"] if row["status"] == "unknown"]))

    no_pull = constraints_module.derive(_fixture_i2c(pullups=False))
    with_pull = constraints_module.derive(_fixture_i2c(pullups=True))
    check("A5-3. I2C 上拉：没有上拉电阻 → **fail**；有两颗 → pass",
          no_pull["status"] == "failed"
          and any(row["id"].startswith("i2c.pullup") and row["status"] == "fail"
                  for row in no_pull["results"])
          and all(row["status"] == "pass" for row in with_pull["results"]
                  if row["id"].startswith("i2c.pullup")),
          f"no_pull={no_pull['counts']} with_pull={with_pull['counts']}")

    no_term = constraints_module.derive(_fixture_can(terminator=False))
    with_term = constraints_module.derive(_fixture_can(terminator=True))
    check("A5-4. CAN 终端：没有 120Ω → **fail**；有 → pass",
          no_term["status"] == "failed"
          and with_term["status"] == "ok"
          and any(row["id"] == "can.termination" and row["status"] == "pass"
                  for row in with_term["results"]),
          f"no_term={no_term['status']} with_term={with_term['status']}")

    cap_elsewhere = _fixture_i2c(pullups=True)
    cap_elsewhere.parts.append(Part(
        ref="C9", kind="capacitor", label="100nF", size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
        pins=["1", "2"], placement=Placement(board_id="PCB#main")))
    cap_elsewhere.nets.append({"net": "3V3_AUX", "class": "power",
                               "pins": [{"ref": "C9", "pin": "1"}]})
    decoup = constraints_module.derive(cap_elsewhere)
    check("A5-5. 去耦：3V3 上没电容但板上有电容 → **fail**（不是 unknown）",
          any(row["id"] == "decoupling.3V3" and row["status"] == "fail"
              for row in decoup["results"]),
          str([row for row in decoup["results"] if row["id"].startswith("decoupling")]))

    tiny = constraints_module.derive(design, spec=spec,
                                    req=_req(numbers={"power_w": 1}))
    # "没给 power_w"要用不含它的 spec（A5 修正后 spec.constraints 也算来源）
    spec_no_power = spec_module.build(_req(numbers={"mass_g": 200},
                                           constraints={"io_voltage_v": 3.3}))
    no_num = constraints_module.derive(design, spec=spec_no_power,
                                       req=_req(numbers={},
                                                constraints={"io_voltage_v": 3.3}))
    budget_tiny = next(row for row in tiny["results"] if row["id"] == "budget.power")
    budget_none = next(row for row in no_num["results"] if row["id"] == "budget.power")
    check("A5-6. 预算：power_w 太小 → **fail**；没给 → **unknown**（不是 pass）",
          tiny["status"] == "failed" and budget_tiny["status"] == "fail"
          and budget_none["status"] == "unknown",
          f"tiny={budget_tiny['detail']} none={budget_none['status']}")

    empty = constraints_module.derive(DesignModel(id="e", board=_board()))
    check("A5-7. 一条都求不出值 → **not_ready**（空设计不是通过）",
          empty["status"] == "not_ready", str(empty["counts"]))


def main() -> int:
    print("=== P62 A4 装配 + A5 约束账本 ===")
    part_assemble()
    part_assemble_cli()
    part_constraints()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())