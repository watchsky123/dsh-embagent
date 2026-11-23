"""封装真几何（`lceda-geom`）—— 尺子从"目录猜"换成"封装源数据"。

## 这一层补什么

`dims` 现在能说清尺寸来源，但**最可信的那一档（`catalog`）一直是空的**：
立创目录里只有 `encapsulationNumber`（每盘颗数，不是尺寸）、JEDEC 表只覆盖
少数封装。于是大部分元件尺寸是 `estimated`（体积×密度估的占位）。

编辑器里其实有**封装真几何**：`LIB_Footprint.getGeometry(footprintUuid, libraryUuid)`
一次返回本体包围盒 / 焊盘包围盒 / 全部焊盘（位置、尺寸、层、各自 bbox，单位 mil）。

## 拿到什么

| 字段 | 含义 | 用途 |
|---|---|---|
| `full_mm` | 焊盘外包（mil→mm）| 占位/间距（最保守的物理边界）|
| `body_mm` | 本体包围盒 | 外形参考（实测 LQFP48 给 8.09×8.58，含丝印/引脚，**不是** 7×7）|
| `title_size_mm` | 3D 模型标题 `L7.0-W7.0-H1.4` | **本体+高度**，和 `dims` 用的是同一份解析 |
| `pads` / `pad_w_mm` / `pitch_mm` / `min_pad_gap_mm` | 焊盘统计 | DFM（最小焊盘间隙）/ 布局 |

`title_size_mm` 与 `full_mm` 是两件事：前者是芯片本体（3D 模型的 L/W/H），
后者是**焊盘最外沿**。写进 `design.json` 的 `size_mm` 用本体 + 高度，
`full_mm` 单独留给间距判据。

## 纪律

* **只覆盖低信任来源**：`datasheet` / `measured` 的尺寸**一个字节都不动**
  （`SOURCE_ORDER` 里 catalog 低于它们）；空/`estimated`/`proposed`/`catalog` 才写回
* 拿不到就是拿不到：`getByLcscIds` 找不到、没有封装、几何返回 `undefined`
  → 进 `unresolved` 并写明原因，**不填默认值**
* 一个都拿不到 → `not_ready`（不是 ok）；部分拿到 → `ok` + `complete=false`
"""

from __future__ import annotations

import json
import re
import statistics
from datetime import date
from typing import Any

from . import lceda

__all__ = ["FILE", "fetch", "apply", "describe"]

FILE = "geom.json"

#: mil → mm（1 mil = 0.0254 mm）。
MIL_TO_MM = 0.0254

#: 焊盘中心距离/间隙的统计口径：只收 SMD（无钻孔）焊盘，避免通孔把
#: 中心距拉到板厚级别。
_SMD_LAYER = 1

#: 一次 `invoke` 里查多少个 LCSC。器件逐个查（`getByLcscIds` 一次只收一个
#: 编号），8 个/批实测 1–2 秒，远低于 30 秒上限。
BATCH = 8

_BATCH_CODE = """
const L = %s;
const mm = (v) => v * 0.0254;
const out = [];
for (const lcsc of L) {
  try {
    const dev = await eda.lib_Device.getByLcscIds(lcsc);
    if (!dev) { out.push({lcsc, ok: false, reason: 'device_not_found'}); continue; }
    const f = dev.footprint || null;
    if (!f || !f.uuid) { out.push({lcsc, ok: false, reason: 'no_footprint', device: dev.name}); continue; }
    const g = await eda.lib_Footprint.getGeometry(f.uuid, f.libraryUuid);
    if (!g) { out.push({lcsc, ok: false, reason: 'geometry_unavailable', device: dev.name}); continue; }
    out.push({
      lcsc, ok: true, device: dev.name || '', model3d: dev.model3DName || '',
      footprint: {uuid: f.uuid, libraryUuid: f.libraryUuid, name: f.name || ''},
      body: g.bodyBBox || null, full: g.fullBBox || null,
      pads: (g.pads || []).map(p => ({
        n: p.padNumber, shape: p.shape, x: p.x, y: p.y,
        w: p.width, h: p.height, rot: p.rotation,
        plated: !!p.plated, hole: p.holeDiameter || 0
      }))
    });
  } catch (e) {
    out.push({lcsc, ok: false, reason: String(e)});
  }
}
return out;
"""


def _box_mm(box: dict[str, Any] | None) -> dict[str, float] | None:
    """`{minX,minY,maxX,maxY}`（mil）→ `{w,h}`（mm）。**缺一个字段就 None**。"""
    if not isinstance(box, dict):
        return None
    try:
        width = (float(box["maxX"]) - float(box["minX"])) * MIL_TO_MM
        height = (float(box["maxY"]) - float(box["minY"])) * MIL_TO_MM
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return {"w": round(width, 3), "h": round(height, 3)}


def _title_size(title: str) -> dict[str, float] | None:
    """`LQFP-48_L7.0-W7.0-H1.4-LS9.0` → `{w:7.0,h:7.0,d:1.4}`（mm）。

    解析**复用** `dims.size_from_attrs` —— 两份解析就是两个事实源。
    """
    from .dims import size_from_attrs  # noqa: PLC0415  延迟导入（避免循环）

    got, _basis = size_from_attrs({"model_title": title})
    return got or None


def _pad_stats(pads: list[dict[str, Any]]) -> dict[str, Any]:
    """焊盘统计：数量、中位尺寸、中位中心距、最小间隙（毫米）。

    * 中心距：SMD 焊盘两两最近邻中心距离的中位数（等距排列时 = pitch）
    * 最小间隙：**不同编号**焊盘之间，按坐标轴分离算的最近距离
      （矩形近似：`max(0, |dx|-(w1+w2)/2)` 与 y 同理取欧氏）
    """
    rows = [p for p in pads if isinstance(p, dict)]
    if not rows:
        return {"pads": 0}
    smd = [p for p in rows if not p.get("hole")]
    base = smd or rows
    widths = [float(p.get("w") or 0) * MIL_TO_MM for p in base]
    heights = [float(p.get("h") or 0) * MIL_TO_MM for p in base]
    stats: dict[str, Any] = {
        "pads": len(rows),
        "smd_pads": len(smd),
        "pad_w_mm": round(statistics.median([w for w in widths if w > 0]), 3)
        if any(w > 0 for w in widths) else 0.0,
        "pad_h_mm": round(statistics.median([h for h in heights if h > 0]), 3)
        if any(h > 0 for h in heights) else 0.0,
    }
    if len(base) >= 2:
        points = [(float(p.get("x") or 0), float(p.get("y") or 0)) for p in base]
        nearest = []
        for i, (x, y) in enumerate(points):
            best = min(
                ((x - ox) ** 2 + (y - oy) ** 2) ** 0.5
                for j, (ox, oy) in enumerate(points) if j != i) if len(points) > 1 else 0
            if best > 0:
                nearest.append(best * MIL_TO_MM)
        if nearest:
            stats["pitch_mm"] = round(statistics.median(nearest), 3)
    if len(base) >= 2:
        gap = None
        for i, a in enumerate(base):
            ax, ay = float(a.get("x") or 0), float(a.get("y") or 0)
            aw = float(a.get("w") or 0) / 2
            ah = float(a.get("h") or 0) / 2
            for j, b in enumerate(base):
                if j <= i or a.get("n") == b.get("n"):
                    continue
                bx, by = float(b.get("x") or 0), float(b.get("y") or 0)
                bw = float(b.get("w") or 0) / 2
                bh = float(b.get("h") or 0) / 2
                dx = max(0.0, abs(ax - bx) - (aw + bw))
                dy = max(0.0, abs(ay - by) - (ah + bh))
                d = (dx * dx + dy * dy) ** 0.5 * MIL_TO_MM
                gap = d if gap is None else min(gap, d)
        if gap is not None:
            stats["min_pad_gap_mm"] = round(gap, 3)
    return stats


def _summarize(item: dict[str, Any]) -> dict[str, Any]:
    """一条 `invoke` 结果 → `geom.json` 里的一条（**没有数就不写数**）。"""
    full = _box_mm(item.get("full"))
    body = _box_mm(item.get("body"))
    title = _title_size(str(item.get("model3d") or ""))
    stats = _pad_stats(list(item.get("pads") or []))
    row: dict[str, Any] = {
        "lcsc": item.get("lcsc"),
        "device": item.get("device") or "",
        "model3d": item.get("model3d") or "",
        "footprint": item.get("footprint") or {},
        "body_mm": body,
        "full_mm": full,
        "title_size_mm": title,
        **stats,
        # 原始 mil 包围盒留着：换算是可复核的（check_p58 会重算一遍）
        "raw_bbox_mil": {"full": item.get("full"), "body": item.get("body")},
        "source": "catalog",
        "basis": "立创封装源几何（LIB_Footprint.getGeometry，单位 mil→mm）",
    }
    return row


def _choose_size(row: dict[str, Any]) -> dict[str, float] | None:
    """写回 `design.json` 用的 `size_mm`：**只用本体（3D 模型 L/W/H）**。

    为什么不用 `full_mm`：它是**焊盘最外沿**——拿它当本体会把 LQFP48 记成
    10×10（实际 7×7）、把 0603 记成 2.9×1.5（实际 1.6×0.8）。`bodyBBox` 实测
    也含丝印/引脚（LQFP48 给了 8.09×8.58），同样不能当本体。

    没有 3D 标题 = 不知道本体尺寸 → **返回 None**（宁可不写，也不写错）。
    """
    got = row.get("title_size_mm")
    if isinstance(got, dict) and got.get("w") and got.get("h"):
        return {"w": float(got["w"]), "h": float(got["h"]),
                "d": float(got.get("d") or 0.0)}
    return None


def fetch(design: Any, *, session: lceda.LcedaSession | None = None,
          limit: int = 0, batch: int = BATCH) -> dict[str, Any]:
    """把设计里所有带 `lcsc` 的元件几何取回来。**不写盘**（`apply` 才写）。"""
    parts = [p for p in getattr(design, "parts", [])
             if str(getattr(p, "lcsc", "") or "").strip()]
    if limit > 0:
        parts = parts[:limit]
    if not session or not session.ready:
        return {"status": "not_ready", "reason": "没有可用的会话（先 open）",
                "file": FILE}
    # **同一个 LCSC 往往对应多个位号**（一堆 100nF）。按 LCSC 查一次，
    # 再展开到所有位号 —— 否则"查了 28 个、只记了 16 个"就是静默丢数据。
    refs_by_lcsc: dict[str, list[str]] = {}
    for part in parts:
        refs_by_lcsc.setdefault(str(part.lcsc).strip().upper(), []).append(part.ref)
    by_lcsc = {lcsc: refs[0] for lcsc, refs in refs_by_lcsc.items()}
    wanted = sorted(by_lcsc)
    # **两轮**：第一轮把库缓存暖起来；`no_footprint` / `geometry_unavailable`
    # 再试一次 —— 实测同一个 LCSC 连查三次，`footprintUuid` 会在"空/有"之间
    # 跳（库 worker 异步装载）。`device_not_found` 不值得重试。
    RETRY_REASONS = ("no_footprint", "geometry_unavailable")
    resolved_items: dict[str, dict[str, Any]] = {}
    last_reason: dict[str, str] = {}
    pending = list(wanted)
    for _round in range(2):
        if not pending:
            break
        retry_next: list[str] = []
        for start_at in range(0, len(pending), max(1, batch)):
            chunk = pending[start_at:start_at + max(1, batch)]
            code = _BATCH_CODE % json.dumps(chunk)
            try:
                got = session.invoke(code, label=f"geom:{_round}:{start_at // max(1, batch)}")
            except Exception as exc:  # noqa: BLE001
                for lcsc in chunk:
                    last_reason[lcsc] = f"invoke 失败：{type(exc).__name__}: {exc}"
                continue
            if not isinstance(got, list):
                for lcsc in chunk:
                    last_reason[lcsc] = "invoke 返回不是数组"
                continue
            for item in got:
                if not isinstance(item, dict):
                    continue
                lcsc = str(item.get("lcsc") or "").upper()
                if item.get("ok"):
                    resolved_items[lcsc] = item
                    last_reason.pop(lcsc, None)
                else:
                    reason = str(item.get("reason") or "unknown")
                    last_reason[lcsc] = reason
                    if _round == 0 and reason in RETRY_REASONS:
                        retry_next.append(lcsc)
        pending = [lcsc for lcsc in retry_next if lcsc not in resolved_items]

    rows: dict[str, Any] = {}
    unresolved: list[dict[str, Any]] = []
    for lcsc in wanted:
        refs = refs_by_lcsc.get(lcsc) or []
        item = resolved_items.get(lcsc)
        if item is None:
            unresolved.append({"lcsc": lcsc, "refs": refs,
                               "reason": last_reason.get(lcsc, "unknown")})
            continue
        row = _summarize(item)
        row["refs"] = refs
        for ref in refs:
            rows[ref] = row

    unresolved_refs = sum(len(set(row.get("refs") or [])) for row in unresolved)
    # **尺寸核对**：几何给的是本体（3D 标题 L/W/H），设计里已写的是人抄的
    # datasheet / 目录值。两者差 >15% 说明至少有一边错了 —— 只提示，
    # **不判 fail**（可能是 datasheet 的"含引脚尺寸"与本体尺寸口径不同）。
    size_check: list[dict[str, Any]] = []
    for part in parts:
        row = rows.get(part.ref)
        if not isinstance(row, dict):
            continue
        geom_size = row.get("title_size_mm")
        cur = dict(getattr(part, "size_mm", {}) or {})
        if not isinstance(geom_size, dict) or not geom_size.get("w"):
            continue
        if not (float(cur.get("w") or 0) > 0 and float(cur.get("h") or 0) > 0):
            continue
        deltas = {axis: abs(float(cur.get(axis) or 0) - float(geom_size.get(axis) or 0))
                  / float(geom_size.get(axis) or 1) for axis in ("w", "h")}
        worst = max(deltas.values())
        size_check.append({
            "ref": part.ref, "lcsc": row.get("lcsc"),
            "design": {"w": cur.get("w"), "h": cur.get("h"), "d": cur.get("d")},
            "geom": geom_size, "delta_pct": round(worst * 100, 1),
            "verdict": ("ok" if worst <= 0.05 else
                        "close" if worst <= 0.15 else "mismatch"),
            "design_source": str(getattr(part, "physical_source", "") or ""),
        })
    mismatches = [row for row in size_check if row["verdict"] == "mismatch"]
    counts = {
        "size_checked": len(size_check),
        "size_mismatch": len(mismatches),
        "parts_with_lcsc": len(parts),
        "resolved": len(rows),
        "unresolved": unresolved_refs,
        "missing": len(parts) - len(rows) - unresolved_refs,
        "lcsc_queried": len(wanted),
        "pads": sum(int((r.get("pads") or 0)) for r in rows.values()),
    }
    status = ("not_ready" if counts["resolved"] == 0 else "ok")
    payload = {
        "status": status,
        "file": FILE,
        "session": {"version": session.version, "headless": session.headless},
        "parts": rows,
        "unresolved": unresolved,
        "size_check": size_check,
        "counts": counts,
        "complete": not unresolved,
        "source": "catalog",
        "basis": ("立创封装源几何（LIB_Footprint.getGeometry）；"
                  "mil→mm=0.0254；本体优先，焊盘外包兜底"),
        "retrieved": date.today().isoformat(),
        "note": ("拿不到的进 unresolved 并写明原因；**不填默认值**。"
                 "一个都没拿到是 not_ready，不是 ok"),
    }
    if status != "ok":
        payload["reason"] = "没有任何元件拿到封装几何"
    return payload


def apply(design: Any, geom: dict[str, Any]) -> dict[str, Any]:
    """把几何写回 `size_mm` —— **只覆盖低信任来源**（P58）。

    规则（`SOURCE_ORDER`: proposed < estimated < catalog < datasheet < measured）：

    * `datasheet` / `measured`：**不动**，只记 `skipped`
    * 空 / `estimated` / `proposed` / `catalog`：写回并标 `catalog`
    * 拿不到本体尺寸（只有焊盘外包）时用外包，但 `basis` 里写明

    返回 `{applied, skipped, unresolved}`，**不保存**（调用方决定怎么落盘）。
    """
    from .model import DesignModel  # noqa: PLC0415

    assert isinstance(design, DesignModel)
    rows = geom.get("parts") or {}
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for part in design.parts:
        row = rows.get(part.ref)
        if not isinstance(row, dict):
            continue
        size = _choose_size(row)
        if not size:
            continue
        current = str(getattr(part, "physical_source", "") or "")
        if current in ("datasheet", "measured"):
            skipped.append({"ref": part.ref, "why": f"已有 {current} 尺寸（更高信任）",
                            "kept": dict(getattr(part, "size_mm", {}) or {}),
                            "geom": row.get("title_size_mm")})
            continue
        old = dict(getattr(part, "size_mm", {}) or {})
        want = {"w": size["w"], "h": size["h"],
                "d": size["d"] if size["d"] > 0 else float(old.get("d") or 0.0)}
        part.size_mm = want
        part.physical_source = "catalog"
        applied.append({"ref": part.ref, "lcsc": row.get("lcsc"),
                        "old": old, "new": want,
                        "basis": ("title" if row.get("title_size_mm")
                                  else "body" if row.get("body_mm") else "full")})
    return {"applied": applied, "skipped": skipped,
            "unresolved": list(geom.get("unresolved") or []),
            "counts": {"applied": len(applied), "skipped": len(skipped)}}


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.lceda_geom",
        "file": FILE,
        "needs": "lceda-pro（桌面客户端）+ 会话（headless 即可）",
        "api": ("LIB_Device.getByLcscIds → LIB_Footprint.getGeometry"
                "（一次给本体/焊盘包围盒 + 每个焊盘）"),
        "trust": ("写回只覆盖空/estimated/proposed/catalog；"
                  "datasheet/measured 一个字节不动"),
        "not_provided": "3D 模型包围盒（本版 getGeometry 不返回 model3DBBox）",
        "discipline": ("拿不到进 unresolved；一个都没拿到 → not_ready；"
                       "本体优先于焊盘外包（别把 LQFP48 记成 10×10）"),
    }