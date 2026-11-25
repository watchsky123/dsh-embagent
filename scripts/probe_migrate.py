"""工程正规化诊断 —— 「API 建的工程没有云文档槽位」这条在大佬手册里是不是真的。

    python -m dsh_embagent.scripts.probe_migrate [--keep]

## 为什么值得单独测

大佬手册 §15.1 / §17.8 记了一条**极具迷惑性**的坑：

> `dmt_Project.createProject()` 建出的工程，云端不会登记文档槽位
> （`getCurrentProjectInfo().data` 为 `[]`）。后果：
> - 原理图**永远存不进云端**：`sch_Document.save()` 恒返回 `false`（PCB 却能存）
> - 所有原理图元数据类 API 抛错、所有枚举类 API 返回空数组
> - **而图元类 API（create / get / delete）全部正常** —— 极具迷惑性

这套症状如果在本机成立，那么 `emit` 交付的工程是**半个废品**：
PCB 存得住、原理图存不住，而我们从来没检查过 `save()` 的返回值。

反过来，如果**不成立**（本机 `data` 非空、`save()` 为 true），
那就说明这条是**版本差异**，不能照抄结论 —— 也不能拿它当布线失败的借口。

## 同时验证 §15.3 的迁移配方

手册给的修法是：`getProjectFileByProjectUuid` 导出 → `importProjectByProjectFile`
以 `New Project` 重新导入 → 新工程拿到正常槽位。本脚本按原文跑一遍，
拿到"迁移前 / 迁移后"两组 `save()` 与槽位数据做对照。

## 它不修东西

只产出 `migrate_probe.json`。要不要把这套写进 `emit`，看证据再说。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import lceda  # noqa: E402

OUT_FILE = "migrate_probe.json"

#: 槽位读数。**只读，不写**。
_JS_SLOTS = r"""
const out = {ok: true};
const cur = await eda.dmt_Project.getCurrentProjectInfo();
out.uuid = cur ? (cur.uuid || '') : '';
out.friendlyName = cur ? (cur.friendlyName || '') : '';
out.dataLen = (cur && cur.data) ? cur.data.length : -1;
out.slots = ((cur && cur.data) || []).map(b => ({
  name: b.name || '',
  itemType: (b.itemType !== undefined ? b.itemType : null),
  schUuid: (b.schematic && b.schematic.uuid) || '',
  schPages: ((b.schematic && b.schematic.page) || []).length,
  pageUuid: ((b.schematic && b.schematic.page && b.schematic.page.length)
    ? b.schematic.page[0].uuid : ''),
  pcbUuid: (b.pcb && b.pcb.uuid) || '',
}));
try { out.boards = ((await eda.dmt_Board.getAllBoardsInfo()) || []).length; }
catch (e) { out.boards = null; out.boardsErr = String((e && e.message) || e); }
try { out.schematics = ((await eda.dmt_Schematic.getAllSchematicsInfo()) || []).length; }
catch (e) { out.schematics = null; out.schematicsErr = String((e && e.message) || e); }
try { out.pcbs = ((await eda.dmt_Pcb.getAllPcbsInfo()) || []).length; }
catch (e) { out.pcbs = null; out.pcbsErr = String((e && e.message) || e); }
return out;
"""

#: 建工程（friendlyName 允许中文；projectName 只允许 a-zA-Z0-9-）。
_JS_NEW = r"""
const friendly = __FRIENDLY__;
const name = __NAME__;
let uuid = null;
let why = '';
for (let i = 1; i <= 5 && !uuid; i++) {
  const n = (i === 1) ? name : (name + '-' + i);
  try {
    uuid = await eda.dmt_Project.createProject(
      friendly, n, undefined, undefined, 'dsh_embagent probe');
  } catch (e) {
    why = String((e && e.message) || e);
    uuid = null;
  }
  if (!uuid) await new Promise(r => setTimeout(r, 200));
}
if (!uuid) return {ok: false, error: {code: 'CREATE', message: why || 'createProject 返回空'}};
const opened = await eda.dmt_Project.openProject(uuid);
if (!opened) {
  await new Promise(r => setTimeout(r, 800));
  const again = await eda.dmt_Project.openProject(uuid);
  if (!again) return {ok: false, error: {code: 'OPEN', message: 'openProject 两次 false'}};
}
return {ok: true, projectUuid: uuid};
"""

#: 原理图 save() —— 手册说这里会恒 false。
_JS_SAVE_SCH = r"""
const page = __PAGE__;
if (!page) return {ok: true, skipped: '没有 pageUuid'};
const opened = await eda.dmt_EditorControl.openDocument(page);
await new Promise(r => setTimeout(r, 900));
const out = {ok: true, opened: !!opened};
try { out.save = await eda.sch_Document.save(); }
catch (e) { out.save = null; out.saveErr = String((e && e.message) || e); }
try { const d = await eda.dmt_SelectControl.getCurrentDocumentInfo();
      out.docType = d ? d.documentType : null; }
catch (e) { out.docType = null; }
return out;
"""

#: PCB save() —— 手册说这个能存。
_JS_SAVE_PCB = r"""
const pcb = __PCB__;
if (!pcb) return {ok: true, skipped: '没有 pcbUuid'};
const opened = await eda.dmt_EditorControl.openDocument(pcb);
await new Promise(r => setTimeout(r, 900));
const out = {ok: true, opened: !!opened};
try { out.save = await eda.pcb_Document.save(); }
catch (e) { out.save = null; out.saveErr = String((e && e.message) || e); }
try { const d = await eda.dmt_SelectControl.getCurrentDocumentInfo();
      out.docType = d ? d.documentType : null; }
catch (e) { out.docType = null; }
return out;
"""

#: 原理图元数据类 API —— 手册说这些会抛 `i is not iterable` / `typeSymbols`。
_JS_SCH_META = r"""
const out = {ok: true};
try { const i = await eda.dmt_Schematic.getCurrentSchematicInfo();
      out.current = {name: (i && i.name) || '', uuid: (i && i.uuid) || ''}; }
catch (e) { out.current = null; out.currentErr = String((e && e.message) || e); }
try {
  const net = await eda.sch_Netlist.getNetlist(0);
  out.netlist = net ? {keys: Object.keys(net).length, sample: String(JSON.stringify(net).slice(0, 200))} : null;
} catch (e) { out.netlist = null; out.netlistErr = String((e && e.message) || e); }
return out;
"""

#: §15.3 第 1 步：导出工程为内存文件。
_JS_EXPORT = r"""
const uuid = __UUID__;
try {
  const f = await eda.sys_FileManager.getProjectFileByProjectUuid(uuid, 'src');
  if (!f) return {ok: true, file: null};
  return {ok: true, file: {
    kind: (f.constructor && f.constructor.name) || typeof f,
    size: (typeof f.size === 'number' ? f.size : null),
    name: f.name || '',
  }};
} catch (e) {
  return {ok: true, file: null, err: String((e && e.message) || e)};
}
"""

#: §15.3 第 2 步：原样重新导入为「新工程」。用个人空间（不给团队 uuid）。
_JS_IMPORT = r"""
const uuid = __UUID__;
const name = __NAME__;
const friendly = __FRIENDLY__;
let f = null;
try { f = await eda.sys_FileManager.getProjectFileByProjectUuid(uuid, 'src'); }
catch (e) { return {ok: false, error: {code: 'EXPORT', message: String((e && e.message) || e)}}; }
if (!f) return {ok: false, error: {code: 'EXPORT', message: '导出返回空文件'}};
try {
  const r = await eda.sys_FileManager.importProjectByProjectFile(
    f, 'JLCEDA Pro', undefined,
    {operation: 'New Project', newProjectName: name,
     newProjectFriendlyName: friendly});
  return {ok: true, imported: true,
          value: (r && typeof r === 'object') ? {uuid: r.uuid || '', name: r.name || ''} : r};
} catch (e) {
  return {ok: false, error: {code: 'IMPORT', message: String((e && e.message) || e)}};
}
"""

_JS_OPEN_ANY = r"""
const uuid = __UUID__;
const opened = await eda.dmt_Project.openProject(uuid);
return {ok: true, opened: !!opened};
"""


def _stamp() -> str:
    return time.strftime("%H%M%S")


def main(argv: list[str]) -> int:
    keep = "--keep" in argv
    stamp = _stamp()
    exe = lceda.find_exe()
    if not exe:
        print(json.dumps({"ok": False, "reason": "没有 lceda-pro"}, ensure_ascii=False))
        return 2

    friendly = f"hwv migrate probe {stamp}"
    name = f"hwv-migrate-probe-{stamp}"
    report: dict = {"ok": True, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "exe": exe, "version": lceda.version_of(exe),
                    "before": {}, "after": {}, "steps": []}
    session = lceda.LcedaSession(exe=exe)

    def step(label: str, value) -> None:
        report["steps"].append({"label": label, "value": value})

    try:
        session.open(headless=True)
        step("session", {"sessionId": session.session_id})
        created = session.invoke(
            _JS_NEW.replace("__FRIENDLY__", json.dumps(friendly, ensure_ascii=False))
                   .replace("__NAME__", json.dumps(name)),
            require_ok=False, timeout_ms=90_000, label="create")
        step("create", created)
        if not created.get("ok"):
            report["ok"] = False
            report["before"]["error"] = created.get("error")
            raise RuntimeError("建工程失败")
        uuid = str(created.get("projectUuid") or "")
        report["uuid"] = uuid

        ready = session.wait_project_ready(timeout_ms=30_000)
        slots = session.invoke(_JS_SLOTS, timeout_ms=60_000, label="slots")
        report["before"]["slots"] = slots
        board = (ready.get("boards") or [{}])[0]
        page_uuid = board.get("pageUuid") or ""
        pcb_uuid = board.get("pcbUuid") or ""

        sch = session.invoke(
            _JS_SAVE_SCH.replace("__PAGE__", json.dumps(page_uuid)),
            require_ok=False, timeout_ms=90_000, label="saveSch")
        report["before"]["saveSchematic"] = sch
        pcb = session.invoke(
            _JS_SAVE_PCB.replace("__PCB__", json.dumps(pcb_uuid)),
            require_ok=False, timeout_ms=90_000, label="savePcb")
        report["before"]["savePcb"] = pcb
        meta = session.invoke(_JS_SCH_META, require_ok=False,
                              timeout_ms=60_000, label="schMeta")
        report["before"]["schMeta"] = meta
        export = session.invoke(_JS_EXPORT.replace("__UUID__", json.dumps(uuid)),
                                require_ok=False, timeout_ms=90_000, label="export")
        report["before"]["export"] = export

        # ---- §15.3 迁移 ----
        imported = session.invoke(
            _JS_IMPORT.replace("__UUID__", json.dumps(uuid))
                      .replace("__NAME__", json.dumps(name + "-m"))
                      .replace("__FRIENDLY__", json.dumps(friendly + " 迁移", ensure_ascii=False)),
            require_ok=False, timeout_ms=180_000, label="import")
        report["migrate"] = imported
        new_uuid = ""
        if imported.get("ok"):
            new_uuid = str((imported.get("value") or {}).get("uuid") or "")
        if new_uuid:
            session.invoke(_JS_OPEN_ANY.replace("__UUID__", json.dumps(new_uuid)),
                           require_ok=False, timeout_ms=120_000, label="openNew")
            try:
                ready2 = session.wait_project_ready(timeout_ms=45_000)
            except Exception as exc:  # noqa: BLE001
                ready2 = {"error": str(exc)}
            report["after"]["ready"] = ready2
            slots2 = session.invoke(_JS_SLOTS, require_ok=False,
                                    timeout_ms=60_000, label="slots2")
            report["after"]["slots"] = slots2
            b2 = (ready2.get("boards") or [{}])[0] if isinstance(ready2, dict) else {}
            sch2 = session.invoke(
                _JS_SAVE_SCH.replace("__PAGE__", json.dumps(b2.get("pageUuid") or "")),
                require_ok=False, timeout_ms=90_000, label="saveSch2")
            report["after"]["saveSchematic"] = sch2
            meta2 = session.invoke(_JS_SCH_META, require_ok=False,
                                   timeout_ms=60_000, label="schMeta2")
            report["after"]["schMeta"] = meta2
        else:
            report["after"]["skipped"] = "迁移没有拿到新工程 uuid"
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
    print(json.dumps({"ok": report.get("ok"), "out": str(target),
                      "before": {k: report["before"].get(k) for k in
                                 ("saveSchematic", "savePcb")},
                      "after": {k: report["after"].get(k) for k in
                                ("saveSchematic",)}},
                     ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))