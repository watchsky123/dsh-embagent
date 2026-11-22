"""关节**提案**（`joints-propose`）—— 先给一份能用的，人再改。

## 为什么需要这一层（而不是让 `joints` 自己宽松一点）

`joints` 对 `hinge`/`axle`/`slide`/`gear` 这些 kind 的态度是：**缺参数就报
error，不编默认值**。这条纪律不能改 —— 一个编出来的齿数比会让整个动力学
结果看起来"跑通了"，而它其实是假的（AGENTS §1.2）。

但另一件事也是真的：**一份没有机械关系的半成品，什么也验不了**。

所以分成两层：

| 层 | 谁写 | 能不能进物理模型 |
|---|---|---|
| `joints.proposed.json` | 本模块（确定性规则） | 不能（只是提案，等应用）|
| `design.joints_list` 里 `source="proposed"` | `--apply` 之后 | 能，但报告里会数出来 |
| `source="declared"` | 人 | 能 |
| `source="inferred"` | `joints.infer` | **不能**（`mjcf` 跳过）|

关键差别：`inferred` 是"拓扑看起来像"，**没有任何数值**；`proposed` 是
"按几何算出了一个具体数，但没人核对"。前者不能进仿真，后者可以，因为
每个数都带着 `evidence`（怎么算出来的）。

## 四条规则（都能复算）

1. **几何类**（`stack`/`port`/`contact`）：直接取 `joints.infer()` 的结果 ——
   它有 z 区间/重叠面积/网表名做依据
2. **`axle`**：轴类零件（servo/motor/shaft…）的**足迹中心**落在另一个零件的
   足迹里 → 轴径 = 轴类零件短边
3. **`slide`**：滑轨 + 滑块（足迹互相包含）→ 行程 = ±(轨长 − 滑块长)/2
4. **`hinge`**：执行器 + 同单元的另一件 → 行程取 `mechanical.limits` 里的
   `hinge_deg`（**没给就不提**，进 `unresolved` 说清要什么）

**推不出来的一律进 `unresolved` 并写 `need`**，不编数字。
"""

from __future__ import annotations

from typing import Any

from .model import JOINT_KINDS, DesignModel, Joint, Part

__all__ = ["propose", "apply", "describe", "SHAFT_KINDS", "RAIL_KINDS",
           "ACTUATOR_KINDS"]

#: 能当"轴"的零件（按 kind / role / label 里的关键词判，全部小写比对）。
SHAFT_KINDS = ("servo", "motor", "gear_motor", "stepper", "shaft", "axle",
               "bearing", "wheel", "hub", "horn")

#: 能当"滑轨"的零件。
RAIL_KINDS = ("rail", "slider", "carriage", "linear", "linear_rail",
              "linear_stage", "lead_screw")

#: 执行器（hinge 的一端）。
ACTUATOR_KINDS = ("servo", "motor", "gear_motor", "stepper", "solenoid")

#: 顶点到中心的容差（mm）—— 判"足迹中心落在这个零件里"。
CENTER_EPS_MM = 0.5


def _text(part: Part) -> str:
    return " ".join(str(x or "").lower()
                    for x in (part.kind, part.role, part.label)).strip()


def _is(part: Part, kinds: tuple[str, ...]) -> bool:
    text = _text(part)
    return any(kind in text for kind in kinds)


def _footprint(part: Part) -> tuple[float, float, float, float] | None:
    try:
        x0, y0, x1, y1 = part.footprint_mm()
    except Exception:  # noqa: BLE001 - 尺寸缺失时 footprint 会抛，如实跳过
        return None
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return x0, y0, x1, y1


def _center(part: Part) -> tuple[float, float] | None:
    box = _footprint(part)
    if box is None:
        return None
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _contains(box: tuple[float, float, float, float],
              point: tuple[float, float], eps: float = CENTER_EPS_MM) -> bool:
    return (box[0] - eps <= point[0] <= box[2] + eps
            and box[1] - eps <= point[1] <= box[3] + eps)


def _lim_deg(design: DesignModel) -> list[float] | None:
    """`mechanical.limits` 里的 hinge 行程（度）。**没给就 None，不猜。**"""
    limits = (design.mechanical or {}).get("limits")
    if not isinstance(limits, dict):
        return None
    for key in ("hinge_deg", "joint_range_deg", "hinge_range_deg"):
        value = limits.get(key)
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                return [float(value[0]), float(value[1])]
            except (TypeError, ValueError):
                return None
    return None


def _skip_existing(design: DesignModel) -> set[tuple[str, str, str]]:
    """已声明/已有的关系：(a, b, kind)。提案不能盖掉人的声明。"""
    out: set[tuple[str, str, str]] = set()
    for row in design.joints_list or []:
        if isinstance(row, dict):
            out.add((str(row.get("a")), str(row.get("b")), str(row.get("kind"))))
    return out


def _merge(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def propose(design: DesignModel) -> dict[str, Any]:
    """给一份设计算关节提案。**只出有依据的，推不出的进 `unresolved`。**"""
    from . import joints as joints_module

    proposals: list[Joint] = []
    unresolved: list[dict[str, Any]] = []
    have = _skip_existing(design)
    taken: set[tuple[str, str, str]] = set()

    def add(joint: Joint) -> None:
        key = (*_merge(joint.a, joint.b), joint.kind)
        if key in have or key in taken:
            return
        taken.add(key)
        proposals.append(joint)

    # ---- 规则 1：几何类（stack/port/contact）------------------------------
    #
    # `joints.infer()` 给的是 `inferred`（不进物理模型）。这里只是把**同一批
    # 几何事实**重新包装成 `proposed`：数值没变，变的是"可以被应用"。
    inference = joints_module.infer(design)
    for joint in inference.joints:
        add(Joint(a=joint.a, b=joint.b, kind=joint.kind, source="proposed",
                  params=dict(joint.params or {}),
                  evidence=(joint.evidence
                            or f"几何推断（{joint.kind}）：见 joints.infer")))

    # ---- 规则 2：axle ----------------------------------------------------
    shafts = [p for p in design.parts if _is(p, SHAFT_KINDS)]
    for shaft in shafts:
        box = _footprint(shaft)
        center = _center(shaft)
        if box is None or center is None:
            continue
        diameter = round(min(box[2] - box[0], box[3] - box[1]), 3)
        if diameter <= 0:
            continue
        for other in design.parts:
            if other.ref == shaft.ref:
                continue
            other_box = _footprint(other)
            if other_box is None or not _contains(other_box, center):
                continue
            add(Joint(a=shaft.ref, b=other.ref, kind="axle", source="proposed",
                      params={"diameter_mm": diameter},
                      evidence=(f"轴：{shaft.ref} 足迹中心 "
                                f"({center[0]:.1f},{center[1]:.1f}) 落在 "
                                f"{other.ref} 足迹内；轴径取短边 {diameter} mm")))

    # ---- 规则 3：slide（滑轨 + 滑块）--------------------------------------
    rails = [p for p in design.parts if _is(p, RAIL_KINDS)]
    for rail in rails:
        rbox = _footprint(rail)
        if rbox is None:
            continue
        r_long = max(rbox[2] - rbox[0], rbox[3] - rbox[1])
        for other in design.parts:
            if other.ref == rail.ref:
                continue
            obox = _footprint(other)
            if obox is None:
                continue
            if not (_contains(rbox, _center(other) or (1e9, 1e9))
                    and _contains(obox, _center(rail) or (1e9, 1e9))):
                continue
            o_long = max(obox[2] - obox[0], obox[3] - obox[1])
            stroke = r_long - o_long
            if stroke <= 0:
                continue                     # 滑块比轨长：推不出行程，不编
            half = round(stroke / 2.0, 3)
            add(Joint(a=rail.ref, b=other.ref, kind="slide", source="proposed",
                      params={"range_mm": [-half, half]},
                      evidence=(f"滑轨：{rail.ref} 长 {round(r_long, 1)} mm、"
                                f"{other.ref} 长 {round(o_long, 1)} mm → "
                                f"行程 ±{half} mm")))

    # ---- 规则 4：hinge（执行器 + 同单元零件）-----------------------------
    limit = _lim_deg(design)
    actuators = [p for p in design.parts if _is(p, ACTUATOR_KINDS)]
    for act in actuators:
        partners = [p for p in design.parts
                    if p.ref != act.ref and p.unit and p.unit == act.unit]
        if not partners:
            partners = [p for p in design.parts
                        if p.ref != act.ref and not _is(p, ACTUATOR_KINDS)
                        and (p.unit or "") == (act.unit or "")
                        and str(p.role or "") in ("link", "arm", "bracket",
                                                  "horn", "leg", "flap")]
        if not partners:
            unresolved.append({
                "need": "hinge_partner",
                "why": (f"{act.ref} 是执行器，但没有找到同单元的从动件"
                        "（unit 或 role=link/arm 标记一下就能推）"),
                "refs": [act.ref],
            })
            continue
        if limit is None:
            unresolved.append({
                "need": "hinge_deg",
                "why": (f"{act.ref} 与 {partners[0].ref} 之间可以建 hinge，"
                        "但行程没给 —— 在 `mechanical.limits.hinge_deg` 写 "
                        "[lo, hi]（度）就会自动提案；**不替你编行程**"),
                "refs": [act.ref, partners[0].ref],
            })
            continue
        for partner in partners:
            add(Joint(a=act.ref, b=partner.ref, kind="hinge", source="proposed",
                      params={"range_deg": list(limit)},
                      evidence=(f"执行器：{act.ref}（{act.kind}）→ "
                                f"{partner.ref}；行程取自 "
                                f"mechanical.limits.hinge_deg={list(limit)}")))

    # ---- 规则 5：gear（齿数不在模型里 → 一律推不出）---------------------
    for act in actuators:
        mates = [p for p in design.parts
                 if p.ref != act.ref and ("gear" in _text(p))]
        for mate in mates:
            unresolved.append({
                "need": "gear_ratio",
                "why": (f"{act.ref} 与 {mate.ref} 看起来是一对齿轮，但齿数/"
                        "模数不在 design.json 里 —— 填 `params.ratio`（从动/主动）"
                        "才会提案"),
                "refs": [act.ref, mate.ref],
            })

    counts: dict[str, int] = {}
    for joint in proposals:
        counts[joint.kind] = counts.get(joint.kind, 0) + 1
    for row in inference.skipped:
        unresolved.append({"need": "geometry", "why": str(row.get("why") or ""),
                           "refs": [str(row.get("ref") or "")]})

    return {
        "status": "ok" if proposals else "not_ready",
        "proposals": [j.to_dict() for j in proposals],
        "unresolved": unresolved,
        "counts": counts,
        "inference": {"joints": len(inference.joints),
                      "skipped": len(inference.skipped)},
        "note": (
            (f"{len(proposals)} 条提案（{'、'.join(f'{k} {v}' for k, v in sorted(counts.items()))}）"
             if proposals else "没有可提案的关系")
            + (f"；{len(unresolved)} 条推不出（见 unresolved，每条写了要补什么）"
               if unresolved else "")
            + "。**全是 proposed：能进物理模型，但没人核对过。**"),
    }


def apply(design: DesignModel, proposals: list[dict[str, Any]]) -> list[str]:
    """把提案写进 `design.joints_list`（`source` 保持 `proposed`）。

    返回新写入的 `a-b/kind` 列表。**不覆盖已有条目**（人写的不动）。
    """
    have = _skip_existing(design)
    added: list[str] = []
    for row in proposals:
        if not isinstance(row, dict):
            continue
        a, b, kind = str(row.get("a")), str(row.get("b")), str(row.get("kind"))
        if (a, b, kind) in have:
            continue
        design.joints_list.append(dict(row))
        have.add((a, b, kind))
        added.append(f"{a}-{b}/{kind}")
    return added


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.joint_propose",
        "kinds": [k for k in JOINT_KINDS],
        "rules": {
            "stack/port/contact": "取 joints.infer() 的几何事实（有 z 间隙/重叠/网名依据）",
            "axle": "轴类零件的足迹中心落在另一件里 → 轴径=短边",
            "slide": "滑轨+滑块互相包含 → 行程=±(轨长−滑块长)/2",
            "hinge": "执行器 + 同单元从动件，行程取 mechanical.limits.hinge_deg",
            "gear": "齿数不在模型里 → 一律 unresolved（不编 ratio）",
        },
        "discipline": ("推不出的进 unresolved 并写 need；"
                       "proposed 能进物理模型但会在报告里被数出来"),
    }