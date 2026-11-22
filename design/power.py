"""电源 / 地网的判据 —— **一处事实源**。

## 为什么要有这个模块

判据原本分在两个文件里：`emit_lceda._GROUND_HINTS` 与 `router._POWER_HINTS`
（emit 的 `_power_nets` 反过来借用 router 的私有函数 `_is_power`）。
两套表、一个私有依赖，加一种网名就要改两处 —— 迟早分叉（AGENTS §1.1/§3.15）。

P41 把表与判据收拢到这里，并且多了一条**优先级**：

```
design.power_nets 非空  → 按声明的判（source = "declared"）
design.power_nets 为空  → 退回按网名正则推断（source = "inferred"）
```

## 为什么要"声明优先"

正则能猜错的地方是具体的，不是理论上的：

* `PWR_EN` 会被当成电源网加宽（它是使能信号，20 mil 宽会挤掉旁边两根线）
* `AGND_REF` 会被当成地铺（它是一根基准点线，铺上去等于把基准和功率地短接）
* 反过来，`VBAT_SW` 这种开关后的电池网**不会**被认出来（原理图上它就是要宽）

所以 spec 里允许显式写 `power_nets`；没写时仍按正则走，但**必须把来源说出来**
（`source: inferred`），因为那是猜的。
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = ["POWER_HINTS", "GROUND_HINTS", "is_power", "is_ground", "classify"]

#: 电源类网络名判据（走线加宽 + 铺铜候选）。**保守**：只认这些前缀/整名。
POWER_HINTS = ("GND", "AGND", "PGND", "VCC", "VDD", "VBUS", "VBAT", "PWR",
               "+3V3", "3V3", "3.3V", "+5V", "5V", "5.0V", "+12V", "12V", "VIN",
               "VOUT", "VREF")

#: 地类网络名判据（铺铜对象）。**比 POWER_HINTS 窄**：只有真能当地铺的。
#:
#: 为什么窄：一个网被认成"地"，铺铜就会把整板铜都接到它上面。把 `VSSA`
#: 当地铺在模拟小板上通常是错的（它是模拟地，要和数字地单点连接）。
GROUND_HINTS = ("GND", "AGND", "PGND", "DGND", "VSS", "0V")


def is_power(net: str) -> bool:
    """像不像电源网（含地）。"""
    up = (net or "").upper()
    return any(up == h or up.startswith(h) or up.endswith(h) for h in POWER_HINTS)


def is_ground(net: str) -> bool:
    """像不像地网。"""
    up = (net or "").upper()
    return any(up == h or up.startswith(h) or up.endswith(h) for h in GROUND_HINTS)


def _dedup(names: Iterable[str]) -> list[str]:
    out: list[str] = []
    for name in names:
        text = str(name or "")
        if text and text not in out:
            out.append(text)
    return out


def classify(declared: Iterable[str] = (), nets: Iterable[str] = ()) -> dict[str, Any]:
    """把一份设计的电源/地网判出来。

    返回::

        {"power": [...], "ground": [...], "source": "declared" | "inferred",
         "notes": [...]}

    `declared` 是 `design.power_nets`（可能为空）；`nets` 是板上/网表里**实际
    存在**的全部网名（用来过滤、也用来给地网兜底）。

    两条不那么显然的规则：

    1. **声明里没有地**时，地网仍要从 `nets` 里按正则找 —— 否则"只声明了
       +3V3"的板会一块铜都不铺，而那几乎肯定不是用户的意图。这时
       `source` 记 `"declared+inferred-ground"`，把"地是猜的"说出来。
    2. 声明的网**不一定在 `nets` 里**（可能是旧声明）。不做静默丢弃：
       它仍进 `power`（加宽无害），但 `notes` 里点出来。
    """
    declared_list = _dedup(declared)
    nets_list = _dedup(nets)
    notes: list[str] = []

    if declared_list:
        power = list(declared_list)
        unknown = [n for n in power if nets_list and n not in nets_list]
        if unknown:
            notes.append(
                f"power_nets 里 {unknown} 不在板上（声明可能是旧的；仍按电源网加宽）")
        ground = [n for n in power if is_ground(n)]
        source = "declared"
        if not ground:
            fallback = [n for n in nets_list if is_ground(n)]
            if fallback:
                ground = fallback
                source = "declared+inferred-ground"
                notes.append(
                    f"power_nets 没有声明地网，按网名推成 {ground}"
                    "（要精确就把它写进 power_nets）")
        return {"power": power, "ground": ground, "source": source, "notes": notes}

    power = [n for n in nets_list if is_power(n)]
    ground = [n for n in power if is_ground(n)]
    return {
        "power": power,
        "ground": ground,
        "source": "inferred",
        "notes": ["power_nets 未声明：电源/地是按网名正则推断的（source=inferred）"],
    }