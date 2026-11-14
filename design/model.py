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

    def __post_init__(self) -> None:
        self.x_mm = _num(self.x_mm, "placement.x_mm")
        self.y_mm = _num(self.y_mm, "placement.y_mm")
        self.z_mm = _num(self.z_mm, "placement.z_mm")
        self.rotation_z = _rotation(self.rotation_z)
        if self.mount not in MOUNTS:
            raise DesignError(f"placement.mount 非法：{self.mount!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "x_mm": round(self.x_mm, 4),
            "y_mm": round(self.y_mm, 4),
            "z_mm": round(self.z_mm, 4),
            "rotation_z": self.rotation_z,
            "mount": self.mount,
        }

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "Placement":
        data = data or {}
        return Placement(
            x_mm=data.get("x_mm", 0), y_mm=data.get("y_mm", 0),
            z_mm=data.get("z_mm", 0), rotation_z=data.get("rotation_z", 0),
            mount=str(data.get("mount", "board")),
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

    def __post_init__(self) -> None:
        if not self.ref.strip():
            raise DesignError("part.ref 不能为空")
        if not self.kind.strip():
            raise DesignError(f"part {self.ref}: kind 不能为空")
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
        """实体 AABB：``(x0, y0, z0, x1, y1, z1)``（板坐标系）。"""
        x0, y0, x1, y1 = self.footprint_mm()
        z0 = self.placement.z_mm
        return (x0, y0, z0, x1, y1, z0 + self.size_mm["d"])

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
        )


@dataclass
class Board:
    """板轮廓 + 厚度 + 安装孔。轮廓是板坐标系里的闭合多边形（mm）。"""

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
        return {
            "kind": self.kind,
            "name": self.name,
            "outline": [[round(x, 4), round(y, 4)] for x, y in self.outline],
            "size_mm": {k: round(v, 4) for k, v in self.size_mm.items()},
            "thickness_mm": self.thickness_mm,
            "mount_holes": [h.to_dict() for h in self.mount_holes],
            "px_per_mm": self.px_per_mm,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Board":
        return Board(
            kind=str(data.get("kind") or "generic"),
            name=str(data.get("name") or "Generic"),
            outline=[(float(p[0]), float(p[1])) for p in (data.get("outline") or [])],
            size_mm=dict(data.get("size_mm") or {}),
            thickness_mm=data.get("thickness_mm", 1.6),
            mount_holes=[MountHole.from_dict(h) for h in (data.get("mount_holes") or [])],
            px_per_mm=data.get("px_per_mm", 3.7795),
        )


@dataclass
class Wire:
    """一根导线：网表是语义真值，`path_mm` 只是走线几何（可缺省）。"""

    net: str
    path_mm: list[Point3] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.net.strip():
            raise DesignError("wire.net 不能为空")
        if self.path_mm and len(self.path_mm) < 2:
            raise DesignError(f"wire {self.net}: path_mm 至少需要 2 个点")

    def to_dict(self) -> dict[str, Any]:
        return {
            "net": self.net,
            "path_mm": [[round(x, 4), round(y, 4), round(z, 4)]
                        for x, y, z in self.path_mm],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Wire":
        return Wire(
            net=str(data["net"]),
            path_mm=[(float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0)
                     for p in (data.get("path_mm") or [])],
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
    """一份设计。2D 与 3D 都从这里投影。"""

    id: str = "untitled"
    title: str = ""
    board: Board = field(default_factory=Board)
    parts: list[Part] = field(default_factory=list)
    nets: list[dict[str, Any]] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)
    enclosure: Enclosure | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    schema: str = SCHEMA
    units: str = UNITS

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
        """返回问题列表（不抛异常，给面板一次显示全部）。"""
        problems: list[str] = []
        seen: set[str] = set()
        bw, bh = self.board.size_mm["w"], self.board.size_mm["h"]
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
            x0, y0, x1, y1 = part.footprint_mm()
            if part.placement.mount == "board" and (
                    x0 < -0.01 or y0 < -0.01 or x1 > bw + 0.01 or y1 > bh + 0.01):
                problems.append(
                    f"{part.ref}: 足迹超出板轮廓（x {x0:.1f}..{x1:.1f} / "
                    f"y {y0:.1f}..{y1:.1f}，板 {bw:.1f}×{bh:.1f} mm）")
        for entry in self.nets:
            for p in entry.get("pins", []):
                ref = p.get("ref")
                if ref == "board":
                    continue
                if ref not in seen:
                    problems.append(f"net {entry.get('net')}: 引脚指向不存在的元件 {ref}")
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
            "parts": [p.to_dict() for p in self.parts],
            "nets": [dict(n) for n in self.nets],
            "wires": [w.to_dict() for w in self.wires],
            "meta": dict(self.meta),
        }
        if self.enclosure is not None:
            out["enclosure"] = self.enclosure.to_dict()
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
            parts=[Part.from_dict(p) for p in (data.get("parts") or [])],
            nets=[dict(n) for n in (data.get("nets") or [])],
            wires=[Wire.from_dict(w) for w in (data.get("wires") or [])],
            enclosure=(Enclosure.from_dict(data["enclosure"])
                       if data.get("enclosure") else None),
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
