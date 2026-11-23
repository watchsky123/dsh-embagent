"""块库（A3）—— 一个块 = 网表片段 + 参数 + 验证记录。

## 这一层补什么

P60 的 `spec` 只说到"这里有块"，块里还是空的；`generate` 仍是"角色 → 一个型号"。
块库把常见电路做成**可实例化的数据**（不是硬编码的整板模板）：

| 之前 | 现在 |
|---|---|
| `synth` 7 个整板模板 | 6 个**可组合**的块：LDO / DCDC / MCU 最小系统 / USB / CAN / 去耦阵列 |
| 生成物没有"这块从哪来" | 每块带 `verified_by`；**空 = unverified，不算已验证** |
| 引脚靠猜 | 块里写死零件与脚（MCU 用 LQFP48 脚号，晶振/复位/BOOT 都是真实连接）|

## 数据格式（`design/blocks/*.json`）

```json
{"id","title","family","params":{"vin":5.0},
 "parts":[{"key","kind","label","ref_prefix","pins":["1","2"],
           "size_mm":{"w","h","d"},"role","lcsc?"}],
 "nets":[{"net","global","class","pins":[{"key","pin"}]}],
 "rules":["..."],"verified_by":[{"project","ladder","date"}]}
```

## 三条纪律

1. **实例化保留来源**：`Part.notes` 记 `block=<id> instance=<名> key=<键>`，
   `Part.unit` 记 `block:<实例>`；网表里带 `block` 与 `rules`。
2. **全局网合并、局部网加前缀**：`GND/3V3/VBUS/5V/VIN/VOUT/VBAT` 标 `global`，
   实例之间同名合并；`CANH`/`USB_DP` 这种加 `实例_` 前缀，避免假并联。
3. **`verified_by` 为空 ≠ 通过**：统计里单独记 `unverified_blocks`。
   现在 6 个块全是空——**它们来自常见参考电路，没在真板上验过**。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import DesignModel, Part, Placement

__all__ = ["LIB_DIR", "load_all", "get", "validate", "instantiate", "compose",
           "merge_into", "describe"]

LIB_DIR = Path(__file__).with_name("blocks")

#: 实例化时零件摆放的起点与间距（**布局是工程师的活**，这里只保证不重叠）。
_ORGIN_MM = 10.0
_STEP_MM = 6.0


def load_all() -> dict[str, dict[str, Any]]:
    """读 `blocks/*.json`。**坏文件不静默跳过**：解析失败会抛出来。"""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(LIB_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: 顶层不是对象")
        key = str(data.get("id") or path.stem)
        if key in out:
            raise ValueError(f"块 id 重复：{key}")
        out[key] = data
    return out


def get(block_id: str) -> dict[str, Any] | None:
    return load_all().get(str(block_id))


def validate(block: dict[str, Any]) -> list[str]:
    """块的结构检查。**返回问题列表**（空 = 通过），不判"能用"。"""
    problems: list[str] = []
    for key in ("id", "title", "family"):
        if not str(block.get(key) or "").strip():
            problems.append(f"缺 {key}")
    parts = block.get("parts") or []
    nets = block.get("nets") or []
    if not isinstance(parts, list) or not parts:
        problems.append("没有零件")
    if not isinstance(nets, list) or not nets:
        problems.append("没有网表")
    keys: set[str] = set()
    pin_of: dict[str, set[str]] = {}
    for index, spec in enumerate(parts):
        if not isinstance(spec, dict):
            problems.append(f"parts[{index}] 不是对象")
            continue
        key = str(spec.get("key") or "")
        if not key:
            problems.append(f"parts[{index}] 缺 key")
            continue
        if key in keys:
            problems.append(f"part key 重复：{key}")
        keys.add(key)
        pins = spec.get("pins")
        if not isinstance(pins, list) or not pins:
            problems.append(f"{key} 没有 pins")
        else:
            pin_of[key] = {str(p) for p in pins}
        size = spec.get("size_mm") or {}
        if not (float(size.get("w") or 0) > 0 and float(size.get("h") or 0) > 0):
            problems.append(f"{key} 尺寸缺失（w/h 必须 > 0）")
    for index, net in enumerate(nets):
        if not isinstance(net, dict):
            problems.append(f"nets[{index}] 不是对象")
            continue
        name = str(net.get("net") or "")
        if not name:
            problems.append(f"nets[{index}] 缺 net 名")
        pins = net.get("pins") or []
        # 单引脚网是**块的对外端口**（CAN_TX 要接到块外的 MCU）—— 这是合法的，
        # 但必须显式标 `"port": true`；没标的单脚网一律算错（防漏线）。
        if not pins:
            problems.append(f"{name}: 没有引脚")
        elif len(pins) < 2 and not net.get("port"):
            problems.append(f"{name}: 少于 2 个引脚（若是块端口请标 port=true）")
        for pin in pins:
            key = str((pin or {}).get("key") or "")
            port = str((pin or {}).get("pin") or "")
            if key not in keys:
                problems.append(f"{name}: 引用了不存在的零件 {key}")
                continue
            if port and pin_of.get(key) and port not in pin_of[key]:
                problems.append(f"{name}: {key} 没有引脚 {port}")
    if not isinstance(block.get("verified_by") or [], list):
        problems.append("verified_by 不是数组")
    return problems


def _alloc_ref(prefix: str, counters: dict[str, int], used: set[str]) -> str:
    index = counters.get(prefix, 0) + 1
    while f"{prefix}{index}" in used:
        index += 1
    counters[prefix] = index
    return f"{prefix}{index}"


def instantiate(block: dict[str, Any], instance: str, *,
                used_refs: set[str] | None = None,
                board_id: str = "",
                max_x_mm: float = 0.0) -> dict[str, Any]:
    """一个块 → 零件 + 网表。**零件/网都带来源**（可追溯）。"""
    problems = validate(block)
    if problems:
        raise ValueError(f"块 {block.get('id')} 不合法：{problems[:3]}")
    used = used_refs if used_refs is not None else set()
    counters: dict[str, int] = {}
    parts: list[Part] = []
    # **摆放要留在板内**：`design.validate()` 会拦"足迹超出板轮廓"。
    # 这里只做栅格排布（layout 是人的活），按板宽折行。
    per_row = max(1, int(((max_x_mm - 2 * _ORGIN_MM) if max_x_mm else 1e9) // _STEP_MM) + 1)
    key_ref: dict[str, str] = {}
    for index, spec in enumerate(block.get("parts") or []):
        prefix = str(spec.get("ref_prefix") or "U")
        ref = _alloc_ref(prefix, counters, used)
        used.add(ref)
        key_ref[str(spec["key"])] = ref
        size = spec.get("size_mm") or {}
        parts.append(Part(
            ref=ref,
            kind=str(spec.get("kind") or "component"),
            label=str(spec.get("label") or spec.get("kind") or ref),
            role=str(spec.get("role") or "component"),
            size_mm={"w": float(size.get("w") or 0), "h": float(size.get("h") or 0),
                     "d": float(size.get("d") or 0)},
            physical_source="catalog",
            lcsc=str(spec.get("lcsc") or "") or None,
            pins=[str(pin) for pin in spec.get("pins") or []],
            notes=[f"block={block.get('id')} instance={instance} key={spec['key']}"],
            unit=f"block:{instance}",
            placement=Placement(
                x_mm=_ORGIN_MM + (index % per_row) * _STEP_MM,
                y_mm=_ORGIN_MM + (index // per_row) * _STEP_MM,
                mount="board", board_id=board_id),
        ))
    nets: list[dict[str, Any]] = []
    for spec in block.get("nets") or []:
        name = str(spec.get("net") or "")
        if not spec.get("global"):
            name = f"{instance}_{name}"
        nets.append({
            "net": name,
            "class": str(spec.get("class") or "signal"),
            "rules": list(block.get("rules") or []),
            "block": f"{block.get('id')}#{instance}",
            "level": "block",
            "source": "catalog",
            "pins": [{"ref": key_ref[str(pin["key"])], "pin": str(pin.get("pin") or "")}
                     for pin in spec.get("pins") or []],
        })
    return {"block": block.get("id"), "instance": instance,
            "parts": parts, "nets": nets,
            "verified_by": list(block.get("verified_by") or [])}


def _merge_nets(nets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同名网合并（全局网跨块并成一条）。**不合并引脚以外的元数据**：保留第一条。"""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for net in nets:
        name = str(net.get("net") or "")
        if name not in merged:
            merged[name] = {**net, "pins": list(net.get("pins") or [])}
            order.append(name)
            continue
        seen = {(p.get("ref"), p.get("pin")) for p in merged[name]["pins"]}
        for pin in net.get("pins") or []:
            key = (pin.get("ref"), pin.get("pin"))
            if key not in seen:
                merged[name]["pins"].append(pin)
                seen.add(key)
    return [merged[name] for name in order]


def compose(plan: list[dict[str, str]], *, board_id: str = "",
            max_x_mm: float = 0.0) -> dict[str, Any]:
    """按计划组合多个块。`plan = [{"instance": "PWR1", "block": "power_ldo_3v3"}]`。"""
    library = load_all()
    used: set[str] = set()
    parts: list[Part] = []
    nets: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for item in plan:
        block_id = str(item.get("block") or "")
        instance = str(item.get("instance") or block_id)
        block = library.get(block_id)
        if block is None:
            missing.append({"instance": instance, "block": block_id,
                            "reason": "块库里没有这个 id"})
            continue
        got = instantiate(block, instance, used_refs=used, board_id=board_id,
                          max_x_mm=max_x_mm)
        parts.extend(got["parts"])
        nets.extend(got["nets"])
        blocks.append({"instance": instance, "block": block_id,
                       "parts": [p.ref for p in got["parts"]],
                       "verified": bool(got["verified_by"])})
    nets = _merge_nets(nets)
    verified = [row for row in blocks if row["verified"]]
    return {
        "status": "ok" if parts and nets and not missing else "not_ready",
        "blocks": blocks,
        "parts": parts,
        "nets": nets,
        "missing": missing,
        "counts": {"blocks": len(blocks), "parts": len(parts), "nets": len(nets),
                   "verified_blocks": len(verified),
                   "unverified_blocks": len(blocks) - len(verified)},
        "note": ("未验证的块单独计数；块来自常见参考电路，不等于在真板上验过"),
    }


def merge_into(design: DesignModel, parts: list[Part],
               nets: list[dict[str, Any]]) -> dict[str, Any]:
    """把组合结果并进一份设计。**ref 冲突就失败**（不覆盖已有元件）。"""
    existing = {p.ref for p in design.parts}
    clash = sorted({p.ref for p in parts} & existing)
    if clash:
        return {"ok": False, "reason": f"位号冲突：{clash[:6]}"}
    design.parts.extend(parts)
    by_name = {str(net.get("net")): net for net in design.nets}
    added = 0
    for net in nets:
        name = str(net.get("net") or "")
        if name in by_name:
            seen = {(p.get("ref"), p.get("pin")) for p in by_name[name]["pins"]}
            for pin in net.get("pins") or []:
                if (pin.get("ref"), pin.get("pin")) not in seen:
                    by_name[name]["pins"].append(pin)
        else:
            design.nets.append(dict(net))
            by_name[name] = design.nets[-1]
            added += 1
    return {"ok": True, "parts_added": len(parts), "nets_added": added,
            "nets_total": len(design.nets)}


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.blocks",
        "dir": str(LIB_DIR),
        "blocks": sorted(load_all()),
        "format": "id/title/family/params/parts(key,kind,ref_prefix,pins,size_mm)/nets(global)/rules/verified_by",
        "instantiate": "ref 自动分配（跳过已用）；全局网合并，局部网加实例前缀；单脚网 = 块端口（port=true）",
        "verified": "`verified_by` 为空 → unverified（不算通过）",
        "discipline": ("块不许静默跳过坏文件；ref 冲突即失败；"
                       "未验证块单独计数"),
    }