"""断言层：**每个元件必须携带一条可执行断言**。

## 为什么需要这一层

现有判定链有一个结构性缺陷：`physical.checks()` 跑完告诉你"没有重叠"，
但你**删掉任何一个元件**，它照样说"没有重叠"——只是检得少了几个。
`budget` 少一个元件也是 `not_ready`，和本来就没数据长得一样。

于是"这个元件为什么在板上"这个问题，在系统的输出里**没有答案**。
删掉它，系统不会变红，只会变安静。**安静和通过长得一样**，
这正是这套系统一直在防的那类假绿灯。

`claims` 把"元件的存在理由"变成可执行断言：

    claim.R12.provides_default_level
      requires: [part.R12]
      check:    net_of(R12).has_default_level == true

删掉 R12 → `requires` 里的 `part.R12` 不存在 → 断言 **fail**。
这就是"删除即失败"（提案 §1.3）。

## 三条设计纪律

1. **断言由规则生成，不由 AI 写。**
   AI 会写出"永远为真"的断言，例如 `exists(part.R12)`——元件不在时
   这条表达式根本求值不到，于是永远不红。规则生成保证断言**必然与
   元件的存在绑定**：每条断言的 `requires` 第一条就是它自己。

2. **三态，不是两态。** `pass` / `fail` / `unknown`。
   缺数据是 `unknown`，不是 `pass`。`unknown` 不进 `reddened`，
   但也不许被渲染成绿色。

3. **`narrow` 是给人看的修复建议**，不是自动修复。
   系统不猜"加个 10k 上去"——那是工程师的判断。

## 与 L5 交接层的关系

`handoff` 需要"哪些默认值是 AI 编的"。`claims` 里 `unknown` 的那些
就是答案的直接来源：断言求不出来，通常是因为元件没申报关键参数。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .model import DesignModel

#: 断言类型。`composite` 是唯一可以嵌套的。
CLAIM_TYPES = ("presence", "capability", "protocol", "numeric", "composite")

#: 优先级。与阶梯的层号对应，供排序与筛选。
PRIORITIES = ("P0", "P1", "P2", "P3")

#: 求值结果。**三态**，没有第四种。
PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Claim:
    """一条断言。

    `requires` 是这个机制的全部要害：**它列出的每个引用都必须存在**，
    否则断言直接 fail，连 `check` 都不求值。这样"元件被删"必然让
    依赖它的断言变红，而不是变成一条求值不到的表达式。
    """

    id: str
    type: str = "presence"
    scope: dict[str, str] = field(default_factory=dict)   # {part|unit|link: id}
    intent: str = ""
    requires: list[str] = field(default_factory=list)     # part.R12 / link.L-1
    check: dict[str, Any] = field(default_factory=dict)
    on_fail: dict[str, Any] = field(default_factory=dict)
    priority: str = "P2"

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("claim.id 不能为空")
        if self.type not in CLAIM_TYPES:
            raise ValueError(f"claim {self.id}: 未知类型 {self.type!r}")
        if self.priority not in PRIORITIES:
            raise ValueError(f"claim {self.id}: 未知优先级 {self.priority!r}")

    @property
    def message(self) -> str:
        return str(self.on_fail.get("message") or self.intent or self.id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "scope": dict(self.scope),
            "intent": self.intent,
            "requires": list(self.requires),
            "check": dict(self.check),
            "on_fail": dict(self.on_fail),
            "priority": self.priority,
        }


@dataclass
class Outcome:
    """一条断言的求值结果 + **为什么**。"""

    claim: Claim
    status: str
    basis: str = ""                    # 一句话说清凭什么
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.claim.id,
            "type": self.claim.type,
            "scope": dict(self.claim.scope),
            "intent": self.claim.intent,
            "priority": self.claim.priority,
            "status": self.status,
            "basis": self.basis,
            "missing": list(self.missing),
            "on_fail": dict(self.claim.on_fail),
        }


@dataclass
class ClaimReport:
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """**全部 pass 才算通过**。unknown 不算。"""
        return bool(self.outcomes) and all(o.status == PASS
                                           for o in self.outcomes)

    def counts(self) -> dict[str, int]:
        out = {PASS: 0, FAIL: 0, UNKNOWN: 0}
        for o in self.outcomes:
            out[o.status] = out.get(o.status, 0) + 1
        return out

    def failures(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == FAIL]

    def unknowns(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status == UNKNOWN]

    def coverage(self) -> dict[str, Any]:
        """**全绿不等于验证过。**

        141 个元件全 `pass`，可能只是因为 141 条断言全是 `connected`
        ——结构上接进了网表，语义上一无所知。覆盖率必须独立报出，
        否则"全绿"会被读成"这东西没问题"。
        """
        by_type: dict[str, int] = {}
        for o in self.outcomes:
            key = o.claim.type
            by_type[key] = by_type.get(key, 0) + 1
        # 语义断言 = 不止"它存在/它接上了"
        semantic = sum(n for t, n in by_type.items()
                       if t in ("capability", "numeric", "protocol",
                                "composite"))
        subjects = {o.claim.scope.get("part") for o in self.outcomes
                    if o.claim.scope.get("part")}
        unusually = {o.claim.scope.get("part") for o in self.outcomes
                     if o.claim.scope.get("part")
                     and o.claim.type in ("capability", "numeric",
                                          "protocol", "composite")}
        return {
            "by_type": by_type,
            "semantic": semantic,
            "structural": len(self.outcomes) - semantic,
            "parts_covered": len(subjects),
            "parts_with_semantic": len(unusually),
            "semantic_ratio": (round(len(unusually) / len(subjects), 3)
                               if subjects else 0.0),
            "note": ("语义断言覆盖 {}/{} 个元件。未覆盖的只是"
                     "结构上接进了网表，**不代表功能正确**"
                     .format(len(unusually), len(subjects))),
        }

    def to_dict(self) -> dict[str, Any]:
        counts = self.counts()
        return {
            "ok": self.ok,
            "count": len(self.outcomes),
            "pass": counts.get(PASS, 0),
            "fail": counts.get(FAIL, 0),
            "unknown": counts.get(UNKNOWN, 0),
            "coverage": self.coverage(),
            "claims": [o.to_dict() for o in self.outcomes],
        }


# ---------------------------------------------------------------------------
# 引用存在性：**删除即失败的支点**
# ---------------------------------------------------------------------------

def _units_of(design: DesignModel) -> list[dict[str, Any]]:
    """单元表。P2 阶段还没有 `units` 字段，从 `meta` 兼容读取。"""
    raw = getattr(design, "units", None)
    if isinstance(raw, list):
        return [u for u in raw if isinstance(u, dict)]
    meta = getattr(design, "meta", None) or {}
    raw = meta.get("units")
    return [u for u in raw if isinstance(u, dict)] if isinstance(raw, list) else []


def _links_of(design: DesignModel) -> list[dict[str, Any]]:
    """板间链路表。同样先走 `meta`，P5 落地后自动切到正式字段。"""
    raw = getattr(design, "links", None)
    if isinstance(raw, list):
        return [l for l in raw if isinstance(l, dict)]
    meta = getattr(design, "meta", None) or {}
    raw = meta.get("links")
    return [l for l in raw if isinstance(l, dict)] if isinstance(raw, list) else []


def exists(design: DesignModel, ref: str) -> bool:
    """`part.R12` / `link.L-1` / `unit.u1` / `net.5V` 是否存在。

    未知前缀一律 False——**不是"没法判断所以放过"**。写错前缀的
    断言必须变红，否则拼写错误会静默地让断言失效。
    """
    kind, _, key = str(ref or "").partition(".")
    if not key:
        return False
    if kind == "part":
        return design.part(key) is not None
    if kind == "link":
        return any(str(l.get("id") or "") == key for l in _links_of(design))
    if kind == "unit":
        return any(str(u.get("id") or "") == key for u in _units_of(design))
    if kind == "net":
        return any(str(n.get("net") or "") == key for n in design.nets)
    return False


def missing_refs(design: DesignModel, claim: Claim) -> list[str]:
    return [r for r in claim.requires if not exists(design, r)]


# ---------------------------------------------------------------------------
# 网表查询（给 check 用，不做表达式解析）
# ---------------------------------------------------------------------------

def nets_of(design: DesignModel, ref: str) -> list[str]:
    """元件接在哪些网络上（去重，保持网表顺序）。"""
    seen: list[str] = []
    for entry in design.nets:
        name = str(entry.get("net") or "")
        if name in seen:
            continue
        for p in entry.get("pins", []):
            if isinstance(p, dict) and p.get("ref") == ref:
                seen.append(name)
                break
    return seen


def pins_on_net(design: DesignModel, net: str) -> list[dict[str, str]]:
    for entry in design.nets:
        if str(entry.get("net") or "") == net:
            return [p for p in entry.get("pins", []) if isinstance(p, dict)]
    return []


def _declares(part: Any, *keys: str) -> float | None:
    """元件申报的某个数值参数（`electrical` 里）。**没有就是没有。**"""
    elec = getattr(part, "electrical", None) or {}
    for key in keys:
        value = elec.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


# ---------------------------------------------------------------------------
# 各类型求值
# ---------------------------------------------------------------------------

def _eval_presence(claim: Claim, design: DesignModel) -> tuple[str, str]:
    """存在性。`requires` 已经在外面查过了，这里查 `check.wants`。

    `check.kind` 可以进一步指名更具体的存在性语义（`attached`）——
    分发表按 `type` 走，所以这里要自己转发，否则 `attached` 会被
    降级成恒真的"依赖引用齐全"。
    """
    kind = str(claim.check.get("kind") or "")
    if kind == "attached":
        return _eval_attached(claim, design)
    wants = claim.check.get("wants")
    if not wants:
        return PASS, "依赖引用齐全"
    ref = str(wants)
    if exists(design, ref):
        return PASS, f"{ref} 存在"
    return FAIL, f"{ref} 不存在"


def _eval_numeric(claim: Claim, design: DesignModel) -> tuple[str, str]:
    """数值断言。**只支持显式写出的比较，不做字符串表达式求值。**

    `check` 形状：
        {"kind": "numeric", "subject": "part.R12", "param": "max_v",
         "op": ">=", "value": 5.0}

    `param` 走 `electrical`；`subject` 走 `requires` 已确认存在的对象。
    """
    subject = str(claim.check.get("subject") or "")
    param = str(claim.check.get("param") or "")
    op = str(claim.check.get("op") or ">=")
    target = claim.check.get("value")

    kind, _, key = subject.partition(".")
    if kind != "part" or not key:
        return UNKNOWN, f"subject {subject!r} 不是元件引用，无法取值"
    part = design.part(key)
    if part is None:
        return FAIL, f"{key} 不存在"

    actual = _declares(part, param)
    if actual is None:
        return UNKNOWN, f"{key} 未申报 {param}（缺数据 ≠ 通过）"
    if not isinstance(target, (int, float)):
        return UNKNOWN, f"断言的目标值不是数字：{target!r}"

    table = {
        ">=": actual >= target, ">": actual > target,
        "<=": actual <= target, "<": actual < target,
        "==": actual == target, "!=": actual != target,
    }
    if op not in table:
        return UNKNOWN, f"不支持的比较符 {op!r}"
    verdict = table[op]
    return (PASS if verdict else FAIL,
            f"{key}.electrical.{param} = {actual:g} {op} {float(target):g}")


def _eval_net_level(claim: Claim, design: DesignModel) -> tuple[str, str]:
    """网络是否有确定电平：**有元件自愿驱动它**，或它是一条电源轨。

    悬空网络（一个引脚都没接，或只接了一个引脚）没有确定电平。
    """
    net = str(claim.check.get("net") or "")
    if not net:
        return UNKNOWN, "断言没指定网络"
    pins = pins_on_net(design, net)
    if not pins:
        return FAIL, f"网络 {net} 不存在或没有引脚"

    refs = {str(p.get("ref") or "") for p in pins}
    # 电源轨按名字认（GND / 5V / 3V3 …）——这些不需要"驱动元件"。
    if re.match(r"^(GND|AGND|DGND|PGND|VSS|0V|GROUND|3V3|5V|12V|VBUS|"
                r"VCC|VDD|\+3V3|\+5V)[A-Z0-9_]*$", net.strip().upper()):
        return PASS, f"{net} 是电源/地网络，电平由定义给出"
    if len(refs) < 2:
        return (FAIL,
                f"{net} 只有 {len(refs)} 个引脚（{', '.join(sorted(refs))}），"
                f"没有驱动源，电平不确定")
    return PASS, f"{net} 连接 {len(refs)} 个元件：{', '.join(sorted(refs))}"


def _eval_attached(claim: Claim, design: DesignModel) -> tuple[str, str]:
    """元件是否**真的**接进了网表。

    这条不是恒真：`requires` 只保证元件对象存在，而一个元件可以存在却
    一个网络都不接（导入器漏引脚、或网表被裁剪）。孤立元件在物理检查里
    不会被点名——它不重叠、不越界，只是安静地待在板上。
    """
    ref = str(claim.check.get("subject") or "").partition(".")[2]
    part = design.part(ref) if ref else None
    if part is None:
        return FAIL, f"{ref or '?'} 不存在"
    nets = nets_of(design, ref)
    if not nets:
        return (FAIL,
                f"{ref} 没有接入任何网络——孤立元件，"
                f"物理检查不会点名它")
    return PASS, f"{ref} 接入 {len(nets)} 个网络：{', '.join(nets)}"


def _eval_powered(claim: Claim, design: DesignModel) -> tuple[str, str]:
    """元件是否获得合规供电：它必须挂到至少一条电源网络，且电压有依据。"""
    ref = str(claim.check.get("subject") or "").partition(".")[2]
    part = design.part(ref) if ref else None
    if part is None:
        return FAIL, f"{ref or '?'} 不存在"

    rails = [n for n in nets_of(design, ref) if _looks_like_rail(n)]
    if not rails:
        return FAIL, f"{ref} 没有接到任何电源网络（只接了信号网）"

    # 电压有依据 = 元件申报了，或网名写明了。
    for net in rails:
        declared = _declares(part, "v_in", "vin", "v_rail", "v_supply")
        if declared is not None:
            return PASS, (f"{ref} 接 {net}，自身申报供电 "
                          f"{declared:g}V")
        if re.match(r"^(3V3|3\.3V|5V|5\.0V|1V8|12V|VBUS|\+3V3|\+5V)",
                    net.strip().upper()):
            return PASS, f"{ref} 接 {net}（网名已写明电压）"
    return (UNKNOWN,
            f"{ref} 接 {', '.join(rails)}，但电压既未申报、网名也未写明"
            f"（缺数据 ≠ 通过）")


_RAIL_RE = re.compile(
    r"^(GND|AGND|DGND|PGND|VSS|0V|GROUND|3V3|3\.3V|5V|5\.0V|1V8|2V5|"
    r"12V|VBUS|VBAT\d?S?|VCC[A-Z0-9_]*|VDD[A-Z0-9_]*|VIN|VMOT|"
    r"\+3V3|\+5V|\+\d+V)[A-Z0-9_]*$")


def _looks_like_rail(net: str) -> bool:
    return bool(_RAIL_RE.match(net.strip().upper()))


def _eval_protocol(claim: Claim, design: DesignModel) -> tuple[str, str]:
    """协议断言：两边是否真的接在同一批网络上（I2C 的 SDA/SCL 等）。"""
    a = str(claim.check.get("a") or "").partition(".")[2]
    b = str(claim.check.get("b") or "").partition(".")[2]
    shared_required = [str(x) for x in claim.check.get("shared", [])]

    if not a or not b:
        return UNKNOWN, "协议断言缺 a/b 两端引用"
    nets_a = set(nets_of(design, a))
    nets_b = set(nets_of(design, b))
    shared = nets_a & nets_b
    if not shared:
        return FAIL, f"{a} 与 {b} 没有共用网络（未连接）"
    if shared_required:
        lack = [n for n in shared_required if n not in shared]
        if lack:
            return (FAIL,
                    f"{a} 与 {b} 共用 {sorted(shared)}，但缺 "
                    f"{', '.join(lack)}")
        return PASS, f"{a}↔{b} 共用 {', '.join(shared_required)}"
    return PASS, f"{a}↔{b} 共用 {', '.join(sorted(shared))}"


def _eval_composite(claim: Claim, design: DesignModel) -> tuple[str, str]:
    """`all_of`：**任一 fail 即 fail**；否则有 unknown 就是 unknown。"""
    subs = claim.check.get("all_of") or []
    if not isinstance(subs, list) or not subs:
        return UNKNOWN, "composite 没有 all_of"
    reasons: list[str] = []
    saw_unknown = False
    for sub in subs:
        sub_claim = sub if isinstance(sub, Claim) else _as_claim(sub, claim)
        if sub_claim is None:
            return UNKNOWN, "all_of 里有无法解析的断言"
        status = evaluate(sub_claim, design).status
        if status == FAIL:
            return FAIL, f"子断言 {sub_claim.id} 失败"
        if status == UNKNOWN:
            saw_unknown = True
            reasons.append(sub_claim.id)
    if saw_unknown:
        return UNKNOWN, f"子断言未判定：{', '.join(reasons)}"
    return PASS, f"{len(subs)} 条子断言全部通过"


def _as_claim(raw: Any, parent: Claim) -> Claim | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Claim(
            id=str(raw.get("id") or f"{parent.id}.sub"),
            type=str(raw.get("type") or "presence"),
            scope=dict(raw.get("scope") or {}),
            intent=str(raw.get("intent") or ""),
            requires=list(raw.get("requires") or []),
            check=dict(raw.get("check") or {}),
            on_fail=dict(raw.get("on_fail") or {}),
            priority=str(raw.get("priority") or parent.priority),
        )
    except ValueError:
        return None


_EVALUATORS: dict[str, Callable[[Claim, DesignModel], tuple[str, str]]] = {
    "presence": _eval_presence,
    "numeric": _eval_numeric,
    "protocol": _eval_protocol,
    "composite": _eval_composite,
    # capability 走具体 check.kind（net_level / powered）
    "capability": lambda c, d: _eval_capability(c, d),
}


def _eval_capability(claim: Claim,
                     design: DesignModel) -> tuple[str, str]:
    kind = str(claim.check.get("kind") or "")
    if kind == "net_level":
        return _eval_net_level(claim, design)
    if kind == "powered":
        return _eval_powered(claim, design)
    if kind == "attached":
        return _eval_attached(claim, design)
    return UNKNOWN, f"capability 的子类型 {kind!r} 没有求值器"


def evaluate(claim: Claim, design: DesignModel) -> Outcome:
    """求值一条断言。**`requires` 先查——这是删除即失败的支点。**"""
    missing = missing_refs(design, claim)
    if missing:
        return Outcome(
            claim, FAIL,
            basis=f"依赖的 {'、'.join(missing)} 不存在——"
                  f"这条断言因它的缺失而变红",
            missing=missing)

    fn = _EVALUATORS.get(claim.type)
    if fn is None:
        return Outcome(claim, UNKNOWN, basis=f"未知类型 {claim.type!r}")
    try:
        status, basis = fn(claim, design)
    except Exception as exc:                       # noqa: BLE001
        # 求值器抛异常 = 这条断言**没有结论**，绝不能记 pass。
        return Outcome(claim, UNKNOWN,
                       basis=f"求值抛异常：{type(exc).__name__}: {exc}")
    if status not in (PASS, FAIL, UNKNOWN):
        return Outcome(claim, UNKNOWN, basis=f"求值器返回了非法状态 {status!r}")
    return Outcome(claim, status, basis=basis)


# ---------------------------------------------------------------------------
# 断言来源：规则生成
# ---------------------------------------------------------------------------

#: 归因结论（`justify` 的 group.kind）→ 断言工厂。
#:
#: ## 为什么断言必须消费归因
#
# 第一版只有 `role == "pullup"` 这一个语义入口，而实测 `示例-单板` 的
# 141 个元件里**没有一个**是 `pullup` 角色（全是导入器兜底值）。
# 于是 `hw_assert --remove R1` 只报出一条 `claim.R1.connected`——
# 那条断言只说"R1 还在"，不说"R1 是 3.3V 到 PB10 的上拉"。
# 删掉它，系统只是**变安静**，没有**变红**。这恰恰是本模块开头
# 那段话说要治的病，却在第一版里又犯了一遍。
#
# `justify` 从拓扑算出了"这个 1kΩ 是 PB10 的上拉"。那是真知识，
# 必须被断言消费，否则它只是报告里的一句话，不参与判定。
#
# ## 只消费 `inferred_by_topology` 及以上的结论
#
# `unknown` 的元件不生成新断言——拿"我判不出"去生成"它应该做某事"
# 是伪造。它们仍然保留 `connected`，如实说明"只知道它接进了网表"。
_JUSTIFY_RULES: tuple[tuple[str, Callable[[Any, list[str]], Claim | None]], ...] = (
    ("pullup", lambda p, n: _pullup_claim(p, n)),
    ("pulldown", lambda p, n: _pullup_claim(p, n)),
    ("pull_to_rail", lambda p, n: _pullup_claim(p, n)),
    ("switch_to_ground", lambda p, n: _switch_claim(p, n)),
    ("high_side_switch", lambda p, n: _role_claim(
        p, n, "switches_power", "把输入电源接到下游")),
    ("low_side_switch", lambda p, n: _role_claim(
        p, n, "switches_to_ground", "把负载网拉到地")),
    ("decoupling", lambda p, n: _decoupling_claim(p, n)),
    ("current_sense", lambda p, n: _series_claim(p, n, "current_sense")),
    ("usb_series", lambda p, n: _series_claim(p, n, "usb_series")),
    ("series", lambda p, n: _series_claim(p, n, "series")),
    ("led_limit", lambda p, n: _series_claim(p, n, "led_limit")),
    ("rail_link", lambda p, n: _role_claim(
        p, n, "links_rails", "连接两条电源轨")),
    ("power_or", lambda p, n: _role_claim(
        p, n, "prevents_backfeed", "阻止电流从下游倒灌回上游轨")),
    ("isolating_diode", lambda p, n: _role_claim(
        p, n, "isolates_reverse", "单向导通、阻止反向电流")),
    ("signal_diode", lambda p, n: _role_claim(
        p, n, "clamps_signal", "对信号做单向钳位/整形")),
    # 判出"受某信号控制的节点"时，**至少**能断言"这条控制通路存在"。
    # 品类（灯/传感器/驱动）确实判不出，但"U1 的 LIGHTING 控制着它"
    # 是从拓扑读出来的事实——删掉这个节点，那条控制通路就断了。
    ("controlled_node", lambda p, n: _role_claim(
        p, n, "receives_control", "接收上游控制信号")),
    ("indicator", lambda p, n: _role_claim(
        p, n, "indicates", "把电流变化表现为可见光")),
    ("reset_cap", lambda p, n: _role_claim(
        p, n, "holds_reset", "在上电瞬间维持复位电平")),
    ("boot_cap", lambda p, n: _role_claim(
        p, n, "boots_mode", "在启动瞬间决定 BOOT 电平")),
    ("osc_cap", lambda p, n: _role_claim(
        p, n, "loads_crystal", "为晶振提供负载电容")),
    ("signal_cap", lambda p, n: _role_claim(
        p, n, "filters_signal", "为信号提供对地时间常数")),
    ("debounce_cap", lambda p, n: _role_claim(
        p, n, "debounces", "吸收按键抖动")),
    ("snubber", lambda p, n: _role_claim(
        p, n, "snubs_switching", "抑制开关节点振铃")),
    ("power_inductor", lambda p, n: _role_claim(
        p, n, "stores_energy", "在开关周期内传递能量")),
    ("clock_source", lambda p, n: _role_claim(
        p, n, "oscillates", "为时钟网络提供振荡源")),
    ("regulator", lambda p, n: _role_claim(
        p, n, "regulates", "把输入轨变换为输出轨")),
)


def _signal_of(part: Any, nets: list[str]) -> str:
    """这条断言关心的**信号网**（排除电源轨与地）。"""
    from .justify import is_ground, is_rail
    for net in nets:
        if is_ground(net) or is_rail(_DESIGN_OF.get(id(part)), net):
            continue
        return net
    return nets[0] if nets else ""


#: `claims.py` 的断言工厂没有拿到 `design`（它们只要 part + nets）。
#: `_signal_of` 需要 design 才能判轨，所以用这个模块级弱引用桥一下。
#: **不放进 Claim 里**：Claim 是要序列化到 claims.json 的，
#: 塞一个 design 引用进去会让产物不可序列化。
_DESIGN_OF: dict[int, DesignModel] = {}


def _role_claim(part: Any, nets: list[str], suffix: str,
                text: str) -> Claim | None:
    """通用归因断言：删掉它，`text` 这件事没人做。

    `requires` **必须含它自己**（`part.<ref>`），这样删掉元件断言就
    fail；同时含它所在的第一条网，删掉那条网也 fail。
    """
    if not nets:
        return None
    anchor = nets[0]
    return Claim(
        id=f"claim.{part.ref}.{suffix}",
        type="capability", scope={"part": part.ref, "net": anchor},
        intent=f"{part.ref} {text}",
        requires=[f"part.{part.ref}", f"net.{anchor}"],
        check={"kind": "attached", "subject": f"part.{part.ref}"},
        on_fail={"message": f"{part.ref} 缺失 →「{text}」这件事没人做",
                 "narrow": [{"action": "replace_part", "part": part.ref}]},
        priority="P2")


def _switch_claim(part: Any, nets: list[str]) -> Claim | None:
    """开关：删掉它，被控信号失去一条通路。"""
    signal = _signal_of(part, nets)
    if not signal:
        return None
    return Claim(
        id=f"claim.{part.ref}.switches",
        type="presence", scope={"part": part.ref, "net": signal},
        intent=f"{part.ref} 把 {signal} 接通到地/轨",
        requires=[f"part.{part.ref}", f"net.{_net_of(part, nets)}"],
        check={"kind": "attached", "subject": f"part.{part.ref}"},
        on_fail={"message": f"{part.ref} 缺失 → {signal} 的开关动作失效",
                 "narrow": [{"action": "replace_part", "part": part.ref}]},
        priority="P2")


def _net_of(part: Any, nets: list[str]) -> str:
    return nets[0] if nets else ""


def _decoupling_claim(part: Any, nets: list[str]) -> Claim | None:
    """去耦电容：删掉它，那条轨失去一个本地回流路径。"""
    from .justify import is_ground, is_rail
    rail = ""
    for net in nets:
        if is_ground(net):
            continue
        if is_rail(_DESIGN_OF.get(id(part)), net):
            rail = net
            break
    if not rail:
        return None
    return Claim(
        id=f"claim.{part.ref}.decouples",
        type="presence", scope={"part": part.ref, "net": rail},
        intent=f"{part.ref} 为 {rail} 提供去耦/储能",
        requires=[f"part.{part.ref}", f"net.{rail}"],
        check={"kind": "attached", "subject": f"part.{part.ref}"},
        on_fail={"message": f"{part.ref} 缺失 → {rail} 少一个本地回流路径",
                 "narrow": [{"action": "add_part", "role": "decoupling"}]},
        priority="P3")


def _series_claim(part: Any, nets: list[str], kind: str) -> Claim | None:
    """串联类：删掉它，两端直连。"""
    if len(nets) < 2:
        return None
    a, b = nets[0], nets[1]
    return Claim(
        id=f"claim.{part.ref}.in_path",
        type="presence", scope={"part": part.ref, "net": a},
        intent=f"{part.ref} 串在 {a} 与 {b} 之间（{kind}）",
        requires=[f"part.{part.ref}", f"net.{a}"],
        check={"kind": "attached", "subject": f"part.{part.ref}"},
        on_fail={"message": f"{part.ref} 缺失 → {a} 与 {b} 直连，{kind} 失效",
                 "narrow": [{"action": "replace_part", "part": part.ref}]},
        priority="P2")


def _pullup_claim(part: Any, nets: list[str] | None = None) -> Claim:
    # 上拉的对象是**信号网**，不是它另一端接的电源轨。
    signal = [n for n in (nets or []) if not _looks_like_rail(n)]
    net = signal[0] if signal else (nets[0] if nets else "")
    return Claim(
        id=f"claim.{part.ref}.provides_default_level",
        type="capability", scope={"part": part.ref, "net": net},
        intent=(f"{part.ref} 为 {net} 提供默认电平" if net
                else f"{part.ref} 为所在网络提供默认电平"),
        requires=[f"part.{part.ref}"] + ([f"net.{net}"] if net else []),
        check={"kind": "net_level", "net": net},
        on_fail={"message": f"{part.ref} 缺失 → 该网络悬空，电平不确定",
                 "narrow": [{"action": "add_part", "role": "pullup"}]},
        priority="P2")


def _powered_claim(part: Any) -> Claim:
    return Claim(
        id=f"claim.{part.ref}.powered",
        type="capability", scope={"part": part.ref},
        intent=f"{part.ref} 获得合规供电",
        requires=[f"part.{part.ref}"],
        check={"kind": "powered", "subject": f"part.{part.ref}"},
        on_fail={"message": f"{part.ref} 没接电源或电压无依据",
                 "narrow": [{"action": "check_power", "part": part.ref}]},
        priority="P1")


def _rated_claim(part: Any) -> Claim:
    return Claim(
        id=f"claim.{part.ref}.rated_voltage",
        type="numeric", scope={"part": part.ref},
        intent=f"{part.ref} 的额定电压已申报",
        requires=[f"part.{part.ref}"],
        check={"kind": "numeric", "subject": f"part.{part.ref}",
               "param": "max_v", "op": ">", "value": 0},
        on_fail={"message": f"{part.ref} 未申报 max_v，"
                            f"绝对最大值无法判定",
                 "narrow": [{"action": "declare", "field": "electrical.max_v"}]},
        priority="P2")


def _connected_claim(part: Any, nets: list[str]) -> Claim:
    net = nets[0]
    return Claim(
        id=f"claim.{part.ref}.connected",
        type="presence", scope={"part": part.ref, "net": net},
        intent=f"{part.ref} 至少接入一个网络",
        requires=[f"part.{part.ref}"],
        check={"kind": "attached", "subject": f"part.{part.ref}"},
        on_fail={"message": f"{part.ref} 是孤立元件，不参与任何网络",
                 "narrow": [{"action": "wire_or_remove", "part": part.ref}]},
        priority="P3")


#: 元件身份是否**可判**。判不出身份时不该给它生成"供电是否合规"这类断言
#: ——那会变成纯粹的猜测，而猜测会以 fail 的形状出现，被人当成真问题。
#:
#: 实测依据：`示例-单板` 141 个元件里 `role` 分布是 component 125 /
#: module 13 / connector 3，**全部是导入器的兜底默认值**，
#: 没有一个是真实语义角色。三条 `role=module` 的按钮模块因此被误判成
#: "没接电源"——按钮本来就不需要电源。
def identifiable(part: Any) -> tuple[bool, str]:
    """能不能判断这个元件是什么。返回 (可否, 依据)。

    三档（与 `pins.py` 同一套精神）：
      * `role` 是**真实语义角色**（不是兜底默认值）
      * `kind`/`label` 能对上已知家族（走 `ladder.family_of`）
      * `electrical` 里有申报（元件自己说了算）
    """
    from .ladder import family_of

    role = str(getattr(part, "role", "") or "")
    if role and role not in DEFAULT_ROLES:
        return True, f"role={role}"

    family = family_of(part)
    if family is not None:
        return True, f"家族 {family}"

    elec = getattr(part, "electrical", None) or {}
    if any(isinstance(v, (int, float)) and not isinstance(v, bool)
           for v in elec.values()):
        return True, "electrical 已申报"

    return False, "身份未识别（哈希 id + 兜底角色）"


#: 导入器写的兜底角色。**不是语义信息**，不能拿来推断言。
#:
#: `module` 在 `示例-单板` 里 13 个，全部是哈希 id 的元件被立创 EDA
#: 导入器统一标成 `module` —— 它表示"这是个模块"（废话），
#: 不表示"这是个需要供电的模块"。3 个按钮模块因此被误判。
DEFAULT_ROLES = ("", "component", "part", "unknown", "generic", "module")

#: 角色 → 断言工厂。全用 `role` 精确匹配，**不做模糊推断**。
CLAIM_RULES: tuple[tuple[Callable[[Any], bool],
                         Callable[[Any], Claim]], ...] = (
    (lambda p: getattr(p, "role", "") == "pullup", _pullup_claim),
    # 供电断言只给**身份可判**的元件。身份不明时生成供电断言 =
    # 拿默认角色冒充语义，误报会以 fail 的形状出现（实测：3 个按钮模块
    # 被判"没接电源"）。
    (lambda p: (getattr(p, "role", "") in
                ("mcu", "module", "sensor", "servo")
                and identifiable(p)[0]),
     _powered_claim),
)


def derive(design: DesignModel) -> list[Claim]:
    """从设计**推导**断言。顺序稳定（按 parts 顺序），便于 diff。

    规则：
      1. 每个接在网络上的元件 → 一条 `connected`（presence）
      2. 匹配 `CLAIM_RULES` 的元件 → 各自角色的断言
      3. **归因判出了具体作用的元件 → 一条对应作用的断言**
         （见 `_JUSTIFY_RULES`；这是"删除即失败"真正变红的地方）
      4. 有 `electrical` 的元件 → 一条 `rated_voltage`（数值可判）

    ## 第 3 条为什么必须有

    只靠第 1 条时，删掉 `R1` 只会让 `claim.R1.connected` 变红——
    那条断言说的是"R1 接进了网表"。它对"R1 是 3.3V 到 PB10 的上拉"
    一无所知。结果就是：删掉一个上拉电阻，系统只少了一条断言，
    却没有一条断言说"PB10 现在悬空了"。

    第 3 条把 `justify` 从拓扑算出的结论变成**可执行断言**。
    `justify` 说 R1 是 PB10 的上拉，这里就生成
    `claim.R1.provides_default_level`，它的 `requires` 含
    `part.R1` 与 `net.PB10`——删 R1 或删 PB10 那条网，
    断言都会 fail。**这才是删除即失败。**
    """
    out: list[Claim] = []
    seen: set[str] = set()

    # 归因需要 design 才能判轨/追网，而断言工厂只拿 part：
    # 用模块级字典把 design 递进去（见 `_DESIGN_OF` 的注释）。
    _DESIGN_OF.clear()
    for part in design.parts:
        _DESIGN_OF[id(part)] = design

    try:
        attributions = _attribution_map(design)
    finally:
        pass

    for part in design.parts:
        nets = nets_of(design, part.ref)
        if nets:
            claim = _connected_claim(part, nets)
            if claim.id not in seen:
                seen.add(claim.id)
                out.append(claim)

        for matches, factory in CLAIM_RULES:
            if not matches(part):
                continue
            claim = (factory(part, nets)
                     if factory is _pullup_claim else factory(part))
            if claim.id in seen:
                continue
            seen.add(claim.id)
            out.append(claim)
            break

        # 归因驱动的断言：只在判出了**具体作用**时生成。
        group_kind = attributions.get(part.ref)
        if group_kind is not None:
            for wanted, maker in _JUSTIFY_RULES:
                if wanted != group_kind:
                    continue
                try:
                    claim = maker(part, nets)
                except Exception:  # noqa: BLE001
                    claim = None
                if claim is not None and claim.id not in seen:
                    seen.add(claim.id)
                    out.append(claim)
                break

        if (part.electrical or {}).get("max_v") is not None:
            claim = _rated_claim(part)
            if claim.id not in seen:
                seen.add(claim.id)
                out.append(claim)

    _DESIGN_OF.clear()
    return out


def _attribution_map(design: DesignModel) -> dict[str, str]:
    """`ref` → 归因的 `group.kind`。**判不出（unknown）的不进表。**

    只收 `inferred_by_topology` 及以上——拿"我判不出"去生成
    "它应该做某事"是伪造。
    """
    try:
        from . import justify as justify_module
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    try:
        report = justify_module.run(design)
    except Exception:  # noqa: BLE001
        return {}
    for row in report.get("parts") or []:
        if not isinstance(row, dict):
            continue
        justification = row.get("justification") or {}
        if str(justification.get("confidence") or "") == "unknown":
            continue
        group = row.get("group") or {}
        kind = str(group.get("kind") or "")
        ref = str(row.get("ref") or "")
        if kind and ref:
            out[ref] = kind
    return out


# ---------------------------------------------------------------------------
# 求值 + 报告
# ---------------------------------------------------------------------------

def check(design: DesignModel,
          claims: list[Claim] | None = None) -> ClaimReport:
    """求值全部断言。不给 `claims` 就现场推导。"""
    items = claims if claims is not None else derive(design)
    return ClaimReport([evaluate(c, design) for c in items])


def describe() -> dict[str, Any]:
    """给 CLI / 工具自省用。"""
    return {
        "module": "hw_velxio.design.claims",
        "claim_types": list(CLAIM_TYPES),
        "priorities": list(PRIORITIES),
        "states": [PASS, FAIL, UNKNOWN],
        "rules": len(CLAIM_RULES),
        "note": ("断言由规则生成，不由 AI 写；requires 缺失 = fail，"
                 "这是删除即失败的支点"),
    }


# ---------------------------------------------------------------------------
# 归因：删掉一个元件，哪些断言变红
# ---------------------------------------------------------------------------

def removal_impact(design: DesignModel, ref: str) -> dict[str, Any] | None:
    """把 `ref` 从**内存副本**里删掉，看哪些断言从 pass 变 fail。

    不写盘、不改原对象。返回 None 表示该元件本来就不存在。
    """
    if design.part(ref) is None:
        return None

    claims = derive(design)
    before = {c.id: evaluate(c, design).status for c in claims}

    clone = DesignModel.from_dict(design.to_dict())
    clone.parts = [p for p in clone.parts if p.ref != ref]
    # 网表里指向它的引脚也必须一起删，否则 V0 会报"引用了不存在的元件"，
    # 那是另一类问题（符号不成立），会掩盖我们要看的断言变红。
    cleaned: list[dict[str, Any]] = []
    for entry in clone.nets:
        pins = [p for p in entry.get("pins", [])
                if not (isinstance(p, dict) and p.get("ref") == ref)]
        if not pins:
            continue
        item = dict(entry)
        item["pins"] = pins
        cleaned.append(item)
    clone.nets = cleaned
    # 走线按网络组织（`Wire` 没有端点字段），所以只保留还在网表里的网络。
    live_nets = {str(e.get("net") or "") for e in cleaned}
    clone.wires = [w for w in clone.wires if w.net in live_nets]

    after = {c.id: evaluate(c, clone).status for c in claims}

    reddened = [cid for cid in before
                if before[cid] == PASS and after[cid] == FAIL]
    unchanged = [cid for cid in before
                 if before[cid] == after[cid] == PASS]
    was_unknown = [cid for cid in before if before[cid] == UNKNOWN]

    return {
        "removed": ref,
        "claims": len(claims),
        "reddened": reddened,
        "count": len(reddened),
        "unchanged_pass": len(unchanged),
        "was_unknown": len(was_unknown),
        "note": ("这些断言因该元件缺失而变红——这就是它的存在理由。"
                 if reddened else
                 "没有任何断言因该元件缺失而变红——它的存在理由未被表达，"
                 "需要补断言规则。"),
    }
