"""项目计划图（`plan`）—— 需求 → 能力 → 模块 → 3D 信息图 → 任务，**按版本累积**。

## 这一层解决什么问题

前面 30 多个模块回答的是"**这个设计对不对**"；没有一个回答
"**这个项目要做到哪、还差什么、下一步先做哪几件**"。

`plan` 把这件事变成**文件**：每个工程一个 `plan/vN.json`。

```
plan/v1.json   需求 A 进来 → 能力清单 + 模块 + 3D 图 + 任务树
plan/v2.json   需求 B 加进来 → 继承 v1 的全部状态，只写"变了什么"
plan/index.json  [{version, requirement_hash, created, counts}]
```

**每一版都是自包含快照**（不是增量补丁）：3D 图、任务树、能力状态都在里面，
所以"看某一版当时怎么想的"不需要回放历史。

## 三层数据（一一对应，不重复存）

| 层 | 是什么 | 证据来自哪 |
|---|---|---|
| `capabilities[]` | **能干什么** + 判据（可测量）| 能力模板 + design.json 的实物 + 需求数值 |
| `modules[]` | **谁来干** + 位置（mm）+ 尺寸 | design.json 的 `boards/parts`（`placement` 是真实坐标）|
| `graph` | 能力/模块/连线的**空间图**（3D 源头）| modules 的 pos + nets 的边 + joints 的机械边 |

## 纪律（三条，都是本仓库踩过的）

1. **判据先写**：每条能力的 `criterion` 必须有 `metric/op/value/unit`；
   写不出量化判据的能力**不进表**（进 `open`，说明"这条我还不会验"）。
2. **缺证据 = `unknown`**：能力状态只有 `pass` / `fail` / `unknown`；
   `unknown` 附带"缺哪份数据"，**不是通过**（AGENTS §0.1）。
3. **任务来自缺口**：一个能力 `unknown` 或 `fail` → 生成任务；
   任务必须带 `verify`（怎么算做完），没有 `verify` 的任务不许生成。

## 版本规则

* 同一份需求（`requirement_hash` 相同）**不新建版本**，只刷新状态
* 新需求 → `v(N+1)`，`parent` 指向上一个版本，并把旧的任务状态**按 id 继承**
* 版本号**只增不减**；删版本 = 删文件，不重排（否则历史会错位）
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .model import DesignModel

__all__ = ["PLAN_DIR", "INDEX", "derive", "ensure", "load_version", "list_versions",
           "graph2d", "describe", "CAPABILITY_TEMPLATES"]

PLAN_DIR = "plan"
INDEX = f"{PLAN_DIR}/index.json"

#: 能力模板。**判据是模板自带的**，不是按需求临时编的。
#:
#: `evidence` 说明"要判它，需要 design.json 里出现什么"：
#: 每项是 `(kind, matcher)`，`kind` 取 `part_lcsc` / `net` / `unit_role`。
CAPABILITY_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "H1", "class": "hardware", "title": "能飞（悬停）",
        "criterion": {"metric": "thrust_weight", "op": ">=", "value": 2.0,
                      "unit": "T/W",
                      "basis": "4 桨总推力 / 起飞重量（含电池）；2.0 是机动余量下限"},
        "depends_on": ["H8"],
        "needs": [("part_role", "motor"), ("part_role", "prop")],
        "missing_need": "机体大件（桨/电机/电池）的型号或实测推力",
    },
    {
        "id": "H2", "class": "hardware", "title": "姿态控制稳定",
        "criterion": {"metric": "rate_loop_hz", "op": ">=", "value": 1000.0,
                      "unit": "Hz",
                      "basis": "角速率环 ≥1 kHz 才能压住桨叶一阶振动"},
        "depends_on": ["H1"],
        "needs": [("unit_role", "mcu"), ("net", "SPI"), ("part_role", "sensor")],
        "missing_need": "IMU 型号与安装刚度（振动会毁掉 1 kHz 环）",
    },
    {
        "id": "H3", "class": "hardware", "title": "定高",
        "criterion": {"metric": "altitude_hold_m", "op": "<=", "value": 0.3,
                      "unit": "m", "basis": "±0.3 m 是 ToF+气压融合的常见水平"},
        "depends_on": ["H2"],
        "needs": [("part_role", "sensor")],
        "missing_need": "气压计是否存在（工作区只见 ToF/光流）",
    },
    {
        "id": "H4", "class": "hardware", "title": "室内水平定位",
        "criterion": {"metric": "hover_drift_m", "op": "<=", "value": 0.5,
                      "unit": "m/30s",
                      "basis": "≥30 lux、地面有纹理；这是光流的下限条件"},
        "depends_on": ["H3"],
        "needs": [("part_role", "sensor")],
        "missing_need": "光流模块（PMW3901）与标定数据",
    },
    {
        "id": "H5", "class": "hardware", "title": "GNSS 定位（室外）",
        "criterion": {"metric": "ttff_s", "op": "<=", "value": 45.0, "unit": "s",
                      "basis": "冷启动到 12 星定位；天线净空与地平面决定它"},
        "depends_on": ["H1"],
        "needs": [("part_lcsc", "C5119087")],
        "missing_need": "GNSS 模组与天线布局实测",
    },
    {
        "id": "H6", "class": "hardware", "title": "避障/急停",
        "criterion": {"metric": "stop_distance_m", "op": ">=", "value": 0.5,
                      "unit": "m", "basis": "2 m/s 前进、1.5 m 检出时的剩余距离"},
        "depends_on": ["H4", "H5"],
        "needs": [("part_role", "sensor")],
        "missing_need": "避障触发逻辑与实测停距",
    },
    {
        "id": "H7", "class": "hardware", "title": "遥控链路",
        "criterion": {"metric": "range_m", "op": ">=", "value": 1000.0, "unit": "m",
                      "basis": "视距、无遮挡；消费级 2.4G 的保守值"},
        "depends_on": [],
        "needs": [("net", "RF"), ("part_role", "radio")],
        "missing_need": "射频模组型号与天线",
    },
    {
        "id": "H8", "class": "hardware", "title": "电源与能量",
        "criterion": {"metric": "rail_5v_under_load_v", "op": ">=", "value": 4.6,
                      "unit": "V", "basis": "4 电调堵转时 5V 轨最低值"},
        "depends_on": [],
        "needs": [("net", "5V"), ("part_role", "power")],
        "missing_need": "电池规格（几 S / 容量 / C 数）与 DC-DC 实测",
    },
    {
        "id": "S1", "class": "software", "title": "风控（失效保护）",
        "criterion": {"metric": "failsafe_latency_s", "op": "<=", "value": 0.5,
                      "unit": "s",
                      "basis": "链路丢失 → 触发保护的最大延迟；必须含 1 次注入测试"},
        "depends_on": ["H2", "H8"],
        "needs": [],
        "missing_need": "失效判据（角度/下降率阈值）的来源与注入测试",
    },
    {
        "id": "S2", "class": "software", "title": "控制律（串级 PID）",
        "criterion": {"metric": "phase_margin_deg", "op": ">=", "value": 30.0,
                      "unit": "deg", "basis": "经典闭环裕度下限"},
        "depends_on": ["H2"],
        "needs": [],
        "missing_need": "增益来源（参数集是黑盒）",
    },
    {
        "id": "S3", "class": "software", "title": "传感器融合（EKF）",
        "criterion": {"metric": "ekf_converge_s", "op": "<=", "value": 5.0, "unit": "s",
                      "basis": "上电到协方差收敛"},
        "depends_on": ["S2"],
        "needs": [],
        "missing_need": "飞控固件是否启用 EKF 及其参数",
    },
    {
        "id": "S4", "class": "software", "title": "视觉算法（光流/避障）",
        "criterion": {"metric": "flow_error_pct", "op": "<=", "value": 5.0,
                      "unit": "%", "basis": "1 m 位移的估计误差"},
        "depends_on": ["H4"],
        "needs": [],
        "missing_need": "算法本体（工作区只有硬件，没有该模块的源码）",
    },
    {
        "id": "S5", "class": "software", "title": "任务/航线",
        "criterion": {"metric": "waypoint_error_m", "op": "<=", "value": 2.0,
                      "unit": "m", "basis": "航点通过半径"},
        "depends_on": ["S1", "S3"],
        "needs": [],
        "missing_need": "任务状态机与断点续飞策略",
    },
    {
        "id": "S6", "class": "software", "title": "遥测/地面站",
        "criterion": {"metric": "telemetry_hz", "op": ">=", "value": 20.0, "unit": "Hz",
                      "basis": "20 Hz 才看得出手感异常"},
        "depends_on": ["H7"],
        "needs": [("part_role", "radio")],
        "missing_need": "遥测协议与帧结构",
    },
)

#: 任务种类 → 该做的动作模板。**每种都要带 verify**。
_TASK_KINDS = {
    "measure": {"verb": "实测", "verify": "记录数值 + 条件（温度/负载/时长）"},
    "procure": {"verb": "采购/补数据", "verify": "料号 + 库存 + 封装核对"},
    "design": {"verb": "设计", "verify": "出图/出参数，并过对应判据"},
    "code": {"verb": "编码", "verify": "跑通并给出可复现的输入输出"},
    "test": {"verb": "测试", "verify": "注入故障后行为符合判据（不只看正常路径）"},
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 证据：设计里到底有没有这个能力需要的东西
# ---------------------------------------------------------------------------


def _evidence(design: DesignModel) -> dict[str, Any]:
    """把 design.json 压成"能查什么"的三张索引。**只统计，不判断**。"""
    lcsc = {str(getattr(p, "lcsc", "") or "").strip() for p in design.parts}
    roles = {str(getattr(p, "role", "") or "") for p in design.parts}
    unit_roles: set[str] = set()
    for row in (getattr(design, "units_list", None) or []):
        if isinstance(row, dict) and row.get("role"):
            unit_roles.add(str(row["role"]))
    nets = [str(e.get("net") or "") for e in design.nets]
    return {
        "lcsc": {x for x in lcsc if x},
        "part_roles": {x for x in roles if x},
        "unit_roles": unit_roles,
        "nets": nets,
        "parts": len(design.parts),
        "boards": len(design.board_list()),
    }


def _has(index: dict[str, Any], kind: str, key: str) -> bool:
    """`needs` 里的一项在不在。**认不出就返回 False**（宁可 unknown）。"""
    if kind == "part_lcsc":
        return key in index["lcsc"]
    if kind == "part_role":
        return key in index["part_roles"]
    if kind == "unit_role":
        return key in index["unit_roles"]
    if kind == "net":
        token = key.upper()
        return any(token in n.upper() for n in index["nets"])
    return False


# ---------------------------------------------------------------------------
# 模块与 3D 图
# ---------------------------------------------------------------------------


def _modules(design: DesignModel) -> list[dict[str, Any]]:
    """板 + 大件 → 模块。位置取**真实坐标**（mm），不是编的。

    分组规则（确定性）：每块板一个 `board` 模块；板上引脚数 ≥8 或
    `role in {module, connector}` 的件单独成 `part` 模块，其余只是坐标。
    """
    out: list[dict[str, Any]] = []
    boards = design.board_list()
    for board in boards:
        size = board.size_mm or {}
        out.append({
            "id": f"mod:board:{board.id or board.name or 'board'}",
            "kind": "board",
            "title": str(board.name or board.id or "board"),
            "pos": {"x_mm": round(float(size.get("w", 0)) / 2, 3),
                    "y_mm": round(float(size.get("h", 0)) / 2, 3),
                    "z_mm": 0.0, "frame": "board"},
            "size_mm": {"w": float(size.get("w", 0)), "h": float(size.get("h", 0)),
                        "d": float(getattr(board, "thickness_mm", 0) or 0)},
            "refs": [], "source": "design",
        })
    for part in design.parts:
        # **每一个元件都进图**：3D 图要能点到一个具体的电容/电阻。
        # `pins`/`role` 只用来分 kind，不用来筛掉。
        pins = len(part.pins or [])
        place = getattr(part, "placement", None)
        size = part.size_mm or {}
        role = str(getattr(part, "role", ""))
        kind = ("connector" if role == "connector" else
                "module" if pins >= 8 or role == "module" else "component")
        out.append({
            "id": f"mod:part:{part.ref}",
            "kind": kind,
            "title": f"{part.ref} {part.label or ''}".strip(),
            "pos": {"x_mm": round(float(getattr(place, "x_mm", 0.0) or 0.0), 3),
                    "y_mm": round(float(getattr(place, "y_mm", 0.0) or 0.0), 3),
                    "z_mm": round(float(getattr(place, "z_mm", 0.0) or 0.0), 3),
                    "frame": "board",
                    "board": str(getattr(place, "board_id", "") or "")},
            "size_mm": {"w": float(size.get("w", 0)), "h": float(size.get("h", 0)),
                        "d": float(size.get("d", 0))},
            "refs": [part.ref], "source": "design",
            "lcsc": str(getattr(part, "lcsc", "") or ""),
        })
    return out


#: 3D 图的层高（mm）。模块层是**真实坐标**；能力层/任务层是抬起来的
#: **示意层**——写在这里而不是画的时候临时加，图例才说得清哪层是实物。
_LAYER_Z = {"capability": 60.0, "task": -60.0}


def _graph(design: DesignModel, modules: list[dict[str, Any]],
           capabilities: list[dict[str, Any]] | None = None,
           tasks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """四层 3D 图：**任务 → 能力 → 模块 → 板**。

    * 模块层用 design.json 的**真实坐标**（mm）
    * 能力层 z=+60、任务层 z=-60：这两层是示意，不是实物位置（图例写明）
    * 每条边都有来源：`net` / `joint` / `placement` / `task` / `template`
    """
    by_ref: dict[str, str] = {}
    for module in modules:
        for ref in module.get("refs") or []:
            by_ref[ref] = module["id"]

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(a: str, b: str, kind: str, label: str, source: str) -> None:
        if not a or not b or a == b:
            return
        key = (a, b, kind + label)
        if key in seen:
            return
        seen.add(key)
        edges.append({"a": a, "b": b, "kind": kind, "labels": [label],
                      "source": source})

    # 1) 网表 → 模块之间的信号/电源边
    for entry in design.nets:
        net = str(entry.get("net") or "")
        refs = sorted({str(p.get("ref") or "") for p in (entry.get("pins") or [])
                       if isinstance(p, dict)})
        mods = sorted({by_ref[r] for r in refs if r in by_ref})
        upper = net.upper()
        kind = ("power" if upper.startswith(("GND", "3V", "5V", "VIN", "VCC",
                                             "VBUS", "VDD", "VBAT", "VDDA"))
                else "signal")
        for i, a in enumerate(mods):
            for b in mods[i + 1:]:
                add(a, b, kind, net, "net")

    # 2) 关节 → 机械边
    for row in (design.joints_list or []):
        if not isinstance(row, dict):
            continue
        add(by_ref.get(str(row.get("a") or "")) or "",
            by_ref.get(str(row.get("b") or "")) or "",
            "mechanical", str(row.get("kind") or ""), "joint")

    # 3) 元件 → 所在板（装配关系）
    board_ids = {m["id"] for m in modules if m["kind"] == "board"}
    for module in modules:
        board = str((module.get("pos") or {}).get("board") or "")
        if module["kind"] in ("board",) or not board:
            continue
        target = f"mod:board:{board}"
        if target in board_ids:
            add(target, module["id"], "mechanical", "mounted_on", "placement")

    nodes: list[dict[str, Any]] = [
        {"id": m["id"], "kind": m["kind"], "title": m["title"],
         "pos": dict(m["pos"]), "size_mm": dict(m["size_mm"]),
         "source": m["source"],
         "layer": ("board" if m["kind"] == "board" else
                   "connector" if m["kind"] == "connector" else
                   "module" if m["kind"] == "module" else "component")}
        for m in modules]
    module_pos = {m["id"]: m["pos"] for m in modules}
    board_pos = next((m["pos"] for m in modules if m["kind"] == "board"), None)
    anchor = (board_pos or {"x_mm": 0.0, "y_mm": 0.0})

    # 4) 能力层：z=+60。位置取"它能用到的模块"的质心，没模块就用板中心。
    cap_pos: dict[str, dict[str, Any]] = {}
    for index, cap in enumerate(capabilities or []):
        targets = _modules_matching(modules, by_ref, cap, edges)
        if targets:
            xs = [float(module_pos[t]["x_mm"]) for t in targets]
            ys = [float(module_pos[t]["y_mm"]) for t in targets]
            px, py = sum(xs) / len(xs), sum(ys) / len(ys)
        else:
            px, py = float(anchor.get("x_mm") or 0), float(anchor.get("y_mm") or 0)
        pos = {"x_mm": round(px, 3), "y_mm": round(py, 3),
               "z_mm": _LAYER_Z["capability"], "frame": "layer"}
        cap_pos[f"cap:{cap['id']}"] = pos
        nodes.append({"id": f"cap:{cap['id']}", "kind": "capability",
                      "title": cap["title"], "pos": pos,
                      "size_mm": {"w": 0.0, "h": 0.0, "d": 0.0},
                      "source": "template", "layer": "capability",
                      "status": cap.get("status")})
        for target in targets:
            add(f"cap:{cap['id']}", target, "needs", "evidence", "template")

    # 5) 任务层：z=-60，挂在其能力节点正下方
    for task in tasks or []:
        base = cap_pos.get(f"cap:{task.get('capability')}")
        if base is None:
            continue
        pos = {"x_mm": base["x_mm"], "y_mm": base["y_mm"],
               "z_mm": _LAYER_Z["task"], "frame": "layer"}
        nodes.append({"id": f"task:{task['id']}", "kind": "task",
                      "title": task["title"], "pos": pos,
                      "size_mm": {"w": 0.0, "h": 0.0, "d": 0.0},
                      "source": "derived", "layer": "task",
                      "task_kind": task.get("kind")})
        add(f"task:{task['id']}", f"cap:{task['capability']}",
            "derived_from", str(task.get("kind") or ""), "task")

    return {
        "nodes": nodes,
        "edges": edges,
        "frame": ("模块层：板坐标 mm（原点在板框左下角，真实坐标）；"
                  "能力层 z=+60 / 任务层 z=-60 是示意层，不是实物位置"),
        "counts": {"nodes": len(nodes), "edges": len(edges),
                   "modules": len(modules),
                   "capabilities": len(capabilities or []),
                   "tasks": len(tasks or [])},
    }


def _modules_matching(modules: list[dict[str, Any]], by_ref: dict[str, str],
                      cap: dict[str, Any],
                      net_edges: list[dict[str, Any]] | None = None) -> list[str]:
    """一个能力"能用上"的模块 id（按模板的 `needs` 匹配实物）。**匹配不到就空**。"""
    template = next((t for t in CAPABILITY_TEMPLATES if t["id"] == cap.get("id")), None)
    if template is None:
        return []
    out: set[str] = set()
    for kind, key in template.get("needs") or []:
        for module in modules:
            if kind == "part_lcsc" and (module.get("lcsc") or "") == key:
                out.add(module["id"])
            elif kind == "part_role" and module.get("kind") == key:
                out.add(module["id"])
    for kind, key in template.get("needs") or []:
        if kind != "net":
            continue
        token = key.upper()
        for edge in net_edges or []:
            if edge.get("source") != "net":
                continue
            if any(token in str(label).upper() for label in edge.get("labels") or []):
                out.add(edge["a"])
                out.add(edge["b"])
    return sorted(out)


def graph2d(payload: dict[str, Any]) -> dict[str, Any]:
    """3D 图的 2D 投影（给画布/检查用）。**不改变数据结构**，只算屏幕坐标。"""
    nodes = (payload.get("graph") or {}).get("nodes") or []
    if not nodes:
        return {"status": "not_ready", "note": "图里没有节点", "nodes": []}
    xs = [float((n.get("pos") or {}).get("x_mm", 0)) for n in nodes]
    ys = [float((n.get("pos") or {}).get("y_mm", 0)) for n in nodes]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    span_x = max(x1 - x0, 1.0)
    span_y = max(y1 - y0, 1.0)
    return {
        "status": "ok",
        "extent_mm": {"x": [x0, x1], "y": [y0, y1]},
        "nodes": [{"id": n["id"], "x": round((float((n.get("pos") or {})
                                                 .get("x_mm", 0)) - x0) / span_x, 4),
                   "y": round((float((n.get("pos") or {})
                                     .get("y_mm", 0)) - y0) / span_y, 4),
                   "label": n.get("title"), "kind": n.get("kind")} for n in nodes],
        "note": "归一化到 0..1；**不是布局优化**，只是把真实坐标映射到画布",
    }


# ---------------------------------------------------------------------------
# 能力 → 状态 → 任务
# ---------------------------------------------------------------------------


def _capabilities(design: DesignModel, requirement: dict[str, Any]) -> list[dict[str, Any]]:
    index = _evidence(design)
    out: list[dict[str, Any]] = []
    for tpl in CAPABILITY_TEMPLATES:
        needs = tpl.get("needs") or []
        missing = [f"{kind}:{key}" for kind, key in needs if not _has(index, kind, key)]
        has_evidence = bool(needs) and not missing
        # **只有需求里显式给了数值时，才能判 pass/fail**；
        # 否则连"该不该用这条判据"都没定，只能 unknown（§0.1）。
        numeric = (requirement.get("numbers") or {}).get(tpl["criterion"]["metric"])
        if numeric is None:
            status = "unknown"
            why = f"需求没给 `{tpl['criterion']['metric']}` 的实测值"
        else:
            status = "pass" if _cmp(float(numeric), tpl["criterion"]) else "fail"
            why = f"需求给了 {numeric} {tpl['criterion']['unit']}"
        out.append({
            "id": tpl["id"], "class": tpl["class"], "title": tpl["title"],
            "criterion": dict(tpl["criterion"]),
            "status": status,
            "reason": why,
            "depends_on": list(tpl.get("depends_on") or []),
            "evidence": {"design_has": has_evidence, "missing": missing},
            "missing_need": tpl.get("missing_need", ""),
            "tasks": [],
        })
    return out


def _attach_param_evidence(capabilities: list[dict[str, Any]], store: Any) -> None:
    """把 `params.json` 的族完整度挂到对应能力上。**只加证据，不动状态。**"""
    payload = store.read("params.json") if store is not None else None
    if not isinstance(payload, dict):
        return
    rows = {row.get("capability"): row
            for row in (payload.get("readiness") or []) if isinstance(row, dict)}
    if not rows:
        return
    for cap in capabilities:
        row = rows.get(cap["id"])
        if row is None:
            continue
        cap["evidence"]["params"] = {
            "present": int(row.get("have") or 0),
            "required": int(row.get("need") or 0),
            "readiness": str(row.get("status") or "absent"),
            "missing": list(row.get("missing") or []),
            "basis": "params.json（飞控参数族完整度，**不是实测**）",
        }


def _cmp(value: float, criterion: dict[str, Any]) -> bool:
    op = str(criterion.get("op") or ">=")
    target = float(criterion.get("value") or 0)
    return value >= target if op == ">=" else value <= target


#: 任务 id 的动作后缀。**必须区分动作**：同一个能力可能同时需要
#: 「补数据」和「改设计」，两条都叫 `T-H1-1` 会让依赖与状态继承错位
#: （实测：重复 id 在 tasks 里出现两次，`depends_on` 指到两条中随机一条）。
_KIND_SUFFIX = {"measure": "M", "procure": "P", "design": "D",
                "code": "C", "test": "V"}

#: **软件能力的分解**（P51）。每条能力拆成一条**写→测→验**的链，
#: 而不是"测一个数 + 一条验收"。
#:
#: `artifact` 是产物路径（相对工程目录）：它让"写了没有"变成**可检查**的事实，
#: 而不是靠人记。**空串表示这一步要靠仪器/台架**，本仓库给不出产物。
SW_BREAKDOWN: dict[str, tuple[dict[str, str], ...]] = {
    "S1": (
        {"key": "spec", "kind": "design",
         "title": "写失效判据表（角度/下降率/链路超时 + 迟滞 + 单位）",
         "artifact": "fw/failsafe/failsafe.h",
         "verify": "每条阈值有数值、单位、迟滞带，且能被 cfg 结构体覆盖"},
        {"key": "impl", "kind": "code",
         "title": "实现风控状态机（ARMED→FAILSAFE，幂等触发）",
         "artifact": "fw/failsafe/failsafe.c",
         "verify": "过 `fw-algo failsafe` 全部用例（含边界与不重复触发）"},
        {"key": "inject", "kind": "test",
         "title": "注入测试：角度/下降率/链路各打一次超限",
         "artifact": "fw/failsafe/test_failsafe.c",
         "verify": "三类超限各触发一次、已 FAILSAFE 不重复、负角度也抓"},
        {"key": "threshold", "kind": "design",
         "title": "阈值来源：从飞控参数集抽取并固化（拆解 ⑤）",
         "artifact": "",
         "verify": "每个阈值写清取自哪个参数名，取不到就标 unknown"},
        {"key": "latency", "kind": "measure",
         "title": "测失效响应延迟（超限 → 动作）",
         "artifact": "",
         "verify": "日志时间戳差值 ≤ 判据，记录采样周期"},
    ),
    "S2": (
        {"key": "gains", "kind": "design",
         "title": "写增益表（角度环/角速率环，含量纲与限幅）",
         "artifact": "fw/pid/pid.h",
         "verify": "每个增益有单位与取值范围，限幅/抗饱和策略写明"},
        {"key": "cascade", "kind": "code",
         "title": "实现串级 PID（限幅 + 抗积分饱和）",
         "artifact": "fw/pid/pid.c",
         "verify": "过阶跃用例：无持续超调、输出不越限"},
        {"key": "step", "kind": "test",
         "title": "阶跃响应测试（超调量/上升时间）",
         "artifact": "fw/pid/test_pid.c",
         "verify": "超调与上升时间有具体数值，且按判据给出 pass/fail"},
        {"key": "margin", "kind": "measure",
         "title": "测相位裕度（≥ 30°）",
         "artifact": "",
         "verify": "给出 Bode 图或等效频域数据；没有就 unknown"},
    ),
    "S3": (
        {"key": "state", "kind": "design",
         "title": "定义状态量与观测方程（姿态/速度/位置）",
         "artifact": "",
         "verify": "每个状态量有物理含义与单位，观测来源写明"},
        {"key": "impl", "kind": "code",
         "title": "实现 EKF 预测/更新（数值稳定）",
         "artifact": "fw/ekf/ekf.c",
         "verify": "过合成数据用例：已知真值时误差收敛"},
        {"key": "converge", "kind": "test",
         "title": "收敛测试（上电 5 s 内协方差收敛）",
         "artifact": "fw/ekf/test_ekf.c",
         "verify": "记录收敛步数/时间；不收敛要能报失败原因"},
        {"key": "noise", "kind": "design",
         "title": "Q/R 的来源与量级依据",
         "artifact": "",
         "verify": "每个噪声参数写清来自 datasheet/实测/经验，取不到就 unknown"},
    ),
    "S4": (
        {"key": "iface", "kind": "design",
         "title": "定义与飞控的接口（帧率/延迟/坐标系）",
         "artifact": "",
         "verify": "三项都有数值，坐标系与飞控一致"},
        {"key": "algo", "kind": "code",
         "title": "选定并接入算法本体（光流/避障）",
         "artifact": "fw/vision/vision.c",
         "verify": "**本体必须存在**：自研或注明开源来源与许可"},
        {"key": "acc", "kind": "test",
         "title": "精度测试（1 m 位移误差 ≤ 5%）",
         "artifact": "fw/vision/test_vision.c",
         "verify": "给出误差分布，不是单次最好值"},
    ),
    "S5": (
        {"key": "fsm", "kind": "design",
         "title": "写任务状态机与断点续飞策略",
         "artifact": "fw/mission/mission.h",
         "verify": "每个状态有进入/退出条件，断点恢复有定义"},
        {"key": "impl", "kind": "code",
         "title": "实现航点解析与状态迁移",
         "artifact": "fw/mission/mission.c",
         "verify": "过用例：到点、超时、中断后恢复"},
        {"key": "wp", "kind": "test",
         "title": "航点通过半径测试（≤ 2 m）",
         "artifact": "fw/mission/test_mission.c",
         "verify": "给出半径判定逻辑与实测值，缺实测就 unknown"},
    ),
    "S6": (
        {"key": "frame", "kind": "design",
         "title": "定义帧结构（帧头/长度/校验/字段表）",
         "artifact": "fw/telemetry/telemetry.h",
         "verify": "字段偏移/宽度/单位写全，校验算法写明"},
        {"key": "impl", "kind": "code",
         "title": "实现打包/发送（含校验）",
         "artifact": "fw/telemetry/telemetry.c",
         "verify": "过用例：篡改一字节必须被校验拒掉"},
        {"key": "rate", "kind": "test",
         "title": "20 Hz 压测（不丢帧/不乱序）",
         "artifact": "fw/telemetry/test_telemetry.c",
         "verify": "给出丢帧率与最坏间隔"},
    ),
}



def _attach_evidence(store: Any, tasks: list[dict[str, Any]]) -> None:
    """给带 `artifact` 的任务挂"这个文件在不在"。

    **只报存在性，不据此判 done**：文件在 ≠ 实现对（可能是空壳）。
    真正的判定要过 `fw-algo` 的用例（§0.1：存在不是通过）。
    """
    for task in tasks:
        artifact = str(task.get("artifact") or "")
        if not artifact:
            continue
        try:
            present = store.path(artifact).is_file()
        except Exception:  # noqa: BLE001 - 路径怪也不该炸计划
            present = False
        task["evidence"] = {"artifact": artifact, "present": present}


def _sw_chain(cap: dict[str, Any], head: list[str]) -> list[dict[str, Any]]:
    """软件能力 → **写→测→验 的链**（P51）。`head` 是链头要等的前置任务。

    为什么软件要单独拆：硬件的"缺数据"靠采购/实测能闭合；软件的"缺"
    是**还没写**。只给"测一个数 + 验收"两条任务，中间那一大段工作不出现，
    任务表就会骗人（12 条任务看着像干完了）。
    """
    steps = SW_BREAKDOWN.get(cap["id"]) or ()
    out: list[dict[str, Any]] = []
    prev = list(head)
    for step in steps:
        tid = f"T-{cap['id']}-{step['key']}"
        out.append({
            "id": tid, "capability": cap["id"], "kind": step["kind"],
            "verb": _TASK_KINDS.get(step["kind"], {"verb": "做"})["verb"],
            "title": step["title"],
            "verify": step["verify"],
            "artifact": step["artifact"],
            "depends_on": sorted(set(prev)),
            "status": "open",
            "why": "软件三层分解：写 → 测 → 验（这一层没有就不算做完）",
        })
        prev = [tid]
    return out


def _tasks_for(cap: dict[str, Any]) -> list[dict[str, Any]]:
    """一个能力 → 一棵最小任务树。**每条任务都必须能验收**。

    * 硬件能力：缺数据 → 采购/实测 + 一条验收（2 层）
    * 软件能力（S*）：**写 → 测 → 验** 的链（`SW_BREAKDOWN`），验收挂在链尾
    """
    if cap["status"] == "pass":
        return []
    tasks: list[dict[str, Any]] = []
    missing = (cap.get("evidence") or {}).get("missing") or []
    if missing:
        kind = "procure"
        tasks.append({
            "id": f"T-{cap['id']}-{_KIND_SUFFIX[kind]}", "capability": cap["id"],
            "kind": kind,
            "verb": _TASK_KINDS[kind]["verb"],
            "title": f"补齐 {cap['title']} 缺的实物/数据：{', '.join(missing)}",
            "verify": _TASK_KINDS[kind]["verify"],
            "depends_on": [], "status": "open",
            "why": cap.get("missing_need") or "",
        })
    if cap["status"] == "unknown" and not missing:
        kind = "measure"
        tasks.append({
            "id": f"T-{cap['id']}-{_KIND_SUFFIX[kind]}", "capability": cap["id"],
            "kind": kind,
            "verb": _TASK_KINDS[kind]["verb"],
            "title": f"测出 `{cap['criterion']['metric']}`（{cap['criterion']['unit']}）",
            "verify": _TASK_KINDS[kind]["verify"],
            "depends_on": [], "status": "open",
            "why": cap.get("reason") or "",
        })
    if cap["status"] == "fail":
        kind = "design"
        tasks.append({
            "id": f"T-{cap['id']}-{_KIND_SUFFIX[kind]}", "capability": cap["id"],
            "kind": kind,
            "verb": _TASK_KINDS[kind]["verb"],
            "title": f"改设计让 `{cap['criterion']['metric']}` 满足 "
                     f"{cap['criterion']['op']} {cap['criterion']['value']}"
                     f" {cap['criterion']['unit']}",
            "verify": _TASK_KINDS[kind]["verify"],
            "depends_on": [], "status": "open",
            "why": cap.get("reason") or "",
        })
    # 软件能力：在（采购/实测任务）之后接上「写 → 测 → 验」的链
    head = [t["id"] for t in tasks]
    if cap.get("class") == "software":
        tasks.extend(_sw_chain(cap, head))

    # 第二层：验收任务。**永远生成**——"做完了怎么证"不是可选项。
    kind = "test"
    tasks.append({
        "id": f"T-{cap['id']}-{_KIND_SUFFIX[kind]}", "capability": cap["id"], "kind": kind,
        "verb": _TASK_KINDS[kind]["verb"],
        "title": f"验收 {cap['title']}："
                 f"{cap['criterion']['metric']} {cap['criterion']['op']} "
                 f"{cap['criterion']['value']} {cap['criterion']['unit']}",
        "verify": _TASK_KINDS[kind]["verify"],
        # 依赖**链尾**（软件链的最后一步），而不是所有前置 —— 否则验收会
        # 和"写"同时可做，等于没排序。
        "depends_on": ([tasks[-1]["id"]] if tasks else []),
        "status": "open",
        "why": "判据先行：没有这条就不算做完",
    })
    return tasks


# ---------------------------------------------------------------------------
# 版本
# ---------------------------------------------------------------------------


def list_versions(store: Any) -> list[dict[str, Any]]:
    directory = store.path(PLAN_DIR)
    if not directory.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("v*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append({
            "version": str(payload.get("version") or path.stem),
            "requirement_hash": str(payload.get("requirement", {}).get("hash") or ""),
            "created": str(payload.get("created") or ""),
            "parent": str(payload.get("parent") or ""),
            "counts": dict(payload.get("counts") or {}),
        })
    return rows


def load_version(store: Any, version: str = "") -> dict[str, Any] | None:
    rows = list_versions(store)
    if not rows:
        return None
    if not version:
        version = rows[-1]["version"]
    path = store.path(f"{PLAN_DIR}/{version}.json")
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _next_version(store: Any) -> tuple[str, str]:
    rows = list_versions(store)
    parent = rows[-1]["version"] if rows else ""
    number = 0
    for row in rows:
        try:
            number = max(number, int(str(row["version"]).lstrip("v")))
        except ValueError:
            continue
    return f"v{number + 1}", parent


def derive(store: Any, design: DesignModel, requirement: dict[str, Any],
           *, text: str = "") -> dict[str, Any]:
    """把"需求 + 现有设计"推成一版计划。**纯推导，不写盘**（`ensure` 才写）。"""
    requirement = dict(requirement or {})
    digest = _hash({"req": requirement, "text": text})
    rows = list_versions(store)
    same = next((r for r in rows if r["requirement_hash"] == digest), None)
    version = same["version"] if same else _next_version(store)[0]
    parent = (same["parent"] if same else _next_version(store)[1]) or ""

    capabilities = _capabilities(design, requirement)
    # **参数证据**（P53）：`params.json` 在就给能力挂上"族齐不齐"。
    # **不改 status** —— 参数说明"增益有值"，不说明"环稳定"（§0.1）。
    _attach_param_evidence(capabilities, store)
    modules = _modules(design)

    # 任务：能力缺口 → 任务树；再把**任务间依赖**按能力依赖补上
    by_cap = {c["id"]: c for c in capabilities}
    tasks: list[dict[str, Any]] = []
    for cap in capabilities:
        own = _tasks_for(cap)
        for dep in cap.get("depends_on") or []:
            for task in own:
                if dep in by_cap:
                    upstream = [f"T-{dep}-V"]
                    task["depends_on"] = sorted(set(task["depends_on"] + upstream))
        cap["tasks"] = [t["id"] for t in own]
        tasks.extend(own)

    # 产物证据（只报存在性；不据此判 done）
    _attach_evidence(store, tasks)

    # 图要**在任务生成之后**建：三层（任务/能力/模块）都要在节点里。
    graph = _graph(design, modules, capabilities, tasks)

    # 继承上一版的任务状态（按 id）。**只继承状态，不继承判据**——
    # 判据永远按当前需求重算，否则"以前过了"会掩盖"现在不成立"。
    previous = load_version(store, parent) if parent else None
    carried: list[dict[str, Any]] = []
    if previous:
        old = {t["id"]: t for t in (previous.get("tasks") or []) if isinstance(t, dict)}
        for task in tasks:
            prior = old.get(task["id"])
            if prior is None:
                continue
            if str(prior.get("status")) in ("done", "blocked"):
                task["status"] = str(prior["status"])
                carried.append(task["id"])

    counts = {
        "capabilities": len(capabilities),
        "pass": sum(1 for c in capabilities if c["status"] == "pass"),
        "unknown": sum(1 for c in capabilities if c["status"] == "unknown"),
        "fail": sum(1 for c in capabilities if c["status"] == "fail"),
        "modules": len(modules),
        "edges": graph["counts"]["edges"],
        "tasks": len(tasks),
        "software_tasks": sum(1 for t in tasks
                              if str(t.get("capability") or "").startswith("S")),
        "hardware_tasks": sum(1 for t in tasks
                              if str(t.get("capability") or "").startswith("H")),
        "with_artifact": sum(1 for t in tasks if t.get("artifact")),
        "artifact_present": sum(1 for t in tasks
                                if (t.get("evidence") or {}).get("present")),
        "open_tasks": sum(1 for t in tasks if t["status"] == "open"),
    }
    return {
        "version": version,
        "parent": parent,
        "project": str(getattr(design, "id", "")),
        "created": _now(),
        "requirement": {"id": requirement.get("id") or "",
                        "hash": digest, "text": text,
                        "roles": list(requirement.get("roles") or []),
                        "numbers": dict(requirement.get("numbers") or {})},
        "capabilities": capabilities,
        "modules": modules,
        "graph": graph,
        "tasks": tasks,
        "carried_tasks": carried,
        "counts": counts,
        "param_evidence": sorted(
            cap["id"] for cap in capabilities if (cap.get("evidence") or {}).get("params")
        ),
        "note": ("每一版都是**自包含快照**；判据按当前需求重算，"
                 "任务状态按 id 继承。`unknown` 不是通过。"),
    }


def ensure(store: Any, design: DesignModel, requirement: dict[str, Any] | None = None,
           *, text: str = "", allow_new: bool = True) -> dict[str, Any]:
    """**幂等**：同需求不新建版本，只刷新最新一版的状态。

    这是"AI 每次操作都默认维护一份"的入口：`snapshot` 会调它，
    同一份需求连刷十次也只留一个文件。

    `allow_new=False`（snapshot 的用法）：**只在还没有任何版本时**建 v1，
    其余情况只报"最新是 vN / 需求变了但没建新版本"。
    为什么要这条：`snapshot` 可能被反复调用（前端轮询），若它每次都按
    "当前 req.json"新建版本，一次需求改动会刷出一串 v3/v4/v5 —— 历史被噪音
    淹没。**建版本是"加需求"这个动作**，由 `plan` 命令或人触发。
    """
    requirement = dict(requirement or {})
    try:
        payload = derive(store, design, requirement, text=text)
    except Exception as exc:  # noqa: BLE001 - 计划不该拖垮快照
        return {"status": "not_ready", "note": f"{type(exc).__name__}: {exc}"}
    path = store.path(f"{PLAN_DIR}/{payload['version']}.json")
    fresh = not path.is_file()
    rows = list_versions(store)
    latest = rows[-1]["version"] if rows else ""
    if not allow_new and rows:
        if fresh:
            # 当前需求对不上最新版 → **建版本是"加需求"这个动作**，不在这里做
            return {"status": "not_ready", "version": latest, "created": False,
                    "reason": ("当前需求与最新 "
                               f"{latest} 不一致，但 `snapshot` **不建新版本**"
                               "（避免前端轮询刷出一串 v3/v4/v5）；"
                               f"要加需求请跑 `plan <id> --req=...`"),
                    "counts": rows[-1].get("counts") or {}}
        if payload["version"] != latest:
            # 命中了**旧版本**：报最新那一版，别把旧版当现状
            newest = load_version(store, latest) or {}
            return {"status": "ok", "version": latest, "created": False,
                    "stale": True,
                    "reason": (f"当前需求对应旧版 {payload['version']}；"
                               f"最新是 {latest}（报最新的，不复活旧版）"),
                    "counts": newest.get("counts") or {}}
    if fresh:
        store.write(f"{PLAN_DIR}/{payload['version']}.json", payload)
        store.write(INDEX, {"project": payload["project"], "updated": _now(),
                            "versions": list_versions(store)})
    return {"status": "ok", "version": payload["version"], "created": fresh,
            "path": f"{PLAN_DIR}/{payload['version']}.json",
            "counts": payload["counts"]}


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.plan",
        "dir": PLAN_DIR,
        "index": INDEX,
        "capabilities": [t["id"] for t in CAPABILITY_TEMPLATES],
        "software_breakdown": {k: [x["key"] for x in v]
                               for k, v in SW_BREAKDOWN.items()},
        "task_kinds": sorted(_TASK_KINDS),
        "versioning": ("同需求不新建版本（按 requirement_hash）；新需求 → v(N+1)，"
                       "parent 指向上一版；任务状态按 id 继承，判据按当前需求重算"),
        "discipline": ("判据先写；缺证据=unknown（不是 pass）；"
                       "每个能力都有一条验收任务（没有 verify 的任务不生成）；"
                       "软件能力走 **写→测→验** 的链（SW_BREAKDOWN），"
                       "带 artifact 的任务只报产物存在性，不据此判 done"),
    }