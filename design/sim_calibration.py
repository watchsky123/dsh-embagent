"""参数校准 —— 用**真实失败**改仿真参数，且必须能"重放"那条失败。

## 这条规则是哪来的

RoboBuilder 的项目页写得很直白（本仓库 `docs/execution-plan-v3.md` P21 引了原文）：

> **≤2 个参数/次失败，且只有"旧设计在新参数下也按录像那样失败"才保留**

第二条是整套方案里**最关键的机制**，理由和我们"不能自己出题自己批"完全同源：

* 如果采纳标准是**新设计能过**，那就等于允许把参数调到"看起来过"——
  参数成了作弊工具，而不是对物理的逼近
* 采纳标准换成**旧设计在新参数下复现录像里的失败**，参数就必须
  先解释**已经发生过的事**，才有资格被用来预测未发生的事

所以 `calibrate()` 里只有一条采纳路径：

| 旧设计在新参数下 | 结论 | 为什么 |
|---|---|---|
| `fail`（复现了） | **采纳** | 参数解释了实测现象 |
| `pass` | 拒绝 `old_passed` | 参数把旧失败"调没了" = 调松了 |
| `unknown` | 拒绝 `old_unknown` | 跑不起来 ≠ 复现。**unknown 不是 pass** |

## 每次只动 ≤2 个参数

一次改 5 个参数，旧设计当然"复现"了——但你不知道是哪 2 个在起作用。
上限 2 是 RoboBuilder 用血的教训换来的可归因性下限。

## 存档必须是**成对**的

`sim_params/initial.json`（初始估计）+ `sim_params/learned-<date>.json`（校准后）。
只有 learned 没有 initial，就永远说不清"改之前是什么"；
只有 initial 没有 learned，校准成果就丢了。
`archive()` 保证两个都在，并把 `diff()` 的 `turned_on` 项单独列出来——
那些"初始是 0、校准后非 0"的参数，正是初始仿真器**根本没建模**的物理。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import sim_params as sp
from .model import DesignModel

__all__ = [
    "MAX_PARAMS_PER_FAILURE",
    "Attempt",
    "Calibration",
    "archive",
    "calibrate",
    "default_runner",
    "describe",
]

#: RoboBuilder 的硬上限：一次失败最多动两个参数（可归因性下限）。
MAX_PARAMS_PER_FAILURE = 2

#: 校准判据里出现的状态。``unknown`` 单独一档，**绝不与 pass/fail 混**。
VERDICTS = ("pass", "fail", "unknown")

#: 一个判断器：给设计和参数集，回答"这套参数下它是过还是挂"。
Runner = Callable[[DesignModel, sp.ParamSet], dict[str, Any]]


@dataclass
class Attempt:
    """一个候选参数组合的评审结果。**拒绝也要写清理由**。"""

    params: dict[str, Any]
    old_status: str = "unknown"
    #: 新设计的判定。**空串 = 没评估**（旧设计没复现时根本不会去判新设计）。
    #: 第一版默认写 "unknown"，于是"没评估"和"判不出"在报告里长得一样。
    new_status: str = ""
    adopted: bool = False
    reason: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "params": dict(self.params),
            "old_status": self.old_status,
            "new_status": self.new_status,
            "new_evaluated": self.new_status in ("pass", "fail", "unknown"),
            "adopted": self.adopted,
            "reason": self.reason,
            "detail": self.detail[:300],
        }


@dataclass
class Calibration:
    """一次校准的完整结果。"""

    test_id: str = ""
    # 注意：`accepted` 是**属性**不是字段。第一版把它写成字段 `accepted=False`，
    # 采纳了参数也不更新，于是 `cal.accepted` 永远是 False——而 `to_dict()`
    # 用的是 `bool(self.adopted)`，两者自相矛盾。
    adopted: dict[str, Any] = field(default_factory=dict)
    attempts: list[Attempt] = field(default_factory=list)
    params: sp.ParamSet | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "test_id": self.test_id,
            "accepted": bool(self.adopted),
            "adopted": dict(self.adopted),
            "attempts": [a.to_dict() for a in self.attempts],
        }
        if self.params is not None:
            out["params_label"] = self.params.label
            out["params_count"] = len(self.params.params)
        if self.notes:
            out["notes"] = list(self.notes)
        return out

    @property
    def accepted(self) -> bool:
        return bool(self.adopted)

    @property
    def rejected(self) -> list[Attempt]:
        return [a for a in self.attempts if not a.adopted]


# ---------------------------------------------------------------------------
# 默认判断器：跑 V7（MuJoCo 动力学 + 三次扰动）
# ---------------------------------------------------------------------------


def default_runner(design: DesignModel, params: sp.ParamSet) -> dict[str, Any]:
    """用 `ladder.level_v7` 判。参数写成 ``design.sim_params`` 覆盖后跑。

    为什么复用 V7 而不是另写一次 MuJoCo 调用：V7 已经包含
    "weld 元件 + 三次扰动 + 翻倒阈值"这套判定；再写一份，两处阈值
    早晚分叉（AGENTS §1.1）。

    MuJoCo 没装 → V7 是 ``not_run`` → 这里如实翻成 ``unknown``。
    **不能翻成 pass**：那会让"跑不起来"变成"校准通过"。
    """
    from . import ladder

    clone = DesignModel.from_dict(design.to_dict())
    base = sp.load()
    overrides: dict[str, Any] = dict(getattr(clone, "sim_params", None) or {})
    for key, param in params.params.items():
        if base.get(key) != param.value:
            overrides[key] = {"value": param.value, "source": param.source}
    clone.sim_params = overrides
    result = ladder.level_v7(clone)
    status = str(getattr(result, "status", "") or "")
    verdict = {"pass": "pass", "fail": "fail"}.get(status, "unknown")
    detail = "；".join(
        str(f.get("message") or f)
        for f in (getattr(result, "findings", None) or [])[:3])
    return {"status": verdict, "ladder_status": status, "detail": detail}


# ---------------------------------------------------------------------------
# 校准
# ---------------------------------------------------------------------------


def calibrate(
    design_old: DesignModel,
    design_new: DesignModel,
    *,
    test_id: str,
    params: sp.ParamSet,
    candidates: Iterable[dict[str, Any]],
    runner: Runner | None = None,
    max_params: int = MAX_PARAMS_PER_FAILURE,
) -> Calibration:
    """用一条真实失败校准参数。**采纳标准是"旧设计复现"，不是"新设计通过"。**

    ``candidates`` 是若干 ``{参数名: 值}``。每个候选依次过四道闸：

    1. 参数个数 ≤ ``max_params``（否则 `too_many_params`）
    2. 参数键必须在参数表里（否则 `unknown_param`——写错名字不能变成"新增项"）
    3. 旧设计在新参数下 **fail**（pass → `old_passed`；unknown → `old_unknown`）
    4. 采纳；同时记下新设计的状态（仅供参考，**不作采纳条件**）

    返回 `Calibration`；``params`` 是采纳后的新参数集（一个都没采纳时是原集）。
    """
    judge = runner or default_runner
    result = Calibration(test_id=str(test_id or ""))
    current = params

    for raw in candidates:
        if not isinstance(raw, dict) or not raw:
            result.attempts.append(Attempt(params={}, reason="empty_candidate",
                                            detail="候选不是非空对象"))
            continue
        attempt = Attempt(params={str(k): v for k, v in raw.items()})
        if len(attempt.params) > max_params:
            attempt.reason = "too_many_params"
            attempt.detail = (f"{len(attempt.params)} 个参数 > 上限 {max_params}；"
                              "再多就说不清是哪几个在起作用")
            result.attempts.append(attempt)
            continue
        unknown = [k for k in attempt.params if k not in current]
        if unknown:
            attempt.reason = "unknown_param"
            attempt.detail = (f"参数表里没有 {unknown}；写错名字不能当成新增项"
                              "（那会让校准结果落在一个没人定义过的物理量上）")
            result.attempts.append(attempt)
            continue

        trial = current.with_params(attempt.params, source="measured",
                                    label=f"candidate:{test_id or 'ad-hoc'}")
        old_out = judge(design_old, trial)
        attempt.old_status = str(old_out.get("status") or "unknown")
        attempt.detail = str(old_out.get("detail") or "")[:300]

        if attempt.old_status == "pass":
            attempt.reason = "old_passed"
            attempt.detail = ("新参数下**旧设计通过了** —— 参数被调松了，"
                              "不是在解释已发生的失败" + (
                                  "；" + attempt.detail if attempt.detail else ""))
            result.attempts.append(attempt)
            continue
        if attempt.old_status != "fail":
            attempt.reason = "old_unknown"
            attempt.detail = ("旧设计在新参数下**跑不起来/判不出**，"
                              "复现无从谈起（unknown 不是 pass）" + (
                                  "；" + attempt.detail if attempt.detail else ""))
            result.attempts.append(attempt)
            continue

        # 走到了这里：旧设计复现了。采纳。
        attempt.adopted = True
        attempt.reason = "reproduces_old_failure"
        current = trial
        result.adopted.update(attempt.params)
        new_out = judge(design_new, trial)
        attempt.new_status = str(new_out.get("status") or "unknown")
        if attempt.new_status == "fail":
            result.notes.append(
                f"{test_id}: 参数 {sorted(attempt.params)} 已采纳（旧失败复现），"
                "但新设计仍然失败 —— 光靠参数解决不了，得改设计")
        elif attempt.new_status == "unknown":
            result.notes.append(
                f"{test_id}: 新设计在新参数下判不出（unknown），不要说成通过")
        result.attempts.append(attempt)

    result.params = current
    if not result.adopted:
        result.notes.append(
            "一个候选都没采纳。**这不是失败**：说明现有参数解释不了这段录像，"
            "要么补候选，要么承认仿真模型缺了这块物理")
    return result


# ---------------------------------------------------------------------------
# 成对存档
# ---------------------------------------------------------------------------


def archive(design: DesignModel, params: sp.ParamSet, root: str | Path, *,
            date: str = "") -> dict[str, Any]:
    """写 ``sim_params/initial.json`` + ``sim_params/learned-<date>.json``。

    `initial` 不存在时用默认表补上（它是"校准前"的定义）。
    `learned` 的内容是传入的 `params`。返回两个路径 + `diff` 的
    `turned_on` 项——那正是初始仿真器没建模的物理。
    """
    base_dir = Path(root) / "sim_params"
    base_dir.mkdir(parents=True, exist_ok=True)
    initial_path = base_dir / "initial.json"
    if not initial_path.is_file():
        try:
            initial = sp.load()
        except FileNotFoundError as exc:
            return {"status": "not_ready", "reason": str(exc)}
        sp.save(initial, initial_path)
    initial = sp.load(initial_path, label="initial")

    day = str(date or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    learned_path = base_dir / f"learned-{day}.json"
    sp.save(params, learned_path)

    changed = initial.diff(params)
    turned_on = [row for row in changed if row.get("kind") == "turned_on"]
    return {
        "status": "ok",
        "initial": str(initial_path),
        "learned": str(learned_path),
        "pair_ok": initial_path.is_file() and learned_path.is_file(),
        "changed": len(changed),
        "turned_on": [{"key": r["key"], "from": r["from"], "to": r["to"]}
                      for r in turned_on],
        "note": ("turned_on 是初始仿真器**根本没建模**的物理项"
                 "（RoboBuilder 实测 136 项里多项从 0 变非 0）"),
    }


def diff_summary(params_a: sp.ParamSet, params_b: sp.ParamSet) -> dict[str, Any]:
    rows = params_a.diff(params_b)
    by_kind: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("kind") or "?")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {"changed": len(rows), "by_kind": by_kind,
            "turned_on": [r["key"] for r in rows if r.get("kind") == "turned_on"]}


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.sim_calibration",
        "max_params_per_failure": MAX_PARAMS_PER_FAILURE,
        "rule": "只有「旧设计在新参数下复现录像里的失败」才采纳",
        "rejections": {
            "old_passed": "参数把旧失败调没了 = 调松了",
            "old_unknown": "跑不起来 ≠ 复现",
            "too_many_params": f"> {MAX_PARAMS_PER_FAILURE} 个，不可归因",
            "unknown_param": "参数表里没有这个名字",
        },
        "not_adoption_criteria": "新设计是否通过 —— 它只记录，不参与采纳",
        "archive": "initial.json 与 learned-<date>.json 成对存在",
    }