"""机械判据（`mech`）—— 干涉 / 重心 / 舵机扭矩 / 关节行程。

## 为什么这四条

电气有 `ladder`，PCB 有 DRC + 自建几何，固件有 `fw-matrix`，**机械此前一条
判据都没有**：`joints` 只查"参数给没给"，`mjcf` 只查"MuJoCo 能不能编译"。
也就是说"两块板子插在一起了""重心在支撑外""9g 舵机推 300g 摆臂"
这三类必炸的问题，之前**全靠人看图**。

## 三种结论，措辞必须不同（AGENTS §0.1 / §0.2）

| 结论 | 含义 | 进交接单哪一类 |
|---|---|---|
| `pass` | 算了，且在范围内 | 不报 |
| `fail` | 算了，超了 | `blocking`（打板必炸）|
| `unknown` | **没法算**（缺质量/缺扭矩/没支撑）| `missing_data`，附"要补什么" |

`unknown` **不是** `pass`。所以每条 check 都同时返回 `checked`（查了几对/几个）
与 `basis`（数字从哪来：实测质量、估算质量、limits 里的行程…）。
"""

from __future__ import annotations

from typing import Any

from .model import DesignModel, Joint, Part

__all__ = ["report", "describe", "DEFAULT_DENSITY_G_MM3"]

#: 估算质量用的密度（g/mm³）= 1050 kg/m³，与 `mjcf.DEFAULT_DENSITY` 同源。
DEFAULT_DENSITY_G_MM3 = 1.05e-3

#: 判"两个包围盒相交"的容差（mm）。0.1 mm 以内算贴合，不算干涉。
TOUCH_EPS_MM = 0.1

#: 舵机扭矩安全系数。舵机标称是**堵转**扭矩，实际可用约为 1/3 ~ 1/2；
#: 这里取 0.5（工程惯例），并把它写成常量而不是藏在公式里。
TORQUE_MARGIN = 0.5

#: 这些 joint 的接触是**有意的**（铰链的轴、叠放的板），不算干涉。
_INTENTIONAL_CONTACT = frozenset({
    "hinge", "slide", "axle", "gear", "chain", "tread", "rack", "pin",
    "stud", "stack", "port", "contact",
})


def _mass_g(part: Part) -> tuple[float | None, str]:
    """零件质量。**None = 算不出来**（尺寸全 0），不是 0 g。"""
    if part.mass_g is not None:
        return float(part.mass_g), "declared"
    size = part.size_mm or {}
    volume = float(size.get("w", 0)) * float(size.get("h", 0)) * float(size.get("d", 0))
    if volume <= 0:
        return None, "unknown"
    return volume * DEFAULT_DENSITY_G_MM3, "estimated"


def _bbox(part: Part) -> tuple[float, float, float, float, float, float] | None:
    try:
        return part.bbox_mm()
    except Exception:  # noqa: BLE001 - 尺寸缺失时 bbox 会抛
        return None


def _overlap(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
    dx = min(a[3], b[3]) - max(a[0], b[0])
    dy = min(a[4], b[4]) - max(a[1], b[1])
    dz = min(a[5], b[5]) - max(a[2], b[2])
    return dx, dy, dz


def _row(check: str, status: str, *, refs: list[str] | None = None,
         message: str = "", basis: str = "", need: str = "",
         **extra: Any) -> dict[str, Any]:
    return {"check": check, "status": status, "refs": list(refs or []),
            "message": message, "basis": basis,
            "fix": ({"need": need, "refs": list(refs or [])} if need else {}),
            **extra}


def _interference(design: DesignModel) -> list[dict[str, Any]]:
    """两两 3D 包围盒相交。**有意接触的（有 joint）不算。**"""
    rows: list[dict[str, Any]] = []
    pairs: set[tuple[str, str]] = set()
    for row in design.joints_list or []:
        if not isinstance(row, dict):
            continue
        try:
            joint = Joint.from_dict(row)
        except Exception:  # noqa: BLE001
            continue
        if joint.kind in _INTENTIONAL_CONTACT:
            pairs.add(tuple(sorted((joint.a, joint.b))))
    parts = [p for p in design.parts if _bbox(p) is not None]
    checked = 0
    for index, left in enumerate(parts):
        for right in parts[index + 1:]:
            if tuple(sorted((left.ref, right.ref))) in pairs:
                continue
            checked += 1
            dx, dy, dz = _overlap(_bbox(left), _bbox(right))  # type: ignore[arg-type]
            if dx > TOUCH_EPS_MM and dy > TOUCH_EPS_MM and dz > TOUCH_EPS_MM:
                rows.append(_row(
                    "interference", "fail", refs=[left.ref, right.ref],
                    message=(f"{left.ref} 与 {right.ref} 实体重叠 "
                             f"{round(dx, 2)}×{round(dy, 2)}×{round(dz, 2)} mm"),
                    basis=f"bbox（{len(parts)} 个有尺寸的零件，{checked} 对）",
                    need="fix_interference"))
    if not rows:
        rows.append(_row("interference", "pass",
                         message=f"{checked} 对零件无实体重叠",
                         basis="bbox + 有意接触的白名单（有 joint 的对跳过）"))
    return rows


def _center_of_gravity(design: DesignModel) -> list[dict[str, Any]]:
    """重心落在**支撑多边形**内吗。

    支撑 = z 最低那层零件的足迹包围盒。这条判据的诚实之处：**没声明哪个
    零件着地，就没有支撑面**，那就是 `unknown`，不是"通过"。
    """
    masses: list[tuple[Part, float, str]] = []
    for part in design.parts:
        mass, how = _mass_g(part)
        if mass is None or mass <= 0:
            continue
        masses.append((part, mass, how))
    if not masses:
        return [_row("center_of_gravity", "unknown",
                     message="一个零件的质量都算不出来（尺寸全 0）",
                     basis="mass_g 与 size_mm 都缺", need="provide_mass")]

    total = sum(m for _, m, _ in masses)
    cx = sum(m * (p.placement.x_mm + p.size_mm["w"] / 2) for p, m, _ in masses) / total
    cy = sum(m * (p.placement.y_mm + p.size_mm["h"] / 2) for p, m, _ in masses) / total
    how = ("declared" if all(h == "declared" for _, _, h in masses) else "estimated")

    # 支撑层 = z 最小（板面 z=0 那层）
    zmin = min(p.placement.z_mm for p, _, _ in masses)
    support = [(p, _bbox(p)) for p, _, _ in masses
               if abs(p.placement.z_mm - zmin) <= 0.01 and _bbox(p) is not None]
    boxes = [(b[0], b[1], b[3], b[4]) for _, b in support if b is not None]
    if not boxes:
        return [_row("center_of_gravity", "unknown", refs=[p.ref for p, _, _ in masses[:6]],
                     message="着地层没有可用足迹，支撑面判不出",
                     basis=f"zmin={zmin} 的零件都没有尺寸", need="give_support")]
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    message = (f"重心 ({round(cx, 1)}, {round(cy, 1)}) mm，支撑盒 "
               f"[{round(x0, 1)}..{round(x1, 1)}]×[{round(y0, 1)}..{round(y1, 1)}]")
    basis = (f"{len(masses)} 个零件质量（{how}）；支撑取 z={zmin} 层包围盒")
    if x0 <= cx <= x1 and y0 <= cy <= y1:
        return [_row("center_of_gravity", "pass", message=message, basis=basis)]
    return [_row("center_of_gravity", "fail",
                 refs=[p.ref for p, _, _ in masses[:6]],
                 message="重心落在支撑面之外（会倾覆）：" + message,
                 basis=basis, need="move_cog")]


def _servo_torque(design: DesignModel) -> list[dict[str, Any]]:
    """铰链要的扭矩 vs 舵机标称（按 0.5 安全系数折）。

    需要两样东西才能算：**从动件质量**、**力臂**。舵机自己有没有
    `electrical.stall_torque_kgcm` 是第三样。缺任何一样 → `unknown`。
    """
    rows: list[dict[str, Any]] = []
    hinges: list[Joint] = []
    for row in design.joints_list or []:
        if not isinstance(row, dict):
            continue
        try:
            joint = Joint.from_dict(row)
        except Exception:  # noqa: BLE001
            continue
        if joint.kind == "hinge":
            hinges.append(joint)
    if not hinges:
        return [_row("servo_torque", "unknown",
                     message="没有铰链关节，扭矩无从计算",
                     basis="joints_list 里没有 hinge")]
    by_ref = {p.ref: p for p in design.parts}
    for joint in hinges:
        actuator = by_ref.get(joint.a) or by_ref.get(joint.b)
        load = by_ref.get(joint.b) or by_ref.get(joint.a)
        if actuator is None or load is None:
            rows.append(_row("servo_torque", "unknown", refs=[joint.a, joint.b],
                             message="关节两端不在设计里", basis="joints_list"))
            continue
        mass, how = _mass_g(load)
        if mass is None:
            rows.append(_row("servo_torque", "unknown", refs=[load.ref],
                             message=f"{load.ref} 质量算不出来",
                             basis="size_mm 全 0 且没有 mass_g", need="provide_mass"))
            continue
        ax = actuator.placement.x_mm + actuator.size_mm["w"] / 2
        ay = actuator.placement.y_mm + actuator.size_mm["h"] / 2
        lx = load.placement.x_mm + load.size_mm["w"] / 2
        ly = load.placement.y_mm + load.size_mm["h"] / 2
        lever_cm = ((lx - ax) ** 2 + (ly - ay) ** 2) ** 0.5 / 10.0
        required_kgcm = (mass / 1000.0) * lever_cm
        declared = None
        for key in ("stall_torque_kgcm", "torque_kgcm", "stall_torque"):
            value = (actuator.electrical or {}).get(key)
            if isinstance(value, (int, float)) and float(value) > 0:
                declared = float(value)
                break
        basis = (f"质量 {round(mass, 2)} g（{how}）× 力臂 {round(lever_cm, 2)} cm；"
                 f"安全系数 {TORQUE_MARGIN}")
        if declared is None:
            rows.append(_row("servo_torque", "unknown", refs=[actuator.ref],
                             message=(f"{actuator.ref} 没有 `electrical.stall_torque_kgcm`，"
                                      f"要的扭矩是 {round(required_kgcm, 2)} kg·cm —— 没标称值就没法比"),
                             basis=basis, need="provide_torque",
                             required_kgcm=round(required_kgcm, 3)))
            continue
        usable = declared * TORQUE_MARGIN
        if required_kgcm <= usable:
            rows.append(_row("servo_torque", "pass", refs=[actuator.ref],
                             message=(f"要 {round(required_kgcm, 2)} kg·cm，"
                                      f"可用 {round(usable, 2)}（标称 {declared}×{TORQUE_MARGIN}）"),
                             basis=basis))
        else:
            rows.append(_row("servo_torque", "fail", refs=[actuator.ref, load.ref],
                             message=(f"扭矩不够：要 {round(required_kgcm, 2)} kg·cm，"
                                      f"可用只有 {round(usable, 2)}"),
                             basis=basis, need="provide_torque",
                             required_kgcm=round(required_kgcm, 3)))
    return rows


def _joint_range(design: DesignModel) -> list[dict[str, Any]]:
    """关节行程够不够。需求从 `mechanical.limits.required_deg` 来；没给就 unknown。"""
    rows: list[dict[str, Any]] = []
    limits = (design.mechanical or {}).get("limits") or {}
    required = limits.get("required_deg") if isinstance(limits, dict) else None
    joints: list[Joint] = []
    for row in design.joints_list or []:
        if not isinstance(row, dict):
            continue
        try:
            joint = Joint.from_dict(row)
        except Exception:  # noqa: BLE001
            continue
        if joint.kind in ("hinge", "slide"):
            joints.append(joint)
    if not joints:
        return [_row("joint_range", "unknown", message="没有铰链/滑轨关节",
                     basis="joints_list")]
    if not (isinstance(required, (int, float)) or
            (isinstance(required, (list, tuple)) and len(required) == 2)):
        return [_row("joint_range", "unknown", refs=[j.a for j in joints[:6]],
                     message=("需求行程没给（`mechanical.limits.required_deg`）"
                              "—— 不知道要转多大角度，就没法说够不够"),
                     basis="limits 里没有 required_deg", need="give_joint_travel")]
    need_span = (abs(float(required[1]) - float(required[0]))
                 if isinstance(required, (list, tuple)) else abs(float(required)))
    for joint in joints:
        params = joint.params or {}
        span = None
        if joint.kind == "hinge":
            value = params.get("range_deg")
            if isinstance(value, (list, tuple)) and len(value) == 2:
                span = abs(float(value[1]) - float(value[0]))
        else:
            value = params.get("range_mm")
            if isinstance(value, (list, tuple)) and len(value) == 2:
                span = abs(float(value[1]) - float(value[0]))
        if span is None:
            rows.append(_row("joint_range", "unknown", refs=[joint.a, joint.b],
                             message=f"{joint.kind} 的行程参数形状不对（要么缺，要么不是 [lo, hi]）",
                             basis="joints_list.params", need="give_joint_travel"))
            continue
        basis = f"关节 {joint.a}-{joint.b} 行程 {round(span, 1)}，需求 {round(need_span, 1)}"
        if span >= need_span - 1e-6:
            rows.append(_row("joint_range", "pass", refs=[joint.a, joint.b],
                             message=f"够（{round(span, 1)} ≥ {round(need_span, 1)}）",
                             basis=basis))
        else:
            rows.append(_row("joint_range", "fail", refs=[joint.a, joint.b],
                             message=f"行程不够：{round(span, 1)} < 需求 {round(need_span, 1)}",
                             basis=basis, need="give_joint_travel"))
    return rows


def report(design: DesignModel) -> dict[str, Any]:
    """跑四条机械判据。**只算，不下"整体可交付"的结论**（那是 handoff 的事）。"""
    checks: list[dict[str, Any]] = []
    for builder in (_interference, _center_of_gravity, _servo_torque,
                    _joint_range):
        try:
            checks.extend(builder(design))
        except Exception as exc:  # noqa: BLE001 - 一条判据炸不该拖垮别三条
            checks.append(_row(str(getattr(builder, "__name__", "check")),
                               "unknown",
                               message=f"{type(exc).__name__}: {exc}",
                               basis="判据自身出错（不是设计的问题）"))
    failed = [c for c in checks if c["status"] == "fail"]
    unknown = [c for c in checks if c["status"] == "unknown"]
    passed = [c for c in checks if c["status"] == "pass"]
    return {
        "status": ("failed" if failed else ("not_ready" if unknown else "ok")),
        "checks": checks,
        "counts": {"total": len(checks), "pass": len(passed),
                   "fail": len(failed), "unknown": len(unknown)},
        "basis": {
            "interference": "3D bbox + 有意接触白名单",
            "center_of_gravity": "质量加权 xy 重心 vs 最低层足迹包围盒",
            "servo_torque": f"质量×力臂 vs 标称×{TORQUE_MARGIN}",
            "joint_range": "limits.required_deg vs 关节 params 行程",
        },
        "note": (f"{len(checks)} 条判据：{len(passed)} pass / {len(failed)} fail / "
                 f"{len(unknown)} unknown。**unknown 不是 pass**。" ),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.mech_check",
        "checks": ["interference", "center_of_gravity", "servo_torque",
                   "joint_range"],
        "tolerances": {"touch_eps_mm": TOUCH_EPS_MM,
                       "torque_margin": TORQUE_MARGIN,
                       "default_density_g_mm3": DEFAULT_DENSITY_G_MM3},
        "discipline": ("unknown = 缺质量/缺扭矩/没支撑，不等于 pass；"
                       "有意接触（有 joint 的对）不算干涉"),
    }