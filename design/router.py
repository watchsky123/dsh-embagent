"""自研布线器 —— `route=ours`。**纯 Python，不碰立创**。

【为什么需要它】

立创自己的 `autoRouting` 现在能用（AGENTS §3.20 修了板框之后 2/2 布通），
所以这条路**不是主力**。它存在的理由是三种情况：

1. 引擎布不通（多网络交叉、密度高、板子形状怪）—— 我们至少能试一次自己的
2. 引擎不可用（未来换版本、或换别家 EDA）
3. 需要**可复现**的布线：同一份 `design.json` + 同样的规则，必须出同样的线，
   而引擎的优化目标是个黑箱

【做法：栅格 + A*，而不是手写航线】

手册 §20/§21 的路线是「人写正交拐点 + 倒角 + 几何检查」。那条路在
**少量网络、明确拓扑**时最快，但它把布线知识留在了人脑里。这里换成：

   板框 → 栅格化（默认 10 mil 一格） → 每格标记占用/空闲
   → 每条网络按 MST 拆成若干对 → 逐对 A*（8 邻域，含 45° 斜走）
   → 需要换层时放过孔（仅在双面都空闲时允许）
   → 路径压成端点序列 → 直接就是 45°/正交走线，不需要再"倒角"

8 邻域 A* 天然只出 0°/45°/90° 三种角度——**倒角是免费的**，而且不会出现
手册 §20 警告的那个坑（倒角吃掉拐点导致 T 型分支断线）。

【它自己不下"连通"结论】

`plan()` 只产出线段与过孔，**判定交回 `pcb_geo.connectivity()`**（拿读回来的
真实几何建并查集）。理由和 AGENTS §1.1 一样：判定只有一份事实源。
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from .pcb_geo import BoardGeometry, Pad, _dist_point_segment

__all__ = ["Plan", "Rules", "Segment", "ViaDrop", "plan", "stitch_sites"]

#: 电源类网络名判据（走线加宽）。**保守**：只认这些前缀/整名，不认"看起来像"。
_POWER_HINTS = ("GND", "AGND", "PGND", "VCC", "VDD", "VBUS", "VBAT", "PWR",
                "+3V3", "3V3", "3.3V", "+5V", "5V", "5.0V", "+12V", "12V", "VIN",
                "VOUT", "VREF")


def _is_power(net: str) -> bool:
    up = (net or "").upper()
    return any(up == h or up.startswith(h) or up.endswith(h) for h in _POWER_HINTS)


@dataclass
class Rules:
    """布线规则（mil）。默认取手册 §17.18/§17.17 的实测值。"""

    grid_mil: float = 10.0
    track_mil: float = 8.0
    power_mil: float = 20.0
    clearance_mil: float = 6.0        # 焊盘到走线 ≥6 mil（手册 §17.18）
    via_diameter_mil: float = 20.0    # 规则下限 19.7 mil（手册 §17.17）
    via_hole_mil: float = 12.0        # 规则下限 11.8 mil
    layers: tuple[int, ...] = (1, 2)
    allow_via: bool = True
    max_cells: int = 400_000

    def width_for(self, net: str) -> float:
        return self.power_mil if _is_power(net) else self.track_mil

    def to_dict(self) -> dict[str, Any]:
        return {"grid_mil": self.grid_mil, "track_mil": self.track_mil,
                "power_mil": self.power_mil, "clearance_mil": self.clearance_mil,
                "via_diameter_mil": self.via_diameter_mil,
                "via_hole_mil": self.via_hole_mil, "layers": list(self.layers),
                "allow_via": self.allow_via}


@dataclass
class Segment:
    net: str
    layer: int
    x1: float
    y1: float
    x2: float
    y2: float
    width: float

    def to_dict(self) -> dict[str, Any]:
        return {"net": self.net, "layer": self.layer,
                "x1": round(self.x1, 2), "y1": round(self.y1, 2),
                "x2": round(self.x2, 2), "y2": round(self.y2, 2),
                "width": self.width}


@dataclass
class ViaDrop:
    net: str
    x: float
    y: float
    hole: float
    diameter: float

    def to_dict(self) -> dict[str, Any]:
        return {"net": self.net, "x": round(self.x, 2), "y": round(self.y, 2),
                "hole": self.hole, "diameter": self.diameter}


@dataclass
class Plan:
    board: str = ""
    segments: list[Segment] = field(default_factory=list)
    vias: list[ViaDrop] = field(default_factory=list)
    routed: list[str] = field(default_factory=list)
    unrouted: list[str] = field(default_factory=list)
    narrow: list[str] = field(default_factory=list)   # 焊盘间距不够、放弃了
    grid: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "board": self.board,
            "grid": self.grid,
            "segments": [s.to_dict() for s in self.segments],
            "vias": [v.to_dict() for v in self.vias],
            "counts": {"segments": len(self.segments), "vias": len(self.vias),
                       "routed": len(self.routed), "unrouted": len(self.unrouted)},
            "routed": self.routed,
            "unrouted": self.unrouted,
            "narrow": self.narrow,
            "notes": self.notes,
        }


class _Grid:
    """一层栅格。占用用 `set[(ix, iy)]`，够快也够省。"""

    def __init__(self, x0: float, y0: float, nx: int, ny: int, cell: float) -> None:
        self.x0 = x0
        self.y0 = y0
        self.nx = nx
        self.ny = ny
        self.cell = cell

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        return (int(round((x - self.x0) / self.cell)),
                int(round((y - self.y0) / self.cell)))

    def center(self, ix: int, iy: int) -> tuple[float, float]:
        return (self.x0 + ix * self.cell, self.y0 + iy * self.cell)

    def inside(self, ix: int, iy: int) -> bool:
        return 0 <= ix < self.nx and 0 <= iy < self.ny


def _outline_points(geo: BoardGeometry) -> list[tuple[float, float]] | None:
    """板框 → 多边形点表（只支持"一个闭合多边形"）。

    `R` 模式按 AGENTS §3.20 的约定展开：`(x, y)` 是左上角、向 −Y 延伸。
    """
    for src in geo.outlines:
        if not src:
            continue
        if str(src[0]).upper() == "R" and len(src) >= 5:
            try:
                x, y, w, h = (float(src[1]), float(src[2]), float(src[3]), float(src[4]))
            except (TypeError, ValueError):
                continue
            return [(x, y), (x + w, y), (x + w, y - h), (x, y - h)]
        nums = [float(v) for v in src if isinstance(v, (int, float))]
        if len(nums) >= 6:
            return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
    return None


def _inside_polygon(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    """射线法。边界上的点算在里（栅格中心落在边上极少见，保守即可）。"""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1) if y2 != y1 else 0.0
            if x < x1 + t * (x2 - x1):
                inside = not inside
    return inside


def _mst_edges(points: list[tuple[float, float]]) -> list[tuple[int, int]]:
    """最小生成树（Prim，按曼哈顿距离）。终端少（≤几十），O(n²) 足够。"""
    n = len(points)
    if n < 2:
        return []
    in_tree = {0}
    edges: list[tuple[int, int]] = []
    while len(in_tree) < n:
        best: tuple[float, int, int] | None = None
        for i in in_tree:
            for j in range(n):
                if j in in_tree:
                    continue
                d = (abs(points[i][0] - points[j][0])
                     + abs(points[i][1] - points[j][1]))
                if best is None or d < best[0]:
                    best = (d, i, j)
        assert best is not None
        edges.append((best[1], best[2]))
        in_tree.add(best[2])
    return edges


_DIRS = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
         (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142))


def _astar(grid: _Grid, blocked: dict[int, set[tuple[int, int]]],
           start: tuple[int, int, int], goal: tuple[int, int, int],
           *, via_cost: float, allow_via: bool) -> list[tuple[int, int, int]] | None:
    """8 邻域 A*，状态带层号。返回 `[(ix, iy, layer), …]` 或 None。"""
    sx, sy, sl = start
    gx, gy, gl = goal
    if not grid.inside(sx, sy) or not grid.inside(gx, gy):
        return None

    def heur(ix: int, iy: int, layer: int) -> float:
        dx, dy = abs(ix - gx), abs(iy - gy)
        h = (dx + dy) + (1.4142 - 2.0) * min(dx, dy)
        return h + (0.0 if layer == gl else via_cost)

    openq: list[tuple[float, float, tuple[int, int, int]]] = []
    heapq.heappush(openq, (heur(sx, sy, sl), 0.0, start))
    best: dict[tuple[int, int, int], float] = {start: 0.0}
    parent: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    while openq:
        _, cost, cur = heapq.heappop(openq)
        if cost > best.get(cur, 1e18) + 1e-9:
            continue
        cx, cy, layer = cur
        if (cx, cy, layer) == (gx, gy, gl):
            path = [cur]
            while path[-1] in parent:
                path.append(parent[path[-1]])
            path.reverse()
            return path
        for dx, dy, w in _DIRS:
            nx, ny = cx + dx, cy + dy
            if not grid.inside(nx, ny):
                continue
            if (nx, ny) in blocked.get(layer, ()):  # type: ignore[arg-type]
                continue
            # 斜走禁止"擦角"：两段正交邻格都必须空
            if dx and dy:
                if ((cx + dx, cy) in blocked.get(layer, ())  # type: ignore[arg-type]
                        or (cx, cy + dy) in blocked.get(layer, ())):  # type: ignore[arg-type]
                    continue
            step = w * grid.cell
            nxt = (nx, ny, layer)
            nc = cost + step
            if nc < best.get(nxt, 1e18) - 1e-9:
                best[nxt] = nc
                parent[nxt] = cur
                heapq.heappush(openq, (nc + heur(nx, ny, layer), nc, nxt))
        if allow_via and len(blocked) > 1:
            for other in blocked:
                if other == layer:
                    continue
                if (cx, cy) in blocked.get(other, ()):  # type: ignore[arg-type]
                    continue
                if (cx, cy) in blocked.get(layer, ()):  # type: ignore[arg-type]
                    continue
                nxt = (cx, cy, other)
                nc = cost + via_cost
                if nc < best.get(nxt, 1e18) - 1e-9:
                    best[nxt] = nc
                    parent[nxt] = cur
                    heapq.heappush(openq, (nc + heur(cx, cy, other), nc, nxt))
    return None


def _simplify(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """去掉共线的中间点（保留端点）。"""
    if len(points) <= 2:
        return points
    out = [points[0]]
    for i in range(1, len(points) - 1):
        ax, ay = out[-1]
        bx, by = points[i]
        cx, cy = points[i + 1]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if abs(cross) > 1e-6:
            out.append((bx, by))
    out.append(points[-1])
    return out


def plan(geo: BoardGeometry, *, rules: Rules | None = None,
         nets: Iterable[str] | None = None) -> Plan:
    """给一块板规划布线。**不写盘、不碰 EDA**。

    输入是 `pcb_geo.read()` 读回来的真实几何；输出是线段 + 过孔 + 未布上的网络。
    """
    rules = rules or Rules()
    result = Plan(board=geo.board)
    poly = _outline_points(geo)
    if poly is None:
        result.notes.append("读不到板框 —— 没有边界就没有栅格（先修板框）")
        result.unrouted = list(nets) if nets else geo.nets_with_pads()
        return result
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    bx0, bx1 = min(xs), max(xs)
    by0, by1 = min(ys), max(ys)
    cell = float(rules.grid_mil)
    nx = int(math.floor((bx1 - bx0) / cell)) + 1
    ny = int(math.floor((by1 - by0) / cell)) + 1
    grid = _Grid(bx0, by0, nx, ny, cell)
    if nx * ny * len(rules.layers) > rules.max_cells:
        result.notes.append(
            f"栅格超限：{nx}×{ny}×{len(rules.layers)} = "
            f"{nx * ny * len(rules.layers)} > {rules.max_cells}"
            f"（板子 {bx1 - bx0:.0f}×{by1 - by0:.0f} mil，格距 {cell:.0f} mil）")
        result.unrouted = list(nets) if nets else geo.nets_with_pads()
        return result
    result.grid = {"x0": round(bx0, 1), "y0": round(by0, 1),
                   "nx": nx, "ny": ny, "cell": cell}

    # **两层账分开记**：
    #   `base`        —— 谁都不能走（板外、悬空焊盘）
    #   `net_fill`    —— 某条网络自己的铜（自己的焊盘/走线/过孔）
    # 布某条网时，障碍 = base ∪ 其它所有网络的 net_fill。
    # 这样"自己的焊盘"永远可进，别人的焊盘永远绕开 —— 不需要给每条网各拷一份。
    base: dict[int, set[tuple[int, int]]] = {layer: set() for layer in rules.layers}
    for layer in rules.layers:
        for ix in range(nx):
            for iy in range(ny):
                cx, cy = grid.center(ix, iy)
                if not _inside_polygon(cx, cy, poly):
                    base[layer].add((ix, iy))

    net_fill: dict[str, set[tuple[int, int]]] = {}

    def _mark(cells: set[tuple[int, int]], ix: int, iy: int,
              half_mil: float) -> None:
        r = max(1, int(math.ceil(half_mil / cell)))
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r + 1:
                    cells.add((ix + dx, iy + dy))

    # 2) 现有走线 / 过孔：有网络的记到自己的账上，没网络的谁都别碰
    for track in geo.tracks:
        half = track.width / 2 + rules.clearance_mil
        ax, ay = grid.cell_of(track.x1, track.y1)
        bx, by = grid.cell_of(track.x2, track.y2)
        steps = max(abs(bx - ax), abs(by - ay), 1)
        for s_i in range(steps + 1):
            ix = int(round(ax + (bx - ax) * s_i / steps))
            iy = int(round(ay + (by - ay) * s_i / steps))
            if track.net:
                _mark(net_fill.setdefault(track.net, set()), ix, iy, half)
            else:
                _mark(base[track.layer], ix, iy, half)
    for via in geo.vias:
        ix, iy = grid.cell_of(via.x, via.y)
        half = via.diameter / 2 + rules.clearance_mil
        if via.net:
            _mark(net_fill.setdefault(via.net, set()), ix, iy, half)
        else:
            for layer in rules.layers:
                _mark(base[layer], ix, iy, half)

    # 3) 焊盘：有网络的记到自己的账上；**没有网络的焊盘也是铜**，进 base
    pad_cells: dict[str, list[tuple[int, int]]] = {}
    for pad in geo.pads:
        ix, iy = grid.cell_of(pad.x, pad.y)
        half = pad.half + rules.clearance_mil
        if pad.net:
            pad_cells.setdefault(pad.net, []).append((ix, iy))
            _mark(net_fill.setdefault(pad.net, set()), ix, iy, half)
        else:
            for layer in (rules.layers if pad.hole else (pad.layer,)):
                if layer in base:
                    _mark(base[layer], ix, iy, half)

    targets = sorted(nets) if nets is not None else geo.nets_with_pads()
    # 先布"焊盘少、跨度小"的网：它们选择最少，留到后面容易布不出来
    def _cost_key(name: str) -> tuple[int, float]:
        pc = pad_cells.get(name) or []
        if len(pc) < 2:
            return (999, 0.0)
        xs2 = [grid.center(ix, iy)[0] for ix, iy in pc]
        ys2 = [grid.center(ix, iy)[1] for ix, iy in pc]
        return (len(pc), (max(xs2) - min(xs2)) + (max(ys2) - min(ys2)))

    targets.sort(key=_cost_key)

    usable_layers = list(rules.layers)
    routed_pts: dict[str, list[tuple[float, float, int]]] = {}

    for net in targets:
        cells = pad_cells.get(net) or []
        if len(cells) < 2:
            result.narrow.append(net)
            continue
        # 本网自己的铜对自己是可走的；所有**别的**网络的铜都是障碍
        other_blocked = {layer: set(base[layer]) for layer in usable_layers}
        for name, fill in net_fill.items():
            if name == net:
                continue
            for layer in usable_layers:
                other_blocked[layer] |= fill

        pads = [p for p in geo.pads if p.net == net]
        pts = [(p.x, p.y) for p in pads] or [grid.center(ix, iy) for ix, iy in cells]
        edges = _mst_edges(pts)
        segs: list[Segment] = []
        vias: list[ViaDrop] = []
        paths_done: list[tuple[list[tuple[int, int, int]], float]] = []
        ok = True
        for a_i, b_i in edges:
            (ax, ay), (bx, by) = pts[a_i], pts[b_i]
            start = (grid.cell_of(ax, ay)[0], grid.cell_of(ax, ay)[1], usable_layers[0])
            goal = (grid.cell_of(bx, by)[0], grid.cell_of(bx, by)[1], usable_layers[0])
            if start[:2] == goal[:2]:
                continue
            path = None
            for layer in usable_layers:
                cand = _astar(grid, other_blocked,
                              (start[0], start[1], layer),
                              (goal[0], goal[1], layer),
                              via_cost=30.0 * cell, allow_via=rules.allow_via)
                if cand is not None:
                    path = cand
                    break
            if path is None:
                ok = False
                break
            width = rules.width_for(net)
            # 路径 → 点列（端点用真实焊盘坐标，中间用格心）
            pts_path: list[tuple[float, float, int]] = []
            for ix, iy, layer in path:
                cx, cy = grid.center(ix, iy)
                pts_path.append((cx, cy, layer))
            pts_path[0] = (ax, ay, path[0][2])
            pts_path[-1] = (bx, by, path[-1][2])
            # 换层处放一个过孔
            for i in range(1, len(pts_path)):
                if pts_path[i][2] != pts_path[i - 1][2]:
                    vx, vy = pts_path[i][0], pts_path[i][1]
                    vias.append(ViaDrop(net=net, x=vx, y=vy,
                                        hole=rules.via_hole_mil,
                                        diameter=rules.via_diameter_mil))
            # 同层的连续点压成线段（跳过换层点）
            run: list[tuple[float, float]] = []
            run_layer = pts_path[0][2]
            for cx, cy, layer in pts_path:
                if layer != run_layer:
                    for seg in _segments_of(run, run_layer, net, width):
                        segs.append(seg)
                    run = [(cx, cy)]
                    run_layer = layer
                else:
                    run.append((cx, cy))
            for seg in _segments_of(run, run_layer, net, width):
                segs.append(seg)
            paths_done.append((path, width))
        if not ok:
            result.unrouted.append(net)
            continue
        # **只有成功的网络才把自己的铜记进障碍图**：失败的半成品不挡别人
        for path, width in paths_done:
            r2 = max(1, int(math.ceil((width / 2 + rules.clearance_mil) / cell)))
            for ix, iy, layer in path:
                for dx in range(-r2, r2 + 1):
                    for dy in range(-r2, r2 + 1):
                        net_fill.setdefault(net, set()).add((ix + dx, iy + dy))
        result.segments.extend(segs)
        result.vias.extend(vias)
        result.routed.append(net)

    if result.unrouted:
        result.notes.append(
            f"{len(result.unrouted)} 条网络在栅格里找不到路径"
            "（A* 穷举过，不是「没试」）")
    return result


def _segments_of(run: list[tuple[float, float]], layer: int, net: str,
                 width: float) -> list[Segment]:
    pts = _simplify(run)
    return [Segment(net=net, layer=layer, x1=pts[i][0], y1=pts[i][1],
                    x2=pts[i + 1][0], y2=pts[i + 1][1], width=width)
            for i in range(len(pts) - 1)]


def stitch_sites(geo: BoardGeometry, net: str, *, rules: Rules | None = None,
                 count: int = 6, inset_mil: float = 60.0) -> list[tuple[float, float]]:
    """给铺铜挑**缝合过孔**的位置（mil）。

    【为什么需要它】

    单面铺铜连不上顶层的 SMD 焊盘（手册 §17.15）；可**只加底层**时，那片铜
    跟任何焊盘都没有电气连接，立创会把孤岛丢掉（`preserveSilos:false`）——
    实测：底层 `getCopperRegion().pourFills` 是 **0**，顶层是 7。
    手册 §17.16 的对策就是「用短线 + 过孔把它们接到另一面」，也就是这里做的。

    挑位置的三条约束（**宁可少放，不能压别人的线**）：

    1. 在板框内、离板边 ≥ `inset_mil`
    2. 离**别的网络**的走线/过孔/焊盘 ≥ 过孔半径 + 线宽/2 + 间距
    3. 尽量分散（按栅格抽样，不扎堆）

    返回空列表是**合法结果**：板子太挤就一个都放不下，调用方必须照实说。
    """
    rules = rules or Rules()
    poly = _outline_points(geo)
    if poly is None:
        return []
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, x1 = min(xs) + inset_mil, max(xs) - inset_mil
    y0, y1 = min(ys) + inset_mil, max(ys) - inset_mil
    if x1 <= x0 or y1 <= y0:
        return []
    keep = rules.via_diameter_mil / 2 + rules.clearance_mil
    others = [t for t in geo.tracks if t.net and t.net != net]
    other_vias = [v for v in geo.vias if v.net != net]
    other_pads = [p for p in geo.pads if p.net != net]

    # 在板内均匀撒候选点（3×2 … 4×3），逐个验间距
    cols, rows = 4, 3
    out: list[tuple[float, float]] = []
    for iy in range(rows):
        for ix in range(cols):
            x = x0 + (x1 - x0) * (ix + 0.5) / cols
            y = y0 + (y1 - y0) * (iy + 0.5) / rows
            if not _inside_polygon(x, y, poly):
                continue
            clash = False
            for t in others:
                if _dist_point_segment(x, y, t.x1, t.y1, t.x2, t.y2) <= (
                        keep + t.width / 2):
                    clash = True
                    break
            if clash:
                continue
            for v in other_vias:
                if math.hypot(x - v.x, y - v.y) <= keep + v.diameter / 2:
                    clash = True
                    break
            if clash:
                continue
            for pad in other_pads:
                if math.hypot(x - pad.x, y - pad.y) <= keep + pad.half:
                    clash = True
                    break
            if clash:
                continue
            out.append((round(x, 2), round(y, 2)))
            if len(out) >= count:
                return out
    return out
