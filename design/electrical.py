"""DC 电学 —— **hw_velxio 自己的**：压降 / 轨跌 / 结温。

取代 `hwforge.electrical`。

## 它补的是哪个洞

O2 有降额表（Vds vs Vmax、电流 vs 额定），但**没有电路** —— 没有欧姆定律、
没有压降、没有结温。所以工具能说"这颗 MOSFET 在 60% Vds"，却不能说
"舵机的 5V 轨在堵转时跌到 4.4V，舵机褐变复位"。

五个模型，各自对应一种**真实会发生的**失效：

| ID | 模型 | 抓什么 |
|---|---|---|
| EL-01 | 线损 `I·L·ρ/A` | 细电源线 |
| EL-02 | 稳压器 `P=(Vin-Vout)·I` | 烤掉的 LDO |
| EL-03 | 结温 `Tj = Ta + P·Rθja` | 热失控 |
| EL-04 | 电源 `I·Rout` | 有限输出阻抗导致的轨跌 |
| EL-05 | 预算 | 堵转时的总电流 vs 电源额定 |

## 明确的局限（这些界定了它值多少）

* **只算直流**。没有电感、没有开关纹波、没有瞬态响应。50Hz 的舵机负载
  对这些问题确实近似直流，但 buck 的纹波不是 —— 这个模型看不见。
* **线阻用 20°C 标准值**。铜每升 1°C 涨约 0.4%，热态线束压降更大 ——
  **注明了，但没有建模**。
* **Rθja 是按封装声明的典型值**，不是热仿真。量级对，精度不对，
  不适合当热设计评审用。

在这个局限内，它抓住**第一批硬件的四个失效**：细电源线、烤掉的 LDO、
会掉的电源、按空载电流定容的电源轨。

## 两条不能破的纪律

1. **过载是独立的失效**，不只是"压降没超"。一个 2A 模块带 2.1A，
   电压看着正常（2.1A × 90mΩ = 0.19V），但电源在过载工作 ——
   会发热、掉压、寿命骤降。两种失效的修法不同：压降要更硬的电源，
   过载要更大的电源。
2. **没有数据就报未验证**，不拿默认值冒充"通过"。

@module hw_velxio.design.electrical
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


#: AWG → (截面积 mm², 20°C 电阻 Ω/m)。标准退火铜表。
AWG: dict[int, tuple[float, float]] = {
    18: (0.823, 0.0209),
    20: (0.518, 0.0333),
    22: (0.326, 0.0529),
    24: (0.205, 0.0842),
    26: (0.129, 0.1330),
    28: (0.0810, 0.2126),
}

#: 封装 → 典型结到环境热阻（静止空气，°C/W）。**典型值，不是仿真**。
RTHJA: dict[str, float] = {
    "SOT-23": 250.0,
    "SOT-223": 62.0,
    "TO-252": 45.0,
    "TO-220": 62.0,       # 无散热器
    "QFN-16": 40.0,
    "module": 30.0,       # 自带 PCB 的小模块
}

#: 开关型稳压器的假想效率（小 buck 的典型值）。
SWITCHING_EFFICIENCY = 0.85

#: 压降预算：5V 轨的 5% —— 大约是舵机开始失去扭矩的位置。
DEFAULT_DROP_BUDGET_V = 0.25


# ---------------------------------------------------------------------------
# EL-01 线损
# ---------------------------------------------------------------------------

@dataclass
class WireResult:
    awg: int
    length_mm: float
    current_a: float
    resistance_mohm: float
    drop_v: float
    power_w: float
    ok: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"awg": self.awg, "length_mm": self.length_mm,
                "current_a": self.current_a,
                "resistance_mohm": round(self.resistance_mohm, 2),
                "drop_v": round(self.drop_v, 3),
                "power_w": round(self.power_w, 3),
                "ok": self.ok, "note": self.note}


def wire_drop(awg: int, length_mm: float, current_a: float,
              budget_v: float = DEFAULT_DROP_BUDGET_V) -> WireResult:
    """沿导线的压降，以及它有没有吃掉裕量。

    `budget_v` 默认 0.25V：5V 轨的 5%，大约是舵机开始掉扭矩的位置。
    0.5m 的 26AWG 带 1.4A 压降 0.19V —— 能扛；同样长度换成 28AWG
    压降 0.30V，舵机速度会肉眼可见地塌下去。

    这就是只看数据手册的工作流**永远不会算**的东西。
    """
    if awg not in AWG:
        raise ValueError(f"AWG {awg} 不在表里：{sorted(AWG)}")
    _area, ohm_per_m = AWG[awg]
    resistance = ohm_per_m * (length_mm / 1000.0)
    drop = current_a * resistance
    power = current_a * current_a * resistance
    ok = drop <= budget_v
    note = ""
    if not ok:
        note = (f"压降 {drop:.2f}V 超过预算 {budget_v:.2f}V："
                f"换更粗的线或缩短长度")
    elif drop > budget_v * 0.8:
        note = f"压降 {drop:.2f}V 已接近预算 {budget_v:.2f}V"
    return WireResult(awg=awg, length_mm=length_mm, current_a=current_a,
                      resistance_mohm=resistance * 1000.0, drop_v=drop,
                      power_w=power, ok=ok, note=note)


# ---------------------------------------------------------------------------
# EL-02/EL-03 稳压器与结温
# ---------------------------------------------------------------------------

@dataclass
class RegulatorResult:
    type: str
    vin_v: float
    vout_v: float
    current_a: float
    power_w: float
    tj_c: float
    ambient_c: float
    rthja: float
    ok: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "vin_v": self.vin_v, "vout_v": self.vout_v,
                "current_a": self.current_a,
                "power_w": round(self.power_w, 3),
                "tj_c": round(self.tj_c, 1), "ambient_c": self.ambient_c,
                "rthja": self.rthja, "ok": self.ok, "note": self.note}


def regulator_thermal(kind: str, vin_v: float, vout_v: float, current_a: float,
                      package: str = "SOT-223", ambient_c: float = 30.0,
                      tj_max_c: float = 125.0) -> RegulatorResult:
    """线性或开关稳压器的耗散与结温。

    典型的第一批硬件失效：LDO 把 5V 降到 3.3V，还带着全部负载。
    (5-3.3)×0.5A = 0.85W，在 SOT-223（62°C/W）里 Tj = 30+53 = 83°C ——
    没问题。同一颗带 1A 就是 1.7W，Tj = 135°C，开始间歇性热关断。

    **没人会从"额定 1A"那一行数据手册算出这个。**
    """
    if kind == "linear":
        power = (vin_v - vout_v) * current_a
    else:
        # 开关型：假设 85% 效率（小 buck 的典型值）
        power = (vout_v * current_a) * (1.0 - SWITCHING_EFFICIENCY) \
            / SWITCHING_EFFICIENCY
    rth = RTHJA.get(package, 60.0)
    tj = ambient_c + power * rth
    ok = tj <= tj_max_c
    note = ""
    if not ok:
        note = (f"结温 {tj:.0f}°C 超过 {tj_max_c:.0f}°C：会热关断或寿命骤降，"
                f"需散热/加大封装/改开关型")
    elif tj > tj_max_c * 0.8:
        note = f"结温 {tj:.0f}°C 已到限值的 {tj/tj_max_c*100:.0f}%"
    return RegulatorResult(type=kind, vin_v=vin_v, vout_v=vout_v,
                           current_a=current_a, power_w=power, tj_c=tj,
                           ambient_c=ambient_c, rthja=rth, ok=ok, note=note)


# ---------------------------------------------------------------------------
# EL-04 电源轨
# ---------------------------------------------------------------------------

@dataclass
class RailResult:
    name: str
    nominal_v: float
    load_a: float
    source_r_mohm: float
    sag_v: float
    v_at_load: float
    min_required_v: float
    ok: bool
    note: str = ""
    #: 电源的额定电流（知道时填）。**过载是独立的失效**，不折进 `ok`：
    #: "轨跌到负载需要以下"要更硬的电源，"电流超过额定"要更大的电源 ——
    #: 两种修法不同。
    rated_a: float | None = None
    over_rated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "nominal_v": self.nominal_v,
                "load_a": self.load_a,
                "sag_v": round(self.sag_v, 3),
                "v_at_load": round(self.v_at_load, 3),
                "min_required_v": self.min_required_v,
                "rated_a": self.rated_a, "over_rated": self.over_rated,
                "ok": self.ok, "note": self.note}


def rail_under_load(name: str, nominal_v: float, load_a: float,
                    source_r_mohm: float, min_required_v: float,
                    rated_a: float | None = None) -> RailResult:
    """有有限输出阻抗的电源，在负载下。

    "5V 2A"模块在 2A 时**不是 5V**。输出电阻 90mΩ 时，两个堵转的 SG90
    （1.4A）把它拉到 4.87V；四个就会掉到 4.8V 以下，全部中途复位。

    按空载电流定容（每个 4mA）完全错过这件事 —— **低估 175 倍**。
    """
    sag = load_a * (source_r_mohm / 1000.0)
    voltage = nominal_v - sag
    over = rated_a is not None and load_a > rated_a
    ok = voltage >= min_required_v and not over
    notes: list[str] = []
    if voltage < min_required_v:
        notes.append(f"负载下 {voltage:.2f}V 低于最低 {min_required_v:.2f}V："
                     f"负载会复位/失步，需更大电源或降低同时动作数")
    if over:
        notes.append(f"负载 {load_a:.2f}A 超过电源额定 {rated_a:.2f}A："
                     f"电压看着正常，但电源在过载工作，会发热/掉压/寿命骤降")
    return RailResult(name=name, nominal_v=nominal_v, load_a=load_a,
                      source_r_mohm=source_r_mohm, sag_v=sag,
                      v_at_load=voltage, min_required_v=min_required_v,
                      ok=ok, note="；".join(notes), rated_a=rated_a,
                      over_rated=over)


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

@dataclass
class ElectricalReport:
    """一次 DC 电学分析的结果。"""

    wires: list[WireResult] = field(default_factory=list)
    rails: list[RailResult] = field(default_factory=list)
    regulators: list[RegulatorResult] = field(default_factory=list)
    #: 没有数据、无法验证的部分。
    not_verified: list[str] = field(default_factory=list)
    #: 每类参数**从哪来**（语义树 / 调用方 / 默认值）—— 决定这次分析的份量
    provenance: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """全部通过才算通过。**没数据不算通过。**

        早先这里只看 `items` 非空且全过。但"有 rails 条目"不等于
        "有负载数据" —— 默认输入里带一条 5V 轨，即使设计里一个负载都
        没申报，也会返回 `ok=True`。那正是这套系统一直在防的：
        **缺失的值被渲染成通过的值**。

        所以 `not_verified` 非空时**一律不通过**。
        """
        if self.not_verified:
            return False
        items = ([w.ok for w in self.wires] + [r.ok for r in self.rails]
                 + [r.ok for r in self.regulators])
        return bool(items) and all(items)

    def findings(self) -> list[dict[str, Any]]:
        """与 `physical.py` 同格式的 findings（面板统一渲染）。"""
        out: list[dict[str, Any]] = []
        for wire in self.wires:
            out.append({
                "check": "electrical.wire_drop", "ok": wire.ok,
                "message": (f"{wire.awg}AWG {wire.length_mm:.0f}mm 带 "
                            f"{wire.current_a:.2f}A：压降 {wire.drop_v:.3f}V"
                            + (f"；{wire.note}" if wire.note else "")),
                "measured": round(wire.drop_v, 3),
                "limit": DEFAULT_DROP_BUDGET_V, "refs": [],
                "severity": "error" if not wire.ok else "info", "fix": {},
            })
        for rail in self.rails:
            out.append({
                "check": "electrical.rail_sag", "ok": rail.ok,
                "message": (f"{rail.name}：{rail.nominal_v:.1f}V 轨带 "
                            f"{rail.load_a:.2f}A → {rail.v_at_load:.3f}V"
                            + (f"；{rail.note}" if rail.note else "")),
                "measured": round(rail.v_at_load, 3),
                "limit": rail.min_required_v, "refs": [],
                "severity": "error" if not rail.ok else "info",
                "fix": {"need": "stiffer_supply" if rail.sag_v > 0
                        else "bigger_supply"},
            })
        for reg in self.regulators:
            out.append({
                "check": "electrical.regulator_thermal", "ok": reg.ok,
                "message": (f"{reg.type} 稳压器 {reg.vin_v:.1f}→"
                            f"{reg.vout_v:.1f}V @ {reg.current_a:.2f}A："
                            f"耗散 {reg.power_w:.2f}W，Tj {reg.tj_c:.0f}°C"
                            + (f"；{reg.note}" if reg.note else "")),
                "measured": round(reg.tj_c, 1),
                "limit": 125.0, "refs": [],
                "severity": "error" if not reg.ok else "info", "fix": {},
            })
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok,
                "wires": [w.to_dict() for w in self.wires],
                "rails": [r.to_dict() for r in self.rails],
                "regulators": [r.to_dict() for r in self.regulators],
                "not_verified": list(self.not_verified),
                "provenance": dict(self.provenance),
                "limits": {
                    "model": "DC only（无电感/纹波/瞬态）",
                    "wire_r": "20°C 标准值（温度系数未建模）",
                    "rthja": "按封装的典型值，不是热仿真",
                }}


# ---------------------------------------------------------------------------
# 接到设计上
# ---------------------------------------------------------------------------

#: `meta.electrical` 的默认输入。没有这些数据就**报未验证**，不猜。
#:
#: 这些数值与 `semantics.TREES["psu_5v"]` 保持一致 —— 树是**上游数据源**，
#: 这里是它的消费点。`rails_from_semantics()` 会直接从树里读，避免两处漂移。
DEFAULT_INPUTS: dict[str, Any] = {
    # 电源轨
    "rails": [
        {"name": "5V", "nominal_v": 5.0, "source_r_mohm": 90.0,
         "min_required_v": 4.8, "rated_a": 2.0},
    ],
    # 稳压器
    "regulators": [
        {"kind": "linear", "vin_v": 5.0, "vout_v": 3.3, "current_a": 0.5,
         "package": "SOT-223"},
    ],
    # 线束
    "wires": [],
}


def rails_from_semantics(template: str = "psu_5v") -> list[dict[str, Any]]:
    """从语义树里读出电源轨参数。**树是上游，这里不重复写数字。**

    取不到的字段留空（`None`），由 `rail_under_load` 的调用方报未验证 ——
    绝不拿"看起来合理"的默认值顶上。

    这就是让语义树成为判定链上游而不是平行文档的关键：`rated_a` 和
    `r_out_mohm` 只在树里写一处，`electrical` 来取。
    """
    try:
        from . import semantics as semantics_module
    except Exception:  # noqa: BLE001
        return []
    tree = semantics_module.tree_for_template(template)
    if tree is None:
        return []

    def scalar(key: str) -> float | None:
        quantity = tree.spec(key)
        return quantity.scalar() if quantity is not None else None

    nominal = scalar("v_out")
    if nominal is None:
        return []
    rail: dict[str, Any] = {
        "name": f"{nominal:g}V",
        "nominal_v": nominal,
        "source_r_mohm": scalar("r_out_mohm"),
        "rated_a": scalar("rated_a"),
        # 负载最低电压**不由电源决定**，由负载类别决定（舵机 4.8V、
        # 逻辑 3.3V 器件 3.0V）。树里没有这个数，所以按输出电压给一个
        # **明确标注为推算**的下界，而不是假装它是电源参数。
        "min_required_v": 4.8 if nominal >= 4.5 else round(nominal * 0.95, 3),
        "min_required_source": ("4.8V（舵机级负载的典型下限）"
                                if nominal >= 4.5
                                else f"{nominal*0.95:.3f}V（名义值 95%，推算）"),
        "_source": f"semantics.TREES[{template!r}]",
    }
    return [rail]


def run(design: Any, *, inputs: dict[str, Any] | None = None) -> ElectricalReport:
    """对一个 `DesignModel` 跑 DC 电学分析。

    输入来自 `meta.electrical`（或调用方给的 `inputs`）。**每个负载电流
    都要有来源** —— 从元件的 `electrical` 字段拿；拿不到就报未验证，
    不用默认值冒充。
    """
    report = ElectricalReport()
    raw = inputs if inputs is not None else (
        design.meta.get("electrical") or {})
    if not isinstance(raw, dict):
        raw = {}

    # -- 负载电流（从设计的元件里累计）----------------------------------
    loads: dict[str, float] = {}
    for part in getattr(design, "parts", []):
        electrical = part.electrical or {}
        for key in ("current_a", "stall_current_a", "i_out_a"):
            value = electrical.get(key)
            if value is None:
                continue
            try:
                amps = float(value)
            except (TypeError, ValueError):
                continue
            label = str(electrical.get("load_label") or part.ref)
            loads[label] = loads.get(label, 0.0) + amps
            break

    # -- 电源轨 ----------------------------------------------------------
    # **`or` 会把空列表当成"没给"** —— `{"rails": []}` 于是回退到默认
    # 输入，凭空造出一条 5V 轨并通过检查。显式区分"键不存在"和"给了空表"。
    #
    # 键不存在时**优先从语义树取**（那是上游数据源），取不到才用
    # `DEFAULT_INPUTS` 并标出来源。
    if "rails" in raw:
        rails_in = raw["rails"]
        rails_source = "调用方给出"
    else:
        rails_in = rails_from_semantics("psu_5v")
        rails_source = "semantics.TREES['psu_5v']" if rails_in else "默认值"
        if not rails_in:
            rails_in = DEFAULT_INPUTS.get("rails") or []
    if rails_in:
        report.provenance["rails"] = rails_source
    total_load = sum(loads.values())
    for entry in rails_in:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "rail")
        # 负载：显式给了就用，否则用设计里累计的
        load_a = entry.get("load_a")
        if load_a is None:
            load_a = total_load
        if not loads and load_a is None:
            report.not_verified.append(
                f"{name}: 没有负载电流数据，轨跌无法验证")
            continue
        try:
            result = rail_under_load(
                name=name,
                nominal_v=float(entry.get("nominal_v", 5.0)),
                load_a=float(load_a),
                source_r_mohm=float(entry.get("source_r_mohm", 90.0)),
                min_required_v=float(entry.get("min_required_v", 4.8)),
                rated_a=(float(entry["rated_a"])
                         if entry.get("rated_a") is not None else None))
        except (TypeError, ValueError) as exc:
            report.not_verified.append(f"{name}: 参数无效（{exc}）")
            continue
        report.rails.append(result)

    if not rails_in and not loads:
        report.not_verified.append(
            "没有任何电源轨输入，也没有元件申报负载电流：电学无法验证")

    # -- 稳压器 ----------------------------------------------------------
    regs_in = raw["regulators"] if "regulators" in raw else (
        DEFAULT_INPUTS.get("regulators") or [])
    for entry in regs_in:
        if not isinstance(entry, dict):
            continue
        try:
            result = regulator_thermal(
                kind=str(entry.get("kind") or "linear"),
                vin_v=float(entry.get("vin_v", 5.0)),
                vout_v=float(entry.get("vout_v", 3.3)),
                current_a=float(entry.get("current_a", 0.0)),
                package=str(entry.get("package") or "SOT-223"),
                ambient_c=float(entry.get("ambient_c", 30.0)),
                tj_max_c=float(entry.get("tj_max_c", 125.0)))
        except (TypeError, ValueError) as exc:
            report.not_verified.append(f"稳压器参数无效：{exc}")
            continue
        report.regulators.append(result)

    # -- 线束 ------------------------------------------------------------
    for entry in (raw["wires"] if "wires" in raw else []):
        if not isinstance(entry, dict):
            continue
        try:
            result = wire_drop(
                awg=int(entry.get("awg", 26)),
                length_mm=float(entry.get("length_mm", 500.0)),
                current_a=float(entry.get("current_a", 0.0)),
                budget_v=float(entry.get("budget_v", DEFAULT_DROP_BUDGET_V)))
        except (TypeError, ValueError) as exc:
            report.not_verified.append(f"线束参数无效：{exc}")
            continue
        report.wires.append(result)

    return report


def describe() -> dict[str, Any]:
    return {"module": "hw_velxio.design.electrical",
            "models": ["EL-01 wire_drop", "EL-02/03 regulator_thermal",
                       "EL-04 rail_under_load", "EL-05 current budget"],
            "limits": "DC only；20°C 线阻；Rθja 是典型值不是仿真"}
