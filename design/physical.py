"""物理/机械检查 —— **体素作为求解索引**，不做渲染。

这是 v3 的核心分工：``design/geometry.VoxelSpace`` 只回答"占不占、撞不撞、
够不够高"，3D 视图另外用网格渲染。判定的数值以 mm 报给面板，体素只是
AABB 的离散索引（0.5mm 默认），所以重叠量会偏保守（向上取整）。

每条 finding 都带 ``refs``：2D 与 3D 用同一组 ref 高亮，不需要各自翻译。
"""

from __future__ import annotations

import math
import sys
from typing import Any

from . import phase as _phase
from . import pins as _pins
from .model import DesignModel, Part

DEFAULT_PITCH_MM = 0.5
MARGIN_MM = 5.0
DEFAULT_MAX_HEIGHT_MM = 12.0
#: 网表跨度的**基准**阈值（mm）。
#:
#: 实测事故：这条检查原本用**绝对** 30mm，于是 96×86mm 的真板上
#: `GND` 网络（109mm 跨距）被报成 error —— 30 条假发现把真问题淹没了。
#: 走线跨度的合理上限与**板尺寸**成正比，所以按板对角线缩放。
MAX_NET_REACH_MM = 30.0
BASE_BOARD_DIAGONAL_MM = 89.0        # 70.58×53.34mm（Arduino Uno）的对角线
CONNECTOR_EDGE_GAP_MM = 3.0


def net_reach_limit_mm(design: DesignModel) -> float:
    """本块板允许的最大网表跨度（mm）。

    上限取**板对角线的 1.5 倍**。理由：

    * 小板上 30mm 已���离谱（Arduino Uno 对角线 89mm → 上限 134mm 偏松，
      所以小板另有 `MAX_NET_REACH_MM` 的下限约束）；
    * 大板上 30mm 只是零头（96×86mm 的板对角线 129mm → 上限 193mm）。

    实际上限 = `max(基准 × 缩放, 板对角线 × 1.5)`，取两者较大者 ——
    既不因为板子大而漏报，也不因为板子小而误报。

    多板时取**最大那块板**的对角线：阈值是全设计共用的一个数，
    用小板算会误报板上的长网络。
    """
    diagonals = [
        math.hypot(float(b.size_mm.get("w") or 0.0),
                   float(b.size_mm.get("h") or 0.0))
        for b in design.board_list()
    ]
    diagonal = max(diagonals) if diagonals else 0.0
    scaled = MAX_NET_REACH_MM * diagonal / BASE_BOARD_DIAGONAL_MM
    return round(max(scaled, diagonal * 1.5, MAX_NET_REACH_MM), 1)

def _declared_height_limit(design: DesignModel) -> tuple[float | None, str]:
    """限高来自哪里：外壳内腔 > 项目声明 > 没有。没有就不假装有限高。"""
    if design.enclosure is not None:
        inner = design.enclosure.inner_mm.get("d", 0.0)
        if inner > 0:
            return (float(inner), "外壳内腔")
    declared = design.meta.get("max_height_mm")
    if declared is not None:
        try:
            value = float(declared)
            if value > 0:
                return (value, "项目声明 meta.max_height_mm")
        except (TypeError, ValueError):
            pass
    return (None, "")


def _role_to_kind(part: Part) -> str:
    """元件角色 → 体素场里的约束语义。"""
    role = part.role.lower()
    if role in ("board", "module"):
        return "module"
    if role == "connector":
        return "connector"
    if role == "keepout":
        return "keepout"
    if role in ("blocker", "heatsink"):
        return "blocker"
    return "module"


def build_space(design: DesignModel, *, pitch_mm: float = DEFAULT_PITCH_MM,
                margin_mm: float = MARGIN_MM,
                max_height_mm: float | None = None) -> dict[str, Any]:
    """把设计装进体素场。返回 ``{space, refs, unsized, offset}``。"""
    from .geometry import VoxelSpace

    board = design.board
    top = 0.0
    for part in design.parts:
        top = max(top, part.placement.z_mm + part.size_mm["d"])
    if max_height_mm is None:
        declared, _where = _declared_height_limit(design)
        # 体素场的 Z 只需要"装得下最高的件"；限高判定由 check_height 单独做。
        max_height_mm = max(declared or 0.0, top + 2.0, DEFAULT_MAX_HEIGHT_MM)

    dims_mm = (board.size_mm["w"] + 2 * margin_mm,
               board.size_mm["h"] + 2 * margin_mm,
               max_height_mm)
    # 多板：体素场要装下**整张拼板**，否则子板上的元件落在场外。
    if design.is_multi_board():
        from .projection import panel_layout
        panel = panel_layout(design)
        dims_mm = (panel["size_mm"]["w"] + 2 * margin_mm,
                   panel["size_mm"]["h"] + 2 * margin_mm,
                   max_height_mm)
    space = VoxelSpace(dims_mm=dims_mm, pitch_mm=pitch_mm)

    refs: dict[str, Any] = {}
    unsized: list[str] = []
    # 多板：体素场覆盖**整张拼板**，每块板按拼板偏移平移进去。
    # 不这样做的话，子板上的元件会全部落进主板的坐标范围里，
    # 重叠判定立刻变成一堆假碰撞（两块板上的元件本来就不在同一个 XY 区域）。
    from .projection import panel_layout
    panel = panel_layout(design)
    offset_of = {entry["board"].id: (entry["x"], entry["y"])
                 for entry in panel["boards"]}
    for part in design.parts:
        w, h, d = part.size_mm["w"], part.size_mm["h"], part.size_mm["d"]
        if w <= 0 or h <= 0:
            unsized.append(part.ref)
            continue
        x0, y0, _x1, _y1 = part.footprint_mm()
        # 板局部坐标 → 拼板坐标（多板时才有非零偏移）
        ox, oy = offset_of.get(design.board_of(part).id, (0.0, 0.0))
        # 板角 -> 体素场原点（留 margin），Y 不翻转（模型本身就是 Y 向上）
        placement = space.add_mm(
            part.ref,
            _role_to_kind(part),
            x_mm=x0 + ox + margin_mm,
            y_mm=y0 + oy + margin_mm,
            z_mm=part.placement.z_mm,
            w_mm=w,
            d_mm=h,      # hwforge: d 是 Y 方向
            h_mm=d,      # hwforge: h 是 Z 方向
            nets=tuple(_nets_of(design, part.ref)),
            meta={"label": part.label, "physical_source": part.physical_source,
                  "role": part.role, "board_id": design.board_of(part).id},
        )
        refs[part.ref] = placement
    return {
        "space": space,
        "refs": refs,
        "unsized": unsized,
        "offset_mm": margin_mm,
        "dims_mm": dims_mm,
        "pitch_mm": pitch_mm,
        "board_offsets": offset_of,
    }


def _nets_of(design: DesignModel, ref: str) -> list[str]:
    out: list[str] = []
    for entry in design.nets:
        for pin in entry.get("pins", []):
            if pin.get("ref") == ref:
                out.append(str(entry.get("net")))
                break
    return out


def _finding(check: str, ok: bool, message: str, *, refs: list[str] | None = None,
             measured: Any = None, limit: Any = None, severity: str | None = None,
             fix: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "check": check,
        "ok": ok,
        "severity": severity or ("info" if ok else "error"),
        "refs": refs or [],
        "measured": measured,
        "limit": limit,
        "message": message,
        "fix": fix or {},
    }


# ---------------------------------------------------------------------------
# 检查项
# ---------------------------------------------------------------------------

def _aabb_overlap_mm(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
    dx = min(a[3], b[3]) - max(a[0], b[0])
    dy = min(a[4], b[4]) - max(a[1], b[1])
    dz = min(a[5], b[5]) - max(a[2], b[2])
    return (max(0.0, dx), max(0.0, dy), max(0.0, dz))


def check_overlap(design: DesignModel, built: dict[str, Any]) -> list[dict[str, Any]]:
    """实体重叠。

    判定用 **mm AABB 精确求交**，不用体素量化：体素是 0.5mm 的索引，量化会
    制造 0.25mm 级别的假阴性（两个真实重叠 0.1mm 的件可能落进同一体素之外）。
    体素场仍然负责占用率/高度等整体几何（``build_space`` 的产物）。

    ## 多板：先平移到**拼板坐标**再比

    元件的 `bbox_mm()` 是**所属板的局部坐标**。直接两两相比，不同板上的
    元件会因为"局部坐标刚好落在同一个范围"而假碰撞 —— 实测 8 板工程
    68 条重叠**全部**是跨板的假碰撞（同板真碰撞 0 条）。

    所以先把 bbox 按 `built["board_offsets"]` 平移到拼板坐标，再求交。
    """
    parts = [p for p in design.parts if p.size_mm["w"] > 0 and p.size_mm["h"] > 0]
    offsets = built.get("board_offsets") or {}

    def panel_bbox(part: Part) -> tuple[float, float, float, float, float, float]:
        x0, y0, z0, x1, y1, z1 = part.bbox_mm()
        ox, oy = offsets.get(design.board_of(part).id, (0.0, 0.0))
        return (x0 + ox, y0 + oy, z0, x1 + ox, y1 + oy, z1)

    out: list[dict[str, Any]] = []
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            if a.role.lower() == "keepout" or b.role.lower() == "keepout":
                continue
            board_a = design.board_of(a).id
            board_b = design.board_of(b).id
            # 不同板上的元件**不可能**实体重叠（它们在物理上是两块板）。
            # 这条短路还避免了一个精度陷阱：两块板贴得很近时，
            # 平移后的 bbox 可能因为间隙小于元件尺寸而"重叠"。
            if board_a != board_b:
                continue
            dx, dy, dz = _aabb_overlap_mm(panel_bbox(a), panel_bbox(b))
            if dx <= 0 or dy <= 0 or dz <= 0:
                continue
            volume = dx * dy * dz
            out.append(_finding(
                "space.overlap", False,
                f"{a.ref} 与 {b.ref} 实体重叠 {dx:.2f}×{dy:.2f}×{dz:.2f} mm"
                f"（{volume:.2f} mm³）",
                refs=[a.ref, b.ref],
                measured={"overlap_mm": [round(dx, 3), round(dy, 3), round(dz, 3)],
                          "overlap_mm3": round(volume, 3)},
                limit=0,
                fix={"need": "move_apart", "refs": [a.ref, b.ref]},
            ))
    if not out:
        out.append(_finding("space.overlap", True, "没有实体重叠",
                            measured=0, limit=0))
    return out


def check_keepout(design: DesignModel, built: dict[str, Any]) -> list[dict[str, Any]]:
    """keepout 区必须空（``SpacePolicy.keepout_must_be_empty`` 的语义）。"""
    keepouts = [p for p in design.parts if p.role.lower() == "keepout"
                and p.size_mm["w"] > 0]
    if not keepouts:
        return [_finding("space.keepout", True, "没有 keepout 区",
                         measured=0, limit=0)]
    out: list[dict[str, Any]] = []
    for keepout in keepouts:
        for other in design.parts:
            if other is keepout or other.role.lower() == "keepout":
                continue
            if other.size_mm["w"] <= 0 or other.size_mm["h"] <= 0:
                continue
            dx, dy, dz = _aabb_overlap_mm(keepout.bbox_mm(), other.bbox_mm())
            if dx <= 0 or dy <= 0 or dz <= 0:
                continue
            out.append(_finding(
                "space.keepout", False,
                f"{other.ref} 侵入 keepout {keepout.ref}"
                f"（{dx:.2f}×{dy:.2f}×{dz:.2f} mm）",
                refs=[keepout.ref, other.ref],
                measured={"overlap_mm": [round(dx, 3), round(dy, 3), round(dz, 3)]},
                limit=0,
                fix={"need": "move_out_of_keepout", "ref": other.ref},
            ))
    if not out:
        out.append(_finding("space.keepout", True,
                            f"{len(keepouts)} 个 keepout 区为空",
                            measured=0, limit=0))
    return out


def check_bounds(design: DesignModel, built: dict[str, Any]) -> list[dict[str, Any]]:
    """元件足迹必须落在**它自己那块板**的轮廓内（多边形判定，不是外接矩形）。

    多板设计时按 `board_id` 分组：拿主板的轮廓去判子板上的元件，
    会让子板元件集体"超界" —— 那不是设计错误，是拿错了参考系。

    连接器允许向板边外伸出最多 ``CONNECTOR_EDGE_GAP_MM``：真实连接器就是
    骑在板边上的，把"伸出 1mm"报成错误会让每个正常设计都挂红。
    """
    out: list[dict[str, Any]] = []
    multi = design.is_multi_board()
    for part in design.parts:
        if part.placement.mount != "board":
            continue
        if part.size_mm["w"] <= 0 or part.size_mm["h"] <= 0:
            continue
        board = design.board_of(part)
        outline = board.outline
        x0, y0, x1, y1 = part.footprint_mm()
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        outside = [c for c in corners if not _point_in_polygon(c, outline)]
        if not outside:
            continue
        major = max(-x0, -y0,
                    x1 - board.size_mm["w"], y1 - board.size_mm["h"])
        where = f"（板 {board.id}）" if multi and board.id else ""
        if part.role.lower() == "connector" and major <= CONNECTOR_EDGE_GAP_MM:
            out.append(_finding(
                "space.bounds", True,
                f"{part.ref} 是连接器，向外伸出 {major:.2f} mm（≤ "
                f"{CONNECTOR_EDGE_GAP_MM:.0f} mm，允许）{where}",
                refs=[part.ref], measured=round(major, 2),
                limit=CONNECTOR_EDGE_GAP_MM, severity="info"))
            continue
        out.append(_finding(
            "space.bounds", False,
            f"{part.ref} 的足迹超出板轮廓 {major:.2f} mm（{len(outside)} 个角在外）{where}",
            refs=[part.ref],
            measured={"outside_mm": [[round(x, 2), round(y, 2)] for x, y in outside],
                      "overhang_mm": round(major, 2),
                      "board_id": board.id},
            limit="足迹完全在轮廓内" if part.role.lower() != "connector"
            else f"连接器最多伸出 {CONNECTOR_EDGE_GAP_MM:.0f}mm",
            fix={"need": "move_inside_outline"},
        ))
    if not out:
        out.append(_finding("space.bounds", True, "所有元件足迹都在板轮廓内"))
    return out


def check_height(design: DesignModel, built: dict[str, Any]) -> list[dict[str, Any]]:
    """限高只在**声明了**限高时才判定（外壳内腔 / ``meta.max_height_mm``）。

    没有声明就不编一个 12mm 天花板：那会把一个正常的高元件设计报成失败，
    而真正该说的是"没有限高依据，未核对"。
    """
    limit, where = _declared_height_limit(design)
    tallest = 0.0
    tallest_ref = ""
    for part in design.parts:
        top = part.placement.z_mm + part.size_mm["d"]
        if top > tallest:
            tallest, tallest_ref = top, part.ref
    if limit is None:
        return [_finding(
            "space.height", True,
            f"最高点 {tallest:.2f} mm（{tallest_ref or '无元件'}）；未声明限高"
            "（没有外壳内腔，也没设 meta.max_height_mm），未核对",
            refs=[tallest_ref] if tallest_ref else [],
            measured=round(tallest, 3), limit=None, severity="info")]
    ok = tallest <= limit + 1e-6
    return [_finding(
        "space.height", ok,
        (f"最高点 {tallest:.2f} mm（{tallest_ref or '无元件'}），限高 {limit:.2f} mm"
         f"（{where}）"
         if ok else
         f"{tallest_ref} 顶面 {tallest:.2f} mm 超过限高 {limit:.2f} mm（{where}）"),
        refs=[tallest_ref] if tallest_ref else [],
        measured=round(tallest, 3), limit=round(limit, 3),
        fix={} if ok else {"need": "lower_or_raise_enclosure"},
    )]


def check_mount_holes(design: DesignModel, built: dict[str, Any]) -> list[dict[str, Any]]:
    """安装孔不能被元件盖住，否则螺钉拧不进去。"""
    holes = design.board.mount_holes
    if not holes:
        return [_finding("mech.mount_holes", True, "板卡没有声明安装孔（未核对）",
                         measured=0, limit=0, severity="info")]
    out: list[dict[str, Any]] = []
    for index, hole in enumerate(holes):
        r = hole.diameter_mm / 2.0
        blockers = []
        for part in design.parts:
            # **只有同一块板上的元件能挡住这个孔**：孔在主板上，子板上的
            # 元件坐标是另一套局部坐标，拿来比就是拿错了参考系。
            if design.board_of(part) is not design.board:
                continue
            x0, y0, x1, y1 = part.footprint_mm()
            nearest_x = min(max(hole.x_mm, x0), x1)
            nearest_y = min(max(hole.y_mm, y0), y1)
            dist = math.hypot(hole.x_mm - nearest_x, hole.y_mm - nearest_y)
            if dist < r:
                blockers.append(part.ref)
        if blockers:
            out.append(_finding(
                "mech.mount_holes", False,
                f"安装孔 #{index + 1}（{hole.x_mm:.1f},{hole.y_mm:.1f}）被 "
                f"{', '.join(blockers)} 挡住，螺钉无法进入",
                refs=blockers,
                measured={"hole_mm": [hole.x_mm, hole.y_mm],
                          "diameter_mm": hole.diameter_mm},
                limit="孔半径内无障碍",
                fix={"need": "clear_hole_area", "refs": blockers},
            ))
    if not out:
        out.append(_finding("mech.mount_holes", True,
                            f"{len(holes)} 个安装孔均无遮挡"))
    return out


def check_net_reach(design: DesignModel, built: dict[str, Any]) -> list[dict[str, Any]]:
    """同一网表两端中心距不得超过阈值（按板尺寸缩放，见 `net_reach_limit_mm`）。"""
    out: list[dict[str, Any]] = []
    limit_mm = net_reach_limit_mm(design)
    for entry in design.nets:
        pins = entry.get("pins", [])
        refs = sorted({str(p.get("ref")) for p in pins if p.get("ref")})
        parts = [design.part(r) for r in refs]
        parts = [p for p in parts if p is not None and p.size_mm["w"] > 0]
        if len(parts) < 2:
            continue
        worst = 0.0
        pair = ("", "")
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                a, b = parts[i], parts[j]
                ca, cb = a.center_mm(), b.center_mm()
                dist = math.dist(ca[:2], cb[:2])
                if dist > worst:
                    worst, pair = dist, (a.ref, b.ref)
        if worst > limit_mm:
            out.append(_finding(
                "space.net_reach", False,
                f"net {entry.get('net')}: {pair[0]} 与 {pair[1]} 相距 "
                f"{worst:.1f} mm，超过 {limit_mm:.0f} mm 上限（改线/移件）",
                refs=[pair[0], pair[1]],
                measured=round(worst, 2), limit=limit_mm,
                fix={"need": "shorten_net", "pair": list(pair)},
            ))
    if not out:
        out.append(_finding("space.net_reach", True,
                            f"{len(design.nets)} 条网表都在 {limit_mm:.0f} mm 内"))
    return out


def check_connector_edge(design: DesignModel, built: dict[str, Any]) -> list[dict[str, Any]]:
    """连接器必须靠**它自己那块板**的边，否则插不进去。"""
    out: list[dict[str, Any]] = []
    multi = design.is_multi_board()
    connectors = [p for p in design.parts if p.role.lower() == "connector"]
    for part in connectors:
        board = design.board_of(part)
        w = board.size_mm["w"]
        h = board.size_mm["h"]
        x0, y0, x1, y1 = part.footprint_mm()
        gap = min(abs(x0), abs(y0), abs(w - x1), abs(h - y1))
        if gap > CONNECTOR_EDGE_GAP_MM:
            where = f"（板 {board.id}）" if multi and board.id else ""
            out.append(_finding(
                "space.connector_edge", False,
                f"连接器 {part.ref} 距板边 {gap:.1f} mm，超过 "
                f"{CONNECTOR_EDGE_GAP_MM:.0f} mm：插头会被壳体挡住{where}",
                refs=[part.ref],
                measured={"gap_mm": round(gap, 2), "board_id": board.id},
                limit=CONNECTOR_EDGE_GAP_MM,
                fix={"need": "move_to_board_edge"},
            ))
    if not connectors:
        return [_finding("space.connector_edge", True, "没有连接器类元件",
                         measured=0, limit=CONNECTOR_EDGE_GAP_MM)]
    if not out:
        out.append(_finding("space.connector_edge", True,
                            f"{len(connectors)} 个连接器都靠边"))
    return out


def check_statics(design: DesignModel, built: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """静力学只在"地面轮式"项目里跑；其余如实标未验证。"""
    mounting = str(design.meta.get("mounting") or "").lower()
    rolling = any(p.role.lower() in ("wheel", "caster_wheel") for p in design.parts)
    if mounting not in ("ground",) or not rolling:
        return ([], ["statics：非地面轮式安装，未运行（不是通过）"])
    from .geometry import Statics
    placements = built["space"].placements
    result = Statics(placements, built["pitch_mm"])
    report = result.report()
    verdict = result.evaluate()
    findings = [
        _finding(f"statics.{check.check_id.split('.', 1)[-1]}", check.passed,
                 check.message or f"{check.check_id}: {check.measured}",
                 measured=check.measured, limit=check.limit)
        for check in verdict["checks"]
    ]
    findings.append(_finding(
        "statics.report", bool(report["com_inside"]),
        f"重心 ({report['com_mm'][0]:.1f},{report['com_mm'][1]:.1f}) mm，"
        f"支撑点 {report['contact_count']} 个",
        measured=report, limit="重心在支撑多边形内"))
    return (findings, [])


# ---------------------------------------------------------------------------
# 2D ↔ 3D 一致性
# ---------------------------------------------------------------------------

def check_consistency(design: DesignModel, built: dict[str, Any]) -> list[dict[str, Any]]:
    """同一 ``placement`` 的 2D 投影与 3D 实体必须吻合。

    真正会漂移的地方是**尺寸来源**：旧画布像素反推、catalog 缺项、
    KiCad 没有高度。逐项列出来，而不是报一句"一致"。
    """
    ppm = design.board.px_per_mm
    out: list[dict[str, Any]] = []
    for part in design.parts:
        legacy = part.electrical.get("legacy_px") if isinstance(part.electrical, dict) else None
        if not isinstance(legacy, dict):
            continue
        x0, y0, _x1, _y1 = part.footprint_mm()
        now_px = round(x0 * ppm, 3)
        then_px = legacy.get("x")
        if then_px is None:
            continue
        drift = abs(now_px - float(then_px))
        if drift > 1.5:
            out.append(_finding(
                "consistency.legacy_px", False,
                f"{part.ref}: 2D 投影 x={now_px:.2f}px 与原画布 "
                f"{float(then_px):.2f}px 相差 {drift:.2f}px（已按 {ppm:.4f} px/mm 归一）",
                refs=[part.ref],
                measured=round(drift, 3), limit=1.5,
                severity="warning",
                fix={"need": "accept_normalisation_or_fix_size"},
            ))
    if not out:
        out.append(_finding("consistency.legacy_px", True,
                            "旧画布像素与 2D 投影一致（差 ≤1.5px）"))
    return out


def check_provenance(design: DesignModel, built: dict[str, Any]) -> list[dict[str, Any]]:
    estimated = [p.ref for p in design.parts if p.physical_source == "estimated"]
    unsized = built["unsized"]
    no_height = [p.ref for p in design.parts
                 if p.size_mm["d"] <= 0 and p.size_mm["w"] > 0]
    findings: list[dict[str, Any]] = []
    if estimated:
        findings.append(_finding(
            "consistency.provenance", False,
            f"{len(estimated)} 个元件的物理尺寸是估算值：{', '.join(estimated)}。"
            "3D 与核对面板必须显示'估算'，不可当精确尺寸使用",
            refs=estimated, measured=len(estimated), limit=0,
            severity="warning",
            fix={"need": "import_datasheet_or_measure"},
        ))
    if unsized:
        findings.append(_finding(
            "consistency.provenance", False,
            f"{len(unsized)} 个元件没有任何尺寸，3D 里不占体积，所有体积类"
            f"检查对它们无效：{', '.join(unsized)}",
            refs=unsized, measured=len(unsized), limit=0,
            severity="error",
            fix={"need": "provide_size_mm"},
        ))
    if no_height:
        findings.append(_finding(
            "consistency.provenance", False,
            f"{len(no_height)} 个元件高度未知（d=0），3D 按薄片显示，"
            f"干涉/限高检查对它们无效：{', '.join(no_height)}",
            refs=no_height, measured=len(no_height), limit=0,
            severity="warning",
            fix={"need": "provide_height_mm"},
        ))
    if not findings:
        findings.append(_finding("consistency.provenance", True,
                                 "所有元件都有非估算尺寸"))
    return findings


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

CHECKS = (
    ("space.overlap", "实体重叠"),
    ("space.keepout", "keepout"),
    ("space.bounds", "板轮廓"),
    ("space.height", "高度"),
    ("space.net_reach", "网表跨度"),
    ("space.connector_edge", "连接器靠边"),
    ("mech.mount_holes", "安装孔"),
    ("consistency.legacy_px", "2D↔3D 像素一致"),
    ("consistency.provenance", "尺寸来源"),
)


def run_checks(design: DesignModel, *, pitch_mm: float = DEFAULT_PITCH_MM) -> dict[str, Any]:
    """跑全部物理检查。返回可直接落盘/给面板的 JSON。"""
    built = build_space(design, pitch_mm=pitch_mm)
    findings: list[dict[str, Any]] = []
    not_verified: list[str] = []

    findings += check_overlap(design, built)
    findings += check_keepout(design, built)
    findings += check_bounds(design, built)
    findings += check_height(design, built)
    findings += check_net_reach(design, built)
    findings += check_connector_edge(design, built)
    findings += check_mount_holes(design, built)
    findings += check_consistency(design, built)
    findings += check_provenance(design, built)
    statics_findings, statics_skipped = check_statics(design, built)
    findings += statics_findings
    not_verified += statics_skipped

    if built["unsized"]:
        not_verified.append(
            f"3D 实体：{len(built['unsized'])} 个元件无尺寸，未参与体积/重叠计算"
            f"（{', '.join(built['unsized'])}）")

    # 引脚核对：能判断的已经报错（validate 的 missing），这里报**不能判断的**。
    # 没声明引脚表、也没有约定表的元件，其网表引脚无法核对——必须说成
    # "没核对"而不是"通过"，否则从 0 搭的网表会被误认为已验证。
    pin_audit = _pins.audit(design)
    not_verified.append(_pins.summary_line(pin_audit))

    failed = [f for f in findings if not f["ok"]]
    errors = [f for f in failed if f["severity"] == "error"]
    warnings = [f for f in failed if f["severity"] == "warning"]
    return {
        "ok": not errors,
        "pitch_mm": pitch_mm,
        "space": {
            "dims_mm": [round(x, 3) for x in built["dims_mm"]],
            "dims_voxels": list(built["space"].dims),
            "occupied_voxels": built["space"].occupied_voxels,
            "placements": len(built["space"].placements),
        },
        "findings": findings,
        "not_verified": not_verified,
        "unsized": built["unsized"],
        "pins": {
            "total": pin_audit["pins_total"],
            "checked": pin_audit["pins_checked"],
            "unverified": pin_audit["pins_unverified"],
            "missing": pin_audit["missing"],
            "unknown_parts": pin_audit["unknown_parts"],
            "sources": pin_audit["sources"],
        },
        "estimated": [p.ref for p in design.parts
                      if p.physical_source == "estimated"],
        # 假绿灯清单：这些"通过"是因为没有可检查的对象（见 design/phase.py）。
        # 必须与真的通过分开呈现，否则空设计看起来像"9 项全过"。
        "vacuous": _phase.vacuous(design, findings),
        "summary": {
            "total": len(findings),
            "failed": len(failed),
            "errors": len(errors),
            "warnings": len(warnings),
            "refs_with_issues": sorted({r for f in failed for r in f["refs"]}),
        },
    }


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    """射线法。点在边界上按内处理（容差 0.01mm）。"""
    x, y = point
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if _on_segment((x, y), (x1, y1), (x2, y2)):
            return True
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xin:
                inside = not inside
    return inside


def _on_segment(p: tuple[float, float], a: tuple[float, float],
                b: tuple[float, float], tol: float = 0.01) -> bool:
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > tol * max(1.0, math.dist(a, b)):
        return False
    return (min(a[0], b[0]) - tol <= p[0] <= max(a[0], b[0]) + tol
            and min(a[1], b[1]) - tol <= p[1] <= max(a[1], b[1]) + tol)
