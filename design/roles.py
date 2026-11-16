"""R2 —— 角色 → 元件的**确定性映射**（需求到装配之间唯一缺的那一格）。

## 这一层为什么必须有

hwforge 曾经提供需求侧的确定性框架（现已解耦，见文末）：

```
spec.analyse_requirement("壁挂云台相机…")
   └──> RequirementSpec.hardware = [HardwareItem(role="camera"),
                                    HardwareItem(role="gimbal_pan"), …]
```

它产出的是**角色**（camera / gimbal_pan / servo_power），而不是元件。
而 `assemble` 需要的是**模板 + 坐标**：

```
assembly_parts.TEMPLATES = {camera_module, gimbal_servo, mcu_board, …}   # 16 个
```

两者之间**没有任何代码**连接。实测：`spec` 能产出的 9 个角色，没有一个是
模板 key。所以在此之前，"需求 → 设计"只能靠手工写
`assembly_specs.wall_gimbal_camera_spec()` —— 那是一个硬编码的参考设计，
只覆盖一个需求。

本模块就是那一格：一张**声明式**映射表 + 一个**校验器**。

## 三条纪律

1. **映射不到就报 `unmatched`，不猜最像的。**
   猜错的代价是整块板的元件选型错误，而且下游所有检查都会基于它。
   这与 hwforge 既有的 `LookupRequest` 是同一条纪律：*知道什么时候该说
   不知道*。

2. **模板 key 必须真实存在。** 表里写 `camera_modul` 而模板叫
   `camera_module` 时，`validate()` 直接报出来 —— 而不是等到 `assemble`
   时才抛 KeyError，那时错误信息已经离开需求很远了。

3. **必填能力必须能被验证。** 每一行声明 `needs`（这个角色要求元件具备
   什么），`validate()` 会检查模板的工厂签名里是否真有对应参数。
   声明一个没人消费的能力，和 hwforge `constraints.py` 里"没有 checker 的
   约束是谎言"是同一条规矩。

## 与 hwforge 的关系：**已解耦**

模板 key 早先从 hwforge 运行时读（`assembly_parts.TEMPLATES`）。那让
整条需求→装配链路在没有 hwforge 时全部 `not_ready`。

现在模板表在 **`design/library.py`** —— hw_velxio 自己的数据（16 个模板，
尺寸 + 引脚 + 引脚位置）。所以：

* `template_keys()` **永远返回非空**；
* `validate()` **永远能跑**，`hwforge_unavailable` 这个状态已从根上
  消失（字段保留只为兼容快照格式，恒为 False）。

`hwforge` 只剩一个用途：`hw_block` 工具跑它的积木。那是**可选增强**，
与本模块无关。

@module hw_velxio.design.roles
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 映射表（数据，不是逻辑）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoleMapping:
    """一个需求角色 → 一个装配模板。

    `template` 为 `None` 表示**"这个角色不占板/不占元件"**（例如 `display_peer`
    就是手机本身），这是合法的映射，不是缺失 —— 但它必须显式写在表里，
    不能靠"表里查不到"来表达，否则"外部件"和"我忘了填"分不清。
    """

    role: str
    #: 装配模板 key（`hwforge.assembly_parts.TEMPLATES` 的键）；`None` = 外部件。
    template: str | None
    #: 这个角色对元件的能力要求。校验时对照模板工厂的签名。
    needs: tuple[str, ...] = ()
    #: 多个角色映射到同一个模板时，装配阶段可以**合并成一个元件**。
    #: 例如 `radio` 通常就是 MCU 自带 Wi-Fi，不必多装一个模块。
    mergeable_with: tuple[str, ...] = ()
    #: 这个映射凭什么成立。写清楚，否则三个月后没人敢改。
    why: str = ""
    #: `template is None` 时的语义区分 —— **这两件事完全不同**：
    #:
    #: * `external=True`  —— 这个角色本来就**不占元件**（手机、MCU 内建编码器）；
    #: * `external=False` —— 我们认出了这个角色，但**还没有对应模板**
    #:   （比如本地显示）。它必须报出来，不能当成"不需要元件"。
    #:
    #: 早先只有 `template=None` 一种表达，于是"认出了但造不出来"和"本来就
    #: 不用造"混在一起，显示模组被静默当成外部件跳过。
    external: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "template": self.template,
                "needs": list(self.needs),
                "mergeable_with": list(self.mergeable_with),
                "why": self.why, "external": self.external}


#: 需求角色 → 装配模板。
#:
#: 左边是 `hwforge.spec.analyse_requirement()` 实际会产出的 role
#: （见该模块里所有 `role="…"`）；右边是 `hwforge.assembly_parts.TEMPLATES`
#: 的 key。**两边都由 `validate()` 对照真实代码校验**，不靠这份注释。
ROLE_TEMPLATES: tuple[RoleMapping, ...] = (
    RoleMapping(
        role="camera", template="camera_module",
        needs=("video",),
        why="相机模组就是 camera_module 模板；尺寸/接口由模板自带的 datasheet 级数据决定",
    ),
    RoleMapping(
        role="encoder", template=None, external=True,
        needs=(),
        why="编码器通常是 MCU 内建（ESP32 硬编码 / H.264 软编），不单独占元件；"
            "若需求明确要独立编码芯片，应作为 `mcu` 的能力要求提出，而不是新增角色",
    ),
    RoleMapping(
        role="gimbal_pan", template="gimbal_servo",
        needs=("pwm",),
        why="云台水平轴 = 一个舵机；`axis` 由装配阶段的 options 指定",
    ),
    RoleMapping(
        role="gimbal_tilt", template="gimbal_servo",
        needs=("pwm",),
        why="云台俯仰轴 = 另一个舵机，与 pan 同模板但不同实例",
    ),
    RoleMapping(
        role="servo_power", template="psu_5v",
        needs=("current",),
        why="舵机堵转电流远高于静态电流（实测 SG90 堵转 700mA / 静态 4mA），"
            "需要独立 5V 供电；映射到 psu_5v 而不是 regulator_3v3，因为舵机吃 5V",
    ),
    RoleMapping(
        role="radio", template="mcu_board",
        # **不声明 wifi**：Wi-Fi 是模组内部能力，不会出现在引脚上。
        # 实测 mcu_board 的引脚只有 VIN/GND/PWM_PAN/CAM_PWR/CAM_DATA——
        # 写 `needs=("wifi",)` 会被校验器判成「声明了没人提供的能力」。
        # 这是诚实的：这块板确实没法从引脚上证明它支持 Wi-Fi，
        # 那属于 datasheet 层面的事实（`partfacts` 里有，模板没有）。
        needs=("pwm",),
        mergeable_with=("mcu",),
        why="无线链路通常由 MCU 自带（ESP32-S3 有 Wi-Fi）；"
            "映射到 mcu_board 且可与之合并，避免重复安装一块板。"
            "Wi-Fi 能力属 datasheet 事实、不在引脚上，故不在 needs 里",
    ),
    RoleMapping(
        role="mcu", template="mcu_board",
        needs=("pwm",),
        mergeable_with=("radio",),
        why="主控。**这是 hw_velxio 补的角色，不是 hwforge specs 产出的**"
            "（`spec.py` 没有 mcu 关键词，只有 `radio` 顺带映射到 mcu_board）。"
            "没有它，`CAM_DATA`/`PWM` 无处可接 —— 实测：只有相机+舵机+电源时，"
            "`CAM1.DATA` 与 `SV1.PWM` 全部悬空",
    ),
    RoleMapping(
        role="sensor", template="ultrasonic_ranger",
        needs=("gpio",),
        why="传感器（默认映射到超声测距模板）。**这是兜底映射**：具体传感器"
            "取决于需求，`spec.py` 目前不产出 sensor 角色；"
            "需要精确选型时应当显式给 `--roles`，而不是依赖这一行",
    ),
    RoleMapping(
        role="display", template=None, external=False,
        needs=(),
        why="本地显示。**hwforge 模板表里没有显示模组** —— 认得出这个角色，"
            "但造不出来。所以 `external=False`：它必须出现在 "
            "`plan.unbuildable` 里，而不是被当成「不需要元件」静默跳过",
    ),
    RoleMapping(
        role="power_in", template="battery",
        needs=("current",),
        why="电源输入。默认映射到 battery（`VBAT`/`GND`）。"
            "若需求是市电，应显式换成 `psu_5v`",
    ),
    RoleMapping(
        role="motor", template="motor_driver",
        needs=("current",),
        why="电机驱动。行走机构还需要 wheel/chassis，但那些取决于数量与"
            "尺寸，由 `--roles` 显式指定，不自作主张补",
    ),
    RoleMapping(
        role="mount", template="wall_bracket",
        needs=(),
        why="壁挂安装件对应 wall_bracket；非壁挂需求不该出现这个角色。"
            "**注意**：该模板造出的元件没有电气引脚（它是机械件），"
            "所以 `needs` 必须为空 —— 给它写电气能力会被校验器判成"
            "「声明了没人提供的能力」",
    ),
    RoleMapping(
        role="display_peer", template=None, external=True,
        needs=(),
        why="接收端是手机，不占板也不占元件 —— 显式映射到 None，"
            "与「表里没写」区分开",
    ),
    RoleMapping(
        role="undefined_hw", template=None, external=True,
        needs=(),
        why="需求未描述硬件形态时的占位角色。映射到 None 是**故意**的："
            "它的存在本身就是「需求不完整」的信号，必须原样传到 unresolved",
    ),
)

BY_ROLE: dict[str, RoleMapping] = {m.role: m for m in ROLE_TEMPLATES}

#: 需求侧**没有**对应模板的角色 —— 只是说明用。
#: `validate()` 不依赖它；真正的判断是"role 在不在 `BY_ROLE` 里"。
EXTERNAL_ROLES: frozenset[str] = frozenset(
    m.role for m in ROLE_TEMPLATES if m.template is None)


# ---------------------------------------------------------------------------
# 模板目录 —— **本地库**，不再依赖 hwforge
# ---------------------------------------------------------------------------
#
# 早先这里从 hwforge 运行时读 `assembly_parts.TEMPLATES`。那让整套
# 需求→装配链路在没有 hwforge 时全部 `not_ready`。现在模板表在
# `design/library.py` 里，是 hw_velxio 自己的数据。
#
# 保留 `hwforge_root()` 是因为 `hw_block` 仍然可以选择性地跑 hwforge 积木
# （那本来就是它的功能），但**装配路径不再需要它**。

def hwforge_root() -> str | None:
    """hwforge checkout 根（**可选**，只有 `hw_block` 用得到）。"""
    try:
        from ..bridge import hwforge_root as _root  # type: ignore
    except Exception:  # noqa: BLE001
        here = Path(__file__).resolve().parent.parent
        for cand in (Path.cwd() / "hwforge", here.parent / "hwforge"):
            if (cand / "hwforge" / "__init__.py").is_file():
                return str(cand)
        return None
    try:
        root = _root()
    except Exception:  # noqa: BLE001
        return None
    return root if (Path(root) / "hwforge" / "__init__.py").is_file() else None


def template_keys() -> set[str]:
    """本地模板库的全部 key。**永远可用**（不依赖外部 checkout）。"""
    from . import library
    return library.keys()


def template_size(template: str) -> tuple[float, float, float] | None:
    """模板的标称尺寸（mm），没有就返回 `None`。"""
    from . import library
    return library.size_mm(template)


def template_pins(template: str) -> set[str]:
    """模板的引脚名（大写）。机械件返回空集 —— 那是**事实**，不是缺失。"""
    from . import library
    return library.pin_names(template)


#: 能力 → 它能由哪个引脚名体现。
#:
#: `needs` 描述的是**元件的电气能力**（能出 PWM、能吃 5V/2A…），不是工厂的
#: 形状参数（w_mm/d_mm）。所以校验方式是：**把模板真的造一遍，看它产出的
#: 引脚里有没有对应名字** —— 而不是查 `inspect.signature`。
#:
#: 这是本文件第一版踩过的坑：拿 `needs=("pwm",)` 去对工厂签名，自然一个都
#: 对不上（签名里只有 `w_mm`/`d_mm`/`axis`…），于是一次报出 5 条
#: `need_not_consumed`。那条检查本身是对的（它确实抓到了"这个要求没人消费"），
#: 错的是"消费"的定义。
CAPABILITY_PINS: dict[str, tuple[str, ...]] = {
    "pwm": ("PWM", "SIG", "SERVO"),
    "video": ("VIDEO", "CAM", "CSI", "DVP", "YUV", "DATA"),
    "current": ("VCC", "VIN", "5V", "PWR", "VBAT", "ACIN"),
    # Wi-Fi 是**模组内部**能力，不会出现在引脚上：板载 Wi-Fi 的模组只会
    # 露出天线脚或完全不露。所以这条能力**无法由引脚核对** —— 见 needs
    # 的注记，它被改成 unchecked 而不是 failed。
    "wifi": ("WIFI", "RF", "ANT"),
    "supply": ("VCC", "VIN", "5V", "3V3", "VDD"),
    "ground": ("GND", "VSS"),
    "gpio": ("GPIO", "IO", "TRIG", "ECHO", "SIG"),
    "i2c": ("SCL", "SDA"),
    "uart": ("TX", "RX"),
    "usb": ("USB", "DP", "DM"),
    # 机械件：没有电气引脚，能力只能是"存在"。空元组 = 只需要造得出来。
    "mechanical": (),
}


def capability_available(template: str, need: str) -> bool | None:
    """模板是否真的提供某项能力。

    返回 `True` / `False` / `None`（无法判断）。**三态**，不是两态：
    把"不知道"混进"通过"是这套系统一直在防的事。
    """
    pins = template_pins(template)
    if not pins:
        return None
    markers = CAPABILITY_PINS.get(need)
    if markers is None:
        # 没在表里的能力无法核对 —— 明确返回 None，不猜
        return None
    if markers == ():
        # 机械件：只要能造出来就算满足（它没有电气引脚）
        return True
    upper = {p.upper() for p in pins}
    return any(any(marker in pin for pin in upper) for marker in markers)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

@dataclass
class RoleProblem:
    """一条校验问题。`kind` 是机器可读的类别，`detail` 给人看。"""

    kind: str
    role: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "role": self.role, "detail": self.detail}


@dataclass
class RoleCheck:
    """映射表的校验结果。"""

    ok: bool
    problems: list[RoleProblem] = field(default_factory=list)
    #: 需求里出现、但表里没有的角色 —— **必须报出来**，不能静默忽略。
    unmatched: list[str] = field(default_factory=list)
    #: 表里有、但 hwforge 里找不到模板的 key。
    missing_templates: list[str] = field(default_factory=list)
    #: **恒为 False**。模板表已迁到本地，不再存在"校验不了"的状态；
    #: 字段保留是为了不破坏已有的调用方与快照格式。
    hwforge_unavailable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok,
                "unmatched": list(self.unmatched),
                "missing_templates": list(self.missing_templates),
                "hwforge_unavailable": self.hwforge_unavailable,
                "problems": [p.to_dict() for p in self.problems]}

    def summary(self) -> str:
        if self.ok:
            return f"映射表 {len(ROLE_TEMPLATES)} 行全部对上本地模板库"
        parts = []
        if self.missing_templates:
            parts.append(f"{len(self.missing_templates)} 个模板不存在："
                         + "、".join(self.missing_templates))
        if self.unmatched:
            parts.append(f"{len(self.unmatched)} 个角色没有映射："
                         + "、".join(self.unmatched))
        if parts:
            return "；".join(parts)
        return f"{len(self.problems)} 条映射问题"


def validate(roles: list[str] | None = None) -> RoleCheck:
    """校验映射表；`roles` 给了就同时检查**这些角色是否都有映射**。

    ## 它抓什么

    1. `missing_templates` —— 表里写的模板 key 在 hwforge 里不存在
       （改名/拼错/模板被删）。
    2. `unmatched` —— 需求里出现的角色在表里没有映射。
       这条最重要：**静默跳过 = 需求的一部分被悄悄丢掉**。
    3. `problems` —— 模板没有引脚、或 `needs` 里的能力在引脚里找不到
       （说明这个"要求"没人能消费，等于装饰）。

    ## 不再有 `hwforge_unavailable`

    早先模板表从 hwforge 读，取不到就返回"无法校验"。现在模板表在
    `design/library.py` 里 —— **校验永远能跑**，`hwforge_unavailable`
    这个状态已经从根上消失了。
    """
    keys = template_keys()
    problems: list[RoleProblem] = []
    missing_templates: list[str] = []

    for mapping in ROLE_TEMPLATES:
        if mapping.template is None:
            # 外部件是合法映射，但必须写明理由 —— 空理由当错误处理，
            # 否则"忘了填模板"会被当成"故意不占元件"。
            if not mapping.why.strip():
                problems.append(RoleProblem(
                    kind="external_without_reason", role=mapping.role,
                    detail="映射到 None（外部件）但没有写 why，无法区分"
                           "「故意不占元件」和「忘了填模板」"))
            continue
        if mapping.template not in keys:
            missing_templates.append(mapping.template)
            problems.append(RoleProblem(
                kind="template_missing", role=mapping.role,
                detail=f"模板 {mapping.template!r} 在 hwforge 里不存在；"
                       f"现有关键字：{_preview(sorted(keys))}"))
            continue
        pins = template_pins(mapping.template)
        if not pins:
            # **机械件没有电气引脚是正常的**，但必须显式声明它不要求
            # 任何电气能力（`needs` 为空），否则"核对不了"和"通过"混在一起。
            if mapping.needs:
                problems.append(RoleProblem(
                    kind="template_has_no_pins", role=mapping.role,
                    detail=f"模板 {mapping.template!r} 造不出引脚，"
                           f"但声明了 needs={list(mapping.needs)} —— "
                           f"这些要求没有任何东西能证明"))
            continue
        # `needs` 是**电气能力**：校验模板产出的引脚里有没有对应名字。
        # 无法判断的能力（不在 CAPABILITY_PINS 里）报 unchecked，
        # **不算通过** —— "不知道"和"满足"是两件事。
        for need in mapping.needs:
            verdict = capability_available(mapping.template, need)
            if verdict is None:
                problems.append(RoleProblem(
                    kind="need_unchecked", role=mapping.role,
                    detail=f"声明需要 {need!r}，但没有办法核对模板 "
                           f"{mapping.template!r} 是否提供它"
                           f"（引脚：{_preview(sorted(pins))}）"))
            elif not verdict:
                problems.append(RoleProblem(
                    kind="need_not_consumed", role=mapping.role,
                    detail=f"声明需要 {need!r}，但模板 {mapping.template!r} "
                           f"的引脚里没有对应能力"
                           f"（引脚：{_preview(sorted(pins))}）"))

    unmatched: list[str] = []
    if roles:
        for role in roles:
            if role not in BY_ROLE:
                unmatched.append(role)
                problems.append(RoleProblem(
                    kind="role_unmapped", role=role,
                    detail=f"需求产出了角色 {role!r}，但映射表里没有它 —— "
                           f"这个需求会被静默丢掉一部分"))

    return RoleCheck(ok=not problems, problems=problems,
                     unmatched=unmatched, missing_templates=missing_templates)


def _preview(items: list[str], limit: int = 6) -> str:
    if len(items) <= limit:
        return "、".join(items)
    return "、".join(items[:limit]) + f"…（共 {len(items)}）"


# ---------------------------------------------------------------------------
# 角色 → 装配计划
# ---------------------------------------------------------------------------

@dataclass
class PlannedPart:
    """一个角色被解析成一个"要装的元件"。"""

    role: str
    template: str
    #: 这个角色为什么需要它（来自 `HardwareItem.why`，原样带过来）。
    why: str = ""
    #: 模板标称尺寸（mm）；`None` 表示 hwforge 没给。
    size_mm: tuple[float, float, float] | None = None
    #: 与哪些角色合并成了同一个元件。
    merged_roles: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "template": self.template, "why": self.why,
                "size_mm": list(self.size_mm) if self.size_mm else None,
                "merged_roles": list(self.merged_roles)}


@dataclass
class AssemblyPlan:
    """需求 → 装配计划的产物。

    **这不是设计**，是"要装哪些元件、用哪个模板"。坐标、连线、板框
    都还没有 —— 那些是 `assemble` 的事。把计划当设计看会高估它。
    """

    parts: list[PlannedPart] = field(default_factory=list)
    #: 被映射成外部件（**本来就**不占元件）的角色，带理由。
    external: list[dict[str, str]] = field(default_factory=list)
    #: 认出了角色、但**没有对应模板** —— 必须报出来，不能当成"不需要元件"。
    unbuildable: list[dict[str, str]] = field(default_factory=list)
    #: 需求里出现但没有映射的角色。
    unmatched: list[str] = field(default_factory=list)
    #: 无法解析的其它问题（模板缺失等）。
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"parts": [p.to_dict() for p in self.parts],
                "external": list(self.external),
                "unbuildable": list(self.unbuildable),
                "unmatched": list(self.unmatched),
                "problems": list(self.problems),
                "part_count": len(self.parts)}

    def summary(self) -> str:
        if self.unmatched:
            return (f"{len(self.parts)} 个元件；**{len(self.unmatched)} 个角色没有映射**："
                    + "、".join(self.unmatched))
        extern = f"，{len(self.external)} 个外部件" if self.external else ""
        miss = (f"；**{len(self.unbuildable)} 个角色认得出但没有模板**："
                + "、".join(e["role"] for e in self.unbuildable)
                ) if self.unbuildable else ""
        return f"{len(self.parts)} 个元件{extern}{miss}"


def plan(roles: list[str], *, reasons: dict[str, str] | None = None) -> AssemblyPlan:
    """把角色列表解析成装配计划。**不猜映射**。

    `reasons`：role → 为什么需要它（通常是 `HardwareItem.why`），原样带走。
    """
    reasons = reasons or {}
    result = AssemblyPlan()
    # 合并在这一步做：`radio` 与 `mcu` 都是 mcu_board，合成一个元件。
    merged_into: dict[str, PlannedPart] = {}

    for role in roles:
        mapping = BY_ROLE.get(role)
        if mapping is None:
            if role not in result.unmatched:
                result.unmatched.append(role)
            continue
        if mapping.template is None:
            if mapping.external:
                result.external.append({"role": role, "why": mapping.why})
            else:
                # 认出了角色但造不出来 —— 必须可见。
                result.unbuildable.append({"role": role, "why": mapping.why})
            continue
        # 能合并进已有元件吗？
        target = None
        for other_role, planned in merged_into.items():
            if role in BY_ROLE[other_role].mergeable_with or \
                    other_role in mapping.mergeable_with:
                target = planned
                break
        if target is not None:
            target = PlannedPart(
                role=target.role, template=target.template, why=target.why,
                size_mm=target.size_mm,
                merged_roles=(*target.merged_roles, role))
            merged_into[target.role] = target
            # 替换列表里那一项
            result.parts = [target if p.role == target.role else p
                            for p in result.parts]
            continue
        planned = PlannedPart(
            role=role, template=mapping.template,
            why=reasons.get(role, mapping.why),
            size_mm=template_size(mapping.template))
        result.parts.append(planned)
        merged_into[role] = planned

    return result


def describe() -> list[dict[str, Any]]:
    """机器可读的映射表（给工具层/面板用）。"""
    return [m.to_dict() for m in ROLE_TEMPLATES]


# ---------------------------------------------------------------------------
# 角色增强：hwforge 的 `spec` 不产出 `mcu`，这里补
# ---------------------------------------------------------------------------

#: 需求里出现这些词 → 需要一个主控。
MCU_WORDS: tuple[str, ...] = (
    "主控", "单片机", "控制器", "处理器", "核心板",
    "mcu", "cpu", "soc", "fpga",
    "stm32", "esp32", "esp8266", "arduino", "raspberry",
    "rp2040", "nrf52", "at32", "gd32", "ch32", "n32", "hc32",
)

#: 这些角色**必须**有主控才能成立（否则引脚无处可接）。
NEEDS_CONTROLLER: frozenset[str] = frozenset({
    "camera", "gimbal_pan", "gimbal_tilt", "servo_power", "sensor",
})

#: 已经算"有主控"的角色（`radio` 映射到 mcu_board，等于半个主控）。
CONTROLLER_ROLES: frozenset[str] = frozenset({"mcu", "radio"})


@dataclass
class RoleEnhancement:
    """角色增强的结果 —— 补了哪些、凭什么补。"""

    roles: list[str]
    added: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"roles": list(self.roles), "added": list(self.added),
                "reason": self.reason}


def enhance(roles_in: list[str], goal: str = "") -> RoleEnhancement:
    """补上 `spec` 不产出但设计必需的角色的。

    ## 为什么需要这一步

    `hwforge.spec.analyse_requirement()` 的能力表里**没有 `mcu`**：它只
    识别 相机/云台/舵机/无线/墙/手机。于是：

    * “相机 + 云台 + 电源”（没说无线）→ **没有主控**，相机的 `DATA` 脚
      和舵机的 `PWM` 脚全部悬空；
    * “STM32 主控的温湿度采集板” → 直接退化成 `undefined_hw`。

    这个函数在 **hw_velxio 侧**补这两件事，**不改 hwforge**：
    需求分析仍归 hwforge，但"哪些角色必须有"是装配侧的知识。

    ## 补的两条规则（都是确定性的）

    1. **提到主控词** → 加 `mcu`（用户明确说了主控，这不是猜）；
    2. **有需要主控的角色但没有主控** → 加 `mcu`（否则那些脚注定悬空）。

    **规则 2 在有 `undefined_hw` 时不生效**：`undefined_hw` 是"需求没描述
    硬件形态"的信号，此时凭空补一个主控会把"没说清"伪装成"说清了"。
    规则 1 不受这个限制 —— 用户自己说了 STM32，那就是说了。
    """
    out = list(dict.fromkeys(r for r in roles_in if r))
    added: list[str] = []
    reasons: list[str] = []

    has_controller = any(r in CONTROLLER_ROLES for r in out)
    undefined = "undefined_hw" in out

    if "mcu" not in out and not has_controller:
        lowered = (goal or "").lower()
        hit = next((w for w in MCU_WORDS if w.lower() in lowered), None)
        if hit is not None:
            out.append("mcu")
            added.append("mcu")
            reasons.append(f"需求提到主控词 {hit!r}")
        elif not undefined and any(r in NEEDS_CONTROLLER for r in out):
            out.append("mcu")
            added.append("mcu")
            reasons.append(
                "有需要主控的角色（"
                + "、".join(r for r in out if r in NEEDS_CONTROLLER)
                + "）但没有主控，不补的话它们的信号脚会全部悬空")

    return RoleEnhancement(roles=out, added=added,
                           reason="；".join(reasons))
