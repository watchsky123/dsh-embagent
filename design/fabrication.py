"""制造交接：从 ``DesignModel`` 导出可追溯的制造包，并如实报告缺什么。

**不做的事**：不伪造 Gerber。Gerber/钻孔需要真实的封装焊盘几何与布线，
本模型只有元件足迹与网表——直接从足迹"画"Gerber 就是在伪造制造文件。

**做的事**：把模型里已经确定的、制造端真正需要的输入导出来：

    bom.csv          参考号/值/封装/LCSC/数量
    positions.csv    贴片坐标（mm，含旋转与面）
    outline.dxf      板轮廓（LWPOLYLINE，可直接量尺寸）
    design.json      规范化模型（可追溯）
    README.txt       包内容 + 缺项 + 下一步

若项目里已经有 tscircuit 源（``sources/circuit.tsx``）且 CLI 可用，则调用
``design/toolchain`` 真跑；否则如实报 ``not_ready``。
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from .model import DesignModel


def _toolchain() -> Any:
    """外部工具探测（本地实现，不再依赖 hwforge.downstream）。"""
    from . import toolchain
    return toolchain


def probe() -> dict[str, Any]:
    """探测外部工具；缺失就如实报，绝不假装可用。"""
    toolchain = _toolchain()
    found = toolchain.discover()
    tools: dict[str, Any] = {}
    for name, status in (found.get("tools") or {}).items():
        tools[name] = {
            "available": bool(status.get("available")),
            "detail": str(status.get("reason") or status.get("version") or ""),
            "path": (status.get("command") or [None])[0],
        }
    return {"available": bool(found.get("available")), "tools": tools,
            "note": found.get("note", "")}


def _bom_csv(design: DesignModel) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Reference", "Value", "Kind", "Footprint", "LCSC", "Qty",
                     "PhysicalSource"])
    for part in design.parts:
        writer.writerow([
            part.ref,
            part.label,
            part.kind,
            part.electrical.get("footprint", ""),
            part.lcsc or "",
            1,
            part.physical_source,
        ])
    return buffer.getvalue()


def _positions_csv(design: DesignModel) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Reference", "X_mm", "Y_mm", "Z_mm", "Rotation", "Side",
                     "Width_mm", "Depth_mm", "Height_mm", "Source"])
    for part in design.parts:
        x0, y0, _x1, _y1 = part.footprint_mm()
        writer.writerow([
            part.ref,
            f"{part.center_mm()[0]:.3f}",
            f"{part.center_mm()[1]:.3f}",
            f"{part.placement.z_mm:.3f}",
            part.placement.rotation_z,
            "top" if part.placement.mount == "board" else part.placement.mount,
            f"{part.size_mm['w']:.3f}",
            f"{part.size_mm['h']:.3f}",
            f"{part.size_mm['d']:.3f}",
            part.physical_source,
        ])
    return buffer.getvalue()


def _outline_dxf(design: DesignModel) -> str:
    """最小 DXF：一个闭合 LWPOLYLINE。单位 mm（$INSUNITS=4）。"""
    points = design.board.outline
    lines = [
        "0", "SECTION", "2", "HEADER",
        "9", "$INSUNITS", "70", "4",
        "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
        "0", "LWPOLYLINE", "8", "BOARD_OUTLINE", "90", str(len(points)),
        "70", "1",
    ]
    for x, y in points:
        lines += ["10", f"{x:.4f}", "20", f"{y:.4f}"]
    lines += ["0", "ENDSEC", "0", "EOF", ""]
    return "\n".join(lines)


def _readme(design: DesignModel, probe_result: dict[str, Any],
            written: list[str], gate_message: str) -> str:
    tools = probe_result.get("tools", {})
    missing = ["Gerber / 钻孔：需要真实封装焊盘与布线，模型不含"]
    if not tools.get("tscircuit", {}).get("available"):
        missing.append("tscircuit：未探测到 CLI，无法生成布线 PCB")
    lines = [
        f"# 制造包 — {design.id}",
        "",
        f"板卡：{design.board.name}（{design.board.kind}）",
        f"轮廓：{design.board.size_mm['w']:.2f} × {design.board.size_mm['h']:.2f} mm，"
        f"厚 {design.board.thickness_mm:.2f} mm",
        f"元件：{len(design.parts)}，网表：{len(design.nets)}",
        "",
        "## 包含",
        *[f"- {name}" for name in written],
        "",
        "## 不含（如实声明）",
        *[f"- {item}" for item in missing],
        "",
        "## 交付闸门",
        f"- {gate_message}",
        "",
        "## 尺寸来源",
        *[f"- {p.ref}: {p.physical_source}"
          + ("（估算，不可当精确值）" if p.physical_source == "estimated" else "")
          for p in design.parts],
        "",
    ]
    return "\n".join(lines)


def export_bundle(design: DesignModel, out_dir: str | Path,
                  *, gate_message: str = "") -> dict[str, Any]:
    """写制造包。调用方必须先过 ``discipline.assert_deliverable``。"""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    (target / "design.json").write_text(
        json.dumps(design.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    written.append("design.json")

    (target / "bom.csv").write_text(_bom_csv(design), encoding="utf-8")
    written.append("bom.csv")

    (target / "positions.csv").write_text(_positions_csv(design), encoding="utf-8")
    written.append("positions.csv")

    (target / "outline.dxf").write_text(_outline_dxf(design), encoding="utf-8")
    written.append("outline.dxf")

    probe_result = probe()
    tscircuit_source = target / "sources" / "circuit.tsx"
    tscircuit: dict[str, Any] = {"ran": False, "reason": ""}
    if tscircuit_source.is_file() and probe_result["tools"].get(
            "tscircuit", {}).get("available"):
        try:
            toolchain = _toolchain()
            result = toolchain.export_fabrication(tscircuit_source, target / "fab")
            tscircuit = {"ran": True,
                         "ok": bool(getattr(result, "ok", False)),
                         "detail": str(getattr(result, "error", "")),
                         "files": list(getattr(result, "files", []) or [])}
            written.extend(tscircuit.get("files") or [])
        except Exception as exc:  # noqa: BLE001
            tscircuit = {"ran": False,
                         "reason": f"调用 tscircuit 失败：{type(exc).__name__}: {exc}"}
    else:
        if not tscircuit_source.is_file():
            tscircuit["reason"] = "项目里没有 sources/circuit.tsx，没有可交给 tscircuit 的源"
        else:
            tscircuit["reason"] = "tscircuit CLI 不可用"

    (target / "README.txt").write_text(
        _readme(design, probe_result, written, gate_message), encoding="utf-8")
    written.append("README.txt")

    return {
        "ok": True,
        "path": str(target),
        "written": written,
        "tools": probe_result["tools"],
        "tscircuit": tscircuit,
        "missing": [
            "Gerber / 钻孔（模型不含真实焊盘与布线，不伪造）",
            "装配图（需要外壳与三维模型）",
        ],
        "note": "制造包只包含模型能自洽导出的内容；缺项写进 README 与 missing",
    }


def copy_source(design: DesignModel, source: str | Path, out_dir: str | Path,
                *, name: str | None = None) -> Path:
    """把原始导入件放进 ``sources/``，保证制造包可追溯。"""
    target = Path(out_dir) / "sources"
    target.mkdir(parents=True, exist_ok=True)
    src = Path(source)
    dest = target / (name or src.name)
    shutil.copyfile(src, dest)
    return dest
