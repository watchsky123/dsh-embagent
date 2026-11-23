"""P60 验收 —— A1 规格层 + A2 总线级网表。

    python -m hw_velxio.scripts.check_p60

## 这一段补什么

| 层 | 之前 | 现在 |
|---|---|---|
| 需求之后 | 直接出元件 | **`spec/vN.json`**：块 / 接口契约 / 约束 / 覆盖率 |
| 链路 | 每条 1 个引脚（`SPI_1`）| **总线级**：`SPI1_SCK/MOSI/MISO/NSS`、I2C 2 根、UART 交叉 |
| 引脚 | 按能力表顺序挑 | 按 **AF 表**挑（这个脚真的能做这个功能）|

## 判据（**反着判**最要紧）

| 项 | 期望 |
|---|---|
| 块覆盖 | 每个角色落一个块；没有模板 → 占位块 `source=proposed` + `missing`（不静默顶替）|
| 契约 | 每条接口有 `pins/voltage/direction/bus/rules`；缺电平写 null 并进 unknown |
| 版本 | 同需求复用同一版；需求变了才 v(N+1)（实测 v1 → v1 → v2）|
| 总线 | SPI 4 根、I2C 2 根；**每个脚的功能名 == 网名**（AF 表核对）|
| 交叉 | UART 必须 TX↔RX，不是 TX↔TX |
| 退化 | 没有 AF 表 → **退回单脚示意网并标 `bus_partial`**，不硬凑 |
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

from hw_velxio.design import afmap  # noqa: E402
from hw_velxio.design import bus as bus_module  # noqa: E402
from hw_velxio.design import capability  # noqa: E402
from hw_velxio.design import generate  # noqa: E402
from hw_velxio.design import spec as spec_module  # noqa: E402
from hw_velxio.design.store import DesignStore  # noqa: E402

PROJECT = "p60-fixture"
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
CAP = capability.CAPABILITY["stm32f103c8t6"]


def _req(**kw) -> object:
    base = dict(id="t", title="t", roles=["mcu", "sensor", "radio", "power_in"],
                interfaces=[{"a": "mcu", "b": "sensor", "protocol": "i2c"},
                            {"a": "mcu", "b": "radio", "protocol": "uart"},
                            {"a": "mcu", "b": "sensor", "protocol": "spi"}],
                constraints={"io_voltage_v": 3.3, "power_w": 5},
                numbers={"mass_g": 250, "endurance_min": 12}, notes="")
    base.update(kw)
    return Req(**base)


# ---------------------------------------------------------------------------
# A1 规格层
# ---------------------------------------------------------------------------


def part_spec() -> None:
    payload = spec_module.build(_req())
    cov = payload.get("coverage") or {}
    check("A1-1. 每个角色落成一个块（4/4），每个接口都有契约（3/3）",
          payload["status"] == "ok" and cov.get("roles_mapped") == 4
          and cov.get("interfaces_mapped") == 3,
          f"{cov.get('roles_mapped')}/{cov.get('roles_total')} 块，"
          f"{cov.get('interfaces_mapped')}/{cov.get('interfaces_total')} 契约")

    spi = next(c for c in payload["interfaces"] if c["protocol"] == "spi")
    check("A1-2. 契约写清**总线角色**（SPI=SCK/MOSI/MISO/NSS）与电平/方向",
          spi.get("pins") == ["SCK", "MOSI", "MISO", "NSS"]
          and spi.get("voltage") == 3.3 and spi.get("direction") == "master_slave"
          and len(spi.get("rules") or []) >= 1,
          str({k: spi.get(k) for k in ("pins", "voltage", "direction")}))

    bad = spec_module.build(_req(roles=["mcu", "quantum_imu"]))
    blocked = [b for b in bad["blocks"] if b["role"] == "quantum_imu"]
    check("A1-3. 没有模板的角色 → 占位块 `proposed` + `missing`（不静默顶替）",
          bad["status"] != "ok" and blocked and blocked[0]["source"] == "proposed"
          and any("quantum_imu" in (row.get("what") or "")
                  for row in (bad.get("coverage") or {}).get("missing") or []),
          f"status={bad['status']} missing={len((bad.get('coverage') or {}).get('missing') or [])}")

    thin = spec_module.build(_req(constraints={"io_voltage_v": 3.3}, numbers={}))
    needs = {row.get("what") for row in thin.get("unknown") or []}
    check("A1-4. 需求没给 power_w/mass_g → 进 `unknown`（**不填默认值**）",
          {"power_w", "mass_g"} <= needs and thin["status"] == "ok",
          str(sorted(needs)))

    markdown = spec_module.render(payload)
    check("A1-5. `spec.md` 人读版：块 / 契约 / 约束 / unknown 都在",
          "## 子系统（块）" in markdown and "## 接口契约" in markdown
          and "## 约束（需求给的数）" in markdown
          and "## 还不知道的" in markdown,
          str([line for line in markdown.splitlines() if line.startswith("## ")]))

    # 版本：同需求复用，变了才 +1（走 CLI，用临时工作区）
    sandbox = Path(tempfile.mkdtemp(prefix="p60-ws-"))
    old = os.environ.get("HWV_WORKSPACE")
    os.environ["HWV_WORKSPACE"] = str(sandbox)
    try:
        from hw_velxio import design_cli  # noqa: PLC0415
        store = DesignStore("spec_cli")
        store.delete()
        store.save(_empty_design())
        store.write("req.json", json.loads(json.dumps(
            {"id": "spec_cli", "title": "t", "roles": ["mcu", "sensor"],
             "interfaces": [{"a": "mcu", "b": "sensor", "protocol": "i2c"}],
             "constraints": {"power_w": 4}, "numbers": {}, "notes": ""})))
        v1 = design_cli.cmd_spec(["spec_cli", "--build"]).get("version")
        again = design_cli.cmd_spec(["spec_cli", "--build"]).get("version")
        store.write("req.json", {"id": "spec_cli", "title": "t",
                                 "roles": ["mcu", "sensor"],
                                 "interfaces": [{"a": "mcu", "b": "sensor",
                                                 "protocol": "i2c"}],
                                 "constraints": {"power_w": 6},
                                 "numbers": {}, "notes": ""})
        v2 = design_cli.cmd_spec(["spec_cli", "--build"]).get("version")
        check("A1-6. 版本：同需求复用 v1，需求变了才 v2",
              v1 == 1 and again == 1 and v2 == 2, f"{v1} → {again} → {v2}")
        files = sorted(p.name for p in (store.path("spec")).glob("v*.json"))
        check("A1-7. 落盘 `spec/v1.json`…`v2.json` + `spec/index.json` + `spec.md`",
              files == ["v1.json", "v2.json"]
              and store.path("spec/index.json").is_file()
              and store.path("spec.md").is_file(), str(files))
        store.delete()
    finally:
        if old is None:
            os.environ.pop("HWV_WORKSPACE", None)
        else:
            os.environ["HWV_WORKSPACE"] = old
        shutil.rmtree(sandbox, ignore_errors=True)


def _empty_design():
    from hw_velxio.design.model import Board, DesignModel  # noqa: PLC0415
    board = Board(kind="custom", name="b", id="PCB#main",
                  size_mm={"w": 40, "h": 30},
                  outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)])
    return DesignModel(id="spec_cli", board=board)


# ---------------------------------------------------------------------------
# A2 总线级网表
# ---------------------------------------------------------------------------


def _pin_function(pin: str, want: str) -> bool:
    table = afmap.load("stm32f103c8t6") or {}
    return any(str(row.get("signal") or "") == want
               for row in (table.get("pins", {}).get(pin, {}).get("functions") or []))


def part_bus() -> None:
    spi = bus_module.expand("spi", "U1", "U2", model_a="stm32f103c8t6",
                            model_b="stm32f103c8t6", cap_a=CAP, cap_b=CAP)
    nets = {row["net"]: row for row in (spi or {}).get("nets") or []}
    check("A2-1. SPI → **4 根**（SCK/MOSI/MISO/NSS），且每个脚的功能名与网名一致",
          set(nets) == {"SPI1_SCK", "SPI1_MOSI", "SPI1_MISO", "SPI1_NSS"}
          and all(_pin_function(nets[name]["pins"][0]["pin"], name)
                  for name in nets),
          str({k: v["pins"][0]["pin"] for k, v in nets.items()}))

    i2c = bus_module.expand("i2c", "U1", "U2", model_a="stm32f103c8t6",
                            model_b="stm32f103c8t6", cap_a=CAP, cap_b=CAP)
    rows = {row["net"]: row for row in (i2c or {}).get("nets") or []}
    check("A2-2. I2C → 2 根，带 class 与规则（上拉/电容）",
          set(rows) == {"I2C1_SCL", "I2C1_SDA"}
          and all(r.get("class") == "i2c" and r.get("rules") for r in rows.values()),
          str([(k, v.get("class"), len(v.get("rules") or [])) for k, v in rows.items()]))

    uart = bus_module.expand("uart", "U1", "U2", model_a="stm32f103c8t6",
                             model_b="stm32f103c8t6", cap_a=CAP, cap_b=CAP)
    tx = next(row for row in (uart or {}).get("nets") or [] if row["net"] == "USART1_TX")
    check("A2-3. UART **交叉**：TX 网一端是 TX 功能脚，另一端是 RX 功能脚",
          _pin_function(tx["pins"][0]["pin"], "USART1_TX")
          and _pin_function(tx["pins"][1]["pin"], "USART1_RX"),
          str([(p["ref"], p["pin"]) for p in tx["pins"]]))

    used_a: set[str] = set()
    used_b: set[str] = set()
    taken: set[str] = set()
    for proto in ("spi", "i2c", "uart"):
        bus_module.expand(proto, "U1", "U2", model_a="stm32f103c8t6",
                          model_b="stm32f103c8t6", cap_a=CAP, cap_b=CAP,
                          used_a=used_a, used_b=used_b, taken_names=taken)
    check("A2-4. **同一颗芯片的引脚不重用**（三条总线共 8 根，脚全不同）",
          len(used_a) == len(taken) and len(used_b) == len(taken),
          f"used_a={sorted(used_a)} nets={len(taken)}")

    no_table = bus_module.expand("spi", "U1", "U2", model_a="esp32s3",
                                 model_b="esp32s3",
                                 cap_a=capability.CAPABILITY.get("esp32s3") or {},
                                 cap_b=capability.CAPABILITY.get("esp32s3") or {})
    check("A2-5. 没有 AF 表的型号 → `expand` 返回 None（**不按顺序硬凑**）",
          no_table is None, str(no_table))


def part_integration() -> None:
    cand = generate.Candidate(
        id="c1",
        parts=[{"model": "stm32f103c8t6", "units": ["mcu"]},
               {"model": "stm32f103c8t6", "units": ["sensor"]}],
        links=[{"id": "L1", "a": {"unit": "unit:mcu"},
                "b": {"unit": "unit:sensor"}, "protocol": "i2c"}],
        units=[{"role": "mcu"}, {"role": "sensor"}])
    design = generate.to_design(cand, _req())
    nets = {row["net"]: row for row in design.nets}
    check("A2-6. `to_design` 出来的是**总线网**（I2C1_SCL/SDA，class=bus）",
          set(nets) >= {"I2C1_SCL", "I2C1_SDA"}
          and all(row.get("class") == "i2c" and row.get("level") == "bus"
                  for row in nets.values()),
          str([(k, v.get("class"), v.get("level")) for k, v in nets.items()]))
    meta = design.meta.get("bus") or []
    check("A2-7. 元数据记下每条链路展开成了哪几根网（可审计）",
          bool(meta) and meta[0].get("group") == "I2C1"
          and meta[0].get("nets"), str(meta))

    original = bus_module.afmap.find
    try:
        bus_module.afmap.find = lambda *a, **k: None  # type: ignore[assignment]
        degraded = generate.to_design(cand, _req())
    finally:
        bus_module.afmap.find = original  # type: ignore[assignment]
    partial = degraded.meta.get("bus_partial") or []
    fallback = [row for row in degraded.nets if row.get("bus_partial")]
    check("A2-8. 退化（没有 AF 表）→ **退回单脚示意网**并标 `bus_partial`，不崩",
          bool(partial) and bool(fallback)
          and all(row.get("level") == "peripheral" for row in fallback),
          f"partial={len(partial)} fallback={len(fallback)}")


def main() -> int:
    print("=== P60 A1 规格层 + A2 总线级网表 ===")
    part_spec()
    part_bus()
    part_integration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())