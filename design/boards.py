"""板卡预设：把 Velxio 的**像素**尺寸换算成规范模型需要的**毫米**。

Velxio 的 ``frontend/src/utils/boardGeometry.ts`` 只给像素（``BOARD_SIZE``），
其中一部分带毫米注释。本模块把两列对上，并算出一个**每块板固定**的
``px_per_mm = px / mm``——这不是"猜一个统一比例"，而是：

  * 旧工程加载时用同一比例反推，画布几何 1:1 不失真；
  * 一块设计内只有一个比例，2D 投影与 3D 实体因此同源；
  * 没有毫米注释的板，按同族比例反推并标 ``estimated``。

`source` 描述这份毫米尺寸的可信度，直接进入 3D/核对面板的展示。
"""

from __future__ import annotations

from typing import Any

from .model import Board, MountHole

# kind -> (name, w_mm, h_mm, px_w, px_h, thickness_mm, source)
#
# 毫米列来自 Velxio `boardGeometry.ts` 的注释；没有注释的按同族 px/mm
# （≈3.78 或 5.0）反推，标 estimated。
_RAW: dict[str, tuple[str, float, float, int, int, float, str]] = {
    "arduino-uno": ("Arduino Uno", 72.58, 53.34, 274, 202, 1.6, "datasheet"),
    "arduino-nano": ("Arduino Nano", 44.9, 17.8, 170, 67, 1.6, "datasheet"),
    "arduino-mega": ("Arduino Mega 2560", 102.66, 50.80, 388, 192, 1.6, "datasheet"),
    "raspberry-pi-pico": ("Raspberry Pi Pico", 21.0, 51.0, 105, 264, 1.0, "datasheet"),
    "pi-pico-w": ("Raspberry Pi Pico W", 21.0, 51.0, 105, 264, 1.0, "datasheet"),
    "esp32": ("ESP32 DevKit V1", 28.2, 53.0, 141, 265, 1.6, "datasheet"),
    "esp32-s3": ("ESP32-S3 DevKitC-1", 25.5, 70.0, 128, 350, 1.6, "datasheet"),
    "esp32-c3": ("ESP32-C3 DevKitM-1", 25.4, 42.9, 127, 215, 1.6, "datasheet"),
    "stm32-bluepill": ("STM32 Blue Pill", 22.855, 54.193, 114, 271, 1.6, "datasheet"),
    "stm32-blackpill": ("STM32 Black Pill", 20.695, 53.125, 103, 266, 1.6, "datasheet"),
    "esp32-devkit-c-v4": ("ESP32 DevKitC V4", 28.0, 56.6, 140, 283, 1.6, "estimated"),
    "esp32-cam": ("ESP32-CAM", 27.2, 40.4, 136, 202, 1.6, "estimated"),
    "wemos-lolin32-lite": ("Wemos Lolin32 Lite", 25.6, 50.0, 128, 250, 1.6, "estimated"),
    "xiao-esp32-s3": ("Seeed XIAO ESP32-S3", 18.2, 23.4, 91, 117, 1.0, "estimated"),
    "xiao-esp32-c3": ("Seeed XIAO ESP32-C3", 18.2, 23.4, 91, 117, 1.0, "estimated"),
    "arduino-nano-esp32": ("Arduino Nano ESP32", 43.4, 18.0, 217, 90, 1.6, "estimated"),
    "aitewinrobot-esp32c3-supermini": ("ESP32-C3 SuperMini", 18.0, 24.6, 90, 123, 1.0, "estimated"),
    "raspberry-pi-zero": ("Raspberry Pi Zero", 65.0, 41.6, 250, 160, 1.6, "estimated"),
    "raspberry-pi-1": ("Raspberry Pi 1", 65.0, 41.6, 250, 160, 1.6, "estimated"),
    "raspberry-pi-2": ("Raspberry Pi 2", 65.0, 41.6, 250, 160, 1.6, "estimated"),
    "raspberry-pi-3": ("Raspberry Pi 3", 65.0, 41.6, 250, 160, 1.6, "estimated"),
    "raspberry-pi-4": ("Raspberry Pi 4", 85.0, 56.0, 330, 215, 1.6, "datasheet"),
    "raspberry-pi-5": ("Raspberry Pi 5", 85.0, 56.7, 330, 220, 1.6, "datasheet"),
    "attiny85": ("ATtiny85 (DIP-8)", 32.0, 26.4, 160, 132, 1.6, "estimated"),
    "breadboard": ("Half Breadboard", 83.0, 55.0, 314, 208, 8.5, "datasheet"),
}

# 只有少数板的安装孔是公开数据；其余为空——**不猜孔位**。
_HOLES: dict[str, list[tuple[float, float, float, str]]] = {
    "arduino-uno": [
        (13.97, 2.54, 3.2, "datasheet"),
        (66.04, 7.62, 3.2, "datasheet"),
        (66.04, 35.56, 3.2, "datasheet"),
        (15.24, 50.8, 3.2, "datasheet"),
    ],
    "arduino-mega": [
        (13.97, 2.54, 3.2, "datasheet"),
        (100.33, 7.62, 3.2, "datasheet"),
        (100.33, 35.56, 3.2, "datasheet"),
        (15.24, 48.26, 3.2, "datasheet"),
    ],
    "raspberry-pi-4": [
        (3.5, 3.5, 2.7, "datasheet"),
        (3.5, 52.5, 2.7, "datasheet"),
        (61.5, 3.5, 2.7, "datasheet"),
        (61.5, 52.5, 2.7, "datasheet"),
    ],
}


def _px_per_mm(px_w: int, w_mm: float) -> float:
    return round(px_w / w_mm, 4) if w_mm > 0 else 3.7795


def board_preset(kind: str, *, px_per_mm: float | None = None) -> Board:
    """按板卡类型建一个 ``Board``（轮廓 = 矩形，毫米）。"""
    raw = _RAW.get(kind)
    if raw is None:
        # 未知板：给一个诚实的通用尺寸，并让调用方在 warnings 里看到。
        return Board(kind=kind, name=kind, size_mm={"w": 50.0, "h": 30.0},
                     thickness_mm=1.6, px_per_mm=px_per_mm or 3.7795)
    name, w_mm, h_mm, px_w, px_h, thickness, source = raw
    holes = [
        MountHole(x_mm=x, y_mm=y, diameter_mm=d, source=s)
        for x, y, d, s in _HOLES.get(kind, [])
    ]
    return Board(
        kind=kind,
        name=name,
        size_mm={"w": w_mm, "h": h_mm},
        thickness_mm=thickness,
        mount_holes=holes,
        px_per_mm=px_per_mm if px_per_mm is not None else _px_per_mm(px_w, w_mm),
    )


def preset_info(kind: str) -> dict[str, Any] | None:
    """给 UI 的板卡条目：毫米优先，像素与比例只作为投影说明。"""
    raw = _RAW.get(kind)
    if raw is None:
        return None
    name, w_mm, h_mm, px_w, px_h, thickness, source = raw
    return {
        "kind": kind,
        "name": name,
        "size_mm": {"w": w_mm, "h": h_mm, "d": thickness},
        "px": {"w": px_w, "h": px_h},
        "px_per_mm": _px_per_mm(px_w, w_mm),
        "thickness_mm": thickness,
        "source": source,
        "mount_holes": len(_HOLES.get(kind, [])),
    }


BOARD_PRESETS: list[str] = sorted(_RAW)


def all_presets() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kind in BOARD_PRESETS:
        info = preset_info(kind)
        if info is not None:
            out.append(info)
    return out
