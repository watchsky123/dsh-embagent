"""可抄的半成品样例（`examples`）。

## 为什么要有它

从零开始最难的一步不是判定，是**空白**：`design.json` 里一个元件都没有时，
所有检查都"通过"（§0.1 的老坑），而人不知道该先填哪几行。

所以带三个**能直接跑通整条链**的样例，抄一份改比从零写快得多：

| 名字 | 有什么 | 用来练什么 |
|---|---|---|
| `blink` | ATtiny85 + LED + 限流电阻 | 最小闭环：元件/网表/板框/引脚表 |
| `button` | ATtiny85 + 按键 + 上拉 | 输入方向（`fw-matrix` 的 direction）|
| `gimbal` | STM32 + 两只舵机 + 摆臂 + 铰链关节 | 机械域（joints/limits → mech → mjcf）|

三个都是**半成品**：缺 `.ioc`、缺真实尺寸来源、缺固件源码。抄过去跑
`audit` 会看到一堆 `not_ready` —— 那正是它们要教的东西。
"""

from __future__ import annotations

from typing import Any

__all__ = ["EXAMPLES", "list_examples", "copy_example", "describe"]

_BOARD = {"kind": "generic", "name": "base",
          "outline": [[0.0, 0.0], [40.0, 0.0], [40.0, 30.0], [0.0, 30.0]],
          "size_mm": {"w": 40, "h": 30}, "thickness_mm": 1.6}


def _part(ref: str, kind: str, label: str, x: float, y: float,
          size: tuple[float, float, float], pins: list[str],
          *, lcsc: str = "", role: str = "component", unit: str = "",
          source: str = "catalog", z: float = 0.0) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ref": ref, "kind": kind, "label": label, "role": role,
        "size_mm": {"w": size[0], "h": size[1], "d": size[2]},
        "physical_source": source,
        "placement": {"x_mm": x, "y_mm": y, "z_mm": z, "side": "top"},
        "pins": list(pins),
    }
    if lcsc:
        out["lcsc"] = lcsc
    if unit:
        out["unit"] = unit
    return out


EXAMPLES: dict[str, dict[str, Any]] = {
    "blink": {
        "title": "最小闭环：LED 闪烁",
        "note": "ATtiny85 + LED + 限流电阻。用来练：元件/网表/板框/引脚表。",
        "design": {
            "id": "blink", "title": "blink",
            "board": _BOARD,
            "parts": [
                _part("U1", "mcu", "ATtiny85", 8.0, 10.0, (10, 10, 2),
                      ["1", "2", "3", "4", "5", "6", "7", "8"],
                      lcsc="C1523", role="mcu"),
                _part("D1", "led", "LED", 24.0, 10.0, (2, 1, 1), ["A", "K"]),
                _part("R1", "resistor", "1k", 30.0, 10.0, (1, 0.5, 0.5), ["1", "2"]),
            ],
            "nets": [
                {"net": "LED_A", "pins": [["U1", "5"], ["R1", "1"]]},
                {"net": "LED_K", "pins": [["R1", "2"], ["D1", "A"]]},
                {"net": "GND", "pins": [["D1", "K"], ["U1", "6"]]},
            ],
        },
    },
    "button": {
        "title": "输入与上拉",
        "note": "ATtiny85 + 按键 + 上拉电阻。用来练：固件方向（哪些脚只读）。",
        "design": {
            "id": "button", "title": "button",
            "board": _BOARD,
            "parts": [
                _part("U1", "mcu", "ATtiny85", 8.0, 10.0, (10, 10, 2),
                      ["1", "2", "3", "4", "5", "6", "7", "8"],
                      lcsc="C1523", role="mcu"),
                _part("SW1", "switch", "BTN", 24.0, 10.0, (3, 3, 1.5), ["1", "2"]),
                _part("R1", "resistor", "10k", 30.0, 10.0, (1, 0.5, 0.5), ["1", "2"]),
            ],
            "nets": [
                {"net": "BTN", "pins": [["U1", "7"], ["SW1", "1"], ["R1", "1"]]},
                {"net": "VCC", "pins": [["U1", "8"], ["R1", "2"]]},
                {"net": "GND", "pins": [["SW1", "2"], ["U1", "4"]]},
            ],
        },
    },
    "gimbal": {
        "title": "云台：一个铰链 + 两只舵机",
        "note": ("STM32 + 舵机 + 摆臂 + 铰链（带行程 limits）。"
                 "用来练：joints / joints-propose / mech / mjcf。"),
        "design": {
            "id": "gimbal", "title": "gimbal",
            "board": _BOARD,
            "parts": [
                _part("U1", "mcu", "STM32F103C6", 8.0, 8.0, (10, 10, 2),
                      ["1", "2", "3", "4"], lcsc="C1523", role="mcu"),
                _part("S1", "servo", "MG90S", 26.0, 8.0, (15, 15, 10),
                      ["1", "2", "3"], unit="gimbal"),
                _part("ARM", "link", "摆臂", 26.0, 8.0, (18, 3, 3), ["1", "2"],
                      unit="gimbal", source="proposed"),
            ],
            "nets": [
                {"net": "PWM", "pins": [["U1", "2"], ["S1", "1"]]},
                {"net": "VCC", "pins": [["U1", "3"], ["S1", "2"]]},
                {"net": "GND", "pins": [["U1", "4"], ["S1", "3"]]},
            ],
            "joints_list": [{
                "a": "S1", "b": "ARM", "kind": "hinge", "source": "declared",
                "params": {"axis": [0, 0, 1], "range_deg": [-45, 45]},
            }],
            "mechanical": {"limits": {"hinge_deg": [-45, 45], "required_deg": [-30, 30]}},
        },
    },
}


def list_examples() -> dict[str, Any]:
    return {
        "status": "ok",
        "examples": [{"name": name, "title": body["title"], "note": body["note"],
                      "parts": len(body["design"]["parts"])}
                     for name, body in EXAMPLES.items()],
        "note": "抄一份：`examples copy <name> --to=<新工程id>`",
    }


def copy_example(name: str, store: Any, *, title: str = "") -> dict[str, Any]:
    """把样例写成工程。**工程已存在就拒绝**（不覆盖人的东西）。"""
    from .model import DesignModel

    body = EXAMPLES.get(name)
    if body is None:
        return {"status": "failed",
                "findings": [f"没有样例 {name!r}（可用：{', '.join(EXAMPLES)}）"]}
    if store.exists():
        return {"status": "failed",
                "findings": [f"工程 {getattr(store, 'id', '')!r} 已存在；换一个 id"]}
    payload = dict(body["design"])
    payload["id"] = getattr(store, "id", name)
    design = DesignModel.from_dict(payload)
    if title:
        design.title = title
    store.save(design)
    return {"status": "ok", "example": name, "project": design.id,
            "parts": len(design.parts), "nets": len(design.nets),
            "joints": len(design.joints_list),
            "note": ("半成品样例：`audit` 会看到一串 not_ready —— 那正是要接着做的"
                     "（`.ioc` / 固件源码 / 真实尺寸来源）")}


def describe() -> dict[str, Any]:
    return {"module": "dsh_embagent.design.examples",
            "examples": list(EXAMPLES),
            "discipline": "只读样例 + 复制；不覆盖已存在的工程"}