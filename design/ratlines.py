"""飞线（ratline）—— 从网表生成"应该连成什么样"的连线。

## 为什么需要它

`design.json` 里的 `wires[]` 是**实际走线几何**。这份立创工程**没有**
走线数据（`WIRE` 记录是空的），所以 `wires` 是空的 —— 于是 2D 画布上
**一根线都没有**，97 条网络完全看不见。

飞线是替代方案：它从**网表**（哪几个引脚该连在一起）推出几何，用**焊盘
真实坐标**做端点，画成细虚线。

## 它是什么、不是什么

| | 飞线 | 铜线 |
|---|---|---|
| 来源 | 网表 + 焊盘坐标 | 实际布线数据 |
| 语义 | "这里应该有一根线" | "这里有一根线" |
| 是不是事实 | **不是** | 是 |

所以飞线在界面上**必须**标成"飞线（未布线）"，不能画成铜线的样式 ——
否则用户会以为板子已经布好了。lceda-pro 也有专门的 `ratlineworker.js`
做同一件事。

## 几何：最小生成树（MST）

一条网络有 N 个引脚，要连成"连通"只需要 N−1 条线。取 **Prim 算法**的
最小生成树：

* 边数最少（N−1），不会画出多余的线；
* 总长度最小，是布线器的合理起点；
* 确定性：同一份网表永远得到同一棵树。

不画"星形"（所有引脚连到中心）是因为那会画出 N−1 条以上重叠的线，
且总长度更大。

@module dsh_embagent.design.ratlines
"""

from __future__ import annotations

import math
from typing import Any

from .model import DesignModel, Part


def _pin_position(part: Part, pin: str) -> tuple[float, float] | None:
    """引脚坐标（板坐标系 mm）。

    优先用**焊盘几何**（真实位置）；没有焊盘就退回元件中心。
    """
    pad = part.pad_of(pin)
    if pad is not None:
        return (pad.x_mm, pad.y_mm)
    x0, y0, x1, y1 = part.footprint_mm()
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _prim(nodes: list[tuple[float, float]]) -> list[tuple[int, int]]:
    """最小生成树的边（Prim，确定性）。

    节点顺序固定 → 同距离时选先到的，结果稳定。
    """
    count = len(nodes)
    if count < 2:
        return []
    in_tree = [False] * count
    best: list[float | None] = [None] * count
    parent: list[int] = [-1] * count
    edges: list[tuple[int, int]] = []
    best[0] = 0.0
    for _ in range(count):
        pick = -1
        pick_cost = math.inf
        for index in range(count):
            if in_tree[index] or best[index] is None:
                continue
            if best[index] < pick_cost:
                pick_cost = best[index]
                pick = index
        if pick < 0:
            break
        in_tree[pick] = True
        if parent[pick] >= 0:
            edges.append((parent[pick], pick))
        for index in range(count):
            if in_tree[index]:
                continue
            ax, ay = nodes[pick]
            bx, by = nodes[index]
            cost = math.hypot(ax - bx, ay - by)
            if best[index] is None or cost < best[index]:
                best[index] = cost
                parent[index] = pick
    return edges


def build_ratlines(design: DesignModel) -> list[dict[str, Any]]:
    """从网表生成飞线。

    返回每条飞线：

        {
          "net": 网络名,
          "segments": [[x0,y0], [x1,y1]],   # 直线段（mm，板坐标）
          "length_mm": 总长,
          "pins": ["U1.12", "C1.1"],       # 两端引脚
          "routed": False,                 # 恒为 False —— 飞线不是铜线
        }

    端点用**焊盘真实坐标**；没有焊盘的元件退回中心（并在 `approximate`
    里标明）。
    """
    by_ref = {part.ref: part for part in design.parts}
    out: list[dict[str, Any]] = []
    for entry in design.nets:
        net_name = str(entry.get("net") or "")
        if net_name == "":
            continue
        nodes: list[tuple[float, float]] = []
        labels: list[str] = []
        # 每个节点**所属的板**：飞线不能跨板，投影要按板平移。
        owners: list[str] = []
        approximate = False
        for pin in entry.get("pins", []):
            ref = str(pin.get("ref") or "")
            name = str(pin.get("pin") or "")
            if ref == "" or name == "":
                continue
            part = by_ref.get(ref)
            if part is None:
                continue
            position = _pin_position(part, name)
            if position is None:
                continue
            if part.pad_of(name) is None:
                approximate = True
            nodes.append(position)
            labels.append(f"{ref}.{name}")
            # 用 `board_of` 解析归属（`board_id` 空 → 主参考板）
            owners.append(design.board_of(part).id)
        if len(nodes) < 2:
            continue
        for a, b in _prim(nodes):
            (ax, ay), (bx, by) = nodes[a], nodes[b]
            length = math.hypot(bx - ax, by - ay)
            # 重合端点（两个引脚在同一坐标，如通孔焊盘的正反面）会产生
            # 零长度飞线 —— 那不是"连线"，画出来是一个点，还让"飞线有长度"
            # 这条不变量失效。直接跳过。
            if length <= 1e-6:
                continue
            # 两端**必须在同一块板上**：跨板连线不是 PCB 铜线，画出来会是一条
            # 横穿拼板的假线。`board_id` 让调用方能过滤，也让投影能按板平移。
            out.append({
                "net": net_name,
                "segments": [[round(ax, 3), round(ay, 3)],
                             [round(bx, 3), round(by, 3)]],
                "length_mm": round(length, 3),
                "pins": [labels[a], labels[b]],
                "board_id": owners[a],
                "cross_board": owners[a] != owners[b],
                "routed": False,
                "approximate": approximate,
            })
    return out


def ratline_stats(design: DesignModel) -> dict[str, Any]:
    """飞线统计（给判定摘要用）。"""
    lines = build_ratlines(design)
    nets = {line["net"] for line in lines}
    total = sum(line["length_mm"] for line in lines)
    longest = max(lines, key=lambda x: x["length_mm"]) if lines else None
    return {
        "segments": len(lines),
        "nets": len(nets),
        "total_length_mm": round(total, 2),
        "longest_mm": round(longest["length_mm"], 2) if longest else 0.0,
        "longest_net": longest["net"] if longest else "",
    }
