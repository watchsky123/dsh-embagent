"""``hw_velxio.design_cli`` —— 规范模型的 Python 入口。

契约与 ``hwforge.blocks_cli`` 一致：**一次调用、一行 JSON**。

    python -m hw_velxio.design_cli boards
    python -m hw_velxio.design_cli catalog
    python -m hw_velxio.design_cli new <id> <board-kind> [title]
    python -m hw_velxio.design_cli list
    python -m hw_velxio.design_cli snapshot <id>
    python -m hw_velxio.design_cli get <id>
    python -m hw_velxio.design_cli put <id> <design.json|-> 
    python -m hw_velxio.design_cli import <id> <path> [--bom <path>]
    python -m hw_velxio.design_cli project2d <id>
    python -m hw_velxio.design_cli scene3d <id>
    python -m hw_velxio.design_cli check <id> [--pitch 0.5]
    python -m hw_velxio.design_cli budget <id> [--limits '<json>']
    python -m hw_velxio.design_cli deferred <id> [lock|defer|show] ...
    python -m hw_velxio.design_cli trace <id> [list|verify|append]
    python -m hw_velxio.design_cli crosscheck <id>
    python -m hw_velxio.design_cli export <id>
    python -m hw_velxio.design_cli evidence <id>

三种状态：``ok`` / ``not_ready``（前置缺失）/ ``failed``（跑了但出错）。
"""

from __future__ import annotations

import json
import sys
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
    """3D 渲染层要的最小几何：全部是 mm 的 AABB / 轮廓 / 路径。"""
    parts = []
    for part in design.parts:
        x0, y0, z0, x1, y1, z1 = part.bbox_mm()
        parts.append({
            "ref": part.ref,
            "label": part.label,
            "kind": part.kind,
            "role": part.role,
            "bbox_mm": [round(x0, 3), round(y0, 3), round(z0, 3),
                        round(x1, 3), round(y1, 3), round(z1, 3)],
            "rotation_z": part.placement.rotation_z,
            "physical_source": part.physical_source,
            "mount": part.placement.mount,
        })
    return {
        "units": "mm",
        "board": {
            "kind": design.board.kind,
            "name": design.board.name,
            "outline": [[round(x, 3), round(y, 3)] for x, y in design.board.outline],
            "size_mm": dict(design.board.size_mm),
            "thickness_mm": design.board.thickness_mm,
        },
        "parts": parts,
        "wires": [
            {"net": w.net,
             "path_mm": [[round(x, 3), round(y, 3), round(z, 3)]
                         for x, y, z in w.path_mm]}
            for w in design.wires
        ],
        "mount_holes": [
            {"x_mm": h.x_mm, "y_mm": h.y_mm, "diameter_mm": h.diameter_mm,
             "source": h.source}
            for h in design.board.mount_holes
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

    try:
        out["evidence"] = discipline.evidence(store, design)
    except RuntimeError as exc:
        out["evidence"] = {"status": "not_ready", "error": str(exc)}

    return out


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

def cmd_new(args: list[str]) -> dict[str, Any]:
    from .design.boards import board_preset
    from .design.model import DesignModel
    from .design.store import DesignStore

    if not args:
        return {"status": "failed", "findings": ["new 需要 <id> [board-kind] [title]"]}
    project_id = args[0]
    board_kind = args[1] if len(args) > 1 else "arduino-uno"
    title = args[2] if len(args) > 2 else project_id

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
    return {"status": "ok", "summary": design.summary(),
            "problems": design.validate()}


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

    store = _store(project_id)
    try:
        design = import_any(path, design_id=project_id)
    except FileNotFoundError as exc:
        return {"status": "not_ready", "findings": [str(exc)]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "findings": [f"{type(exc).__name__}: {exc}"]}

    unmatched: list[str] = []
    if bom_path:
        try:
            bom = import_bom(bom_path)
            unmatched = apply_bom(design, bom)
        except Exception as exc:  # noqa: BLE001
            design.warnings.append(f"BOM 未应用：{type(exc).__name__}: {exc}")

    from .design.fabrication import copy_source
    try:
        copy_source(design, path, store.dir)
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
        "auto_locked": auto_locked,
    }


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


COMMANDS = {
    "boards": cmd_boards,
    "catalog": cmd_catalog,
    "formats": cmd_formats,
    "new": cmd_new,
    "list": cmd_list,
    "get": cmd_get,
    "put": cmd_put,
    "import": cmd_import,
    "snapshot": cmd_snapshot,
    "project2d": cmd_project2d,
    "scene3d": cmd_scene3d,
    "check": cmd_check,
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
