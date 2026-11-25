"""编排器（`scaffold`）—— 一句需求 → 一份**能装、能编、能看 3D** 的半成品。

## 它解决什么

在这之前，从需求到一个"能动的半成品"要人（或模型）按顺序跑七八个命令：
`req-draft` → `generate`/`synth`/`roles` → `put` → `pinmap-gen` → `joints-propose`
→ `fw-scaffold` → `mjcf`。每一步都依赖上一步**已经落盘**，顺序错了就静默出
一份"看着有东西、其实没接上"的工程。

这个模块把那条链**固定下来**，并且做两件别处没人做的事：

1. **每一步都记账**（`scaffold.json` 里一行一段：status / counts / note）
2. **缺件不中止**（用户明确要求：所有项目都是半成品，AI 出不了成品）
   —— 缺 `.ioc` 就走网表推引脚；缺关节参数就走提案；它们进 `open_items.md`，
   不阻塞产出

## 与 `emit` 的分界

本模块**不碰立创**。`--to pcb` 由 `design_cli.cmd_scaffold` 在拿到半成品之后
再调 `emit`（那里已经有 30 秒批处理、通道选择、回读校验）。这样 `scaffold`
永远是纯离线的、可重放的。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .model import DesignModel

__all__ = ["ROUTES", "build", "open_items_markdown", "describe"]

#: 选路。**必须显式给** —— 三条路出来的东西差别很大，猜错比不做更糟。
#:
#: * `synth` —— 走模板（7 个）。确定性强，但只覆盖模板范围
#: * `generate` —— 需求 → 有限枚举候选 → 取排序第一
#: * `roles` —— 需求 → 角色 → 装配计划 → 装成设计（依赖模板库）
ROUTES = ("synth", "generate", "roles")

#: 缺件时**继续跑后面的段**吗。默认 True —— 这是用户明确要求的：
#: 所有项目都是半成品，AI 出不了成品；缺 `.ioc`/关节参数不该让产出归零。
#:
#: 它不是配置项，是**可验证的开关**：`check_p43` 会把它关掉，确认
#: "缺件不中止"这条断言真的挂在它上面（否则那条断言可能是恒真的）。
CONTINUE_ON_GAP = True


def _row(stage: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"stage": stage, "status": status, **extra}


def _req_from(requirement: Any):
    """需求可以给 dict，也可以给路径（.yaml/.yml/.json）。"""
    from . import generate as generate_module

    if requirement in (None, ""):
        return None
    if isinstance(requirement, dict):
        return generate_module.Requirement.from_dict(requirement)
    return generate_module.load_requirement(str(requirement))


def _design_synth(template: str, spec: dict[str, Any] | None,
                  project_id: str, title: str) -> tuple[DesignModel | None, str]:
    from . import synthesize as synth_module

    if not template:
        return None, "route=synth 需要 --template（可用：" + "、".join(
            sorted(synth_module.TEMPLATES)) + "）"
    payload = {"template": template, **(spec or {})}
    try:
        return synth_module.synthesize(payload, design_id=project_id,
                                       title=title), ""
    except synth_module.SynthError as exc:
        return None, str(exc)


def _design_generate(req, project_id: str, title: str,
                     max_candidates: int) -> tuple[DesignModel | None, str]:
    from . import generate as generate_module

    if req is None:
        return None, "route=generate 需要需求（req.json 或 --requirement）"
    report = generate_module.generate(req, max_candidates=max_candidates,
                                      run_ladder=False)
    candidates = report.get("candidates") or []
    if not candidates:
        return None, "枚举不出候选（看 generate 的 explain）"
    # 排序第一 = `(hard_failures, soft_failures, unknowns, cost)` 最小。
    # **不挑"看起来最好的"**，排序已经在 generate 里定死。
    best = candidates[0]
    try:
        design = generate_module.materialize(best, req)
    except Exception as exc:  # noqa: BLE001 - 候选形状变了要能说出来
        return None, f"materialize 失败：{type(exc).__name__}: {exc}"
    design.id = project_id
    if title:
        design.title = title
    return design, ""


def _design_roles(req, project_id: str, title: str) -> tuple[DesignModel | None, str]:
    from . import assemble as assemble_module
    from . import roles as roles_module

    role_list = list(getattr(req, "roles", []) or []) if req is not None else []
    if not role_list:
        return None, "route=roles 需要需求里的 roles（或 --roles）"
    plan = roles_module.plan(role_list)
    result = assemble_module.assemble_plan(
        plan, design_id=project_id, title=title or project_id)
    design = getattr(result, "design", None)
    if design is None:
        problems = list(getattr(result, "problems", []) or [])
        return None, "装配失败：" + ("; ".join(problems) or "没有产出设计")
    return design, ""


def _pinmap_for(design: DesignModel, project_dir: Path, ioc: str, ref: str,
                mcu: str = ""):
    """引脚表：`.ioc` 优先，没有就**从 design.json 的网表推**（不等人给）。"""
    from .fwgen import netlist as nl_module
    from .fwgen import pinmap as pm_module

    if ioc:
        path = Path(ioc)
        if not path.is_absolute():
            path = project_dir / path
        if path.is_file():
            pm, info = pm_module.from_ioc(path, ref=ref or "U1")
            return pm, {**info, "origin": "ioc"}
        return None, {"origin": "ioc", "reason": f"没有这个 .ioc：{path}"}
    try:
        net = nl_module.from_design(design)
        pm = pm_module.build_pinmap(net, ref=ref or None, mcu=mcu or None)
    except Exception as exc:  # noqa: BLE001
        # 判不出 MCU 是最常见的一种。把"表里有哪几个"说出来，
        # 否则调用方只会看到一句 "pass --mcu"，然后不知道能填什么。
        known = "、".join(sorted(keys := [str(k) for k in pm_module.MCUS]))
        return None, {
            "origin": "design.json",
            "reason": (f"{type(exc).__name__}: {exc}"
                       f"（引脚表只有 {len(keys)} 个型号：{known}；"
                       "开发板不是裸 MCU，要么 --mcu= 指定型号，要么给 .ioc）"),
            "mcu_options": keys,
        }
    return pm, {"origin": "design.json", "assignments": len(pm.assignments)}


def build(*, project_id: str, store, design: DesignModel | None = None,
          requirement: Any = None, route: str = "", template: str = "",
          spec: dict[str, Any] | None = None, title: str = "",
          target: str = "", drivers: list[str] | None = None,
          ioc: str = "", ref: str = "U1", mcu: str = "",
          max_candidates: int = 8, stages: tuple[str, ...] = ()) -> dict[str, Any]:
    """跑一遍编排。**不抛异常**：失败落成 stage 行。"""
    want = set(stages or ("design", "pinmap", "joints", "firmware", "mechanical"))
    rows: list[dict[str, Any]] = []
    open_items: list[dict[str, str]] = []

    def note(text: str, need: str = "") -> None:
        open_items.append({"what": text, "need": need})

    # ---- 1. 需求 ---------------------------------------------------------
    req = None
    if requirement not in (None, ""):
        try:
            req = _req_from(requirement)
            rows.append(_row("requirement", "ok",
                             counts={"roles": len(getattr(req, "roles", []) or []),
                                     "interfaces": len(getattr(req, "interfaces", []) or [])}))
        except Exception as exc:  # noqa: BLE001
            rows.append(_row("requirement", "failed",
                             note=f"{type(exc).__name__}: {exc}"))
            return {"status": "failed", "stages": rows, "open_items": open_items,
                    "note": "需求读不进来：没有产出任何半成品"}
    elif route in ("generate", "roles"):
        rows.append(_row("requirement", "not_ready",
                         note=f"route={route} 需要需求，但没有给"))
        return {"status": "failed", "stages": rows, "open_items": open_items,
                "note": "缺需求，无法选路"}
    else:
        rows.append(_row("requirement", "skipped", note="route=synth 不需要需求"))

    # ---- 2. 选路 → design.json ------------------------------------------
    if design is None:
        if route == "synth":
            design, why = _design_synth(template, spec, project_id, title)
        elif route == "generate":
            design, why = _design_generate(req, project_id, title, max_candidates)
        elif route == "roles":
            design, why = _design_roles(req, project_id, title)
        elif route == "":
            design, why = None, ("必须显式给 --route=synth|generate|roles"
                                 "（三条路出来的东西差别很大，不猜）")
        else:
            design, why = None, f"未知 route：{route!r}"
        if design is None:
            rows.append(_row("design", "failed", note=why))
            return {"status": "failed", "stages": rows, "open_items": open_items,
                    "note": why}
    problems = design.validate()
    store.save(design)
    rows.append(_row("design", "ok",
                     counts={"parts": len(design.parts), "nets": len(design.nets),
                             "boards": len(design.board_list())},
                     note=f"route={route or 'prebuilt'}"
                          + (f"；validate 有 {len(problems)} 条提示" if problems else "")))
    if problems:
        for item in problems[:12]:
            note(item, "改 design.json")

    project_dir = store.dir

    # ---- 3. 引脚表（.ioc 优先，没有就推）--------------------------------
    pm = None
    if "pinmap" in want:
        pm, info = _pinmap_for(design, project_dir, ioc, ref, mcu)
        if pm is None:
            rows.append(_row("pinmap", "not_ready", note=str(info.get("reason") or "")))
            note("引脚表推不出来：" + str(info.get("reason") or "没有网表"),
                 "补网表，或给 .ioc")
            if not CONTINUE_ON_GAP:
                return {"status": "not_ready", "stages": rows,
                        "open_items": open_items, "project": project_id,
                        "route": route or "",
                        "note": "CONTINUE_ON_GAP=False：缺引脚表就停（测试用）"}
        else:
            pinmap = {str(a.net): {"pin": str(a.pin), "function": str(a.function),
                                   "source": "proposed"}
                      for a in pm.assignments}
            design.firmware = {**(design.firmware or {}),
                               "target": target or "",
                               "mcu": pm.mcu.name,
                               "pinmap": pinmap}
            store.save(design)
            rows.append(_row("pinmap", "ok",
                             counts={"assignments": len(pm.assignments),
                                     "origin": "ioc" if info.get("origin") == "ioc"
                                               else "netlist"},
                             note=f"{pm.mcu.name}；来源 {info.get('origin')}；"
                                  "全部标 proposed（没人核对过）"))
            note(f"引脚表是按网名推的（{len(pm.assignments)} 条，source=proposed）："
                 "建议核对 MCU 的 AF 复用", "核对 firmware.pinmap")

    # ---- 4. 关节提案 -----------------------------------------------------
    if "joints" in want:
        from . import joint_propose as propose_module

        report = propose_module.propose(design)
        applied = propose_module.apply(design, report["proposals"])
        if applied:
            store.save(design)
        store.write("joints.proposed.json", report)
        rows.append(_row("joints", "ok" if report["proposals"] else "not_ready",
                         counts={"proposed": len(report["proposals"]),
                                 "applied": len(applied),
                                 "unresolved": len(report["unresolved"])},
                         note=f"提案 {len(report['proposals'])} 条"
                              f"（写入 {len(applied)}）"))
        for item in report["unresolved"][:8]:
            note(f"关节推不出（{item.get('need')}）：{item.get('why')}",
                 str(item.get("need") or ""))

    # ---- 5. 固件骨架 -----------------------------------------------------
    if "firmware" in want:
        from .fwgen import scaffold as fw_scaffold

        if pm is None:
            rows.append(_row("firmware", "not_ready", note="没有引脚表，出不了骨架"))
            note("固件骨架需要引脚表", "补网表/.ioc")
        else:
            chosen = target or fw_scaffold.default_target(pm)
            dest = project_dir / "firmware" / f"scaffold-{chosen}"
            try:
                files = fw_scaffold.scaffold(
                    pm, dest, chosen, project_id, drivers or None,
                    source=f"design.json（{pm.mcu.name}）", overwrite=True)
                rel = [str(Path(f).relative_to(project_dir)) for f in files
                       if Path(f).is_relative_to(project_dir)]
                rows.append(_row("firmware", "ok",
                                 counts={"target": chosen, "files": len(files)},
                                 note="、".join(rel[:4]) or dest.name))
            except fw_scaffold.ScaffoldError as exc:
                rows.append(_row("firmware", "not_ready", note=str(exc)))
                note(f"固件骨架出不来（{chosen}）：{exc}", "换 target 或补 MCU")

    # ---- 6. 机械模型 -----------------------------------------------------
    if "mechanical" in want:
        from . import mjcf as mjcf_module

        built = mjcf_module.build(design)
        target_xml = project_dir / "model.xml"
        target_xml.write_text(built.xml, encoding="utf-8")
        load = mjcf_module.load_check(path=str(target_xml))
        stats = built.stats()
        status = ("failed" if load["status"] == "failed"
                  else "ok" if (load["status"] == "ok"
                                and not stats["parts_skipped"]
                                and not stats["joints_skipped"]
                                and (design.joints_list or []))
                  else "not_ready")
        if not (design.joints_list or []):
            why = "没有关节表：这是静几何，不是可动的机械结构（joints 段会提案）"
        elif stats["parts_skipped"] or stats["joints_skipped"]:
            why = (f"被跳过：{stats['parts_skipped']} 零件 / "
                   f"{stats['joints_skipped']} 关节")
        else:
            why = load.get("reason") or built.summary()
        rows.append(_row("mechanical", status,
                         counts={"load": load["status"], "njnt": load.get("njnt", 0),
                                 "parts_skipped": stats["parts_skipped"],
                                 "joints_skipped": stats["joints_skipped"]},
                         note=why))
        if status != "ok":
            note(f"机械模型不完整（{stats['parts_skipped']} 零件 / "
                 f"{stats['joints_skipped']} 关节被跳过）", "补尺寸/关节参数")

    return {
        "status": "ok" if any(r["status"] == "ok" for r in rows) else "not_ready",
        "project": project_id,
        "route": route or "",
        "stages": rows,
        "open_items": open_items,
        "note": (f"{len(rows)} 段跑完；{len(open_items)} 条待办写在 open_items.md"
                 "。**半成品允许带待办 —— 不阻塞产出**"),
    }


def open_items_markdown(project_id: str, report: dict[str, Any]) -> str:
    """把 `open_items` 写成一份人读清单。**不是闸门**：不影响 status/exit code。"""
    rows = report.get("open_items") or []
    lines = [f"# {project_id} 待办清单", "",
             "> 这份清单**不阻塞**任何东西：工程已经是可用的半成品。",
             "> 每一条都是「我没把握的地方」，改完再跑一遍 `scaffold` 就会消失。", ""]
    if not rows:
        lines.append("_没有待办。_")
        return "\n".join(lines) + "\n"
    lines.extend(["| # | 待办 | 需要什么 |", "|---|---|---|"])
    for index, row in enumerate(rows, 1):
        what = str(row.get("what") or "").replace("|", "/")
        need = str(row.get("need") or "").replace("|", "/")
        lines.append(f"| {index} | {what} | {need} |")
    lines.extend(["", "## 各段状态", "", "| 段 | 状态 | 摘要 |", "|---|---|---|"])
    for stage in report.get("stages") or []:
        note = str(stage.get("note") or "").replace("|", "/")[:120]
        counts = ", ".join(f"{k}={v}" for k, v in (stage.get("counts") or {}).items())
        lines.append(f"| {stage.get('stage')} | {stage.get('status')} | "
                     f"{note}{('（' + counts + '）') if counts else ''} |")
    return "\n".join(lines) + "\n"


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.scaffold",
        "routes": list(ROUTES),
        "stages": ["requirement", "design", "pinmap", "joints", "firmware",
                   "mechanical"],
        "discipline": ("选路必须显式给；缺 .ioc 走网表、缺关节走提案；"
                       "缺件不中止，进 open_items.md（不是闸门）；"
                       "不碰立创（emit 由 cmd_scaffold 在外层调）"),
    }