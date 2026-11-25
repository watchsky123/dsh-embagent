"""P61 验收 —— A3 块库（块 = 网表片段 + 参数 + 验证记录）。

    python -m dsh_embagent.scripts.check_p61

## 这一段补什么

`spec` 说"这里有块"但块是空的；`generate` 仍是"角色→一个型号"。
块库把 6 个常见电路做成可实例化数据：LDO / DCDC / MCU 最小系统 / USB /
CAN / 去耦阵列。

## 判据（**反着判**最要紧）

| 项 | 期望 |
|---|---|
| 结构 | 6 个块 validate 全过；每个 ≥2 零件、≥2 网 |
| 实例化 | ref 自动分配不撞；全局网不加前缀；局部网加实例前缀；引脚都在零件上 |
| 组合 | 三块组合后 GND/3V3/VBUS 各只有一条；19 个零件位号不重复 |
| 落地 | `compose --write` 进空设计后 V0 阶梯 pass（网表引用完整）|
| 未验证 | `verified_by` 为空 → 计入 unverified，**不算通过** |
| 退化 | 网表引用不存在的 key / 重复 key → validate 报错（不是静默）|
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

from dsh_embagent.design import blocks as blocks_module  # noqa: E402
from dsh_embagent.design import ladder  # noqa: E402
from dsh_embagent.design.model import Board, DesignModel  # noqa: E402
from dsh_embagent.design.store import DesignStore  # noqa: E402

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


def _design(pid: str = "p61-fixture") -> DesignModel:
    board = Board(kind="custom", name="b", id="PCB#main",
                  size_mm={"w": 60, "h": 40},
                  outline=[(0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0)])
    return DesignModel(id=pid, board=board)


def part_library() -> None:
    library = blocks_module.load_all()
    bad = {block_id: blocks_module.validate(block)
           for block_id, block in library.items()
           if blocks_module.validate(block)}
    core = {"power_ldo_3v3", "power_dcdc_5v", "mcu_support",
            "usb_device", "can_transceiver", "decoupling_bank"}
    check("1a. 原有 6 个块都在、结构全过（≥2 零件、≥2 网）",
          core <= set(library) and not bad
          and all(len(block.get("parts") or []) >= 2
                  and len(block.get("nets") or []) >= 2 for block in library.values()),
          f"blocks={len(library)} bad={bad}")

    rows = [{"block": block_id, "verified": bool(block.get("verified_by") or [])}
            for block_id, block in library.items()]
    verified = [row for row in rows if row["verified"]]
    check("1b. **没有块被标成已验证**（verified_by 全空）",
          len(verified) == 0, f"verified={len(verified)} / {len(rows)}")


def part_instantiate() -> None:
    block = blocks_module.get("power_ldo_3v3")
    got = blocks_module.instantiate(block, "PWR1", board_id="PCB#main")
    refs = [part.ref for part in got["parts"]]
    nets = {net["net"]: net for net in got["nets"]}
    pins_ok = all(pin["pin"] in part.pins
                  for net in got["nets"] for pin in net["pins"]
                  for part in got["parts"] if part.ref == pin["ref"])
    check("1c. 实例化：ref 按前缀分配且唯一（U1/C1/C2/C3）",
          refs == ["U1", "C1", "C2", "C3"], str(refs))
    check("1d. **全局网不加前缀**（VIN/VOUT/GND），引脚都在零件上，来源可追溯",
          set(nets) == {"VIN", "VOUT", "GND"} and pins_ok
          and all(any("block=power_ldo_3v3" in note for note in part.notes)
                  for part in got["parts"]),
          str({k: len(v["pins"]) for k, v in nets.items()}))


def part_compose() -> None:
    plan = [{"instance": "PWR1", "block": "power_ldo_3v3"},
            {"instance": "MCU1", "block": "mcu_support"},
            {"instance": "USB1", "block": "usb_device"}]
    result = blocks_module.compose(plan, board_id="PCB#main", max_x_mm=60.0)
    nets = {row["net"]: row for row in result["nets"]}
    refs = [part.ref for part in result["parts"]]
    check("1e. 三块组合：19 件、位号不重复、**GND/3V3/VBUS 各只有一条**",
          result["status"] == "ok" and len(refs) == 19
          and len(set(refs)) == 19
          and sum(1 for name in nets if name == "GND") == 1
          and "3V3" in nets and "VBUS" in nets,
          f"parts={len(refs)} nets={sorted(nets)}")
    check("1f. 局部网加实例前缀（USB1_USB_DP），不会和别的块假并联",
          "USB1_USB_DP" in nets and "USB_DP" not in nets,
          str([name for name in nets if name.startswith("USB")])[:120])
    all_plan = [{"instance": "PWR1", "block": "power_dcdc_5v"},
                {"instance": "PWR2", "block": "power_ldo_3v3"},
                {"instance": "MCU1", "block": "mcu_support"},
                {"instance": "USB1", "block": "usb_device"},
                {"instance": "CAN1", "block": "can_transceiver"},
                {"instance": "DE1", "block": "decoupling_bank"}]
    full = blocks_module.compose(all_plan, board_id="PCB#main", max_x_mm=60.0)
    refs_all = [part.ref for part in full["parts"]]
    check("1g0. **全部 6 块组合**：33 件 / 位号不重复 / 网表非空",
          full["status"] == "ok" and len(refs_all) == 33
          and len(set(refs_all)) == 33 and len(full["nets"]) >= 12,
          f"parts={len(refs_all)} nets={len(full['nets'])}")

    check("1g. 未验证块单独计数（verified=0 / unverified=3）",
          result["counts"]["verified_blocks"] == 0
          and result["counts"]["unverified_blocks"] == 3,
          str(result["counts"]))


def part_land_and_gate() -> None:
    design = _design()
    plan = [{"instance": "PWR1", "block": "power_ldo_3v3"},
            {"instance": "MCU1", "block": "mcu_support"}]
    result = blocks_module.compose(plan, board_id="PCB#main", max_x_mm=60.0)
    merged = blocks_module.merge_into(design, result["parts"], result["nets"])
    v0 = ladder.level_v0(design)
    check("1h. 组合进空设计 → V0 阶梯 pass（网表引用完整）",
          merged.get("ok") and v0.status == "pass" and not v0.findings,
          f"merged={merged} v0={v0.status} findings={len(v0.findings)}")

    clash = blocks_module.merge_into(design, result["parts"], [])
    check("1i. 位号冲突 → **失败**（不覆盖已有元件）",
          clash.get("ok") is False and "冲突" in str(clash.get("reason")),
          str(clash.get("reason")))

    # 退化：坏块必须被 validate 抓住
    broken = json.loads(json.dumps(blocks_module.get("can_transceiver")))
    broken["nets"][0]["pins"][0]["key"] = "nonexistent"
    dup = json.loads(json.dumps(blocks_module.get("can_transceiver")))
    dup["parts"].append(dict(dup["parts"][0]))
    check("1j. 退化：网表引用不存在的 key / 重复 part key → validate 报错",
          blocks_module.validate(broken) and blocks_module.validate(dup),
          f"{blocks_module.validate(broken)[:2]} / {blocks_module.validate(dup)[:2]}")


def part_cli() -> None:
    sandbox = Path(tempfile.mkdtemp(prefix="p61-ws-"))
    old = os.environ.get("HWV_WORKSPACE")
    os.environ["HWV_WORKSPACE"] = str(sandbox)
    try:
        from dsh_embagent import design_cli  # noqa: PLC0415
        store = DesignStore("p61-cli")
        store.delete()
        store.save(_design("p61-cli"))
        listed = design_cli.cmd_blocks(["list"])
        valid = design_cli.cmd_blocks(["validate"])
        composed = design_cli.cmd_blocks([
            "compose", "p61-cli",
            "--plan=PWR1:power_ldo_3v3,MCU1:mcu_support,USB1:usb_device",
            "--write"])
        check("1k. CLI：list≥6 / validate 全过 / compose --write 落到 design.json",
              listed["counts"]["blocks"] >= 6 and valid["status"] == "ok"
              and composed["status"] == "ok"
              and len(store.load().parts) == 19,
              f"list={listed['counts']} compose={composed.get('counts')}")
        store.delete()
    finally:
        if old is None:
            os.environ.pop("HWV_WORKSPACE", None)
        else:
            os.environ["HWV_WORKSPACE"] = old
        shutil.rmtree(sandbox, ignore_errors=True)


def main() -> int:
    print("=== P61 A3 块库（block library）===")
    part_library()
    part_instantiate()
    part_compose()
    part_land_and_gate()
    part_cli()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())