"""语义树 —— **hw_velxio 自己的**：把"一个大元件不是原子"变成可判定的数据。

取代「照搬 hwforge 的 49 棵字符串树」，改成**按需补充的结构化树**。

## 它填的是什么

舵机被建模成一个 20×20×20 的盒子 + 3 个引脚。后果：

| 层 | 丢失的东西 |
|---|---|
| 判定链 | 只知道"死区 5µs → 0.9°"，**不知道这 0.9° 是内部电位器决定的** |
| 修复建议 | 只能说"换舵机"，说不了"换控制板没用，得换电位器或整机" |
| 风险 | H 桥堵转过热、齿轮掉齿、电位器磨损 —— 全是内部失效 |

所以语义树是**判定链的解释层**，不是另一个判定器。

## 与 hwforge 版本的三处关键改动

### 1. 键名对齐：模板 key → 语义树 key 是**显式表**，不是字符串匹配

```python
TEMPLATE_TO_SEMANTIC = {"camera_module": "camera", "mcu_board": "mcu", ...}
```

实测：直接拿模板 key 查 hwforge 的 `internals_for()`，16 个里只有 **7 个**
命中 —— `camera_module` 查不到 `camera`，`ultrasonic_ranger` 查不到 `ranger`。
靠字符串启发式猜不准，所以用一张能**校验**的表（指向不存在的树要报错）。

### 2. specs 是**带单位的数值**，不是字符串

hwforge 版存的是 `"4.8-6.0 V"`、`"≈ 1:250"`、`"1.0-2.0"` —— 判定层拿到
字符串没法比较。

这里有 `Quantity`：标量或 `(min, max)` 范围 + 单位 + 来源。

**范围不给"代表值"。** `(1.0, 2.0)ms` 的脉宽映射到角度时，取中值 1.5ms
算出来的角度是编的。判定层要标量时，范围返回 `None` —— 与
`capability_available` 的三态是同一条纪律。

### 3. 只做被消费的树，其余**如实标"未建模"**

hwforge 有 49 棵。我们只做 4 棵（`gimbal_servo` / `battery` / `camera` /
`mcu`），因为只有它们有消费者（`coverify` 的 root_cause）。

其余模板 `semantic_tree()` 返回 `None` —— **"没建模"不等于"这个件简单"**，
调用方必须区分。这与 hwforge 自己那句话一致："a part with no internals is
not thereby simple, it is *unexamined*"。

## 消费者的契约

`coverify` 的每条 finding 可以带 `root_cause`：

```python
{
  "subsystem": "control_board",
  "why": "板上 PWM 解析的死区 5µs，经 180° 行程折算",
  "fix_options": [...],
  "cannot_fix_by": "固件 —— 这是模拟量分辨率，不是软件问题",
  "source": "teardown",
  "trust": 0.7,
}
```

**`cannot_fix_by` 是最有价值的字段**：它把"硬件下限"变成一条可执行判断，
让 AI 不在固件上浪费时间。

@module hw_velxio.design.semantics
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Source = Literal["datasheet", "teardown", "inferred", "estimated"]

#: 来源可信度。**用于排序与显示，不用于判定** —— 判定只看数值本身。
TRUST: dict[str, float] = {
    "datasheet": 1.0,
    "teardown": 0.7,
    "inferred": 0.3,
    "estimated": 0.2,
}


# ---------------------------------------------------------------------------
# 带单位的量
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Quantity:
    """一个带单位、带来源的量。标量或 `(min, max)` 范围。"""

    value: float | tuple[float, float]
    unit: str = ""
    source: Source = "inferred"
    note: str = ""

    @property
    def is_range(self) -> bool:
        return isinstance(self.value, tuple)

    def scalar(self) -> float | None:
        """判定层要单个数字时用。**范围返回 `None`，不取中值。**

        取中值会造出一个"不存在的典型值"，而判定链下游没法知道它是编的。
        """
        if isinstance(self.value, tuple):
            return None
        return float(self.value)

    def low(self) -> float | None:
        """范围的下界；标量返回自己的值。"""
        if isinstance(self.value, tuple):
            return float(self.value[0])
        return float(self.value)

    def high(self) -> float | None:
        if isinstance(self.value, tuple):
            return float(self.value[1])
        return float(self.value)

    def to_si(self) -> float | None:
        """转换成基本单位（V/A/Ω/s）的标量。范围返回 `None`。"""
        value = self.scalar()
        if value is None:
            return None
        scale = {"mv": 1e-3, "ma": 1e-3, "ua": 1e-6, "ms": 1e-3,
                 "us": 1e-6, "ns": 1e-9, "mohm": 1e-3, "kohm": 1e3,
                 "khz": 1e3, "mhz": 1e6, "gb": 1e9, "mb": 1e6, "kb": 1e3}
        return value * scale.get(self.unit.lower(), 1.0)

    def text(self) -> str:
        if isinstance(self.value, tuple):
            lo, hi = self.value
            return f"{_fmt(lo)}-{_fmt(hi)} {self.unit}".strip()
        return f"{_fmt(self.value)} {self.unit}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {"value": list(self.value) if self.is_range else self.value,
                "unit": self.unit, "source": self.source,
                "note": self.note, "text": self.text()}


def _fmt(value: float) -> str:
    if float(value) == int(value):
        return str(int(value))
    return f"{value:g}"


# ---------------------------------------------------------------------------
# 子系统与整件
# ---------------------------------------------------------------------------

@dataclass
class Subsystem:
    """元件内部的一层。`role` 与依赖图同一套词汇。"""

    name: str
    role: str
    why: str = ""
    source: Source = "inferred"
    #: 它提供什么能力（依赖图用）
    provides: tuple[str, ...] = ()
    #: 它需要什么（依赖图用）
    needs: tuple[str, ...] = ()
    #: **判定链用的数值**，键名与消费方一致（见 FIELD_ALIASES）
    specs: dict[str, Quantity] = field(default_factory=dict)
    #: 失效模式 —— 进风险账本
    failure_modes: list[str] = field(default_factory=list)
    #: 装配/使用注意事项（"注释层"）
    notes: list[str] = field(default_factory=list)
    children: list["Subsystem"] = field(default_factory=list)

    @property
    def trust(self) -> float:
        """可信度取**子系统和数值中较低的那个**。

        子系统构成是 `teardown`（0.7），但里面的 `deadband_us` 可能直接
        来自 datasheet（1.0）。**不能因为数值可信就把"它在哪个子系统里"
        这个推断也当成可信** —— 那条链路的强度是最弱一环。
        """
        lowest = TRUST.get(self.source, 0.0)
        for quantity in self.specs.values():
            lowest = min(lowest, TRUST.get(quantity.source, 0.0) or lowest)
        return lowest

    def walk(self, depth: int = 0) -> list[tuple[int, "Subsystem"]]:
        out = [(depth, self)]
        for child in self.children:
            out.extend(child.walk(depth + 1))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "role": self.role, "why": self.why,
                "source": self.source, "trust": self.trust,
                "provides": list(self.provides), "needs": list(self.needs),
                "specs": {k: v.to_dict() for k, v in self.specs.items()},
                "failure_modes": list(self.failure_modes),
                "notes": list(self.notes),
                "children": [c.to_dict() for c in self.children]}


@dataclass
class SemanticTree:
    """一个元件类型的内部结构。"""

    role: str
    model: str
    subsystems: list[Subsystem] = field(default_factory=list)
    #: 不可引用为事实的声明
    caveats: list[str] = field(default_factory=list)
    #: 出处（URL → 它给了什么）
    sources: dict[str, str] = field(default_factory=dict)

    def flat(self) -> list[tuple[int, Subsystem]]:
        out: list[tuple[int, Subsystem]] = []
        for subsystem in self.subsystems:
            out.extend(subsystem.walk())
        return out

    def spec(self, key: str) -> Quantity | None:
        """按字段名取数值（**跨子系统搜索**，取第一个命中的）。"""
        for _depth, subsystem in self.flat():
            if key in subsystem.specs:
                return subsystem.specs[key]
        return None

    def find(self, key: str) -> tuple[Subsystem | None, Quantity | None]:
        """取数值 + 它属于哪个子系统 —— `root_cause` 要两者。"""
        for _depth, subsystem in self.flat():
            if key in subsystem.specs:
                return subsystem, subsystem.specs[key]
        return None, None

    def values(self) -> dict[str, Quantity]:
        """全部数值（后者覆盖前者，跨子系统同名时）。"""
        out: dict[str, Quantity] = {}
        for _depth, subsystem in self.flat():
            out.update(subsystem.specs)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "model": self.model,
                "subsystems": [s.to_dict() for s in self.subsystems],
                "caveats": list(self.caveats),
                "sources": dict(self.sources)}

    def digest(self, limit_lines: int = 60) -> str:
        """给模型看的紧凑视图。**注释（notes）才是重点。**"""
        lines = [f"[{self.role}] {self.model}"]
        for depth, subsystem in self.flat():
            pad = "  " * depth
            specs = " ".join(f"{k}={v.text()}" for k, v in
                             list(subsystem.specs.items())[:3])
            lines.append(f"{pad}{subsystem.name} ({subsystem.role})"
                         + (f" {specs}" if specs else ""))
            lines.extend(f"{pad}  # {note}" for note in subsystem.notes)
            lines.extend(f"{pad}  ! {mode}" for mode in subsystem.failure_modes)
            if len(lines) >= limit_lines:
                lines.append("  … 已截断")
                break
        if self.caveats:
            lines.append("不可引用为事实:")
            lines.extend(f"  ! {c}" for c in self.caveats)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 字段别名：消费方的键名 → 树里的键名
# ---------------------------------------------------------------------------

#: `coverify.ServoSpec` 的字段 → 语义树里的 spec key 与单位换算。
#:
#: 这是让树成为**判定链上游数据源**的关键，而不是一份平行文档。
#: 实测 hwforge 版树里叫 `pwm_frame_hz`，而 coverify 要 `frame_hz` ——
#: 名字对不上，数据就永远接不进去。
SERVO_FIELDS: dict[str, str] = {
    "deadband_us": "deadband_us",
    "frame_hz": "pwm_frame_hz",
    "stall_current_ma": "stall_current_ma",
    "idle_current_ma": "idle_current_ma",
}

#: 范围型字段：树里存 `(lo, hi)` 一个量，消费方要两个标量。
RANGE_FIELDS: dict[str, tuple[str, str, str]] = {
    # 树里的 key -> (消费方下界字段, 消费方上界字段, 源的单位)
    "pulse_ms": ("pulse_min_us", "pulse_max_us", "ms"),
    "rated_v": ("vin_min_v", "vin_max_v", "V"),
}


# ---------------------------------------------------------------------------
# 四棵树（只做被消费的）
# ---------------------------------------------------------------------------

def _q(value: float | tuple[float, float], unit: str, source: Source,
       note: str = "") -> Quantity:
    return Quantity(value=value, unit=unit, source=source, note=note)


def _gimbal_servo() -> SemanticTree:
    """SG90 级微型舵机。

    外部数值：OSEPP LS-SG90SR 数据手册。
    内部构成：FabAcademy 拆解教程（"Inside the servomotor has four
    principal parts"）。**它们之间的连接关系是推断的。**
    """
    return SemanticTree(
        role="gimbal_servo", model="SG90（LS-SG90SR 级）",
        sources={
            "https://www.mouser.com/catalog/specsheets/"
            "OSEPP_Electronics_LS-SG90SR_Datasheet.pdf":
                "尺寸、堵转/空载电流、PWM 帧频、脉宽范围、死区、行程、重量",
        },
        subsystems=[
            Subsystem(
                name="dc_motor", role="motor",
                why="把电能转为转矩；有刷换向，寿命受电刷限制",
                source="teardown",
                provides=("shaft_torque",), needs=("vin_4v8_to_6v",),
                specs={
                    "rated_v": _q((4.8, 6.0), "V", "datasheet",
                                  "最低/最高工作电压"),
                    "stall_current_ma": _q(700.0, "ma", "datasheet", "堵转电流"),
                    "idle_current_ma": _q(4.0, "ma", "datasheet", "空载电流"),
                },
                failure_modes=[
                    "电刷磨损 → 转矩下降、电流脉动增大",
                    "堵转过热 → 线圈绝缘劣化",
                ],
                notes=[
                    "堵转 700mA：不能由 MCU 的 3V3 稳压或 GPIO 供电",
                    "空载仅 4mA，差异 175 倍 —— 按空载估电源必定失败",
                ],
            ),
            Subsystem(
                name="gear_train", role="mechanical",
                why="多级塑料齿轮降速增扭，输出 180° 行程",
                source="teardown",
                provides=("reduced_rotation",), needs=("shaft_torque",),
                specs={
                    "gear_type": _q(0.0, "", "teardown", "塑料（定性）"),
                    "ratio_est": _q(0.0, "", "inferred", "≈1:250（类别典型值）"),
                },
                failure_modes=["塑料齿轮在冲击负载下掉齿", "回差随磨损增大"],
                notes=[
                    "塑料齿轮是这类舵机最先坏的部件，尤其侧向冲击时",
                    "机械回差大于电气死区：标称精度好于一等并不现实",
                ],
            ),
            Subsystem(
                name="potentiometer", role="sensor",
                why="把输出轴角度变成电阻，供控制板比较",
                source="teardown",
                provides=("angle_feedback",), needs=(),
                specs={"type": _q(0.0, "", "teardown", "三端可变电阻")},
                failure_modes=["电位器磨损 → 中位漂移", "接触不良 → 抖动"],
                notes=[
                    "电位器磨损会使中位漂移；这是舵机老化的主因",
                    "改连续旋转就是把电位器换成两个等值电阻，欺骗控制板",
                ],
            ),
            Subsystem(
                name="control_board", role="mcu",
                why="把 PWM 脉宽与电位器反馈比较，驱动 H 桥",
                source="teardown",
                provides=("motor_drive",), needs=("vin_4v8_to_6v",),
                specs={
                    "pwm_frame_hz": _q(50.0, "hz", "datasheet", "PWM 帧频"),
                    "pulse_ms": _q((1.0, 2.0), "ms", "datasheet",
                                   "0°..180° 对应脉宽"),
                    "deadband_us": _q(5.0, "us", "datasheet", "死区"),
                },
                failure_modes=["H 桥堵转过热"],
                notes=[
                    "比例控制：起始速度快，接近目标变慢",
                    "板上没有位置环积分项，负载变化会引起稳态误差",
                ],
                children=[
                    Subsystem(
                        name="h_bridge", role="driver",
                        why="双向驱动直流电机",
                        source="inferred",
                        provides=(), needs=("motor_drive",),
                        specs={},
                        failure_modes=["堵转时桥臂过热烧毁"],
                        notes=["堵转时电流全部走桥臂，是板上最热的元件"],
                    ),
                ],
            ),
        ],
        caveats=[
            "内部四大部分的**具体型号与连接关系**为推断（inferred），非原厂公布",
            "OSEPP 手册与常见 Tower Pro 标称值有出入（1.3/1.5 vs 1.8/2.5 kg·cm）",
            "输出的规格均来自该手册，未做实测",
        ],
    )


def _battery() -> SemanticTree:
    """18650 ×2 电池组（7.4V 标称）。"""
    return SemanticTree(
        role="battery", model="2S 18650 电池组",
        sources={},
        subsystems=[
            Subsystem(
                name="cells", role="power",
                why="储能本体，2 串锂电",
                source="inferred",
                provides=("vbat",), needs=(),
                specs={
                    "v_nominal": _q(7.4, "V", "inferred", "2S 标称"),
                    "v_empty": _q(6.0, "V", "inferred", "每节 3.0V 截止"),
                    "v_full": _q(8.4, "V", "inferred", "每节 4.2V 满充"),
                },
                failure_modes=["过放 → 容量永久损失", "过充 → 起火"],
                notes=[
                    "7.4V 不能直接进 5V/3.3V 器件，必须经变换器",
                    "电压随荷电状态变化 6.0..8.4V：按 7.4V 设计的变换器要在两端都工作",
                ],
            ),
            Subsystem(
                name="protection_ic", role="driver",
                why="过充/过放/过流保护",
                source="inferred",
                specs={},
                failure_modes=["保护动作时整机掉电（不是故障，是设计）"],
                notes=["保护板有内阻，会在堵转瞬间触发过流保护"],
            ),
            Subsystem(
                name="internal_resistance", role="passive",
                why="内阻决定带载压降",
                source="inferred",
                specs={"r_est_mohm": _q(80.0, "mohm", "inferred",
                                        "每串约 40mΩ（类别典型值）")},
                failure_modes=["老化后内阻上升 → 带载压降变大"],
                notes=["2A 负载下 80mΩ 内阻压降 0.16V —— 计算轨跌时要算进去"],
            ),
        ],
        caveats=[
            "全部为按 2S 18650 类别推断（inferred）：无具体电芯手册，未实测",
            "内阻、容量均为类别典型值，不可用于选型判定",
        ],
    )


def _psu_5v() -> SemanticTree:
    """5V 电源模块（AC-DC 或 DC-DC 砖）。

    **这是 `electrical` 的直接上游**：`rail_under_load()` 需要的
    `nominal_v` / `source_r_mohm` / `rated_a` 全部来自这里。

    为什么内阻是单独一个子系统：一个"5V 2A"的模块在 2A 时**不是 5V**。
    用户看的是额定值，判定链必须看内阻 —— 2.1A 时 90mΩ 内阻压降 0.19V，
    电压还"看着正常"，但已经超过额定。这两件事（压降 / 过载）的修法不同，
    所以内阻必须能被单独取出来算。
    """
    return SemanticTree(
        role="psu_5v", model="5V/2A 电源模块（AC-DC 砖级）",
        sources={
            "(类别典型值，无具体型号手册)": "额定、内阻、效率区间",
        },
        subsystems=[
            Subsystem(
                name="rectifier", role="driver",
                why="AC 输入整流（若为 AC-DC）或直接 DC 输入",
                source="inferred",
                provides=(), needs=("acin",),
                specs={
                    "vac_in_min": _q(90.0, "V", "inferred", "宽压输入下界"),
                    "vac_in_max": _q(264.0, "V", "inferred", "宽压输入上界"),
                },
                failure_modes=["输入欠压 → 输出跌落", "浪涌电流触发空开"],
                notes=[
                    "AC 输入端**永远接不到板上的引脚**：这块板只出 5V/GND，"
                    "ACIN 是模块自己的接线端子",
                    "浪涌电流可达额定数倍：保险丝要按浪涌选，不能按额定选",
                ],
            ),
            Subsystem(
                name="converter", role="regulator_5v",
                why="把输入变换成稳压 5V",
                source="inferred",
                provides=("5v_rail",), needs=(),
                specs={
                    "v_out": _q(5.0, "V", "inferred", "标称输出"),
                    "v_out_tol_pct": _q(5.0, "%", "inferred", "输出电压容差"),
                    "rated_a": _q(2.0, "a", "inferred", "连续输出额定"),
                    "eff_pct": _q((80.0, 88.0), "%", "inferred",
                                  "效率区间（随负载变化）"),
                },
                failure_modes=[
                    "过载 → 发热、掉压、寿命骤降（**电压可能仍看着正常**）",
                    "长时间贴额定运行 → 电解电容干涸",
                ],
                notes=[
                    "额定 2A 是**散热条件下的连续值**，不是峰值能力",
                    "效率 80-88% 意味着 2A 输出时自身发热 1.4-2.5W",
                ],
            ),
            Subsystem(
                name="output_impedance", role="passive",
                why="有限输出内阻决定带载压降",
                source="inferred",
                provides=(), needs=("5v_rail",),
                specs={
                    "r_out_mohm": _q(90.0, "mohm", "inferred",
                                     "输出内阻（类别典型值）"),
                },
                failure_modes=["内阻随电解电容老化上升 → 带载压降变大"],
                notes=[
                    "90mΩ 在 2A 时压降 0.18V —— 5V 变 4.82V，"
                    "已贴近舵机 4.8V 最低值",
                    "内阻让'额定 2A'和'能带 2A'成为两件事",
                ],
            ),
            Subsystem(
                name="bulk_cap", role="passive",
                why="储能，吸收负载突变",
                source="inferred",
                provides=(), needs=("5v_rail",),
                specs={
                    "c_out_uf": _q(1000.0, "uf", "inferred", "输出电解电容"),
                },
                failure_modes=["电容 ESR 上升 → 纹波变大、瞬态跌落"],
                notes=[
                    "舵机堵转是阶跃负载：容值不够时瞬态跌落比稳态压降深",
                    "本模型**只算稳态**，看不见这个瞬态",
                ],
            ),
        ],
        caveats=[
            "全部为按 5V/2A 模块类别推断（inferred）：无具体型号手册，未实测",
            "内阻、效率均为类别典型值，不可用于选型判定",
            "**只算直流**：没有纹波、没有瞬态响应、没有环路稳定性",
        ],
    )


def _camera() -> SemanticTree:
    """OV2640 级相机模组。"""
    return SemanticTree(
        role="camera", model="OV2640 级模组（640×480）",
        sources={},
        subsystems=[
            Subsystem(
                name="image_sensor", role="sensor",
                why="把光转成数字图像",
                source="inferred",
                specs={"interface": _q(0.0, "", "inferred", "DVP 8-bit 并口")},
                failure_modes=["曝光不足 → 检测距离下降"],
                notes=["DVP 并口占 8 根数据线 + 时钟，引脚预算要先算"],
            ),
            Subsystem(
                name="encoder", role="mcu",
                why="硬件 JPEG 压缩",
                source="inferred",
                specs={"codec": _q(0.0, "", "inferred", "硬件 JPEG")},
                failure_modes=["只支持 JPEG → 码率高于 H.264"],
                notes=["JPEG 约 0.2-0.5 bpp，H.264 约 0.08 bpp："
                       "码率预算差 3-6 倍"],
            ),
            Subsystem(
                name="wifi_radio", role="radio",
                why="承载图像码流",
                source="inferred",
                specs={"band": _q(0.0, "", "inferred", "2.4 GHz")},
                failure_modes=["2.4G 拥塞 → 丢包、卡顿"],
                notes=["名义速率永远到不了：持续码流按 60% 估"],
            ),
        ],
        caveats=["构成按 OV2640 类模组推断（inferred），无具体型号手册"],
    )


def _mcu() -> SemanticTree:
    """ESP32-S3 级双核 MCU 模组。"""
    return SemanticTree(
        role="mcu", model="ESP32-S3 级模组",
        sources={},
        subsystems=[
            Subsystem(
                name="cpu", role="mcu",
                why="跑控制逻辑",
                source="datasheet",
                specs={
                    "cores": _q(2, "", "datasheet", "双核"),
                    "max_mhz": _q(240, "mhz", "datasheet", "最高主频"),
                    "sram_kb": _q(512, "kb", "datasheet", "片内 SRAM"),
                },
                failure_modes=["主频降低 → 控制环周期变长"],
                notes=["160MHz 下 16 位定时器 @20ms 帧：预分频后在死区附近"],
            ),
            Subsystem(
                name="wifi_phy", role="radio",
                why="无线链路",
                source="datasheet",
                specs={"band": _q(0.0, "", "datasheet", "2.4GHz"),
                       "max_mbps": _q(150, "mbps", "datasheet", "理论峰值")},
                failure_modes=["实际持续吞吐远低于峰值"],
                notes=["150Mbps 是 PHY 峰值；TCP 持续约 20-40Mbps，按 60% 估"],
            ),
            Subsystem(
                name="camera_encoder", role="mcu",
                why="硬件 JPEG 编码",
                source="inferred",
                specs={"codec": _q(0.0, "", "inferred", "硬件 JPEG")},
                failure_modes=["只支持 JPEG"],
                notes=["与 camera 模组的编码器重复：二者只需一个"],
            ),
            Subsystem(
                name="usb_serial", role="mcu",
                why="烧录与调试",
                source="datasheet",
                specs={"iface": _q(0.0, "", "datasheet", "USB-Serial-JTAG")},
                failure_modes=[],
                notes=["调试口占一个 USB 口，量产可省"],
            ),
        ],
        caveats=[
            "按 ESP32-S3 类模组推断；具体型号的引脚定义要查手册",
            "150Mbps 是 PHY 峰值，不是可用吞吐",
        ],
    )


#: 已建模的树。**只做被消费的 4 个**，其余如实标"未建模"。
TREES: dict[str, SemanticTree] = {
    "gimbal_servo": _gimbal_servo(),
    "psu_5v": _psu_5v(),
    "battery": _battery(),
    "camera": _camera(),
    "mcu": _mcu(),
}


# ---------------------------------------------------------------------------
# 模板 key → 语义树 key（**显式表，可校验**）
# ---------------------------------------------------------------------------

#: 装配模板 → 语义树角色。
#:
#: 实测：直接拿模板 key 查语义树，16 个里只有 7 个命中 ——
#: `camera_module` 查不到 `camera`。靠字符串启发式猜不准
#: （`ultrasonic_ranger` 猜不出 `ranger`），所以用一张**能校验**的表。
#: `None` 表示"这个模板没有可建模的内部结构"（占位件、通用件）。
TEMPLATE_TO_SEMANTIC: dict[str, str | None] = {
    "gimbal_servo": "gimbal_servo",
    "camera_module": "camera",
    "mcu_board": "mcu",
    "mcu_block": "mcu",
    "battery": "battery",
    "psu_5v": "psu_5v",
    # 以下在 TREES 里还没有树 —— 如实标 None，不是"简单"
    "regulator": None,
    "regulator_3v3": None,
    "ultrasonic_ranger": None,
    "wheel_motor": None,
    "wheel": None,
    "motor_driver": None,
    "chassis": None,
    "wall_bracket": None,
    "sensor_keepout": None,
    "step_mechanical": None,
}


# ---------------------------------------------------------------------------
# 查询与校验
# ---------------------------------------------------------------------------

def tree_for_template(template: str) -> SemanticTree | None:
    """按**装配模板 key** 取树。这是 `design/` 里唯一该用的入口。"""
    semantic_key = TEMPLATE_TO_SEMANTIC.get(template)
    if semantic_key is None:
        return None
    return TREES.get(semantic_key)


def tree_for(semantic_key: str) -> SemanticTree | None:
    """按语义树 key 取（少数场景直接用）。"""
    return TREES.get(semantic_key)


@dataclass
class SemanticProblem:
    """一条映射/数据问题。"""

    kind: str
    subject: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "subject": self.subject,
                "detail": self.detail}


@dataclass
class SemanticCheck:
    """映射表与树的校验结果。"""

    ok: bool
    problems: list[SemanticProblem] = field(default_factory=list)
    #: 有映射但没树的模板（诚实的缺口清单）
    unmodelled: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "unmodelled": list(self.unmodelled),
                "problems": [p.to_dict() for p in self.problems]}

    def summary(self) -> str:
        if not self.ok:
            return f"{len(self.problems)} 条语义树问题"
        from . import library

        all_templates = library.keys()
        modelled = [t for t in all_templates
                    if tree_for_template(t) is not None]
        # **树的数量和覆盖的模板数不是一回事**：`mcu_board` 与 `mcu_block`
        # 共用 `mcu` 一棵树。分开报，否则"4 棵树"看起来像只覆盖 4 个模板。
        return (f"{len(TREES)} 棵语义树覆盖 {len(modelled)}/{len(all_templates)} "
                f"个模板（{len(self.unmodelled)} 个如实标未建模）")


def validate(templates: set[str] | None = None) -> SemanticCheck:
    """校验映射表。

    抓三类问题：

    1. `mapping_missing` —— 装配模板在映射表里没有条目
       （新加模板时这条会失败，逼着人决定它有没有语义树）；
    2. `tree_missing` —— 映射指向了一个不存在的树
       （拼错或树被删 —— 静默返回 None 是最坏的情况）；
    3. `spec_not_numeric` —— 标为结构化的树里出现了非 Quantity 的 spec。
    """
    from . import library

    problems: list[SemanticProblem] = []
    unmodelled: list[str] = []

    known = templates if templates is not None else library.keys()
    for template in sorted(known):
        if template not in TEMPLATE_TO_SEMANTIC:
            problems.append(SemanticProblem(
                kind="mapping_missing", subject=template,
                detail=f"模板 {template!r} 在 TEMPLATE_TO_SEMANTIC 里没有条目 —— "
                       f"新模板必须显式声明有没有语义树"))
            continue
        semantic_key = TEMPLATE_TO_SEMANTIC[template]
        if semantic_key is None:
            unmodelled.append(template)
            continue
        if semantic_key not in TREES:
            problems.append(SemanticProblem(
                kind="tree_missing", subject=template,
                detail=f"模板 {template!r} 映射到 {semantic_key!r}，"
                       f"但 TREES 里没有这棵树（已建：{sorted(TREES)}）"))

    # 结构化树里不能有裸字符串
    for key, tree in TREES.items():
        for depth, subsystem in tree.flat():
            for spec_key, value in subsystem.specs.items():
                if not isinstance(value, Quantity):
                    problems.append(SemanticProblem(
                        kind="spec_not_numeric",
                        subject=f"{key}.{subsystem.name}.{spec_key}",
                        detail=f"spec {spec_key!r} 是 {type(value).__name__}，"
                               f"不是 Quantity —— 判定层拿到字符串没法比较"))

    return SemanticCheck(ok=not problems, problems=problems,
                         unmodelled=unmodelled)


def describe() -> list[dict[str, Any]]:
    """机器可读的树目录（给工具层/面板用）。"""
    return [TREES[key].to_dict() for key in sorted(TREES)]


def coverage() -> dict[str, Any]:
    """覆盖率报告 —— 诚实版。"""
    from . import library

    all_templates = library.keys()
    modelled = [t for t in sorted(all_templates)
                if tree_for_template(t) is not None]
    return {
        "templates": len(all_templates),
        "modelled": len(modelled),
        "modelled_list": modelled,
        "unmodelled_list": sorted(set(all_templates) - set(modelled)),
        "note": "未建模不等于该件简单，是**未考证**",
    }
