"""``hw_velxio.design_cli`` —— 规范模型的 Python 入口。

契约与 ``hwforge.blocks_cli`` 一致：**一次调用、一行 JSON**。

    python -m hw_velxio.design_cli boards
    python -m hw_velxio.design_cli catalog
    python -m hw_velxio.design_cli new <id> <board-kind> [title]
    python -m hw_velxio.design_cli list
    python -m hw_velxio.design_cli snapshot <id>
    python -m hw_velxio.design_cli get <id>
    python -m hw_velxio.design_cli put <id> <design.json|-> 
    python -m hw_velxio.design_cli import <id> <path> [--bom <path>] [--boards all|main|<doc>]
    python -m hw_velxio.design_cli bom <id> <bom-path> [--dry-run]
    python -m hw_velxio.design_cli roles [--validate] [--roles a,b,c] [--goal "…"] [--assemble <id>]
    python -m hw_velxio.design_cli project2d <id>
    python -m hw_velxio.design_cli scene3d <id>
    python -m hw_velxio.design_cli check <id> [--pitch 0.5]
    python -m hw_velxio.design_cli budget <id> [--limits '<json>']
    python -m hw_velxio.design_cli deferred <id> [lock|defer|show] ...
    python -m hw_velxio.design_cli trace <id> [list|verify|append]
    python -m hw_velxio.design_cli crosscheck <id>
    python -m hw_velxio.design_cli export <id>
    python -m hw_velxio.design_cli evidence <id>
    python -m hw_velxio.design_cli survey [--write] [--json]

三种状态：``ok`` / ``not_ready``（前置缺失）/ ``failed``（跑了但出错）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import __version__


def _emit(obj: dict[str, Any]) -> None:
    """唯一写 stdout 的地方：一行 UTF-8 JSON，不受终端代码页影响。"""
    blob = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(blob)
    sys.stdout.buffer.flush()


def _store(project_id: str):
    from .design.store import DesignStore
    return DesignStore(project_id)


def _load(project_id: str):
    store = _store(project_id)
    if not store.exists():
        raise FileNotFoundError(f"工程 {project_id!r} 不存在（没有 design.json）")
    return store, store.load()


# ---------------------------------------------------------------------------
# 场景载荷
# ---------------------------------------------------------------------------

def scene3d(design) -> dict[str, Any]:
    """3D 渲染层要的最小几何：全部是 mm 的 AABB / 轮廓 / 路径。

    多板：元件坐标平移到**拼板坐标**（与 2D 同一套 `panel_layout` 偏移），
    每块板各自挤出。渲染层拿到的是拼板坐标，不需要知道板的概念。
    """
    from .design.projection import PANEL_GAP_MM, panel_layout
    from .design.ratlines import build_ratlines

    panel = panel_layout(design)
    offset_of = {entry["board"].id: (entry["x"], entry["y"])
                 for entry in panel["boards"]}

    def shift(part) -> tuple[float, float]:
        return offset_of.get(design.board_of(part).id, (0.0, 0.0))

    parts = []
    for part in design.parts:
        x0, y0, z0, x1, y1, z1 = part.bbox_mm()
        ox, oy = shift(part)
        parts.append({
            "ref": part.ref,
            "label": part.label,
            "kind": part.kind,
            "role": part.role,
            "board_id": design.board_of(part).id,
            # bbox 与焊盘都平移到**拼板坐标**（渲染层不再关心板归属）
            "bbox_mm": [round(x0 + ox, 3), round(y0 + oy, 3), round(z0, 3),
                        round(x1 + ox, 3), round(y1 + oy, 3), round(z1, 3)],
            # 未旋转的足迹尺寸：渲染层要用它建几何，再按 rotation_z 转。
            # bbox_mm 是**旋转后**的 AABB，拿它建盒子再转一次就是转两遍。
            "size_mm": dict(part.size_mm),
            "rotation_z": part.placement.rotation_z,
            "physical_source": part.physical_source,
            "mount": part.placement.mount,
            "side": part.placement.side,
            # 焊盘（3D 要有焊点：焊盘是真实的金属面）。
            "pads": [{
                "num": pad.num,
                "x_mm": round(pad.x_mm + ox, 3),
                "y_mm": round(pad.y_mm + oy, 3),
                "w_mm": round(pad.w_mm, 3),
                "h_mm": round(pad.h_mm, 3),
                "shape": pad.shape,
                "rotation_z": pad.rotation_z,
                "layer": pad.layer,
            } for pad in part.pads],
        })
    # 铜线只属于主板（`Wire` 还没有 board_id），按主板偏移平移。
    main_ox, main_oy = offset_of.get(design.board.id, (0.0, 0.0))
    return {
        "units": "mm",
        "panel": {
            "size_mm": dict(panel["size_mm"]),
            "gap_mm": PANEL_GAP_MM,
            "board_count": len(panel["boards"]),
        },
        # 主参考板（兼容旧字段）
        "board": {
            "kind": design.board.kind,
            "id": design.board.id,
            "outline": [[round(x + main_ox, 3), round(y + main_oy, 3)]
                        for x, y in design.board.outline],
            "size_mm": dict(design.board.size_mm),
            "thickness_mm": design.board.thickness_mm,
        },
        # 全部板：每块板各自挤出（拼板坐标）
        "boards": [{
            "id": entry["board"].id,
            "kind": entry["board"].kind,
            "name": entry["board"].name,
            "x_mm": entry["x"],
            "y_mm": entry["y"],
            "outline": [[round(x + entry["x"], 3), round(y + entry["y"], 3)]
                        for x, y in entry["board"].outline],
            "size_mm": dict(entry["board"].size_mm),
            "thickness_mm": entry["board"].thickness_mm,
            "parts": len(design.parts_of(entry["board"])),
        } for entry in panel["boards"]],
        "parts": parts,
        "wires": [
            {"net": w.net,
             "path_mm": [[round(x + main_ox, 3), round(y + main_oy, 3),
                          round(z, 3)] for x, y, z in w.path_mm],
             "layer": w.layer,
             "width_mm": round(w.width_mm, 4),
             "routed": True}
            for w in design.wires
        ],
        # 飞线：从网表推导，**不是铜线**（`routed: False`）。
        # 端点按**所属板**平移；跨板飞线不画（不是 PCB 铜线）。
        "ratlines": [{
            "net": line["net"],
            "path_mm": [[round(x + offset_of.get(str(line.get("board_id") or ""),
                                                 (0.0, 0.0))[0], 3),
                         round(y + offset_of.get(str(line.get("board_id") or ""),
                                                 (0.0, 0.0))[1], 3), 0.0]
                        for x, y in line["segments"]],
            "length_mm": line["length_mm"],
            "pins": line["pins"],
            "routed": False,
            "approximate": line["approximate"],
        } for line in build_ratlines(design) if not line.get("cross_board")],
        "mount_holes": [
            {"board_id": entry["board"].id,
             "x_mm": round(h.x_mm + entry["x"], 3),
             "y_mm": round(h.y_mm + entry["y"], 3),
             "diameter_mm": h.diameter_mm,
             "source": h.source}
            for entry in panel["boards"]
            for h in entry["board"].mount_holes
        ],
        "enclosure": design.enclosure.to_dict() if design.enclosure else None,
    }


def cmd_emit_status(args: list[str]) -> dict[str, Any]:
    """``emit-status <id>`` —— 只读发射摘要（不跑判定、不碰立创）。

    P46 的前端要**轮询**这个（比 `snapshot` 便宜得多：snapshot 会跑检查/
    预算/核对，而这里只读 `emit.json`）。
    """
    if not args or args[0].startswith("-"):
        return {"status": "failed", "findings": ["emit-status 需要 <id>"]}
    store = _store(args[0])
    if not store.exists():
        return {"status": "failed", "findings": [f"工程 {args[0]!r} 不存在"]}
    summary = emit_summary(store)
    path = store.path("emit.json")
    summary["mtime"] = (path.stat().st_mtime if path.is_file() else 0)
    return summary


def emit_summary(store) -> dict[str, Any]:
    """`emit.json` → 首屏要的那几行（**只投影，不重判**）。

    P46：发射是本项目最贵的一步（几十秒到几分钟，8 段），而此前 UI 上
    **一点都看不到**。这里把"哪段成了、哪段没过、产物在哪"压成一份摘要。
    """
    payload = store.read("emit.json")
    if not isinstance(payload, dict):
        return {"status": "not_ready", "reason": "还没发射过（没有 emit.json）",
                "jobs": [], "artifacts": [], "verify": {}, "fabrication": {}}
    jobs = [{"name": str(name), "status": str((job or {}).get("status") or ""),
             "note": str((job or {}).get("note") or "")[:200]}
            for name, job in (payload.get("jobs") or {}).items()]
    outputs = (payload.get("jobs") or {}).get("outputs") or {}
    artifacts = [{"kind": str(a.get("kind") or ""), "name": str(a.get("name") or ""),
                  "size": int(a.get("size") or 0)}
                 for a in (outputs.get("artifacts") or []) if isinstance(a, dict)]
    verify = payload.get("verify_detail") or {}
    totals = verify.get("totals") or {}
    return {
        "status": str(payload.get("status") or "not_ready"),
        "version": str(payload.get("version") or ""),
        "project": dict(payload.get("project") or {}),
        "jobs": jobs,
        "bad_jobs": [j["name"] for j in jobs if j["status"] != "ok"],
        "verify": {"nets": int(totals.get("nets") or 0),
                   "connected": int(totals.get("connected") or 0),
                   "split": int(totals.get("split") or 0)},
        "fabrication": payload.get("fabrication") or {},
        "channel": payload.get("channel") or {},
        "pareto": payload.get("parity") or {},
        "artifacts": artifacts,
        "note": (f"{len(jobs)} 段，"
                 + ("全 ok" if jobs and not [j for j in jobs if j["status"] != "ok"]
                    else "未通过：" + "、".join(j["name"] for j in jobs
                                              if j["status"] != "ok"))),
    }


def scaffold_summary(store) -> dict[str, Any]:
    """`scaffold.json` + `open_items.md` → 半成品摘要（P46）。

    **没有 open_items 也不代表"没问题"**：那是"还没跑过 scaffold"。
    """
    payload = store.read("scaffold.json")
    if not isinstance(payload, dict):
        return {"status": "not_ready", "reason": "还没跑过 scaffold（没有 scaffold.json）",
                "stages": [], "open_items": [], "count": 0}
    items = [{"what": str(row.get("what") or ""), "need": str(row.get("need") or "")}
             for row in (payload.get("open_items") or []) if isinstance(row, dict)]
    return {
        "status": str(payload.get("status") or "not_ready"),
        "route": str(payload.get("route") or ""),
        "stages": [{"stage": str(r.get("stage") or ""),
                    "status": str(r.get("status") or ""),
                    "note": str(r.get("note") or "")[:200]}
                   for r in (payload.get("stages") or []) if isinstance(r, dict)],
        "open_items": items,
        "count": len(items),
        "has_markdown": store.path("open_items.md").is_file(),
        "note": (f"{len(items)} 条待办（不阻塞，改完重跑就消失）" if items
                 else "没有待办；但**没待办也可能只是没跑过**"),
    }


def snapshot(project_id: str, *, pitch_mm: float = 0.5) -> dict[str, Any]:
    """一次读取渲染 + 判定 + 预算 + 闸门所需的一切（一次往返）。"""
    from .design import budgets, crosscheck, discipline, physical
    from .design.projection import to_2d

    store, design = _load(project_id)
    problems = design.validate()

    out: dict[str, Any] = {
        "status": "ok",
        "version": __version__,
        "project": store.summary(),
        "design": design.to_dict(),
        "summary": design.summary(),
        "problems": problems,
        "projection2d": to_2d(design),
        "scene3d": scene3d(design),
    }

    try:
        checks = physical.run_checks(design, pitch_mm=pitch_mm)
        # **每条 finding 带上可点的下一步**（P1）。`physical` 早就给了
        # `fix={"need": "move_apart", "refs": [...]}`；这里只做翻译，
        # 不重新判断"该怎么改"。翻译表在 `design/nextstep.py`（唯一一处）。
        from .design import nextstep as nextstep_module
        for row in checks.get("findings") or []:
            if isinstance(row, dict) and isinstance(row.get("fix"), dict):
                step = nextstep_module.build(row["fix"], project=project_id,
                                             refs=row.get("refs"))
                if step is not None:
                    row["next"] = step
        out["checks"] = checks
    except RuntimeError as exc:
        out["checks"] = {"ok": None, "status": "not_ready", "error": str(exc),
                         "findings": [], "not_verified": [str(exc)]}

    try:
        out["budget"] = budgets.evaluate(design)
    except RuntimeError as exc:
        out["budget"] = {"ok": None, "status": "not_ready", "error": str(exc)}

    try:
        ledger = discipline.ledger_from_json(store.read("deferred.json"))
        out["deferred"] = {**discipline.ledger_to_json(ledger),
                           **discipline.gate(ledger)}
    except RuntimeError as exc:
        out["deferred"] = {"status": "not_ready", "error": str(exc)}

    try:
        out["crosscheck"] = crosscheck.run_crosscheck(design)
    except Exception as exc:  # noqa: BLE001
        out["crosscheck"] = {"ok": None, "error": f"{type(exc).__name__}: {exc}"}

    # **协同验证**：需求数值 vs 硬件能力（hwforge coverify 的 8 条 CV 检查）。
    #
    # 它回答的是**别的检查都不回答**的问题：需求里写的数字，硬件能否兑现。
    # 之前的缺口是它只在 hwforge 的积木里跑过，需求→设计的自动链路
    # 从来没跑过 —— 于是"检查全绿"不代表"需求被满足"。
    try:
        from .design import coverify as coverify_module
        co = coverify_module.run(design)
        out["coverify"] = {**co.to_dict(),
                           "summary": coverify_module.summarise(co)}
    except Exception as exc:  # noqa: BLE001
        out["coverify"] = {"ok": None, "status": "not_ready",
                           "error": f"{type(exc).__name__}: {exc}",
                           "findings": [], "not_verified": [str(exc)]}

    # **DC 电学**：压降 / 轨跌 / 结温（EL-01..05）。
    #
    # 它补的是降额表补不了的那一块：降额表能说"这颗 MOSFET 在 60% Vds"，
    # 但说不出"5V 轨在堵转时跌到 4.4V，舵机褐变复位"。
    try:
        from .design import electrical as electrical_module
        elec = electrical_module.run(design)
        out["electrical"] = {**elec.to_dict(),
                             "summary": {
                                 "ok": elec.ok,
                                 "wire_drops": len(elec.wires),
                                 "rails": len(elec.rails),
                                 "regulators": len(elec.regulators),
                                 "failed": len([f for f in elec.findings()
                                                if not f["ok"]]),
                             }}
    except Exception as exc:  # noqa: BLE001
        out["electrical"] = {"ok": None, "status": "not_ready",
                             "error": f"{type(exc).__name__}: {exc}",
                             "wires": [], "rails": [], "regulators": [],
                             "not_verified": [str(exc)]}

    # **阶梯**：把上面这些检查映射到 hwforge 的 21 级 tier 上，
    # 让"哪些层验过、哪些没有工具、哪些这次没跑"三者分开可见。
    try:
        from .design import tiers as tiers_module
        tier_report = tiers_module.report(
            checks=out.get("checks"),
            coverify=out.get("coverify"),
            budget=out.get("budget"),
            electrical=out.get("electrical"))
        out["tiers"] = {**tier_report.to_dict(),
                        "counts": tier_report.counts(),
                        "summary": tier_report.summary()}
    except Exception as exc:  # noqa: BLE001
        out["tiers"] = {"ok": None, "available": False,
                        "error": f"{type(exc).__name__}: {exc}", "tiers": []}

    try:
        out["evidence"] = discipline.evidence(store, design)
    except RuntimeError as exc:
        out["evidence"] = {"status": "not_ready", "error": str(exc)}

    # 阶段：确定性推导（空/半成品/成品）。UI 用它着色与挡导出，
    # 不依赖任何 AI 判断——见 design/phase.py。
    from .design import phase as phase_module
    try:
        ledger_json = out.get("deferred") or {}
        open_list = ledger_json.get("open_irreversible")
        out["phase"] = phase_module.evaluate(
            design,
            open_irreversible=list(open_list) if isinstance(open_list, list) else None)
    except Exception as exc:  # noqa: BLE001
        out["phase"] = {"phase": "partial",
                        "reason": f"阶段推导失败：{type(exc).__name__}: {exc}",
                        "veto": [], "counts": {}}

    # **需求草稿摘要**（P5）：草稿在不在、过没过校验、应用了没。
    #
    # 只做聚合，**不跑 generate**（那要几秒）。首屏只需要一句话：
    # "有一份草稿，3 个角色 2 条接口，校验过了但还没应用"。
    try:
        from .design import req_draft as req_module
        draft = req_module.load(store)
        validation = req_module.validate(draft) if draft is not None else None
        applied = isinstance(store.read(req_module.REQ_FILE), dict)
        scope = req_module.draft_scope(store)
        exists = store.exists()
        out["req"] = {
            "draft": None if draft is None else draft.to_dict(),
            "validation": validation,
            "applied": applied,
            # **草稿落在哪一层**：工程级 / 工作区级（从零开局）/ 没有
            "scope": scope,
            "project_exists": exists,
            "files": {"draft": req_module.DRAFT_FILE,
                      "requirement": req_module.REQ_FILE},
            "next": (
                "还没有草稿：用 hw_req_draft 写一份" if draft is None
                else "草稿没通过校验，按 problems 改" if not validation["ok"]
                else ("草稿合法（工程还不存在，apply 时会顺手建工程），"
                      "等人在面板上确认" if not exists
                      else "草稿合法，等人在面板上确认后 apply")
                if not applied
                else "已应用，可以 hw_generate"),
        }
    except Exception as exc:  # noqa: BLE001
        out["req"] = {"draft": None, "validation": None, "applied": False,
                      "error": f"{type(exc).__name__}: {exc}"}

    # **固件矩阵摘要**（P4）：引脚 × (配置/使用/结论) 的**计数与前几行问题**。
    #
    # 这里要算 `extract_uses`（扫源码）+ `crosscheck`（判定），所以有成本；
    # 但它回答的是"固件和硬件对不对得上"，那是首屏该有的信息。
    # 没有 `.ioc` 或没有源码时如实 `not_ready`——不是"全对"。
    try:
        from .design import firmware_matrix as matrix_module
        from .design.fwgen import pinmap as pm_module
        ioc_path = _first_ioc(store.dir)
        if ioc_path is None:
            out["firmware"] = {
                "status": "not_ready",
                "reason": "工作区里没有 .ioc，硬件侧没有可核对的引脚配置",
                "counts": {}, "rows": [], "checked": 0, "used_pins": 0}
        else:
            pins = pm_module.parse_pins(ioc_path)
            roots = sorted(p for p in (store.dir / "src").rglob("*")
                           if p.suffix.lower() in (".c", ".h", ".cpp", ".hpp")) \
                if (store.dir / "src").is_dir() else []
            if not roots:
                roots = sorted(p for p in store.dir.rglob("*")
                               if p.suffix.lower() in (".c", ".h")
                               and p.name != "")
                roots = [p for p in roots if "node_modules" not in str(p)][:40]
            extracted = pm_module.extract_uses(roots, root=store.dir)
            if extracted.get("status") != "ok":
                out["firmware"] = {
                    "status": "not_ready",
                    "reason": extracted.get("reason") or "固件侧没有可核对的内容",
                    "counts": {}, "rows": [], "checked": 0, "used_pins": 0,
                    "unknown": len(extracted.get("unpaired") or [])}
            else:
                full = matrix_module.matrix(pins, extracted["uses"],
                                            source=Path(ioc_path).name)
                out["firmware"] = matrix_module.summarize(full)
    except Exception as exc:  # noqa: BLE001
        out["firmware"] = {"status": "not_ready",
                           "reason": f"{type(exc).__name__}: {exc}",
                           "counts": {}, "rows": [], "checked": 0, "used_pins": 0}

    # ---- 项目计划图（P49）-----------------------------------------------
    #
    # **默认维护**：每次打开快照就保证有一版 `plan/vN.json`。幂等——同一份
    # 需求刷新十次也只留一个文件（按 requirement_hash 判重）。
    try:
        from .design import plan as plan_module
        requirement = store.read("req.json")
        if not isinstance(requirement, dict):
            requirement = {}
        out["plan"] = plan_module.ensure(store, design, requirement,
                                         allow_new=False)
    except Exception as exc:  # noqa: BLE001 - 计划失败不该拖垮快照
        out["plan"] = {"status": "not_ready",
                       "note": f"{type(exc).__name__}: {exc}"}

    # ---- 发射 / 半成品（P46）------------------------------------------
    #
    # 这两块此前在 UI 上**完全不可见**：8 段发射只回聊天里，`open_items.md`
    # 只有人自己去翻文件。两者都是"只投影已有产物"，不重新判定。
    try:
        out["emit"] = emit_summary(store)
    except Exception as exc:  # noqa: BLE001
        out["emit"] = {"status": "not_ready",
                       "reason": f"{type(exc).__name__}: {exc}",
                       "jobs": [], "artifacts": []}
    try:
        out["scaffold"] = scaffold_summary(store)
    except Exception as exc:  # noqa: BLE001
        out["scaffold"] = {"status": "not_ready",
                           "reason": f"{type(exc).__name__}: {exc}",
                           "stages": [], "open_items": [], "count": 0}

    # **参数敏感度**（P3）：只有模型层的**分类与计数**进快照。
    #
    # 为什么不放整张 136 行表：那是"打开工程"时最贵的一步（要逐个 build
    # MJCF），而首屏只需要一句话——"136 个参数里只有 2 个真的驱动模型"。
    # 完整表走 `sensitivity` 命令按需取。
    try:
        from .design import sensitivity as sensitivity_module
        from .design import sim_params as sim_params_module
        params = sim_params_module.effective(design)
        sen = sensitivity_module.report(design, params)
        out["params"] = {
            "total": sen["params_total"],
            "counts": sen["counts"],
            "priority": [{"key": row["key"], "group": row["group"],
                          "value": row["value"], "changed": row["changed"]}
                         for row in sen["priority"]],
            "note": ("只有 wired 类才真的驱动模型；unwired 是「没有任何代码读」，"
                     "不是「低敏感」"),
        }
    except Exception as exc:  # noqa: BLE001
        out["params"] = {"total": 0, "counts": {}, "priority": [],
                         "error": f"{type(exc).__name__}: {exc}"}

    # **实体索引**（P2）：每个位号一行三态摘要。2D 画布按它着色、
    # Inspector 按它显示"这个件验到哪一步"。
    # 只放计数与三态，**不放正文**——316 个元件的正文会把快照撑到几 MB。
    try:
        from .design import entity as entity_module
        out["entities"] = entity_module.index(
            design, claims=store.read("claims.json"),
            checks=out.get("checks"))
    except Exception as exc:  # noqa: BLE001
        out["entities"] = {}
        out.setdefault("warnings", []).append(
            f"实体索引生成失败：{type(exc).__name__}: {exc}")

    # **结论**：前端首屏就是它。只投影已有产物（不重跑判定），
    # 所以这一步很便宜——而 `checks`/`tiers` 那几步是贵的。
    # 出错也不挡其它字段：一屏结论挂了不该让整个工作台打不开。
    try:
        from .design import verdict as verdict_module
        out["verdict"] = verdict_module.compose(store)
    except Exception as exc:  # noqa: BLE001
        out["verdict"] = {"status": "not_ready",
                          "error": f"{type(exc).__name__}: {exc}",
                          "levels": [], "counts": {}, "next": []}

    return out


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

def cmd_workspace(args: list[str]) -> dict[str, Any]:
    """``workspace [show|set <path>|default]`` —— 查看/切换工作区。

    工作区是**用户资产目录**：工程全在里面，与本插件的安装位置无关。
    """
    from .design import workspace as ws

    action = args[0] if args else "show"
    if action == "set":
        if len(args) < 2:
            return {"status": "failed", "findings": ["workspace set 需要 <绝对路径>"]}
        try:
            ws.set_workspace(args[1])
        except ValueError as exc:
            return {"status": "failed", "findings": [str(exc)]}
        root = ws.workspace_root()
        root.mkdir(parents=True, exist_ok=True)
        # 与 show 返回**同一组字段**：缺 source/writable 会让面板在刚切换完
        # 这一刻显示"来源：unknown"，而 show 又是对的——同一个事实两种形状。
        return _describe_workspace(root, changed=True, projects=_project_ids(root))
    if action == "default":
        target = ws.default_workspace()
        target.mkdir(parents=True, exist_ok=True)
        ws.save_config({"workspace": str(target)})
        return _describe_workspace(target, changed=True, projects=_project_ids(target))
    if action == "create":
        # ``workspace create <parent> <name>``：在**用户指定的父目录**下建子目录
        # 并切换过去。(3) 的语义：新建工程落在 DSH 当前工作区的子文件夹里。
        if len(args) < 3:
            return {"status": "failed",
                    "findings": ["workspace create 需要 <父目录> <子目录名>"]}
        parent, name = Path(args[1]), args[2].strip()
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            return {"status": "failed", "findings": [f"目录名不合法：{name!r}"]}
        if not parent.is_dir():
            return {"status": "failed",
                    "findings": [f"父目录不存在：{parent}（先选一个存在的文件夹）"]}
        target = parent / name
        if target.exists():
            return {"status": "failed",
                    "findings": [f"{target} 已存在；换一个名字，或直接"
                                 "用「选择文件夹」打开它"]}
        try:
            target.mkdir(parents=True)
        except OSError as exc:
            return {"status": "failed", "findings": [f"无法创建 {target}：{exc}"]}
        config = ws.save_config({"workspace": str(target)})
        return _describe_workspace(target, changed=True, created=True,
                                   projects=[], config=config)
    if action != "show":
        return {"status": "failed",
                "findings": [f"未知 workspace 动作 {action!r}（show/set/create/default）"]}

    return _describe_workspace(ws.workspace_root(), changed=False)


def _describe_workspace(root: Path, changed: bool, **extra: Any) -> dict[str, Any]:
    """workspace 各动作的统一返回形状（show/set/create/default 共用）。

    `source` 说清这个根从哪来（env/config/default），`writable` 是**写探针**
    的结果而不是猜的——只读目录必须在保存前暴露，而不是等到写失败。
    """
    import os as _os
    from .design import workspace as ws

    env = bool(_os.environ.get("HWV_WORKSPACE", "").strip())
    configured = isinstance(ws.load_config().get("workspace"), str)
    body: dict[str, Any] = {
        "status": "ok",
        "workspace": str(root),
        "exists": root.is_dir(),
        "writable": _probe_writable(root),
        "source": "env" if env else ("config" if configured else "default"),
        "default": str(ws.default_workspace()),
        "config_path": str(ws.config_path()),
        "changed": changed,
    }
    body.update(extra)
    return body


def _project_ids(root: Path) -> list[str]:
    """工作区里**真正的工程**：有 design.json 的那些目录。

    以前 `bridge status` 把根目录下每个文件夹都当工程（连 `logs/`、
    `*-master/` 都算），于是列表里有 15 项而真工程只有 5 个。
    """
    try:
        return [p.name for p in sorted(root.iterdir())
                if p.is_dir() and (p / "design.json").is_file()]
    except OSError:
        return []


def _probe_writable(path) -> bool:
    """写一次探针再删；只读目录/权限问题在这里暴露，而不是在保存时。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".hwv-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:  # noqa: BLE001
        return False


def cmd_new(args: list[str]) -> dict[str, Any]:
    from .design.boards import board_preset
    from .design.model import DesignModel
    from .design.store import DesignStore, project_root

    if not args:
        return {"status": "failed", "findings": ["new 需要 <id> [board-kind] [title]"]}
    project_id = args[0]
    board_kind = args[1] if len(args) > 1 else "arduino-uno"
    title = args[2] if len(args) > 2 else project_id

    # 工作区不存在就建——"新建工程"的语义包含"把工作区准备好"。
    root = project_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"status": "failed",
                "findings": [f"工作区无法创建：{root}（{exc}）"]}

    store = DesignStore(project_id)
    if store.exists():
        return {"status": "failed",
                "findings": [f"工程 {project_id!r} 已存在；换一个 id 或先删除"]}

    design = DesignModel(id=project_id, title=title, board=board_preset(board_kind))
    from .design import discipline

    ledger = discipline.new_ledger()
    discipline.auto_lock(design, ledger)
    store.save(design)
    store.write("deferred.json", discipline.ledger_to_json(ledger))
    discipline.append(store, "cli", "design.created",
                      {"board": board_kind, "title": title})
    return {"status": "ok", "project": store.summary(),
            "workspace": str(root),
            "design": design.to_dict(),
            "warnings": design.warnings}


def cmd_list(_args: list[str]) -> dict[str, Any]:
    from .design.store import list_projects
    projects = list_projects()
    return {"status": "ok", "projects": projects, "count": len(projects)}


def cmd_get(args: list[str]) -> dict[str, Any]:
    if not args:
        return {"status": "failed", "findings": ["get 需要 <id>"]}
    store, design = _load(args[0])
    return {"status": "ok", "design": design.to_dict(),
            "summary": design.summary(), "project": store.summary()}


def cmd_put(args: list[str]) -> dict[str, Any]:
    from .design.model import DesignModel

    if len(args) < 2:
        return {"status": "failed", "findings": ["put 需要 <id> <design.json|->"]}
    project_id, source = args[0], args[1]
    try:
        if source == "-":
            data = json.load(sys.stdin)
        else:
            from pathlib import Path
            data = json.loads(Path(source).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "findings": [f"读不到设计 JSON：{exc}"]}
    try:
        design = DesignModel.from_dict(data)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "findings": [f"模型不合法：{exc}"]}
    design.id = project_id
    store = _store(project_id)
    store.save(design)
    from .design import discipline
    discipline.append(store, "cli", "design.updated",
                      {"parts": len(design.parts), "nets": len(design.nets)})

    # **`put` 也要跑 auto_lock**：`new`/`import` 都会锁 `mcu_family`（板卡
    # 型号就是已定的决策），而 `put` 之前漏了这一步。后果是：用 put 建的设计
    # 把四个决策都锁了，导出闸门仍然报「mcu_family 未闭合」——用户会以为
    # 自己漏了什么，其实是这条路径少了一步。
    ledger = discipline.ledger_from_json(store.read("deferred.json"))
    auto_locked = discipline.auto_lock(design, ledger)
    if auto_locked:
        store.write("deferred.json", discipline.ledger_to_json(ledger))
    return {"status": "ok", "summary": design.summary(),
            "problems": design.validate(),
            "auto_locked": auto_locked,
            "deferred": {**discipline.ledger_to_json(ledger),
                         **discipline.gate(ledger)}}


def _resolve_input(path_text: str) -> Path:
    """解析待导入文件：绝对路径原样；相对路径**相对当前工作区**再退回 CWD。

    为什么要有这一步：工作台里的所有文件操作都在工作区内进行，用户粘一个
    ``sources/board.kicad_pcb`` 时，它指的是工作区里的那个，不是进程 cwd 的。
    """
    from .design.store import project_root

    candidate = Path(path_text).expanduser()
    if candidate.is_absolute():
        return candidate
    in_workspace = project_root() / candidate
    if in_workspace.exists():
        return in_workspace
    return candidate


def cmd_import(args: list[str]) -> dict[str, Any]:
    from .design import discipline
    from .design.importers import apply_bom, import_any, import_bom

    if len(args) < 2:
        return {"status": "failed",
                "findings": ["import 需要 <id> <path> [--bom <path>]"]}
    project_id, path = args[0], args[1]
    bom_path = None
    if "--bom" in args:
        index = args.index("--bom")
        if index + 1 < len(args):
            bom_path = args[index + 1]
    # `--boards all` 导入全部板；`--boards main`（默认）只导主板；
    # `--boards <doc>` 精确选一块。多板工程的元件不会被静默丢弃。
    boards_mode = "main"
    if "--boards" in args:
        index = args.index("--boards")
        if index + 1 < len(args):
            boards_mode = args[index + 1]

    source = _resolve_input(path)
    if bom_path is not None:
        bom_path = str(_resolve_input(bom_path))

    store = _store(project_id)
    try:
        design = import_any(source, design_id=project_id,
                            boards_mode=boards_mode)
    except FileNotFoundError as exc:
        return {"status": "not_ready",
                "findings": [f"找不到文件：{source}（相对路径按工作区解析）", str(exc)]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "findings": [f"{type(exc).__name__}: {exc}"]}

    unmatched: list[str] = []
    bom_fill_note: dict[str, Any] | None = None
    if bom_path:
        try:
            bom = import_bom(bom_path)
            # 第一步：给**已有**元件贴值/LCSC/封装（真实布局优先）。
            unmatched = apply_bom(design, bom)
            # 第二步：把 BOM 里**PCB 没有的** ref 补进来。
            # 只有一步的话，"空工程 + 完整 BOM"仍然显示 0 个元件 ——
            # 而 `unmatched` 明明已经把缺的 ref 全列出来了。
            #
            # **BOM 补全只在这里做一次**：`import_any` 的 `bom_path` 参数
            # 只用于 KiCad 等"导入器内部会用到 BOM"的格式，
            # 立创路径不传（否则会补两遍，ref 重复）。
            if bom:
                from .design.bom_fill import fill_from_bom
                bom_fill_note = fill_from_bom(design, bom)
        except Exception as exc:  # noqa: BLE001
            design.warnings.append(f"BOM 未应用：{type(exc).__name__}: {exc}")

    from .design.fabrication import copy_source
    try:
        copy_source(design, source, store.dir)
        if bom_path:
            copy_source(design, bom_path, store.dir)
    except Exception as exc:  # noqa: BLE001
        design.warnings.append(f"原始件未复制进 sources/：{exc}")

    store.save(design)
    ledger = discipline.ledger_from_json(store.read("deferred.json"))
    auto_locked = discipline.auto_lock(design, ledger)
    store.write("deferred.json", discipline.ledger_to_json(ledger))
    discipline.append(store, "cli", "design.imported",
                      {"file": str(path), "parts": len(design.parts)})
    return {
        "status": "ok",
        "design": design.to_dict(),
        "summary": design.summary(),
        "problems": design.validate(),
        "warnings": design.warnings,
        "bom_unmatched": unmatched,
        "bom_fill": bom_fill_note,
        "auto_locked": auto_locked,
    }


def cmd_bom(args: list[str]) -> dict[str, Any]:
    """``bom <id> <bom-path> [--dry-run]`` —— 用 BOM 补全**已有**工程的元件。

    为什么需要这条命令：`import` 只能从源文件（`.epro2` / `.kicad_pcb` /
    `.json`）建工程。而真实项目的常见形态是**工程已经有了、但内容不全**
    ——比如 `示例-无人机` 的 `design.json` 里 `parts: []`，而工作区里躺着
    一份 45 个设计号的 BOM。这时 `import` 帮不上，只能"往现有工程里补"。

    `--dry-run` 只报告能补多少，不落盘（先看清再决定）。
    """
    from .design import discipline
    from .design.bom_fill import fill_from_bom
    from .design.importers import apply_bom, import_bom

    if len(args) < 2:
        return {"status": "failed",
                "findings": ["bom 需要 <id> <bom-path> [--dry-run]"]}
    project_id = args[0]
    bom_path = str(_resolve_input(args[1]))
    dry_run = "--dry-run" in args

    store, design = _load(project_id)
    try:
        bom = import_bom(bom_path)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed",
                "findings": [f"读不了 BOM：{type(exc).__name__}: {exc}"]}
    if not bom:
        return {"status": "not_ready",
                "findings": [f"BOM {bom_path} 没读出任何条目（列名不匹配？）"]}

    before = len(design.parts)
    # 先给已有元件贴值/LCSC/封装（真实布局优先，不覆盖坐标）
    unmatched = apply_bom(design, bom)
    note = fill_from_bom(design, bom)
    after = len(design.parts)

    if dry_run:
        return {
            "status": "ok", "dry_run": True,
            "before": before, "would_add": after - before,
            "bom_entries": len(bom),
            "bom_fill": note,
            "bom_matched_existing": sorted(set(bom) - set(unmatched)),
        }

    store.save(design)
    ledger = discipline.ledger_from_json(store.read("deferred.json"))
    auto_locked = discipline.auto_lock(design, ledger)
    discipline.append(store, "cli", "bom.fill",
                      {"before": before, "after": after,
                       "source": str(bom_path)})
    return {
        "status": "ok", "before": before, "after": after,
        "added": after - before,
        "bom_entries": len(bom),
        "bom_fill": note,
        "bom_matched_existing": sorted(set(bom) - set(unmatched)),
        "auto_locked": auto_locked,
    }


def cmd_roles(args: list[str]) -> dict[str, Any]:
    """``roles [--validate] [--goal "<需求>"] [--roles a,b,c]`` —— 需求角色 → 装配计划。

    这一层填的是"需求 → 装配"之间唯一缺失的一格：`spec` 产出**角色**
    （camera / gimbal_pan…），`assemble` 需要**模板**（camera_module…），
    本命令把它们对上，并把对不上的如实报出来。

    三种用法：

    * `roles --validate`：只校验映射表（表里写的模板在 hwforge 里真的存在吗）；
    * `roles --roles camera,gimbal_pan`：给角色，输出装配计划；
    * `roles --goal "壁挂云台相机"`：从自然语言需求出发（需要 hwforge 可导入）。
    """
    from .design import roles as roles_module

    if "--validate" in args and "--roles" not in args and "--goal" not in args:
        check = roles_module.validate()
        return {"status": "ok" if check.ok else "failed",
                **check.to_dict(), "summary": check.summary(),
                "table": roles_module.describe(),
                # 模板表是**本地**的（design/library.py），不再需要 hwforge
                "template_source": "design/library.py"}

    role_list: list[str] = []
    reasons: dict[str, str] = {}
    spec_digest = ""
    unresolved: list[str] = []
    goal = ""
    goal_used = False
    capability_hits: list[dict[str, Any]] = []

    if "--roles" in args:
        index = args.index("--roles")
        if index + 1 < len(args):
            role_list = [r.strip() for r in args[index + 1].split(",") if r.strip()]

    if "--goal" in args:
        index = args.index("--goal")
        if index + 1 >= len(args):
            return {"status": "failed", "findings": ["--goal 需要一个字符串"]}
        goal = " ".join(args[index + 1:]).strip()
        goal_used = True
        # -- 需求分析（**本地能力表**，不依赖 hwforge）--------------------
        #
        # 早先这里跑两条路：hwforge `spec` + 本地能力表。现在只剩本地 ——
        # 能力表认得的说法更多，且不需要外部 checkout。
        #
        # `spec` 独有的软件视图 / 跨域契约 / LookupRequest 目前没有对等
        # 实现。它们不见了，**必须说出来**，而不是让调用方以为还在。
        from .design import requirements as requirements_module
        local = requirements_module.analyse(goal)
        role_list = local.roles or role_list
        capability_hits = [h.to_dict() for h in local.hits]
        unresolved.extend(local.unresolved)
        if not local.ok:
            unresolved.append(
                "需求没有命中任何能力 —— 请用 --roles 直接给角色，"
                "或把说法补进 design/requirements.py 的能力表")

    if not role_list:
        return {"status": "failed",
                "findings": ["需要 --validate、--roles a,b,c 或 --goal \"…\" 之一"]}

    # **角色增强**：`spec` 不产出 `mcu`，这里按需求文本/依赖关系补。
    # 不补的话"相机+云台+电源"没有主控，信号脚注定悬空。
    enhancement = roles_module.enhance(role_list, goal if goal_used else "")
    role_list = enhancement.roles if enhancement.added else role_list

    check = roles_module.validate(role_list)
    plan = roles_module.plan(role_list, reasons=reasons)

    # `--assemble <id>`：把计划**装成设计**并落盘（R3）。
    # 不给就只出计划 —— 计划和设计是两件事，默认不写盘。
    if "--assemble" in args:
        index = args.index("--assemble")
        if index + 1 >= len(args):
            return {"status": "failed", "findings": ["--assemble 需要一个工程 id"]}
        project_id = args[index + 1]
        from .design import assemble_blocks as assemble_module
        from .design import discipline

        title = ""
        if "--title" in args:
            title = " ".join(args[args.index("--title") + 1:]).strip()
        store = _store(project_id)
        result = assemble_module.assemble_plan(
            plan, design_id=project_id, title=title or project_id)
        if result.design is None:
            return {"status": "failed", **result.to_dict(),
                    "findings": result.problems}
        store.save(result.design)
        ledger = discipline.ledger_from_json(store.read("deferred.json"))
        auto_locked = discipline.auto_lock(result.design, ledger)
        discipline.append(store, "cli", "roles.assemble",
                          {"project": project_id,
                           "parts": len(result.design.parts),
                           "nets": len(result.design.nets)})
        return {"status": "ok" if result.ok else "not_ready",
                **result.to_dict(),
                "project": project_id,
                "auto_locked": auto_locked,
                "role_check": check.to_dict(),
                "role_enhancement": enhancement.to_dict(),
                "capability_hits": capability_hits,
                "unresolved": unresolved}

    status = "ok" if check.ok and not plan.unmatched else "not_ready"
    out: dict[str, Any] = {
        "status": status,
        "roles": role_list,
        "plan": plan.to_dict(),
        "summary": plan.summary(),
        "role_check": check.to_dict(),
        "role_enhancement": enhancement.to_dict(),
        "capability_hits": capability_hits,
        "unresolved": unresolved,
    }
    if spec_digest:
        out["spec_digest"] = spec_digest
    if plan.unmatched:
        out["findings"] = [
            f"角色没有映射：{'、'.join(plan.unmatched)} —— "
            f"这些需求会被丢掉，先在 design/roles.py 里补映射"]
    return out


def cmd_firmware(args: list[str]) -> dict[str, Any]:
    """``firmware <源码目录> [--scope uncalled,timeout,...] [--vendor]``

    固件源码的**文本/结构**检查（6 条）：未调用函数、失联处理、配置表
    覆盖率、BOM 一致性、重复副本、调试残留。

    这些检查**不生成代码、不改写文件**；每条 finding 都写明"看不见什么"。
    """
    from .design import firmware as firmware_module
    from .design import importers as importers_module

    if not args or args[0].startswith("-"):
        return {"status": "failed",
                "findings": ["需要源码目录；用 --scope 选检查项（默认全跑）"],
                "available": list(firmware_module.ALL_CHECKS)}

    root = args[0]
    scope: list[str] | None = None
    if "--scope" in args:
        index = args.index("--scope")
        if index + 1 < len(args):
            scope = [s.strip() for s in args[index + 1].split(",") if s.strip()]
    include_vendor = "--vendor" in args

    bom_rows: list[dict[str, Any]] = []
    if "--bom" in args:
        index = args.index("--bom")
        if index + 1 < len(args):
            try:
                from .design.bom import import_bom
                bom = import_bom(args[index + 1])
                bom_rows = [row.to_dict() if hasattr(row, "to_dict") else dict(row)
                            for row in bom]
            except Exception as exc:  # noqa: BLE001
                return {"status": "failed",
                        "findings": [f"读不到 BOM：{type(exc).__name__}: {exc}"]}

    resolved = _resolve_input(root)
    report = firmware_module.run(resolved, scope=scope,
                                 include_vendor=include_vendor,
                                 bom_rows=bom_rows)
    return {"status": "ok" if report.ok else "failed", **report.to_dict()}


def cmd_semantics(args: list[str]) -> dict[str, Any]:
    """``semantics [--validate] [<template>]`` —— 语义树（元件内部结构）。

    语义树是**判定链的解释层**，不是另一个判定器。它唯一的目的：
    让 `coverify` 的失败项能说出"这个硬件下限由内部哪个子系统决定、
    换什么能解决、**什么层面修不好**"。

    * 不带参数：列覆盖率（哪些模板有树、哪些如实标未建模）；
    * `--validate`：校验映射表；
    * 给模板名：输出那棵树的 digest。
    """
    from .design import semantics as semantics_module

    if "--validate" in args:
        check = semantics_module.validate()
        return {"status": "ok" if check.ok else "failed",
                **check.to_dict(), "summary": check.summary(),
                "coverage": semantics_module.coverage()}

    if not args or args[0].startswith("-"):
        return {"status": "ok", "coverage": semantics_module.coverage(),
                "trees": semantics_module.describe()}

    template = args[0]
    tree = semantics_module.tree_for_template(template)
    if tree is None:
        return {"status": "not_ready",
                "findings": [
                    f"模板 {template!r} 没有语义树。",
                    "**未建模不等于该件简单，是未考证** —— "
                    f"已建：{sorted(semantics_module.TREES)}",
                ],
                "coverage": semantics_module.coverage()}
    return {"status": "ok", "template": template,
            "tree": tree.to_dict(), "digest": tree.digest()}


def cmd_tiers(args: list[str]) -> dict[str, Any]:
    """``tiers [<id>]`` —— hwforge 的 21 级阶梯 + 这次运行到哪一级。

    不带 id：只看阶梯本身（每级"能证明什么、有没有检查、需不需要人"）。
    带 id：跑一次该工程的检查，把结果映射到阶梯上 —— **哪些层验过、
    哪些有检查但没跑、哪些根本没有工具、哪些需要人**，四者分开。
    """
    from .design import tiers as tiers_module

    table = tiers_module.describe()
    if not table:
        return {"status": "not_ready",
                "findings": ["hwforge 不可用（HWFORGE_ROOT 未配置或未 clone）："
                             "阶梯表取不到"],
                "tiers": []}
    if not args or args[0].startswith("-"):
        return {"status": "ok", "tiers": table,
                "counts": {"total": len(table),
                           "built": sum(1 for t in table if t.get("status") == "built"),
                           "planned": sum(1 for t in table if t.get("status") == "planned"),
                           "manual": sum(1 for t in table if t.get("status") == "manual")}}

    project_id = args[0]
    store = _store(project_id)
    if not store.exists():
        return {"status": "not_ready", "findings": [f"工程不存在：{project_id}"]}
    design = store.load()
    from .design import budgets, coverify as coverify_module, physical
    try:
        checks = physical.run_checks(design)
    except RuntimeError as exc:
        checks = {"ok": None, "findings": [], "error": str(exc)}
    try:
        co = coverify_module.run(design)
        co_payload = {**co.to_dict(), "summary": coverify_module.summarise(co)}
    except Exception as exc:  # noqa: BLE001
        co_payload = {"ok": None, "findings": [], "not_verified": [str(exc)]}
    try:
        budget = budgets.evaluate(design)
    except RuntimeError as exc:
        budget = {"ok": None, "error": str(exc)}
    report = tiers_module.report(checks=checks, coverify=co_payload,
                                 budget=budget)
    return {"status": "ok" if report.ok else "failed",
            **report.to_dict(), "counts": report.counts(),
            "summary": report.summary(), "project": project_id}


def _judge_status(payload: dict[str, Any]) -> str:
    """判定类命令的顶层 `status` —— **从 payload 自己的结论推**，不要写死。

    ## 为什么（P47 真机实测）

    `cmd_check` / `cmd_ladder` 原来是 `return {"status": "ok", **result}`。
    于是同一份 payload 里出现 **`status="ok"` 而 `ok=false / failed=2`**：
    人读顶层、工具读顶层，全都以为过了。`payload` 自带的结论被这层包装
    覆盖掉（写死的 ok 在前，payload 的键在后，但 payload 里**没有**
    `status` 这个键，所以写死的那个活下来了）。

    这是 §1.1（判定只有一份）在**命令包装层**的复刻。
    """
    if str(payload.get("status") or ""):
        return str(payload["status"])
    # **阶梯要按层看**：`ok=False` 可能只是"某层 unknown"（判不出来），
    # 那不是 `failed`（判出来是错的）。两者对下游的意义完全不同。
    levels = [l for l in (payload.get("levels") or []) if isinstance(l, dict)]
    if levels:
        if any(str(l.get("status")) == "fail" for l in levels):
            return "failed"
        if any(str(l.get("status")) in ("unknown", "not_run") for l in levels):
            return "not_ready"
        return "ok"
    # **`summary` 不一定是对象**：`handoff` 的 summary 是一句话（str）。
    # 第一版直接 `.get()` → `AttributeError: 'str' object has no attribute 'get'`
    # （实测：`handoff 重建-最小系统板` 直接 failed）。
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    if int(summary.get("errors") or 0) > 0:
        return "failed"
    if payload.get("ok") is False:
        return "failed"
    if int(summary.get("failed") or 0) > 0 or int(summary.get("warnings") or 0) > 0:
        return "not_ready"
    if payload.get("vacuous"):
        return "not_ready"
    if payload.get("ok") is None:
        return "not_ready"
    return "ok"


def cmd_check(args: list[str]) -> dict[str, Any]:
    from .design import discipline, physical

    if not args:
        return {"status": "failed", "findings": ["check 需要 <id>"]}
    pitch = 0.5
    if "--pitch" in args:
        try:
            pitch = float(args[args.index("--pitch") + 1])
        except (IndexError, ValueError):
            return {"status": "failed", "findings": ["--pitch 需要一个数字"]}
    store, design = _load(args[0])
    try:
        result = physical.run_checks(design, pitch_mm=pitch)
    except RuntimeError as exc:
        return {"status": "not_ready", "findings": [str(exc)]}
    store.write("checks.json", result)
    discipline.append(store, "cli", "checks.run",
                      {"ok": result["ok"], "failed": result["summary"]["failed"]})
    return {"status": _judge_status(result), **result}


def cmd_ladder(args: list[str]) -> dict[str, Any]:
    """``ladder <id> [--levels V0,V1] [--no-truncate] [--pitch 0.5]``。

    V0→V6 阶梯：便宜的先跑，V0/V1 硬失败就截断下游（记 `not_run`，
    **不是 pass**）。不带 id 时只输出阶梯说明。
    """
    from .design import ladder as ladder_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **ladder_module.describe()}

    levels: list[str] | None = None
    if "--with-sim" in args:
        # **默认不跑 V7**：实测 V7 进默认序列会让 266 项单测从 1.4s 变 394s
        # （几乎每个测试都 run_ladder，而 V7 每次要跑 3 次扰动）。
        # 要仿真就得说要——这是"成本可见"。
        levels = list(ladder_module.DEFAULT_LEVELS) + list(
            ladder_module.SIM_LEVELS)
    if "--levels" in args:
        try:
            raw = args[args.index("--levels") + 1]
        except IndexError:
            return {"status": "failed", "findings": ["--levels 需要一个列表"]}
        levels = [x.strip() for x in raw.split(",") if x.strip()]
    pitch = 0.5
    if "--pitch" in args:
        try:
            pitch = float(args[args.index("--pitch") + 1])
        except (IndexError, ValueError):
            return {"status": "failed", "findings": ["--pitch 需要一个数字"]}
    inputs = None
    if "--inputs" in args:
        try:
            inputs = json.loads(args[args.index("--inputs") + 1])
        except (IndexError, ValueError) as exc:
            return {"status": "failed",
                    "findings": [f"--inputs 需要一个 JSON：{exc}"]}

    store, design = _load(args[0])
    try:
        result = ladder_module.run_ladder(
            design, levels=levels,
            stop_on_hard="--no-truncate" not in args,
            pitch_mm=pitch, inputs=inputs)
    except ValueError as exc:
        return {"status": "failed", "findings": [str(exc)]}

    payload = result.to_dict()
    store.write("ladder.json", payload)
    return {"status": _judge_status(payload), **payload}


def cmd_claims(args: list[str]) -> dict[str, Any]:
    """``claims <id> [--evaluate] [--priority P0,P1]``。

    从设计**推导**断言（规则生成，不由 AI 写），然后求值。
    `--evaluate` 只求值不重写 `claims.json`（用于对比）。
    """
    from .design import claims as claims_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **claims_module.describe()}

    priorities: set[str] | None = None
    if "--priority" in args:
        try:
            raw = args[args.index("--priority") + 1]
        except IndexError:
            return {"status": "failed", "findings": ["--priority 需要一个列表"]}
        priorities = {x.strip().upper() for x in raw.split(",") if x.strip()}

    store, design = _load(args[0])
    items = claims_module.derive(design)
    if priorities is not None:
        items = [c for c in items if c.priority in priorities]

    report = claims_module.check(design, items)
    payload = report.to_dict()
    payload["derived"] = len(items)
    if "--evaluate" not in args:
        store.write("claims.json", payload)
    return {"status": "ok", **payload}


def cmd_assert(args: list[str]) -> dict[str, Any]:
    """``assert <id> --remove <REF>``：**删除即失败**的验证入口。

    把 REF 从内存副本里剔除后重新求值断言，报出哪些断言因此变红。
    原设计一个字节都不改。`reddened` 为空说明断言机制没建对。
    """
    from .design import claims as claims_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **claims_module.describe()}
    if "--remove" not in args:
        return {"status": "not_ready",
                "findings": ["需要 --remove <REF>：删掉哪个元件"]}
    try:
        ref = args[args.index("--remove") + 1]
    except IndexError:
        return {"status": "failed", "findings": ["--remove 需要一个元件号"]}

    store, design = _load(args[0])
    impact = claims_module.removal_impact(design, ref)
    if impact is None:
        return {"status": "not_ready",
                "findings": [f"工程 {args[0]!r} 里没有元件 {ref}"]}

    basis_report = claims_module.check(design)
    impact["basis"] = {
        "claims": basis_report.counts(),
        "note": "移除前的基线；reddened 是相对它从 pass 掉到 fail 的那些",
    }
    return {"status": "ok", **impact}


def cmd_justify(args: list[str]) -> dict[str, Any]:
    """``justify <id> [--ref R1] [--write] [--json]`` —— 归因反推。

    从网表拓扑反推每个元件**为什么在板上**。判不出时返回 `unknown`
    并说清为什么判不出（哈希 kind？没有网表？），不编理由。

    `--write` 把结论写进 `design.json` 的 `part.justification`
    （默认不写：推导结果混进规范模型后，分不清哪条是人说的）。
    """
    from .design import justify as justify_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **justify_module.describe()}

    store, design = _load(args[0])
    wanted: str | None = None
    if "--ref" in args:
        try:
            wanted = args[args.index("--ref") + 1]
        except IndexError:
            return {"status": "failed", "findings": ["--ref 需要一个元件号"]}

    report = justify_module.run(design)
    written = False
    if "--write" in args:
        justify_module.run(design, write_back=True)
        store.save(design)
        written = True

    if wanted is not None:
        rows = [r for r in report["parts"]
                if str(r.get("ref") or "") == wanted]
        if not rows:
            return {"status": "not_ready",
                    "findings": [f"工程 {args[0]!r} 里没有元件 {wanted}"]}
        report = {**report, "parts": rows, "filtered": wanted}

    store.write("justify.json", report)
    return {"status": "ok", "written": written, **report}


def cmd_handoff(args: list[str]) -> dict[str, Any]:
    """``handoff <id> [--write]`` —— 交接层：一份「必须确认才能打板」的清单。

    聚合散在 4 处的"未验证"（阶梯的 `not_run`/`unknown`、断言的
    `unknown`、预算的缺数据、协同验证的默认值），输出 4 张表 +
    `handoff.md`（人读）/ `handoff.json`（机读）。

    **这个命令不重新跑判定**——它读已有产物。理由：判定很贵
    （V2 要跑几何体素），而且交接清单必须**对着某一版产物**，
    而不是每次看都重算出一个新版本。
    """
    from .design import handoff as handoff_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **handoff_module.describe()}

    store = _store(args[0])
    if not store.exists():
        return {"status": "not_ready",
                "findings": [f"工程 {args[0]!r} 不存在（没有 design.json）"]}

    try:
        result = handoff_module.build(store)
    except ValueError as exc:
        return {"status": "failed", "findings": [str(exc)]}

    payload = result.to_dict()
    store.write("handoff.json", payload)
    markdown = handoff_module.render_markdown(payload)
    store.write_text("handoff.md", markdown)
    payload["markdown"] = markdown
    return {"status": _judge_status(payload), **payload}


def cmd_renode(args: list[str]) -> dict[str, Any]:
    """``renode [status|<id>] [--check-cpu] [--write]`` —— Renode 接入。

    不给参数或 `status` 时只探测（Renode / ARM 工具链 / 能否加载 CPU 平台）。
    给工程名时生成 `.resc` 与 `run.cmd`。

    **本机实测无法在 DSH 内加载 CPU 平台**（Renode 要独占打开锁文件，
    沙箱拒绝）。所以这个命令的价值是**产出可外部执行的完整脚本**，
    而不是假装能跑。`runnable_here` 字段如实报告。
    """
    from .design import renode as renode_module

    if not args or args[0] == "status":
        result = renode_module.probe(check_cpu="--check-cpu" in args)
        return {"status": "ok", **result.to_dict(),
                "describe": renode_module.describe()}

    store, design = _load(args[0])

    firmware: dict[str, str] = {}
    if "--firmware" in args:
        try:
            raw = args[args.index("--firmware") + 1]
        except IndexError:
            return {"status": "failed", "findings": ["--firmware 需要 JSON"]}
        try:
            firmware = {str(k): str(v) for k, v in json.loads(raw).items()}
        except ValueError as exc:
            return {"status": "failed",
                    "findings": [f"--firmware 需要 JSON 对象：{exc}"]}

    ioc_root = None
    if "--ioc-root" in args:
        try:
            ioc_root = args[args.index("--ioc-root") + 1]
        except IndexError:
            return {"status": "failed",
                    "findings": ["--ioc-root 需要目录"]}

    explicit: dict[str, str] = {}
    if "--board-mcu" in args:
        try:
            raw = args[args.index("--board-mcu") + 1]
            explicit = {str(k): str(v) for k, v in json.loads(raw).items()}
        except (IndexError, ValueError) as exc:
            return {"status": "failed",
                    "findings": [f"--board-mcu 需要 JSON 对象：{exc}"]}

    report = renode_module.report(
        design, firmware=firmware, check_cpu="--check-cpu" in args,
        ioc_root=ioc_root, explicit_mcu=explicit)
    report["project"] = args[0]
    store.write("renode.json", report)

    written: list[str] = []
    if "--write" in args:
        store.write_text(f"{design.id}.resc", report["resc"]["script"])
        written.append(f"{design.id}.resc")
        for name, body in (report.get("runner") or {}).items():
            store.write_text(name, body)
            written.append(name)
    report["written"] = written

    status = "ok" if report["ok"] else "not_ready"
    return {"status": status, **report}


def cmd_ioc_links(args: list[str]) -> dict[str, Any]:
    """``ioc-links --root <dir> [--apply <id>] [--json]`` —— 从 .ioc 抽板间链路。

    默认**只读**：扫描 `--root` 下所有 `.ioc`，配出链路草稿并打印。
    `--apply <工程>` 时才把 `source=declared` 的写进那个工程的
    `links_list`（推断的一条都不写——推断会错，不能判人 fail）。
    """
    from .design import ioc_links as ioc_module

    if not args:
        return {"status": "ok", **ioc_module.describe()}

    if "--root" not in args:
        return {"status": "failed",
                "findings": ["需要 --root <目录>：从哪里找 .ioc"]}
    try:
        root = args[args.index("--root") + 1]
    except IndexError:
        return {"status": "failed", "findings": ["--root 需要目录路径"]}

    report = ioc_module.extract(root)

    report["review"] = ioc_module.review_list(
        ioc_module.Draft(links=report["draft"]["links"]))

    applied = 0
    if "--apply" in args:
        try:
            project = args[args.index("--apply") + 1]
        except IndexError:
            return {"status": "failed", "findings": ["--apply 需要工程名"]}
        store, design = _load(project)
        draft = ioc_module.Draft(
            links=report["draft"]["links"],
            unpaired=report["draft"]["unpaired"],
            boards=report["draft"]["boards"],
            counts=report["counts"])
        # `--promote` 才提升为 declared；不给就只写本来已 declared 的
        if "--promote" in args:
            try:
                raw = args[args.index("--promote") + 1]
                ids = None if raw == "all" else [x.strip() for x in raw.split(",") if x.strip()]
            except IndexError:
                ids = None
            report["promoted"] = ioc_module.promote(draft, ids)
        applied = ioc_module.apply_draft(design, draft, only_declared=True)
        if applied:
            store.save(design)
        report["applied"] = applied
        report["applied_project"] = project
        if applied == 0:
            report["apply_note"] = (
                "没有写入：全部链路都是 inferred。"
                "要落地必须先由人确认（改 source 为 declared，"
                "或显式调用 apply_draft(only_declared=False)）")

    return {"status": report.get("status", "not_ready"), **report}


def cmd_capability(args: list[str]) -> dict[str, Any]:
    """``capability [--needs a,b] [--role mcu] [--area 100] [--exclude x]``

    能力窄表查询。给 needs 时返回候选型号；否则只返回表的自省。
    """
    from .design import capability as capability_module

    if not args or (args[0].startswith("-") and "--needs" not in args
                    and "--role" not in args):
        return {"status": "ok", **capability_module.describe()}

    needs: set[str] = set()
    if "--needs" in args:
        try:
            raw = args[args.index("--needs") + 1]
        except IndexError:
            return {"status": "failed", "findings": ["--needs 需要值"]}
        needs = {part.strip() for part in raw.split(",") if part.strip()}

    role: str | None = None
    if "--role" in args:
        try:
            role = args[args.index("--role") + 1]
        except IndexError:
            return {"status": "failed", "findings": ["--role 需要值"]}

    constraints: dict[str, Any] = {}
    if "--area" in args:
        try:
            constraints["area_mm2"] = float(args[args.index("--area") + 1])
        except (IndexError, ValueError):
            return {"status": "failed", "findings": ["--area 需要数字"]}

    exclude: set[str] = set()
    if "--exclude" in args:
        try:
            exclude = {x.strip() for x in
                       args[args.index("--exclude") + 1].split(",") if x.strip()}
        except IndexError:
            return {"status": "failed", "findings": ["--exclude 需要值"]}

    found = capability_module.candidates(needs, constraints, exclude, role=role)
    if not found:
        return {"status": "not_ready",
                "candidates": [], **(capability_module.explain_miss(
                    needs, constraints, exclude, role=role))}
    return {"status": "ok", "candidates": found,
            "count": len(found), "needs": sorted(needs),
            "role": role, "constraints": constraints}


def cmd_graph(args: list[str]) -> dict[str, Any]:
    """``graph <id> [--impact=U1,net:3V3,block:MCU1] [--write]`` —— 变更图（B4）。

    派生图（零件/网/块/固件四种节点）；`--impact` 沿边双向走两跳，列出受影响
    的零件/网/块/固件引脚。**目标不存在 → not_ready**（不是"没有影响"）。
    """
    from .design import graph as graph_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **graph_module.describe()}
    store, design = _load(args[0])
    targets: list[str] = []
    for item in args[1:]:
        if item.startswith("--impact="):
            targets = [chunk.strip() for chunk in item.split("=", 1)[1].split(",")
                       if chunk.strip()]
    if targets:
        return {"project_id": args[0], **graph_module.impact(design, targets)}
    payload = graph_module.build(design)
    if "--write" in args:
        store.write(graph_module.FILE, payload)
    return {"project_id": args[0], **payload}


def cmd_pin_assign(args: list[str]) -> dict[str, Any]:
    """``pin-assign <id> [--mcu=stm32f103c8t6] [--need=USART1_TX,...] [--avoid=PA9]``
    —— 引脚分配器（B5）。

    候选来自 AF 表（功能名完全匹配），按候选数升序贪心，冲突换组；
    分不出来进 `unassigned`，**不硬塞**。没给 `--need` 时从 spec 的接口推。
    """
    from .design import afmap as afmap_module
    from .design import pin_assign as pin_module
    from .design import spec as spec_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **pin_module.describe()}
    store, design = _load(args[0])
    model = ""
    needs: list[str] = []
    avoid: set[str] = set()
    for item in args[1:]:
        if item.startswith("--mcu="):
            model = item.split("=", 1)[1]
        elif item.startswith("--need="):
            needs = [chunk.strip() for chunk in item.split("=", 1)[1].split(",")
                     if chunk.strip()]
        elif item.startswith("--avoid="):
            avoid = {chunk.strip().upper() for chunk in
                     item.split("=", 1)[1].split(",") if chunk.strip()}
    if not model:
        model = str((design.firmware or {}).get("mcu") or "")
    if not model:
        mcu = next((part for part in design.parts
                    if str(part.kind).lower() in {t.lower() for t in afmap_module.list_tables()}),
                   None)
        model = str(mcu.kind) if mcu else ""
    if not model:
        return {"status": "not_ready", "project_id": args[0],
                "reason": "没给 --mcu，设计里也没有 AF 表覆盖的 MCU",
                "tables": afmap_module.list_tables()}
    if not afmap_module.find(model):
        return {"status": "not_ready", "project_id": args[0],
                "reason": f"AF 表里没有 {model}", "tables": afmap_module.list_tables()}
    used: set[str] = set()
    results: list[dict[str, Any]] = []
    if needs:
        got = pin_module.assign(model, needs, avoid=avoid, used=used)
        payload = {**got, "model": model, "mode": "explicit"}
    else:
        spec = spec_module.load_latest(store.dir)
        protocols = pin_module.derive_needs(spec)
        if not protocols:
            return {"status": "not_ready", "project_id": args[0],
                    "reason": ("没有 spec 接口，也没给 --need —— 不知道要分什么脚"),
                    "model": model}
        for protocol in protocols:
            one = pin_module.assign_protocol(model, protocol, avoid=avoid, used=used)
            if one["status"] == "ok":
                used |= set(one["assignment"].values())
            results.append(one)
        assignment = {key: value for row in results
                      for key, value in (row.get("assignment") or {}).items()}
        unassigned = [item for row in results for item in (row.get("unassigned") or [])]
        payload = {"status": ("ok" if not unassigned else
                              "partial" if assignment else "not_ready"),
                   "assignment": assignment, "unassigned": unassigned,
                   "protocols": results, "used": sorted(used),
                   "model": model, "mode": "from-spec"}
    if "--write" in args and payload.get("assignment"):
        store.write("pin_assign.json", payload)
    return {"project_id": args[0], **payload}


def cmd_assemble(args: list[str]) -> dict[str, Any]:
    """``assemble <id> [--req=PATH] [--board=60x40] [--write]`` —— 装配（A4）。

    需求 → `spec.build` → 块计划 → `blocks.compose` → 设计。块库没有的角色/
    接口进 `uncovered`，**不造占位件**；装配后必须过 `validate()`。
    `--write` 只允许写进**空工程**（不覆盖已有元件）。
    """
    from .design import assemble_blocks as assemble_module
    from .design import generate as generate_module
    from .design import ladder as ladder_module
    from .design import spec as spec_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **assemble_module.describe()}
    store, design = _load(args[0])
    req_path = ""
    width, height = 60.0, 40.0
    write = False
    for item in args[1:]:
        if item.startswith("--req="):
            req_path = item.split("=", 1)[1]
        elif item.startswith("--board=") and "x" in item:
            try:
                w, h = item.split("=", 1)[1].split("x", 1)
                width, height = float(w), float(h)
            except ValueError:
                pass
        elif item == "--write":
            write = True
    raw = None
    if req_path:
        path = Path(req_path)
        if not path.is_absolute():
            path = store.dir / path
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = store.read("req.json")
    if not isinstance(raw, dict):
        return {"status": "not_ready", "project_id": args[0],
                "reason": "没有 req.json（先 req-draft，或给 --req=PATH）"}
    fields = {"id", "title", "roles", "interfaces", "constraints", "numbers", "notes"}
    req = generate_module.Requirement(**{k: v for k, v in raw.items() if k in fields})
    result = assemble_module.assemble(req, board_size=(width, height))
    if result["status"] == "not_ready":
        return {"status": "not_ready", "project_id": args[0],
                "reason": result.get("reason"), "uncovered": result.get("uncovered")}
    built = result["design"]
    if write:
        if design.parts:
            return {"status": "failed", "project_id": args[0],
                    "reason": ("工程里已有元件 —— `assemble --write` 只写空工程，"
                               "要重来请先 new 一个空工程"),
                    "counts": result["counts"]}
        if built.parts:
            # **整份替换**（含板框）：装配用的是自己的板 id，只换零件/网表
            # 会让 `placement.board_id` 指向不存在的板（实测报满屏）。
            design.board = built.board
            design.boards = built.boards
            design.parts = built.parts
            design.nets = built.nets
            # B3：块实例表也要跟着写（否则 graph 看不到块边界）。
            design.blocks_list = [dict(row) for row in built.blocks_list]
            problems = design.validate()
            if problems:
                return {"status": "failed", "project_id": args[0],
                        "problems": problems[:8]}
            store.save(design)
    v0 = ladder_module.level_v0(built)
    v1 = ladder_module.level_v1(built)
    return {"status": result["status"], "project_id": args[0],
            "counts": result["counts"], "uncovered": result["uncovered"],
            "plan": result["plan"]["plan"],
            "ladder": {"V0": v0.status, "V1": v1.status},
            "written": bool(write), "note": result["note"]}


def cmd_constraints(args: list[str]) -> dict[str, Any]:
    """``constraints <id> [--derive] [--write]`` —— 约束账本（A5）。

    从 `spec` 的接口契约 + 网表（class/block 规则）+ 需求数值派生约束，再按
    真设计求值：`pass` / `fail` / `unknown`。缺数据是 `unknown`，**不是通过**。
    """
    from .design import constraints as constraints_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **constraints_module.describe()}
    store, design = _load(args[0])
    existing = store.read(constraints_module.FILE)
    if "--derive" not in args:
        if not isinstance(existing, dict):
            return {"status": "not_ready", "project_id": args[0],
                    "reason": "还没有 constraints.json（先跑 constraints <id> --derive）"}
        return {"status": existing.get("status", "not_ready"), "project_id": args[0],
                **existing}

    from .design import generate as generate_module
    from .design import spec as spec_module

    spec = spec_module.load_latest(store.dir)
    raw = store.read("req.json")
    req = None
    if isinstance(raw, dict):
        fields = {"id", "title", "roles", "interfaces", "constraints",
                  "numbers", "notes"}
        try:
            req = generate_module.Requirement(
                **{k: v for k, v in raw.items() if k in fields})
        except TypeError:
            req = None
    payload = constraints_module.derive(design, spec=spec, req=req)
    if "--write" in args:
        store.write(constraints_module.FILE, payload)
    return {"status": payload["status"], "project_id": args[0], **payload,
            "written": "--write" in args}


def cmd_blocks(args: list[str]) -> dict[str, Any]:
    """``blocks [list|show <id>|validate|compose <id> --plan=INST:BLOCK,... [--write]]``
    —— 块库（A3）。

    一个块 = 网表片段 + 参数 + 验证记录。实例化时 ref 自动分配（跳过已用），
    全局网（GND/3V3/5V…）跨块合并，局部网加实例前缀。`verified_by` 为空
    单独记 `unverified`，**不算通过**。
    """
    from .design import blocks as blocks_module

    if not args or args[0] == "list":
        library = blocks_module.load_all()
        rows = []
        for block_id, block in library.items():
            rows.append({"id": block_id, "title": block.get("title"),
                         "family": block.get("family"),
                         "parts": len(block.get("parts") or []),
                         "nets": len(block.get("nets") or []),
                         "verified": bool(block.get("verified_by") or [])})
        verified = [row for row in rows if row["verified"]]
        return {"status": "ok" if rows else "not_ready", "blocks": rows,
                "counts": {"blocks": len(rows), "verified": len(verified),
                           "unverified": len(rows) - len(verified)},
                "note": "unverified 不算通过；块来自参考电路，需真板验证后才进 verified"}

    action = args[0]
    if action == "show":
        if len(args) < 2:
            return {"status": "not_ready", "reason": "给块 id：blocks show <id>"}
        block = blocks_module.get(args[1])
        if block is None:
            return {"status": "not_ready", "reason": f"块库没有 {args[1]}"}
        return {"status": "ok", **block}

    if action == "validate":
        library = blocks_module.load_all()
        rows = [{"id": block_id, "problems": blocks_module.validate(block)}
                for block_id, block in library.items()]
        bad = [row for row in rows if row["problems"]]
        return {"status": "ok" if rows and not bad else "failed",
                "rows": rows, "counts": {"blocks": len(rows), "bad": len(bad)}}

    if action == "compose":
        if len(args) < 2:
            return {"status": "not_ready", "reason": "给工程 id：blocks compose <id> --plan=..."}
        store, design = _load(args[1])
        plan: list[dict[str, str]] = []
        write = False
        for item in args[2:]:
            if item.startswith("--plan="):
                for chunk in item.split("=", 1)[1].split(","):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    if ":" in chunk:
                        instance, block_id = chunk.split(":", 1)
                    else:
                        instance, block_id = chunk, chunk
                    plan.append({"instance": instance.strip(), "block": block_id.strip()})
            if item == "--write":
                write = True
        if not plan:
            return {"status": "not_ready", "reason": "缺 --plan=INST:BLOCK,..."}
        board_id = design.boards[0].id if design.boards else design.board.id
        first = design.boards[0] if design.boards else design.board
        width = float((getattr(first, "size_mm", {}) or {}).get("w") or 0.0)
        result = blocks_module.compose(plan, board_id=board_id, max_x_mm=width)
        if not write or result["status"] != "ok":
            return {"status": result["status"], "project_id": args[1], **result}
        merged = blocks_module.merge_into(design, result["parts"], result["nets"])
        if not merged.get("ok"):
            return {"status": "failed", "reason": merged.get("reason"),
                    "project_id": args[1]}
        problems = design.validate()
        if problems:
            return {"status": "failed", "reason": "合并后设计校验不过",
                    "problems": problems[:8], "project_id": args[1]}
        store.save(design)
        return {"status": "ok", "project_id": args[1],
                "counts": {**result["counts"], "parts_in_design": len(design.parts),
                           "nets_in_design": len(design.nets)},
                "merged": merged, "blocks": result["blocks"],
                "note": result["note"]}

    return {"status": "not_ready", "reason": f"不认识的动作 {action}"}


def cmd_spec(args: list[str]) -> dict[str, Any]:
    """``spec <id> [--build] [--req=PATH]`` —— 规格层（A1）。

    需求 → **功能/子系统/接口契约**（`spec/vN.json` + `spec.md`），在选型与
    排脚之前。`--build` 同需求复用同一版（`requirement_hash`），需求变了才
    `v(N+1)`；缺模板进 `missing`（占位块标 `proposed`），缺的数写 null 进
    `unknown` —— **不填默认值**。
    """
    from .design import generate as generate_module
    from .design import spec as spec_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **spec_module.describe()}
    store, design = _load(args[0])
    latest = spec_module.load_latest(store.dir)
    if "--build" not in args:
        if latest is None:
            return {"status": "not_ready", "project_id": args[0],
                    "reason": "还没有 spec（先跑 spec <id> --build）"}
        return {"status": latest.get("status", "ok"), "project_id": args[0], **latest}

    req_path = ""
    for item in args[1:]:
        if item.startswith("--req="):
            req_path = item.split("=", 1)[1]
    raw = None
    if req_path:
        path = Path(req_path)
        if not path.is_absolute():
            path = store.dir / path
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = store.read("req.json")
    if not isinstance(raw, dict):
        return {"status": "not_ready", "project_id": args[0],
                "reason": "没有 req.json（先 req-draft，或给 --req=PATH）"}
    fields = {"id", "title", "roles", "interfaces", "constraints", "numbers", "notes"}
    req = generate_module.Requirement(**{k: v for k, v in raw.items() if k in fields})
    spec = spec_module.build(req)
    root = store.path(spec_module.DIR)
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / spec_module.INDEX
    meta = {}
    if index_path.is_file():
        try:
            meta = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
    versions = [row for row in (meta.get("versions") or [])
                if isinstance(row, dict) and row.get("version")]
    same = next((row for row in versions
                 if row.get("requirement_hash") == spec["requirement_hash"]), None)
    if same:
        version = int(same["version"])
    else:
        version = max([int(row["version"]) for row in versions] or [0]) + 1
    name = f"v{version}"
    spec = {**spec, "version": version}
    (root / f"{name}.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    store.write_text("spec.md", spec_module.render(spec))
    row = {"version": version, "file": f"{name}.json",
           "requirement_hash": spec["requirement_hash"],
           "status": spec["status"],
           "blocks": len(spec["blocks"]),
           "interfaces": len(spec["interfaces"]),
           "created": spec["retrieved"]}
    versions = [r for r in versions if int(r["version"]) != version] + [row]
    versions.sort(key=lambda r: int(r["version"]))
    index_path.write_text(json.dumps(
        {"latest": name, "versions": versions}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return {"status": spec["status"], "project_id": args[0],
            "version": version, "file": str(root / f"{name}.json"), **spec}


def cmd_generate(args: list[str]) -> dict[str, Any]:
    """``generate --requirement <file> [--max-candidates N] [--fast]``

    按需求有限枚举候选。**不是收敛优化**：枚举有硬上限，天然终止。

    `--fast` 跳过验证阶梯（只出候选清单），用于快速看枚举结果。
    """
    from .design import generate as generate_module

    if not args:
        return {"status": "ok", **generate_module.describe()}

    if "--requirement" not in args:
        return {"status": "failed",
                "findings": ["需要 --requirement <req.yaml|req.json>"]}
    try:
        path = args[args.index("--requirement") + 1]
    except IndexError:
        return {"status": "failed", "findings": ["--requirement 需要文件路径"]}

    budget = 50
    if "--max-candidates" in args:
        try:
            budget = int(args[args.index("--max-candidates") + 1])
        except (IndexError, ValueError):
            return {"status": "failed",
                    "findings": ["--max-candidates 需要整数"]}
    if budget <= 0:
        return {"status": "failed",
                "findings": ["--max-candidates 必须为正"]}

    try:
        req = generate_module.load_requirement(path)
    except (OSError, ValueError) as exc:
        return {"status": "failed", "findings": [f"读不到需求：{exc}"]}

    report = generate_module.generate(
        req, max_candidates=budget, run_ladder="--fast" not in args)
    report["requirement_file"] = str(path)
    return {"status": report.get("status", "not_ready"), **report}


def cmd_sim(args: list[str]) -> dict[str, Any]:
    """``sim [status|<id>] [--domain X] [--infer] [--duration N] [--params PATH]``

    分域仿真。不给工程名时只报各域可用性（**没有模型的域标 unknown**）。

    `--domain kinematic` 真跑 MuJoCo；其余域调本机已有判定器。
    参数表默认用 `sim_params_default.json`（136 项，全 public 级）。
    """
    from .design import sim_dispatch, sim_params, mujoco_backend

    if not args or args[0].startswith("-"):
        rows = [d.to_dict() for d in sim_dispatch.domains()]
        return {"status": "ok", "domains": rows,
                "mujoco": mujoco_backend.available(),
                "params": sim_params.describe(),
                "describe": sim_dispatch.describe()}

    store, design = _load(args[0])

    domain = ""
    if "--domain" in args:
        try:
            domain = args[args.index("--domain") + 1]
        except IndexError:
            return {"status": "failed", "findings": ["--domain 需要域名"]}

    params = None
    if "--params" in args:
        try:
            params = sim_params.load(args[args.index("--params") + 1])
        except (IndexError, FileNotFoundError) as exc:
            return {"status": "failed", "findings": [f"--params: {exc}"]}
    else:
        params = sim_params.effective(design)

    duration = 3.0
    if "--duration" in args:
        try:
            duration = float(args[args.index("--duration") + 1])
        except (IndexError, ValueError):
            return {"status": "failed", "findings": ["--duration 需要数字"]}

    if domain:
        kw = {"duration_s": duration, "params": params}
        if "--weld" in args:
            kw["weld_parts"] = True
            kw["free_bodies"] = True
        row = sim_dispatch.run_domain(design, domain, **kw)
        row["project"] = args[0]
        row["params"] = params.summary()
        store.write("sim.json", row)
        status = ("ok" if row["status"] == "ok" else
                  "failed" if row["status"] == "failed" else "not_ready")
        return {"status": status, **row}

    report = sim_dispatch.report(design)
    report["project"] = args[0]
    report["params"] = params.summary()
    store.write("sim.json", report)
    return {"status": "ok" if report["ok"] else "not_ready", **report}


def cmd_joints(args: list[str]) -> dict[str, Any]:
    """``joints <id> [--infer] [--apply] [--promote]`` —— 机械关系表。

    - 不带参数：报告声明的 + 推断的候选 + 校验结果
    - `--apply`：把 declared 的写进 design.json（inferred 不写）
    - `--promote`：把推断的提升为 declared（**人确认动作**），
      与 `--apply` 联用才会落地
    """
    from .design import joints as joints_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **joints_module.describe()}

    store, design = _load(args[0])
    report = joints_module.report(design)
    store.write("joints.json", report)

    applied = 0
    promoted = 0
    if "--apply" in args or "--promote" in args:
        inference = joints_module.infer(design)
        if "--promote" in args:
            promoted = joints_module.promote(inference)
        declared = [j for j in inference.joints if j.source == "declared"]
        # 同时把已有的声明也纳入（保持幂等）
        existing = [joints_module.Joint.from_dict(j)
                    for j in (design.joints_list or [])
                    if isinstance(j, dict)]
        applied = joints_module.apply(
            design, existing + declared, only_declared=True)
        if applied or promoted:
            store.save(design)
    report["applied"] = applied
    report["promoted"] = promoted

    status = "ok" if report["status"] == "ok" else "not_ready"
    return {"status": status, **report}


def cmd_dfm(args: list[str]) -> dict[str, Any]:
    """``dfm [<id>] [--live]`` —— 制造可行性：DRC 分级 + DFM 三态。

    默认**只读**：拿 `<工程>/emit.json` 里的 `drc` / `fabrication` 重判一遍
    （不重跑立创，所以是"对着旧证据再看一眼"）。`--live` 才去立创里真跑
    DRC + DFM（要 lceda CLI，单次 30 秒上限）。

    为什么重判也有用：`emit.json` 里存的是**原始证据**（逐板 DRC 明细 +
    DFM 的结果或错误文本），判定口径（哪些类别可信、DFM 不完整算 unknown）
    可以在这里独立复核，不必重跑整条发射。
    """
    from .design import dfm as dfm_module
    from .design import lceda as lceda_mod

    if not args or args[0].startswith("-"):
        return {"status": "ok", **dfm_module.describe()}

    store, design = _load(args[0])
    payload = store.read("emit.json")
    if not isinstance(payload, dict):
        return {"status": "not_ready",
                "findings": ["没有 emit.json（先跑 `emit` 才有制造证据）"]}

    drc = payload.get("drc") or {}
    boards = drc.get("boards") or []
    dfm_rows = drc.get("dfm") or []
    worst = next((r for r in dfm_rows if r.get("status") != "ok"), None) \
        or (dfm_rows[0] if dfm_rows else None)
    merged = dfm_module.merge(drc, worst)

    if "--live" in args:
        try:
            session = lceda_mod.LcedaSession(project_path=str(store.dir))
            session.open(headless=True)
        except Exception as exc:  # noqa: BLE001
            merged.setdefault("live", {})["error"] = f"{type(exc).__name__}: {exc}"
        else:
            try:
                got = session.invoke(
                    "const a = await (async () => { try { return await "
                    "eda.pcb_Dfm.getDfmAvailability(); } catch (e) { return null; } })();"
                    "let r = null, err = '';"
                    "try { r = await eda.pcb_Dfm.checkDfm({}); }"
                    "catch (e) { err = String((e && e.message) || e); }"
                    "return {available: a, result: r, error: err};",
                    require_ok=False, label="dfm")
                merged["live"] = dfm_module.dfm_verdict(
                    (got or {}).get("result"), error=str((got or {}).get("error") or ""),
                    available=(got or {}).get("available"))
            finally:
                session.close()

    merged["project"] = args[0]
    merged["checked"] = {"drc_boards": len(boards), "dfm_boards": len(dfm_rows)}
    return {"status": merged["status"], **merged}


def cmd_fw_build(args: list[str]) -> dict[str, Any]:
    """``fw-build <id> [--out=fw/build] [--no-build]`` —— 可编译骨架 + **真编译**（P52）。

    从 `design.json` + `interface.json` 生成启动文件 / 链接脚本 / main / Makefile，
    再用**仓库内** arm-none-eabi-gcc 编成 `.elf` + `.bin`。四条硬判据：
    编译过 / Machine=ARM / Entry 在 FLASH / text+data ≤ FLASH 且 data+bss ≤ RAM。
    """
    from .design import fw_build as build_module
    from .design import interface as iface_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **build_module.describe()}

    store, design = _load(args[0])
    out = ""
    for item in args[1:]:
        if item.startswith("--out="):
            out = item.split("=", 1)[1]
    out = out or build_module.OUT_DIR

    contract = store.read(iface_module.FILE)
    if not isinstance(contract, dict):
        contract = iface_module.build(design)
        store.write(iface_module.FILE, contract)

    made = build_module.generate(store, design, contract, out_dir=out)
    if made["status"] != "ok":
        return {"status": "not_ready", "project_id": args[0], **made}
    store.write(build_module.FILE, made)
    if "--no-build" in args:
        return {"status": "ok", "project_id": args[0], **made,
                "note": "只生成了骨架（--no-build）"}

    result = build_module.build(store, out_dir=out)
    return {"status": result["status"], "project_id": args[0], **made,
            "build": result, "findings": result.get("findings") or []}


def cmd_params(args: list[str]) -> dict[str, Any]:
    """``params <id> [--roots=a;b]`` —— 飞控参数 → 能力映射（P53）。

    解析两种真实格式：`name=value` 的 `.param`，和 `MF_PARAM(...)` 的参数表。
    只报**族完整度**：参数存在说明"增益有值"，**不说明"环稳定"** ——
    能力状态仍归 `plan` 的判据。缺必需参数 = `missing`，不是通过。
    """
    from .design import params as params_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **params_module.describe()}

    store = _store(args[0])
    roots: list[str] = []
    for item in args[1:]:
        if item.startswith("--roots="):
            raw = item.split("=", 1)[1]
            roots = [part for part in raw.split(";") if part.strip()]
    if not roots:
        from .design import workspace as ws  # noqa: PLC0415

        roots = [str(ws.workspace_root())]
    payload = params_module.build(roots=roots)
    store.write(params_module.FILE, payload)
    store.write_text("params.md", params_module.render(payload))
    return {"status": payload["status"], "project_id": args[0], **payload}


def cmd_interface(args: list[str]) -> dict[str, Any]:
    """``interface <id> [--ioc=PATH]`` —— 软硬接口契约（P50 + P55）。

    从 `design.json` 的引脚+网表 + `.ioc` 合成 `interface.json` / `interface.md`：
    固件侧读它，检查器也读它。**唯一硬判据是引脚↔网络逐条一致**。

    * 没有 `.ioc`：外设分组标 `inferred`，时钟/中断/AF/时序一律 `unknown`
    * 有 `.ioc`：时钟/中断/复用功能/外设参数变成**声明的**（`source=ioc`）
    * 有引脚表（P56）→ 逐脚对账；有 AF 表（P57）→ 功能是否真在这根脚上
    * 有 AF 表后 `unknown` 只剩「时序」；芯片身份（族/封装）不符 = `failed`
    """
    from .design import interface as iface_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **iface_module.describe()}

    store, design = _load(args[0])
    ioc = ""
    for item in args[1:]:
        if item.startswith("--ioc="):
            ioc = item.split("=", 1)[1]
    payload = iface_module.build(design, ioc_path=(ioc or None))
    store.write(iface_module.FILE, payload)
    store.write_text("interface.md", iface_module.render(payload))
    return {"status": payload["status"], "project_id": args[0], **payload}


def cmd_pinout(args: list[str]) -> dict[str, Any]:
    """``pinout`` / ``list`` / ``show <part>`` / ``build --lcsc=… [--part=…]``

    **引脚定义表**（P56）：封装脚号 ↔ 端口名。`.ioc` 用 `PA9`，网表用 `30`；
    有这张表才能**逐脚**对账（`ioc.pin_conflict` 才是硬判据）。

    来源是 LCSC/EasyEDA 的厂商符号；**符号不完整就拒绝建表**（不补猜的脚）。
    参数两种写法都认：`pinout show <part>` 与 `pinout --action=show --part=<part>`
    （后者给 DSH 工具用 —— `build_argv` 只会发 `--flag=value`）。
    """
    from .design import pinout as pinout_module

    action, part, lcsc = "", "", ""
    for item in args:
        if item.startswith("--action="):
            action = item.split("=", 1)[1]
        elif item.startswith("--part="):
            part = item.split("=", 1)[1]
        elif item.startswith("--lcsc="):
            lcsc = item.split("=", 1)[1]
        elif not item.startswith("--"):
            if not action:
                action = item
            elif not part:
                part = item
    if not action:
        return {"status": "ok", **pinout_module.describe(),
                "tables_detail": pinout_module.list_tables()}
    if action == "list":
        rows = pinout_module.list_tables()
        bad = [row for row in rows if row.get("error") or row.get("problems")]
        return {"status": "ok" if not bad else "failed", "tables": rows,
                "count": len(rows)}
    if action == "show":
        if not part:
            return {"status": "not_ready",
                    "reason": "缺 part 名（pinout show <part>）",
                    "known": [row.get("part") for row in pinout_module.list_tables()]}
        table = pinout_module.load(part)
        if table is None:
            return {"status": "not_ready",
                    "reason": f"没有 {part} 的表",
                    "known": [row.get("part") for row in pinout_module.list_tables()]}
        return {"status": "ok", **table,
                "problems": pinout_module.validate(table)}
    if action == "build":
        made = pinout_module.build_table(lcsc, part=part)
        if made.get("status") != "ok":
            return {"status": made.get("status", "failed"), **made}
        table = made["table"]
        target = pinout_module.DIR / f"{table['part']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(table, ensure_ascii=False, indent=1,
                                     sort_keys=True) + "\n", encoding="utf-8")
        return {"status": "ok", "file": str(target), "part": table["part"],
                "count": table["count"], "package": table["package"],
                "mpn": made.get("mpn"), "lcsc": table["lcsc"],
                "problems": pinout_module.validate(table)}
    if action == "validate":
        rows = pinout_module.list_tables()
        bad = [row for row in rows if row.get("error") or row.get("problems")]
        return {"status": "ok" if not bad else "failed", "tables": rows,
                "count": len(rows), "checked": len(rows), "failed": len(bad)}
    return {"status": "not_ready", "reason": f"不认识的 action {action!r}",
            "known": ["list", "show", "build", "validate"]}


def cmd_afmap(args: list[str]) -> dict[str, Any]:
    """``afmap`` / ``list`` / ``show <part> [--pin=PA9|--signal=I2C1_SCL]`` / ``build --part=…``

    **AF 复用表**（P57）：这个脚能做哪些复用功能、AF 编号是几。
    来源是 ST 官方 open pin data（CubeMX 同源，BSD-3-Clause）。
    F1 用"固定功能 + AFIO 重映射"，**没有 AF 编号**；F4/H7 是 AF0..15。
    """
    from .design import afmap as afmap_module

    action, part, pin, signal = "", "", "", ""
    for item in args:
        if item.startswith("--action="):
            action = item.split("=", 1)[1]
        elif item.startswith("--part="):
            part = item.split("=", 1)[1]
        elif item.startswith("--pin="):
            pin = item.split("=", 1)[1]
        elif item.startswith("--signal="):
            signal = item.split("=", 1)[1]
        elif not item.startswith("--"):
            if not action:
                action = item
            elif not part:
                part = item
    if not action:
        return {"status": "ok", **afmap_module.describe(),
                "tables_detail": afmap_module.list_tables()}
    if action == "list":
        rows = afmap_module.list_tables()
        bad = [row for row in rows if row.get("error") or row.get("problems")]
        return {"status": "ok" if not bad else "failed", "tables": rows,
                "count": len(rows)}
    if action in ("show", "can"):
        if not part:
            return {"status": "not_ready", "reason": "缺 part 名",
                    "known": [row.get("part") for row in afmap_module.list_tables()]}
        table = afmap_module.load(part)
        if table is None:
            return {"status": "not_ready", "reason": f"没有 {part} 的表",
                    "known": [row.get("part") for row in afmap_module.list_tables()]}
        if signal:
            return {"status": "ok", "part": table["part"], "signal": signal,
                    "pins": afmap_module.pins_for_signal(table, signal),
                    "style": table.get("style")}
        if pin:
            port = afmap_module._canon(pin) or pin
            info = (table.get("pins") or {}).get(port)
            if info is None:
                return {"status": "not_ready",
                        "reason": f"{part} 没有 {pin} 这个脚"}
            got = dict(info, pin=port,
                       matched=(afmap_module.match_signal(table, port, signal)
                                if signal else None))
            return {"status": "ok", "part": table["part"], "style": table.get("style"),
                    "pin": got}
        return {"status": "ok", **table,
                "problems": afmap_module.validate(table)}
    if action == "build":
        meta = afmap_module.PARTS.get(afmap_module._norm(part), {})
        from .design import pinout as pinout_module  # noqa: PLC0415
        made = afmap_module.build_table(part, pinout=pinout_module.load(part))
        if made.get("status") != "ok":
            return {"status": made.get("status", "failed"), **made}
        table = made["table"]
        target = afmap_module.DIR / f"{table['part']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(table, ensure_ascii=False, indent=1,
                                     sort_keys=True) + "\n", encoding="utf-8")
        return {"status": "ok", "file": str(target), "part": table["part"],
                "count": table["count"], "style": table["style"],
                "af_functions": table["af_functions"],
                "sources": table["sources"], "mpn": meta.get("mpn", "")}
    if action == "validate":
        rows = afmap_module.list_tables()
        bad = [row for row in rows if row.get("error") or row.get("problems")]
        return {"status": "ok" if not bad else "failed", "tables": rows,
                "count": len(rows), "checked": len(rows), "failed": len(bad)}
    return {"status": "not_ready", "reason": f"不认识的 action {action!r}",
            "known": ["list", "show", "can", "build", "validate"]}


def cmd_fw_algo(args: list[str]) -> dict[str, Any]:
    """``fw-algo list`` / ``fw-algo <id> --algo=failsafe [--out=fw]``

    **纯 C 算法 + PC 侧单元测试**（P50）：Renode 在本机跑不了、也没有目标板，
    所以只验不依赖硬件的逻辑（状态机/滤波/…）。用本机 gcc 真编译真跑。
    """
    from .design import fw_algo as algo_module

    if not args or args[0] == "list" or args[0].startswith("-"):
        return {"status": "ok", **algo_module.list_algos()}

    store, _design = _load(args[0])
    algo = ""
    out = ""
    for item in args[1:]:
        if item.startswith("--algo="):
            algo = item.split("=", 1)[1]
        elif item.startswith("--out="):
            out = item.split("=", 1)[1]
    if not algo:
        return {"status": "failed",
                "findings": ["需要 --algo=（可用："
                             + "、".join(algo_module.ALGOS) + "）"]}
    target = out or f"fw/{algo}"
    directory = store.path(target)
    written = algo_module.generate(algo, directory)
    if written["status"] != "ok":
        return written
    result = algo_module.build_and_run(algo, directory)
    return {"status": result["status"], "project_id": args[0], **result,
            "dir": target}


def cmd_plan(args: list[str]) -> dict[str, Any]:
    """``plan <id> [--req='<json>'] [--text=...] [--list] [--show=v1] [--graph]``

    **项目计划图**（P49）：需求 → 能力（带判据）→ 模块（带真实坐标）→
    3D 信息图 → 任务树。每次给同一个项目**加需求**就多一个 `plan/vN.json`；
    同一份需求不会重复建版。

    `--list` 只列版本；`--graph` 输出 2D 投影（3D 图的画布入口）。
    """
    from .design import plan as plan_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **plan_module.describe()}

    store = _store(args[0])
    if not store.exists():
        return {"status": "not_ready",
                "findings": [f"工程 {args[0]!r} 还没有 design.json"]}

    if "--list" in args:
        rows = plan_module.list_versions(store)
        return {"status": "ok", "project": args[0], "versions": rows,
                "count": len(rows),
                "note": ("还没有计划（第一次会建 v1）" if not rows
                         else f"{len(rows)} 版")}

    store_design = store.load()
    version = ""
    requirement: dict[str, Any] = {}
    text = ""
    for item in args[1:]:
        if item.startswith("--show="):
            version = item.split("=", 1)[1]
        elif item.startswith("--req="):
            try:
                requirement = json.loads(item.split("=", 1)[1])
            except ValueError as exc:
                return {"status": "failed", "findings": [f"--req 需要 JSON：{exc}"]}
        elif item.startswith("--text="):
            text = item.split("=", 1)[1]

    if "--show" in args and not version:
        rows = plan_module.list_versions(store)
        version = rows[-1]["version"] if rows else ""
    if version:
        payload = plan_module.load_version(store, version)
        if payload is None:
            return {"status": "not_ready",
                    "findings": [f"没有 {version}（用 --list 看有哪些）"]}
        if "--graph" in args:
            return {"status": "ok", "version": version,
                    **plan_module.graph2d(payload)}
        return {"status": "ok", **payload}

    result = plan_module.ensure(store, store_design, requirement, text=text)
    payload = plan_module.load_version(store, result.get("version", "")) or {}
    if "--graph" in args:
        return {"status": "ok", **plan_module.graph2d(payload)}
    return {"status": "ok", **result, "plan": payload}


def cmd_audit(args: list[str]) -> dict[str, Any]:
    """``audit [<id>] [--stages=intake,pcb]`` —— 六段审计（P47）。

    **只投影已有产物**：需求→单元→选料→原理图→PCB→固件→机械→交接，
    每段一行 `status/checked/basis/findings`。缺产物 = `not_ready`，不是 ok。
    写 `audit.json` + `audit.md`。
    """
    from .design import audit as audit_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **audit_module.describe()}

    store, design = _load(args[0])
    stages: tuple[str, ...] = ()
    for item in args[1:]:
        if item.startswith("--stages="):
            stages = tuple(x.strip() for x in item.split("=", 1)[1].split(",")
                           if x.strip())
    report = audit_module.audit(store, design, stages=stages)
    store.write("audit.json", report)
    (store.dir / "audit.md").write_text(audit_module.render_markdown(report),
                                        encoding="utf-8")
    return {"status": report["status"], "project_id": args[0], **report}


def cmd_doctor(args: list[str]) -> dict[str, Any]:
    """``doctor`` —— 环境探测：立创/Bridge/LCSC/MuJoCo/工具链（P47）。

    **只探测，不安装、不修复、不联网**。required 全 ok 才是 ok。
    """
    from .design import doctor as doctor_module

    if args and args[0].startswith("-"):
        return {"status": "ok", **doctor_module.describe()}
    return {"status": "ok", **doctor_module.run()}


def cmd_examples(args: list[str]) -> dict[str, Any]:
    """``examples [list]`` / ``examples copy <name> --to=<id> [--title=...]``

    三个可抄的半成品样例（P47）。复制**不覆盖**已存在的工程。
    """
    from .design import examples as examples_module

    if not args or args[0] == "list" or args[0].startswith("-"):
        return {"status": "ok", **examples_module.list_examples()}
    if args[0] != "copy":
        return {"status": "failed",
                "findings": ["用法：examples list | examples copy <name> --to=<id>"]}
    name = args[1] if len(args) > 1 and not args[1].startswith("-") else ""
    project_id, title = "", ""
    for item in args[2:]:
        if item.startswith("--to="):
            project_id = item.split("=", 1)[1]
        elif item.startswith("--title="):
            title = item.split("=", 1)[1]
    if not name or not project_id:
        return {"status": "failed", "findings": ["copy 需要 <name> 与 --to=<id>"]}
    store = _store(project_id)
    result = examples_module.copy_example(name, store, title=title)
    return {"status": result["status"], **result}


def cmd_fixloop(args: list[str]) -> dict[str, Any]:
    """``fixloop [<id>] [--rounds=3]`` —— **有界**自修循环（P47）。

    `status` 只报"这一轮链有没有结论"；**不改设计**：没有注入 editor 时
    只出计划（`stop=needs_editor`）。要真改，用 agent 循环（P22）或手改。
    """
    from .design import fixloop as fixloop_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **fixloop_module.describe()}

    store, design = _load(args[0])
    rounds = 3
    stages: tuple[str, ...] = ()
    for item in args[1:]:
        if item.startswith("--rounds="):
            try:
                rounds = int(item.split("=", 1)[1])
            except ValueError:
                return {"status": "failed", "findings": ["--rounds 需要整数"]}
        elif item.startswith("--stages="):
            stages = tuple(x.strip() for x in item.split("=", 1)[1].split(",")
                           if x.strip())
    report = fixloop_module.run(store, max_rounds=rounds, stages=stages)
    store.write("fixloop.json", report)
    return {"status": report["status"], "project_id": args[0], **report}


def cmd_mech(args: list[str]) -> dict[str, Any]:
    """``mech [<id>]`` —— 四条机械判据：干涉 / 重心 / 舵机扭矩 / 关节行程。

    **P44 补的是"机械一条判据都没有"这个洞**：`joints` 只查参数给没给，
    `mjcf` 只查能不能编译；"两块板插在一起""重心在支撑外""9g 舵机推 300g"
    这三类必炸的问题此前只能看图。

    三态：`pass`（算了且在范围内）/ `fail`（超了）/ `unknown`（**没法算**，
    不是通过）。写 `mech.json`，由 `handoff` 归入 blocking / missing_data。
    """
    from .design import mech_check as mech_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **mech_module.describe()}

    store, design = _load(args[0])
    report = mech_module.report(design)
    store.write("mech.json", report)
    return {"status": report["status"], "project_id": args[0], **report}


def cmd_scaffold(args: list[str]) -> dict[str, Any]:
    """``scaffold [<req-file>] --project=<id> --route=synth|generate|roles
    [--template=X] [--spec '<json>'] [--target=arduino|...] [--drivers=A,B]
    [--ioc=PATH] [--ref=U1] [--to=design|fw|mech|pcb|all] [--title=...]``

    **一句需求 → 一份半成品**（P43）。把 P40–P42 的零件串成一条固定链：

    ```
    req → 选路(synth/generate/roles) → design.json
        → pinmap（.ioc 优先，没有就按网表推）
        → joints 提案（有依据的算，没依据的进 open_items）
        → fw 骨架 → MJCF → [可选] 立创发射
    ```

    三条纪律：

    * **选路必须显式给** —— 三条路出来的东西差别很大，猜错比不做更糟
    * **缺件不中止** —— 缺 `.ioc` 走网表、缺关节参数走提案；它们进
      `open_items.md`（人读清单，**不是闸门**）
    * **本命令不碰立创**，除非 `--to pcb|all`；发射失败也**不拖累**其它段
    """
    from .design import scaffold as scaffold_module

    if not args or args[0].startswith("-") and "--project=" not in " ".join(args):
        if not args:
            return {"status": "ok", **scaffold_module.describe()}

    project_id = ""
    req_path = ""
    route = ""
    template = ""
    spec: dict[str, Any] = {}
    title = ""
    target = ""
    drivers: list[str] = []
    ioc = ""
    ref = "U1"
    mcu = ""
    to: list[str] = []
    stages: list[str] = []
    for index, item in enumerate(args):
        if item.startswith("--project="):
            project_id = item.split("=", 1)[1]
        elif item.startswith("--route="):
            route = item.split("=", 1)[1]
        elif item.startswith("--template="):
            template = item.split("=", 1)[1]
        elif item.startswith("--title="):
            title = item.split("=", 1)[1]
        elif item.startswith("--target="):
            target = item.split("=", 1)[1]
        elif item.startswith("--drivers="):
            drivers = [x.strip() for x in item.split("=", 1)[1].split(",") if x.strip()]
        elif item.startswith("--ioc="):
            ioc = item.split("=", 1)[1]
        elif item.startswith("--ref="):
            ref = item.split("=", 1)[1]
        elif item.startswith("--mcu="):
            mcu = item.split("=", 1)[1]
        elif item.startswith("--to="):
            to = [x.strip() for x in item.split("=", 1)[1].split(",") if x.strip()]
        elif item.startswith("--stages="):
            stages = [x.strip() for x in item.split("=", 1)[1].split(",") if x.strip()]
        elif item.startswith("--spec="):
            try:
                spec = json.loads(item.split("=", 1)[1])
            except ValueError as exc:
                return {"status": "failed", "findings": [f"--spec 需要 JSON：{exc}"]}
        elif not item.startswith("-") and index == 0:
            req_path = item

    if not project_id:
        return {"status": "failed", "findings": ["需要 --project=<id>"]}
    if route and route not in scaffold_module.ROUTES:
        return {"status": "failed",
                "findings": [f"未知 route：{route!r}（可用 {', '.join(scaffold_module.ROUTES)}）"]}

    store = _store(project_id)
    requirement: Any = req_path if req_path else None
    if not req_path and store.exists():
        # 工程已存在且已有 req.json → 直接用它；否则要求显式给
        candidate = store.path("req.json")
        if candidate.is_file():
            requirement = str(candidate)

    payload = scaffold_module.build(
        project_id=project_id, store=store, requirement=requirement,
        route=route, template=template, spec=spec, title=title,
        target=target, drivers=drivers, ioc=ioc, ref=ref, mcu=mcu,
        stages=tuple(stages))
    store.write("scaffold.json", payload)
    (store.dir / "open_items.md").write_text(
        scaffold_module.open_items_markdown(project_id, payload), encoding="utf-8")

    # -- 可选：立创发射（**失败不拖累前面的段**）------------------------
    if any(t in ("pcb", "all") for t in to):
        try:
            emitted = cmd_emit([project_id, "--jobs=all"])
            payload["stages"].append(_emit_stage_row(emitted))
        except Exception as exc:  # noqa: BLE001 - 发射挂掉要让前面的段保住
            payload["stages"].append({
                "stage": "pcb", "status": "not_ready",
                "note": f"{type(exc).__name__}: {exc}",
            })
        store.write("scaffold.json", payload)

    return {"status": payload["status"], "project": project_id,
            "route": payload.get("route"), "stages": payload["stages"],
            "open_items": payload["open_items"], "note": payload["note"],
            "artifacts": {"scaffold": store.path("scaffold.json").is_file(),
                          "open_items": store.path("open_items.md").is_file()}}


def _emit_stage_row(report: dict[str, Any]) -> dict[str, Any]:
    """把 `emit` 的结果折成 scaffold 的一行（不复制它的判定）。"""
    jobs = report.get("jobs") or {}
    bad = [name for name, job in jobs.items()
           if str((job or {}).get("status")) != "ok"]
    return {
        "stage": "pcb",
        "status": "ok" if jobs and not bad else "not_ready",
        "counts": {name: str((job or {}).get("status")) for name, job in jobs.items()},
        "note": ("发射 8 段全 ok" if jobs and not bad
                 else ("没跑起来" if not jobs
                       else "未通过：" + "、".join(bad))),
    }


def cmd_joints_propose(args: list[str]) -> dict[str, Any]:
    """``joints-propose [<id>] [--apply]`` —— 机械关系的**提案**（P42）。

    默认只写 `joints.proposed.json`（**不动** `joints.json`，也不动
    `design.json`）。`--apply` 才把提案写进 `design.joints_list`，`source`
    保持 `proposed` —— 能进物理模型，但报告里会数出来"其中几条是提案"。

    为什么要有这一层：`joints` 对 hinge/axle/slide 缺参数一律报 error（纪律，
    不编默认值）。但"没有任何机械关系的半成品"什么也验不了。所以中间插一层
    提案：**有依据的算出来，没依据的写清要补什么**。
    """
    from .design import joint_propose as propose_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **propose_module.describe()}

    store, design = _load(args[0])
    report = propose_module.propose(design)
    store.write("joints.proposed.json", report)

    applied: list[str] = []
    if "--apply" in args:
        applied = propose_module.apply(design, report["proposals"])
        if applied:
            store.save(design)
    report["applied"] = applied
    report["proposal_file"] = "joints.proposed.json"
    return {"status": report["status"], **report}


def cmd_dims(args: list[str]) -> dict[str, Any]:
    """``dims [<id>] [--apply]`` —— 每颗元件的 L/W/H 从哪来、能不能当精确值。

    **P41 的"尺寸来源"段**：机械/布局/3D 全都吃 `size_mm`，但它的质量分三档
    （datasheet/measured 可信、catalog 中等、proposed/estimated 不可信、空 = 没有）。
    这个命令不重新推导尺寸——推导在 `easyeda._size_from_attrs` 只有一份，
    这里只**分类 + 报告 + 可选回写**（回写标 `catalog`，不冒充 datasheet）。
    """
    from .design import dims as dims_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **dims_module.describe()}

    store, design = _load(args[0])
    report = dims_module.for_design(design)

    applied: list[str] = []
    if "--apply" in args:
        # 只回写"能补的"：`physical_source` 标 catalog（3D 包围盒也是目录级）。
        # 已经在 `size_mm` 里的真值不动 —— 人抄的数字不能被目录覆盖。
        sizes = {row["ref"]: row.get("from_3d") for row in report["rows"]}
        for part in design.parts:
            size = sizes.get(part.ref)
            if not size:
                continue
            if all(float(part.size_mm.get(k, 0) or 0) > 0 for k in ("w", "h", "d")):
                continue
            part.size_mm = {"w": float(size.get("w", 0)),
                            "h": float(size.get("h", 0)),
                            "d": float(size.get("d", 0))}
            part.physical_source = "catalog"
            applied.append(part.ref)
        if applied:
            store.save(design)
    report["applied"] = applied
    return {"status": report["status"], **report}


def cmd_lceda_panel(args: list[str]) -> dict[str, Any]:
    """``lceda-panel <id> [--name=...]`` —— 拼板容器（P61）。

    能做到的：建面板、命名、列出。**做不到的**：把板子放进面板、面板 DRC、
    面板制造文件 —— 这一版 `doc api` 里 `PNL_ManufactureData/PNL_Drc/PNL_Import`
    返回 null，`PNL_Document` 只有 save()。所以总状态是 `not_ready`，
    并带 `blocked_by` + 证据，不假装拼板完成。
    """
    from .design import lceda as lceda_module
    from .design import lceda_panel as panel_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **panel_module.describe()}
    store, design = _load(args[0])
    record = store.read("emit.json")
    project = (record or {}).get("project") or {}
    if not project.get("uuid"):
        return {"status": "not_ready", "project_id": args[0],
                "reason": "没有 emit.json / 里面没有工程 uuid —— 先跑 emit"}
    name = ""
    for item in args[1:]:
        if item.startswith("--name="):
            name = item.split("=", 1)[1]
    session = None
    try:
        session = lceda_module.LcedaSession().open("", headless=True)
        payload = panel_module.create(store.dir, session=session,
                                      ctx={"projectUuid": str(project["uuid"])},
                                      name=name)
    except Exception as exc:  # noqa: BLE001
        return {"status": "not_ready", "project_id": args[0],
                "reason": f"立创会话起不来：{type(exc).__name__}: {exc}"}
    finally:
        if session is not None and session.session_id:
            try:
                session.close(destroy=True)
            except Exception:  # noqa: BLE001
                pass
    store.write(panel_module.FILE, payload)
    return {"status": payload["status"], "project_id": args[0], **payload}


def cmd_lceda_sch(args: list[str]) -> dict[str, Any]:
    """``lceda-sch-drc <id> [--limit=1]`` —— 原理图 DRC（P60）。

    从 `emit.json` 拿工程 uuid，逐页 `SCH_Drc.check(true,false,true)`，
    按 `type`（fatalError/error/warn）自己过滤 —— **不用布尔模式**
    （立创在 strict 下把 warn 也算 false，刻意悬空脚会误判）。

    `warn` 不影响 gate；`fatalError`/`error` 才判 `failed`；一页都没查成是
    `not_ready`，不是通过。
    """
    from .design import lceda as lceda_module
    from .design import lceda_sch as sch_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **sch_module.describe()}
    store, design = _load(args[0])
    record = store.read("emit.json")
    project = (record or {}).get("project") or {}
    if not project.get("uuid"):
        return {"status": "not_ready", "project_id": args[0],
                "reason": "没有 emit.json / 里面没有工程 uuid —— 先跑 emit"}
    limit = 0
    for item in args[1:]:
        if item.startswith("--limit="):
            try:
                limit = int(item.split("=", 1)[1])
            except ValueError:
                limit = 0
    session = None
    try:
        session = lceda_module.LcedaSession().open("", headless=True)
        payload = sch_module.run(store.dir, session=session,
                                 ctx={"projectUuid": str(project["uuid"])},
                                 limit_pages=limit)
    except Exception as exc:  # noqa: BLE001
        return {"status": "not_ready", "project_id": args[0],
                "reason": f"立创会话起不来：{type(exc).__name__}: {exc}"}
    finally:
        if session is not None and session.session_id:
            try:
                session.close(destroy=True)
            except Exception:  # noqa: BLE001
                pass
    store.write(sch_module.FILE, payload)
    return {"status": payload["status"], "project_id": args[0],
            "project": {"uuid": project.get("uuid"), "name": project.get("name")},
            **payload}


def cmd_lceda_fab(args: list[str]) -> dict[str, Any]:
    """``lceda-fab <id> [--kinds=step,dxf,pdf,netlist,testpoint]`` —— 制造包扩产（P59）。

    从 `emit.json` 拿工程 uuid 与 PCB uuid，打开工程后调 `pcb_ManufactureData`
    的导出接口，把 STEP/DXF/PDF/网表/测试点写进 `artifacts/fab/`，并验头字节。

    `invoke` 返回上限 8 MB —— STEP（实测 14 MB）走 4 MB 分块；
    `ipc2581` 在这一版只返回 2 字节 `{}`，所以**不产出假文件**（not_ready）。
    """
    from .design import lceda as lceda_module
    from .design import lceda_fab as fab_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **fab_module.describe()}
    store, design = _load(args[0])
    record = store.read("emit.json")
    if not isinstance(record, dict) or not (record.get("project") or {}).get("uuid"):
        return {"status": "not_ready", "project_id": args[0],
                "reason": "没有 emit.json / 里面没有工程 uuid —— 先跑 emit"}
    project = record.get("project") or {}
    boards = project.get("boards") or []
    pcb_uuid = ""
    for board in boards:
        if isinstance(board, dict) and board.get("pcbUuid"):
            pcb_uuid = str(board["pcbUuid"])
            break
    if not pcb_uuid:
        return {"status": "not_ready", "project_id": args[0],
                "reason": "emit.json 里没有 pcbUuid"}
    kinds: list[str] = []
    for item in args[1:]:
        if item.startswith("--kinds="):
            kinds = [part.strip() for part in item.split("=", 1)[1].split(",") if part.strip()]
    session = None
    try:
        session = lceda_module.LcedaSession().open("", headless=True)
        payload = fab_module.export(store.dir, kinds=kinds or None, session=session,
                                    ctx={"projectUuid": str(project["uuid"]),
                                         "pcbUuid": pcb_uuid,
                                         "settleMs": 900})
    except Exception as exc:  # noqa: BLE001
        return {"status": "not_ready", "project_id": args[0],
                "reason": f"立创会话起不来：{type(exc).__name__}: {exc}"}
    finally:
        if session is not None and session.session_id:
            try:
                session.close(destroy=True)
            except Exception:  # noqa: BLE001
                pass
    store.write(fab_module.FILE, payload)
    return {"status": payload["status"], "project_id": args[0],
            "project": {"uuid": project.get("uuid"), "name": project.get("name")},
            **payload}


def cmd_lceda_geom(args: list[str]) -> dict[str, Any]:
    """``lceda-geom <id> [--apply] [--limit=N]`` —— 封装真几何（P58）。

    从立创编辑器取 `LIB_Footprint.getGeometry`：本体/焊盘包围盒 + 每个焊盘
    （mil→mm），写 `geom.json`。`--apply` 把尺寸写回 `design.json`，**只覆盖
    低信任来源**（空/estimated/proposed/catalog）——`datasheet`/`measured`
    一个字都不动。

    需要桌面客户端 `lceda-pro`；没装/起不来 → `not_ready`（不是 failed）。
    """
    from .design import lceda as lceda_module
    from .design import lceda_geom as geom_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **geom_module.describe()}

    store, design = _load(args[0])
    limit = 0
    for item in args[1:]:
        if item.startswith("--limit="):
            try:
                limit = int(item.split("=", 1)[1])
            except ValueError:
                limit = 0
    session = None
    try:
        session = lceda_module.LcedaSession().open("", headless=True)
        payload = geom_module.fetch(design, session=session, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {"status": "not_ready", "project_id": args[0],
                "reason": f"立创会话起不来：{type(exc).__name__}: {exc}"}
    finally:
        if session is not None and session.session_id:
            try:
                session.close(destroy=True)
            except Exception:  # noqa: BLE001
                pass
    store.write(geom_module.FILE, payload)
    applied = None
    if "--apply" in args and payload.get("status") == "ok":
        result = geom_module.apply(design, payload)
        if result["applied"]:
            problems = design.validate()
            if problems:
                return {"status": "failed", "project_id": args[0],
                        "reason": "写回后设计校验不过", "problems": problems[:8]}
            store.save(design)
        applied = result
    return {"status": payload["status"], "project_id": args[0], **payload,
            "applied": applied}


def cmd_mjcf(args: list[str]) -> dict[str, Any]:
    """``mjcf [<id>] [--build] [--free] [--weld] [--no-floor] [--out=PATH]``
    —— 机械模型（MJCF）。

    **P40 补的就是这一段**：`mjcf.build()` 一直存在，但没有任何命令调用它，
    于是"机械仿真"在 CLI / 工具里根本不可达（死代码）。

    三种动作：

    * 不带工程 id → 返回 `describe()`（有哪些关节类型、各自要什么参数）
    * `mjcf <id>` → **只检查**：构建 XML + 用 MuJoCo 真的加载一次，不写盘
    * `mjcf <id> --build` → 落 `<工程>/model.xml`，并再做一次加载检查

    判据（三态，**缺数据不是通过**）：

    | 情况 | status |
    |---|---|
    | 能构建 + MuJoCo 加载通过 + 没有跳过件 + 有关节表 | `ok` |
    | 没装 MuJoCo / 有零件被跳过 / 没有关节表 / 零件为空 | `not_ready`（各带原因）|
    | MuJoCo 在位但编译报错 | `failed`（带异常原文）|

    `--free` / `--weld` 是**必须显式给**的模型选择（见 `mjcf.build` 的说明）：
    默认焊死在世界里（纯几何），`--free` 让零件自由下落，`--weld` 让每块板
    成为一个刚体（PCB 的正确力学模型）。
    """
    from .design import joints as joints_module
    from .design import mjcf as mjcf_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **mjcf_module.describe()}

    store, design = _load(args[0])
    rest = list(args[1:])
    flags = {a for a in rest if a.startswith("--") and "=" not in a}
    out_path = ""
    for item in rest:
        if item.startswith("--out="):
            out_path = item.split("=", 1)[1]

    free = "--free" in flags
    weld = "--weld" in flags
    include_floor = "--no-floor" not in flags
    want_build = "--build" in flags

    joints_report = joints_module.report(design)
    try:
        built = mjcf_module.build(design, free_bodies=free, weld_parts=weld,
                                  include_floor=include_floor)
    except Exception as exc:  # noqa: BLE001 - 构建失败要落成 status，不抛
        return {"status": "failed",
                "findings": [f"构建 MJCF 失败：{type(exc).__name__}: {exc}"]}

    xml = built.xml
    stats = built.stats()
    reported_path = ""
    if want_build:
        target = Path(out_path) if out_path else store.path("model.xml")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(xml, encoding="utf-8")
        reported_path = str(target)

    load = mjcf_module.load_check(path=reported_path) if reported_path else \
        mjcf_module.load_check(xml=xml)

    notes: list[str] = []
    if stats["parts_total"] == 0:
        status = "not_ready"
        notes.append("设计里一个零件都没有 —— 机械模型没有对象（空输入不算通过）")
    elif load["status"] == "not_ready":
        status = "not_ready"
        notes.append(f"MuJoCo 不在位，**没有验证过**这份 XML：{load.get('reason')}")
    elif load["status"] == "failed":
        status = "failed"
        notes.append(f"MuJoCo 编译报错：{load.get('reason')}")
    elif stats["parts_skipped"] or stats.get("joints_skipped"):
        status = "not_ready"
        bits = []
        if stats["parts_skipped"]:
            bits.append(f"{stats['parts_skipped']}/{stats['parts_total']} 个零件"
                        "（缺尺寸/质量）")
        if stats.get("joints_skipped"):
            bits.append(f"{stats['joints_skipped']} 个关节（缺必需参数）")
        notes.append("被跳过：" + "，".join(bits) + " —— 模型不完整，"
                     "跳过的原因逐条在 `stats.skipped_reasons`")
    elif not (design.joints_list or []):
        status = "not_ready"
        notes.append("没有关节表（`joints` 段）—— 这是一份静几何，不是可动的机械结构；"
                     "先跑 `joints <id>`")
    else:
        status = "ok"

    return {
        "status": status,
        "mode": {"free_bodies": free, "weld_parts": weld,
                 "include_floor": include_floor},
        "stats": stats,
        "load": load,
        "joints": {"status": joints_report.get("status"),
                   "declared": len(design.joints_list or []),
                   "inferred": len(joints_report.get("inferred") or [])},
        "xml": {"chars": len(xml), "path": reported_path},
        "artifacts": {"model.xml": bool(reported_path)},
        "notes": notes,
        "warnings": list(built.warnings)[:12],
    }


def cmd_lceda(args: list[str]) -> dict[str, Any]:
    """``lceda [status|probe|sessions|close]`` —— 立创EDA 通道自检。

    - 无参 / `status`：探测 exe、版本、关键类与方法、单位换算
    - `probe`：额外逐条核对四段需要的方法，缺哪个点名
    - `sessions`：列出当前 lceda-pro 会话
    """
    from .design import lceda as lceda_module

    action = args[0] if args and not args[0].startswith("-") else "status"
    if action == "status":
        return lceda_module.report()
    if action == "probe":
        found = lceda_module.probe()
        found["capabilities"] = {
            job: [f"{c}.{m}" for c, m in items]
            for job, items in lceda_module.CAPABILITIES.items()
        }
        return found
    if action == "sessions":
        exe = lceda_module.find_exe()
        if not exe:
            return {"status": "not_ready",
                    "findings": ["没有 lceda-pro 可执行文件"]}
        sessions = lceda_module.LcedaSession.list_sessions(exe)
        return {"status": "ok", "count": len(sessions), "sessions": sessions}
    if action == "close":
        exe = lceda_module.find_exe()
        if not exe:
            return {"status": "not_ready", "findings": ["没有 lceda-pro"]}
        target = ""
        for item in args:
            if item.startswith("--session="):
                target = item.split("=", 1)[1]
        if not target:
            return {"status": "not_ready",
                    "findings": ["要指定 --session=<id>（先跑 sessions 看列表）"]}
        session = lceda_module.LcedaSession(exe=exe, session_id=target)
        result = session.close(destroy=True)
        return {"status": "ok" if result.get("ok") else "not_ready",
                "result": result}
    return {"status": "failed",
            "findings": [f"未知动作 {action!r}；可用 status / probe / sessions / close"]}


def cmd_emit(args: list[str]) -> dict[str, Any]:
    """``emit <id> [--jobs a,b] [--route native|ours|none] [--reuse]
    [--save-dir DIR] [--max-parts N] [--name NAME]`` —— 发射到立创EDA。

    落盘为 ``<工程>/emit.json``。**不抛异常**：缺 lceda 是 ``not_ready``，
    某一段失败是 ``failed``，两种情况都会写产物。
    """
    from .design import emit_lceda

    if not args or args[0].startswith("-"):
        return {"status": "ok", **emit_lceda.describe()}

    store, design = _load(args[0])
    jobs = list(emit_lceda.JOBS)
    route = "native"
    reuse = False
    save_dir = ""
    max_parts = emit_lceda.MAX_PARTS
    name = ""
    autolayout = False
    channel = "cli"
    for i, item in enumerate(args[1:]):
        if item.startswith("--jobs="):
            jobs = [x for x in item.split("=", 1)[1].split(",") if x]
        elif item.startswith("--route="):
            route = item.split("=", 1)[1]
        elif item == "--reuse":
            reuse = True
        elif item.startswith("--save-dir="):
            save_dir = item.split("=", 1)[1]
        elif item.startswith("--max-parts="):
            max_parts = int(item.split("=", 1)[1])
        elif item.startswith("--name="):
            name = item.split("=", 1)[1]
        elif item == "--autolayout":
            autolayout = True
        elif item.startswith("--channel="):
            channel = item.split("=", 1)[1]

    # 默认落盘位置：``<工程>/artifacts``。不给它的话 `outputs` 段永远只能
    # 报「生成成功、saved=0」—— 那份报告对"能不能打板"毫无用处。
    if not save_dir:
        save_dir = str(store.dir / "artifacts")

    report = emit_lceda.emit(design, project_name=name or args[0], jobs=jobs,
                             route=route, reuse=reuse, save_dir=save_dir,
                             max_parts=max_parts, autolayout=autolayout,
                             channel=channel)
    payload = report.to_dict()
    payload["project_id"] = args[0]
    store.write("emit.json", payload)
    return payload


def cmd_readback(args: list[str]) -> dict[str, Any]:
    """``readback <id> [--path=<eprj3>]`` —— 只回读（不发射）：PCB 网表 parity + DRC。

    审查入口。工程从哪来：``--path`` 优先；否则用 ``emit.json`` 里记的
    上次发射路径（那是**磁盘上的真工程**，能直接重开）。两者都没有就
    ``not_ready`` 并说清——不猜。
    """
    from .design import emit_lceda
    from .design import lceda as lceda_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **emit_lceda.describe()}
    store, design = _load(args[0])
    path = ""
    for item in args[1:]:
        if item.startswith("--path="):
            path = item.split("=", 1)[1]
    if not path:
        emit = store.read("emit.json") or {}
        path = str((emit.get("project") or {}).get("path") or "")
    if not path or not Path(path).exists():
        return {"status": "not_ready",
                "findings": ["没有可回读的立创工程：先跑 emit，或给 --path=<*.eprj3>"]}
    exe = lceda_module.find_exe()
    if not exe:
        return {"status": "not_ready", "findings": ["没有 lceda-pro 可执行文件"]}
    session = lceda_module.LcedaSession(exe=exe)
    try:
        session.open(path=path, headless=True)
        ready = session.wait_project_ready()
        boards = [b for b in (ready.get("boards") or []) if b.get("pcbUuid")]
        if not boards:
            return {"status": "not_ready",
                    "findings": [f"工程里没有可回读的 PCB：{path}"]}
        result = emit_lceda.read_back(session, design, boards=boards)
        ok = result["parity"].get("status") == "ok"
        payload = {"status": "ok" if ok else "not_ready",
                   "project_id": args[0], "project_path": path, **result}
        store.write("emit_readback.json", payload)
        return payload
    except lceda_module.CliError as exc:
        return {"status": "not_ready", "findings": [str(exc)]}
    finally:
        session.close()


def _parts_cache(project_id: str = ""):
    """缓存放哪：有工程 → 工程级 `parts_cache.json`；没有 → 临时目录。

    临时目录不是"随便放"：目录数据是**全局**的（跟工程无关），
    放在系统临时目录能跨工程复用，又不往用户工作区扔文件。
    """
    from .design import parts_catalog

    if project_id:
        store = _store(project_id)
        if store.dir.is_dir():
            return parts_catalog.PartCache(store.path("parts_cache.json"))
    import tempfile
    return parts_catalog.PartCache(Path(tempfile.gettempdir()) / "hwv_parts_cache.json")


def cmd_parts_search(args: list[str]) -> dict[str, Any]:
    """``parts-search <关键词> [--limit N] [--page N]`` —— 目录搜索（免 key）。

    返回每条的 lcsc / mpn / 封装 / 库存 / 分级。**搜不到是 ok**（`items=[]`），
    只有拿不到结果（网络/接口形状变了）才是 `not_ready`。
    """
    from .design import parts_catalog

    if not args or args[0].startswith("-"):
        return {"status": "ok", **parts_catalog.describe()}
    keyword = args[0]
    limit, page = 10, 1
    for item in args[1:]:
        if item.startswith("--limit="):
            limit = int(item.split("=", 1)[1])
        elif item.startswith("--page="):
            page = int(item.split("=", 1)[1])
    result = parts_catalog.search(keyword, limit=limit, page=page)
    if result.get("status") != "ok":
        return {"status": "not_ready",
                "findings": [result.get("reason") or "搜索失败"], **result}
    return {"status": "ok", **result}


def cmd_parts_enrich(args: list[str]) -> dict[str, Any]:
    """``parts-enrich <lcsc> [--project <id>] [--refs R1,U1] [--save-catalog] [--offline]``

    查一个编号的详情。``--project`` 给了就把结果**回填**到该工程里
    所有引用这个 lcsc 的元件（`physical_source=catalog`，公式估的标
    `estimated`）。``--save-catalog`` 写进能力表的目录覆盖层，
    让它出现在 `explain_miss.rejected` 里（**不进选型**）。
    """
    from .design import parts_catalog

    if not args or args[0].startswith("-"):
        return {"status": "ok", **parts_catalog.describe()}
    lcsc = args[0]
    project_id = ""
    refs: list[str] = []
    save_catalog = False
    no_network = False
    for item in args[1:]:
        if item.startswith("--project="):
            project_id = item.split("=", 1)[1]
        elif item.startswith("--refs="):
            refs = [x.strip() for x in item.split("=", 1)[1].split(",") if x.strip()]
        elif item == "--save-catalog":
            save_catalog = True
        elif item == "--offline":
            no_network = False  # 语义交给环境变量，这里只是显式提示
            no_network = True

    result = parts_catalog.enrich(
        lcsc, cache=_parts_cache(project_id), allow_network=not no_network)
    payload: dict[str, Any] = {"lcsc": parts_catalog.normalize_lcsc(lcsc), **result}
    if result.get("status") != "ok":
        payload["status"] = "not_ready"
        payload.setdefault("findings", [result.get("reason") or "查不到"])
        return payload

    part = result.get("part") or {}
    fields = parts_catalog.to_design_fields(part)
    payload["design_fields"] = fields

    # -- 回填到工程 ------------------------------------------------------
    if project_id:
        store, design = _load(project_id)
        touched: list[str] = []
        for row in design.parts:
            if refs and row.ref not in refs:
                continue
            if not refs and (row.lcsc or "").upper() != fields.get("lcsc"):
                continue
            if "size_mm" in fields:
                row.size_mm = dict(fields["size_mm"])
                row.physical_source = str(fields.get("physical_source") or "catalog")
            if fields.get("pins") and not row.pins:
                row.pins = list(fields["pins"])
            if fields.get("lcsc"):
                row.lcsc = fields["lcsc"]
            touched.append(row.ref)
        if touched:
            store.save(design)
        payload["updated"] = touched
        payload["updated_source"] = fields.get("physical_source", "")
        if not touched:
            payload.setdefault("findings", []).append(
                "工程里没有引用这个 lcsc 的元件（用 --refs 指定）")

    # -- 写进能力表的目录层（未确认） -------------------------------------
    if save_catalog:
        from .design import capability as capability_module
        key = str(part.get("mpn") or "").strip().lower()
        if not key:
            payload.setdefault("findings", []).append("没有型号，无法写进能力表")
        else:
            entry = {
                "fills": set(),
                "provides": set(),
                "requires": set(),
                "footprint_mm2": (
                    round(float((part.get("size_mm") or {}).get("w", 0.0))
                          * float((part.get("size_mm") or {}).get("h", 0.0)), 3)
                    or None),
                "pins": {},
                "clock_hz": None,
                "lcsc": part.get("lcsc"),
                "mpn": part.get("mpn"),
                "package": part.get("package"),
                "datasheet_url": part.get("datasheet_url"),
                "note": (f"目录回填（{part.get('package') or '封装未知'}）；"
                         "fills/provides 未确认，所以不进选型"),
                "source": "catalog",
            }
            if capability_module.save_catalog_entry(key, _jsonable(entry)):
                payload["catalog_key"] = key
                payload["catalog_note"] = (
                    "已写进能力表的目录层：会出现在 explain_miss.rejected，"
                    "但**不会**进 candidates（source=catalog）")

    payload["status"] = "ok"
    return payload


def cmd_parts_promote(args: list[str]) -> dict[str, Any]:
    """``parts-promote <型号|lcsc> --datasheet <url> --role <role> [--needs a,b]``

    目录级 → datasheet 级。**必须给手册页 URL 留痕**，否则不写。
    升级后它会进 `capability.candidates()` 的硬选型——所以 `--role`
    与 `--needs` 是这个动作真正的内容：人（或 agent 读完手册）说
    "它是什么"和"它能干什么"，目录里没有这两项。
    """
    from .design import capability as capability_module
    from .design import parts_catalog

    if not args or args[0].startswith("-"):
        return {"status": "ok", "how": "parts-promote <型号|lcsc> --datasheet=<url> "
                                      "--role=<role> [--needs=a,b,c]"}
    key = args[0].strip()
    datasheet = ""
    role = ""
    needs: list[str] = []
    for item in args[1:]:
        if item.startswith("--datasheet="):
            datasheet = item.split("=", 1)[1]
        elif item.startswith("--role="):
            role = item.split("=", 1)[1]
        elif item.startswith("--needs="):
            needs = [x.strip() for x in item.split("=", 1)[1].split(",") if x.strip()]

    catalog_key = key.lower()
    if parts_catalog.normalize_lcsc(key):
        enriched = parts_catalog.enrich(key, cache=_parts_cache())
        mpn = str((enriched.get("part") or {}).get("mpn") or "").strip()
        if mpn:
            catalog_key = mpn.lower()
    if not role:
        return {"status": "not_ready",
                "findings": ["--role 必填：目录里没有「它是什么」这项，"
                             "不替你猜它是 mcu 还是 sensor"]}
    result = capability_module.promote_entry(
        catalog_key,
        {"fills": {role}, "provides": set(needs)}, datasheet)
    if not result.get("ok"):
        return {"status": "not_ready", "findings": [result.get("reason") or "promote 失败"],
                "key": catalog_key}
    entry = result.get("entry") or {}
    return {
        "status": "ok",
        "key": catalog_key,
        "datasheet": datasheet,
        "role": role,
        "needs": needs,
        "source": entry.get("source"),
        "trusted": capability_module.trusted(catalog_key),
        "in_candidates": catalog_key in capability_module.candidates(
            set(needs), role=role) if needs else None,
        "note": ("已升级为 datasheet 级：现在参与硬选型。"
                 "fills/provides 来自你给的值，不是目录" ),
    }


def _jsonable(value: Any) -> Any:
    """set → sorted list，供写 JSON。"""
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _resolve_under(project_dir: Path, raw: str) -> Path:
    """相对路径的实际所指：绝对路径 → cwd → **工程目录**。

    试的顺序按"人写命令行时的直觉"：`--ioc=board.ioc` 显然指工程里那个；
    `--ioc=somedir/board.ioc` 从 cwd 能找到就用 cwd 的。都不存在时返回
    `工程目录/raw`（报错信息里就是这个路径，人能看出我们试了什么）。
    """
    path = Path(raw)
    if path.is_absolute() or path.is_file() or path.is_dir():
        return path
    under_project = project_dir / raw
    return under_project if under_project.exists() else path if path.exists() else under_project


def _first_ioc(project_dir: Path) -> Path | None:
    """工程目录里找 `.ioc`（含子目录）。找不到返回 None —— **不猜路径**。"""
    if not project_dir.is_dir():
        return None
    hits = sorted(p for p in project_dir.rglob("*.ioc") if p.is_file())
    return hits[0] if hits else None


def _resolve_ioc(store, ioc: str) -> tuple[str, list[str]]:
    """定 `.ioc`：显式给 → 工程目录里唯一一个 → 工作区里唯一一个。

    工作区有多个时**不猜**（板名与 `.ioc` 名无交集，AGENTS §2.3），
    返回候选清单让人选。
    """
    if ioc:
        return ioc, []
    in_project = _first_ioc(store.dir)
    if in_project is not None:
        return str(in_project), []
    from .design import ioc_links
    from .design.store import project_root
    found = ioc_links.discover(project_root())
    if len(found) == 1:
        return str(found[0]), []
    return "", [str(p) for p in found]


def _pinmap_from_project(design, project_dir: Path, ioc: str, ref: str):
    """拿到一份 `PinMap`：`.ioc` 优先，没有就退回 design.json 的网表。"""
    from .design.fwgen import netlist as nl_module
    from .design.fwgen import pinmap as pm_module

    path: Path | None = _resolve_under(project_dir, ioc) if ioc else _first_ioc(project_dir)
    if path is not None and not path.is_file():
        return None, {"origin": "ioc", "reason": f"没有这个 .ioc：{path}"}
    if path is not None:
        pm, info = pm_module.from_ioc(path, ref=ref or "U1")
        return pm, {**info, "origin": "ioc"}
    try:
        net = nl_module.from_design(design)
    except nl_module.NetlistError as exc:
        return None, {"origin": "design.json", "reason": str(exc)}
    try:
        pm = pm_module.build_pinmap(net, ref=ref or None)
    except nl_module.NetlistError as exc:
        return None, {"origin": "design.json", "reason": str(exc)}
    return pm, {"origin": "design.json", "assignments": len(pm.assignments)}


def cmd_pinmap_gen(args: list[str]) -> dict[str, Any]:
    """``pinmap-gen <id> [--ioc=PATH] [--ref=U1] [--out=DIR] [--alias=HWV_]``

    生成 `pins.h`（默认写 `<工程>/firmware/`）+ `pinmap.json`。

    来源优先 `.ioc`（它带 label 与信号名），没有就退回 `design.json` 的网表。
    两边都没有 → `not_ready`，**不编一张引脚表**。
    """
    from .design.fwgen import pinmap as pm_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", "mcu_count": len(pm_module.MCUS),
                "header_files": dict(pm_module.HEADER_FILES),
                "note": "用法：pinmap-gen <id> [--ioc=PATH] [--ref=U1] [--out=DIR]"}
    store, design = _load(args[0])
    ioc, ref, out, alias, style = "", "U1", "", "HWV_", "c"
    for item in args[1:]:
        if item.startswith("--ioc="):
            ioc = item.split("=", 1)[1]
        elif item.startswith("--ref="):
            ref = item.split("=", 1)[1]
        elif item.startswith("--out="):
            out = item.split("=", 1)[1]
        elif item.startswith("--alias="):
            alias = item.split("=", 1)[1]
        elif item.startswith("--style="):
            style = item.split("=", 1)[1]

    ioc, options = _resolve_ioc(store, ioc)
    if options:
        return {"status": "not_ready", "project_id": args[0],
                "findings": [f"工作区里有 {len(options)} 个 .ioc，"
                             "用 --ioc=<路径> 指定（板名与文件名无交集，不猜）"],
                "options": options}
    pm, info = _pinmap_from_project(design, store.dir, ioc, ref)
    if pm is None:
        return {"status": "not_ready", "project_id": args[0], "info": info,
                "findings": [info.get("reason") or "没有可用的引脚来源"
                             "（工程里没有 .ioc，design.json 也没有网表）"]}

    # --out 的相对路径按**工程目录**解析：这些是工程产物，不该落到 cwd
    target_dir = (store.dir / out) if out and not Path(out).is_absolute() else (
        Path(out) if out else store.dir / "firmware")
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = pm_module.HEADER_FILES.get(style, "pins.h")
    header = pm_module.render_header(pm, style, source=info.get("path") or "design.json",
                                     alias_prefix=alias)
    (target_dir / filename).write_text(header, encoding="utf-8")
    store.write("pinmap.json", {"status": "ok", "info": info, **pm.as_dict()})
    payload = {
        "status": "ok",
        "project_id": args[0],
        "mcu": pm.mcu.name,
        "origin": info.get("origin"),
        "assignments": len(pm.assignments),
        "reserved_debug": info.get("reserved_debug") or [],
        "header": str(target_dir / filename),
        "macros": [f"PIN_{a.macro}" for a in pm.assignments][:40],
        "warnings": (pm.warnings or [])[:10],
    }
    return payload


def cmd_pinmap_check(args: list[str]) -> dict[str, Any]:
    """``pinmap-check <id> [--ioc=PATH] [--src=DIR_or_file,...]``

    固件引脚 vs 硬件配置的交叉核对。写 `pinmap_crosscheck.json`。

    * 幽灵引脚（软件用了硬件没配）与方向冲突是 **fail**
    * 闲置引脚是提示（`idle`），不拉低状态
    """
    from .design.fwgen import pinmap as pm_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", "note": "用法：pinmap-check <id> [--ioc=PATH] "
                                        "[--src=DIR,...]"}
    store, design = _load(args[0])
    ioc, ref = "", "U1"
    sources: list[str] = []
    for item in args[1:]:
        if item.startswith("--ioc="):
            ioc = item.split("=", 1)[1]
        elif item.startswith("--ref="):
            ref = item.split("=", 1)[1]
        elif item.startswith("--src="):
            sources.extend(x for x in item.split("=", 1)[1].split(",") if x.strip())

    ioc, options = _resolve_ioc(store, ioc)
    if options:
        return {"status": "not_ready", "project_id": args[0],
                "findings": [f"工作区里有 {len(options)} 个 .ioc，用 --ioc=<路径> 指定"],
                "options": options}
    pm, info = _pinmap_from_project(design, store.dir, ioc, ref)
    if pm is None:
        return {"status": "not_ready", "project_id": args[0], "info": info,
                "findings": [info.get("reason") or "没有可用的硬件引脚配置"]}

    roots: list[Path] = []
    for raw in sources:
        candidate = _resolve_under(store.dir, raw)
        if candidate.is_dir():
            roots.extend(sorted(p for p in candidate.rglob("*")
                                if p.suffix.lower() in (".c", ".h", ".cpp", ".hpp")))
        elif candidate.is_file():
            roots.append(candidate)
    extracted = pm_module.extract_uses(roots, root=store.dir)
    if extracted.get("status") != "ok":
        payload = {"status": "not_ready", "project_id": args[0],
                   "mcu": pm.mcu.name, "extract": extracted,
                   "findings": [extracted.get("reason") or "固件侧没有可核对的内容"]}
        store.write("pinmap_crosscheck.json", payload)
        return payload

    # 用 .ioc 的 IocPin（有 mode）做硬件侧；没有 .ioc 时退回 PinMap
    ioc_path = _resolve_under(store.dir, ioc) if ioc else _first_ioc(store.dir)
    if ioc_path is not None:
        pins = pm_module.parse_pins(ioc_path)
        basis = f"{Path(ioc_path).name}"
    else:
        pins = pm
        basis = "design.json"

    report = pm_module.crosscheck(pins, extracted["uses"], source=basis)
    # `crosscheck` 的三态是 ok/fail（判定用语），CLI 的三态是 ok/not_ready/failed。
    # **先摘掉再翻译**：否则 `**report` 会把 status 又覆盖回 "fail"，
    # 而工具层只认 "failed"（这一条实测踩过：CLI 报 fail、TS 判成异常）。
    verdict = str(report.pop("status", "ok"))
    payload = {
        "status": "ok" if verdict == "ok" else "failed",
        "verdict": verdict,
        "project_id": args[0],
        "mcu": pm.mcu.name,
        "extract": {k: extracted.get(k) for k in
                    ("files", "uses", "unpaired_count", "unpaired", "status")},
        **report,
    }
    store.write("pinmap_crosscheck.json", payload)
    return payload


def cmd_fw_driver(args: list[str]) -> dict[str, Any]:
    """``fw-driver <PART> [--out=DIR] [--address=0x44]``

    生成一个器件的 `<part>.h` / `<part>.c`（含寄存器地址与 datasheet 章节引用）。
    器件不在表里 → `not_ready` 并列出支持的 7 个，**不写半成品代码**。
    """
    from .design.fwgen import drivers as drivers_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", "supported": drivers_module.supported_parts()}
    part = args[0]
    out = ""
    address: int | None = None
    for item in args[1:]:
        if item.startswith("--out="):
            out = item.split("=", 1)[1]
        elif item.startswith("--address="):
            try:
                address = int(item.split("=", 1)[1], 0)
            except ValueError:
                return {"status": "failed",
                        "findings": [f"--address 不是数字：{item}"]}
    try:
        files = drivers_module.generate_driver(part, address)
    except KeyError:
        return {"status": "not_ready",
                "findings": [f"驱动表里没有 {part!r}；支持："
                             f"{', '.join(drivers_module.supported_parts())}"]}
    base = Path(out) if out else None
    written: list[str] = []
    if base is not None:
        base.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            path = base / name
            path.write_text(text, encoding="utf-8")
            written.append(str(path))
    return {
        "status": "ok",
        "part": part,
        "files": sorted(files),
        "written": written,
        "lines": {name: len(text.splitlines()) for name, text in files.items()},
        "gen": {name: text for name, text in files.items()} if base is None else {},
    }


def cmd_fw_scaffold(args: list[str]) -> dict[str, Any]:
    """``fw-scaffold <id> [--ioc=PATH] [--target=X] [--out=DIR] [--name=N]
    [--drivers=A,B] [--overwrite]``

    生成工程骨架（5 个 target：arduino / platformio / esp-idf / stm32 / avr）。
    target 与 MCU 家族不匹配时报 `not_ready`（它靠 C 语义，不靠猜）。
    """
    from .design.fwgen import scaffold as scaffold_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", "targets": list(scaffold_module.TARGETS)}
    store, design = _load(args[0])
    ioc, ref, target, out, name = "", "U1", "", "", ""
    drivers_list: list[str] = []
    overwrite = False
    for item in args[1:]:
        if item.startswith("--ioc="):
            ioc = item.split("=", 1)[1]
        elif item.startswith("--ref="):
            ref = item.split("=", 1)[1]
        elif item.startswith("--target="):
            target = item.split("=", 1)[1]
        elif item.startswith("--out="):
            out = item.split("=", 1)[1]
        elif item.startswith("--name="):
            name = item.split("=", 1)[1]
        elif item.startswith("--drivers="):
            drivers_list = [x.strip() for x in item.split("=", 1)[1].split(",") if x.strip()]
        elif item == "--overwrite":
            overwrite = True

    ioc, options = _resolve_ioc(store, ioc)
    if options:
        return {"status": "not_ready", "project_id": args[0],
                "findings": [f"工作区里有 {len(options)} 个 .ioc，用 --ioc=<路径> 指定"],
                "options": options}
    pm, info = _pinmap_from_project(design, store.dir, ioc, ref)
    if pm is None:
        return {"status": "not_ready", "project_id": args[0], "info": info,
                "findings": [info.get("reason") or "没有可用的引脚来源"]}
    resolved = target or scaffold_module.default_target(pm)
    dest = ((store.dir / out) if out and not Path(out).is_absolute()
            else (Path(out) if out else store.dir / "firmware" / f"scaffold-{resolved}"))
    try:
        files = scaffold_module.scaffold(
            pm, dest, resolved, name or args[0], drivers_list or None,
            source=info.get("path") or "design.json", overwrite=overwrite)
    except scaffold_module.ScaffoldError as exc:
        return {"status": "not_ready", "project_id": args[0], "mcu": pm.mcu.name,
                "family": pm.mcu.family, "target": resolved,
                "findings": [str(exc)]}
    return {
        "status": "ok",
        "project_id": args[0],
        "mcu": pm.mcu.name,
        "target": resolved,
        "out": str(dest),
        "files": [str(p) for p in files],
        "count": len(files),
        "drivers": drivers_list,
    }


def cmd_physical(args: list[str]) -> dict[str, Any]:
    """``physical <id> [--record --test=… --result=pass|fail|unknown --observed=…
    --implicated=ref:U1,ref:W2 --video=PATH --frames=00:03,00:07
    --sim-params=initial --reproduced=true|false|none]``

    实物测试记录。不带 `--record` 时只读：列出记录 + 统计
    （`reproduced` / `not_reproduced` / `untried` 三类分开数）。

    **写进去就不许糊**：`fail` 必须有 `observed`；`implicated` 里的位号
    必须在设计里存在；`sim_reproduced` 是三态（`none` = 没试过）。
    """
    from .design import real_tests

    if not args or args[0].startswith("-"):
        return {"status": "ok", **real_tests.describe()}
    store, design = _load(args[0])
    record = "--record" in args
    fields: dict[str, str] = {}
    for item in args[1:]:
        if not item.startswith("--") or "=" not in item:
            continue
        key, value = item[2:].split("=", 1)
        fields[key] = value

    if not record:
        tests = real_tests.load(design)
        report = real_tests.summary(tests)
        return {"status": "ok", "project_id": args[0], "tests": report,
                "records": [t.to_dict() for t in tests]}

    result = str(fields.get("result") or "unknown").strip().lower()
    if result not in real_tests.RESULTS:
        return {"status": "failed",
                "findings": [f"--result={result!r} 非法（可用 {real_tests.RESULTS}）"]}
    repro_raw = str(fields.get("reproduced") or "").strip().lower()
    repro: bool | None = None
    if repro_raw in ("true", "1", "yes"):
        repro = True
    elif repro_raw in ("false", "0", "no"):
        repro = False
    evidence: dict[str, Any] = {}
    if fields.get("video"):
        evidence["video"] = fields["video"]
    if fields.get("frames"):
        evidence["frames"] = [x for x in fields["frames"].split(",") if x]
    if fields.get("contact-sheet"):
        evidence["contact_sheet"] = fields["contact-sheet"]

    record_obj, problems = real_tests.add(
        design,
        test=str(fields.get("test") or ""),
        result=result,
        observed=str(fields.get("observed") or ""),
        implicated=[x for x in str(fields.get("implicated") or "").split(",") if x],
        evidence=evidence,
        subject=str(fields.get("subject") or ""),
        sim_params=str(fields.get("sim-params") or ""),
        sim_reproduced=repro,
        by=str(fields.get("by") or ""),
    )
    blocking = [p for p in problems if "警告" not in p]
    if blocking:
        return {"status": "failed", "project_id": args[0],
                "findings": problems, "not_written": True,
                "draft": record_obj.to_dict()}
    store.save(design)
    return {"status": "ok", "project_id": args[0],
            "recorded": record_obj.to_dict(), "warnings": problems,
            "summary": real_tests.summary(real_tests.load(design))}


def cmd_calibrate(args: list[str]) -> dict[str, Any]:
    """``calibrate <id> --test=pt-001 --candidates='[{"key":值},…]'
    [--old=<另一个工程>] [--archive] [--date=YYYY-MM-DD]``

    用一条实物失败校准仿真参数。**采纳标准是「旧设计在新参数下复现录像里的失败」**，
    不是「新设计能过」。
    """
    from .design import sim_calibration, sim_params
    from .design import real_tests

    if not args or args[0].startswith("-"):
        return {"status": "ok", **sim_calibration.describe()}
    store, design = _load(args[0])
    fields: dict[str, str] = {}
    for item in args[1:]:
        if item.startswith("--") and "=" in item:
            key, value = item[2:].split("=", 1)
            fields[key] = value
    test_id = str(fields.get("test") or "")
    raw = str(fields.get("candidates") or "")
    if not raw:
        return {"status": "not_ready",
                "findings": ["--candidates 必填：JSON 数组，每个元素是一组要试的参数"]}
    try:
        candidates = json.loads(raw)
    except ValueError as exc:
        return {"status": "failed", "findings": [f"--candidates 不是 JSON：{exc}"]}
    if isinstance(candidates, dict):
        candidates = [candidates]
    if not isinstance(candidates, list):
        return {"status": "failed", "findings": ["--candidates 必须是数组"]}

    # 旧设计：给了 --old 就读那个工程；否则用同一个（如实说明）
    old_design = design
    old_note = ""
    old_id = str(fields.get("old") or "")
    if old_id:
        _, old_design = _load(old_id)
    else:
        old_note = ("没给 --old，旧设计与新设计是同一份："
                    "只能验「参数能不能复现这条失败」，验不了「新设计是否通过」")

    params = sim_params.effective(design)
    result = sim_calibration.calibrate(
        old_design, design, test_id=test_id, params=params, candidates=candidates)
    payload = {"status": "ok", "project_id": args[0], **result.to_dict()}
    if old_note:
        payload.setdefault("notes", []).append(old_note)

    # 采纳了就回写三处：设计级覆盖、原测试记录的 sim_reproduced、可选的成对存档
    if result.adopted and result.params is not None:
        merged = dict(getattr(design, "sim_params", None) or {})
        for key, value in result.adopted.items():
            merged[key] = {"value": value, "source": "measured"}
        design.sim_params = merged
        store.save(design)
        payload["written_to_design"] = sorted(result.adopted)
        if test_id and real_tests.set_reproduced(design, test_id, True):
            store.save(design)
            payload["test_marked_reproduced"] = test_id
    if "--archive" in args and result.params is not None:
        payload["archive"] = sim_calibration.archive(
            design, result.params, store.dir, date=str(fields.get("date") or ""))
    store.write("calibration.json", payload)
    return payload


def cmd_contact_sheet(args: list[str]) -> dict[str, Any]:
    """``contact-sheet <video> [--out=PATH] [--cols=4] [--frames=16]``

    视频 → 单张 tile 缩略图（ffmpeg）。**本机实测没有 ffmpeg**，
    所以这里返回的是「能直接粘走跑」的命令行 + `not_ready`。
    """
    from .design import real_tests

    if not args or args[0].startswith("-"):
        return {"status": "ok", **real_tests.describe()}
    out = ""
    cols, frames = 4, 16
    for item in args[1:]:
        if item.startswith("--out="):
            out = item.split("=", 1)[1]
        elif item.startswith("--cols="):
            cols = int(item.split("=", 1)[1])
        elif item.startswith("--frames="):
            frames = int(item.split("=", 1)[1])
    return real_tests.contact_sheet(args[0], out, columns=cols, frames=frames)


def cmd_req_draft(args: list[str]) -> dict[str, Any]:
    """``req-draft <id> [--set=<json>] [--validate] [--apply] [--confirm]
    [--board=<kind>] [--title=<t>]``

    **从零开局**：工程还不存在时，草稿写到 `<工作区>/_drafts/<id>.json`；
    `--apply --confirm` 会顺手建一个空工程（`--board` 默认 `custom` → 50×30 通用板），
    再写 `req.json`。所以"一句话 → 工程 + 需求"是一条命令链。

    需求草稿（P5）：模型产出的结构化需求**先落成草稿**，校验通过 + 人确认
    之后才成为 `generate` 的输入（`req.json`）。三个动作：

    * `--set=<json>` 写草稿（模型可反复改）
    * `--validate`    只校验，不落盘
    * `--apply`       把草稿变成真需求 —— **必须同时 `--confirm`**

    不给动作时返回 `describe()` + 当前草稿与校验结果。
    """
    from .design import req_draft as rd

    if not args or args[0].startswith("-"):
        # `--list` 单独可用：列工作区草稿（从零开局时工程还不存在的那些）
        if "--list" in args:
            rows = rd.list_workspace()
            return {"status": "ok", "scope": "workspace",
                    "count": len(rows), "drafts": rows,
                    "note": (f"这些草稿在 <工作区>/{rd.DRAFT_DIR}/ 下，"
                             "apply --confirm 会建工程") if rows
                    else "工作区里没有草稿",
                    **rd.describe()}
        return {"status": "ok", **rd.describe()}
    store = _store(args[0])
    payload = ""
    action = ""
    confirm = False
    board = "custom"
    title = ""
    for item in args[1:]:
        if item.startswith("--set="):
            payload = item.split("=", 1)[1]
            action = action or "draft"
        elif item.startswith("--validate"):
            action = "validate"
        elif item.startswith("--apply"):
            action = "apply"
        elif item.startswith("--confirm"):
            confirm = True
        elif item.startswith("--board="):
            board = item.split("=", 1)[1]
        elif item.startswith("--title="):
            title = item.split("=", 1)[1]

    if action == "draft":
        try:
            data = json.loads(payload)
        except ValueError as exc:
            return {"status": "failed",
                    "findings": [f"--set 需要一个 JSON 对象：{exc}"]}
        if not isinstance(data, dict):
            return {"status": "failed", "findings": ["--set 必须是 JSON 对象"]}
        saved = rd.save(store, rd.ReqDraft.from_dict(data))
        report = saved["validation"]
        out = {
            "status": "ok" if report["ok"] else "failed",
            "project_id": store.id, "action": "draft", "file": saved["file"],
            # **scope 必须说清**：工程在 → 工程目录；工程还没有 → 工作区草稿。
            # 不写的话人不知道这份草稿在"还没有工程"时放到了哪。
            "scope": saved.get("scope"), "path": saved.get("path"),
            "validation": report, "draft": rd.load(store).to_dict(),
            "findings": [p["message"] for p in report["problems"]],
        }
        if saved.get("scope") == "workspace":
            out["note"] = ("这个工程还不存在：草稿写在工作区 "
                           f"{rd.DRAFT_DIR}/ 下。apply --confirm 时会顺手建工程。")
        return out

    if action == "validate":
        draft = rd.load(store)
        if draft is None:
            return {"status": "not_ready",
                    "reason": f"没有草稿（{rd.DRAFT_FILE} 不存在）"}
        report = rd.validate(draft)
        return {"status": "ok" if report["ok"] else "failed",
                "project_id": store.id, "action": "validate",
                "validation": report, "draft": draft.to_dict(),
                "findings": [p["message"] for p in report["problems"]]}

    if action == "apply":
        result = rd.apply(store, confirm=confirm, board=board, title=title)
        out = {"project_id": store.id, "action": "apply", **result}
        if result.get("status") != "ok":
            out.setdefault("findings", []).append(result.get("reason") or "")
        return out

    # 无动作：报当前状态（草稿在不在、过没过、应用了没）
    draft = rd.load(store)
    requirement = store.read(rd.REQ_FILE)
    report = rd.validate(draft) if draft is not None else None
    return {
        "status": "ok", "project_id": store.id,
        "draft": None if draft is None else draft.to_dict(),
        "validation": report,
        "scope": rd.draft_scope(store),
        "project_exists": store.exists(),
        "applied": isinstance(requirement, dict),
        "requirement": requirement if isinstance(requirement, dict) else None,
        **rd.describe(),
    }


def cmd_fw_matrix(args: list[str]) -> dict[str, Any]:
    """``firmware <id> [--ioc=PATH] [--src=DIR_or_file] [--ref=U1]``

    固件矩阵：**引脚 × (硬件配置 / 固件使用 / 结论)**，每脚一行。

    与 `pinmap-check` 的分工：那个只报三张**问题表**，没有问题的脚不在任何
    表里；这个按脚一行，才能回答"这个脚配没配、用没用"。
    判定口径仍然只有 `pinmap.crosscheck` 一处，这里只做投影。
    """
    from .design import firmware_matrix as matrix_module
    from .design.fwgen import pinmap as pm_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **matrix_module.describe()}
    store, design = _load(args[0])
    ioc, ref, sources = "", "U1", []
    for item in args[1:]:
        if item.startswith("--ioc="):
            ioc = item.split("=", 1)[1]
        elif item.startswith("--ref="):
            ref = item.split("=", 1)[1]
        elif item.startswith("--src="):
            sources.extend(x for x in
                           item.split("=", 1)[1].split(",") if x.strip())

    ioc, options = _resolve_ioc(store, ioc)
    if options:
        return {"status": "not_ready", "project_id": store.id,
                "findings": [f"工作区里有 {len(options)} 个 .ioc，"
                             f"用 --ioc=<路径> 指定"],
                "options": options}
    pm_obj, info = _pinmap_from_project(design, store.dir, ioc, ref)
    if pm_obj is None:
        return {"status": "not_ready", "project_id": store.id, "info": info,
                "findings": [info.get("reason") or "没有可用的硬件引脚配置"]}

    roots: list[Path] = []
    for raw in sources:
        candidate = _resolve_under(store.dir, raw)
        if candidate.is_dir():
            roots.extend(sorted(p for p in candidate.rglob("*")
                                if p.suffix.lower() in (".c", ".h", ".cpp", ".hpp")))
        elif candidate.is_file():
            roots.append(candidate)
    extracted = pm_module.extract_uses(roots, root=store.dir)
    if extracted.get("status") != "ok":
        return {"status": "not_ready", "project_id": store.id,
                "mcu": pm_obj.mcu.name, "extract": extracted,
                "findings": [extracted.get("reason") or "固件侧没有可核对的内容"]}

    ioc_path = _resolve_under(store.dir, ioc) if ioc else _first_ioc(store.dir)
    if ioc_path is not None:
        pins = pm_module.parse_pins(ioc_path)
        basis = Path(ioc_path).name
    else:
        pins, basis = pm_obj, "design.json"

    payload = matrix_module.matrix(pins, extracted["uses"], source=basis)
    # `crosscheck` 的三态是 ok/fail（判定用语），CLI 的是 ok/not_ready/failed。
    # **先摘掉再翻译**：否则 `**payload` 会把 status 覆盖回 "fail"（P20 踩过）。
    verdict = str(payload.pop("status", "ok"))
    out = {
        "status": "ok" if verdict in ("ok", "not_ready") else "failed",
        "verdict": verdict,
        "project_id": store.id,
        **payload,
        "mcu": pm_obj.mcu.name,
        "unpaired": extracted.get("unpaired") or [],
        # **键名是 `files`，不是 `scanned`**：`extract_uses` 的返回值里
        # 那个计数器叫 `files`（AGENTS §3.2：猜字段名，tsc 抓不到）。
        # 这个数字必须带出来——否则"扫了 0 个文件"和"扫到了但没用引脚"
        # 在报告里长得一模一样（§0.2）。
        "scanned": int(extracted.get("files") or 0),
        "unpaired_count": int(extracted.get("unpaired_count") or 0),
    }
    store.write("firmware_matrix.json", out)
    return out


def cmd_sensitivity(args: list[str]) -> dict[str, Any]:
    """``sensitivity <id> [--verdict] [--keys k1,k2]`` —— 参数敏感度（P3）。

    默认只做**模型层**：扰动每个参数，看 MJCF 文本变不变。不需要 MuJoCo。

    `--verdict` 再跑**判定层**：只对模型层敏感的参数跑 V7，看结论翻不翻。
    没有 MuJoCo 时那一层是 `unknown`，**不是「不敏感」**。

    报四类，各报各的：`wired`（真的驱动模型）/ `wired_noop`（读了但本设计
    里没有对应机制）/ `unwired`（**没有任何代码读**）/ `missing`（代码读了
    但表里没这一项）。
    """
    from .design import sensitivity as sensitivity_module
    from .design import sim_params

    if not args:
        return {"status": "ok", **sensitivity_module.describe()}
    store, design = _load(args[0])
    params = sim_params.effective(design)
    keys = None
    if "--keys" in args:
        raw = args[args.index("--keys") + 1] if len(args) > args.index("--keys") + 1 else ""
        keys = [k.strip() for k in raw.split(",") if k.strip()]

    report = sensitivity_module.report(design, params, keys=keys)
    report["project"] = store.id
    if "--verdict" in args:
        wired = [row["key"] for row in report["priority"]]
        report["verdict_layer"] = sensitivity_module.sweep_verdict(
            design, params, keys=wired or keys)
    return report


def cmd_entity(args: list[str]) -> dict[str, Any]:
    """``entity <id> [<ref>]`` —— 一个元件的完整画像（P2 按对象聚合）。

    不给 ref 时返回**紧凑索引**（每个位号一行），供 2D 画布着色与列表用。
    给 ref 时返回完整画像：断言 / 归因 / 检查 / 实测 / 所属单元 / 网表。

    只聚合已有产物，**不跑任何判定**——打开一个元件不该触发体素几何。
    """
    from .design import entity as entity_module

    if not args:
        return {"status": "ok", **entity_module.describe()}
    store, design = _load(args[0])
    claims = store.read("claims.json")
    justify = store.read("justify.json")
    checks = store.read("checks.json")
    units = store.read("units.json")

    if len(args) == 1:
        return {"status": "ok", "project": store.id,
                "entities": entity_module.index(design, claims=claims,
                                                checks=checks)}

    profile = entity_module.profile(design, args[1], claims=claims,
                                    justify=justify, checks=checks, units=units)
    payload: dict[str, Any] = {"project": store.id, **profile}
    if profile.get("status") == "failed":
        return payload
    return payload


def cmd_verdict(args: list[str]) -> dict[str, Any]:
    """``verdict <id>`` —— 一屏结论：九层横条 + 六类计数 + 排好序的下一步。

    只读已有产物，**不跑判定**（`ladder` 才跑）。所以它很快，
    可以随时看；但也因此可能是**过期的**——`stale=true` 就是那个意思。
    """
    from .design import verdict as verdict_module

    if not args:
        return {"status": "ok", **verdict_module.describe()}
    return verdict_module.compose(_store(args[0]))


def cmd_agent(args: list[str]) -> dict[str, Any]:
    """``agent "<任务>" [--project=] [--mode=read-only|ask|auto] [--script=FILE]
    [--journal=PATH] [--resume=PATH] [--max-steps=N] [--approve=all|none]``

    跑一轮 agent 循环。**模型永远是可替换的**：

    * `--script=<file>`：用 `scripts/fake_model.ScriptedClient` 读一份脚本
      （JSON 数组，每项 `{"tool": "...", "args": {...}}` 或 `{"text": "..."}`）。
      **0 token**，能进回归。
    * 不给 `--script` → `not_ready` 并说明"没有模型"（**不假装跑过**）。

    审批：`--approve=all` 表示自动同意所有 ask（**仅用于回归**）；
    默认没有审批回调 → 需要人的档一律拒绝。
    """
    from .design import agent_loop, agent_tools
    from .scripts import fake_model

    if not args or args[0].startswith("-"):
        return {"status": "ok", "loop": agent_loop.describe(),
                "tools": agent_tools.describe()}

    task = args[0]
    fields: dict[str, str] = {}
    for item in args[1:]:
        if item.startswith("--") and "=" in item:
            key, value = item[2:].split("=", 1)
            fields[key] = value
    mode = str(fields.get("mode") or "auto")
    project = str(fields.get("project") or "")
    max_steps = int(fields.get("max-steps") or agent_loop.MAX_STEPS)

    resume_path = str(fields.get("resume") or "")
    script_path = str(fields.get("script") or "")
    if not resume_path and not script_path:
        return {"status": "not_ready",
                "findings": ["没有模型：给 --script=<file>（假模型，0 token）"
                             "或用 --resume=<journal> 接着跑已中断的会话"],
                "modes": ["read-only", "ask", "auto"]}

    client = None
    if script_path:
        from pathlib import Path
        try:
            steps = json.loads(Path(script_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"status": "failed", "findings": [f"读不到脚本：{exc}"]}
        built = []
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            if step.get("tool"):
                built.append(fake_model.call(str(step["tool"]),
                                             **dict(step.get("args") or {})))
            elif "text" in step:
                built.append(fake_model.text(str(step["text"])))
        client = fake_model.ScriptedClient(built)

    approve = str(fields.get("approve") or "")
    approval = (lambda request: True) if approve == "all" else None

    default_journal = ""
    if project:
        default_journal = str(_store(project).path("agent_journal.jsonl"))
    journal = str(fields.get("journal") or default_journal)

    if resume_path:
        report = agent_loop.resume(resume_path, client, mode=mode,
                                   approval=approval, max_steps=max_steps,
                                   out=journal or None)
    else:
        report = agent_loop.run(client, task, project=project, mode=mode,
                                journal=journal or None, approval=approval,
                                max_steps=max_steps)
    payload = {"status": report.get("status", "?"), **report}
    if project:
        payload["project_id"] = project
    return payload


def cmd_units(args: list[str]) -> dict[str, Any]:
    """``units <id> [--apply]`` —— 单元层：把设计切成功能单元并判角色。

    `--apply` 把结果写进 `design.json` 的 `units_list`（默认只读推导）。
    """
    from .design import units as units_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **units_module.describe()}

    store, design = _load(args[0])
    report = units_module.report(design)
    report["project"] = args[0]
    store.write("units.json", report)

    applied = False
    if "--apply" in args:
        units_module.apply(design)
        store.save(design)
        applied = True

    return {"status": "ok" if report["ok"] else "not_ready",
            "applied": applied, **report}


def cmd_links(args: list[str]) -> dict[str, Any]:
    """``links <id> [--infer] [--apply]`` —— 板间链路四条检查。

    默认只检查 `design.json` 里**声明**的链路（`links_list`）。
    `--infer` 额外把从网表推出的候选一起看（候选只提示，不判 fail）。
    `--apply --infer` 把推断结果写进 `links_list`（source 保持 inferred）。
    """
    from .design import links as links_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **links_module.describe()}

    store, design = _load(args[0])
    report = links_module.report(design)
    report["project"] = args[0]
    store.write("links.json", report)

    applied = False
    if "--apply" in args and report.get("inferred"):
        merged = list(design.links_list or []) + [
            {k: v for k, v in link.items()}
            for link in report["inferred"]]
        design.links_list = merged
        store.save(design)
        applied = True

    # 状态：有声明链路才可能"通过"；表为空是 not_ready，不是 ok。
    status = "ok"
    if report.get("status") == "not_ready":
        status = "not_ready"
    elif not report.get("ok"):
        status = "failed"

    return {"status": status, "applied": applied, **report}


def cmd_coverify(args: list[str]) -> dict[str, Any]:
    """``coverify <id>`` —— 协同验证：需求数值 vs 硬件能力（8 条 CV 检查）。

    ## 为什么要给它一条独立命令

    `coverify` 的结果原来**只出现在 `snapshot` 的返回值里，从不落盘**。
    交接层 `handoff` 要找"机器用了哪些默认值"时，去 `budget.json` 里
    翻 `coverify` 字段——那个字段从来不存在。于是交接单的第三类
    （"机器用了默认值"）**永远是 0 条**，而实际上 `示例-单板` 有
    7 条 `not_verified`：指向精度、跟踪速率、探测距离、链路带宽、
    延迟全部是拿默认值算的。

    这属于「静默丢项」：表格空着，人以为"没有默认值"，
    真相是"有 7 个，但没人去看"。给它独立产物，交接层读得到。

    写 `coverify.json`。
    """
    if not args or args[0].startswith("-"):
        from .design import coverify as coverify_module
        return {"status": "ok", **coverify_module.describe()}

    store, design = _load(args[0])
    from .design import coverify as coverify_module

    try:
        report = coverify_module.run(design)
    except Exception as exc:                                  # noqa: BLE001
        return {"status": "failed",
                "findings": [f"{type(exc).__name__}: {exc}"]}

    payload = {**report.to_dict(),
               "summary": coverify_module.summarise(report),
               "project": args[0]}
    store.write("coverify.json", payload)
    return {"status": "ok" if report.ok else "failed", **payload}


def cmd_project2d(args: list[str]) -> dict[str, Any]:
    from .design.projection import to_2d
    if not args:
        return {"status": "failed", "findings": ["project2d 需要 <id>"]}
    _store_, design = _load(args[0])
    return {"status": "ok", "projection": to_2d(design)}


def cmd_scene3d(args: list[str]) -> dict[str, Any]:
    if not args:
        return {"status": "failed", "findings": ["scene3d 需要 <id>"]}
    _store_, design = _load(args[0])
    return {"status": "ok", "scene": scene3d(design)}


def cmd_budget(args: list[str]) -> dict[str, Any]:
    from .design import budgets, discipline

    if not args:
        return {"status": "failed", "findings": ["budget 需要 <id>"]}
    store, design = _load(args[0])
    if "--limits" in args:
        try:
            limits = json.loads(args[args.index("--limits") + 1])
        except (IndexError, ValueError) as exc:
            return {"status": "failed", "findings": [f"--limits 需要 JSON：{exc}"]}
        budgets.set_limits(design, limits)
        store.save(design)
        discipline.append(store, "cli", "budget.limits", {"limits": limits})
    try:
        result = budgets.evaluate(design)
    except RuntimeError as exc:
        return {"status": "not_ready", "findings": [str(exc)]}
    store.write("budget.json", result)
    return {"status": "ok", **result}


def cmd_deferred(args: list[str]) -> dict[str, Any]:
    from .design import discipline

    if not args:
        return {"status": "failed", "findings": ["deferred 需要 <id>"]}
    project_id = args[0]
    store = _store(project_id)
    if not store.exists():
        return {"status": "not_ready", "findings": [f"工程 {project_id!r} 不存在"]}
    ledger = discipline.ledger_from_json(store.read("deferred.json"))
    action = args[1] if len(args) > 1 else "show"

    if action == "lock":
        if len(args) < 4:
            return {"status": "failed",
                    "findings": ["deferred <id> lock <name> <value>"]}
        try:
            ledger.lock(args[2], " ".join(args[3:]))
        except ValueError as exc:
            return {"status": "failed", "findings": [str(exc)]}
        store.write("deferred.json", discipline.ledger_to_json(ledger))
        discipline.append(store, "cli", "deferred.lock",
                          {"name": args[2], "value": " ".join(args[3:])})
    elif action == "defer":
        if len(args) < 6:
            return {"status": "failed",
                    "findings": ["deferred <id> defer <kind> <ref> <why...>"]}
        try:
            ledger.defer(args[2], args[3], " ".join(args[4:]))
        except ValueError as exc:
            return {"status": "failed", "findings": [str(exc)]}
        store.write("deferred.json", discipline.ledger_to_json(ledger))
        discipline.append(store, "cli", "deferred.defer",
                          {"kind": args[2], "ref": args[3]})
    elif action == "auto":
        design = store.load()
        locked = discipline.auto_lock(design, ledger)
        store.write("deferred.json", discipline.ledger_to_json(ledger))
        return {"status": "ok", "locked": locked,
                **discipline.ledger_to_json(ledger), **discipline.gate(ledger)}
    elif action != "show":
        return {"status": "failed",
                "findings": [f"未知 deferred 动作 {action!r}（show/lock/defer/auto）"]}

    return {"status": "ok", **discipline.ledger_to_json(ledger),
            **discipline.gate(ledger)}


def cmd_trace(args: list[str]) -> dict[str, Any]:
    from .design import discipline

    if not args:
        return {"status": "failed", "findings": ["trace 需要 <id>"]}
    store = _store(args[0])
    if not store.exists():
        return {"status": "not_ready", "findings": [f"工程 {args[0]!r} 不存在"]}
    action = args[1] if len(args) > 1 else "list"

    if action == "verify":
        return {"status": "ok", **discipline.verify(store)}
    if action == "append":
        if len(args) < 4:
            return {"status": "failed",
                    "findings": ["trace <id> append <type> <payload-json>"]}
        try:
            payload = json.loads(args[3])
        except ValueError as exc:
            return {"status": "failed", "findings": [f"payload 需要 JSON：{exc}"]}
        return {"status": "ok",
                **discipline.append(store, "cli", args[2], payload)}
    rows = store.read("trace.json") or []
    return {"status": "ok", "events": rows, "count": len(rows),
            "chain_ok": discipline.verify(store)["ok"]}


def cmd_crosscheck(args: list[str]) -> dict[str, Any]:
    from .design import crosscheck, discipline

    if not args:
        return {"status": "failed", "findings": ["crosscheck 需要 <id>"]}
    store, design = _load(args[0])
    overlay = None
    if "--overlay" in args:
        try:
            from pathlib import Path
            overlay = json.loads(Path(args[args.index("--overlay") + 1])
                                 .read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "findings": [f"读不到 overlay：{exc}"]}
    result = crosscheck.run_crosscheck(design, overlay=overlay)
    store.write("crosscheck.json", result)
    discipline.append(store, "cli", "crosscheck.run",
                      {"mismatches": result["summary"]["mismatches"]})
    return {"status": "ok", **result}


def cmd_export(args: list[str]) -> dict[str, Any]:
    from .design import discipline, fabrication

    if not args:
        return {"status": "failed", "findings": ["export 需要 <id>"]}
    store, design = _load(args[0])
    try:
        discipline.assert_deliverable(store)
    except RuntimeError as exc:
        store.write("export.json", {"ok": False, "blocked": True,
                                    "message": str(exc)})
        return {"status": "not_ready", "blocked": True, "findings": [str(exc)]}
    ledger = discipline.ledger_from_json(store.read("deferred.json"))
    gate_message = discipline.gate(ledger)["message"]
    result = fabrication.export_bundle(design, store.dir / "fab",
                                       gate_message=gate_message)
    store.write("export.json", result)
    discipline.append(store, "cli", "export.bundle",
                      {"path": result["path"], "written": result["written"]})
    return {"status": "ok", **result}


def cmd_evidence(args: list[str]) -> dict[str, Any]:
    from .design import discipline

    if not args:
        return {"status": "failed", "findings": ["evidence 需要 <id>"]}
    store, design = _load(args[0])
    return {"status": "ok", **discipline.evidence(store, design)}


def cmd_move(args: list[str]) -> dict[str, Any]:
    """改 placement：``move <id> <ref> <x_mm> <y_mm> [rot] [z_mm]``。"""
    from .design import discipline
    from .design.model import Placement

    if len(args) < 4:
        return {"status": "failed",
                "findings": ["move 需要 <id> <ref> <x_mm> <y_mm> [rot] [z_mm]"]}
    store, design = _load(args[0])
    part = design.part(args[1])
    if part is None:
        return {"status": "failed", "findings": [f"没有元件 {args[1]!r}"]}
    try:
        x_mm = float(args[2])
        y_mm = float(args[3])
        rot = int(args[4]) if len(args) > 4 else part.placement.rotation_z
        z_mm = float(args[5]) if len(args) > 5 else part.placement.z_mm
    except ValueError as exc:
        return {"status": "failed", "findings": [f"参数不是数字：{exc}"]}
    part.placement = Placement(x_mm=x_mm, y_mm=y_mm, z_mm=z_mm,
                               rotation_z=rot, mount=part.placement.mount)
    store.save(design)
    discipline.append(store, "editor", "part.moved",
                      {"ref": part.ref, "x_mm": x_mm, "y_mm": y_mm,
                       "rotation_z": part.placement.rotation_z})
    return {"status": "ok", "part": part.to_dict(),
            "problems": design.validate()}


def cmd_addpart(args: list[str]) -> dict[str, Any]:
    """加元件：``addpart <id> <ref> <kind> [role] [x] [y] [rot]``。

    尺寸从内置 catalog 查；查不到就是 0 尺寸 + ``estimated``，面板会单列。
    """
    from .design import discipline
    from .design.catalog import part_size
    from .design.model import Part, Placement

    if len(args) < 3:
        return {"status": "failed",
                "findings": ["addpart 需要 <id> <ref> <kind> [role] [x] [y] [rot]"]}
    store, design = _load(args[0])
    if design.part(args[1]) is not None:
        return {"status": "failed", "findings": [f"元件 {args[1]!r} 已存在"]}
    info = part_size(args[2])
    try:
        role = args[3] if len(args) > 3 else ("connector" if info["known"] and
                                              args[2].lower() in ("connector", "header")
                                              else "component")
        x_mm = float(args[4]) if len(args) > 4 else 0.0
        y_mm = float(args[5]) if len(args) > 5 else 0.0
        rot = int(args[6]) if len(args) > 6 else 0
    except ValueError as exc:
        return {"status": "failed", "findings": [f"参数不合法：{exc}"]}
    part = Part(ref=args[1], kind=args[2], label=args[2], role=role,
                size_mm=dict(info["size_mm"]),
                physical_source=info["physical_source"],
                placement=Placement(x_mm=x_mm, y_mm=y_mm, rotation_z=rot))
    design.add_part(part)
    store.save(design)
    discipline.append(store, "editor", "part.added",
                      {"ref": part.ref, "kind": part.kind,
                       "physical_source": part.physical_source})
    return {"status": "ok", "part": part.to_dict(),
            "size_known": info["known"], "problems": design.validate()}


def cmd_delpart(args: list[str]) -> dict[str, Any]:
    from .design import discipline

    if len(args) < 2:
        return {"status": "failed", "findings": ["delpart 需要 <id> <ref>"]}
    store, design = _load(args[0])
    part = design.part(args[1])
    if part is None:
        return {"status": "failed", "findings": [f"没有元件 {args[1]!r}"]}
    design.parts = [p for p in design.parts if p.ref != args[1]]
    design.nets = [
        {**entry, "pins": [pin for pin in entry.get("pins", [])
                           if pin.get("ref") != args[1]]}
        for entry in design.nets
    ]
    design.nets = [entry for entry in design.nets if entry.get("pins")]
    store.save(design)
    discipline.append(store, "editor", "part.deleted", {"ref": args[1]})
    return {"status": "ok", "problems": design.validate()}


def cmd_setpart(args: list[str]) -> dict[str, Any]:
    """改元件字段：``setpart <id> <ref> <json>``（size_mm/label/role/source…）。"""
    from .design import discipline
    from .design.model import DesignError

    if len(args) < 3:
        return {"status": "failed", "findings": ["setpart 需要 <id> <ref> <json>"]}
    store, design = _load(args[0])
    part = design.part(args[1])
    if part is None:
        return {"status": "failed", "findings": [f"没有元件 {args[1]!r}"]}
    try:
        patch = json.loads(args[2])
    except ValueError as exc:
        return {"status": "failed", "findings": [f"patch 需要 JSON：{exc}"]}
    if not isinstance(patch, dict):
        return {"status": "failed", "findings": ["patch 必须是对象"]}
    try:
        for key in ("label", "role", "lcsc", "kind"):
            if key in patch and isinstance(patch[key], str):
                setattr(part, key, patch[key])
        if "physical_source" in patch:
            from .design.model import PHYSICAL_SOURCES
            if patch["physical_source"] not in PHYSICAL_SOURCES:
                raise DesignError(f"physical_source 非法：{patch['physical_source']!r}")
            part.physical_source = patch["physical_source"]
        if "size_mm" in patch:
            part.size_mm = {
                "w": float(patch["size_mm"].get("w", part.size_mm["w"])),
                "h": float(patch["size_mm"].get("h", part.size_mm["h"])),
                "d": float(patch["size_mm"].get("d", part.size_mm["d"])),
            }
            part.__post_init__()
        if isinstance(patch.get("electrical"), dict):
            part.electrical = {**part.electrical, **patch["electrical"]}
    except (DesignError, ValueError, TypeError, AttributeError) as exc:
        return {"status": "failed", "findings": [str(exc)]}
    store.save(design)
    discipline.append(store, "editor", "part.updated",
                      {"ref": part.ref, "fields": sorted(patch)})
    return {"status": "ok", "part": part.to_dict(),
            "problems": design.validate()}


def cmd_formats(_args: list[str]) -> dict[str, Any]:
    from .design.importers import supported_formats
    return {"status": "ok", "formats": supported_formats()}


def cmd_boards(_args: list[str]) -> dict[str, Any]:
    from .design.boards import all_presets
    return {"status": "ok", "boards": all_presets()}


def cmd_catalog(_args: list[str]) -> dict[str, Any]:
    from .design.catalog import catalogue
    return {"status": "ok", "parts": catalogue()}


def cmd_snapshot(args: list[str]) -> dict[str, Any]:
    if not args:
        return {"status": "failed", "findings": ["snapshot 需要 <id>"]}
    pitch = 0.5
    if "--pitch" in args:
        try:
            pitch = float(args[args.index("--pitch") + 1])
        except (IndexError, ValueError):
            return {"status": "failed", "findings": ["--pitch 需要一个数字"]}
    return snapshot(args[0], pitch_mm=pitch)


def cmd_synth(args: list[str]) -> dict[str, Any]:
    """``synth <id> <template> [--spec <json>] [--title ...]`` —— 按需求组装设计。

    走**模板**而不是让调用方自由写 JSON：每条网表的引脚名都在引脚库里，
    组装完还会自检一遍。这是"需求 → 元件+网表+板框"目前唯一有确定性保证的
    路径（覆盖范围见 `design/synthesize.py` 的模板表）。
    """
    from .design import discipline, synthesize as synth_module

    if len(args) < 2:
        return {"status": "failed",
                "findings": ["synth 需要 <id> <template> [--spec '<json>']"
                             "；可用模板：" + "、".join(sorted(synth_module.TEMPLATES))]}
    project_id, template = args[0], args[1]
    spec: dict[str, Any] = {"template": template}
    if "--spec" in args:
        try:
            extra = json.loads(args[args.index("--spec") + 1])
        except (IndexError, ValueError) as exc:
            return {"status": "failed", "findings": [f"--spec 需要 JSON：{exc}"]}
        if not isinstance(extra, dict):
            return {"status": "failed", "findings": ["--spec 必须是 JSON 对象"]}
        spec.update(extra)
    spec["template"] = template
    title = ""
    if "--title" in args:
        title = " ".join(args[args.index("--title") + 1:]).strip()

    store = _store(project_id)
    if store.exists():
        return {"status": "failed",
                "findings": [f"工程 {project_id!r} 已存在；换一个 id 或先删除"]}
    try:
        design = synth_module.synthesize(spec, design_id=project_id, title=title)
    except synth_module.SynthError as exc:
        return {"status": "not_ready", "findings": [str(exc)]}
    store.save(design)
    discipline.append(store, "cli", "design.synthesized",
                      {"template": template, "parts": len(design.parts)})
    return {"status": "ok", "template": template,
            "summary": design.summary(), "problems": design.validate(),
            "board": {"name": design.board.name,
                      "size_mm": dict(design.board.size_mm)}}


def cmd_scene(args: list[str]) -> dict[str, Any]:
    """``scene <id> [--pitch 0.5] [--out <path>]`` —— 转成 hwforge 的 scene.json。

    工作台的 `design.json` 与 hwforge 的 `scene.json` 是两套形状（mm 模型 vs
    体素场景），`simulate` / `electrical` / `coverify` 只认后者。本命令做
    这个转换，让工作台里建的板子进得了仿真链。
    """
    from .design import scene as scene_module

    if not args:
        return {"status": "failed", "findings": ["scene 需要 <id>"]}
    project_id = args[0]
    pitch = 0.5
    if "--pitch" in args:
        try:
            pitch = float(args[args.index("--pitch") + 1])
        except (IndexError, ValueError):
            return {"status": "failed", "findings": ["--pitch 需要一个数字"]}
    store, design = _load(project_id)
    payload = scene_module.to_scene(design, pitch_mm=pitch)
    out_path = None
    if "--out" in args:
        try:
            out_path = args[args.index("--out") + 1]
        except IndexError:
            return {"status": "failed", "findings": ["--out 需要一个路径"]}
    if out_path is None:
        store.write("scene.json", payload)
        out_path = str(store.path("scene.json"))
    else:
        from pathlib import Path as _Path
        written = _Path(out_path)
        written.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        written.write_text(_json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return {"status": "ok", "scene": out_path,
            "summary": scene_module.summary(payload),
            "unverified": payload.get("unverified") or []}


def cmd_survey(args: list[str]) -> dict[str, Any]:
    """``survey [--write] [--json]`` —— 勘察工作区，生成/刷新初审.md 的事实段。

    **事实**由本命令确定性生成；**判断**留给 AI（写在同一个文件的判据段）。
    分开的理由见 ``design/survey.py`` 顶部：让 AI 一次写完整个文件，它会把
    "我看到 N 个 .bin" 说成"完成度 70%"，而这句话落盘后下一轮会被当事实引用。
    """
    from .design import survey as survey_module

    if "--read" in args:
        return {"status": "ok", **survey_module.read_initial_review()}

    write = "--write" in args
    payload = survey_module.survey()
    if write:
        try:
            path = survey_module.write_initial_review(payload)
            payload["initial_review"] = str(path)
            payload["written"] = True
        except OSError as exc:
            payload["status"] = "failed"
            payload["findings"] = [f"写初审.md 失败：{exc}"]
    else:
        payload["initial_review"] = str(survey_module.initial_review_path())
        payload["written"] = False
    return payload


def cmd_log(args: list[str]) -> dict[str, Any]:
    """``log [list|read <name>|coverage|append <step> <outcome> <detail>]``。

    工程式日志：写在 ``<工作区>/logs/``，**只追加**。
    存在的首要原因是回答"哪些看了、哪些没看"——勘察器只报它数到的东西，
    用户就以为"评审过了"；实际上源码与固件从未被读取。日志把这条边界
    显式写出来，而不是让它作为一句无根据的形容词留在对话里。
    """
    from .design import log as log_module
    from .design.store import project_root

    base = project_root()
    action = args[0] if args else "list"

    if action == "append":
        if len(args) < 3:
            return {"status": "failed",
                    "findings": ["log append 需要 <step> <outcome> [detail]"]}
        detail = " ".join(args[3:]) if len(args) > 3 else ""
        try:
            path = log_module.append_activity(
                base, step=args[1], outcome=args[2], detail=detail)
        except OSError as exc:
            return {"status": "failed", "findings": [f"写日志失败：{exc}"]}
        return {"status": "ok", "path": str(path)}

    if action == "read":
        if len(args) < 2:
            return {"status": "failed", "findings": ["log read 需要 <文件名>"]}
        # 只允许读 logs/ 下的文件：拒绝任何带路径分隔符的名字。
        name = args[1]
        if "/" in name or "\\" in name or name.startswith(".."):
            return {"status": "failed", "findings": [f"非法文件名：{name!r}"]}
        path = log_module.logs_dir(base) / name
        if not path.is_file():
            return {"status": "not_ready", "findings": [f"没有日志 {name!r}"]}
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return {"status": "failed", "findings": [f"读不出：{exc}"]}
        return {"status": "ok", "name": name, "path": str(path), "text": text}

    if action == "coverage":
        current = survey_client()
        return {"status": "ok", **(current.get("coverage") or {})}

    return {"status": "ok", "logs": log_module.list_reports(base),
            "dir": str(log_module.logs_dir(base)),
            "activity": log_module.read_activity(base, limit=30)}


def survey_client() -> dict[str, Any]:
    """勘察一次（不写盘）。给 ``log coverage`` 复用，避免重复实现。"""
    from .design import survey as survey_module
    return survey_module.survey()


def cmd_serve(args: list[str]) -> dict[str, Any]:
    """``serve [status|start|stop|logs|prepare|ensure]`` —— Velxio 后端生命周期。

    实现全在 ``hw_velxio/serve.py``（进程/venv/工具链都在那一层）。这里只做
    **一次转发**：不能让 design_cli 自己再写一份进程管理，那是第二事实源。

    注意调用的是 `serve.call()` 而不是 `serve.main()` —— 后者会把 JSON 打到
    stdout 并返回退出码，套在 design_cli 里会输出两行 JSON，桥接层只取最后
    一行，于是命令的退出码与内容可能来自不同的东西。
    """
    from . import serve as serve_module

    return serve_module.call(args)


COMMANDS = {
    "boards": cmd_boards,
    "catalog": cmd_catalog,
    "formats": cmd_formats,
    "workspace": cmd_workspace,
    "new": cmd_new,
    "list": cmd_list,
    "get": cmd_get,
    "put": cmd_put,
    "import": cmd_import,
    "bom": cmd_bom,
    "snapshot": cmd_snapshot,
    "project2d": cmd_project2d,
    "scene3d": cmd_scene3d,
    "check": cmd_check,
    "ladder": cmd_ladder,
    "claims": cmd_claims,
    "assert": cmd_assert,
    "justify": cmd_justify,
    "handoff": cmd_handoff,
    "coverify": cmd_coverify,
    "sim": cmd_sim,
    "joints": cmd_joints,
    "joints-propose": cmd_joints_propose,
    "mjcf": cmd_mjcf,
    "lceda-panel": cmd_lceda_panel,
    "lceda-sch-drc": cmd_lceda_sch,
    "lceda-fab": cmd_lceda_fab,
    "lceda-geom": cmd_lceda_geom,
    "dims": cmd_dims,
    "scaffold": cmd_scaffold,
    "mech": cmd_mech,
    "dfm": cmd_dfm,
    "plan": cmd_plan,
    "interface": cmd_interface,
    "afmap": cmd_afmap,
    "pinout": cmd_pinout,
    "params": cmd_params,
    "fw-build": cmd_fw_build,
    "fw-algo": cmd_fw_algo,
    "audit": cmd_audit,
    "doctor": cmd_doctor,
    "examples": cmd_examples,
    "fixloop": cmd_fixloop,
    "emit-status": cmd_emit_status,
    "units": cmd_units,
    "links": cmd_links,
    "ioc-links": cmd_ioc_links,
    "renode": cmd_renode,
    "capability": cmd_capability,
    "graph": cmd_graph,
    "pin-assign": cmd_pin_assign,
    "assemble": cmd_assemble,
    "constraints": cmd_constraints,
    "blocks": cmd_blocks,
    "spec": cmd_spec,
    "generate": cmd_generate,
    "budget": cmd_budget,
    "move": cmd_move,
    "addpart": cmd_addpart,
    "delpart": cmd_delpart,
    "setpart": cmd_setpart,
    "deferred": cmd_deferred,
    "trace": cmd_trace,
    "crosscheck": cmd_crosscheck,
    "export": cmd_export,
    "evidence": cmd_evidence,
    "survey": cmd_survey,
    "scene": cmd_scene,
    "synth": cmd_synth,
    "roles": cmd_roles,
    "tiers": cmd_tiers,
    "firmware": cmd_firmware,
    "semantics": cmd_semantics,
    "log": cmd_log,
    "serve": cmd_serve,
    "lceda": cmd_lceda,
    "emit": cmd_emit,
    "readback": cmd_readback,
    "parts-search": cmd_parts_search,
    "parts-enrich": cmd_parts_enrich,
    "parts-promote": cmd_parts_promote,
    "pinmap-gen": cmd_pinmap_gen,
    "pinmap-check": cmd_pinmap_check,
    "fw-driver": cmd_fw_driver,
    "fw-scaffold": cmd_fw_scaffold,
    "physical": cmd_physical,
    "calibrate": cmd_calibrate,
    "contact-sheet": cmd_contact_sheet,
    "agent": cmd_agent,
    "verdict": cmd_verdict,
    "entity": cmd_entity,
    "sensitivity": cmd_sensitivity,
    "fw-matrix": cmd_fw_matrix,
    "req-draft": cmd_req_draft,
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not args or args[0] in ("-h", "--help"):
        _emit({"usage": " | ".join(sorted(COMMANDS)), "version": __version__})
        return 0
    command = COMMANDS.get(args[0])
    if command is None:
        _emit({"status": "failed",
               "findings": [f"未知命令 {args[0]!r}；可用：{', '.join(sorted(COMMANDS))}"]})
        return 1
    try:
        payload = command(args[1:])
    except FileNotFoundError as exc:
        payload = {"status": "not_ready", "findings": [str(exc)]}
    except Exception as exc:  # noqa: BLE001
        payload = {"status": "failed",
                   "findings": [f"{type(exc).__name__}: {exc}"[:1000]]}
    _emit(payload)
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
