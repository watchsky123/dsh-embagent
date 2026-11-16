"""BOM 补全 —— 让"元件清单"变成视图里看得见的元件。

## 它解决什么

一个真实项目往往只有 BOM（或 BOM 比 PCB 工程更完整），而这个模型是
**单板**的、导入器只取一块板。于是视图里出现"141 个元件的板"或
"0 个元件的空板"，而用户手上明明有 45 个设计号的物料清单。

本模块把 BOM 补成 `Part[]`：

```
BOM 行 ──> {ref, value, footprint, lcsc}
             │
             ├─ packages.package_size(footprint) ──> w/h/d（实测表）
             └─ 标号前缀（C/R/U/L…）            ──> role
                        │
                        ▼
                 网格摆位（复用 synthesize._place）
                        ▼
                   Part(physical_source='derived')
```

## 纪律：位置是**排布**，不是**布局**

BOM 里没有坐标。补出来的元件只能按网格排（`synthesize._place`），
这是**估算位置**，不是真实 PCB 布局。

所以这里做三件事，一件都不省：

1. 每个补出来的元件标 `physical_source='derived'`（尺寸来自封装表推导）
   —— 尺寸可信，位置不可信；
2. `meta` 里写明 `placement='grid-estimate'`，`warnings` 里也写一句；
3. 界面上标题加"（BOM 清单 · 位置为自动排布）"，让人一眼知道这不是实际布局。

把排布位置当实际布局展示，比不显示更糟 —— 用户会照着它去布线。

## 与已有元件的关系

BOM 里**已经有**的 ref（来自 PCB 导入）**不覆盖**：那是真实布局，比排布
位置可信得多。本模块只补 `missing` 的那部分。

@module hw_velxio.design.bom_fill
"""

from __future__ import annotations

import re
from typing import Any

from . import packages
from .model import DesignError, DesignModel, Part, Placement

#: 网格排布用的间距（mm）。和 `synthesize._place` 同一套常量，
#: 两处各写一份必然漂移。
MARGIN_MM = 4.0
PACK_GAP_MM = 2.0

#: 标号前缀 → 角色。与 `easyeda_import.ROLE_BY_PREFIX` 的语义一致，
#: 但这里只用于 BOM（没有封装信息时的兜底）。
ROLE_BY_PREFIX = {
    "R": "component", "C": "component", "L": "component",
    "D": "component", "Q": "component", "U": "module",
    "J": "connector", "P": "connector", "X": "component",
    "SW": "component", "F": "component", "Y": "component",
    "LED": "component", "BT": "module", "CN": "connector",
    "USB": "connector", "ANT": "component",
}

#: 封装名里出现这些词 → 连接器。
CONNECTOR_WORDS = ("CONN", "WAFER", "SMA", "USB", "HEADER", "SOCKET",
                   "PLUG", "JACK", "TERMINAL", "MX1.25", "PH2.0", "XH",
                   "P1.25", "P2.00", "P2.50", "TYPE-C", "MICRO")


def _prefix_of(ref: str) -> str:
    match = re.match(r"^([A-Za-z]+)", ref.strip())
    return match.group(1).upper() if match else ""


def _role_of(ref: str, footprint: str) -> str:
    """角色：先看**封装名**（确定性），再看标号前缀（约定）。

    与 `easyeda_import._role_of` 同一条纪律 —— 封装名比标号可靠：
    `U` 开头的常常是模块而不是芯片，`USB` 开头的必然是连接器。
    """
    upper = str(footprint or "").upper()
    if any(word in upper for word in CONNECTOR_WORDS):
        return "connector"
    prefix = _prefix_of(ref)
    if prefix[:4] in ROLE_BY_PREFIX:
        return ROLE_BY_PREFIX[prefix[:4]]
    if prefix[:2] in ROLE_BY_PREFIX:
        return ROLE_BY_PREFIX[prefix[:2]]
    return ROLE_BY_PREFIX.get(prefix[:1], "component")


def _occupied(design: DesignModel) -> list[tuple[float, float, float, float]]:
    """已有元件 + **安装孔**的占位盒。BOM 补的元件要避开它们。

    安装孔必须算进来：实测 004 补完 BOM 后，`R5` 正好压住 #2 安装孔，
    检查器报"螺钉无法进入" —— 那条**是我们自己造的**，不是设计问题。
    """
    boxes: list[tuple[float, float, float, float]] = []
    for part in design.parts:
        if part.size_mm.get("w", 0.0) <= 0 or part.size_mm.get("h", 0.0) <= 0:
            continue
        boxes.append(part.footprint_mm())
    for hole in design.board.mount_holes or []:
        radius = float(getattr(hole, "diameter_mm", 0.0) or 0.0) / 2.0
        # 螺钉头部比孔径大：留 1.5mm 半径余量（M3 头约 5.5mm）
        reach = max(radius, 1.5) + 0.5
        cx = float(hole.x_mm)
        cy = float(hole.y_mm)
        boxes.append((cx - reach, cy - reach, cx + reach, cy + reach))
    return boxes


def _free(box: tuple[float, float, float, float],
          occupied: list[tuple[float, float, float, float]],
          gap: float = 0.3) -> bool:
    x0, y0, x1, y1 = box
    for ox0, oy0, ox1, oy1 in occupied:
        if (x0 < ox1 + gap and x1 > ox0 - gap
                and y0 < oy1 + gap and y1 > oy0 - gap):
            return False
    return True


def _grid_place(parts: list[Part], board_w: float, board_h: float,
                occupied: list[tuple[float, float, float, float]] | None = None
                ) -> dict[str, Any]:
    """把 BOM 补出来的元件排到板内**空着的地方**。

    ## 为什么必须避开已有元件

    实测：141 个真元件已经在板上，直接从头铺网格会让补进来的 29 个
    压在真元件上 —— 检查器立刻报 6 条 `space.overlap`，而那 6 条
    **是我们自己造出来的假问题**，不是设计缺陷。假问题比不补更糟：
    它把真正的重叠淹掉。

    ## 排不下怎么办

    **不硬塞**。排不下就把剩下的元件留在板外并如实上报
    （`unplaced` 列表 + warning）。硬塞进板外会变成 `space.bounds`
    假问题，同样污染判定。

    返回 `{"placed": n, "unplaced": [ref...], "overflow": bool}`。
    """
    occupied = list(occupied or [])
    cursor_x = MARGIN_MM
    cursor_y = MARGIN_MM
    row_height = 0.0
    usable_w = max(1.0, board_w - MARGIN_MM)
    usable_h = max(1.0, board_h - MARGIN_MM)

    placed = 0
    unplaced: list[str] = []
    for part in parts:
        w = float(part.size_mm.get("w", 0.0)) or 1.0
        h = float(part.size_mm.get("h", 0.0)) or 1.0
        found = False
        # 沿网格找第一个**空位**；找不到就整行下移，直到板底
        while cursor_y + h <= usable_h:
            while cursor_x + w <= usable_w:
                box = (cursor_x, cursor_y, cursor_x + w, cursor_y + h)
                if _free(box, occupied):
                    part.placement.x_mm = round(cursor_x, 3)
                    part.placement.y_mm = round(cursor_y, 3)
                    occupied.append(box)
                    cursor_x += w + PACK_GAP_MM
                    row_height = max(row_height, h)
                    placed += 1
                    found = True
                    break
                cursor_x += max(w * 0.5, PACK_GAP_MM)
            if found:
                break
            cursor_x = MARGIN_MM
            cursor_y += row_height + PACK_GAP_MM
            row_height = 0.0
        if not found:
            unplaced.append(part.ref)

    return {"placed": placed, "unplaced": unplaced,
            "overflow": bool(unplaced)}


def bom_parts(bom: dict[str, dict[str, str]], *,
              skip: set[str] | None = None) -> tuple[list[Part], dict[str, Any]]:
    """把 BOM 条目转成 `Part[]`（**不含摆位**）。

    `skip` 里的 ref 会被跳过（通常传"PCB 里已有的"——真实布局优先）。

    返回 `(parts, stats)`；`stats` 里如实分类三类：
    `sized`（有实测封装尺寸）/ `unsized`（封装查不到）/ `skipped`。
    """
    skip = set(skip or ())
    parts: list[Part] = []
    sized: list[str] = []
    unsized: list[str] = []
    skipped: list[str] = []
    issues: list[str] = []

    for ref in sorted(bom, key=lambda r: (re.sub(r"\d", "", r), _num_of(r))):
        entry = bom.get(ref) or {}
        if ref in skip:
            skipped.append(ref)
            continue
        value = str(entry.get("value") or "").strip()
        footprint = str(entry.get("footprint") or "").strip()
        lcsc = str(entry.get("lcsc") or "").strip()
        spec = packages.package_size(footprint)
        if spec is None:
            size_mm = {"w": 0.0, "h": 0.0, "d": 0.0}
            source = "estimated"
            height = packages.height_for_keywords(footprint)
            if height > 0:
                size_mm["d"] = height
            unsized.append(ref)
        else:
            size_mm = {"w": float(spec["w"]), "h": float(spec["h"]),
                       "d": float(spec["d"])}
            if size_mm["d"] <= 0:
                size_mm["d"] = packages.height_for_keywords(footprint)
            # 名字自带尺寸 = 数据手册级；标准系列表 = 公开标准（derived）。
            source = "derived"
            sized.append(ref)
        try:
            parts.append(Part(
                ref=ref,
                # kind 用封装名：比 "component" 有信息量，且能在检查器里区分
                kind=footprint or "component",
                label=value or footprint or ref,
                role=_role_of(ref, footprint),
                size_mm=size_mm,
                physical_source=source,
                placement=Placement(mount="board"),
                lcsc=lcsc or None,
                electrical={"value": value} if value else {},
            ))
        except DesignError as exc:
            issues.append(f"{ref}: {exc}")

    return parts, {
        "sized": sized,
        "unsized": unsized,
        "skipped": skipped,
        "issues": issues,
    }


def _num_of(ref: str) -> int:
    match = re.search(r"(\d+)", ref)
    return int(match.group(1)) if match else 0


def fill_from_bom(design: DesignModel,
                  bom: dict[str, dict[str, str]]) -> dict[str, Any]:
    """把 BOM 补进 `design`（就地修改），返回统计。

    **不覆盖已有元件**：PCB 导入的真实布局与实测尺寸，比 BOM 的排布位置
    可信得多。只补 `missing`。
    """
    existing = {part.ref for part in design.parts}
    parts, stats = bom_parts(bom, skip=existing)
    board_w = float(design.board.size_mm.get("w") or 0.0)
    board_h = float(design.board.size_mm.get("h") or 0.0)
    layout = _grid_place(parts, board_w, board_h, _occupied(design))
    # 排不下的**不进**设计：留在板外会变成 space.bounds 假问题，
    # 把真正的边界问题淹掉。宁可少几个元件，也不制造假问题。
    unplaced = set(layout["unplaced"])
    kept = [part for part in parts if part.ref not in unplaced]
    design.parts.extend(kept)

    added = len(kept)
    note = {
        "source": "bom",
        "added": added,
        "unplaced": sorted(unplaced),
        "skipped_existing": len(stats["skipped"]),
        "sized": len(stats["sized"]),
        "unsized": len(stats["unsized"]),
        "unsized_refs": stats["unsized"],
        "issues": stats["issues"],
        # **位置是排布，不是布局** —— 这句话必须在数据里，不能只写在文档里
        "placement": "grid-estimate" if added > 0 else "none",
        "placement_note": (
            "BOM 里没有坐标，补出来的元件排在板上**空着的地方**。"
            "**这是估算位置，不是真实 PCB 布局**，不能照着布线。"),
    }
    if added > 0:
        design.warnings.append(
            f"从 BOM 补入 {added} 个元件（{note['sized']} 个有实测封装尺寸，"
            f"{note['unsized']} 个封装查不到、尺寸标 estimated）。"
            f"**这些元件的位置是网格排布，不是真实布局。**")
    if unplaced:
        design.warnings.append(
            f"{len(unplaced)} 个 BOM 元件在板上排不下，**没有加进来**"
            f"（{', '.join(sorted(unplaced)[:8])}"
            f"{'…' if len(unplaced) > 8 else ''}）。"
            "板子不够大，不是漏了。")
        note["unplaced_note"] = "板内没有足够空位，这些元件未加入设计"
    if added > 0 or unplaced:
        design.meta["bom_fill"] = note
    return note