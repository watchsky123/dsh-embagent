"""布局规则 —— 摆放**之前**就该守住的几条几何约束。

【为什么单独一层】

布线段只会"想办法连上"，它不负责"这块板能不能装配"。
手册 §14.3 的三条实测教训全部属于布局阶段，而且**在布局阶段解决比在布线
阶段补救便宜得多**：

1. **连接器必须出板**：SMD 卧贴连接器的开口方向由焊盘分布决定，
   信号焊盘所在侧是"后背焊尾"，**开口在相反侧**。板框边线要放在开口侧，
   让外壳悬伸出板外；否则线缆**插不进去**。
2. **相邻无源件的中心距**：手册实测 0603 焊盘 31.8×34 mil、两焊盘中心距
   约 59.4 mil；相邻元件中心距 70~80 mil 时**焊盘直接重叠**（DRC 报短路）。
   **安全值 120 mil（≈3.05 mm）**。
3. **封装占位要实算**：SOT-223（约 234×181 mil）与 4P 2.54 排针（约
   400×100 mil）放一起极易重叠，摆放前先算包围盒。

这一层只做**几何**，不做电气；也不猜型号 —— 尺寸从 `Part.size_mm` 拿，
拿不到就按 `unknown` 报出来（AGENTS §0.1：缺数据不是通过）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = ["Finding", "LayoutReport", "audit", "autoplace", "connector_edge"]

#: 焊盘间安全中心距（mil）。手册 §17.19 的实测值：0603 中心距 ≥100 mil，
#: 安全值 120 mil。这里取**安全值**——贴着下限放，DRC 会在边缘反复报。
MIN_CENTER_MIL = 120.0

#: 元件到板边的最小距离（mil）。连接器例外（它要出板）。
EDGE_MARGIN_MIL = 40.0

#: 连接器"出板"的悬伸量（mil）。手册 §14.3：外壳要悬伸出板外。
CONNECTOR_OVERHANG_MIL = 60.0

#: 这些 kind 视为连接器（要出板）。判据用**语义 kind**，不猜型号。
_CONNECTOR_KINDS = ("connector", "usb", "header", "socket", "plug", "jack",
                    "terminal", "fpc", "pin_header")

_MM_TO_MIL = 1.0 / 0.0254


@dataclass
class Finding:
    code: str          # overlap / too_close / outside / connector / unknown_size
    board: str = ""
    refs: list[str] = field(default_factory=list)
    detail: str = ""
    severity: str = "warn"   # warn / fail

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "board": self.board, "refs": self.refs,
                "detail": self.detail, "severity": self.severity}


@dataclass
class LayoutReport:
    findings: list[Finding] = field(default_factory=list)
    checked_parts: int = 0
    boards: int = 0

    @property
    def blockers(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "fail"]

    @property
    def status(self) -> str:
        if self.checked_parts == 0:
            return "not_ready"          # 空输入不算通过（AGENTS §0.1）
        return "ok" if not self.blockers else "not_ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "boards": self.boards,
            "checked_parts": self.checked_parts,
            "blockers": [f.to_dict() for f in self.blockers],
            "warnings": [f.to_dict() for f in self.findings
                         if f.severity != "fail"],
            "rules": {"min_center_mil": MIN_CENTER_MIL,
                      "edge_margin_mil": EDGE_MARGIN_MIL,
                      "connector_overhang_mil": CONNECTOR_OVERHANG_MIL},
        }


def _size_mil(part: Any) -> tuple[float, float] | None:
    """元件的封装占位（宽 × 高，mil）。取不到就返回 None —— **不猜**。"""
    size = getattr(part, "size_mm", None) or {}
    try:
        w = float(size.get("w") or 0.0)
        h = float(size.get("h") or 0.0)
    except (TypeError, ValueError, AttributeError):
        return None
    if w <= 0 or h <= 0:
        return None
    return (w * _MM_TO_MIL, h * _MM_TO_MIL)


def _is_connector(part: Any) -> bool:
    kind = str(getattr(part, "kind", "") or "").lower()
    return any(k in kind for k in _CONNECTOR_KINDS)


def connector_edge(part: Any, board: Any) -> str:
    """连接器该贴在**哪一边**（返回 `left/right/top/bottom`）。

    判据只用它的设计坐标：离哪条边最近就贴哪条。**不猜"哪个方向是开口"**
    —— 手写方向会把连接器转 180°，线缆更插不进去。
    """
    place = getattr(part, "placement", None)
    if place is None:
        return "bottom"
    w = float(getattr(board, "size_mm", {}).get("w") or 0.0)
    h = float(getattr(board, "size_mm", {}).get("h") or 0.0)
    if w <= 0 or h <= 0:
        return "bottom"
    d = {"left": float(place.x_mm),
         "right": w - float(place.x_mm),
         "bottom": float(place.y_mm),
         "top": h - float(place.y_mm)}
    return min(d, key=lambda k: d[k])


def audit(design: Any) -> LayoutReport:
    """逐板检查布局规则。**不移动任何东西**。"""
    report = LayoutReport()
    boards = design.board_list()
    report.boards = len(boards)
    for board in boards:
        w = float(getattr(board, "size_mm", {}).get("w") or 0.0)
        h = float(getattr(board, "size_mm", {}).get("h") or 0.0)
        parts = [p for p in design.parts if design.board_of(p) is board]
        report.checked_parts += len(parts)
        boxes: list[tuple[Any, tuple[float, float]]] = []
        for part in parts:
            size = _size_mil(part)
            if size is None:
                report.findings.append(Finding(
                    code="unknown_size", board=str(getattr(board, "name", "")),
                    refs=[str(getattr(part, "ref", ""))],
                    detail="没有 size_mm —— 占位算不出来，请补封装尺寸", severity="warn"))
                continue
            boxes.append((part, size))
        # 1) 出板 / 边距
        for part, (pw, ph) in boxes:
            place = getattr(part, "placement", None)
            if place is None or w <= 0 or h <= 0:
                continue
            x = float(place.x_mm) * _MM_TO_MIL      # **统一换成 mil**
            y = float(place.y_mm) * _MM_TO_MIL
            half_w = pw / 2.0                        # `_size_mil` 已经是 mil
            half_h = ph / 2.0
            if _is_connector(part):
                # 判据：连接器本体必须**跨过**某条板边（外壳悬伸出板外），
                # 否则外壳整个在板内 → 线缆插不进去（手册 §14.3 第 1 条）。
                edge = connector_edge(part, board)
                near = {"left": x, "right": w * _MM_TO_MIL - x,
                        "bottom": y, "top": h * _MM_TO_MIL - y}[edge]
                half_toward = half_w if edge in ("left", "right") else half_h
                if near > half_toward + 5.0:
                    report.findings.append(Finding(
                        code="connector_in_board",
                        board=str(getattr(board, "name", "")),
                        refs=[str(part.ref)],
                        detail=(f"连接器中心离{edge}边 {near:.0f} mil，"
                                f"而半宽只有 {half_toward:.0f} mil —— "
                                "本体没跨过板边，外壳会整个留在板内"),
                        severity="fail"))
                continue
            inside = (x - half_w >= EDGE_MARGIN_MIL
                      and y - half_h >= EDGE_MARGIN_MIL
                      and (w * _MM_TO_MIL - x) - half_w >= EDGE_MARGIN_MIL
                      and (h * _MM_TO_MIL - y) - half_h >= EDGE_MARGIN_MIL)
            if not inside:
                report.findings.append(Finding(
                    code="outside",
                    board=str(getattr(board, "name", "")),
                    refs=[str(part.ref)],
                    detail=(f"({x / _MM_TO_MIL:.1f},{y / _MM_TO_MIL:.1f}) mm 的"
                            f"封装占位超出板内 {EDGE_MARGIN_MIL:.0f} mil 边距"),
                    severity="fail"))
        # 2) 重叠 / 中心距
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                pa, (wa, ha) = boxes[i]
                pb, (wb, hb) = boxes[j]
                pa_place = getattr(pa, "placement", None)
                pb_place = getattr(pb, "placement", None)
                if pa_place is None or pb_place is None:
                    continue
                dx = abs(float(pa_place.x_mm) - float(pb_place.x_mm)) * _MM_TO_MIL
                dy = abs(float(pa_place.y_mm) - float(pb_place.y_mm)) * _MM_TO_MIL
                overlap = (dx < (wa + wb) / 2.0) and (dy < (ha + hb) / 2.0)
                center = math.hypot(dx, dy)
                if overlap:
                    report.findings.append(Finding(
                        code="overlap", board=str(getattr(board, "name", "")),
                        refs=[str(pa.ref), str(pb.ref)],
                        detail=(f"封装占位重叠（dx={dx:.0f} mil, dy={dy:.0f} mil；"
                                f"半宽和={((wa + wb) / 2.0):.0f}/"
                                f"{((ha + hb) / 2.0):.0f}）"),
                        severity="fail"))
                elif center < MIN_CENTER_MIL:
                    report.findings.append(Finding(
                        code="too_close", board=str(getattr(board, "name", "")),
                        refs=[str(pa.ref), str(pb.ref)],
                        detail=(f"中心距 {center:.0f} mil < {MIN_CENTER_MIL:.0f} "
                                "（手册 §17.19：0603 相邻 70~80 mil 就焊盘重叠）"),
                        severity="fail"))
    return report


def autoplace(design: Any, *, board: Any = None,
              margin_mm: float = 2.0) -> dict[str, tuple[float, float]]:
    """给一块板重新排一个**守规则**的栅格。返回 `{ref: (x_mm, y_mm)}`。

    只在需要时用（`emit --autolayout`）：它按封装占位的大小分列排布，
    列宽/行距都留出 `MIN_CENTER_MIL` 的余量，**连接器单独贴边**。

    为什么不做"全局最优摆放"：那是 EDA 里人做的事，且要考虑电气分区、
    散热、装配顺序。这里只保证**不会因为摆放直接短路**。
    """
    target = board or design.board_list()[0]
    w = float(getattr(target, "size_mm", {}).get("w") or 0.0)
    h = float(getattr(target, "size_mm", {}).get("h") or 0.0)
    if w <= 0 or h <= 0:
        return {}
    parts = [p for p in design.parts if design.board_of(p) is target]
    sizeable = []
    for part in parts:
        size = _size_mil(part)
        if size is None:
            continue
        sizeable.append((part, size))
    out: dict[str, tuple[float, float]] = {}
    # 连接器贴左边，纵向排开
    connectors = [(p, s) for p, s in sizeable if _is_connector(p)]
    y_cursor = margin_mm
    for part, (pw, ph) in connectors:
        # 中心放在**板边线上偏内半宽**的位置：本体跨过板边，外壳悬伸出板外
        x = round(pw / 2.0 / _MM_TO_MIL * 0.6, 2)
        out[str(part.ref)] = (max(0.5, x), round(y_cursor, 2))
        y_cursor += ph / _MM_TO_MIL + 4.0
    # 其余按列排：列宽 = 最宽件 + 安全间距
    rest = [(p, s) for p, s in sizeable if not _is_connector(p)]
    if not rest:
        return out
    col_w = max(s[0] for _p, s in rest) / _MM_TO_MIL + MIN_CENTER_MIL / _MM_TO_MIL
    row_h = max(s[1] for _p, s in rest) / _MM_TO_MIL + MIN_CENTER_MIL / _MM_TO_MIL
    # 给连接器留出一整列，别让无源件压到它身上（第一版 x0 只加了 3mm，
    # 6mm 宽的连接器和无源件直接重叠）
    conn_col = (max(pw for _p, (pw, _h) in connectors) / _MM_TO_MIL
                + MIN_CENTER_MIL / _MM_TO_MIL / 2.0) if connectors else 0.0
    x0 = margin_mm + conn_col
    y0 = margin_mm
    per_col = max(1, int((h - 2 * margin_mm) // row_h))
    for idx, (part, _s) in enumerate(rest):
        col = idx // per_col
        row = idx % per_col
        x = x0 + col * col_w
        y = y0 + row * row_h
        if x > w - margin_mm or y > h - margin_mm:
            # 放不下就**不硬塞**：超出的那些不进结果，由调用方报出来
            continue
        out[str(part.ref)] = (round(x, 2), round(y, 2))
    return out