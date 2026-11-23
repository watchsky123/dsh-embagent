"""引脚分配器（B5）—— 按 AF 表把一组功能分到具体引脚。

## 这一层补什么

`generate` 挑脚是"找第一个能用的"；`bus` 只处理两条链路之间。真项目要
**同时**满足多组外设：USART1 + I2C1 + SPI1 一起要 8 个脚，且不能抢、
不能落在 `avoid` 里。这是一个带约束的分配问题。

算法**贪心 + 少量回溯**（不做精确求解，可行解比最优解重要）：
1. 每个功能列出候选脚（AF 表里功能名完全等于 `组_角色` 的脚）
2. 按候选数从少到多排序（先分最挑的）
3. 依次取"不在 used / avoid 里"的第一个（prefer 列表优先）
4. 某功能没有候选 → 记 `unassigned` 与原因；**不硬塞**

## 纪律

* 候选来自 AF 表（这个脚真的能做这个功能），不是按脚位顺序猜
* 分配结果**不重复用脚**；冲突就换组（`USART1` → `USART2`）
* 分不出来就是 `unassigned`（带候选数与原因），不是"随便给一个"
"""

from __future__ import annotations

from typing import Any

from . import afmap

__all__ = ["PROTOCOLS", "assign", "assign_protocol", "derive_needs", "describe"]

#: 协议 → (组前缀, [角色...])。角色名就是功能名的后缀（`USART1_TX` 的 `TX`）。
PROTOCOLS: dict[str, tuple[str, list[str]]] = {
    "i2c": ("I2C", ["SCL", "SDA"]),
    "uart": ("USART", ["TX", "RX"]),
    "spi": ("SPI", ["SCK", "MOSI", "MISO", "NSS"]),
    "can": ("CAN", ["TX", "RX"]),
}

#: 组号尝试上限（`USART1`/`USART2`/`USART3`）。
_MAX_GROUP = 4


def _signal_map(model: str) -> dict[str, list[str]]:
    table = afmap.find(model) or {}
    out: dict[str, list[str]] = {}
    for pin, info in (table.get("pins") or {}).items():
        for row in info.get("functions") or []:
            signal = str(row.get("signal") or "").upper()
            if signal:
                out.setdefault(signal, []).append(pin)
    for pins in out.values():
        pins.sort()
    return out


def assign(model: str, needs: list[str], *, avoid: set[str] | None = None,
           prefer: list[str] | None = None,
           used: set[str] | None = None) -> dict[str, Any]:
    """一组功能名（如 `USART1_TX`）→ 引脚。**按候选数排序，不重复用脚**。"""
    signals = _signal_map(model)
    if not signals:
        return {"status": "not_ready", "reason": f"AF 表里没有型号 {model}",
                "assignment": {}, "unassigned": [{"need": n, "reason": "没有 AF 表"}
                                                 for n in needs]}
    avoid = avoid or set()
    used = used or set()
    prefer = prefer or []
    assignment: dict[str, str] = {}
    unassigned: list[dict[str, Any]] = []
    ordered = sorted(needs, key=lambda name: (len(signals.get(str(name).upper(), [])),
                                              str(name)))
    for need in ordered:
        key = str(need).upper()
        candidates = [pin for pin in signals.get(key, [])
                      if pin not in avoid and pin not in used]
        if not candidates:
            reason = ("没有这个功能" if not signals.get(key)
                      else "所有候选脚都被 avoid/used 占用")
            unassigned.append({"need": need, "reason": reason,
                               "candidates": list(signals.get(key, []))})
            continue
        chosen = next((pin for pin in prefer if pin in candidates), candidates[0])
        assignment[need] = chosen
        used.add(chosen)
    status = ("ok" if not unassigned
              else "partial" if assignment else "not_ready")
    return {"status": status, "assignment": assignment, "unassigned": unassigned,
            "used": sorted(used),
            "checked": {"needs": len(needs), "assigned": len(assignment),
                        "unassigned": len(unassigned)}}


def assign_protocol(model: str, protocol: str, *,
                    avoid: set[str] | None = None,
                    prefer: list[str] | None = None,
                    used: set[str] | None = None) -> dict[str, Any]:
    """一个协议 → **同一组**内的全部角色（`USART1_TX` + `USART1_RX`）。

    组内拼不出来就换下一个组（`USART2`…）；换完还不行就 `unassigned`。
    """
    entry = PROTOCOLS.get(str(protocol).lower())
    if entry is None:
        return {"status": "not_ready", "protocol": protocol,
                "reason": "不认识的协议（支持 i2c/uart/spi/can）"}
    prefix, roles = entry
    used = used if used is not None else set()
    last: dict[str, Any] | None = None
    for index in range(1, _MAX_GROUP + 1):
        group = f"{prefix}{index}"
        needs = [f"{group}_{role}" for role in roles]
        got = assign(model, needs, avoid=avoid, prefer=prefer, used=set(used))
        if got["status"] == "ok":
            return {"status": "ok", "protocol": protocol, "group": group,
                    "assignment": got["assignment"], "used": got["used"],
                    "unassigned": []}
        last = got
    return {"status": "not_ready" if last is None else last["status"],
            "protocol": protocol, "group": "",
            "assignment": (last or {}).get("assignment") or {},
            "unassigned": (last or {}).get("unassigned") or [],
            "reason": f"{prefix}1..{prefix}{_MAX_GROUP} 都分不出来"}


def derive_needs(spec: dict[str, Any] | None) -> list[str]:
    """spec 里的接口 → 协议列表（去重，保留出现顺序）。"""
    out: list[str] = []
    for contract in (spec or {}).get("interfaces") or []:
        protocol = str(contract.get("protocol") or "").lower()
        if protocol in PROTOCOLS and protocol not in out:
            out.append(protocol)
    return out


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.pin_assign",
        "protocols": {key: roles for key, (_, roles) in PROTOCOLS.items()},
        "algorithm": "候选=AF 表功能名；按候选数升序贪心；冲突换组",
        "status": "ok / partial / not_ready（没有 AF 表）",
        "discipline": "不重复用脚；分不出来进 unassigned，不硬塞",
    }