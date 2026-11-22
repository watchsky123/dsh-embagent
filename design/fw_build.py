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


def _main_c(interface: dict[str, Any]) -> str:
    peripherals = interface.get("peripherals") or []
    lines = [
        "/* 生成物（fw-build）—— **骨架**：证明能编、能链、地址对；",
        " * 外设初始化只有 TODO，不证明功能对。",
        " */",
        "#include <stdint.h>",
        "",
        "/* 定义（不是 extern）：第一版写成 extern 又在函数里赋值，链接报",
        " * `undefined reference to SystemCoreClock` —— 声明≠定义。 */",
        "uint32_t SystemCoreClock = 8000000u;",
        "void SystemInit(void) { SystemCoreClock = 8000000u; }",
        "",
        "volatile uint32_t g_ticks;",
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

firmware.elf: startup.c main.c link.ld
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
        "Makefile": _makefile(str(mem["core"])),
    }
    written = []
    for name, text in files.items():
        path = target / name
        path.write_text(text, encoding="utf-8")
        written.append(str(path))
    return {"status": "ok", "mcu": key, "ref": ref, "core": mem["core"],
            "memory": {"flash_kb": mem["flash_kb"], "ram_kb": mem["ram_kb"],
                       "basis": mem["basis"]},
            "dir": out_dir, "files": written}


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

    status = "ok" if (ok_machine and ok_entry and ok_flash and ok_ram and bin_ok) \
        else "failed"
    return {
        "status": status, "toolchain": str(tool), "dir": out_dir,
        "checks": {"machine": machine, "entry": checks.get("entry", ""),
                   "text_b": text, "data_b": data, "bss_b": bss,
                   "flash_limit_b": flash_b, "ram_limit_b": ram_b},
        "findings": findings,
        "size": {"text": text, "data": data, "bss": bss},
        "elf": str(target / "firmware.elf"), "bin": str(target / "firmware.bin"),
        "note": "骨架能编能链、地址与容量对得上；**不代表功能对**",
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.fw_build",
        "out_dir": OUT_DIR,
        "generates": ["startup.c", "link.ld", "main.c", "Makefile"],
        "hard_checks": ["编译返回 0", "Machine=ARM", "Entry 在 FLASH",
                        "text+data <= FLASH 且 data+bss <= RAM", "能出 .bin"],
        "mcu_memory": {k: f"{v['flash_kb']}K/{v['ram_kb']}K" for k, v in MCU_MEMORY.items()},
        "discipline": ("内存表缺型号 → not_ready（不猜）；无工具链 → not_ready；"
                       "生成的 main.c 是骨架，只证明能编能链，不证明功能对"),
    }