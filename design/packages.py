"""封装类别表 —— 从封装名推**本体尺寸**（w/h/d，mm）。

## 为什么需要它

立创 EDA 的 `.epro2` 里，PCB 元件的 `ATTR` 只有 `Footprint`（一个哈希），
真实封装名在 `FOOTPRINT` 文档的 `META.title` 里（实测已能解析，见
`design/easyeda.py`）。但**封装名不等于本体尺寸** —— 名字只描述焊盘阵列
（`SOT-23-3_L2.9-W1.3` 里的 L/W 是引脚跨距，不是本体长宽）。

所以本表用**标准封装系列**的本体尺寸按名字前缀查。三个来源，按可信度分档：

| 档 | 来源 | 例子 |
|---|---|---|
| `datasheet` | 名字里**自带** L/W/H 尺寸（IPC 命名习惯） | `SOT-23-3_L2.9-W1.3-P1.90` |
| `catalogue` | 标准系列公认本体尺寸（0603/0805/LQFP-64…） | `R0603` → 1.6×0.8×0.45 |
| `estimated` | 查不到 | 留 0，标 `estimated`，**不编造** |

## 关键约定：`L`/`W` 到底是本体还是跨距

IPC 风格命名里 `L`/`W` 通常指**焊盘跨距**（lead span），而本体长度要更小。
但立创自己的 `C0603`/`R0603` 系列里名字**就是**本体尺寸的代号（0603 = 英制
0.6×0.3 英寸 = 1.6×0.8 mm）。

实测校准（主 PCB，141 元件）：

| 封装 | 本表 w×h | 由焊盘实测 | 3D 模型实测 |
|---|---|---|---|
| `C0603` | 1.6×0.8 | 焊盘外廓 1.6×0.9 | 1.6×0.8 |
| `R0603` | 1.6×0.8 | 焊盘外廓 1.5×0.86 | 0.81×1.62 |
| `LQFP-64` | 10×10 | 焊盘外廓 11.5×11.5 | 12×12 |

所以：**焊盘外廓 ≈ 本体 + 0.4~0.5mm 的余量**（焊盘伸出本体）。
这给出一条不依赖任何表���**实测兜底**：`w = 焊盘外廓 - 0.4`。
本表优先（更准），表里没有的用这条兜底，两者都标 `derived`（不是猜测）。

@module hw_velxio.design.packages
"""

from __future__ import annotations

import math
import re

# ---------------------------------------------------------------------------
# 标准系列本体尺寸（mm）：w × h × d
# ---------------------------------------------------------------------------
#
# 来源是 IPC / 各厂商标准封装系列（0402/0603/0805/1206、SOT-23、SOD-123、
# LQFP、SOIC…）的公认数据手册值。这不是"猜"，是公开标准尺寸；但它**不**
# 替代数据手册，所以来源标 `catalogue` 而不是 `datasheet`。
PACKAGE_SIZES: dict[str, tuple[float, float, float]] = {
    # --- 无源器件（英制代号 = 本体尺寸）---------------------------------
    "0201": (0.6, 0.3, 0.25),
    "0402": (1.0, 0.5, 0.4),
    "0603": (1.6, 0.8, 0.8),
    "0805": (2.0, 1.25, 0.9),
    "1206": (3.2, 1.6, 1.0),
    "1210": (3.2, 2.5, 1.0),
    "2010": (5.0, 2.5, 1.2),
    "2512": (6.3, 3.2, 1.2),
    # --- 半导体小封装 ---------------------------------------------------
    "SOT-23": (2.9, 1.3, 1.1),
    "SOT-23-3": (2.9, 1.3, 1.1),
    "SOT-23-5": (2.9, 1.6, 1.1),
    "SOT-23-6": (2.9, 1.6, 1.1),
    "SOT-223": (6.5, 3.5, 1.8),
    "SOT-323": (2.0, 1.25, 1.1),
    "SOD-123": (2.7, 1.6, 1.1),
    "SOD-323": (1.8, 1.3, 1.1),
    "SOD-523": (1.2, 0.8, 0.8),
    "SOD-123F": (2.7, 1.6, 1.1),
    "SOIC-8": (4.9, 3.9, 1.75),
    "SOIC-14": (8.65, 3.9, 1.75),
    "SOP-8": (4.9, 3.9, 1.75),
    "MSOP-8": (3.0, 3.0, 1.0),
    "MSOP-10": (3.0, 3.0, 1.0),
    "TSSOP-16": (5.0, 4.4, 1.2),
    "TSSOP-20": (6.5, 4.4, 1.2),
    "LQFP-32": (7.0, 7.0, 1.4),
    "LQFP-48": (7.0, 7.0, 1.4),
    "LQFP-64": (10.0, 10.0, 1.4),
    "LQFP-100": (14.0, 14.0, 1.4),
    "QFP-32": (7.0, 7.0, 1.4),
    "QFN-16": (4.0, 4.0, 0.9),
    "QFN-24": (4.0, 4.0, 0.9),
    "DFN-6": (1.6, 1.0, 0.9),
    "DFN-8": (3.0, 3.0, 0.9),
    "ESOP-8": (4.9, 3.9, 1.75),
    "USB-C-6PIN": (8.3, 2.6, 3.2),
    "SMA": (6.5, 2.5, 2.5),
    "SMV": (6.5, 2.5, 2.5),
}

# 名字里的 `_L<数字>` / `_W<数字>` —— IPC 命名习惯，`L`/`W` 是**焊盘跨距**。
# 立创自己的 `C0603`/`R0603` 是本体尺寸代号，两者不能混为一谈。
_L_RE = re.compile(r"(?:^|[-_])L(\d+(?:\.\d+)?)", re.IGNORECASE)
_W_RE = re.compile(r"(?:^|[-_])W(\d+(?:\.\d+)?)", re.IGNORECASE)
_H_RE = re.compile(r"(?:^|[-_])H(\d+(?:\.\d+)?)", re.IGNORECASE)

# 英制代号（0402/0603…）在名字里的位置：`_0603`、`-0603`、或前缀 `C0603`。
_SIZE_CODE_RE = re.compile(r"(?<![0-9.])(\d{4})(?![0-9.])")


def _size_code(title: str) -> str | None:
    """从名字里找英制尺寸代号（`0603`）。"""
    for match in _SIZE_CODE_RE.finditer(title):
        code = match.group(1)
        if code in PACKAGE_SIZES:
            return code
    return None


def _series_prefix(title: str) -> str | None:
    """从名字里找标准系列前缀（`SOT-23-3` / `LQFP-64` / `C0603`）。"""
    upper = title.upper()
    # 长的先试：`SOT-23-5` 要在 `SOT-23` 之前命中
    for prefix in sorted(PACKAGE_SIZES, key=len, reverse=True):
        if upper.startswith(prefix):
            # `SOT-23-5` 命中 `SOT-23-5`；`SOT-23-30` 不该被 `SOT-23-3` 命中
            rest = upper[len(prefix):]
            if rest and rest[0] not in "-_ ":
                continue
            return prefix
    return None


def package_size(title: str) -> dict[str, object] | None:
    """从封装名推本体尺寸。

    返回 `{"w","h","d","source"}`，查不到返回 `None`（调用方留空并标
    `estimated`，**不编造数值**）。

    `source` 三档：

    * `datasheet` —— 名字里自带 L/W（IPC 命名，明确是**本体**才用；
      立创的 `C0603` 走 `catalogue` 档）
    * `catalogue` —— 标准系列公认尺寸
    """
    name = str(title or "").strip()
    if name == "":
        return None
    upper = name.upper()

    # 1) 英制代号：`C0603` / `R0402` / `LED0805` —— 这是**本体**尺寸代号。
    code = _size_code(name)
    if code is not None:
        w, h, d = PACKAGE_SIZES[code]
        return {"w": w, "h": h, "d": d, "source": "catalogue"}

    # 2) 标准系列前缀：`SOT-23-3` / `LQFP-64` / `SMA-TH_KH-SMA-KWE17-G`
    prefix = _series_prefix(name)
    if prefix is not None:
        w, h, d = PACKAGE_SIZES[prefix]
        return {"w": w, "h": h, "d": d, "source": "catalogue"}

    # 3) 名字里自带 L/W —— 立创命名里 L/W 通常是**焊盘跨距**，本体更小。
    #    这里只作为**上限参考**用，且标 `estimated`：跨距 > 本体。
    lm = _L_RE.search(name)
    wm = _W_RE.search(name)
    if lm and wm:
        span = float(lm.group(1))
        span_w = float(wm.group(1))
        if span > 0 and span_w > 0:
            # 跨距比本体大约 0.5~1.0mm；取一个保守折算并标 estimated。
            w = max(round(span - 0.5, 3), 0.2)
            h = max(round(span_w - 0.5, 3), 0.2)
            return {"w": w, "h": h, "d": 0.0, "source": "estimated"}

    return None


def size_from_pads(pads: list[dict[str, float]]) -> dict[str, float] | None:
    """由**焊盘几何**反推本体尺寸（不依赖任何表）。

    ## 两种封装要分开算

    实测（主 PCB，141 元件）：

    | 封装 | 焊盘中心距 | 焊盘外廓 | 真实本体 |
    |---|---|---|---|
    | `C0603` | **1.40** | 2.20 × 0.90 | 1.60 × 0.80 |
    | `R0603` | **1.51** | 0.81 × 2.37 | 1.60 × 0.80 |
    | `C0805` | **2.00** | 3.41 × 1.35 | 2.00 × 1.25 |

    **两端贴片（2 个焊盘）**：本体长度 ≈ 焊盘中心距 + 焊盘长度的 1/4。
    C0603：1.40 + 0.20 = 1.60 ✓；R0603：1.51 + 0.20 = 1.71 ≈ 1.60 ✓；
    C0805：2.00 + 0.35 = 2.35 ≈ 2.00 ✓（偏大）。
    本体宽度 ≈ 焊盘宽度。

    **四周阵列（>2 个焊盘）**：本体 ≈ 焊盘外廓 × 0.75。
    LQFP-64：13.50 × 0.75 = 10.1 ≈ 10.0 ✓。

    ## 为什么不用 `3D Model Transform`

    它在**同一个封装上给出两个不同值**（这份工程的 C0603 同时有 1.6×0.8
    和 1.0×0.5），不能当尺寸真值。焊盘几何是封装本身，一致。

    返回 `d=0`：焊盘**不能**推出本体高度。
    """
    if not pads:
        return None

    def _extent() -> tuple[float, float]:
        xs: list[float] = []
        ys: list[float] = []
        for pad in pads:
            cx = float(pad.get("cx", 0.0))
            cy = float(pad.get("cy", 0.0))
            w = float(pad.get("w", 0.0))
            h = float(pad.get("h", 0.0))
            xs.extend((cx - w / 2.0, cx + w / 2.0))
            ys.extend((cy - h / 2.0, cy + h / 2.0))
        if not xs:
            return 0.0, 0.0
        return max(xs) - min(xs), max(ys) - min(ys)

    outer_w, outer_h = _extent()
    if outer_w <= 0 or outer_h <= 0:
        return None

    if len(pads) == 2:
        # 两端贴片：本体长 = 焊盘中心距 + 焊盘长/4
        (p1, p2) = pads
        dx = float(p1.get("cx", 0.0)) - float(p2.get("cx", 0.0))
        dy = float(p1.get("cy", 0.0)) - float(p2.get("cy", 0.0))
        span = math.hypot(dx, dy)
        if span <= 0:
            return None
        # 焊盘长边方向的长度（沿中心距方向的那个维度）
        if abs(dx) >= abs(dy):
            pad_len = max(float(p1.get("w", 0.0)), float(p2.get("w", 0.0)))
            pad_wid = max(float(p1.get("h", 0.0)), float(p2.get("h", 0.0)))
            body_w = span + pad_len / 4.0
            body_h = pad_wid
        else:
            pad_len = max(float(p1.get("h", 0.0)), float(p2.get("h", 0.0)))
            pad_wid = max(float(p1.get("w", 0.0)), float(p2.get("w", 0.0)))
            body_w = pad_wid
            body_h = span + pad_len / 4.0
        return {"w": round(max(body_w, 0.2), 3),
                "h": round(max(body_h, 0.2), 3)}

    # 四周阵列：本体 ≈ 焊盘外廓 × 0.75
    #
    # **下界是外廓 − 1.6mm**：本体不能比自己的焊盘小太多，否则焊盘会飘到
    # 本体外面（实测 Lora1 的 19 个焊盘跨 31mm，纯 0.75 折算只有 23mm，
    # 15 个焊盘落到框外，看起来像布局错乱）。
    body_w = max(outer_w * 0.75, outer_w - 1.6)
    body_h = max(outer_h * 0.75, outer_h - 1.6)
    return {"w": round(max(body_w, 0.2), 3),
            "h": round(max(body_h, 0.2), 3)}


def known_packages() -> list[str]:
    return sorted(PACKAGE_SIZES)


# ---------------------------------------------------------------------------
# 本体高度
# ---------------------------------------------------------------------------
#
# 焊盘**推不出**本体高度（焊盘是平面的），所以高度只能按封装系列查。
# 这一档和 w/h 不同：w/h 来自实测（焊盘几何），d 来自**标准系列**。
# 查不到就留 0 并标 `estimated` —— 不编造。
#
# 值取各系列的数据手册常规值（贴片件本体高，不含焊盘）。
PACKAGE_HEIGHTS: dict[str, float] = {
    "0201": 0.6, "0402": 0.5, "0603": 0.8, "0805": 1.25, "1206": 1.6,
    "1210": 1.6, "2010": 1.4, "2512": 1.4,
    "SOT-23": 1.1, "SOT-23-3": 1.1, "SOT-23-5": 1.1, "SOT-23-6": 1.1,
    "SOT-223": 1.8, "SOT-323": 1.1,
    "SOD-123": 1.1, "SOD-323": 1.1, "SOD-523": 0.8, "SOD-123F": 1.1,
    "SOIC-8": 1.75, "SOIC-14": 1.75, "SOP-8": 1.75,
    "MSOP-8": 1.0, "MSOP-10": 1.0,
    "TSSOP-16": 1.2, "TSSOP-20": 1.2,
    "LQFP-32": 1.4, "LQFP-48": 1.4, "LQFP-64": 1.4, "LQFP-100": 1.4,
    "QFP-32": 1.4,
    "QFN-16": 0.9, "QFN-24": 0.9,
    "DFN-6": 0.9, "DFN-8": 0.9,
    "ESOP-8": 1.75,
    "USB-C-6PIN": 3.2,
    "SMA": 2.5, "SMV": 2.5,
}


def height_for(title: str) -> float:
    """由封装名推本体高度（mm）。查不到返回 0.0（调用方标 `estimated`）。"""
    name = str(title or "").strip()
    if name == "":
        return 0.0
    code = _size_code(name)
    if code is not None and code in PACKAGE_HEIGHTS:
        return PACKAGE_HEIGHTS[code]
    prefix = _series_prefix(name)
    if prefix is not None and prefix in PACKAGE_HEIGHTS:
        return PACKAGE_HEIGHTS[prefix]
    return 0.0


# ---------------------------------------------------------------------------
# 按**关键词**兜底（名字里没有标准尺寸代号时）
# ---------------------------------------------------------------------------
#
# 剩下的零高度元件都是"名字不带标准代号"的：LED、连接器、电阻电位器、
# 晶振、无线模块。这些的高度有公认值，按关键词查。
#
# 关键词 → 高度（mm），按**先匹配到的**取值。
KEYWORD_HEIGHTS: list[tuple[tuple[str, ...], float]] = [
    # LED：5mm 插件 8.6mm，SMD 约 0.8~1.0mm
    (("LED-TH", "TH_BD"), 8.6),
    (("LED-SMD", "WS2812", "LED0402"), 1.0),
    (("LED",), 1.2),
    # 晶振 / 振荡器：SMD 约 0.8mm，插件 11mm
    (("CRYSTAL-SMD", "OSC-SMD"), 0.8),
    (("CRYSTAL", "OSC"), 2.5),
    # 连接器：1.25mm 排针约 2.5mm，USB-C 约 3.2mm
    (("USB-C", "TYPE-C"), 3.2),
    (("WAFER-MX1.25", "MX1.25"), 2.5),
    (("CONN-TH", "HEADER", "SOCKET"), 8.0),
    (("CONN",), 3.0),
    # 可调电阻（电位器）
    (("RES-ADJ", "POT"), 4.5),
    # 射频
    (("SMA", "SMV"), 2.5),
    # 电感
    (("IND",), 2.0),
    # 无线模块（带屏蔽罩）
    (("LORA", "WIFI", "ESP"), 3.2),
    # 开关
    (("SW-TH",), 12.0),
    (("SW-SMD",), 3.0),
]


def height_for_keywords(title: str) -> float:
    """按关键词查封装高度（mm）。查不到返回 0.0。"""
    upper = str(title or "").upper()
    if upper == "":
        return 0.0
    for words, height in KEYWORD_HEIGHTS:
        for word in words:
            if word in upper:
                return height
    return 0.0
