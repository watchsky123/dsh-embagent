"""总线级网表（A2）—— 一条链路不是一根线，是一组有规则的总线。

## 这一层补什么

`generate.to_design` 原来每条链路只连**一个引脚**（源码里自己写着"真实 SPI 要
SCK/MOSI/MISO/NSS 四根"）。于是生成的板子只有"功能示意"，没有拓扑：

| 之前 | 现在 |
|---|---|
| `SPI_1: U1.PA5 — U2.PA6` | `SPI1_SCK / SPI1_MOSI / SPI1_MISO / SPI1_NSS` 四条网 |
| 没有上拉/端接/串阻 | 每条网带 `class` 与 `rules`（I2C 上拉 4.7k、CAN 120Ω、USB 22Ω…）|
| 引脚随便挑 | 按 **AF 表**挑：`SPI1_SCK` 必须是那个脚真的能做的事 |

## 怎么挑脚（**不猜**）

1. 从能力表的 `pins` 里找同族外设组（`SPI1` / `USART1` / `I2C1`…）
2. 在该组的候选脚里，用 `afmap` 找**功能名**等于 `组_角色` 的脚
   （`SPI1_SCK`、`USART1_TX`…）
3. 找不到（没有 AF 表 / 该组没有这个角色）→ 返回 `None`，**由调用方退回
   单脚示意网并标 `bus_partial`**，不硬凑

引脚用完即占（同一颗芯片的两条链路不会抢同一个脚）。

## 收发关系（谁接谁）

`UART` 要**交叉**（A 的 TX 接 B 的 RX），`SPI`/`I2C`/`CAN`/`USB` **同名相接**。
写错会造出一条"TX 接 TX"的假网——台架才发现。
"""

from __future__ import annotations

from typing import Any

from . import afmap, capability

__all__ = ["BUSES", "expand", "describe", "net_count"]

#: 协议 → 角色、组前缀、网络类、规则。`cross` 是收发交叉表。
BUSES: dict[str, dict[str, Any]] = {
    "spi": {
        "prefix": ("SPI", "I2S"),
        "roles": [("sck", "SCK"), ("mosi", "MOSI"), ("miso", "MISO"), ("nss", "NSS")],
        "class": "spi", "cross": {},
        "rules": ["SCK 高速时串 22–33Ω", "NSS 上拉 10k（从设备）"],
    },
    "i2c": {
        "prefix": ("I2C",),
        "roles": [("scl", "SCL"), ("sda", "SDA")],
        "class": "i2c", "cross": {},
        "rules": ["SCL/SDA 上拉 4.7k", "总线电容 ≤400pF"],
    },
    "uart": {
        "prefix": ("USART", "UART"),
        "roles": [("tx", "TX"), ("rx", "RX")],
        "class": "uart", "cross": {"tx": "rx", "rx": "tx"},
        "rules": ["TX↔RX 交叉", "必须共地"],
    },
    "can": {
        "prefix": ("CAN", "FDCAN"),
        "roles": [("canh", "CANH"), ("canl", "CANL")],
        "class": "can", "cross": {},
        "rules": ["两端各 120Ω 终端", "差分 90Ω"],
    },
    "usb": {
        "prefix": ("USB",),
        "roles": [("dp", "DP"), ("dm", "DM")],
        "class": "usb", "cross": {},
        "rules": ["D+/D- 串 22Ω", "ESD 保护"],
    },
    "power": {
        "prefix": (),
        "roles": [("v", "V"), ("gnd", "GND")],
        "class": "power", "cross": {},
        "rules": ["每轨去耦 100nF+10µF", "反接/过流保护"],
    },
}


def _group(cap: dict[str, Any], protocol: str) -> str:
    """能力表里的外设组名（`SPI1` / `USART1`…）。**找不到返回空串**。"""
    prefixes = BUSES[protocol]["prefix"]
    for key in sorted(cap.get("pins") or {}):
        upper = key.upper()
        if any(upper.startswith(p) for p in prefixes):
            return key
    return ""


def _pin_for(table: dict[str, Any] | None, group: str, suffix: str,
             used: set[str]) -> str:
    """在 AF 表里找**真的能做** `group_suffix` 的脚。**找不到返回空串**。"""
    if not table:
        return ""
    want = f"{group}_{suffix}".upper()
    for port, info in sorted((table.get("pins") or {}).items()):
        if port in used:
            continue
        for row in info.get("functions") or []:
            if str(row.get("signal") or "").upper() == want:
                return port
    return ""


def expand(protocol: str, ref_a: str, ref_b: str, *, model_a: str = "",
           model_b: str = "", cap_a: dict[str, Any] | None = None,
           cap_b: dict[str, Any] | None = None,
           used_a: set[str] | None = None, used_b: set[str] | None = None,
           taken_names: set[str] | None = None) -> dict[str, Any] | None:
    """一条链路 → 一组总线网。**做不到就返回 None**（调用方退回单脚示意网）。

    返回 `{"nets": [...], "group": "SPI1", "missing": [角色...]}`。
    """
    key = str(protocol or "").lower()
    cfg = BUSES.get(key)
    if cfg is None:
        return None
    used_a = used_a if used_a is not None else set()
    used_b = used_b if used_b is not None else set()
    taken = taken_names if taken_names is not None else set()
    group = _group(cap_a or {}, key) or _group(cap_b or {}, key)
    if not group:
        return None
    table_a = afmap.find(model_a)
    table_b = afmap.find(model_b)
    if table_a is None and table_b is None:
        return None
    nets: list[dict[str, Any]] = []
    missing: list[str] = []
    for role, suffix in cfg["roles"]:
        peer = (cfg.get("cross") or {}).get(role, role)
        pin_a = _pin_for(table_a, group, suffix, used_a)
        pin_b = _pin_for(table_b, group, peer, used_b)
        if not pin_a or not pin_b or pin_a in used_a or pin_b in used_b:
            missing.append(role)
            continue
        name = f"{group}_{suffix}"
        if name in taken:                      # 同名总线：第二条加序号，避免假并联
            index = 2
            while f"{name}_{index}" in taken:
                index += 1
            name = f"{name}_{index}"
        taken.add(name)
        used_a.add(pin_a)
        used_b.add(pin_b)
        nets.append({"net": name, "class": cfg["class"], "rules": list(cfg["rules"]),
                     "level": "bus", "source": "derived",
                     "pins": [{"ref": ref_a, "pin": pin_a},
                              {"ref": ref_b, "pin": pin_b}]})
    if len(nets) < 2:
        # 一个角色都没凑出来（或只凑出一个）→ 不把它当总线，退回示意网
        return None
    return {"nets": nets, "group": group, "missing": missing,
            "note": (f"{group} 总线 {len(nets)}/{len(cfg['roles'])} 条"
                     + (f"，缺 {missing}" if missing else ""))}


def net_count(protocol: str) -> int:
    """这条总线**应该**有几根线（按角色数）。"""
    cfg = BUSES.get(str(protocol or "").lower())
    return len(cfg["roles"]) if cfg else 0


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.bus",
        "buses": {key: [role for _, role in cfg["roles"]] for key, cfg in BUSES.items()},
        "pin_source": "AF 表（afmap）：脚的功能名必须等于 组_角色",
        "cross": {key: cfg.get("cross") for key, cfg in BUSES.items() if cfg.get("cross")},
        "fallback": "凑不出 ≥2 条 → 返回 None，调用方退回单脚示意网并标 bus_partial",
        "discipline": ("不硬凑引脚；没有 AF 表就退回示意，且**标出来**；"
                       "同名总线加序号，不制造假并联"),
    }