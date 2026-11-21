"""能力窄表 —— 把"能不能干这件事"压成一次字典查询。

## 为什么是字典，不是知识图谱

用户已决定：**生成器只对新工程有效**，不为 EasyEDA 哈希 kind 补映射。

在这个前提下，搜索空间有多大？不是 10²⁰。需求里的角色是有限的
（`roles.py` 有 14 个），每个角色能落到几十个型号，加上约束过滤，
枚举上限设 50 就够。图的多跳推理在这个规模上用不上，而维护成本
会吃掉全部收益——一张 30 行的表能解决的，不需要一个图数据库。

## 表里放什么，不放什么

**只放我们真能说出出处的数字。** 每条 `source` 必须是：

* `datasheet` —— 该型号的官方数据手册里有这个数
* `measured` —— 我们自己量过
* 其他（`inferred` / `guess` / 空）→ **`candidates()` 直接跳过**

这条规则是硬的：一个猜出来的 `footprint_mm2` 会让生成器认为"这个
型号装得下"，于是候选人拿到一个过不了几何检查的候选。
**猜出来的能力值 = 假通过。** 宁可返回空列表，说"没有可选型号"。

## 覆盖范围（诚实声明）

当前只收录本项目实际用到的型号族。没有收录的型号
`candidates()` 返回空 —— 那不是"不存在"，是"这张表没收录"，
调用方必须区分这两件事（见 `explain_miss()`）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: 能力表。键是型号（小写），值是能力描述。
#:
#: `pins` 只写**外设名 → 引脚名**，不写整份引脚表——生成器只需要知道
#: "这个外设能用哪几个脚"，不需要知道每个脚的复用功能全集。
CAPABILITY: dict[str, dict[str, Any]] = {
    # ---- MCU：本项目 7 块板上的主控族 ----
    "stm32f103c8t6": {
        "fills": {"mcu"},
        "provides": {"uart", "spi", "i2c", "can", "usb", "gpio", "pwm",
                     "adc"},
        "requires": {"power(3.3V,150mA)"},
        "footprint_mm2": 7.0 * 7.0,
        "pins": {"USART1": ["PA9", "PA10"], "USART2": ["PA2", "PA3"],
                 "USART3": ["PB10", "PB11"], "SPI1": ["PA5", "PA6", "PA7"],
                 "I2C1": ["PB6", "PB7"], "CAN1": ["PA11", "PA12"]},
        "clock_hz": 72_000_000,
        "source": "datasheet",
        "note": "LQFP48 7×7mm；F103 中容量，72MHz",
    },
    "stm32f401ccu6": {
        "fills": {"mcu"},
        "provides": {"uart", "spi", "i2c", "usb", "gpio", "pwm", "adc"},
        "requires": {"power(3.3V,120mA)"},
        "footprint_mm2": 7.0 * 7.0,
        "pins": {"USART1": ["PA9", "PA10"], "USART2": ["PA2", "PA3"],
                 "USART6": ["PC6", "PC7"], "SPI1": ["PA5", "PA6", "PA7"],
                 "I2C1": ["PB6", "PB7"]},
        "clock_hz": 84_000_000,
        "source": "datasheet",
        "note": "UFQFPN48 7×7mm；F401，84MHz",
    },
    "stm32f405rgt6": {
        "fills": {"mcu"},
        "provides": {"uart", "spi", "i2c", "can", "usb", "gpio", "pwm",
                     "adc"},
        "requires": {"power(3.3V,200mA)"},
        "footprint_mm2": 10.0 * 10.0,
        "pins": {"USART1": ["PA9", "PA10"], "USART2": ["PA2", "PA3"],
                 "USART3": ["PB10", "PB11"], "USART6": ["PC6", "PC7"],
                 "SPI1": ["PA5", "PA6", "PA7"], "I2C1": ["PB6", "PB7"],
                 "CAN1": ["PB8", "PB9"]},
        "clock_hz": 168_000_000,
        "source": "datasheet",
        "note": "LQFP64 10×10mm；F405，168MHz",
    },
    "stm32h743vit6": {
        "fills": {"mcu"},
        "provides": {"uart", "spi", "i2c", "can", "usb", "gpio", "pwm",
                     "adc", "eth"},
        "requires": {"power(3.3V,400mA)"},
        "footprint_mm2": 14.0 * 14.0,
        "pins": {"USART1": ["PA9", "PA10"], "USART2": ["PD5", "PD6"],
                 "USART3": ["PD8", "PD9"], "SPI1": ["PA5", "PA6", "PA7"],
                 "I2C1": ["PB6", "PB7"], "CAN1": ["PD0", "PD1"]},
        "clock_hz": 480_000_000,
        "source": "datasheet",
        "note": "LQFP100 14×14mm；H743，480MHz",
    },
    "atmega328p": {
        "fills": {"mcu"},
        "provides": {"uart", "spi", "i2c", "gpio", "pwm", "adc"},
        "requires": {"power(5V,50mA)"},
        "footprint_mm2": 7.0 * 7.0,
        "pins": {"USART0": ["PD0", "PD1"], "SPI0": ["PB3", "PB4", "PB5"],
                 "I2C0": ["PC4", "PC5"]},
        "clock_hz": 16_000_000,
        "source": "datasheet",
        "note": "TQFP32 7×7mm；Arduino Uno 用的就是它，16MHz",
    },
    "esp32s3": {
        "fills": {"mcu", "radio"},
        "provides": {"uart", "spi", "i2c", "gpio", "pwm", "adc", "wifi",
                     "ble"},
        "requires": {"power(3.3V,500mA)"},
        "footprint_mm2": 7.0 * 7.0,
        "pins": {"UART0": ["GPIO43", "GPIO44"], "UART1": ["GPIO17", "GPIO18"],
                 "SPI2": ["GPIO12", "GPIO13", "GPIO14"],
                 "I2C0": ["GPIO8", "GPIO9"]},
        "clock_hz": 240_000_000,
        "source": "datasheet",
        "note": "模组尺寸按 ESP32-S3-WROOM-1 的 7×7 核心计；含 Wi-Fi",
    },
    # ---- 模块 ----
    "lora-sx1278": {
        "fills": {"radio"},
        "provides": {"spi", "gpio", "rf"},
        "requires": {"power(3.3V,120mA)"},
        "footprint_mm2": 17.0 * 17.0,
        "pins": {"SPI": ["SCK", "MISO", "MOSI", "NSS"]},
        "clock_hz": None,
        "source": "datasheet",
        "note": "常见 Ra-01/SX1278 模组 17×17mm",
    },
    "gps-neo6m": {
        "fills": {"sensor"},
        "provides": {"uart", "gpio"},
        "requires": {"power(3.3V,60mA)"},
        "footprint_mm2": 25.0 * 25.0,
        "pins": {"UART": ["TX", "RX"]},
        "clock_hz": None,
        "source": "datasheet",
        "note": "NEO-6M 模组 25×25mm，串口输出 NMEA",
    },
    "imu-mpu6050": {
        "fills": {"sensor"},
        "provides": {"i2c", "gpio"},
        "requires": {"power(3.3V,10mA)"},
        "footprint_mm2": 21.0 * 16.0,
        "pins": {"I2C": ["SCL", "SDA"]},
        "clock_hz": None,
        "source": "datasheet",
        "note": "GY-521 模组 21×16mm",
    },
    "bmp280": {
        "fills": {"sensor"},
        "provides": {"i2c", "spi", "gpio"},
        "requires": {"power(3.3V,3mA)"},
        "footprint_mm2": 2.0 * 2.5,
        "pins": {"I2C": ["SCL", "SDA"]},
        "clock_hz": None,
        "source": "datasheet",
        "note": "LGA8 2×2.5mm",
    },
    "camera-ov2640": {
        "fills": {"sensor"},
        "provides": {"spi", "i2c", "gpio"},
        "requires": {"power(3.3V,120mA)"},
        "footprint_mm2": 21.0 * 21.0,
        "pins": {"SCCB": ["SIOC", "SIOD"], "DVP": ["PCLK", "VSYNC", "HREF"]},
        "clock_hz": None,
        "source": "datasheet",
        "note": "常见 OV2640 模组 21×21mm；SCCB 是 I2C 变种",
    },
    # ---- 电源 ----
    "ldo-ams1117-3.3": {
        "fills": {"power"},
        "provides": {"power"},
        "requires": {"power(5V,800mA)"},
        "footprint_mm2": 6.5 * 3.5,
        "pins": {"VIN": ["1"], "VOUT": ["2"], "GND": ["3"]},
        "clock_hz": None,
        "source": "datasheet",
        "note": "SOT-223；3.3V/1A 线性，需散热",
    },
    "buck-mp1584": {
        "fills": {"power"},
        "provides": {"power"},
        "requires": {"power(5V,3000mA)"},
        "footprint_mm2": 6.0 * 6.0,
        "pins": {"VIN": ["1"], "SW": ["2"], "FB": ["3"], "GND": ["4"]},
        "clock_hz": None,
        "source": "datasheet",
        "note": "SOIC-8；5V/3A 同步降压",
    },
    # ---- 执行器 ----
    "servo-sg90": {
        "fills": {"motor"},
        "provides": {"pwm"},
        "requires": {"power(5V,700mA)"},
        "footprint_mm2": 23.0 * 12.2,
        "pins": {"PWM": ["1"], "VCC": ["2"], "GND": ["3"]},
        "clock_hz": None,
        "source": "datasheet",
        "note": "堵转 700mA、静态 4mA —— 电流需求按堵转算",
    },
}

#: 可信的能力来源。**其他一律不参与选择**（见模块开头纪律）。
TRUSTED_SOURCES = frozenset({"datasheet", "measured"})

# ---------------------------------------------------------------------------
# 覆盖层（P19）：目录数据与人工确认的数据
# ---------------------------------------------------------------------------
#
# 内置表 `CAPABILITY` 是**手写的、每条都有出处**的 14 个型号。
# 目录（LCSC）能把它扩到目录级，但目录数据是 **catalog 级**：
#
#   estimated(0) < catalog(1) < datasheet(2) < measured(3)
#
# 所以覆盖层分成两个文件，**文件名本身就是信任级**：
#
#   catalog_parts.json       source="catalog"    进 rejected，不进选型
#   capability_promoted.json source="datasheet"  人工核对手册后升级
#
# 为什么分成两个文件而不是一个带 flag 的：升级是**移动**文件的事
# （从目录文件删、往确认文件写），让人一眼看得出哪些是"人点过头的"。
# 一个文件加 flag 的话，读的时候还得逐条看 flag，而写的时候容易漏改。

#: 目录级条目（未确认）。模块属性，测试可以指到临时文件。
CATALOG_FILE = Path(__file__).resolve().parent / "catalog_parts.json"
#: 人工确认过的条目（datasheet 级）。
PROMOTED_FILE = Path(__file__).resolve().parent / "capability_promoted.json"

#: 覆盖层缓存。`reset_overlay()` 之后重新读盘。
_OVERLAY: dict[str, dict[str, Any]] | None = None

#: 目录条目的原因文案 —— `explain_miss` 与验收都靠它认这一档。
CATALOG_REASON = "source=catalog 未确认（parts-promote --datasheet 后可进选型）"


def _read_entries(path: Path) -> dict[str, dict[str, Any]]:
    """宽松读一个覆盖文件。坏文件当没有——它是扩展，不是事实源。"""
    try:
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(rows, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in rows.items():
        if isinstance(value, dict) and value:
            out[str(key).lower()] = dict(value)
    return out


def overlay() -> dict[str, dict[str, Any]]:
    """`catalog_parts.json` + `capability_promoted.json` 合并视图。"""
    global _OVERLAY
    if _OVERLAY is None:
        merged: dict[str, dict[str, Any]] = {}
        merged.update(_read_entries(CATALOG_FILE))
        # 确认过的覆盖目录（同一个型号两边都有时以确认为准）
        merged.update(_read_entries(PROMOTED_FILE))
        _OVERLAY = merged
    return _OVERLAY


def reset_overlay() -> None:
    """丢掉缓存，下次重新读盘（写完之后、测试里都要调）。"""
    global _OVERLAY
    _OVERLAY = None


def entries() -> dict[str, dict[str, Any]]:
    """内置表 + 覆盖层。**所有判定都走这一个视图。**"""
    merged: dict[str, dict[str, Any]] = dict(CAPABILITY)
    merged.update(overlay())
    return merged


# -- 写盘（唯一的两个写入点，都在这里） ------------------------------------


def _jsonable(value: Any) -> Any:
    """set/tuple → 有序 list。**在写盘这一个点上做**，调用方不用记得。"""
    if isinstance(value, (set, frozenset, tuple)):
        return sorted(_jsonable(v) for v in value) if all(
            isinstance(v, str) for v in value) else [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _write_entries(path: Path, rows: dict[str, dict[str, Any]]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "entries": _jsonable(rows),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        reset_overlay()
        return True
    except OSError:
        return False


def save_catalog_entry(key: str, entry: dict[str, Any]) -> bool:
    """把一条目录数据写进 `catalog_parts.json`（**不变量**：强制 source=catalog）。

    这条不变量很重要：如果调用方传了 `source="datasheet"` 进来，
    就等于绕过"必须人确认"的闸门。这里直接覆盖成 catalog。
    """
    normalized = str(key or "").strip().lower()
    if not normalized:
        return False
    rows = {k: v for k, v in _read_entries(CATALOG_FILE).items()}
    merged = {**entry, "source": "catalog",
              "catalog_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    rows[normalized] = merged
    return _write_entries(CATALOG_FILE, rows)


def promote_entry(key: str, entry: dict[str, Any], datasheet: str) -> dict[str, Any]:
    """目录条目 → 确认条目。**必须带 datasheet URL 留痕。**

    返回 ``{ok, reason, entry}``。不满足条件就**不写**：
    没有手册页 = 没法说这个数据是从哪来的，那就还是目录级。
    """
    normalized = str(key or "").strip().lower()
    url = str(datasheet or "").strip()
    if not normalized:
        return {"ok": False, "reason": "型号为空"}
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "reason": "promote 必须给 http(s) 的 --datasheet 手册页（留痕）"}
    source_entry = {**_read_entries(CATALOG_FILE).get(normalized, {}), **entry}
    if not source_entry:
        return {"ok": False, "reason": f"目录里没有 {normalized!r}，先跑 parts-enrich --save"}
    promoted = {**source_entry, "source": "datasheet", "datasheet_url": url,
                "promoted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    promoted.pop("catalog_at", None)
    rows = {k: v for k, v in _read_entries(PROMOTED_FILE).items()}
    rows[normalized] = promoted
    ok = _write_entries(PROMOTED_FILE, rows)
    if ok:
        # 从目录文件里删掉：同一个型号不该两边都在
        catalog_rows = {k: v for k, v in _read_entries(CATALOG_FILE).items()
                        if k != normalized}
        _write_entries(CATALOG_FILE, catalog_rows)
    return {"ok": ok, "reason": "" if ok else "写盘失败", "entry": promoted}


def trusted(key: str) -> bool:
    """这个型号的数据是否可信到能参与硬选择。"""
    cap = entries().get(key)
    if cap is None:
        return False
    return str(cap.get("source") or "") in TRUSTED_SOURCES


def candidates(needs: set[str] | frozenset[str],
               constraints: dict[str, Any] | None = None,
               exclude: set[str] | frozenset[str] = frozenset(),
               role: str | None = None) -> list[str]:
    """纯过滤，毫秒级。没有图遍历。

    五条过滤，全部是硬条件：

      1. `role` 给了的话，型号的 `fills` 必须含它
      2. `needs ⊆ provides` —— 缺一项能力就不选
      3. `footprint_mm2 <= constraints.area_mm2` —— 面积上限
      4. `source ∈ {datasheet, measured}` —— **猜的不要**
      5. 不在 `exclude` 里

    ## 第 1 条（`fills`）为什么必须有

    第一版只按能力过滤，于是"radio 单元"选出了 `bmp280`
    ——一个气压计。它的能力集合（`spi`/`i2c`/`gpio`）确实覆盖
    了 radio 的需求（`spi`/`gpio`），过滤条件成立，**但语义是错的**。

    能力集合回答的是"它能干什么活"，`fills` 回答的是"它是什么东西"。
    两者不能互相替代：气压计能干 SPI 的活，但它不是电台。

    `fills` 是可核对的**类别事实**（看 datasheet 第一页就知道），
    不是猜出来的数值——所以它可以参与硬过滤，而
    `footprint_mm2` 那种必须写清 `source` 的数值要更严格。

    返回按型号名排序（确定性），不是按分数——排序要有依据，
    而"哪个更好"这件事没有依据时不该假装有。
    """
    constraints = constraints or {}
    area_limit = constraints.get("area_mm2")
    wanted = set(needs)

    view = entries()
    out: list[str] = []
    for key in sorted(view):
        if key in exclude:
            continue
        cap = view[key]
        if not trusted(key):
            continue
        if role is not None and role not in set(cap.get("fills") or ()):
            continue
        provides = set(cap.get("provides") or ())
        if not wanted <= provides:
            continue
        if area_limit is not None:
            footprint = cap.get("footprint_mm2")
            if not isinstance(footprint, (int, float)):
                continue
            if float(footprint) > float(area_limit):
                continue
        out.append(key)
    return out


def explain_miss(needs: set[str] | frozenset[str],
                 constraints: dict[str, Any] | None = None,
                 exclude: set[str] | frozenset[str] = frozenset(),
                 role: str | None = None) -> dict[str, Any]:
    """一条候选都没有时，**说清是哪一条过滤砍光的**。

    空列表有两种含义，必须分开：
      * "表里没有这种型号" —— 要补表
      * "有但被你自己的约束砍了" —— 要放宽约束

    不解释的话，人只会看到"生成失败"。
    """
    constraints = constraints or {}
    area_limit = constraints.get("area_mm2")
    wanted = set(needs)
    if not wanted:
        return {"reason": "no_needs", "detail": "没有声明需要什么能力",
                "candidates": 0}

    view = entries()
    checked = 0
    rejected: dict[str, str] = {}
    for key in sorted(view):
        if key in exclude:
            rejected[key] = "excluded"
            continue
        cap = view[key]
        checked += 1
        # **先查信任级，再查能力。** 顺序有语义：目录条目连 fills 都没有，
        # 先报「不是 mcu」会把真正的原因（未确认）藏起来——而那条才是
        # 唯一能让人行动的（去 promote）。
        if not trusted(key):
            rejected[key] = (CATALOG_REASON
                             if str(cap.get("source") or "") == "catalog"
                             else f"source={cap.get('source')!r} 不可信")
            continue
        if role is not None and role not in set(cap.get("fills") or ()):
            rejected[key] = f"不是 {role}（fills={sorted(cap.get('fills') or [])}）"
            continue
        if not wanted <= set(cap.get("provides") or ()):
            missing = sorted(wanted - set(cap.get("provides") or ()))
            rejected[key] = f"缺能力 {missing}"
            continue
        footprint = cap.get("footprint_mm2")
        if area_limit is not None and isinstance(footprint, (int, float)) \
                and float(footprint) > float(area_limit):
            rejected[key] = f"面积 {footprint} > {area_limit}"
            continue
    return {
        "reason": "filtered_out" if checked else "table_empty",
        "detail": (f"表里 {checked} 个型号全部被过滤；"
                   if checked else "能力表里没有任何型号；"),
        "needs": sorted(wanted),
        "constraints": dict(constraints),
        "rejected": rejected,
        "hint": ("补能力表（CAPABILITY）" if not checked
                 else "放宽 constraints 或去掉 exclude"),
    }


def get(key: str) -> dict[str, Any] | None:
    return entries().get(str(key).lower())


def describe() -> dict[str, Any]:
    view = entries()
    by_source: dict[str, int] = {}
    for cap in view.values():
        src = str(cap.get("source") or "")
        by_source[src] = by_source.get(src, 0) + 1
    return {
        "module": "hw_velxio.design.capability",
        "entries": len(view),
        "builtin": len(CAPABILITY),
        "overlay": len(view) - len(CAPABILITY),
        "by_source": by_source,
        "trusted_sources": sorted(TRUSTED_SOURCES),
        "model": "窄字典 + 纯过滤，不是知识图谱",
        "honest": ("source 不是 datasheet/measured 的型号直接不参与选择；"
                   "空结果会被 explain_miss 解释成'表没收录'或'约束太紧'"),
        "scope": "只对新工程有效（不为导入的哈希 kind 补映射）",
    }
