"""预算：五种货币各自独立成树，不做加权折中。

直接复用 ``hwforge.budget``（``BudgetTree`` / ``Claim`` / ``CURRENCIES`` /
``RESOLUTION_ORDER``），所以仲裁顺序与"耦合组按池计算"的语义与 hwforge
完全一致——这是**同一套预算代数**，不是另写一份。

本模块只做两件事：

1. 从 ``DesignModel`` 生成一棵合理的默认树（不写死 buck 域的数字）；
2. 把每个 claim 映射回 ``ref``，让 2D/3D 能着色、面板能点回元件。
"""

from __future__ import annotations

import sys
from typing import Any

from .model import DesignModel

CURRENCIES = ("power_w", "thermal_c", "emc_dbuv", "timing_ns", "cost_usd")

# 默认上限：只是"还没人给项目设预算时"的占位，面板必须显示"默认"。
DEFAULT_LIMITS: dict[str, float] = {
    "power_w": 5.0,
    "thermal_c": 100.0,
    "emc_dbuv": 40.0,
    "timing_ns": 1000.0,
    "cost_usd": 30.0,
}

ROLE_GROUPS: dict[str, str] = {
    "board": "board",
    "module": "modules",
    "connector": "connectors",
    "component": "passives",
    "keepout": "keepout",
    "blocker": "mechanical",
    "wheel": "actuators",
    "caster_wheel": "actuators",
    "sensor": "sensors",
}


def _policy(design: DesignModel) -> dict[str, Any]:
    raw = design.meta.get("budget") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def build_tree(design: DesignModel) -> Any:
    """生成预算树：system → 角色分组 → 元件。"""
    from .budget import BudgetTree
    tree = BudgetTree()
    policy = _policy(design)
    limits = dict(DEFAULT_LIMITS)
    limits.update({k: float(v) for k, v in (policy.get("limits") or {}).items()
                   if k in CURRENCIES})
    system_label = str(policy.get("label") or "system")

    tree.add_node("system", None, dict(limits))
    tree.add_node("board", "system", {c: limits[c] * 0.2 for c in CURRENCIES})
    tree.add_node("modules", "system", {c: limits[c] * 0.5 for c in CURRENCIES},
                  coupling_group="shared_supply")
    tree.add_node("passives", "system", {c: limits[c] * 0.1 for c in CURRENCIES})
    tree.add_node("connectors", "system", {c: limits[c] * 0.05 for c in CURRENCIES})

    claimed: dict[str, dict[str, float]] = {}
    for part in design.parts:
        electrical = part.electrical or {}
        values: dict[str, float] = {}
        for currency in CURRENCIES:
            raw = electrical.get(currency)
            if raw is None:
                continue
            try:
                amount = float(raw)
            except (TypeError, ValueError):
                continue
            if amount < 0:
                continue
            values[currency] = amount
        if not values:
            continue
        group = ROLE_GROUPS.get(part.role.lower())
        if group not in tree.nodes:
            group = "passives"
        claimed[part.ref] = values
        for currency, amount in values.items():
            tree.add_claim(group, currency, amount,
                           assumption_refs=[part.ref])

    # 未声明 claim 的元件不参与等式；覆盖率在 evaluate() 里如实报告。
    return tree


def evaluate(design: DesignModel) -> dict[str, Any]:
    """跑预算代数，并把 claim 映射回 ref（2D/3D 着色 + 面板点回）。"""
    from .budget import RESOLUTION_ORDER
    tree = build_tree(design)
    verdict = tree.evaluate()
    policy = _policy(design)
    limits = dict(DEFAULT_LIMITS)
    limits.update({k: float(v) for k, v in (policy.get("limits") or {}).items()
                   if k in CURRENCIES})

    claims: list[dict[str, Any]] = []
    for claim in tree.claims:
        ref = claim.assumption_refs[0] if claim.assumption_refs else ""
        claims.append({
            "module": claim.module_id,
            "currency": claim.currency,
            "amount": claim.amount,
            "ref": ref,
            "ratio": round(claim.amount / limits[claim.currency], 4)
            if limits.get(claim.currency) else None,
        })

    by_currency: dict[str, dict[str, Any]] = {}
    for currency in CURRENCIES:
        used = sum(c.amount for c in tree.claims if c.currency == currency)
        limit = limits.get(currency)
        by_currency[currency] = {
            "used": round(used, 4),
            "limit": limit,
            "ratio": round(used / limit, 4) if limit else None,
            "over": bool(limit is not None and used > limit),
            "default_limit": currency not in (policy.get("limits") or {}),
        }

    covered = {c.assumption_refs[0] for c in tree.claims if c.assumption_refs}
    uncovered = [p.ref for p in design.parts if p.ref not in covered]

    # 没有任何 claim 时，"可行"没有意义——必须说"无可评估数据"。
    if not tree.claims:
        confidence = "none"
    elif uncovered:
        confidence = "partial"
    else:
        confidence = "full"

    heat: dict[str, dict[str, float]] = {}
    for claim in claims:
        if not claim["ref"]:
            continue
        heat.setdefault(claim["ref"], {})[claim["currency"]] = claim["amount"]

    return {
        "ok": bool(verdict["feasible"]) and confidence != "none",
        "confidence": confidence,
        "status": "ok" if confidence != "none" else "not_ready",
        "currencies": list(CURRENCIES),
        "resolution_order": list(RESOLUTION_ORDER),
        "limits": limits,
        "by_currency": by_currency,
        "violations": verdict["violations"],
        "claims": claims,
        "heatmap": heat,
        "coverage": {
            "claimed_parts": len(covered),
            "total_parts": len(design.parts),
            "uncovered": uncovered,
            "note": "没有 claim 的元件不参与预算等式；面板必须显示覆盖率",
        },
        "nodes": {
            node_id: {"parent": node.parent, "budget": node.budget,
                      "coupling_group": node.coupling_group}
            for node_id, node in tree.nodes.items()
        },
    }


def set_limits(design: DesignModel, limits: dict[str, float]) -> DesignModel:
    """改预算上限（写回 ``meta.budget.limits``）。"""
    meta = dict(design.meta)
    budget = dict(meta.get("budget") or {})
    clean = {k: float(v) for k, v in limits.items() if k in CURRENCIES}
    budget["limits"] = {**(budget.get("limits") or {}), **clean}
    meta["budget"] = budget
    design.meta = meta
    return design
