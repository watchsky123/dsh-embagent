"""六段 `audit` —— 从需求到交接，每段一行**可复核**的结论。

## 为什么要有它

到 P46 为止，判定散在七八个产物里（`units.json` / `checks.json` /
`emit.json` / `mech.json` / `handoff.json` …），每份都只回答自己那一小段。
人问"这份工程现在到哪一步了"时，没有一个地方能一眼看全。

`audit` **不重新判定**：它只把各段已有的产物按 Traceforge 的 validate 顺序
串成一行一段，并把"这段有没有数据"与"这段结论是什么"分开说。

## 六段（顺序 = 人做事的顺序）

```
intake → architecture → parts → schematic → pcb → firmware → mechanical → release
```

每段输出：

| 字段 | 含义 |
|---|---|
| `status` | `ok` / `not_ready`（缺数据）/ `failed`（有硬问题）|
| `checked` | **查了几项**（不是"查了没有"）|
| `basis` | 结论来自哪个产物/字段 —— 没有它这条结论不可复核 |
| `findings` | 具体问题（可点到 `ref` 就带 `refs`）|

**空产物不是通过**：每段前言都先看"输入在不在"，不在就 `not_ready` 并写
清缺哪个文件（AGENTS §0.1/§0.2）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["STAGES", "audit", "describe", "render_markdown"]

#: 段的顺序。**顺序本身就是结论的一部分**：前面的段缺数据会让后面的段
#: 只能"不知道"，不会自动变成 ok。
STAGES = ("intake", "architecture", "parts", "schematic", "pcb",
          "firmware", "mechanical", "release")

_STAGE_TITLE = {
    "intake": "需求进来",
    "architecture": "单元与角色",
    "parts": "选料与尺寸",
    "schematic": "原理图与网表一致",
    "pcb": "布线/铺铜/连通 + 制造",
    "firmware": "固件与引脚对得上",
    "mechanical": "机械四条判据",
    "release": "交接单是否可打板",
}


def _row(stage: str, status: str, *, checked: Any = 0, basis: str = "",
         findings: list[dict[str, Any]] | None = None,
         note: str = "") -> dict[str, Any]:
    return {"stage": stage, "title": _STAGE_TITLE.get(stage, stage),
            "status": status, "checked": checked, "basis": basis,
            "findings": list(findings or []), "note": note}


def _stage_intake(store: Any, design: Any) -> dict[str, Any]:
    from . import req_draft as req_module

    req = store.read(req_module.REQ_FILE)
    draft = req_module.load(store)
    if isinstance(req, dict):
        roles = req.get("roles") or []
        return _row("intake", "ok", checked={"roles": len(roles),
                                             "interfaces": len(req.get("interfaces") or [])},
                    basis=req_module.REQ_FILE,
                    note=f"已确认的需求（{len(roles)} 个角色）")
    if draft is not None:
        validation = req_module.validate(draft)
        return _row("intake", "not_ready",
                    checked={"roles": len(draft.roles)},
                    basis=f"{req_module.DRAFT_FILE}（草稿，未确认）",
                    note=("草稿合法但没人确认（`req-draft --apply --confirm`）"
                          if validation.get("ok") else "草稿没通过校验"))
    return _row("intake", "not_ready", basis="没有 req.json / req_draft.json",
                note="没有需求：后面所有段都只能算「猜」的一半")


def _stage_architecture(store: Any, design: Any) -> dict[str, Any]:
    payload = store.read("units.json")
    if not isinstance(payload, dict):
        return _row("architecture", "not_ready", basis="units.json 缺失",
                    note="没跑过 `units`：说不清这块板承担什么角色")
    total = int(payload.get("total") or 0)
    by_role = payload.get("by_role") or {}
    named = int(payload.get("named_net_units") or 0)
    status = "ok" if total else "not_ready"
    return _row("architecture", status, checked={"units": total, "named": named},
                basis="units.json",
                note=(f"{total} 个单元，角色分布 {dict(list(by_role.items())[:4])}"
                      if total else "切不出单元（元件没有归属或全空）"))


def _stage_parts(store: Any, design: Any) -> dict[str, Any]:
    parts = list(getattr(design, "parts", []) or [])
    if not parts:
        return _row("parts", "not_ready", basis="design.json / parts",
                    note="一个元件都没有")
    lcsc = [p for p in parts if str(getattr(p, "lcsc", "") or "").strip()]
    sized = [p for p in parts if float(p.size_mm.get("w", 0) or 0) > 0
             and float(p.size_mm.get("h", 0) or 0) > 0]
    jlc = [p for p in parts if str(getattr(p, "jlc_class", "") or "").strip()]
    findings: list[dict[str, Any]] = []
    for part in parts:
        if part not in sized:
            findings.append({"check": "parts.size", "refs": [part.ref],
                             "message": f"{part.ref} 尺寸缺失（w/h 为 0）"})
    # **"有几颗有 lcsc"不算够**：缺一颗就下不了单（BOM 缺项）。
    # 所以 ok 的条件是**全部**都有 lcsc 与尺寸（§0.1：别把部分当全部）。
    status = "ok" if (len(sized) == len(parts)
                      and len(lcsc) == len(parts)) else "not_ready"
    return _row("parts", status,
                checked={"parts": len(parts), "with_lcsc": len(lcsc),
                         "with_size": len(sized), "with_jlc_class": len(jlc)},
                basis="design.json / parts[]",
                findings=findings[:8],
                note=(f"{len(lcsc)}/{len(parts)} 有 lcsc，{len(sized)}/{len(parts)} 有尺寸，"
                      f"{len(jlc)} 有贴片档位"))


def _stage_schematic(store: Any, design: Any) -> dict[str, Any]:
    emit = store.read("emit.json")
    if not isinstance(emit, dict):
        return _row("schematic", "not_ready", basis="emit.json 缺失",
                    note="没发射过：原理图/网表一致性没有证据")
    parity = emit.get("parity") or {}
    detail = emit.get("schematic_detail") or {}
    bad = sum(int((b or {}).get("pin_nets_bad", {}).__len__())  # noqa: PLC2801
              for b in (detail.get("boards") or []) if isinstance(b, dict))
    saved = [b for b in (detail.get("boards") or [])
             if isinstance(b, dict) and b.get("saved")]
    findings = [{"check": "parity", "refs": [],
                 "message": f"网表与设计不一致：{(parity.get('missing') or [])[:4]}"}] \
        if str(parity.get("status")) == "not_ready" else []
    status = "ok" if str(parity.get("status")) == "ok" and not bad else "not_ready"
    return _row("schematic", status,
                checked={"borads_saved": len(saved), "pin_nets_bad": bad},
                basis="emit.json / parity + schematic_detail",
                findings=findings,
                note=(f"parity={parity.get('status')}，"
                      f"{bad} 个引脚的网络对不上"))


def _stage_pcb(store: Any, design: Any) -> dict[str, Any]:
    emit = store.read("emit.json")
    if not isinstance(emit, dict):
        return _row("pcb", "not_ready", basis="emit.json 缺失",
                    note="没发射过：没有板子，就没有布线/铺铜/连通")
    verify = (emit.get("verify_detail") or {}).get("totals") or {}
    fab = emit.get("fabrication") or {}
    cur = (emit.get("drc") or {}).get("trustworthy_violations")
    nets = int(verify.get("nets") or 0)
    connected = int(verify.get("connected") or 0)
    findings: list[dict[str, Any]] = []
    if nets and connected < nets:
        findings.append({"check": "pcb.connectivity", "refs": [],
                         "message": f"{connected}/{nets} 条网连通（铺铜之后）"})
    status = ("failed" if (nets and connected < nets)
              else "ok" if str(fab.get("status")) == "ok" else "not_ready")
    return _row("pcb", status,
                checked={"nets": nets, "connected": connected,
                         "drc_trustworthy": cur if cur is not None else "unknown",
                         "dfm": str((fab.get("dfm") or {}).get("status") or "")},
                basis="emit.json / verify_detail + fabrication",
                findings=findings,
                note=(f"连通 {connected}/{nets}；制造结论 {fab.get('status')}"
                      f"（DFM {(fab.get('dfm') or {}).get('status')}）"))


def _stage_firmware(store: Any, design: Any) -> dict[str, Any]:
    payload = store.read("firmware_matrix.json")
    pinmap = (getattr(design, "firmware", None) or {}).get("pinmap") or {}
    if isinstance(payload, dict) and payload.get("counts"):
        counts = payload.get("counts") or {}
        bad = int(counts.get("ghost") or 0) + int(counts.get("direction") or 0)
        used = int(payload.get("used_pins") or 0)
        findings = [{"check": "firmware.ghost", "refs": [],
                     "message": "固件用了硬件没有的脚"}] if bad else []
        return _row("firmware", "failed" if bad else "ok",
                    checked={"pins": used, "problems": bad},
                    basis="firmware_matrix.json", findings=findings,
                    note=f"{used} 个引脚有固件使用记录，{bad} 处对不上")
    if pinmap:
        return _row("firmware", "not_ready",
                    checked={"pinmap": len(pinmap)},
                    basis="design.json / firmware.pinmap",
                    note=("有引脚表但没有固件矩阵：跑 `fw-matrix` 才知道"
                          "固件有没有用到不存在的脚"))
    return _row("firmware", "not_ready", basis="firmware_matrix.json / pinmap 都缺",
                note="既没有引脚表也没有固件矩阵")


def _stage_mechanical(store: Any, design: Any) -> dict[str, Any]:
    payload = store.read("mech.json")
    if not isinstance(payload, dict):
        return _row("mechanical", "not_ready", basis="mech.json 缺失",
                    note="没跑过 `mech`：干涉/重心/扭矩/行程都没算")
    counts = payload.get("counts") or {}
    fails = int(counts.get("fail") or 0)
    unknowns = int(counts.get("unknown") or 0)
    findings = [{"check": f"mech.{c.get('check')}", "refs": c.get("refs") or [],
                 "message": str(c.get("message") or "")}
                for c in (payload.get("checks") or [])
                if isinstance(c, dict) and c.get("status") == "fail"][:8]
    return _row("mechanical", "failed" if fails else ("not_ready" if unknowns else "ok"),
                checked=dict(counts),
                basis="mech.json", findings=findings,
                note=(f"pass {counts.get('pass')} / fail {fails} / unknown {unknowns}"
                      "（unknown 是缺数据，不是通过）"))


def _stage_release(store: Any, design: Any) -> dict[str, Any]:
    from . import handoff as handoff_module

    try:
        payload = handoff_module.build(store).to_dict()
    except Exception as exc:  # noqa: BLE001
        return _row("release", "not_ready", basis="handoff",
                    note=f"交接单生成失败：{type(exc).__name__}: {exc}")
    counts = payload.get("counts") or {}
    blocking = int(counts.get("blocking") or 0)
    missing = int(counts.get("missing_data") or 0)
    status = "failed" if blocking else ("not_ready" if missing else "ok")
    # 措辞要跟着 status 走：阻断 0 但有缺数据时，**不能说"可打板"**
    # （那正是 §0.2：空表看起来像没问题）。
    verdict_word = ("不可打板" if blocking else
                    f"阻断 0，但还有 {missing} 条缺数据" if missing else "可打板")
    return _row("release", status, checked=dict(counts),
                basis="handoff.json（现场聚合）",
                note=f"{verdict_word}（阻断 {blocking} / 缺数据 {missing}）")


_BUILDERS = {
    "intake": _stage_intake,
    "architecture": _stage_architecture,
    "parts": _stage_parts,
    "schematic": _stage_schematic,
    "pcb": _stage_pcb,
    "firmware": _stage_firmware,
    "mechanical": _stage_mechanical,
    "release": _stage_release,
}


def audit(store: Any, design: Any = None, *, stages: tuple[str, ...] = ()) -> dict[str, Any]:
    """跑六段。**不抛异常**：单段出错落成该段的一行 `not_ready`。"""
    design = design if design is not None else store.load()
    want = [s for s in (stages or STAGES) if s in _BUILDERS]
    rows: list[dict[str, Any]] = []
    for name in want:
        try:
            rows.append(_BUILDERS[name](store, design))
        except Exception as exc:  # noqa: BLE001 - 一段炸不该拖垮别的段
            rows.append(_row(name, "not_ready", basis="audit 自身",
                             note=f"{type(exc).__name__}: {exc}"))
    failed = [r for r in rows if r["status"] == "failed"]
    not_ready = [r for r in rows if r["status"] == "not_ready"]
    ok = [r for r in rows if r["status"] == "ok"]
    return {
        "status": "failed" if failed else ("not_ready" if not_ready else "ok"),
        "project": getattr(store, "id", ""),
        "stages": rows,
        "counts": {"total": len(rows), "ok": len(ok),
                   "not_ready": len(not_ready), "failed": len(failed)},
        "note": (f"{len(ok)}/{len(rows)} 段 ok"
                 + (f"；{len(failed)} 段有硬问题" if failed else "")
                 + (f"；{len(not_ready)} 段缺数据" if not_ready else "")
                 + "。**`not_ready` 不是通过**。"),
    }


def render_markdown(payload: dict[str, Any]) -> str:
    """人读版（`audit.md`）。每段都渲染，哪怕只有一句话。"""
    lines = [f"# 审计：{payload.get('project', '')}", "",
             f"> {payload.get('note', '')}", "",
             "| 段 | 结论 | 查了几项 | 依据 | 摘要 |", "|---|---|---|---|---|"]
    for row in payload.get("stages") or []:
        checked = ", ".join(f"{k}={v}" for k, v in (row.get("checked") or {}).items()
                            if not isinstance(v, (dict, list)))
        lines.append(f"| {row.get('title')} | {row.get('status')} | "
                     f"{checked or '-'} | {row.get('basis') or '-'} | "
                     f"{row.get('note') or '-'} |")
    findings = [(row.get("title"), row.get("findings") or [])
                for row in payload.get("stages") or []]
    if any(items for _, items in findings):
        lines.extend(["", "## 具体问题", ""])
        for title, items in findings:
            for item in items:
                lines.append(f"* **{title}**：{item.get('message')}")
    return "\n".join(lines) + "\n"


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.audit",
        "stages": list(STAGES),
        "titles": dict(_STAGE_TITLE),
        "input_artifacts": {
            "intake": ["req.json", "req_draft.json"],
            "architecture": ["units.json"],
            "parts": ["design.json"],
            "schematic": ["emit.json(parity, schematic_detail)"],
            "pcb": ["emit.json(verify_detail, fabrication)"],
            "firmware": ["firmware_matrix.json", "design.json/firmware"],
            "mechanical": ["mech.json"],
            "release": ["handoff（现场聚合）"],
        },
        "discipline": ("只投影已有产物，不重判；缺产物 = not_ready（不是 ok）；"
                       "每段都带 checked 与 basis"),
    }