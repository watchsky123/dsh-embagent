"""R1' —— **能力需求表**：把"任意需求"变成角色，而不是只认一种需求。

## 这个模块补的是什么

hwforge 的 `spec.analyse_requirement()` 是一份**关键词表**，而且只覆盖一个
参考案例。实测：

| 需求 | `spec` 产出 |
|---|---|
| `壁挂云台相机 手机看视频` | ✅ 6 个角色 |
| `做一个 STM32 主控的温湿度采集板` | ❌ `undefined_hw` |
| `无人机飞控板` | ❌ `undefined_hw` |

它的关键词只有 相机/云台/舵机/无线/墙/手机/显示/固定/安装。

**这是关键词表的通病**：需求里换个说法就不认识了。本模块换成**能力表**：

```
需求文本  ──> 能力（image / motion / wireless / sense / power / mount / compute）
                │
                └──> 角色（camera / gimbal_pan / radio / sensor / …）
```

能力只有 8 个左右，是**封闭集合**；每种能力的说法可以有很多个
（`摄像头`/`相机`/`camera`/`视频`/`拍照` 都指向 `image`）。加一种说法只是
往表里加一个词，不需要新写一个 `if`。

## 三条纪律

1. **能力表是封闭的，说法表是开放的。** 新增说法不改结构；新增能力必须
   同时给出"它需要哪个模板"，否则装配阶段无从下手。
2. **说不上来就说不上来。** 一条能力都识别不出时返回 `undefined_hw`，
   并且**列出它试过哪些词** —— 让调用方知道是"需求没提硬件"还是
   "我们没认出这个说法"。
3. **与 hwforge 的 spec 合并，不替换。** `spec` 仍然负责：软件视图、
   跨域契约、`LookupRequest`（不臆造型号）、未决清单。本模块只补
   **硬件角色**那一列。

@module hw_velxio.design.requirements
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 能力表（封闭集合）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Capability:
    """一种能力：需要什么、由哪些角色承载、凭什么判断。"""

    key: str
    #: 说法（**开放**：加一种说法只是加一个词）。
    words: tuple[str, ...]
    #: 承载它的角色。
    roles: tuple[str, ...]
    #: 这条能力对元件的电气要求（进 `roles.RoleMapping.needs` 的语义）。
    needs: tuple[str, ...] = ()
    #: 为什么需要它（写进元件的 note）。
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "words": list(self.words),
                "roles": list(self.roles), "needs": list(self.needs),
                "why": self.why}


#: 能力表。顺序即**优先级**（同一需求命中多条时按此顺序排角色）。
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="compute",
        words=("主控", "单片机", "控制器", "处理器", "核心板", "控制板",
               "飞控", "飞行控制", "主板",
               "mcu", "cpu", "soc", "fpga", "fc",
               "stm32", "esp32", "esp8266", "arduino", "raspberry",
               "rp2040", "nrf52", "at32", "gd32", "ch32"),
        roles=("mcu",),
        needs=("pwm",),
        why="需要一颗主控来跑逻辑与接口",
    ),
    Capability(
        key="image",
        words=("相机", "摄像头", "视频", "图像", "画面", "拍照", "摄像",
               "camera", "video", "image", "photo", "webcam", "csi"),
        roles=("camera",),
        needs=("video",),
        why="采集图像/视频",
    ),
    Capability(
        key="motion",
        words=("云台", "舵机", "转向", "俯仰", "水平转动", "扫射",
               "云台相机", "gimbal", "servo", "pan", "tilt", "steer"),
        roles=("gimbal_pan", "gimbal_tilt"),
        needs=("pwm",),
        why="需要可控制的机械运动（云台两轴）",
    ),
    Capability(
        key="wireless",
        words=("无线", "蓝牙", "图传", "遥控", "远程", "联网",
               "wifi", "wi-fi", "bluetooth", "ble", "lora", "4g", "5g",
               "wireless", "remote"),
        roles=("radio",),
        needs=(),
        why="承载无线链路",
    ),
    Capability(
        key="sense",
        words=("传感器", "采集", "测量", "监测", "测距", "温湿度", "温度",
               "湿度", "气压", "加速度", "陀螺仪", "姿态", "距离", "避障",
               "sensor", "imu", "mpu6050", "dht22", "bme280", "ultrasonic",
               "range", "distance"),
        roles=("sensor",),
        needs=("gpio",),
        why="需要感知量（具体传感器型号需按需求细化）",
    ),
    Capability(
        key="display",
        words=("显示", "屏幕", "液晶", "oled", "lcd", "tft", "显示板",
               "screen", "display", "monitor"),
        roles=("display",),
        needs=("i2c",),
        why="需要本地显示",
    ),
    Capability(
        key="power",
        words=("电池", "供电", "电源", "续航", "锂电", "移动供电",
               "battery", "power", "psu", "supply"),
        roles=("power_in",),
        needs=("current",),
        why="需要电源（电池或电源模块）",
    ),
    Capability(
        key="drive",
        words=("电机", "马达", "驱动", "车轮", "轮子", "底盘", "行驶",
               "螺旋桨", "旋翼", "桨", "舵机驱动",
               "movement", "motor", "drive", "wheel", "chassis", "propeller"),
        roles=("motor",),
        needs=("current",),
        why="需要电机驱动与行走机构",
    ),
    Capability(
        key="mount",
        words=("壁挂", "墙面", "支架", "安装", "固定", "挂装", "桌面",
               "wall", "mount", "bracket", "stand"),
        roles=("mount",),
        needs=(),
        why="需要机械安装件",
    ),
    Capability(
        key="peer",
        words=("手机", "平板", "电脑", "上位机", "app", "网页", "远程查看",
               "phone", "tablet", "pc", "host", "browser"),
        roles=("display_peer",),
        needs=(),
        why="接收端是外部设备，不占元件",
    ),
)

BY_KEY: dict[str, Capability] = {c.key: c for c in CAPABILITIES}


# ---------------------------------------------------------------------------
# 需求 → 能力
# ---------------------------------------------------------------------------

@dataclass
class CapabilityHit:
    """一条命中的能力。`word` 是**哪个词命中**的 —— 便于人核对。"""

    key: str
    word: str
    roles: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "word": self.word,
                "roles": list(self.roles)}


@dataclass
class RequirementResult:
    """需求分析结果。**只有硬件角色那一列**，软件视图归 hwforge。"""

    goal: str
    hits: list[CapabilityHit] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    #: 试过的词（识别不出时用得着：让调用方知道是"需求没说"还是"没认出来"）。
    tried: list[str] = field(default_factory=list)
    #: 无法判断的部分。
    unresolved: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.roles) and "undefined_hw" not in self.roles

    def to_dict(self) -> dict[str, Any]:
        return {"goal": self.goal,
                "hits": [h.to_dict() for h in self.hits],
                "roles": list(self.roles),
                "tried": list(self.tried),
                "unresolved": list(self.unresolved),
                "ok": self.ok}

    def summary(self) -> str:
        if not self.roles:
            return (f"没有识别出任何能力（试过的说法：{len(self.tried)} 个）；"
                    f"需求里没有描述硬件形态")
        if "undefined_hw" in self.roles:
            return "硬件形态未定义"
        return (f"{len(self.roles)} 个角色：{ '、'.join(self.roles) }"
                f"（命中能力 {len(self.hits)} 条）")


def analyse(goal: str, *,
            extra_capabilities: tuple[Capability, ...] = ()) -> RequirementResult:
    """从需求文本推导角色。**纯确定性**，不含模型。

    `extra_capabilities` 让调用方在运行时补能力（例如某个项目专用的说法），
    不必改本文件。
    """
    text = (goal or "").lower()
    result = RequirementResult(goal=goal)
    if not text.strip():
        result.roles = ["undefined_hw"]
        result.unresolved.append("需求为空")
        return result

    capabilities = (*CAPABILITIES, *extra_capabilities)
    for capability in capabilities:
        for word in capability.words:
            result.tried.append(word)
            if word.lower() in text:
                result.hits.append(CapabilityHit(
                    key=capability.key, word=word, roles=capability.roles))
                for role in capability.roles:
                    if role not in result.roles:
                        result.roles.append(role)
                break        # 一条能力只记一次命中

    if not result.roles:
        # **说不上来就说不上来**，并说明试过什么。
        result.roles = ["undefined_hw"]
        result.unresolved.append(
            f"需求没有命中任何能力（试过 {len(result.tried)} 种说法）；"
            f"硬件形态未定义")
    return result


def merge(spec_roles: list[str], ours: list[str]) -> list[str]:
    """把 hwforge `spec` 的角色与本模块的角色合并。

    优先级：**本地能力表在前**（它认得更多说法），`spec` 的在后（它认得
    软件侧与跨域契约）。`undefined_hw` 只在两边都没别的角色时保留。
    """
    merged: list[str] = []
    for role in list(ours) + list(spec_roles):
        if role == "undefined_hw" and merged:
            continue      # 已经有具体角色了，占位符没意义
        if role not in merged:
            merged.append(role)
    return merged or ["undefined_hw"]


def capabilities() -> list[dict[str, Any]]:
    """机器可读的能力表（给工具层/面板用）。"""
    return [c.to_dict() for c in CAPABILITIES]
