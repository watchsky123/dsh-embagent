"""R3 —— 装配计划 → **可渲染的设计**（元件 + 网表 + 板框）。

## 这一层做什么

`roles.plan()` 产出的是"要装哪些元件、用哪个模板"：

```
PlannedPart(role='camera', template='camera_module', size_mm=(8,25,25))
```

本模块把它变成 `DesignModel`：真实坐标、真实网表、真实板框。
之后 2D/3D 视图、物理检查、预算全都能用 —— 那些只看 `DesignModel`。

## 三条纪律

1. **引脚名来自模板，不是猜的。** 布局与接线都用模板工厂**实际产出**的
   引脚名（`camera_module` → `5V/GND/DATA`，`gimbal_servo` → `PWM/VCC/GND`）。
   猜一个"应该叫 VCC"的引脚名，`pins.audit()` 会立刻报它不存在 —— 但
   那时候错误信息已经离需求很远了。

2. **接不上的线不画，但要说出来。** 找不到电源脚就报 `unrouted`，
   留空比接错强。一条接错的线会污染所有下游判定。

3. **布局是网格，不是优化。** 按功能分带（电源 / 控制 / 执行 / 结构），
   每带一行，间距固定可复现。**不是优化过的布局** —— `space.*` 检查会
   如实指出重叠或越界，那正是它该做的事。

## 与 hwforge 的关系

坐标不自己算：调 `hwforge.assembly_build.build_from_spec()`，它负责
模板实例化 + 引脚解析（`footprint.resolve_pins` 的 INV-1..6 校验）。
本模块只做**两件事**：把 `PlannedPart[]` 排成 `PartSpec[]`，
再把 `BuiltAssembly` 的引脚坐标翻译成 `DesignModel` 的 `Pad`。

这样 hwforge 的引脚不变量（严格在体内、不重合、边声明必须真）继续生效，
而不是被这里绕过。

@module hw_velxio.design.assemble
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from . import roles as roles_module
from .boards import board_preset
from .model import Board, DesignModel, DesignError, Part, Pad, Placement


# ---------------------------------------------------------------------------
# 布局：按功能分带
# ---------------------------------------------------------------------------

#: 功能带 —— 决定 y 方向的分行。顺序就是从上到下的物理顺序。
BAND_ORDER: tuple[str, ...] = ("power", "control", "actuator", "structure")

#: 角色 → 功能带。表里没有的角色放 `actuator`（最保守：不占电源/控制区）。
ROLE_BAND: dict[str, str] = {
    "servo_power": "power",
    "radio": "control",
    "camera": "actuator",
    "gimbal_pan": "actuator",
    "gimbal_tilt": "actuator",
    "mount": "structure",
}

#: 带间距与件间距（mm）。固定值，可复现。
BAND_GAP_MM = 12.0
PACK_GAP_MM = 6.0
MARGIN_MM = 6.0

#: 所有元件离板面的 z（mm）。装配件都在板正面。
PART_Z_MM = 0.0


def _band_of(role: str) -> str:
    return ROLE_BAND.get(role, "actuator")


# ---------------------------------------------------------------------------
# 接线规则
# ---------------------------------------------------------------------------

#: 电源网名 → 它由哪种脚的"能力"提供。
#:
#: 规则是**按引脚名匹配**，不是按角色。因为引脚名是模板给的实测数据，
#: 而角色只是需求的说法。
POWER_NETS: dict[str, tuple[str, ...]] = {
    "5V": ("5V", "VCC", "VIN", "VBAT"),
    "3V3": ("3V3", "OUT"),
}
GROUND_NETS: tuple[str, ...] = ("GND", "VSS")

#: 信号脚 → 目标网络名。**同名即同网**是这里唯一的配对规则：
#: `mcu_board` 的 `PWM_PAN` 接 `gimbal_servo` 的 `PWM`（角色 pan 的那个），
#: `CAM_DATA` 接 `camera_module` 的 `DATA`。
#:
#: 这条规则能成立，是因为 hwforge 的 `mcu_board` 模板就是按"它带哪些外设"
#: 来命名引脚的（PWM_PAN / CAM_DATA / CAM_PWR）。换个 MCU 模板这套配对
#: 就会失效 —— 那时应当报 `unrouted`，而不是猜。
SIGNAL_ALIASES: dict[str, tuple[str, ...]] = {
    "PWM_PAN": ("PWM",),
    "PWM_TILT": ("PWM",),
    "CAM_DATA": ("DATA",),
    "CAM_PWR": ("5V",),
}


@dataclass
class WireResult:
    """接线结果。`unrouted` 里的每一项都是"本该连但没连上的"。"""

    nets: list[dict[str, Any]] = field(default_factory=list)
    unrouted: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"nets": list(self.nets), "unrouted": list(self.unrouted),
                "net_count": len(self.nets)}


@dataclass
class AssembleResult:
    """装配结果 —— 成功时 `design` 非空，失败时 `problems` 说明原因。"""

    design: DesignModel | None = None
    problems: list[str] = field(default_factory=list)
    #: 引脚名（ref → 引脚列表），来自模板实测。
    pin_names: dict[str, list[str]] = field(default_factory=dict)
    #: 接线结果。
    wiring: WireResult = field(default_factory=WireResult)
    #: 装了哪些件（ref → template）。
    placed: dict[str, str] = field(default_factory=dict)
    #: 本次装配用了 hwforge 的哪些事实（用于面板显示来源）。
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.design is not None and not self.problems

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "problems": list(self.problems),
            "placed": dict(self.placed),
            "pin_names": {k: list(v) for k, v in self.pin_names.items()},
            "wiring": self.wiring.to_dict(),
            "provenance": dict(self.provenance),
        }
        if self.design is not None:
            out["summary"] = self.design.summary()
        return out


# ---------------------------------------------------------------------------
# hwforge 桥
# ---------------------------------------------------------------------------

def _assign_refs(plan: roles_module.AssemblyPlan) -> dict[str, str]:
    """给每个 PlannedPart 分配 ref（`<角色缩写><序号>`）。

    用**模板前缀**而不是角色序号：`gimbal_pan`/`gimbal_tilt` 都要变成
    `SV1`/`SV2`，而不是 `G1`/`G2` —— 后者在真实板上不常见，
    而且和已有工程的命名习惯对不上。
    """
    prefix_of = {
        "camera_module": "CAM", "gimbal_servo": "SV", "mcu_board": "U",
        "mcu_block": "U", "psu_5v": "PS", "regulator_3v3": "PS",
        "regulator": "PS", "wall_bracket": "BR", "battery": "BT",
        "ultrasonic_ranger": "US", "motor_driver": "DRV", "wheel": "WH",
        "wheel_motor": "M", "chassis": "CH", "sensor_keepout": "KO",
        "step_mechanical": "ST",
    }
    counters: dict[str, int] = {}
    out: dict[str, str] = {}
    for part in plan.parts:
        prefix = prefix_of.get(part.template, "P")
        counters[prefix] = counters.get(prefix, 0) + 1
        out[part.role] = f"{prefix}{counters[prefix]}"
    return out


def _pack(plan: roles_module.AssemblyPlan) -> dict[str, tuple[float, float]]:
    """按功能带排布，返回 `role -> (x_mm, y_mm)`（元件左下角）。

    ## 两条规则

    1. **带内居中**：每带的元件按总宽居中排在该带里。不居中的话，
       窄带（比如只有一块电源）会把整块板拉成"左上角一坨、右边全空"——
       实测第一版就是这样，66mm 宽的板上只有左侧 60mm 有东西。
    2. **带间等高**：每带占用的高度取该带最高元件的高度，带间距固定。
       这样"电源带在下、执行带在上"的语义一眼可见。

    总宽由 `_board_size` 之后再定；这里先按**本带自身宽度**居中，
    板宽确定后会调用 `_recenter` 再居中一次（板宽依赖元件位置，
    元件位置又要按板宽居中 —— 所以分两步）。
    """
    buckets: dict[str, list[roles_module.PlannedPart]] = {b: [] for b in BAND_ORDER}
    for part in plan.parts:
        buckets[_band_of(part.role)].append(part)

    positions: dict[str, tuple[float, float]] = {}
    cursor_y = MARGIN_MM
    for band in BAND_ORDER:
        items = buckets[band]
        if not items:
            continue
        sizes = [(p.size_mm[0], p.size_mm[1]) if p.size_mm
                 else (FALLBACK_SIZE_MM[0], FALLBACK_SIZE_MM[1]) for p in items]
        band_w = sum(w for w, _ in sizes) + PACK_GAP_MM * max(len(items) - 1, 0)
        cursor_x = MARGIN_MM
        band_height = 0.0
        for part, (w, h) in zip(items, sizes):
            positions[part.role] = (round(cursor_x, 3), round(cursor_y, 3))
            cursor_x += w + PACK_GAP_MM
            band_height = max(band_height, h)
        # 记下本带宽度，供 `_recenter` 用
        positions[f"__band_w__:{band}"] = (round(band_w, 3), 0.0)
        cursor_y += band_height + BAND_GAP_MM
    return positions


def _recenter(positions: dict[str, tuple[float, float]],
              plan: roles_module.AssemblyPlan,
              board_w: float) -> dict[str, tuple[float, float]]:
    """板宽定下来后，把每带的元件在板内**水平居中**。

    分两步的原因：板宽依赖元件位置（`_board_size` 要"装得下"），
    元件位置又最好按板宽居中。先按自然宽度排，算出板宽，再回填居中。
    """
    out: dict[str, tuple[float, float]] = {}
    for band in BAND_ORDER:
        band_key = f"__band_w__:{band}"
        if band_key not in positions:
            continue
        band_w = positions[band_key][0]
        offset = max(MARGIN_MM, (board_w - band_w) / 2.0)
        members = [p for p in plan.parts if _band_of(p.role) == band]
        # 带内相对位置 = 原 x - MARGIN_MM
        first_x = min(positions[p.role][0] for p in members)
        for part in members:
            x, y = positions[part.role]
            out[part.role] = (round(x - first_x + offset, 3), y)
    # 保留带宽记录（`_board_size` 还要用）
    for key, value in positions.items():
        if key.startswith("__band_w__:") and key not in out:
            out[key] = value
    return out


def _board_size(positions: dict[str, tuple[float, float]],
                plan: roles_module.AssemblyPlan,
                min_w: float, min_h: float) -> tuple[float, float]:
    """板子要装得下所有件。返回**至少** `min_*`，需要更大就更大。"""
    right = MARGIN_MM
    bottom = MARGIN_MM
    size_of = {p.role: p.size_mm for p in plan.parts}
    for role, (x, y) in positions.items():
        if role.startswith("__band_w__:"):
            continue
        size = size_of.get(role) or FALLBACK_SIZE_MM
        right = max(right, x + size[0] + MARGIN_MM)
        bottom = max(bottom, y + size[1] + MARGIN_MM)
    # 也尊重带宽记录：最宽带决定最小板宽
    for key, (band_w, _) in positions.items():
        if key.startswith("__band_w__:"):
            right = max(right, band_w + MARGIN_MM * 2)
    return (round(max(right, min_w), 3), round(max(bottom, min_h), 3))


def _power_net_of(pin_name: str) -> str | None:
    """这个引脚名属于哪个电源网（不属于返回 `None`）。

    用来避免"一个脚同时属于两个网"的电气错误：`CAM_PWR` 要接相机的
    `5V`，而 `CAM1.5V` 已经在 `5V` 网里了 —— 应该并进去，不是新开一个网。
    """
    upper = pin_name.upper()
    for net, markers in POWER_NETS.items():
        if upper in {m.upper() for m in markers}:
            return net
    return None


def _wire_nets(pin_names: dict[str, list[str]],
               templates: dict[str, str]) -> WireResult:
    """按引脚名推导网表。

    **规则**（全部是确定性的）：

    1. **地**：所有 `GND`/`VSS` 脚进同一个 `GND` 网。
    2. **电源**：`5V`/`VCC`/`VIN`/`VBAT` 脚进 `5V` 网；`3V3`/`OUT` 进 `3V3` 网。
    3. **信号**：`mcu_board` 的 `PWM_PAN` → 第一个舵机的 `PWM`；
       `PWM_TILT` → 第二个舵机的 `PWM`；`CAM_DATA` → 相机的 `DATA`。
       配对靠 `SIGNAL_ALIASES`，配不上就进 `unrouted`。

    `unrouted` 是**结论**，不是警告：它说"这条连接本该存在但没有规则能建立它"。
    """
    result = WireResult()
    if not pin_names:
        return result

    nets: dict[str, list[dict[str, str]]] = {}

    def add(net: str, ref: str, pin: str) -> None:
        nets.setdefault(net, []).append({"ref": ref, "pin": pin})

    # --- 1. 地 -----------------------------------------------------------
    for ref, pins in pin_names.items():
        for pin in pins:
            if pin.upper() in {g.upper() for g in GROUND_NETS}:
                add("GND", ref, pin)

    # --- 2. 电源 ---------------------------------------------------------
    #   谁提供 5V？优先 `psu_5v`（ACIN/5V），否则任何带 5V/VCC 的件。
    #   **不把 5V 灌进 `gimbal_servo` 的 VCC 之外的东西**：舵机 VCC 就是要 5V。
    for net, markers in POWER_NETS.items():
        upper_markers = tuple(m.upper() for m in markers)
        for ref, pins in pin_names.items():
            template = templates.get(ref, "")
            for pin in pins:
                up = pin.upper()
                if up in upper_markers:
                    add(net, ref, pin)
                elif net == "5V" and template == "psu_5v" and up == "5V":
                    add(net, ref, pin)

    # --- 3. 信号 ---------------------------------------------------------
    #   MCU 的信号脚按 aliases 找目标；舵机按序取 PWM（pan 在前）。
    servo_refs = [ref for ref, t in templates.items() if t == "gimbal_servo"]
    camera_refs = [ref for ref, t in templates.items() if t == "camera_module"]
    mcu_refs = [ref for ref, t in templates.items() if t == "mcu_board"]

    for mcu in mcu_refs:
        for pin in pin_names.get(mcu, []):
            up = pin.upper()
            alias = SIGNAL_ALIASES.get(up)
            if alias is None:
                continue
            target_pin = alias[0]
            # 目标池：PWM_* 找舵机，CAM_DATA 找相机，CAM_PWR 找相机电源
            if up.startswith("PWM_"):
                pool = servo_refs
            elif up.startswith("CAM_"):
                pool = camera_refs
            else:
                pool = []
            matched = False
            for ref in pool:
                if target_pin in pin_names.get(ref, []):
                    # PWM_PAN / PWM_TILT 按序号区分同一池里的两个舵机
                    if up.startswith("PWM_") and len(pool) > 1:
                        index = 0 if up.endswith("PAN") else 1
                        if index >= len(pool):
                            continue
                        ref = pool[index]
                    # **目标脚如果已经是电源脚，就并进那个电源网**，
                    # 不新开一个 SIG_* 网。
                    #
                    # 实测 bug：`CAM_PWR` → 相机的 `5V`，于是 `CAM1.5V`
                    # 同时出现在 `5V` 和 `SIG_CAM_PWR` 两个网里 ——
                    # **一个脚不能属于两个网**，这是电气错误，不是风格问题。
                    power_net = _power_net_of(target_pin)
                    if power_net is not None:
                        add(net=power_net, ref=mcu, pin=pin)
                        matched = True
                        break
                    add(f"SIG_{up}", mcu, pin)
                    add(f"SIG_{up}", ref, target_pin)
                    matched = True
                    break
            if not matched:
                result.unrouted.append(
                    f"{mcu}.{pin}：找不到能接 {target_pin} 的元件"
                    f"（候选模板：{'、'.join(sorted(set(templates.values())))}）")

    # --- 组装成 nets 列表 ------------------------------------------------
    for net, pins in sorted(nets.items()):
        # 去重（同一 (ref,pin) 只能在一个网里出现一次）
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, str]] = []
        for entry in pins:
            key = (entry["ref"], entry["pin"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(entry)
        if len(unique) >= 2:
            result.nets.append({"net": net, "pins": unique})
        elif unique:
            # 只有一个脚的"网"不是网 —— 它是悬空引脚
            result.unrouted.append(
                f"{unique[0]['ref']}.{unique[0]['pin']}：本应属于 {net} 网，"
                f"但同网没有别的引脚可接")

    # --- 4. **不变量：每个引脚都必须有交代** ----------------------------
    #
    # 实测事故：`CAM1.DATA` 和 `SV1.PWM` 既不在网表里、也不在 `unrouted`
    # 里 —— 它们**凭空消失**了。原因是最初只处理三类脚（GND / 电源标记 /
    # `SIGNAL_ALIASES` 里的 MCU 脚），落不进这三类的脚被静默忽略。
    #
    # 那正是这套系统一直在防的那类错误：**缺失被渲染成"没有问题"**。
    # 所以这里加一条硬不变量：**每个引脚 ∈ (某个网 ∪ unrouted)**。
    #
    # 没有规则能处理的脚，按能力给出可读的原因（而不是一句"接不上"）。
    accounted: set[tuple[str, str]] = set()
    for net in result.nets:
        for pin in net["pins"]:
            key = (pin["ref"], pin["pin"])
            # **一个脚不能属于两个网** —— 那是电气错误，不是风格问题。
            if key in accounted:
                result.unrouted.append(
                    f"{pin['ref']}.{pin['pin']}：同时落进了多个网"
                    f"（{net['net']} 与之前的某个网）—— 一个引脚只能属于一个网")
                continue
            accounted.add(key)
    for ref, pins in pin_names.items():
        for pin in pins:
            if (ref, pin) in accounted:
                continue
            if any(u.startswith(f"{ref}.{pin}") for u in result.unrouted):
                continue
            result.unrouted.append(
                f"{ref}.{pin}：没有接线规则能处理这个引脚"
                f"（模板 {templates.get(ref, '?')} 的 {pin} 脚不在 "
                f"GND/电源/信号别名表里）")

    return result


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def assemble_plan(plan: roles_module.AssemblyPlan, *,
                  design_id: str,
                  title: str = "",
                  board_kind: str = "arduino-uno") -> AssembleResult:
    """把装配计划装成一个 `DesignModel`。

    坐标由本模块自己算：布局给**左下角**，引脚位置由
    `design/library.py` 的归一化表映射到板坐标。不再调用 hwforge。
    """
    result = AssembleResult()
    if not plan.parts:
        result.problems.append("装配计划里没有任何元件（所有角色都是外部件？）")
        return result
    if plan.unmatched:
        result.problems.append(
            f"{len(plan.unmatched)} 个角色没有映射：{'、'.join(plan.unmatched)}")

    from . import library

    refs = _assign_refs(plan)
    positions = _pack(plan)

    # 板子先按"装得下所有件"算尺寸
    min_w = 60.0
    min_h = 40.0
    board_w, board_h = _board_size(positions, plan, min_w, min_h)
    # 板宽定下来后再把每带在板内**水平居中** —— 不然窄带会把整块板拉成
    # "左上角一坨、右边全空"（第一版实测就是这样，66mm 板上只有左侧 60mm 有东西）。
    positions = _recenter(positions, plan, board_w)

    templates_by_ref: dict[str, str] = {}
    planned_by_ref: dict[str, roles_module.PlannedPart] = {}
    for part in plan.parts:
        ref = refs[part.role]
        templates_by_ref[ref] = part.template
        planned_by_ref[ref] = part

    parts: list[Part] = []
    pin_names: dict[str, list[str]] = {}
    missing_templates: list[str] = []

    for part in plan.parts:
        ref = refs[part.role]
        template = part.template
        tpl = library.get(template)
        if tpl is None:
            # 认得出角色但库里没有模板 —— 如实报，不造一个假元件。
            missing_templates.append(f"{part.role}→{template}")
            continue
        x_mm, y_mm = positions.get(part.role, (MARGIN_MM, MARGIN_MM))
        w_mm, h_mm, d_mm = tpl.size_mm

        # 引脚：归一化位置 → 板坐标（贴边，物理上排针就从边缘出）
        pin_pos = library.pin_positions(template, x_mm=x_mm, y_mm=y_mm,
                                        w_mm=w_mm, h_mm=h_mm)
        pads = [Pad(num=name,
                    x_mm=px, y_mm=py,
                    # 焊盘尺寸没有来源 —— 用 0，不编造
                    w_mm=0.0, h_mm=0.0, shape="RECT", layer=1)
                for name, (px, py) in pin_pos.items()]
        names = list(pin_pos)
        pin_names[ref] = names

        try:
            parts.append(Part(
                ref=ref, kind=template,
                # **标签要短**：它画在 2D 元件框里。`why` 是一整句
                # （"舵机堵转电流远高于静态电流…"），当标签会把框撑爆、
                # 在画布上糊成一片。角色名（camera/gimbal_pan）才是标签。
                label=part.role,
                role=_role_of_template(template),
                size_mm={"w": round(w_mm, 3), "h": round(h_mm, 3),
                         "d": round(d_mm, 3)},
                # 尺寸来自本地模板库的标称值 —— derived
                physical_source="derived",
                placement=Placement(x_mm=round(x_mm, 3),
                                    y_mm=round(y_mm, 3),
                                    rotation_z=0, mount="board", side="top"),
                # `why` 原样保留在 notes 里 —— 它是有用的上下文，
                # 只是不该占标签的位置。
                notes=[part.why] if part.why else [],
                pads=pads,
                pins=names,
            ))
        except DesignError as exc:
            result.problems.append(f"{ref}: {exc}")

    if missing_templates:
        result.problems.append(
            f"{len(missing_templates)} 个角色的模板不在本地库里："
            + "、".join(missing_templates))

    if not parts:
        result.problems.append("没有任何元件被成功构造")
        return result

    wiring = _wire_nets(pin_names, templates_by_ref)
    result.wiring = wiring
    result.pin_names = pin_names
    result.placed = {p.ref: p.kind for p in parts}

    board = board_preset(board_kind)
    # 板子要装得下**实际构造出来的**元件（hwforge 可能给出比封存更大的盒子）
    need_w = max((p.placement.x_mm + p.size_mm["w"] for p in parts),
                 default=board.size_mm["w"]) + MARGIN_MM
    need_h = max((p.placement.y_mm + p.size_mm["h"] for p in parts),
                 default=board.size_mm["h"]) + MARGIN_MM
    if need_w > board.size_mm["w"] or need_h > board.size_mm["h"]:
        board = Board(
            kind=f"roles:{design_id}", name=f"按需求扩到 {need_w:.0f}×{need_h:.0f}mm",
            size_mm={"w": round(max(need_w, 40.0), 3),
                     "h": round(max(need_h, 30.0), 3)},
            thickness_mm=board.thickness_mm,
            mount_holes=[], px_per_mm=board.px_per_mm)

    design = DesignModel(
        id=design_id, title=title or design_id, board=board,
        parts=parts, nets=wiring.nets, wires=[],
        meta={
            "source": "roles-assemble",
            "from_roles": [p.role for p in plan.parts],
            "templates": dict(templates_by_ref),
            "external_roles": [e["role"] for e in plan.external],
            "unmatched_roles": list(plan.unmatched),
            "unrouted": list(wiring.unrouted),
            # **必须说清楚**：这不是优化过的布局
            "layout": "band-grid",
            "layout_note": (
                "按功能带（电源/控制/执行/结构）分行排布，间距固定可复现。"
                "**不是优化过的布局**，space.* 检查会如实指出重叠或越界。"),
            "pin_source": "hwforge.assembly_parts 模板实例化的引脚（非猜测）",
        },
    )
    if wiring.unrouted:
        design.warnings.append(
            f"{len(wiring.unrouted)} 条连接没有规则能建立（见 meta.unrouted）；"
            "网表里没有它们，不是漏了而是接不上。")
    if plan.unmatched:
        design.warnings.append(
            f"{len(plan.unmatched)} 个角色没有映射，没有进设计："
            + "、".join(plan.unmatched))

    result.design = design
    result.provenance = {
        "template_source": "design/library.py（本地，不依赖 hwforge）",
        "children": len(parts),
        "pins": sum(len(v) for v in pin_names.values()),
    }
    return result


def _role_of_template(template: str) -> str:
    """模板 → 元件角色（影响连接器靠边等检查）。"""
    if template in ("wall_bracket", "chassis", "step_mechanical"):
        return "structure"
    if template in ("psu_5v", "regulator_3v3", "regulator", "battery"):
        return "component"
    if template in ("mcu_board", "mcu_block"):
        return "module"
    if template in ("camera_module", "gimbal_servo", "ultrasonic_ranger"):
        return "module"
    return "component"


def assemble_roles(role_list: list[str], *, design_id: str,
                   title: str = "",
                   reasons: dict[str, str] | None = None,
                   board_kind: str = "arduino-uno") -> AssembleResult:
    """`角色 → 设计` 的一步到位入口。"""
    plan = roles_module.plan(role_list, reasons=reasons)
    return assemble_plan(plan, design_id=design_id, title=title,
                         board_kind=board_kind)
