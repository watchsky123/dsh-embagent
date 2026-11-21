"""验证阶梯 V0→V6 —— 便宜的层先跑，硬失败截断下游。

## 为什么要截断

`physical.run_checks()`（680 行）、`electrical.run()`（513 行）、
`coverify.run()`（674 行）都是"全量跑完再报"。那对**审查**是对的：
一次给出完整图像，不让用户点十次。

但**生成**阶段要的是另一个方向：极性反接意味着板子会烧，此时算时序、
算 SPICE 是浪费算力，更糟的是把注意力从真正原因上引开。所以这里只加
一层**编排与截断**，不改那三个判定器一行。

## 两个必须守住的纪律

1. **没对象不等于通过。** 每层返回 `checked`（真正检查了多少个对象）。
   `checked == 0` 时状态是 `unknown`，不是 `pass`。空设计不会因为
   "没有反例"而拿到绿灯 —— 那正是 `phase.vacuous()` 在治的病。

2. **截断的下游是 `not_run`，不是 `pass`，也不是 `fail`。**
   上游硬失败后下游的结论**无意义**：说成"通过"是假报告，说成"失败"
   会掩盖真正原因（用户会去查时序，而问题在电压）。

## 各层谁来证

| 层 | 名称 | 实现 | 复用 |
|---|---|---|---|
| V0 | 符号 | 本文件 | — |
| V1 | 约束 | 本文件（绝对最大值 / 额定上限 / 极性） | — |
| V2 | 几何 | `physical.run_checks()` | 680 行 |
| V3 | 电气 DC | `electrical.run()` | 513 行 |
| V4 | 时序 | 本文件（波特率可达性） | — |
| V5 | 数字仿真 | P7 前 `not_run` | — |
| V6 | SPICE | 永久 `not_run`（没接） | — |

V6 不是"以后再做"，是**如实报没接**。没接的东西说成待办，比说成通过好；
但说成"计划中"仍然是拖，不如直接 `not_run`。

@module hw_velxio.design.ladder
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .model import DesignModel

LEVELS = ("V0", "V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8")

#: **默认跑的层**。不含 V7/V8。
#:
#: 实测教训：V7（MuJoCo）一加进默认，266 项单测从 **1.8s 变成 394s**
#: —— 几乎每个测试都会 `run_ladder()`，而 V7 每次要跑 3 次扰动。
#:
#: 所以默认只跑"便宜且確定"的 V0–V6。V7/V8 是**可选增强**：
#: 要跑必须显式 `levels=[...]`（CLI 的 `--with-sim`）。
#: 这不是"隐藏功能"，是**成本可见**——想用仿真就得说要。
DEFAULT_LEVELS = ("V0", "V1", "V2", "V3", "V4", "V5", "V6")

#: 需要 MuJoCo 的层。它们不在默认序列里。
SIM_LEVELS = ("V7",)

#: 硬失败的层：失败后下游不再跑。V0/V1 都是"板子会烧/网表根本不成立"。
HARD_LEVELS = ("V0", "V1")

LEVEL_TITLE = {
    "V0": "符号（引用完整性）",
    "V1": "约束（绝对最大值 / 额定 / 极性）",
    "V2": "几何（重叠 / 越界 / 高度 / 走线可达）",
    "V3": "电气 DC（轨跌 / 压降 / 稳压器）",
    "V4": "时序（波特率可达性）",
    "V5": "数字仿真（实跑固件）",
    "V6": "SPICE（模拟波形）",
    "V7": "运动（MuJoCo 动力学 + 三次扰动）",
    "V8": "制造（立创EDA DRC，外部证据）",
}


# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------

@dataclass
class LevelResult:
    """一层的结论。

    `checked` 是**真正检查过的对象数**，不是"跑过一次"。这两者混同
    就是假绿灯的源头：空设计跑 `physical` 也是"跑过"，但一个对象都
    没检查。
    """

    level: str
    status: str = "unknown"          # pass | fail | unknown | not_run
    findings: list[dict[str, Any]] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    checked: int = 0
    ms: float = 0.0

    @property
    def title(self) -> str:
        return LEVEL_TITLE.get(self.level, self.level)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "title": self.title,
            "status": self.status,
            "checked": self.checked,
            "failed": sum(1 for f in self.findings if not f.get("ok")),
            "findings": list(self.findings),
            "unknown": list(self.unknown),
            "ms": round(self.ms, 2),
        }


@dataclass
class LadderResult:
    levels: list[LevelResult] = field(default_factory=list)
    stopped_at: str = ""             # "" = 跑完
    downstream: str = "run"          # run | truncated

    def level(self, name: str) -> LevelResult | None:
        return next((r for r in self.levels if r.level == name), None)

    @property
    def ok(self) -> bool:
        """全部层 `pass` 才算通过。`unknown` / `not_run` 都不算。"""
        return bool(self.levels) and all(r.status == "pass" for r in self.levels)

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for res in self.levels:
            counts[res.status] = counts.get(res.status, 0) + 1
        return {
            "levels": len(self.levels),
            "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0),
            "unknown": counts.get("unknown", 0),
            "not_run": counts.get("not_run", 0),
            "stopped_at": self.stopped_at,
            "downstream": self.downstream,
            "checked": sum(r.checked for r in self.levels),
            "ms": round(sum(r.ms for r in self.levels), 2),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stopped_at": self.stopped_at,
            "downstream": self.downstream,
            "levels": [r.to_dict() for r in self.levels],
            "summary": self.summary(),
        }


# ---------------------------------------------------------------------------
# 轨电压：**从哪里来**必须说清楚
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RailVolts:
    """一条网络的电压，以及这个数字的**依据**。"""

    volts: float
    basis: str


#: 网络名 → 电压。**只用最坏情况**：锂电标称 7.4V 但满电 8.4V，
#: 绝对最大值要按满电判，否则电池满了就烧。
#:
#: 这张表是"网络名恰好写明了电压"的情形，不是猜测厂商规格。
#: 判不出来的（`VIN` / `VBAT` / `$1N8009` 这类）一律 `None` → 记 unknown。
RAIL_NAME_RULES: tuple[tuple[str, float, str], ...] = (
    # 地：0V。绝对值永远不会超，但要单独认出来，否则会被当成"未知电压"。
    (r"^(GND|AGND|DGND|PGND|VSS|0V|GROUND)[A-Z0-9_]*$", 0.0,
     "网络名为地"),
    (r"^(1V8|1\.8V|VCC_1V8|\+1V8)$", 1.8, "网络名 1.8V"),
    (r"^(2V5|2\.5V|VCC_2V5|\+2V5)$", 2.5, "网络名 2.5V"),
    # 3V3 家族：MCU / 逻辑。满电一般 3.3V，有的 LDO 出 3.36V，按 3.3 判。
    (r"^(3V3|3\.3V|VCC_3V3|VDD_3V3|3V3_[A-Z0-9]+|\+3V3)$", 3.3,
     "网络名 3.3V"),
    (r"^(5V|5\.0V|VCC_5V|VDD_5V|5V_[A-Z0-9]+|\+5V)$", 5.0, "网络名 5V"),
    (r"^(12V|VCC_12V|12V_[A-Z0-9]+)$", 12.0, "网络名 12V"),
    # USB VBUS：规范上限 5.25V，按 5.25 判（不是 5.0）。
    (r"^(VBUS|USB_VBUS|USB5V)$", 5.25, "USB VBUS 规范上限"),
    # 2S 锂电：**满电 8.4V**，不是标称 7.4V。
    (r"^(VBAT_2S|BAT_2S|VBAT2S)$", 8.4, "2S 锂电满电 8.4V"),
    (r"^(VBAT_3S|BAT_3S|VBAT3S)$", 12.6, "3S 锂电满电 12.6V"),
    (r"^(VBAT_4S|BAT_4S|VBAT4S)$", 16.8, "4S 锂电满电 16.8V"),
)

#: 明确**判不出来**的名字：列出来是为了让 unknown 的消息具体，
#: 而不是笼统的"推不出电压"。
AMBIGUOUS_NAMES = ("VIN", "VBAT", "VCC", "VDD", "V+", "VMOT", "PWR")


def _norm_net_name(name: str) -> str:
    return str(name or "").strip().upper().replace(" ", "")


def net_volts(design: DesignModel, net: str,
              declared: dict[str, float] | None = None) -> RailVolts | None:
    """推一条网络的电压。**优先元件申报，其次网络名，都不行返回 None。**

    元件申报优先，因为 `electrical.v_out` 是设计者明确写下的；网络名是
    约定俗成的命名，能对上才用。两者都没有 → `None`，由调用方记 unknown。
    """
    if declared and net in declared:
        return RailVolts(declared[net], "元件申报的输出电压")

    for part in design.parts:
        for entry in design.nets:
            if entry.get("net") != net:
                continue
            if not any(p.get("ref") == part.ref
                       for p in entry.get("pins", []) if isinstance(p, dict)):
                continue
            elec = part.electrical or {}
            for key in ("v_out", "output_v", "v_rail"):
                value = elec.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    return RailVolts(float(value), f"{part.ref}.electrical.{key}")

    clean = _norm_net_name(net)
    for pattern, volts, basis in RAIL_NAME_RULES:
        if re.match(pattern, clean):
            return RailVolts(volts, basis)
    return None


def unknown_net_reason(net: str) -> str:
    """判不出电压时，把**为什么**说出来。"""
    clean = _norm_net_name(net)
    if clean in AMBIGUOUS_NAMES:
        return f"{net}: 名称是通用电源名，电压取决于上游（无法从名字判断）"
    if clean.startswith("$"):
        return f"{net}: 自动生成的网络名（立创 EDA 未命名网络），无电压信息"
    return f"{net}: 名称为信号网或无法解析，未参与绝对最大值判定"


# ---------------------------------------------------------------------------
# 元件家族：判不出就是判不出
# ---------------------------------------------------------------------------

#: 引脚/电源绝对最大值。**来源写在注释里，不是编的。**
#: 单位 V。查手册得到的保守上限（宁严不宽）。
ABS_MAX = {
    # STM32F1/F4/H7 手册：VDD max = 4.0V（部分型号 3.6V，取宽的 4.0 是
    # 为了让"3.3V 供电"能过；但 5V 灌进来一定被这里拦住）
    "stm32": {"pin_v": 4.0, "basis": "STM32 数据手册 VDD 上限 4.0V"},
    # ESP32 数据手册：VDD 3.0~3.6V
    "esp32": {"pin_v": 3.6, "basis": "ESP32 数据手册 VDD 上限 3.6V"},
    # ATmega328P：-0.5V ~ +6.0V
    "avr": {"pin_v": 6.0, "basis": "ATmega328P 绝对最大值 6.0V"},
    # 通用 3.3V 逻辑件（LDO 输出侧、电平转换、传感器）
    "logic3v3": {"pin_v": 3.6, "basis": "通用 3.3V 逻辑器件上限 3.6V"},
    # 通用 5V 逻辑件（74HC 系列等）
    "logic5v": {"pin_v": 5.5, "basis": "通用 5V 逻辑器件上限 5.5V"},
    # 舵机 / 电机驱动：工作电压上限（SG90 级 6.0V）
    "servo": {"pin_v": 6.0, "basis": "常见舵机工作上限 6.0V"},
}

#: `kind` / `role` / `label` 里出现这些词就认定家族。
#: **哈希 id 一个都匹配不上**——那就是判不出来，如实记 unknown。
FAMILY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stm32", ("stm32", "stm32f", "stm32h", "stm32g")),
    ("esp32", ("esp32", "esp8266", "esp32s3", "esp32c3")),
    ("avr", ("atmega", "atmega328", "arduino", "attiny")),
    ("servo", ("servo", "sg90", "mg90", "mg996")),
    ("logic5v", ("74hc", "74ls", "74ahc")),
    ("logic3v3", ("3v3", "3.3v", "logic_level")),
)


def family_of(part: Any) -> str | None:
    """从 `kind`/`role`/`label` 猜元件家族。**猜不出返回 None，不猜最近似。**
    """
    haystack = " ".join(str(x or "") for x in (
        getattr(part, "kind", ""), getattr(part, "role", ""),
        getattr(part, "label", ""))).lower()
    for family, words in FAMILY_HINTS:
        if any(word in haystack for word in words):
            return family
    return None


def _declared_max(part: Any) -> tuple[float, str] | None:
    """元件自己申报的额定上限（`electrical.max_v`）。比家族表可信。"""
    elec = getattr(part, "electrical", None) or {}
    for key in ("max_v", "v_max", "vin_max_v"):
        value = elec.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value), f"{getattr(part, 'ref', '?')}.electrical.{key}"
    return None


def pin_limit(part: Any) -> tuple[float, str] | None:
    """这个元件允许的最高引脚电压 + 依据。申报优先，家族次之，都没有 None。"""
    declared = _declared_max(part)
    if declared is not None:
        return declared
    family = family_of(part)
    if family is None:
        return None
    spec = ABS_MAX[family]
    return spec["pin_v"], spec["basis"]


# ---------------------------------------------------------------------------
# V0 符号：网表引用是否成立
# ---------------------------------------------------------------------------

def level_v0(design: DesignModel, **_kw: Any) -> LevelResult:
    """V0：网表里引用的元件/网络/板 —— 都存在吗？

    这一层不涉及任何物理，纯引用完整性。它先跑，因为网表本身坏了，
    后面每层都在算不存在的东西。
    """
    res = LevelResult("V0")
    refs = {p.ref for p in design.parts}
    nets = {e.get("net") for e in design.nets}
    board_ids = {b.id for b in design.board_list()}
    checked = 0

    # 1. 元件 ref 唯一
    seen: set[str] = set()
    for part in design.parts:
        checked += 1
        if part.ref in seen:
            res.findings.append(_finding(
                "symbol.duplicate_ref", part.ref,
                f"元件 ref 重复：{part.ref}（2D/3D/判定共用这个 id，"
                f"重复会让三处指向不同东西）"))
        seen.add(part.ref)

    # 2. 网表引脚指向存在的元件
    for entry in design.nets:
        net = entry.get("net")
        for pin in entry.get("pins", []):
            if not isinstance(pin, dict):
                res.findings.append(_finding(
                    "symbol.bad_pin_entry", str(net),
                    f"网 {net} 的引脚条目不是对象：{pin!r}"))
                continue
            ref = pin.get("ref")
            if ref == "board":
                checked += 1
                continue
            checked += 1
            if ref not in refs:
                res.findings.append(_finding(
                    "symbol.dangling_pin", str(ref),
                    f"网 {net} 的引脚指向不存在的元件 {ref!r}"))
            name = pin.get("pin")
            if name in (None, ""):
                res.findings.append(_finding(
                    "symbol.pin_unnamed", str(ref),
                    f"网 {net} 上 {ref} 的引脚没有名字 —— 无法核对存在性"))

    # 3. 导线必须落在网表里
    for wire in design.wires:
        checked += 1
        if wire.net not in nets:
            res.findings.append(_finding(
                "symbol.orphan_wire", wire.net,
                f"导线 {wire.net} 不在网表里（2D 会画出一条不存在的连接）"))

    # 4. 元件归属的板必须存在
    for part in design.parts:
        wanted = part.placement.board_id
        if wanted and wanted not in board_ids:
            res.findings.append(_finding(
                "symbol.orphan_board", part.ref,
                f"{part.ref}.placement.board_id={wanted!r} 指向不存在的板"))

    _finish(res, checked)
    return res


# ---------------------------------------------------------------------------
# V1 约束：绝对最大值 / 额定上限 / 极性
# ---------------------------------------------------------------------------

def level_v1(design: DesignModel, **_kw: Any) -> LevelResult:
    """V1：电压有没有超过元件能承受的极限？极性有没有接反？

    这是**硬层**：超压/反接会当场烧器件，下游算什么都无意义。

    判不出来的情形（立创哈希 `kind`、`VIN` 这类通用网络名）**记 unknown，
    不猜**。141 个元件里判不出家族的那些会合并成一条，不刷屏。
    """
    res = LevelResult("V1")
    volt_cache: dict[str, RailVolts | None] = {}
    unresolved: dict[str, list[str]] = {}     # 原因 → 网络列表
    checked = 0

    # 每个元件在哪条网上
    nets_of_part: dict[str, list[str]] = {}
    for entry in design.nets:
        net = str(entry.get("net") or "")
        for pin in entry.get("pins", []):
            if isinstance(pin, dict) and pin.get("ref") not in (None, "board"):
                nets_of_part.setdefault(str(pin["ref"]), []).append(net)

    no_family: list[str] = []
    for part in design.parts:
        for net in nets_of_part.get(part.ref, []):
            if net not in volt_cache:
                volt_cache[net] = net_volts(design, net)
            rail = volt_cache[net]
            if rail is None:
                unresolved.setdefault(unknown_net_reason(net), []).append(part.ref)
                continue
            if rail.volts <= 0.0:            # 地：永远不超
                checked += 1
                continue
            limit = pin_limit(part)
            if limit is None:
                no_family.append(part.ref)
                continue
            checked += 1
            max_v, basis = limit
            if rail.volts > max_v:
                res.findings.append(_finding(
                    "hard.abs_max", part.ref,
                    f"{part.ref}（{part.kind}）接在 {net}（{rail.volts}V，"
                    f"{rail.basis}）上，超过上限 {max_v}V（{basis}）："
                    f"**上电即烧**",
                    measured=round(rail.volts, 3), limit=max_v))

    # 极性：只有引脚名里带 +/- 或 A/K 的元件才判得了
    checked += _polarity(design, res, volt_cache)

    if no_family:
        uniq = sorted(set(no_family))
        res.unknown.append(
            f"{len(uniq)} 个元件的 kind 判不出厂商家族"
            f"（如 {', '.join(uniq[:5])}），绝对最大值未判"
            f" —— 补 `electrical.max_v` 或给 kind 一个可识别名即可")
    for reason, refs in unresolved.items():
        res.unknown.append(f"{reason}（涉及 {len(set(refs))} 个元件）")

    _finish(res, checked)
    return res


#: 引脚名 → 极性。`+`/`-` 是电解电容和电池，`A`/`K` 是二极管。
POLARITY_NAMES = {
    "+": "pos", "-": "neg", "P": "pos", "N": "neg",
    "A": "anode", "K": "cathode", "ANODE": "anode", "CATHODE": "cathode",
}


def _polarity(design: DesignModel, res: LevelResult,
              volt_cache: dict[str, RailVolts | None]) -> int:
    """检查极性元件有没有接反。返回**实际判定过的元件数**。

    只在三种信息**同时**具备时才判：元件声明了极性引脚名、该引脚在
    网表里、所在网络的电压已知。缺一个就跳过 —— 跳过不算检查过。
    """
    pin_net: dict[tuple[str, str], str] = {}
    for entry in design.nets:
        for pin in entry.get("pins", []):
            if isinstance(pin, dict):
                pin_net[(str(pin.get("ref")), str(pin.get("pin")))] = \
                    str(entry.get("net") or "")

    judged = 0
    for part in design.parts:
        poles: dict[str, str] = {}
        for name in part.pins:
            mapped = POLARITY_NAMES.get(str(name).strip().upper())
            if mapped in ("pos", "anode"):
                poles["pos"] = str(name)
            elif mapped in ("neg", "cathode"):
                poles["neg"] = str(name)
        if len(poles) < 2:
            continue
        pos_net = pin_net.get((part.ref, poles["pos"]))
        neg_net = pin_net.get((part.ref, poles["neg"]))
        if not pos_net or not neg_net:
            continue
        if pos_net not in volt_cache:
            volt_cache[pos_net] = net_volts(design, pos_net)
        if neg_net not in volt_cache:
            volt_cache[neg_net] = net_volts(design, neg_net)
        pos_v, neg_v = volt_cache[pos_net], volt_cache[neg_net]
        if pos_v is None or neg_v is None:
            continue
        judged += 1
        if pos_v.volts < neg_v.volts:
            res.findings.append(_finding(
                "hard.reverse_polarity", part.ref,
                f"{part.ref} 极性接反：正向脚 {poles['pos']} 在 {pos_net}"
                f"（{pos_v.volts}V），负向脚 {poles['neg']} 在 {neg_net}"
                f"（{neg_v.volts}V）—— 反了 {neg_v.volts - pos_v.volts:.2f}V",
                measured=round(pos_v.volts - neg_v.volts, 3), limit=0.0))
    return judged


# ---------------------------------------------------------------------------
# V2 / V3：直接复用现有判定器
# ---------------------------------------------------------------------------

def level_v2(design: DesignModel, *, pitch_mm: float = 0.5,
             **_kw: Any) -> LevelResult:
    """V2：几何。直接调 `physical.run_checks()`，一行判定逻辑都不重写。"""
    from . import physical

    res = LevelResult("V2")
    if not design.parts:
        res.unknown.append("没有元件：几何检查无对象（空集通过不是通过）")
        _finish(res, 0)
        return res
    try:
        report = physical.run_checks(design, pitch_mm=pitch_mm)
    except RuntimeError as exc:
        res.unknown.append(f"几何检查没跑起来：{exc}")
        _finish(res, 0)
        return res

    res.findings = list(report.get("findings", []))
    # `physical` 的 `not_verified` 里混了两种东西：真的缺口（未运行/无尺寸）
    # 和**肯定**的核对结论。后者是 `pins.summary_line()` 的产品——它在
    # 全部核对通过时返回"431 个引脚全部核对过"，与 `not_verified` 的语义
    # 相反。整份塞进 `unknown` 会把"已核对"渲染成"未知"。
    #
    # 所以丢掉那条散文，改用结构化的 `pins` 字典自己判：
    # 有 `unverified` 才算缺口。
    res.unknown.extend(
        item for item in report.get("not_verified", [])
        if not str(item).startswith("引脚校验："))
    pin_audit = report.get("pins", {}) or {}
    pin_unverified = int(pin_audit.get("unverified", 0) or 0)
    pin_total = int(pin_audit.get("total", 0) or 0)
    if pin_unverified:
        refs = pin_audit.get("unknown_parts") or []
        preview = ", ".join(str(r) for r in refs[:6]) + ("…" if len(refs) > 6 else "")
        res.unknown.append(
            f"引脚校验：{pin_total} 个引脚中 {pin_unverified} 个无法核对"
            f"（元件没有引脚表：{preview}）—— 不是通过，是没核对")
    summary = report.get("summary", {})
    # `checked` 用真正进了 3D 空间的元件数，不是 parts 总数：
    # 没尺寸的元件被跳过了，它们不算检查过。
    unsized = set(report.get("unsized", []))
    res.checked = sum(1 for p in design.parts if p.ref not in unsized)
    if report.get("vacuous"):
        res.unknown.extend(str(v) for v in report["vacuous"])
    _finish(res, res.checked)
    return res


def level_v3(design: DesignModel, *, inputs: dict[str, Any] | None = None,
             **_kw: Any) -> LevelResult:
    """V3：电气 DC。直接调 `electrical.run()`。

    `electrical` 自己就把"零负载"判成不通过并写进 `not_verified`
    （P0 修的），这一层只做状态翻译。
    """
    from . import electrical

    res = LevelResult("V3")
    try:
        report = electrical.run(design, inputs=inputs)
    except (RuntimeError, ValueError) as exc:
        res.unknown.append(f"电学分析没跑起来：{exc}")
        _finish(res, 0)
        return res

    res.findings = [f for f in report.findings() if f.get("severity") != "info"]
    res.unknown.extend(report.not_verified)
    res.checked = len(report.rails) + len(report.wires) + len(report.regulators)
    if not res.checked:
        res.unknown.append("没有电源轨/导线/稳压器可算（空集通过不是通过）")
    _finish(res, res.checked)
    return res


# ---------------------------------------------------------------------------
# V4 时序：波特率可达性
# ---------------------------------------------------------------------------

#: 常见 UART 过采样。16 是 AVR/STM32 默认，8 是 STM32 高速模式。
OVERSAMPLING = (16, 8)

#: 允许的波特率误差（%）。UART 收发两端各差 2% 就接近失锁，取 2.0 保守。
BAUD_TOLERANCE_PCT = 2.0


def baud_error_pct(clock_hz: float, baud: int,
                   oversample: int = 16) -> float | None:
    """从时钟和波特率算分频误差（%）。算不出来返回 None。

    这就是真实固件里 `UBRR = f/(16*baud) - 1` 那一步。误差超过 2% 时
    通信会间歇性出错——那种 bug 在台架上极难复现，所以值得在纸上先算。
    """
    if clock_hz <= 0 or baud <= 0 or oversample <= 0:
        return None
    ideal = clock_hz / (oversample * baud)
    if ideal < 1.0:
        return None                      # 时钟太低，这个波特率根本到不了
    divisor = round(ideal)               # 硬件只能取整数分频
    actual = clock_hz / (oversample * divisor)
    return abs(actual - baud) / baud * 100.0


def level_v4(design: DesignModel, **_kw: Any) -> LevelResult:
    """V4：有没有"算得出但跑不通"的波特率？

    只判**元件自己申报了 clock 和 baud** 的情形。没有任何元件申报时
    返回 `unknown` —— 你工程实测 0 个元件申报时钟，所以这层现在是
    unknown，不是 pass。
    """
    res = LevelResult("V4")
    judging = 0
    for part in design.parts:
        elec = part.electrical or {}
        clock = elec.get("clock_hz")
        baud = elec.get("uart_baud") or elec.get("baud")
        if not isinstance(clock, (int, float)) or not isinstance(baud, (int, float)):
            continue
        if clock <= 0 or baud <= 0:
            continue
        judging += 1
        best = None
        for os_rate in OVERSAMPLING:
            err = baud_error_pct(float(clock), int(baud), os_rate)
            if err is None:
                continue
            if best is None or err < best[1]:
                best = (os_rate, err)
            if err <= BAUD_TOLERANCE_PCT:
                break
        if best is None:
            res.findings.append(_finding(
                "timing.baud_unreachable", part.ref,
                f"{part.ref}：时钟 {float(clock)/1e6:.1f}MHz 无法产生 "
                f"{int(baud)}bps（分频比不足 1）", measured=0.0, limit=baud))
            continue
        os_rate, err = best
        if err > BAUD_TOLERANCE_PCT:
            res.findings.append(_finding(
                "timing.baud_error", part.ref,
                f"{part.ref}：时钟 {float(clock)/1e6:.1f}MHz 产生 "
                f"{int(baud)}bps 的最佳误差 {err:.2f}%（过采样 {os_rate}）"
                f"，超过 {BAUD_TOLERANCE_PCT}% —— 高波特率下会丢字节",
                measured=round(err, 3), limit=BAUD_TOLERANCE_PCT))

    if not judging:
        res.unknown.append(
            "没有任何元件申报 clock_hz + uart_baud，时序无法验证"
            "（不是通过）")
    _finish(res, judging)
    return res


# ---------------------------------------------------------------------------
# V5 / V6：没接的层，如实报
# ---------------------------------------------------------------------------

def level_v5(design: DesignModel, **_kw: Any) -> LevelResult:
    """V5 数字仿真。P7 之前**没接**，一律 not_run（不是 pass）。"""
    res = LevelResult("V5", status="not_run",
                      unknown=["数字仿真未接入（需要 arm-none-eabi-gcc + "
                               "Renode，见执行方案 P7）：本层没跑"])
    return res


def level_v6(design: DesignModel, **_kw: Any) -> LevelResult:
    """V6 SPICE。**这个工作台没有**，永久 not_run。"""
    res = LevelResult("V6", status="not_run",
                      unknown=["SPICE 未接入：本工作台没有模拟波形验证能力，"
                               "本层永远 not_run（不要说成通过）"])
    return res


#: V7 的零件数阈值。超过就缩短仿真时长——**性能预算，不是精度妥协**。
#:
#: 实测：316 元件的多板工程 weld 后跑 1 秒要 >300 秒墙钟
#: （约束数 ~1 万个，arena 256MB）。V7 要跑 3 次扰动，不控的话
#: 一次阶梯检查就是 15 分钟。降级后仍然跑 3 次，只是时长短。
V7_LARGE_PARTS = 200
V7_LARGE_DURATION_S = 0.4


def level_v7(design: DesignModel, *,
             duration_s: float = 0.0, **_kw: Any) -> LevelResult:
    """V7 运动：MuJoCo 动力学 + **三次启动扰动**。

    ## 为什么这一层默认 not_run 而不是硬跑

    MuJoCo 是**可选增强**（装在项目内 `vendor/pylib`）。没装时这一层
    必须 `not_run`，不能假装。而且跑物理仿真有成本：141 个元件的板
    单次 0.5s 仿真墙钟 0.02s，但三次扰动 × 多候选就是数量级放大。

    ## 判定链条

    1. `robustness.triplicate("kinematic", run)` 跑三次扰动
    2. 每次扰动跑 `mujoco_backend.run(..., weld_parts=True)`
       —— **weld 是必须的**：PCB 元件焊死在板上，
       不 weld 会让 141 个元件各自下落，数值爆炸（实测末速 61574 m/s）
    3. 单次判定：翻倒（倾角 > 阈值）= fail；跑不起来 = unknown

    合并规则交给 `robustness.combine()`：任一 fail → fail，
    有 unknown → unknown，全 pass → pass。
    """
    res = LevelResult("V7")
    try:
        from . import mujoco_backend, robustness
    except ImportError as exc:                            # noqa: BLE001
        res.status = "not_run"
        res.unknown.append(f"运动域不可用：{exc}")
        return res

    available, why = mujoco_backend.available()
    if not available:
        res.status = "not_run"
        res.unknown.append(
            f"MuJoCo 不可用（{why}）——本层没跑，**不是通过**")
        return res

    # 时长：显式给了就用；否则按零件数降级
    effective_s = float(duration_s) if duration_s > 0 else (
        V7_LARGE_DURATION_S if len(design.parts) > V7_LARGE_PARTS else 2.0)
    if effective_s < 2.0:
        # 降级必须**说出来**，但它是 info 不是 warning：
        # 时长缩短不改变"稳不稳"的结论方向，只是分辨率降低。
        # 放 `findings`（severity=info）而不是 `unknown`——
        # 放 unknown 会让整层变成 unknown，那是另一种失真。
        res.findings.append({
            "check": "kinematic.perf_budget",
            "ok": True,
            "severity": "info",
            "message": (f"零件数 {len(design.parts)} > {V7_LARGE_PARTS}，"
                        f"仿真时长降到 {effective_s}s"
                        f"（性能预算，仍跑 3 次扰动）"),
        })

    def _one(perturb: dict[str, Any]) -> tuple[str, str]:
        try:
            result = mujoco_backend.run(
                design, duration_s=effective_s, free_bodies=True,
                weld_parts=True)
        except Exception as exc:                          # noqa: BLE001
            return "unknown", f"{type(exc).__name__}: {exc}"
        if not result.ok:
            return "unknown", result.error
        flipped = [ref for ref, tilt in result.max_tilt_deg.items()
                   if tilt > mujoco_backend.FLIP_ANGLE_DEG]
        if flipped:
            return "fail", (f"{len(flipped)} 个刚体翻倒"
                            f"（>{mujoco_backend.FLIP_ANGLE_DEG}°）："
                            f"{', '.join(flipped[:4])}")
        return "pass", ""

    robust = robustness.triplicate("kinematic", _one)
    res.checked = len(robust.attempts)
    res.status = robust.status
    for attempt in robust.attempts:
        if attempt.status == "fail":
            res.findings.append({
                "check": "kinematic.stability",
                "ok": False,
                "severity": "error",
                "message": (f"扰动 {attempt.name} 下失效：{attempt.detail}"),
                "applied": dict(attempt.applied),
            })
        elif attempt.status == "unknown":
            res.unknown.append(
                f"扰动 {attempt.name} 没跑起来：{attempt.detail}")
    if not robust.attempts:
        res.status = "unknown"
        res.unknown.append("没有任何运动扰动被执行")
    return res


def level_v8(design: DesignModel, **_kw: Any) -> LevelResult:
    """V8 制造：立创EDA DRC。**外部证据，不是我们的判定。**

    ## 为什么单列一层而不并进 V2

    V2 是**我们自己**量的几何（重叠/越界/高度/走线可达）。
    V8 是**别人**（立创EDA）的 DRC/DFM 结论。两者标准不同：
    KiCad/立创的安全间距、层规则、焊盘补偿与我们不同。

    混进同一层会让人分不清"谁说的"。所以：
    * V2 findings 进 `handoff` 的 `blocking`
    * V8 findings 进 `handoff` 的 `external`（**不进阻断**）

    ## P17 之前一律 not_run

    发射层（`emit_lceda`）还没写，所以现在**没有工程可 DRC**。
    这一层保持 `not_run` 并说清"缺 P17"，不假装跑过。
    """
    res = LevelResult("V8", status="not_run",
                      unknown=[
                          "制造域未接入：需要 P17 的 emit_lceda 把设计发射成"
                          ".eprj3 工程，才能跑立创EDA 的 DRC。"
                          "本层没跑（不是通过）"])
    return res


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

RUNNERS: dict[str, Callable[..., LevelResult]] = {
    "V0": level_v0,
    "V1": level_v1,
    "V2": level_v2,
    "V3": level_v3,
    "V4": level_v4,
    "V5": level_v5,
    "V6": level_v6,
    "V7": level_v7,
    "V8": level_v8,
}


def _finding(check: str, ref: str, message: str, *,
             measured: float | None = None,
             limit: float | None = None) -> dict[str, Any]:
    """与 `physical.py` 同格式的 finding（面板统一渲染）。"""
    return {
        "check": check, "ok": False, "severity": "error", "priority": "P0",
        "refs": [ref], "message": message,
        "measured": measured, "limit": limit, "fix": {},
    }


def _finish(res: LevelResult, checked: int) -> None:
    """按"有对象、有结论"的纪律定状态。

    顺序要紧：
      1. 有 **error 级** failed finding → fail
         （**即使没检查够也算 fail**：已经证明坏了，就不需要更多证据）
      2. 一个对象都没过 → unknown（空集通过不是通过）
      3. 有未判项 / 有 warning → unknown（部分未知不能报通过）
      4. 都没有 → pass

    ## 为什么 warning 不能判 fail（实测出的 bug）

    第一版这里是 `any(not f.get("ok", True) ...)`——只看 `ok`，
    **不看 `severity`**。于是 `consistency.provenance` 那条
    "元件尺寸是估算值"的 **warning** 把整个 V2 判成 `fail`。

    实测证据：`001-blink`（3 个元件）V2=fail，但失败项里
    `error=0 / warning=1`——**几何上什么都没错**，只是 `btn1` 的
    尺寸是估算的。这个判定是错的：

      * "尺寸是估算值"不是"设计错了"，是"需要人确认"
      * 报 fail 会让人去改本来正确的布局
      * 更糟的是它污染了交接单的阻断项

    warning 的语义是"结论有保留"，对应状态应是 **unknown**
    （结论不确定），不是 **fail**（结论是否定）。
    """
    res.checked = checked
    errors = [f for f in res.findings
              if not f.get("ok", True)
              and str(f.get("severity") or "error") == "error"]
    warnings = [f for f in res.findings
                if not f.get("ok", True)
                and str(f.get("severity") or "") == "warning"]

    if errors:
        res.status = "fail"
        return
    # warning 进 unknown，让"未确认"可见但不算失败
    for item in warnings:
        message = str(item.get("message") or item.get("check") or "")
        if message and message not in res.unknown:
            res.unknown.append(message)
    if checked <= 0:
        res.status = "unknown"
        if not res.unknown:
            res.unknown.append("本层没有可检查的对象")
        return
    res.status = "unknown" if res.unknown else "pass"


def run_ladder(design: DesignModel, *, levels: list[str] | None = None,
               stop_on_hard: bool = True, pitch_mm: float = 0.5,
               inputs: dict[str, Any] | None = None) -> LadderResult:
    """按顺序跑阶梯。`stop_on_hard` 时 V0/V1 失败就截断。

    下游截断记 `not_run` + 一句"上游硬失败，本层未运行（结论无意义）"。
    """
    seq = [lv.upper() for lv in (levels or DEFAULT_LEVELS)]
    bad = [lv for lv in seq if lv not in RUNNERS]
    if bad:
        raise ValueError(f"未知的层：{', '.join(bad)}（可用：{', '.join(LEVELS)}）")

    out = LadderResult()
    for index, name in enumerate(seq):
        started = time.perf_counter()
        runner = RUNNERS[name]
        kwargs: dict[str, Any] = {}
        if name == "V2":
            kwargs["pitch_mm"] = pitch_mm
        elif name == "V3":
            kwargs["inputs"] = inputs
        try:
            res = runner(design, **kwargs)
        except NotImplementedError:
            res = LevelResult(name, status="not_run",
                              unknown=["本层未实现"])
        except Exception as exc:                      # noqa: BLE001
            # 判定器抛异常 = 这一层**没结论**，绝不能记成 pass。
            res = LevelResult(name, status="unknown",
                              unknown=[f"本层抛异常，未得出结论："
                                       f"{type(exc).__name__}: {exc}"])
        res.ms = (time.perf_counter() - started) * 1000
        out.levels.append(res)

        if stop_on_hard and name in HARD_LEVELS and res.status == "fail":
            out.stopped_at = name
            out.downstream = "truncated"
            for rest in seq[index + 1:]:
                out.levels.append(LevelResult(
                    rest, status="not_run",
                    unknown=[f"上游 {name} 硬失败，本层未运行（结论无意义）"]))
            break
    return out


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.ladder",
        "levels": [
            {"level": lv, "title": LEVEL_TITLE[lv],
             "hard": lv in HARD_LEVELS,
             "delegates_to": {"V2": "physical.run_checks",
                              "V3": "electrical.run"}.get(lv, ""),
             "implemented": lv not in ("V5", "V6")}
            for lv in LEVELS
        ],
        "hard_levels": list(HARD_LEVELS),
        "statuses": ["pass", "fail", "unknown", "not_run"],
    }
