"""尺寸来源：把"元件的 L/W/H 是从哪来的"说清楚（P41）。

## 为什么单独一个命令

机械域（MJCF）、布局规则（120 mil 中心距）、3D 场景、PCB 焊盘排布**全都**
依赖 `size_mm`。而这些数字有三种质量完全不同的来源：

| 来源 | 可信度 | 怎么来的 |
|---|---|---|
| `datasheet` / `measured` | 高 | 人抄手册，或实测 |
| `catalog` | 中 | 立创 3D 模型包围盒 / JEDEC 封装表 |
| `proposed` | 低 | AI 提案（"这看起来像 0402"） |
| 空/全 0 | **没有** | 没人给 |

现在的代码已经有一条推导链（`easyeda._size_from_attrs`：`3D Model Transform`
的 sizeX/Y/Z（mil）→ `3D Model Title` 的 `L1.6-W0.8-H0.8`（mm）→ 兜底不猜）。
`dims` 不重写它 —— 重写就是第二份事实源（AGENTS §1.1）。这里做的是：

1. **按来源分类**每一颗的尺寸（`spec` / `3d` / `catalog` / `unknown`）
2. 告诉调用方**哪些能当精确值**（`trusted` 列表），哪些不能
3. `--apply` 时只把**3D/目录**来源写回，并标 `catalog`（不冒充 datasheet）
"""

from __future__ import annotations

from typing import Any, Iterable

from .easyeda import _size_from_attrs  # noqa: PLC2701 - 同一份推导，别抄第二份

__all__ = ["size_from_attrs", "for_design", "describe", "TRUSTED_SIZE_SOURCES"]

#: 哪些来源的尺寸可以直接用于机械/布局判据。
TRUSTED_SIZE_SOURCES = ("datasheet", "measured", "catalog")


def size_from_attrs(attrs: dict[str, Any] | None) -> tuple[dict[str, float] | None, str]:
    """从立创原理图属性推 L/W/H（mm）。返回 `(size, 依据)`。

    `依据` 是 `transform` / `title` / `""`（都没给）。**拿不到就返回 None**，
    不填默认值（一个 0×0 的盒子会让布局规则全部静默通过）。
    """
    got = _size_from_attrs(attrs or {})
    if not got:
        return None, ""
    source = "transform" if str((attrs or {}).get("transform_3d") or "").strip() else "title"
    return got, source


def _classify(part: Any) -> tuple[str, bool, str]:
    """`(来源类别, 是否可信, 说明)`。"""
    size = getattr(part, "size_mm", {}) or {}
    w, h, d = (float(size.get(k, 0) or 0) for k in ("w", "h", "d"))
    source = str(getattr(part, "physical_source", "") or "")
    if w <= 0 or h <= 0:
        return "unknown", False, "尺寸缺失（w/h 为 0）—— 不能进机械/布局判据"
    if source in TRUSTED_SIZE_SOURCES:
        return source, True, f"来源 {source}（可信）"
    if source == "proposed":
        return "proposed", False, "AI 提案尺寸（未核对）—— 只能当占位"
    if source == "estimated":
        return "estimated", False, "按公式估算（体积×密度那类）—— 不能当精确值"
    if source == "derived":
        return "derived", False, "推导值 —— 不能当精确值"
    return "unknown", False, f"来源不明（{source or '空'}）"


def for_design(design: Any, *, attrs_by_ref: dict[str, dict[str, Any]] | None = None
               ) -> dict[str, Any]:
    """逐颗报告尺寸来源。

    `attrs_by_ref` 可给"立创侧刚读到的属性"（`transform_3d` / `model_title`）：
    那些能用 3D 模型补出真尺寸，而且**只用于报告**，不回写（回写要 `--apply`）。
    """
    attrs_by_ref = attrs_by_ref or {}
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for part in getattr(design, "parts", []) or []:
        category, trusted, why = _classify(part)
        row: dict[str, Any] = {
            "ref": part.ref,
            "lcsc": getattr(part, "lcsc", None) or "",
            "size_mm": dict(getattr(part, "size_mm", {}) or {}),
            "category": category,
            "trusted": trusted,
            "why": why,
        }
        attrs = attrs_by_ref.get(part.ref)
        if attrs:
            derived, basis = size_from_attrs(attrs)
            row["from_3d"] = derived
            row["from_3d_basis"] = basis
            if derived and not trusted:
                row["why"] += (f"；立创 3D 模型给出 {basis} 尺寸，"
                               "`dims --apply` 可写回（标 catalog）")
        rows.append(row)
        counts[category] = counts.get(category, 0) + 1

    total = len(rows)
    trusted_n = sum(1 for r in rows if r["trusted"])
    fixable = [r["ref"] for r in rows
               if (r.get("from_3d") or {}) and not r["trusted"]]
    unknown = [r["ref"] for r in rows if r["category"] == "unknown"]
    return {
        "status": "ok" if total and trusted_n == total else "not_ready",
        "total": total,
        "trusted": trusted_n,
        "counts": counts,
        "rows": rows,
        "fixable_by_3d": fixable,
        "unknown_refs": unknown,
        "note": (f"{trusted_n}/{total} 颗的尺寸可信"
                 + (f"；{len(fixable)} 颗能用立创 3D 模型补" if fixable else "")
                 + (f"；{len(unknown)} 颗完全没尺寸" if unknown else "")),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.dims",
        "trusted_sources": list(TRUSTED_SIZE_SOURCES),
        "categories": ["datasheet", "measured", "catalog", "estimated",
                       "derived", "proposed", "unknown"],
        "chain": "3D Model Transform（mil）→ 3D Model Title（L/W/H mm）→ 不猜",
        "discipline": ("拿不到尺寸就报 unknown，不填 0；"
                       "proposed/estimated 不可进机械/布局判据"),
    }