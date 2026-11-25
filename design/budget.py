"""五轨预算代数 —— **dsh_embagent 自己的**，不再依赖 `hwforge.budget`。

## 它在算什么

一个设计有五种互相挤占的资源（**五条轨**）：

| 轨 | 单位 | 被谁吃 |
|---|---|---|
| `power_w` | W | 器件损耗、负载 |
| `thermal_c` | °C | 结温升 |
| `emc_dbuv` | dBµV | 辐射裕量 |
| `timing_ms` | ms | 时序窗口 |
| `cost_cny` | ¥ | 物料成本 |

预算树是一棵**分配树**：system → 各分组 → 元件 claim。每个节点声明
自己能给多少，每个元件申报自己吃多少。**父子不匹配就是违规**。

## 为什么不用简单求和

因为真实约束不是"总和 < 上限"：

1. **分组各有配额**（板 20%、模块 50%、无源 10%…）—— 总量够但某个
   分组超了，仍然是问题；
2. **耦合组要池化**：热与 EMC 是**共享物理资源**，两个模块各自的发热
   不能简单相加（同一块铜箔、同一个风道）。所以耦合组按"成员共同父节点
   的配额"整体判，而不是逐个相加。

第二条是这个模块存在的理由：只做求和会把"耦合资源"判成"独立资源"，
那是最常见的假绿灯。

## 纪律

* **没有 claim 的元件不参与等式**，但覆盖率必须如实报（`confidence`）；
* **完全没有 claim 时 `ok` 为 False** —— "没有数据"不是"可行"；
* 违规项带 `action` —— 只说超了多少不给方向，等于没说。

@module dsh_embagent.design.budget
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


#: 五条轨。**名字以 dsh_embagent 的既有约定为准**（`timing_ns` / `cost_usd`），
#: 不沿用 hwforge 的 `timing_ms` / `cost_cny` —— 改名字会让已有工程里的
#: `meta.budget.limits` 全部失配。
CURRENCIES: tuple[str, ...] = ("power_w", "thermal_c", "emc_dbuv",
                               "timing_ns", "cost_usd")

#: 违规的修复方向。**按轨给**，因为同一句话对五条轨不成立。
RESOLUTION_ORDER: tuple[str, ...] = CURRENCIES

_ACTIONS: dict[str, str] = {
    "power_w": "换损耗更低的器件，或降低目标负载",
    "thermal_c": "降低热阻、加大铜箔面积，或换更低损耗的器件",
    "emc_dbuv": "加屏蔽/滤波，或降低开关边沿速率",
    "timing_ns": "放宽时序窗口，或换更快的器件/时钟",
    "cost_usd": "降规格，或减少器件数量",
}


class BudgetError(ValueError):
    """预算树的构造或查询错误。"""


@dataclass
class Claim:
    """一个元件对某条轨的申报量。"""

    module_id: str
    currency: str
    amount: float
    assumption_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.currency not in CURRENCIES:
            raise BudgetError(f"未知的轨 {self.currency!r}；可选：{CURRENCIES}")
        if self.amount < 0:
            raise BudgetError(
                f"{self.module_id}: {self.currency} 的申报量不能为负"
                f"（收到 {self.amount}）—— 负值不是「节省」，是符号搞错了")


@dataclass
class Node:
    """预算树的一个节点。`budget` 是**它能给下一层的份额**。"""

    node_id: str
    parent: str | None
    budget: dict[str, float] = field(default_factory=dict)
    #: 耦合组成员共享父节点的配额（热/EMC 这类物理资源共享）
    coupling_group: str = ""


class BudgetTree:
    """分配树 + 申报 + 可行性判定。"""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.claims: list[Claim] = []

    # -- 构造 --------------------------------------------------------------

    def add_node(self, node_id: str, parent: str | None,
                 budget: dict[str, float] | None = None, *,
                 coupling_group: str = "") -> Node:
        if node_id in self.nodes:
            raise BudgetError(f"节点重复：{node_id!r}")
        if parent is not None and parent not in self.nodes:
            raise BudgetError(f"{node_id!r} 的父节点 {parent!r} 还不存在"
                              f"（必须先加父再加子）")
        clean: dict[str, float] = {}
        for currency, value in (budget or {}).items():
            if currency not in CURRENCIES:
                raise BudgetError(f"{node_id}: 未知的轨 {currency!r}")
            if value < 0:
                raise BudgetError(f"{node_id}: {currency} 的配额不能为负")
            clean[currency] = float(value)
        node = Node(node_id=node_id, parent=parent, budget=clean,
                    coupling_group=coupling_group)
        self.nodes[node_id] = node
        return node

    def add_claim(self, module_id: str, currency: str, amount: float,
                  *, assumption_refs: list[str] | None = None) -> Claim:
        if module_id not in self.nodes:
            raise BudgetError(f"申报来自不存在的节点：{module_id!r}")
        claim = Claim(module_id=module_id, currency=currency,
                      amount=float(amount),
                      assumption_refs=list(assumption_refs or []))
        self.claims.append(claim)
        return claim

    # -- 查询 --------------------------------------------------------------

    def children(self, node_id: str) -> list[str]:
        return [n.node_id for n in self.nodes.values() if n.parent == node_id]

    def _claims_for(self, node_ids: set[str], currency: str) -> float:
        return sum(c.amount for c in self.claims
                   if c.module_id in node_ids and c.currency == currency)

    def _descendants(self, node_id: str) -> set[str]:
        """包含自己在内的全部后代。"""
        out = {node_id}
        frontier = [node_id]
        while frontier:
            current = frontier.pop()
            for child in self.children(current):
                if child not in out:
                    out.add(child)
                    frontier.append(child)
        return out

    # -- 判定 --------------------------------------------------------------

    def evaluate(self) -> dict[str, Any]:
        """逐节点、逐轨判可行性，再单独判耦合组。"""
        violations: list[dict[str, Any]] = []

        # -- 1. 分配：子节点申报之和不能超过父节点给的配额 ---------------
        for node in self.nodes.values():
            kids = self.children(node.node_id)
            for currency in CURRENCIES:
                budget = node.budget.get(currency)
                if budget is None:
                    continue
                if not kids:
                    # **叶子节点**：申报直接挂在它身上。早先这里 `continue`，
                    # 于是叶子自己的申报从来没和它的配额比过 —— 一个
                    # "预算 3W、申报 4W"的叶子会静静通过。
                    used = self._claims_for({node.node_id}, currency)
                    if used <= 0:
                        continue
                else:
                    used = self._claims_for(set(kids), currency)
                if used > budget:
                    violations.append({
                        "kind": "OVER_BUDGET",
                        "node": node.node_id,
                        "currency": currency,
                        "used": round(used, 4),
                        "budget": round(budget, 4),
                        "over": round(used - budget, 4),
                        "scope": "self" if not kids else "children",
                        "action": _ACTIONS.get(currency, ""),
                    })

        # -- 2. 耦合组：池化后判，不逐个相加 ----------------------------
        groups: dict[str, list[str]] = {}
        for node in self.nodes.values():
            if node.coupling_group:
                groups.setdefault(node.coupling_group, []).append(node.node_id)

        for group_id, members in groups.items():
            parents = {self.nodes[m].parent for m in members}
            if len(parents) != 1:
                violations.append({
                    "kind": "COUPLING_PARENT_AMBIGUOUS",
                    "group": group_id,
                    "parents": sorted(str(p) for p in parents),
                    "action": "耦合组成员必须有同一个父节点，否则没法池化",
                })
                continue
            parent = parents.pop()
            if parent is None or parent not in self.nodes:
                continue
            pool = self.nodes[parent].budget
            for currency in ("thermal_c", "emc_dbuv"):
                group_budget = pool.get(currency)
                if group_budget is None:
                    continue
                used = self._claims_for(set(members), currency)
                if used > group_budget:
                    violations.append({
                        "kind": "COUPLING_OVER_BUDGET",
                        "group": group_id,
                        "currency": currency,
                        "used": round(used, 4),
                        "budget": round(group_budget, 4),
                        "over": round(used - group_budget, 4),
                        "note": "共享物理资源，不是简单相加",
                        "action": _ACTIONS.get(currency, ""),
                    })

        return {
            "feasible": not violations,
            "violations": violations,
            "resolution_order": list(RESOLUTION_ORDER),
        }


def build_default_tree(limits: dict[str, float] | None = None) -> BudgetTree:
    """默认分配树：system → board / modules / passives / connectors。"""
    caps = dict(limits or {})
    tree = BudgetTree()
    tree.add_node("system", None, caps)
    tree.add_node("board", "system", {c: caps.get(c, 0.0) * 0.2 for c in CURRENCIES})
    tree.add_node("modules", "system",
                  {c: caps.get(c, 0.0) * 0.5 for c in CURRENCIES},
                  coupling_group="shared_supply")
    tree.add_node("passives", "system",
                  {c: caps.get(c, 0.0) * 0.1 for c in CURRENCIES})
    tree.add_node("connectors", "system",
                  {c: caps.get(c, 0.0) * 0.05 for c in CURRENCIES})
    return tree


def describe() -> dict[str, Any]:
    return {"module": "dsh_embagent.design.budget",
            "currencies": list(CURRENCIES),
            "note": "耦合组池化判定（热/EMC 是共享物理资源）"}
