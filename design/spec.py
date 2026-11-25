"""规格层（A1）—— 需求 → **功能/子系统/接口契约**，在生成元件之前。

## 这一层补什么

原来的 `generate` 直接"需求 → 元件清单"，中间没有可评审的东西。真实项目
是：功能清单 → 子系统（块）→ 每块的**接口契约** → 再谈选型。没有这三层，
生成物只能是"功能示意板"，工程师接手也看不出为什么这样分。

| 产物 | 读者 | 内容 |
|---|---|---|
| `spec/vN.json` | 机器 | 块、契约、约束、覆盖率、unknown |
| `spec.md` | 人 | 同一份东西的可读版 |
| `spec/index.json` | 前端 | 版本列表（同 `plan` 的规矩）|

## 三条纪律

1. **块先行**：需求里的每个角色/接口都要落成一个块；落不了的进 `missing`，
   **不偷偷用通用块顶替**（顶替的块标 `source="proposed"`，可见）。
2. **契约要能验**：每条接口写 `type/voltage/direction/pins/bus`；缺的数写
   `null` 并进 `unknown`，**不填默认值**（默认值就是"机器替你决定了"）。
3. **同需求不建版**：`requirement_hash` 相同就复用同一版；需求变了才 `v(N+1)`。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from . import bus

__all__ = ["DIR", "BLOCKS", "build", "render", "describe", "load_latest"]

DIR = Path("spec")
INDEX = "index.json"

#: 角色 → 块模板。`budget` 是**上限**（电流/面积/成本），没有值就是 None。
BLOCKS: dict[str, dict[str, Any]] = {
    "mcu": {"family": "mcu", "provides": ["gpio", "uart", "spi", "i2c", "adc", "pwm"],
            "budget": {"current_ma": 150, "area_mm2": 49, "cost_cny": 12}},
    "radio": {"family": "radio", "provides": ["link"],
              "budget": {"current_ma": 120, "area_mm2": 100, "cost_cny": 20}},
    "sensor": {"family": "sensor", "provides": ["sense"],
               "budget": {"current_ma": 10, "area_mm2": 16, "cost_cny": 8}},
    "power_in": {"family": "power", "provides": ["5v", "3v3"],
                 "budget": {"current_ma": 1500, "area_mm2": 100, "cost_cny": 5}},
    "servo_power": {"family": "power", "provides": ["5v_servo"],
                    "budget": {"current_ma": 3000, "area_mm2": 64, "cost_cny": 6}},
    "camera": {"family": "sensor", "provides": ["video"],
               "budget": {"current_ma": 200, "area_mm2": 100, "cost_cny": 40}},
    "display": {"family": "ui", "provides": ["display"],
                "budget": {"current_ma": 80, "area_mm2": 400, "cost_cny": 35}},
    "gimbal_pan": {"family": "actuator", "provides": ["rotate_z"],
                   "budget": {"current_ma": 600, "area_mm2": 100, "cost_cny": 30}},
    "gimbal_tilt": {"family": "actuator", "provides": ["rotate_y"],
                    "budget": {"current_ma": 600, "area_mm2": 100, "cost_cny": 30}},
    "motor": {"family": "actuator", "provides": ["thrust"],
              "budget": {"current_ma": 20000, "area_mm2": 400, "cost_cny": 60}},
    "encoder": {"family": "sensor", "provides": ["position"],
                "budget": {"current_ma": 5, "area_mm2": 25, "cost_cny": 9}},
    "mount": {"family": "mechanical", "provides": ["fixture"],
              "budget": {"current_ma": 0, "area_mm2": 0, "cost_cny": 0}},
}

#: 需求里 `numbers` / `constraints` 的常用键 → 单位（只用于展示与核对，
#: **不做单位换算**——换算就是另一份事实源）。
_UNITS = {"power_w": "W", "mass_g": "g", "range_m": "m", "endurance_min": "min",
          "voltage_v": "V", "cost_cny": "CNY", "area_mm2": "mm²",
          "io_voltage_v": "V", "current_ma": "mA"}


def _norm_interface(item: Any) -> dict[str, Any]:
    """需求里的接口 → 契约草稿。字符串就是协议名。"""
    if isinstance(item, str):
        return {"protocol": item.lower(), "a": "", "b": ""}
    if isinstance(item, dict):
        return {"protocol": str(item.get("protocol") or item.get("type") or "").lower(),
                "a": str(item.get("a") or ""), "b": str(item.get("b") or "")}
    return {"protocol": "", "a": "", "b": ""}


def _hash(req: Any) -> str:
    data = dataclasses.asdict(req)
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()[:16]


def build(req: Any) -> dict[str, Any]:
    """需求 → 规格（块/契约/约束/覆盖率）。**不改设计，只产出规格**。"""
    roles = [str(r) for r in (getattr(req, "roles", None) or [])]
    interfaces = [_norm_interface(i) for i in (getattr(req, "interfaces", None) or [])]
    constraints = dict(getattr(req, "constraints", None) or {})
    numbers = dict(getattr(req, "numbers", None) or {})

    blocks: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for role in roles:
        tpl = BLOCKS.get(role)
        if tpl is None:
            missing.append({"what": f"角色 {role}", "need": "在 spec.BLOCKS 里加模板"})
            blocks.append({"id": f"blk-{role}", "role": role, "family": "unknown",
                           "provides": [], "interfaces": [], "budget": None,
                           "source": "proposed", "verified_by": [],
                           "note": "没有块模板 —— 只能当占位，不能拿去选型"})
            continue
        blocks.append({"id": f"blk-{role}", "role": role, "family": tpl["family"],
                       "provides": list(tpl["provides"]),
                       "interfaces": [], "budget": dict(tpl["budget"]),
                       "source": "catalog", "verified_by": []})

    contracts: list[dict[str, Any]] = []
    io_voltage = constraints.get("io_voltage_v") or constraints.get("io_voltage")
    for index, item in enumerate(interfaces):
        protocol = item["protocol"]
        cfg = bus.BUSES.get(protocol)
        if cfg is None:
            missing.append({"what": f"接口 {protocol or '?'}",
                            "need": "在 bus.BUSES 里加协议模板"})
            contracts.append({"id": f"if{index + 1}", "protocol": protocol,
                              "a": item["a"], "b": item["b"], "pins": [],
                              "voltage": None, "direction": "unknown",
                              "source": "proposed"})
            continue
        contact = {
            "id": f"if{index + 1}", "protocol": protocol,
            "a": item["a"], "b": item["b"],
            "pins": [role for _, role in cfg["roles"]],
            "bus": protocol, "class": cfg["class"], "rules": list(cfg["rules"]),
            "voltage": io_voltage if io_voltage else None,
            "direction": ("bidirectional" if protocol in ("i2c", "can", "usb")
                          else "duplex" if protocol == "uart" else "master_slave"),
            "source": "catalog",
            "verified_by": [],
        }
        contracts.append(contact)
    # 把接口挂到块的端口上（a/b 为空时按顺序挂前两块）
    with_iface = [b for b in blocks if b["provides"]]
    for index, contract in enumerate(contracts):
        if contract.get("source") != "catalog":
            continue
        targets = [contract.get("a"), contract.get("b")]
        for slot, name in enumerate(targets):
            if not name:
                if index + slot < len(with_iface):
                    targets[slot] = with_iface[index + slot]["role"]
                    contract["a" if slot == 0 else "b"] = targets[slot]

    known: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for key, value in {**constraints, **numbers}.items():
        unit = _UNITS.get(key, "")
        if value in (None, ""):
            unknown.append({"what": key, "need": f"{key} 的数值（单位 {unit or '?'}）"})
        else:
            known.append({"kind": key, "value": value, "unit": unit,
                          "source": "req"})
    for need in ("power_w", "mass_g"):
        if need not in {**constraints, **numbers}:
            unknown.append({"what": need,
                            "need": f"需求里没给 {need} —— 预算/质量无法核对"})

    mapped = [b for b in blocks if b["source"] == "catalog"]
    coverage = {"roles_total": len(roles), "roles_mapped": len(mapped),
                "interfaces_total": len(interfaces),
                "interfaces_mapped": len([c for c in contracts
                                          if c.get("source") == "catalog"]),
                "missing": missing}
    status = ("not_ready" if not roles and not interfaces
              else "ok" if len(mapped) == len(roles) and not missing else "partial")
    return {
        "id": str(getattr(req, "id", "") or ""),
        "title": str(getattr(req, "title", "") or ""),
        "requirement_hash": _hash(req),
        "blocks": blocks,
        "interfaces": contracts,
        "constraints": known,
        "unknown": unknown,
        "coverage": coverage,
        "status": status,
        "retrieved": date.today().isoformat(),
        "note": ("规格只写块/契约/约束；**不选型、不排脚、不生成网表**。"
                 "缺的数写 null 并进 unknown"),
    }


def render(spec: dict[str, Any]) -> str:
    """人读版：块 / 接口契约 / 约束 / 还不知道的。"""
    lines = [f"# 规格：{spec.get('title') or spec.get('id')}", ""]
    lines.append(f"> {spec.get('note', '')}")
    cov = spec.get("coverage") or {}
    lines += ["", f"块 {len(spec.get('blocks') or [])}，契约 "
             f"{len(spec.get('interfaces') or [])}；"
             f"角色覆盖 {cov.get('roles_mapped')}/{cov.get('roles_total')}，"
             f"接口覆盖 {cov.get('interfaces_mapped')}/{cov.get('interfaces_total')}", ""]
    lines += ["## 子系统（块）", ""]
    for block in spec.get("blocks") or []:
        budget = block.get("budget") or {}
        lines.append(f"* **{block['role']}**（{block['family']}，{block['source']}）"
                     f" 提供 {', '.join(block.get('provides') or []) or '—'}"
                     f"；预算 I≤{budget.get('current_ma')}mA"
                     f" / A≤{budget.get('area_mm2')}mm²"
                     f" / ¥{budget.get('cost_cny')}")
    lines += ["", "## 接口契约", ""]
    for contract in spec.get("interfaces") or []:
        lines.append(f"* **{contract['id']}** {contract.get('protocol')} "
                     f"{contract.get('a') or '?'} ↔ {contract.get('b') or '?'}："
                     f"脚 {', '.join(contract.get('pins') or []) or '—'}；"
                     f"电平 {contract.get('voltage') or 'unknown'}；"
                     f"{contract.get('direction')}"
                     + (f"；规则 {'；'.join(contract.get('rules') or [])}"
                        if contract.get("rules") else ""))
    lines += ["", "## 约束（需求给的数）", ""]
    for row in spec.get("constraints") or []:
        lines.append(f"* {row['kind']} = {row['value']} {row.get('unit') or ''}")
    lines += ["", "## 还不知道的（**unknown，不是通过**）", ""]
    for row in spec.get("unknown") or []:
        lines.append(f"* {row.get('what')} —— 需要：{row.get('need')}")
    for row in cov.get("missing") or []:
        lines.append(f"* 缺模板：{row.get('what')} —— {row.get('need')}")
    return "\n".join(lines) + "\n"


def load_latest(project_dir: Path) -> dict[str, Any] | None:
    """读最新一版（`index.json` 的 `latest`）。没有就 None。"""
    root = Path(project_dir) / DIR
    index = root / INDEX
    if not index.is_file():
        return None
    try:
        meta = json.loads(index.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    name = str(meta.get("latest") or "")
    path = root / f"{name}.json" if name else None
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.spec",
        "dir": str(DIR),
        "blocks": sorted(BLOCKS),
        "produces": ["spec/vN.json", "spec.md", "spec/index.json"],
        "versioning": "同 requirement_hash 复用；变了才 v(N+1)",
        "discipline": ("块先行；缺模板进 missing（占位块标 proposed）；"
                       "缺的数写 null 并进 unknown；规格不选型不排脚"),
    }