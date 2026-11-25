"""外壳（参数化腔体）—— 开口校验 + 2D/3D 投影。

## 尺寸语义

见 `model.Enclosure` 的 docstring（`inner.d` 是**板顶以上**的可用高度，
`physical._declared_height_limit` 拿它当限高）。这里只补两件事：

1. **开口的面内坐标**：`openings[].x_mm/y_mm` 是**开孔中心**。
   * `top` / `bottom` —— 用**拼板坐标**（与 `projection.to_2d` 同一套）：
     `x` 向右、`y` 向深，原点在拼板左下角。这样"USB 座在哪，孔就开在
     它正上方"可以直接从零件坐标推。
   * `front` / `back` / `left` / `right` —— `x` 沿该面的水平方向（宽或深），
     `y` 从**内腔底面**向上。板底在 `floor_z_mm`，板顶再往上一个板厚。
2. **三态**：没声明外壳 → `not_ready`；声明了但没开口 → `unknown`
   （"没开口"可能是密封盒，也可能是没填）；越界 → `fail`。**空不是通过。**
"""

from __future__ import annotations

from typing import Any

from .model import ENCLOSURE_FACES

#: 拼板与内腔之间至少要留的装配间隙（每边）。**这是判据的默认值**，
#: 可以被 `evaluate(clearance_mm=...)` 改，但不会不告而用。
DEFAULT_CLEARANCE_MM = 0.5


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def board_of(design: Any):
    """主参考板。外壳围着**主参考板**建（多板拼板时内腔按拼板尺寸判）。"""
    return design.board


def board_thickness(design: Any) -> float:
    return _f(board_of(design).thickness_mm, 1.6)


def panel_size(design: Any) -> tuple[float, float]:
    from .projection import panel_layout
    size = panel_layout(design)["size_mm"]
    return _f(size.get("w")), _f(size.get("h"))


def board_offset(design: Any) -> tuple[float, float]:
    """板原点在内腔里的位置：默认居中，不存字段（派生量不进文件）。"""
    enc = design.enclosure
    if enc is None:
        return (0.0, 0.0)
    inner_w = _f(enc.inner_mm.get("w"))
    inner_h = _f(enc.inner_mm.get("h"))
    panel_w, panel_h = panel_size(design)
    return (max(0.0, (inner_w - panel_w) / 2.0),
            max(0.0, (inner_h - panel_h) / 2.0))


def normalize_opening(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "face": str(row.get("face") or "").strip(),
        "x_mm": round(_f(row.get("x_mm")), 4),
        "y_mm": round(_f(row.get("y_mm")), 4),
        "w_mm": round(_f(row.get("w_mm")), 4),
        "h_mm": round(_f(row.get("h_mm")), 4),
        "kind": str(row.get("kind") or "user"),
    }


def _face_size(design: Any, face: str) -> tuple[float, float]:
    """面的 (水平, 垂直) 尺寸。垂直方向对侧面是"内腔底面向上"的高度。"""
    enc = design.enclosure
    if enc is None:
        return (0.0, 0.0)
    inner = {k: _f(enc.inner_mm.get(k)) for k in ("w", "h", "d")}
    if face in ("top", "bottom"):
        return (inner["w"], inner["h"])
    if face in ("front", "back"):
        return (inner["w"], enc.floor_z_mm + board_thickness(design) + inner["d"])
    if face in ("left", "right"):
        return (inner["h"], enc.floor_z_mm + board_thickness(design) + inner["d"])
    return (0.0, 0.0)


def opening_problem(design: Any, opening: dict[str, Any]) -> str | None:
    """一个开口越界没有。返回一句人话（None = 在面内）。"""
    face = opening["face"]
    if face not in ENCLOSURE_FACES:
        return (f"开口 face={face!r} 未知（可用：{', '.join(ENCLOSURE_FACES)}）")
    w, h = opening["w_mm"], opening["h_mm"]
    if w <= 0 or h <= 0:
        return f"开口 {face} 的 w/h 必须为正（收到 {w}×{h} mm）"
    horiz, vert = _face_size(design, face)
    ox, oy = 0.0, 0.0
    x, y = opening["x_mm"], opening["y_mm"]
    if face in ("top", "bottom"):
        # 板坐标 → 内腔坐标
        ox, oy = board_offset(design)
        x, y = x + ox, y + oy
    if w / 2 > x or x + w / 2 > horiz:
        return (f"开口 {face} 横向越界：中心 x={opening['x_mm']} mm、宽 {w} mm，"
                f"面宽 {horiz} mm")
    if h / 2 > y or y + h / 2 > vert:
        return (f"开口 {face} 纵向越界：中心 y={opening['y_mm']} mm、高 {h} mm，"
                f"面高 {vert} mm")
    return None


def structure_problems(design: Any) -> list[str]:
    """**结构**问题（进 `DesignModel.validate()`，`put` 会拒收）。"""
    enc = design.enclosure
    if enc is None:
        return []
    out: list[str] = []
    inner = {k: _f(enc.inner_mm.get(k)) for k in ("w", "h", "d")}
    if enc.wall_mm <= 0:
        out.append(f"enclosure.wall_mm 必须为正（收到 {enc.wall_mm}）")
    if enc.lid_mm < 0:
        out.append(f"enclosure.lid_mm 不能为负（收到 {enc.lid_mm}）")
    if enc.floor_z_mm < 0:
        out.append(f"enclosure.floor_z_mm 不能为负（收到 {enc.floor_z_mm}）")
    for key in ("w", "h", "d"):
        if inner[key] < 0:
            out.append(f"enclosure.inner_mm.{key} 不能为负")
    if all(inner[k] <= 0 for k in inner):
        out.append("enclosure.inner_mm 全为 0（外壳没有内腔，等于没建）")
    seen: set[tuple[str, int, int]] = set()
    for row in enc.openings:
        if not isinstance(row, dict):
            out.append("enclosure.openings 里有一项不是对象")
            continue
        opening = normalize_opening(row)
        message = opening_problem(design, opening)
        if message is not None:
            out.append(message)
        key = (opening["face"], round(opening["x_mm"] * 10),
               round(opening["y_mm"] * 10))
        if key in seen:
            out.append(f"开口重复：{opening['face']} "
                       f"({opening['x_mm']}, {opening['y_mm']}) 声明了两次")
        seen.add(key)
    return out


# ---------------------------------------------------------------------------
# 三态评估
# ---------------------------------------------------------------------------

def _row(check: str, status: str, message: str, *, measured: Any = None,
         limit: Any = None, refs: list[str] | None = None,
         basis: str = "") -> dict[str, Any]:
    return {"check": check, "status": status, "message": message,
            "measured": measured, "limit": limit, "refs": refs or [],
            "basis": basis}


def evaluate(design: Any, *, clearance_mm: float = DEFAULT_CLEARANCE_MM) -> dict[str, Any]:
    """外壳的判据。三态：`pass` / `fail` / `unknown`，缺数据不是 pass。"""
    enc = design.enclosure
    if enc is None:
        return {
            "status": "not_ready",
            "rows": [_row("shell.enclosure", "unknown",
                          "没有声明外壳（`enclosure` 为空）——未核对，不是通过")],
            "summary": "没有外壳：开口与内腔都没核对",
        }
    rows: list[dict[str, Any]] = []
    panel_w, panel_h = panel_size(design)
    inner_w = _f(enc.inner_mm.get("w"))
    inner_h = _f(enc.inner_mm.get("h"))
    inner_d = _f(enc.inner_mm.get("d"))

    # 1) 内腔放得下拼板
    if inner_w > 0 and inner_h > 0 and panel_w > 0 and panel_h > 0:
        need_w = panel_w + 2 * clearance_mm
        need_h = panel_h + 2 * clearance_mm
        ok = inner_w >= need_w - 1e-6 and inner_h >= need_h - 1e-6
        rows.append(_row(
            "shell.inner", "pass" if ok else "fail",
            (f"内腔 {inner_w:.1f}×{inner_h:.1f} mm 容得下拼板 "
             f"{panel_w:.1f}×{panel_h:.1f} mm（每边间隙 {clearance_mm} mm）"
             if ok else
             f"内腔 {inner_w:.1f}×{inner_h:.1f} mm 装不下拼板 "
             f"{panel_w:.1f}×{panel_h:.1f} mm（每边要 {clearance_mm} mm）"),
            measured={"w": round(inner_w, 2), "h": round(inner_h, 2)},
            limit={"w": round(need_w, 2), "h": round(need_h, 2)},
            basis="enclosure.inner_mm vs panel_layout"))
    else:
        rows.append(_row("shell.inner", "unknown",
                         "内腔或拼板尺寸缺失，装不装得下无法判断",
                         measured={"inner": [inner_w, inner_h],
                                  "panel": [panel_w, panel_h]},
                         basis="enclosure.inner_mm / projection.panel_layout"))

    # 2) 开口在面内
    openings = [normalize_opening(r) for r in enc.openings
                if isinstance(r, dict)]
    if not openings:
        rows.append(_row("shell.openings", "unknown",
                         "没有声明任何开口（密封盒可能是有意的，也可能是没填）",
                         basis="enclosure.openings"))
    else:
        bad = [(o, opening_problem(design, o)) for o in openings]
        bad = [(o, m) for o, m in bad if m is not None]
        rows.append(_row(
            "shell.openings", "fail" if bad else "pass",
            ("；".join(m for _o, m in bad) if bad
             else f"{len(openings)} 个开口都在面内"),
            measured={"openings": len(openings), "out_of_bounds": len(bad)},
            limit=0, basis="enclosure.openings vs 面尺寸"))

    # 3) 盖
    if enc.lid_mm > 0:
        rows.append(_row("shell.lid", "pass",
                         f"盖厚 {enc.lid_mm} mm（总高已计入）",
                         measured=enc.lid_mm, basis="enclosure.lid_mm"))
    else:
        rows.append(_row("shell.lid", "unknown",
                         "没有盖厚（`lid_mm=0`）：总高按「无盖」算，不是通过",
                         basis="enclosure.lid_mm"))

    # 4) 高度：**这里只报数，不判**（限高判据在 physical.space.height）。
    # 写成 pass 会把"量了个数"当成"核对过"，正是 AGENTS §0.1 那个形状。
    rows.append(_row("shell.height", "unknown",
                     (f"板顶以上可用高度 {inner_d:.1f} mm；"
                      "这里只报数，限高判据在 `space.height`" if inner_d > 0
                      else "内腔高度为 0：没有可用空间，也没法判"),
                     measured=inner_d, basis="enclosure.inner_mm.d"))

    status = ("failed" if any(r["status"] == "fail" for r in rows)
              else ("not_ready" if all(r["status"] == "unknown" for r in rows)
                    else "ok"))
    passed = sum(1 for r in rows if r["status"] == "pass")
    return {
        "status": status,
        "rows": rows,
        "passed": passed,
        "checked": len(rows),
        "outer_mm": enc.outer_mm(board_thickness(design)),
        "summary": f"{passed}/{len(rows)} 条通过"
                   + ("（其余是 unknown，不是通过）"
                      if any(r["status"] == "unknown" for r in rows) else ""),
    }


# ---------------------------------------------------------------------------
# 投影
# ---------------------------------------------------------------------------

def payload(design: Any) -> dict[str, Any] | None:
    """`scene3d` / `projection2d` 用的外壳几何。**没有外壳返回 None**。"""
    enc = design.enclosure
    if enc is None:
        return None
    thickness = board_thickness(design)
    ox, oy = board_offset(design)
    panel_w, panel_h = panel_size(design)
    return {
        "inner_mm": {k: round(_f(enc.inner_mm.get(k)), 4) for k in ("w", "h", "d")},
        "wall_mm": round(_f(enc.wall_mm, 2.0), 4),
        "lid_mm": round(_f(enc.lid_mm), 4),
        "floor_z_mm": round(_f(enc.floor_z_mm), 4),
        "board_thickness_mm": round(thickness, 4),
        "outer_mm": enc.outer_mm(thickness),
        "board_offset_mm": {"x": round(ox, 4), "y": round(oy, 4)},
        "panel_mm": {"w": round(panel_w, 4), "h": round(panel_h, 4)},
        "openings": [normalize_opening(r) for r in enc.openings
                     if isinstance(r, dict)],
        #: 面的可视尺寸（渲染层不必再推一遍）
        "faces_mm": {face: [round(v, 4) for v in _face_size(design, face)]
                     for face in ENCLOSURE_FACES},
    }


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.shell",
        "faces": list(ENCLOSURE_FACES),
        "default_clearance_mm": DEFAULT_CLEARANCE_MM,
        "coords": ("top/bottom 用拼板坐标；侧面 x 沿面水平、y 从内腔底面向上；"
                   "x_mm/y_mm 是开孔中心"),
        "discipline": ("没外壳 not_ready；没开口 unknown；越界 fail；"
                       "盖厚缺失 unknown，不按无盖当通过"),
    }