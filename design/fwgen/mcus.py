# 从 Traceforge（MIT）移植 —— 原路径 src/traceforge/firmware/mcus.py
#
# MIT License, Copyright (c) 2026 Traceforge contributors。
# 本文件保留原实现的逻辑与"每项注明 datasheet 章节"的纪律；改动处见下方注释，
# 每个改动都标了 `# [hwv]`。
#
"""MCU pin tables (data). Package pin number -> pin name, and what each pin can do.

Sources are cited per entry. ``functions`` use these tokens: GPIO, ADC, PWM, I2C_SDA,
I2C_SCL, SPI_MOSI, SPI_MISO, SPI_SCK, UART_TX, UART_RX. Pins a design may use even
though the datasheet warns about them go in ``caution``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Mcu:
    key: str
    family: str  # avr | esp32 | stm32
    name: str
    package: str
    source: str  # datasheet table the data was taken from
    match: str  # regex over "<symbol> <value> <footprint>"
    pins: dict[str, str] = field(default_factory=dict)  # package pin number -> pin name
    functions: dict[str, tuple[str, ...]] = field(default_factory=dict)  # pin name -> tokens
    arduino: dict[str, int] = field(default_factory=dict)  # pin name -> Arduino number
    adc: dict[str, int] = field(default_factory=dict)  # pin name -> ADC channel
    caution: dict[str, str] = field(default_factory=dict)  # pin name -> warning
    power_pins: tuple[str, ...] = ()
    platformio: dict[str, str] = field(default_factory=dict)
    fqbn: str = ""
    f_cpu: int = 0


_ATTINY85 = Mcu(
    key="attiny85",
    family="avr",
    name="ATtiny85",
    package="SOIC-8 / DIP-8",
    source="Microchip ATtiny25/45/85 datasheet (DS40002586), section 1.1 Pin Descriptions, "
    "section 10.3 Alternate Port Functions",
    match=r"attiny85",
    pins={
        "1": "PB5",
        "2": "PB3",
        "3": "PB4",
        "4": "GND",
        "5": "PB0",
        "6": "PB1",
        "7": "PB2",
        "8": "VCC",
    },
    functions={
        # PB0: MOSI/DI/SDA/AIN0/OC0A; PB1: MISO/DO/AIN1/OC0B/OC1A; PB2: SCK/USCK/SCL/ADC1
        "PB0": ("GPIO", "PWM", "I2C_SDA", "SPI_MOSI"),
        "PB1": ("GPIO", "PWM", "SPI_MISO"),
        "PB2": ("GPIO", "ADC", "I2C_SCL", "SPI_SCK"),
        "PB3": ("GPIO", "ADC", "PWM"),
        "PB4": ("GPIO", "ADC", "PWM"),
        "PB5": ("GPIO", "ADC"),
    },
    arduino={"PB0": 0, "PB1": 1, "PB2": 2, "PB3": 3, "PB4": 4, "PB5": 5},
    adc={"PB5": 0, "PB2": 1, "PB4": 2, "PB3": 3},
    caution={"PB5": "RESET pin: using it as I/O needs the RSTDISBL fuse and loses ISP"},
    power_pins=("VCC", "GND"),
    platformio={"platform": "atmelavr", "board": "attiny85", "framework": "arduino"},
    fqbn="ATTinyCore:avr:attinyx5",
    f_cpu=8_000_000,
)

_ESP32S3_PINS = {
    "1": "GND",
    "2": "3V3",
    "3": "EN",
    "4": "IO4",
    "5": "IO5",
    "6": "IO6",
    "7": "IO7",
    "8": "IO15",
    "9": "IO16",
    "10": "IO17",
    "11": "IO18",
    "12": "IO8",
    "13": "IO19",
    "14": "IO20",
    "15": "IO3",
    "16": "IO46",
    "17": "IO9",
    "18": "IO10",
    "19": "IO11",
    "20": "IO12",
    "21": "IO13",
    "22": "IO14",
    "23": "IO21",
    "24": "IO47",
    "25": "IO48",
    "26": "IO45",
    "27": "IO0",
    "28": "IO35",
    "29": "IO36",
    "30": "IO37",
    "31": "IO38",
    "32": "IO39",
    "33": "IO40",
    "34": "IO41",
    "35": "IO42",
    "36": "IO44",
    "37": "IO43",
    "38": "IO2",
    "39": "IO1",
    "40": "GND",
    "41": "GND",
}
_ESP32S3_GPIO = [n for n in range(49) if n not in range(22, 26)]  # GPIO22-25 do not exist
_ESP32S3_FUNCS: dict[str, tuple[str, ...]] = {}
for _n in _ESP32S3_GPIO:
    # The GPIO matrix routes any digital peripheral to any pin; ADC1 = 1-10, ADC2 = 11-20.
    _f = ["GPIO", "PWM", "I2C_SDA", "I2C_SCL", "SPI_MOSI", "SPI_MISO", "SPI_SCK"]
    _f += ["UART_TX", "UART_RX"]
    if 1 <= _n <= 20:
        _f.append("ADC")
    _ESP32S3_FUNCS[f"IO{_n}"] = tuple(_f)
_ESP32S3_ADC = {f"IO{n}": n - 1 for n in range(1, 21)}  # ADC1 ch0-9, ADC2 ch0-9 (n 11-20)
_ESP32S3_ADC.update({f"IO{n}": n - 11 for n in range(11, 21)})
_ESP32S3_CAUTION = {
    **{f"IO{n}": "strapping pin: keep its boot level" for n in (0, 3, 45, 46)},
    **{f"IO{n}": "connected to the module's flash/PSRAM: not usable" for n in range(26, 33)},
    **{
        f"IO{n}": "octal PSRAM on N8R8/N16R8 modules: not usable there"
        for n in (33, 34, 35, 36, 37)
    },
    "IO19": "USB D- (native USB); using it as GPIO disables USB",
    "IO20": "USB D+ (native USB); using it as GPIO disables USB",
}
_ESP32S3 = Mcu(
    key="esp32s3",
    family="esp32",
    name="ESP32-S3-WROOM-1",
    package="module, 41 pads",
    source="Espressif ESP32-S3-WROOM-1 datasheet, Table 3-1 Pin Definitions; ESP32-S3 datasheet "
    "section 2.2 (IO MUX, GPIO matrix, strapping pins)",
    match=r"esp32-s3|esp32s3",
    pins=_ESP32S3_PINS,
    functions=_ESP32S3_FUNCS,
    arduino={f"IO{n}": n for n in _ESP32S3_GPIO},
    adc=_ESP32S3_ADC,
    caution=_ESP32S3_CAUTION,
    power_pins=("3V3", "GND", "EN"),
    platformio={
        "platform": "espressif32",
        "board": "esp32-s3-devkitc-1",
        "framework": "arduino",
    },
    fqbn="esp32:esp32:esp32s3",
    f_cpu=240_000_000,
)

_STM32F103 = Mcu(
    key="stm32f103",
    family="stm32",
    name="STM32F103C8T6",
    package="LQFP-48",
    source="ST STM32F103x8/xB datasheet (DS5319), Table 5 pin definitions. Only pin names "
    "are used (e.g. PA5); package pin numbers are not tabulated here",
    match=r"stm32f103",
    functions={},
    power_pins=("VDD", "VSS", "VBAT", "VDDA", "VSSA", "NRST", "BOOT0"),
    platformio={"platform": "ststm32", "board": "bluepill_f103c8", "framework": "stm32cube"},
    f_cpu=8_000_000,
)

# ---------------------------------------------------------------------------
# [hwv] 3 → 7：补上本项目 7 个 `.ioc` 文件里真实出现的 STM32 型号
# ---------------------------------------------------------------------------
#
# 为什么只写型号/封装/平台，**不写 pins 与 functions**：
# `.ioc` 给的是端口引脚名（PA0/PH9），不是封装脚号；`pins.h` 与交叉核对
# 都按端口名工作，所以不需要"封装脚号 → 引脚名"这张表。而 `functions`
# （某个脚能做哪些复用功能）要从 datasheet 的 AF 表逐脚抄——**没抄就不写**。
# 留空的后果只是少一条"这个脚做不了 I2C"的提醒，比编一张假表好。
#
# `ports` 同理：F405RG 的 LQFP-64 到底引出哪几个端口，不同批次/封装手册
# 说法不同，我不替它打包票。缺这一项时 `describe()` 会如实报
# `port_table: missing`，而不是假装"这个脚不存在"。

_STM32F103C6 = Mcu(
    key="stm32f103c6",
    family="stm32",
    name="STM32F103C6T6",
    package="LQFP-48",
    source="ST STM32F103x4/x6 datasheet (DS5319). 与 C8 同为 LQFP-48，"
    "引脚定义一致；分两条只为报对型号名",
    match=r"stm32f103c[46]",
    functions={},
    power_pins=("VDD", "VSS", "VBAT", "VDDA", "VSSA", "NRST", "BOOT0"),
    platformio={"platform": "ststm32", "board": "bluepill_f103c6", "framework": "stm32cube"},
    f_cpu=8_000_000,
)

_STM32F401RC = Mcu(
    key="stm32f401rc",
    family="stm32",
    name="STM32F401RCT6",
    package="LQFP-64",
    source="ST STM32F401xB/C datasheet (DS9716), Table 4 pin definitions",
    match=r"stm32f401r[bc]",
    functions={},
    power_pins=("VDD", "VSS", "VBAT", "VDDA", "VSSA", "NRST", "BOOT0"),
    platformio={"platform": "ststm32", "board": "genericSTM32F401RC",
                "framework": "stm32cube"},
    f_cpu=84_000_000,
)

_STM32F405RG = Mcu(
    key="stm32f405rg",
    family="stm32",
    name="STM32F405RGT6",
    package="LQFP-64",
    source="ST STM32F405xx/407xx datasheet (DS8626), Table 5 pin definitions",
    match=r"stm32f405r[gt]",
    functions={},
    power_pins=("VDD", "VSS", "VBAT", "VDDA", "VSSA", "NRST", "BOOT0", "PDR_ON"),
    platformio={"platform": "ststm32", "board": "genericSTM32F405RG",
                "framework": "stm32cube"},
    f_cpu=168_000_000,
)

_STM32H743II = Mcu(
    key="stm32h743ii",
    family="stm32",
    name="STM32H743IIT6",
    package="LQFP-176",
    source="ST STM32H742xI/H743xI datasheet (DS12110), Table 24 pin definitions",
    match=r"stm32h743i[it]",
    functions={},
    power_pins=("VDD", "VSS", "VDDA", "VSSA", "VBAT", "NRST", "BOOT0", "PDR_ON"),
    platformio={"platform": "ststm32", "board": "genericSTM32H743II",
                "framework": "stm32cube"},
    f_cpu=480_000_000,
)

#: C6 必须排在通用 f103 **之前**：`find_mcu` 按插入顺序取第一个命中，
#: 否则 `.ioc` 写 C6 会被报成 C8（引脚一样但型号报错）。
MCUS: dict[str, Mcu] = {m.key: m for m in (
    _ATTINY85, _ESP32S3, _STM32F103C6, _STM32F103,
    _STM32F401RC, _STM32F405RG, _STM32H743II,
)}


def find_mcu(text: str) -> Mcu | None:
    for mcu in MCUS.values():
        if re.search(mcu.match, text, re.I):
            return mcu
    return None
