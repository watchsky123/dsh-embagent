"""交接层 —— 把散在各处的「未验证」聚成一份人能照着做的清单。

## 为什么需要独立一层

跑完 `ladder` / `claims` / `budget` / `coverify` 之后，"还没验证的东西"
**散在四个文件里，四种 schema，四种措辞**：

    阶梯：  V5 数字仿真 not_run        （因为没接固件）
    断言：  claim.U1025.powered unknown（因为没申报电流）
    预算：  confidence: none            （因为 0/141 有电流数据）
    协同：  指向精度未声明，按默认 1.0° 算

对一个要打板的人来说，这四句话指向同一件事：**你必须先确认某些事**。
但没有任何一个界面把它们排在一起，于是最常发生的是：
人看了阶梯的"V2 通过"，就认为可以打板了——而 V3 是 `not_run`。

这一层只做聚合与排序，**不重新跑任何判定**。理由有两条：

1. 判定很贵（V2 跑体素几何），看一眼交接单不该触发全量重算；
2. 交接单必须**对着某一版产物**。每次看都重算，等于每次看的都是
   不同的东西，"我昨天确认过了"就无从谈起。

## 五类，不是四张表

输出分五类，因为**"你要做什么"根本不同**：

| 类 | 含义 | 人的动作 |
|---|---|---|
| `blocking` | 打板必炸 | 修设计，否则别打 |
| `assumed` | 机器用了默认值 | 确认或替换那个数 |
| `missing_data` | 元件没申报关键参数 | 补数据 |
| `unverifiable` | 我们根本没有这套模型 | 自己实测 |
| `orphan` | 生成了但没接线 | 确认是故意的还是漏了 |

第一类与第二类的区别不是严重程度，而是**责任归属**：
`blocking` 是设计的错，`assumed` 是数据没给。混在一张表里，
人会以为"都是要改设计"，然后去改本来正确的设计。

## 一句话纪律

**空表也要说"空"**。四张表全空时不能只输出一个空文件——
那和"没跑"长得一样。所以每类都带 `checked` 与 `basis`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .model import DesignModel

#: 五类的顺序 = 报告里的顺序 = "先看哪个"的顺序。
CATEGORIES = ("blocking", "missing_data", "assumed", "unverifiable", "orphan")

CATEGORY_TITLE = {
    "blocking": "一、必须修好才能打板（阻断）",
    "missing_data": "二、缺数据：机器不知道，你需要补",
    "assumed": "三、机器用了默认值（需替换或确认）",
    "unverifiable": "四、这套系统不会验，只能你自己实测",
    "orphan": "五、生成了但没接线（确认是否故意）",
}


@dataclass
class Item:
    """一条待确认事项。

    `action` 是**给人做的动作**，必须可执行（"给出 5V 轨每个负载的
    电流"），不能是"注意供电"这种无法勾选的句子。
    """

    category: str
    title: str
    status: str = ""
    action: str = ""
    risk: str = ""
    basis: str = ""            # 这条从哪儿来的（文件 + 字段）

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "title": self.title,
            "status": self.status,
            "action": self.action,
            "risk": self.risk,
            "basis": self.basis,
        }


@dataclass
class Handoff:
    items: list[Item] = field(default_factory=list)
    #: 每类都记"检查了几项"，**0 与"没查"是两回事**
    checked: dict[str, int] = field(default_factory=dict)
    project: str = ""
    #: 产物指纹：这份交接单对着哪一版判定
    sources: dict[str, str] = field(default_factory=dict)

    def by_category(self, category: str) -> list[Item]:
        return [i for i in self.items if i.category == category]

    @property
    def deliverable(self) -> bool:
        """能不能打板。**只看 blocking**——其余都是"提醒"，不是否决。"""
        return not self.by_category("blocking")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "project": self.project,
            "deliverable": self.deliverable,
            "sources": dict(self.sources),
            "counts": {c: len(self.by_category(c)) for c in CATEGORIES},
            "checked": dict(self.checked),
            "items": [i.to_dict() for i in self.items],
            "summary": self.summary(),
        }
        return out

    def summary(self) -> str:
        parts = [f"{CATEGORY_TITLE[c].split('（')[-1].rstrip('）')} "
                 f"{len(self.by_category(c))}" for c in CATEGORIES]
        head = ("**可打板**（无阻断项）" if self.deliverable
                else "**不可打板**（有阻断项）")
        return f"{head} — " + " · ".join(parts)


# ---------------------------------------------------------------------------
# 各类的收集器
# ---------------------------------------------------------------------------

def _finding_text(raw: Any) -> str:
    """把一条 finding 变成人话。

    ## 为什么需要这个转换

    阶梯的 `findings` 里放的是**结构化的检查记录**，不是字符串：

        {"check": "space.overlap", "ok": false, "refs": ["C77","U1"],
         "message": "C77 与 U1 实体重叠 0.90×1.60×0.80 mm（1.15 mm³）",
         "fix": {"need": "move_apart", ...}}

    第一版直接 `str(f)`，于是 `handoff.md` 的"你要做什么"列里
    出现了一整段 Python dict 字面量——JSON 里嵌套的引号还把
    表格行截断了。**交接单给的是要照着做事的人，不是给程序看的。**

    有 `message` 就用它（那已经是最精炼的描述），其次 `check`，
    最后才退回原文。
    """
    if isinstance(raw, dict):
        for key in ("message", "detail", "text", "check"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        refs = raw.get("refs")
        if isinstance(refs, list) and refs:
            return f"{raw.get('check', '检查')}：{', '.join(map(str, refs))}"
        return str(raw.get("check") or "（未描述）")
    return str(raw)


def _blocking(ladder: dict | None, claims: dict | None,
              checks: dict | None, deferred: dict | None) -> list[Item]:
    """打板必炸的：阶梯硬失败、物理失败、不可逆决策未闭合。

    ## 去重：V2 就是 checks

    阶梯的 V2 直接调用 `physical.run_checks()`，所以 V2 失败与
    `checks.json` 的 failed 是**同一批问题**。第一版两条都报，
    交接单上出现"V2 几何失败"与"物理检查 6 项失败"并列——读的人
    会以为有 12 个问题，去修两遍。

    规则：V2 失败时只报 V2（它带 findings 详情），不再单列 checks；
    V2 没跑（被截断）时才靠 checks 兜底。
    """
    out: list[Item] = []
    v2_failed = False

    if ladder:
        stopped = str(ladder.get("stopped_at") or "")
        for level in ladder.get("levels") or []:
            if not isinstance(level, dict):
                continue
            status = str(level.get("status") or "")
            name = str(level.get("level") or "")
            title = str(level.get("title") or "")
            if status != "fail":
                continue
            if name == "V2":
                v2_failed = True
            findings = [_finding_text(f) for f in (level.get("findings") or [])]
            out.append(Item(
                category="blocking",
                title=f"{name} {title} 失败",
                status="fail",
                action="；".join(findings[:4]) or "修好该层报出的问题",
                risk=("板子会烧或网表根本不成立"
                      if name in ("V0", "V1") else
                      "该层的判据不成立"),
                basis=f"ladder.json:{name}.status=fail"))
        if stopped:
            out.append(Item(
                category="blocking",
                title=(f"{stopped} 硬失败，下游各层未运行"),
                status="truncated",
                action=f"先修 {stopped}；它没修好之前，下游的结论无意义",
                risk="在错误的前提上做后续验证，会得出看似通过的假报告",
                basis="ladder.json:stopped_at"))

    if checks and not v2_failed:
        summary = checks.get("summary") or {}
        failed = int(summary.get("failed") or 0)
        if failed:
            rows: list[str] = []
            for check in checks.get("checks") or []:
                if not isinstance(check, dict) or check.get("ok", True):
                    continue
                rows.append(_finding_text(check))
            out.append(Item(
                category="blocking",
                title=f"物理/机械检查 {failed} 项失败",
                status="fail",
                action="；".join(rows[:4]) or "查看 checks.json",
                risk="元件重叠/越界会导致装配做不出来",
                basis="checks.json:summary.failed"))

    if deferred:
        open_items = [str(x) for x in (deferred.get("open_irreversible") or [])]
        if open_items:
            out.append(Item(
                category="blocking",
                title=f"{len(open_items)} 项不可逆决策未闭合",
                status="open",
                action=("逐项闭合：" + ", ".join(open_items)
                        + "。晚改一次就是一次重投"),
                risk="这些决策锁定层数/扇出/外壳/电源拓扑，改一次要重新投板",
                basis="deferred.json:open_irreversible"))

    if claims:
        failures = [str(f) for f in (claims.get("failures") or [])]
        if failures:
            out.append(Item(
                category="blocking",
                title=f"断言层 {len(failures)} 条失败",
                status="fail",
                action="；".join(failures[:5]),
                risk="这些元件的存在理由已被证伪（它该起的作用在当前拓扑下不成立）",
                basis="claims.json:failures"))

    return out


def _missing_data(design: DesignModel, budget: dict | None,
                  justify: dict | None) -> list[Item]:
    """缺数据：机器不知道，人必须补。

    **这类和 `assumed` 的区别**：`assumed` 是"机器替你做主了"，
    `missing_data` 是"机器连做主都不敢"。
    """
    out: list[Item] = []
    total = len(design.parts)

    # 1. 电气申报：没有任何元件申报电流/电压
    declared = sum(1 for p in design.parts
                   if any(isinstance(v, (int, float)) and not isinstance(v, bool)
                          for v in (p.electrical or {}).values()))
    if declared == 0 and total > 0:
        out.append(Item(
            category="missing_data",
            title="0 个元件申报电气参数（current_ma / v_in / v_out）",
            status=f"{declared}/{total}",
            action="给出每个耗电器件的电流；至少给出各电源轨的总负载",
            risk="电源预算、压降、温升全部算不出来——不是通过，是没算",
            basis="design.json:parts[].electrical 全空"))

    # 2. 预算置信度。**产物不存在时也要说**——第一次写这段只处理了
    # `budget is not None` 的情况，于是"根本没有 budget.json"时
    # 这一类什么都不报。那是静默丢项：用户以为查过了没问题。
    if budget:
        confidence = str(budget.get("confidence") or "")
        coverage = budget.get("coverage") or {}
        claimed = int(coverage.get("claimed_parts") or 0)
        if confidence == "none":
            out.append(Item(
                category="missing_data",
                title="五轨预算无任何 claim，无法评估",
                status="confidence=none",
                action="补 `electrical` 申报，或显式给出各项上限",
                risk="功率/热/EMC/时序/成本五条全部未知",
                basis="budget.json:confidence=none"))
        elif confidence == "partial":
            out.append(Item(
                category="missing_data",
                title=f"预算只覆盖 {claimed}/{total} 个元件",
                status="confidence=partial",
                action="补齐未覆盖元件的申报（见 budget.json:coverage.uncovered）",
                risk="未被覆盖的部分可能超限而不会被发现",
                basis="budget.json:coverage"))
    else:
        out.append(Item(
            category="missing_data",
            title="没有预算产物（budget.json 不存在）",
            status="missing",
            action="跑一次 `hw_budget`；在那之前五轨预算完全未知",
            risk="功率/热/EMC/时序/成本从未被计算过，不是'通过'",
            basis="budget.json 缺失"))

    # 3. 归一化：连网表都没有的元件，归因无法进行
    if justify:
        missing = justify.get("missing_data") or {}
        count = int(missing.get("no_netlist_count") or 0)
        if count:
            out.append(Item(
                category="missing_data",
                title=f"{count}/{total} 个元件没有任何网表连接",
                status=f"{count}/{total}",
                action=("重新导入带网表的源文件，或走 .ioc 提取；"
                        "只给出元件清单无法判定这些件的作用"),
                risk=("这些元件的存在理由无法验证；删掉它们系统也不会报错"
                      "（断言机制覆盖不到没有网表的元件）"),
                basis="justify.json:missing_data.no_netlist"))

    # 4. 尺寸来源：估算值不能当精确值
    estimated = [p.ref for p in design.parts
                 if p.physical_source == "estimated"]
    if estimated:
        out.append(Item(
            category="missing_data",
            title=f"{len(estimated)} 个元件尺寸是估算值（physical_source=estimated）",
            status=f"{len(estimated)}/{total}",
            action=("查 datasheet 替换为实测尺寸；估算值只能用来发现"
                    "明显重叠，不能用来判断装配"),
            risk="按估算尺寸排的布局可能装不进去",
            basis="design.json:parts[].physical_source"))

    return out


def _assumed(coverify: dict | None, ladder: dict | None) -> list[Item]:
    """机器用了默认值：**这些数不是设计里的，是代码里的。**"""
    out: list[Item] = []

    if coverify:
        for note in (coverify.get("not_verified") or []):
            text = str(note)
            out.append(Item(
                category="assumed",
                title=text if len(text) <= 80 else text[:77] + "…",
                status="default",
                action="给出实际数值，替换默认值后重跑协同验证",
                risk="协同验证的结论建立在默认值上；默认值不成立时结论作废",
                basis="coverify 的 not_verified"))
        provenance = coverify.get("provenance") or {}
        defaults = [f"{k}={v}" for k, v in provenance.items()
                    if isinstance(v, str) and "默认值" in v]
        if defaults and not coverify.get("not_verified"):
            out.append(Item(
                category="assumed",
                title=f"协同验证用了 {len(defaults)} 个默认值",
                status="default",
                action="确认这些值：" + ", ".join(defaults[:6]),
                risk="默认值与实际需求不符时，协同验证的结论不成立",
                basis="coverify:provenance"))

    return out


def _unverifiable(ladder: dict | None, checks: dict | None) -> list[Item]:
    """这套系统不会验的。**说"不会"比说"待办"诚实。**

    `V5`/`V6` 在 P7 之前永远是 `not_run`：它们不是"待做"，是
    "没有对应的模型/工具链"。把它们混进 blocking 会让人以为
    修一下设计就能过——实际上要装工具链、写仿真场景。
    """
    out: list[Item] = []
    if ladder:
        for level in ladder.get("levels") or []:
            if not isinstance(level, dict):
                continue
            if str(level.get("status") or "") != "not_run":
                continue
            name = str(level.get("level") or "")
            if name in ("V0", "V1"):
                continue                   # 这两层的 not_run 是截断，已在 blocking
            title = str(level.get("title") or "")
            if name == "V5":
                action = ("接固件后重跑（需要 .elf/hex 与板级仿真场景）；"
                          "在那之前只能靠实物测试")
            elif name == "V6":
                action = "接 SPICE 仿真器；当前没有任何电路仿真后端"
            else:
                action = "该层的前置条件不满足，见 ladder.json"
            out.append(Item(
                category="unverifiable",
                title=f"{name} {title} 未运行",
                status="not_run",
                action=action,
                risk=f"{name} 能发现的问题，这一版完全没查",
                basis=f"ladder.json:{name}.status=not_run"))
    if checks:
        for note in (checks.get("unverified") or []):
            out.append(Item(
                category="unverifiable",
                title=str(note)[:90],
                status="unverified",
                action="人工确认或补数据",
                risk="该项检查没有执行",
                basis="checks.json:unverified"))
    return out


def _link_gaps(links_payload: dict | None,
               units_payload: dict | None) -> list[Item]:
    """P5 的产物（单元层 + 链路）里的「未验证」，聚合进交接单。

    ## 为什么必须接进来

    单元层和链路检查是本轮新加的。如果交接单不读它们，它们就成了
    **没人消费的报告**——正是 M1 修过的那个病（`justify` 算出了
    "R1 是上拉"，但没人用它）。

    实测 `示例-多板` 的 `links.json`：

      * 声明链路 0 条（三条链路检查从未执行）
      * 15 条外设信号网没有对应链路（仅提示）
      * 7 个单元完全没有网表 → 板间接口无从判断

    这三条都是"人必须去确认"的事，正好落在交接单的
    `missing_data` 类（机器不知道，你要补）。
    """
    out: list[Item] = []

    if links_payload:
        checked = links_payload.get("checked") or {}
        declared = int(checked.get("declared_links") or 0)
        if declared == 0:
            out.append(Item(
                category="missing_data",
                title="板间链路一条都没声明",
                status="not_ready",
                action=("为每对互连的单元补 `links_list`："
                        "写清 a/b 两端的外设名、协议、电平、波特率"),
                risk=("协议/电平/波特率三条检查从未执行；"
                      "两块板插上才发现不通"),
                basis="links.json:checked.declared_links=0"))

        orphans = links_payload.get("orphans") or {}
        counts = orphans.get("counts") or {}
        declared_unlinked = int(counts.get("declared_unlinked") or 0)
        if declared_unlinked:
            rows = [str(i.get("peripheral") or "")
                    for i in (orphans.get("declared_unlinked") or [])
                    if isinstance(i, dict)][:6]
            out.append(Item(
                category="missing_data",
                title=f"{declared_unlinked} 个外设声明了但没说接谁",
                status="orphan",
                action="补链路：" + ", ".join(r for r in rows if r),
                risk="这是隐性假设——网表上通，但两端可能根本不匹配",
                basis="links.json:orphans.declared_unlinked"))

        observed_unlinked = int(counts.get("observed_unlinked") or 0)
        if observed_unlinked:
            rows = [str(i.get("peripheral") or "")
                    for i in (orphans.get("observed_unlinked") or [])
                    if isinstance(i, dict)][:6]
            out.append(Item(
                category="missing_data",
                title=f"{observed_unlinked} 条外设信号网没有对应链路",
                status="observed",
                action=("确认这些网是板内连接还是跨板；跨板的要建链路："
                        + ", ".join(r for r in rows if r)),
                risk="仅提示（从网名推断的），但跨板而未声明就会漏检",
                basis="links.json:orphans.observed_unlinked"))

        unknown_units = int(counts.get("units_without_nets") or 0)
        if unknown_units:
            out.append(Item(
                category="missing_data",
                title=f"{unknown_units} 个单元完全没有网表",
                status="missing",
                action="重新导入这些板的网表；只给元件清单无法判断它们的对外接口",
                risk="板间链路无从推导，这些单元的角色也判不出",
                basis="links.json:orphans.units_without_nets"))

    if units_payload:
        missing = units_payload.get("missing_data") or {}
        count = int(missing.get("no_netlist_count") or 0)
        total = int(units_payload.get("total") or 0)
        judgeable = int(units_payload.get("judgeable") or 0)
        if count and not links_payload:
            # links 已经报过一次就不重复报（避免同一件事两张表）
            out.append(Item(
                category="missing_data",
                title=f"{count}/{total} 个单元没有任何网表",
                status="missing",
                action="补这几块板的网表",
                risk="单元角色判不出（是缺数据，不是算法判不出）",
                basis="units.json:missing_data.no_netlist"))
        if total and judgeable == 0:
            out.append(Item(
                category="missing_data",
                title="所有单元都没有具名网络，角色全部判不出",
                status="unknown",
                action="给关键网络起名字（OSC_IN / SCL / USART1_TX 之类）",
                risk="单元层只剩'分了组'，没有'这组干什么'",
                basis="units.json:judgeable=0"))

    return out


def _orphan(design: DesignModel, justify: dict | None) -> list[Item]:
    """生成了但没接线：确认是故意的还是漏了。"""
    out: list[Item] = []
    if justify:
        for row in justify.get("parts") or []:
            if not isinstance(row, dict):
                continue
            group = row.get("group") or {}
            if str(group.get("kind") or "") != "unconnected":
                continue
            ref = str(row.get("ref") or "")
            justification = row.get("justification") or {}
            out.append(Item(
                category="orphan",
                title=f"{ref} 没有连接任何网络",
                status="unconnected",
                action="确认它是机械件（安装孔/支架）还是漏接线",
                risk=("若是漏接线，该功能整体缺失且不会被任何检查发现"
                      if ref else ""),
                basis=str(justification.get("why") or "")))
    return out


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------

def _fingerprint(payload: Any) -> str:
    """产物指纹：这份交接单对着哪一版判定。

    用内容的 sha1 而不是 mtime——mtime 会被 `touch` 或检出操作改掉，
    那时内容其实没变，指纹却变了，人就会以为判定更新过。
    """
    import hashlib
    if payload is None:
        return "missing"
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def build(store: Any) -> Handoff:
    """从磁盘上已有的产物构建交接单。**不跑任何判定。**

    `store` 是一个 `DesignStore`。缺哪份产物就少聚一类，
    但**永远在 `sources` 里记下"缺"**，不假装查过。
    """
    if not store.exists():
        raise ValueError(f"工程 {store.id!r} 没有 design.json")

    design = store.load()
    ladder = store.read("ladder.json")
    claims = store.read("claims.json")
    checks = store.read("checks.json")
    budget = store.read("budget.json")
    deferred = store.read("deferred.json")
    justify = store.read("justify.json")
    links_payload = store.read("links.json")
    units_payload = store.read("units.json")

    # coverify 有自己的产物 `coverify.json`。
    #
    # **原来这里去 `budget.json` 里翻 `coverify` 字段——那个字段从来不存在。**
    # 结果是第三类永远是 0 条，而 `示例-单板` 实际有 7 条 `not_verified`
    # （指向精度 / 跟踪速率 / 探测距离 / 链路带宽 / 延迟全用默认值）。
    # 空表看起来像"没有默认值"，真相是"有 7 个但没人去看"。
    coverify = store.read("coverify.json")
    if not isinstance(coverify, dict):
        coverify = None

    result = Handoff(project=store.id)
    result.items = (
        _blocking(ladder, claims, checks, deferred)
        + _missing_data(design, budget, justify)
        + _link_gaps(links_payload, units_payload)
        + _assumed(coverify, ladder)
        + _unverifiable(ladder, checks)
        + _orphan(design, justify)
    )
    result.checked = {
        "ladder": len((ladder or {}).get("levels") or []),
        "claims": int((claims or {}).get("count") or 0),
        "checks": int(((checks or {}).get("summary") or {}).get("total") or 0),
        "parts": len(design.parts),
        "budget_claims": len((budget or {}).get("claims") or []),
        "justified": int((justify or {}).get("known") or 0),
        "coverify_not_verified": len(
            (coverify or {}).get("not_verified") or []),
        "units": int((units_payload or {}).get("total") or 0),
        "declared_links": int(((links_payload or {}).get("checked") or {})
                              .get("declared_links") or 0),
    }
    result.sources = {
        "design.json": _fingerprint(store.read("design.json")),
        "ladder.json": _fingerprint(ladder),
        "claims.json": _fingerprint(claims),
        "checks.json": _fingerprint(checks),
        "budget.json": _fingerprint(budget),
        "deferred.json": _fingerprint(deferred),
        "justify.json": _fingerprint(justify),
        "coverify.json": _fingerprint(coverify),
        "units.json": _fingerprint(units_payload),
        "links.json": _fingerprint(links_payload),
    }
    return result


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------

def render_markdown(payload: dict[str, Any]) -> str:
    """把 JSON 交接单渲染成人读的 `handoff.md`。

    **每类都渲染，哪怕是空的**。空表写"无"而不是省略——
    省略会让人以为这一类没查。
    """
    project = str(payload.get("project") or "")
    deliverable = bool(payload.get("deliverable"))
    counts = payload.get("counts") or {}
    checked = payload.get("checked") or {}
    sources = payload.get("sources") or {}
    items = payload.get("items") or []

    lines: list[str] = []
    lines.append(f"# 交接：{project}（AI 生成，待人工确认）")
    lines.append("")
    lines.append("> 本文件由 `hw_handoff` 从**已有判定产物**聚合而成，"
                 "不重新跑判定。")
    lines.append("> 产物指纹见文末；指纹不同说明判定已更新，需重新确认。")
    lines.append("")
    if deliverable:
        lines.append("**结论：可打板** —— 没有阻断项。")
        lines.append("")
        lines.append("注意：可打板不等于验证充分。下面二~五类仍然存在，"
                     "只是不会立刻烧板。")
    else:
        lines.append("**结论：不可打板** —— 存在阻断项，见第一类。")
    lines.append("")

    # 概览
    lines.append("| 类别 | 条数 |")
    lines.append("|---|---|")
    for category in CATEGORIES:
        lines.append(f"| {CATEGORY_TITLE[category]} | {int(counts.get(category) or 0)} |")
    lines.append("")

    for category in CATEGORIES:
        rows = [i for i in items
                if isinstance(i, dict) and i.get("category") == category]
        lines.append(f"## {CATEGORY_TITLE[category]}")
        lines.append("")
        if not rows:
            lines.append("_无。_")
            lines.append("")
            continue
        lines.append("| # | 项 | 状态 | 你要做什么 | 风险 |")
        lines.append("|---|---|---|---|---|")
        for index, row in enumerate(rows, 1):
            lines.append("| {} | {} | {} | {} | {} |".format(
                index, _cell(row.get("title")), _cell(row.get("status")),
                _cell(row.get("action")), _cell(row.get("risk"))))
        lines.append("")

    # 覆盖情况：**说明"查了什么"**，否则空表与没跑无法区分
    lines.append("## 六、这份单子查了什么")
    lines.append("")
    lines.append("| 产物 | 指纹 | 查到的东西 |")
    lines.append("|---|---|---|")
    labels = {
        "ladder": "验证阶梯层数",
        "claims": "断言条数",
        "checks": "物理检查项",
        "parts": "元件数",
        "budget_claims": "预算 claim 数",
        "justified": "已归因元件数",
        "coverify_not_verified": "协同验证默认值条数",
        "units": "单元数",
        "declared_links": "已声明板间链路",
    }
    for key, label in labels.items():
        value = checked.get(key)
        artifact = {
            "ladder": "ladder.json", "claims": "claims.json",
            "checks": "checks.json", "budget_claims": "budget.json",
            "justified": "justify.json",
            "coverify_not_verified": "coverify.json",
            "units": "units.json",
            "declared_links": "links.json",
        }.get(key, "design.json")
        lines.append(f"| {label} | `{sources.get(artifact, 'missing')}` | "
                     f"{'—' if value is None else value} |")
    lines.append("")
    lines.append("缺失的产物在指纹列显示 `missing`——"
                 "那一类没有被检查过，不是「检查了没问题」。")
    lines.append("")
    return "\n".join(lines)


def _cell(value: Any, limit: int = 220) -> str:
    """表格单元格转义 + 截断。

    管道符会截断表格，换行会让整行错位——这两个必须处理。
    另外**过长的单元格要截断**：实测 V2 的 findings 拼起来有 700+ 字符，
    塞进一个表格单元格后整张表无法阅读。截断时保留前几句并标记，
    不静默丢内容（完整内容在 `handoff.json` 里）。
    """
    text = str(value if value is not None else "")
    text = text.replace("|", "/").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + f"…（完整内容见 handoff.json）"
    return text or "—"


# ---------------------------------------------------------------------------
# 自省
# ---------------------------------------------------------------------------

def describe() -> dict[str, Any]:
    """给 CLI / 工具自省用。"""
    return {
        "module": "hw_velxio.design.handoff",
        "categories": list(CATEGORIES),
        "titles": {c: CATEGORY_TITLE[c] for c in CATEGORIES},
        "artifacts_read": ["design.json", "ladder.json", "claims.json",
                           "checks.json", "budget.json", "deferred.json",
                           "justify.json", "coverify.json",
                           "units.json", "links.json"],
        "writes": ["handoff.json", "handoff.md"],
        "note": ("聚合已有产物的未验证项；不重新跑判定，"
                 "空表也显式写『无』并记下查了什么"),
    }
