"""EDA 导入：KiCad PCB / BOM / Wokwi diagram → ``DesignModel``。

导入是**一次导入、两份投影**：板轮廓（Edge.Cuts）与封装位置同时喂给 2D 与 3D。

纪律：

* 不猜尺寸。封装没有 courtyard/尺寸信息时标 ``estimated`` 并用 5×5mm 占位，
  面板会单列"估算件"。
* KiCad 的 Y 轴向下，规范模型 Y 向上——**只在这里翻转一次**。
* 解析失败如实报 ``ImportError``，绝不返回一个"看起来空"的设计。
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

from .catalog import part_size
from .model import Board, DesignError, DesignModel, Part, Placement, Point2, Wire


class ImportError_(ValueError):
    """导入失败。消息要说清楚是哪个文件、哪一段。"""


ImportError = ImportError_  # 对外名字，同时避免遮蔽内建（这里显式重绑）


# ---------------------------------------------------------------------------
# S-expression
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


def parse_sexpr(text: str) -> list[Any]:
    """把 KiCad 的 S-表达式解析成嵌套列表（原子全是 str）。"""
    tokens = _TOKEN.findall(text)
    if not tokens:
        raise ImportError_("S-表达式为空")
    stack: list[list[Any]] = [[]]
    for token in tokens:
        if token == "(":
            stack.append([])
        elif token == ")":
            if len(stack) < 2:
                raise ImportError_("S-表达式括号不配对（多余的 ')'）")
            done = stack.pop()
            stack[-1].append(done)
        elif token.startswith('"'):
            stack[-1].append(bytes(token[1:-1], "utf-8")
                              .decode("unicode_escape", "replace"))
        else:
            stack[-1].append(token)
    if len(stack) != 1:
        raise ImportError_("S-表达式括号不配对（缺少 ')'）")
    if len(stack[0]) != 1 or not isinstance(stack[0][0], list):
        raise ImportError_("S-表达式必须只有一个顶层节点")
    return stack[0][0]


def _children(node: list[Any], key: str) -> list[list[Any]]:
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == key]


def _child(node: list[Any], key: str) -> list[Any] | None:
    found = _children(node, key)
    return found[0] if found else None


def _atom(node: list[Any] | None, index: int, default: float = 0.0) -> float:
    if node is None or len(node) <= index:
        return default
    try:
        return float(node[index])
    except (TypeError, ValueError):
        return default


def _text(node: list[Any] | None, index: int, default: str = "") -> str:
    if node is None or len(node) <= index:
        return default
    value = node[index]
    return str(value) if not isinstance(value, list) else default


# ---------------------------------------------------------------------------
# KiCad PCB
# ---------------------------------------------------------------------------

def _arc_points(start: Point2, mid: Point2, end: Point2, segments: int = 12) -> list[Point2]:
    """三点定圆，离散成折线。三点共线时退化为直线。"""
    (x1, y1), (x2, y2), (x3, y3) = start, mid, end
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        return [start, end]
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1)
          + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3)
          + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1 = math.atan2(y1 - uy, x1 - ux)
    a3 = math.atan2(y3 - uy, x3 - ux)
    am = math.atan2(y2 - uy, x2 - ux)
    # 选择经过 mid 的弧向
    two_pi = 2 * math.pi
    ccw_span = (a3 - a1) % two_pi
    mid_ccw = (am - a1) % two_pi
    if mid_ccw <= ccw_span:
        span, direction = ccw_span, 1
    else:
        span, direction = two_pi - ccw_span, -1
    return [
        (ux + r * math.cos(a1 + direction * span * i / segments),
         uy + r * math.sin(a1 + direction * span * i / segments))
        for i in range(segments + 1)
    ]


def _edge_segments(root: list[Any]) -> list[tuple[Point2, Point2]]:
    """Edge.Cuts 上的所有线段（弧已离散）。"""
    segments: list[tuple[Point2, Point2]] = []
    for node in root[1:]:
        if not isinstance(node, list) or not node:
            continue
        kind = node[0]
        layer = _child(node, "layer")
        if layer is None or _text(layer, 1) != "Edge.Cuts":
            continue
        if kind == "gr_line" or kind == "fp_line":
            start, end = _child(node, "start"), _child(node, "end")
            if start and end:
                segments.append(((_atom(start, 1), _atom(start, 2)),
                                 (_atom(end, 1), _atom(end, 2))))
        elif kind == "gr_rect":
            start, end = _child(node, "start"), _child(node, "end")
            if start and end:
                x0, y0 = _atom(start, 1), _atom(start, 2)
                x1, y1 = _atom(end, 1), _atom(end, 2)
                corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
                segments.extend(zip(corners, corners[1:]))
        elif kind == "gr_arc" or kind == "fp_arc":
            start, mid, end = _child(node, "start"), _child(node, "mid"), _child(node, "end")
            if start and mid and end:
                pts = _arc_points((_atom(start, 1), _atom(start, 2)),
                                  (_atom(mid, 1), _atom(mid, 2)),
                                  (_atom(end, 1), _atom(end, 2)))
                segments.extend(zip(pts, pts[1:]))
        elif kind == "gr_circle":
            center, end = _child(node, "center"), _child(node, "end")
            if center and end:
                cx, cy = _atom(center, 1), _atom(center, 2)
                r = math.hypot(_atom(end, 1) - cx, _atom(end, 2) - cy)
                pts = [(cx + r * math.cos(2 * math.pi * i / 32),
                        cy + r * math.sin(2 * math.pi * i / 32)) for i in range(33)]
                segments.extend(zip(pts, pts[1:]))
    return segments


def _chain_outline(segments: list[tuple[Point2, Point2]], tol: float = 0.25) -> list[Point2]:
    """把散线段接成闭合轮廓。接不上就报错，而不是猜一个矩形。"""
    if not segments:
        raise ImportError_("Edge.Cuts 上没有任何线段；KiCad PCB 需要板框图层")
    pending = list(segments)
    first = pending.pop(0)
    loop: list[Point2] = [first[0], first[1]]

    def close_enough(a: Point2, b: Point2) -> bool:
        return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

    while pending:
        tail = loop[-1]
        for index, (a, b) in enumerate(pending):
            if close_enough(tail, a):
                loop.append(b)
                pending.pop(index)
                break
            if close_enough(tail, b):
                loop.append(a)
                pending.pop(index)
                break
        else:
            # 换一端继续（闭合环从两个方向都能接）
            if close_enough(loop[0], loop[-1]):
                break
            head = loop[0]
            for index, (a, b) in enumerate(pending):
                if close_enough(head, a):
                    loop.insert(0, b)
                    pending.pop(index)
                    break
                if close_enough(head, b):
                    loop.insert(0, a)
                    pending.pop(index)
                    break
            else:
                raise ImportError_(
                    f"板框有 {len(pending)} 段线段接不成闭合轮廓（最近端点距 "
                    f"({tail[0]:.3f},{tail[1]:.3f}) 太远）；拒绝猜一个矩形")
    if not close_enough(loop[0], loop[-1]):
        loop.append(loop[0])
    if len(loop) < 5:
        raise ImportError_("板框闭合后少于 4 个角，无法作为轮廓")
    return loop[:-1]


def _footprint_size(fp: list[Any]) -> tuple[float, float, str]:
    """封装尺寸：优先 courtyard / fab 的矩形，否则 5×5 估算。"""
    best: tuple[float, float] | None = None
    for layer_name in ("F.CrtYd", "B.CrtYd", "F.Fab", "B.Fab"):
        xs: list[float] = []
        ys: list[float] = []
        for node in fp[1:]:
            if not isinstance(node, list) or not node:
                continue
            if node[0] not in ("fp_rect", "fp_line", "fp_poly"):
                continue
            layer = _child(node, "layer")
            if layer is None or _text(layer, 1) != layer_name:
                continue
            start, end = _child(node, "start"), _child(node, "end")
            points = _child(node, "pts")
            pts: list[Point2] = []
            if start and end:
                pts = [(_atom(start, 1), _atom(start, 2)), (_atom(end, 1), _atom(end, 2))]
            elif points:
                for xy in _children(points, "xy"):
                    pts.append((_atom(xy, 1), _atom(xy, 2)))
            for (x, y) in pts:
                xs.append(x)
                ys.append(y)
        if len(xs) >= 2 and len(ys) >= 2:
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            if w > 0.05 and h > 0.05:
                if best is None or w * h > best[0] * best[1]:
                    best = (w, h)
    if best is None:
        return (5.0, 5.0, "estimated")
    return (best[0], best[1], "derived")


def _pad_bounds(fp: list[Any]) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for pad in _children(fp, "pad"):
        at = _child(pad, "at")
        size = _child(pad, "size")
        if at is None:
            continue
        px, py = _atom(at, 1), _atom(at, 2)
        sx = _atom(size, 1, 0.0) if size else 0.0
        sy = _atom(size, 2, 0.0) if size else 0.0
        xs.extend([px - sx / 2, px + sx / 2])
        ys.extend([py - sy / 2, py + sy / 2])
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def import_kicad_pcb(path: str | Path, *, design_id: str | None = None,
                     title: str = "") -> DesignModel:
    """读 KiCad PCB：Edge.Cuts → 轮廓，封装 → 元件，焊盘 → 尺寸兜底。"""
    file = Path(path)
    text = file.read_text(encoding="utf-8", errors="replace")
    root = parse_sexpr(text)
    if root[0] != "kicad_pcb":
        raise ImportError_(f"{file.name} 不是 kicad_pcb（顶层是 {root[0]!r}）")

    thickness = 1.6
    general = _child(root, "general")
    if general is not None:
        thickness_node = _child(general, "thickness")
        if thickness_node is not None:
            thickness = _atom(thickness_node, 1, 1.6)

    loop = _chain_outline(_edge_segments(root))
    min_x = min(x for x, _ in loop)
    min_y = min(y for _, y in loop)
    max_y = max(y for _, y in loop)
    outline: list[Point2] = [
        (round(x - min_x, 4), round(max_y - y, 4)) for x, y in loop
    ]
    width = max(x for x, _ in outline)
    height = max(y for _, y in outline)

    parts: list[Part] = []
    warnings: list[str] = []
    for fp in _children(root, "footprint"):
        lib_name = _text(fp, 1, "footprint")
        at = _child(fp, "at")
        if at is None:
            continue
        fx, fy = _atom(at, 1), _atom(at, 2)
        rotation = _atom(at, 3, 0.0)

        ref = ""
        value = ""
        for node in fp[1:]:
            if isinstance(node, list) and node and node[0] == "fp_text":
                which = _text(node, 1)
                if which == "reference":
                    ref = _text(node, 2)
                elif which == "value":
                    value = _text(node, 2)
        for node in _children(fp, "property"):
            name = _text(node, 1).lower()
            if name == "reference" and not ref:
                ref = _text(node, 2)
            elif name == "value" and not value:
                value = _text(node, 2)
        if not ref:
            ref = f"FP{len(parts) + 1}"

        w, h, size_source = _footprint_size(fp)
        pads = _pad_bounds(fp)
        if size_source == "estimated" and pads is not None:
            pw = pads[2] - pads[0]
            ph = pads[3] - pads[1]
            if pw > 0.05 and ph > 0.05:
                w, h, size_source = pw, ph, "derived"

        info = part_size(lib_name) if part_size(lib_name)["known"] else None
        height_mm = 0.0
        if info is not None:
            height_mm = info["size_mm"]["d"]
            warnings.append(
                f"{ref}: 高度取自内置尺寸表（{lib_name}），PCB 文件里没有高度")
        else:
            warnings.append(f"{ref}: 高度未知（PCB 文件不含 3D 高度），3D 按薄片显示")

        # 归一：板角为原点；KiCad Y 向下 -> 模型 Y 向上（封装本地原点近似为足迹中心）
        x_mm = (fx - min_x) - w / 2.0
        y_mm = (max_y - fy) - h / 2.0

        parts.append(Part(
            ref=ref,
            kind=lib_name,
            label=value or lib_name,
            role="component",
            size_mm={"w": round(w, 4), "h": round(h, 4), "d": height_mm},
            physical_source=size_source,
            placement=Placement(x_mm=round(x_mm, 4), y_mm=round(y_mm, 4),
                                z_mm=0.0,
                                rotation_z=int(round(abs(rotation) / 90.0)) * 90 % 360),
        ))

    board = Board(
        kind="kicad",
        name=f"{file.stem}",
        outline=[(round(x, 4), round(y, 4)) for x, y in outline],
        size_mm={"w": round(width, 4), "h": round(height, 4)},
        thickness_mm=round(thickness, 3),
        mount_holes=[],
        px_per_mm=3.7795,
    )

    design = DesignModel(
        id=design_id or file.stem,
        title=title or file.stem,
        board=board,
        parts=parts,
        meta={"source": "kicad-pcb", "file": file.name,
              "origin": {"x_mm": round(min_x, 4), "y_mm": round(min_y, 4)}},
    )
    if not parts:
        warnings.append("PCB 里没有封装；只有板轮廓")
    design.warnings = warnings
    return design


# ---------------------------------------------------------------------------
# BOM
# ---------------------------------------------------------------------------

def import_bom(path: str | Path) -> dict[str, dict[str, str]]:
    """读 BOM CSV，返回 ``ref -> {value, footprint, lcsc}``。

    列名按大小写不敏感匹配（Reference/References/Designator 等）。
    """
    file = Path(path)
    text = file.read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportError_(f"{file.name} 没有表头")
    lower = {name.lower().strip(): name for name in reader.fieldnames}

    def pick(*names: str) -> str | None:
        for name in names:
            if name in lower:
                return lower[name]
        return None

    ref_col = pick("reference", "references", "designator", "ref", "refs")
    if ref_col is None:
        raise ImportError_(
            f"{file.name} 找不到参考号列（尝试 reference/references/designator/ref）")
    value_col = pick("value", "comment", "description")
    fp_col = pick("footprint", "package", "case")
    lcsc_col = pick("lcsc", "lcsc part", "lcsc#", "supplier part")

    out: dict[str, dict[str, str]] = {}
    for row in reader:
        raw_refs = str(row.get(ref_col) or "")
        for ref in re.split(r"[,;\s]+", raw_refs.strip()):
            if not ref:
                continue
            out[ref] = {
                "value": str(row.get(value_col) or "") if value_col else "",
                "footprint": str(row.get(fp_col) or "") if fp_col else "",
                "lcsc": str(row.get(lcsc_col) or "") if lcsc_col else "",
            }
    return out


def apply_bom(design: DesignModel, bom: dict[str, dict[str, str]]) -> list[str]:
    """把 BOM 信息贴到设计上；返回未匹配的参考号（不静默丢弃）。"""
    unmatched: list[str] = []
    for ref, row in bom.items():
        part = design.part(ref)
        if part is None:
            unmatched.append(ref)
            continue
        if row.get("value"):
            part.label = row["value"]
        if row.get("lcsc"):
            part.lcsc = row["lcsc"]
        if row.get("footprint"):
            part.electrical["footprint"] = row["footprint"]
    return unmatched


# ---------------------------------------------------------------------------
# Wokwi / Velxio
# ---------------------------------------------------------------------------

def wokwi_to_legacy(diagram: dict[str, Any]) -> dict[str, Any]:
    """Wokwi diagram.json → Velxio 示例工程形状（统一交给 projection 换算）。"""
    board_kind = "arduino-uno"
    components: list[dict[str, Any]] = []
    for entry in diagram.get("parts") or []:
        part_type = str(entry.get("type") or "")
        if part_type.startswith("wokwi-board") or part_type in (
                "board-arduino-uno", "board-esp32", "board-raspberry-pi-pico"):
            board_kind = part_type.replace("board-", "")
            if board_kind.startswith("wokwi-board"):
                board_kind = board_kind.replace("wokwi-board-", "")
            continue
        components.append({
            "type": part_type,
            "id": str(entry.get("id") or f"P{len(components) + 1}"),
            "x": float(entry.get("left", entry.get("x", 0)) or 0),
            "y": float(entry.get("top", entry.get("y", 0)) or 0),
            "properties": dict(entry.get("attrs") or {}),
        })
    wires: list[dict[str, Any]] = []
    for index, connection in enumerate(diagram.get("connections") or []):
        if not isinstance(connection, list) or len(connection) < 2:
            continue

        def split(endpoint: str) -> tuple[str, str]:
            if ":" not in endpoint:
                return ("", "")
            ref, _, pin = endpoint.partition(":")
            return (ref, pin)

        start_ref, start_pin = split(str(connection[0]))
        end_ref, end_pin = split(str(connection[1]))
        wires.append({
            "id": f"w{index + 1}",
            "net": f"w{index + 1}",
            "start": {"componentId": start_ref, "pinName": start_pin},
            "end": {"componentId": end_ref, "pinName": end_pin},
            "color": str(connection[2]) if len(connection) > 2 else "",
        })
    return {
        "id": str(diagram.get("id") or "wokwi"),
        "title": str(diagram.get("title") or ""),
        "boardType": board_kind,
        "components": components,
        "wires": wires,
    }


def load_diagram(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def import_any(path: str | Path, *, design_id: str | None = None) -> DesignModel:
    """按扩展名分派：``.kicad_pcb`` / ``.json``（Wokwi 或 Velxio 工程）。"""
    from .projection import from_legacy_canvas

    file = Path(path)
    suffix = file.suffix.lower()
    if suffix == ".kicad_pcb":
        return import_kicad_pcb(file, design_id=design_id)
    if suffix == ".json":
        data = load_diagram(file)
        if "parts" in data and "connections" in data:
            data = wokwi_to_legacy(data)
        return from_legacy_canvas(data, design_id=design_id or file.stem)
    raise ImportError_(
        f"不支持的导入格式 {suffix!r}；目前支持 .kicad_pcb 与 Wokwi/Velxio .json")


def supported_formats() -> list[dict[str, Any]]:
    return [
        {"ext": ".kicad_pcb", "kind": "kicad-pcb",
         "reads": ["Edge.Cuts 轮廓", "封装位置/旋转", "焊盘包围盒", "板厚"],
         "missing": ["元件高度（PCB 文件不含）", "网表（需原理图）"]},
        {"ext": ".json", "kind": "wokwi|velxio",
         "reads": ["元件类型/位置", "连接关系"],
         "missing": ["物理尺寸（查内置表）", "板轮廓（用板卡预设）"]},
        {"ext": ".csv", "kind": "bom",
         "reads": ["参考号", "值", "封装", "LCSC"], "missing": []},
    ]
