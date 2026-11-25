"""可选基线（C2，降级版）—— **只作参考，不参与 pass/fail**。

## 为什么降级

第一版写的是"拿 `示例-多板` 当基准，指标不许降"——那会变成
"满足那个工程 = 满足需求"。P64 起：

* 基线由人**显式记录**（默认没有文件）；
* 对比只输出 delta 与 `regressions`，`gate=false`，**不判失败**；
* 没有基线 → `not_ready`（"没记录"不是"没退化"）。

## 记录什么

从 `audit` 现场跑出来的阶段状态 + `maturity` 的 level + 工程规模：

| 指标 | 变差的方向 |
|---|---|
| `stages_ok` / `maturity_level` / `constraints_pass` / `parts` / `nets` | 变小 |
| `stages_failed` / `stages_not_ready` / `constraints_fail` | 变大 |

**它证明的是"改动没让东西变差"**，不是"满足需求"。
"""

from __future__ import annotations

from datetime import date
from typing import Any

__all__ = ["FILE", "snapshot", "record", "compare", "describe"]

FILE = "baseline.json"

#: 变差 = 变小。
_LOWER_IS_BAD = ("stages_ok", "maturity_level", "constraints_pass", "parts", "nets")
#: 变差 = 变大。
_HIGHER_IS_BAD = ("stages_failed", "stages_not_ready", "constraints_fail")


def snapshot(store: Any, design: Any) -> dict[str, Any]:
    """当前指标（**现场跑 audit + maturity**）。"""
    from . import audit as audit_module
    from . import constraints as constraints_module
    from . import maturity as maturity_module

    report = audit_module.audit(store, design)
    stages = {str(row.get("stage")): str(row.get("status"))
              for row in report.get("stages") or []}
    mat = maturity_module.compute(store, design)
    cons = ((store.read(constraints_module.FILE) or {}).get("counts") or {})
    metrics = {
        "parts": len(design.parts),
        "nets": len(design.nets),
        "blocks": len(getattr(design, "blocks_list", None) or []),
        "stages_ok": sum(1 for value in stages.values() if value == "ok"),
        "stages_not_ready": sum(1 for value in stages.values() if value == "not_ready"),
        "stages_failed": sum(1 for value in stages.values() if value == "failed"),
        "maturity_level": int(mat.get("level") or 0),
        "constraints_pass": int(cons.get("pass") or 0),
        "constraints_fail": int(cons.get("fail") or 0),
    }
    return {"metrics": metrics, "stages": stages,
            "maturity": mat.get("level"), "at": date.today().isoformat()}


def record(store: Any, design: Any) -> dict[str, Any]:
    """把当前指标记成基线。**人显式调用**，不自动。"""
    now = snapshot(store, design)
    payload = {**now, "note": "基线只作参考（gate=false）；指标变化不判 pass/fail"}
    store.write(FILE, payload)
    return {"status": "ok", **payload}


def compare(store: Any, design: Any) -> dict[str, Any]:
    """当前 vs 已记录的基线。**没有基线 → not_ready**。"""
    base = store.read(FILE)
    if not isinstance(base, dict) or not isinstance(base.get("metrics"), dict):
        return {"status": "not_ready",
                "reason": "还没有基线（baseline <id> --record）—— 没记录 ≠ 没退化"}
    now = snapshot(store, design)
    old = base["metrics"]
    new = now["metrics"]
    deltas = {key: int(new.get(key) or 0) - int(old.get(key) or 0)
              for key in set(old) | set(new)}
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    for key, delta in sorted(deltas.items()):
        if not delta:
            continue
        worse = (delta < 0 if key in _LOWER_IS_BAD else
                 delta > 0 if key in _HIGHER_IS_BAD else False)
        row = {"metric": key, "from": old.get(key), "to": new.get(key),
               "delta": delta}
        (regressions if worse else improvements).append(row)
    return {
        "status": "ok",
        "gate": False,
        "reference": True,
        "baseline_at": base.get("at"),
        "deltas": deltas,
        "regressions": regressions,
        "improvements": improvements,
        "counts": {"regressions": len(regressions),
                   "improvements": len(improvements)},
        "note": ("**只作参考**：基线对比不参与 pass/fail；"
                 "出现 regression 说明「改动让某个指标变差了」，需要人看一眼"),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.baseline",
        "file": FILE,
        "mode": "可选（默认无基线）；record 后才 compare",
        "gate": False,
        "metrics": list(_LOWER_IS_BAD) + list(_HIGHER_IS_BAD),
        "discipline": ("不绑定任何工程名；没有基线是 not_ready；"
                       "regressions 只报告不判失败"),
    }