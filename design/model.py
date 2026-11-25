"""规范模型 `DesignModel`（单位 mm）—— 2D 与 3D 的唯一事实源。

约定的轴（与 hwforge `space.py` 一致）：

    +X 向右（东）   +Y 向上（北）   +Z 垂直板面向上

`size_mm` 字典的键是 **二维足迹 + 高度**，写清楚以免和 hwforge 的
``w/d/h``（w=X, d=Y, h=Z）混淆：

    w = X 方向尺寸（宽）
    h = Y 方向尺寸（2D 视图里的"高"）
    d = Z 方向尺寸（实体高度 / 厚度方向）

`placement.x_mm, y_mm` 是**未旋转足迹的最小角**（与 Velxio 的
``Component{x,y}`` 同一约定）；旋转绕足迹中心，`rotation_z ∈ {0,90,180,270}`。
`z_mm` 是元件底面相对板面（板顶）的高度，板本身 ``z ∈ [-thickness, 0]``。

`physical_source` 记录尺寸从哪来，**判定与 3D 必须如实显示**：

    datasheet  数据手册/官方尺寸
    measured   实测
    estimated  由画布像素或类别默认值反推（不可当精确值）
    derived    由其他已知量推导

同一份 JSON 在 Python 宿主、TS 宿主、客户端之间往返，字段名不许改。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "dsh_embagent.design/1"
#: 改包名（2026-11-25）之前的 schema 字面量。**必须继续接收**：
#: 用户磁盘上已有的 design.json 就是这样写的（示例-单板 / 示例-无人机…），
#: 换个包名不该让老工程读不出来。读到旧值时按新值规范化后写回。
LEGACY_SCHEMAS = ("hw_velxio.design/1",)
UNITS = "mm"

#: 网络类（B2）。**单一事实源**：`bus.BUSES` 的 class、`constraints.py` 的判据、
#: `assert`/`handoff` 的措辞都读这一张表；再抄一份就是 AGENTS §3.15 的老坑。
NET_CLASSES = (
    "power", "ground", "signal", "spi", "i2c", "uart", "can", "usb",
    "clock", "reset", "boot", "debug", "analog", "hs", "diff",
)

#: 允许的来源值。**这一份是唯一事实源**（`validate()` 与各处按它判，别另抄）。
#:
#: P41 补了两个：
#: * `catalog` —— `parts_catalog.py:650` 一直在写它（JEDEC 尺寸），但这里没有，
#:   于是"目录回填过的元件"**过不了 validate**。这是老 bug，不是新功能。
#: * `proposed` —— AI 先给提案、人再改。它**比 estimated 更低**：estimated 至少
#:   是按公式算的，proposed 只是"看起来像"。
PHYSICAL_SOURCES = ("datasheet", "measured", "catalog", "estimated",
                    "derived", "proposed")

#: 来源可信度（**低 → 高**）。比较来源时用它，不要自己写 if/elif。
#: `derived` 与 `proposed` 都低，但 `derived` 有推导过程，排在 proposed 之上。
SOURCE_ORDER = ("proposed", "derived", "estimated", "catalog", "datasheet", "measured")

#: 立创贴片档位。`basic` = 基础库（无上料费），`extended` = 扩展库（每个编号收费）。
JLC_CLASSES = ("basic", "preferred", "extended")


def source_at_least(source: str, floor: str) -> bool:
    """`source` 的可信度是否**不低于** `floor`（两者都必须在表里）。"""
    try:
        return SOURCE_ORDER.index(source) >= SOURCE_ORDER.index(floor)
    except ValueError:
        return False
MOUNTS = ("board", "panel", "air")
ROTATIONS = (0, 90, 180, 270)

#: 整机实体的类别。**这不是封装类别**：`motor` 是电机本体，不是驱动 IC。
#: 新增一类要同时想清楚三件事：形状怎么画、质量怎么算、关节怎么连。
BODY_KINDS = (
    "motor", "propeller", "arm", "frame", "shell", "lid",
    "battery", "gimbal", "sensor", "generic",
)

#: 形状白名单。**只做这四种 + 网格引用**：每一种都能被 3D 直接画出来；
#: 画不出来的形状（异形/扫掠）先不收录，免得"有类型没几何"。
BODY_SHAPES = ("box", "cylinder", "disc", "prism", "mesh")

#: 外壳面的名字。**投影约定**（`shell.py` 与 2D/3D 共用同一套）：
#:
#:   `top` / `bottom` —— x 沿宽（w）、y 沿深（h）
#:   `front` / `back` —— x 沿宽（w）、y 沿高（d）
#:   `left` / `right` —— x 沿深（h）、y 沿高（d）
#:
#: 写反了不会报错，只会把孔开到另一面——所以这套约定只写在这里一处。
ENCLOSURE_FACES = ("top", "bottom", "front", "back", "left", "right")

Point2 = tuple[float, float]
Point3 = tuple[float, float, float]


class DesignError(ValueError):
    """模型不合法。消息必须说清楚是哪个字段、哪个对象。"""


def _num(value: Any, what: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise DesignError(f"{what} 必须是数字，收到 {value!r}") from exc
    if out != out or out in (float("inf"), float("-inf")):
        raise DesignError(f"{what} 必须是有限数，收到 {value!r}")
    return out


def _rotation(value: Any) -> int:
    try:
        rot = int(round(float(value) / 90.0)) * 90 % 360
    except (TypeError, ValueError) as exc:
        raise DesignError(f"rotation_z 必须是 0/90/180/270，收到 {value!r}") from exc
    if rot not in ROTATIONS:
        raise DesignError(f"rotation_z 必须是 0/90/180/270，收到 {value!r}")
    return rot


@dataclass
class MountHole:
    """板上的安装孔。`source` 与 `physical_source` 同一套取值。"""

    x_mm: float
    y_mm: float
    diameter_mm: float = 3.2
    source: str = "estimated"

    def __post_init__(self) -> None:
        self.x_mm = _num(self.x_mm, "mount_hole.x_mm")
        self.y_mm = _num(self.y_mm, "mount_hole.y_mm")
        self.diameter_mm = _num(self.diameter_mm, "mount_hole.diameter_mm")
        if self.diameter_mm <= 0:
            raise DesignError("mount_hole.diameter_mm 必须为正")
        if self.source not in PHYSICAL_SOURCES:
            raise DesignError(f"mount_hole.source 非法：{self.source!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "x_mm": round(self.x_mm, 4),
            "y_mm": round(self.y_mm, 4),
            "diameter_mm": self.diameter_mm,
            "source": self.source,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "MountHole":
        return MountHole(
            x_mm=data.get("x_mm", 0), y_mm=data.get("y_mm", 0),
            diameter_mm=data.get("diameter_mm", 3.2),
            source=str(data.get("source", "estimated")),
        )


@dataclass
class Placement:
    """位置/朝向/安装面 —— 2D 与 3D 共享的唯一几何输入。"""

    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0
    rotation_z: int = 0
    mount: str = "board"
    #: 安装面：`"top"`（顶层）/ `"bottom"`（底层）/ `""`（未声明）。
    #:
    #: **这一项决定重叠判定是否成立。** 实测主 PCB：141 个元件里 71 个在顶层、
    #: 70 个在底层。双面贴装的板上，顶层的 C80 和底层的 C82 **XY 重叠是正常的**
    #: ——它们在板的两侧。没有这一项，重叠检查会把所有双面板报成几十条假错误。
    side: str = ""
    #: 所属板（多板设计）。空串 = 用 `DesignModel.board`（单板/主参考板）。
    #:
    #: 坐标是**这块板的局部坐标**，不是拼板坐标。这样移动一块板不会让
    #: 它上面所有元件的坐标都要跟着改。
    board_id: str = ""

    def __post_init__(self) -> None:
        self.x_mm = _num(self.x_mm, "placement.x_mm")
        self.y_mm = _num(self.y_mm, "placement.y_mm")
        self.z_mm = _num(self.z_mm, "placement.z_mm")
        self.rotation_z = _rotation(self.rotation_z)
        if self.mount not in MOUNTS:
            raise DesignError(f"placement.mount 非法：{self.mount!r}")
        self.side = str(self.side or "").strip().lower()
        if self.side not in ("", "top", "bottom"):
            raise DesignError(
                f"placement.side 非法：{self.side!r}（应为 top/bottom/空）")
        self.board_id = str(self.board_id or "").strip()

    def to_dict(self) -> dict[str, Any]:
        out = {
            "x_mm": round(self.x_mm, 4),
            "y_mm": round(self.y_mm, 4),
            "z_mm": round(self.z_mm, 4),
            "rotation_z": self.rotation_z,
            "mount": self.mount,
        }
        if self.side:
            out["side"] = self.side
        if self.board_id:
            out["board_id"] = self.board_id
        return out

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "Placement":
        data = data or {}
        return Placement(
            x_mm=data.get("x_mm", 0), y_mm=data.get("y_mm", 0),
            z_mm=data.get("z_mm", 0), rotation_z=data.get("rotation_z", 0),
            mount=str(data.get("mount", "board")),
            side=str(data.get("side", "")),
            board_id=str(data.get("board_id", "")),
        )


#: 归因的可信度来源。**顺序就是可信度顺序**（高的在前）。
#:
#: `asserted` 是人/AI 明确写下的；`backfill` 是从已有工程反推出来的；
#: `inferred_by_topology` 是从网表拓扑算出来的；`unknown` 是判不出。
#:
#: 为什么要分这么细而不是一个 bool：`handoff.md` 要按它分表——
#: 人声明的进"已确认"，拓扑推的进"AI 推的待核"，unknown 进"空白"。
#: 混在一起的话，从拓扑推出来的 1kΩ 上拉电阻会被当成人的决定。
JUSTIFICATION_CONFIDENCE = (
    "asserted",              # 人明确声明（或 AI 声明并已人工确认）
    "backfill",              # 从现有工程反推（有依据，但没人签字）
    "inferred_by_topology",  # 纯结构推导（只说得出"看起来是干什么的"）
    "unknown",               # 判不出
)

#: 归因的来源方。
JUSTIFICATION_SOURCES = ("human", "ai", "import")


@dataclass
class Justification:
    """元件/单元**为什么在**。这是 S2/S3 判据的载体。

    为什么独立成类型而不是塞进 `notes`：`notes` 是自由文本，写什么都行，
    没有任何消费者；而这里的每个字段都要被 `handoff.md` 消费、被断言引用、
    被"删除即失败"验证。**能被消费的字段才会被认真填。**

    `why` 必须含**具体网名/元件名**（由 `justify.py` 强制拼接）。
    没有具体指称的理由是套话，套话无法被证伪，也就无法被审查。
    """

    #: 服务的需求 id（对应 `requirements.py` 的条目）
    requirement: list[str] = field(default_factory=list)
    #: 人话理由。**必须含具体指称**（网名 / 元件号 / 数值）
    why: str = ""
    #: 删掉它会怎样。空字符串表示"没想过"——不允许
    effect_if_removed: str = ""
    #: 便于换料：同类可替代件的说明
    alternatives: list[str] = field(default_factory=list)
    #: 见 `JUSTIFICATION_CONFIDENCE`
    confidence: str = "asserted"
    #: 见 `JUSTIFICATION_SOURCES`
    source: str = "human"
    #: 归因依据的可读形式（网表路径 / 角色表条目 / 申报字段）
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.confidence not in JUSTIFICATION_CONFIDENCE:
            raise DesignError(
                f"justification.confidence 非法：{self.confidence!r}"
                f"（允许 {', '.join(JUSTIFICATION_CONFIDENCE)}）")
        if self.source not in JUSTIFICATION_SOURCES:
            raise DesignError(
                f"justification.source 非法：{self.source!r}"
                f"（允许 {', '.join(JUSTIFICATION_SOURCES)}）")

    #: 这条理由是否"算数"——只有 asserted / backfill 算人确认过。
    @property
    def signed(self) -> bool:
        return self.confidence in ("asserted", "backfill")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "why": self.why,
            "effect_if_removed": self.effect_if_removed,
            "confidence": self.confidence,
            "source": self.source,
        }
        if self.requirement:
            out["requirement"] = list(self.requirement)
        if self.alternatives:
            out["alternatives"] = list(self.alternatives)
        if self.evidence:
            out["evidence"] = self.evidence
        return out

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "Justification":
        data = data or {}
        return Justification(
            requirement=[str(x) for x in (data.get("requirement") or [])],
            why=str(data.get("why") or ""),
            effect_if_removed=str(data.get("effect_if_removed") or ""),
            alternatives=[str(x) for x in (data.get("alternatives") or [])],
            confidence=str(data.get("confidence") or "asserted"),
            source=str(data.get("source") or "human"),
            evidence=str(data.get("evidence") or ""),
        )


@dataclass
class Part:
    """一个物理/电气对象。`ref` 是 2D、3D、判定共用的稳定 id。"""

    ref: str
    kind: str
    label: str = ""
    role: str = "component"
    size_mm: dict[str, float] = field(
        default_factory=lambda: {"w": 0.0, "h": 0.0, "d": 0.0})
    physical_source: str = "estimated"
    placement: Placement = field(default_factory=Placement)
    lcsc: str | None = None
    #: 立创贴片档位：`basic` / `preferred` / `extended`（空 = 不知道）。
    #:
    #: **它决定贴片费**：basic 不收上料费，extended 每个编号一笔。所以它不是
    #: 装饰字段——选料时它是约束（同一功能优先 basic）。值来自 LCSC 目录，
    #: 不给就空着，**不猜**。
    jlc_class: str = ""
    electrical: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # 引脚表：**只有导入器实测到才填**（立创 EDA 的焊盘号、KiCad 的焊盘）。
    # 为空表示"这个元件没有引脚信息"，此时网表引脚**不校验**，
    # 由 `pins.audit()` 作为未验证项报出——见 design/pins.py 的三档来源。
    pins: list[str] = field(default_factory=list)
    # 焊盘**几何**（板坐标系）。有它，2D 能画焊盘、飞线能连到真实引脚、
    # 3D 有焊点；没有它，网表引脚只是名字（仍可校验存在性）。
    pads: list[Pad] = field(default_factory=list)
    #: 这个元件为什么在板上。**None 表示没人说过**——不是"不需要理由"。
    #: 旧文件没有这个键 → None；`to_dict` 时省略，所以旧文件往返不变。
    justification: Justification | None = None
    #: 所属功能单元 id（对应 `DesignModel.units` 里的项）。空 = 未归属。
    unit: str = ""
    # ---- 选型 / 供应链（B1）--------------------------------------------
    #
    # 这五项让"为什么选它"能被机器读到：型号、封装、替代料、单价、库存交期、
    # 降额。**全部可选**：空 = 没人填，`to_dict` 省略，旧文件往返不变。
    #: 制造商型号（与 `lcsc` 并列：一个是目录号，一个是厂家号）。
    mpn: str = ""
    #: 封装名（如 `LQFP-48`、`SOT-223`）。**不是尺寸**；尺寸仍在 `size_mm`。
    footprint: str = ""
    #: 替代料清单：`[{mpn, lcsc, note}]`。空 = 没评估过替代。
    alternates: list[dict[str, Any]] = field(default_factory=list)
    #: 成本：`{"unit_cny": 0.85, "qty": 1, "currency": "CNY", "source": "lcsc"}`。
    cost: dict[str, Any] = field(default_factory=dict)
    #: 供货：`{"stock": 12000, "lead_time_days": 3, "source": "lcsc"}`。
    supply: dict[str, Any] = field(default_factory=dict)
    #: 降额：`{"voltage_pct": 0.8, "current_pct": 0.7, "temp_c": 85}`。
    #: 比例是 0–1；缺项 = 没评估，不是 0。
    derating: dict[str, Any] = field(default_factory=dict)
    # ---- 机械域（P15）-------------------------------------------------
    #
    # 这三项是 MuJoCo 仿真的输入（v3 架构 L2 的 kinematic 域）。
    # **全部可选**：旧文件没有 → None，`to_dict` 省略，往返不变。
    #
    # 为什么不放进 `electrical`：`electrical` 是电气参数（电压/电流），
    # 混进质量会让 `electrical.run()` 的"零申报"判定失效——
    # 它按 `electrical` 非空来数申报元件，质量不该算作电气申报。
    #: 质量（克）。None = **没人称过**，不是 0 g。
    #: 来源见 `physical_source`：datasheet/measured 可信，catalog/estimated 不可信。
    mass_g: float | None = None
    #: 质心（mm，零件自身坐标系原点为基准）。None = 按几何中心近似。
    com_mm: list[float] | None = None
    #: 转动惯量（kg·m²），6 分量 `[Ixx, Iyy, Izz, Ixy, Ixz, Iyz]`。
    #: None = **不算**，而不是当作 0（点质量）——点质量的惯量是 0，
    #: 会让旋转仿真变成"无阻力"，那是假通过。
    inertia: list[float] | None = None

    def __post_init__(self) -> None:
        if not self.ref.strip():
            raise DesignError("part.ref 不能为空")
        if not self.kind.strip():
            raise DesignError(f"part {self.ref}: kind 不能为空")
        # 引脚表去重、去空白，保持声明顺序（焊盘顺序有意义）。
        seen_pins: set[str] = set()
        cleaned: list[str] = []
        for pin in self.pins or []:
            name = str(pin).strip()
            if name == "" or name in seen_pins:
                continue
            seen_pins.add(name)
            cleaned.append(name)
        self.pins = cleaned
        # 机械域三项：只校验"给了就合法"，不给就保持 None。
        #
        # 为什么 mass_g 允许 None 而不默认 0：0 g 的零件在 MuJoCo 里
        # 会让质量矩阵奇异，求解器直接崩或给出荒谬结果。
        # **None（没人称过）与 0（称出来是 0）必须分开**，这是三态纪律。
        if self.mass_g is not None:
            value = float(self.mass_g)
            if value < 0:
                raise DesignError(f"part {self.ref}: mass_g 不能为负（{value}）")
            self.mass_g = value
        if self.com_mm is not None:
            points = [float(x) for x in self.com_mm]
            if len(points) != 3:
                raise DesignError(
                    f"part {self.ref}: com_mm 需要 3 个数，收到 {len(points)}")
            self.com_mm = points
        if self.inertia is not None:
            values = [float(x) for x in self.inertia]
            if len(values) != 6:
                raise DesignError(
                    f"part {self.ref}: inertia 需要 6 个分量"
                    f"（Ixx Iyy Izz Ixy Ixz Iyz），收到 {len(values)}")
            self.inertia = values

        size = self.size_mm or {}
        self.size_mm = {
            "w": _num(size.get("w", 0), f"part {self.ref}.size_mm.w"),
            "h": _num(size.get("h", 0), f"part {self.ref}.size_mm.h"),
            "d": _num(size.get("d", 0), f"part {self.ref}.size_mm.d"),
        }
        for axis, value in self.size_mm.items():
            if value < 0:
                raise DesignError(f"part {self.ref}.size_mm.{axis} 不能为负")
        if self.jlc_class and self.jlc_class not in JLC_CLASSES:
            raise DesignError(
                f"part {self.ref}.jlc_class 非法：{self.jlc_class!r}"
                f"（允许 {', '.join(JLC_CLASSES)}）")
        if self.physical_source not in PHYSICAL_SOURCES:
            raise DesignError(
                f"part {self.ref}.physical_source 非法：{self.physical_source!r}")
        if not self.label:
            self.label = self.kind

    # -- geometry ---------------------------------------------------------

    def footprint_mm(self) -> tuple[float, float, float, float]:
        """旋转后的足迹 AABB：``(x0, y0, x1, y1)``，绕足迹中心旋转。"""
        w, h = self.size_mm["w"], self.size_mm["h"]
        cx = self.placement.x_mm + w / 2.0
        cy = self.placement.y_mm + h / 2.0
        if self.placement.rotation_z in (90, 270):
            w, h = h, w
        return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)

    def bbox_mm(self) -> tuple[float, float, float, float, float, float]:
        """实体 AABB：``(x0, y0, z0, x1, y1, z1)``（板坐标系）。

        **底层元件的 z 是负的**（板厚向下延伸），顶层是 0..d。
        这样"顶层的件"和"底层的件"在 z 上天然不相交 —— 重叠检查不再需要
        额外的过滤逻辑，而且 3D 渲染可以直接用这个盒子。
        """
        x0, y0, x1, y1 = self.footprint_mm()
        d = self.size_mm["d"]
        if self.placement.side == "bottom":
            # 底层：贴着板底面向下伸
            z1 = self.placement.z_mm
            z0 = z1 - d
        else:
            z0 = self.placement.z_mm
            z1 = z0 + d
        return (x0, y0, z0, x1, y1, z1)

    def center_mm(self) -> Point3:
        x0, y0, z0, x1, y1, z1 = self.bbox_mm()
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0, (z0 + z1) / 2.0)

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ref": self.ref,
            "kind": self.kind,
            "label": self.label,
            "role": self.role,
            "size_mm": {k: round(v, 4) for k, v in self.size_mm.items()},
            "physical_source": self.physical_source,
            "placement": self.placement.to_dict(),
        }
        if self.lcsc:
            out["lcsc"] = self.lcsc
        if self.jlc_class:
            out["jlc_class"] = self.jlc_class
        if self.electrical:
            out["electrical"] = dict(self.electrical)
        if self.notes:
            out["notes"] = list(self.notes)
        if self.pins:
            out["pins"] = list(self.pins)
        if self.pads:
            out["pads"] = [p.to_dict() for p in self.pads]
        # 只有填了才写：旧文件读进来没有这两个键，写回去也不新增，
        # 于是"读-改-写"不会把 141 个元件全变成带 justification 的文件。
        if self.justification is not None:
            out["justification"] = self.justification.to_dict()
        if self.unit:
            out["unit"] = self.unit
        # B1：只在填了才写（旧文件往返不变）。
        if self.mpn:
            out["mpn"] = self.mpn
        if self.footprint:
            out["footprint"] = self.footprint
        if self.alternates:
            out["alternates"] = [dict(a) for a in self.alternates]
        if self.cost:
            out["cost"] = dict(self.cost)
        if self.supply:
            out["supply"] = dict(self.supply)
        if self.derating:
            out["derating"] = dict(self.derating)
        # 机械域三项：**None 与 0 分开**。
        # mass_g=0 是"称出来是 0"（合法但不常见），None 是"没人称过"。
        # 用 `is not None` 而不是真值判断，否则 0 会被当成"没填"丢掉。
        if self.mass_g is not None:
            out["mass_g"] = round(float(self.mass_g), 4)
        if self.com_mm is not None:
            out["com_mm"] = [round(float(x), 4) for x in self.com_mm]
        if self.inertia is not None:
            out["inertia"] = [float(x) for x in self.inertia]
        return out

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Part":
        return Part(
            ref=str(data["ref"]),
            kind=str(data["kind"]),
            label=str(data.get("label") or ""),
            role=str(data.get("role") or "component"),
            size_mm=dict(data.get("size_mm") or {}),
            physical_source=str(data.get("physical_source") or "estimated"),
            placement=Placement.from_dict(data.get("placement")),
            lcsc=data.get("lcsc"),
            jlc_class=str(data.get("jlc_class") or "").strip(),
            electrical=dict(data.get("electrical") or {}),
            notes=[str(x) for x in (data.get("notes") or [])],
            pins=[str(x) for x in (data.get("pins") or [])],
            pads=[Pad.from_dict(p) for p in (data.get("pads") or [])],
            # 旧文件没有这两个键：justification=None / unit=""，行为不变。
            justification=(Justification.from_dict(data["justification"])
                           if data.get("justification") else None),
            unit=str(data.get("unit") or ""),
            # B1：旧文件没有这些键 → 空，行为不变。
            mpn=str(data.get("mpn") or ""),
            footprint=str(data.get("footprint") or ""),
            alternates=[dict(a) for a in (data.get("alternates") or [])
                        if isinstance(a, dict)],
            cost=dict(data.get("cost") or {}),
            supply=dict(data.get("supply") or {}),
            derating=dict(data.get("derating") or {}),
            # 旧文件没有这三个键 → None，行为不变。
            mass_g=(float(data["mass_g"])
                    if data.get("mass_g") is not None else None),
            com_mm=([float(x) for x in data["com_mm"]]
                    if data.get("com_mm") is not None else None),
            inertia=([float(x) for x in data["inertia"]]
                     if data.get("inertia") is not None else None),
        )

    def pad_of(self, num: str) -> Pad | None:
        """按引脚号找焊盘（大小写不敏感）。"""
        wanted = str(num).strip().upper()
        for pad in self.pads:
            if pad.num.upper() == wanted:
                return pad
        return None


@dataclass
class Board:
    """板轮廓 + 厚度 + 安装孔。轮廓是板坐标系里的闭合多边形（mm）。

    `id` 是这块板在**多板设计**里的稳定标识（来自导入器的文档 id，
    如 `PCB#a22effc90c196540`）。单板设计留空。

    每块板有**自己的局部坐标系**（原点在它自己的左下角）。元件用
    `Placement.board_id` 声明归属，坐标是**那块板的局部坐标**。
    渲染时才按拼板排布平移过去 —— 平移只在 `projection` 里做一次。
    """

    id: str = ""
    kind: str = "arduino-uno"
    name: str = "Arduino Uno"
    outline: list[Point2] = field(default_factory=list)
    size_mm: dict[str, float] = field(
        default_factory=lambda: {"w": 72.58, "h": 53.34})
    thickness_mm: float = 1.6
    mount_holes: list[MountHole] = field(default_factory=list)
    px_per_mm: float = 3.7795

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise DesignError("board.kind 不能为空")
        self.thickness_mm = _num(self.thickness_mm, "board.thickness_mm")
        if self.thickness_mm <= 0:
            raise DesignError("board.thickness_mm 必须为正")
        size = self.size_mm or {}
        self.size_mm = {
            "w": _num(size.get("w", 0), "board.size_mm.w"),
            "h": _num(size.get("h", 0), "board.size_mm.h"),
        }
        if self.size_mm["w"] <= 0 or self.size_mm["h"] <= 0:
            raise DesignError("board.size_mm 必须为正")
        self.px_per_mm = _num(self.px_per_mm, "board.px_per_mm")
        if self.px_per_mm <= 0:
            raise DesignError("board.px_per_mm 必须为正")
        if not self.outline:
            w, h = self.size_mm["w"], self.size_mm["h"]
            self.outline = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
        if len(self.outline) < 3:
            raise DesignError("board.outline 至少需要 3 个点")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "name": self.name,
            "outline": [[round(x, 4), round(y, 4)] for x, y in self.outline],
            "size_mm": {k: round(v, 4) for k, v in self.size_mm.items()},
            "thickness_mm": self.thickness_mm,
            "mount_holes": [h.to_dict() for h in self.mount_holes],
            "px_per_mm": self.px_per_mm,
        }
        if self.id:
            out["id"] = self.id
        return out

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Board":
        return Board(
            id=str(data.get("id") or ""),
            kind=str(data.get("kind") or "generic"),
            name=str(data.get("name") or "Generic"),
            outline=[(float(p[0]), float(p[1])) for p in (data.get("outline") or [])],
            size_mm=dict(data.get("size_mm") or {}),
            thickness_mm=data.get("thickness_mm", 1.6),
            mount_holes=[MountHole.from_dict(h) for h in (data.get("mount_holes") or [])],
            px_per_mm=data.get("px_per_mm", 3.7795),
        )


@dataclass
class Pad:
    """一个焊盘：**引脚的几何事实**。

    这是"网表从声明变成事实"的那一步。`Part.pins` 只有引脚**名字**
    （`"1"`、`"A"`），没有位置——于是 2D 画不出焊盘、飞线只能连元件中心、
    3D 没有焊点。

    坐标是**板坐标系**（mm，Y 向上，原点在板框左下角），和 `Part.placement`
    同一套坐标系。`layer` �� 1 = 顶层，2 = 底层，12 = 多层（通孔）。
    """

    num: str
    x_mm: float = 0.0
    y_mm: float = 0.0
    w_mm: float = 0.0
    h_mm: float = 0.0
    shape: str = "RECT"
    rotation_z: float = 0.0
    layer: int = 1
    hole_mm: float = 0.0

    def __post_init__(self) -> None:
        if not str(self.num).strip():
            raise DesignError("pad.num 不能为空")
        self.num = str(self.num).strip()
        self.x_mm = float(self.x_mm)
        self.y_mm = float(self.y_mm)
        self.w_mm = float(self.w_mm)
        self.h_mm = float(self.h_mm)
        self.rotation_z = float(self.rotation_z) % 360.0
        self.layer = int(self.layer)
        self.hole_mm = float(self.hole_mm)

    def to_dict(self) -> dict[str, Any]:
        return {
            "num": self.num,
            "x_mm": round(self.x_mm, 4),
            "y_mm": round(self.y_mm, 4),
            "w_mm": round(self.w_mm, 4),
            "h_mm": round(self.h_mm, 4),
            "shape": self.shape,
            "rotation_z": round(self.rotation_z, 3),
            "layer": self.layer,
            "hole_mm": round(self.hole_mm, 4),
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Pad":
        return Pad(
            num=str(data.get("num") or ""),
            x_mm=float(data.get("x_mm") or 0.0),
            y_mm=float(data.get("y_mm") or 0.0),
            w_mm=float(data.get("w_mm") or 0.0),
            h_mm=float(data.get("h_mm") or 0.0),
            shape=str(data.get("shape") or "RECT"),
            rotation_z=float(data.get("rotation_z") or 0.0),
            layer=int(data.get("layer") or 1),
            hole_mm=float(data.get("hole_mm") or 0.0),
        )


@dataclass
class Wire:
    """一根导线：网表是语义真值，`path_mm` 只是走线几何（可缺省）。

    `layer` / `width_mm` 来自 PCB 文件（立创的 `LINE.layerId` / `LINE.width`）。
    它们是**真实铜层属性** —— 3D 里走线贴在顶层还是底层由 `layer` 决定。
    """

    net: str
    path_mm: list[Point3] = field(default_factory=list)
    layer: int = 1
    width_mm: float = 0.25

    def __post_init__(self) -> None:
        if not self.net.strip():
            raise DesignError("wire.net 不能为空")
        if self.path_mm and len(self.path_mm) < 2:
            raise DesignError(f"wire {self.net}: path_mm 至少需要 2 个点")
        self.layer = int(self.layer or 1)
        self.width_mm = _num(self.width_mm, f"wire {self.net}.width_mm")
        if self.width_mm <= 0:
            self.width_mm = 0.25

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "net": self.net,
            "path_mm": [[round(x, 4), round(y, 4), round(z, 4)]
                        for x, y, z in self.path_mm],
        }
        if self.layer != 1:
            out["layer"] = self.layer
        if self.width_mm != 0.25:
            out["width_mm"] = round(self.width_mm, 4)
        return out

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Wire":
        return Wire(
            net=str(data["net"]),
            path_mm=[(float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0)
                     for p in (data.get("path_mm") or [])],
            layer=int(data.get("layer") or 1),
            width_mm=float(data.get("width_mm") or 0.25),
        )


@dataclass
class Enclosure:
    """外壳：**参数化腔体**（内腔 + 壁厚 + 地板/盖 + 开口）。

    ## 尺寸语义（写反了会让"限高"算错，所以逐条定死）

    * `inner_mm.w/h` —— **内腔footprint**（长、宽）。
    * `inner_mm.d`   —— 板顶以上的**可用高度**。`physical._declared_height_limit`
      正是拿它当限高用，所以它的语义不能改。
    * `floor_z_mm`   —— 内腔地板到**板底面**的高度（螺柱/台阶）。
    * `lid_mm`       —— 盖厚（0 = 没有盖）。**限高不扣盖厚**：那是另一个判据
      （`shell.height`），这里不偷改既有语义。

    于是总高 = `wall + floor_z + board_thickness + inner.d + lid`。
    `outer_mm()` 是**方法**而不是字段：板厚不属于外壳，必须由调用方给进来。

    ## 开口

    `openings[]` 每项 `{face, x_mm, y_mm, w_mm, h_mm, kind}`；`face` 取
    `ENCLOSURE_FACES`，面上坐标的轴向见那里的注释。**位置只记语义，不做曲面**：
    异形开孔（倒角、沉孔、网罩）表达不了，如实记 `kind="user"`。
    """

    inner_mm: dict[str, float] = field(
        default_factory=lambda: {"w": 0.0, "h": 0.0, "d": 0.0})
    wall_mm: float = 2.0
    #: 盖厚（0 = 没有盖）。**空值不写进文件**（旧工程往返不变）。
    lid_mm: float = 0.0
    #: 内腔地板到板底面的高度（螺柱高度）。
    floor_z_mm: float = 0.0
    openings: list[dict[str, Any]] = field(default_factory=list)

    def outer_mm(self, board_thickness_mm: float = 0.0) -> dict[str, float]:
        """外形尺寸。**板厚由调用方给**：外壳不知道板有多厚。"""
        inner = {k: float(self.inner_mm.get(k) or 0.0) for k in ("w", "h", "d")}
        return {
            "w": round(inner["w"] + 2.0 * self.wall_mm, 4),
            "h": round(inner["h"] + 2.0 * self.wall_mm, 4),
            "d": round(self.wall_mm + self.floor_z_mm + board_thickness_mm
                       + inner["d"] + self.lid_mm, 4),
        }

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "inner_mm": {k: round(float(v), 4) for k, v in self.inner_mm.items()},
            "wall_mm": self.wall_mm,
            "openings": [dict(o) for o in self.openings],
        }
        # 只在非默认时写：旧工程读进来再写回去，文件逐字节不变。
        if self.lid_mm != 0.0:
            out["lid_mm"] = self.lid_mm
        if self.floor_z_mm != 0.0:
            out["floor_z_mm"] = self.floor_z_mm
        return out

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Enclosure":
        return Enclosure(
            inner_mm=dict(data.get("inner_mm") or {}),
            wall_mm=float(data.get("wall_mm", 2.0) or 2.0),
            lid_mm=float(data.get("lid_mm") or 0.0),
            floor_z_mm=float(data.get("floor_z_mm") or 0.0),
            openings=[dict(o) for o in (data.get("openings") or [])],
        )


#: 机械关系的 kind 全集。**每个 kind 的语义与 MuJoCo 的映射写在注释里**，
#: 因为 P16 要按 kind 生成不同的 MJCF 关节，kind 猜错就等于物理模型错。
#:
#: 分两档：
#:   可推断（`joints.py` 能从几何/网表推）—— stack / port / contact
#:   必须声明（需要轴径、齿数、轨迹参数）—— pin / axle / gear / chain /
#:                                          tread / rack / hinge / slide
JOINT_KINDS = (
    "pin", "axle", "gear", "chain", "tread", "rack",
    "hinge", "slide", "stud", "stack", "port", "contact",
)

#: 能从现有数据推断的 kind。**推断结果 `source="inferred"`，不参与硬处理。**
JOINT_INFERABLE = frozenset({"stack", "port", "contact"})

#: 关节的来源。**顺序无关，语义有关**：
#:
#: * `declared` —— 人写的，进硬判定
#: * `inferred` —— 拓扑/几何推的，**不进物理模型**（`mjcf.build` 跳过）
#: * `imported` —— 导入器带的，视同 declared
#: * `proposed` —— P42：**有几何依据、但没人核对的数值**。它能进物理模型
#:   （否则"半成品"验不了），区别在于报告里会被数出来，并且
#:   `dims`/`handoff` 一律当"不可当精确值"。
#:
#: 为什么 `proposed` 和 `inferred` 要分开：`inferred` 是"看起来像"，**没有数值**；
#: `proposed` 是"算出了一个具体数（轴径/行程），只是没人签字"。前者进仿真
#: 等于用猜的结构；后者进仿真至少有 evidence 可复算。
JOINT_SOURCES = ("declared", "inferred", "imported", "proposed")


@dataclass
class Joint:
    """两个零件之间的**机械关系**。

    ## 为什么它和 `nets` 并列，而不是塞进 `Part`

    同一条关系属于两端（A 的轴插进 B 的孔），挂在任一端都会让
    "另一端删了怎么办"变成悬空引用。单列一张表，两端都是 `ref`，
    V0 的引用完整性检查能统一覆盖（和 `nets[].pins` 一样的处理）。

    ## `source` 的语义与 `links` / `claims` 完全一致

    `declared` —— 人写的，参与硬判定（P16 生成 MJCF 关节、P18 进阶梯）
    `inferred` —— 推断的，**只提示不判定**（AGENTS §1.2）
    `imported` —— 导入器带进来的，视同 declared 但要标注来源
    `proposed` —— P42 的提案：有几何依据的数值，能进物理模型但**没人核对**

    ## `dof` 与 `params` 的分工

    `dof`    —— 允许的自由度，如 `["rz"]`（转轴）/ `[]`（固定）。
                P16 直接翻译成 MuJoCo 的 `<joint type="hinge">` 或省略。
    `params` —— kind 专属参数：gear 的 `ratio`、axle 的 `diameter_mm`、
                slide 的 `range_mm`、tread 的 `spacing_mm`。
                **不给默认值**：缺参数就是缺参数，不能编一个齿数比。
    """

    a: str                                  # part.ref
    b: str
    kind: str = "contact"
    params: dict[str, Any] = field(default_factory=dict)
    dof: list[str] = field(default_factory=list)
    source: str = "declared"
    evidence: str = ""

    def __post_init__(self) -> None:
        self.a = str(self.a or "").strip()
        self.b = str(self.b or "").strip()
        if not self.a or not self.b:
            raise DesignError(f"joint 两端都要 ref：a={self.a!r} b={self.b!r}")
        if self.a == self.b:
            raise DesignError(f"joint 不能自己连自己：{self.a}")
        self.kind = str(self.kind or "contact").strip()
        if self.kind not in JOINT_KINDS:
            raise DesignError(
                f"joint {self.a}-{self.b}: 未知 kind {self.kind!r}"
                f"（可用：{', '.join(JOINT_KINDS)}）")
        self.source = str(self.source or "declared").strip()
        if self.source not in JOINT_SOURCES:
            raise DesignError(
                f"joint {self.a}-{self.b}: source 必须是 "
                f"{'/'.join(JOINT_SOURCES)}，收到 {self.source!r}")
        self.dof = [str(x) for x in (self.dof or [])]
        self.params = dict(self.params or {})

    @property
    def key(self) -> str:
        """无向键：`a|b|kind` 的规范形式（排序两端）。

        用途：去重。**方向不参与去重** —— A 的轴插 B 的孔和 B 的孔套 A 的轴
        是同一条关系，存成两条会让 P16 生成两个关节。
        """
        first, second = sorted((self.a, self.b))
        return f"{first}|{second}|{self.kind}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "a": self.a, "b": self.b, "kind": self.kind,
            "source": self.source,
        }
        if self.params:
            out["params"] = dict(self.params)
        if self.dof:
            out["dof"] = list(self.dof)
        if self.evidence:
            out["evidence"] = self.evidence
        return out

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "Joint":
        data = data or {}
        return Joint(
            a=str(data.get("a") or ""),
            b=str(data.get("b") or ""),
            kind=str(data.get("kind") or "contact"),
            params=dict(data.get("params") or {}),
            dof=[str(x) for x in (data.get("dof") or [])],
            source=str(data.get("source") or "declared"),
            evidence=str(data.get("evidence") or ""),
        )


@dataclass
class Program:
    """开环时序动作。对应 RoboBuilder 的 `motor_program: [{t, motor, speed}]`。

    ## 为什么是"开环时序"而不是"控制律"

    开环 = 每个时刻给定执行器一个值，**没有反馈**。RoboBuilder 用它，
    因为闭环控制律属于固件（P20），不属于"设计"。
    我们照这个边界：`Program` 只描述"设计意图的动作序列"，
    真实控制律在固件里，由 `pins.h` 交叉核对保证引脚一致。

    ## `units` 为什么必须显式

    `normalized`（0..1 的油门）与 `rpm` 差一个数量级。不声明就会
    在 P16 里被当成油门喂给 MuJoCo，仿真结果全错且**看起来像跑通了**。
    """

    steps: list[dict[str, Any]] = field(default_factory=list)
    loop: bool = False
    units: str = "normalized"

    def __post_init__(self) -> None:
        self.units = str(self.units or "normalized").strip()
        if self.units not in ("normalized", "rpm", "rad_s"):
            raise DesignError(
                f"program.units 必须是 normalized/rpm/rad_s，收到 {self.units!r}")
        cleaned: list[dict[str, Any]] = []
        for index, step in enumerate(self.steps or []):
            if not isinstance(step, dict):
                raise DesignError(f"program.steps[{index}] 必须是对象")
            entry: dict[str, Any] = {}
            for key in ("t_s", "actuator", "value"):
                if key not in step:
                    raise DesignError(
                        f"program.steps[{index}] 缺少 {key}"
                        f"（需要 t_s / actuator / value 三项）")
                entry[key] = step[key]
            entry["t_s"] = float(entry["t_s"])
            entry["actuator"] = str(entry["actuator"])
            entry["value"] = float(entry["value"])
            # 保留额外键（如注释、条件），但核心三项已规范化
            for key, value in step.items():
                if key not in entry:
                    entry[key] = value
            cleaned.append(entry)
        # 时序必须单调，否则"先转后停"会被执行成"先停后转"
        times = [s["t_s"] for s in cleaned]
        if times != sorted(times):
            raise DesignError("program.steps 必须按 t_s 升序（时序不能倒流）")
        self.steps = cleaned

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"steps": [dict(s) for s in self.steps]}
        if self.loop:
            out["loop"] = True
        if self.units != "normalized":
            out["units"] = self.units
        return out

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "Program":
        data = data or {}
        return Program(
            steps=[dict(s) for s in (data.get("steps") or [])
                   if isinstance(s, dict)],
            loop=bool(data.get("loop")),
            units=str(data.get("units") or "normalized"),
        )


@dataclass
class DesignModel:
    """一份设计。2D 与 3D 都从这里投影。

    ## 单板与多板

    `board` 始终是**主参考板**（兼容旧字段与小工具）；`boards` 是**全部板**。

    不变式（由 `__post_init__` / `board_list()` 保证）：

    * `boards` 为空时 = 单板设计，一切按 `board`；
    * `boards` 非空时，`board` **必须**是其中之一（同一个对象），
      否则元件归属与渲染会分叉。

    元件通过 `Placement.board_id` 归属某块板；空 `board_id` 归主参考板。
    坐标是**所属板的局部坐标**，拼板平移只在投影里做一次。
    """

    id: str = "untitled"
    title: str = ""
    board: Board = field(default_factory=Board)
    #: 全部板（多板设计）。空 = 单板，等价于 `[board]`。
    boards: list[Board] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    nets: list[dict[str, Any]] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)
    #: 功能单元表（P5 落地）。**空 = 没有单元概念**，旧行为不变。
    units_list: list[dict[str, Any]] = field(default_factory=list)
    #: 板间链路表（P5 落地）。**空 = 单板**，旧行为不变。
    links_list: list[dict[str, Any]] = field(default_factory=list)
    #: 块实例表（B3）：`[{instance, block, parts: [ref], nets: [name]}]`。
    #: **它只是元数据**：零件和网表仍然平铺在 `parts`/`nets`（flatten 是恒等），
    #: 这样所有已有判定器不用改。空 = 没按块装配，旧行为不变。
    blocks_list: list[dict[str, Any]] = field(default_factory=list)
    # ---- 四域（P41）------------------------------------------------------
    #
    #: 显式声明的电源/地网（如 `["GND", "+3V3"]`）。
    #:
    #: **空 = 没声明**，此时退回按网名正则推断，并在报告里标 `source: inferred`。
    #: 为什么要显式：正则会把 `PWR_EN` 当电源网加宽、把 `AGND_REF` 当地铺，
    #: 猜错会让铺铜铺错网（AGENTS §1.2：推断不当结论）。
    power_nets: list[str] = field(default_factory=list)
    #: 固件域。形状见 `design/fwgen/`：
    #: `{target, mcu, pinmap: {net: {pin, function, source}}, peripherals: [...]}`
    #:
    #: **None = 没有固件概念**（旧文件读进来就是 None，行为不变）。
    firmware: dict[str, Any] | None = None
    #: 机械域。形状：`{joints: [...], limits: {...}}`。
    #:
    #: **`joints` 仍只存 `joints_list`**：`from_dict` 会把这里的 joints 归并过去
    #: 并从这个 dict 里删掉。两个存储位置就是两个事实源（AGENTS §1.1），
    #: 所以这里只接形状、不做第二份存储。
    mechanical: dict[str, Any] | None = None
    # ---- 多域（P15）----------------------------------------------------
    #
    # 全部可选、全部空列表/None 起手：旧文件读进来这些键不存在，
    # 行为与 v2 完全一致（M1–M3 的 266 项测试必须全绿）。
    #: 机械关系表（`Joint`）。**空 = 没有机械域概念**。
    joints_list: list[dict[str, Any]] = field(default_factory=list)
    #: 整机实体表（电机/桨/机臂/外壳…）。**空 = 只有 PCB**。
    #:
    #: 与 `parts` **分开存**，不是风格问题：`parts` 是 PCB 域的元件，
    #: 会进网表校验、重叠检查、发射器件数与 parity 对账。电机塞进去会
    #: 让这四件事同时变脏（`mount="air"` 的老口子只解决坐标，不解决语义）。
    #: 理由与 `joints_list` 单列同源：**关系/整机不该污染电气判定**。
    #: 形状与校验在 `design/body.py`。
    bodies_list: list[dict[str, Any]] = field(default_factory=list)
    #: 开环时序（`Program`）。None = 没有动作序列。
    program: dict[str, Any] | None = None
    #: 仿真器参数（对应 RoboBuilder 的 `Params`）。
    #: **初始估计与校准后分开存**：`sim_params/labels` 记哪一版。
    sim_params: dict[str, Any] = field(default_factory=dict)
    #: 真实测试记录（P21 闭环）。**空 = 从未实测**，不是"实测通过"。
    physical_tests: list[dict[str, Any]] = field(default_factory=list)
    enclosure: Enclosure | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    schema: str = SCHEMA
    units: str = UNITS

    def _absorb_mechanical_joints(self) -> int:
        """把 `mechanical.joints` 归并进 `joints_list`，并清掉那个键。

        返回并入的条数。**去重键是 (a, b, kind)**：同一对零件可以既有
        hinge 又有 axle（不同 kind），那是两条关系，不该被去重吃掉。
        """
        if not isinstance(self.mechanical, dict):
            return 0
        raw = self.mechanical.pop("joints", None)
        if not isinstance(raw, list):
            if self.mechanical == {}:
                self.mechanical = None
            return 0
        known = {(str(j.get("a")), str(j.get("b")), str(j.get("kind")))
                 for j in self.joints_list if isinstance(j, dict)}
        added = 0
        for row in raw:
            if not isinstance(row, dict):
                continue
            key = (str(row.get("a")), str(row.get("b")), str(row.get("kind")))
            if key in known:
                continue
            self.joints_list.append(dict(row))
            known.add(key)
            added += 1
        if self.mechanical == {}:
            self.mechanical = None
        return added

    def __post_init__(self) -> None:
        # `mechanical.joints` → `joints_list`（只有一个存储位置）。
        self._absorb_mechanical_joints()
        # 多板时保证 `board` 就是 `boards` 里的**同一个对象**。
        #
        # 不能用 `board in boards` 判：dataclass 的 `__eq__` 是**按值**比较，
        # 所以"相等"不代表"同一个"。实测：从磁盘读回后，`board` 与
        # `boards[0]` 值相等但不是同一对象，`board in boards` 返回 True，
        # 于是这里不做替换 —— 而 `panel_layout` 用 `is not main` 过滤，
        # 结果主板被算了两次（8 块板排出 9 块，多出来的那块 0 个元件）。
        #
        # 所以必须**按 id 找到并替换成同一个对象**。
        if self.boards:
            same = next(
                (b for b in self.boards
                 if b is self.board
                 or (b.id != "" and b.id == self.board.id)),
                None)
            self.board = same if same is not None else self.boards[0]

    # -- boards -----------------------------------------------------------

    def board_list(self) -> list[Board]:
        """全部板。单板设计返回 `[board]`（永远至少一块）。"""
        return list(self.boards) if self.boards else [self.board]

    def is_multi_board(self) -> bool:
        return len(self.board_list()) > 1

    def board_of(self, part: Part) -> Board:
        """元件所属的板。

        `board_id` 空 → 主参考板；给了但找不到 → 也退主参考板
        （`validate()` 会报这个悬空归属）。
        """
        wanted = part.placement.board_id
        if wanted == "":
            return self.board
        for board in self.board_list():
            if board.id == wanted:
                return board
        return self.board

    def board_by_id(self, board_id: str) -> Board | None:
        if board_id == "":
            return self.board
        return next((b for b in self.board_list() if b.id == board_id), None)

    def parts_of(self, board: Board) -> list[Part]:
        """某块板上的元件（按 `board_id` 分组，空归属算主板）。"""
        return [p for p in self.parts if self.board_of(p) is board]

    # -- authoring --------------------------------------------------------

    def part(self, ref: str) -> Part | None:
        return next((p for p in self.parts if p.ref == ref), None)

    def entity(self, ref: str):
        """按 `ref` 找**实体**：`Part` 或 `Body`（两表同一命名空间）。

        `joints_list` 的 a/b 可以指向任意一端：电机与桨的关系里，
        电机不是 PCB 元件，却和电阻共用同一张关系表。所以判"引用是否存在"
        必须跨两张表，否则"电机—桨"这条关系永远被判成悬空。
        """
        part = self.part(ref)
        if part is not None:
            return part
        from .body import find as _find_body
        return _find_body(self, ref)

    def require(self, ref: str) -> Part:
        part = self.part(ref)
        if part is None:
            raise DesignError(f"没有元件 {ref!r}")
        return part

    def add_part(self, part: Part) -> Part:
        if self.part(part.ref) is not None:
            raise DesignError(f"元件 ref 重复：{part.ref!r}")
        self.parts.append(part)
        return part

    def connect(self, net: str, *pins: tuple[str, str]) -> None:
        """声明网表：``design.connect('D13', ('U1','D13'), ('D1','A'))``。

        允许 ``("board", "<pin>")`` 指代板卡本身——板卡不是 ``parts`` 里的
        元件，但它的引脚是真实的电气端点，网表必须能引用它。
        """
        if not pins:
            raise DesignError(f"net {net!r} 没有引脚")
        for ref, _pin in pins:
            if ref == "board":
                continue
            self.require(ref)
        for entry in self.nets:
            if entry.get("net") == net:
                have = {(p["ref"], p["pin"]) for p in entry.get("pins", [])}
                for ref, pin in pins:
                    if (ref, pin) not in have:
                        entry.setdefault("pins", []).append({"ref": ref, "pin": pin})
                return
        self.nets.append({"net": net,
                          "pins": [{"ref": r, "pin": p} for r, p in pins]})

    def net_of(self, ref: str, pin: str) -> str | None:
        for entry in self.nets:
            for p in entry.get("pins", []):
                if p.get("ref") == ref and p.get("pin") == pin:
                    return str(entry.get("net"))
        return None

    # -- validation -------------------------------------------------------

    def validate(self) -> list[str]:
        """返回问题列表（不抛异常，给面板一次显示全部）。

        **边界检查必须按板分组**：多板设计的元件在各自板的局部坐标里，
        拿全局 `design.board` 的尺寸去判，子板上的元件会集体"超界"。
        """
        problems: list[str] = []
        seen: set[str] = set()
        boards = self.board_list()
        known_board_ids = {b.id for b in boards if b.id}
        for part in self.parts:
            if part.ref in seen:
                problems.append(f"元件 ref 重复：{part.ref}")
            seen.add(part.ref)
            w, h, d = part.size_mm["w"], part.size_mm["h"], part.size_mm["d"]
            if w <= 0 or h <= 0:
                problems.append(f"{part.ref}: 尺寸缺失（w/h），无法进入 3D 校验")
            if d <= 0:
                problems.append(f"{part.ref}: 高度未知（d=0），3D 面板按薄片显示")
            if part.physical_source == "estimated" and w > 0:
                problems.append(f"{part.ref}: 尺寸为估算值，不可当精确值")
            elif part.physical_source == "proposed" and w > 0:
                # proposed 比 estimated 更低：它只是"看起来像"，连公式都没有。
                # 照样报出来，但措辞必须不同（否则人分不清是哪一种不可信）。
                problems.append(
                    f"{part.ref}: 尺寸是 **AI 提案**（未核对），不可当精确值")
            # 悬空归属：声明了 board_id 但没有这块板
            if (part.placement.board_id
                    and part.placement.board_id not in known_board_ids):
                problems.append(
                    f"{part.ref}: placement.board_id 指向不存在的板 "
                    f"{part.placement.board_id!r}")
            owner = self.board_of(part)
            bw, bh = owner.size_mm["w"], owner.size_mm["h"]
            x0, y0, x1, y1 = part.footprint_mm()
            if part.placement.mount == "board" and (
                    x0 < -0.01 or y0 < -0.01 or x1 > bw + 0.01 or y1 > bh + 0.01):
                where = f"板 {owner.id or owner.kind} " if self.is_multi_board() else "板 "
                problems.append(
                    f"{part.ref}: 足迹超出板轮廓（x {x0:.1f}..{x1:.1f} / "
                    f"y {y0:.1f}..{y1:.1f}，{where}{bw:.1f}×{bh:.1f} mm）")
        for entry in self.nets:
            for p in entry.get("pins", []):
                ref = p.get("ref")
                if ref == "board":
                    continue
                if ref not in seen:
                    problems.append(f"net {entry.get('net')}: 引脚指向不存在的元件 {ref}")
        # 引脚存在性：**能判断的才报错**，不能判断的留给 pins.audit() 报未验证。
        # 这一条是"从 0 搭网表"能不能被信任的分水岭——在这之前
        # `{"ref":"U1","pin":"NOT_A_PIN"}` 会静默通过。
        from . import pins as _pins
        for message in _pins.audit(self)["missing"]:
            problems.append(f"引脚不存在：{message}")
        for wire in self.wires:
            if wire.net not in {e.get("net") for e in self.nets}:
                problems.append(f"导线 {wire.net} 不在网表里")
        # ---- B2：网络类 / 约束 / 重复引脚 ----------------------------
        for entry in self.nets:
            name = str(entry.get("net") or "")
            cls = entry.get("class")
            if cls not in (None, ""):
                if str(cls) not in NET_CLASSES:
                    problems.append(
                        f"net {name}: 未知 class {cls!r}（允许：{', '.join(NET_CLASSES)}）")
            cons = entry.get("constraints")
            if cons is not None and not isinstance(cons, list):
                problems.append(f"net {name}: constraints 必须是数组")
            seen_pins: set[tuple[str, str]] = set()
            for pin in entry.get("pins") or []:
                key = (str(pin.get("ref")), str(pin.get("pin")))
                if key in seen_pins:
                    problems.append(f"net {name}: 引脚重复 {key[0]}.{key[1]}")
                seen_pins.add(key)
        # ---- B1：选型/供应链字段的格式（只查"给了但不对"）-------------
        for part in self.parts:
            cost = part.cost or {}
            unit = cost.get("unit_cny")
            if unit is not None and (not isinstance(unit, (int, float))
                                     or float(unit) < 0):
                problems.append(f"{part.ref}: cost.unit_cny 必须是非负数")
            der = part.derating or {}
            for key in ("voltage_pct", "current_pct"):
                value = der.get(key)
                if value is None:
                    continue
                try:
                    ratio = float(value)
                except (TypeError, ValueError):
                    problems.append(f"{part.ref}: derating.{key} 不是数")
                    continue
                if not (0 < ratio <= 1):
                    problems.append(f"{part.ref}: derating.{key} 必须在 (0,1]")
        # ---- B3：块实例表（引用存在、一个元件只属于一个块）------------
        known_refs = {p.ref for p in self.parts}
        claimed: dict[str, str] = {}
        for row in self.blocks_list:
            instance = str(row.get("instance") or "")
            if not instance:
                problems.append("blocks_list: 缺 instance")
            for ref in row.get("parts") or []:
                text = str(ref)
                if text not in known_refs:
                    problems.append(f"块 {instance}: 引用了不存在的元件 {text}")
                if text in claimed and claimed[text] != instance:
                    problems.append(
                        f"元件 {text} 同时属于块 {claimed[text]} 和 {instance}")
                claimed[text] = instance
        # ---- V1：整机实体 / 外壳腔体的结构问题（引用、枚举、几何可表达性）
        from . import body as _body
        from . import shell as _shell
        problems.extend(_body.problems(self))
        problems.extend(_shell.structure_problems(self))
        problems.extend(self._validate_domains())
        return problems

    def _validate_domains(self) -> list[str]:
        """四域（P41）的检查。**只查"给了但不对"，不要求必须有。**"""
        problems: list[str] = []
        net_names = {str(e.get("net") or "") for e in self.nets if isinstance(e, dict)}
        # 电源网：重复、空串、指向不存在的网
        seen: set[str] = set()
        for name in self.power_nets:
            text = str(name or "").strip()
            if text == "":
                problems.append("power_nets 里有空名字")
                continue
            if text in seen:
                problems.append(f"power_nets 重复：{text}")
            seen.add(text)
            # 网表为空时不做存在性检查（空网表 = 还没录网，不是"这个名字错"）
            if net_names and text not in net_names:
                problems.append(f"power_nets 里的 {text} 不在网表里")
        # 固件域
        fw = self.firmware
        if fw is not None:
            if not isinstance(fw, dict):
                problems.append("firmware 必须是对象")
            else:
                if "pinmap" in fw and not isinstance(fw.get("pinmap"), dict):
                    problems.append("firmware.pinmap 必须是 `{net: 分配}` 对象")
                pinmap = fw.get("pinmap") if isinstance(fw.get("pinmap"), dict) else {}
                for net, alloc in pinmap.items():
                    if net_names and str(net) not in net_names:
                        problems.append(f"firmware.pinmap 里的 {net} 不在网表里")
                    if isinstance(alloc, dict):
                        if not str(alloc.get("pin") or "").strip():
                            problems.append(
                                f"firmware.pinmap[{net}] 缺 pin（没指定物理引脚）")
                    else:
                        problems.append(f"firmware.pinmap[{net}] 必须是对象")
        # 机械域
        mech = self.mechanical
        if mech is not None:
            if not isinstance(mech, dict):
                problems.append("mechanical 必须是对象")
            elif "limits" in mech and not isinstance(mech.get("limits"), dict):
                problems.append("mechanical.limits 必须是对象")
        return problems

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema": self.schema,
            "units": self.units,
            "id": self.id,
            "title": self.title,
            "board": self.board.to_dict(),
            # 多板设计才写 `boards`：单板设计不写，旧文件读进来也不变。
            **({"boards": [b.to_dict() for b in self.boards]} if self.boards else {}),
            "parts": [p.to_dict() for p in self.parts],
            "nets": [dict(n) for n in self.nets],
            "wires": [w.to_dict() for w in self.wires],
            "meta": dict(self.meta),
        }
        if self.enclosure is not None:
            out["enclosure"] = self.enclosure.to_dict()
        if self.units_list:
            out["units_list"] = [dict(u) for u in self.units_list]
        if self.links_list:
            out["links_list"] = [dict(l) for l in self.links_list]
        if self.blocks_list:
            out["blocks_list"] = [dict(b) for b in self.blocks_list]
        # 四域（P41）：只在非空时写，旧文件往返不变。
        if self.power_nets:
            out["power_nets"] = [str(n) for n in self.power_nets]
        if self.firmware is not None:
            out["firmware"] = dict(self.firmware)
        if self.mechanical is not None:
            out["mechanical"] = dict(self.mechanical)
        # ---- 多域（P15）：同样只在非空时写 ----
        if self.joints_list:
            out["joints_list"] = [dict(j) for j in self.joints_list]
        if self.bodies_list:
            out["bodies_list"] = [dict(b) for b in self.bodies_list]
        if self.program is not None:
            out["program"] = dict(self.program)
        if self.sim_params:
            out["sim_params"] = dict(self.sim_params)
        if self.physical_tests:
            out["physical_tests"] = [dict(t) for t in self.physical_tests]
        if self.warnings:
            out["warnings"] = list(self.warnings)
        return out

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "DesignModel":
        schema = str(data.get("schema") or "")
        if schema and schema != SCHEMA and schema not in LEGACY_SCHEMAS:
            raise DesignError(
                f"不认识的 schema：{schema!r}"
                f"（期望 {SCHEMA}；旧包名 {LEGACY_SCHEMAS} 仍可读）")
        if data.get("units") not in (None, UNITS):
            raise DesignError(f"单位必须是 mm，收到 {data.get('units')!r}")
        return DesignModel(
            id=str(data.get("id") or "untitled"),
            title=str(data.get("title") or ""),
            board=Board.from_dict(data.get("board") or {}),
            # 旧文件没有 `boards`：空列表 = 单板，一切按 `board`。
            boards=[Board.from_dict(b) for b in (data.get("boards") or [])],
            parts=[Part.from_dict(p) for p in (data.get("parts") or [])],
            nets=[dict(n) for n in (data.get("nets") or [])],
            wires=[Wire.from_dict(w) for w in (data.get("wires") or [])],
            enclosure=(Enclosure.from_dict(data["enclosure"])
                       if data.get("enclosure") else None),
            # 旧文件没有这两个键：空列表，行为不变。
            units_list=[dict(u) for u in (data.get("units_list") or [])
                        if isinstance(u, dict)],
            links_list=[dict(l) for l in (data.get("links_list") or [])
                        if isinstance(l, dict)],
            blocks_list=[dict(b) for b in (data.get("blocks_list") or [])
                         if isinstance(b, dict)],
            power_nets=[str(n) for n in (data.get("power_nets") or [])],
            firmware=(dict(data["firmware"])
                      if isinstance(data.get("firmware"), dict) else None),
            mechanical=(dict(data["mechanical"])
                        if isinstance(data.get("mechanical"), dict) else None),
            # 旧文件没有这四个键 → 空/None，行为不变。
            joints_list=[dict(j) for j in (data.get("joints_list") or [])
                         if isinstance(j, dict)],
            bodies_list=[dict(b) for b in (data.get("bodies_list") or [])],
            program=(dict(data["program"])
                     if isinstance(data.get("program"), dict) else None),
            sim_params=dict(data.get("sim_params") or {}),
            physical_tests=[dict(t) for t in (data.get("physical_tests") or [])
                            if isinstance(t, dict)],
            meta=dict(data.get("meta") or {}),
            warnings=[str(w) for w in (data.get("warnings") or [])],
        )

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
                          + "\n", encoding="utf-8")
        return target

    @staticmethod
    def load(path: str | Path) -> "DesignModel":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return DesignModel.from_dict(data)

    # -- convenience ------------------------------------------------------

    def clone(self) -> "DesignModel":
        return DesignModel.from_dict(self.to_dict())

    def with_warnings(self, warnings: Iterable[str]) -> "DesignModel":
        merged = list(self.warnings)
        for w in warnings:
            if w not in merged:
                merged.append(w)
        return replace(self, warnings=merged)

    def summary(self) -> dict[str, Any]:
        by_source: dict[str, int] = {}
        for part in self.parts:
            by_source[part.physical_source] = by_source.get(part.physical_source, 0) + 1
        return {
            "id": self.id,
            "title": self.title,
            "board": {"kind": self.board.kind, "name": self.board.name,
                      "size_mm": dict(self.board.size_mm),
                      "thickness_mm": self.board.thickness_mm},
            "parts": len(self.parts),
            "parts_by_source": by_source,
            "nets": len(self.nets),
            "wires": len(self.wires),
            "enclosure": self.enclosure is not None,
            "estimated_parts": [p.ref for p in self.parts
                                if p.physical_source == "estimated"],
        }
