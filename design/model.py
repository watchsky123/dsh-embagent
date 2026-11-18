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

SCHEMA = "hw_velxio.design/1"
UNITS = "mm"

PHYSICAL_SOURCES = ("datasheet", "measured", "estimated", "derived")
MOUNTS = ("board", "panel", "air")
ROTATIONS = (0, 90, 180, 270)

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
        size = self.size_mm or {}
        self.size_mm = {
            "w": _num(size.get("w", 0), f"part {self.ref}.size_mm.w"),
            "h": _num(size.get("h", 0), f"part {self.ref}.size_mm.h"),
            "d": _num(size.get("d", 0), f"part {self.ref}.size_mm.d"),
        }
        for axis, value in self.size_mm.items():
            if value < 0:
                raise DesignError(f"part {self.ref}.size_mm.{axis} 不能为负")
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
            electrical=dict(data.get("electrical") or {}),
            notes=[str(x) for x in (data.get("notes") or [])],
            pins=[str(x) for x in (data.get("pins") or [])],
            pads=[Pad.from_dict(p) for p in (data.get("pads") or [])],
            # 旧文件没有这两个键：justification=None / unit=""，行为不变。
            justification=(Justification.from_dict(data["justification"])
                           if data.get("justification") else None),
            unit=str(data.get("unit") or ""),
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
    """外壳：内腔尺寸 + 壁厚 + 开口。开口只记语义，不做曲面建模。"""

    inner_mm: dict[str, float] = field(
        default_factory=lambda: {"w": 0.0, "h": 0.0, "d": 0.0})
    wall_mm: float = 2.0
    openings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inner_mm": {k: round(v, 4) for k, v in self.inner_mm.items()},
            "wall_mm": self.wall_mm,
            "openings": [dict(o) for o in self.openings],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Enclosure":
        return Enclosure(
            inner_mm=dict(data.get("inner_mm") or {}),
            wall_mm=data.get("wall_mm", 2.0),
            openings=[dict(o) for o in (data.get("openings") or [])],
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
    enclosure: Enclosure | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    schema: str = SCHEMA
    units: str = UNITS

    def __post_init__(self) -> None:
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
        if self.warnings:
            out["warnings"] = list(self.warnings)
        return out

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "DesignModel":
        schema = str(data.get("schema") or "")
        if schema and schema != SCHEMA:
            raise DesignError(f"不认识的 schema：{schema!r}（期望 {SCHEMA}）")
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
