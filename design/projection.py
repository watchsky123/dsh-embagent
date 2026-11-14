"""2D 投影：mm 规范模型 → 画布像素，以及旧 Velxio 工程的换算。

两条纪律：

1. **mm 是唯一真值。** 像素只在 ``to_2d`` 里出现，3D 从不读像素。
2. **一块设计只有一个 ``px_per_mm``**（存在 ``board.px_per_mm``）。旧工程
   加载时按该值一次性换算，于是 2D 画布与 3D 实体同源；如果旧画布本来就
   用这个比例画的，几何 1:1 不失真。

坐标翻转：Velxio 画布 Y 向下，规范模型 Y 向北（向上）。所有换算都做一次
翻转，写在一处，避免每个视图各翻一次。
"""

from __future__ import annotations

from typing import Any

from .catalog import part_size
from .model import Board, DesignError, DesignModel, Part, Placement, Wire

DEFAULT_PPM = 3.7795  # 96 dpi 的 px/mm，仅在没有板卡比例时兜底


def px_per_mm(design: DesignModel) -> float:
    return design.board.px_per_mm or DEFAULT_PPM


# ---------------------------------------------------------------------------
# 正向：模型 -> 2D 载荷
# ---------------------------------------------------------------------------

def to_2d(design: DesignModel) -> dict[str, Any]:
    """给 2D 画布的最小载荷。所有坐标都是**画布像素**（Y 向下）。"""
    ppm = px_per_mm(design)
    bw_px = design.board.size_mm["w"] * ppm
    bh_px = design.board.size_mm["h"] * ppm

    def x_px(x_mm: float) -> float:
        return round(x_mm * ppm, 3)

    def y_px(y_mm: float) -> float:
        # 模型 Y 向上，画布 Y 向下
        return round((design.board.size_mm["h"] - y_mm) * ppm, 3)

    parts: list[dict[str, Any]] = []
    for part in design.parts:
        x0, y0, x1, y1 = part.footprint_mm()
        parts.append({
            "ref": part.ref,
            "kind": part.kind,
            "label": part.label,
            "role": part.role,
            "x_px": x_px(x0),
            "y_px": y_px(y1),
            "w_px": round((x1 - x0) * ppm, 3),
            "h_px": round((y1 - y0) * ppm, 3),
            "rotation_z": part.placement.rotation_z,
            "physical_source": part.physical_source,
            "size_mm": dict(part.size_mm),
            "z_mm": part.placement.z_mm,
        })

    wires: list[dict[str, Any]] = []
    for wire in design.wires:
        wires.append({
            "net": wire.net,
            "path_px": [[x_px(x), y_px(y)] for x, y, _z in wire.path_mm],
        })

    return {
        "units": "mm",
        "px_per_mm": ppm,
        "board": {
            "kind": design.board.kind,
            "name": design.board.name,
            "w_px": round(bw_px, 3),
            "h_px": round(bh_px, 3),
            "thickness_mm": design.board.thickness_mm,
            "px_per_mm": ppm,
            "size_mm": dict(design.board.size_mm),
            "outline_px": [[x_px(x), y_px(y)] for x, y in design.board.outline],
        },
        "parts": parts,
        "wires": wires,
        "mount_holes": [
            {"x_px": x_px(h.x_mm), "y_px": y_px(h.y_mm),
             "d_px": round(h.diameter_mm * ppm, 3), "source": h.source}
            for h in design.board.mount_holes
        ],
        "estimated_parts": [p.ref for p in design.parts
                            if p.physical_source == "estimated"],
        "warnings": list(design.warnings),
    }


# ---------------------------------------------------------------------------
# 反向：旧 Velxio 画布 -> 规范模型
# ---------------------------------------------------------------------------

def _parse_legacy(project: dict[str, Any]) -> tuple[str, list[dict], list[dict]]:
    """拆出 ``(board_kind, components, wires)``。

    兼容三种写法：
      * 示例工程 ``{boardType, components, wires}``
      * 保存的工程 ``{board: {...}, components, wires}``
      * Wokwi diagram ``{parts:[{type,id,x,y}], connections:[...]}``（由调用方
        先转成上面的形状；这里只认扁平的 components/wires）
    """
    board_kind = str(project.get("boardType") or project.get("boardKind") or "")
    board = project.get("board")
    if isinstance(board, dict):
        board_kind = str(board.get("boardKind") or board.get("kind") or board_kind)
        if not board_kind and board.get("type"):
            board_kind = str(board["type"])
    components = project.get("components")
    if components is None:
        components = project.get("parts") or []
    wires = project.get("wires")
    if wires is None:
        wires = []
    return (board_kind or "arduino-uno",
            [dict(c) for c in components],
            [dict(w) for w in wires])


def _is_board_component(component: dict[str, Any], board_kind: str) -> bool:
    kind = str(component.get("type") or component.get("kind") or "").lower()
    if kind == board_kind.lower():
        return True
    return kind.startswith("board-") or kind.startswith("wokwi-board")


def from_legacy_canvas(
    project: dict[str, Any],
    *,
    board_kind: str | None = None,
    design_id: str | None = None,
) -> DesignModel:
    """把旧 Velxio 画布（像素）转成 mm 规范模型。

    规则：
      * 板卡尺寸/比例来自 ``boards.board_preset``（毫米优先）；
      * 元件尺寸来自 ``catalog.part_size``，查不到的标 ``estimated`` 且尺寸为 0，
        **不从像素反推**——那会造出看起来精确的假数据；
      * 画布 Y 翻转一次；
      * 每条换算写进 ``warnings``，面板要能告诉用户"已按 X px/mm 归一"。
    """
    from .boards import board_preset

    kind, components, wires = _parse_legacy(project)
    kind = board_kind or kind
    board = board_preset(kind)
    ppm = board.px_per_mm
    bh_mm = board.size_mm["h"]
    warnings: list[str] = []

    design = DesignModel(
        id=design_id or str(project.get("id") or "imported"),
        title=str(project.get("title") or ""),
        board=board,
        meta={
            "source": "velxio-canvas",
            "px_per_mm": ppm,
            "legacy_board": kind,
        },
    )

    board_comp: dict[str, Any] | None = None
    for component in components:
        if _is_board_component(component, kind):
            board_comp = component
            continue

        ctype = str(component.get("type") or component.get("kind") or "unknown")
        ref = str(component.get("id") or component.get("ref") or f"P{len(design.parts) + 1}")
        x_px = float(component.get("x", 0) or 0)
        y_px = float(component.get("y", 0) or 0)
        properties = dict(component.get("properties") or {})

        info = part_size(ctype)
        size = info["size_mm"]
        source = info["physical_source"]
        if not info["known"]:
            warnings.append(
                f"{ref}（{ctype}）没有物理尺寸，标为 estimated；3D 与核对面板会单列")
        # 足迹尺寸未知时用 0，但位置仍然换算——3D 会显示为薄片而不是假装有体积
        w_mm = size["w"]
        h_mm = size["h"]
        d_mm = size["d"] or 0.0

        x_mm = x_px / ppm
        # 画布 Y 向下：元件底边距板底 = 板高 - (画布顶边 + 元件高)
        y_mm = bh_mm - (y_px + h_mm * ppm) / ppm
        if properties.get("rotation") is not None:
            rot = int(round(float(properties["rotation"]) / 90.0)) * 90 % 360
        elif properties.get("rotate") is not None:
            rot = int(round(float(properties["rotate"]) / 90.0)) * 90 % 360
        else:
            rot = 0

        legacy_px: dict[str, float] = {
            "x": round(x_px, 3),
            "y": round(y_px, 3),
        }
        # Velxio 的 Component 没有宽高（渲染尺寸来自 DOM），所以只有当来源
        # 真的给了像素尺寸时才记下来——否则交叉核对只能说"没有画图尺寸"。
        for key, px_key in (("width", "w_px"), ("height", "h_px")):
            raw = component.get(key)
            if raw is not None:
                try:
                    legacy_px[px_key] = round(float(raw), 3)
                except (TypeError, ValueError):
                    pass

        part = Part(
            ref=ref,
            kind=ctype,
            label=str(properties.get("label") or ctype),
            role=str(properties.get("role") or "component"),
            size_mm={"w": w_mm, "h": h_mm, "d": d_mm},
            physical_source=source,
            placement=Placement(
                x_mm=round(x_mm, 3), y_mm=round(y_mm, 3), z_mm=0.0,
                rotation_z=rot, mount="board",
            ),
            electrical={
                **properties,
                # 画图像素原样留住：交叉核对要拿它和物理尺寸比，而不是反过来
                "legacy_px": legacy_px,
            },
        )
        try:
            design.add_part(part)
        except DesignError as exc:
            warnings.append(str(exc))

    if board_comp is not None:
        bx = float(board_comp.get("x", 0) or 0)
        by = float(board_comp.get("y", 0) or 0)
        if abs(bx) > 0.01 or abs(by) > 0.01:
            warnings.append(
                f"旧工程板卡在画布上有偏移（{bx:.0f},{by:.0f}px）；"
                "规范模型以板角为原点，元件坐标已按板角重算")

    # 网表：连线端点就是电气事实；位置来自 waypoints（没有就用端点中心）
    #
    # Velxio 的画布把板卡当成"隐式元件"：连线的 componentId 是板卡 id（`uno`、
    # `board-arduino-uno` …），而板卡不在 components 列表里。规范模型用固定
    # ref `"board"` 代表板卡本身，所以这里把认不出的端点统一映射到它，并把
    # 原始 id 记进 warnings——不静默吞掉。
    known_refs = {p.ref for p in design.parts}
    board_endpoints: set[str] = set()
    for index, wire in enumerate(wires):
        start = wire.get("start") or {}
        end = wire.get("end") or {}
        net = str(wire.get("net") or f"net-{index + 1}")
        pins: list[tuple[str, str]] = []
        for endpoint in (start, end):
            ref = endpoint.get("componentId") or endpoint.get("ref")
            pin = endpoint.get("pinName") or endpoint.get("pin")
            if not ref or not pin:
                continue
            ref = str(ref)
            if ref not in known_refs:
                board_endpoints.add(ref)
                ref = "board"
            pins.append((ref, str(pin)))
        if len(pins) == 2:
            design.connect(net, *pins)

        path_mm: list[tuple[float, float, float]] = []
        for point in wire.get("waypoints") or []:
            path_mm.append((
                round(float(point.get("x", 0)) / ppm, 3),
                round(bh_mm - float(point.get("y", 0)) / ppm, 3),
                0.0,
            ))
        if path_mm:
            design.wires.append(Wire(net=net, path_mm=path_mm))

    warnings.insert(
        0,
        f"旧画布按 {ppm:.4f} px/mm 归一（板卡 {board.kind}，"
        f"{board.size_mm['w']:.2f}×{board.size_mm['h']:.2f} mm）；"
        "2D 视觉可能轻微缩放，但 2D 与 3D 从此同源")
    if board_endpoints:
        warnings.append(
            "这些连线端点不是元件，按板卡引脚处理："
            + "、".join(sorted(board_endpoints)))
    design.warnings = warnings
    return design


def legacy_summary(design: DesignModel) -> dict[str, Any]:
    """旧工程导入后的核对信息，给 2D↔3D 一致性面板用。"""
    missing = [p.ref for p in design.parts if p.size_mm["w"] <= 0]
    estimated = [p.ref for p in design.parts if p.physical_source == "estimated"]
    return {
        "px_per_mm": design.board.px_per_mm,
        "parts": len(design.parts),
        "size_unknown": missing,
        "estimated": estimated,
        "warnings": list(design.warnings),
    }
