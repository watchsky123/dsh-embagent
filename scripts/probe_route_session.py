"""同一个工程、两种窗口来源，布线表现一样吗？—— 一次说清。

    python -m dsh_embagent.scripts.probe_route_session --path <x.eprj3> [--headless]
    python -m dsh_embagent.scripts.probe_route_session --session <sessionId>

## 为什么要做这个对照

P17 得出的结论是「布线的执行环境在 GUI 渲染进程里，headless 会话没有」。
但 `lceda-pro --help` 里有一句当时没读到的话：

> `session list` —— List sessions (each with origin cli/user, the project path
> and the window title)
> 「every editor window has one: **opened by this CLI, or opened by the user
> in the GUI**」

也就是说：**用户自己在 GUI 里打开的那扇窗，是一个 CLI 能 `invoke` 进去的会话**。
如果布线对环境的要求是「非 CLI 创建的窗口」，那结论就要改写——
`origin=user` 的窗口能布通，而 `origin=cli` 的不能。

本脚本同一个工程跑一遍、把 `origin` 一起记下来，让这条分叉**可判别**：

| 结果 | 结论 |
|---|---|
| `cli` 窗 0/N，`user` 窗 N/N | 差异是**窗口来源**，不是渲染进程有无 |
| 两者都 0/N | 差异在别处（本机环境），需继续排除 |
| 两者都 N/N | 之前的 0/N 是**那块板**的问题，不是环境 |

## 它不修东西

只产出一份 `route_session_probe.json`。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import lceda  # noqa: E402

OUT_FILE = "route_session_probe.json"

_JS_PCB_OPEN = r"""
// **`open` 只保证会话存在，不保证工程装好了**（`lceda.py` 的 `wait_project_ready`
// 就是为这个写的）。第一版这里直接读 `data[0].pcb`，拿到空 → 报「工程里没有
// PCB」——那是**读早了**，不是工程坏（同一份工程 3 秒后就能读到 pcb）。
const out = {ok: true};
let cur = null;
let boards = [];
for (let i = 0; i < 60; i++) {
  cur = await eda.dmt_Project.getCurrentProjectInfo();
  boards = ((cur && cur.data) || []).map(b => ({
    name: b.name || '',
    pcbUuid: (b.pcb && b.pcb.uuid) || '',
  }));
  if (boards.some(b => b.pcbUuid)) break;
  await new Promise(r => setTimeout(r, 250));
}
out.friendlyName = cur ? (cur.friendlyName || '') : '';
out.boards = boards;
const pcb = (boards.find(b => b.pcbUuid) || {}).pcbUuid || '';
if (!pcb) return {ok: false, error: {code: 'NO_PCB', message: '工程里没有 PCB'}};
const opened = await eda.dmt_EditorControl.openDocument(pcb);
await new Promise(r => setTimeout(r, 1500));
let docType = null;
try { const d = await eda.dmt_SelectControl.getCurrentDocumentInfo();
      docType = d ? d.documentType : null; } catch (e) {}
return {ok: true, pcbUuid: pcb, opened: !!opened, docType: docType};
"""

_JS_ENV = r"""
const out = {ok: true};
try {
  const ids = await eda.pcb_PrimitiveComponent.getAllPrimitiveId();
  out.components = (ids || []).length;
} catch (e) { out.components = null; out.componentsErr = String((e && e.message) || e); }
try { out.nets = (await eda.pcb_Net.getAllNetName()) || []; }
catch (e) { out.nets = null; out.netsErr = String((e && e.message) || e); }
try {
  const pads = await eda.pcb_PrimitivePad.getAll();
  out.pads = [];
  for (const p of (pads || [])) {
    const row = {net: '', num: '', x: null, y: null, layer: null};
    try { row.net = String(p.net || ''); } catch (e) {}
    try { row.num = String(p.padNumber !== undefined ? p.padNumber : ''); } catch (e) {}
    try { row.x = p.x; row.y = p.y; } catch (e) {}
    try { row.layer = p.layer; } catch (e) {}
    out.pads.push(row);
  }
  out.padNets = {};
  for (const p of out.pads) {
    const k = p.net || '(none)';
    out.padNets[k] = (out.padNets[k] || 0) + 1;
  }
} catch (e) { out.pads = null; out.padsErr = String((e && e.message) || e); }
try {
  const page = await eda.pcb_Document.getRatlines({});
  out.ratlines = page && page.total !== undefined ? page.total
                : ((page && page.items) || []).length;
} catch (e) { out.ratlines = null; out.ratlinesErr = String((e && e.message) || e); }
try {
  const cfg = await eda.pcb_Drc.getCurrentRuleConfiguration();
  out.ruleChars = cfg ? JSON.stringify(cfg).length : 0;
  out.ruleName = await eda.pcb_Drc.getCurrentRuleConfigurationName();
} catch (e) { out.ruleErr = String((e && e.message) || e); }
try {
  const all = await eda.pcb_Layer.getAllLayers();
  out.layers = (all || []).map(l => ({id: l.id, name: l.name, status: l.layerStatus}))
                         .filter(l => l.id <= 2);
} catch (e) { out.layersErr = String((e && e.message) || e); }
return out;
"""

_JS_ROUTE = r"""
const t0 = Date.now();
try {
  const r = await eda.pcb_Document.autoRouting(__PROPS__);
  return {ok: true, ms: Date.now() - t0, result: r};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""

_JS_TRACKS = r"""
const out = {ok: true, tracks: 0, vias: 0};
try {
  const names = (await eda.pcb_Net.getAllNetName()) || [];
  let n = 0;
  for (const name of names) {
    try { n += ((await eda.pcb_PrimitiveLine.getAll(name)) || []).length; } catch (e) {}
  }
  out.tracks = n;
} catch (e) { out.tracksErr = String((e && e.message) || e); }
try {
  out.vias = ((await eda.pcb_PrimitiveVia.getAll()) || []).length;
} catch (e) { out.viasErr = String((e && e.message) || e); }
return out;
"""


def _sessions(exe: str) -> list[dict[str, Any]]:
    raw = lceda.run_cli(exe, ["session", "list"], timeout_ms=30_000)
    value = raw.get("value") or []
    return value if isinstance(value, list) else []


def main(argv: list[str]) -> int:
    path = ""
    session_id = ""
    headless = "--headless" in argv
    build = "--build" in argv
    for i, a in enumerate(argv):
        if a == "--path" and i + 1 < len(argv):
            path = argv[i + 1]
        elif a == "--session" and i + 1 < len(argv):
            session_id = argv[i + 1]
    exe = lceda.find_exe()
    if not exe:
        print(json.dumps({"ok": False, "reason": "没有 lceda-pro"}, ensure_ascii=False))
        return 2

    report: dict[str, Any] = {"ok": True, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "version": lceda.version_of(exe),
                              "headless": headless, "steps": []}
    session = lceda.LcedaSession(exe=exe)
    opened_here = False
    try:
        if session_id:
            # 接管一扇**已经存在的窗**。`origin=user` 的那种是用户自己在 GUI
            # 里开的 —— 它有登录态，而 CLI 新建的窗没有（实测：`createProject`
            # 在 cli 窗抛 `Cannot read properties of undefined (reading
            # 'username')`，在同一次登录之后的 cli 窗仍然抛）。
            session.session_id = session_id
            session.headless = headless
        elif build:
            # 建一块**真有焊盘和飞线**的小板。r3demo 的 PCB 是空的：P17 那 4 个
            # 元件全是 `no_symbol`，一个都没放上去 —— 拿它测布线只会得到
            # `totalNetsCount: 0`，那证明不了任何事。
            session.open(headless=headless)
            opened_here = True
        else:
            if not path:
                print(json.dumps({"ok": False, "reason": "要给 --path / --session / --build"},
                                 ensure_ascii=False))
                return 2
            session.open(path=path, headless=headless)
            opened_here = True
        if build:
            from dsh_embagent.design import emit_lceda
            from dsh_embagent.scripts.probe_route import design as probe_design

            d = probe_design()
            name = f"hwv-route-gui-{time.strftime('%H%M%S')}"
            pieces = emit_lceda.EmitReport()
            emit_lceda._run_project(session, d, pieces, name=name, reuse=False)
            emit_lceda._run_schematic(session, d, pieces)
            emit_lceda._run_pcb(session, d, pieces)
            report["built"] = {
                "name": name,
                "jobs": {k: {"status": v.status, "note": v.note[:200],
                             "counts": v.counts}
                         for k, v in pieces.jobs.items()},
            }
        report["session"] = {
            "sessionId": session.session_id,
            "origin": next((s.get("origin") for s in _sessions(exe)
                            if s.get("sessionId") == session.session_id), None),
            "headless": next((s.get("headless") for s in _sessions(exe)
                              if s.get("sessionId") == session.session_id), None),
        }
        pcbinfo = session.invoke(_JS_PCB_OPEN, require_ok=False,
                                 timeout_ms=120_000, label="pcb")
        report["pcb"] = pcbinfo
        if not pcbinfo.get("ok"):
            raise RuntimeError(f"打不开 PCB：{pcbinfo.get('error')}")
        env = session.invoke(_JS_ENV, require_ok=False, timeout_ms=120_000, label="env")
        report["env"] = env
        props = ["{}", "{layers: [1, 2]}"]
        report["route"] = []
        for p in props:
            r = session.invoke(_JS_ROUTE.replace("__PROPS__", p),
                               require_ok=False, timeout_ms=240_000, label="route")
            report["route"].append({"props": p, "outcome": r})
        tracks = session.invoke(_JS_TRACKS, require_ok=False,
                                timeout_ms=120_000, label="tracks")
        report["tracks"] = tracks
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if opened_here:
                session.close(destroy=True)
            else:
                session.close()
        except Exception:  # noqa: BLE001
            pass

    target = Path(__file__).resolve().parents[1] / OUT_FILE
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
    summary = {
        "ok": report.get("ok"),
        "session": report.get("session"),
        "pads": (report.get("env") or {}).get("padNets"),
        "ratlines": (report.get("env") or {}).get("ratlines"),
        "route": [{"props": r["props"],
                   "result": (r["outcome"] or {}).get("result"),
                   "error": (r["outcome"] or {}).get("error")}
                  for r in (report.get("route") or [])],
        "tracks": report.get("tracks"),
        "out": str(target),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))