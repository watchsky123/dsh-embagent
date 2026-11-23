"""CubeMX `.ioc` → **外设配置**（时钟 / 中断 / 复用功能 / 外设参数）。

## 这一层补什么

P50 的 `interface.json` 把四类东西一律记成 `unknown`，因为 `design.json`
只有"哪个脚接哪个网"，没有"这个脚配成什么外设、跑多快"：

| 项 | P50 的写法 | 现在（有 `.ioc`）|
|---|---|---|
| 时钟树 | `unknown` | `RCC.*` 的**原样引用**（HCLK/APB1/APB2/PLL 分频）|
| 中断/NVIC | `unknown` | `NVIC.<IRQ>_IRQn` 拆出抢占/子优先级 |
| 复用功能 | `unknown` | 每个脚的**功能名**（`PA9 = USART1_TX`）|
| 外设参数 | `unknown` | `<IP>.<参数>=值`（波特率/预分频/地址…）|

`.ioc` 是 CubeMX 写的 `key=value`，几百行里大部分是界面状态。本模块只认
**硬件配置**那几个前缀：`Mcu.` / `RCC.` / `NVIC.` / `P<端口><n>.`，以及
`Mcu.IPn` 列出来的外设。

## 三条纪律（每条都对应一个真实的坑）

1. **`.ioc` 只写非默认值。** 实测 7 个文件：`USART1.BaudRate` 只出现在 5 个里，
   值都是 115200；`I2C1.ClockSpeed` 一个都没有。所以"没写" ≠ "没配置"
   ≠ "默认值"。`<IP>.IPParameters` 记着 CubeMX 跟踪了哪些项——跟踪了但没值，
   记成 `tracked_not_written`，并**明确标注是推断**，不当事实用。
2. **不给 AF 编号。** `.ioc` 说"PA9 做 USART1_TX"，没说那是 AF7。编号在
   datasheet 的复用功能表里，本仓库没录 → `af_number` 一律缺失，写进 `unknown`。
3. **不做逐脚对账。** `.ioc` 用端口名（`PA9`），网表用封装脚号（`30`），
   中间缺一张 datasheet 的引脚定义表 → `join="unknown"`，**不编表**。

## 实测覆盖率（7 个真实文件，`check_p55` 会复核）

    文件                       引脚  外设  用户中断  时钟    封装
    F103-GPS.ioc                19    4      3     64 MHz  LQFP48
    F401-Bottom.ioc             25    8      7     72 MHz  LQFP64
    F401-Front.ioc              16    4      3     72 MHz  LQFP64
    F405-Joystick-Screen.ioc    36   13     10    168 MHz  LQFP64
    F405Joystick.ioc            35   11     12     96 MHz  LQFP64
    H743PilotX.ioc              63   22     17    480 MHz  LQFP176
    f103c6-car.ioc              14    5      4     64 MHz  LQFP48
                                ─────────────────────────────
                                208   67     56

（"外设"只数 `Mcu.IPn` 里的**用户外设**，不含 NVIC/RCC/SYS；"用户中断"不含
内核异常——`ioc_links` 文档里的 186 引脚是**只认严格 `P<端口><数字>` 形态**
的旧口径，这里 208 多出的 22 个是晶振/BOOT/SWD 这些带后缀的脚，见
`_PIN_KEY_RE` 的注释。）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import ioc_links

__all__ = ["parse", "describe", "classify_signal", "FAMILY_OF_IP"]

#: CubeMX 在 `.ioc` 里对 `:` `#` `=` `\` 空格做转义（`NVIC.X=true\:0\:0`）。
_ESCAPE_RE = re.compile(r"\\([\\:#= ])")

#: `PA9.Signal=USART1_TX` / `PB12.GPIO_Label=IN1`
#:
#: **端口名后面可能带后缀**：`PC13-ANTI_TAMP`、`PH0 - OSC_IN`、`PA0-WKUP`、
#: `PB3 (JTDO/TRACESWO)`。第一版只认 `^(P[A-Z]\d+)\.`，于是每个文件少 3–4 个脚
#: （实测 7 个文件：17→19、22→25、13→16、33→36、31→35、59→63、11→14）。
#: 少的恰好是**晶振脚与 SWD 脚**——它们当然也被配置过。
_PIN_KEY_RE = re.compile(
    r"^(P[A-Z]\d{1,2})(?!\d)[^.]*\.(Signal|Mode|GPIO_Label|GPIOParameters|Locked)$")

#: `Mcu.IP3=RCC`（外设清单，**权威**：比按前缀猜可靠）
_MCU_IP_RE = re.compile(r"^Mcu\.IP(\d+)$")

#: `Mcu.IPn` 里的内核/系统项 —— 它们不是"用户外设"，不参与外设分组。
SYSTEM_IPS = frozenset({"NVIC", "RCC", "SYS", "SH", "PCC"})

#: IP 名 → 协议族（USB_OTG_FS 这类没有数字的也在这里）
FAMILY_OF_IP = dict(ioc_links._FAMILY_OF_PERIPHERAL)

#: 时钟里我们真正关心的量。**只引用，不重算倍频链**——重算等于把 ST 的
#: 公式再实现一遍，错了还看不出来。
_CLOCK_HZ_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sysclk_hz", ("SYSCLKFreq_VALUE",)),
    ("hclk_hz", ("HCLKFreq_Value",)),
    ("ahb_hz", ("AHBFreq_Value", "AXIClockFreq_Value", "AHB12Freq_Value")),
    ("apb1_hz", ("APB1Freq_Value",)),
    ("apb2_hz", ("APB2Freq_Value",)),
    ("hse_hz", ("HSE_VALUE",)),
    ("hsi_hz", ("HSI_VALUE",)),
    ("lse_hz", ("LSE_VALUE",)),
)

#: 分频/倍频参数（F1/F4 是 PLLM/PLLN/PLLP/PLLQ，H7 是 DIVM1/DIVN1/DIVP1…）。
_CLOCK_DIVIDER_RE = re.compile(
    r"^(PLL[MPNQRS]\d?|DIV[MPNQRS]\d?|APB[12]CLKDivider|AHBCLKDivider|"
    r"HPRE|D2PPRE[12]|D1PPRE|D1CPRE)$")

#: NVIC 里的内核异常（和用户外设中断分开报，否则"12 个中断"会被误读）
_CORE_IRQS = frozenset({
    "BusFault", "DebugMonitor", "HardFault", "MemoryManagement",
    "NonMaskableInt", "PendSV", "SVCall", "SysTick", "UsageFault",
})

#: `_COMM_ROLES` 只管链路端点（tx/rx/sck…），USB 的 DM/DP 用它补上。
_ROLE_FALLBACK = {"DP": "dp", "DM": "dm", "CC": "cc"}


def _unescape(text: str) -> str:
    return _ESCAPE_RE.sub(r"\1", text)


def _pairs(text: str) -> list[tuple[str, str]]:
    """`.ioc` → `[(key, value), ...]`（保持原顺序；注释/空行跳过）。"""
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        rows.append((_unescape(key).strip(), _unescape(value).strip()))
    return rows


def _hz(value: str) -> int | None:
    """`72000000` → 72000000；不是纯数字就 None（**不猜**）。"""
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def classify_signal(signal: str) -> dict[str, Any]:
    """一个脚的 `Signal` → 它到底配成了什么。

    `.ioc` 的 `Signal` 值有几类（实测 7 个文件的全部取值）：

    | 值 | 例子 | kind |
    |---|---|---|
    | `GPIO_Output` / `GPIO_Input` | 53 / 12 次 | gpio |
    | `USARTn_TX` / `I2Cn_SCL` / `SPIn_SCK` … | 通信 | peripheral |
    | `ADCx_INn` | 8 次 | analog |
    | `S_TIMn_CHn` | 12 次 | timer |
    | `SYS_JTMS-SWDIO` / `SYS_JTCK-SWCLK` | 调试口 | debug |
    | `GPXTIn` | 外部中断 | gpio |

    认不出的原样进 `other` —— **不静默丢弃**（丢掉的东西看不见，
    覆盖率就成了假的）。
    """
    sig = str(signal or "").strip()
    if not sig:
        return {"kind": "none", "function": ""}
    if sig in ("GPIO_Output", "GPIO_Input") or re.match(r"^GPXTI\d*$", sig):
        return {"kind": "gpio", "function": sig}
    if sig.startswith("SYS_"):
        return {"kind": "debug", "function": sig}
    if sig.startswith("RCC_"):
        # `RCC_OSC_IN` / `RCC_MCO1`：晶振/时钟输出，**不是** GPIO
        return {"kind": "clock", "function": sig}
    if re.match(r"^ADC\w*_IN\d+$", sig):
        return {"kind": "analog", "function": sig}
    if re.match(r"^(S_)?TIM\d+_", sig):
        return {"kind": "timer", "function": sig}
    match = ioc_links._PERIPHERAL_SIGNAL_RE.match(sig)
    if match:
        peripheral, suffix = match.group(1), match.group(2)
        upper = suffix.upper()
        role = ioc_links._COMM_ROLES.get(upper) or _ROLE_FALLBACK.get(upper, "")
        return {"kind": "peripheral", "function": sig, "peripheral": peripheral,
                "family": ioc_links._family_of(peripheral), "role": role}
    return {"kind": "other", "function": sig}


def _clock(raw: dict[str, str]) -> dict[str, Any]:
    """`RCC.*` → 时钟事实。**只引用**已写出来的量，缺的进 `absent`。"""
    fields: dict[str, Any] = {}
    absent: list[str] = []
    for name, keys in _CLOCK_HZ_KEYS:
        value = next((raw[f"RCC.{k}"] for k in keys if f"RCC.{k}" in raw), None)
        hz = _hz(value) if value is not None else None
        if hz is None:
            absent.append(name)
        else:
            fields[name] = hz
    dividers: dict[str, Any] = {}
    for key, value in raw.items():
        if not key.startswith("RCC."):
            continue
        name = key.split(".", 1)[1]
        if _CLOCK_DIVIDER_RE.match(name):
            dividers[name] = int(value) if value.isdigit() else value
    for name in ("SYSCLKSource", "PLLSourceVirtual"):
        if f"RCC.{name}" in raw:
            fields["sysclk_source" if name == "SYSCLKSource" else "pll_source"] = \
                raw[f"RCC.{name}"]
        else:
            absent.append(name)
    status = ("declared" if fields.get("sysclk_hz") else
              "partial" if fields else "unknown")
    return {"status": status, "source": "ioc", **fields,
            "dividers": dividers, "absent": sorted(absent)}


def _nvic(raw: dict[str, str]) -> dict[str, Any]:
    """`NVIC.*` → 中断事实。内核异常与用户外设中断**分开报**。

    `.ioc` 的写法是 `NVIC.TIM3_IRQn=true\\:15\\:0\\:false\\:…`：
    第 0 项是"启用"，第 1 项是**抢占优先级**，第 2 项是**子优先级**。
    （`NVIC_PRIORITYGROUP_4` 时子优先级恒 0 —— 7 个真实文件全部符合，
    `check_p55` 把这条不变量当断言用。）
    """
    irqs: list[dict[str, Any]] = []
    core: list[dict[str, Any]] = []
    disabled: list[str] = []
    for key, value in raw.items():
        if not key.startswith("NVIC.") or not key.endswith("_IRQn"):
            continue
        name = key.split(".", 1)[1]
        parts = value.split(":")
        enabled = parts[0].strip().lower() == "true"
        preempt = int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else None
        sub = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else None
        row = {"irq": name, "preempt": preempt, "sub": sub}
        if not enabled:
            disabled.append(name)
            continue
        (core if name.removesuffix("_IRQn") in _CORE_IRQS else irqs).append(row)
    irqs.sort(key=lambda r: str(r["irq"]))
    core.sort(key=lambda r: str(r["irq"]))
    priority_group = raw.get("NVIC.PriorityGroup", "")
    status = "declared" if (irqs or priority_group) else "unknown"
    return {"status": status, "source": "ioc",
            "priority_group": priority_group,
            "irqs": irqs, "core": core, "disabled": sorted(disabled),
            "time_base": raw.get("NVIC.TimeBase", ""),
            "time_base_ip": raw.get("NVIC.TimeBaseIP", ""),
            "count": len(irqs)}


def _mcu(raw: dict[str, str]) -> dict[str, Any]:
    ips = sorted(((int(m.group(1)), value) for key, value in raw.items()
                  if (m := _MCU_IP_RE.match(key))),
                 key=lambda kv: kv[0])
    return {
        "user_name": raw.get("Mcu.UserName", ""),
        "cpn": raw.get("Mcu.CPN", ""),
        "name": raw.get("Mcu.Name", ""),
        "family": raw.get("Mcu.Family", ""),
        "package": raw.get("Mcu.Package", ""),
        "ip_count": len(ips),
        "ips": [value for _, value in ips],
        "device_id": raw.get("ProjectManager.DeviceId", ""),
        "project": raw.get("ProjectManager.ProjectName", ""),
    }


def _peripherals(raw: dict[str, str], pins: dict[str, dict[str, Any]],
                 mcu: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """按 `Mcu.IPn` 的清单逐个外设收参数 + 引脚（**不按前缀猜**）。

    `Mcu.IPn` 含 NVIC/RCC/SYS 这些系统项（进 `SYSTEM_IPS`，不算外设）。
    """
    out: dict[str, dict[str, Any]] = {}
    for name in mcu["ips"]:
        if name in SYSTEM_IPS:
            continue
        prefix = f"{name}."
        params: dict[str, str] = {}
        tracked: list[str] = []
        for key, value in raw.items():
            if not key.startswith(prefix):
                continue
            param = key.split(".", 1)[1]
            if param == "IPParameters":
                tracked = [item for item in value.split(",") if item]
                continue
            params[param] = value
        pins_of = {pin: row["signal"] for pin, row in pins.items()
                   if row.get("peripheral") == name}
        family = ioc_links._family_of(name) if name.upper().startswith(
            tuple(FAMILY_OF_IP)) else ""
        out[name] = {
            "family": family,
            "kind": ("comm" if family else "other"),
            "mode": params.get("VirtualMode", ""),
            "params": params,
            "params_tracked": sorted(tracked),
            # 跟踪了但没写值 = 推断"用了默认值"，**不是事实**
            "tracked_not_written": sorted(set(tracked) - set(params)),
            "pins": pins_of,
        }
    return out


def _pins(raw: dict[str, str]) -> dict[str, dict[str, Any]]:
    """每个被配置过的脚 → 功能/模式/label。"""
    rows: dict[str, dict[str, Any]] = {}
    raw_names: dict[str, str] = {}
    for key, value in raw.items():
        match = _PIN_KEY_RE.match(key)
        if match is None:
            continue
        pin, field = match.group(1), match.group(2)
        rows.setdefault(pin, {})[field] = value
        raw_names.setdefault(pin, key.split(".", 1)[0])
    out: dict[str, dict[str, Any]] = {}
    for pin, fields in sorted(rows.items()):
        signal = fields.get("Signal", "")
        info = classify_signal(signal)
        mode = fields.get("Mode", "")
        # 纯 GPIO 在 `.ioc` 里没有 `Mode` 行（实测：只有 6 种模式值，
        # 没有 Input/Output/Analog）—— 于是从 `Signal` 反推，并标出来源。
        if not mode and signal in ("GPIO_Output", "GPIO_Input"):
            mode = signal.replace("GPIO_", "")
            mode_from = "signal"
        else:
            mode_from = "ioc" if mode else ""
        out[pin] = {
            "pin": pin, "signal": signal, "function": info.get("function", ""),
            "kind": info.get("kind", "none"),
            "peripheral": info.get("peripheral", ""),
            "family": info.get("family", ""), "role": info.get("role", ""),
            "mode": mode, "mode_source": mode_from,
            "name_raw": raw_names.get(pin, pin),
            "label": fields.get("GPIO_Label", ""),
            "locked": fields.get("Locked", "").lower() == "true",
        }
    return out


def parse(path: str | Path) -> dict[str, Any]:
    """读一个 `.ioc`，返回**确定性事实**（不认识的东西进 `other`，不猜）。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    raw = dict(_pairs(text))          # 同键重复时 CubeMX 语义是"后者生效"
    mcu = _mcu(raw)
    pins = _pins(raw)
    peripherals = _peripherals(raw, pins, mcu)
    clock = _clock(raw)
    nvic = _nvic(raw)

    af: dict[str, Any] = {
        "source": "ioc", "join": "unknown", "af_number": "unknown",
        "functions": {pin: row["function"] for pin, row in pins.items()
                      if row["function"]},
        "peripheral_pins": {pin: {"peripheral": row["peripheral"],
                                  "family": row["family"], "role": row["role"]}
                            for pin, row in pins.items() if row["peripheral"]},
        "note": ("`.ioc` 给的是**功能名**（PA9=USART1_TX）；AF **编号**"
                 "（AF7/AF8）在 datasheet 的复用表里，本仓库没录"),
    }
    counts = {
        "pins": len(pins),
        "peripherals": len(peripherals),
        "comm_peripherals": sum(1 for row in peripherals.values()
                                if row["kind"] == "comm"),
        "irqs": nvic["count"],
        "core_irqs": len(nvic["core"]),
        "clock_fields": len([k for k in clock if k.endswith("_hz")]),
    }
    return {
        "path": str(path), "file": path.name, "board": path.stem,
        "mcu": mcu, "clock": clock, "nvic": nvic,
        "peripherals": peripherals, "pins": pins, "af": af,
        "counts": counts,
        "absent": [
            {"what": f"时钟量 {name}", "why": "RCC 里没有这一行（.ioc 只写非默认值）"}
            for name in clock["absent"]
        ] + [
            {"what": "AF 编号", "why": "datasheet 的复用功能表未录入"}
        ] + [
            {"what": "封装脚号 ↔ 端口名", "why": "datasheet 的引脚定义表未录入"}
        ],
        "note": ("只引用 .ioc 写出来的量；没写的当'未知'，不当默认值；"
                 "AF 编号与脚号对照一律 unknown"),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.ioc",
        "input": "CubeMX .ioc（key=value）",
        "extracts": ["芯片身份（Family/Package/UserName/CPN）", "时钟（RCC.*）",
                     "中断（NVIC.*，抢占/子优先级）", "复用功能（Signal=功能名）",
                     "外设参数（<IP>.*，只收写出来的）"],
        "always_unknown": ["AF 编号（datasheet 表）", "时序约束（datasheet）",
                           "封装脚号 ↔ 端口名（datasheet 表）"],
        "discipline": ("`.ioc` 只写非默认值 → 没写不算默认值；"
                       "tracked_not_written 标注为推断；"
                       "不重算倍频链、不给 AF 编号、不编引脚对照表"),
    }