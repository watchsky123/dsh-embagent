"""常见元件的物理尺寸表（mm）。

这是 ``partfacts`` 的**兜底档**：hwforge 的 ``partfacts`` 只覆盖约 50 件，
Velxio 的 Wokwi 元素只有像素尺寸。查不到的件一律让调用方标
``estimated``——**绝不从像素数字反推出看起来精确的毫米值**。

每条记录 ``(w, h, d, source)``：

    w = X 宽    h = Y 深    d = Z 高

`source` 是 ``datasheet`` 或 ``estimated``；``estimated`` 的条目是常见
封装/模块的**类别典型值**，用于让 3D 有个合理体积，判定面板会单独列出。
"""

from __future__ import annotations

from typing import Any

# kind(小写, Velxio metadataId 或通用名) -> (w, h, d, source)
PART_SIZES: dict[str, tuple[float, float, float, str]] = {
    # --- 板卡（与 boards.py 同源，这里给厚度方向的高度） --------------
    "arduino-uno": (72.58, 53.34, 10.0, "datasheet"),
    "arduino-nano": (44.9, 17.8, 10.0, "datasheet"),
    "arduino-mega": (102.66, 50.80, 10.0, "datasheet"),
    "esp32": (28.2, 53.0, 12.0, "datasheet"),
    "esp32-s3": (25.5, 70.0, 12.0, "datasheet"),
    "esp32-c3": (25.4, 42.9, 12.0, "datasheet"),
    "raspberry-pi-pico": (21.0, 51.0, 5.0, "datasheet"),
    "pi-pico-w": (21.0, 51.0, 5.0, "datasheet"),
    "stm32-bluepill": (22.855, 54.193, 10.0, "datasheet"),
    "raspberry-pi-4": (85.0, 56.0, 20.0, "datasheet"),
    # --- 分立器件 -----------------------------------------------------
    "led": (5.0, 5.0, 8.6, "datasheet"),
    "led-red": (5.0, 5.0, 8.6, "datasheet"),
    "led-green": (5.0, 5.0, 8.6, "datasheet"),
    "led-blue": (5.0, 5.0, 8.6, "datasheet"),
    "rgb-led": (5.0, 5.0, 8.6, "estimated"),
    "resistor": (6.3, 2.3, 2.3, "datasheet"),
    "resistor-220": (6.3, 2.3, 2.3, "datasheet"),
    "resistor-1k": (6.3, 2.3, 2.3, "datasheet"),
    "resistor-10k": (6.3, 2.3, 2.3, "datasheet"),
    "capacitor": (5.0, 2.5, 5.0, "estimated"),
    "capacitor-electrolytic": (6.3, 6.3, 11.0, "estimated"),
    "diode": (4.0, 2.0, 2.0, "estimated"),
    "transistor": (4.5, 2.0, 4.0, "estimated"),
    "pushbutton": (6.0, 6.0, 5.0, "estimated"),
    "button": (6.0, 6.0, 5.0, "estimated"),
    "potentiometer": (9.5, 9.5, 12.0, "estimated"),
    "buzzer": (12.0, 12.0, 9.0, "estimated"),
    "buzzer-12mm": (12.0, 12.0, 9.0, "estimated"),
    "neopixel": (5.0, 5.0, 3.0, "estimated"),
    "servo": (12.2, 23.0, 29.0, "datasheet"),          # SG90
    "servo-sg90": (12.2, 23.0, 29.0, "datasheet"),
    # --- 模块 ---------------------------------------------------------
    "dht22": (15.0, 8.0, 30.0, "estimated"),
    "dht11": (12.0, 8.0, 25.0, "estimated"),
    "hc-sr04": (45.0, 20.0, 15.0, "datasheet"),
    "ultrasonic": (45.0, 20.0, 15.0, "datasheet"),
    "lcd1602": (80.0, 36.0, 12.0, "estimated"),
    "lcd2004": (98.0, 60.0, 12.0, "estimated"),
    "ssd1306": (27.0, 27.0, 4.0, "estimated"),
    "oled-ssd1306": (27.0, 27.0, 4.0, "estimated"),
    "bme280": (20.0, 20.0, 4.0, "estimated"),
    "mpu6050": (21.0, 16.0, 4.0, "estimated"),
    "relay-module": (50.0, 26.0, 19.0, "estimated"),
    "buck-converter": (43.0, 21.0, 14.0, "estimated"),
    "breadboard": (83.0, 55.0, 8.5, "datasheet"),
    "breadboard-half": (83.0, 55.0, 8.5, "datasheet"),
    # --- 连接器 -------------------------------------------------------
    "connector": (5.08, 2.54, 8.5, "estimated"),
    "header": (5.08, 2.54, 8.5, "estimated"),
    "usb-connector": (12.0, 8.0, 6.0, "estimated"),
    "usb-c": (9.0, 7.0, 3.2, "estimated"),
    "antenna": (15.0, 5.0, 1.0, "estimated"),
}


def _normalise(kind: str) -> str:
    return kind.strip().lower().replace("_", "-")


def part_size(kind: str) -> dict[str, Any]:
    """查尺寸。返回 ``{size_mm, physical_source, known}``，查不到就是 0 尺寸。"""
    key = _normalise(kind)
    hit = PART_SIZES.get(key)
    if hit is None:
        # 允许 "wokwi-led" / "board-arduino-uno" 这类前缀写法
        for prefix in ("wokwi-", "board-", "part-"):
            if key.startswith(prefix):
                hit = PART_SIZES.get(key[len(prefix):])
                if hit is not None:
                    break
    if hit is None:
        return {
            "size_mm": {"w": 0.0, "h": 0.0, "d": 0.0},
            "physical_source": "estimated",
            "known": False,
        }
    w, h, d, source = hit
    return {
        "size_mm": {"w": w, "h": h, "d": d},
        "physical_source": source,
        "known": True,
    }


def catalogue() -> list[dict[str, Any]]:
    return [
        {"kind": kind, "size_mm": {"w": w, "h": h, "d": d}, "source": source}
        for kind, (w, h, d, source) in sorted(PART_SIZES.items())
    ]
