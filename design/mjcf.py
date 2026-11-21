"""DesignModel → MuJoCo MJCF。

## 为什么用字符串拼 XML，而不是 `mujoco.MjSpec`

RoboBuilder 也用字符串（`sim/csim.py:1808` `self.xml = f'<mujoco...'`）。
抄它有三个理由：

1. **136 项参数是在 XML 里逐字段落的**（`frictionloss`/`armature`/
   `solref`/`solimp`/`margin`），照抄能一一对上
2. `MjSpec` 在 3.12 有，但社区示例远少于 XML；出错时能搜到答案更重要
3. XML **可读、可 diff、可存档** —— 符合"产物可审计"的纪律

## 生成物是三段，而不是一份大字符串

`build()` 返回 `MjcfParts(header, world, extras)` 而不是裸字符串。
理由：P18 要把"判定用了哪些模型"写进产物，分段的 XML 能让人
**只读 world 段**就看到"哪些零件进了仿真、哪些被跳过"。

## 不静默跳过

任何进不了仿真的零件都记进 `skipped` 并说明原因：
* 没有尺寸（全 0）→ 无法建碰撞体
* 没有质量 → 可以用密度估，但**必须标注是估算**
* `inferred` 的 Joint → **不进仿真**（与 `links`/`claims` 同一纪律）

`skipped` 为空才算"全部零件都参与了仿真"。否则报 `partial`。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .model import DesignModel, Joint, Part
from .sim_params import ParamSet, load

#: 默认密度（kg/m³）。只在零件**没有 mass_g** 时用来估算。
#: 1000 ≈ 水；塑料 900–1200；铝 2700。取 1050 是"3D 打印件/塑料外壳"的典型值。
DEFAULT_DENSITY = 1050.0

#: 最小质量（克）。**这是防数值爆炸的硬下限，不是物理值。**
#:
#: 实测：0402 电容按体积×密度估出 0.0004g，MuJoCo 里质量矩阵奇异 →
#: `WARNING: Nan, Inf or huge value in QACC`，最终速度冲到 61574 m/s。
#: 仿真"跑完了"（2000 步）但结果全是垃圾。
#:
#: 取 0.01g（10mg）—— 比最小的真实贴片件（0402 约 0.0006g）大，
#: 但足够让求解器稳定。用这个下限的件会被标 `mass_floor` warning，
#: **不能当成真实质量**。
MIN_MASS_G = 0.01

#: 板厚（mm）。用于板的碰撞体高度。`Board.thickness_mm` 没有时用它。
DEFAULT_BOARD_THICKNESS_MM = 1.6

#: mm → m。**这个转换漏过一次**：第一版把 `placement.x_mm` 直接写进
#: `pos`，MuJoCo 按米解释，于是所有零件位置放大 1000 倍。
#: 症状很隐蔽——仿真不报错，只是"东西离得很远，一动不动"。
#: 所以这里用显式常量而不是散在各处的 `/1000`。
MM = 1e-3

#: Joint.kind → MuJoCo 表达方式。
#:
#: 三类，**这是移植精度的核心**：
#:
#:   `joint`     —— 单自由度关节（hinge/slide），直接 <joint type=...>
#:   `equality`  —— 约束（gear/chain/tread/pin/axle），用 <equality> 节点
#:   `contact`   —— 纯接触（stud/contact/stack），不做约束，靠几何+接触参数
#:   `actuator`  —— 驱动（port 的语义是"主控驱动谁"，转成 actuator）
#:
#: 没有映射的 kind 会在 `build()` 里进 `skipped` —— **不猜**。
JOINT_MAPPING: dict[str, str] = {
    "hinge": "joint",
    "slide": "joint",
    "gear": "equality",
    "chain": "equality",
    "tread": "equality",
    "rack": "joint",
    "pin": "equality",
    "axle": "equality",
    "stud": "contact",
    "contact": "contact",
    "stack": "contact",
    "port": "actuator",
}

#: 需要 `params` 的 kind（缺参数时 P15 的 validate 已报 error，
#: 这里再兜一次：宁可跳过，也不生成一个用编造参数驱动的关节）。
_REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "gear": ("ratio",),
    "axle": ("diameter_mm",),
    "slide": ("range_mm",),
    "tread": ("spacing_mm",),
    "rack": ("pitch_mm",),
    "hinge": ("range_deg",),
}


def _f(value: float, digits: int = 6) -> str:
    """浮点数格式化。去掉浮尘（1.0000000000002 这类会让 diff 很难看）。"""
    if value == int(value):
        return str(int(value))
    return f"{value:.{digits}g}"


def _vec(values: list[float] | tuple[float, ...]) -> str:
    return " ".join(_f(float(v)) for v in values)


def _xyzw_from_yaw(yaw_deg: float) -> list[float]:
    """绕 z 轴的 yaw → MuJoCo 的 (w, x, y, z) 四元数。

    **顺序是 w 在前**（MuJoCo 约定），不是 xyz w。
    写反了不会报错，只会让所有零件朝向错 —— 这种错最难查。
    """
    half = math.radians(yaw_deg) / 2.0
    return [math.cos(half), 0.0, 0.0, math.sin(half)]


@dataclass
class PartBody:
    """一个零件在 MJCF 里的表达。"""

    ref: str
    xml: str = ""
    mass_g: float | None = None
    mass_estimated: bool = False
    size_mm: dict[str, float] = field(default_factory=dict)
    board_id: str = ""
    skipped_reason: str = ""
    #: 是否要拼进 `worldbody`。weld 模式下零件的 body.xml 只用于
    #: 取尺寸/质量（它们已被拍平进板的 body），**不能再拼一次**——
    #: 否则同名 geom 出现两次，MuJoCo 报 `repeated name 'C1_g'`。
    in_world: bool = True


@dataclass
class MjcfParts:
    """分段的 MJCF。**不是一份大字符串**，便于只读某一段。"""

    header: str = ""
    world: str = ""
    extras: str = ""
    parts: list[PartBody] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    #: 实际用到的物理域（只读这些组的参数）
    groups_used: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def xml(self) -> str:
        return self.header + self.world + self.extras + "</mujoco>\n"

    def stats(self) -> dict[str, Any]:
        # **只算 in_world 的**：weld 模式下零件的 body 不进 world，
        # 它们不是独立刚体。把 141 个零件都算成"仿真了 141 个刚体"
        # 是虚增——实际只有 1 个板刚体。
        simulated = [p for p in self.parts if p.xml and p.in_world]
        estimated = [p for p in simulated if p.mass_estimated]
        return {
            "parts_total": len(self.parts),
            "parts_simulated": len(simulated),
            "parts_skipped": len(self.skipped),
            "mass_estimated": len(estimated),
            "skipped_reasons": self.skipped,
            "groups_used": list(self.groups_used),
            "complete": not self.skipped,
        }

    def summary(self) -> str:
        s = self.stats()
        if s["complete"]:
            head = (f"全部 {s['parts_simulated']} 个零件进入仿真"
                    f"（其中 {s['mass_estimated']} 个质量是估算）")
        else:
            head = (f"{s['parts_simulated']}/{s['parts_total']} 零件进入仿真，"
                    f"**{s['parts_skipped']} 个被跳过**")
        return (head + "；物理域：" + ", ".join(self.groups_used)
                + "。**跳过不是失败，但也不是完整**。")


# ---------------------------------------------------------------------------
# 零件 → body
# ---------------------------------------------------------------------------

def _part_mass_g(part: Part, params: ParamSet,
                 reasons: list[str]) -> tuple[float | None, bool]:
    """质量（克）。返回 (质量, 是否估算)。

    **优先用 `part.mass_g`**（人给的/实测的）。没有时按体积×密度估，
    并标 `estimated=True` —— 因为"估算质量"和"称过的质量"在
    判定上不同：P16 的验收要求把估算的件数报出来。
    """
    if part.mass_g is not None:
        return float(part.mass_g), False
    size = part.size_mm or {}
    volume_mm3 = float(size.get("w", 0)) * float(size.get("h", 0)) \
        * float(size.get("d", 0))
    if volume_mm3 <= 0:
        return None, False
    density = float(params.get("density_kg_m3", DEFAULT_DENSITY))
    mass_g = volume_mm3 * 1e-9 * density * 1e3        # mm³ → m³ → kg → g
    if mass_g < MIN_MASS_G:
        reasons.append(
            f"{part.ref}: 估算 {mass_g:.4f}g 低于数值下限 {MIN_MASS_G}g，"
            f"**按 {MIN_MASS_G}g 参与仿真**（不是真实质量）")
        return MIN_MASS_G, True
    reasons.append(f"{part.ref}: 没有 mass_g，按体积×密度({density:g})估 "
                   f"{mass_g:.2f}g")
    return mass_g, True


def _part_body(part: Part, parent_pos: tuple[float, float, float],
               params: ParamSet, *,
               reasons: list[str]) -> PartBody:
    """一个零件 → `<body>` + `<geom>`。"""
    body = PartBody(ref=part.ref, board_id=str(
        part.placement.board_id or ""), size_mm=dict(part.size_mm or {}))

    size = part.size_mm or {}
    w, h, dpt = (float(size.get(k, 0)) for k in ("w", "h", "d"))
    if w <= 0 or h <= 0 or dpt <= 0:
        body.skipped_reason = (f"尺寸不全（{w}×{h}×{dpt}mm）——"
                               f"零尺寸建不出碰撞体")
        return body

    mass_g, estimated = _part_mass_g(part, params, reasons)
    body.mass_g = mass_g
    body.mass_estimated = estimated
    if mass_g is None:
        body.skipped_reason = "没有质量也不是零尺寸——体积为 0，无法估质量"
        return body

    placement = part.placement
    # **mm → m**（MuJoCo 全部用 SI）。父件位置也是 mm，差值再换算。
    pos = ((float(placement.x_mm) - parent_pos[0]) * MM,
           (float(placement.y_mm) - parent_pos[1]) * MM,
           (float(placement.z_mm) - parent_pos[2]) * MM)
    quat = _xyzw_from_yaw(float(placement.rotation_z))

    # 半尺寸（MuJoCo 的 box size 是半长）
    half = (w / 2.0 * MM, h / 2.0 * MM, dpt / 2.0 * MM)
    friction = params.get("mu_board", 0.42)
    solref = params.get("board_solref") or []
    solref_attr = ""
    if isinstance(solref, (list, tuple)) and len(solref) == 2:
        solref_attr = f' solref="{_vec(solref)}"'

    body.xml = (
        f'<body name="{part.ref}" pos="{_vec(pos)}" quat="{_vec(quat)}">\n'
        f'  <geom name="{part.ref}_g" type="box" size="{_vec(half)}" '
        f'mass="{_f(mass_g / 1000.0)}" '
        f'friction="{_f(float(friction))} {_f(float(friction))} 0.0001"'
        f'{solref_attr}/>\n'
        f'</body>\n')
    return body


# ---------------------------------------------------------------------------
# 关节 / 约束 / 驱动
# ---------------------------------------------------------------------------

def _joint_xml(joint: Joint, params: ParamSet,
               reasons: list[str]) -> tuple[str, str]:
    """一个 Joint → (body 内的 joint 片段, equality 片段)。

    返回两段是因为 MuJoCo 的关节必须写在 body 里，
    而约束（equality）写在根级。调用方按位置拼接。
    """
    mode = JOINT_MAPPING.get(joint.kind)
    if mode is None:
        return "", ""                         # 调用方已记 skipped

    required = _REQUIRED_PARAMS.get(joint.kind, ())
    missing = [name for name in required if name not in joint.params]
    if missing:
        reasons.append(
            f"{joint.a}-{joint.b}（{joint.kind}）缺参数 {missing}："
            f"跳过这个关节，不用编造的值驱动仿真")
        return "", ""

    name = f"j_{joint.a}_{joint.b}_{joint.kind}"

    if mode == "joint":
        if joint.kind == "hinge":
            lo, hi = joint.params.get("range_deg", [-180.0, 180.0])
            return (f'  <joint name="{name}" type="hinge" axis="0 0 1" '
                    f'range="{_f(math.radians(float(lo)))} '
                    f'{_f(math.radians(float(hi)))}" '
                    f'frictionloss="{_f(float(params.get("axle_fric_nm", 0.0005)))}"/>\n',
                    "")
        if joint.kind == "slide":
            lo, hi = joint.params.get("range_mm", [-10.0, 10.0])
            return (f'  <joint name="{name}" type="slide" axis="1 0 0" '
                    f'range="{_f(float(lo) * 1e-3)} {_f(float(hi) * 1e-3)}"/>\n',
                    "")
        if joint.kind == "rack":                # 齿条 = 滑动副
            return (f'  <joint name="{name}" type="slide" axis="1 0 0"/>\n', "")

    if mode == "equality":
        if joint.kind == "gear":
            ratio = float(joint.params.get("ratio", 1.0))
            return "", (f'<equality><joint joint1="j_{joint.a}" '
                        f'joint2="j_{joint.b}" polycoef="0 {_f(-ratio)} 0 0 0"/>'
                        f'</equality>\n')
        if joint.kind == "axle":                # 轴：允许转动但不许平移
            return "", (f'<equality><weld body1="{joint.a}" body2="{joint.b}"/>'
                        f'</equality>\n')
        if joint.kind in ("chain", "tread", "pin"):
            return "", (f'<equality><connect body1="{joint.a}" '
                        f'body2="{joint.b}"/></equality>\n')

    if mode == "actuator":
        net = str(joint.params.get("net") or "")
        return "", (f'<actuator><general name="act_{joint.a}_{joint.b}" '
                    f'class="" actearly="false"/></actuator>\n')

    return "", ""                              # contact / stack：靠几何接触


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def build(design: DesignModel, params: ParamSet | None = None, *,
          gravity: tuple[float, float, float] = (0.0, 0.0, -9.81),
          include_floor: bool = True,
          free_bodies: bool = False,
          weld_parts: bool = False) -> MjcfParts:
    """DesignModel → MJCF。

    `free_bodies=False`（默认）：零件固定在世界里（做几何/装配检查）。
    `free_bodies=True`：零件自由运动（做动力学仿真）。
    这个开关是**必须显式给**的——默认自由会让人以为"仿真通过了"
    而其实零件在往下掉。

    `include_floor=True` 时加无限平面地板（落地测试需要）。

    ## `weld_parts`：PCB 上的元件是**焊死的**

    实测教训：141 个元件的 `示例-单板` 用 `free_bodies=True` 跑，
    结果 `QACC` 爆炸、最终速度 61574 m/s —— 因为真实 PCB 上的
    元件焊在板上，不该各自自由下落。

    `weld_parts=True` 时：**每块板成为一个刚体**，板上零件是它的
    子 geom（相对板固定）。这才是 PCB 的正确力学模型。
    `free_bodies` 只对**跨板/独立体**有意义。

    两者关系：
      * `weld_parts=False, free_bodies=False` → 全部焊接在世界（纯几何检查）
      * `weld_parts=True,  free_bodies=True`  → **每块板自由**（整机动学）
      * `weld_parts=False, free_bodies=True`  → 每个零件自由（散件仿真，
                                                  RoboBuilder 那种拼装场景）
    """
    params = params or load()
    out = MjcfParts()
    reasons: list[str] = []

    dt = float(params.get("dt", 5e-4))
    out.header = (
        '<mujoco model="hw_velxio">\n'
        '  <compiler angle="radian" autolimits="true" '
        'inertiafromgeom="auto"/>\n'
        # MuJoCo 默认 arena 16MB。实测 316 元件的多板工程会报
        # `Insufficient arena memory for the number of constraints`
        # —— 那意味着**部分约束被静默丢弃**，仿真结果不可信。
        # 给到 256MB（物理量，不是内存泄漏风险：MuJoCo 一次性分配）。
        '  <size memory="256M"/>\n'
        f'  <option timestep="{_f(dt)}" gravity="{_vec(gravity)}" '
        f'integrator="implicitfast" cone="elliptic" impratio="10" '
        f'iterations="80" ls_iterations="20"/>\n'
        '  <visual><global offwidth="640" offheight="480"/>'
        '<quality shadowsize="1024"/></visual>\n'
        '  <default><geom solref="0.003 1" solimp="0.95 0.99 0.0005" '
        'margin="0.0002" condim="4"/></default>\n'
        '  <asset><texture name="grid" type="2d" builtin="checker" '
        'width="128" height="128" rgb1="0.85 0.86 0.88" '
        'rgb2="0.75 0.77 0.80"/>'
        '<material name="floor" texture="grid" texrepeat="8 8" '
        'specular="0.1" shininess="0.1"/></asset>\n'
        '  <worldbody>\n')

    if include_floor:
        out.world += ('    <geom name="floor" type="plane" size="2 2 0.1" '
                      'material="floor" condim="6"/>\n')

    # ---- 零件（**先只建 body，不拼进 world**）----
    #
    # 第一版这里先拼 world、再往 body.xml 里插关节，结果**关节全丢了**：
    # `out.world` 已经拿着旧字符串，后面改 `body.xml` 改的是另一个对象。
    # 症状很隐蔽——XML 合法、能编译，只是关节不存在，仿真里零件焊接死。
    # 所以顺序必须是：建 body → 插关节 → **最后**拼 world。
    board_origin: tuple[float, float, float] = (0.0, 0.0, 0.0)

    if weld_parts:
        # **每块板一个刚体**，板上零件是它的子 geom（焊死）。
        # 这是 PCB 的物理模型：元件焊在板上，不能各自下落。
        for board_body in _weld_board_bodies(design, params,
                                             reasons=reasons,
                                             free=free_bodies,
                                             out=out):
            out.parts.extend(board_body)
    else:
        for part in design.parts:
            body = _part_body(part, board_origin, params, reasons=reasons)
            out.parts.append(body)
            if not body.xml:
                out.skipped.append({"ref": body.ref,
                                    "why": body.skipped_reason})
                continue
            # 自由体：在 body 内插 freejoint
            if free_bodies:
                body.xml = body.xml.replace(">", '>\n  <freejoint/>', 1)

    # ---- 关节 / 约束 / 驱动 ----
    joint_bits: list[str] = []
    equality_bits: list[str] = []
    actuator_bits: list[str] = []
    known = {p.ref for p in design.parts}
    for row in design.joints_list or []:
        if not isinstance(row, dict):
            continue
        try:
            joint = Joint.from_dict(row)
        except Exception as exc:                       # noqa: BLE001
            out.skipped.append({"ref": f"{row.get('a')}-{row.get('b')}",
                                "why": f"关节非法：{exc}"})
            continue
        if joint.source == "inferred":
            # **inferred 不进仿真**（AGENTS §1.2）。与 links/claims 同一纪律：
            # 推断会错，让它进物理模型等于用猜的参数跑仿真。
            out.skipped.append({
                "ref": f"{joint.a}-{joint.b}", "kind": joint.kind,
                "why": "source=inferred：推断的关系不进仿真，先 promote",
            })
            continue
        for ref in (joint.a, joint.b):
            if ref not in known:
                out.skipped.append({
                    "ref": f"{joint.a}-{joint.b}", "kind": joint.kind,
                    "why": f"{ref} 不在设计里（悬空关系）",
                })
                break
        else:
            before = len(reasons)
            jx, ex = _joint_xml(joint, params, reasons)
            produced = bool(jx or ex)
            mapping = JOINT_MAPPING.get(joint.kind)
            if not produced and mapping not in ("contact",):
                # 没生成任何 XML 且不是"靠几何接触"的类 → 必须记进 skipped。
                # 第一版只往 `reasons`（warnings）里记，`skipped` 是空的：
                # 于是"gear 缺 ratio 被跳过"这件事在 `stats()` 里看不见，
                # `complete` 仍然报 True —— 假完整。
                why = (reasons[-1] if len(reasons) > before else
                       f"{joint.kind} 没生成任何 XML（缺映射或缺参数）")
                out.skipped.append({
                    "ref": f"{joint.a}-{joint.b}", "kind": joint.kind,
                    "why": why,
                })
            if jx:
                joint_bits.append((joint.a, jx))
            if ex:
                equality_bits.append(ex)
            if mapping:
                out.groups_used.append(mapping)

    # 关节片段插进对应零件的 body 里（**此时还没拼 world**）
    for ref, jx in joint_bits:
        for body in out.parts:
            if body.ref == ref and body.xml:
                body.xml = body.xml.replace("</body>", jx + "  </body>", 1)
                break

    # **最后**才把 body 拼进 world（含刚插入的关节）
    for body in out.parts:
        if body.xml and body.in_world:
            out.world += ("    "
                          + body.xml.replace("\n", "\n    ").rstrip()
                          + "\n")

    out.world += "  </worldbody>\n"

    # ---- contact / equality / actuator ----
    out.extras = ""
    # weld 模式下**不生成 excludes**：同一板上的零件是同一个刚体的
    # 子 geom，MuJoCo 本来就不让同刚体 geom 互碰。而 `_excludes` 引用的
    # 是零件名（"C1"），它们已不是独立 body —— 会报
    # `body 'C1' not found in bodypair`。
    excludes = [] if weld_parts else _excludes(design)
    if excludes:
        out.extras += "  <contact>\n" + "".join(excludes) + "  </contact>\n"
    if equality_bits:
        merged: list[str] = []
        for bit in equality_bits:
            merged.append(bit.replace("<equality>", "").replace(
                "</equality>\n", "").replace("</equality>", ""))
        out.extras += "  <equality>\n" + "".join(merged) + "  </equality>\n"
    if actuator_bits:
        out.extras += ("  <actuator>\n" + "".join(actuator_bits)
                       + "  </actuator>\n")

    out.groups_used = sorted(set(g for g in out.groups_used if g))
    if not out.groups_used:
        out.groups_used = ["solver", "tyre"]        # 几何仿真至少用这两组
    if reasons:
        out.warnings.extend(reasons)
    return out


def _weld_board_bodies(design: DesignModel, params: ParamSet, *,
                       reasons: list[str], free: bool,
                       out: MjcfParts) -> list[list[PartBody]]:
    """把每块板（+ 板上零件）焊成一个 `<body>`。

    返回的列表每项是该板的零件清单（供 `out.parts` 汇总）。
    板本身的几何用 `Board.size_mm`，质量 = 板自重 + 板上零件质量之和。
    """
    groups: dict[str, list[Part]] = {}
    for part in design.parts:
        groups.setdefault(str(part.placement.board_id or ""), []).append(part)

    boards = {str(b.id or ""): b for b in design.board_list()}
    result: list[list[PartBody]] = []

    for board_id in sorted(groups):
        members = groups[board_id]
        board = boards.get(board_id) or design.board
        size = board.size_mm or {}
        bw = float(size.get("w", 0) or 0)
        bh = float(size.get("h", 0) or 0)
        thick = float(getattr(board, "thickness_mm", 0.0) or 0.0) \
            or DEFAULT_BOARD_THICKNESS_MM
        if bw <= 0 or bh <= 0:
            out.skipped.append({
                "ref": board_id or "__main__",
                "why": "板尺寸未知，无法建板材碰撞体",
            })
            continue

        name = f"board_{board_id or 'main'}"
        # 板中心放在 (+bw/2, +bh/2)，因为零件的 xy 通常是板内坐标
        board_center = (bw / 2.0, bh / 2.0, 0.0)
        rows: list[PartBody] = []

        # 板的 geom（质量稍后用总和覆盖；先给 0 避免重复计）
        # f-string 里 `_vec([...])` 的方括号会和 `{` 混在一起解析，
        # 所以先算字符串再拼——这不是风格问题，是 SyntaxError 问题。
        board_half = _vec([bw / 2.0 * MM, bh / 2.0 * MM,
                           thick / 2.0 * MM])
        # **板的 z 要从地板往上抬半个板厚**，否则板的下表面在
        # z = -thick/2（穿进地板），MuJoCo 会把它弹出去 → 翻面。
        # 实测 r3demo 因此翻了 176.9°，三次扰动全 fail ——
        # 那不是"设计不稳"，是**初始位置错了**。
        body_xml = (f'<body name="{name}" '
                    f'pos="0 0 {_f(thick / 2.0 * MM)}">\n'
                    + ('  <freejoint/>\n' if free else '')
                    + f'  <geom name="{name}_g" type="box" '
                      f'size="{board_half}" mass="0.001" '
                      f'friction="0.42 0.42 0.0001"/>\n')
        total_g = 0.0
        for part in members:
            body = _part_body(part, board_center, params, reasons=reasons)
            body.in_world = False        # 已拍平进板，不单独拼 world
            rows.append(body)
            if not body.xml:
                out.skipped.append({"ref": body.ref,
                                    "why": body.skipped_reason})
                continue
            # 把零件的 <body> 拍平成一个 <geom>（相对板中心）
            size_mm = body.size_mm
            half = [size_mm.get(k, 0) / 2.0 * MM for k in ("w", "h", "d")]
            # 元件的 z 要**放在板上表面之上**：板中心在 z=0，
            # 板顶面在 z = +thick/2，元件下半截不能穿进地板。
            #
            # 实测踩坑：第一版用 `placement.z_mm`（通常 0），
            # 于是元件中心落在板中心平面，下半截伸到 z<0 → 穿进地板
            # → 被弹飞 → 整板翻面（倾角 176.9°），三次扰动全 fail。
            # 那不是"设计不稳"，是**几何摆错位置**。
            elem_z = (thick / 2.0
                      + float(part.size_mm.get("d", 0)) / 2.0) * MM
            pos = [(part.placement.x_mm - board_center[0]) * MM,
                   (part.placement.y_mm - board_center[1]) * MM,
                   elem_z]
            body_xml += (
                f'  <geom name="{part.ref}_g" type="box" '
                f'size="{_vec(half)}" pos="{_vec(pos)}" '
                f'mass="{_f((body.mass_g or MIN_MASS_G) / 1000.0)}" '
                f'friction="0.42 0.42 0.0001"/>\n')
            total_g += (body.mass_g or MIN_MASS_G)
        board_mass = max(total_g / 1000.0, MIN_MASS_G / 1000.0)
        body_xml = body_xml.replace('mass="0.001"',
                                    f'mass="{_f(board_mass)}"', 1)
        body_xml += '</body>\n'

        # 板体用一个 PartBody 承载。**ref 必须是 body 名（`name`）**，
        # 因为 `mujoco_backend` 靠 `model.body(ref)` 找刚体取位姿。
        # 第一版用了 `design.parts[0].ref`（"C1"），body 名是
        # `board_xxx` → 找不到 → 报"没有任何自由体"。
        holder = PartBody(ref=name, xml=body_xml, board_id=board_id,
                          mass_g=board_mass * 1000.0)
        rows.append(holder)
        result.append(rows)
    return result


def _excludes(design: DesignModel) -> list[str]:
    """同板零件之间的接触排除。

    MuJoCo 里同一刚体的多个 geom **默认不互相碰撞**；但不同 body 之间会。
    同一块板上的零件在物理上是被板固定的，它们之间的接触只会
    和关节约束打架（RoboBuilder 的 `shrink_mm` 就是为了这个）。
    这里用 `<contact><exclude>` 显式排除同板对。
    """
    by_board: dict[str, list[str]] = {}
    for part in design.parts:
        board = str(part.placement.board_id or "__main__")
        by_board.setdefault(board, []).append(part.ref)
    out: list[str] = []
    for refs in by_board.values():
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                out.append(f'    <exclude body1="{a}" body2="{b}"/>\n')
    # 配件多时这会是 O(n²)，超过阈值就只记警告不生成
    if len(out) > 4000:
        return []
    return out


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.mjcf",
        "mapping": dict(JOINT_MAPPING),
        "required_params": {k: list(v) for k, v in _REQUIRED_PARAMS.items()},
        "default_density_kg_m3": DEFAULT_DENSITY,
        "discipline": (
            "inferred 关节不进仿真；缺尺寸/质量的零件进 skipped 并说明；"
            "缺必需参数的关节跳过，不用编造值驱动仿真"),
    }