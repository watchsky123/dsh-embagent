"""验收阶梯（21 级）—— **hw_velxio 自己的**，不再依赖 `hwforge.tiers`。

## 这条阶梯是干什么的

一个设计"验过多少"不能用一句"通过/不通过"回答。真实情况有四类：

| 状态 | 含义 |
|---|---|
| `verified` | 有检查，这次跑过，通过 |
| `failed` | 有检查，跑过，没通过 |
| `not_run` | 有检查，这次**没跑**（不是没工具） |
| `no_checker` | 这个工作台**根本没有**这一级的检查 |
| `needs_human` | 只能人和台架做（样机、HIL） |

把前三者混成"未验证"，和把"没有工具"说成"通过"一样有害。

## 与 hwforge 的关系

阶梯的**概念**（O0..O6 分层）沿用 hwforge 的设计 —— 它是对的，没必要
另发明一套。但**表在本地**：没有 hwforge 也能报"哪些层验过"。

`CHECK_TIERS` 是**本地检查 → 阶梯**的映射。说过头就是假报告，所以下面
几处刻意**不映射**（见注释）。

@module hw_velxio.design.tiers
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 阶梯表
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Tier:
    """一级阶梯：证明什么、谁证明、有没有实现。"""

    id: str
    rank: int
    name: str
    proves: str
    #: `built` / `planned` / `manual`
    status: str = "built"
    module: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "rank": self.rank, "name": self.name,
                "proves": self.proves, "status": self.status,
                "module": self.module, "note": self.note}


TIERS: tuple[Tier, ...] = (
    Tier("O0", 0, "schema / 清单 / 出处", "配置完整、字段有来源", "built",
         "oracles"),
    Tier("O1", 10, "连通性 + 引脚语义", "每个网能连、引脚类型相容", "built",
         "oracles/types"),
    Tier("O1.0", 11, "需求形式化", "需求被拆成硬件/软件/接缝三视图", "built",
         "requirements"),
    Tier("O1.5", 12, "矛盾扫描", "需求内部矛盾被逐条点名", "built", "requirements"),
    Tier("O2", 20, "降额 + 解析应力", "每个元件在应力窗口内", "built",
         "oracles/detectors"),
    Tier("O2.5", 25, "体素空间", "重叠/越界/间距/可达", "built", "geometry"),
    Tier("O2.7", 27, "装配线缆", "走线连通、长度预算、不穿本体", "planned", "",
         "工作台不出铜线，只有飞线"),
    Tier("O2.8", 28, "KiCad 往返", "符号与网表能生成并被读回", "planned", "",
         "没做 KiCad 导出"),
    Tier("O2.9", 29, "地面静力学", "重力路径、支撑、稳定性", "built", "geometry"),
    Tier("O2.11", 31, "壁挂静力学", "壁挂专用的失效模型", "planned", "",
         "壁挂只有安装件，没有抗拔模型"),
    Tier("O2.12", 32, "软硬协同", "需求数值 vs 硬件能力", "built", "coverify"),
    Tier("O2.13", 33, "DC 电学", "压降、轨跌、结温", "built", "electrical"),
    Tier("O2.14", 34, "固件内存余量", "Flash/RAM 占用在容量余量内", "planned", "",
         "没有编译产物，拿不到 Flash/RAM 占用（源码检查在 O2.18）"),
    Tier("O2.15", 35, "能量预算", "唤醒频次在电池预算内", "planned", "", ""),
    Tier("O2.16", 36, "任务栈余量", "任务栈估算留足余量", "planned", "", ""),
    Tier("O2.17", 37, "升级分区与标志", "分区自洽且标志迁移合法", "planned", "", ""),
    Tier("O2.18", 34.5, "固件源码检查", "未调用/失联处理/配置表/调试残留",
         "built", "firmware",
         "6 条文本与结构检查；不是语义分析，每条都写明看不见什么"),
    Tier("O3.0", 38, "依赖闭合", "每个需求都有提供者且已接线", "planned", "",
         "`unrouted` 只做了引脚级「谁给这个信号」；没有真正的依赖图检查"),
    Tier("O3", 50, "瞬态", "纹波、过冲、环路裕度", "planned", "",
         "需要 SPICE"),
    Tier("O4", 60, "热", "结温随时间、热耦合", "planned", "", ""),
    Tier("O5", 70, "样机 / 真 HIL", "实物签字", "manual", "",
         "需要人和台架；代码只能记录，不能代替"),
    Tier("O6", 80, "联调 / bench-in-the-loop", "串口契约对真实板", "planned", "", ""),
)

BY_ID: dict[str, Tier] = {t.id: t for t in TIERS}


# ---------------------------------------------------------------------------
# 本地检查 → 阶梯
# ---------------------------------------------------------------------------

#: 工作台的 `checks[].check` 名 → tier id。
#:
#: 每一行都必须能说出"为什么这个检查证明这一级"，**说过头就是假报告**。
#: 下面几处刻意**不映射**：
#:
#: * `space.net_reach` **不映射到 O1**。O1 是"连通性 + 引脚语义"，而
#:   `net_reach` 只量两端距离 —— 它证明不了引脚类型相容。工作台目前
#:   没有类型格检查。
#: * `budget` **不映射到 O2**。O2 是"降额 + 解析应力"，而五轨预算是
#:   功率/热/EMC/时序/成本 —— 名字像，问的不是同一件事。
CHECK_TIERS: dict[str, str] = {
    "consistency.provenance": "O0",
    "space.overlap": "O2.5",
    "space.bounds": "O2.5",
    "space.height": "O2.5",
    "space.keepout": "O2.5",
    "mech.mount_holes": "O2.5",
    "space.connector_edge": "O2.5",
    "space.statics": "O2.9",
}

#: 固件源码检查的 tier。它们判的是**源码文本**，不是板子。
FIRMWARE_TIER = "O2.18"


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def order_of(tier_id: str) -> int:
    tier = BY_ID.get(tier_id)
    return tier.rank if tier is not None else -1


def built() -> list[Tier]:
    return [t for t in TIERS if t.status == "built"]


def highest_built() -> str:
    items = built()
    return max(items, key=lambda t: t.rank).id if items else ""


def reached(tier_ids: list[str]) -> str:
    """**实际跑到**的最高一级（不是代码能到哪一级）。"""
    best: Tier | None = None
    for tier_id in tier_ids:
        tier = BY_ID.get(tier_id)
        if tier is None:
            continue
        if best is None or tier.rank > best.rank:
            best = tier
    return best.id if best is not None else ""


def table() -> list[dict[str, Any]]:
    return [t.to_dict() for t in TIERS]


def describe() -> list[dict[str, Any]]:
    return table()


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

@dataclass
class TierStatus:
    id: str
    name: str
    rank: int
    built_status: str
    #: verified / failed / not_run / no_checker / needs_human
    outcome: str
    proves: str = ""
    evidence: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "rank": self.rank,
                "built_status": self.built_status, "outcome": self.outcome,
                "proves": self.proves, "evidence": list(self.evidence),
                "note": self.note}


@dataclass
class TierReport:
    ok: bool = True
    tiers: list[TierStatus] = field(default_factory=list)
    reached: str = ""
    highest_built: str = ""
    available: bool = True
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reached": self.reached,
                "highest_built": self.highest_built,
                "available": self.available,
                "problems": list(self.problems),
                "tiers": [t.to_dict() for t in self.tiers]}

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for tier in self.tiers:
            out[tier.outcome] = out.get(tier.outcome, 0) + 1
        return out

    def summary(self) -> str:
        counts = self.counts()
        verified = counts.get("verified", 0)
        failed = counts.get("failed", 0)
        no_tool = counts.get("no_checker", 0) + counts.get("needs_human", 0)
        not_run = counts.get("not_run", 0)
        parts = [f"到达 {self.reached or '—'}", f"{verified} 级验过"]
        if failed:
            parts.append(f"**{failed} 级失败**")
        if not_run:
            parts.append(f"{not_run} 级有检查但没跑")
        if no_tool:
            parts.append(f"{no_tool} 级无检查或需人工")
        return "；".join(parts)


def report(checks: dict[str, Any] | None = None,
           coverify: dict[str, Any] | None = None,
           budget: dict[str, Any] | None = None,
           electrical: dict[str, Any] | None = None,
           firmware: dict[str, Any] | None = None) -> TierReport:
    """把一次运行的检查结果映射到阶梯上。"""
    passed_by_tier: dict[str, list[str]] = {}
    failed_by_tier: dict[str, list[str]] = {}

    findings = (checks or {}).get("findings") or []
    for finding in findings:
        name = str(finding.get("check") or "")
        tier_id = CHECK_TIERS.get(name)
        if tier_id is None:
            continue
        target = passed_by_tier if finding.get("ok") else failed_by_tier
        target.setdefault(tier_id, []).append(name)

    # `budget` 刻意不映射（见 CHECK_TIERS 的注释）。

    # electrical 的 EL-01..05 全在 O2.13
    for finding in (electrical or {}).get("findings") or []:
        target = passed_by_tier if finding.get("ok") else failed_by_tier
        target.setdefault("O2.13", []).append(str(finding.get("check")))
    # `run()` 产出的 report 没有 findings 键时，按 rails/regulators 判
    if electrical and "findings" not in electrical:
        items = ([*(electrical.get("rails") or []),
                  *(electrical.get("regulators") or []),
                  *(electrical.get("wires") or [])])
        if items:
            target = (passed_by_tier if all(x.get("ok") for x in items)
                      else failed_by_tier)
            target.setdefault("O2.13", []).append("electrical")

    # 固件源码检查全在 O2.18。**info/warning 不算失败** —— 它们说的是
    # "文本里有这个事实"，不是"设计错了"。
    for finding in (firmware or {}).get("findings") or []:
        severity = str(finding.get("severity") or "warning")
        target = failed_by_tier if severity == "error" else passed_by_tier
        target.setdefault(FIRMWARE_TIER, []).append(
            str(finding.get("check")))
    if firmware and not (firmware.get("findings") or []):
        ran = firmware.get("ran") or []
        if ran:
            passed_by_tier.setdefault(FIRMWARE_TIER, []).append(
                "firmware(" + ",".join(ran) + ")")

    # coverify 的 8 条 CV 全在 O2.12
    for finding in (coverify or {}).get("findings") or []:
        target = passed_by_tier if finding.get("ok") else failed_by_tier
        target.setdefault("O2.12", []).append(str(finding.get("check")))

    statuses: list[TierStatus] = []
    for tier in TIERS:
        passed = passed_by_tier.get(tier.id, [])
        failed = failed_by_tier.get(tier.id, [])
        evidence = passed + failed
        if failed:
            outcome = "failed"
        elif passed:
            outcome = "verified"
        elif tier.status == "manual":
            outcome = "needs_human"
        elif tier.status == "built":
            outcome = "not_run"
        else:
            outcome = "no_checker"
        statuses.append(TierStatus(
            id=tier.id, name=tier.name, rank=tier.rank,
            built_status=tier.status, outcome=outcome,
            proves=tier.proves, evidence=evidence, note=tier.note))

    statuses.sort(key=lambda t: t.rank)
    reached_ids = [t.id for t in statuses if t.outcome in ("verified", "failed")]
    failed_ids = [t.id for t in statuses if t.outcome == "failed"]
    return TierReport(
        ok=not failed_ids, tiers=statuses,
        reached=reached(reached_ids), highest_built=highest_built(),
        problems=[f"这些层级有检查但没通过：{'、'.join(failed_ids)}"]
        if failed_ids else [])
