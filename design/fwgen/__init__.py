"""固件生成：引脚映射、驱动片段、项目骨架。

## 这一包解决什么

「原理图对 → 板子做出来 → 固件能跑」这条链上，中间的翻译一直靠人：
`.ioc` 里的 `PA15.GPIO_Label=VL53-PWR-EN` 要手工变成 `#define VL53_PWR_EN`，
再手工写进 `main.c`。P20 把这一段机器化，并且**顺手做一件人常忘的事**：
把固件里实际用到的引脚和硬件配置对撞（幽灵引脚 / 闲置 / 方向冲突）。

## 哪些是搬的，哪些是新的

| 模块 | 来源 | 改动 |
|---|---|---|
| `drivers_data.py` | Traceforge（MIT） | 原样；每个器件保留 datasheet 章节引用 |
| `drivers.py` | Traceforge（MIT） | 原样 |
| `scaffold.py` | Traceforge（MIT） | 导入改本包；5 个 target 不变 |
| `mcus.py` | Traceforge（MIT） | 3 → 7 个 MCU（补上 `.ioc` 里真实用的 5 个 STM32） |
| `netlist.py` | **重写** | 原实现读 KiCad/yaml；我们只读 `design.json`，压成它下游要的形状 |
| `pinmap.py` | Traceforge（MIT）+ **增量** | 加 `.ioc` 入口、`value` 字段、HAL 别名、交叉核对 |

**没搬**：`cli.py`（我们有自己的 `design_cli`，它依赖 `rich`）、
`toolchain.py`（`design/toolchain.py` 已有探针与产线导出，再搬一份会分叉）。

## 这里的每一条结论都带"凭什么"

`drivers_data.py` 里每个寄存器都写 `(5.4.3)` 这种章节号；`mcus.py` 每条写
datasheet 表号。**没有出处的数字不写** —— 猜一个寄存器地址，生成的代码会
往错误的寄存器写值，那比不生成更糟。
"""

from __future__ import annotations

__all__ = ["drivers_data", "drivers", "mcus", "netlist", "pinmap", "scaffold"]
