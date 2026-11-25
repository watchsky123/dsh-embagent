"""引脚库 —— 让网表里的引脚名**可被校验**。

## 为什么这是分水岭

在这之前 `Part` 根本没有引脚字段，`validate()` 只检查 `ref` 是否存在。
实测：`{"ref":"U1","pin":"NOT_A_REAL_PIN"}` 会**通过校验**——AI 可以写出一条
语法合法、电气上毫无意义的网表，而检查器全绿。于是"从 0 搭"出来的网表
只是**声明**，不是被验证的电气事实。

## 三档来源，可信度不同，必须分开说

1. **declared** —— 元件自己在 `design.json` 里写了 `pins`。来自导入器实测
   （立创 EDA 的 `PAD_NET` 焊盘号、KiCad 的焊盘），**最可信**。
2. **convention** —— 常见元件的标准引脚名（LED 的 `A`/`K`、电阻的 `1`/`2`、
   Arduino 的 `D0..D13`）。这是**行业约定**，不是从这个器件的数据手册读到的。
3. **unknown** —— 没有声明也没有约定。此时网表引脚**不校验**，而是作为
   `not_verified` 报出来：不是通过，是没核对。

第 3 档刻意不猜。给一个猜出来的引脚表，等于把"未验证"伪装成"已验证"——
比不校验更糟，因为用户会信它。

@module dsh_embagent.design.pins
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# 引脚名的规范化：`A1`/`a1`、`GND`/`gnd`、`D13`/`d13` 视为同一个。
_WS = re.compile(r"\s+")


def normalise(name: str) -> str:
    """引脚名归一化：去空白、转大写。用于比较，不用于显示。"""
    return _WS.sub("", str(name)).upper()


# ---------------------------------------------------------------------------
# 约定引脚表（convention）
# ---------------------------------------------------------------------------
#
# 只收**行业约定明确**的器件。取值来自各类开发板/模块的通用引脚标注习惯，
# 不是逐份数据手册核对的结果 —— 所以它们的来源标 `convention`，
# 与导入实测的 `declared` 分开统计。
PIN_LIBRARY: dict[str, tuple[str, ...]] = {
    # --- 开发板（Arduino 排针命名）-------------------------------------
    "arduino-uno": (
        "D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9",
        "D10", "D11", "D12", "D13",
        "A0", "A1", "A2", "A3", "A4", "A5",
        "5V", "3V3", "GND", "VIN", "AREF", "RESET", "IOREF",
    ),
    "arduino-nano": (
        "D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9",
        "D10", "D11", "D12", "D13",
        "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7",
        "5V", "3V3", "GND", "VIN", "AREF", "RESET",
    ),
    "arduino-mega": (
        *(f"D{i}" for i in range(54)),
        *(f"A{i}" for i in range(16)),
        "5V", "3V3", "GND", "VIN", "AREF", "RESET", "IOREF",
    ),
    "stm32-bluepill": (
        "3V3", "5V", "GND", "VBAT",
        "PA0", "PA1", "PA2", "PA3", "PA4", "PA5", "PA6", "PA7",
        "PA8", "PA9", "PA10", "PA11", "PA12", "PA15",
        "PB0", "PB1", "PB3", "PB4", "PB5", "PB6", "PB7",
        "PB8", "PB9", "PB10", "PB11", "PB12", "PB13", "PB14", "PB15",
        "PC13", "PC14", "PC15",
    ),
    # --- 分立器件 -------------------------------------------------------
    "led": ("A", "K"),
    "led-red": ("A", "K"),
    "led-green": ("A", "K"),
    "led-blue": ("A", "K"),
    "rgb-led": ("R", "G", "B", "COM"),
    "resistor": ("1", "2"),
    "resistor-220": ("1", "2"),
    "resistor-1k": ("1", "2"),
    "resistor-10k": ("1", "2"),
    "capacitor": ("1", "2"),
    "capacitor-electrolytic": ("+", "-"),
    "diode": ("A", "K"),
    "transistor": ("B", "C", "E"),
    "pushbutton": ("1", "2"),
    "button": ("1", "2"),
    "potentiometer": ("1", "2", "3"),
    "buzzer": ("1", "2"),
    "buzzer-12mm": ("1", "2"),
    "neopixel": ("VCC", "GND", "DIN", "DOUT"),
    # --- 模块 -----------------------------------------------------------
    "servo": ("VCC", "GND", "SIG"),
    "servo-sg90": ("VCC", "GND", "SIG"),
    "dht22": ("VCC", "GND", "SDA"),
    "dht11": ("VCC", "GND", "SDA"),
    "hc-sr04": ("VCC", "TRIG", "ECHO", "GND"),
    "ultrasonic": ("VCC", "TRIG", "ECHO", "GND"),
    "lcd1602": ("VCC", "GND", "SDA", "SCL"),
    "lcd2004": ("VCC", "GND", "SDA", "SCL"),
    "ssd1306": ("VCC", "GND", "SCL", "SDA"),
    "oled-ssd1306": ("VCC", "GND", "SCL", "SDA"),
    "bme280": ("VCC", "GND", "SCL", "SDA"),
    "mpu6050": ("VCC", "GND", "SCL", "SDA", "INT"),
    "relay-module": ("VCC", "GND", "IN"),
    "buck-converter": ("VIN", "GND", "VOUT"),
    # --- 连接器 ---------------------------------------------------------
    "connector": ("1",),
    "header": ("1",),
    "usb-connector": ("VBUS", "D-", "D+", "GND"),
    "usb-c": ("VBUS", "D-", "D+", "CC1", "CC2", "GND"),
    "antenna": ("FEED", "GND"),
}

# 板卡本身也可以出现在网表里（`ref="board"`），它的引脚就是开发板排针。
BOARD_KINDS = ("arduino-uno", "arduino-nano", "arduino-mega",
               "stm32-bluepill")


def declared_pins(part: Any) -> tuple[str, ...]:
    """元件自己声明的引脚（导入器实测得到）。"""
    raw = getattr(part, "pins", None)
    if not raw:
        return ()
    return tuple(str(p) for p in raw if str(p).strip() != "")


def resolve(part: Any) -> tuple[tuple[str, ...], str]:
    """返回 `(引脚表, 来源)`；来源是 `declared` / `convention` / `unknown`。

    **优先级：declared > convention**。导入器从真实工程读到的焊盘号比
    我们手写的约定表可信——真实工程里同一个 kind 可能有不同封装。
    """
    declared = declared_pins(part)
    if declared:
        return declared, "declared"
    kind = str(getattr(part, "kind", "")).strip().lower()
    if kind in PIN_LIBRARY:
        return PIN_LIBRARY[kind], "convention"
    return (), "unknown"


def has_pin(part: Any, pin: str) -> bool | None:
    """`True` 有、`False` 明确没有、`None` **无法判断**（没声明也没约定）。

    返回 `None` 时调用方必须把它当"未验证"而不是"通过"。
    """
    table, source = resolve(part)
    if source == "unknown" or not table:
        return None
    wanted = normalise(pin)
    return any(normalise(name) == wanted for name in table)


def index_of(part: Any) -> dict[str, str]:
    """归一化名 → 原始名，用于把用户写的 `a` 映射回库里的 `A`。"""
    table, _source = resolve(part)
    return {normalise(name): name for name in table}


def audit(design: Any) -> dict[str, Any]:
    """对整个设计做一次引脚审计。

    返回：

        {
          "pins_total":  网表里出现的 (ref, pin) 数,
          "pins_checked": 能判断的（declared/convention）,
          "pins_unverified": 无法判断的,
          "missing": [ "U1.NOT_A_PIN: 该元件没有这个引脚（候选：D13, GND, …）" ],
          "unknown_parts": [ref...],   # 完全没有引脚信息的元件
          "sources": {"declared": n, "convention": n, "unknown": n},
        }
    """
    parts = {str(getattr(p, "ref", "")): p for p in (design.parts or [])}
    sources: dict[str, int] = {"declared": 0, "convention": 0, "unknown": 0}
    for part in parts.values():
        sources[resolve(part)[1]] += 1

    pins_total = 0
    checked = 0
    unverified = 0
    missing: list[str] = []
    unknown_refs: set[str] = set()

    for entry in design.nets or []:
        net_name = str(entry.get("net") or "")
        for pin_ref in entry.get("pins") or []:
            ref = str(pin_ref.get("ref") or "")
            pin = str(pin_ref.get("pin") or "")
            if ref == "" or pin == "":
                continue
            pins_total += 1
            part = parts.get(ref)
            if part is None:
                # 指向不存在的元件由 validate() 报，这里不重复。
                continue
            verdict = has_pin(part, pin)
            if verdict is None:
                unverified += 1
                unknown_refs.add(ref)
            elif verdict:
                checked += 1
            else:
                checked += 1
                table, _source = resolve(part)
                preview = ", ".join(table[:8])
                more = "…" if len(table) > 8 else ""
                missing.append(
                    f"{ref}.{pin}（net {net_name}）：该元件没有这个引脚"
                    f"（候选：{preview}{more}）")

    return {
        "pins_total": pins_total,
        "pins_checked": checked,
        "pins_unverified": unverified,
        "missing": sorted(set(missing)),
        "unknown_parts": sorted(unknown_refs),
        "sources": sources,
    }


def summary_line(audit_result: dict[str, Any]) -> str:
    """给 `not_verified` 用的一句话：把"没核对"说清楚。"""
    total = audit_result.get("pins_total", 0)
    unverified = audit_result.get("pins_unverified", 0)
    if total == 0:
        return "引脚校验：网表里没有引脚，无需核对"
    if unverified == 0:
        return f"引脚校验：{total} 个引脚全部核对过"
    refs = audit_result.get("unknown_parts") or []
    preview = ", ".join(refs[:6]) + ("…" if len(refs) > 6 else "")
    return (
        f"引脚校验：{total} 个引脚中 {unverified} 个**无法核对**"
        f"（元件没有引脚表：{preview}）。不是通过，是没核对。")


def known_kinds() -> Iterable[str]:
    return sorted(PIN_LIBRARY)
