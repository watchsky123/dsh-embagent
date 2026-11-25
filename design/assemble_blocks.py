"""装配（A4）—— 需求 → spec → **块实例** → 设计。

## 这一层补什么

`generate` 是"角色 → 一个型号 + 每链路 1 脚"的枚举器；块库（A3）有真电路但
没接线到需求。`assemble` 把两头接起来：

```
req.json → spec.build()（块/契约/约束）→ 块计划 → blocks.compose → design
```

## 块的映射是**显式表**，不是猜

| 来自 spec | 映射 |
|---|---|
| `blocks[].role == "mcu"` | `mcu_support` |
| `blocks[].role == "power_in"` | `power_dcdc_5v` + `power_ldo_3v3` |
| `interfaces[].protocol == "usb"` | `usb_device` |
| `interfaces[].protocol == "can"` | `can_transceiver` |

映射不到的**不静默补**：进 `uncovered`（角色/接口 + 原因）。块库没有的电路
（传感器、射频、显示）就只能报出来，不能拿占位件冒充。

## 纪律

* 块之外的角色**不造假件**（以前 `generate` 至少还选一个型号；那属于枚举器，
  `assemble` 不重复）
* 装配后必须过 `DesignModel.validate()`；不过就 `failed`
* 报 `coverage`：块数 / 覆盖角色 / 未覆盖角色 / 未覆盖接口
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from . import blocks as blocks_module
from . import spec as spec_module
from .model import Board, DesignModel

__all__ = ["ROLE_BLOCKS", "INTERFACE_BLOCKS", "plan", "assemble", "describe"]

#: 角色 → 块实例。**一个角色可能对应多个块**（电源是 DCDC + LDO 两级）。
ROLE_BLOCKS: dict[str, list[tuple[str, str]]] = {
    "mcu": [("MCU1", "mcu_support")],
    "power_in": [("PWR_DCDC", "power_dcdc_5v"), ("PWR_LDO", "power_ldo_3v3")],
    "servo_power": [("PWR_LDO", "power_ldo_3v3")],
    # P66：6 个新角色 → 板侧接口块（模块/舵机/电调是外接件，板上是座子）
    "radio": [("RADIO1", "radio_nrf24")],
    "sensor": [("SENS1", "sensor_i2c")],
    "camera": [("CAM1", "camera_usb")],
    "gimbal_pan": [("PAN1", "servo_out")],
    "gimbal_tilt": [("TILT1", "servo_out")],
    "motor": [("MOTOR1", "motor_esc")],
}

#: 接口协议 → 块实例（需要独立器件的才映射；纯总线不占块）。
INTERFACE_BLOCKS: dict[str, list[tuple[str, str]]] = {
    "usb": [("USB1", "usb_device")],
    "can": [("CAN1", "can_transceiver")],
}


def plan(spec: dict[str, Any]) -> dict[str, Any]:
    """spec → 块计划 + 未覆盖清单。**同实例名只出现一次**。"""
    plan_rows: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(instance: str, block_id: str) -> None:
        if instance in seen:
            return
        seen.add(instance)
        plan_rows.append({"instance": instance, "block": block_id})

    covered_roles: list[str] = []
    uncovered_roles: list[dict[str, str]] = []
    for block in spec.get("blocks") or []:
        role = str(block.get("role") or "")
        mapped = ROLE_BLOCKS.get(role)
        if not mapped:
            uncovered_roles.append({
                "role": role, "reason": "块库没有这个角色的块",
                "need": "在 assemble.ROLE_BLOCKS 里加映射，并在 design/blocks/ 里加块"})
            continue
        for instance, block_id in mapped:
            add(instance, block_id)
        covered_roles.append(role)

    covered_ifaces: list[str] = []
    uncovered_ifaces: list[dict[str, str]] = []
    for contract in spec.get("interfaces") or []:
        protocol = str(contract.get("protocol") or "")
        mapped = INTERFACE_BLOCKS.get(protocol)
        if not mapped:
            uncovered_ifaces.append({
                "protocol": protocol,
                "reason": "块库没有这个协议的接口块（总线本身由块内/网表提供）"})
            continue
        for instance, block_id in mapped:
            add(instance, block_id)
        covered_ifaces.append(protocol)
    return {"plan": plan_rows, "covered_roles": covered_roles,
            "uncovered_roles": uncovered_roles,
            "covered_interfaces": covered_ifaces,
            "uncovered_interfaces": uncovered_ifaces}


def assemble(req: Any, *, board_size: tuple[float, float] = (60.0, 40.0),
             spec: dict[str, Any] | None = None) -> dict[str, Any]:
    """需求 → 设计。**不写盘**（调用方决定）。"""
    spec = spec or spec_module.build(req)
    planned = plan(spec)
    width, height = float(board_size[0]), float(board_size[1])
    board = Board(kind="custom", name="主板", id="PCB#main",
                  size_mm={"w": width, "h": height}, thickness_mm=1.6,
                  outline=[(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)])
    design = DesignModel(id=str(getattr(req, "id", "") or "assembled"),
                         title=str(getattr(req, "title", "") or ""),
                         board=board, boards=[board])
    if not planned["plan"]:
        return {"status": "not_ready", "design": design, "spec": spec,
                "plan": planned,
                "reason": "块计划为空（需求里的角色/接口都没有对应块）"}
    composed = blocks_module.compose(planned["plan"], board_id=board.id,
                                     max_x_mm=width, max_y_mm=height)
    if composed["status"] != "ok":
        return {"status": "not_ready", "design": design, "spec": spec,
                "plan": planned, **composed}
    merged = blocks_module.merge_into(design, composed["parts"], composed["nets"])
    if not merged.get("ok"):
        return {"status": "failed", "design": design, "spec": spec,
                "plan": planned, "reason": merged.get("reason")}
    # B3：块实例进 `blocks_list`（零件/网表仍平铺，flatten 是恒等）。
    design.blocks_list = [
        {"instance": row["instance"], "block": row["block"],
         "parts": list(row.get("parts") or []),
         "nets": list(row.get("nets") or [])}
        for row in composed["blocks"]
    ]
    # **板框不够大时按内容扩**：需求没给板尺寸，块布局只保证不重叠。
    # 不扩就等于"装配失败"，而失败原因只是板框太小（实测 26 件需要 60×40+）。
    spans = [part.footprint_mm() for part in design.parts]
    need_w = max((span[2] for span in spans), default=0.0) + 5.0
    need_h = max((span[3] for span in spans), default=0.0) + 5.0
    auto_sized = False
    if need_w > width + 0.01 or need_h > height + 0.01:
        width = max(width, need_w)
        height = max(height, need_h)
        board.size_mm = {"w": round(width, 2), "h": round(height, 2)}
        board.outline = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
        design.board = board
        design.boards = [board]
        auto_sized = True
    problems = design.validate()
    if problems:
        return {"status": "failed", "design": design, "spec": spec,
                "plan": planned, "reason": "装配后校验不过",
                "problems": problems[:8]}
    uncovered = planned["uncovered_roles"] + planned["uncovered_interfaces"]
    return {
        "status": "ok" if not uncovered else "partial",
        "design": design,
        "spec": spec,
        "plan": planned,
        "counts": {
            "parts": len(design.parts),
            "layout_fits": all(span[0] >= -0.01 and span[1] >= -0.01
                               and span[2] <= width + 0.01
                               and span[3] <= height + 0.01 for span in spans)
            if spans else True,
            "board_auto_sized": auto_sized,
            "board_mm": {"w": round(width, 2), "h": round(height, 2)},
            "nets": len(design.nets),
            "blocks": composed["counts"]["blocks"],
            "verified_blocks": composed["counts"]["verified_blocks"],
            "unverified_blocks": composed["counts"]["unverified_blocks"],
            "roles_covered": len(planned["covered_roles"]),
            "roles_uncovered": len(planned["uncovered_roles"]),
            "interfaces_covered": len(planned["covered_interfaces"]),
            "interfaces_uncovered": len(planned["uncovered_interfaces"]),
        },
        "uncovered": uncovered,
        "note": ("块之外的角色**没有造占位件**；未覆盖的进 uncovered，"
                 "要补块库或手工接线"
                 + ("；板框按内容自动扩到 "
                    f"{width:.0f}×{height:.0f} mm" if auto_sized else "")),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.assemble_blocks",
        "role_blocks": {role: [block for _, block in rows]
                        for role, rows in ROLE_BLOCKS.items()},
        "interface_blocks": {proto: [block for _, block in rows]
                             for proto, rows in INTERFACE_BLOCKS.items()},
        "flow": "req → spec.build → 块计划 → blocks.compose → design（过 validate）",
        "discipline": ("映射不到就进 uncovered，不造假件；"
                       "装配后必须过 validate；verified_blocks 单独报"),
    }