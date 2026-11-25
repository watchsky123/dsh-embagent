"""布线为什么 0/N：先量几何，再猜引擎。

    python -m dsh_embagent.scripts.probe_route_geom [--name X]

## 已经排除到只剩几何

在**用户登录态的 GUI 窗口**里（`origin=user`）对同一块板调 `autoRouting`：

    {success: true, totalNetsCount: 2, successNetsCount: 0,
     failedNets: ["GND","MID"], duration: 931}

`success` 的官方定义是「是否成功**启动**」——所以引擎确实启动了、看得到 2 条网络，
但一条都没布通。已经排除：窗口来源（cli/user 一样）、登录态（登录后一样）、
headless/可见（一样）、DRC 规则（`JLCPCB Capability(Two Layers Board)`，6766 字符）、
层状态（TOP/BOTTOM 均 SHOW）、飞线（3 条）。

剩下的就只有**几何**：焊盘在哪、在不在板内、有没有重叠。手册第十轮的教训是
「先怀疑自己的代码，再怀疑工具」，而这块板的元件坐标是我自己算的。

## 它做什么

在**同一个会话**里跑完 emit 的 project/schematic/pcb 三段，然后：

1. **量**元件坐标、焊盘坐标、板框、飞线数（这是唯一还没量过的东西）
2. 调 `autoRouting`（`{}` 与显式 `RoutingNets` 两种）
3. 布线后再量一次导线/过孔数
4. 显式 `pcb_Document.save()` 并**读回 save 的返回值**
   （P17 之后的板子在磁盘上读到 0 个元件，怀疑 save 没落盘）

## 产出

`route_geom_probe.json`。不修东西。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import emit_lceda, lceda  # noqa: E402

OUT_FILE = "route_geom_probe.json"

_JS_GEOM = r"""
const out = {ok: true};
const cur = await eda.dmt_Project.getCurrentProjectInfo();
let pcb = '';
for (const b of ((cur && cur.data) || [])) { if (b.pcb && b.pcb.uuid) { pcb = b.pcb.uuid; break; } }
out.pcb = pcb;
out.project = cur ? (cur.friendlyName || '') : '';
if (!pcb) return {ok: false, error: {code: 'NO_PCB', message: '工程里没有 PCB'}};
await eda.dmt_EditorControl.openDocument(pcb);
for (let i = 0; i < 25; i++) {
  const d = await eda.dmt_SelectControl.getCurrentDocumentInfo();
  if (d && d.documentType === 3) break;
  await new Promise(r => setTimeout(r, 300));
}
const doc = await eda.dmt_SelectControl.getCurrentDocumentInfo();
out.docType = doc ? doc.documentType : null;
out.comps = [];
try {
  const ids = (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()) || [];
  const comps = (await eda.pcb_PrimitiveComponent.get(ids)) || [];
  for (const c of comps) {
    const row = {d: c.designator || '', x: c.x, y: c.y, layer: c.layer,
                 rot: c.rotation, pads: []};
    let ps = [];
    try { ps = (await eda.pcb_PrimitiveComponent.getAllPinsByPrimitiveId(c.primitiveId)) || []; }
    catch (e) { row.padsErr = String((e && e.message) || e); }
    for (const p of ps) {
      row.pads.push({net: p.net || '', num: p.padNumber, x: p.x, y: p.y,
                     layer: p.layer, hole: !!p.hole});
    }
    out.comps.push(row);
  }
} catch (e) { out.compsErr = String((e && e.message) || e); }
try {
  const ls = (await eda.pcb_PrimitiveLine.getAll('')) || [];
  out.outline = ls.length;
  out.outlineSample = ls.slice(0, 8).map(l => [l.startX, l.startY, l.endX, l.endY]);
} catch (e) { out.outlineErr = String((e && e.message) || e); }
try { out.ratlines = ((await eda.pcb_Document.getRatlines({})) || {}); 
      out.ratlineTotal = out.ratlines.total; delete out.ratlines; }
catch (e) { out.ratlineErr = String((e && e.message) || e); }
try { out.nets = (await eda.pcb_Net.getAllNetName()) || []; }
catch (e) { out.netsErr = String((e && e.message) || e); }
try { out.netlist = await eda.pcb_Net.getNetlist('JLCEDA'); }
catch (e) { out.netlistErr = String((e && e.message) || e); }
return out;
"""

_JS_ROUTE = r"""
const t0 = Date.now();
try {
  const r = await eda.pcb_Document.autoRouting(__PROPS__);
  return {ok: true, ms: Date.now() - t0, result: r};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""

_JS_AFTER = r"""
const out = {ok: true};
try {
  out.save = await eda.pcb_Document.save();
} catch (e) { out.save = null; out.saveErr = String((e && e.message) || e); }
try {
  const nets = (await eda.pcb_Net.getAllNetName()) || [];
  let n = 0;
  const per = {};
  for (const name of nets) {
    let c = 0;
    try { c = ((await eda.pcb_PrimitiveLine.getAll(name)) || []).length; } catch (e) {}
    per[name] = c; n += c;
  }
  out.tracks = n; out.tracksPerNet = per;
} catch (e) { out.tracksErr = String((e && e.message) || e); }
try { out.vias = ((await eda.pcb_PrimitiveVia.getAll()) || []).length; }
catch (e) { out.viasErr = String((e && e.message) || e); }
return out;
"""


def main(argv: list[str]) -> int:
    name = ""
    session_id = ""
    for i, a in enumerate(argv):
        if a == "--name" and i + 1 < len(argv):
            name = argv[i + 1]
        elif a == "--session" and i + 1 < len(argv):
            session_id = argv[i + 1]
    exe = lceda.find_exe()
    if not exe:
        print(json.dumps({"ok": False, "reason": "没有 lceda-pro"}, ensure_ascii=False))
        return 2

    report: dict[str, Any] = {"ok": True, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "version": lceda.version_of(exe)}
    session = lceda.LcedaSession(exe=exe)
    try:
        if session_id:
            session.session_id = session_id
            session.headless = False
            report["opened"] = {"session": session_id}
        elif name:
            # 接管一个已存在的工程（**不新建**）：登录态下 `createProject`
            # 在 CLI 窗口里会抛 `username undefined`，所以"新建"这条路
            # 在 headless 里已经走不通了，只能复用。
            ready = session.open_project(name)
            report["opened"] = {k: ready.get(k) for k in ("projectUuid", "projectName")}
        else:
            print(json.dumps({"ok": False, "reason": "要给 --name <已存在的工程名>"},
                             ensure_ascii=False))
            return 2

        from dsh_embagent.scripts.probe_route import design as probe_design
        d = probe_design()
        pieces = emit_lceda.EmitReport()
        emit_lceda._run_project(session, d, pieces, name=name, reuse=True)
        emit_lceda._run_schematic(session, d, pieces)
        emit_lceda._run_pcb(session, d, pieces)
        report["emit"] = {k: {"status": v.status, "note": v.note[:300],
                              "counts": v.counts, "warnings": v.warnings[:4]}
                          for k, v in pieces.jobs.items()}

        geom = session.invoke(_JS_GEOM, require_ok=False,
                              timeout_ms=180_000, label="geom")
        report["geom"] = geom
        props = [
            ("{}", "{}"),
            ("routing_nets",
             "{RoutingNets: ['GND', 'MID'], layers: [1, 2], "
             "cornerStyle: 0, optimization: 1}"),
        ]
        report["route"] = []
        for tag, p in props:
            got = session.invoke(_JS_ROUTE.replace("__PROPS__", p),
                                 require_ok=False, timeout_ms=900_000, label="route")
            report["route"].append({"tag": tag, "props": p, "outcome": got})
        after = session.invoke(_JS_AFTER, require_ok=False,
                               timeout_ms=180_000, label="after")
        report["after"] = after
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

    target = Path(__file__).resolve().parents[1] / OUT_FILE
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
    geom = report.get("geom") or {}
    print(json.dumps({
        "ok": report.get("ok"),
        "error": report.get("error"),
        "emit": {k: v["status"] for k, v in (report.get("emit") or {}).items()},
        "comps": [{"d": c.get("d"), "x": c.get("x"), "y": c.get("y"),
                   "pads": [{"net": p.get("net"), "x": p.get("x"), "y": p.get("y")}
                            for p in (c.get("pads") or [])]}
                  for c in (geom.get("comps") or [])],
        "nets": geom.get("nets"),
        "ratlines": geom.get("ratlineTotal"),
        "outline": geom.get("outline"),
        "route": [{"tag": r["tag"], "result": (r["outcome"] or {}).get("result"),
                   "error": (r["outcome"] or {}).get("error")}
                  for r in (report.get("route") or [])],
        "after": report.get("after"),
        "out": str(target),
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))