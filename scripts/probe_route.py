"""自动布线诊断 —— 为什么 `autoRouting` 返回 success:false / duration:0。

    python -m hw_velxio.scripts.probe_route [--keep]

## 为什么不复用 P17 的排除实验

P17 试了 5 条（画布渲染/有网络/元件在板内/板框识别/headless），全部排除了。
但那些实验**都带着 `{layers:[1,2]}` 这一个参数**调用的，而 EDA 自己的文档写着：

> `props.layers` 必须传入**启用层**（`layerStatus !== NOT_USED`），
> **传停用层会被静默忽略**

所以「参数被静默忽略」这条路从未被排除过。本脚本做三件 P17 没做的事：

1. **零参数对照**：`autoRouting()` 不带任何 props（文档说这样布所有未布网络）
2. **作业模型**：`startAutoRouting()` + `getAutoJobStatus` + **`getAutoJobLog`**
   —— 日志是引擎自己说原因的地方，P17 一次都没读过
3. **环境快照**：元件数 / 网络 / 板框 / 层状态 / DRC 规则，一次性打出来

## 它不修东西

只产出一份 `route_probe.json` 给人看。修不修、怎么修，看证据再说。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import emit_lceda, lceda  # noqa: E402
from hw_velxio.design.model import (Board, DesignModel, Part,  # noqa: E402
                                    Placement)

PROBE_NAME = "hwv-route-probe"
LCSC = "C1523"          # 本机系统库实测存在（1nF 0402）

#: 环境快照。**不问"能不能布"，先把"板子长什么样"读全**。
_JS_ENV = r"""
const out = {ok: true};
try {
  const comps = await eda.pcb_Component.getAll();
  out.components = (comps || []).length;
  out.refs = (comps || []).map(c => {
    try { return c.designator || c.name || ''; } catch (e) { return ''; }
  }).filter(Boolean).slice(0, 12);
} catch (e) { out.components = null; out.components_error = String((e && e.message) || e); }
try {
  out.nets = await eda.pcb_Net.getAllNetName();
} catch (e) { out.nets = null; out.nets_error = String((e && e.message) || e); }
try {
  const boards = await eda.pcb_Document.getAllBoardsAndPcbs();
  out.boards = (boards || []).length;
} catch (e) { out.boards = null; }
try {
  const info = await eda.pcb_Document.getCurrentPcbInfo();
  out.pcb = info ? { title: info.title || '', uuid: info.uuid || '' } : null;
} catch (e) { out.pcb = null; }
return out;
"""

#: 尝试 A：零参数（文档明说这样布所有未布网络）。
_JS_A = r"""
try {
  const t0 = Date.now();
  const r = await eda.pcb_Document.autoRouting();
  return {ok: true, ms: Date.now() - t0, result: r};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""

#: 尝试 B：P17 的老调用（带 layers）。
_JS_B = r"""
try {
  const t0 = Date.now();
  const r = await eda.pcb_Document.autoRouting({layers: [1, 2]});
  return {ok: true, ms: Date.now() - t0, result: r};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""

#: 尝试 C：显式优化目标 + 推倒重布。
_JS_C = r"""
try {
  const t0 = Date.now();
  const r = await eda.pcb_Document.autoRouting(
    {optimization: 1, existingPrimitiveMode: 'remove'});
  return {ok: true, ms: Date.now() - t0, result: r};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""

#: 尝试 D：作业模型 + **读日志**。这是 P17 没做的那一步。
_JS_D_START = r"""
try {
  const job = await eda.pcb_Document.startAutoRouting();
  return {ok: true, job: job};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""

#: **入参是 `jobId` 字符串**（`ISYS_JobHandle.jobId`），不是整个 handle 对象。
#: 第一版把 `job` 对象直接传进去 → `getAutoJobStatus` 返回 `undefined`，
#: 于是"作业根本没在跑"这个结论**是我读错了**（AGENTS §3.2 第 N 次）。
_JS_D_STATUS = r"""
const jobId = __JOB__;
try {
  const st = await eda.pcb_Document.getAutoJobStatus(jobId);
  return {ok: true, status: st === undefined ? null : st};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""

_JS_D_LOG = r"""
const jobId = __JOB__;
try {
  const log = await eda.pcb_Document.getAutoJobLog(jobId);
  return {ok: true, log: log === undefined ? null : log};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""

#: 层状态。**文档说的前置条件就是这一项**，所以必须读。
_JS_LAYERS = r"""
try {
  const all = await eda.pcb_Layer.getAllLayers();
  const rows = (all || []).map(l => ({
    id: l.id !== undefined ? l.id : null,
    name: l.name || '',
    status: l.layerStatus !== undefined ? l.layerStatus : null,
  }));
  return {ok: true, layers: rows};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""



#: 运行时枚举 API（**不靠文档猜方法名**——AGENTS §3.18 的反面用法：
#: 让对象自己说它有什么方法）。
_JS_API = r"""
const pick = (obj, re) => Object.keys(obj || {}).filter(k => re.test(k));
const out = {ok: true, doc: [], net: [], comp: []};
try { out.doc = pick(eda.pcb_Document, /pad|net|rule|rout/i); } catch (e) {}
try { out.net = Object.keys(eda.pcb_Net || {}); } catch (e) {}
try { out.comp = Object.keys(eda.pcb_Component || {}); } catch (e) {}
return out;
"""

#: 逐个网络数焊盘。**焊盘为 0 = 布不通的根因**，不是引擎拒绝。
#:
#: 用 `getAllPrimitivesByNet`（文档原话「获取关联指定网络的所有图元」）。
#: 运行时 `Object.keys(eda.pcb_Net)` 返回空——那个对象是 proxy，
#: 方法不是 own enumerable key，所以**只能按文档调，不能靠枚举猜**。
_JS_NETPADS = r"""
const out = {ok: true, nets: {}};
const names = __NETS__;
for (const name of names) {
  const row = {primitives: null, types: {}, error: ''};
  try {
    const ps = await eda.pcb_Net.getAllPrimitivesByNet(name);
    const list = ps || [];
    row.primitives = list.length;
    for (const p of list) {
      let t = '?';
      try {
        t = String(p.primitiveType !== undefined ? p.primitiveType
                  : (p.type !== undefined ? p.type : p.constructor && p.constructor.name));
      } catch (e) { t = '?'; }
      row.types[t] = (row.types[t] || 0) + 1;
    }
  } catch (e) { row.error = String((e && e.message) || e); }
  out.nets[name] = row;
}
return out;
"""

#: 网络详情（beta）：看每个网络里有多少焊盘/引脚。
_JS_NETDETAIL = r"""
try {
  const nets = await eda.pcb_Net.getAllNets();
  const rows = (nets || []).map(n => ({
    name: n.netName || n.name || '',
    pads: (n.pads || n.padList || n.pins || []).length,
    keys: Object.keys(n || {}).slice(0, 10),
  }));
  return {ok: true, nets: rows.slice(0, 8)};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""



#: 交换格式导出。**这是绕开内部引擎的那条路**：
#: `getDsnFile`（Specctra DSN）+ `getAutoRouteJsonFile`（立创布线 JSON）。
#: 有导出就有「外部布线器 → `importAutoRouteSesFile` / `importAutoRouteJsonFile`」。
#: 交换格式导出。**返回的是 `File | Blob`，不是字符串**——
#: `JSON.stringify(File)` 得到 `{}`，第一版据此得出"导出是空的"，那是读错了。
#: 正确读法是 `await file.text()`（`saveFile` 走 Electron 对话框，被外部交互
#: 权限拦住，所以**读进内存再从 Python 落盘**）。
_JS_EXPORT = r"""
const out = {ok: true, dsn: null, json: null, errors: {}};
async function describe(x, key) {
  if (x === undefined || x === null) { out[key] = {absent: true}; return; }
  const row = {type: typeof x};
  try { row.ctor = (x.constructor && x.constructor.name) || ''; } catch (e) {}
  try { row.size = x.size !== undefined ? x.size : null; } catch (e) {}
  try {
    if (typeof x.text === 'function') {
      const t = await x.text();
      row.chars = (t || '').length;
      row.head = (t || '').slice(0, 200);
      row.tail = (t || '').slice(-160);
    } else {
      const t = typeof x === 'string' ? x : JSON.stringify(x);
      row.chars = (t || '').length;
      row.head = (t || '').slice(0, 200);
    }
  } catch (e) { row.read_error = String((e && e.message) || e); }
  out[key] = row;
}
try { await describe(await eda.pcb_ManufactureData.getDsnFile('hwv'), 'dsn'); }
catch (e) { out.errors.dsn = String((e && e.message) || e); }
try { await describe(await eda.pcb_ManufactureData.getAutoRouteJsonFile(), 'json'); }
catch (e) { out.errors.json = String((e && e.message) || e); }
return out;
"""


#: 飞线（ratsnest）。**布线器要连的就是飞线；飞线没算 = 没有连通性图。**
#: 这一条 P17 完全没碰过，而 `PCB_Document` 上明摆着有这三个接口。
_JS_RATLINE_STATUS = r"""
try {
  const st = await eda.pcb_Document.getCalculatingRatlineStatus();
  return {ok: true, status: st === undefined ? null : st};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""

_JS_RATLINE_START = r"""
try {
  const r = await eda.pcb_Document.startCalculatingRatline();
  return {ok: true, result: r === undefined ? null : r};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""

#: **入参是一个 options 对象，不是两个数字**（`IPCB_RatlineQueryOptions`），
#: 返回 `{items, nextCursor?, hasMore, total?}`。
#:
#: 第一版写 `getRatlines(0, 200)` 并按 `.data` 取值 → 拿到 `[]`，
#: 于是把"读错了"当成"飞线为 0"（AGENTS §3.2）。
_JS_RATLINES = r"""
try {
  const page = await eda.pcb_Document.getRatlines({});
  const items = (page && page.items) || [];
  return {ok: true,
          total: page && page.total !== undefined ? page.total : null,
          hasMore: !!(page && page.hasMore),
          count: items.length,
          sample: items.slice(0, 3).map(r => {
            try { return JSON.stringify(r).slice(0, 90); } catch (e) { return '?'; }
          })};
} catch (e) { return {ok: false, error: String((e && e.message) || e)}; }
"""


#: 焊盘的真相。**"布不通"要么是没焊盘，要么是焊盘没 net。**
_JS_PADS = r"""
const out = {ok: true};
try {
  const pads = await eda.pcb_PrimitivePad.getAll();
  const list = pads || [];
  out.count = list.length;
  out.sample = list.slice(0, 4).map(p => {
    const row = {};
    for (const k of ['net', 'netName', 'designator', 'componentDesignator',
                     'layer', 'padNumber', 'primitiveId']) {
      try { if (p[k] !== undefined) row[k] = String(p[k]).slice(0, 40); } catch (e) {}
    }
    try { row.keys = Object.keys(p).slice(0, 14).join(','); } catch (e) {}
    return row;
  });
} catch (e) { out.ok = false; out.error = String((e && e.message) || e); }
return out;
"""


#: DRC 规则。**"GUI 能布、CLI 不能"最可能的差异就在这里**：
#: GUI 打开工程时会装载规则配置，CLI 会话可能拿到一份空的。
_JS_RULES = r"""
const out = {ok: true};
try {
  out.default = await eda.pcb_Drc.getDefaultRuleConfigurationName();
} catch (e) { out.default_error = String((e && e.message) || e); }
try {
  out.current = await eda.pcb_Drc.getCurrentRuleConfigurationName();
} catch (e) { out.current_error = String((e && e.message) || e); }
try {
  const all = await eda.pcb_Drc.getAllRuleConfigurations();
  out.all = Array.isArray(all) ? all.map(r => (typeof r === 'string' ? r : (r && r.name) || '?')) : all;
} catch (e) { out.all_error = String((e && e.message) || e); }
try {
  const cfg = await eda.pcb_Drc.getCurrentRuleConfiguration();
  const text = JSON.stringify(cfg);
  out.config_chars = text ? text.length : 0;
  out.config_head = (text || '').slice(0, 600);
} catch (e) { out.config_error = String((e && e.message) || e); }
return out;
"""

#: 布之前把**默认规则配置**应用上去。
_JS_APPLY_RULES = r"""
const name = __NAME__;
try {
  const r = await eda.pcb_Drc.setAsDefaultRuleConfiguration(name);
  return {ok: true, set_default: r === undefined ? null : r};
} catch (e) {
  return {ok: false, error: String((e && e.message) || e)};
}
"""

def design() -> DesignModel:
    """一块小但真实的板：3 个 0402 + 2 条网络，全部在板框内。

    比 P17 夹具多留了间距（P17 的 R1/R2 只隔 14mm，0402 布线空间足够，
    但那时还没资格讨论"布得开"——先确认引擎会不会开始）。
    """
    board = Board(kind="generic", name="probe",
                  outline=[(0, 0), (50, 0), (50, 40), (0, 40)],
                  size_mm={"w": 50, "h": 40}, thickness_mm=1.6)
    d = DesignModel(title="route probe")
    d.board = board
    for ref, (x, y) in {"R1": (10, 12), "R2": (34, 12), "R3": (22, 28)}.items():
        d.add_part(Part(ref=ref, kind="capacitor", label="0402B102K500NT",
                        lcsc=LCSC, size_mm={"w": 1.0, "h": 0.5, "d": 0.5},
                        physical_source="datasheet",
                        placement=Placement(x_mm=x, y_mm=y, side="top"),
                        pins=["1", "2"]))
    d.connect("MID", ("R1", "1"), ("R2", "1"))
    d.connect("GND", ("R1", "2"), ("R2", "2"), ("R3", "2"))
    return d


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    keep = "--keep" in args
    out_path = Path(__file__).resolve().parents[1] / "route_probe.json"

    report: dict[str, Any] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                  time.gmtime())}
    d = design()
    exe = lceda.find_exe()
    session = lceda.LcedaSession(exe)
    try:
        session.open()
        print("=== 1. 建工程（**同一个会话内** project+schematic+pcb）===")
        # 不调 `emit()`：它自己开一个会话然后关掉，而之后我们要在这个会话里
        # 反复调路由。工程名每次唯一——`createProject` 对同名工程返回
        # undefined（实测），重名会直接崩在 open_project。
        name = f"{PROBE_NAME}-{time.strftime('%H%M%S')}"
        pieces = emit_lceda.EmitReport()
        # 三段都用 emit 自己的实现（不重写）：project 段负责建工程 + 补板数
        # + 等就绪，schematic/pcb 段负责装器件与摆放。
        emit_lceda._run_project(session, d, pieces, name=name, reuse=False)
        emit_lceda._run_schematic(session, d, pieces)
        emit_lceda._run_pcb(session, d, pieces)
        report["project"] = {k: pieces.project.get(k) for k in
                             ("uuid", "name", "friendly", "created")}
        report["emit"] = {k: {"status": v.status, "note": v.note[:200],
                              "counts": v.counts}
                          for k, v in pieces.jobs.items()}
        for label, row in report["emit"].items():
            print(f"  {label}: {row['status']} {row['note'][:90]}")

        env = session.invoke(_JS_ENV, require_ok=False, timeout_ms=60_000,
                             label="env")
        report["env"] = env
        print(f"  元件={env.get('components')} 网络={env.get('nets')}")

        layers = session.invoke(_JS_LAYERS, require_ok=False,
                                timeout_ms=30_000, label="layers")
        report["layers"] = layers

        print("=== 1.5 运行时 API + 每个网络的焊盘数 ===")
        api = session.invoke(_JS_API, require_ok=False, timeout_ms=30_000,
                             label="api")
        report["api"] = api
        print(f"  pcb_Document 里 pad/net/rule/rout 相关：{api.get('doc')}")
        print(f"  pcb_Net 方法：{api.get('net')}")
        print(f"  pcb_Component 方法：{api.get('comp')}")
        nets = list(env.get("nets") or [])
        pads = session.invoke(
            _JS_NETPADS.replace("__NETS__", json.dumps(nets, ensure_ascii=False)),
            require_ok=False, timeout_ms=60_000, label="netpads")
        report["net_pads"] = pads
        print(f"  网络图元：{json.dumps(pads.get('nets'), ensure_ascii=False)}")
        detail = session.invoke(_JS_NETDETAIL, require_ok=False,
                                timeout_ms=60_000, label="netdetail")
        report["net_detail"] = detail
        print(f"  网络详情：{json.dumps(detail, ensure_ascii=False)[:400]}")
        enabled = [row for row in (layers.get("layers") or [])
                   if row.get("status") not in (None, 0)]
        print(f"  层={len(layers.get('layers') or [])} 其中启用 {len(enabled)}")

        print("=== 2. 三次 autoRouting 尝试 ===")
        for tag, script in (("A_zero_args", _JS_A), ("B_layers", _JS_B),
                            ("C_options", _JS_C)):
            got = session.invoke(script, require_ok=False, timeout_ms=900_000,
                                 label=tag)
            report[tag] = got
            print(f"  {tag}: ok={got.get('ok')} "
                  f"ms={got.get('ms')} "
                  f"result={json.dumps(got.get('result'), ensure_ascii=False)[:160]}")

        print("=== 2.0 DRC 规则（GUI 有、CLI 可能没有） ===")
        rules = session.invoke(_JS_RULES, require_ok=False,
                               timeout_ms=60_000, label="rules")
        report["rules"] = rules
        print(f"  default={rules.get('default')!r} current={rules.get('current')!r}")
        print(f"  all={json.dumps(rules.get('all'), ensure_ascii=False)[:200]}")
        print(f"  config 长度={rules.get('config_chars')} "
              f"head={str(rules.get('config_head'))[:200]}")
        for k in ("default_error", "current_error", "all_error", "config_error"):
            if rules.get(k):
                print(f"  {k}: {rules[k]}")

        print("=== 2.1 焊盘（有没有 / 挂没挂 net） ===")
        pads_info = session.invoke(_JS_PADS, require_ok=False,
                                   timeout_ms=60_000, label="pads")
        report["pads"] = pads_info
        print(f"  焊盘数={pads_info.get('count')} err={pads_info.get('error')}")
        for row in pads_info.get("sample") or []:
            print(f"    {json.dumps(row, ensure_ascii=False)[:220]}")

        print("=== 2.2 飞线（布线器要连的东西） ===")
        before = session.invoke(_JS_RATLINE_STATUS, require_ok=False,
                                timeout_ms=30_000, label="rat_before")
        report["ratline_before"] = before
        print(f"  算之前状态: {json.dumps(before, ensure_ascii=False)}")
        started = session.invoke(_JS_RATLINE_START, require_ok=False,
                                 timeout_ms=120_000, label="rat_start")
        report["ratline_start"] = started
        print(f"  startCalculatingRatline: {json.dumps(started, ensure_ascii=False)[:160]}")
        for _ in range(5):
            time.sleep(1.0)
            st = session.invoke(_JS_RATLINE_STATUS, require_ok=False,
                                timeout_ms=30_000, label="rat_after")
            report.setdefault("ratline_poll", []).append(st)
            print(f"  状态: {json.dumps(st, ensure_ascii=False)[:120]}")
        rat = session.invoke(_JS_RATLINES, require_ok=False,
                             timeout_ms=60_000, label="ratlines")
        report["ratlines"] = rat
        print(f"  飞线条数: {json.dumps(rat, ensure_ascii=False)[:200]}")

        if job is not None:
            again = session.invoke(
                _JS_NETPADS.replace("__NETS__",
                                    json.dumps(list(env.get("nets") or []),
                                               ensure_ascii=False)),
                require_ok=False, timeout_ms=60_000, label="pads_after")
            report["net_primitives_after_job"] = again
            print(f"  作业后各网络图元：{json.dumps(again.get('nets'), ensure_ascii=False)}")

        print("=== 2.5 交换格式导出（绕开内部引擎的路） ===")
        exported = session.invoke(_JS_EXPORT, require_ok=False,
                                  timeout_ms=120_000, label="export")
        report["export"] = exported
        print(f"  DSN: {json.dumps(exported.get('dsn'), ensure_ascii=False)[:200]}")
        print(f"  JSON: {json.dumps(exported.get('json'), ensure_ascii=False)[:200]}")
        if exported.get("errors"):
            print(f"  错误: {exported['errors']}")

        print("=== 3. 作业模型 + 日志（P17 没做过） ===")
        started = session.invoke(_JS_D_START, require_ok=False,
                                 timeout_ms=120_000, label="job_start")
        report["job_start"] = started
        print(f"  startAutoRouting: {json.dumps(started, ensure_ascii=False)[:200]}")
        job = started.get("job")
        if job is not None:
            job_id = (job.get("jobId") if isinstance(job, dict) else job) or ""
            payload = json.dumps(job_id, ensure_ascii=False)
            for attempt in range(24):
                time.sleep(2.0)
                st = session.invoke(
                    _JS_D_STATUS.replace("__JOB__", payload),
                    require_ok=False, timeout_ms=60_000, label="job_status")
                report.setdefault("job_status", []).append(st)
                print(f"  状态[{attempt}] {json.dumps(st, ensure_ascii=False)[:180]}")
                done = json.dumps(st, ensure_ascii=False)
                if any(k in done.lower() for k in ("finish", "done", "fail",
                                                   "cancel", "error")):
                    break
            log = session.invoke(_JS_D_LOG.replace("__JOB__", payload),
                                 require_ok=False, timeout_ms=60_000,
                                 label="job_log")
            report["job_log"] = log
            print(f"  日志: {json.dumps(log, ensure_ascii=False)[:600]}")
        else:
            report["job_log"] = {"skipped": "作业没起来，没有 jobId 可查"}
    finally:
        session.close()

        print("=== 4. 应用默认规则后重试 ===")
        default_name = rules.get("default")
        if isinstance(default_name, str) and default_name:
            applied = session.invoke(
                _JS_APPLY_RULES.replace("__NAME__",
                                        json.dumps(default_name, ensure_ascii=False)),
                require_ok=False, timeout_ms=60_000, label="apply_rules")
            report["apply_rules"] = applied
            print(f"  应用规则: {json.dumps(applied, ensure_ascii=False)[:160]}")
            after = session.invoke(_JS_A, require_ok=False, timeout_ms=900_000,
                                   label="route_after_rules")
            report["route_after_rules"] = after
            print(f"  规则后布线: {json.dumps(after, ensure_ascii=False)[:200]}")
        else:
            print("  没有默认规则名可应用 —— 这一条**不是**通过，是没有对象")

    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"\n写到 {out_path}")
    if not keep:
        print("（加 --keep 可保留刚建的工程）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())