"""P66 验收 —— 连接器贴边 / 块内 LCSC / 6 个新角色块。

    python -m dsh_embagent.scripts.check_p66

## 三件事

| 项 | 之前 | 现在 |
|---|---|---|
| 连接器 | 布局是行式排，J2 距板边 10.9 mm → V2 fail | 连接器先贴**底边一行**、再贴**左边一列**，每颗 ≤3 mm |
| LCSC | 2/26 件有编号，emit 没有料可装 | **43/43** 有 `lcsc`（23 个唯一编号，全部能在立创解析成器件）|
| 角色块 | radio/sensor/camera/gimbal/motor 无块（uncovered）| 5 个板侧接口块覆盖 **6 个角色**，uncovered 角色 = 0 |

## 判据

* 连接器：`physical.check_connector_edge` 无 fail；V2 无 error
* LCSC：每个块零件都有 `lcsc`；编号去重后逐个能在立创解析（没客户端 → skip，不判通过）
* 角色：9 个角色全部落到块；件数 43、块 11
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import assemble_blocks as assemble_module  # noqa: E402
from dsh_embagent.design import blocks as blocks_module  # noqa: E402
from dsh_embagent.design import generate  # noqa: E402
from dsh_embagent.design import lceda  # noqa: E402
from dsh_embagent.design import ladder  # noqa: E402
from dsh_embagent.design import physical  # noqa: E402

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
REQ = Req(id="p66", title="t",
          roles=["mcu", "power_in", "radio", "sensor", "camera",
                 "gimbal_pan", "gimbal_tilt", "motor", "servo_power"],
          interfaces=[{"a": "mcu", "b": "radio", "protocol": "uart"},
                      {"a": "mcu", "b": "camera", "protocol": "usb"},
                      {"a": "mcu", "b": "sensor", "protocol": "i2c"},
                      {"a": "mcu", "b": "motor", "protocol": "can"}],
          constraints={"io_voltage_v": 3.3}, numbers={"power_w": 60}, notes="")


def part_connector() -> None:
    result = assemble_module.assemble(REQ, board_size=(60.0, 40.0))
    design = result["design"]
    findings = physical.check_connector_edge(design, {})
    fails = [row for row in findings if row.get("severity") == "error"]
    v2 = ladder.level_v2(design)
    v2_errors = [row for row in v2.findings if row.get("severity") == "error"]
    check("A1. **连接器贴边**：每颗距板边 ≤3 mm（physical 无 error）",
          not fails,
          str([row.get("refs") for row in fails][:3]))
    check("A2. V2 阶梯没有 error（连接器规则不再挡门）",
          not v2_errors,
          str([(row.get("check"), row.get("refs")) for row in v2_errors][:3]))


def part_lcsc() -> None:
    library = blocks_module.load_all()
    parts = [spec for block in library.values()
             for spec in block.get("parts") or []]
    missing = [spec.get("key") for spec in parts if not spec.get("lcsc")]
    form = re.compile(r"^C\d+$")
    bad_form = [spec.get("lcsc") for spec in parts
                if spec.get("lcsc") and not form.match(str(spec["lcsc"]))]
    codes = sorted({str(spec["lcsc"]) for spec in parts if spec.get("lcsc")})
    check("B1. 每个块零件都有 **LCSC 编号**（格式 C<数字>）",
          not missing and not bad_form,
          f"零件 {len(parts)}，缺 {missing[:3]}，坏格式 {bad_form[:3]}")

    if not lceda.find_exe():
        print(_safe("[skip] B2. 没有 lceda-pro —— 解析没验，**不是通过**"))
        return
    session = lceda.LcedaSession().open("", headless=True)
    try:
        got = session.invoke("""
const L = %s; const out = [];
for (const c of L) {
  try { const d = await eda.lib_Device.getByLcscIds(c);
        out.push({c, ok: !!d, name: d ? d.name : ''}); }
  catch (e) { out.push({c, ok: false}); }
}
return out;
""" % json.dumps(codes), timeout_ms=280000)
    except Exception as exc:  # noqa: BLE001
        print(_safe(f"[skip] B2. 立创查询失败（{type(exc).__name__}）—— 没验，不是通过"))
        return
    finally:
        session.close(destroy=True)
    bad = [row["c"] for row in got if not row.get("ok")]
    check(f"B2. 23 个编号**全部能在立创解析成器件**（{len(codes)} 个）",
          not bad, f"解析不了：{bad}")


def part_roles() -> None:
    plan = assemble_module.plan({"blocks": [{"role": role} for role in REQ.roles],
                                 "interfaces": REQ.interfaces})
    mapped = {row["instance"] for row in plan["plan"]}
    expected = {"MCU1", "PWR_DCDC", "PWR_LDO", "RADIO1", "SENS1", "CAM1",
                "PAN1", "TILT1", "MOTOR1", "USB1", "CAN1"}
    check("C1. 11 个角色/接口都落到块上（9 角色 + usb/can）",
          expected <= mapped, f"缺 {sorted(expected - mapped)}")

    result = assemble_module.assemble(REQ, board_size=(60.0, 40.0))
    counts = result.get("counts") or {}
    check("C2. 装配：43 件 / 11 块，**uncovered 角色 = 0**",
          counts.get("parts") == 43 and counts.get("blocks") == 11
          and counts.get("roles_uncovered") == 0,
          str(counts))

    check("C3. 新块都在库里有 `verified_by` 字段（空 = 未验证，不是通过）",
          all("verified_by" in (blocks_module.get(name) or {})
              for name in ("radio_nrf24", "sensor_i2c", "camera_usb",
                           "servo_out", "motor_esc")),
          str([name for name in ("radio_nrf24", "sensor_i2c", "camera_usb",
                                 "servo_out", "motor_esc")
               if (blocks_module.get(name) or {}).get("verified_by")]))


def main() -> int:
    print("=== P66 连接器贴边 / LCSC / 6 个新角色 ===")
    part_connector()
    part_lcsc()
    part_roles()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())