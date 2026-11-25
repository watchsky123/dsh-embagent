"""变更图（B4）—— 改一个东西，谁受影响。

## 这一层补什么

改了 MCU 型号/电源轨，网表、固件引脚、块实例都该跟着变，但它们是**四份**
产物。没有依赖图时，人只能靠记忆（AGENTS §1.1 的两个事实源）。

`graph.json` 把设计里的四种节点连起来：

```
part(U1) ──pin──▶ net(3V3) ──block_net──▶ block(MCU1)
   ▲                                        │
   └──────────── block_part ────────────────┘
net(3V3) ──firmware──▶ fw(3V3)  （design.firmware.pinmap）
```

`impact()` 给一组目标（`U1` / `net:3V3` / `block:MCU1`），沿边双向走两跳，
列出受影响的零件/网/块/固件引脚，并写**从哪条边传过去的**。

## 纪律

* 图是**派生**的（不落 design.json）；每次从零件/网表/块/固件现算
* 目标不存在 → `not_ready`，不是"没有影响"
* 影响面只走**两跳**（part→net→part 是"同一网络的邻居"；
  再远就会把整块板都说成受影响，等于没说）
"""

from __future__ import annotations

from typing import Any

__all__ = ["FILE", "build", "impact", "describe"]

FILE = "graph.json"

_HOPS = 2


def build(design: Any) -> dict[str, Any]:
    """零件/网/块/固件 → 节点 + 边。**不写盘**。"""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    def node(node_id: str, **info: Any) -> None:
        nodes[node_id] = {"id": node_id, **info}

    for part in design.parts:
        node(part.ref, type="part", kind=str(part.kind or ""),
             lcsc=str(getattr(part, "lcsc", "") or ""),
             mpn=str(getattr(part, "mpn", "") or ""))
    for net in design.nets:
        name = str(net.get("net") or "")
        if not name:
            continue
        node(f"net:{name}", type="net", net=name,
             cls=str(net.get("class") or "signal"))
        for pin in net.get("pins") or []:
            ref = str(pin.get("ref") or "")
            if ref in nodes:
                edges.append({"from": ref, "to": f"net:{name}", "kind": "pin"})
    for row in getattr(design, "blocks_list", None) or []:
        instance = str(row.get("instance") or "")
        if not instance:
            continue
        block_id = f"block:{instance}"
        node(block_id, type="block", instance=instance,
             block=str(row.get("block") or ""))
        for ref in row.get("parts") or []:
            if str(ref) in nodes:
                edges.append({"from": block_id, "to": str(ref), "kind": "block_part"})
        for name in row.get("nets") or []:
            if f"net:{name}" in nodes:
                edges.append({"from": block_id, "to": f"net:{name}",
                              "kind": "block_net"})
    pinmap = ((design.firmware or {}).get("pinmap") or {})
    for net_name in pinmap:
        if f"net:{net_name}" in nodes:
            edges.append({"from": f"net:{net_name}", "to": f"fw:{net_name}",
                          "kind": "firmware"})
            node(f"fw:{net_name}", type="firmware", net=net_name,
                 pin=(pinmap.get(net_name) or {}).get("pin") if isinstance(
                     pinmap.get(net_name), dict) else "")

    counts: dict[str, int] = {}
    for info in nodes.values():
        counts[str(info["type"])] = counts.get(str(info["type"]), 0) + 1
    return {
        "status": "ok" if nodes else "not_ready",
        "nodes": nodes,
        "edges": edges,
        "counts": {**counts, "edges": len(edges)},
        "note": "派生图：不落 design.json；目标不存在是 not_ready",
    }


def impact(design: Any, targets: list[str]) -> dict[str, Any]:
    """沿边双向走 ≤2 跳。`targets` 形如 `U1` / `net:3V3` / `block:MCU1`。"""
    graph = build(design)
    nodes = graph["nodes"]
    if not nodes:
        return {"status": "not_ready", "reason": "设计里没有节点", "graph": graph}
    wanted: list[str] = []
    missing: list[str] = []
    for target in targets:
        text = str(target or "").strip()
        if not text:
            continue
        # 允许用户只写网名/块实例名（省掉前缀）
        for candidate in (text, f"net:{text}", f"block:{text}"):
            if candidate in nodes:
                wanted.append(candidate)
                break
        else:
            missing.append(text)
    if not wanted:
        return {"status": "not_ready", "reason": f"目标都不存在：{missing}",
                "graph": graph}
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in graph["edges"]:
        adjacency.setdefault(edge["from"], []).append((edge["to"], edge["kind"]))
        adjacency.setdefault(edge["to"], []).append((edge["from"], edge["kind"]))

    reasons: list[dict[str, Any]] = []
    seen = set(wanted)
    frontier = list(wanted)
    for depth in range(1, _HOPS + 1):
        following: list[str] = []
        for node_id in frontier:
            for other, kind in adjacency.get(node_id, []):
                if other in seen:
                    continue
                seen.add(other)
                following.append(other)
                reasons.append({"node": other, "via": f"{node_id} --{kind}-->",
                                "depth": depth})
        frontier = following
        if not frontier:
            break
    affected = {"parts": [], "nets": [], "blocks": [], "firmware": []}
    for node_id in sorted(seen):
        info = nodes.get(node_id) or {}
        kind = info.get("type")
        payload = info.get("net") or info.get("instance") or node_id
        if kind == "part":
            affected["parts"].append(payload)
        elif kind == "net":
            affected["nets"].append(payload)
        elif kind == "block":
            affected["blocks"].append(payload)
        elif kind == "firmware":
            affected["firmware"].append(payload)
    return {
        "status": "ok",
        "targets": wanted,
        "missing": missing,
        "affected": affected,
        "reasons": reasons,
        "counts": {key: len(value) for key, value in affected.items()},
        "note": f"两跳内受影响（含目标自己）；{'、'.join(f'{k}={len(v)}' for k, v in affected.items())}",
    }


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.graph",
        "file": FILE,
        "nodes": ["part", "net", "block", "firmware"],
        "edges": ["pin", "block_part", "block_net", "firmware"],
        "impact": "双向 ≤2 跳；目标不存在 → not_ready",
        "discipline": "图是派生的（不改 design.json）；不扩到全板",
    }