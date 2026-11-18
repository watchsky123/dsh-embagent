"""归因反推 —— 从网表结构反推「这个元件为什么在板上」。

## 为什么需要它，以及它不能做什么

`claims.py` 解决了"删除即失败"：删掉元件，断言会变红。但它变红只说明
**机制覆盖到了**，不说明**理由说得对**。一条
`claim.R1.provides_default_level` 红了，用户只知道 R1 有作用，
不知道它把哪条线拉到了几伏。

这一层要回答的是那句话本身：**R1 为什么在**。

它**不猜厂商型号语义**。立创 EDA 导入的元件 `kind` 是
`0deda055e6014e4e` 这样的哈希，`label` 也是哈希（实测 `示例-单板`
141 个元件里 16 个标签是哈希）。从哈希推功能只能靠编，编出来的理由
读起来很像真的，这是这套系统最该避免的失败形态。所以判不出时
返回 `unknown`，**并如实计入"未覆盖"**。

## 三条线索，按可信度从高到低

| 序 | 线索 | 能得出什么 | 可信度 |
|---|---|---|---|
| L1 | **拓扑 + 元件类别** | 去耦/上拉/下拉/串联/开关/时钟 | `inferred_by_topology` |
| L2 | **网络名协议** | 它是 I2C 从设备 / SPI 从设备 / 串口 | `inferred_by_topology` |
| L3 | **角色表** `roles.BY_ROLE` | 人声明的角色含义 | `asserted` |

拓扑排第一，因为它**可复算**：任何人拿同一份 `design.json` 都会得到
同一个结论，而且结论里必然带着具体的网名。角色表排第三，因为角色
是导入器写的兜底默认值（实测 141 个里 125 个是 `component`）——
那不是语义信息。

## 每条理由必须含具体指称

`why` 里必须出现**网名或元件号**。这条由 `_cited()` 在 `run()` 里
强制检查：判不出具体指称的理由会被降级为 `unknown`，而不是以
"该元件起到滤波作用"这种套话的身份留在报告里。套话无法被证伪，
也就无法被审查。

## 归因粒度：无源件按组

单个 0402 电容不需要独立理由，但"3.3V 轨上这 9 个去耦电容"需要。
所以报告同时给两层：

* `parts[]` —— 逐个元件（handoff 要能点到具体 ref）
* `groups[]` —— 按（规则，目标网络）聚合（人读的是这一层）

实测 `示例-单板`：141 个元件里 125 个是 `role="component"`，
按组能压到 20 组上下。

@module hw_velxio.design.justify
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from . import claims as claims_module
from . import ladder as ladder_module
from .model import DesignModel, Justification, Part

# ---------------------------------------------------------------------------
# 网络分类：地 / 电源轨 / 信号
# ---------------------------------------------------------------------------

#: 判为"地"的网络名。**只认明确的**——`GNDD`、`AGND`、`PGND` 都算，
#: 但 `B-` 这种不认（它是电池负极，可能是负压采样点，不等于系统地）。
_GROUND_RE = re.compile(r"^(A|D|P|S)?GND[A-Z0-9_]*$|^(VSS|0V|GROUND|EARTH)[A-Z0-9_]*$")

#: 网络名里出现这些词就认它是电源轨（即使 `ladder.net_volts` 判不出电压）。
#: 判不出的（`VIN`/`VBAT`）仍然算**轨**，只是不知道几伏——
#: 对"这是个去耦电容"这个结论，电压未知不影响它是去耦电容。
_RAIL_WORD_RE = re.compile(
    r"^(VIN|VBUS|VBAT[A-Z0-9_]*|B\+|VCC[A-Z0-9_]*|VDD[A-Z0-9_]*|"
    r"[A-Z0-9_]*3V3[A-Z0-9_]*|[A-Z0-9_]*5V[A-Z0-9_]*|"
    r"RF-?3\.?3V|WIFI3V3|LGS_VCC|D\+|D-)$")

#: 自动生成的网络名。**没有语义**。两种导入器各有各的写法：
#:
#:   `$1N8009`  立创 EDA 未命名网络（实测 `示例-单板` 119 条网里 55 条）
#:   `w1`/`w12` Wokwi 连线（实测 `001-blink` 全部是这种）
#:
#: 两者都**不是**"有名字的网络"。第一版只认 `$` 前缀，于是
#: `001-blink` 的 `w1`/`w2` 被当成有语义的信号名，理由里出现
#: "串在 w1 与 w2 之间"——读的人以为那是什么信号，其实那是导入器
#: 随手编的序号。
_AUTO_NET_RE = re.compile(r"^\$|^[wW]\d+$")


def is_ground(net: str) -> bool:
    return bool(_GROUND_RE.match(str(net or "").strip().upper()))


def is_rail(design: DesignModel, net: str) -> bool:
    """这条网络是不是电源轨。

    两条判据取"或"：
      1. `ladder.net_volts()` 能从名字/申报推出电压 → 它是轨；
      2. 名字里带明确的轨词（`VIN`/`VBAT_2S`/`B+`）→ 它是轨，只是电压未知。

    **地不算轨**——分开判，因为"去了地"和"去了电源"是两种完全不同的
    作用，混在一起会把下拉电阻说成上拉。
    """
    if is_ground(net):
        return False
    if ladder_module.net_volts(design, net) is not None:
        return True
    return bool(_RAIL_WORD_RE.match(str(net or "").strip().upper()))


def is_signal(net: str) -> bool:
    """有语义的**命名**信号网（排除地和自动网名）。"""
    return not is_ground(net) and not _AUTO_NET_RE.match(str(net or ""))


def is_named(net: str) -> bool:
    """这条网络**有没有名字**。地也算有名字。

    和 `is_signal` 分开：`is_signal` 是"它是哪类网"，`is_named` 是
    "我从这条网上能不能读出语义"。自动网名（`$1N394`）没有语义，
    但它是**拓扑上的真实节点**——下面 `trace_named()` 就靠它是真节点
    这一点，穿过串联无源件去够一条有名字的网。
    """
    return is_ground(net) or not _AUTO_NET_RE.match(str(net or ""))


def trace_named(design: DesignModel, start: str, *,
                max_hops: int = 2) -> tuple[str, list[str]] | None:
    """从一条无名网络出发，**穿过串联无源件**，找最近的有名网络。

    这是本模块的核心算法，其余规则都是它的消费者。

    ## 为什么必须做这一步

    立创 EDA 导出时会把中间节点自动命名成 `$1N394`。那里没有语义，
    但**中间节点不等于没有信息**：`$1N394` 经 `R90` 连到 `GND`，
    `$1N399` 经 `R91` 连到 `SCL`。把这两跳接起来就知道
    `U10` 那三个脚分别是什么。

    只看一跳（只问"这条网还挂了谁"）得到的是另一堆无名网，等于没问。
    实测 `示例-单板`：141 个元件里 55 条网是自动名，不穿透的话
    所有挂在中间的器件都只能报"判不出"。

    ## 穿什么、不穿什么

    **只穿无源件**（电阻/电容/电感/磁珠）。这些件在信号路径上是透明的
    ——它们改变幅度或阻抗，不改变"这是哪条信号"。绝不穿 IC/连接器：
    那是功能边界，穿过去会把两个不相干的功能域连成一条。

    ## 返回值

    `(最近的有名网名, 走过的路径)`。路径里的人话形式是
    `$1N399 -R91-> SCL`，直接进理由，让结论可以被人复核。
    找不到返回 None——**不猜**。
    """
    queue: list[tuple[str, list[str]]] = [(start, [])]
    seen = {start}
    while queue:
        net, path = queue.pop(0)
        if len(path) >= max_hops * 2:
            continue
        for part in design.parts:
            if category_of(part) not in PASSIVE_CATEGORIES:
                continue
            pnets = claims_module.nets_of(design, part.ref)
            if net not in pnets:
                continue
            for other in pnets:
                if other == net or other in seen:
                    continue
                step = f"{net} -{part.ref}-> {other}"
                if is_named(other):
                    return other, path + [step]
                seen.add(other)
                queue.append((other, path + [step]))
    return None


def rail_volts(design: DesignModel, net: str) -> tuple[float, str] | None:
    """轨电压 + 依据。判不出返回 None（不猜）。"""
    got = ladder_module.net_volts(design, net)
    if got is None:
        return None
    return got.volts, got.basis


def _net_label(design: DesignModel, net: str) -> str:
    """网名 + 电压（知道的话）。让理由具体到「5V 轨（5.0V）」。"""
    got = rail_volts(design, net)
    return f"{net} 轨（{got[0]:g}V）" if got else f"{net} 轨"


# ---------------------------------------------------------------------------
# 元件分类：从 ref / label 读**结构**，不读语义
# ---------------------------------------------------------------------------

#: 标称值解析：`1kΩ` / `4.7k` / `100R` / `22Ω` / `82mΩ` / `1uF` / `100nF` / `10uH`
_VALUE_RE = re.compile(
    r"^\s*([0-9]*\.?[0-9]+)\s*([pnuµmkKMGR]?)\s*(Ω|OHM|R|F|H)?\s*$", re.I)

_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3,
       "": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}


def parse_value(label: str) -> tuple[float, str] | None:
    """把 `label` 解析成（数值，单位）。**解析不出返回 None，不猜。**

    单位归一：`Ω`/`OHM`/`R` 全部归成 `R`。这一步必须做——
    实测 bug：`82mΩ` 解析出单位 `Ω`，而下游判据写的是
    `value[1] in ("", "R")`，于是这个 82mΩ 采样电阻被当成普通串联电阻，
    理由从"电流采样"退化成"限流/分压/端接"。**一个单位别名就能让
    结论错一个类别**，所以归一放在这里，不放给每个调用方各自处理。
    """
    text = str(label or "").strip()
    m = _VALUE_RE.match(text)
    if not m:
        return None
    number, prefix, unit = m.group(1), m.group(2), m.group(3)
    try:
        base = float(number)
    except ValueError:
        return None
    if prefix not in _SI:
        return None
    raw = (unit or "").upper()
    if raw in ("Ω", "OHM", "R"):
        raw = "R"
    return base * _SI[prefix], raw


#: 立创 EDA 导入器在拿不到值时会把**哈希**写进 label
#: （`507537214f1516dc`）。那不是值，是一个"我不知道"。
_HASH_LABEL_RE = re.compile(r"^[0-9a-f]{14,32}$", re.I)


def _looks_like_hash(text: str) -> bool:
    return bool(_HASH_LABEL_RE.match(str(text or "").strip()))


def shown_value(part: Part, unit: str = "F") -> str:
    """给人看的元件值。**哈希一律显示成"值未知"**。

    实测 bug：`C67` 的 label 是 `507537214f1516dc`，第一版把它直接
    拼进理由，于是报告里出现"507537214f1516dc 复位电路的滤波电容"——
    一段看不出意义的十六进制被当成容值呈现给读者。**伪具体比笼统更糟**：
    笼统会让人去查，伪具体会让人以为已经知道了。

    `unit`：`F` 电容 / `R` 电阻 / `H` 电感。
    """
    label = str(getattr(part, "label", "") or "").strip()
    if not label or _looks_like_hash(label):
        return {"F": "容值未知", "R": "阻值未知", "H": "感值未知"}.get(
            unit, "值未知")
    parsed = parse_value(label)
    if parsed is None:
        return label                       # 解析不出原样显示（如 `32.768kHz`）
    value, got_unit = parsed
    if unit == "F":
        if got_unit == "F":
            return (f"{value * 1e6:g}µF" if value >= 1e-6
                    else f"{value * 1e9:g}nF")
        return label
    if unit == "R":
        if got_unit in ("R", ""):
            return (f"{value / 1000:g}kΩ" if value >= 1000
                    else f"{value * 1000:g}mΩ" if value < 1
                    else f"{value:g}Ω")
        return label
    if unit == "H":
        if got_unit == "H":
            return (f"{value * 1e6:g}µH" if value >= 1e-6
                    else f"{value * 1e9:g}nH")
        return label
    return label


def ref_prefix(ref: str) -> str:
    """`R12` → `R`，`LEDCAR` → `LED`，`USB1` → `USB`，`U1024` → `U`。

    取**开头连续的字母**。所以 `LEDCAR` 与 `LED12` 都归到 `LED`，
    这是对的：它们都是发光二极管，只是编号方式不同（实测立创 EDA
    导出的 `示例-单板` 里同时存在 `LED1..LED9` 和 `LEDCAR`/`LEDHOME`）。
    """
    m = re.match(r"^([A-Za-z]+)", str(ref or ""))
    return m.group(1).upper() if m else ""


#: ref 前缀 → 元件类别。**这是结构分类，不是语义猜测**——
#: 电阻电容电感的参考号前缀是行业约定，不是从哈希猜功能。
_CATEGORY = {
    "R": "resistor", "C": "capacitor", "L": "inductor",
    "D": "diode", "LED": "led", "Q": "transistor", "X": "crystal",
    "Y": "crystal", "FB": "ferrite", "F": "fuse", "SW": "switch",
    "K": "relay", "T": "transformer", "BZ": "buzzer", "SP": "speaker",
    "M": "motor", "BT": "battery", "FAN": "fan",
}

#: 无源件：**按组归因**（单个 0402 不需要独立理由）。
PASSIVE_CATEGORIES = frozenset(
    {"resistor", "capacitor", "inductor", "ferrite"})


def category_of(part: Part) -> str:
    """元件类别。

    **三条线索，顺序就是可信度**：
      1. `ref` 前缀（`R12`→电阻）——行业约定，最可靠
      2. `kind` 里的**真实类型名**（`C0603`→电容、`wokwi-resistor`→电阻、
         `camera_module`→模组）——Wokwi/KiCad/hw_velxio 自己生成的工程
         都有可读的 kind
      3. 都判不出 → `unknown`（立创 EDA 的 `0deda055e6014e4e` 哈希）

    ## 第 2 条为什么必须加

    第一版只认 ref 前缀，于是 `示例-无人机` 的 44 个元件全是 `C1..Rn`
    加 `kind="C0603"`——ref 前缀本该命中，但那个工程的 ref 是**大写
    `C` 却只有 `kind=C0603` 没有网表**，另一个问题。真正的证据是
    `r3demo`：4 个元件全是 `kind=camera_module`/`gimbal_servo`/`psu_5v`
    这种**完全可读**的类型名，却因为 `ref` 是 `CAM1`/`SV1` 而全部
    落到 `unknown`。有类型名不用，等于假装信息不存在。

    第 2 条只在 `ref` 前缀判不出时兜底——ref 前缀更可靠，因为它记录
    的是设计者填参考号时的意图。
    """
    by_ref = _CATEGORY.get(ref_prefix(part.ref))
    if by_ref is not None:
        return by_ref

    haystack = " ".join(str(x or "") for x in (
        getattr(part, "kind", ""), getattr(part, "role", ""),
        getattr(part, "label", ""))).lower()
    if not haystack.strip():
        return "unknown"
    for category, words in _KIND_HINTS:
        if any(w in haystack for w in words):
            return category
    return "unknown"


#: `kind`/`role`/`label` 里的类型词 → 类别。**只在 ref 前缀判不出时兜底。**
#:
#: 注意区分"类型名"与"哈希"：`camera_module` 是类型名，
#: `0deda055e6014e4e` 是哈希。这里列的都是类型名，哈希一个都命中不了，
#: 于是落到 `unknown`——那正是我们要的结果。
_KIND_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("resistor", ("resistor", "res", "电阻", " C0603-")),
    ("capacitor", ("capacitor", "cap_", "ceramic", "电解", " c0402", " c0603",
                   " c0805", " c1206")),
    ("inductor", ("inductor", "power_inductor")),
    ("ferrite", ("ferrite", "bead")),
    ("diode", ("diode",)),
    ("led", ("led", "ws2812", "rgb")),
    ("transistor", ("transistor", "mosfet", " bjt")),
    ("crystal", ("crystal", "oscillator", "xtal")),
    ("connector", ("connector", "header", "socket", "usb_c", "usb-c")),
    ("battery", ("battery", "li-ion", "lipo")),
    ("buzzer", ("buzzer", "speaker", "beeper")),
    ("motor", ("motor", "servo", "gimbal")),
    ("camera", ("camera", "sensor_ov", "ov2640", "ov5640")),
    # 开关/按键：`wokwi-pushbutton`、`tactile`、`switch`。
    # ref 前缀 `SW` 已经能命中，但 Wokwi 的 `btn1` 前缀是 `BTN`，命不中。
    ("switch", ("pushbutton", "push_button", "button", "tactile",
                "key_", "keyswitch")),
    ("module", ("module", "board", "mcu", "esp32", "stm32", "arduino")),
)


def _pin_count(part: Part) -> int:
    """引脚数。优先 `pins`（导入器实测），其次 `pads`。都没有返回 0。"""
    if part.pins:
        return len(part.pins)
    if part.pads:
        return len(part.pads)
    return 0


# ---------------------------------------------------------------------------
# 引脚方向三档推断（`Part.pins` 没有方向，见 model.py 的 pins 字段注释）
# ---------------------------------------------------------------------------

#: 名字里带这些就是输出（按命名约定，不是按 datasheet）。
_OUT_WORDS = ("TX", "OUT", "DO", "MOSI", "SCK", "SCL", "CLK", "PWM", "NSS", "CS")
_IN_WORDS = ("RX", "IN", "DI", "MISO", "GND", "VCC", "VDD", "VSS", "EN", "NRST")


def is_driver(design: DesignModel, pinref: dict[str, str]) -> bool | None:
    """这个引脚是输出吗？**三档，第三档返回 None（不猜）**。

    1. `ref == "board"` → 板卡引脚，约定为输出（它是主机侧）
    2. 引脚名含 `TX`/`OUT`/`SCL`... → 输出；含 `RX`/`IN`/`GND`... → 输入
    3. 其余（纯数字焊盘号 `1`/`2`/`3`） → None

    实测 `示例-单板`：绝大部分引脚是纯数字（`{"ref":"C1","pin":"1"}`），
    所以这一档**大量返回 None**。这是诚实的——立创 EDA 的焊盘号本来
    就不携带方向。要真方向得走 P8 的 `.ioc` 引脚映射（`PA2=USART2_TX`）。
    """
    ref = str(pinref.get("ref") or "")
    if ref == "board":
        return True
    name = str(pinref.get("pin") or "").upper()
    if not name:
        return None
    if any(w in name for w in _OUT_WORDS):
        return True
    if any(w in name for w in _IN_WORDS):
        return False
    return None


# ---------------------------------------------------------------------------
# 归因结论
# ---------------------------------------------------------------------------

@dataclass
class Attribution:
    """一个元件的归因结果 + 它属于哪个功能组（给分组用）。"""

    ref: str
    category: str
    justification: Justification
    #: 分组键：「同一类作用 + 同一目标」。`("decoupling", "3.3V")`
    group: tuple[str, str] = ("unknown", "")
    #: 参与结论的具体网名（用于 `_cited()` 自检）
    nets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "category": self.category,
            "group": {"kind": self.group[0], "target": self.group[1]},
            "nets": list(self.nets),
            "justification": self.justification.to_dict(),
        }


def _j(why: str, effect: str, *,
       confidence: str = "inferred_by_topology",
       source: str = "ai", evidence: str = "") -> Justification:
    return Justification(why=why, effect_if_removed=effect,
                         confidence=confidence, source=source,
                         evidence=evidence)


# ---------------------------------------------------------------------------
# 规则表
# ---------------------------------------------------------------------------
#
# 每条规则：(名字, 谓词, 求解函数)。
#
# **顺序有意义**：越具体的越靠前。`晶振` 必须在 `去耦电容` 之前，
# 否则一个 3 脚的晶振会被当成"到地的电容"。`LED 限流电阻` 必须在
# `串联电阻` 之前，否则会退化成"串在两条信号之间"。

RuleResult = tuple[Justification, tuple[str, str], list[str]] | None


def _rule_unconnected(design: DesignModel, part: Part,
                      nets: list[str]) -> RuleResult:
    """没有接任何网络。"""
    if nets:
        return None
    kind = ("机械件（安装孔/支架类，不参与电气）"
            if part.placement.mount != "board" else
            "未连接到任何网络")
    return (_j(f"{part.ref} 不接任何网络：{kind}",
               "无电气影响；但若是漏连，功能会整体缺失",
               confidence="unknown" if part.placement.mount == "board"
               else "inferred_by_topology"),
            ("unconnected", ""), [])


def _rule_crystal(design: DesignModel, part: Part,
                  nets: list[str]) -> RuleResult:
    """晶振：一个地 + 两个信号，或标签是可解析的频率。"""
    cat = category_of(part)
    looks_freq = bool(re.search(r"\d+(\.\d+)?\s*[kM]Hz", str(part.label or ""), re.I))
    if cat != "crystal" and not looks_freq:
        return None
    grounds = [n for n in nets if is_ground(n)]
    signals = [n for n in nets if is_signal(n)]
    if not signals:
        return None
    freq = ""
    m = re.search(r"([0-9.]+)\s*([kM])Hz", str(part.label or ""), re.I)
    if m:
        freq = f"{m.group(1)}{m.group(2).upper()}Hz"
    target = "/".join(signals)
    why = (f"{freq + ' 的' if freq else ''}石英晶体，接在 {target} 上"
           f"（{len(grounds)} 个接地脚做屏蔽）")
    return (_j(why, f"{target} 不再有振荡源：MCU 不起振或退回内部 RC，"
                     "所有依赖该时钟的波特率/定时一起偏",
               evidence=f"nets={nets}, label={part.label}"),
            ("clock_source", target), signals)


def _rule_decoupling(design: DesignModel, part: Part,
                     nets: list[str]) -> RuleResult:
    """去耦/储能电容：一端地，另一端电源轨。"""
    if category_of(part) != "capacitor" or len(nets) != 2:
        return None
    grounds = [n for n in nets if is_ground(n)]
    rails = [n for n in nets if is_rail(design, n)]
    if len(grounds) != 1 or len(rails) != 1:
        return None
    rail = rails[0]
    value = parse_value(part.label)
    farads = value[0] if value and value[1] == "F" else None
    # 同轨上还有几个电容：说出来，理由才具体。
    siblings = _caps_on_rail(design, rail)
    bulk = farads is not None and farads >= 1e-6
    role = "储能/低频滤波" if bulk else "高频去耦"
    shown = shown_value(part, "F")
    why = (f"{_net_label(design, rail)} 的{role}电容（{shown}），"
           f"一端接 {grounds[0]}；该轨共 {siblings} 个电容")
    return (_j(why,
               f"{rail} 在该点失去一个{role}通路，IC 供电的瞬态电流要"
               "绕更远的路径取，纹波与地弹上升",
               evidence=f"nets={nets}"),
            ("decoupling", rail), [rail, grounds[0]])


def _caps_on_rail(design: DesignModel, rail: str) -> int:
    """同一电源轨上挂了多少个电容（无源件按组的量化依据）。"""
    count = 0
    for entry in design.nets:
        if str(entry.get("net") or "") != rail:
            continue
        refs = {str(p.get("ref") or "") for p in entry.get("pins", [])
                if isinstance(p, dict)}
        for part in design.parts:
            if part.ref in refs and category_of(part) == "capacitor":
                count += 1
        break
    return max(count, 1)


def _rule_pull(design: DesignModel, part: Part,
               nets: list[str]) -> RuleResult:
    """上拉/下拉电阻：一端电源轨/地，另一端信号。"""
    if category_of(part) != "resistor" or len(nets) != 2:
        return None
    grounds = [n for n in nets if is_ground(n)]
    rails = [n for n in nets if is_rail(design, n)]
    signals = [n for n in nets if is_signal(n)
               and n not in grounds and n not in rails]
    if len(signals) != 1:
        return None
    signal = signals[0]
    value = parse_value(part.label)
    ohms = value[0] if value and value[1] in ("", "R") else None
    shown = shown_value(part, "R")
    # I2C 上拉是**功能必需**（开漏输出没有上拉就没有高电平），
    # 和其他信号上拉不是一回事，必须说清楚。
    i2c = _is_i2c_pair(design, signal)
    if grounds and len(grounds) == 1:
        why = (f"{shown} 下拉电阻：把 {signal} 在无驱动时钳到 {grounds[0]}")
        effect = f"{signal} 悬空，电平不确定"
        if i2c:
            effect = f"{signal} 是 I2C 总线，缺上拉/下拉会让总线无法建立确定电平"
        return (_j(why, effect, evidence=f"nets={nets}, R={part.label}"),
                ("pulldown", signal), [signal, grounds[0]])
    if len(rails) == 1:
        why = (f"{shown} 上拉电阻：把 {signal} 在无驱动时拉到 "
               f"{_net_label(design, rails[0])}")
        effect = (f"{signal} 悬空，电平不确定"
                  if not i2c else
                  f"{signal} 是 I2C 总线（开漏），**没有上拉就没有高电平**，"
                  "总线完全无法通信")
        return (_j(why, effect, evidence=f"nets={nets}, R={part.label}"),
                ("pullup", signal), [signal, rails[0]])
    return None


def _is_i2c_pair(design: DesignModel, signal: str) -> bool:
    upper = signal.upper()
    if upper.startswith(("SDA", "SCL")):
        return True
    return False


def _rule_shunt(design: DesignModel, part: Part,
                nets: list[str]) -> RuleResult:
    """电流采样电阻：阻值 < 1Ω 的电阻。"""
    if category_of(part) != "resistor" or len(nets) != 2:
        return None
    value = parse_value(part.label)
    if not value or value[1] not in ("", "R"):
        return None
    ohms = value[0]
    if ohms >= 1.0 or ohms <= 0:
        return None
    a, b = nets
    why = (f"{ohms * 1000:g}mΩ 采样电阻，串在 {a} 与 {b} 之间："
           "阻值远小于回路其它阻抗，只有在测电流时才需要这么小")
    return (_j(why,
               f"{a} 与 {b} 之间断开，电流检测/保护失去输入；"
               "若这是主回路，整条供电链不通",
               evidence=f"nets={nets}, R={part.label}"),
            ("current_sense", f"{a}|{b}"), [a, b])


def _rule_led_series(design: DesignModel, part: Part,
                     nets: list[str]) -> RuleResult:
    """LED 限流电阻：与某个 LED 共网，自身另一端是驱动信号或轨。"""
    if category_of(part) != "resistor" or len(nets) != 2:
        return None
    led_refs = _leds_sharing(design, part.ref, nets)
    if not led_refs:
        return None
    other = [n for n in nets if n not in _nets_of_any(design, led_refs)]
    drive = other[0] if other else nets[0]
    value = parse_value(part.label)
    ohms = value[0] if value and value[1] in ("", "R") else None
    shown = shown_value(part, "R")
    why = (f"{shown} 限流电阻：串在 {drive} 与 {led_refs[0]} 之间，"
           "把 LED 的正向电流限制在允许范围内")
    return (_j(why,
               f"{led_refs[0]} 直接接 {drive}，正向电流只受自身内阻限制，"
               "可能超额定而烧毁；驱动脚也可能过流",
               evidence=f"nets={nets}, 共网 LED={led_refs}"),
            ("led_limit", drive), [drive] + nets)


def _leds_sharing(design: DesignModel, ref: str,
                  nets: list[str]) -> list[str]:
    """与 `ref` 共网且类别是 LED（或 ref 前缀是 LED）的元件。"""
    wanted: set[str] = set()
    for entry in design.nets:
        if str(entry.get("net") or "") not in nets:
            continue
        for p in entry.get("pins", []):
            if not isinstance(p, dict):
                continue
            other = str(p.get("ref") or "")
            if other and other != ref:
                wanted.add(other)
    out: list[str] = []
    for part in design.parts:
        if part.ref not in wanted:
            continue
        if category_of(part) == "led" or str(part.kind).upper().startswith("LED"):
            out.append(part.ref)
    return sorted(out)


def _nets_of_any(design: DesignModel, refs: list[str]) -> set[str]:
    out: set[str] = set()
    for ref in refs:
        out.update(claims_module.nets_of(design, ref))
    return out


def _rule_series(design: DesignModel, part: Part,
                 nets: list[str]) -> RuleResult:
    """串联电阻：两端都不是地，也都不是电源。"""
    if category_of(part) != "resistor" or len(nets) != 2:
        return None
    if any(is_ground(n) or is_rail(design, n) for n in nets):
        return None
    a, b = nets
    usb = _looks_usb(nets)
    value = parse_value(part.label)
    ohms = value[0] if value and value[1] in ("", "R") else None
    shown = shown_value(part, "R")
    if usb:
        why = (f"{shown} USB 串阻，串在 {a} 与 {b} 之间做源端匹配/限流")
        effect = "USB 信号完整性变差（反射/过冲），高速时可能枚举失败"
        kind = "usb_series"
    else:
        why = f"{shown} 串联电阻，串在 {a} 与 {b} 之间（限流/分压/端接）"
        effect = f"{a} 与 {b} 直连：电流或边沿不受控，可能过流或过冲"
        kind = "series"
    return (_j(why, effect, evidence=f"nets={nets}, R={part.label}"),
            (kind, f"{a}|{b}"), [a, b])


def _looks_usb(nets: list[str]) -> bool:
    joined = " ".join(nets).upper()
    return any(t in joined for t in ("DP", "DM", "D+", "D-", "USB"))


def _rule_rail_link(design: DesignModel, part: Part,
                    nets: list[str]) -> RuleResult:
    """跨两个电源轨的元件：磁珠 / 0Ω 跳线 / 理想二极管。"""
    if len(nets) != 2:
        return None
    if category_of(part) not in ("inductor", "ferrite", "resistor"):
        return None
    rails = [n for n in nets if is_rail(design, n)]
    if len(rails) != 2:
        return None
    a, b = rails
    cat = category_of(part)
    if cat == "resistor":
        value = parse_value(part.label)
        if not value or value[0] > 1.0:
            return None
        what = f"{value[0]:g}Ω 跳线"
    elif cat == "inductor":
        value = parse_value(part.label)
        shown = shown_value(part, "H")
        what = f"{shown} 电感"
    else:
        what = "磁珠"
    why = (f"{what}连接 {a} 与 {b} 两条电源轨："
           "直流导通、高频隔离（单点接地或电源域隔离）")
    return (_j(why,
               f"{a} 与 {b} 之间没有直流通路，下游整片电路失电",
               evidence=f"nets={nets}, label={part.label}"),
            ("rail_link", f"{a}|{b}"), [a, b])


def _rule_led(design: DesignModel, part: Part,
              nets: list[str]) -> RuleResult:
    """LED 本体。四种拓扑，按可判程度排序。"""
    if category_of(part) != "led":
        return None
    signals = [n for n in nets if is_signal(n) and not is_rail(design, n)]
    grounds = [n for n in nets if is_ground(n)]
    # WS2812 这类可寻址灯：网名里带 2812，或 4 个以上网络（VDD/DIN/DOUT/GND）
    addr = any("2812" in n.upper() for n in nets) or len(nets) >= 4
    if addr:
        data = [n for n in nets if "2812" in n.upper()]
        why = (f"可寻址 RGB LED（{'/'.join(data) if data else '4 线'}）："
               f"串在灯链上，网络 {', '.join(nets)}")
        effect = "灯链从该点断开，其后所有灯不再接收数据"
        target = "/".join(signals) or "addr"
    elif len(signals) == 1:
        why = (f"发光二极管，接在 {signals[0]} 上"
               f"（另一端 {', '.join(grounds) or '非地'}）")
        effect = f"{signals[0]} 的状态指示消失，操作者失去该反馈"
        target = signals[0]
    elif len(nets) >= 3 and grounds:
        # 3 条网 + 一个地：典型是"GND + 数据网 + 另一条数据网"，
        # 实测 `LED9` 是 `GND / $1N8009 / $1N8411`——灯链的中间级
        # （数据线进、数据线出）。这类只要说清"链上的一段"就够，
        # 不必强判 DIN/DOUT（网名是自动生成的，判不出来）。
        data = [n for n in nets if n not in grounds]
        why = (f"发光二极管，3 个网络（{', '.join(data)} + {grounds[0]}）："
               "灯链的串接节点，数据从一个网络进、从另一个网络出")
        effect = f"灯链从 {part.ref} 处断开：其后的灯不再收到数据"
        target = "/".join(data)
    elif len(nets) == 2:
        # 两端都是无名网：典型的"MOS 漏极 → LED → 限流电阻 → 轨"链中的一段。
        # 拓扑上能确定的只有一点：它是这条链上唯一的发光元件。
        a, b = nets
        why = (f"发光二极管，串在 {a} 与 {b} 两个无名网之间："
               "属于某条由开关管驱动的指示链，链上其余环节见共网元件")
        effect = "该指示链断开，对应的状态灯不亮"
        target = f"{a}|{b}"
    else:
        return None
    return (_j(why, effect, evidence=f"nets={nets}"),
            ("indicator", target), nets)


def _rule_switch(design: DesignModel, part: Part,
                 nets: list[str]) -> RuleResult:
    """三极管/MOS：一端地，另两端分别是被控负载与控制信号。

    **不强制有地**：实测 `Q2` 接在 `VIN` / `VBAT_2S` / `$1N10410` 上，
    没有地——它是高端开关（P-MOS / 理想二极管），把电池接到主轨。
    第一版要求必须有地，于是 Q2 落到了兜底的"仅接电源"规则，
    理由变成"VIN 上少一个负载"——它其实是**决定整机有没有电的那个管子**。
    这是把"我没想到"说成"它没什么用"，比不判更糟。
    """
    if category_of(part) != "transistor":
        return None
    grounds = [n for n in nets if is_ground(n)]
    rails = [n for n in nets if is_rail(design, n)]

    # 高端开关：两端是电源轨（或无名的中间网），没有地。
    # **这个分支必须在 len(others) 检查之前**：实测 `Q2` 有 3 条网
    # （VIN / VBAT_2S / $1N10410），按"三脚必须有地"的假设直接不匹配，
    # 于是它被兜底规则说成"VIN 上少一个负载"。它其实是主电源开关。
    if not grounds and len(rails) >= 2:
        upstream = next((n for n in nets
                         if re.match(r"^(VBAT|B\+|VIN)", n.upper())), rails[0])
        downstream = next((n for n in nets if n != upstream
                           and not is_rail(design, n)), None)
        if downstream is None:
            # 没有无名中间网：两个轨直接受它控制
            downstream = next(n for n in rails if n != upstream)
        why = (f"高端开关：以 {_net_label(design, upstream)} 为输入，"
               f"控制是否把电送到 {_net_label(design, downstream)}"
               f"（无地脚，属于高边拓扑）")
        return (_j(why,
                   f"{downstream} 永远得不到电：整条主供电路径断开，"
                   f"挂在该轨上的所有电路不工作"
                   if is_rail(design, downstream) else
                   f"{downstream} 无法被连到 {upstream}：其下游整片失电",
                   evidence=f"nets={nets}, 无地脚→高端拓扑"),
                ("high_side_switch", downstream), nets)

    others = [n for n in nets if n not in grounds]
    if len(others) != 2 or len(grounds) != 1:
        return None
    load, control = others
    # 控制端往往接在带名字的信号上（`LED_EN`/`BEEPER`），负载端接在
    # 自动网名上（MOS 漏极到灯）。这里只按"哪一个名字更有语义"排序，
    # 命名不出来就保持原序并**在理由里说明不确定**。
    if _AUTO_NET_RE.match(control) and not _AUTO_NET_RE.match(load):
        load, control = control, load
    why = (f"低边开关：以 {control} 为控制端，把 {load} 负载网拉到 {grounds[0]}")
    return (_j(why,
               f"{load} 无法被拉到地，该负载（LED/蜂鸣器/继电器）失去"
               "开通能力；控制端驱动脚可能因失去负载而状态未定",
               evidence=f"nets={nets}"),
            ("low_side_switch", control), nets)


def _rule_diode(design: DesignModel, part: Part,
                nets: list[str]) -> RuleResult:
    """二极管：两轨之间（防倒灌/或门），或轨到地（钳位/续流）。"""
    if category_of(part) != "diode" or len(nets) != 2:
        return None
    rails = [n for n in nets if is_rail(design, n)]
    grounds = [n for n in nets if is_ground(n)]
    if len(rails) == 2:
        a, b = rails
        why = (f"两个电源轨 {a} 与 {b} 之间的单向导通器件："
               "允许一方给另一方供电而不许倒灌（或门/防反接）")
        effect = (f"{a} 与 {b} 之间失去单向通路：要么整条供电路径断开，"
                  "要么两路电源互相倒灌")
        kind = "power_or"
    elif len(rails) == 1 and len(grounds) == 1:
        why = f"接在 {rails[0]} 与 {grounds[0]} 之间的钳位/续流二极管"
        effect = f"{rails[0]} 上的负向尖峰失去泄放路径，可能击穿下游器件"
        kind = "clamp"
    else:
        return None
    return (_j(why, effect, evidence=f"nets={nets}"),
            (kind, "/".join(rails)), nets)


def _rule_switching(design: DesignModel, part: Part,
                    nets: list[str]) -> RuleResult:
    """开关电源的功率电感：一端电源轨，另一端叫 `SW`/`LX`/`PHASE`。

    实测 `L6`（10uH）接在 `VBUS` 与 `SW` 之间——这是 buck/boost 的
    储能电感。第一版没有这条规则，它落到兜底 unknown。
    """
    if category_of(part) not in ("inductor",):
        return None
    if len(nets) != 2:
        return None
    rails = [n for n in nets if is_rail(design, n)]
    if len(rails) != 1:
        return None
    rail = rails[0]
    other = next(n for n in nets if n != rail)
    if not re.search(r"^(SW|LX|PHASE|SWITCH)[A-Z0-9_]*$",
                     other.strip().upper()):
        return None
    value = parse_value(part.label)
    shown = shown_value(part, "H")
    why = (f"开关电源的储能电感（{shown}）：接在 {_net_label(design, rail)} "
           f"与开关节点 {other} 之间，负责在开关周期内传递能量")
    return (_j(why,
               "开关电源完全停止工作：输出电压归零，"
               "且开关管失去电流通路可能过流损坏",
               evidence=f"nets={nets}, label={part.label}"),
            ("power_inductor", other), nets)


def _rule_signal_cap(design: DesignModel, part: Part,
                     nets: list[str]) -> RuleResult:
    """到地的电容挂在信号网上：复位/去抖/滤波。

    实测 `示例-单板` 有 12 个这种件（`C67`→`NRST`、`C87`→`BTN_HOME`、
    `C89`→`BOOT0`、`C74/C75`→`OSC_IN/OSC_OUT`）。第一版没有这条规则，
    它们的理由是"拓扑特征不匹配任何已知规则"——**这句话等于没说**。
    一个电容一端接地、另一端接某个有名信号，拓扑上就能确定它在那条
    信号上做时间常数，剩下的只是"这条信号是复位还是按键"的差别，
    而那由网名说得很清楚。
    """
    if category_of(part) != "capacitor" or len(nets) != 2:
        return None
    grounds = [n for n in nets if is_ground(n)]
    if len(grounds) != 1:
        return None
    other = next(n for n in nets if n != grounds[0])
    if is_rail(design, other):
        return None                      # 那是去耦，由 decoupling 管
    upper = other.upper()
    if "NRST" in upper or "RESET" in upper:
        kind, role = "reset_cap", "复位电路的滤波/延时电容"
        effect = (f"{other} 失去滤波：复位脚可能被噪声误触发，"
                  "MCU 随机重启或无法可靠复位")
    elif upper.startswith(("BTN", "KEY", "SW", "S2", "S3")):
        kind, role = "debounce_cap", "按键去抖电容"
        effect = (f"{other} 失去去抖：一次按键会被识别成多次，"
                  "触发重复动作")
    elif upper.startswith(("OSC", "XTAL")):
        kind, role = "osc_cap", "晶振负载电容"
        effect = (f"{other} 的负载电容改变：振荡频率偏移甚至不起振，"
                  "所有依赖该时钟的定时全部不准")
    elif upper.startswith(("BOOT",)):
        kind, role = "boot_cap", "启动模式滤波电容"
        effect = f"{other} 在复位沿可能被干扰，芯片进入非预期启动模式"
    else:
        kind, role = "signal_cap", "信号滤波电容"
        effect = f"{other} 上的高频噪声失去本地泄放通路"
    value = parse_value(part.label)
    shown = shown_value(part, "F")
    why = (f"{shown} {role}：接在 {other} 与 {grounds[0]} 之间，"
           f"给 {other} 这条信号一个对地的时间常数")
    return (_j(why, effect, evidence=f"nets={nets}, label={part.label}"),
            (kind, other), nets)


def _rule_two_terminal(design: DesignModel, part: Part,
                       nets: list[str]) -> RuleResult:
    """两脚器件，一端接**有名信号**，另一端追到地或电源轨。

    ## 这条规则为什么能下确定结论

    一个两脚元件，一端是 `BTN_HOME` 这样的有名信号，另一端经无源件
    能追到 `GND`——它只可能是**把那条信号拉到地的开关**。这不是猜，
    是两脚器件在这两个网络之间的全部可能性：要么开关（可变连通），
    要么固定分压（但另一端是地就不是分压）。

    同理，一端是信号、另一端是电源轨，就是**上拉**。

    ## 为什么不覆盖纯无源件

    电阻/电容/电感有自己的规则（`pull`/`decoupling`/`signal_cap`），
    它们能说到"多大的电阻"这一层。这里只处理**类别判不出**的件
    （哈希 ref + 哈希 kind 的模块），因为对它们，这个结论已经是
    从拓扑能得到的全部。

    实测：`U1026`（`BTN_CHANG_MODE` + 无名网）、`U1028`（`BTN_HOME` +
    无名网）、`U1029`（`3.3V` + `BOOT0`）都落在这里。
    """
    if len(nets) != 2:
        return None
    cat = category_of(part)
    if cat in PASSIVE_CATEGORIES or cat in ("transistor", "diode"):
        return None                        # 有更具体的规则管
    if _pin_count(part) > 3:
        return None                        # 3 脚以上不再是"两端器件"

    # 两条网**都可能是有名的**：`U1029` 就是 `3.3V` + `BOOT0`，
    # 两条都有名字。所以不能要求"恰好一条有名网"——那会把最清楚的
    # 情况（一条轨 + 一条信号）排除在外。
    #
    # 但 `signals` **必须只收有名网**：自动名（`$1N10582`）不是信号，
    # 它是还没追的中间节点。把它算进 `signals` 会让
    # `U1026`（`BTN_CHANG_MODE` + `$1N10582`）数出两条信号而落空——
    # 实测就是这么丢掉两条的。
    rails = [n for n in nets if is_rail(design, n)]
    grounds = [n for n in nets if is_ground(n)]
    signals = [n for n in nets
               if n not in rails and n not in grounds and is_named(n)]

    # 情况 A：一条轨 + 一条有名信号 → 上拉/默认电平（两脚都一样）
    if len(rails) == 1 and len(signals) == 1:
        anchor, rail = signals[0], rails[0]
        why = (f"两端器件：一端接有名信号 {anchor}，另一端直接接 "
               f"{_net_label(design, rail)}；只可能是把 {anchor} 拉到该轨"
               f"（上拉/默认电平）")
        return (_j(why,
                   f"{anchor} 在无驱动时悬空，电平不确定："
                   "可能被噪声误触发，或读不到确定状态",
                   evidence=f"nets={nets}"),
                ("pull_to_rail", anchor), nets)

    if len(signals) != 1:
        return None                        # 需要恰好一条信号作为锚点
    anchor = signals[0]
    other = next(n for n in nets if n != anchor)
    if is_rail(design, other) or is_ground(other):
        return None                        # 上面 A 已覆盖，或两端都是轨

    # 另一端是**无名网**：穿无源件追到地/轨。
    got = trace_named(design, other)
    if got is None:
        return None
    target, path = got
    via = f"（经 {' → '.join(path)}）"

    if is_ground(target):
        why = (f"两端器件：一端接有名信号 {anchor}，另一端{via}连到 "
               f"{target}；只可能是在需要时把 {anchor} 拉到地"
               f"（按键/开关/复位）")
        return (_j(why,
                   f"{anchor} 失去被拉到地的通路：该按键或复位动作完全失效，"
                   "对应的操作员指令进不来",
                   evidence=f"nets={nets}, path={path}"),
                ("switch_to_ground", anchor), nets + [target])

    if is_rail(design, target):
        why = (f"两端器件：一端接有名信号 {anchor}，另一端{via}连到 "
               f"{_net_label(design, target)}；只可能是把 {anchor} 拉到该轨"
               f"（上拉/默认电平）")
        return (_j(why,
                   f"{anchor} 在无驱动时悬空，电平不确定："
                   "可能被噪声误触发，或读不到确定状态",
                   evidence=f"nets={nets}, path={path}"),
                ("pull_to_rail", anchor), nets + [target])

    return None


def _rule_sw_node_cap(design: DesignModel, part: Part,
                      nets: list[str]) -> RuleResult:
    """开关节点（`SW`/`LX`）上的电容：缓冲吸收（snubber）。

    实测 `C189` 接在 `SW` + 一条无名网，另一条随即连到开关管漏极。
    开关电源的 SW 节点 dv/dt 很高，并一个小电容到地/回边是抑制振铃的
    标准做法。这条判得出来是因为 `SW` 这个网名**本身就是拓扑标记**：
    它只由开关变换器产生。
    """
    if category_of(part) != "capacitor" or len(nets) != 2:
        return None
    sw = [n for n in nets
          if re.match(r"^(SW|LX|PHASE|SWITCH)[A-Z0-9_]*$", n.strip().upper())]
    if len(sw) != 1:
        return None
    other = next(n for n in nets if n != sw[0])
    shown = shown_value(part, "F")
    why = (f"{shown} 缓冲吸收电容：一脚接开关节点 {sw[0]}，"
           f"另一脚接 {other}，用于抑制开关沿的振铃与电压尖峰")
    return (_j(why,
               f"{sw[0]} 的开关尖峰失去吸收通路：开关管承受更高电压应力，"
               "EMI 上升，长期可能击穿",
               evidence=f"nets={nets}, label={part.label}"),
            ("snubber", sw[0]), nets)


def _rule_signal_diode(design: DesignModel, part: Part,
                       nets: list[str]) -> RuleResult:
    """二极管接在有名信号与无名网之间：隔离/钳位/续流。

    实测 `D1`（`$1N10408` + `PB10`）、`D2`（两条无名网，经无源件够到
    `VBAT_2S`）。二极管只有两个方向的可能：**隔离**（只许单向流动）
    或**钳位**（把越界电压引走）。判不出的部分（是哪一种）如实说出。
    """
    if category_of(part) != "diode" or len(nets) != 2:
        return None
    named = [n for n in nets if is_named(n)]
    auto = [n for n in nets if not is_named(n)]
    if not named and not auto:
        return None
    anchor = named[0] if named else None
    other = next(n for n in nets if n != anchor) if anchor else auto[0]

    target, path = other, []
    if not is_named(other):
        got = trace_named(design, other)
        if got is not None:
            target, path = got
    via = f"（经 {' → '.join(path)}）" if path else ""

    if anchor is None:
        why = (f"二极管串在两条无名网之间，其中一条{via}通到 {target}："
               "单向器件，作用是让电流只往一个方向走（隔离/防倒灌）")
        tip = f"{target} 与另一侧之间失去单向性：可能出现倒灌或通路中断"
        return (_j(why, tip, evidence=f"nets={nets}, path={path}"),
                ("isolating_diode", target), nets + [target])

    why = (f"二极管一端接有名信号 {anchor}，另一端{via}接 {target}："
           "单向器件，只允许电流从一端流向另一端（隔离/钳位/续流）")
    return (_j(why,
               f"{anchor} 与 {target} 之间的单向通路断开："
               "钳位失效（可能过压）或续流失效（电感尖峰无处泄放）",
               evidence=f"nets={nets}, path={path}"),
            ("signal_diode", anchor), nets + [target])


def _rule_connector_to_module(design: DesignModel, part: Part,
                              nets: list[str]) -> RuleResult:
    """连接器与某个多脚模块**独占**一条无名网 → 那个模块的对外端口。

    实测 `RF1`（5 脚连接器，`GND` + `$1N43000`），而 `$1N43000` 上
    只有它和 `Lora1` 脚 10 两个节点。一条只连着"一个连接器 + 一个模块"
    的网络，除了这个模块的对外引出线不可能是别的东西。

    这条能下结论，靠的是**独占性判断**（网上恰好 2 个节点），
    不是靠网名——网名是自动生成的，没有语义。
    """
    cat = ref_prefix(part.ref)
    is_conn = (str(getattr(part, "role", "")) == "connector"
               or cat in ("USB", "RF", "J", "CON", "CN", "HDR"))
    if not is_conn:
        return None
    for net in nets:
        if is_named(net):
            continue
        pins = [p for p in claims_module.pins_on_net(design, net)
                if isinstance(p, dict)]
        others = [p for p in pins if str(p.get("ref") or "") != part.ref]
        peers = {str(p.get("ref") or "") for p in others}
        if len(peers) != 1:
            continue
        peer = next(iter(peers))
        peer_part = design.part(peer)
        if peer_part is None or _pin_count(peer_part) < 3:
            continue
        how = f"{_pin_count(part)} 脚连接器 {part.ref}"
        why = (f"{how} 与 {peer}（{_pin_count(peer_part)} 脚）**独占**网络 "
               f"{net}（该网只有这 2 个节点）：这是 {peer} 的对外引出端口")
        return (_j(why,
                   f"{peer} 与外部之间断开：它的射频/信号/天线通路失去，"
                   "整机在该接口上无法工作",
                   evidence=f"net={net}, pins={pins}"),
                ("module_port", peer), nets + [net])
    return None


def _rule_controlled_node(design: DesignModel, part: Part,
                          nets: list[str]) -> RuleResult:
    """由一个**有名控制信号**驱动、且其余网经无源件通往电源轨的节点。

    实测 `U1025`（3 脚模块）：脚 2 接 `LIGHTING`（来自 `U1` 脚 54），
    脚 1 经 `R270` 通到 `GND`，脚 3 经 `R269` 通到 `3.3V`。

    能下的结论很具体：**它受 `LIGHTING` 控制，且两侧都有偏置到轨**。
    不能下的是"它是灯还是传感器"——那要 datasheet。所以理由只写到
    能证的那一层，并且明确说出"具体品类判不出"。
    """
    cat = category_of(part)
    if cat in PASSIVE_CATEGORIES or cat in ("transistor", "diode", "led"):
        return None
    if _pin_count(part) > 6:
        return None                        # 大模块不是这种偏置结构

    named = [n for n in nets if is_named(n)
             and not is_rail(design, n) and not is_ground(n)]
    if len(named) != 1:
        return None
    control = named[0]

    # 控制信号是不是来自一个多脚器件（MCU）？
    driver = None
    for pinref in claims_module.pins_on_net(design, control):
        if not isinstance(pinref, dict):
            continue
        ref = str(pinref.get("ref") or "")
        if ref == part.ref or not ref:
            continue
        candidate = design.part(ref)
        if candidate is not None and _pin_count(candidate) >= 8:
            driver = candidate
            break
    if driver is None:
        return None

    biased: list[str] = []
    for net in nets:
        if is_named(net):
            continue
        got = trace_named(design, net)
        if got is not None and (is_rail(design, got[0]) or is_ground(got[0])):
            biased.append(f"{net}→{got[0]}")

    why = (f"{_pin_count(part)} 脚节点，受 {driver.ref} 的 {control} 信号控制"
           + (f"，其余引脚经无源件偏置到 {', '.join(sorted({b.split('→')[1] for b in biased}))}"
              if biased else "，其余引脚无偏置")
           + "；具体品类（灯/传感器/驱动）判不出，需 datasheet")
    if not biased:
        return None
    return (_j(why,
               f"{control} 的控制对象消失：{driver.ref} 该路输出空载，"
               "对应的执行/指示功能缺失",
               evidence=f"nets={nets}, bias={biased}"),
            ("controlled_node", control), nets)


def _rule_bare_switch(design: DesignModel, part: Part,
                      nets: list[str]) -> RuleResult:
    """按键/开关本体：两条网都无名，也没有第三方能告诉我们去哪。

    实测 `btn1`（`wokwi-pushbutton`，`w1`/`w2` 两条自动名）。
    这种情况下拓扑上**唯一还能确定的事**是它自己是开关——因为
    `kind` 说了，而 `kind` 是可读的类型名（不是哈希）。

    结论只写到这一层：不说它接到哪个 MCU 引脚，因为网名里没有信息。
    这比"判不出"有用：读者至少知道这是一个按键，而不是一堆未知数。
    """
    if category_of(part) != "switch":
        return None
    if len(nets) != 2:
        return None
    others: list[str] = []
    for net in nets:
        pins = [p for p in claims_module.pins_on_net(design, net)
                if isinstance(p, dict)]
        peers = sorted({str(p.get("ref") or "") for p in pins} - {part.ref})
        if peers:
            others.append(f"{net}→{peers[0]}")
    tail = f"；另一端分别去 {', '.join(others)}" if others else ""
    why = (f"按键/开关本体（kind={part.kind}），两个端子接 "
           f"{' 与 '.join(nets)}{tail}；按下时把两端短接")
    return (_j(why,
               "该按键完全失效：操作员的这一路输入进不来",
               evidence=f"nets={nets}, kind={part.kind}"),
            ("bare_switch", "/".join(nets)), nets)


def _rule_connector(design: DesignModel, part: Part,
                    nets: list[str]) -> RuleResult:
    """连接器：说出它承载了哪些接口。"""
    cat = ref_prefix(part.ref)
    is_conn = (str(getattr(part, "role", "")) == "connector"
               or cat in ("USB", "RF", "J", "CON", "CN", "HDR"))
    if not is_conn and _pin_count(part) < 2:
        return None
    if not is_conn:
        return None
    families: list[str] = []
    upper = [n.upper() for n in nets]
    if any("DP" in n or "D+" in n or "USB" in n for n in upper):
        families.append("USB")
    if any("SPI" in n or "MOSI" in n or "MISO" in n for n in upper):
        families.append("SPI")
    if any(n.startswith(("SDA", "SCL")) for n in upper):
        families.append("I2C")
    if any("TX" in n or "RX" in n for n in upper):
        families.append("串口")
    if any("RF" in n or "ANT" in n for n in upper):
        families.append("射频")
    if any(is_rail(design, n) for n in nets):
        families.append("供电")
    if not families:
        return None
    what = "对外的" + "/".join(families) + "接口"
    why = f"{what}连接器（{_pin_count(part) or '?'} 脚），网络：{', '.join(nets)}"
    return (_j(why, f"外部{('/'.join(families))}线束无处可接，整机无法互联",
               evidence=f"nets={nets}, pins={_pin_count(part)}"),
            ("connector", "/".join(families)), nets)


def _rule_regulator(design: DesignModel, part: Part,
                    nets: list[str]) -> RuleResult:
    """稳压器：地 + 至少两条不同的电源轨（入、出）。"""
    grounds = [n for n in nets if is_ground(n)]
    rails = [n for n in nets if is_rail(design, n)]
    if not grounds or len(rails) < 2:
        return None
    # 入轨判定：先按名字认（VIN/VBUS/VBAT 一定是上游），否则按**电压**
    # 取较高的那条（降压是绝大多数字号）。两者都判不出时说"之间"，
    # 不硬指定方向——方向说错会让人去查错的输入端。
    vin = next((n for n in rails if re.match(r"^(VIN|VBUS|VBAT|B\+)", n.upper())), None)
    if vin is None:
        volted = [(n, rail_volts(design, n)) for n in rails]
        known = [(n, v[0]) for n, v in volted if v is not None]
        if len(known) >= 2:
            known.sort(key=lambda kv: -kv[1])
            vin = known[0][0]
    if vin is not None:
        vout = [n for n in rails if n != vin]
        why = (f"电压变换：从 {_net_label(design, vin)} 产生 "
               f"{', '.join(_net_label(design, n) for n in vout)}")
        target = f"{vin}->{'/'.join(vout)}"
    else:
        why = (f"电压变换：连接 {' 与 '.join(_net_label(design, n) for n in rails)}"
               "（输入端判不出，按轨间变换处理）")
        target = "|".join(rails)
    return (_j(why,
               f"{'/'.join(vout)} 整片失去供电，挂在上面的器件全部不工作",
               evidence=f"nets={nets}"),
            ("regulator", target), nets)


#: 能当"总线节点"的类别：纯无源件（电阻/电容/电感/磁珠）**不是节点**。
#: 它们挂总线上是做端接/上拉/滤波的，不实现协议。实测 bug：
#: `C1`（1uF，接 GND 与 RF_NRST）被网络名里的 `RF` 命中，报成
#: "RF 总线上的一个节点"——一个电容怎么会是总线节点。
#: 这条门控放在协议规则里，而不是让每条规则各自小心。
_NOT_A_NODE = frozenset({"resistor", "capacitor", "inductor", "ferrite"})

#: 端点太少也不足以当节点（2 脚的 D/LED/按钮不是总线设备）。
_MIN_NODE_PINS = 3
_MIN_NODE_NETS = 3


def _rule_protocol(design: DesignModel, part: Part,
                   nets: list[str]) -> RuleResult:
    """从**网络名**推协议角色。这是唯一能对哈希 kind 给出具体结论的线索。

    判据是网名里出现协议特征词。判不出返回 None——**不按"引脚多就是 MCU"猜**。

    门控（三档，缺一不可）：
      1. 类别不是纯无源件（电阻/电容/电感/磁珠不实现协议）
      2. 至少 3 个引脚（2 脚器件没有总线可言）
      3. 至少 3 条网络（一条地 + 两条信号是总线的下限）
    """
    cat = category_of(part)
    if cat in _NOT_A_NODE:
        return None
    if _pin_count(part) < _MIN_NODE_PINS and len(nets) < _MIN_NODE_NETS:
        return None
    upper = {n: n.upper() for n in nets}
    hits: list[str] = []
    detail: list[str] = []

    def has(*tokens: str) -> list[str]:
        return [n for n, u in upper.items() if any(t in u for t in tokens)]

    spi_sck = has("SPI_SCK", "SCK", "SPI3_SCK")
    spi_mosi = has("MOSI")
    spi_miso = has("MISO")
    if spi_sck and (spi_mosi or spi_miso):
        hits.append("SPI")
        pins = sorted(set(spi_sck + spi_mosi + spi_miso))[:4]
        detail.append("SPI（" + "/".join(pins) + "）")
    i2c = [n for n, u in upper.items() if u.startswith(("SDA", "SCL"))]
    if len(i2c) >= 2:
        hits.append("I2C")
        detail.append("I2C（" + "/".join(sorted(i2c)) + "）")
    uart = []
    for n, u in upper.items():
        if re.search(r"(TX|RX)(_?\d+)?$", u):
            uart.append(n)
    if len(uart) >= 2:
        hits.append("UART")
        detail.append("串口（" + "/".join(sorted(uart)[:4]) + "）")
    adc = has("ADC_")
    if adc:
        hits.append("ADC")
        detail.append("模拟量采样（" + "/".join(sorted(adc)[:4]) + "）")
    rf = has("RF_", "LGS_", "LORA")
    if rf:
        hits.append("RF")
        detail.append("射频（" + "/".join(sorted(rf)[:4]) + "）")
    if not hits:
        return None

    rails = [n for n in nets if is_rail(design, n)]
    supply = f"，由 {_net_label(design, rails[0])} 供电" if rails else ""
    why = (f"接在 {'/'.join(detail)} 上{supply}："
           f"从网络命名看它是 {'/'.join(hits)} 总线上的一个节点")
    return (_j(why,
               f"{'/'.join(hits)} 总线失去该节点："
               + ("主机侧则整条总线无人驱动" if len(hits) > 1 else "该外设功能消失"),
               evidence=f"nets={nets}, pins={_pin_count(part)}"),
            ("protocol_node", "/".join(hits)), nets)


def _rule_rail_powered(design: DesignModel, part: Part,
                       nets: list[str]) -> RuleResult:
    """只接在电源轨上的元件（没有可识别的信号）。

    **这条规则最弱**，所以放在最后，而且理由只说得出"它耗这条轨的电"。
    一个元件如果只接轨和地，拓扑上能确定的就只有这一点。

    **按类别给不同的删除后果**：把主动件（IC/模块/连接器）说成
    "少一个负载"是误导——它不是负载，它是功能本体。第一版对 `Q2`
    就说错了，所以这里按类别分档。
    """
    rails = [n for n in nets if is_rail(design, n)]
    if not rails:
        return None
    grounds = [n for n in nets if is_ground(n)]
    named_signals = [n for n in nets if is_named(n)
                     and not is_ground(n) and n not in rails]
    if named_signals:
        return None

    # 没有**有名**的信号网，但可能有接到无名网上的引脚——那些无名网
    # 要穿透无源件追一跳，才知道它们到底算不算信号。
    # 第一版直接说"未连任何信号网"，而实测 `U10` 的
    # `$1N394/$1N399/$1N400` 分别经 `R90/R91/R92` 连到 `GND/SCL/SDA`：
    # 它明明是两个 I2C 上拉电阻的**负载端**，写成"未连信号"是错的。
    traces: list[tuple[str, str, list[str]]] = []
    for net in nets:
        if is_named(net):
            continue
        got = trace_named(design, net)
        if got is not None:
            traces.append((net, got[0], got[1]))

    cat = category_of(part)
    rail = rails[0]
    pins = _pin_count(part)

    if traces:
        # 穿过无源件后够到了有名网 → 它**不是**纯电源件。
        targets = sorted({t[1] for t in traces})
        paths = "; ".join(f"{t[0]}→{t[1]}" for t in traces[:4])
        why = (f"{part.ref} 的 {len(traces)} 个引脚接在无名网上，"
               f"但穿过串联无源件后这些网连到 {', '.join(targets)}"
               f"（路径：{paths}）；它自己还由 {_net_label(design, rail)} 供电"
               f"（{pins} 脚）")
        effect = (f"{'/'.join(targets)} 上失去该节点：挂在那些信号上的"
                  "功能（上拉/端接/采样）随之失效")
        return (_j(why, effect, evidence=f"nets={nets}, traced={paths}"),
                ("traced_node", "/".join(targets)), nets + targets)

    why = (f"只接在 {_net_label(design, rail)}"
           f"{' 与 ' + grounds[0] if grounds else ''} 上，"
           f"其余 {len(nets) - len(rails) - len(grounds)} 个引脚接的无名网"
           "穿透无源件后仍未连到任何有名网络")
    if cat == "unknown":
        # 类别判不出 + 够不到有名网 → **真的没有拓扑线索**。
        return (_j(why,
                   "未知——若它是主动件则整块功能缺失；若只是旁路元件则影响很小。"
                   "拓扑上区分不了这两种情况，需要人工说明",
                   confidence="unknown",
                   evidence=f"nets={nets}, pins={pins}"),
                ("rail_only", rail), nets)
    if cat in PASSIVE_CATEGORIES:
        effect = (f"{rail} 上少一个无源件：若是储能/旁路则瞬态响应变差，"
                  "若是分压则偏置点偏移")
    else:
        effect = (f"{rail} 上少一个有源件（{cat}）：对应功能整体缺失，"
                  "且该轨电流需求下降")
    return (_j(why, effect, evidence=f"nets={nets}, pins={pins}"),
            ("rail_only", rail), nets)


#: 规则表。**顺序就是要害**：具体的在前，笼统的在后。
#:
#: 每条规则都自带类别门控，所以顺序不会造成"错误匹配"，只会造成
#: "先被笼统的那条吃掉"。实测踩过的两个顺序 bug：
#:   * `led` 排在 `series` 后面 → 灯链上的 LED 先被"串联"吃掉（类别不同，
#:     实际没命中，但 `rail_only` 这类无类别门控的规则会吃掉一切）
#:   * `switch` 排在 `rail_only` 后面 → 高端 MOS 被说成"轨上少一个负载"
RULES: tuple[tuple[str, Callable[[DesignModel, Part, list[str]], RuleResult]], ...] = (
    ("unconnected", _rule_unconnected),
    ("crystal", _rule_crystal),
    ("decoupling", _rule_decoupling),
    ("signal_cap", _rule_signal_cap),
    ("shunt", _rule_shunt),
    ("led_series", _rule_led_series),
    ("pull", _rule_pull),
    ("rail_link", _rule_rail_link),
    ("switching", _rule_switching),
    ("sw_node_cap", _rule_sw_node_cap),
    ("usb_series", _rule_series),
    ("led", _rule_led),
    ("switch", _rule_switch),
    ("diode", _rule_diode),
    ("signal_diode", _rule_signal_diode),
    ("connector", _rule_connector),
    ("module_port", _rule_connector_to_module),
    ("regulator", _rule_regulator),
    ("protocol", _rule_protocol),
    # `two_terminal` 必须排在 `controlled` **前面**：
    # 一个"有名信号 + 经无源件到地"的两脚器件，两个规则都能匹配，
    # 但"对地开关"是**更具体**的结论（它说了另外那一端去哪），
    # 而"受控节点"只说得出"受谁控制"。实测 `U1026` 被 controlled 抢先，
    # 结论从"按键"退化成"受控节点，品类判不出"。
    ("two_terminal", _rule_two_terminal),
    ("controlled", _rule_controlled_node),
    ("bare_switch", _rule_bare_switch),
    ("rail_only", _rule_rail_powered),
)


# ---------------------------------------------------------------------------
# 单件归因
# ---------------------------------------------------------------------------

#: 理由里必须出现具体指称。**这条是套话过滤器**：判出 `why` 却拿不出
#: 任何具体网名/元件号，说明这条理由没有信息量，降级为 unknown。
def _cited(why: str, nets: list[str], ref: str) -> bool:
    """理由里有没有具体指称（网名 / 元件号 / 带单位的数值）。

    数值必须带单位才算（`82mΩ`、`100nF`、`32.768kHz`）。光有数字不算——
    否则 `"该元件起到滤波作用"` 里拼一个编号就能蒙混过关。
    """
    text = str(why or "")
    if any(n and n in text for n in nets):
        return True
    if ref and ref in text:
        return True
    if re.search(r"\d\s*(Ω|R|kΩ|F|µF|nF|uF|H|µH|uH|Hz|kHz|MHz|V|A|mA|脚|条)",
                 text):
        return True
    return False


def infer(design: DesignModel, part: Part) -> Attribution:
    """从一个已存在的元件反推它的存在理由。

    按 `RULES` 顺序试，**第一条能给出结论的就用**。都不给 → `unknown`，
    并说清为什么判不出（哈希 kind？只接自动网名？）。

    ## 规则抛异常时不吞

    规则崩溃是**代码 bug**，不是"这个元件判不出"。第一版把两者都记成
    `unknown`，结果 `set(...)[:4]` 的 `TypeError` 让 `Lora1`（19 脚 LoRa
    模块）以"接在自动网名上判不出"的样子出现在报告里——报告看起来
    正常，代码里有个真 bug 没人知道。

    所以：`TypeError`/`AttributeError`/`KeyError`/`IndexError` 这几类
    **几乎只可能是写错**，直接向上抛，由 `run()` 整个失败。业务上
    "判不出"用 `return None` 表达，不走异常。
    """
    nets = claims_module.nets_of(design, part.ref)
    cat = category_of(part)
    for name, rule in RULES:
        got = rule(design, part, nets)
        if got is None:
            continue
        justification, group, cited_nets = got
        # 套话过滤：没有具体指称 → 降级
        if (justification.confidence != "unknown"
                and not _cited(justification.why, nets + cited_nets, part.ref)):
            justification = _j(
                f"{part.ref}：{justification.why}（未含具体指称，降级）",
                justification.effect_if_removed,
                confidence="unknown",
                evidence=justification.evidence)
            group = ("unknown", name)
        return Attribution(ref=part.ref, category=cat,
                           justification=justification, group=group,
                           nets=nets)

    # 全都判不出：如实报，并说清为什么
    return _unknown(part, nets, cat, design)


def _semantic_anchors(design: DesignModel, part: Part,
                      nets: list[str]) -> list[str]:
    """这块局部网表里有哪些**可读的**线索（类型名 / 有名网）。

    返回空列表 = 整块子图上没有一个字是给人看的，全是哈希和自动名。
    这时"判不出"是数据的问题，不是算法的问题。
    """
    anchors: list[str] = []
    for net in nets:
        if is_named(net):
            anchors.append(f"net:{net}")
        for pin in claims_module.pins_on_net(design, net):
            if not isinstance(pin, dict):
                continue
            ref = str(pin.get("ref") or "")
            if not ref or ref == part.ref:
                continue
            peer = next((p for p in design.parts if p.ref == ref), None)
            if peer is None:
                continue
            for field in ("kind", "label"):
                value = str(getattr(peer, field, "") or "")
                if value and not _looks_like_hash(value) \
                        and category_of(peer) != "unknown":
                    anchors.append(f"{ref}.{field}={value}")
                    break
    return anchors


def _unknown(part: Part, nets: list[str], cat: str,
             design: DesignModel | None = None) -> Attribution:
    """判不出时，**把"为什么判不出"写出来**，而不是留空。

    先试一次 `trace_named()`：如果它的无名网穿过去能够到有名网，
    那就有线索了，把线索写出来（仍然是 unknown，因为"连到了 SCL"
    不等于"我知道它是什么"，但这句话能让人自己判断）。
    """
    traces: list[tuple[str, str, list[str]]] = []
    if design is not None:
        for net in nets:
            if is_named(net):
                continue
            got = trace_named(design, net)
            if got is not None:
                traces.append((net, got[0], got[1]))

    if not nets:
        reason = "不接任何网络"
        hint = ""
    elif cat == "unknown" and all(not is_named(n) for n in nets):
        reason = (f"ref 前缀无法归类，且 {len(nets)} 条网全是导入器自动名"
                  f"（{', '.join(nets[:3])}）")
        # **只在实际是哈希时才说哈希**。`btn1` 的 kind 是
        # `wokwi-pushbutton`——可读的类型名，只是我的词表没收录。
        # 把"我的词表没收录"说成"kind 是哈希"是在编造原因。
        hint = ("；kind/label 也是导入器哈希"
                if _looks_like_hash(part.kind) or _looks_like_hash(part.label)
                else f"；kind={part.kind!r} 未收录在类别词表里")
    elif all(not is_named(n) for n in nets):
        reason = f"只接自动网名（{', '.join(nets[:3])}）"
        hint = ""
    else:
        reason = (f"接在 {', '.join(nets[:4])} 上，但拓扑特征不匹配任何已知规则")
        hint = "（可能是桥接/复合网络）"

    if traces:
        paths = "; ".join(f"{t[0]}→{t[1]}" for t in traces[:4])
        reason += f"；穿过串联无源件后够到 {', '.join(sorted({t[1] for t in traces}))}"
        detail_evidence = f"nets={nets}, traced={paths}"
    else:
        detail_evidence = f"nets={nets}, kind={part.kind}"

    # 判不出时**说清缺哪个锚点**。实测 `U1027` 就是典型：
    # 它追得到 GND（所以 `traces` 非空），但"另一端接地"对判断品类
    # 没有帮助；它的两条网都无名，对端 `D1`/`D2`/`R273` 的 kind 也全
    # 是哈希，整块局部网表里没有一个可读的类型名。
    #
    # 这不叫"算法没写"，叫**这份数据里没有可用的语义锚点**。
    # 两者要分开说，因为修法不同：前者改代码，后者补数据。
    if design is not None and nets:
        anchors = _semantic_anchors(design, part, nets)
        if not anchors:
            reason += ("；相邻元件的 kind 也全是哈希，"
                       "这块局部网表里没有任何可读的类型名"
                       + ("（追到的 GND 只说明回路，不说明品类）"
                          if traces else ""))

    return Attribution(
        ref=part.ref, category=cat,
        justification=_j(
            f"{part.ref}：{reason}{hint}",
            "未知——删掉它系统不会报错，因为还没人说过它为什么在",
            confidence="unknown", evidence=detail_evidence),
        group=("unknown", reason.split("（")[0].split("；")[0]),
        nets=nets)


# ---------------------------------------------------------------------------
# 全设计归因 + 分组
# ---------------------------------------------------------------------------

#: 分组的中文名。未列出的 kind 直接显示原名（不编中文）。
GROUP_LABEL = {
    "decoupling": "去耦/储能电容",
    "signal_cap": "信号滤波电容",
    "reset_cap": "复位电容",
    "debounce_cap": "按键去抖电容",
    "osc_cap": "晶振负载电容",
    "boot_cap": "启动模式电容",
    "power_inductor": "储能电感",
    "high_side_switch": "高边开关",
    "switch_to_ground": "对地开关",
    "pull_to_rail": "上拉到轨",
    "traced_node": "穿透追踪到的节点",
    "snubber": "开关缓冲电容",
    "signal_diode": "信号二极管",
    "isolating_diode": "隔离二极管",
    "module_port": "模块对外端口",
    "controlled_node": "受控节点",
    "pullup": "上拉电阻",
    "pulldown": "下拉电阻",
    "current_sense": "电流采样",
    "led_limit": "LED 限流",
    "series": "串联电阻",
    "usb_series": "USB 串阻",
    "rail_link": "电源轨间连接",
    "clock_source": "时钟源",
    "low_side_switch": "低边开关",
    "power_or": "电源或门/防倒灌",
    "clamp": "钳位/续流",
    "connector": "对外连接器",
    "regulator": "电压变换",
    "protocol_node": "总线节点",
    "rail_only": "仅接电源",
    "indicator": "指示灯",
    "unconnected": "未连接",
    "unknown": "判不出",
}


def run(design: DesignModel, *,
        write_back: bool = False) -> dict[str, Any]:
    """对整份设计做归因。

    `write_back=True` 时把结论写进 `part.justification`（内存对象），
    调用方决定要不要落盘。**默认不写**——归因是推导结果，
    推导结果混进 `design.json` 后就分不清哪条是人说的了
    （`confidence` 字段能分，但那要求读的人每次都看）。

    ## 两个比例，不是一个

    | 指标 | 分母 | 含义 |
    |---|---|---|
    | `ratio` | 全部元件 | 保守值，**: 不掩盖缺数据** |
    | `ratio_judgeable` | 有网表的元件 | 只衡量"该判的判了没有" |

    为什么必须分开：实测三个工程的 unknown 全部分布在
    `nets == []` 的元件上——

        示例-多板   316 件里 175 件没有网表（55.4%）
        示例-无人机       44 件里  44 件没有网表（100%）
        示例-单板         141 件里   0 件没有网表（0%）

    只报一个总数的话，`示例-无人机` 的 0% 看起来像"归因算法坏了"，
    而真相是**它根本没有网表**——那是数据缺失，算法无从下手。
    更糟的是反过来：一个工程可能靠少数有网表的元件把比例拉高，
    让人以为覆盖很好。

    所以 `ok` 判据用 `ratio_judgeable`（算法本身的成效），但
    `no_netlist` 与 `ratio` **永远同时报出**，`missing_data` 单独列。
    """
    attributions = [infer(design, part) for part in design.parts]
    if write_back:
        by_ref = {a.ref: a for a in attributions}
        for part in design.parts:
            got = by_ref.get(part.ref)
            if got is not None:
                part.justification = got.justification

    counts: dict[str, int] = {}
    for a in attributions:
        key = a.justification.confidence
        counts[key] = counts.get(key, 0) + 1

    total = len(attributions)
    known = total - counts.get("unknown", 0)
    ratio = (known / total) if total else 0.0

    # 没有网表的元件：**判不出是因为没数据，不是因为算法不行**。
    no_netlist = [a.ref for a in attributions if not a.nets]
    judgeable = total - len(no_netlist)
    judgeable_known = sum(
        1 for a in attributions
        if a.nets and a.justification.confidence != "unknown")
    ratio_judgeable = (judgeable_known / judgeable) if judgeable else 0.0

    # 分组：按（kind, target）聚合。无源件读这一层，功能件读 parts。
    groups: dict[tuple[str, str], list[str]] = {}
    for a in attributions:
        groups.setdefault(a.group, []).append(a.ref)
    group_rows: list[dict[str, Any]] = []
    for (kind, target), refs in sorted(groups.items(),
                                       key=lambda kv: (kv[0][0], kv[0][1])):
        group_rows.append({
            "kind": kind,
            "label": GROUP_LABEL.get(kind, kind),
            "target": target,
            "count": len(refs),
            "refs": sorted(refs),
            "why": _group_why(design, kind, target, refs),
        })

    # 无源件覆盖率单独报：功能件全 unknown 时总覆盖率会显得还行，
    # 而那正是最该被发现的情况。
    passives = [a for a in attributions
                if a.category in PASSIVE_CATEGORIES and a.nets]
    passive_known = [a for a in passives
                     if a.justification.confidence != "unknown"]

    summary = (
        f"{known}/{total} 个元件判出了存在理由（{ratio * 100:.1f}%）；"
        f"其中有网表的 {judgeable} 个里判出 {judgeable_known} 个"
        f"（{ratio_judgeable * 100:.1f}%）。"
        f"{counts.get('asserted', 0)} 条人声明，"
        f"{counts.get('inferred_by_topology', 0)} 条从拓扑推出。"
    )
    if no_netlist:
        summary += (f"**{len(no_netlist)} 个元件完全没有网表**"
                    f"（{len(no_netlist) / total * 100:.1f}%），"
                    "它们判不出是缺数据，不是算法判不出——见 missing_data。")

    return {
        "ok": ratio_judgeable > 0.20 and judgeable > 0,
        "total": total,
        "known": known,
        "unknown": counts.get("unknown", 0),
        "ratio": round(ratio, 4),
        "judgeable": judgeable,
        "judgeable_known": judgeable_known,
        "ratio_judgeable": round(ratio_judgeable, 4),
        "threshold": 0.20,
        "by_confidence": counts,
        "by_category": _count_by(attributions, lambda a: a.category),
        "by_kind": _count_by(attributions, lambda a: a.group[0]),
        "passives": {
            "total": len(passives),
            "known": len(passive_known),
            "ratio": round(len(passive_known) / len(passives), 4)
            if passives else 0.0,
        },
        "missing_data": {
            "no_netlist": sorted(no_netlist),
            "no_netlist_count": len(no_netlist),
            "no_netlist_ratio": round(len(no_netlist) / total, 4)
            if total else 0.0,
            "note": ("这些元件在 design.json 里没有任何网表引脚。"
                     "归因完全依赖拓扑，没有拓扑就只能如实报 unknown。"
                     "补齐办法：重新导入网表，或走 P8 从 .ioc 提取。"),
        },
        "parts": [a.to_dict() for a in attributions],
        "groups": group_rows,
        "unknown_parts": [a.ref for a in attributions
                          if a.justification.confidence == "unknown"],
        "summary": summary,
    }


def _count_by(attributions: list[Attribution],
              key: Callable[[Attribution], str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in attributions:
        k = key(a)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _group_why(design: DesignModel, kind: str, target: str,
               refs: list[str]) -> str:
    """一组无源件的合并理由。**含具体目标**（哪条轨/哪个信号）。"""
    n = len(refs)
    label = GROUP_LABEL.get(kind, kind)
    if kind == "decoupling":
        return f"{_net_label(design, target)} 上的 {n} 个去耦/储能电容（{', '.join(sorted(refs)[:6])}{'…' if n > 6 else ''}）"
    if kind in ("pullup", "pulldown"):
        direction = "拉到电源" if kind == "pullup" else "拉到地"
        return f"{target} 上的 {n} 个{direction}电阻：无驱动时给定确定电平"
    if kind == "led_limit":
        return f"驱动 {target} 的 {n} 个 LED 限流电阻"
    if kind == "rail_link":
        return f"{target.replace('|', ' ↔ ')} 之间的 {n} 个直流通路"
    if kind == "current_sense":
        return f"{target.replace('|', ' 与 ')} 之间的 {n} 个采样电阻"
    if kind == "series":
        return f"{target.replace('|', ' ↔ ')} 之间的 {n} 个串联电阻"
    return f"{label}（{target}）：{n} 个"


# ---------------------------------------------------------------------------
# 自省
# ---------------------------------------------------------------------------

def describe() -> dict[str, Any]:
    """给 CLI / 工具自省用。"""
    return {
        "module": "hw_velxio.design.justify",
        "rules": [name for name, _ in RULES],
        "rule_count": len(RULES),
        "confidence": ["asserted", "backfill",
                       "inferred_by_topology", "unknown"],
        "passive_categories": sorted(PASSIVE_CATEGORIES),
        "threshold": 0.20,
        "note": ("从网表拓扑反推存在理由；判不出时返回 unknown 并说明"
                 "为什么判不出。理由必须含具体网名/元件号，否则降级。"),
    }
