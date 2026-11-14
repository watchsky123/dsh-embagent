"""交叉核对：画图几何 vs 物理事实，以及 2D↔3D 一致性。

两条来源不同的信息放在一起比对，差异要能定位到 ``ref``：

* **画图**：旧 Velxio 画布的像素（``electrical.legacy_px``）
* **事实**：``size_mm`` / 数据手册 / catalog
* **复用**：``hwforge.crosscheck`` 的 overlay 判定（Wokwi 引脚间距 vs 数据手册）

原则：**"未采购"与"矛盾"分开**。没有数据的件进 ``not_verified``，不进
``mismatches``——否则一个还没买传感器的项目会被报成"尺寸矛盾"。
"""

from __future__ import annotations

import sys
from typing import Any

from .model import DesignModel


def _hwforge_crosscheck(overlay: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from ..bridge import hwforge_root
        root = hwforge_root()
        if root and root not in sys.path:
            sys.path.insert(0, root)
    except Exception:  # noqa: BLE001
        pass
    try:
        from hwforge import crosscheck as crosscheck_module  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return [{
            "kind": "overlay/unavailable", "severity": "info", "refs": [],
            "message": f"hwforge.crosscheck 不可用（{type(exc).__name__}: {exc}）",
        }]
    findings = crosscheck_module.crosscheck(overlay)
    out: list[dict[str, Any]] = []
    for finding in findings:
        out.append({
            "kind": getattr(finding, "kind", "overlay"),
            "severity": "error" if getattr(finding, "blocking", False) else "warning",
            "refs": list(getattr(finding, "refs", []) or []),
            "message": getattr(finding, "message", str(finding)),
            "measured": getattr(finding, "measured", None),
            "expected": getattr(finding, "expected", None),
        })
    return out


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
        mismatches.extend(_hwforge_crosscheck(overlay))

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
