# 从 Traceforge（MIT）移植 —— 原路径 src/traceforge/firmware/pinmap.py
#
# MIT License, Copyright (c) 2026 Traceforge contributors。
# 本文件保留原实现的逻辑与"每项注明 datasheet 章节"的纪律；改动处见下方注释，
# 每个改动都标了 `# [hwv]`。
"""引脚映射：两个入口，一份输出。

## 两个入口

| 入口 | 数据从哪来 | 什么时候用 |
|---|---|---|
| `build_pinmap(netlist)` | 我们的 `design.json` 网表 | 设计里有网表、但没 `.ioc` |
| `from_ioc(path)` | CubeMX 的 `.ioc` | **首选**：它带 label 与信号名（`PA15.GPIO_Label=VL53-PWR-EN`） |

两者都产同一个 `PinMap`，所以下游（`scaffold` 的 5 个 target、`pins.h` 渲染）
只有一条代码路径。

## [hwv] 相对原实现的改动

1. **`.ioc` 入口**（原实现只有网表入口）。
2. **`PinAssignment.value`**：原实现把 STM32 的引脚写成数字
   （`(port-'A')*16+bit`）。但 HAL 要的是 `GPIO_PIN_8` + `GPIOA` 两个东西，
   所以 `.ioc` 入口给 `value="GPIO_PIN_8"`，渲染时原样输出。
3. **`alias_prefix`**：同一个 `pins.h` 同时给出两套写法——
   寄存器级的 `PIN_x_PORT`（字符 `'A'` + 位号，`scaffold` 的 stm32 target 用）
   和 HAL 的 `HWV_PIN_x = GPIO_PIN_8` / `HWV_PORT_x = GPIOA`。
   **一套事实，两种写法**，不需要手改任何一边。
4. **`extract_uses()` / `crosscheck()`**：固件源码 → 引脚使用；与硬件配置对撞。
   原实现没有这个（它的 pinmap 只从网表推，不看固件）。

## 交叉核对的三类结论（照 plan §P20）

| 结论 | 含义 | 级别 |
|---|---|---|
| 幽灵引脚 | 软件用了 `.ioc` 里**没配**的脚 | **error**（运行时必炸或静默失效） |
| 闲置引脚 | `.ioc` 配了、固件**没用** | 提示（可能只是还没写到） |
| 方向冲突 | `.ioc` 配 Input、固件 `WritePin`；或配 Output、固件 `ReadPin` | **error** |

**输出必须引用双方**：`"幽灵引脚 PA10: F401-Front.ioc 无此配置 ← main.c:42"`。
只报"有幽灵引脚"而不给行号，人得自己搜，等于没报。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from dsh_embagent.design.fwgen.mcus import MCUS, Mcu, find_mcu
from dsh_embagent.design.fwgen.netlist import Netlist, NetlistError

_POWER = re.compile(
    r"^(\+?\d+V\d*|\+?\d+\.\d+V|VCC\w*|VDD\w*|VBUS\w*|VIN|VBAT|GND\w*|AGND|VSS\w*)$", re.I
)

# (regex over the upper-case net name, peripheral function, direction)
_ROLES: list[tuple[str, str, str]] = [
    (r"(^|_)(SDA|SIOD)(\d*)($|_)", "I2C_SDA", "io"),
    (r"(^|_)(SCL|SIOC)(\d*)($|_)", "I2C_SCL", "io"),
    (r"(^|_)(MOSI|COPI|SDI)($|_)", "SPI_MOSI", "out"),
    (r"(^|_)(MISO|CIPO|SDO)($|_)", "SPI_MISO", "in"),
    (r"(^|_)(SCK|SCLK|SPI_CLK)($|_)", "SPI_SCK", "out"),
    (r"(^|_)(TX|TXD|UART_TX)\d*($|_)", "UART_TX", "out"),
    (r"(^|_)(RX|RXD|UART_RX)\d*($|_)", "UART_RX", "in"),
    (r"(^|_)(PWM|BL|BACKLIGHT)($|_)", "PWM", "out"),
    (r"(^|_)(ADC|AIN|VSENSE|VBAT_SENSE|VMON|NTC)\d*($|_)", "ADC", "in"),
    (r"(^|_)(BTN|BUTTON|SW|KEY|IRQ|INT|DRDY|PIR|BOOT|PCLK|VSYNC|HREF|Y\d)($|_)", "GPIO", "in"),
    (r"(^|_)(LED|EN|CS|SS|NSS|CSN|RST|RESET|PWDN|XCLK|BUZZER|RELAY)($|_)", "GPIO", "out"),
]
_SPI_CS = re.compile(r"(^|_)(CS|SS|NSS|CSN)($|_)")


@dataclass
class PinAssignment:
    net: str
    macro: str  # identifier-safe upper-case name, unique in the map
    ref: str
    pin: str  # as written in the netlist (number or name)
    pin_name: str  # the MCU pin name (PB3, IO4, PA5)
    function: str  # GPIO, ADC, PWM, I2C_SDA, ...
    direction: str  # in | out | io
    pull_up: bool = False
    capabilities: tuple[str, ...] = ()
    port: str | None = None  # AVR/STM32 port letter
    bit: int | None = None
    number: int | None = None  # the number code uses (GPIO number, Arduino pin)
    adc_channel: int | None = None
    #: [hwv] 渲染时原样输出的值（HAL 要 `GPIO_PIN_8`，不是 8）。
    value: str = ""
    #: [hwv] `.ioc` 说的模式（Input/Output/Alternate…），用于方向冲突判定。
    mode: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class PinMap:
    mcu: Mcu
    ref: str
    assignments: list[PinAssignment]
    unconnected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: [hwv] 这份映射的来源（`.ioc` 路径 / design.json / 合成）。
    source: str = ""

    def by_net(self, net: str) -> PinAssignment | None:
        return next((a for a in self.assignments if a.net == net), None)

    def by_pin(self, pin_name: str) -> PinAssignment | None:
        return next((a for a in self.assignments if a.pin_name == pin_name), None)

    def as_dict(self) -> dict:
        return {
            "mcu": self.mcu.name,
            "ref": self.ref,
            "source": self.mcu.source,
            "origin": self.source,
            "pins": [
                {
                    "net": a.net,
                    "macro": f"PIN_{a.macro}",
                    "pin": a.pin,
                    "pin_name": a.pin_name,
                    "function": a.function,
                    "direction": a.direction,
                    "number": a.number,
                    "adc_channel": a.adc_channel,
                    "mode": a.mode,
                    "warnings": a.warnings,
                }
                for a in self.assignments
            ],
            "unconnected": self.unconnected,
            "warnings": self.warnings,
        }


def classify(net: str) -> tuple[str, str, bool]:
    """Guess (function, direction, pull-up) from a net name."""
    name = net.upper().lstrip("/")
    for pattern, function, direction in _ROLES:
        if re.search(pattern, name):
            pull = function == "GPIO" and direction == "in" and "BTN" in name
            if function == "SPI_SCK" and re.search(r"(^|_)SCL($|_)", name):
                continue
            return function, direction, pull
    if _SPI_CS.search(name):
        return "GPIO", "out", False
    return "GPIO", "io", False


def macro_name(net: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", net.upper().replace("+", "P").replace("~", "N")).strip("_")


def _pin_name(mcu: Mcu, pin: str) -> str | None:
    if pin in mcu.pins:
        return mcu.pins[pin]
    upper = pin.upper()
    if mcu.family == "esp32":
        upper = {"RXD0": "IO44", "TXD0": "IO43"}.get(upper, re.sub(r"^GPIO", "IO", upper))
        names = set(mcu.pins.values())
        return upper if upper in names else None
    return (
        upper if re.fullmatch(r"P[A-K]\d{1,2}", upper) or upper in set(mcu.pins.values()) else None
    )


def _split(mcu: Mcu, name: str) -> tuple[str | None, int | None, int | None]:
    """Return (port, bit, number) for a pin name."""
    if mcu.family == "avr":
        m = re.fullmatch(r"P([A-F])(\d)", name)
        return (m.group(1), int(m.group(2)), mcu.arduino.get(name)) if m else (None, None, None)
    if mcu.family == "esp32":
        m = re.fullmatch(r"IO(\d+)", name)
        return (None, None, int(m.group(1))) if m else (None, None, None)
    m = re.fullmatch(r"P([A-K])(\d{1,2})", name)
    if m:
        return m.group(1), int(m.group(2)), (ord(m.group(1)) - 65) * 16 + int(m.group(2))
    return None, None, None


def find_mcu_ref(netlist: Netlist, mcu: Mcu | None = None) -> tuple[str, Mcu]:
    for ref, text in sorted(netlist.parts.items()):
        found = find_mcu(text)
        if found is not None and (mcu is None or found.key == mcu.key):
            return ref, found
    raise NetlistError(
        "no known MCU in the design (supported: "
        + ", ".join(m.name for m in MCUS.values())
        + "); pass --mcu and --ref"
    )


def build_pinmap(netlist: Netlist, ref: str | None = None, mcu: str | None = None) -> PinMap:
    chosen = MCUS.get(mcu) if mcu else None
    if mcu and chosen is None:
        raise NetlistError(f"unknown MCU {mcu!r} (known: {', '.join(MCUS)})")
    if ref is None:
        ref, chosen = find_mcu_ref(netlist, chosen)
    elif chosen is None:
        chosen = find_mcu(netlist.parts.get(ref, ""))
        if chosen is None:
            raise NetlistError(f"{ref} is not a known MCU; pass --mcu")
    pm = PinMap(chosen, ref, [], source=netlist.source)
    used: set[str] = set()
    for net, members in netlist.nets.items():
        if _POWER.match(net.lstrip("/")) or net.lstrip("/").startswith("unconnected-"):
            continue
        for member_ref, pin in members:
            if member_ref != ref:
                continue
            name = _pin_name(chosen, str(pin))
            if name is None:
                pm.warnings.append(f"{ref}.{pin} (net {net}): not a pin of {chosen.name}")
                continue
            if name in chosen.power_pins or name == "GND":
                continue
            function, direction, pull = classify(net)
            port, bit, number = _split(chosen, name)
            caps = chosen.functions.get(name, ())
            macro = macro_name(net)
            base, k = macro, 2
            while macro in used:
                macro, k = f"{base}_{k}", k + 1
            used.add(macro)
            item = PinAssignment(
                net, macro, ref, str(pin), name, function, direction, pull, caps,
                port, bit, number, chosen.adc.get(name),
            )  # fmt: skip
            if caps and function not in caps and function != "GPIO":
                item.warnings.append(
                    f"{net} looks like {function} but {name} cannot do it (can: {', '.join(caps)})"
                )
            if function == "GPIO" and caps and "GPIO" not in caps:
                item.warnings.append(f"{name} has no GPIO function")
            if name in chosen.caution:
                item.warnings.append(f"{name}: {chosen.caution[name]}")
            pm.assignments.append(item)
    pm.assignments.sort(key=lambda a: (a.number is None, a.number or 0, a.net))
    mapped = {a.pin_name for a in pm.assignments}
    pm.unconnected = sorted(
        n
        for n in set(chosen.pins.values())
        if n not in mapped and n not in chosen.power_pins and n != "GND"
    )
    for a in pm.assignments:
        pm.warnings.extend(a.warnings)
    _check_peripherals(pm)
    return pm


def _check_peripherals(pm: PinMap) -> None:
    funcs: dict[str, list[PinAssignment]] = {}
    for a in pm.assignments:
        funcs.setdefault(a.function, []).append(a)
    for function, items in funcs.items():
        if function.startswith(("I2C", "SPI", "UART")) and len(items) > 1:
            pm.warnings.append(
                f"{function} appears on several nets: " + ", ".join(i.net for i in items)
            )


# ---------------------------------------------------------------------------
# [hwv] 入口二：CubeMX 的 .ioc
# ---------------------------------------------------------------------------

#: `.ioc` 里这些 Mode 值表示「这个引脚被硬件配置了」。
_CONFIGURED_MODES = ("INPUT", "OUTPUT", "ALTERNATE", "ANALOG", "ASYNCHRONOUS",
                     "SYNCHRONOUS", "SERIAL_WIRE", "I2C", "SPI", "USART", "UART")


@dataclass
class IocPin:
    """`.ioc` 里的一个引脚。``label`` 来自 ``GPIO_Label``（人起的名字）。"""

    pin: str                # PA8
    port: str               # A
    bit: int                # 8
    mode: str = ""          # Output / Input / Alternate / Asynchronous…
    signal: str = ""        # USART2_TX / GPIO_Output…
    label: str = ""         # VL53-PWR-EN
    macro: str = ""         # VL53_PWR_EN

    @property
    def configured(self) -> bool:
        """被硬件配置了：有 Mode、或 Signal、或人给的 Label。

        **实测坑**：CubeMX 对普通 GPIO 输出**不写 `.Mode` 行**，只写
        `PA15.Signal=GPIO_Output` 与 `PA15.GPIO_Label=…`。第一版只看
        `Mode` 是否非空，于是 PA15/PB9/PC0 全被当成"没配置"漏掉——
        而它们恰恰是 `.ioc` 里**唯一带人起名字**的那几个脚。
        """
        if self.mode.strip().upper() in ("RESET",):
            return False
        return bool(self.mode or self.signal or self.label)

    @property
    def reserved_debug(self) -> bool:
        """SWD/JTAG 调试脚。它们在硬件上确实被配置了，但**不属于应用 I/O**，
        所以 `crosscheck` 不把它们算进"闲置"。"""
        signal = self.signal.upper()
        return (self.mode.upper() == "SERIAL_WIRE"
                or signal.startswith(("SYS_JT", "SYS_SW")))

    @property
    def direction(self) -> str:
        """方向：Mode 优先，其次 Signal（`GPIO_Output` / `GPXTIxx`）。

        **推不出就返回 ``"io"``** —— 不猜成输入或输出，否则方向冲突检查会造出
        假错误（把双向脚报成"配了 Output 却在读"）。
        """
        mode = self.mode.upper()
        if mode.endswith("OUTPUT") or mode == "OUTPUT":
            return "out"
        if mode.endswith("INPUT") or mode == "INPUT":
            return "in"
        signal = self.signal.upper()
        if signal in ("GPIO_OUTPUT", "GPIO_OUT"):
            return "out"
        if signal in ("GPIO_INPUT", "GPIO_IN") or signal.startswith("GPXT"):
            return "in"
        return "io"

    def to_dict(self) -> dict[str, Any]:
        return {"pin": self.pin, "port": self.port, "bit": self.bit,
                "mode": self.mode, "signal": self.signal, "label": self.label,
                "macro": self.macro}


def sanitize_label(text: str) -> str:
    """label → 宏名片段。`VL53-PWR-EN` → `VL53_PWR_EN`（`-`/空格/− 都换下划线）。"""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_")
    return cleaned.upper()


def parse_pins(path: str | Path) -> dict[str, IocPin]:
    """`.ioc` → ``{pin: IocPin}``。复用 `ioc_links.parse_ioc`（P8 的解析器）。

    只做**重组**，不重新解析文本：那个解析器已经处理了 mode/signal/label 三种行，
    再写一份迟早分叉（AGENTS §2.5）。
    """
    from dsh_embagent.design import ioc_links  # 延迟导入：避免 fwgen 依赖整个 design 包

    parsed = ioc_links.parse_ioc(path)
    modes = parsed.get("modes") or {}
    signals = parsed.get("signals") or {}
    labels = parsed.get("labels") or {}
    out: dict[str, IocPin] = {}
    for raw_pin in sorted(set(modes) | set(signals) | set(labels)):
        # CubeMX 会给复用脚加后缀：`PC13-ANTI_TAMP`、`PH0 - OSC_IN`。
        # 取前面的 P<端口><号>；取不出来（`VP_*` 虚拟脚）就跳过。
        match = re.match(r"^(P[A-K]\d{1,2})(?![0-9])", raw_pin.upper())
        if match is None:
            continue
        pin = match.group(1)
        port, bit = pin[1], int(pin[2:])
        label = str(labels.get(raw_pin) or labels.get(pin) or "").strip()
        out[pin] = IocPin(
            pin=pin, port=port, bit=bit,
            mode=str(modes.get(raw_pin) or modes.get(pin) or ""),
            signal=str(signals.get(raw_pin) or signals.get(pin) or ""),
            label=label,
            # 宏名优先用人给的名字；没有就留空，由 from_ioc 依次退到信号名、引脚名
            macro=sanitize_label(label) if label else "",
        )
    return out


def from_ioc(path: str | Path, *, ref: str = "U1",
             mcu: str = "") -> tuple[PinMap | None, dict[str, Any]]:
    """`.ioc` → `PinMap`。认不出 MCU 就返回 ``(None, 原因)``，**不猜型号**。

    返回 ``(pm, info)``；``info`` 里有 mcu 型号、板名、配置了几个脚、
    以及哪些脚因为表里没有这个 MCU 而只是"记录"未参与能力校验。
    """
    from dsh_embagent.design import ioc_links

    parsed = ioc_links.parse_ioc(path)
    mcu_text = str(parsed.get("mcu") or "")
    chosen = MCUS.get(mcu.lower()) if mcu else None
    if chosen is None:
        chosen = find_mcu(mcu_text)
    pins = parse_pins(path)
    info: dict[str, Any] = {
        "path": str(path),
        "board": parsed.get("board") or "",
        "mcu_text": mcu_text,
        "pins_total": len(pins),
        "configured": sorted(p.pin for p in pins.values() if p.configured),
        "with_label": sorted(p.pin for p in pins.values() if p.label),
        "mcu": chosen.name if chosen else "",
    }
    if chosen is None:
        info["reason"] = (f"认不出 MCU 型号 {mcu_text!r}（已知："
                          f"{', '.join(m.name for m in MCUS.values())}）")
        return None, info

    pm = PinMap(chosen, ref, [], source=f"ioc:{Path(path).name}")
    used: set[str] = set()
    reserved: list[str] = []
    for item in sorted(pins.values(), key=lambda p: (p.port, p.bit)):
        if not item.configured or item.pin in chosen.power_pins:
            continue
        if item.reserved_debug:
            reserved.append(item.pin)
        # 网名与宏名：label（人起的名字）→ 信号名 → 引脚名。
        # 顺序不能反：`USART2_TX` 比 `A2` 有用得多，而 A2 是引脚名不是功能名。
        net = item.label or item.signal or item.pin
        macro_base = item.macro or sanitize_label(item.signal) or sanitize_label(net) or item.pin
        macro, k = macro_base, 2
        while macro in used:
            macro, k = f"{macro_base}_{k}", k + 1
        used.add(macro)
        function, _direction, pull = classify(net)
        caps = chosen.functions.get(item.pin, ())
        axis = PinAssignment(
            net=net, macro=macro, ref=ref, pin=item.pin, pin_name=item.pin,
            function=function, direction=item.direction, pull_up=pull,
            capabilities=caps, port=item.port, bit=item.bit,
            number=(ord(item.port) - 65) * 16 + item.bit if chosen.family == "stm32" else None,
            adc_channel=chosen.adc.get(item.pin),
            # [hwv] HAL 要的是 GPIO_PIN_8 这种字面量，不是数字 8
            value=(f"GPIO_PIN_{item.bit}" if chosen.family == "stm32" else ""),
            mode=item.mode,
        )
        if not item.label and not item.signal:
            axis.warnings.append(f"{item.pin}: .ioc 里既无 label 也无 signal，网名用引脚名")
        if caps and function not in caps and function != "GPIO":
            axis.warnings.append(
                f"{net} 看起来是 {function}，但 {item.pin} 做不了（能：" 
                f"{', '.join(caps)}）")
        if item.pin in chosen.caution:
            axis.warnings.append(f"{item.pin}: {chosen.caution[item.pin]}")
        pm.assignments.append(axis)
    for axis in pm.assignments:
        pm.warnings.extend(axis.warnings)
    _check_peripherals(pm)
    info["assignments"] = len(pm.assignments)
    info["reserved_debug"] = reserved
    return pm, info


# ---------------------------------------------------------------------------
# [hwv] 固件侧的引脚使用抽取
# ---------------------------------------------------------------------------

#: 同一行里要同时出现端口与位号才算「成对使用」——只出现 `GPIO_PIN_5` 时不猜端口。
_PORT_RE = re.compile(r"\bGPIO([A-K])\b")
_BIT_RE = re.compile(r"\bGPIO_PIN_(\d{1,2})\b")

#: HAL 调用的语义。**只看函数名**，不做数据流分析——分析不了就如实报 unknown。
_API_KINDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bHAL_GPIO_ReadPin\b|\bLL_GPIO_IsInputPinSet\b"), "read"),
    (re.compile(r"\bHAL_GPIO_WritePin\b|\bHAL_GPIO_TogglePin\b|"
                r"\bLL_GPIO_SetOutputPin\b|\bLL_GPIO_ResetOutputPin\b"), "write"),
    (re.compile(r"\bHAL_GPIO_Init\b|\bLL_GPIO_Init\b|GPIO_InitTypeDef"), "config"),
    (re.compile(r"->\s*(?:ODR|BSRR|IDR|MODER|OTYPER|PUPDR)\b"), "register"),
)

#: 非 STM32 HAL 的家族特征。命中即 `not_ready`——**不硬猜**别的 SDK 的引脚写法。
_FOREIGN_SDK: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"#include\s*<avr/io\.h>|\bDDR[A-K]\b|\bPORT[A-K]\s*="), "AVR"),
    (re.compile(r"\bgpio_set_level\b|\besp_err_t\b|#include\s*\"driver/gpio\.h\""), "ESP-IDF"),
    (re.compile(r"\bpinMode\s*\(|\bdigitalWrite\s*\(|\bArduino\.h\b"), "Arduino"),
    (re.compile(r"\bGpioDataRegs\b|\bSysCtrlRegs\b"), "C2000"),
)


@dataclass
class PinUse:
    """固件里的一次引脚使用。``api`` 决定能不能判方向冲突。"""

    pin: str            # PA10
    api: str            # read | write | config | register | unknown
    file: str
    line: int
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"pin": self.pin, "api": self.api, "file": self.file,
                "line": self.line, "text": self.text[:120]}


def extract_uses(paths: Iterable[str | Path], *,
                 root: str | Path | None = None) -> dict[str, Any]:
    """扫固件源码，抽 ``GPIOx`` + ``GPIO_PIN_n`` 成对的用法。

    * 只抽**同一行**里同时出现端口与位号的组合。只出现一半时记进
      ``unpaired``（**不猜端口**）——猜错端口比不报更糟。
    * 命中非 STM32 家族特征 → ``status="not_ready"`` 并说明是哪个 SDK。
    """
    files = [Path(p) for p in paths]
    uses: list[PinUse] = []
    unpaired: list[PinUse] = []
    foreign: dict[str, str] = {}
    scanned = 0
    root_path = Path(root) if root else None
    for path in files:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        # `root` 只用来把路径显示得短一点，**不是约束**：`--src` 允许指到
        # 工程目录之外（用户的固件常常是另一个仓库）。第一版直接
        # `relative_to`，遇到外部路径抛 ValueError，整个核对挂掉。
        if root_path:
            try:
                label = str(path.relative_to(root_path))
            except ValueError:
                label = path.name
        else:
            label = path.name
        for index, line in enumerate(text.splitlines(), start=1):
            for pattern, sdk in _FOREIGN_SDK:
                if pattern.search(line) and sdk not in foreign:
                    foreign[sdk] = f"{label}:{index}"
            ports = _PORT_RE.findall(line)
            bits = _BIT_RE.findall(line)
            if not ports or not bits:
                if bits:
                    for bit in bits:
                        unpaired.append(PinUse(pin=f"?{bit}", api="unknown",
                                               file=label, line=index, text=line.strip()))
                continue
            api = "unknown"
            for pattern, kind in _API_KINDS:
                if pattern.search(line):
                    api = kind
                    break
            # 一行多个端口/位号时按顺序配对（HAL 的实参顺序就是这样）
            for port, bit in zip(ports, bits):
                uses.append(PinUse(pin=f"P{port}{bit}", api=api, file=label,
                                   line=index, text=line.strip()))
    result: dict[str, Any] = {
        "files": scanned,
        "uses": [u.to_dict() for u in uses],
        "unpaired": [u.to_dict() for u in unpaired[:20]],
        "unpaired_count": len(unpaired),
        "foreign_sdk": foreign,
        "status": "ok",
    }
    if foreign and not uses:
        result["status"] = "not_ready"
        result["reason"] = ("未支持的固件 SDK：" + "、".join(sorted(foreign))
                            + "（本层只认 STM32 HAL 形状；不猜别的写法）")
    elif not uses and not scanned:
        result["status"] = "not_ready"
        result["reason"] = "没有扫到任何源文件"
    return result


def crosscheck(pins: dict[str, IocPin] | PinMap, uses: list[dict[str, Any]],
               *, source: str = "") -> dict[str, Any]:
    """硬件配置 vs 固件使用。三类结论 + 双方引用。

    ``pins`` 可以是 `.ioc` 的 `IocPin` 字典（首选，有 mode）或 `PinMap`。
    返回 ``{status, ghost, idle, direction, counts, checked, basis}``。
    ``status`` 只有 ``ok`` / ``fail`` / ``not_ready``——
    幽灵引脚与方向冲突是 ``fail``，**不是提示**（运行时必炸或静默失效）。
    """
    if isinstance(pins, PinMap):
        configured = {a.pin_name: IocPin(pin=a.pin_name, port=a.port or "",
                                         bit=int(a.bit or 0), mode=a.mode,
                                         label=a.net, macro=a.macro,
                                         signal=a.function)
                      for a in pins.assignments}
        basis = pins.source or "pinmap"
    else:
        configured = {pin: item for pin, item in pins.items() if item.configured}
        basis = source or "ioc"

    if not configured:
        return {"status": "not_ready", "reason": "硬件侧没有可核对的引脚配置",
                "checked": 0, "basis": basis}

    used: dict[str, list[dict[str, Any]]] = {}
    for row in uses:
        used.setdefault(str(row.get("pin")), []).append(row)

    ghost: list[dict[str, Any]] = []
    direction: list[dict[str, Any]] = []
    for pin, rows in sorted(used.items()):
        item = configured.get(pin)
        if item is None:
            first = rows[0]
            ghost.append({
                "pin": pin,
                "firmware": f"{first.get('file')}:{first.get('line')}",
                "text": str(first.get("text") or "")[:100],
                "why": f"硬件 {basis} 里没有 {pin} 的配置",
            })
            continue
        mode_dir = item.direction
        for row in rows:
            api = str(row.get("api") or "")
            conflict = ""
            if mode_dir == "in" and api == "write":
                conflict = f"{pin} 在 {basis} 里配成 Input，固件却在写（{api}）"
            elif mode_dir == "out" and api == "read":
                conflict = f"{pin} 在 {basis} 里配成 Output，固件却在读（{api}）"
            if conflict:
                direction.append({
                    "pin": pin,
                    "firmware": f"{row.get('file')}:{row.get('line')}",
                    "mode": item.mode,
                    # 方向的依据：CubeMX 对普通 GPIO 输出只写 Signal 不写 Mode，
                    # 所以只报 `mode` 会让人以为"没依据"。
                    "mode_basis": item.mode or item.signal,
                    "api": api,
                    "why": conflict,
                })
                break

    idle = []
    for pin, item in sorted(configured.items()):
        if pin in used:
            continue
        # 调试脚不算闲置：SWD/JTAG 由调试器用，不该出现在应用代码里
        if item.reserved_debug:
            continue
        # 只报 GPIO 类；通信外设脚由模块自己的驱动代码用，不会直接出现在固件里
        if item.signal and not item.signal.upper().startswith(("GPIO", "GPXT")):
            continue
        idle.append({
            "pin": pin,
            "label": item.label,
            "mode": item.mode,
            "why": f"{basis} 配了 {pin}（{item.label or item.signal or '无标签'}），固件没用到",
        })

    counts = {"ghost": len(ghost), "idle": len(idle), "direction": len(direction)}
    status = "fail" if (ghost or direction) else "ok"
    return {
        "status": status,
        "counts": counts,
        "checked": len(configured),
        "used_pins": len(used),
        "ghost": ghost,
        "idle": idle,
        "direction": direction,
        "basis": basis,
        "note": "" if status == "ok" else "幽灵引脚/方向冲突是 error 级：运行时必炸或静默失效",
    }


# ---- headers -------------------------------------------------------------------------

_HEADER_NOTE = "由 dsh_embagent 从 {src} 生成。不要手改；改硬件后重新生成。"


def _entries(pm: PinMap) -> list[tuple[str, str, PinAssignment]]:
    """(macro, value expression, assignment) for every pin."""
    out = []
    for a in pm.assignments:
        if a.value:
            value = a.value          # [hwv] HAL 字面量（GPIO_PIN_8）原样输出
        elif a.number is not None:
            value = str(a.number)
        else:
            value = f'"{a.pin_name}"'
        out.append((f"PIN_{a.macro}", value, a))
    return out


def _aliases(pm: PinMap) -> list[tuple[str, str]]:
    """Peripheral aliases (PIN_I2C_SDA = PIN_<net>) and camera names for esp_camera."""
    aliases: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in pm.assignments:
        if a.function.startswith(("I2C_", "SPI_", "UART_")) and a.function not in seen:
            seen.add(a.function)
            aliases.append((f"PIN_{a.function}", f"PIN_{a.macro}"))
        if a.macro.startswith("CAM_"):
            aliases.append((f"CAM_PIN_{a.macro[4:]}", f"PIN_{a.macro}"))
    return aliases


def render_header(pm: PinMap, style: str = "c", source: str = "the netlist",
                  *, alias_prefix: str = "") -> str:
    """Render ``pins.h`` (style c), ``pins.hpp`` (cpp) or ``board_pins.rs`` (rust).

    ``alias_prefix`` 给第二套写法用（如 ``"HWV_"`` → ``HWV_PIN_x = GPIO_PIN_8``
    与 ``HWV_PORT_x = GPIOA``）。寄存器级的 ``PIN_x_PORT``（字符）保持不变，
    所以 `scaffold` 的 stm32 target 不需要改一行。
    """
    note = _HEADER_NOTE.format(src=source)
    entries = _entries(pm)
    aliases = _aliases(pm)
    if style == "rust":
        lines = [f"//! {note}", f"//! MCU {pm.mcu.name} ({pm.ref}); pin data: {pm.mcu.source}", ""]
        for macro, value, a in entries:
            lines.append(f"/// {a.net}: {pm.ref} pin {a.pin} = {a.pin_name}, {a.function}")
            if a.number is not None:
                lines.append(f"pub const {macro}: u8 = {value};")
            else:
                lines.append(f"pub const {macro}: &str = {value};")
            if a.port is not None:
                lines.append(f"pub const {macro}_PORT: char = '{a.port}';")
                lines.append(f"pub const {macro}_BIT: u8 = {a.bit};")
            if a.adc_channel is not None and a.function == "ADC":
                lines.append(f"pub const {macro}_ADC: u8 = {a.adc_channel};")
        for alias, target in aliases:
            lines.append(f"pub const {alias}: u8 = {target};")
        return "\n".join(lines) + "\n"
    cpp = style == "cpp"
    guard = "HWV_PINS_HPP" if cpp else "HWV_PINS_H"   # [hwv] 原实现用 TRACEFORGE_ 前缀
    lines = [f"/* {note} */", f"/* MCU {pm.mcu.name} ({pm.ref}); pin data: {pm.mcu.source} */"]
    if pm.source:
        lines.append(f"/* 来源：{pm.source} */")
    lines += [f"#ifndef {guard}", f"#define {guard}", ""]
    if pm.mcu.family == "avr":
        lines += ["#include <avr/io.h>", ""]
    for macro, value, a in entries:
        lines.append(f"/* {a.net}: {pm.ref} pin {a.pin} = {a.pin_name}, {a.function}"
                     + (f", mode={a.mode}" if a.mode else "") + " */")
        if cpp:
            lines.append(f"constexpr int {macro} = {value};")
        else:
            lines.append(f"#define {macro} {value}")
        extras: list[tuple[str, str]] = []
        if a.port is not None:
            extras.append((f"{macro}_BIT", str(a.bit)))
            if pm.mcu.family == "avr":
                extras += [
                    (f"{macro}_DDR", f"DDR{a.port}"),
                    (f"{macro}_PORT", f"PORT{a.port}"),
                    (f"{macro}_PINREG", f"PIN{a.port}"),
                ]
            else:
                extras += [(f"{macro}_PORT", f"'{a.port}'"), (f"{macro}_PIN", str(a.bit))]
        if a.adc_channel is not None and a.function == "ADC":
            extras.append((f"{macro}_ADC", str(a.adc_channel)))
        for name, val in extras:
            if cpp and val.lstrip("-").isdigit():
                lines.append(f"constexpr int {name} = {val};")
            else:
                lines.append(f"#define {name} {val}")
        # [hwv] HAL 那套写法：值直接是 GPIO_PIN_n，端口是 GPIOx
        if alias_prefix and a.port is not None and a.value:
            lines.append(f"#define {alias_prefix}{macro} {a.value}")
            guard_name = "GPIO" + a.port
            lines.append(f"#define {alias_prefix}PORT_{a.macro} {guard_name}")
    if aliases:
        lines.append("")
    for alias, target in aliases:
        lines.append(f"constexpr int {alias} = {target};" if cpp else f"#define {alias} {target}")
    lines += ["", f"#endif /* {guard} */", ""]
    return "\n".join(lines)


HEADER_FILES = {"c": "pins.h", "cpp": "pins.hpp", "rust": "board_pins.rs"}