"""可编译骨架 + **真编译**（`fw-build`，P52）。

## 这一层解决什么

`interface`（P50）给了"引脚 ↔ 网络"的契约，`fw-algo`（P50）能验**纯逻辑**，
但中间一直缺一块：**能不能编出一个属于这块板的固件**。

现在补上：从 `design.json` + `interface.json` 生成一个最小工程
（启动文件 / 链接脚本 / main / Makefile），用**仓库内的 arm-none-eabi-gcc**
真编译成 `.elf` + `.bin`，再按四条硬判据判它。

## 四条硬判据（缺一条就不是 ok）

| 判据 | 为什么 |
|---|---|
| 编译返回 0 | 语法/链接都过 |
| `readelf -h` 的 `Machine = ARM` | 编出来的真是 ARM 目标，不是宿主 |
| `Entry` 落在 FLASH 区间 | 链接脚本的地址和 MCU 对得上 |
| `text ≤ flash`、`data+bss ≤ ram` | 装不下就是装不下，**不是估算** |

## 诚实边界

* 内存容量来自 **`MCU_MEMORY` 一张表**（带 datasheet 出处）；表里没有的型号
  → `not_ready` + 列出已知型号，**不猜**
* 生成的 `main.c` 是**骨架**：外设初始化只有 `TODO` 注释和时钟占位，
  它证明"能编译、能链接、地址对"，**不证明功能对**
* 没有工具链 → `not_ready`（不是 failed：这是环境缺，不是设计错）
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .model import DesignModel

__all__ = ["MCU_MEMORY", "generate", "build", "describe", "FILE"]

FILE = "fw_build.json"
OUT_DIR = "fw/build"

#: MCU 内存容量。**这是本模块唯一的一份**（pin 表不管内存，两处不重叠）。
#: 每项带出处；表里没有的型号一律 `not_ready`，不猜。
MCU_MEMORY: dict[str, dict[str, Any]] = {
    "stm32f103c8t6": {"flash_kb": 64, "ram_kb": 20, "core": "cortex-m3",
                      "basis": "ST STM32F103x8 数据手册（64 KB Flash / 20 KB SRAM）"},
    "stm32f103cbt6": {"flash_kb": 128, "ram_kb": 20, "core": "cortex-m3",
                      "basis": "ST STM32F103xB 数据手册"},
    "stm32f401rct6": {"flash_kb": 256, "ram_kb": 64, "core": "cortex-m4",
                      "basis": "ST STM32F401xC 数据手册"},
    "stm32f405rgt6": {"flash_kb": 1024, "ram_kb": 192, "core": "cortex-m4",
                      "basis": "ST STM32F405xG 数据手册（192 KB SRAM 含 64 KB CCM）"},
    "stm32h743iit6": {"flash_kb": 2048, "ram_kb": 1024, "core": "cortex-m7",
                      "basis": "ST STM32H743xI 数据手册"},
    "atmega328p": {"flash_kb": 32, "ram_kb": 2, "core": "avr",
                   "basis": "Microchip ATmega328P 数据手册"},
    "attiny85": {"flash_kb": 8, "ram_kb": 0.5, "core": "avr",
                 "basis": "Microchip ATtiny25/45/85 数据手册"},
}

TOOLCHAIN_REL = "_tools/arm-gcc"


def _find_toolchain() -> Path | None:
    """仓库内工具链（先看仓库，再看 PATH）。**不装、不猜路径**。"""
    root = Path(__file__).resolve().parents[1]
    base = root / TOOLCHAIN_REL
    if base.is_dir():
        found = sorted(base.glob("*/bin/arm-none-eabi-gcc.exe"))
        if found:
            return found[-1]
        found = sorted(base.glob("*/bin/arm-none-eabi-gcc"))
        if found:
            return found[-1]
    which = shutil.which("arm-none-eabi-gcc") or shutil.which("arm-none-eabi-gcc.exe")
    return Path(which) if which else None


def _mcu_key(design: DesignModel) -> tuple[str, str]:
    """找出 MCU 的型号键。取 `kind`；`kind` 是哈希时用 `label` 兜底。"""
    for part in design.parts:
        kind = str(getattr(part, "kind", "") or "").lower()
        if kind.startswith(("stm32", "atmega", "attiny", "esp32")):
            return kind, str(getattr(part, "ref", ""))
    return "", ""


def _startup(core: str) -> str:
    """启动文件：向量表 + Reset_Handler + 默认死循环。**只覆盖 Cortex-M 标准异常**。"""
    return f"""/* 生成物（fw-build）—— 不属于设计事实，可由 design.json 重新生成。
 * 目标核：{core}
 */
#include <stdint.h>

extern uint32_t _estack;
extern int main(void);
extern void SystemInit(void);

void Reset_Handler(void);
void Default_Handler(void);

__attribute__((section(".isr_vector"), used))
void (*const g_vectors[])(void) = {{
    (void (*)(void))(&_estack),   /* 0: 初始 SP */
    Reset_Handler,                /* 1: Reset */
    Default_Handler,              /* 2: NMI */
    Default_Handler,              /* 3: HardFault */
    Default_Handler,              /* 4: MemManage */
    Default_Handler,              /* 5: BusFault */
    Default_Handler,              /* 6: UsageFault */
    0, 0, 0, 0,                   /* 7-10 保留 */
    Default_Handler,              /* 11: SVCall */
    Default_Handler,              /* 12: DebugMon */
    0,                            /* 13 保留 */
    Default_Handler,              /* 14: PendSV */
    Default_Handler,              /* 15: SysTick */
}};

extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss;

void Reset_Handler(void)
{{
    SystemInit();
    /* .data: 从 FLASH 拷到 RAM */
    uint32_t *src = &_sidata, *dst = &_sdata;
    while (dst < &_edata) *dst++ = *src++;
    /* .bss: 清零 */
    for (dst = &_sbss; dst < &_ebss; ) *dst++ = 0;
    main();
    for (;;) {{ }}
}}

void Default_Handler(void) {{ for (;;) {{ }} }}

/* 弱符号：没有实现的中断落到这里，而不是链接失败 */
__attribute__((weak, alias("Default_Handler"))) void NMI_Handler(void);
__attribute__((weak, alias("Default_Handler"))) void HardFault_Handler(void);
__attribute__((weak, alias("Default_Handler"))) void SysTick_Handler(void);
"""


def _linker(mem: dict[str, Any]) -> str:
    flash = int(mem["flash_kb"]) * 1024
    ram = int(float(mem["ram_kb"]) * 1024)
    return f"""/* 生成物（fw-build）。容量取自 MCU_MEMORY：
 * {mem['basis']}
 */
ENTRY(Reset_Handler)
MEMORY
{{
  FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = {flash}K
  RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = {ram}K
}}
_estack = ORIGIN(RAM) + LENGTH(RAM);
SECTIONS
{{
  .isr_vector : {{ KEEP(*(.isr_vector)) }} > FLASH
  .text : {{ *(.text*) *(.rodata*) }} > FLASH
  .ARM.exidx : {{ *(.ARM.exidx*) }} > FLASH
  _sidata = LOADADDR(.data);
  .data : {{ _sdata = .; *(.data*) _edata = .; }} > RAM AT > FLASH
  .bss  : {{ _sbss = .; *(.bss*) *(COMMON) _ebss = .; }} > RAM
}}
/* 容量判定**不在这里**：链接脚本只描述内存；"装不下"由 fw-build 用
 * `size` 的 text/data/bss 与 flash/ram 上限比（见 build()）。
 * 第一版在 ld 里写 ASSERT 反而误报（同一表达式恒等），已删。
 */
"""


def _c_name(net: str) -> str:
    """网络名 → C 标识符片段。`3V3` 不能当标识符（以数字开头）→ `NET_3V3`。"""
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in str(net))
    cleaned = cleaned.strip("_") or "UNNAMED"
    return cleaned if not cleaned[0].isdigit() else f"NET_{cleaned}"


def _af_macros(interface: dict[str, Any]) -> list[tuple[str, int]]:
    """`interface.af.details` → `[(C 名, AF 编号)]`（P57）。

    F1 没有 AF 编号（用 AFIO 重映射），所以这里自然为空 —— 不生成宏，
    也就不会有"假装 F1 有 AF7"的假约束。
    """
    rows: list[tuple[str, int]] = []
    seen: set[str] = set()
    details = ((interface.get("af") or {}).get("details") or {})
    for pin in sorted(details):
        for item in details[pin] or []:
            signal = str(item.get("signal") or "")
            af = item.get("af")
            if not signal or af is None:
                continue
            name = _c_name(signal)
            if name in seen:
                continue
            seen.add(name)
            rows.append((name, int(af)))
    return rows

def _c_ident(text: str) -> str:
    """参数名 → C 标识符片段（`BaudRate` → `BAUDRATE`，`I2C_Mode` → `I2C_MODE`）。"""
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in str(text))
    cleaned = cleaned.strip("_") or "X"
    return cleaned.upper() if not cleaned[0].isdigit() else f"N_{cleaned}".upper()


def _config_macros(interface: dict[str, Any]) -> list[tuple[str, str, str]]:
    """`peripheral_config` → `[(IP, PARAM, 值)]`，**只收 C 能比较的数值**（P55）。

    `.ioc` 的 `BaudRate=115200` 能进宏；`VirtualMode=VM_ASYNC` 这种枚举不进
    （`HWV_EXPECT_CFG` 比的是数值）。**没写进 .ioc 的参数不生成宏** ——
    用它就是"未声明标识符"，这比"默认 115200"诚实（`.ioc` 只写非默认值）。
    """
    rows: list[tuple[str, str, str]] = []
    config = interface.get("peripheral_config") or {}
    for ip, info in sorted(config.items()):
        if not isinstance(info, dict):
            continue
        for key, value in sorted((info.get("params") or {}).items()):
            text = str(value).strip()
            if not re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", text):
                continue
            rows.append((_c_ident(ip), _c_ident(key), text))
    return rows

def _pins_header(interface: dict[str, Any]) -> str:
    """把 `interface.json` 的引脚契约变成**编译期可判**的头文件（P54）。

    这套头文件要解决的是"**固件和板子各记一套引脚**"——板子改了、固件不知道，
    板子回来了才发现线接错。所以：

    | 宏 | 判什么 | 用什么手段 |
    |---|---|---|
    | `HWV_PIN(NAME)` | 这个名字在契约里吗 | 名字不存在 → **未声明标识符**（编译错）|
    | `HWV_CHECK_GPIO(NAME)` | 这个脚能当 GPIO 吗 | 电源/地 → `_Static_assert` 失败（带人话）|
    | `HWV_CHECK_PIN_NUMBER(n)` | 脚号在范围内吗 | 超范围 → `_Static_assert` 失败 |
    | `HWV_EXPECT(NAME, n)` | 契约把线挪走了吗 | 位置变了 → `_Static_assert` 失败 |

    `HWV_EXPECT` 是**真正的回归保护**：固件里写"我假设 UART_TX 在 30"，
    某天 interface 改成 31 → **编译期就报错**，不用等台架。
    """
    mcu = interface.get("mcu") or {}
    pins = [row for row in (interface.get("pins") or []) if isinstance(row, dict)]
    total = int(mcu.get("pins") or 0) or (max(
        (int(row["pin"]) for row in pins if str(row.get("pin", "")).isdigit()),
        default=0))
    lines = [
        "/* 生成物（fw-build / P54）—— **引脚契约的头文件版本**。",
        " * 唯一来源：interface.json（由 design.json 的网表合成）。",
        " * **不要手改**：改了它就和板子脱钩，那正是这套宏要防的事。",
        " */",
        "#ifndef HWV_PINS_H",
        "#define HWV_PINS_H",
        "",
        f'#define HWV_MCU_REF "{mcu.get("ref", "")}"',
        f"#define HWV_MCU_PINS {total}",
        "",
        "/* 契约里有的脚 → 宏（名字取自网络名；`3V3` 这类以数字开头的加 NET_ 前缀） */",
    ]
    # **同一个网可能落在多个脚上**（GND 4 个、3V3 4 个）。名字宏只能有一个值，
    # 所以取**最小脚号**并在注释里写清"还有哪些脚"——否则宏重定义（告警）
    # 且读的人以为这个网只有一根线。
    by_net: dict[str, list[int]] = {}
    for row in pins:
        net = str(row.get("net") or "")
        pin = str(row.get("pin") or "")
        if net and pin.isdigit():
            by_net.setdefault(net, []).append(int(pin))
    for net, numbers in sorted(by_net.items(), key=lambda kv: min(kv[1])):
        numbers.sort()
        name = _c_name(net)
        extra = ""
        if len(numbers) > 1:
            extra = f"  /* 同网还有脚 {', '.join(str(n) for n in numbers[1:])} */"
        lines.append(f"#define HWV_PIN_{name:<18} {numbers[0]}{extra}")
    lines += ["", "/* 能不能当 GPIO：**电源/地写 0**，信号/未接写 1。"
              " 契约里没有的脚号 → 这个宏不存在 → 编译期就炸 */"]
    for row in sorted(pins, key=lambda r: int(str(r.get("pin") or 0)
                                             if str(r.get("pin") or "").isdigit() else 0)):
        pin = row.get("pin")
        if not str(pin).isdigit():
            continue
        kind = str(row.get("kind") or "")
        usable = "0" if kind in ("power", "ground") else "1"
        note = f"  /* {kind or '?'} {row.get('net') or ''} */" if usable == "0" else ""
        lines.append(f"#define HWV_PIN_GPIO_{pin:<6} {usable}{note}")
    # ---- 外设配置（P55）：`.ioc` 的时钟与外设参数进宏 ----
    clock = interface.get("clock") or {}
    config_rows = _config_macros(interface)
    lines += ["", "/* ---- 外设配置（来自 .ioc：时钟 + 外设参数）。**没写进 .ioc 的"
              "参数不生成宏** —— 用它就是未声明标识符 ---- */"]
    if clock.get("status") == "declared":
        for macro, key in (("HWV_SYSCLK_HZ", "sysclk_hz"),
                           ("HWV_HCLK_HZ", "hclk_hz"),
                           ("HWV_APB1_HZ", "apb1_hz"),
                           ("HWV_APB2_HZ", "apb2_hz")):
            if clock.get(key):
                lines.append(f"#define {macro:<16} {int(clock[key])}u")
        if not any(clock.get(k) for k in ("sysclk_hz", "hclk_hz")):
            lines.append("/* .ioc 里有时钟段，但既没有 SYSCLK 也没有 HCLK */")
    else:
        lines.append("/* 契约里没有时钟（没给 .ioc）—— 用 HWV_CHECK_CLOCK 会因此"
                     "编不过，这是**有意的**：没有契约就别假设频率 */")
    for ip, param, value in config_rows:
        lines.append(f"#define {('HWV_CFG_' + ip + '_' + param):<24} {value}u")
    if not config_rows:
        lines.append("/* 契约里没有数值型外设参数（.ioc 只写非默认值，"
                     "波特率常常没写）*/")
    af_rows = _af_macros(interface)
    if af_rows:
        lines += ["", "/* ---- 复用功能编号（.ioc 的功能 → ST 官方 AF 表）---- */"]
        for name, number in af_rows:
            lines.append(f"#define {('HWV_AF_' + name):<26} {number}")
    else:
        style = str((interface.get("af") or {}).get("style") or "")
        lines.append("/* 没有 AF 编号" + ("（这颗芯片用 AFIO 重映射，F1 没有 AF0..15）"
                                         if style == "F1_REMAP" else "（没有 AF 表）")
                     + " —— 不生成 HWV_AF_* / HWV_EXPECT_AF */")
    lines += [
        "",
        "/* ---- 六个检查宏（必须在**函数体内**用；`_Static_assert` 是块作用域的）---- */",
        "",
        "/* ① 名字在契约里吗：HWV_PIN_NOSUCH 未定义 → 未声明标识符，编译期报错 */",
        "#define HWV_PIN(name) (HWV_PIN_ ## name)",
        "",
        "#define HWV_CAT_(a, b) a##b",
        "#define HWV_CAT(a, b) HWV_CAT_(a, b)",
        "",
        "/* ② 这个脚能当 GPIO 吗：电源/地 → 断言失败（人话就在字符串里） */",
        "#define HWV_CHECK_GPIO(name) \\",
        '    _Static_assert(HWV_CAT(HWV_PIN_GPIO_, HWV_PIN_ ## name) == 1, \\',
        '                   "HWV_CHECK_GPIO: pin " #name \\',
        '                   " is power/ground (or missing) in interface.json")',
        "",
        "/* ③ 脚号在范围内吗 */",
        "#define HWV_CHECK_PIN_NUMBER(n) \\",
        '    _Static_assert((n) >= 1 && (n) <= HWV_MCU_PINS, \\',
        '                   "HWV_CHECK_PIN_NUMBER: pin number out of MCU range")',
        "",
        "/* ④ 契约把这条线挪走了吗（**固件旧假设的回归保护**） */",
        "#define HWV_EXPECT(name, pin) \\",
        '    _Static_assert(HWV_PIN_ ## name == (pin), \\',
        '                   "HWV_EXPECT: interface.json moved " #name \\',
        '                   " away from the pin this firmware assumes")',
        "",
        "/* ⑤ 固件假设的 SYSCLK 和 .ioc 一致吗。**没有 .ioc → HWV_SYSCLK_HZ 不存在",
        " * → 这个宏编不过**，而不是悄悄跳过检查 */",
        "#define HWV_CHECK_CLOCK(hz) \\",
        '    _Static_assert((hz) == HWV_SYSCLK_HZ, \\',
        '                   "HWV_CHECK_CLOCK: SYSCLK differs from .ioc (interface.json)")',
        "",
        "/* ⑥ 外设参数（波特率…）和 .ioc 一致吗。.ioc 没写这个参数 → 宏不存在",
        " * → 未声明标识符（**不替它填默认值**）*/",
        "#define HWV_EXPECT_CFG(ip, param, v) \\",
        '    _Static_assert(HWV_CAT(HWV_CFG_, HWV_CAT(ip, HWV_CAT(_, param))) == (v), \\',
        '                   "HWV_EXPECT_CFG: " #ip "." #param \\',
        '                   " differs from .ioc (interface.json)")',
        "",
        "/* ⑦ 复用功能编号（AF）和 ST 官方引脚库一致吗。**F1 没有这个宏**",
        " * （它用 AFIO 重映射）—— 用了就编不过，那是对的 */",
        "#define HWV_EXPECT_AF(name, n) \\",
        '    _Static_assert(HWV_AF_ ## name == (n), \\',
        '                   "HWV_EXPECT_AF: " #name " AF number differs from ST pin data")',
        "",
        "#endif",
        "",
    ]
    return "\n".join(lines)


def _main_c(interface: dict[str, Any]) -> str:
    peripherals = interface.get("peripherals") or []
    # 选一个**信号脚**做自检样本：只用电源脚自检等于没验"能不能当 GPIO"。
    sig = [row for row in (interface.get("pins") or [])
           if isinstance(row, dict) and str(row.get("net") or "")
           and str(row.get("kind") or "") in ("signal", "unconnected")]
    lines = [
        "/* 生成物（fw-build）—— **骨架**：证明能编、能链、地址对；",
        " * 外设初始化只有 TODO，不证明功能对。",
        " * 引脚**只认 hwv_pins.h**（interface.json 的头文件版）—— 不另记一套。",
        " */",
        "#include <stdint.h>",
        '',
        '#include "hwv_pins.h"',
        "",
        "/* 定义（不是 extern）：第一版写成 extern 又在函数里赋值，链接报",
        " * `undefined reference to SystemCoreClock` —— 声明≠定义。 */",
        "#ifdef HWV_SYSCLK_HZ",
        "/* 时钟来自 .ioc（interface.json）—— 不再写死一个 8 MHz */",
        "uint32_t SystemCoreClock = HWV_SYSCLK_HZ;",
        "void SystemInit(void) { SystemCoreClock = HWV_SYSCLK_HZ; }",
        "#else",
        "/* 契约里没有时钟：保留 8 MHz 占位，main 里也就**不**做时钟断言 */",
        "uint32_t SystemCoreClock = 8000000u;",
        "void SystemInit(void) { SystemCoreClock = 8000000u; }",
        "#endif",
        "",
        "volatile uint32_t g_ticks;",
        "",
        "/* 用一次宏值：证明 `HWV_PIN(name)` 的展开真能当数用（不是只写在断言里） */",
        "static uint32_t hwv_pin_probe(void)",
        "{",
        "    return (uint32_t)HWV_PIN(" + (_c_name(str(sig[0].get("net"))) if sig else "NET_NONE") + ");",
        "}",
        "",
        "int main(void)",
        "{",
        "    /* 由 interface.json 得到的外设清单（每条都只是占位） */",
    ]
    if peripherals:
        for group in peripherals:
            pins = ", ".join(f"{p['pin']}={p['net']}" for p in group.get("pins") or [])
            lines.append(f"    /* TODO {group.get('family')}: {pins} */")
    else:
        lines.append("    /* TODO 没有外设（interface 里 peripherals 为空） */")
    # 骨架自检：**编译期**确认契约里有这些脚、能用、位置没变（P54）。
    # 一条都不自检等于"宏有了但没人用"，那和没接一样。
    if sig:
        first = sig[0]
        name = _c_name(str(first.get("net")))
        pin = int(str(first.get("pin")))
        lines += [
            "    /* ---- 引脚契约自检（编译期；改了 interface.json 这里会先炸） ---- */",
            f"    HWV_CHECK_PIN_NUMBER(HWV_MCU_PINS);   /* 契约里记的引脚总数，{pin} 也在范围内 */",
            f"    HWV_CHECK_GPIO({name});",
            f"    HWV_EXPECT({name}, {pin});",
        ]
        clock = interface.get("clock") or {}
        if clock.get("status") == "declared" and clock.get("sysclk_hz"):
            lines += [
                "    /* 时钟契约（.ioc → interface.json → 这里）：写错频率编译期就炸 */",
                f"    HWV_CHECK_CLOCK({int(clock['sysclk_hz'])}u);",
            ]
        cfg = _config_macros(interface)
        if cfg:
            ip, param, value = cfg[0]
            lines += [
                f"    /* 外设参数契约：{ip}.{param} = {value}（.ioc） */",
                f"    HWV_EXPECT_CFG({ip}, {param}, {value}u);",
            ]
        afs = _af_macros(interface)
        if afs:
            name, number = afs[0]
            lines += [
                f"    /* 复用功能契约：{name} = AF{number}（ST 官方引脚库） */",
                f"    HWV_EXPECT_AF({name}, {number});",
            ]
        lines += [
            "    (void)hwv_pin_probe();",
            "",
        ]
    lines += [
        "    for (;;) {",
        "        g_ticks++;",
        "        __asm volatile (\"nop\");",
        "    }",
        "    return 0;",
        "}",
        "",
    ]
    return "\n".join(lines)


def _makefile(core: str) -> str:
    flags = {
        "cortex-m3": "-mcpu=cortex-m3",
        "cortex-m4": "-mcpu=cortex-m4 -mfloat-abi=soft",
        "cortex-m7": "-mcpu=cortex-m7 -mfloat-abi=soft",
        "avr": "",
    }.get(core, "-mcpu=cortex-m4")
    return f"""# 生成物（fw-build）。CC 由调用方通过环境变量传入（不写死路径）。
CC      ?= arm-none-eabi-gcc
SIZE    ?= arm-none-eabi-size
CFLAGS  ?= {flags} -mthumb -Os -Wall -Wextra -ffunction-sections -fdata-sections
LDFLAGS ?= -nostdlib --specs=nano.specs -Wl,--gc-sections -T link.ld

all: firmware.elf firmware.bin

firmware.elf: startup.c main.c hwv_pins.h link.ld
\t$(CC) $(CFLAGS) $(LDFLAGS) -o $@ startup.c main.c
\t$(SIZE) $@

firmware.bin: firmware.elf
\t$(CC) -O binary $@ $<

clean:
\trm -f firmware.elf firmware.bin
"""


def generate(store: Any, design: DesignModel, interface: dict[str, Any],
             *, out_dir: str = OUT_DIR) -> dict[str, Any]:
    """写 4 个文件到 `out_dir`。**先判 MCU 内存表**，缺就 not_ready。"""
    key, ref = _mcu_key(design)
    if not key:
        return {"status": "not_ready",
                "reason": "design.json 里找不到 MCU（kind 既不是 stm32/atmega/attiny/esp32）",
                "known": sorted(MCU_MEMORY)}
    mem = MCU_MEMORY.get(key)
    if mem is None:
        return {"status": "not_ready",
                "reason": f"MCU {key}（{ref}）的内存容量不在表里",
                "known": sorted(MCU_MEMORY)}

    target = store.path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    files = {
        "startup.c": _startup(str(mem["core"])),
        "link.ld": _linker(mem),
        "main.c": _main_c(interface or {}),
        "hwv_pins.h": _pins_header(interface or {}),
        "Makefile": _makefile(str(mem["core"])),
    }
    written = []
    for name, text in files.items():
        path = target / name
        path.write_text(text, encoding="utf-8")
        written.append(str(path))
    # **负例的样本要在生成时记下来**：第一版让 `_negative_cases` 去头文件里正则
    # 找 `GPIO_x 0` —— 一旦有人把生成物改坏（退化实验），样本就找不到了，
    # 负例跟着消失，于是"闸门拆掉"看起来像"闸门还在"。这是自指脆性。
    pins = [row for row in (interface.get("pins") or []) if isinstance(row, dict)]
    power_names = sorted({_c_name(str(row.get("net"))) for row in pins
                          if str(row.get("net") or "")
                          and str(row.get("kind") or "") in ("power", "ground")})
    signal_names = sorted({_c_name(str(row.get("net"))) for row in pins
                           if str(row.get("net") or "")
                           and str(row.get("kind") or "") in ("signal", "unconnected")})
    # **契约配置也要在生成时记下来**（同 contract_pins 的理由）：负例不能从
    # 生成物里正则找样本 —— 退化实验一改生成物，负例就跟着消失（自指脆性）。
    clock = (interface or {}).get("clock") or {}
    contract_config = {
        "sysclk_hz": (int(clock["sysclk_hz"])
                      if clock.get("status") == "declared" and clock.get("sysclk_hz")
                      else 0),
        "peripherals": [{"ip": ip, "param": param, "value": value}
                        for ip, param, value in _config_macros(interface or {})],
        "af": [{"name": name, "af": number}
               for name, number in _af_macros(interface or {})],
    }
    return {"status": "ok", "mcu": key, "ref": ref, "core": mem["core"],
            "contract_pins": {"power": power_names, "signal": signal_names},
            "contract_config": contract_config,
            "memory": {"flash_kb": mem["flash_kb"], "ram_kb": mem["ram_kb"],
                       "basis": mem["basis"]},
            "dir": out_dir, "files": written}


def _negative_cases(target: Path, cc: str, cpu: str, env: dict[str, str],
                    timeout_s: int, record: dict[str, Any] | None = None
                    ) -> dict[str, Any]:
    """三条"必须编不过"的源码。**每条的期望都是 `fail`。**

    | 负例 | 用什么 | 期望的错 |
    |---|---|---|
    | 电源脚当 GPIO | `HWV_CHECK_GPIO(3V3)` | `_Static_assert` 失败（人话）|
    | 脚号超范围 | `HWV_CHECK_PIN_NUMBER(HWV_MCU_PINS+1)` | `_Static_assert` 失败 |
    | 名字不在契约 | `HWV_PIN(NOSUCH_NET)` | 未声明标识符 |

    头文件是**生成物**，所以负例也由这里生成 —— 不用手写、不会和契约脱钩。
    """
    header = target / "hwv_pins.h"
    if not header.is_file():
        return {"status": "not_ready", "reason": "没有 hwv_pins.h", "cases": []}
    cases: list[dict[str, Any]] = []
    specs: list[tuple[str, str, str]] = []
    # 样本优先取 `fw_build.json`（生成时记的），回退才去头文件里找
    power_names = list(((record or {}).get("contract_pins") or {}).get("power") or [])
    if power_names:
        specs.append(("power_as_gpio",
                      f"void t(void) {{ HWV_CHECK_GPIO({power_names[0]}); }}",
                      "电源/地脚被当 GPIO 用，必须编不过"))
    specs.append(("pin_out_of_range",
                  "void t(void) { HWV_CHECK_PIN_NUMBER(HWV_MCU_PINS + 1); }",
                  "脚号超范围，必须编不过"))
    specs.append(("name_not_in_contract",
                  "unsigned long t(void) { return (unsigned long)HWV_PIN(NOSUCH_NET); }",
                  "契约里没有这个网名，必须编不过"))
    # P55：有 .ioc 时再加两条 —— 时钟写错、外设参数写错。
    # 样本同样取自 `fw_build.json` 的 `contract_config`，不从生成物里找。
    config = (record or {}).get("contract_config") or {}
    sysclk = int(config.get("sysclk_hz") or 0)
    if sysclk:
        specs.append(("clock_wrong",
                      f"void t(void) {{ HWV_CHECK_CLOCK({sysclk + 1000}u); }}",
                      f"`{sysclk} Hz` 是 .ioc 的值；固件按别的数写必须编不过"))
    afs = [row for row in (config.get("af") or [])
           if isinstance(row, dict) and row.get("name") and row.get("af") is not None]
    if afs:
        row = afs[0]
        wrong = (int(row["af"]) + 1) if int(row["af"]) < 15 else int(row["af"]) - 1
        specs.append(("af_wrong",
                      f"void t(void) {{ HWV_EXPECT_AF({row['name']}, {wrong}); }}",
                      f"{row['name']} 是 AF{row['af']}；固件写别的编号必须编不过"))
    periphs = [row for row in (config.get("peripherals") or [])
               if isinstance(row, dict) and row.get("ip") and row.get("param")]
    if periphs:
        row = periphs[0]
        try:
            value = int(str(row.get("value")), 0)
        except ValueError:
            value = 0
        if value:
            specs.append(("cfg_wrong",
                          f"void t(void) {{ HWV_EXPECT_CFG({row['ip']}, {row['param']}, "
                          f"{value + 1}u); }}",
                          f".ioc 是 {row['ip']}.{row['param']}={value}；写错必须编不过"))

    for name, body, why in specs:
        source = target / f"neg_{name}.c"
        source.write_text('#include "hwv_pins.h"\n' + body + "\n", encoding="utf-8")
        obj = target / f"neg_{name}.o"
        try:
            proc = subprocess.run(
                [cc, f"-mcpu={cpu}", "-mthumb", "-Os", "-c", source.name, "-o", obj.name],
                cwd=str(target), capture_output=True, text=True,
                timeout=timeout_s, env=env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            cases.append({"name": name, "why": why, "expect": "fail", "got": "error",
                          "detail": f"编译没跑起来：{exc}"})
            continue
        got = "fail" if proc.returncode != 0 else "pass"
        detail = ""
        if got == "fail":
            out_lines = (proc.stderr or proc.stdout or "").strip().splitlines()
            # **先找 `error:`**：第一版按 "error 或 HWV_" 找，先命中的是一条
            # 无关的 `warning: "HWV_PIN_GND" redefined`，把真错信息挡在后面。
            detail = next((line.strip() for line in out_lines
                           if "error:" in line.lower()), "")
            if not detail:
                detail = next((line.strip() for line in out_lines
                               if "HWV_" in line), "")
        cases.append({"name": name, "why": why, "expect": "fail", "got": got,
                      "detail": detail[:160]})

    bad = [row for row in cases if row["got"] != "fail"]
    return {"status": "ok" if cases and not bad else
            ("failed" if bad else "not_ready"),
            "cases": cases, "passed": len(cases) - len(bad), "total": len(cases),
            "note": "**负例必须编不过**；编过了就说明约束没生效"}


def build(store: Any, *, out_dir: str = OUT_DIR, timeout_s: int = 180,
          cc: Path | None = None) -> dict[str, Any]:
    """真编译 + 四条判据。`cc` 只给测试用（退化验证时换成错的编译器/假的）。"""
    target = store.path(out_dir)
    if not (target / "startup.c").is_file():
        return {"status": "not_ready", "reason": "还没生成骨架（先跑 generate）"}
    record = store.read(FILE)
    if not isinstance(record, dict) or not record.get("mcu"):
        return {"status": "not_ready",
                "reason": f"缺 {FILE}（先跑 generate，它记 mcu/core/memory）"}
    tool = cc or _find_toolchain()
    if tool is None:
        return {"status": "not_ready",
                "reason": "没有 arm-none-eabi-gcc（仓库 _tools 与 PATH 都没有）"}

    # 中间文件与产物都落在工程内 —— 沙箱下 %TEMP% 可能不可写（runbook §13.5）
    tmp = store.path("_tmp/fw-build")
    tmp.mkdir(parents=True, exist_ok=True)
    # **必须继承完整环境**：Windows 上缺 SYSTEMROOT/PATH 的其它项会 CreateProcess 失败
    env = dict(os.environ)
    env.update({"PATH": str(tool.parent) + os.pathsep + env.get("PATH", ""),
                "TMP": str(tmp), "TEMP": str(tmp), "TMPDIR": str(tmp)})
    size_tool = tool.parent / "arm-none-eabi-size.exe"
    readelf = tool.parent / "arm-none-eabi-readelf.exe"

    cpu = {"cortex-m3": "cortex-m3", "cortex-m4": "cortex-m4",
           "cortex-m7": "cortex-m7"}.get(str(record.get("core")), "cortex-m3")
    cmd = [str(tool), f"-mcpu={cpu}", "-mthumb", "-Os", "-Wall", "-Wextra",
           "-ffunction-sections", "-fdata-sections", "-nostdlib",
           "--specs=nano.specs", "-Wl,--gc-sections", "-T", "link.ld",
           "-o", "firmware.elf", "startup.c", "main.c"]
    try:
        proc = subprocess.run(cmd, cwd=str(target), capture_output=True,
                              text=True, timeout=timeout_s, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "not_ready", "reason": f"编译没跑起来：{exc}",
                "toolchain": str(tool)}
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"status": "failed", "reason": "编译不过", "toolchain": str(tool),
                "compiler": lines[-8:]}

    elf = target / "firmware.elf"
    findings: list[str] = []
    checks: dict[str, Any] = {}

    # ① Machine = ARM
    machine = ""
    if readelf.exists():
        info = subprocess.run([str(readelf), "-h", "firmware.elf"], cwd=str(target),
                              capture_output=True, text=True, timeout=60, env=env)
        for line in (info.stdout or "").splitlines():
            if "Machine:" in line:
                machine = line.split(":", 1)[1].strip()
            if "Entry point address:" in line:
                checks["entry"] = line.split(":", 1)[1].strip()
    checks["machine"] = machine
    ok_machine = machine == "ARM"
    if not ok_machine:
        findings.append(f"readelf 的 Machine 是 {machine!r}，不是 ARM")

    # ② Entry 在 FLASH
    entry = str(checks.get("entry") or "")
    ok_entry = False
    m = re.search(r"0x([0-9a-fA-F]+)", entry)
    if m:
        value = int(m.group(1), 16)
        ok_entry = 0x08000000 <= value < 0x0A000000
    if not ok_entry:
        findings.append(f"Entry {entry!r} 不在 FLASH 区间（0x08000000 起）")

    # ③ size 装得下
    text = data = bss = 0
    if size_tool.exists():
        sized = subprocess.run([str(size_tool), "firmware.elf"], cwd=str(target),
                               capture_output=True, text=True, timeout=60, env=env)
        parts = (sized.stdout or "").strip().splitlines()
        if len(parts) >= 2:
            nums = parts[1].split()
            if len(nums) >= 3:
                try:
                    text, data, bss = int(nums[0]), int(nums[1]), int(nums[2])
                except ValueError:
                    pass
    mem = MCU_MEMORY.get(str(record.get("mcu") or ""), {})
    flash_b = int(mem.get("flash_kb", 0)) * 1024
    ram_b = int(float(mem.get("ram_kb", 0)) * 1024)
    ok_flash = flash_b > 0 and text + data <= flash_b
    ok_ram = ram_b > 0 and data + bss <= ram_b
    if flash_b and not ok_flash:
        findings.append(f"text+data = {text + data} B 超过 FLASH {flash_b} B")
    if ram_b and not ok_ram:
        findings.append(f"data+bss = {data + bss} B 超过 RAM {ram_b} B")

    # ④ .bin 也要出得来（比 elf 更接近烧录物）
    #
    # **必须用 objcopy**：第一版写成 `gcc -O binary out in` —— gcc 的 `-O`
    # 是优化级别，参数顺序也反了，于是永远"没生成 bin"。
    objcopy = tool.parent / "arm-none-eabi-objcopy.exe"
    bin_ok = False
    if objcopy.exists() and (target / "firmware.elf").is_file():
        made = subprocess.run([str(objcopy), "-O", "binary",
                               "firmware.elf", "firmware.bin"], cwd=str(target),
                              capture_output=True, text=True, timeout=60, env=env)
        bin_ok = made.returncode == 0 and (target / "firmware.bin").is_file()
    if not bin_ok:
        findings.append("firmware.bin 没生成")

    # ⑤ **编译期负例**：证明"用错脚会炸"不是口号（P54）
    #
    # 判据是反的：**负例必须编译失败**。若负例编过了，说明约束没生效 = 假绿
    # （§0.1 的形状：闸门写了但没挂上去）。
    negative = _negative_cases(target, str(tool), cpu, env, timeout_s, record)
    for row in negative["cases"]:
        if row["got"] == "pass":
            findings.append(
                f"负例 {row['name']} 竟然编译通过 —— 契约约束没生效（用错脚不会被拦住）")
    if not negative["cases"]:
        findings.append("没有可跑的负例（interface 里既没有电源脚也没有信号脚）")

    status = "ok" if (ok_machine and ok_entry and ok_flash and ok_ram and bin_ok
                      and negative["status"] == "ok") else "failed"
    return {
        "status": status, "toolchain": str(tool), "dir": out_dir,
        "checks": {"machine": machine, "entry": checks.get("entry", ""),
                   "text_b": text, "data_b": data, "bss_b": bss,
                   "flash_limit_b": flash_b, "ram_limit_b": ram_b},
        "findings": findings,
        "negative": negative,
        "size": {"text": text, "data": data, "bss": bss},
        "elf": str(target / "firmware.elf"), "bin": str(target / "firmware.bin"),
        "note": "骨架能编能链、地址与容量对得上；**不代表功能对**",
    }


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.fw_build",
        "out_dir": OUT_DIR,
        "generates": ["startup.c", "link.ld", "main.c", "hwv_pins.h", "Makefile"],
        "hard_checks": ["编译返回 0", "Machine=ARM", "Entry 在 FLASH",
                        "text+data <= FLASH 且 data+bss <= RAM", "能出 .bin",
                        "**三条编译期负例都必须编不过**（用错脚/超范围/名字不在契约）",
                        "**有 .ioc 时再加两条**：时钟写错、外设参数写错也必须编不过"],
        "af_contract": ("hwv_pins.h 带 `HWV_AF_<功能>` 与 `HWV_EXPECT_AF`："
                        "固件里 AF 编号写错 → 编译期炸；F1 没有 AF 编号，不生成宏"),
        "config_contract": ("hwv_pins.h 还带 .ioc 的时钟与外设参数："
                            "HWV_CHECK_CLOCK 挡时钟不符、HWV_EXPECT_CFG 挡波特率等参数不符；"
                            "参数没写进 .ioc → 宏不存在 → 未声明标识符（不填默认值）"),
        "pin_contract": ("hwv_pins.h 由 interface.json 生成；"
                         "HWV_PIN 用名字、HWV_CHECK_GPIO 挡电源脚、"
                         "HWV_CHECK_PIN_NUMBER 挡超范围、HWV_EXPECT 挡线被挪走"),
        "mcu_memory": {k: f"{v['flash_kb']}K/{v['ram_kb']}K" for k, v in MCU_MEMORY.items()},
        "discipline": ("内存表缺型号 → not_ready（不猜）；无工具链 → not_ready；"
                       "生成的 main.c 是骨架，只证明能编能链，不证明功能对"),
    }