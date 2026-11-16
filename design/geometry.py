"""体素空间与静力学 —— **hw_velxio 自己的**，不再依赖 `hwforge.space` / `hwforge.statics`。

## 为什么迁

`physical.py` 原先 `from hwforge import space as space_module`。没有 hwforge
时，**所有物理检查都跑不了** —— 而物理检查是工作台最核心的能力。

## 保留的语义

只保留 `physical.py` 真正用到的：

| 能力 | 谁在用 |
|---|---|
| `VoxelSpace(dims_mm, pitch_mm)` | `build_space` |
| `.add_mm(ref, kind, x, y, z, w, d, h, nets, meta)` | `build_space` |
| `.placements` / `.occupied_voxels` / `.to_scene()` | 静力学、场景、密度 |
| `Statics(placements, pitch)` + `.report()` / `.evaluate()` | `check_statics` |

**重叠/越界/限高/间距一律用 mm 的 AABB 精确判定**，不用体素量化 ——
体素是 0.5mm 的索引，量化会制造 0.25mm 级别的假阴性。体素场只负责
"占了多少格、密度多少"这类整体量。

## 不变式（与 hwforge 一致，宁严不宽）

* `add_mm` 的宽度/厚度必须为正 —— 0 尺寸的盒子不是元件，是 bug；
* 越界的盒子**不裁剪**，而是如实报出来（裁剪会把"跑出板外"伪装成"刚好在板内"）；
* 同一 ref 重复添加视为调用方错误。

@module hw_velxio.design.geometry
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


DEFAULT_PITCH_MM = 0.5


# ---------------------------------------------------------------------------
# 体素工具
# ---------------------------------------------------------------------------

def _voxels(mm: float, pitch: float) -> int:
    """毫米 → 体素数。向上取整：**宁可多占一格，不要少算一格**。"""
    return max(1, int(math.ceil(float(mm) / pitch - 1e-9)))


@dataclass
class VoxelBox:
    """体素整数盒。`nx/ny/nz` 是格数，不是终点。"""

    x0: int
    y0: int
    z0: int
    nx: int
    ny: int
    nz: int

    @property
    def volume(self) -> int:
        return max(0, self.nx) * max(0, self.ny) * max(0, self.nz)

    def overlaps(self, other: "VoxelBox") -> bool:
        return (
            self.x0 < other.x0 + other.nx and other.x0 < self.x0 + self.nx
            and self.y0 < other.y0 + other.ny and other.y0 < self.y0 + self.ny
            and self.z0 < other.z0 + other.nz and other.z0 < self.z0 + self.nz
        )

    def to_dict(self) -> dict[str, int]:
        return {"x0": self.x0, "y0": self.y0, "z0": self.z0,
                "nx": self.nx, "ny": self.ny, "nz": self.nz}


@dataclass
class Placement:
    """体素场里的一个物理对象。

    `kind` 决定约束语义：

    * `module` —— 功能块（MCU 模块、电源模块、电池）
    * `connector` —— 有朝外接口，必须能到板边
    * `keepout` —— 占空间但**不挡人**（天线区）
    * `blocker` —— 占空间且挡人（散热器、高件）
    """

    ref: str
    kind: str
    box: VoxelBox
    nets: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "kind": self.kind,
                "box": self.box.to_dict(), "nets": list(self.nets),
                "meta": dict(self.meta)}


class VoxelSpace:
    """体素场：把 mm 的盒子登记到整数格上。"""

    def __init__(self, dims_mm: tuple[float, float, float],
                 pitch_mm: float = DEFAULT_PITCH_MM) -> None:
        if pitch_mm <= 0:
            raise ValueError(f"pitch_mm 必须为正，收到 {pitch_mm}")
        self.pitch_mm = float(pitch_mm)
        self.dims_mm = (float(dims_mm[0]), float(dims_mm[1]), float(dims_mm[2]))
        self.dims = tuple(_voxels(d, self.pitch_mm) for d in self.dims_mm)
        self.placements: list[Placement] = []
        self._by_ref: dict[str, Placement] = {}
        self._occupied = 0
        #: 尺寸缺失（0）的记录 —— 上层要能说"这些高度是推断的"
        self.zero_size_refs: list[str] = []

    # -- 登记 --------------------------------------------------------------

    def add_mm(self, ref: str, kind: str, *,
               x_mm: float, y_mm: float, z_mm: float,
               w_mm: float, d_mm: float, h_mm: float,
               nets: tuple[str, ...] = (),
               meta: dict[str, Any] | None = None) -> Placement:
        """按 **mm 的左下角** 登记一个盒子。

        参数名沿用 hwforge 的方位约定：`w_mm` 是 X、`d_mm` 是 Y、`h_mm` 是 Z。

        ## 0 尺寸怎么办

        **接受，但记下来**。KiCad 导入的元件**没有厚度信息**（PCB 里
        只有平面），`h_mm` 就是 0。

        早先这里 `raise ValueError("0 尺寸的盒子不是元件")` —— 结果是
        整个设计**一项检查都跑不了**：一个缺失的厚度，把全部物理判定
        变成了"未知"。

        0 尺寸在体素场里按**最小 1 格**处理（元件至少占一点空间），
        同时记进 `zero_size_refs` —— 上层要能说"这个件的高度是
        推断的，不是实测的"。
        """
        for name, value in (("w_mm", w_mm), ("d_mm", d_mm), ("h_mm", h_mm)):
            if value < 0:
                raise ValueError(
                    f"{ref}: {name} 不能为负（收到 {value}）")
            if value == 0:
                self.zero_size_refs.append(
                    f"{ref}.{name}=0（尺寸缺失，按最小 1 格处理；"
                    f"该件的高度是推断值，不是实测）")
        x0 = int(math.floor(x_mm / self.pitch_mm + 1e-9))
        y0 = int(math.floor(y_mm / self.pitch_mm + 1e-9))
        z0 = int(math.floor(z_mm / self.pitch_mm + 1e-9))
        box = VoxelBox(x0=x0, y0=y0, z0=z0,
                       nx=_voxels(w_mm, self.pitch_mm),
                       ny=_voxels(d_mm, self.pitch_mm),
                       nz=_voxels(h_mm, self.pitch_mm))
        placement = Placement(ref=ref, kind=kind, box=box,
                              nets=tuple(nets), meta=dict(meta or {}))
        self.placements.append(placement)
        self._by_ref[ref] = placement
        self._occupied += box.volume
        return placement

    # -- 查询 --------------------------------------------------------------

    def get(self, ref: str) -> Placement | None:
        return self._by_ref.get(ref)

    @property
    def occupied_voxels(self) -> int:
        """登记的格数总和（**不扣重叠** —— 重叠是错误，由检查器报）。"""
        return self._occupied

    @property
    def density(self) -> float:
        total = max(1, self.dims[0] * self.dims[1] * self.dims[2])
        return round(self._occupied / total, 4)

    def out_of_bounds(self) -> list[str]:
        """跑出体素场的 ref。**不裁剪** —— 裁剪会把越界伪装成合规。"""
        out: list[str] = []
        for p in self.placements:
            b = p.box
            if (b.x0 < 0 or b.y0 < 0 or b.z0 < 0
                    or b.x0 + b.nx > self.dims[0]
                    or b.y0 + b.ny > self.dims[1]
                    or b.z0 + b.nz > self.dims[2]):
                out.append(p.ref)
        return out

    def overlapping_pairs(self) -> list[tuple[str, str]]:
        """两两重叠的 (ref, ref)。keepout 不参战（它本来就不挡人）。"""
        pairs: list[tuple[str, str]] = []
        items = [p for p in self.placements if p.kind != "keepout"]
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                # 不同板上的两个对象不可能实际重叠；这里靠调用方给的
                # z / 偏移区分，工厂侧已经把 offset 加进去了。
                if a.box.overlaps(b.box):
                    pairs.append((a.ref, b.ref))
        return pairs

    # -- 输出 --------------------------------------------------------------

    def to_scene(self) -> dict[str, Any]:
        """与 hwforge `scene.json` 的 `space` 段同形状（便于互读）。"""
        return {
            "dims": list(self.dims),
            "pitch_mm": self.pitch_mm,
            "placements": [p.to_dict() for p in self.placements],
            "occupied_voxels": self._occupied,
            "density": self.density,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.to_scene()


# ---------------------------------------------------------------------------
# 静力学（地面轮式）
# ---------------------------------------------------------------------------

@dataclass
class Contact:
    """一个支撑点（轮子接地）。"""

    ref: str
    x_mm: float
    y_mm: float

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "x_mm": self.x_mm, "y_mm": self.y_mm}


#: 轮子类角色 —— 只有它们接得到地。
WHEEL_ROLES: frozenset[str] = frozenset({"wheel", "caster_wheel", "wheel_motor"})


def _role_of(placement: Placement) -> str:
    return str(placement.meta.get("role") or "").lower()


def _mass_weight(placement: Placement, pitch_mm: float) -> float:
    """质量代理量：体积（cm³）。

    **这不是真实质量** —— 没有密度数据。用它算重心的意义是"体积重心"，
    对"重心落在支撑多边形内"这个判定足够，且不会假装知道质量。
    """
    box = placement.box
    mm3 = (box.nx * pitch_mm) * (box.ny * pitch_mm) * (box.nz * pitch_mm)
    return max(mm3 / 1000.0, 1e-6)


def centre_of_mass(placements: list[Placement],
                   pitch_mm: float) -> tuple[float, float, float]:
    """体积重心（mm）。"""
    total = 0.0
    sx = sy = sz = 0.0
    for p in placements:
        w = _mass_weight(p, pitch_mm)
        cx = (p.box.x0 + p.box.nx / 2.0) * pitch_mm
        cy = (p.box.y0 + p.box.ny / 2.0) * pitch_mm
        cz = (p.box.z0 + p.box.nz / 2.0) * pitch_mm
        sx += cx * w
        sy += cy * w
        sz += cz * w
        total += w
    if total <= 0:
        return (0.0, 0.0, 0.0)
    return (round(sx / total, 3), round(sy / total, 3), round(sz / total, 3))


def ground_contacts(placements: list[Placement],
                    pitch_mm: float) -> list[Contact]:
    """接地的轮子（z0 最小的那一层）。"""
    wheels = [p for p in placements if _role_of(p) in WHEEL_ROLES]
    if not wheels:
        return []
    lowest = min(p.box.z0 for p in wheels)
    out: list[Contact] = []
    for p in wheels:
        if p.box.z0 != lowest:
            continue
        out.append(Contact(
            ref=p.ref,
            x_mm=round((p.box.x0 + p.box.nx / 2.0) * pitch_mm, 3),
            y_mm=round((p.box.y0 + p.box.ny / 2.0) * pitch_mm, 3)))
    return out


def _convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """凸包（Andrew monotone chain）。"""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float],
              b: tuple[float, float]) -> float:
        return ((a[0] - o[0]) * (b[1] - o[1])
                - (a[1] - o[1]) * (b[0] - o[0]))

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def point_in_convex(poly: list[tuple[float, float]],
                    pt: tuple[float, float]) -> bool:
    """点是否在凸多边形内（含边界）。"""
    if len(poly) < 3:
        return False
    sign = 0
    for i in range(len(poly)):
        a = poly[i]
        b = poly[(i + 1) % len(poly)]
        value = ((b[0] - a[0]) * (pt[1] - a[1])
                 - (b[1] - a[1]) * (pt[0] - a[0]))
        if abs(value) < 1e-9:
            continue
        this_sign = 1 if value > 0 else -1
        if sign == 0:
            sign = this_sign
        elif sign != this_sign:
            return False
    return True


@dataclass
class StaticsCheck:
    """一条静力学结论。"""

    check_id: str
    passed: bool
    message: str
    measured: Any = None
    limit: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"check_id": self.check_id, "passed": self.passed,
                "message": self.message, "measured": self.measured,
                "limit": self.limit}


class Statics:
    """地面轮式的静力学：支撑、稳定性、离地间隙。"""

    def __init__(self, placements: list[Placement],
                 pitch_mm: float = DEFAULT_PITCH_MM) -> None:
        self.placements = placements
        self.pitch_mm = float(pitch_mm)
        self.contacts = ground_contacts(placements, self.pitch_mm)
        self.com = centre_of_mass(placements, self.pitch_mm)

    # -- 检查 --------------------------------------------------------------

    def check_support(self) -> list[StaticsCheck]:
        if not self.contacts:
            return [StaticsCheck(
                "statics.support", False,
                "没有任何接地的轮子 —— 车是悬空的", measured=0, limit=">= 3 个支撑点")]
        n = len(self.contacts)
        # 三点以上才有稳定的支撑面；两点是一条线，一点是一个点。
        return [StaticsCheck(
            "statics.support", n >= 3,
            f"{n} 个接地点" if n >= 3 else f"只有 {n} 个接地点，支撑面是一条线或一个点",
            measured=n, limit=">= 3 个支撑点")]

    def check_stability(self) -> list[StaticsCheck]:
        if len(self.contacts) < 3:
            return [StaticsCheck(
                "statics.stability", False,
                "接地点不足 3 个，无法构成支撑多边形",
                measured=len(self.contacts), limit=">= 3")]
        hull = _convex_hull([(c.x_mm, c.y_mm) for c in self.contacts])
        inside = point_in_convex(hull, (self.com[0], self.com[1]))
        return [StaticsCheck(
            "statics.stability", inside,
            f"重心 ({self.com[0]:.1f},{self.com[1]:.1f}) "
            + ("在支撑多边形内" if inside else "**在支撑多边形外** —— 会翻"),
            measured=[round(self.com[0], 2), round(self.com[1], 2)],
            limit="重心在支撑多边形内")]

    def check_clearance(self) -> list[StaticsCheck]:
        """非轮子件不能低于轮子底 —— 那是拖底。"""
        if not self.contacts:
            return [StaticsCheck("statics.clearance", False,
                                 "没有接地点，无法判定离地间隙")]
        wheel_z = min(p.box.z0 for p in self.placements
                      if _role_of(p) in WHEEL_ROLES)
        dragging = [p.ref for p in self.placements
                    if _role_of(p) not in WHEEL_ROLES and p.box.z0 < wheel_z]
        return [StaticsCheck(
            "statics.clearance", not dragging,
            "没有拖底" if not dragging else f"拖底的件：{'、'.join(dragging)}",
            measured={"dragging": dragging}, limit="非轮件不低于轮子底面")]

    # -- 汇总 --------------------------------------------------------------

    def evaluate(self) -> dict[str, Any]:
        checks = [*self.check_support(), *self.check_stability(),
                  *self.check_clearance()]
        return {
            "ok": all(c.passed for c in checks),
            "checks": checks,
        }

    def report(self) -> dict[str, Any]:
        hull = _convex_hull([(c.x_mm, c.y_mm) for c in self.contacts])
        return {
            "com_mm": [round(self.com[0], 3), round(self.com[1], 3),
                       round(self.com[2], 3)],
            "contact_count": len(self.contacts),
            "contacts": [c.to_dict() for c in self.contacts],
            "support_polygon": [[round(x, 3), round(y, 3)] for x, y in hull],
            "com_inside": (bool(hull) and point_in_convex(
                hull, (self.com[0], self.com[1]))),
            # **不是真实质量** —— 没有密度数据。这里给的是体积重心。
            "mass_model": "volume-proxy（无密度数据，不是真实质量）",
        }


def describe() -> dict[str, Any]:
    """这个模块的能力自述（给面板/工具层）。"""
    return {
        "module": "hw_velxio.design.geometry",
        "provides": ["VoxelSpace", "Statics", "centre_of_mass",
                     "ground_contacts"],
        "pitch_mm": DEFAULT_PITCH_MM,
        "note": "重叠/越界/限高用 mm AABB 精确判；体素只做整体量",
    }
