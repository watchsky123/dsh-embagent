"""P15 验收：多域图（机械关系 + 时序 + 质量属性）。

从仓库根运行：

    python -m hw_velxio.scripts.check_m15

## 这个脚本守的五件事

1. **旧文件零回归** —— 5 个真实工程读进来，`to_dict` 逐字节不变，
   且**不新增任何键**。这是 P15 唯一的硬约束（不升 schema）。
2. **round-trip** —— 带 Joint/Program 的设计写出去再读回来，逐字段相等。
3. **推断的正确性** —— 叠板能推出来、主控能认出来、port 不瞎配。
4. **闸门的出口都要有断言** —— `inferred` 不落地；`promote` 才落地。
   这类 `if ... continue` 退化后测试天然是绿的，**必须专写"闸门生效"的断言**。
5. **反例比正例重要** —— 用 GND 网推 port、把电容当主控、把滤波电容当
   被驱动对象，这三种我都实测踩过，每个都留了一个反向夹具。

## 为什么这里的"识别"用例必须用合成夹具

真实工程 `示例-单板` 的 `kind` 全是 EasyEDA 哈希（`4becac96152c70`），
判身份只能靠网名。合成夹具能把"网名线索"和"kind 线索"分开测，
否则分不清是哪个判据在起作用。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hw_velxio.design import joints                     # noqa: E402
from hw_velxio.design.model import (Board, DesignModel, Joint,   # noqa: E402
                                    Part, Placement, Program)
from hw_velxio.design.store import DesignStore, list_projects  # noqa: E402


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}{f' — {detail}' if detail else ''}")
            self.items.append(name)


def _part(ref: str, kind: str, *, x: float = 10, y: float = 10,
          z: float = 0.0, side: str = "", w: float = 4, h: float = 4,
          d: float = 1, role: str = "component", board: str = "",
          mass_g: float | None = None) -> Part:
    return Part(
        ref=ref, kind=kind, label=kind, role=role,
        size_mm={"w": w, "h": h, "d": d},
        physical_source="datasheet",
        placement=Placement(x_mm=x, y_mm=y, z_mm=z, side=side,
                            board_id=board),
        mass_g=mass_g,
        pins=["1", "2"],
    )


def _design(parts: list[Part], nets: dict[str, list[str]] | None = None,
            **kw) -> DesignModel:
    design = DesignModel(id="fixture", title="P15 夹具",
                         board=Board(kind="custom",
                                     size_mm={"w": 120, "h": 120}), **kw)
    design.parts = parts
    for net, pinrefs in (nets or {}).items():
        pins = []
        for pinref in pinrefs:
            ref, _, pin = pinref.partition(".")
            pins.append({"ref": ref, "pin": pin or "1"})
        design.nets.append({"net": net, "pins": pins})
    return design


# ---------------------------------------------------------------------------
# 1. 旧文件零回归
# ---------------------------------------------------------------------------

def check_regression(f: Failures) -> None:
    print("== 1. 旧文件零回归（P15 的硬约束：不升 schema）==")

    projects = list_projects()
    if not projects:
        print("  skip 没有工程")
        return

    for row in projects:
        if not row.get("has_design"):
            continue
        store = DesignStore(row["id"])
        design = store.load()
        payload = design.to_dict()
        again = DesignModel.from_dict(json.loads(json.dumps(payload)))
        text_a = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        text_b = json.dumps(again.to_dict(), ensure_ascii=False, sort_keys=True)
        f.check(f"{row['id'][:14]} 往返逐字节一致",
                text_a == text_b, "to_dict 与 from_dict 不互逆")
        # **不能新增键**：旧文件的 to_dict 里不该出现四域字段
        added = [k for k in ("joints_list", "program", "sim_params",
                             "physical_tests") if k in payload]
        f.check(f"{row['id'][:14]} 未新增多域键（空值不写）",
                not added, str(added))


# ---------------------------------------------------------------------------
# 2. round-trip
# ---------------------------------------------------------------------------

def check_roundtrip(f: Failures) -> None:
    print("\n== 2. 带多域字段的 round-trip ==")

    design = _design(
        [_part("U1", "stm32f103", role="mcu", mass_g=2.5),
         _part("SV1", "servo", role="motor", z=8.0, mass_g=55.0)],
        {"PWM1": ["U1.1", "SV1.1"]})
    design.joints_list = [
        Joint(a="U1", b="SV1", kind="hinge",
              params={"range_deg": [-90, 90]},
              dof=["rz"], source="declared",
              evidence="云台水平轴").to_dict(),
    ]
    design.program = Program(
        steps=[{"t_s": 0.0, "actuator": "SV1", "value": 0.5},
               {"t_s": 1.0, "actuator": "SV1", "value": 0.0}]).to_dict()
    design.sim_params = {"dt": 0.0005, "label": "initial"}
    design.physical_tests = [{"id": "pt-1", "result": "fail"}]

    text = json.dumps(design.to_dict(), ensure_ascii=False)
    back = DesignModel.from_dict(json.loads(text))
    f.check("joints_list 往返一致",
            back.joints_list == design.joints_list)
    f.check("program 往返一致", back.program == design.program)
    f.check("sim_params 往返一致", back.sim_params == design.sim_params)
    f.check("physical_tests 往返一致",
            back.physical_tests == design.physical_tests)
    f.check("Part.mass_g 往返一致",
            back.parts[0].mass_g == 2.5, str(back.parts[0].mass_g))

    print("\n== 2b. 字段校验（不给默认值，只校验合法性）==")

    for bad, label in (
        ({"mass_g": -1.0}, "负质量"),
        ({"com_mm": [0, 0]}, "质心少一维"),
        ({"inertia": [1, 2]}, "惯量少分量"),
    ):
        try:
            Part(ref="X1", kind="k", **bad)
            f.check(f"{label} 被拒绝", False, "居然通过了")
        except Exception:
            f.check(f"{label} 被拒绝", True)

    try:
        Program(steps=[{"t_s": 1.0, "actuator": "M", "value": 1},
                       {"t_s": 0.0, "actuator": "M", "value": 0}])
        f.check("时序倒流被拒绝", False, "居然通过了")
    except Exception:
        f.check("时序倒流被拒绝", True)

    try:
        Program(units="furlongs")
        f.check("未知 units 被拒绝", False, "居然通过了")
    except Exception:
        f.check("未知 units 被拒绝", True)

    try:
        Joint(a="U1", b="U1", kind="hinge")
        f.check("自连被拒绝", False, "居然通过了")
    except Exception:
        f.check("自连被拒绝", True)

    try:
        Joint(a="U1", b="U2", kind="telepathy")
        f.check("未知 kind 被拒绝", False, "居然通过了")
    except Exception:
        f.check("未知 kind 被拒绝", True)


# ---------------------------------------------------------------------------
# 3. 推断正确性
# ---------------------------------------------------------------------------

def check_infer_stack(f: Failures) -> None:
    print("\n== 3. stack 推断 ==")

    # 两块板叠放：z 分离 5mm，xy 完全重叠
    stacked = _design([
        _part("A1", "board_marker", x=20, y=20, z=0, w=40, h=30, d=1.6),
        _part("A2", "board_marker", x=20, y=20, z=5, w=40, h=30, d=1.6),
    ])
    result = joints.infer(stacked)
    kinds = [j.kind for j in result.joints]
    f.check("两块叠放的板被推成 stack", "stack" in kinds, str(kinds))
    if "stack" in kinds:
        joint = next(j for j in result.joints if j.kind == "stack")
        f.check("stack 标 inferred（不参与硬处理）",
                joint.source == "inferred", joint.source)
        f.check("stack 带证据（间隙 + 重叠面积）",
                "gap_mm" in joint.params and "overlap_mm2" in joint.params,
                str(joint.params))

    # **反向**：同一层的两个件不该被判成 stack（这是最容易误报的）
    same_layer = _design([
        _part("B1", "c", x=10, y=10, z=0, w=4, h=4, d=1),
        _part("B2", "c", x=12, y=10, z=0, w=4, h=4, d=1),
    ])
    f.check("同层重叠的件不判成 stack",
            not any(j.kind == "stack"
                    for j in joints.infer(same_layer).joints),
            "同层被误判为叠放")

    # 间距过大的不判
    far = _design([
        _part("C1", "c", x=10, y=10, z=0, w=10, h=10, d=1),
        _part("C2", "c", x=10, y=10, z=50, w=10, h=10, d=1),
    ])
    result = joints.infer(far)
    f.check("间距 50mm 不判成 stack",
            not any(j.kind == "stack" for j in result.joints))
    f.check("间距过大进 skipped 并说明",
            any("z 间隙" in s["why"] for s in result.skipped),
            str([s["why"][:40] for s in result.skipped]))


def check_infer_port(f: Failures) -> None:
    print("\n== 4. port 推断（含三个实测反例）==")

    # 正例：主控 + 舵机，网名是 PWM 信号
    good = _design([
        _part("U1", "mcu_board", role="mcu"),
        _part("SV1", "servo", role="motor"),
        _part("R9", "resistor"),                  # 无源件，不该当 b 端
    ], {"SERVO_A": ["U1.1", "SV1.1"]})
    result = joints.infer(good)
    ports = [j for j in result.joints if j.kind == "port"]
    f.check("主控 + 舵机 推出 port", len(ports) == 1, f"{len(ports)} 条")
    if ports:
        f.check("port 的 b 端是舵机而不是电阻",
                ports[0].b == "SV1", ports[0].b)
        f.check("port 记录网名与对端类型",
                ports[0].params.get("net") == "SERVO_A"
                and ports[0].params.get("peer_type") == "actuator",
                str(ports[0].params))

    print("\n  反例 1：电源/地网不能推 port")
    gnd = _design([
        _part("U1", "mcu_board", role="mcu"),
        _part("D9", "led", role="component"),
    ], {"GND": ["U1.1", "D9.1"], "LED_A": ["U1.2", "D9.1"]})
    result = joints.infer(gnd)
    gnd_ports = [j for j in result.joints
                 if j.kind == "port" and j.params.get("net") == "GND"]
    f.check("GND 网不产生 port 关系", not gnd_ports, str(len(gnd_ports)))

    print("\n  反例 2：只有无源件时 b 端不硬凑")
    passive = _design([
        _part("U1", "mcu_board", role="mcu"),
        _part("R1", "resistor"),
        _part("C1", "capacitor"),
    ], {"ADC_BAT": ["U1.1", "R1.1", "C1.1"]})
    result = joints.infer(passive)
    adc_ports = [j for j in result.joints
                 if j.kind == "port" and j.params.get("net") == "ADC_BAT"]
    f.check("ADC 网只有无源件 → 不推 port", not adc_ports,
            str([(j.a, j.b) for j in adc_ports]))
    f.check("该网进 skipped 并说明「只有无源件」",
            any("只有无源件" in s["why"] for s in result.skipped),
            str([s["why"][:50] for s in result.skipped]))

    print("\n  反例 3：连在 BOOT0 上的电容不是主控")
    boot = _design([
        _part("U1", "4becac96152c70", role="module"),   # kind 是哈希
        _part("C89", "9a1b2c3d"),
        _part("R248", "611c3d3932db1684"),
    ], {
        "BOOT0": ["U1.1", "C89.1", "R248.1"],
        "NRST": ["U1.2"],
        "OSC_IN": ["U1.1"],
        "OSC_OUT": ["U1.2"],
    })
    controllers, notes = joints.find_controllers(boot)
    f.check("主控判定是全局的：只有 U1（不是 C89/R248）",
            controllers == {"U1"}, str(sorted(controllers)))

    print("\n  反例 4：只有 SPI 连接的模块不是主控（Lora1 那种）")
    lora = _design([
        _part("U1", "4becac96152c70"),
        _part("LORA1", "15e0527ceae800"),
    ], {
        "BOOT0": ["U1.1"],
        "NRST": ["U1.2"],
        "SPI_SCK": ["U1.1", "LORA1.1"],
        "SPI_MISO": ["U1.2", "LORA1.2"],
        "SPI_MOSI": ["U1.1", "LORA1.1"],
    })
    controllers, _ = joints.find_controllers(lora)
    f.check("连 SPI 的模块不被判成主控",
            controllers == {"U1"}, str(sorted(controllers)))

    print("\n  反例 5：没有主控特征网 → 不推 port")
    nocon = _design([
        _part("A1", "board_marker"),
        _part("A2", "board_marker"),
    ], {"LED_X": ["A1.1", "A2.1"]})
    result = joints.infer(nocon)
    f.check("没有主控时 port 为空",
            not any(j.kind == "port" for j in result.joints),
            str([(j.a, j.b) for j in result.joints]))
    f.check("没有主控进 skipped 说明",
            any("主控" in s["why"] for s in result.skipped),
            str([s["why"][:50] for s in result.skipped]))


# ---------------------------------------------------------------------------
# 5. 闸门 + 出口
# ---------------------------------------------------------------------------

def check_gate_and_exit(f: Failures) -> None:
    print("\n== 5. 闸门（inferred 不落地）+ 出口（promote 才落地）==")

    design = _design([
        _part("U1", "mcu_board", role="mcu"),
        _part("SV1", "servo", role="motor"),
    ], {"SERVO_A": ["U1.1", "SV1.1"]})

    inference = joints.infer(design)
    f.check("推断出候选", bool(inference.joints), "0 条")

    added = joints.apply(design, inference.joints, only_declared=True)
    f.check("only_declared=True → 一条都不写",
            added == 0 and not design.joints_list, f"added={added}")

    # 出口：promote 之后才写
    promoted = joints.promote(inference)
    f.check("promote 把 inferred 提升为 declared",
            promoted > 0 and all(j.source == "declared"
                                 for j in inference.joints), str(promoted))
    added = joints.apply(design, inference.joints, only_declared=True)
    f.check("promote 后 apply 真的写入", added > 0, f"added={added}")
    f.check("写入的 design.joints_list 标 declared",
            all(j.get("source") == "declared"
                for j in design.joints_list), str(design.joints_list))

    print("\n== 5b. 空表不是通过 ==")

    empty = _design([_part("U1", "k")])
    result = joints.validate(empty, [])
    f.check("没有声明关系 → status=not_ready",
            result["status"] == "not_ready", result["status"])
    f.check("reason 明说检查未执行",
            "不是通过" in result["reason"], result["reason"])

    print("\n== 5c. 缺参数要说出来（不编默认值）==")

    broken = _design([
        _part("U1", "k"), _part("G1", "gear"),
    ])
    result = joints.validate(broken, [
        Joint(a="U1", b="G1", kind="gear", source="declared")])
    f.check("gear 缺 ratio → error",
            any(fd["check"] == "joint.params" for fd in result["findings"]),
            str(result["findings"]))

    print("\n== 5d. 悬空引用 ==")

    result = joints.validate(broken, [
        Joint(a="U1", b="NOBODY", kind="hinge", source="declared",
              params={"range_deg": [0, 10]})])
    f.check("关系指向不存在的元件 → error",
            any(fd["check"] == "joint.part_ref"
                for fd in result["findings"]),
            str(result["findings"]))


# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    f = Failures()

    check_regression(f)
    check_roundtrip(f)
    check_infer_stack(f)
    check_infer_port(f)
    check_gate_and_exit(f)

    print()
    if f.items:
        print(f"FAILED {len(f.items)} 项：")
        for name in f.items:
            print(f"  - {name}")
        return 1
    print("P15 验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())