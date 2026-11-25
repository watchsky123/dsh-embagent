"""结论层 —— 一屏之内回答"这版设计到底怎么样、下一步做什么"。

## 为什么需要它（而不是再加一个报告）

到 P23 为止，一次完整判定会落下 **11 份产物**：

    ladder / claims / checks / budget / deferred / crosscheck
    coverify / electrical / justify / units / links / handoff

每一份都对着某一个问题，每一份都对。**但对"我现在该干什么"这个问题的回答，
散在 11 个文件里。** 于是最常见的失败方式是：人看了阶梯的 "V2 通过"，
就以为可以打板了——而 V3 是 `not_run`。

`handoff` 已经解决了"把未验证项聚成一张单子"。这一层解决的是**入口**：
用户打开工程时看到的第一屏，不该是 11 个页签，而该是
"结论一句话 + 九层横条 + 下一步 5 件事"。

## 三条纪律

1. **不重新判定**。这里只做投影与排序：读 `handoff.build(store)`（它自己也是
   纯聚合），把 9 个阶梯层、6 类计数、排序后的 next 放在一起。
2. **`next` 的排序是判定，所以必须在这里算**，不能交给 UI
   （AGENTS §1.1）。UI 只负责按顺序渲染。
3. **明确说"该重跑了"**。设计改过而 `ladder.json` 没更新时，
   这屏的结论就是**过期的**；不说出来，人会拿旧结论做新决定。

## 排序口径

类别优先（谁能承担后果）：

    blocking < missing_data < assumed < unverifiable < orphan < external

同类别内按状态：`fail` < `truncated` < `open` < `not_ready` < `missing`
< `unknown` < `default` < `pass`。

理由：同样是"缺数据"，已经报出 `fail` 的那条比"从未检查"更急；
而 `external` 排最后，因为它用的是别人的标准（见 `handoff._external`）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import handoff

__all__ = ["MAX_NEXT", "compose", "describe", "level_rollup"]

#: 一屏只放得下 5 件事。**超过 5 条等于没有优先级**——
#: 人对着 12 条待办会先关掉页面，而不是先做第一条。
MAX_NEXT = 5

#: 类别顺序（越小越急）。
CATEGORY_RANK = {name: index for index, name in enumerate(handoff.CATEGORIES)}

#: 同类别内的状态顺序。没列到的状态按 8 处理（未知状态排后面，不排最前）。
STATUS_RANK = {
    "fail": 0,
    "truncated": 1,
    "open": 2,
    "not_ready": 3,
    "missing": 4,
    "orphan": 5,
    "unknown": 6,
    "default": 7,
    "unverified": 7,
    "no_model": 7,
    "pass": 9,
}

#: 阶梯的层名 → 人能读的短标题（`ladder.json` 里也有 title，这里只兜底）。
LEVEL_ORDER = ("V0", "V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8")


def _count(value: Any) -> int:
    """计数型字段：数或列表都收（`failed` 是数，`unknown` 是列表）。"""
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def level_rollup(levels: list[dict[str, Any]]) -> str:
    """九层压成一个结论。**`fail` 压过一切，`unknown` 压过 `pass`。**

    没有层（还没跑过判定）返回 `not_ready`——不是 `pass`。
    """
    statuses = {str(level.get("status") or "") for level in levels}
    if not levels:
        return "not_ready"
    if "fail" in statuses:
        return "fail"
    if statuses & {"unknown", "not_run", "truncated"}:
        return "unknown"
    return "pass"


def _sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    category = str(item.get("category") or "")
    status = str(item.get("status") or "")
    return (CATEGORY_RANK.get(category, 99),
            STATUS_RANK.get(status, 8),
            str(item.get("title") or ""))


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _age_text(seconds: float) -> str:
    if seconds <= 0:
        return ""
    minutes = int(seconds // 60)
    if minutes < 1:
        return "刚跑"
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    return f"{hours // 24} 天前"


def compose(store: Any) -> dict[str, Any]:
    """一个工程的结论。``store`` 是 `DesignStore`。

    只读磁盘上的**已有产物**，不跑任何判定（V2 体素几何要几十秒，
    打开工程不该触发它）。
    """
    if not store.exists():
        return {"status": "failed",
                "findings": [f"工程 {store.id!r} 没有 design.json"]}

    ladder = store.read("ladder.json")
    report = handoff.build(store)          # 纯聚合，不重算判定
    payload = report.to_dict()

    levels: list[dict[str, Any]] = []
    for raw in (ladder or {}).get("levels") or []:
        if not isinstance(raw, dict):
            continue
        # `failed` 是**计数**，`unknown` 是**明细列表**——两者形状不同，
        # 因为"失败了几条"能在报告里一眼看到，而"判不出的是哪几条"
        # 必须逐条列出来才有人能补数据。第一版把 `unknown` 当计数读，
        # 直接 TypeError（int(list)）。
        levels.append({
            "level": str(raw.get("level") or ""),
            "title": str(raw.get("title") or ""),
            "status": str(raw.get("status") or "unknown"),
            "checked": _count(raw.get("checked")),
            "failed": _count(raw.get("failed")),
            "unknown": _count(raw.get("unknown")),
            "ms": int(float(raw.get("ms") or 0)),
        })
    levels.sort(key=lambda row: LEVEL_ORDER.index(row["level"])
                if row["level"] in LEVEL_ORDER else 99)

    # -- 下一步：排序在这里算，UI 只渲染 ---------------------------------
    ordered = sorted((i for i in payload.get("items") or [] if isinstance(i, dict)),
                     key=_sort_key)
    next_rows = [{
        "category": str(row.get("category") or ""),
        "title": str(row.get("title") or ""),
        "action": str(row.get("action") or ""),
        "risk": str(row.get("risk") or ""),
        "status": str(row.get("status") or ""),
        "basis": str(row.get("basis") or ""),
        # 可点的动作：由 `handoff` 从 `fix.need` 翻出来（P1）。
        # **没有就是没有**——UI 退回显示 `action` 自由文本，不编一个按钮。
        **({"step": dict(row["next"])} if isinstance(row.get("next"), dict) else {}),
    } for row in ordered[:MAX_NEXT]]

    counts = {c: int((payload.get("counts") or {}).get(c) or 0)
              for c in handoff.CATEGORIES}

    # -- 过期判定 ---------------------------------------------------------
    design_at = _mtime(store.path("design.json"))
    ladder_at = _mtime(store.path("ladder.json"))
    stale = bool(ladder_at and design_at and design_at > ladder_at + 1.0)
    age = _age_text(time.time() - ladder_at) if ladder_at else ""

    rollup = level_rollup(levels)
    deliverable = bool(payload.get("deliverable"))
    blocking = counts["blocking"]

    if not levels:
        summary = ("还没跑过判定——这一屏的空白不代表通过，"
                   "先跑 `hw_ladder`")
    elif stale:
        summary = ("设计改过了，下面是**上次判定**的结论："
                   f"{_level_text(levels)}；重跑判定前别拿它做决定")
    elif blocking:
        summary = f"不可打板：{blocking} 条阻断，{_level_text(levels)}"
    elif rollup == "unknown":
        summary = (f"没有阻断，但结论不完整：{_level_text(levels)}。"
                   f"另有 {counts['missing_data']} 条缺数据、"
                   f"{counts['assumed']} 条用了默认值")
    else:
        summary = f"九层全过：{_level_text(levels)}"

    return {
        "status": "ok",
        "project": store.id,
        "summary": summary,
        "deliverable": deliverable,
        #: 九层压成一个：fail > unknown > pass；没跑过是 not_ready
        "rollup": rollup,
        "levels": levels,
        "levels_total": len(LEVEL_ORDER),
        "counts": counts,
        "next": next_rows,
        "next_total": len(ordered),
        "checked": dict(payload.get("checked") or {}),
        "sources": dict(payload.get("sources") or {}),
        "stale": stale,
        "ladder_age": age,
        #: 一句话说清"这一屏的数字从哪来、什么时候过期"
        "note": ("来自已有产物（ladder/handoff/claims/checks/budget/deferred/"
                 "justify/units/links/coverify/emit），不重新判定；"
                 "设计改过就会 stale"),
    }


def _level_text(levels: list[dict[str, Any]]) -> str:
    """``V0 ✓ · V1 ? · V2 ✗ …`` —— 一句话看完九层。"""
    marks = {"pass": "✓", "fail": "✗", "unknown": "?", "not_run": "–",
             "truncated": "!"}
    return " · ".join(f"{row['level']} {marks.get(row['status'], '?')}"
                      for row in levels)


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.verdict",
        "max_next": MAX_NEXT,
        "level_order": list(LEVEL_ORDER),
        "category_rank": dict(CATEGORY_RANK),
        "status_rank": dict(STATUS_RANK),
        "why": ("一屏结论：九层横条 + 六类计数 + 排好序的下一步；"
                "只投影已有产物，不重新判定"),
        "stale": "design.json 比 ladder.json 新 → stale（设计改过但没重跑判定）",
    }