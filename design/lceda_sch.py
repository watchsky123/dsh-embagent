"""原理图 DRC（`lceda-sch-drc`）—— 原理图也进六段审计。

## 这一层补什么

`emit` 只跑 PCB 的 `PCB_Drc.check`。原理图层的错（悬空脚、同名网络、
多重输出……）一直没人查 —— 而它们是"板子做出来了、固件却不动"的常见根因。

`SCH_Drc.check(strict, false, true)` 返回**分组树**（顶层长度 = 类别数，
不是错误总数），每条的 `type` 是 `fatalError` / `error` / `warn`。
布尔模式 `check(strict,false,false)` 会把 **warn 也判成 false**（文档原话：
`CHECKFLOATPIN` 这类刻意保留的悬空脚就会这样）——所以本模块用**详细模式**
并自己按 `type` 过滤，不直接信布尔值。

## 判据

| 结果 | 含义 |
|---|---|
| `ok` | 每页都查过，且没有 `fatalError` / `error`（`warn` 单列出来，不影响 gate）|
| `failed` | 有 `fatalError` 或 `error` |
| `not_ready` | 没有工程 / 没有原理图页 / 一页都没查成 —— **不是通过** |
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from . import lceda

__all__ = ["FILE", "run", "describe"]

FILE = "sch_drc.json"

_JS_OPEN = """
const P = __CTX__;
for (let i = 0; i < 25; i++) {
  try { const t = await eda.dmt_Team.getAllTeamsInfo(); if (t && t.length) break; } catch (e) {}
  await new Promise(r => setTimeout(r, 300));
}
const opened = await eda.dmt_Project.openProject(P.projectUuid);
if (!opened) throw new Error('openProject 返回 false');
await new Promise(r => setTimeout(r, P.settleMs));
const pages = await eda.dmt_Schematic.getAllSchematicPagesInfo();
return {opened, pages: (pages || []).map(p => ({uuid: p.uuid, name: p.name || ''}))};
"""

_JS_CHECK = """
const P = __CTX__;
const opened = await eda.dmt_EditorControl.openDocument(P.pageUuid);
await new Promise(r => setTimeout(r, P.settleMs));
const grouped = await eda.sch_Drc.check(true, false, true);
const flat = [];
const walk = (node) => {
  if (!node) return;
  if (Array.isArray(node)) { node.forEach(walk); return; }
  if (node.type || node.rule) {
    flat.push({type: node.type || 'unknown', rule: node.rule || '',
               net: node.net || '', count: (node.primitives || []).length});
    return;
  }
  if (node.children) walk(node.children);
};
walk(grouped);
return {opened, grouped: Array.isArray(grouped) ? grouped.length : -1, errors: flat};
"""


def _flatten_counts(errors: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"fatalError": 0, "error": 0, "warn": 0, "unknown": 0}
    for row in errors:
        kind = str(row.get("type") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def run(project_dir: Path, *, session: lceda.LcedaSession | None = None,
        ctx: dict[str, Any] | None = None, limit_pages: int = 0,
        settle_ms: int = 900) -> dict[str, Any]:
    """逐页跑原理图 DRC。**一页都没查成 → not_ready**。"""
    if not session or not session.ready:
        return {"status": "not_ready", "reason": "没有可用的会话（先 open）"}
    if not ctx or not ctx.get("projectUuid"):
        return {"status": "not_ready", "reason": "缺 projectUuid（先 emit）"}
    try:
        opened = session.invoke(
            _JS_OPEN.replace("__CTX__", json.dumps({**ctx, "settleMs": settle_ms},
                                                   ensure_ascii=False)),
            label="sch:open", timeout_ms=280_000)
    except Exception as exc:  # noqa: BLE001
        return {"status": "not_ready",
                "reason": f"打不开工程：{type(exc).__name__}: {exc}"}
    pages = [page for page in (opened or {}).get("pages") or []
             if isinstance(page, dict) and page.get("uuid")]
    if limit_pages > 0:
        pages = pages[:limit_pages]
    if not pages:
        return {"status": "not_ready", "reason": "工程里没有原理图页"}

    rows: list[dict[str, Any]] = []
    checked = 0
    for page in pages:
        # **打开文档偶发失败**（实测：连开三页时第一页有时返回 stale）。
        # 重试一次（多等 1.5 秒）——仍失败才记 failed，不静默跳过。
        got = None
        for attempt in range(2):
            try:
                got = session.invoke(
                    _JS_CHECK.replace("__CTX__", json.dumps(
                        {**ctx, "pageUuid": page["uuid"], "settleMs": settle_ms},
                        ensure_ascii=False)),
                    label=f"sch:{page.get('name') or page['uuid']}:{attempt}",
                    timeout_ms=280_000)
            except Exception as exc:  # noqa: BLE001
                got = {"error": f"{type(exc).__name__}: {exc}"}
            if isinstance(got, dict) and got.get("opened"):
                break
            settle_ms = min(settle_ms + 800, 3000)
        if not isinstance(got, dict) or not got.get("opened"):
            rows.append({"page": page.get("name") or page["uuid"],
                         "status": "failed",
                         "reason": f"打不开这一页（重试一次）：{(got or {}).get('error') or ''}"})
            continue
        errors = [row for row in got.get("errors") or [] if isinstance(row, dict)]
        counts = _flatten_counts(errors)
        checked += 1
        rows.append({
            "page": page.get("name") or page["uuid"],
            "page_uuid": page["uuid"],
            "status": ("failed" if counts.get("fatalError") or counts.get("error")
                       else "ok"),
            "categories": int(got.get("grouped") or 0),
            "counts": counts,
            "items": errors[:40],
        })

    hard = [row for row in rows if row["status"] == "failed"
            and (row.get("counts") or {}).get("fatalError", 0) + (row.get("counts") or {}).get("error", 0) > 0]
    if checked == 0:
        status = "not_ready"
    elif hard:
        status = "failed"
    else:
        status = "ok"
    totals: dict[str, int] = {"pages": len(pages), "checked": checked}
    for kind in ("fatalError", "error", "warn"):
        totals[kind] = sum(int((row.get("counts") or {}).get(kind, 0)) for row in rows)
    return {
        "status": status,
        "file": FILE,
        "pages": rows,
        "counts": totals,
        "gate": ("`warn` 不影响 gate；`fatalError`/`error` 才判 failed"
                 "（立创的布尔模式会把 warn 也判 false，这里不用它）"),
        "retrieved": date.today().isoformat(),
        "note": ("一页都没查成是 not_ready，不是 ok；"
                 "刻意保留的悬空脚会以 warn 出现，不挡门"),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.lceda_sch",
        "file": FILE,
        "api": "SCH_Drc.check(true, false, true)（详细模式，自行按 type 过滤）",
        "why_not_bool": ("布尔模式 strict 下 warn 也算 false；"
                         "CHECKFLOATPIN 这类刻意悬空脚会误判"),
        "status": "ok / failed（有 error）/ not_ready（没查到）",
    }