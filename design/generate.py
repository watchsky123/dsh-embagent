"""按需求从 0 组装候选设计 —— **有限枚举，不证收敛**。

## 与"迭代收敛"的区别（这是本模块的核心决定）

提案的做法是：

    while not ok:
        改设计 → 验证

这条路要求证明"每次修改都让约束单调收窄"。**没有这个证明**，
循环可能震荡、可能永不终止，而"它能收敛"这句话就变成了信仰。

本模块的做法是：

    枚举 ≤ max_candidates 个候选 → 批量验证 → 按阶梯排序

好处是**天然终止**：枚举有硬上限，循环不存在，震荡不可能。
代价是不保证全局最优。对"AI 生成可运行初版、工程师二次开发"这个
目标，**可行解比最优解重要**——工程师会在这里继续改，他需要的
是一个有理由、能验证的起点，不是一个数学上最优的黑盒。

## 四段（不是五个）

| 段 | 做什么 | 为什么是确定性的 |
|---|---|---|
| ① 需求 → 单元 | `units.derive_from_req` | 纯映射表，同输入同输出 |
| ② 单元 → 元件组合 | `capability.candidates` + 笛卡尔积 | 过滤 + 字典序遍历 |
| ③ 组合 → 链路 | `links.wire` | 按需求声明的接口接，不智能匹配 |
| ④ 组合 → 断言 | `claims.derive` | 规则生成，不让 AI 写 |

**没有 ⑤"再优化一轮"。** 有第 ⑤ 段就回到收敛问题了。

## 一个必须守的纪律：候选之间必须可区分

两个候选如果只差一个型号名字、拓扑完全一样，那是**同一个方案**，
报成两个是虚增数量。所以按"零件清单 + 链路签名"去重，
重复的丢掉，不做填充凑数。
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import bus as bus_module
from . import capability, claims as claims_module, units as units_module
from .model import Board, DesignModel, Part, Placement


@dataclass
class Requirement:
    """需求。

    `roles` 是**功能角色**（与 `roles.py` 同一套写法），
    `interfaces` 声明单元之间的连接，`numbers` 是协同验证要用的数值。
    """

    id: str = "req"
    title: str = ""
    roles: list[str] = field(default_factory=list)
    interfaces: list[dict[str, Any]] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    numbers: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Requirement":
        return Requirement(
            id=str(data.get("id") or "req"),
            title=str(data.get("title") or ""),
            roles=[str(r) for r in (data.get("roles") or [])],
            interfaces=[dict(i) for i in (data.get("interfaces") or [])
                        if isinstance(i, dict)],
            constraints=dict(data.get("constraints") or {}),
            numbers=dict(data.get("numbers") or {}),
            notes=str(data.get("notes") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "roles": list(self.roles),
            "interfaces": [dict(i) for i in self.interfaces],
            "constraints": dict(self.constraints),
            "numbers": dict(self.numbers), "notes": self.notes,
        }


def load_requirement(path: str | Path) -> Requirement:
    """从 `.yaml` / `.yml` / `.json` 读需求。

    YAML 用 `yaml.safe_load`——**不用 `yaml.load`**：需求文件可能是
    从别处拷来的，允许它构造 Python 对象等于允许它执行代码。
    """
    text = Path(path).read_text(encoding="utf-8")
    suffix = str(path).lower()
    if suffix.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError as exc:                    # pragma: no cover
            raise ValueError(
                "读 .yaml 需要 pyyaml；装不上就把需求存成 .json") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("需求文件必须是一个对象（mapping）")
    return Requirement.from_dict(data)


@dataclass
class Candidate:
    """一个候选：零件组合 + 链路 + 断言 + 排序依据。"""

    id: str
    parts: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    units: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    #: 排序依据（越小越好）
    hard_failures: int = 0
    soft_failures: int = 0
    unknowns: int = 0
    cost: float = 0.0
    #: 阶梯各层状态，人看的
    ladder: dict[str, str] = field(default_factory=dict)
    why: str = ""

    def signature(self) -> str:
        """拓扑签名：零件清单 + 链路。**不含 id**，否则永远不重复。"""
        part_key = "|".join(
            f"{','.join(p.get('units') or [p['unit']])}:{p['model']}"
            for p in sorted(self.parts, key=lambda p: p["model"]))
        link_key = "|".join(sorted(f"{l['a'].get('unit')}->{l['b'].get('unit')}"
                                  for l in self.links))
        return f"{part_key}||{link_key}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parts": list(self.parts),
            "links": list(self.links),
            "claim_count": len(self.claims),
            "claims": list(self.claims),
            "units": [dict(u) for u in self.units],
            "unresolved": list(self.unresolved),
            "hard_failures": self.hard_failures,
            "soft_failures": self.soft_failures,
            "unknowns": self.unknowns,
            "cost": self.cost,
            "ladder": dict(self.ladder),
            "why": self.why,
        }


# ---------------------------------------------------------------------------
# ② 枚举零件组合
# ---------------------------------------------------------------------------

def _enumerate_parts(unit_list: list[dict[str, Any]],
                     constraints: dict[str, Any],
                     budget: int) -> list[dict[str, str]]:
    """笛卡尔积枚举，**到上限就停**。

    返回 `[{unit_role: model}]`。某个单元没有任何可选型号时，
    该单元记 `None` 并进 `unresolved`——**不跳过、不静默**。
    """
    per_unit: list[tuple[str, list[str]]] = []
    for unit in unit_list:
        needs = set(unit.get("needs") or [])
        models = capability.candidates(needs, constraints,
                                       role=str(unit.get("role") or ""))
        per_unit.append((str(unit.get("role") or ""), models))

    combos: list[dict[str, str]] = []
    # 有单元无候选 → 直接返回空，并保留原因（调用方用 explain_miss 解释）
    if any(not models for _role, models in per_unit if _role != "mount"):
        return []

    pools = [[(role, model) for model in models]
             for role, models in per_unit if role != "mount"]
    if not pools:
        return []

    for combination in itertools.product(*pools):
        combos.append({role: model for role, model in combination})
        if len(combos) >= budget:
            break
    return combos


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _collapse(combo: dict[str, str],
              unit_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把"同一个型号服务多个单元"合并成一颗料。

    实测例子：`esp32s3` 既能当 `mcu` 又能当 `radio`（它有 Wi-Fi，
    `roles.py` 里 `radio.mergeable_with=('mcu',)` 就写了这件事）。
    第一版把两个单元各列一行，于是：

      * 面积被算了两次（49mm² 的芯片算成 98mm²）
      * 链路里出现"自己连自己"

    **物理上它是一颗芯片。** 合并后 `units` 记多个、`model` 只一个，
    面积按一颗算。
    """
    by_model: dict[str, list[str]] = {}
    needs_by_unit = {str(u.get("role") or ""): list(u.get("needs") or [])
                     for u in unit_list}
    for unit_role in sorted(combo):
        model = combo[unit_role]
        by_model.setdefault(model, []).append(unit_role)

    rows: list[dict[str, Any]] = []
    for model in sorted(by_model):
        owners = sorted(by_model[model])
        rows.append({
            "unit": owners[0],
            "units": owners,
            "model": model,
            "needs": sorted({n for owner in owners
                             for n in needs_by_unit.get(owner, [])}),
        })
    return rows


#: 默认仿真预算（次数）。对应 RoboBuilder 给 agent 的"B 次调用"约束：
#:
#: 它的论文说"真实测试要几小时，每次迭代都贵，必须选得好"。
#: 我们的仿真比真机快，但**不是免费的**：316 元件 weld 后跑 1 秒要
#: >300 秒墙钟（arena 256MB、约 1 万约束）。50 个候选 × 3 次扰动 × 2 秒
#: 就是几小时。所以预算必须显式。
DEFAULT_SIM_BUDGET = 60


def generate(req: Requirement, *,
             max_candidates: int = 50,
             run_ladder: bool = True,
             sim_budget: int = DEFAULT_SIM_BUDGET) -> dict[str, Any]:
    """四段式生成。返回 JSON 报告。

    `run_ladder=False` 时不跑验证阶梯（只出候选），用于快速看枚举结果。

    排序键：`(hard_failures, soft_failures, unknowns, cost)`。
    **硬失败数为 0 的候选排在前面**，但即使全是硬失败也照样输出——
    报告里 `deliverable` 会说明有没有一个能用的。

    ## `sim_budget`：验证次数是**硬成本**

    `max_candidates` 限的是"生成几个"，`sim_budget` 限的是"验证几个"。
    两者不同：跑阶梯（尤其 V7 运动）比枚举候选贵几个数量级。

    超预算时**停止验证，剩下的候选保留但标 `unverified`**——
    不是丢掉，也不是假装验过。报告里给 `used / limit`。
    """
    unit_list = units_module.derive_from_req(req)
    unresolved_roles = sorted(
        set(req.roles) - {r for u in unit_list for r in u["from_roles"]})

    if not unit_list:
        return {
            "ok": False, "status": "not_ready",
            "requirement": req.to_dict(),
            "candidates": [],
            "reason": ("需求里没有任何能映射到单元的角色；"
                       "可用角色见 units.describe()"),
            "unresolved_roles": unresolved_roles,
        }

    combos = _enumerate_parts(unit_list, req.constraints, max_candidates)
    if not combos:
        misses = {}
        for unit in unit_list:
            needs = set(unit.get("needs") or [])
            if unit["role"] == "mount":
                continue
            if not capability.candidates(needs, req.constraints):
                misses[unit["role"]] = capability.explain_miss(
                    needs, req.constraints, role=str(unit["role"]))
        return {
            "ok": False, "status": "not_ready",
            "requirement": req.to_dict(),
            "candidates": [],
            "reason": ("某个单元没有任何可选型号（能力表没收录，"
                       "或约束把候选砍光了）"),
            "misses": misses,
            "unresolved_roles": unresolved_roles,
        }

    # 链路只依赖**单元划分与需求声明的接口**，不依赖选了哪个零件。
    # 所以提到循环外算一次——放循环里每轮重算，结果一样但白费，
    # 而且会让人误以为"链路随选型变化"。
    links_choice = links_wire(unit_list, req.interfaces)

    seen: set[str] = set()
    candidates: list[Candidate] = []
    budget_left = max(0, int(sim_budget))
    for index, combo in enumerate(combos):
        candidate = Candidate(
            id=f"cand{index + 1:02d}",
            parts=_collapse(combo, unit_list),
            links=links_choice,
            units=unit_list,
            unresolved=[str(l.get("unresolved")) for l in links_choice
                        if l.get("unresolved")] + unresolved_roles,
        )
        signature = candidate.signature()
        if signature in seen:
            continue
        seen.add(signature)

        design = to_design(candidate, req)
        candidate.claims = [
            {"id": c.id, "type": c.type, "intent": c.intent}
            for c in claims_module.derive(design)]
        if run_ladder and budget_left > 0:
            _score(candidate, design)
            budget_left -= 1
        elif run_ladder:
            candidate.unresolved.append(
                f"仿真预算耗尽（{sim_budget} 次），这个候选**没验**")
        candidate.why = _why(candidate)
        candidates.append(candidate)

    ranked = sorted(
        candidates,
        key=lambda c: (c.hard_failures, c.soft_failures, c.unknowns, c.cost))

    deliverable = [c for c in ranked if c.hard_failures == 0]
    return {
        "ok": bool(deliverable),
        "status": "ok" if deliverable else "not_ready",
        "requirement": req.to_dict(),
        "max_candidates": max_candidates,
        "enumerated": len(combos),
        "unique": len(ranked),
        "candidates": [c.to_dict() for c in ranked],
        "deliverable_count": len(deliverable),
        "sim_budget": {
            "limit": int(sim_budget),
            "used": int(sim_budget) - budget_left,
            "exhausted": budget_left <= 0,
            "note": ("验证次数是硬成本；超出预算的候选保留但标 unverified"
                     if budget_left <= 0 else ""),
        },
        "unresolved_roles": unresolved_roles,
        "summary": _summary(ranked, deliverable, unresolved_roles),
    }


def links_wire(unit_list: list[dict[str, Any]],
               interfaces: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    from . import links as links_module
    return links_module.wire(unit_list, interfaces)


def _score(candidate: Candidate, design: DesignModel) -> None:
    """跑阶梯并记硬/软失败数。

    **硬失败只看 V0/V1**（网表成立 / 绝对最大值），与 `ladder.HARD_LEVELS`
    保持一致。几何（V2）算软失败——一个候选因为面积超了被排到后面，
    但它算不算"不可交付"由人决定。
    """
    from . import ladder as ladder_module

    report = ladder_module.run_ladder(design, stop_on_hard=True).to_dict()
    levels = report.get("levels") or []
    candidate.ladder = {str(l.get("level")): str(l.get("status"))
                        for l in levels if isinstance(l, dict)}
    hard = sum(1 for l in levels if isinstance(l, dict)
               and l.get("status") == "fail"
               and str(l.get("level")) in ("V0", "V1"))
    soft = sum(1 for l in levels if isinstance(l, dict)
               and l.get("status") == "fail")
    unknowns = sum(len(l.get("unknown") or [])
                   for l in levels if isinstance(l, dict))
    candidate.hard_failures = hard
    candidate.soft_failures = soft
    candidate.unknowns = unknowns
    candidate.cost = float(sum(
        float(capability.get(p["model"]).get("footprint_mm2") or 0)
        for p in candidate.parts if capability.get(p["model"]) is not None))


#: 协议族 → 能力表里可能的外设组名（按"组名包含"匹配）。
_PROTOCOL_GROUPS: dict[str, tuple[str, ...]] = {
    "SPI": ("SPI",), "UART": ("USART", "UART"), "I2C": ("I2C",),
    "CAN": ("CAN",), "USB": ("USB",), "PWM": ("PWM", "TIM"),
}


def _pick_pin(cap: dict[str, Any], protocol: str,
              used: set[str]) -> str:
    """从能力表的外设组里挑一个**真实存在的引脚名**。挑不到返回 `""`（不猜）。"""
    groups = cap.get("pins") or {}
    if not isinstance(groups, dict):
        return ""
    keys = _PROTOCOL_GROUPS.get(protocol.upper(), (protocol.upper(),))
    for group in sorted(groups):
        label = str(group).upper()
        if not any(key in label for key in keys):
            continue
        pins = groups.get(group) or []
        if not isinstance(pins, list):
            continue
        for pin in pins:
            name = str(pin)
            if name and name not in used:
                used.add(name)
                return name
    return ""


def to_design(candidate: Candidate, req: Requirement) -> DesignModel:
    """把候选变成一份真设计，好让阶梯能验它。

    每块单元一块板；元件放在板内固定位置（**不做布局优化**——
    布局是工程师的活，生成器只保证拓扑成立）。
    """
    boards = [
        Board(kind="custom", name=f"{u['role']} 单元",
              size_mm={"w": 50.0, "h": 50.0},
              id=f"unit:{u['role']}")
        for u in candidate.units if u["role"] != "mount"
    ]
    if not boards:                                    # pragma: no cover
        boards = [Board(kind="custom", size_mm={"w": 50.0, "h": 50.0})]
    design = DesignModel(
        id=f"{req.id}-{candidate.id}", title=req.title or req.id,
        board=boards[0], boards=boards)
    parts: list[Part] = []
    ref_of: dict[str, str] = {}
    pin_of: dict[str, dict[str, Any]] = {}
    model_of: dict[str, str] = {}
    for index, row in enumerate(candidate.parts):
        cap = capability.get(row["model"]) or {}
        pins = sorted({p for group in (cap.get("pins") or {}).values()
                       for p in group})
        owners = list(row.get("units") or [row["unit"]])
        # 一颗料服务多个单元时，它属于**第一个**单元的板；
        # 其余单元通过 `ref_of` 指向同一颗料（不重复放元件）
        board_id = f"unit:{owners[0]}"
        ref = f"U{index + 1}"
        for owner in owners:
            # **两种键都要放**：`ref_of` 在单元建立时用的是 role（`mcu`），
            # 而链路里的 `unit` 字段带前缀（`unit:mcu`，来自 `unit.id`）。
            # 只放 role 会让 `ref_of.get("unit:mcu")` 永远取不到 →
            # 每条链路都 `continue` → **生成的每个候选都没有网表**
            # （P47 实测：materialize(cand) 恒 nets=0，去掉前缀就出现 SPI_1/UART_2）。
            ref_of[str(owner)] = ref
            ref_of[f"unit:{owner}"] = ref
        pin_of[ref] = cap
        model_of[ref] = str(row["model"])
        parts.append(Part(
            ref=ref, kind=row["model"], label=row["model"],
            role="module",
            size_mm={"w": 5.0, "h": 5.0, "d": 1.0},
            physical_source="datasheet",
            placement=Placement(x_mm=10.0 + index * 8.0, y_mm=10.0,
                                board_id=board_id),
            pins=pins or ["1"],
        ))
    design.parts = parts

    # 链路 → 网表
    #
    # **引脚名必须真实存在**：空字符串会让 V0 报 `symbol.pin_unnamed`
    # （"无法核对存在性"）。所以按协议族去能力表里挑同名的外设组：
    # `spi` → `SPI1`/`SPI2`…，`uart` → `USART1`…，取组里第一个没用过的脚。
    #
    # 这**仍然是"每链路 1 个引脚"的示意连接**（真实 SPI 要 SCK/MOSI/MISO/NSS
    # 四根）。网表里标 `"level": "peripheral"`，`handoff` 会把它记成缺数据 ——
    # 不许当成真实原理图。
    used_pins: dict[str, set[str]] = {}
    taken_nets: set[str] = set()
    bus_meta: list[dict[str, Any]] = []
    bus_partial: list[dict[str, Any]] = []
    nets: list[dict[str, Any]] = []
    unresolved_links: list[dict[str, Any]] = []
    for index, link in enumerate(candidate.links):
        if link.get("unresolved"):
            continue
        ref_a = ref_of.get(str((link.get("a") or {}).get("unit") or ""))
        ref_b = ref_of.get(str((link.get("b") or {}).get("unit") or ""))
        if not ref_a or not ref_b:
            unresolved_links.append({"link": link.get("id"),
                                     "reason": "链路端点对不到元件"})
            continue
        if ref_a == ref_b:
            # 两端是同一颗料 —— 芯片内部连接，不是板间链路。
            # 第一版会造出一条 `U1 -- U1` 的自环网，V0 查不出来但物理上是假的。
            continue
        protocol = str(link.get("protocol") or "sig").upper()
        # ---- A2：先试**总线级**（SPI 4 根 / I2C 2 根 / UART 交叉 …）----
        # 引脚来自 AF 表（这个脚真的能做这个功能），不是按名字顺序猜。
        # 凑不出 ≥2 条就退回下面的单脚示意网，并记进 `bus_partial`。
        expanded = None
        if protocol.lower() in bus_module.BUSES and protocol.lower() != "power":
            expanded = bus_module.expand(
                protocol.lower(), ref_a, ref_b,
                model_a=model_of.get(ref_a, ""), model_b=model_of.get(ref_b, ""),
                cap_a=pin_of.get(ref_a) or {}, cap_b=pin_of.get(ref_b) or {},
                used_a=used_pins.setdefault(ref_a, set()),
                used_b=used_pins.setdefault(ref_b, set()),
                taken_names=taken_nets)
        if expanded:
            nets.extend(expanded["nets"])
            bus_meta.append({"link": link.get("id"), "protocol": protocol,
                             "group": expanded["group"],
                             "nets": [row["net"] for row in expanded["nets"]],
                             "missing": expanded["missing"]})
            continue
        if protocol.lower() in bus_module.BUSES:
            bus_partial.append({"link": link.get("id"), "protocol": protocol,
                                "reason": "AF 表/能力表凑不出总线（或用不到 ≥2 条）"})
        pin_a = _pick_pin(pin_of.get(ref_a) or {}, protocol,
                          used_pins.setdefault(ref_a, set()))
        pin_b = _pick_pin(pin_of.get(ref_b) or {}, protocol,
                          used_pins.setdefault(ref_b, set()))
        if not pin_a or not pin_b:
            unresolved_links.append({
                "link": link.get("id"), "protocol": protocol,
                "reason": (f"能力表里 {ref_a}/{ref_b} 没有 {protocol} 组，"
                           "接不到具体引脚")})
            continue
        name = f"{protocol}_{index + 1}"
        taken_nets.add(name)
        nets.append({"net": name, "level": "peripheral", "bus_partial": True,
                     "source": "derived",
                     "pins": [{"ref": ref_a, "pin": pin_a},
                              {"ref": ref_b, "pin": pin_b}]})
    design.nets = nets
    design.meta = dict(getattr(design, "meta", None) or {})
    if bus_meta:
        design.meta["bus"] = bus_meta
    if bus_partial:
        design.meta["bus_partial"] = bus_partial
    if unresolved_links:
        design.meta["unresolved_links"] = unresolved_links
    design.links_list = [dict(l) for l in candidate.links]
    return design


def materialize(candidate: dict[str, Any], req: Requirement) -> DesignModel:
    """报告里的一个候选（`to_dict()` 的形状）→ 一份真设计。

    P22 的 agent 要「generate → 选一个 → put → ladder」串起来，而 `generate()`
    返回的是**报告**（候选摘要），不是 `Candidate` 对象。这里把它还原回去——
    只还原 `to_design` 真正用到的字段（units/parts/links/id），
    其余（claims/ladder/why）是给人看的，还原了也没人读。
    """
    rebuilt = Candidate(
        id=str(candidate.get("id") or ""),
        parts=[dict(p) for p in (candidate.get("parts") or [])],
        links=[dict(l) for l in (candidate.get("links") or [])],
        units=[dict(u) for u in (candidate.get("units") or [])],
        claims=[dict(c) for c in (candidate.get("claims") or [])],
        unresolved=[str(x) for x in (candidate.get("unresolved") or [])],
        hard_failures=int(candidate.get("hard_failures") or 0),
        soft_failures=int(candidate.get("soft_failures") or 0),
        unknowns=int(candidate.get("unknowns") or 0),
        cost=float(candidate.get("cost") or 0.0),
        ladder=dict(candidate.get("ladder") or {}),
        why=str(candidate.get("why") or ""),
    )
    return to_design(rebuilt, req)


def _why(candidate: Candidate) -> str:
    """一句能读的推荐理由。**只陈述事实**，不吹。"""
    bits = []
    if candidate.hard_failures:
        bits.append(f"{candidate.hard_failures} 项硬失败（不会推荐）")
    else:
        bits.append("无硬失败")
    if candidate.soft_failures:
        bits.append(f"{candidate.soft_failures} 项软失败")
    if candidate.unresolved:
        bits.append(f"{len(candidate.unresolved)} 项没接上")
    bits.append(f"面积合计 {candidate.cost:.0f}mm²")
    return "；".join(bits)


def _summary(ranked: list[Candidate], deliverable: list[Candidate],
             unresolved_roles: list[str]) -> str:
    if not ranked:
        return "没有生成任何候选。"
    head = (f"枚举出 {len(ranked)} 个互不相同的候选，"
            f"其中 {len(deliverable)} 个没有硬失败。")
    if unresolved_roles:
        head += f" 未处理的角色：{', '.join(unresolved_roles)}。"
    if deliverable:
        best = deliverable[0]
        head += f" 排第一的是 {best.id}（{best.why}）。"
    else:
        head += " **没有任何候选可用——先解决硬失败再谈选型。**"
    return head


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.generate",
        "approach": "有限枚举 + 批量排序，**没有收敛循环**",
        "stages": ["需求→单元", "单元→元件组合", "组合→链路", "组合→断言"],
        "ranking": ["hard_failures", "soft_failures", "unknowns", "cost"],
        "dedupe": "按（零件清单 + 链路）签名去重，不凑数",
        "honest": ("没候选时返回 not_ready 并解释是哪条过滤砍光的；"
                   "不保证全局最优"),
    }
