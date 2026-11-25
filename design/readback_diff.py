"""设计 vs EDA 的差异（C1）—— 闭环的有一半。

## 这一层补什么

`emit` 是单向的：设计 → EDA。之后人在编辑器里改了网表（加个上拉、换个脚），
`design.json` 不知道。下次再 emit 就把人的改动**覆盖掉**。

`readback --diff` 把 EDA 的实际网表读回来，与设计逐引脚对比：

| 差异 | 含义 | 自动回写？ |
|---|---|---|
| `pin_moved` | 同一脚在两个网表里挂的网不同 | ✅ 目标网存在时 |
| `eda_only` | EDA 里多出来的引脚 | ✅ 网/件/脚都在设计里时 |
| `design_only` | EDA 里少了设计中的引脚 | ❌ **不自动删**（删除是破坏性动作）|
| `net_added` / `net_removed` | EDA 里新增/丢失的网 | ❌ 需要人确认 |
| `part_added` / `part_missing` | 元件差异 | ❌ 需要人确认 |

## 纪律

* **只按引脚对账**（`(ref, pin) → net`），不按网名猜——网名可能被 EDA 重命名
* 自动回写**只做加法/搬迁**；删除一律留给人（`skipped` 里写原因）
* 回写后必须过 `DesignModel.validate()`；不过就整体不落盘
"""

from __future__ import annotations

from typing import Any

__all__ = ["FILE", "diff", "apply", "describe"]

FILE = "diff.json"


def _design_pins(design: Any) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for net in design.nets:
        name = str(net.get("net") or "")
        for pin in net.get("pins") or []:
            out[(str(pin.get("ref") or ""), str(pin.get("pin") or ""))] = name
    return out


def diff(design: Any, eda_pins: dict[tuple[str, str], str]) -> dict[str, Any]:
    """两份 `(ref, pin) → net` 的差异。**不写盘**。"""
    if not eda_pins:
        return {"status": "not_ready", "reason": "EDA 网表为空（没读到）",
                "differences": [], "counts": {}}
    design_pins = _design_pins(design)
    design_refs = {part.ref for part in design.parts}
    eda_refs = {ref for ref, _pin in eda_pins}
    design_nets = {str(net.get("net") or "") for net in design.nets}
    eda_nets = set(eda_pins.values())
    rows: list[dict[str, Any]] = []
    for key in sorted(set(design_pins) & set(eda_pins)):
        want, got = design_pins[key], eda_pins[key]
        if want != got:
            rows.append({"kind": "pin_moved", "ref": key[0], "pin": key[1],
                         "design_net": want, "eda_net": got})
    for key in sorted(set(eda_pins) - set(design_pins)):
        rows.append({"kind": "eda_only", "ref": key[0], "pin": key[1],
                     "eda_net": eda_pins[key]})
    for key in sorted(set(design_pins) - set(eda_pins)):
        rows.append({"kind": "design_only", "ref": key[0], "pin": key[1],
                     "design_net": design_pins[key]})
    for name in sorted(eda_nets - design_nets):
        rows.append({"kind": "net_added", "net": name})
    for name in sorted(design_nets - eda_nets):
        rows.append({"kind": "net_removed", "net": name})
    for ref in sorted(eda_refs - design_refs):
        rows.append({"kind": "part_added", "ref": ref})
    for ref in sorted(design_refs - eda_refs):
        rows.append({"kind": "part_missing", "ref": ref})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    return {
        "status": "ok" if not rows else "changed",
        "differences": rows,
        "counts": {**counts, "total": len(rows)},
        "checked": {"design_pins": len(design_pins), "eda_pins": len(eda_pins),
                    "design_nets": len(design_nets), "eda_nets": len(eda_nets)},
        "basis": "design.json 的网表 vs 立创 PCB 网表（逐引脚）",
        "note": ("自动回写只做加法/搬迁；删除与新网/新件一律人工确认"),
    }


def _drop_pin(net: dict[str, Any], ref: str, pin: str) -> bool:
    pins = [p for p in (net.get("pins") or [])
            if not (str(p.get("ref")) == ref and str(p.get("pin")) == pin)]
    if len(pins) == len(net.get("pins") or []):
        return False
    net["pins"] = pins
    return True


def apply(design: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """把差异里**能安全加**的部分写进设计。**不保存**（调用方决定）。"""
    rows = payload.get("differences") or []
    nets = {str(net.get("net") or ""): net for net in design.nets}
    pins_of = {part.ref: set(part.pins) for part in design.parts}
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        kind = row.get("kind")
        ref = str(row.get("ref") or "")
        pin = str(row.get("pin") or "")
        if kind == "pin_moved":
            target = str(row.get("eda_net") or "")
            net = nets.get(target)
            if net is None:
                skipped.append({**row, "why": f"目标网 {target} 不在设计里"})
                continue
            if ref not in pins_of or (pins_of[ref] and pin not in pins_of[ref]):
                skipped.append({**row, "why": "设计里没有这个元件/引脚"})
                continue
            if any(str(p.get("ref")) == ref and str(p.get("pin")) == pin
                   for p in net.get("pins") or []):
                skipped.append({**row, "why": "目标网里已经有这个脚"})
                continue
            _drop_pin(nets.get(str(row.get("design_net") or ""), {}), ref, pin)
            net.setdefault("pins", []).append({"ref": ref, "pin": pin})
            applied.append(row)
        elif kind == "eda_only":
            target = str(row.get("eda_net") or "")
            net = nets.get(target)
            if net is None:
                skipped.append({**row, "why": f"网 {target} 不在设计里"})
                continue
            if ref not in pins_of or (pins_of[ref] and pin not in pins_of[ref]):
                skipped.append({**row, "why": "设计里没有这个元件/引脚"})
                continue
            if any(str(p.get("ref")) == ref and str(p.get("pin")) == pin
                   for p in net.get("pins") or []):
                continue
            net.setdefault("pins", []).append({"ref": ref, "pin": pin})
            applied.append(row)
        else:
            skipped.append({**row, "why": "删除/新增需要人工确认（自动回写不做）"})
    return {"applied": applied, "skipped": skipped,
            "counts": {"applied": len(applied), "skipped": len(skipped)}}


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.readback_diff",
        "file": FILE,
        "kinds": ["pin_moved", "eda_only", "design_only", "net_added",
                  "net_removed", "part_added", "part_missing"],
        "apply_policy": "只做 pin_moved / eda_only；删除与新网/新件人工确认",
        "discipline": ("按 (ref,pin) 对账，不按网名猜；回写后必须过 validate；"
                       "删除不自动做"),
    }