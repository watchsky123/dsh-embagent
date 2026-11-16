"""交叉核对：画图几何 vs 物理事实，以及 2D↔3D 一致性。

两条来源不同的信息放在一起比对，差异要能定位到 ``ref``：

* **画图**：旧 Velxio 画布的像素（``electrical.legacy_px``）
* **事实**：``size_mm`` / 数据手册 / catalog
* **本地**：overlay 判定（画图外框 vs 数据手册、引脚间距）

原则：**"未采购"与"矛盾"分开**。没有数据的件进 ``not_verified``，不进
``mismatches``——否则一个还没买传感器的项目会被报成"尺寸矛盾"。
"""

from __future__ import annotations

import sys
from typing import Any

from .model import DesignModel


def _overlay_crosscheck(overlay: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """overlay 交叉核对（**本地实现**，不依赖 hwforge）。

    ## overlay 是什么

    一份**外部补全数据**：把某个元件在画图工具里的声明尺寸/引脚，与
    数据手册（`partfacts`）的数值并排放，看有没有矛盾。

    期望的形状（缺字段就跳过，不报矛盾）::

        {"parts": {
            "<element>": {
                "wokwi_outline_mm": {"x": .., "y": .., "z": ..},
                "wokwi_pins": {"<name>": [x, y], ...},
                "overlay": {"size_mm": {"x": {"value": ..}, "y": {...}}},
            }
        }}

    ## 纪律

    **只有两侧都有数字时才能报矛盾。** 一侧缺失进 `not_verified` ——
    "没数据"不是"对不上"。
    """
    findings: list[dict[str, Any]] = []
    not_verified: list[str] = []

    parts = (overlay or {}).get("parts")
    if not isinstance(parts, dict) or not parts:
        return ([{"kind": "overlay/empty", "severity": "info", "refs": [],
                  "message": "overlay 里没有 parts，跳过", "measured": None,
                  "expected": None}], [])

    def _num(block: Any, key: str) -> float | None:
        if not isinstance(block, dict):
            return None
        value = block.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    for element, rec in sorted(parts.items()):
        if not isinstance(rec, dict):
            not_verified.append(f"{element}: overlay 条目不是对象")
            continue
        facts = rec.get("overlay")
        drawn = rec.get("wokwi_outline_mm") or {}

        # XC-05：有没有 Z。**没有 Z 不能默认为 0** —— 那会让 3D 里所有件
        # 都变成零厚度。
        z_fact = _num((facts or {}).get("size_mm"), "z") if facts else None
        if z_fact is None:
            not_verified.append(
                f"{element}: 没有 Z 尺寸，3D 里不能可靠放置（不默认为 0）")

        if not facts:
            not_verified.append(
                f"{element}: 没有补全数据（不是「它简单」，是没考证）")
            continue

        # XC-01：画图外框 vs 数据手册本体
        for axis in ("x", "y"):
            drawn_mm = drawn.get(axis)
            fact_mm = _num(facts.get("size_mm"), axis)
            if drawn_mm is None or fact_mm is None:
                continue
            try:
                delta = abs(float(drawn_mm) - fact_mm)
            except (TypeError, ValueError):
                not_verified.append(f"{element}: {axis} 尺寸不是数字")
                continue
            # 画图外框含焊盘外沿，通常比本体大；只报**画图比本体小**
            # 或大出 3mm 以上的情况。
            tolerance = 3.0
            if float(drawn_mm) < fact_mm - 0.05 or delta > tolerance:
                findings.append({
                    "kind": "overlay/outline-vs-body",
                    "severity": "warning",
                    "refs": [element],
                    "message": (
                        f"{element}: 画图 {axis}={float(drawn_mm):.2f}mm，"
                        f"数据手册 {axis}={fact_mm:.2f}mm，差 {delta:.2f}mm"),
                    "measured": {"drawn_mm": float(drawn_mm)},
                    "expected": {"datasheet_mm": fact_mm},
                })

        # XC-04：引脚最小间距（只核对"是否过分密集"，不判对错）
        pins = rec.get("wokwi_pins")
        if isinstance(pins, dict) and len(pins) >= 2:
            coords = [tuple(v) for v in pins.values()
                      if isinstance(v, (list, tuple)) and len(v) >= 2]
            distances = []
            for a in range(len(coords)):
                for b in range(a + 1, len(coords)):
                    dx = coords[a][0] - coords[b][0]
                    dy = coords[a][1] - coords[b][1]
                    d = (dx * dx + dy * dy) ** 0.5
                    if d > 1e-6:
                        distances.append(d)
            if distances and min(distances) < 0.5:
                findings.append({
                    "kind": "overlay/pin-spacing",
                    "severity": "warning",
                    "refs": [element],
                    "message": (
                        f"{element}: 两个引脚间距只有 {min(distances):.2f}mm，"
                        f"实际排针做不到"),
                    "measured": {"min_spacing_mm": round(min(distances), 3)},
                    "expected": {"min_spacing_mm": 0.5},
                })

    return (findings, not_verified)


def run_crosscheck(design: DesignModel, *, overlay: dict[str, Any] | None = None) -> dict[str, Any]:
    from .projection import to_2d

    ppm = design.board.px_per_mm
    mismatches: list[dict[str, Any]] = []
    not_verified: list[str] = []

    # 1) 位置往返：mm -> 2D 投影 是否回到原画布像素。
    #    这不是同义反复——它验证整条换算链（含 Y 翻转）没有符号/比例错误。
    projected = {p["ref"]: p for p in to_2d(design)["parts"]}
    for part in design.parts:
        electrical = part.electrical if isinstance(part.electrical, dict) else {}
        legacy = electrical.get("legacy_px")
        if not isinstance(legacy, dict):
            continue
        current = projected.get(part.ref)
        if current is None:
            continue
        try:
            dx = abs(float(current["x_px"]) - float(legacy.get("x", 0)))
            dy = abs(float(current["y_px"]) - float(legacy.get("y", 0)))
        except (TypeError, ValueError):
            not_verified.append(f"{part.ref}: 画图像素不是数字，无法往返核对")
            continue
        if dx > 1.5 or dy > 1.5:
            mismatches.append({
                "kind": "consistency/pixel-roundtrip",
                "severity": "warning",
                "refs": [part.ref],
                "message": (
                    f"{part.ref}: mm→2D 投影回到 ({current['x_px']:.1f},"
                    f"{current['y_px']:.1f})px，原画布 ({legacy.get('x')},"
                    f"{legacy.get('y')})px，差 {dx:.1f}/{dy:.1f}px"),
                "measured": {"projected_px": [current["x_px"], current["y_px"]]},
                "expected": {"legacy_px": [legacy.get("x"), legacy.get("y")]},
            })

    # 2) 画图尺寸 vs 物理尺寸（只有来源真的记录了像素尺寸时才可比）
    for part in design.parts:
        electrical = part.electrical if isinstance(part.electrical, dict) else {}
        legacy = electrical.get("legacy_px")
        size = part.size_mm
        if not isinstance(legacy, dict):
            if size["w"] <= 0 or size["h"] <= 0:
                not_verified.append(f"{part.ref}: 没有物理尺寸，也没有画图尺寸")
            elif part.physical_source == "estimated":
                not_verified.append(
                    f"{part.ref}: 物理尺寸是估算值，无法与画图尺寸交叉核对")
            continue

        drawn_w = legacy.get("w_px")
        drawn_h = legacy.get("h_px")
        if drawn_w is None or drawn_h is None:
            not_verified.append(
                f"{part.ref}: Velxio 画布不记录元件像素尺寸（渲染尺寸来自 DOM），"
                "只能核对位置")
            continue
        try:
            drawn_w_mm = float(drawn_w) / ppm
            drawn_h_mm = float(drawn_h) / ppm
        except (TypeError, ValueError):
            not_verified.append(f"{part.ref}: 画图像素尺寸不是数字")
            continue

        if size["w"] <= 0 or size["h"] <= 0:
            not_verified.append(
                f"{part.ref}: 画图 {drawn_w_mm:.1f}×{drawn_h_mm:.1f} mm，"
                "但没有物理尺寸可核对")
            continue
        if part.physical_source not in ("datasheet", "measured"):
            not_verified.append(
                f"{part.ref}: 物理尺寸来源是 {part.physical_source}，"
                "与画图比较不构成「矛盾」证据")
            continue

        delta_w = abs(drawn_w_mm - size["w"])
        delta_h = abs(drawn_h_mm - size["h"])
        tolerance = max(2.0, 0.15 * max(size["w"], size["h"], 1.0))
        if delta_w > tolerance or delta_h > tolerance:
            mismatches.append({
                "kind": "consistency/pixel-vs-mm",
                "severity": "warning",
                "refs": [part.ref],
                "message": (
                    f"{part.ref}: 画图按 {ppm:.3f} px/mm 折合 "
                    f"{drawn_w_mm:.1f}×{drawn_h_mm:.1f} mm，"
                    f"物理尺寸 {size['w']:.1f}×{size['h']:.1f} mm，"
                    f"相差 {delta_w:.1f}/{delta_h:.1f} mm（容差 {tolerance:.1f}）"),
                "measured": {"drawn_mm": [round(drawn_w_mm, 2), round(drawn_h_mm, 2)]},
                "expected": {"size_mm": [size["w"], size["h"]]},
            })

    for entry in design.nets:
        pins = entry.get("pins", [])
        if len(pins) < 2:
            mismatches.append({
                "kind": "consistency/net",
                "severity": "error",
                "refs": [str(p.get("ref")) for p in pins if p.get("ref")],
                "message": f"net {entry.get('net')} 只有 {len(pins)} 个引脚，不构成连接",
                "measured": len(pins), "expected": ">=2",
            })

    if overlay is not None:
        overlay_findings, overlay_unverified = _overlay_crosscheck(overlay)
        mismatches.extend(overlay_findings)
        not_verified.extend(overlay_unverified)

    errors = [m for m in mismatches if m.get("severity") == "error"]
    warnings = [m for m in mismatches if m.get("severity") == "warning"]
    return {
        "ok": not errors,
        "px_per_mm": ppm,
        "mismatches": mismatches,
        "not_verified": not_verified,
        "summary": {
            "mismatches": len(mismatches),
            "errors": len(errors),
            "warnings": len(warnings),
            "unverified": len(not_verified),
            "refs": sorted({r for m in mismatches for r in m.get("refs", [])}),
        },
        "note": "未采购/无数据进 not_verified，不当作矛盾；矛盾必须有两侧数字",
    }
