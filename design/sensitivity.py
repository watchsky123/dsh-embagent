"""参数敏感度 —— 把「哪个参数最该去核」排出来。

## 这一层解决什么

136 个参数躺在表里，全部 `source=public`（**一项都进不了硬判定**）。
人面对这张表的问题是：**我该去查哪一个？**

答案是：**132 个根本不需要查**。

## 为什么「未接线」必须和「不敏感」分开（这是本模块的核心）

实测：`ParamSet` 的**唯一消费者是 `mjcf.build`**，它只 `params.get` 了
4 个键：

```
dt  mu_board  board_solref  axle_fric_nm
```

另外 `density_kg_m3` 也被读，但**不在参数表里**（走 `DEFAULT_DENSITY` 常量）。
其余 **132 个参数没有任何代码读它们**——把它们在表里改成任何值，
仿真结果一个字都不会变。

如果把 132 个"没人读"的参数报成"敏感度低"，那是**假绿**：
读的人会以为"低敏感 = 不重要但已评估过"，而真相是**从未接入**。
所以本模块报三类，**各报各的**：

| 类别 | 含义 | 该做什么 |
|---|---|---|
| `wired` | 代码真的读，且改它 MJCF 会变 | 核这个值 |
| `wired_noop` | 代码读了，但本设计里没有对应机制（如无 axle 关节） | 设计里没这个物理，**不用核** |
| `unwired` | **没有任何代码读** | 要么补接线，要么从表里拿掉 |
| `missing` | 代码读了但表里没这一项 | **参数表缺项**，需要补一行 |

## 两层敏感度

| 层 | 测什么 | 依赖 | 成本 |
|---|---|---|---|
| **模型层** | 扰动参数 → MJCF 文本变不变 | 无（纯字符串比对） | 便宜，全量可做 |
| **判定层** | 扰动参数 → 某一层状态翻 | V7 需要 MuJoCo | 贵，只对模型层敏感的做 |

**模型层永远能算**（只要参数在表里且类型可扰动）。
**判定层在没有 MuJoCo 时对物理参数是 `unknown`，不是「不敏感」**——
AGENTS §0.1 那条，在参数维度上的复述：**测不出来 ≠ 测过没问题**。

## 扰动策略

数值型取 **×0.5 与 ×2** 两个点。选这两个是因为"线性响应"的参数在
这两个点上都能体现变化，而非线性参数（如摩擦的饱和）至少有一个方向会变。
布尔与列表类型**不扰动**——没有自然的扰动方式，报 `unknown` 而不是编一个。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

__all__ = ["PERTURBATIONS", "report", "sweep_model", "sweep_verdict", "wired_keys"]

#: 扰动因子。**两个方向都要**：只测一个方向会把"有响应但只在另一个方向"的
#: 参数漏成不敏感。
PERTURBATIONS = (0.5, 2.0)

#: 扫源码找 ParamSet 读取点。与 `check_p1` 的 1a 同一套手法：
#: **字面量散在代码里，漏了不会报错，只会静默失去功能**。
#:
#: 注意 `(?<![\w.])` 这个后向断言：`joint.params.get("range_deg")` 是
#: **关节自己的私有参数**，不是 `ParamSet`。第一版正则没管这个，
#: 把 `range_deg` / `ratio` / `net` 也当成 ParamSet 的接线，
#: 于是它们被报成"参数表缺项"——那是假问题（这些值来自 `Joint.params`，
#: 本就不该在这张表里）。
_READ_RE = re.compile(r"(?<![\w.])params\.get\(\s*[\"\']([a-zA-Z0-9_]+)")


def wired_keys(mjcf_source: str | Path | None = None) -> list[str]:
    """源码里 `params.get("...")` 出现过的键，**去重并排序**。

    这是「接线」的事实来源。**不要手写一张表**——手写的表会和代码分叉，
    而分叉的方式就是"表里多一个、代码里没有"。
    """
    if mjcf_source is None:
        mjcf_source = Path(__file__).resolve().parent / "mjcf.py"
    text = Path(mjcf_source).read_text(encoding="utf-8")
    return sorted(set(_READ_RE.findall(text)))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _perturb(value: Any, factor: float) -> Any | None:
    """数值 → value × factor；其余 → None（**不扰动，不编**）。"""
    if _is_number(value):
        if value == 0:
            # 0 乘什么都是 0，扰动不出变化。给一个"可观察的偏移"。
            return factor
        return value * factor
    return None


def _mjcf_fingerprint(parts: Any) -> tuple[str, tuple[str, ...]]:
    """MJCF 的指纹：头 + 每件的 XML。**不跑 MuJoCo**，只是字符串比对。"""
    header = getattr(parts, "header", "") or ""
    bodies = tuple(body.xml for body in (parts.parts or []))
    return header, bodies


def sweep_model(design: Any, params: Any,
                *, keys: list[str] | None = None,
                build: Callable[[Any, Any], Any] | None = None
                ) -> list[dict[str, Any]]:
    """模型层：逐个参数扰动，看 MJCF 变不变。

    返回每个参数的一行：`{key, group, value, status, perturbed, changed_layers}`
    `status` 三态：`wired`（变了）/ `wired_noop`（没变）/ `unwired`（不在 `keys` 里）。
    """
    from . import mjcf

    build = build or mjcf.build
    chosen = list(keys or params.params)
    rows: list[dict[str, Any]] = []
    baseline_set = params.params
    # 基准 MJCF 全表只构造一次（见下方 baseline_cache）
    baseline_cache: list[Any] = []

    for key in sorted(chosen):
        spec = baseline_set.get(key)
        if spec is None:
            rows.append({"key": key, "group": "", "value": None,
                         "status": "missing", "perturbed": [],
                         "changed": [], "note": "代码读了但表里没有这一项"})
            continue
        value = spec.value
        row = {"key": key, "group": spec.group, "value": value,
               "status": "unwired", "perturbed": [], "changed": [], "note": ""}
        if key not in (keys or []):
            row["note"] = "没有任何代码读它"
            rows.append(row)
            continue

        # **基准只构造一次**：build 是 O(元件数)，放在循环里等于白算。
        if not baseline_cache:
            baseline_cache.append(_mjcf_fingerprint(build(design, params)))
        baseline = baseline_cache[0]
        changed: list[str] = []
        tried = 0
        for factor in PERTURBATIONS:
            new_value = _perturb(value, factor)
            if new_value is None:
                row["perturbed"].append(
                    {"factor": factor, "status": "not_perturbed",
                     "reason": "值不是数值，没有自然的扰动方式"})
                continue
            perturbed = params.with_params({key: new_value}, source=spec.source)
            fingerprint = _mjcf_fingerprint(build(design, perturbed))
            ok = fingerprint != baseline
            tried += 1
            row["perturbed"].append({"factor": factor, "value": new_value,
                                     "changed": ok})
            if ok:
                changed.append(str(factor))
        if tried and changed:
            row["status"] = "wired"
            row["changed"] = changed
        elif tried:
            row["status"] = "wired_noop"
            row["note"] = ("被代码读取，但本设计里没有对应机制"
                           "（如没有 axle 关节 → axle_fric_nm 无处生效）")
        else:
            row["status"] = "wired_noop"
            row["note"] = "被代码读取，但值类型不是数值，没有自然的扰动方式"
        rows.append(row)
    return rows


def sweep_verdict(design: Any, params: Any, *,
                  keys: list[str] | None = None,
                  runner: Callable[[Any, Any], Any] | None = None
                  ) -> dict[str, list[dict[str, Any]]]:
    """判定层：只对**模型层敏感**的参数跑判定，看结论翻不翻。

    ## 复用 `sim_calibration.default_runner`，不另写一份

    它内部就是 `ladder.level_v7`（weld + 三次扰动 + 翻倒阈值）。再写一套
    MuJoCo 调用，两处阈值早晚分叉——AGENTS §1.1。

    ## 没有 MuJoCo 时

    `default_runner` 把 V7 的 `not_run` 翻成 `unknown`。**这一层如实报
    unknown，不报「不敏感」**：AGENTS §0.1 在参数维度上的复述——
    测不出来不等于测过没问题。

    返回 `{key: [行]}`，每行 `{factor, value, baseline, perturbed, flipped}`。
    """
    from . import sim_calibration

    chosen = runner or sim_calibration.default_runner
    out: dict[str, list[dict[str, Any]]] = {}
    for key in keys or []:
        spec = params.params.get(key)
        if spec is None or not _is_number(spec.value):
            continue
        baseline = chosen(design, params)
        rows: list[dict[str, Any]] = []
        for factor in PERTURBATIONS:
            value = _perturb(spec.value, factor)
            if value is None:
                continue
            perturbed = params.with_params({key: value}, source=spec.source)
            after = chosen(design, perturbed)
            rows.append({
                "factor": factor, "value": value,
                "baseline": str(baseline.get("status") or ""),
                "perturbed": str(after.get("status") or ""),
                "flipped": (str(after.get("status") or "")
                            != str(baseline.get("status") or "")),
                "detail": str(after.get("detail") or "")[:120],
            })
        out[key] = rows
    return out


def report(design: Any, params: Any, *,
           keys: list[str] | None = None,
           build: Callable[[Any, Any], Any] | None = None
           ) -> dict[str, Any]:
    """完整报告：分类 + 排序 + 摘要。**只做聚合，不重新判定。**"""
    wired = keys if keys is not None else wired_keys()
    rows = sweep_model(design, params, keys=wired, build=build)

    buckets = {"wired": [], "wired_noop": [], "unwired": [], "missing": []}
    for row in rows:
        buckets.setdefault(row["status"], []).append(row)

    ordered = [{"rank": index + 1, **row}
               for index, row in enumerate(
                   sorted(buckets["wired"],
                          key=lambda r: (-len(r["changed"]), r["key"])))]

    uncovered = sorted(set(params.params) - {r["key"] for r in rows})
    if uncovered:
        buckets["unwired"].extend(
            {"key": key, "group": params.params[key].group,
             "value": params.params[key].value, "status": "unwired",
             "perturbed": [], "changed": [],
             "note": "没有任何代码读它"} for key in uncovered)

    return {
        "status": "ok",
        "params_total": len(params.params),
        # **表内**参数分成这三类，三者之和 = `params_total`。
        # `missing` 是**表外**的（代码读了但表里没这一项），不计入这个和——
        # 所以四类计数相加会比 `params_total` 多，那是**对的**，不是漏算。
        "in_table_counts": {
            "wired": len(buckets.get("wired") or []),
            "wired_noop": len(buckets.get("wired_noop") or []),
            "unwired": len(buckets.get("unwired") or []),
        },
        "off_table": [row["key"] for row in (buckets.get("missing") or [])],
        "wired_total": len(wired),
        "counts": {name: len(buckets.get(name) or [])
                   for name in ("wired", "wired_noop", "unwired", "missing")},
        "priority": ordered,                 # **已排序**：前几个最该核
        "wired_noop": buckets.get("wired_noop") or [],
        "unwired": buckets.get("unwired") or [],
        "missing": buckets.get("missing") or [],
        "perturbations": list(PERTURBATIONS),
        "note": ("模型层：扰动参数看 MJCF 变不变（无 MuJoCo 依赖）；"
                 "判定层：只对模型层敏感的参数跑判定，见 sweep_verdict()"),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.sensitivity",
        "perturbations": list(PERTURBATIONS),
        "wired": wired_keys(),
        "why_split": ("未接线（没人读）与不敏感（读了但没效果）必须分开——"
                      "把 132 个没人读的报成「低敏感」是假绿"),
        "verdict_runner": "复用 sim_calibration.default_runner（= ladder.level_v7），"
                          "不另写第二份 MuJoCo 调用",
        "verdict_layer": "没有 MuJoCo 时物理参数的判定层是 unknown，不是不敏感",
    }