"""约束账本（A5）—— 每块/每网一条**可测**约束，缺数据就是 `unknown`。

## 这一层补什么

`ladder` 只查"绝对最大值"（笼统）；块里的 `rules` 是文字，没人执行。约束账本
把"这条规则到底有没有被满足"变成可求值的三态：

| 规则 | 判据 | 缺数据 |
|---|---|---|
| `decoupling` | 每条电源轨 ≥1 颗去耦电容 | 没有电容件 → `unknown`（不是 pass）|
| `i2c.pullup` | 每条 I2C 线 ≥1 个上拉到电源的电阻 | 网表没有 I2C → 不生成 |
| `can.termination` | CANH–CANL 之间 ≥1 个 120Ω | 没有 CAN 网 → 不生成 |
| `budget.power` | 块预算（电流×5V）≤ 需求 `power_w` | 需求没给 → `unknown` |
| `interface` | 接口在网表里有没有对应的网 | 没有 → `unknown` |

## 纪律

* **规则来源必须可追溯**：`source` = `spec` / `block` / `netlist` / `req`
* `unknown` 不是 `pass`：总状态只有在"至少一条 pass 且没有 fail"时才是 `ok`
* 求值**不猜**：没有电容件时不能说"去耦够了"，只能说 `unknown`
"""

from __future__ import annotations

from typing import Any

__all__ = ["FILE", "derive", "evaluate", "describe"]

FILE = "constraints.json"

#: 电源轨名（不靠 class 也能认）。
_RAILS = ("3V3", "5V", "VBUS", "VIN", "VOUT", "VBAT")
_CAP_KINDS = ("capacitor", "cap", "mlcc")
_RES_KINDS = ("resistor", "res")


def _pin_nets(design: Any) -> dict[tuple[str, str], list[str]]:
    """`(ref, pin) → [net...]`，用来查"这个件的这个脚接在哪"。"""
    out: dict[tuple[str, str], list[str]] = {}
    for net in design.nets:
        name = str(net.get("net") or "")
        for pin in net.get("pins") or []:
            out.setdefault((str(pin.get("ref")), str(pin.get("pin"))), []).append(name)
    return out


def _parts_on(design: Any, net: str) -> list[Any]:
    refs = {str(pin.get("ref")) for net_row in design.nets
            if str(net_row.get("net")) == net
            for pin in net_row.get("pins") or []}
    return [part for part in design.parts if part.ref in refs]


def _is_kind(part: Any, kinds: tuple[str, ...]) -> bool:
    kind = str(getattr(part, "kind", "") or "").lower()
    label = str(getattr(part, "label", "") or "").lower()
    return any(k in kind for k in kinds) or any(k in label for k in kinds)


def _eval_decoupling(design: Any, source: str = "netlist") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rails = [str(net.get("net")) for net in design.nets
             if str(net.get("class")) == "power"
             and str(net.get("net")) not in ("GND", "VSS", "VSSA")]
    for rail in sorted(set(rails) | {name for name in _RAILS
                                     if any(str(net.get("net")) == name
                                            for net in design.nets)}):
        parts = _parts_on(design, rail)
        caps = [part for part in parts if _is_kind(part, _CAP_KINDS)]
        if not parts:
            continue
        if not caps:
            status = "unknown" if not any(_is_kind(p, _CAP_KINDS)
                                          for p in design.parts) else "fail"
            rows.append({"id": f"decoupling.{rail}", "kind": "decoupling",
                         "status": status, "checked": {"rail": rail, "parts": len(parts)},
                         "basis": source,
                         "detail": f"{rail} 上 {len(parts)} 颗件，0 颗去耦电容"})
            continue
        rows.append({"id": f"decoupling.{rail}", "kind": "decoupling", "status": "pass",
                     "checked": {"rail": rail, "parts": len(parts), "caps": len(caps)},
                     "basis": source,
                     "detail": f"{rail} 上 {len(caps)} 颗去耦电容"})
    return rows


def _eval_pullup(design: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pin_nets = _pin_nets(design)
    i2c = [str(net.get("net")) for net in design.nets
           if str(net.get("class")) == "i2c" or "I2C" in str(net.get("net")).upper()]
    for net in sorted(set(i2c)):
        resistors = [part for part in design.parts if _is_kind(part, _RES_KINDS)]
        pulled = False
        for part in resistors:
            nets = pin_nets.get((part.ref, part.pins[0]), []) + \
                pin_nets.get((part.ref, part.pins[-1]), [])
            if net in nets and any(name in _RAILS or name.startswith("3V3")
                                   for name in nets):
                pulled = True
                break
        rows.append({"id": f"i2c.pullup.{net}", "kind": "i2c.pullup",
                     "status": "pass" if pulled else "fail",
                     "checked": {"net": net, "resistors": len(resistors)},
                     "basis": "netlist",
                     "detail": (f"{net} 有上拉" if pulled
                                else f"{net} 没有上拉到电源的电阻")})
    return rows


def _eval_termination(design: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pin_nets = _pin_nets(design)
    can_nets = {str(net.get("net")) for net in design.nets
                if str(net.get("class")) == "can" or "CAN" in str(net.get("net")).upper()}
    if not can_nets:
        return rows
    terminated = False
    for part in design.parts:
        if not _is_kind(part, _RES_KINDS):
            continue
        ends = [pin_nets.get((part.ref, pin), []) for pin in part.pins[:2]]
        if len(ends) == 2 and ends[0] and ends[1] and ends[0] != ends[1] \
                and ends[0][0] in can_nets and ends[1][0] in can_nets:
            terminated = True
            break
    rows.append({"id": "can.termination", "kind": "termination",
                 "status": "pass" if terminated else "fail",
                 "checked": {"can_nets": sorted(can_nets)},
                 "basis": "netlist",
                 "detail": ("CANH–CANL 之间有终端电阻" if terminated
                            else "CAN 总线没有终端电阻")})
    return rows


def _eval_budget(spec: dict[str, Any] | None, req: Any) -> list[dict[str, Any]]:
    numbers = dict(getattr(req, "numbers", None) or {}) if req is not None else {}
    limit = numbers.get("power_w")
    blocks = (spec or {}).get("blocks") or []
    current_ma = sum(float((block.get("budget") or {}).get("current_ma") or 0)
                     for block in blocks)
    if not blocks or current_ma <= 0:
        return [{"id": "budget.power", "kind": "budget", "status": "unknown",
                 "checked": {"blocks": len(blocks), "current_ma": current_ma},
                 "basis": "spec", "detail": "没有块预算，算不出功耗"}]
    watts = current_ma / 1000.0 * 5.0
    if limit in (None, "", 0):
        return [{"id": "budget.power", "kind": "budget", "status": "unknown",
                 "checked": {"current_ma": current_ma, "estimated_w": round(watts, 2)},
                 "basis": "spec（块预算上限×5V，估算）",
                 "detail": "需求没给 power_w —— 无法判超没超"}]
    status = "pass" if watts <= float(limit) else "fail"
    return [{"id": "budget.power", "kind": "budget", "status": status,
             "checked": {"current_ma": current_ma, "estimated_w": round(watts, 2),
                         "limit_w": float(limit)},
             "basis": "spec（块预算上限×5V，估算；不是实测）",
             "detail": f"估算 {watts:.2f} W vs 需求 {limit} W"}]


def _eval_interfaces(design: Any, spec: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    classes = {str(net.get("class")) for net in design.nets}
    for contract in (spec or {}).get("interfaces") or []:
        protocol = str(contract.get("protocol") or "")
        cls = str(contract.get("class") or protocol)
        if cls in classes:
            rows.append({"id": f"iface.{contract['id']}", "kind": "interface",
                         "status": "pass",
                         "checked": {"protocol": protocol, "class": cls},
                         "basis": "spec+netlist",
                         "detail": f"网表里有 {cls} 类的网"})
        else:
            rows.append({"id": f"iface.{contract['id']}", "kind": "interface",
                         "status": "unknown",
                         "checked": {"protocol": protocol, "class": cls},
                         "basis": "spec+netlist",
                         "detail": f"网表里没有 {cls} 类的网 —— 接口没接"})
    return rows


def derive(design: Any, *, spec: dict[str, Any] | None = None,
           req: Any = None) -> dict[str, Any]:
    """从设计/规格/需求**派生**约束并求值。"""
    rules: list[dict[str, Any]] = []
    if spec:
        for contract in spec.get("interfaces") or []:
            rules.append({"id": f"iface.{contract['id']}", "kind": "interface",
                          "subject": {"a": contract.get("a"), "b": contract.get("b"),
                                      "protocol": contract.get("protocol")},
                          "rules": list(contract.get("rules") or []),
                          "source": "spec"})
    for net in design.nets:
        cls = str(net.get("class") or "")
        rules.append({"id": f"net.{net.get('net')}", "kind": "net",
                      "subject": {"net": net.get("net"), "class": cls},
                      "rules": list(net.get("rules") or []),
                      "source": "block" if net.get("block") else "netlist"})
    rows = ([] if not design.nets else
            _eval_decoupling(design) + _eval_pullup(design)
            + _eval_termination(design))
    if spec is not None:
        rows += _eval_interfaces(design, spec)
    rows += _eval_budget(spec, req)
    passed = [row for row in rows if row["status"] == "pass"]
    failed = [row for row in rows if row["status"] == "fail"]
    unknown = [row for row in rows if row["status"] == "unknown"]
    status = ("failed" if failed
              else "ok" if passed
              else "not_ready" if rows else "not_ready")
    return {
        "status": status,
        "file": FILE,
        "rules": rules,
        "results": rows,
        "counts": {"rules": len(rules), "evaluated": len(rows),
                   "pass": len(passed), "fail": len(failed),
                   "unknown": len(unknown)},
        "failed": [{"id": row["id"], "detail": row["detail"]} for row in failed],
        "unknown": [{"id": row["id"], "detail": row["detail"]} for row in unknown],
        "note": ("unknown **不是** pass；有 fail 就是 failed；"
                 "一条都求不出值是 not_ready"),
    }


def evaluate(design: Any, payload: dict[str, Any], *, spec: dict[str, Any] | None = None,
             req: Any = None) -> dict[str, Any]:
    """按已有规则表重新求值（保留 `source`，重算三态）。"""
    fresh = derive(design, spec=spec, req=req)
    fresh["rules"] = payload.get("rules") or fresh["rules"]
    return fresh


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.constraints",
        "file": FILE,
        "kinds": ["decoupling", "i2c.pullup", "can.termination", "budget",
                  "interface", "net"],
        "status": "ok（有 pass 且无 fail）/ failed / not_ready",
        "discipline": ("来源可追溯（spec/block/netlist/req）；"
                       "unknown 不是 pass；没有件就说 unknown，不猜"),
    }