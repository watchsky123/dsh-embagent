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
        from .design import assemble as assemble_module
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
    return {"status": "ok", **result}


def cmd_ladder(args: list[str]) -> dict[str, Any]:
    """``ladder <id> [--levels V0,V1] [--no-truncate] [--pitch 0.5]``。

    V0→V6 阶梯：便宜的先跑，V0/V1 硬失败就截断下游（记 `not_run`，
    **不是 pass**）。不带 id 时只输出阶梯说明。
    """
    from .design import ladder as ladder_module

    if not args or args[0].startswith("-"):
        return {"status": "ok", **ladder_module.describe()}

    levels: list[str] | None = None
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
    return {"status": "ok", **payload}


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
    return {"status": "ok", **payload}


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
    "units": cmd_units,
    "links": cmd_links,
    "capability": cmd_capability,
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
