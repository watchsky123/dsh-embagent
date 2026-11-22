"""软硬接口契约（`interface`）—— 让固件和硬件**共用同一份引脚事实**。

## 这一层解决什么问题

在这之前，"软件"和"硬件"只有一条线连着：`fw-gen pins` 吐一个 `pins.h`
（引脚名）。除此之外全是**口头约定**：

| 固件需要的 | 原来从哪来 |
|---|---|
| 这个脚是哪路 SPI 的第几根线 | 人记 |
| 波特率 / 时钟 / 中断 | 人记，代码里写死 |
| 复用功能（这个脚能不能做 I2C）| 人查 datasheet |
| 哪些脚是电源/地（不能当 GPIO 用）| 人记 |

结果就是：**板子改了、固件不知道**。`fw-matrix` 只能事后发现"用了不存在的脚"，
发现不了"SPI 换了一组脚"。

`interface.json` 是那份**契约**：从 `design.json`（引脚 + 网表）+ `.ioc`
（外设配置）合成一份，固件侧读它、检查器也读它。

## 三态纪律

| 项 | 能判 | 不能判 |
|---|---|---|
| 引脚 ↔ 网络 | ✅ 逐引脚对（**唯一硬判据**）| — |
| 外设分组（SPI/I2C/UART…）| 有 `.ioc` 时按 `.ioc`；没有就按网名族**推断**（标 `inferred`）| 复用功能表（没录）|
| 时钟 / 中断 / 时序 | — | **一律 `unknown` + 写明缺什么**（不猜）|
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import power as power_mod
from .model import DesignModel

__all__ = ["build", "render", "describe", "FILE"]

FILE = "interface.json"

#: 网名 → 协议族（**按词元**，不是子串）。和 `links.PROTOCOL_FAMILIES` 同一套
#: 思路：`RF_RXEN` 不该被判成 UART。这里只做**接口契约**用，不参与判决。
_NET_FAMILY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("usb", ("usb_dp", "usb_dm", "usbdp", "usbdm", "dp1", "dn1")),
    ("spi", ("spi", "sck", "mosi", "miso", "nss", "flash_cs")),
    ("i2c", ("scl", "sda", "i2c")),
    ("uart", ("uart", "usart", "txd", "rxd", "u1_tx", "u1_rx",
              "u3_tx", "u3_rx")),
    ("can", ("canh", "canl", "can")),
)

#: 这些词元的网**不是**接口（是使能/中断/状态脚）。命中就当 GPIO。
_NOT_INTERFACE = ("en", "en_", "int", "irq", "nrst", "reset", "busy", "dio",
                  "rxen", "detect", "rdy", "cs_", "pwr", "event", "armed",
                  "1pps", "pps")


def _tokens(name: str) -> list[str]:
    out: list[str] = []
    cur = ""
    for ch in str(name or ""):
        if ch.isalnum():
            cur += ch.lower()
        elif cur:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def family_of(net: str) -> str | None:
    """网名 → 协议族。**判不出返回 None**（绝不猜）。"""
    tokens = _tokens(net)
    if not tokens:
        return None
    joined = "_".join(tokens)
    for bad in _NOT_INTERFACE:
        if any(t == bad or t.startswith(bad) and bad.endswith("_") for t in tokens):
            return None
    for family, words in _NET_FAMILY:
        if any(word in joined or word == joined for word in words):
            return family
    return None


def _direction(entry: dict[str, Any], family: str | None) -> str:
    """方向。**只能从"它在不在某条接口里"推**，推不出就 `unknown`。"""
    if family is None:
        return "unknown"
    return "io"


def build(design: DesignModel, *, ioc_path: str | Path | None = None) -> dict[str, Any]:
    """合成契约。**唯一硬判据是"引脚 ↔ 网络"逐条一致。**"""
    nets = [e for e in design.nets if isinstance(e, dict)]
    parts = {p.ref: p for p in design.parts}
    declared_power = (design.power_nets
                      if isinstance(design.power_nets, dict) else {})
    classified = power_mod.classify(
        declared_power, [str(e.get("net") or "") for e in nets])
    power_nets = list(classified.get("power") or [])
    ground_nets = list(classified.get("ground") or [])

    # 1) 引脚 → 网络（design.json 是唯一事实源）
    pin_net: dict[tuple[str, str], str] = {}
    for entry in nets:
        net = str(entry.get("net") or "").strip()
        if not net:
            continue
        for pin in entry.get("pins") or []:
            if not isinstance(pin, dict):
                continue
            ref = str(pin.get("ref") or "").strip()
            name = str(pin.get("pin") or "").strip()
            if ref and name:
                pin_net[(ref, name)] = net

    # 2) 外设分组：按 MCU 的引脚 + 协议族聚
    mcus = [p for p in design.parts
            if str(getattr(p, "kind", "")).lower().startswith(
                ("stm32", "atmega", "esp32", "avr", "mcu"))
            or str(getattr(p, "role", "")) == "mcu"]
    mcu = mcus[0] if mcus else None
    peripherals: list[dict[str, Any]] = []
    if mcu is not None:
        groups: dict[str, list[dict[str, Any]]] = {}
        for (ref, name), net in sorted(pin_net.items()):
            if ref != mcu.ref:
                continue
            family = family_of(net)
            if family is None:
                continue
            groups.setdefault(family, []).append({
                "pin": name, "net": net,
                "role": _signal_role(net, family),
            })
        for family, pins in sorted(groups.items()):
            peripherals.append({
                "family": family,
                "pins": pins,
                "source": "inferred" if not ioc_path else "ioc",
                "note": ("从网名族推断（没有 .ioc）" if not ioc_path
                         else "按 .ioc 的外设配置"),
            })

    # 3) 逐引脚契约
    pins: list[dict[str, Any]] = []
    for part in design.parts:
        if mcu is not None and part.ref != mcu.ref:
            continue
        for name in (part.pins or []):
            net = pin_net.get((part.ref, str(name)))
            family = family_of(net) if net else None
            is_power = bool(net) and net in power_nets
            is_ground = bool(net) and net in ground_nets
            pins.append({
                "ref": part.ref, "pin": str(name), "net": net or "",
                "kind": ("ground" if is_ground else "power" if is_power
                         else "signal" if net else "unconnected"),
                "family": family,
                "direction": ("-" if (is_power or is_ground)
                              else _direction({}, family)),
                "usable_as_gpio": (not net) or (
                    not is_power and not is_ground and family is None),
                "source": "design",
            })

    # 4) .ioc 有就读（外设/复用是"声明的"，比推断可信）
    ioc_info: dict[str, Any] = {"present": False}
    if ioc_path:
        try:
            from .fwgen import pinmap as pm  # noqa: PLC0415

            parsed = pm.parse_pins(str(ioc_path))
            ioc_info = {
                "present": True, "path": str(ioc_path),
                "pins": {k: v.to_dict() for k, v in parsed.items()},
                "count": len(parsed),
            }
        except Exception as exc:  # noqa: BLE001
            ioc_info = {"present": False,
                        "error": f"{type(exc).__name__}: {exc}"}

    # 5) 一致性：**唯一硬判据**
    problems: list[dict[str, Any]] = []
    declared = set()
    for part in design.parts:
        for name in (part.pins or []):
            declared.add((part.ref, str(name)))
    for (ref, name) in pin_net:
        if ref not in parts:
            problems.append({"check": "iface.pin_dangling", "refs": [ref],
                             "message": f"网表里 {ref}.{name} 指向不存在的元件"})
        elif (ref, name) not in declared:
            problems.append({"check": "iface.pin_missing", "refs": [ref],
                             "message": f"网表里用了 {ref}.{name}，但元件没有这个脚"})
    # 未接的 MCU 脚是正常的 —— 不报问题，只在 `pins` 里记 kind=unconnected
    status = "failed" if problems else (
        "ok" if any(p["kind"] in ("power", "signal") for p in pins) else "not_ready")

    counts = {
        "pins": len(pins),
        "connected": sum(1 for p in pins if p["net"]),
        "unconnected": sum(1 for p in pins if not p["net"]),
        "power": sum(1 for p in pins if p["kind"] == "power"),
        "ground": sum(1 for p in pins if p["kind"] == "ground"),
        "peripherals": len(peripherals),
    }
    return {
        "status": status,
        "mcu": ({"ref": mcu.ref, "kind": str(getattr(mcu, "kind", "")),
                 "pins": len(mcu.pins or [])} if mcu is not None else {}),
        "pins": pins,
        "peripherals": peripherals,
        "power_nets": sorted(power_nets),
        "ground_nets": sorted(ground_nets),
        "power_source": "declared" if (design.power_nets or {}) else "inferred",
        "ioc": ioc_info,
        "problems": problems,
        "counts": counts,
        # **缺的东西一律 unknown + 写明缺什么**，不猜
        # **缺的东西一律 unknown + 写明缺什么**，不猜（AGENTS §0.1）
        "unknown": [
            {"what": "时钟树（HCLK/PCLK/外设时钟）",
             "need": "MCU 型号对应的时钟配置或 .ioc"},
            {"what": "中断向量与优先级",
             "need": ".ioc 的 NVIC 配置或固件里的 IRQ handler 表"},
            {"what": "GPIO 复用功能表（AF map）",
             "need": "datasheet 的 AF 表（本仓库未录）"},
            {"what": "时序约束（建立/保持、采样时刻）",
             "need": "器件 datasheet 的时序章节"},
        ],
        "note": ("唯一硬判据 = 引脚↔网络逐条一致；外设分组在没有 .ioc 时是"
                 "**推断**（source=inferred）；时钟/中断/AF/时序一律 unknown，不猜"),
    }


def _signal_role(net: str, family: str) -> str:
    tokens = _tokens(net)
    if family == "spi":
        for key, role in (("sck", "clock"), ("mosi", "data_out"),
                          ("miso", "data_in"), ("nss", "chip_select"),
                          ("spi", "unknown")):
            if any(key in t for t in tokens):
                return role
        return "unknown"
    if family == "i2c":
        if any("scl" in t for t in tokens):
            return "clock"
        if any("sda" in t for t in tokens):
            return "data"
        return "unknown"
    if family == "uart":
        if any(t in ("tx", "txd") or t.endswith("tx") for t in tokens):
            return "data_out"
        if any(t in ("rx", "rxd") or t.endswith("rx") for t in tokens):
            return "data_in"
        return "unknown"
    if family == "usb":
        return "data"
    return "unknown"


def render(payload: dict[str, Any]) -> str:
    """人读版（`interface.md`）：引脚表 + 外设 + 缺口。"""
    counts = payload.get("counts") or {}
    mcu = payload.get("mcu") or {}
    lines = [f"# 软硬接口契约（{mcu.get('ref', '?')} {mcu.get('kind', '')}）", "",
             f"> {payload.get('note', '')}", "",
             f"引脚 {counts.get('pins')}（连通 {counts.get('connected')} / "
             f"未接 {counts.get('unconnected')}），电源 {counts.get('power')}、"
             f"地 {counts.get('ground')}，外设组 {counts.get('peripherals')}", "",
             "## 外设", ""]
    if payload.get("peripherals"):
        for group in payload["peripherals"]:
            pins = ", ".join(f"{p['pin']}={p['net']}" for p in group["pins"])
            lines.append(f"* **{group['family']}**（{group['source']}）：{pins}")
    else:
        lines.append("_无（没有 .ioc，也没有可识别的接口网名）_")
    lines += ["", "## 引脚", "", "| 脚 | 网络 | 类别 | 方向 | 可否当 GPIO |", "|---|---|---|---|---|"]
    for row in payload.get("pins") or []:
        lines.append(f"| {row['pin']} | {row['net'] or '—'} | {row['kind']} | "
                     f"{row['direction']} | {'是' if row['usable_as_gpio'] else '否'} |")
    lines += ["", "## 还不知道的（**unknown，不是通过**）", ""]
    for item in payload.get("unknown") or []:
        lines.append(f"* {item.get('what')} —— 需要：{item.get('need')}")
    if payload.get("problems"):
        lines += ["", "## 不一致", ""]
        for row in payload["problems"]:
            lines.append(f"* {row.get('check')}：{row.get('message')}")
    return "\n".join(lines) + "\n"


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.interface",
        "file": FILE,
        "hard_checks": ["iface.pin_missing", "iface.pin_dangling"],
        "inferred": ["peripherals（无 .ioc 时按网名族推断）", "power_nets（未声明时）"],
        "always_unknown": ["时钟树", "中断/NVIC", "GPIO 复用 AF 表", "时序约束"],
        "discipline": ("引脚↔网络逐条一致是唯一硬判据；"
                       "外设分组无 .ioc 时标 inferred；"
                       "时钟/中断/AF/时序一律 unknown 并写明缺什么"),
    }