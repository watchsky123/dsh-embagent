"""``hw_velxio.design`` —— 一份 mm 规范模型（v3 方案 §2.2）。

2D 与 3D 都是它的投影，不各自维护数据：

  * 2D = 电气/拓扑权威（网表、引脚、线缆语义）
  * 3D = 物理/机械权威（轮廓、实体、高度、keepout）
  * 共享 ``placement``（位置/朝向/安装面）

单位一律 **mm**。像素只出现在 ``projection`` 里，且只用于 2D 视图。
"""

from __future__ import annotations

from .model import (
    Board,
    DesignError,
    DesignModel,
    MountHole,
    Part,
    Placement,
    Point2,
    Point3,
    Wire,
)
from .boards import BOARD_PRESETS, board_preset
from .catalog import PART_SIZES, part_size
from .projection import to_2d, from_legacy_canvas
from .store import DesignStore, project_dir

__all__ = [
    "Board",
    "DesignError",
    "DesignModel",
    "MountHole",
    "Part",
    "Placement",
    "Point2",
    "Point3",
    "Wire",
    "BOARD_PRESETS",
    "board_preset",
    "PART_SIZES",
    "part_size",
    "to_2d",
    "from_legacy_canvas",
    "DesignStore",
    "project_dir",
]
