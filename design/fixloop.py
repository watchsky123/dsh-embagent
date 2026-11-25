"""有界自修循环（`fixloop`）—— **限轮、可停、每步留痕**。

## 它不是什么

它**不是**"让模型自己改设计直到全绿"：

* 改设计是人的决定（AGENTS §3.14：没有自动修复按钮）
* 无界循环会震荡（§1.4：生成器都只能有限枚举）

## 它是什么

把"看结论 → 决定改什么 → 再判定"这件事**装进一个有界的框**：

```
round 1..max_rounds:
    拿 audit + handoff 的可执行项（findings 里带 fix.need 的）
    没有可执行项 → stop("no_findings")
    没有 editor（人/模型）→ stop("needs_editor")，只出计划
    调 editor(items)            ← 注入的；本模块不猜怎么改
    重新 audit → 算 score
    score 没降 → stall+1；stall>=2 → stop("stalled")
```

`score` = `failed 段数 × 100 + not_ready 段数`。**只降不升才算"有改善"**；
一轮里改坏了两处也会被算成没改善（这是故意的：宁可停，不要越改越糟）。

## 出口

`stop` 一定是一个**明确原因**之一：`no_findings` / `needs_editor` /
`stalled` / `max_rounds` / `editor_failed`。没有"跑完了"这种含糊状态。
"""

from __future__ import annotations

from typing import Any, Callable

__all__ = ["run", "describe", "SCORE_FAIL", "SCORE_NOT_READY"]

SCORE_FAIL = 100
SCORE_NOT_READY = 1

#: 注入的编辑器：拿到可执行项，返回它做了什么（自由形状，只记日志）。
Editor = Callable[[list[dict[str, Any]]], dict[str, Any]]


def _score(payload: dict[str, Any]) -> int:
    counts = payload.get("counts") or {}
    return (int(counts.get("failed") or 0) * SCORE_FAIL
            + int(counts.get("not_ready") or 0) * SCORE_NOT_READY)


def _fixable(audit_payload: dict[str, Any],
             handoff_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """把"能动手的"挑出来：audit 段里的 findings + handoff 里带 `fix.need` 的条目。

    **不重排、不合并**：一条 finding 一条待办（P1 的纪律：一条问题一条动作）。
    """
    items: list[dict[str, Any]] = []
    for row in audit_payload.get("stages") or []:
        for finding in row.get("findings") or []:
            if isinstance(finding, dict):
                items.append({"stage": row.get("stage"),
                              "check": finding.get("check"),
                              "refs": list(finding.get("refs") or []),
                              "message": str(finding.get("message") or "")})
    for item in (handoff_payload or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("category")) not in ("blocking", "missing_data"):
            continue
        fix = item.get("fix") or {}
        if fix.get("need") and item.get("next"):
            items.append({"stage": "release", "check": fix.get("need"),
                          "refs": list(item.get("refs") or []),
                          "message": str(item.get("title") or "")})
    return items


def run(store: Any, *, editor: Editor | None = None, max_rounds: int = 3,
        stages: tuple[str, ...] = ()) -> dict[str, Any]:
    """跑有界循环。**不抛异常**；任何一步失败都落成 `failed` + 原因。"""
    from . import audit as audit_mod
    from . import handoff as handoff_mod

    rounds: list[dict[str, Any]] = []
    stall = 0
    previous: int | None = None
    stop = ""
    last_audit: dict[str, Any] = {}

    for index in range(1, max(1, int(max_rounds)) + 1):
        try:
            last_audit = audit_mod.audit(store, stages=stages)
            handoff_payload = handoff_mod.build(store).to_dict()
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "stop": "audit_failed", "rounds": rounds,
                    "findings": [f"{type(exc).__name__}: {exc}"]}
        score = _score(last_audit)
        items = _fixable(last_audit, handoff_payload)
        row: dict[str, Any] = {"round": index, "score": score,
                               "counts": dict(last_audit.get("counts") or {}),
                               "items": len(items)}
        rounds.append(row)

        if not items:
            stop = "no_findings"
            row["did"] = "没有可执行项"
            break
        if editor is None:
            stop = "needs_editor"
            row["did"] = "只出计划（没给 editor）"
            row["plan"] = items[:20]
            break
        try:
            outcome = editor(items)
        except Exception as exc:  # noqa: BLE001
            stop = "editor_failed"
            row["did"] = f"{type(exc).__name__}: {exc}"
            break
        row["did"] = str((outcome or {}).get("did") or "改了一些东西")

        if previous is not None and score >= previous:
            # **只降不升**才算改善。一轮改坏两处也当没改善 —— 宁可停。
            stall += 1
            row["improved"] = False
            if stall >= 2:
                stop = "stalled"
                break
        else:
            stall = 0
            row["improved"] = True
        previous = score
    else:
        stop = "max_rounds"

    return {
        "status": ("ok" if stop == "no_findings" or (rounds and rounds[-1].get("improved"))
                   else "not_ready"),
        "stop": stop,
        "rounds": rounds,
        "final": {"counts": dict(last_audit.get("counts") or {}),
                  "status": str(last_audit.get("status") or "")},
        "note": ({"no_findings": "已经没有可执行项了",
                  "needs_editor": "只出了计划：改设计要人或模型动手（本模块不自动改）",
                  "stalled": "连续两轮没有改善，停了（避免越改越糟）",
                  "max_rounds": f"到了轮数上限 {max_rounds}",
                  "editor_failed": "editor 自己抛了异常"}.get(stop, stop)),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.fixloop",
        "stop_reasons": ["no_findings", "needs_editor", "stalled", "max_rounds",
                         "editor_failed"],
        "score": f"failed×{SCORE_FAIL} + not_ready×{SCORE_NOT_READY}",
        "discipline": ("有界：默认 3 轮、连续 2 轮无改善即停；"
                       "不改设计（editor 由人或模型注入）；每轮留痕")}