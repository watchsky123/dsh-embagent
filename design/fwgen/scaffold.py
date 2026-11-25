# 从 Traceforge（MIT）移植 —— 原路径 src/traceforge/firmware/scaffold.py
#
# MIT License, Copyright (c) 2026 Traceforge contributors。
# 本文件保留原实现的逻辑与"每项注明 datasheet 章节"的纪律；改动处见下方注释，
# 每个改动都标了 `# [hwv]`。
#
"""Project scaffolds for Arduino, PlatformIO, ESP-IDF, STM32 (bare CMake/Make) and AVR."""

from __future__ import annotations

import json
import re
from pathlib import Path

from dsh_embagent.design.fwgen.drivers import HAL_H, generate_driver, hal_arduino
from dsh_embagent.design.fwgen.pinmap import HEADER_FILES, PinAssignment, PinMap, render_header

TARGETS = ("arduino", "platformio", "esp-idf", "stm32", "avr")
# [hwv] 清单文件名保留 tf_ 前缀会让新项目的构建脚本认不出；改成我们自己的名字。
MANIFEST = "hwv_firmware.json"
# [hwv] 同一条 pins.h 里再给一套 HAL 写法（HWV_PIN_x = GPIO_PIN_n）。
# 寄存器级的 PIN_x_PORT/PIN_x_PIN 不变，所以下面的 main 模板不用改。
ALIAS_PREFIX = "HWV_"

# The pin header style per target (C++ sketches take the C header too).
_STYLE = {"arduino": "c", "platformio": "c", "esp-idf": "c", "stm32": "c", "avr": "c"}


class ScaffoldError(Exception):
    pass


def default_target(pm: PinMap) -> str:
    return {"avr": "avr", "esp32": "platformio", "stm32": "stm32"}[pm.mcu.family]


def _usable(pm: PinMap) -> tuple[list[PinAssignment], list[PinAssignment]]:
    """(outputs, inputs) that the demo main may drive: plain GPIO nets with a pin number."""
    outs: list[PinAssignment] = []
    ins: list[PinAssignment] = []
    for a in pm.assignments:
        if a.function != "GPIO" or a.number is None:
            continue
        if any("not usable" in w or "RESET pin" in w or "strapping" in w for w in a.warnings):
            continue
        if a.net.upper() in {"RESET", "NRST", "EN"}:
            continue
        (ins if a.direction == "in" else outs).append(a)
    return outs, ins


def _files_arduino_like(pm: PinMap, outs, ins) -> str:
    lines = ["#include <Arduino.h>", '#include "pins.h"', "", "void setup() {"]
    for a in outs:
        lines.append(f"  pinMode(PIN_{a.macro}, OUTPUT);")
    for a in ins:
        lines.append(f"  pinMode(PIN_{a.macro}, {'INPUT_PULLUP' if a.pull_up else 'INPUT'});")
    lines += ["}", "", "void loop() {"]
    if outs and ins:
        lines += [
            f"  // {outs[0].net} follows {ins[0].net} (pressed = low)",
            f"  digitalWrite(PIN_{outs[0].macro}, digitalRead(PIN_{ins[0].macro}) ? LOW : HIGH);",
            "  delay(10);",
        ]
    elif outs:
        lines += [
            f"  digitalWrite(PIN_{outs[0].macro}, HIGH);",
            "  delay(500);",
            f"  digitalWrite(PIN_{outs[0].macro}, LOW);",
            "  delay(500);",
        ]
    else:
        lines += ["  delay(1000);"]
    lines += ["}", ""]
    return "\n".join(lines)


def _main_avr(pm: PinMap, outs, ins) -> str:
    lines = [
        f"#ifndef F_CPU\n#define F_CPU {pm.mcu.f_cpu}UL\n#endif",
        "#include <avr/io.h>",
        "#include <util/delay.h>",
        '#include "pins.h"',
        "",
        "int main(void) {",
    ]
    for a in outs:
        lines.append(f"    PIN_{a.macro}_DDR |= _BV(PIN_{a.macro}_BIT);")
    for a in ins:
        lines.append(f"    PIN_{a.macro}_PORT |= _BV(PIN_{a.macro}_BIT); /* pull-up */")
    lines += ["    for (;;) {"]
    if outs and ins:
        o, i = outs[0].macro, ins[0].macro
        lines += [
            f"        if (PIN_{i}_PINREG & _BV(PIN_{i}_BIT)) PIN_{o}_PORT &= ~_BV(PIN_{o}_BIT);",
            f"        else PIN_{o}_PORT |= _BV(PIN_{o}_BIT);",
            "        _delay_ms(10);",
        ]
    elif outs:
        o = outs[0].macro
        lines += [f"        PIN_{o}_PORT ^= _BV(PIN_{o}_BIT);", "        _delay_ms(500);"]
    else:
        lines += ["        _delay_ms(1000);"]
    lines += ["    }", "}", ""]
    return "\n".join(lines)


def _main_idf(pm: PinMap, outs, ins) -> str:
    lines = [
        '#include "driver/gpio.h"',
        '#include "freertos/FreeRTOS.h"',
        '#include "freertos/task.h"',
        '#include "pins.h"',
        "",
        "void app_main(void) {",
    ]
    for a in outs:
        lines.append(f"    gpio_set_direction(PIN_{a.macro}, GPIO_MODE_OUTPUT);")
    for a in ins:
        lines.append(f"    gpio_set_direction(PIN_{a.macro}, GPIO_MODE_INPUT);")
        lines.append(f"    gpio_set_pull_mode(PIN_{a.macro}, GPIO_PULLUP_ONLY);")
    lines.append("    for (;;) {")
    if outs and ins:
        lines.append(
            f"        gpio_set_level(PIN_{outs[0].macro}, !gpio_get_level(PIN_{ins[0].macro}));"
        )
        lines.append("        vTaskDelay(pdMS_TO_TICKS(10));")
    elif outs:
        lines += [
            f"        gpio_set_level(PIN_{outs[0].macro}, 1);",
            "        vTaskDelay(pdMS_TO_TICKS(500));",
            f"        gpio_set_level(PIN_{outs[0].macro}, 0);",
            "        vTaskDelay(pdMS_TO_TICKS(500));",
        ]
    else:
        lines.append("        vTaskDelay(pdMS_TO_TICKS(1000));")
    lines += ["    }", "}", ""]
    return "\n".join(lines)


def _main_stm32(pm: PinMap, outs, ins) -> str:
    lines = [
        "/* Register-level STM32F1 GPIO (RM0008 section 9). No vendor library needed. */",
        "#include <stdint.h>",
        '#include "pins.h"',
        "",
        "#define RCC_APB2ENR (*(volatile uint32_t *)0x40021018u)",
        "#define GPIO_BASE(p) (0x40010800u + 0x400u * (uint32_t)((p) - 'A'))",
        "#define GPIO_CR(p, pin) (*(volatile uint32_t *)(GPIO_BASE(p) + ((pin) >= 8 ? 4u : 0u)))",
        "#define GPIO_IDR(p) (*(volatile uint32_t *)(GPIO_BASE(p) + 0x08u))",
        "#define GPIO_BSRR(p) (*(volatile uint32_t *)(GPIO_BASE(p) + 0x10u))",
        "",
        "static void mode(char port, int pin, uint32_t cnf_mode) {",
        "    RCC_APB2ENR |= 1u << (2 + (port - 'A'));",
        "    uint32_t shift = (uint32_t)(pin % 8) * 4u;",
        "    GPIO_CR(port, pin) = (GPIO_CR(port, pin) & ~(0xFu << shift)) | (cnf_mode << shift);",
        "}",
        'static void delay(volatile uint32_t n) { while (n--) { __asm__ volatile("nop"); } }',
        "",
        "int main(void) {",
    ]
    for a in outs:
        lines.append(
            f"    mode(PIN_{a.macro}_PORT, PIN_{a.macro}_PIN, 0x2u); /* output 2 MHz push-pull */"
        )
    for a in ins:
        m = a.macro
        lines.append(f"    mode(PIN_{m}_PORT, PIN_{m}_PIN, 0x8u); /* input pull-up/down */")
        lines.append(f"    GPIO_BSRR(PIN_{m}_PORT) = 1u << PIN_{m}_PIN; /* pull-up */")
    lines.append("    for (;;) {")
    if outs:
        o = outs[0].macro
        if ins:
            i = ins[0].macro
            lines += [
                f"        if (GPIO_IDR(PIN_{i}_PORT) & (1u << PIN_{i}_PIN)) GPIO_BSRR(PIN_{o}_PORT) = 1u << (PIN_{o}_PIN + 16);",
                f"        else GPIO_BSRR(PIN_{o}_PORT) = 1u << PIN_{o}_PIN;",
            ]
        else:
            lines += [
                f"        GPIO_BSRR(PIN_{o}_PORT) = 1u << PIN_{o}_PIN;",
                "        delay(400000);",
                f"        GPIO_BSRR(PIN_{o}_PORT) = 1u << (PIN_{o}_PIN + 16);",
                "        delay(400000);",
            ]
    lines += ["    }", "}", ""]
    return "\n".join(lines)


_STM32_STARTUP = """\
/* Minimal Cortex-M3 start-up: vector table, data/bss init, call main. */
#include <stdint.h>
extern uint32_t _sidata, _sdata, _edata, _sbss, _ebss, _estack;
int main(void);
void Reset_Handler(void) {
    uint32_t *src = &_sidata;
    for (uint32_t *dst = &_sdata; dst < &_edata;) *dst++ = *src++;
    for (uint32_t *dst = &_sbss; dst < &_ebss;) *dst++ = 0;
    main();
    for (;;) {}
}
void Default_Handler(void) { for (;;) {} }
__attribute__((section(".isr_vector"), used))
const void *const vectors[] = {&_estack, Reset_Handler, Default_Handler, Default_Handler};
"""

_STM32_LD = """\
/* STM32F103C8: 64 KB flash, 20 KB RAM (datasheet DS5319 table 2; many boards have 128 KB). */
MEMORY {
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 64K
  RAM (rwx)  : ORIGIN = 0x20000000, LENGTH = 20K
}
_estack = ORIGIN(RAM) + LENGTH(RAM);
SECTIONS {
  .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
  .text : { *(.text*) *(.rodata*) . = ALIGN(4); } > FLASH
  _sidata = LOADADDR(.data);
  .data : { _sdata = .; *(.data*) . = ALIGN(4); _edata = .; } > RAM AT > FLASH
  .bss : { _sbss = .; *(.bss*) *(COMMON) . = ALIGN(4); _ebss = .; } > RAM
}
"""

_STM32_MAKEFILE = """\
# Bare-metal STM32F103 (Cortex-M3) project: no HAL, no CMSIS, register-level code only.
# Limits: no clock setup (runs on the 8 MHz HSI), no interrupts, no peripherals besides GPIO.
# For real work add STM32Cube (HAL/LL) and replace startup.c and link.ld with ST's files.
PREFIX ?= arm-none-eabi-
CC = $(PREFIX)gcc
CFLAGS = -mcpu=cortex-m3 -mthumb -Os -Wall -Wextra -ffreestanding -ffunction-sections -fdata-sections
LDFLAGS = -nostdlib -T link.ld -Wl,--gc-sections -Wl,-Map=firmware.map

all: firmware.elf firmware.bin size

firmware.elf: main.c startup.c pins.h link.ld
\t$(CC) $(CFLAGS) main.c startup.c $(LDFLAGS) -o $@

firmware.bin: firmware.elf
\t$(PREFIX)objcopy -O binary $< $@

size: firmware.elf
\t$(PREFIX)size $<

flash: firmware.bin
\tst-flash write firmware.bin 0x8000000

clean:
\trm -f firmware.elf firmware.bin firmware.map
"""

_STM32_CMAKE = """\
cmake_minimum_required(VERSION 3.16)
set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR arm)
set(CMAKE_C_COMPILER arm-none-eabi-gcc)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
project({name} C)
add_executable(firmware main.c startup.c)
target_compile_options(firmware PRIVATE -mcpu=cortex-m3 -mthumb -Os -Wall -ffreestanding)
target_link_options(firmware PRIVATE -nostdlib -T${{CMAKE_SOURCE_DIR}}/link.ld -Wl,--gc-sections)
"""


def _avr_makefile(pm: PinMap, name: str, sources: list[str]) -> str:
    mcu = pm.mcu.key
    return f"""\
# avr-gcc build for {pm.mcu.name} ({mcu}). Flash with an ISP programmer (the board's ISP header).
MCU = {mcu}
F_CPU = {pm.mcu.f_cpu}UL
PROGRAMMER ?= usbasp
SRC = {" ".join(sources)}

CFLAGS = -mmcu=$(MCU) -DF_CPU=$(F_CPU) -Os -Wall -Wextra -std=gnu11 -ffunction-sections -fdata-sections
LDFLAGS = -mmcu=$(MCU) -Wl,--gc-sections

all: firmware.hex size

firmware.elf: $(SRC) pins.h
\tavr-gcc $(CFLAGS) $(SRC) $(LDFLAGS) -o $@

firmware.hex: firmware.elf
\tavr-objcopy -O ihex -R .eeprom $< $@

size: firmware.elf
\tavr-size --mcu=$(MCU) -C $<

flash: firmware.hex
\tavrdude -c $(PROGRAMMER) -p {mcu} -U flash:w:firmware.hex:i

clean:
\trm -f firmware.elf firmware.hex
"""


def scaffold(
    pm: PinMap,
    out: str | Path,
    target: str | None = None,
    name: str | None = None,
    drivers: list[str] | None = None,
    source: str = "the netlist",
    overwrite: bool = False,
) -> list[Path]:
    """Write a project for ``target`` into ``out``; return the files written."""
    target = target or default_target(pm)
    if target not in TARGETS:
        raise ScaffoldError(f"unknown target {target!r} (use {', '.join(TARGETS)})")
    fam = pm.mcu.family
    allowed = {
        "avr": {"avr"},
        "esp-idf": {"esp32"},
        "stm32": {"stm32"},
        "arduino": {"avr", "esp32"},
        "platformio": {"avr", "esp32"},
    }[target]
    if fam not in allowed:
        raise ScaffoldError(f"target {target} does not fit {pm.mcu.name} ({fam})")
    name = re.sub(r"[^A-Za-z0-9_]", "_", name or "firmware")
    out = Path(out)
    files: dict[str, str] = {}
    outs, ins = _usable(pm)
    header = render_header(pm, _STYLE[target], source,
                           alias_prefix=ALIAS_PREFIX)
    drivers = drivers or []
    driver_files: dict[str, str] = {}
    for part in drivers:
        driver_files.update(generate_driver(part))
    if driver_files:
        driver_files["tf_hal.h"] = HAL_H

    def put(rel: str, text: str) -> None:
        files[rel] = text

    sources_c = [f for f in driver_files if f.endswith(".c")]
    if target == "arduino":
        base = f"{name}/"
        put(f"{base}{name}.ino", _files_arduino_like(pm, outs, ins))
        put(f"{base}pins.h", header)
        for f, t in driver_files.items():
            put(base + f, t)
        if driver_files:
            put(f"{base}tf_hal_arduino.cpp", hal_arduino())
        put(MANIFEST, json.dumps(_manifest(pm, target, name, fqbn=pm.mcu.fqbn), indent=2) + "\n")
    elif target == "platformio":
        pio = pm.mcu.platformio
        ini = [f"[env:{pio['board']}]", f"platform = {pio['platform']}", f"board = {pio['board']}",
               f"framework = {pio['framework']}"]  # fmt: skip
        if fam == "avr":
            ini.append(f"board_build.f_cpu = {pm.mcu.f_cpu}L")
        if fam == "esp32":
            ini += ["monitor_speed = 115200", "build_flags = -DARDUINO_USB_CDC_ON_BOOT=1"]
        put("platformio.ini", "\n".join(ini) + "\n")
        put("include/pins.h", header)
        put("src/main.cpp", _files_arduino_like(pm, outs, ins))
        for f, t in driver_files.items():
            put(f"{'include' if f.endswith('.h') else 'src'}/{f}", t)
        if driver_files:
            put("src/tf_hal_arduino.cpp", hal_arduino())
        put(MANIFEST, json.dumps(_manifest(pm, target, name), indent=2) + "\n")
    elif target == "esp-idf":
        put("CMakeLists.txt", "cmake_minimum_required(VERSION 3.16)\n"
            "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n" f"project({name})\n")  # fmt: skip
        srcs = " ".join(f'"{s}"' for s in ["main.c", *sources_c])
        put("main/CMakeLists.txt", f'idf_component_register(SRCS {srcs} INCLUDE_DIRS ".")\n')
        put("main/main.c", _main_idf(pm, outs, ins))
        put("main/pins.h", header)
        for f, t in driver_files.items():
            put(f"main/{f}", t)
        if driver_files:
            put("main/tf_hal_idf.c", _hal_idf())
            put(
                "main/CMakeLists.txt",
                f'idf_component_register(SRCS {srcs} "tf_hal_idf.c" INCLUDE_DIRS ".")\n',
            )
        put("sdkconfig.defaults", 'CONFIG_IDF_TARGET="esp32s3"\n')
        put(MANIFEST, json.dumps(_manifest(pm, target, name), indent=2) + "\n")
    elif target == "stm32":
        put("main.c", _main_stm32(pm, outs, ins))
        put("pins.h", header)
        put("startup.c", _STM32_STARTUP)
        put("link.ld", _STM32_LD)
        put("Makefile", _STM32_MAKEFILE)
        put("CMakeLists.txt", _STM32_CMAKE.format(name=name))
        put(MANIFEST, json.dumps(_manifest(pm, target, name), indent=2) + "\n")
    else:  # avr
        put("main.c", _main_avr(pm, outs, ins))
        put("pins.h", header)
        for f, t in driver_files.items():
            put(f, t)
        put("Makefile", _avr_makefile(pm, name, ["main.c", *sources_c]))
        put(MANIFEST, json.dumps(_manifest(pm, target, name), indent=2) + "\n")
    put("README.md", _readme(pm, target, name, drivers))
    written: list[Path] = []
    for rel in files:
        path = out / rel
        if path.exists() and not overwrite:
            raise ScaffoldError(f"{path} exists (use --overwrite)")
    for rel, text in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def _manifest(pm: PinMap, target: str, name: str, fqbn: str = "") -> dict:
    data = {"target": target, "mcu": pm.mcu.key, "name": name, "header": HEADER_FILES["c"]}
    if fqbn:
        data["fqbn"] = fqbn
    return data


def _hal_idf() -> str:
    return """\
/* ESP-IDF implementation of tf_hal.h (new I2C master driver, port 0). Fill in the pins
   with PIN_I2C_SDA / PIN_I2C_SCL from pins.h and create the bus in app_main. */
#include "driver/i2c_master.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "tf_hal.h"

i2c_master_bus_handle_t tf_i2c_bus;
static i2c_master_dev_handle_t dev_for(uint8_t addr) {
    i2c_master_dev_handle_t dev;
    i2c_device_config_t cfg = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = addr, .scl_speed_hz = 100000};
    return i2c_master_bus_add_device(tf_i2c_bus, &cfg, &dev) == ESP_OK ? dev : NULL;
}
int tf_i2c_write(uint8_t addr, const uint8_t *data, size_t len) {
    i2c_master_dev_handle_t d = dev_for(addr);
    int r = d ? i2c_master_transmit(d, data, len, 100) : 1;
    if (d) i2c_master_bus_rm_device(d);
    return r != ESP_OK;
}
int tf_i2c_read(uint8_t addr, uint8_t *data, size_t len) {
    i2c_master_dev_handle_t d = dev_for(addr);
    int r = d ? i2c_master_receive(d, data, len, 100) : 1;
    if (d) i2c_master_bus_rm_device(d);
    return r != ESP_OK;
}
void tf_delay_ms(uint32_t ms) { vTaskDelay(pdMS_TO_TICKS(ms)); }
void tf_delay_us(uint32_t us) { esp_rom_delay_us(us); }
void tf_pin_output_low(int pin) {}
void tf_pin_release(int pin) {}
int tf_pin_read(int pin) { return 0; }
"""


def _readme(pm: PinMap, target: str, name: str, drivers: list[str]) -> str:
    build = {
        "arduino": f"arduino-cli compile --fqbn {pm.mcu.fqbn or '<board fqbn>'} {name}",
        "platformio": "pio run",
        "esp-idf": "idf.py set-target esp32s3 && idf.py build",
        "stm32": "make   (needs arm-none-eabi-gcc)",
        "avr": "make   (needs avr-gcc and avr-libc)",
    }[target]
    lines = [
        f"# {name}",
        "",
        f"Firmware scaffold for {pm.mcu.name} ({pm.ref}), target `{target}`, generated by",
        "`dsh_embagent fw-scaffold`. The pin header is generated from the hardware config;",
        "regenerate it with `dsh_embagent pinmap-gen` instead of editing it.",
        "",
        f"Build: `{build}`",
        "",
        "## Pins",
        "",
        "| Net | MCU pin | Function |",
        "| --- | --- | --- |",
    ]
    for a in pm.assignments:
        lines.append(f"| {a.net} | {a.pin_name} (pin {a.pin}) | {a.function} |")
    if drivers:
        lines += ["", "## Drivers", "", "Generated: " + ", ".join(drivers) + ". They call `tf_hal.h`;"
                  " the project includes an implementation for this target."]  # fmt: skip
    if pm.warnings:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in pm.warnings]
    return "\n".join(lines) + "\n"
