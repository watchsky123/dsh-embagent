"""`design.json` → `scene.json` —— 打通工作台与 hwforge 的判定/仿真链。

## 为什么需要这个转换

两套形状描述**互不兼容**，实测确认：

| 工作台 `design.json` | hwforge `scene.json` |
|---|---|
| `parts[]` + `placement.x_mm`（mm） | `space.placements[]` + `box{x0,y0,z0,nx,ny,nz}`（体素） |
| `nets[]`（网 → 引脚） | `wires.runs[]`（每条连线 → 起终点体素） |
| `board.outline`（mm 多边形） | `space.dims` + `policy` |
| — | `assembly`（target/install/sources，`inventory.bom` 靠它） |

`hwforge simulate` 只认 `scene.json`，所以**在工作台里建的板子进不了仿真**。
这个模块把两者对齐。

## 单位与坐标

- 体素 = `round(mm / pitch)`，pitch 默认 0.5mm（与 hwforge 一致）。
- `placement.x_mm`/`y_mm` 在 `Part.footprint_mm()` 里是**足迹左下角**
  （`cx = x + w/2`，`x0 = cx - w/2 = x`），所以 `box.x0 = round(x/pitch)` 直接对齐。
- 高度方向从板面 z=0 起算。

## 什么是"如实转换"、什么不是

`wires.runs` 的端点**只到元件中心**，因为 `design.json` 里没有焊盘坐标
（导入器能拿到焊盘号，但拿不到每个焊盘的 mm 位置）。所以 `length` 是
**中心距的体素化**，不是真实走线长度。

这不影响 `simulate` 的用途（它读 `assembly` 与 `inventory.bom` 判电流/时序），
但会影响任何"看走线长度"的解读。所以 `meta.route_note` 里写明这一点，
且 `wires.estimated=True` —— 不让下游把中心距当走线长度。

@module hw_velxio.design.scene
"""

from __future__ import annotations

import math
from typing import Any

from .model import DesignModel

DEFAULT_PITCH_MM = 0.5
DEFAULT_MAX_HEIGHT_MM = 12.0

# 与 `physical.py` 的常量保持一致：转换出来的 scene 若用另一套策略，
# 两个引擎会对同一个设计给出不同判定。
MAX_NET_REACH_MM = 30.0
CONNECTOR_EDGE_GAP_MM = 3.0


def _vox(mm: float, pitch: float) -> int:
    return int(round(float(mm) / pitch))


def _part_box(part: Any, pitch: float) -> dict[str, int]:
    """元件 AABB → 体素 box。宽高为 0 时至少占 1 个体素（否则会"消失"）。"""
    x0, y0, x1, y1 = part.footprint_mm()
    z0 = float(getattr(part.placement, "z_mm", 0.0) or 0.0)
    z1 = z0 + float(part.size_mm.get("d", 0.0) or 0.0)
    bx0, by0, bz0 = _vox(x0, pitch), _vox(y0, pitch), _vox(z0, pitch)
    bx1 = max(_vox(x1, pitch), bx0 + 1)
    by1 = max(_vox(y1, pitch), by0 + 1)
    bz1 = max(_vox(z1, pitch), bz0 + 1)
    return {"x0": bx0, "y0": by0, "z0": bz0,
            "nx": bx1 - bx0, "ny": by1 - by0, "nz": bz1 - bz0}


def _net_runs(design: DesignModel, parts_by_ref: dict[str, Any],
              pitch: float) -> list[dict[str, Any]]:
    """把网表转成连线。用**最小生成树**连接同一网络的引脚，不是全连接。

    全连接会让 n 个引脚产生 n(n-1)/2 条线，长度统计会虚高；
    MST 给出"能连通的最短"上界，对判定够用，且不会伪造冗余。
    """
    runs: list[dict[str, Any]] = []

    def centre(ref: str) -> tuple[float, float, float] | None:
        part = parts_by_ref.get(ref)
        if part is None:
            return None
        x0, y0, x1, y1 = part.footprint_mm()
        z = float(getattr(part.placement, "z_mm", 0.0) or 0.0)
        half_d = float(part.size_mm.get("d", 0.0) or 0.0) / 2.0
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0, z + half_d)

    for entry in design.nets or []:
        net_name = str(entry.get("net") or "")
        pin_list = [p for p in (entry.get("pins") or [])
                    if isinstance(p, dict) and p.get("ref")]
        nodes: list[tuple[str, str, tuple[float, float, float]]] = []
        for pin in pin_list:
            ref = str(pin.get("ref"))
            point = centre(ref)
            if point is None:
                continue
            nodes.append((ref, str(pin.get("pin") or ""), point))
        if len(nodes) < 2:
            continue

        # Prim MST（点数很小，O(n²) 足够）
        used = {0}
        edges: list[tuple[float, int, int]] = []
        while len(used) < len(nodes):
            best: tuple[float, int, int] | None = None
            for i in used:
                for j in range(len(nodes)):
                    if j in used:
                        continue
                    dist = math.dist(nodes[i][2], nodes[j][2])
                    if best is None or dist < best[0]:
                        best = (dist, i, j)
            if best is None:
                break
            used.add(best[2])
            edges.append(best)

        for dist, i, j in edges:
            a, b = nodes[i], nodes[j]
            va = [_vox(v, pitch) for v in a[2]]
            vb = [_vox(v, pitch) for v in b[2]]
            runs.append({
                "net": net_name,
                "length": abs(va[0] - vb[0]) + abs(va[1] - vb[1])
                          + abs(va[2] - vb[2]),
                "length_mm": round(dist, 3),
                "layer": "top",
                "from": {"ref": a[0], "pin": a[1], "voxel": va},
                "to": {"ref": b[0], "pin": b[1], "voxel": vb},
                "vias": [],
                "endpoints": [va, vb],
                # 端点是**元件中心**，不是焊盘 —— 见模块文档。
                "approx": "component_centre",
            })
    return runs


def to_scene(design: DesignModel, *, pitch_mm: float = DEFAULT_PITCH_MM,
             max_height_mm: float | None = None,
             target: str = "") -> dict[str, Any]:
    """把设计转成 hwforge 能读的 `scene.json`。

    返回的结构与 `hwforge assemble` 产出一致（`space` / `wires` / `assembly` /
    `unverified`），下游 `simulate`、`electrical`、`coverify` 可以直接读。
    """
    board = design.board
    width = float(board.size_mm.get("w", 0.0) or 0.0)
    height = float(board.size_mm.get("h", 0.0) or 0.0)

    parts_by_ref = {p.ref: p for p in design.parts}
    placements: list[dict[str, Any]] = []
    highest = 0.0
    for part in design.parts:
        box = _part_box(part, pitch_mm)
        top_mm = (box["z0"] + box["nz"]) * pitch_mm
        highest = max(highest, top_mm)
        placements.append({
            "ref": part.ref,
            "kind": part.role or "component",
            "box": box,
            "nets": sorted({str(entry.get("net") or "")
                            for entry in design.nets or []
                            if any(str(p.get("ref")) == part.ref
                                   for p in (entry.get("pins") or []))}),
            "meta": {
                # `inventory.bom()` 只认 `meta.role`，没它就整件被跳过，
                # 于是仿真里的电流预算会少算一整类器件。
                #
                # **但两套 role 词汇不同**：工作台的 role 是结构性的
                # （component/module/connector），hwforge 的是功能性的
                # （servo/regulator_5v/camera），后者才映射到每条轨的电流。
                # 所以这里优先用作者显式给的 `electrical.rail`，没有就用结构性
                # role —— 下游会如实报 `load_complete=false`，而不是把它当 0。
                "role": (part.electrical or {}).get("rail") or part.role or "component",
                "structural_role": part.role or "component",
                "source": part.physical_source,
                "kind": part.kind,
                "label": part.label,
                "lcsc": part.lcsc or "",
            },
        })

    limit_h = float(max_height_mm if max_height_mm is not None
                    else DEFAULT_MAX_HEIGHT_MM)
    dims = [
        max(1, _vox(width, pitch_mm)),
        max(1, _vox(height, pitch_mm)),
        max(1, _vox(max(limit_h, highest, 1.0), pitch_mm)),
    ]

    runs = _net_runs(design, parts_by_ref, pitch_mm)
    net_names = sorted({str(entry.get("net") or "") for entry in design.nets or []})

    # 未验证项：设计侧已经知道的缺口，原样带过去，不在这里"补齐"。
    unverified: list[str] = []
    unsized = [p.ref for p in design.parts
               if p.size_mm.get("w", 0) <= 0 or p.size_mm.get("h", 0) <= 0]
    if unsized:
        unverified.append(
            f"{len(unsized)} 个元件无尺寸，体积类判定对它们无效"
            f"（{', '.join(unsized[:8])}{'…' if len(unsized) > 8 else ''}）")
    estimated = [p.ref for p in design.parts if p.physical_source == "estimated"]
    if estimated:
        unverified.append(
            f"{len(estimated)} 个元件尺寸为估算值（{', '.join(estimated[:8])}"
            f"{'…' if len(estimated) > 8 else ''}）")
    if not placements:
        unverified.append("没有任何元件：场景为空，所有判定都无从谈起")
    rail_mapped = sum(1 for p in design.parts
                      if (p.electrical or {}).get("rail"))
    if design.parts and rail_mapped == 0:
        unverified.append(
            "电源轨归属：没有任何元件声明 `electrical.rail`，"
            "按电流计的负载只能算已知件，结果是**下界**不是实测值")

    return {
        "space": {
            "dims": dims,
            "pitch_mm": pitch_mm,
            "policy": {
                "allow_overlap_voxels": 0,
                "max_net_reach_mm": MAX_NET_REACH_MM,
                "max_connector_edge_gap_mm": CONNECTOR_EDGE_GAP_MM,
                "keepout_must_be_empty": True,
                "max_height_mm": limit_h,
                "min_test_points": 2,
            },
            "placements": placements,
            "occupied_voxels": sum(p["box"]["nx"] * p["box"]["ny"] * p["box"]["nz"]
                                   for p in placements),
            "density": round(
                sum(p["box"]["nx"] * p["box"]["ny"] * p["box"]["nz"]
                    for p in placements)
                / max(1, dims[0] * dims[1] * dims[2]), 4),
        },
        "wires": {
            "pitch_mm": pitch_mm,
            "policy": {"max_net_reach_mm": MAX_NET_REACH_MM},
            "nets": net_names,
            "runs": runs,
            "voxel_count": sum(run["length"] for run in runs),
            # 端点只到元件中心 —— 下游别把它当真实走线长度。
            "estimated": True,
            "route_note": (
                "端点取自元件中心（design.json 没有焊盘坐标），"
                "length 是中心距的体素化，不是走线长度。"),
        },
        "marks": [],
        "cables": [],
        "unverified": unverified,
        "assembly": {
            "target": target or design.id,
            "install": "board",
            "sources": {p.ref: p.physical_source for p in design.parts},
            "unsourced_parts": estimated,
            "cables_unrouted": [],
            # 下游可据此知道这是"工作台导出的场景"，不是 hwforge 装配出来的。
            "origin": "hw_velxio.design.json",
            "design_id": design.id,
        },
    }


def summary(scene: dict[str, Any]) -> dict[str, Any]:
    space = scene.get("space") or {}
    wires = scene.get("wires") or {}
    return {
        "dims_voxels": space.get("dims"),
        "pitch_mm": space.get("pitch_mm"),
        "placements": len(space.get("placements") or []),
        "nets": len(wires.get("nets") or []),
        "runs": len(wires.get("runs") or []),
        "unverified": len(scene.get("unverified") or []),
    }