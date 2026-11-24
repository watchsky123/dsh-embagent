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
from .ratlines import build_ratlines

DEFAULT_PPM = 3.7795  # 96 dpi 的 px/mm，仅在没有板卡比例时兜底


def px_per_mm(design: DesignModel) -> float:
    return design.board.px_per_mm or DEFAULT_PPM


# ---------------------------------------------------------------------------
# 正向：模型 -> 2D 载荷
# ---------------------------------------------------------------------------

#: 拼板里板与板之间的间距（mm）。留一点缝，否则相邻两块板的轮廓线会重叠，
#: 看起来像一块板。
PANEL_GAP_MM = 8.0


def panel_layout(design: DesignModel) -> dict[str, Any]:
    """计算**拼板**排布：每块板在拼板坐标系里的偏移。

    ## 排布规则（确定性，不随字典序漂移）

    1. 主板（`design.board`）固定放在 **(0, 0)**；
    2. 其余板按 **元件数从多到少**、同数按 `id` 字典序，从左到右排在主板右侧；
    3. 所有板**底边对齐**（y = 0），间距 `PANEL_GAP_MM`。

    排不下就换行（很少发生，但拼板可能有十几块）。

    ## 为什么用"偏移到拼板 + 全程一次 Y 翻转"

    每块板**各自**做 Y 翻转是错的：翻转是相对各自板高的镜像，拼板时两块
    板的相对位置就乱了（一块翻左、一块翻右）。

    正确做法是：先把局部坐标**平移**到拼板坐标（这一步不翻转），
    最后用**拼板总高度**做**一次**翻转。这样板和板之间的相对位置保持
    导入时的意图，而且翻转只在一处发生（和单板时完全同一条代码路径）。

    返回：

        {
          "boards": [{"board": Board, "x": ox, "y": oy, "w": .., "h": ..}],
          "size_mm": {"w": 拼板宽, "h": 拼板高},
        }
    """
    boards = design.board_list()
    main = design.board
    # 主板在前，其余按元件数降序、id 升序
    others = [b for b in boards if b is not main]
    counts = {id(b): len(design.parts_of(b)) for b in others}
    others.sort(key=lambda b: (-counts[id(b)], b.id))

    placed: list[dict[str, Any]] = []
    cursor_x = 0.0
    cursor_y = 0.0
    row_height = 0.0
    # 行宽上限 = 主板宽度的 1.6 倍（拼板通常围着一块主板摆）。
    #
    # 用 1.0 倍时实测 8 块板排成 96×288mm 的窄条，画面上大量空白；
    # 1.6 倍刚好能让两块中等子板并排。这个系数是**显示偏好**，不影响
    # 任何判定（判定按各自的板坐标系算）。
    row_limit = float(main.size_mm["w"]) * 1.6 if main is not None else 120.0
    panel_w = 0.0
    for index, board in enumerate([main, *others]):
        w = float(board.size_mm["w"])
        h = float(board.size_mm["h"])
        # 换行：只有"这一行已经有东西"且再放会超出行宽时才换。
        # 之前用 `cursor_x + w > max(panel_w, w)`（跟着已用宽度走）会导致
        # 永远只要放得下就往后排 —— 实测 8 块板排成一列 288mm 高的长条，
        # 每块板都很小，画面上大片空。
        if index > 0 and cursor_x > 0.0 and cursor_x + w > row_limit:
            cursor_x = 0.0
            cursor_y += row_height + PANEL_GAP_MM
            row_height = 0.0
        placed.append({"board": board, "x": round(cursor_x, 4),
                       "y": round(cursor_y, 4), "w": w, "h": h})
        cursor_x += w + PANEL_GAP_MM
        row_height = max(row_height, h)
        panel_w = max(panel_w, cursor_x - PANEL_GAP_MM)
    panel_h = cursor_y + row_height if placed else 0.0
    return {
        "boards": placed,
        "size_mm": {"w": round(panel_w, 4), "h": round(panel_h, 4)},
    }


def to_2d(design: DesignModel) -> dict[str, Any]:
    """给 2D 画布的最小载荷。所有坐标都是**画布像素**（Y 向下）。

    ## 多板

    投影先把每块板上的元件平移到**拼板坐标**，再用拼板总高度做**一次**
    Y 翻转（见 `panel_layout` 的说明）。所以下面所有 `x_px`/`y_px`
    接收的都是拼板坐标，不是某块板的局部坐标。

    单板设计时拼板 = 那块板、偏移为 0，与旧行为**逐位一致**。
    """
    ppm = px_per_mm(design)
    panel = panel_layout(design)
    panel_w = panel["size_mm"]["w"]
    panel_h = panel["size_mm"]["h"]
    bw_px = panel_w * ppm
    bh_px = panel_h * ppm

    # 板 id → 偏移（局部坐标 → 拼板坐标）
    offset_of: dict[str, tuple[float, float]] = {}
    board_payloads: list[dict[str, Any]] = []
    for entry in panel["boards"]:
        board = entry["board"]
        key = board.id
        offset_of[key] = (entry["x"], entry["y"])
        board_payloads.append({
            "id": board.id,
            "kind": board.kind,
            "name": board.name,
            "x_mm": entry["x"],
            "y_mm": entry["y"],
            "w_mm": entry["w"],
            "h_mm": entry["h"],
            "w_px": round(entry["w"] * ppm, 3),
            "h_px": round(entry["h"] * ppm, 3),
            "x_px": round(entry["x"] * ppm, 3),
            "y_px": round(entry["y"] * ppm, 3),
            "thickness_mm": board.thickness_mm,
            "size_mm": dict(board.size_mm),
            "outline_px": [
                [round((x + entry["x"]) * ppm, 3),
                 round((panel_h - (y + entry["y"])) * ppm, 3)]
                for x, y in board.outline],
            "parts": len(design.parts_of(board)),
        })

    main_offset = offset_of.get(design.board.id, (0.0, 0.0))

    def x_px(x_mm: float) -> float:
        return round(x_mm * ppm, 3)

    def y_px(y_mm: float) -> float:
        # 模型 Y 向上，画布 Y 向下（用**拼板**总高度，不是某块板的高度）
        return round((panel_h - y_mm) * ppm, 3)

    def to_panel(part: Part) -> tuple[float, float]:
        """元件的局部坐标 → 拼板坐标。"""
        owner = design.board_of(part)
        ox, oy = offset_of.get(owner.id, (0.0, 0.0))
        return (ox, oy)

    parts: list[dict[str, Any]] = []
    for part in design.parts:
        ox, oy = to_panel(part)
        x0, y0, x1, y1 = part.footprint_mm()
        parts.append({
            "ref": part.ref,
            "kind": part.kind,
            "label": part.label,
            "role": part.role,
            "board_id": design.board_of(part).id,
            "x_px": x_px(x0 + ox),
            "y_px": y_px(y1 + oy),
            "w_px": round((x1 - x0) * ppm, 3),
            "h_px": round((y1 - y0) * ppm, 3),
            "rotation_z": part.placement.rotation_z,
            "physical_source": part.physical_source,
            "size_mm": dict(part.size_mm),
            "z_mm": part.placement.z_mm,
            "side": part.placement.side,
            # 焊盘（真实几何）：画焊盘、连飞线都用它。
            "pads": [{
                "num": pad.num,
                "x_px": x_px(pad.x_mm + ox),
                "y_px": y_px(pad.y_mm + oy),
                "w_px": round(pad.w_mm * ppm, 3),
                "h_px": round(pad.h_mm * ppm, 3),
                "shape": pad.shape,
                "rotation_z": pad.rotation_z,
                "layer": pad.layer,
            } for pad in part.pads],
        })

    # 走线：`Wire` 目前没有 board_id（导入器只取主板的铜线），
    # 所以按主板的偏移平移。多板铜线要等 `Wire` 加 `board_id`（后续）。
    wires: list[dict[str, Any]] = []
    for wire in design.wires:
        wires.append({
            "net": wire.net,
            "path_px": [[x_px(x + main_offset[0]), y_px(y + main_offset[1])]
                        for x, y, _z in wire.path_mm],
            "layer": wire.layer,
            "width_px": round(wire.width_mm * ppm, 3),
            "routed": True,
        })

    # 飞线：跨板网络**不画**（板与板之间的连接不是 PCB 铜线，
    # 画出来会是一条横穿拼板的假线）。如实计数。
    ratlines: list[dict[str, Any]] = []
    cross_board = 0
    for line in build_ratlines(design):
        if line.get("cross_board"):
            cross_board += 1
            continue
        # 端点按**所属板**平移：飞线是板内几何，坐标是那块板的局部坐标。
        ox, oy = offset_of.get(str(line.get("board_id") or ""), (0.0, 0.0))
        ratlines.append({
            "net": line["net"],
            "path_px": [[x_px(x + ox), y_px(y + oy)]
                        for x, y in line["segments"]],
            "length_mm": line["length_mm"],
            "pins": line["pins"],
            "board_id": line.get("board_id", ""),
            "routed": False,
            "approximate": line["approximate"],
        })

    return {
        "units": "mm",
        "px_per_mm": ppm,
        "panel": {
            "size_mm": {"w": round(panel_w, 4), "h": round(panel_h, 4)},
            "gap_mm": PANEL_GAP_MM,
            "board_count": len(panel["boards"]),
        },
        # 主参考板（兼容旧字段：前端少量地方直接读 `board.size_mm`）
        "board": board_payloads[0] if board_payloads else {},
        "boards": board_payloads,
        "parts": parts,
        "wires": wires,
        "ratlines": ratlines,
        "cross_board_ratlines_skipped": cross_board,
        "mount_holes": [
            {"board_id": entry["board"].id,
             "x_px": x_px(h.x_mm + entry["x"]),
             "y_px": y_px(h.y_mm + entry["y"]),
             "d_px": round(h.diameter_mm * ppm, 3), "source": h.source}
            for entry in panel["boards"]
            for h in entry["board"].mount_holes
        ],
        # 外壳：2D 只画**俯视图**（外框 + 内腔虚线 + 开口矩形）。
        # 与 3D 共用 `shell.payload()`——同一份 mm，不在 TS 里再推一次。
        "enclosure": _enclosure_2d(design, x_px, y_px, ppm),
        "estimated_parts": [p.ref for p in design.parts
                            if p.physical_source == "estimated"],
        "warnings": list(design.warnings),
    }


def _enclosure_2d(design: DesignModel, x_px, y_px, ppm: float
                  ) -> dict[str, Any] | None:
    """外壳的**俯视图**投影：外框 / 内腔 / 顶面开口（px）。

    **px 在 Python 里算**：TS 只画，不做 mm↔px 换算（判定与几何只有一个地方）。
    侧面开口在俯视图里只是一条边，画出来必然错位——所以只计数，
    不去猜它落在哪。没有外壳返回 None（不是空对象）。
    """
    from . import shell as shell_module

    payload = shell_module.payload(design)
    if payload is None:
        return None
    wall = payload["wall_mm"]
    ox = payload["board_offset_mm"]["x"]
    oy = payload["board_offset_mm"]["y"]
    inner = payload["inner_mm"]
    outer = payload["outer_mm"]

    def rect(x0: float, y0: float, w: float, h: float) -> dict[str, float]:
        # y 轴在画布上是反的：矩形的**上边**来自 mm 的 y0+h。
        return {"x_px": round(x_px(x0), 3),
                "y_px": round(y_px(y0 + h), 3),
                "w_px": round(w * ppm, 3),
                "h_px": round(h * ppm, 3)}

    top = [o for o in payload["openings"] if o["face"] in ("top", "bottom")]
    return {
        "outer": {**rect(-ox - wall, -oy - wall, outer["w"], outer["h"]),
                  "w_mm": outer["w"], "h_mm": outer["h"]},
        "inner": {**rect(-ox, -oy, inner["w"], inner["h"]),
                  "w_mm": inner["w"], "h_mm": inner["h"]},
        "openings": [{**rect(o["x_mm"] - o["w_mm"] / 2,
                             o["y_mm"] - o["h_mm"] / 2,
                             o["w_mm"], o["h_mm"]),
                      "face": o["face"], "kind": o["kind"]}
                     for o in top],
        "side_openings": sum(1 for o in payload["openings"]
                             if o["face"] not in ("top", "bottom")),
        "wall_mm": wall,
        "lid_mm": payload["lid_mm"],
        "inner_mm": inner,
        "outer_mm": outer,
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
