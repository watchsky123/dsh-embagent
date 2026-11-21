"""封装族 → 尺寸表的桥 —— 把 LCSC 的封装字符串翻译成 mm。

## 为什么需要独立一张表

LCSC 的 `componentSpecificationEn` 是**字符串**（`"0402"` / `"SOIC-8-208mil"`
/ `"LQFP-48(7x7)"` / `"SOT-23-3"`），而 `design.json` 的 `size_mm` 要三个数。
没有这张表，`parts-enrich` 拿回来的数据落不进设计。

**常见封装是标准化的**——这正是"快速复现 2D"可行的原因：一张 ~60 行的表
覆盖绝大多数芯片，不必为每个型号量尺寸。

## 三种来源，必须可区分

| `source` | 含义 | 进 `size_mm`？ |
|---|---|---|
| `jedec` | JEDEC/厂商封装图上的名义尺寸（人工录入） | 是，标 `catalog` 级 |
| `formula` | 按族公式 + 引脚数 + 引脚间距算出来 | 是，但**必须**标 `estimated` |
| `unknown` | 表里没有这个封装 | **否**——返回 `None`，不编 |

## 为什么公式要单独标出来

`SOIC-8-208mil` 的体宽是名义值（208 mil = 5.28 mm），体长却随引脚数变。
表里能列全常见组合，列不全的用 `L = (pins/2 - 1) * pitch + 2 * end_margin`
估。**估出来的数不能和名义值混为一谈**——所以 `source` 字段一路带到
`size_source`，最后由 `physical_source` 决定它能不能进硬几何检查。

## 数字的来源

名义尺寸取自 JEDEC 外形图与常见厂商封装页（一次性录入）。这里**不用**
LCSC 的 `encapsulationNumber` 猜——那个字段是"每盘多少颗"，不是尺寸。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PACKAGE_FAMILIES",
    "PackageSize",
    "describe",
    "lookup",
    "normalize",
]

#: 片式元件（电阻/电容/电感）。键是**英制**代号；公制代号另有一张对照。
CHIP_SIZES: dict[str, tuple[float, float, float]] = {
    # 英制代号 → (长, 宽, 高) mm
    "01005": (0.4, 0.2, 0.2),
    "0201": (0.6, 0.3, 0.3),
    "0402": (1.0, 0.5, 0.5),
    "0603": (1.6, 0.8, 0.8),
    "0805": (2.0, 1.25, 1.25),
    "1206": (3.2, 1.6, 1.6),
    "1210": (3.2, 2.5, 2.5),
    "1812": (4.5, 3.2, 3.2),
    "2010": (5.0, 2.5, 2.5),
    "2512": (6.3, 3.2, 3.2),
}

#: 公制代号 → 英制代号（同一个封装两种叫法，LCSC 两种都用）。
METRIC_TO_IMPERIAL = {
    "0402": "01005", "0603": "0201", "1005": "0402", "1608": "0603",
    "2012": "0805", "3216": "1206", "3225": "1210", "4532": "1812",
    "5025": "2010", "6432": "2512",
}

#: 表里的封装族。键 = 归一化后的族名。
#:
#: `w` = 体宽（引脚出来的两侧之间），`h` = 体长（沿引脚排列方向），
#: `d` = 高。**单位 mm，名义值**。
PACKAGE_FAMILIES: dict[str, dict[str, Any]] = {
    # -- 分立半导体 ------------------------------------------------------
    "SOT-23": {"w": 1.4, "h": 2.9, "d": 1.1, "pitch": 0.95, "pins": 3,
               "source": "jedec", "note": "SOT-23-3（pin1 独立，2/3 同侧）"},
    "SOT-23-5": {"w": 1.6, "h": 2.9, "d": 1.1, "pitch": 0.95, "pins": 5,
                 "source": "jedec"},
    "SOT-23-6": {"w": 1.6, "h": 2.9, "d": 1.1, "pitch": 0.95, "pins": 6,
                 "source": "jedec"},
    "SOT-89": {"w": 1.5, "h": 4.5, "d": 1.5, "pitch": 1.5, "pins": 3,
               "source": "jedec"},
    "SOT-223": {"w": 3.5, "h": 6.5, "d": 1.6, "pitch": 2.3, "pins": 4,
                "source": "jedec"},
    "SOT-363": {"w": 1.25, "h": 2.1, "d": 0.9, "pitch": 0.65, "pins": 6,
                "source": "jedec"},
    "SOD-123": {"w": 1.6, "h": 2.7, "d": 1.1, "pitch": 0.0, "pins": 2,
                "source": "jedec"},
    "SOD-323": {"w": 1.25, "h": 1.7, "d": 1.0, "pitch": 0.0, "pins": 2,
                "source": "jedec"},
    "SOD-523": {"w": 0.8, "h": 1.2, "d": 0.6, "pitch": 0.0, "pins": 2,
                "source": "jedec"},
    "SMA": {"w": 2.6, "h": 4.3, "d": 2.3, "pitch": 0.0, "pins": 2,
            "source": "jedec", "note": "DO-214AC"},
    "SMB": {"w": 3.3, "h": 4.3, "d": 2.3, "pitch": 0.0, "pins": 2,
            "source": "jedec", "note": "DO-214AA"},
    "SMC": {"w": 6.0, "h": 7.9, "d": 2.3, "pitch": 0.0, "pins": 2,
            "source": "jedec", "note": "DO-214AB"},
    "TO-252": {"w": 6.1, "h": 9.8, "d": 2.3, "pitch": 4.6, "pins": 3,
               "source": "jedec", "note": "DPAK"},
    "TO-220": {"w": 10.1, "h": 15.5, "d": 4.5, "pitch": 2.54, "pins": 3,
               "source": "jedec"},
    # -- 双列小外形 ------------------------------------------------------
    "SOIC-8": {"w": 3.9, "h": 4.9, "d": 1.75, "pitch": 1.27, "pins": 8,
               "source": "jedec", "note": "窄体 150mil"},
    "SOIC-14": {"w": 3.9, "h": 8.65, "d": 1.75, "pitch": 1.27, "pins": 14,
                "source": "jedec"},
    "SOIC-16": {"w": 3.9, "h": 9.9, "d": 1.75, "pitch": 1.27, "pins": 16,
                "source": "jedec"},
    "SOIC-8W": {"w": 5.28, "h": 5.2, "d": 1.75, "pitch": 1.27, "pins": 8,
                "source": "jedec", "note": "宽体 208mil（SOIC-8-208mil）"},
    "SOIC-16W": {"w": 7.5, "h": 10.3, "d": 2.65, "pitch": 1.27, "pins": 16,
                 "source": "jedec", "note": "宽体 300mil"},
    "TSSOP-8": {"w": 4.4, "h": 3.0, "d": 1.1, "pitch": 0.65, "pins": 8,
                "source": "jedec"},
    "TSSOP-14": {"w": 4.4, "h": 5.0, "d": 1.1, "pitch": 0.65, "pins": 14,
                 "source": "jedec"},
    "TSSOP-16": {"w": 4.4, "h": 5.1, "d": 1.1, "pitch": 0.65, "pins": 16,
                 "source": "jedec"},
    "TSSOP-20": {"w": 4.4, "h": 6.5, "d": 1.1, "pitch": 0.65, "pins": 20,
                 "source": "jedec"},
    "TSSOP-24": {"w": 4.4, "h": 7.8, "d": 1.1, "pitch": 0.65, "pins": 24,
                 "source": "jedec"},
    # -- 四边引脚 --------------------------------------------------------
    "LQFP-32": {"w": 7.0, "h": 7.0, "d": 1.4, "pitch": 0.8, "pins": 32,
                "source": "jedec", "note": "7×7"},
    "LQFP-44": {"w": 10.0, "h": 10.0, "d": 1.4, "pitch": 0.8, "pins": 44,
                "source": "jedec", "note": "10×10"},
    "LQFP-48": {"w": 7.0, "h": 7.0, "d": 1.4, "pitch": 0.5, "pins": 48,
                "source": "jedec", "note": "7×7×1.4"},
    "LQFP-64": {"w": 10.0, "h": 10.0, "d": 1.4, "pitch": 0.5, "pins": 64,
                "source": "jedec", "note": "10×10"},
    "LQFP-100": {"w": 14.0, "h": 14.0, "d": 1.4, "pitch": 0.5, "pins": 100,
                 "source": "jedec", "note": "14×14"},
    "QFN-16": {"w": 3.0, "h": 3.0, "d": 0.8, "pitch": 0.5, "pins": 16,
               "source": "jedec", "note": "3×3"},
    "QFN-20": {"w": 4.0, "h": 4.0, "d": 0.8, "pitch": 0.5, "pins": 20,
               "source": "jedec", "note": "4×4"},
    "QFN-24": {"w": 4.0, "h": 4.0, "d": 0.8, "pitch": 0.5, "pins": 24,
               "source": "jedec", "note": "4×4"},
    "QFN-28": {"w": 5.0, "h": 5.0, "d": 0.8, "pitch": 0.5, "pins": 28,
               "source": "jedec", "note": "5×5"},
    "QFN-32": {"w": 5.0, "h": 5.0, "d": 0.8, "pitch": 0.5, "pins": 32,
               "source": "jedec", "note": "5×5"},
    "QFN-48": {"w": 7.0, "h": 7.0, "d": 0.85, "pitch": 0.5, "pins": 48,
               "source": "jedec", "note": "7×7"},
    # -- 通孔 ------------------------------------------------------------
    "DIP-8": {"w": 6.4, "h": 9.7, "d": 3.3, "pitch": 2.54, "pins": 8,
              "source": "jedec", "note": "300mil 体宽"},
    "DIP-14": {"w": 6.4, "h": 19.3, "d": 3.3, "pitch": 2.54, "pins": 14,
               "source": "jedec"},
    "DIP-16": {"w": 6.4, "h": 19.3, "d": 3.3, "pitch": 2.54, "pins": 16,
               "source": "jedec"},
    "DIP-28": {"w": 6.4, "h": 35.5, "d": 3.3, "pitch": 2.54, "pins": 28,
               "source": "jedec"},
}

#: 族公式：族名 → (引脚间距, 体宽, 高, 端部余量)。表里没列的引脚数用它估。
FORMULAS: dict[str, dict[str, float]] = {
    "SOIC": {"pitch": 1.27, "w": 3.9, "d": 1.75, "end": 1.05},
    "TSSOP": {"pitch": 0.65, "w": 4.4, "d": 1.1, "end": 1.0},
    "LQFP": {"pitch": 0.5, "w": 0.0, "d": 1.4, "end": 0.0},
    "QFN": {"pitch": 0.5, "w": 0.0, "d": 0.8, "end": 0.0},
    "DIP": {"pitch": 2.54, "w": 6.4, "d": 3.3, "end": 1.9},
}

#: 别名归一（LCSC 上见过多种写法）。
_ALIASES = {
    "SOP-8": "SOIC-8", "SOP8": "SOIC-8", "SOP-16": "SOIC-16",
    "SO-8": "SOIC-8", "SOIC8": "SOIC-8",
    "SOIC-8-208MIL": "SOIC-8W", "SOIC-8W": "SOIC-8W",
    "SOIC-16-300MIL": "SOIC-16W",
    "TSSOP20": "TSSOP-20", "TSSOP16": "TSSOP-16",
    "LQFP48": "LQFP-48", "LQFP64": "LQFP-64", "LQFP32": "LQFP-32",
    "LQFP100": "LQFP-100", "TQFP-48": "LQFP-48", "TQFP48": "LQFP-48",
    "QFN32": "QFN-32", "DFN-8": "SOIC-8",
    "SOT23": "SOT-23", "SOT23-3": "SOT-23", "SOT-23(3)": "SOT-23",
    "SOT23-5": "SOT-23-5", "SOT23-6": "SOT-23-6",
    "SOT223": "SOT-223", "SOT89": "SOT-89", "SOT363": "SOT-363",
    "DO-214AC": "SMA", "DO-214AA": "SMB", "DO-214AB": "SMC",
    "DPAK": "TO-252", "TO-252-2": "TO-252", "TO252": "TO-252",
}


@dataclass
class PackageSize:
    """一个封装的尺寸。``source`` 决定它能不能进硬几何检查。"""

    package: str = ""
    family: str = ""
    w: float = 0.0
    h: float = 0.0
    d: float = 0.0
    pins: int = 0
    pitch: float = 0.0
    source: str = "unknown"   # jedec | formula | unknown
    note: str = ""

    @property
    def size_mm(self) -> dict[str, float]:
        return {"w": round(self.w, 3), "h": round(self.h, 3), "d": round(self.d, 3)}

    @property
    def ok(self) -> bool:
        return self.source != "unknown" and self.w > 0 and self.h > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "family": self.family,
            "size_mm": self.size_mm,
            "pins": self.pins,
            "pitch": self.pitch,
            "source": self.source,
            "note": self.note,
        }


def normalize(raw: str) -> str:
    """把 LCSC 的封装字符串压成规范键。

    实测见过的形态：``"0402"``、``"0402 (1005 Metric)"``、``"SOIC-8-208mil"``、
    ``"LQFP-48(7x7)"``、``"SOT-23-3"``、``"SMD,0603"``。
    括号里的尺寸/单位先剥掉，再查别名表。
    """
    text = str(raw or "").strip().upper()
    if not text:
        return ""
    # 剥括号内容："LQFP-48(7X7)" → "LQFP-48"
    text = re.sub(r"\([^)]*\)", "", text).strip()
    text = text.replace("，", ",").replace("、", ",")
    # 去掉常见前缀与空白
    for junk in ("SMD", "SMD,", "SMT", "SMT,", "PACKAGE", "封装"):
        text = text.replace(junk, "")
    text = text.strip(" ,-")
    text = re.sub(r"\s+", "", text)
    if text in _ALIASES:
        return _ALIASES[text]
    # "SOT-23-3" 与 "SOT-23" 是同一个封装（3 是引脚数）
    if re.fullmatch(r"SOT-?23[-_]?3", text):
        return "SOT-23"
    # 片式元件：纯数字（英制或公制）
    if re.fullmatch(r"\d{4,5}", text):
        if text in CHIP_SIZES:
            return text
        if text in METRIC_TO_IMPERIAL:
            return METRIC_TO_IMPERIAL[text]
    return text


def _pins_of(text: str) -> int:
    """从键里抠引脚数（``SOIC-8`` → 8，``LQFP-48`` → 48）。"""
    match = re.findall(r"(\d+)", text)
    # 片式元件的数字是尺寸不是引脚数，调用方不会走到这里
    return int(match[-1]) if match else 0


def lookup(package: str) -> PackageSize:
    """封装字符串 → 尺寸。查不到返回 ``source="unknown"`` **而不是猜**。

    这条路是 `parts-enrich` 的终点：查不到就说明"这个封装的尺寸我们不知道"，
    调用方据此把 `size_mm` 留空并标 `physical_source="estimated"`。
    """
    key = normalize(package)
    if not key:
        return PackageSize(package=str(package or ""), note="封装字符串为空")

    # 1) 片式元件
    if key in CHIP_SIZES:
        length, width, height = CHIP_SIZES[key]
        # **长轴放 X**：片式元件的两个焊盘沿长轴排开，默认贴装方向也是长轴沿 X。
        # 反过来（w=短轴）会让 0402 的 2D 投影变成竖着的，和实际贴装不符。
        return PackageSize(package=str(package), family="chip", w=length, h=width,
                           d=height, pins=2, pitch=0.0, source="jedec",
                           note=f"片式 {key}（英制代号；长轴沿 X）")

    # 2) 表里有
    row = PACKAGE_FAMILIES.get(key)
    if row is not None:
        return PackageSize(package=str(package), family=key, w=float(row["w"]),
                           h=float(row["h"]), d=float(row["d"]),
                           pins=int(row.get("pins") or 0),
                           pitch=float(row.get("pitch") or 0.0),
                           source=str(row.get("source") or "jedec"),
                           note=str(row.get("note") or ""))

    # 3) 族公式（标 formula，**不叫 jedec**）
    family_match = re.match(r"^(SOIC|TSSOP|LQFP|QFN|DIP)[-_]?(\d+)", key)
    if family_match:
        family = family_match.group(1)
        pins = int(family_match.group(2))
        rule = FORMULAS.get(family)
        if rule and pins >= 4:
            per_side = pins // 2
            height = (per_side - 1) * rule["pitch"] + 2 * rule["end"]
            if family in ("LQFP", "QFN"):
                # 四边封装：体宽≈体长，按 (n/4-1)*pitch + 2*end 估
                side = max(3.0, (pins / 4 - 1) * rule["pitch"] + 2 * 2.0)
                return PackageSize(package=str(package), family=f"{family}-{pins}",
                                   w=round(side, 2), h=round(side, 2),
                                   d=rule["d"], pins=pins, pitch=rule["pitch"],
                                   source="formula",
                                   note=f"{family}-{pins} 按族公式估（表里没有这一档）")
            return PackageSize(package=str(package), family=f"{family}-{pins}",
                               w=rule["w"], h=round(height, 2), d=rule["d"],
                               pins=pins, pitch=rule["pitch"], source="formula",
                               note=f"{family}-{pins} 按族公式估（体长随引脚数推）")

    return PackageSize(package=str(package), family=key,
                       note=f"封装表里没有 {key!r}（不猜尺寸）")


def describe() -> dict[str, Any]:
    """自省：表里有什么、公式覆盖什么。"""
    by_source: dict[str, int] = {}
    for row in PACKAGE_FAMILIES.values():
        key = str(row.get("source") or "?")
        by_source[key] = by_source.get(key, 0) + 1
    return {
        "module": "hw_velxio.design.package_sizes",
        "chip_sizes": len(CHIP_SIZES),
        "metric_aliases": len(METRIC_TO_IMPERIAL),
        "families": len(PACKAGE_FAMILIES),
        "by_source": by_source,
        "formulas": sorted(FORMULAS),
        "aliases": len(_ALIASES),
        "honest": ("查不到封装返回 source=unknown 且 size_mm 全 0；"
                   "公式推出来的标 formula，不冒充 jedec 名义值"),
    }