# 从 Traceforge（MIT）移植 —— 原路径 src/traceforge/firmware/drivers_data.py
#
# MIT License, Copyright (c) 2026 Traceforge contributors。
# 本文件保留原实现的逻辑与"每项注明 datasheet 章节"的纪律；改动处见下方注释，
# 每个改动都标了 `# [hwv]`。
#
"""Register tables for common parts (data). Every entry cites the datasheet section.

Kinds (see drivers.py): ``regs`` = register-addressed I2C part with an init list and read
blocks; ``cmd`` = command-addressed I2C part (command write, then read); ``oled`` =
command/data-byte display; ``onewire`` = DHT-style single-wire timing protocol.
Addresses are 7-bit.
"""

from __future__ import annotations

from typing import Any

PARTS: dict[str, dict[str, Any]] = {
    "BME280": {
        "kind": "regs",
        "bus": "i2c",
        "address": 0x76,
        "address_note": "0x76 with SDO low, 0x77 with SDO high (datasheet 6.2 I2C interface)",
        "datasheet": "Bosch BST-BME280-DS002, sections 5.3 (registers) and 4.2.3 (compensation)",
        "registers": [
            ("ID", 0xD0, "chip id, reads 0x60 (5.4.1)"),
            ("RESET", 0xE0, "write 0xB6 to soft-reset (5.4.2)"),
            ("CTRL_HUM", 0xF2, "humidity oversampling; write before CTRL_MEAS (5.4.3)"),
            ("STATUS", 0xF3, "bit 3 measuring, bit 0 im_update (5.4.4)"),
            ("CTRL_MEAS", 0xF4, "osrs_t[7:5] osrs_p[4:2] mode[1:0] (5.4.5)"),
            ("CONFIG", 0xF5, "t_sb[7:5] filter[4:2] spi3w_en[0] (5.4.6)"),
            ("PRESS_MSB", 0xF7, "press[19:12]; 8 bytes F7..FE = press, temp, hum (5.4.7-9)"),
            ("CALIB00", 0x88, "dig_T1.. dig_H1 block, 26 bytes 0x88..0xA1 (4.2.2)"),
            ("CALIB26", 0xE1, "dig_H2.. dig_H6 block, 7 bytes 0xE1..0xE7 (4.2.2)"),
        ],
        "check": ("ID", 0x60),
        "init": [
            ("RESET", 0xB6, "soft reset"),
            ("CTRL_HUM", 0x01, "humidity x1"),
            ("CONFIG", 0x00, "standby 0.5 ms, filter off"),
            ("CTRL_MEAS", 0x27, "temp x1, pressure x1, normal mode"),
        ],
        "init_delay_ms": 10,
        "reads": [
            {
                "name": "raw",
                "doc": "raw 20-bit pressure/temperature and 16-bit humidity (needs the "
                "calibration block and the datasheet 4.2.3 compensation formulas for units)",
                "start": "PRESS_MSB",
                "length": 8,
                "fields": [
                    (
                        "uint32_t",
                        "press_raw",
                        "((uint32_t)b[0] << 12) | ((uint32_t)b[1] << 4) | (b[2] >> 4)",
                    ),
                    (
                        "uint32_t",
                        "temp_raw",
                        "((uint32_t)b[3] << 12) | ((uint32_t)b[4] << 4) | (b[5] >> 4)",
                    ),
                    ("uint16_t", "hum_raw", "((uint16_t)b[6] << 8) | b[7]"),
                ],
            },
            {
                "name": "calibration",
                "doc": "calibration bytes 0x88..0xA1 (dig_T1.. in little endian pairs)",
                "start": "CALIB00",
                "length": 26,
                "raw_bytes": True,
            },
        ],
    },
    "SHT31": {
        "kind": "cmd",
        "bus": "i2c",
        "address": 0x44,
        "address_note": "0x44 with ADDR low, 0x45 with ADDR high (datasheet 4.3)",
        "datasheet": "Sensirion SHT3x-DIS datasheet, sections 4.3 (I2C address), 4.5 (commands), "
        "4.12 (CRC-8 poly 0x31 init 0xFF), 4.13 (conversion)",
        "commands": [
            ("SOFT_RESET", 0x30A2, "soft reset (4.9)"),
            ("MEAS_HIGHREP", 0x2400, "single shot, high repeatability, no clock stretching (4.5)"),
            ("STATUS", 0xF32D, "read status register (4.10)"),
        ],
        "init": [("SOFT_RESET", None, "soft reset")],
        "init_delay_ms": 2,
        "reads": [
            {
                "name": "measurement",
                "doc": "temperature in degC and relative humidity in percent",
                "command": "MEAS_HIGHREP",
                "delay_ms": 16,
                "length": 6,
                "crc": [(0, 1, 2), (3, 4, 5)],
                "fields": [
                    (
                        "float",
                        "temperature_c",
                        "-45.0f + 175.0f * (float)(((uint16_t)b[0] << 8) | b[1]) / 65535.0f",
                    ),
                    (
                        "float",
                        "humidity_pct",
                        "100.0f * (float)(((uint16_t)b[3] << 8) | b[4]) / 65535.0f",
                    ),
                ],
            }
        ],
    },
    "MCP23017": {
        "kind": "regs",
        "bus": "i2c",
        "address": 0x20,
        "address_note": "0x20 + A2:A0 straps, 0x20..0x27 (datasheet 3.3.1, Figure 1-1)",
        "datasheet": "Microchip MCP23017 datasheet DS20001952, Table 3-3 (register map, IOCON.BANK=0)",
        "registers": [
            ("IODIRA", 0x00, "port A direction, 1 = input (default 0xFF)"),
            ("IODIRB", 0x01, "port B direction"),
            ("IPOLA", 0x02, "port A input polarity"),
            ("GPPUA", 0x0C, "port A pull-up enable"),
            ("GPPUB", 0x0D, "port B pull-up enable"),
            ("IOCON", 0x0A, "configuration (BANK, MIRROR, SEQOP, ODR, INTPOL)"),
            ("GPIOA", 0x12, "port A pins; read returns the pin levels"),
            ("GPIOB", 0x13, "port B pins"),
            ("OLATA", 0x14, "port A output latch"),
            ("OLATB", 0x15, "port B output latch"),
        ],
        "init": [
            ("IODIRA", 0xFF, "port A all inputs"),
            ("IODIRB", 0xFF, "port B all inputs"),
            ("GPPUA", 0x00, "no pull-ups on A"),
            ("GPPUB", 0x00, "no pull-ups on B"),
        ],
        "reads": [
            {
                "name": "gpio",
                "doc": "both ports: bits 0-7 = GPIOA, bits 8-15 = GPIOB",
                "start": "GPIOA",
                "length": 2,
                "fields": [("uint16_t", "pins", "((uint16_t)b[1] << 8) | b[0]")],
            }
        ],
        "writes": [
            {
                "name": "set_output",
                "doc": "drive output latches (A low byte, B high byte)",
                "start": "OLATA",
                "length": 2,
            },
        ],
    },
    "PCF85063A": {
        "kind": "regs",
        "bus": "i2c",
        "address": 0x51,
        "address_note": "fixed 0x51 (datasheet 8.1 I2C interface, 7-bit)",
        "datasheet": "NXP PCF85063A datasheet, Table 4 (register overview, 0x00..0x11) and "
        "section 8.4 (time and date, BCD)",
        "registers": [
            ("CONTROL_1", 0x00, "EXT_TEST, STOP bit 5, SR, CIE, 12_24, CAP_SEL (Table 5)"),
            ("CONTROL_2", 0x01, "AIE, AF, MI, HMI, TF, COF (Table 8)"),
            ("OFFSET", 0x02, "offset register"),
            ("SECONDS", 0x04, "bit 7 OS = oscillator stop, bits 6:0 BCD seconds (Table 15)"),
            ("MINUTES", 0x05, "BCD minutes"),
            ("HOURS", 0x06, "BCD hours, 24 h mode"),
            ("DAYS", 0x07, "BCD day of month"),
            ("WEEKDAYS", 0x08, "weekday 0-6"),
            ("MONTHS", 0x09, "BCD month"),
            ("YEARS", 0x0A, "BCD year 00-99"),
        ],
        "init": [("CONTROL_1", 0x00, "clock running, 24 h mode, normal operation")],
        "reads": [
            {
                "name": "time",
                "doc": "date and time (year 2000 + yy); sets osc_stopped if the time is invalid",
                "start": "SECONDS",
                "length": 7,
                "fields": [
                    ("uint8_t", "second", "(uint8_t)(((b[0] >> 4) & 0x7) * 10 + (b[0] & 0xF))"),
                    ("uint8_t", "minute", "(uint8_t)(((b[1] >> 4) & 0x7) * 10 + (b[1] & 0xF))"),
                    ("uint8_t", "hour", "(uint8_t)(((b[2] >> 4) & 0x3) * 10 + (b[2] & 0xF))"),
                    ("uint8_t", "day", "(uint8_t)(((b[3] >> 4) & 0x3) * 10 + (b[3] & 0xF))"),
                    ("uint8_t", "weekday", "(uint8_t)(b[4] & 0x7)"),
                    ("uint8_t", "month", "(uint8_t)(((b[5] >> 4) & 0x1) * 10 + (b[5] & 0xF))"),
                    ("uint16_t", "year", "(uint16_t)(2000 + (b[6] >> 4) * 10 + (b[6] & 0xF))"),
                    ("uint8_t", "osc_stopped", "(uint8_t)(b[0] >> 7)"),
                ],
            }
        ],
    },
    "LM75": {
        "kind": "regs",
        "bus": "i2c",
        "address": 0x48,
        "address_note": "0x48 + A2:A0 straps, 0x48..0x4F (datasheet 'Slave address')",
        "datasheet": "NXP LM75B datasheet, section 7.4 (register map: Temp 0x00, Conf 0x01, "
        "Thyst 0x02, Tos 0x03) and 7.4.3 (11-bit temperature, 0.125 degC/LSB)",
        "registers": [
            ("TEMP", 0x00, "temperature, 11 bits left-justified in 16, 0.125 degC/LSB"),
            ("CONF", 0x01, "bit 0 SHUTDOWN, bit 1 OS comparator/interrupt, bit 2 OS polarity"),
            ("THYST", 0x02, "hysteresis, 9 bits, 0.5 degC/LSB"),
            ("TOS", 0x03, "over-temperature shutdown threshold, 9 bits"),
        ],
        "init": [("CONF", 0x00, "normal operation, comparator mode")],
        "reads": [
            {
                "name": "temperature",
                "doc": "temperature in degC",
                "start": "TEMP",
                "length": 2,
                "fields": [
                    (
                        "float",
                        "temperature_c",
                        "(float)((int16_t)(((uint16_t)b[0] << 8) | b[1]) >> 5) * 0.125f",
                    ),
                ],
            }
        ],
    },
    "SSD1306": {
        "kind": "oled",
        "bus": "i2c",
        "address": 0x3C,
        "address_note": "0x3C with D/C# (SA0) low, 0x3D with SA0 high (datasheet 8.1.5.1)",
        "datasheet": "Solomon Systech SSD1306 datasheet, section 8.1.5.1 (I2C control byte: "
        "Co, D/C#) and section 9 (command table)",
        "control": {"command": 0x00, "data": 0x40},
        "width": 128,
        "height": 64,
        "init": [
            (0xAE, [], "display off (9.1.7)"),
            (0xD5, [0x80], "clock divide / oscillator (9.1.8)"),
            (0xA8, [0x3F], "multiplex ratio 64 (9.1.6)"),
            (0xD3, [0x00], "display offset 0 (9.1.5)"),
            (0x40, [], "start line 0 (9.1.4)"),
            (0x8D, [0x14], "charge pump on (2.1 of the application note)"),
            (0x20, [0x00], "horizontal addressing mode (9.1.3)"),
            (0xA1, [], "segment remap (9.1.1)"),
            (0xC8, [], "COM scan direction remapped"),
            (0xDA, [0x12], "COM pins configuration"),
            (0x81, [0xCF], "contrast"),
            (0xD9, [0xF1], "pre-charge period"),
            (0xDB, [0x40], "VCOMH deselect level"),
            (0xA4, [], "resume from RAM content"),
            (0xA6, [], "normal (non-inverted) display"),
            (0xAF, [], "display on"),
        ],
    },
    "DHT11": {
        "kind": "onewire",
        "bus": "gpio",
        "address": None,
        "datasheet": "Aosong DHT11 datasheet, sections 5 (communication process: 18 ms start "
        "low, 20-40 us release, 80 us response, bit = 26-28 us high for 0 / 70 us for 1) and "
        "5.2 (40-bit frame: RH int, RH dec, T int, T dec, checksum)",
        "start_low_ms": 18,
        "bit_threshold_us": 40,
    },
}
