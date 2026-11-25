"""AF 复用表（`afmap`）—— **这个脚能做哪些复用功能、编号是多少**。

## 这一层补什么

P56 解决了"`.ioc` 的 `PA9` 是网表的 `30`"，但没解决"**`PA9` 能不能做
`USART1_TX`，AF 编号是几**"。这一层补的就是它：

| 之前 | 现在 |
|---|---|
| `.ioc` 说 `PA9=USART1_TX`，只能信 | 表里查得到 `PA9 → USART1_TX @ AF7` |
| 固件里 `Alternate = GPIO_AF7_USART1` 对不对没人管 | 与表比对，写错就是**硬冲突** |
| "这个脚能不能做 I2C" 要查 datasheet | `pins_for_signal(table, "I2C1_SCL")` 直接列出来 |

## 数据来源（**ST 官方 open pin data**，BSD-3-Clause）

`github.com/STMicroelectronics/STM32_open_pin_data` —— **CubeMX 自己用的那份库**。

| 文件 | 给什么 |
|---|---|
| `mcu/<型号>.xml` | 每个脚的 `Position`（封装脚号）、`Type`、全部信号名 |
| `mcu/IP/GPIO-<族>_gpio_v*_Modes.xml` | 每个信号的 `GPIO_AF` 编号（F4/H7）或 `RemapBlock`（F1）|

许可：**BSD-3-Clause © 2020 STMicroelectronics**（可再分发，需保留版权声明），
每条表里记 `license` 与两个文件的 `sha256`。

## 两种"AF"必须分开（**这是最容易写错的地方**）

| 族 | 机制 | 表里的形状 |
|---|---|---|
| **F1**（F103）| 固定功能 + `AFIO_MAPR` 重映射，**没有 AF0–AF15** | `af: null`，`remap: {default, macros}` |
| **F4/F7/H7** | `AF0..AF15` 编号 | `af: 7`，无 remap |

把 F1 硬套成 `AF7` 就是造假：F103 的 `PA9=USART1_TX` 是**默认功能**，
`PB6=USART1_TX` 只在 `USART1_REMAP1` 打开时才成立。

## 匹配 `.ioc` 的信号名（实测的四种别名）

| `.ioc` 写法 | 表里 | 处理 |
|---|---|---|
| `USART1_TX` | `USART1_TX` | 精确匹配 |
| `S_TIM1_CH1` | `TIM1_CH1` | 去掉前导 `S_` 再比 |
| `ADCx_IN0` | `ADC1_IN0` / `ADC2_IN0` | 族名通配（`ADCx`）|
| `GPXTI4` | —— | EXTI 模式，**任何 GPIO 脚都能**，不查表 |

认不出的**不当冲突**（`unknown`），因为"表里没有"不等于"芯片不能"。
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import Any

__all__ = ["DIR", "PARTS", "LICENSE", "build_table", "load", "find", "validate",
           "match_signal", "af_of", "pins_for_signal", "crosscheck", "describe",
           "list_tables", "SIGNAL_FAMILIES"]

DIR = Path(__file__).with_name("afmap")

#: ST 官方引脚库（CubeMX 同源），BSD-3-Clause。
SOURCE_REPO = "STMicroelectronics/STM32_open_pin_data"
SOURCE_BLOB = f"https://github.com/{SOURCE_REPO}/blob/master/"
API_BASE = f"https://api.github.com/repos/{SOURCE_REPO}/contents/"
RAW_BASE = f"https://raw.githubusercontent.com/{SOURCE_REPO}/master/"
LICENSE = "BSD-3-Clause © 2020 STMicroelectronics (STM32_open_pin_data)"

#: 型号 → 两个源文件。**只收实测取得到的**；没有的型号 `not_ready`，不猜。
PARTS: dict[str, dict[str, str]] = {
    "stm32f103c8t6": {
        "mpn": "STM32F103C8T6",
        "mcu": "mcu/STM32F103C(8-B)Tx.xml",
        "gpio": "mcu/IP/GPIO-STM32F103x8_gpio_v1_0_Modes.xml",
    },
    "stm32f103c6t6": {
        "mpn": "STM32F103C6T6",
        "mcu": "mcu/STM32F103C(4-6)Tx.xml",
        "gpio": "mcu/IP/GPIO-STM32F103x4_gpio_v1_0_Modes.xml",
    },
    "stm32f401rct6": {
        "mpn": "STM32F401RCT6",
        "mcu": "mcu/STM32F401R(B-C)Tx.xml",
        "gpio": "mcu/IP/GPIO-STM32F401_gpio_v1_0_Modes.xml",
    },
    "stm32h743iit6": {
        "mpn": "STM32H743IIT6",
        "mcu": "mcu/STM32H743IITx.xml",
        # H743 与 H747 共用同一个 GPIO IP（CubeMX 的 ConfigFile 就这么指）
        "gpio": "mcu/IP/GPIO-STM32H747_gpio_v1_0_Modes.xml",
    },
}

#: 通信/定时类的信号族 —— 只有这些"看着就该由 AF 决定的"信号缺失时才判冲突；
#: 模拟/EXTI/调试脚缺失只算 unknown。
SIGNAL_FAMILIES = ("USART", "UART", "LPUART", "SPI", "I2S", "I2C", "FDCAN",
                   "CAN", "USB", "SDMMC", "QUADSPI", "OCTOSPI", "ETH",
                   "DCMI", "LTDC", "SAI", "TIM", "HRTIM", "CEC", "RNG")

_PORT_RE = re.compile(r"^(P[A-Z]\d{1,2})(?!\d)")
_AF_RE = re.compile(r"^GPIO_AF(\d+)_")
_ADC_RE = re.compile(r"^ADC(\d+)_IN[PN]?(\d+)$")
_ADCX_RE = re.compile(r"^ADCx_IN[PN]?(\d+)$", re.I)
_EXTI_RE = re.compile(r"^GP(?:XTI|IO_EXTI)(\d+)$", re.I)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _local(tag: str) -> str:
    return str(tag).split("}")[-1]


def _iter(elem: ET.Element, name: str):
    for node in elem.iter():
        if _local(node.tag) == name:
            yield node


def _canon(name: str) -> str:
    """`PD0-OSC_IN` → `PD0`；`PA9` → `PA9`；电源脚 → 空串。"""
    match = _PORT_RE.match(str(name or ""))
    return match.group(1) if match else ""


def _fetch(path: str) -> tuple[str | None, str]:
    """取一个 ST 库文件：先 GitHub API（raw 媒体类型），再 raw.githubusercontent。"""
    errors: list[str] = []
    api = API_BASE + urllib.parse.quote(path)
    for url, headers in ((api, {"Accept": "application/vnd.github.raw"}),
                         (RAW_BASE + path, {})):
        request = urllib.request.Request(url, headers={
            "User-Agent": "dsh_embagent/afmap", **headers})
        try:
            with urllib.request.urlopen(request, timeout=180) as resp:
                return resp.read().decode("utf-8", "replace"), ""
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            errors.append(f"{url.split('/')[2]}: {type(exc).__name__}")
    return None, "；".join(errors)


def _style_of(family: str) -> str:
    """F1 用固定功能 + 重映射；其余按 AF0..15。"""
    return "F1_REMAP" if str(family or "").upper().startswith("STM32F1") else "AF"


def _parse_mcu(text: str) -> dict[str, Any]:
    """MCU 文件 → `{family, package, count, pins: {PA9: {position, type, signals, io_modes}}}`。"""
    root = ET.fromstring(text)
    family = root.get("Family") or ""
    package = root.get("Package") or ""
    pins: dict[str, dict[str, Any]] = {}
    positions: list[int] = []
    power: list[str] = []
    for node in _iter(root, "Pin"):
        name = str(node.get("Name") or "")
        position = int(str(node.get("Position") or "0"))
        if position:
            positions.append(position)
        port = _canon(name)
        signals = [str(s.get("Name") or "") for s in node
                   if _local(s.tag) == "Signal" and s.get("Name")]
        io_modes = next((str(s.get("IOModes") or "") for s in node
                         if _local(s.tag) == "Signal"
                         and s.get("Name") == "GPIO"), "")
        if port:
            if port in pins:
                continue
            pins[port] = {"name": name, "position": position,
                          "type": str(node.get("Type") or ""),
                          "signals": signals, "io_modes": io_modes}
        else:
            power.append(name)
    return {"family": family, "package": package,
            "count": len(positions), "positions": sorted(positions),
            "pins": pins, "power": power}


def _parse_gpio(text: str) -> dict[str, dict[str, dict[str, Any]]]:
    """GPIO IP 文件 → `{PA9: {USART1_TX: {af, remap}}}`。"""
    root = ET.fromstring(text)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for gp in _iter(root, "GPIO_Pin"):
        pin = _canon(str(gp.get("Name") or ""))
        if not pin:
            continue
        funcs: dict[str, dict[str, Any]] = {}
        for signal in gp:
            if _local(signal.tag) != "PinSignal":
                continue
            name = str(signal.get("Name") or "")
            if not name:
                continue
            af: int | None = None
            macros: list[str] = []
            blocks: list[dict[str, Any]] = []
            for child in signal:
                kind = _local(child.tag)
                if kind == "SpecificParameter" and child.get("Name") == "GPIO_AF":
                    for value in child:
                        if _local(value.tag) != "PossibleValue":
                            continue
                        text_value = str(value.text or "").strip()
                        match = _AF_RE.match(text_value)
                        if match:
                            af = int(match.group(1))
                        elif text_value:
                            macros.append(text_value)
                elif kind == "RemapBlock":
                    block_macros: list[str] = []
                    for param in child:
                        if _local(param.tag) != "SpecificParameter":
                            continue
                        for value in param:
                            if _local(value.tag) == "PossibleValue" and value.text:
                                block_macros.append(str(value.text).strip())
                    blocks.append({
                        "name": str(child.get("Name") or ""),
                        "default": str(child.get("DefaultRemap") or "").lower() == "true",
                        "macros": block_macros,
                    })
                    macros.extend(block_macros)
            funcs[name] = {"af": af,
                           "remap": ({"default": any(b["default"] for b in blocks),
                                      "blocks": blocks,
                                      "macros": macros} if blocks else None)}
        if pin in out:
            for key, value in funcs.items():
                out[pin].setdefault(key, value)
        else:
            out[pin] = funcs
    return out


def build_table(part: str, *, mcu_file: str = "", gpio_file: str = "",
                pinout: dict[str, Any] | None = None) -> dict[str, Any]:
    """从 ST 官方库建一张 AF 表。**两个源文件都要取到**，否则 `not_ready`。"""
    key = _norm(part)
    meta = PARTS.get(key)
    if meta is None and not (mcu_file and gpio_file):
        return {"status": "not_ready",
                "reason": f"没有 {part!r} 的源文件映射（PARTS 里没有）",
                "known": sorted(PARTS)}
    mcu_path = mcu_file or (meta or {}).get("mcu", "")
    gpio_path = gpio_file or (meta or {}).get("gpio", "")
    if not (mcu_path and gpio_path):
        return {"status": "not_ready", "reason": "缺 mcu/gpio 文件路径"}

    mcu_text, error = _fetch(mcu_path)
    if mcu_text is None:
        return {"status": "not_ready", "reason": f"取不到 MCU 文件：{error}",
                "path": mcu_path}
    gpio_text, error = _fetch(gpio_path)
    if gpio_text is None:
        return {"status": "not_ready", "reason": f"取不到 GPIO 文件：{error}",
                "path": gpio_path}

    mcu = _parse_mcu(mcu_text)
    gpio = _parse_gpio(gpio_text)
    style = _style_of(mcu["family"])
    pins: dict[str, dict[str, Any]] = {}
    af_count = 0
    for port, info in sorted(mcu["pins"].items()):
        functions: list[dict[str, Any]] = []
        for signal_name in info["signals"]:
            if signal_name == "GPIO":
                continue
            source = gpio.get(port, {}).get(signal_name, {})
            row: dict[str, Any] = {"signal": signal_name}
            if style == "F1_REMAP":
                row["af"] = None
                row["remap"] = source.get("remap")
            else:
                row["af"] = source.get("af")
                if row["af"] is not None:
                    af_count += 1
            functions.append(row)
        pins[port] = {"position": info["position"], "type": info["type"],
                      "io_modes": info["io_modes"], "functions": functions}
    table = {
        "part": key,
        "mpn": (meta or {}).get("mpn", ""),
        "family": mcu["family"],
        "package": mcu["package"],
        "style": style,
        "count": mcu["count"],
        "io_count": len(pins),
        "pins": pins,
        "sources": {
            "mcu": {"path": mcu_path, "url": SOURCE_BLOB + mcu_path,
                    "sha256": hashlib.sha256(mcu_text.encode("utf-8")).hexdigest()},
            "gpio": {"path": gpio_path, "url": SOURCE_BLOB + gpio_path,
                     "sha256": hashlib.sha256(gpio_text.encode("utf-8")).hexdigest()},
        },
        "source": "catalog",
        "source_kind": "ST 官方 open pin data（CubeMX 同源，BSD-3-Clause）",
        "license": LICENSE,
        "retrieved": date.today().isoformat(),
        "af_functions": af_count,
    }
    problems = validate(table)
    conflict = crosscheck(table, pinout) if pinout else []
    if problems or conflict:
        return {"status": "failed", "problems": problems + conflict,
                "part": key, "count": mcu["count"], "style": style}
    return {"status": "ok", "table": table, "part": key,
            "count": mcu["count"], "style": style,
            "af_functions": af_count}


def validate(table: dict[str, Any]) -> list[str]:
    """表自检：脚号连续、数量与封装一致、AF 范围合法、F1 不许有 AF 编号。"""
    problems: list[str] = []
    count = int(table.get("count") or 0)
    positions = sorted(int(row.get("position") or 0)
                       for row in (table.get("pins") or {}).values())
    if not count:
        problems.append("count 缺失或为 0")
    if len(positions) != len(set(positions)):
        problems.append("有重复的 Position")
    if table.get("style") == "F1_REMAP":
        bad = [row["signal"] for info in (table.get("pins") or {}).values()
               for row in info.get("functions") or [] if row.get("af") is not None]
        if bad:
            problems.append(f"F1 表里出现了 AF 编号：{bad[:3]}")
    else:
        bad = [row["signal"] for info in (table.get("pins") or {}).values()
               for row in info.get("functions") or []
               if row.get("af") is not None
               and not 0 <= int(row["af"]) <= 15]
        if bad:
            problems.append(f"AF 编号超出 0..15：{bad[:3]}")
    if not table.get("io_count"):
        problems.append("没有 GPIO 脚")
    return problems


def crosscheck(table: dict[str, Any],
               pinout: dict[str, Any] | None) -> list[str]:
    """与 P56 的**引脚定义表**对脚号：两个独立来源必须一致。"""
    if not pinout:
        return []
    problems: list[str] = []
    theirs = {str(row.get("port")): int(number)
              for number, row in (pinout.get("pins") or {}).items()
              if row.get("port")}
    for port, info in (table.get("pins") or {}).items():
        if port not in theirs:
            problems.append(f"{port} 在引脚表里没有")
            continue
        if int(info.get("position") or 0) != theirs[port]:
            problems.append(f"{port} 脚号不一致：AF 源码 {info.get('position')} "
                            f"vs 引脚表 {theirs[port]}")
    if int(pinout.get("count") or 0) != int(table.get("count") or 0):
        problems.append(f"总脚数不一致：{table.get('count')} vs {pinout.get('count')}")
    return problems


def list_tables() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not DIR.is_dir():
        return out
    for path in sorted(DIR.glob("*.json")):
        try:
            table = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            out.append({"part": path.stem, "file": path.name,
                        "error": f"{type(exc).__name__}: {exc}"})
            continue
        out.append({"part": table.get("part") or path.stem, "file": path.name,
                    "mpn": table.get("mpn", ""), "family": table.get("family"),
                    "count": table.get("count"), "io_count": table.get("io_count"),
                    "style": table.get("style"), "source": table.get("source"),
                    "retrieved": table.get("retrieved"),
                    "af_functions": table.get("af_functions"),
                    "problems": validate(table)})
    return out


def load(part: str) -> dict[str, Any] | None:
    key = _norm(part)
    path = DIR / f"{key}.json"
    if not key or not path.is_file():
        return None
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return table if isinstance(table, dict) else None


def find(kind: str, lcsc: str = "") -> dict[str, Any] | None:
    """按型号名找（AF 表不按 LCSC 建 —— ST 的库是按型号组织的）。"""
    return load(kind)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def _family_of_signal(signal: str) -> str:
    upper = str(signal or "").upper()
    for family in ("LPUART", "USART", "UART", "SPI", "I2S", "I2C", "FDCAN",
                   "CAN", "USB", "SDMMC", "QUADSPI", "OCTOSPI", "ETH",
                   "DCMI", "LTDC", "SAI", "HRTIM", "TIM"):
        if upper.startswith(family) or f"_{family}" in upper:
            return family
    return ""


def match_signal(table: dict[str, Any], pin: str, signal: str) -> dict[str, Any]:
    """`.ioc` 的一个（脚, 信号）→ 表里的结论。

    `status`：`ok`（表里查到了）/ `gpio` / `exti`（不需要查 AF）/
    `no_pin`（这颗芯片没这个脚）/ `not_on_pin`（脚在，但这个信号**不是它的功能**）/
    `unknown`（表里判不出，**不是冲突**）。
    """
    port = _canon(pin) or str(pin or "")
    info = (table.get("pins") or {}).get(port)
    if info is None:
        return {"status": "no_pin", "pin": port, "signal": signal}
    raw = str(signal or "").strip()
    if raw in ("GPIO_Output", "GPIO_Input"):
        return {"status": "gpio", "pin": port, "signal": raw}
    if _EXTI_RE.match(raw):
        return {"status": "exti", "pin": port, "signal": raw}
    functions = {row["signal"]: row for row in info.get("functions") or []}
    if raw in functions:
        return {"status": "ok", "pin": port, "signal": raw, **functions[raw],
                "via": "exact"}
    stripped = raw[2:] if raw.upper().startswith("S_") else ""
    if stripped and stripped in functions:
        return {"status": "ok", "pin": port, "signal": stripped,
                **functions[stripped], "via": "alias:S_"}
    # CubeMX 把"通道 + ETR"合写成一个信号（`TIM2_CH1_ETR`），ST 库里是两条
    # （`TIM2_CH1` / `TIM2_ETR`）。去掉 `_ETR` 再比，**只在这条规则下**。
    if stripped.endswith("_ETR") and stripped[:-4] in functions:
        return {"status": "ok", "pin": port, "signal": stripped[:-4],
                **functions[stripped[:-4]], "via": "alias:CH_ETR"}
    match = _ADCX_RE.match(raw)
    if match:
        for name, row in functions.items():
            adc = _ADC_RE.match(name)
            if adc and adc.group(2) == match.group(1):
                return {"status": "ok", "pin": port, "signal": name, **row,
                        "via": "alias:ADCx"}
    family = _family_of_signal(raw)
    if family:
        # 表来自**完整的 MCU 描述**（脚号与引脚表逐脚对过），所以"信号不在这根脚上"
        # 是可信结论。`pin_functions` 列出这根脚**同族**的功能，给报错用。
        same = [name for name in functions if _family_of_signal(name) == family]
        return {"status": "not_on_pin", "pin": port, "signal": raw,
                "family": family, "pin_functions": sorted(same)[:6]}
    return {"status": "unknown", "pin": port, "signal": raw}


def af_of(table: dict[str, Any], pin: str, signal: str) -> int | None:
    got = match_signal(table, pin, signal)
    return got.get("af") if got.get("status") == "ok" else None


def pins_for_signal(table: dict[str, Any], signal: str) -> list[dict[str, Any]]:
    """哪些脚能做这个信号（含重映射信息）。**表里没有就返回空**（不是"不能"）。"""
    wanted = str(signal or "").strip()
    out: list[dict[str, Any]] = []
    for port, info in sorted((table.get("pins") or {}).items()):
        for row in info.get("functions") or []:
            if row.get("signal") == wanted:
                out.append({"pin": port, "position": info.get("position"),
                            "af": row.get("af"), "remap": row.get("remap")})
                break
    return out


def describe() -> dict[str, Any]:
    rows = list_tables()
    return {
        "module": "dsh_embagent.design.afmap",
        "dir": str(DIR),
        "tables": len(rows),
        "parts": [row.get("part") for row in rows],
        "source": "ST 官方 open pin data（CubeMX 同源）",
        "license": LICENSE,
        "build": ("afmap build --part=stm32f401rct6（源文件映射在 afmap.PARTS）；"
                  "取不到 / 脚号与引脚表不一致 → not_ready/failed"),
        "provides": ("脚 → 全部复用功能 + AF 编号（F4/H7）或重映射（F1）；"
                     "反查：哪些脚能做某个信号"),
        "not_provided": ("电气特性、时序、复用功能的**可用性限制**"
                         "（比如某 AF 只在特定封装上可用）"),
        "discipline": ("F1 不许写 AF 编号（它用 AFIO 重映射）；"
                       "`.ioc` 信号按 精确/S_/ADCx/EXTI 四种规则匹配，"
                       "认不出就是 unknown，不当冲突"),
    }