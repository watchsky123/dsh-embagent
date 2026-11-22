"""制造可行性证据（DFM + DRC 分级）—— **哪些能信、哪些不能**。

## 为什么需要这一层

立创自己有 DRC，也有 DFM（设计可制造性）。它们**不是同一种证据**：

| 证据 | 可信 | 为什么 |
|---|---|---|
| `clearance` / `netlist` / `physical` | ✅ | 量的是几何与网表一致性 |
| `connection` | ❌ | **不认 API 放的过孔**，实测 4 条全是这一类的误报 |
| **DFM** | 有条件 | 见下 —— 它可能"什么都没查" |

## DFM 的三个坑（都是 API 文档里写明的）

`pcb_Dfm.checkDfm` 的元数据原文：

> **仅私有化构建可用**
> 结果可能不完整：未配置检查规则或存在检查失败项时，将以 `INCOMPLETE_RESULT`
> 错误抛出，**不得视为检查通过**
> ADD since EDA v4.1.93

于是三件事必须分开说：

1. **`getDfmAvailability()` 说不可用** → `not_ready`（这里没有这个能力）
2. **抛 `INCOMPLETE_RESULT`** → **`unknown`**（跑了，但结果不完整）——
   文档的原话就是"不得视为检查通过"，所以**任何情况下都不能算 ok**
3. 正常返回 → `ok`，但仍有 `warnings`（如 `DFM_NOT_CONFIGURED`）要带出来

这条和 AGENTS §0.1 是同一件事：**"没查"与"查过了没问题"必须分开**。
"""

from __future__ import annotations

from typing import Any

__all__ = ["DFM_INCOMPLETE", "TRUSTWORTHY_DRC", "dfm_verdict", "merge",
           "describe"]

#: DFM 抛出的"结果不完整"错误码（API 文档原文 `INCOMPLETE_RESULT`）。
DFM_INCOMPLETE = "INCOMPLETE_RESULT"

#: DRC 里**可信**的类别。`connection` 不在里面：实测它不认 API 放的过孔。
TRUSTWORTHY_DRC = ("clearance", "netlist", "physical")


#: 错误/可用性文本里出现这些词 → 这台机器**没有** DFM 能力。
_UNAVAILABLE_HINTS = ("私有化", "private", "not available", "unavailable", "不可用")


def _says_unavailable(value: Any) -> bool:
    """把"不可用"的各种表达形式认出来。**认不出就是不认**（保留 unknown）。"""
    if value is None or value is True:
        return False
    if isinstance(value, dict):
        for key in ("available", "enabled", "ok", "supported"):
            if key in value:
                return value.get(key) is False
        return False
    text = str(value)
    lowered = text.lower()
    if lowered in ("false", "0", "no"):
        return True
    return any(hint in text or hint in lowered for hint in _UNAVAILABLE_HINTS)


def dfm_verdict(result: Any = None, *, error: str = "",
                available: Any = None, ran: bool = True) -> dict[str, Any]:
    """把一次 DFM 调用的三种可能结果翻成三态。**纯函数，好测。**

    `available` 可以是 `getDfmAvailability()` 的原样返回值（bool / 字符串 /
    对象都行）；`result` 是 `IPCB_DfmCheckResult`；`error` 是抛出来的异常文本。
    """
    # 可用性可能是 bool、字符串，也可能是个对象（库的包装层各版本不一样）。
    # **只认明确的"不可用"**，别把"没返回"当成"可用"。
    if available is False or _says_unavailable(available):
        return {"status": "not_ready",
                "reason": "DFM 仅在**私有化构建**可用（`getDfmAvailability()` 报不可用）",
                "issues": [], "failed_items": 0}
    if not ran:
        return {"status": "not_ready", "reason": "没跑 DFM",
                "issues": [], "failed_items": 0}
    if error:
        text = str(error)
        # **本机实测（4.1.97，非私有化构建）**：`checkDfm` 直接抛
        # "DFM 仅在私有化构建可用" —— 这时正确结论是 `not_ready`（这里没这能力），
        # 不是 `unknown`（有能力但结果不完整）。两者对下游的意义完全不同。
        if _says_unavailable(text):
            return {"status": "not_ready",
                    "reason": f"DFM 在本机不可用（{text[:120]}）",
                    "issues": [], "failed_items": 0}
        if DFM_INCOMPLETE in text:
            return {
                "status": "unknown",
                "reason": ("DFM 结果**不完整**（未配置检查规则，或有检查失败项）—— "
                           "API 文档原文：不得视为检查通过"),
                "issues": [], "failed_items": 0,
            }
        return {"status": "unknown", "reason": f"DFM 调用失败：{text[:200]}",
                "issues": [], "failed_items": 0}
    if not isinstance(result, dict):
        return {"status": "not_ready", "reason": "DFM 没有返回结果",
                "issues": [], "failed_items": 0}

    failed = int(result.get("failedItems") or 0)
    issues = result.get("issues") or []
    warnings = [str(w) for w in (result.get("warnings") or [])]
    if failed > 0:
        # 文档说这种情况会抛 `INCOMPLETE_RESULT`；这里是防御性分支：
        # 万一某版本改成返回而不抛，**结论也不能变 ok**。
        return {"status": "unknown",
                "reason": f"DFM 有 {failed} 条规则检查失败 → 结果不完整",
                "issues": issues, "failed_items": failed, "warnings": warnings}
    configured = [w for w in warnings if "NOT_CONFIGURED" in w.upper()]
    if configured:
        return {"status": "not_ready",
                "reason": ("DFM 没有配置任何规则（" + "、".join(configured)
                           + "）—— 没查 ≠ 通过，去立创里配规则"),
                "issues": issues, "failed_items": failed, "warnings": warnings}
    return {"status": "ok",
            "reason": f"DFM 跑完，{len(issues)} 条问题",
            "issues": issues, "failed_items": 0, "warnings": warnings}


def merge(drc: dict[str, Any] | None, dfm: dict[str, Any] | None,
          geometry: dict[str, Any] | None = None) -> dict[str, Any]:
    """把三路证据合成一条"能不能打板"的制造结论。

    * **可信违规**（`clearance/netlist/physical` + 自建几何）> 0 → `failed`
    * **DFM 不是 ok**（不可用/不完整/没配置）→ `not_ready`
    * 都干净 → `ok`

    `connection` 单独计数并附一句说明：它**不进结论**，只提示。
    """
    drc = drc or {}
    dfm = dfm or {"status": "not_ready", "reason": "没有 DFM 结果"}
    geometry = geometry or {}
    categories = dict(drc.get("categories") or {})
    trustworthy = sum(int(categories.get(k) or 0) for k in TRUSTWORTHY_DRC)
    connection = int(categories.get("connection") or 0)
    if geometry:
        trustworthy += int(geometry.get("violations") or 0)

    reasons: list[str] = []
    if trustworthy > 0:
        status = "failed"
        reasons.append(f"可信违规 {trustworthy} 条（{'/'.join(TRUSTWORTHY_DRC)} 或自建几何）")
    elif dfm.get("status") != "ok":
        status = "not_ready"
        reasons.append(f"DFM {dfm.get('status')}：{dfm.get('reason')}")
    else:
        status = "ok"

    return {
        "status": status,
        "trustworthy_violations": trustworthy,
        "connection_violations": connection,
        "connection_note": (
            "Connection 类不进结论：实测它**不认 API 放的过孔**，"
            "连通性以 `verify` 的几何判定为准" if connection else ""),
        "drc_status": str(drc.get("status") or ""),
        "dfm": dfm,
        "geometry": geometry,
        "reasons": reasons,
        "note": ("制造结论：" + ("可打板" if status == "ok" else "不可打板")
                 + (f"；{reasons}" if reasons else "")),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.dfm",
        "dfm_method": "eda.pcb_Dfm.checkDfm(options?)",
        "dfm_availability": "eda.pcb_Dfm.getDfmAvailability()",
        "trustworthy_drc": list(TRUSTWORTHY_DRC),
        "untrustworthy_drc": ["connection"],
        "dfm_incomplete_error": DFM_INCOMPLETE,
        "discipline": ("DFM 不可用 → not_ready；抛 INCOMPLETE_RESULT → unknown；"
                       "两者都不是 ok。Connection 类只提示，不进结论。"),
    }