"""元件模板库 —— **hw_velxio 自己的**，不再依赖 `hwforge.assembly_parts`。

## 为什么要迁

原先 `roles.py` / `assemble.py` 从 hwforge 运行时读模板表：

```python
from hwforge.assembly_parts import TEMPLATES   # 外部依赖
```

这带来两个问题：

1. **hw_velxio 跑不起来** —— 没有 hwforge checkout 时，需求→装配整条链路
   全部 `not_ready`；
2. **两种事实源** —— 模板的尺寸与引脚在 hwforge 里，装配规则在 hw_velxio
   里，改一边不知道另一边。

本模块把 16 个模板的**尺寸 + 引脚 + 引脚位置**搬进来。数据是从 hwforge
实测导出的（见 `tests/test_design.py::TestNativeLibrary` 的对照测试），
不是凭印象写的。

## 引脚位置模型

每个引脚两个量：

* `at_norm`：在**未旋转足迹**上的归一化位置 `(x, y)`，取值 0..1；
  `(0,0)` = 左下角，`(1,1)` = 右上角。这是 hwforge 的做法，沿用它是为了
  互相兼容（`scene_import` 还要读它的 `scene.json`）。
* `role`：`power` / `ground` / `digital`。**只用于接线的语义判断**，
  不参与类型格检查（那是另一件事，目前没做）。

`at_norm` 的第三分量恒为 0（引脚在顶面）。保留三元组是为了让
`scene_import` 读进来的形状一致。

## 与 hwforge 的差异（诚实标注）

hwforge 的模板是**工厂函数**（`make_gimbal_servo(...)` 几十个参数，
带 `marks`、`assumptions`、`unverified`）。本模块只保留**装配需要的**：
尺寸、引脚名、引脚位置、角色。那些 "假设/未验证" 文本归
`provenance` 管，不在模板里。

@module hw_velxio.design.library
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PinDef:
    """一个引脚。`at_norm` 是未旋转足迹上的归一化位置。"""

    name: str
    at_norm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    #: `power` / `ground` / `digital` —— 只给接线用
    role: str = "digital"
    #: `source` / `sink` / `bidir`（目前只记录，不判定）
    direction: str = "bidir"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "at_norm": list(self.at_norm),
                "role": self.role, "direction": self.direction}


@dataclass(frozen=True)
class Template:
    """一个装配模板：标称尺寸 + 引脚表。"""

    key: str
    size_mm: tuple[float, float, float]
    pins: tuple[PinDef, ...] = ()
    #: 人读的用途说明。
    summary: str = ""

    @property
    def pin_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.pins)

    def is_mechanical(self) -> bool:
        """没有电气引脚 = 机械件。"""
        return not self.pins

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "size_mm": list(self.size_mm),
                "pins": [p.to_dict() for p in self.pins],
                "summary": self.summary}


# ---------------------------------------------------------------------------
# 模板表（数据来自 hwforge 的实测导出）
# ---------------------------------------------------------------------------

_P = PinDef

TEMPLATES: dict[str, Template] = {}


def _register(template: Template) -> Template:
    TEMPLATES[template.key] = template
    return template


_register(Template(
    key="camera_module", size_mm=(8.0, 25.0, 25.0),
    pins=(_P("5V", (0.0, 0.3, 0.0), "power", "sink"),
          _P("GND", (0.0, 0.6, 0.0), "ground", "sink"),
          _P("DATA", (0.0, 0.9, 0.0), "digital", "bidir")),
    summary="相机模组（左边缘出线）",
))

_register(Template(
    key="gimbal_servo", size_mm=(20.0, 20.0, 20.0),
    pins=(_P("PWM", (1.0, 0.2, 0.0), "digital", "sink"),
          _P("VCC", (1.0, 0.5, 0.0), "power", "sink"),
          _P("GND", (1.0, 0.8, 0.0), "ground", "sink")),
    summary="云台舵机（右边缘出线）",
))

_register(Template(
    key="mcu_board", size_mm=(45.0, 30.0, 10.0),
    pins=(_P("VIN", (1.0, 0.1, 0.0), "power", "sink"),
          _P("GND", (1.0, 0.3, 0.0), "ground", "sink"),
          _P("PWM_PAN", (0.0, 0.2, 0.0), "digital", "source"),
          _P("CAM_DATA", (0.0, 0.5, 0.0), "digital", "bidir"),
          _P("CAM_PWR", (0.0, 0.8, 0.0), "power", "source")),
    summary="主控板（右侧电源、左侧外设）",
))

_register(Template(
    key="mcu_block", size_mm=(30.0, 25.0, 6.0),
    pins=(_P("TRIG", (1.0, 0.15, 0.0), "digital", "source"),
          _P("ECHO", (1.0, 0.35, 0.0), "digital", "sink"),
          _P("IN1", (1.0, 0.6, 0.0), "digital", "source"),
          _P("IN2", (1.0, 0.8, 0.0), "digital", "source"),
          _P("3V3", (0.0, 0.0, 0.0), "power", "source"),
          _P("GND", (0.0, 1.0, 0.0), "ground", "sink")),
    summary="主控块（小尺寸，超声/电机接口）",
))

_register(Template(
    key="psu_5v", size_mm=(30.0, 20.0, 12.0),
    pins=(_P("ACIN", (0.0, 0.2, 0.0), "power", "sink"),
          _P("GND", (1.0, 0.8, 0.0), "ground", "sink"),
          _P("5V", (1.0, 0.5, 0.0), "power", "source")),
    summary="5V 电源模块",
))

_register(Template(
    key="regulator_3v3", size_mm=(22.0, 16.0, 8.0),
    pins=(_P("IN", (0.0, 0.3, 0.0), "power", "sink"),
          _P("GND", (0.0, 0.6, 0.0), "ground", "sink"),
          _P("OUT", (1.0, 0.5, 0.0), "power", "source")),
    summary="3.3V 稳压器",
))

_register(Template(
    key="regulator", size_mm=(22.0, 16.0, 6.0),
    pins=(_P("VIN", (1.0, 0.2, 0.0), "power", "sink"),
          _P("GND", (1.0, 0.5, 0.0), "ground", "sink"),
          _P("VOUT", (0.0, 0.5, 0.0), "power", "source")),
    summary="通用稳压器",
))

_register(Template(
    key="ultrasonic_ranger", size_mm=(20.0, 15.0, 12.0),
    pins=(_P("TRIG", (0.0, 0.1, 0.0), "digital", "sink"),
          _P("ECHO", (0.0, 0.35, 0.0), "digital", "source"),
          _P("VCC", (0.0, 0.6, 0.0), "power", "sink"),
          _P("GND", (0.0, 0.85, 0.0), "ground", "sink")),
    summary="超声测距（HC-SR04 级）",
))

_register(Template(
    key="motor_driver", size_mm=(25.0, 20.0, 8.0),
    pins=(_P("IN1", (1.0, 0.0, 0.0), "digital", "sink"),
          _P("IN2", (1.0, 0.15, 0.0), "digital", "sink"),
          _P("OUTA", (0.0, 1.0, 0.0), "power", "source"),
          _P("OUTB", (0.0, 0.85, 0.0), "power", "source"),
          _P("VIN", (1.0, 0.55, 0.0), "power", "sink"),
          _P("GND", (1.0, 0.7, 0.0), "ground", "sink")),
    summary="电机驱动（双路 H 桥）",
))

_register(Template(
    key="wheel_motor", size_mm=(20.0, 20.0, 20.0),
    pins=(_P("PWR", (1.0, 0.2, 0.05), "power", "sink"),
          _P("GND", (1.0, 0.6, 0.05), "ground", "sink")),
    summary="带减速箱的轮电机",
))

_register(Template(
    key="battery", size_mm=(40.0, 30.0, 18.0),
    pins=(_P("VBAT", (0.0, 0.0, 0.0), "power", "source"),
          _P("GND", (0.0, 1.0, 0.0), "ground", "sink")),
    summary="电池组",
))

# -- 机械件：没有电气引脚 ------------------------------------------------
_register(Template(
    key="wall_bracket", size_mm=(4.0, 90.0, 80.0), pins=(),
    summary="壁挂支架（机械件，无电气引脚）"))
_register(Template(
    key="chassis", size_mm=(110.0, 100.0, 3.0), pins=(),
    summary="底盘（机械件）"))
_register(Template(
    key="wheel", size_mm=(8.0, 42.0, 42.0), pins=(),
    summary="轮子（机械件）"))
_register(Template(
    key="sensor_keepout", size_mm=(30.0, 40.0, 25.0), pins=(),
    summary="传感器禁布区（占位，不是元件）"))
_register(Template(
    key="step_mechanical", size_mm=(20.0, 20.0, 20.0), pins=(),
    summary="通用机械件占位（尺寸需按实际替换）"))


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def keys() -> set[str]:
    """全部模板 key。"""
    return set(TEMPLATES)


def get(key: str) -> Template | None:
    return TEMPLATES.get(key)


def size_mm(key: str) -> tuple[float, float, float] | None:
    tpl = TEMPLATES.get(key)
    return tpl.size_mm if tpl is not None else None


def pin_names(key: str) -> set[str]:
    """模板的引脚名（大写）。机械件返回空集。"""
    tpl = TEMPLATES.get(key)
    return {p.name.upper() for p in tpl.pins} if tpl is not None else set()


def pin_defs(key: str) -> tuple[PinDef, ...]:
    tpl = TEMPLATES.get(key)
    return tpl.pins if tpl is not None else ()


def pin_positions(key: str, *,
                  x_mm: float, y_mm: float,
                  w_mm: float | None = None,
                  h_mm: float | None = None) -> dict[str, tuple[float, float]]:
    """引脚在**板坐标**里的位置（mm）。

    `x_mm`/`y_mm` 是元件的左下角（与 `Placement` 语义一致）。
    `w_mm`/`h_mm` 不给时用模板标称尺寸。

    归一化位置 `(nx, ny)` 映射到：
      * `nx=0` → 左边缘内侧 `x_mm`
      * `nx=1` → 右边缘内侧 `x_mm + w_mm`
      * `ny=0` → 下边缘 `y_mm`；`ny=1` → 上边缘 `y_mm + h_mm`

    引脚贴边是**物理事实**（排针/排线从边缘出），所以不往里缩；
    缩进去反而与真实元件不符。
    """
    tpl = TEMPLATES.get(key)
    if tpl is None:
        return {}
    w = float(w_mm if w_mm is not None else tpl.size_mm[0])
    h = float(h_mm if h_mm is not None else tpl.size_mm[1])
    out: dict[str, tuple[float, float]] = {}
    for pin in tpl.pins:
        nx, ny = pin.at_norm[0], pin.at_norm[1]
        out[pin.name] = (round(x_mm + nx * w, 4), round(y_mm + ny * h, 4))
    return out


def describe() -> list[dict[str, Any]]:
    """机器可读的模板目录（给工具层/面板用）。"""
    return [TEMPLATES[k].to_dict() for k in sorted(TEMPLATES)]


def size_from_pads(pads: list[tuple[float, float, float, float]]) -> tuple[float, float] | None:
    """从焊盘几何推本体尺寸（与 `packages.size_from_pads` 同思路）。

    保留在这里是因为装配路径偶尔需要"没有模板但有焊盘"的情况。
    2 脚件：本体 = 焊盘中心距 + 半焊盘长；多脚件：外框的 75%。
    """
    if not pads:
        return None
    xs = [p[0] for p in pads]
    ys = [p[1] for p in pads]
    ws = [p[2] for p in pads]
    hs = [p[3] for p in pads]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    if len(pads) == 2:
        body_w = span_x + (max(ws) if ws else 0.0) / 2.0
        body_h = span_y + (max(hs) if hs else 0.0) / 2.0
    else:
        outer_w = span_x + (max(ws) if ws else 0.0)
        outer_h = span_y + (max(hs) if hs else 0.0)
        body_w = max(outer_w * 0.75, outer_w - 1.6)
        body_h = max(outer_h * 0.75, outer_h - 1.6)
    if body_w <= 0 or body_h <= 0:
        return None
    return (round(body_w, 4), round(body_h, 4))
