"""``design.json`` → 立创EDA专业版工程（``.eprj3``）：四段发射 + 回读校验。

## 为什么是四段，而不是一个函数

段与段**只通过立创工程状态耦合**，不传内存对象。这样：

* 任何一段失败，已落盘的部分仍然可用（不会把半成品的内存态一起丢掉）；
* 前端可以分开触发（先出原理图看看，再出 PCB）；
* 重跑某一段不需要重跑前面的。

| 段 | 做什么 | 失败时的后果 |
|---|---|---|
| `project` | 建工程 / 逐板建板 / 设层数 | 后面全做不了，直接停 |
| `schematic` | 装器件 → 引脚处挂导线 → 网络标签 | 器件搜不到进 `unresolved`，**其余继续** |
| `pcb` | 画板框 → `importChanges` 导入 → 按设计坐标摆放 → DRC | 坐标缺失的器件留在导入位置并报出 |
| `outputs` | Gerber / BOM / 坐标文件 | 生成成功但落盘要外部权限（见下） |

## 实测的三条硬事实（决定了代码为什么长这样）

**R1. 网络标签必须挂在导线上。** 只把标签放在引脚坐标上，DRC 会报
`CHECKFLOATPIN`，PCB 网表里该引脚 `net` 为空。所以每个引脚先拉一段 100
单位的短线，标签放在线端——实测这样 `importChanges` 之后 PCB 网表
`pinInfoMap[pin].net` 才是对的。

**R2. 原理图网表读回来是**陈旧的**。** 刚写完引脚就
`sch_Netlist.getNetlist('JLCEDA')`，`nets` 还是空的；同一时刻
`importChanges` 导进 PCB 却是对的。所以 parity **以 PCB 网表为准**，
不以原理图网表为准（差别：多一次导入，但那才是真正被拿去制造的东西）。

**R3. `importChanges` 不管坐标。** 它把器件放在板框外面（实测 1600×1200
的板，器件落在 y≈-225），DRC 立刻报「焊盘到板框间距不足」。所以导入之后
必须按 `Placement.x_mm/y_mm` 逐个 `modify` 摆进去。

## 坐标

设计里是 mm、原点在板左下角；立创 PCB 单位是 mil（1 mm = 39.3700787 mil，
见 `lceda.MM_TO_MIL`）。板框和器件坐标用**同一套换算**，所以相对关系一致。
立创 Y 轴的方向（向上/向下）本机**未做对照实验**，因此非对称板框的上下
镜像可能反了——这一条记在这里，不装作知道。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import lceda as lceda_mod
from . import stackup_spec
from .model import DesignModel

__all__ = [
    "EmitReport",
    "JobResult",
    "JOBS",
    "ROUTES",
    "emit",
    "read_back",
    "describe",
]

#: 四段 + 两个可独立触发的验证段。
JOBS = ("project", "schematic", "pcb", "route", "outputs")

#: 布线三条路。
ROUTES = ("native", "gui", "ours", "none")

#: 原理图网格：列宽/行高（原理图单位 = 10 mil，见 lceda.SCH_UNIT_MM）。
GRID_COLS = 2
GRID_DX = 700
GRID_DY = 600
GRID_X0 = 500
GRID_Y0 = 400

#: 引脚引出线长度（原理图单位）。太短标签会压到符号上。
STUB_LEN = 120

#: 默认最多装多少个器件（防止 316 元件的工程把会话拖死）。
MAX_PARTS = 260


# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------


@dataclass
class JobResult:
    """一段的结果。``status`` 只有三种：ok / not_ready / failed。"""

    name: str
    status: str = "not_ready"
    note: str = ""
    counts: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "counts": dict(self.counts),
        }
        if self.note:
            out["note"] = self.note
        if self.artifacts:
            out["artifacts"] = list(self.artifacts)
        if self.warnings:
            out["warnings"] = list(self.warnings)
        return out


@dataclass
class EmitReport:
    """发射总报告。落盘为 ``<工程>/emit.json``。"""

    status: str = "not_ready"
    version: str = ""
    project: dict[str, Any] = field(default_factory=dict)
    jobs: dict[str, JobResult] = field(default_factory=dict)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    parity: dict[str, Any] = field(default_factory=dict)
    drc: dict[str, Any] = field(default_factory=dict)
    stackup: dict[str, Any] = field(default_factory=dict)
    #: 成功装进原理图的位号。给 PCB 段轮询用：`importChanges` 之后图元表
    #: **不是立刻满的**（实测同一夹具 3 个元件，一次等 2.5s 只看到 2 个）。
    placed_refs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def job(self, name: str) -> JobResult:
        result = self.jobs.get(name)
        if result is None:
            result = JobResult(name=name)
            self.jobs[name] = result
        return result

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "version": self.version,
            "project": dict(self.project),
            "jobs": {k: v.to_dict() for k, v in self.jobs.items()},
            "commands": list(JOBS),
            "routes": list(ROUTES),
        }
        if self.unresolved:
            payload["unresolved"] = list(self.unresolved)
        if self.parity:
            payload["parity"] = dict(self.parity)
        if self.drc:
            payload["drc"] = dict(self.drc)
        if self.stackup:
            payload["stackup"] = dict(self.stackup)
        if self.notes:
            payload["notes"] = list(self.notes)
        return payload

    def recompute_status(self) -> str:
        """总状态：任一 failed → failed；否则任一 not_ready → not_ready；否则 ok。

        **空 jobs 表不算 ok**（AGENTS §0.1）：什么都没跑就是 `not_ready`。
        """
        statuses = [j.status for j in self.jobs.values()]
        if not statuses:
            self.status = "not_ready"
        elif "failed" in statuses:
            self.status = "failed"
        elif "not_ready" in statuses:
            self.status = "not_ready"
        else:
            self.status = "ok"
        return self.status


# ---------------------------------------------------------------------------
# 设计侧的准备（纯函数，可单测）
# ---------------------------------------------------------------------------


def _sanitize_project_name(name: str) -> str:
    """工程名只允许 a-zA-Z0-9-（`createProject` 的硬限制）。"""
    cleaned = re.sub(r"[^a-zA-Z0-9-]+", "-", name).strip("-")
    return cleaned[:48] or "hwv-design"


def _lcsc_of(part: Any) -> str:
    """取立创编号。占位符（`?` / `C?` / 空）当没有。"""
    raw = str(getattr(part, "lcsc", "") or "").strip()
    if not raw or "?" in raw:
        return ""
    if not re.fullmatch(r"C\d+", raw, re.IGNORECASE):
        # 立创编号形如 C1523；别的写法不去猜
        return ""
    return raw.upper()


def _mpn_of(part: Any) -> str:
    for key in ("mpn", "manufacturer_part", "model"):
        raw = str(getattr(part, key, "") or "").strip()
        if raw and "?" not in raw:
            return raw
    label = str(getattr(part, "label", "") or "").strip()
    # label 有明确型号特征才拿来搜（含数字且长度 >= 4），否则不猜
    if len(label) >= 4 and re.search(r"\d", label) and "?" not in label:
        return label
    return ""


def _parts_for_board(design: DesignModel, board_id: str, total_boards: int) -> list[Any]:
    """挑出这块板上的元件（顺序稳定，便于网格坐标可复现）。

    用模型自己的 `board_of()`，不在这里重写一遍分板规则 —— 那套规则已经有
    「`board_id` 空 → 主参考板」「给了但找不到 → 也退主参考板」两条约定，
    自己写一份早晚分叉（AGENTS §2.5：复用已有判据，别各写一份）。
    """
    if total_boards == 1:
        return list(design.parts)
    picked = []
    for part in design.parts:
        if design.board_of(part).id == board_id:
            picked.append(part)
    return picked


def _grid_xy(index: int) -> tuple[int, int]:
    """第 index 个器件的原理图坐标（单位 10 mil）。"""
    col = index % GRID_COLS
    row = index // GRID_COLS
    return GRID_X0 + col * GRID_DX, GRID_Y0 + row * GRID_DY


def _pcb_xy(design: DesignModel, part: Any) -> tuple[float, float]:
    """元件在板上的 mil 坐标（F3 换算）。"""
    placement = getattr(part, "placement", None)
    x_mm = float(getattr(placement, "x_mm", 0.0) or 0.0)
    y_mm = float(getattr(placement, "y_mm", 0.0) or 0.0)
    return lceda_mod.mm_to_mil(x_mm), lceda_mod.mm_to_mil(y_mm)


def _pcb_layer(part: Any) -> int:
    side = str(getattr(getattr(part, "placement", None), "side", "") or "")
    return 2 if side == "bottom" else 1


def _outline_source(design: DesignModel, board: Any) -> list[Any]:
    """板框 → `TPCB_PolygonSourceArray`（mil）。

    矩形走 `['R', …]`（实测能建），其它走折线 `[x0,y0,'L',x1,y1,…]`。
    """
    outline = [(float(x), float(y)) for x, y in (board.outline or [])]
    if len(outline) < 3:
        return []
    xs = {round(x, 3) for x, _ in outline}
    ys = {round(y, 3) for _, y in outline}
    if len(outline) == 4 and len(xs) == 2 and len(ys) == 2:
        x0, y0 = min(xs), min(ys)
        w = max(xs) - x0
        h = max(ys) - y0
        # **`R` 模式的 (x, y) 是左上角，矩形向 −Y 延伸 height**（手册 §19 实测）。
        # 所以左上角的 y 必须写 `max(ys)`，不是 `min(ys)`。
        #
        # 这条写错的后果**不报错、只表现为布线布不出来**：
        # 板框落在 y∈[−h, 0]，而元件摆在 y>0 → 整块板在板框外
        # → `autoRouting` 返回 `{totalNetsCount: 2, successNetsCount: 0,
        #    failedNets: [全部], duration: ~800}`（板内根本没有可布的空间）。
        # 2026-11-21 实测：只把这一处从 `y0` 改成 `y0+h`，同一块板立刻布出
        # 9 段 45° 单层走线、0 过孔。之前记的「引擎拒绝」「headless 没有
        # 布线执行环境」两条结论**都是把这条 bug 当成了环境问题**。
        return ["R", lceda_mod.mm_to_mil(x0), lceda_mod.mm_to_mil(y0 + h),
                lceda_mod.mm_to_mil(w), lceda_mod.mm_to_mil(h), 0, 0]
    flat: list[Any] = []
    for i, (x, y) in enumerate(outline):
        if i:
            flat.append("L")
        flat.extend([lceda_mod.mm_to_mil(x), lceda_mod.mm_to_mil(y)])
    if outline[0] != outline[-1]:
        flat.append("L")
        flat.extend([lceda_mod.mm_to_mil(outline[0][0]), lceda_mod.mm_to_mil(outline[0][1])])
    return flat


# ---------------------------------------------------------------------------
# 段 1：工程
# ---------------------------------------------------------------------------

_JS_PROJECT_NEW = r"""
const base = __NAME__;
// 立创不允许重名工程，而**文件名查不了**（getProjectsPaths 也被外部交互权限
// 拦着，实测报「未启用扩展和独立脚本的外部交互权限」）。所以只能试：
// createProject 重名时返回 undefined，加序号再试，最多 20 次。
let projectUuid = null;
let want = base;
for (let i = 1; i <= 20 && !projectUuid; i++) {
  want = (i === 1) ? base : (base + '-' + i);
  projectUuid = await eda.dmt_Project.createProject(
    want, undefined, undefined, undefined, 'hw_velxio emit');
  if (!projectUuid) await new Promise(r => setTimeout(r, 200));
}
if (!projectUuid) throw new Error('连试 20 个名字都建不了工程（全被占用？）');
const opened = await eda.dmt_Project.openProject(projectUuid);
if (!opened) {
  // 实测偶发一次 false，重试一次即成功
  await new Promise(r => setTimeout(r, 800));
  const again = await eda.dmt_Project.openProject(projectUuid);
  if (!again) throw new Error('openProject 连续两次返回 false');
}
return {ok: true, projectUuid: projectUuid, created: true, name: want};
"""

#: 工程已打开时只保证「板数够」。
_JS_ENSURE_BOARDS = r"""
const want = __BOARDS__;
let info = null;
for (let i = 0; i < 60; i++) {
  const cur = await eda.dmt_Project.getCurrentProjectInfo();
  const d0 = cur && cur.data && cur.data[0];
  if (d0 && d0.schematic && d0.schematic.page && d0.schematic.page.length
      && d0.pcb && d0.pcb.uuid) { info = cur; break; }
  await new Promise(r => setTimeout(r, 250));
}
if (!info) throw new Error('工程 15 秒内没有就绪（schematic 或 pcb 缺失）');
const live0 = (info.data || []).filter(b => b.pcb && b.pcb.uuid);
for (let i = live0.length; i < want; i++) {
  const name = await eda.dmt_Board.createBoard();
  if (!name) throw new Error('createBoard 第 ' + (i + 1) + ' 块返回 undefined');
  await new Promise(r => setTimeout(r, 600));
}
return {ok: true, projectUuid: info.uuid, created: false, name: info.friendlyName || ''};
"""

def _run_project(session: lceda_mod.LcedaSession, design: DesignModel,
                 report: EmitReport, *, name: str, reuse: bool) -> None:
    """建（或接管）工程并补齐板数。

    **同名工程只能靠磁盘判断是否已存在**：实测 `getAllProjectsUuid()`
    在工程未打开时返回空数组（n=0），拿它找同名工程永远找不到。
    """
    job = report.job("project")
    boards = design.board_list()

    if not reuse:
        script = _JS_PROJECT_NEW.replace("__NAME__", json.dumps(name, ensure_ascii=False))
        value = session.invoke(script, expect=("projectUuid",), label="project")
        created = True
        name = str(value.get("name") or name)
    else:
        value = {"projectUuid": session.project_uuid, "created": False}
        created = False

    ensure = _JS_ENSURE_BOARDS.replace("__BOARDS__", str(max(1, len(boards))))
    live_value = session.invoke(ensure, expect=("projectUuid",), label="boards")
    session.project_uuid = str(live_value.get("projectUuid") or value.get("projectUuid") or "")

    ready = session.wait_project_ready()
    live = [b for b in (ready.get("boards") or []) if b.get("pcbUuid")]
    report.project.update({
        "uuid": session.project_uuid,
        "name": name,
        "friendly": ready.get("friendlyName") or "",
        "created": created,
        "boards": live,
    })
    job.counts = {"boards_requested": len(boards), "boards_live": len(live)}
    if len(live) < len(boards):
        job.status = "not_ready"
        job.note = f"设计有 {len(boards)} 块板，立创里只有 {len(live)} 块"
        return
    job.status = "ok"
    job.note = "新建工程" if created else "接管已存在的同名工程"
    if len(boards) > 1:
        job.warnings.append(
            "多板按顺序对应：设计第 i 块 → 立创第 i 块（板名是尺寸串，无法按名字匹配）")


# ---------------------------------------------------------------------------
# 段 2：原理图
# ---------------------------------------------------------------------------

_JS_SCHEMATIC = r"""
const P = __CTX__;
const parts = __PARTS__;
const sysLib = await eda.lib_LibrariesList.getSystemLibraryUuid();
if (!sysLib) throw new Error('拿不到系统库 uuid（lib_LibrariesList.getSystemLibraryUuid）');

const opened = await eda.dmt_EditorControl.openDocument(P.pageUuid);
if (!opened) throw new Error('openDocument 返回空（pageUuid 对不对？）');
await new Promise(r => setTimeout(r, P.settleMs));

const id = {projectId: P.projectId, schematicName: P.schematicName, pageName: P.pageName};
const placed = [];
const unresolved = [];

let fuzzyMisses = 0;

async function resolve(p) {
  if (p.lcsc) {
    const hits = await eda.lib_Device.searchByProperties(
      {supplierId: p.lcsc}, undefined, undefined, undefined, 5, 1);
    // searchByProperties 是**模糊**的：查一个不存在的 C 编号也会返回近似结果
    // （实测 C999999999 返回了非空列表）。所以必须回核 Supplier Part。
    for (const hit of (hits || [])) {
      const got = String(((hit.otherProperty || {})['Supplier Part']) || '');
      if (got && got.toUpperCase() === String(p.lcsc).toUpperCase()) {
        return {dev: hit, how: 'lcsc'};
      }
      fuzzyMisses += 1;
    }
  }
  if (p.mpn) {
    let hits = [];
    try { hits = await eda.lib_Device.search(p.mpn, undefined, undefined, undefined, 5, 1); }
    catch (e) { hits = []; }
    for (const hit of (hits || [])) {
      // 模糊搜索会返回近似结果 → 必须核对型号，不能拿第一个
      const props = (hit && hit.otherProperty) || {};
      const mpn = String(props['Manufacturer Part'] || '');
      const key = String(p.mpn || '');
      if (mpn && key && mpn.toLowerCase() === key.toLowerCase()) return {dev: hit, how: 'mpn'};
    }
    if (hits && hits.length && String(hits[0].name || '').toLowerCase() === String(p.mpn).toLowerCase()) {
      return {dev: hits[0], how: 'name'};
    }
  }
  return {dev: null, how: '', fuzzyMisses: fuzzyMisses};
}

for (const p of parts) {
  const found = await resolve(p);
  if (!found.dev) {
    unresolved.push({
      ref: p.ref, reason: 'no_symbol', lcsc: p.lcsc, mpn: p.mpn,
      detail: found.fuzzyMisses
        ? (found.fuzzyMisses + ' 个模糊命中，但 Supplier Part 对不上（不采用）')
        : '系统库里没有这个 lcsc/mpn',
    });
    continue;
  }
  const r = await eda.sch_SourcePrimitiveComponent.add({
    ...id, libraryUuid: sysLib, name: found.dev.name,
    x: p.x, y: p.y, designator: p.ref,
  });
  if (!r || !r.ok) {
    unresolved.push({
      ref: p.ref, reason: 'add_failed', lcsc: p.lcsc,
      error: (r && r.error) || null,
    });
    continue;
  }
  placed.push({ref: p.ref, designator: r.value.designator, how: found.how,
               name: found.dev.name, x: p.x, y: p.y});
}

// 引脚坐标只能从 get 拿；按 ref 缓存，一个器件只查一次。
const pinsByRef = {};
async function pinsOf(ref, designator) {
  if (pinsByRef[ref]) return pinsByRef[ref];
  const g = await eda.sch_SourcePrimitiveComponent.get({...id, designator: designator});
  if (!g || !g.ok) return null;
  pinsByRef[ref] = (g.value && g.value.pins) || [];
  return pinsByRef[ref];
}

const wired = [];
const wiringFailed = [];
for (const net of (P.nets || [])) {
  for (const pin of (net.pins || [])) {
    const target = placed.find(x => x.ref === pin.ref);
    if (!target) continue;
    const pins = await pinsOf(pin.ref, target.designator);
    if (!pins) { wiringFailed.push({net: net.net, ref: pin.ref, pin: pin.pin, why: 'get_failed'}); continue; }
    const hit = pins.find(x => String(x.pinNumber) === String(pin.pin));
    if (!hit) { wiringFailed.push({net: net.net, ref: pin.ref, pin: pin.pin, why: 'pin_not_found'}); continue; }
    // R1：标签必须挂在线端 → 先拉短线，再放标签。
    // 方向必须**背离器件锚点**：两个引脚都往右拉时，同一器件的两根平行短线
    // 会在 y 相同处重叠，`wire.add` 的「连通分量规范化」把它们并成一组，
    // 一个网络名吃掉另一个（实测 MID 整条被并成 GND，只剩 1 个网）。
    const dir = (hit.x >= target.x) ? 1 : -1;
    const endX = hit.x + dir * P.stubLen, endY = hit.y;
    const w = await eda.sch_SourcePrimitiveWire.add({...id, line: [hit.x, hit.y, endX, endY], net: net.net});
    if (!w || !w.ok) { wiringFailed.push({net: net.net, ref: pin.ref, pin: pin.pin, why: 'wire_failed', error: (w && w.error) || null}); continue; }
    const l = await eda.sch_SourcePrimitiveAttribute.addNetLabel({...id, net: net.net, x: endX, y: endY});
    if (!l || !l.ok) { wiringFailed.push({net: net.net, ref: pin.ref, pin: pin.pin, why: 'label_failed', error: (l && l.error) || null}); continue; }
    wired.push({net: net.net, ref: pin.ref, pin: pin.pin});
  }
}

return {ok: true, placed: placed, unresolved: unresolved, wired: wired, wiringFailed: wiringFailed};
"""


def _board_context(design: DesignModel, live_board: dict[str, Any],
                   report: EmitReport) -> dict[str, Any]:
    return {
        "projectId": report.project.get("uuid") or "",
        "schematicName": live_board.get("schematicName") or "",
        "pageName": live_board.get("pageName") or "",
        "pageUuid": live_board.get("pageUuid") or "",
        "pcbUuid": live_board.get("pcbUuid") or "",
        "schematicUuid": live_board.get("schematicUuid") or "",
        "settleMs": 800,
        "stubLen": STUB_LEN,
        "nets": list(design.nets),
    }


def _run_schematic(session: lceda_mod.LcedaSession, design: DesignModel,
                   report: EmitReport, *, max_parts: int = MAX_PARTS) -> None:
    """逐板装器件、拉线、贴网络标签。搜不到符号的器件进 `unresolved`。"""
    job = report.job("schematic")
    design_boards = design.board_list()
    live_boards = report.project.get("boards") or []
    total_boards = len(design_boards)

    placed_total = 0
    wired_total = 0
    resolve_how: dict[str, int] = {}
    failed: list[str] = []
    for index, live in enumerate(live_boards):
        board = design_boards[index] if index < len(design_boards) else None
        if board is None:
            continue
        parts = _parts_for_board(design, board.id or "", total_boards)
        payload = []
        for i, part in enumerate(parts[:max_parts]):
            x, y = _grid_xy(i)
            payload.append({
                "ref": part.ref,
                "lcsc": _lcsc_of(part),
                "mpn": _mpn_of(part),
                "x": x,
                "y": y,
            })
        if not payload:
            continue
        ctx = _board_context(design, live, report)
        script = (_JS_SCHEMATIC
                  .replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                  .replace("__PARTS__", json.dumps(payload, ensure_ascii=False)))
        try:
            value = session.invoke(script, expect=("placed", "unresolved"),
                                   timeout_ms=600_000, label=f"schematic/{live.get('name')}")
        except lceda_mod.LcedaError as exc:
            failed.append(f"{live.get('name')}：{exc}")
            continue
        placed_total += len(value.get("placed") or [])
        report.placed_refs.extend(str(x.get("ref")) for x in (value.get("placed") or []))
        for item in value.get("placed") or []:
            how = str(item.get("how") or "?")
            resolve_how[how] = resolve_how.get(how, 0) + 1
        wired_total += len(value.get("wired") or [])
        for item in value.get("unresolved") or []:
            report.unresolved.append({**item, "board": live.get("name")})
        for item in value.get("wiringFailed") or []:
            report.unresolved.append({
                "ref": item.get("ref"), "pin": item.get("pin"),
                "net": item.get("net"), "reason": f"wire:{item.get('why')}",
                "board": live.get("name"),
            })
        if len(parts) > max_parts:
            job.warnings.append(
                f"{live.get('name')}：{len(parts)} 个元件只装了前 {max_parts} 个"
                f"（MAX_PARTS，防止会话拖死）")

    job.counts = {
        "placed": placed_total,
        "wired": wired_total,
        "unresolved": len(report.unresolved),
        # 按哪种线索找到符号：lcsc（精确）/ mpn（核对型号）/ label（按标签猜）
        "resolved_by": resolve_how,
    }
    if failed:
        job.status = "failed"
        job.note = "；".join(failed)[:600]
        return
    if placed_total == 0:
        job.status = "not_ready"
        job.note = "一个器件都没装上（缺少 lcsc/mpn，或系统库不可用）"
        return
    job.status = "ok"
    job.counts["unresolved"] = len(report.unresolved)
    job.note = (f"装 {placed_total} 个器件、{wired_total} 个引脚接线"
                + (f"；{len(report.unresolved)} 个未解析" if report.unresolved else ""))


# ---------------------------------------------------------------------------
# 段 3：PCB
# ---------------------------------------------------------------------------

_JS_PCB = r"""
const P = __CTX__;
const outline = __OUTLINE__;
const comps = __COMPS__;
const steps = [];
async function attempt(name, fn) {
  try { const v = await fn(); steps.push({step: name, ok: true, value: v}); return v; }
  catch (e) { steps.push({step: name, ok: false, error: String((e && e.message) || e)}); return null; }
}

await attempt('openPcb', () => eda.dmt_EditorControl.openDocument(P.pcbUuid));
await new Promise(r => setTimeout(r, P.settleMs));

let outlineOk = null;
if (outline && outline.length) {
  outlineOk = await attempt('outline', async () => {
    const poly = eda.pcb_MathPolygon.createPolygon(outline);
    if (!poly) throw new Error('createPolygon 返回 undefined（源数组不合法）');
    const pl = await eda.pcb_PrimitivePolyline.create('', 11, poly, 10, false);
    if (!pl) throw new Error('pcb_PrimitivePolyline.create 返回空');
    return pl.getState_PrimitiveId ? pl.getState_PrimitiveId() : 'ok';
  });
}

const imported = await attempt('importChanges',
  () => eda.pcb_Document.importChanges(P.schematicUuid, true));

// importChanges 返回 true 不等于图元已经全部落地：实测同一夹具一次只看到
// 2/3 个（等 2.5s 也不够）。所以按**期望位号**轮询，最多 6 秒。
const expected = P.expected || [];
let all = [];
let waited = 0;
for (let i = 0; i < 20; i++) {
  await new Promise(r => setTimeout(r, 300));
  waited += 300;
  all = (await eda.pcb_PrimitiveComponent.getAll()) || [];
  if (!expected.length) { if (i >= 8) break; continue; }
  const have = new Set(all.map(c => c.getState_Designator ? c.getState_Designator() : ''));
  if (expected.every(ref => have.has(ref))) break;
}
const allCount = all.length;
const byDesignator = {};
for (const c of all) {
  const d = c.getState_Designator ? c.getState_Designator() : null;
  if (d) byDesignator[d] = c;
}
const moved = [];
const missing = [];
for (const want of comps) {
  const obj = byDesignator[want.ref];
  if (!obj) { missing.push(want.ref); continue; }
  const r = await attempt('move:' + want.ref, () => eda.pcb_PrimitiveComponent.modify(
    obj.getState_PrimitiveId(), {x: want.x, y: want.y, layer: want.layer, rotation: want.rotation}));
  if (r) moved.push({ref: want.ref, x: r.getState_X(), y: r.getState_Y()});
}

const saved = await attempt('save', () => eda.pcb_Document.save());
const nets = await attempt('nets', () => eda.pcb_Net.getAllNetName());
const netlist = await attempt('netlist', () => eda.pcb_Net.getNetlist('JLCEDA'));
const drcPass = await attempt('drc', () => eda.pcb_Drc.check(true, false, false));
const drcDetail = await attempt('drcDetail', () => eda.pcb_Drc.check(true, false, true));

return {
  ok: true,
  steps: steps,
  imported: imported,
  outlineOk: outlineOk,
  moved: moved,
  missing: missing,
  componentCount: allCount,
  waitedMs: waited,
  nets: nets,
  netlist: netlist,
  drcPass: drcPass,
  drcDetail: drcDetail,
};
"""


def _run_pcb(session: lceda_mod.LcedaSession, design: DesignModel,
             report: EmitReport) -> None:
    """画板框 → 导入 → 按设计坐标摆放 → DRC。"""
    job = report.job("pcb")
    design_boards = design.board_list()
    live_boards = report.project.get("boards") or []
    total_boards = len(design_boards)

    comps_total = 0
    moved_total = 0
    missing_total: list[str] = []
    outline_ready = 0
    drc_records: list[dict[str, Any]] = []
    netlists: list[str] = []

    # 叠层/网络类/差分对：在摆放之前应用（改层数会让已有铜皮失效）。
    spec = stackup_spec.derive(design)
    applied_steps: list[dict[str, Any]] = []
    for live in live_boards:
        ctx = {"pcbUuid": live.get("pcbUuid") or "", "settleMs": 800}
        try:
            applied = stackup_spec.apply(session, spec, ctx)
        except lceda_mod.LcedaError as exc:
            applied_steps.append({"step": "stackup", "ok": False, "error": str(exc)})
            continue
        applied_steps.extend(applied.get("steps") or [])
        if applied.get("status") != "ok":
            job.warnings.append(
                f"{live.get('name')}：叠层约束部分失败 → {applied.get('failed')}")
    report.stackup = {**spec.to_dict(),
                      "applied": [s for s in applied_steps if s.get("step") != "openPcb"][:40]}
    if spec.assumed:
        report.notes.extend(spec.assumed)

    for index, live in enumerate(live_boards):
        board = design_boards[index] if index < len(design_boards) else None
        if board is None:
            continue
        parts = _parts_for_board(design, board.id or "", total_boards)
        comps = [{
            "ref": part.ref,
            "x": _pcb_xy(design, part)[0],
            "y": _pcb_xy(design, part)[1],
            "layer": _pcb_layer(part),
            "rotation": int(getattr(getattr(part, "placement", None), "rotation_z", 0) or 0),
        } for part in parts]
        ctx = _board_context(design, live, report)
        ctx["expected"] = [p.ref for p in parts if p.ref in set(report.placed_refs)]
        script = (_JS_PCB
                  .replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                  .replace("__OUTLINE__", json.dumps(_outline_source(design, board)))
                  .replace("__COMPS__", json.dumps(comps, ensure_ascii=False)))
        try:
            value = session.invoke(script, expect=("steps",), timeout_ms=600_000,
                                   label=f"pcb/{live.get('name')}")
        except lceda_mod.LcedaError as exc:
            job.status = "failed"
            job.note = f"{live.get('name')}：{exc}"
            return
        steps = {s.get("step"): s for s in (value.get("steps") or [])}
        if steps.get("importChanges", {}).get("ok"):
            comps_total += int(value.get("componentCount") or 0)
        else:
            job.warnings.append(
                f"{live.get('name')}：importChanges 失败 → "
                f"{steps.get('importChanges', {}).get('error')}")
        if value.get("outlineOk"):
            outline_ready += 1
        else:
            err = (steps.get("outline") or {}).get("error") or "板框源数组为空"
            job.warnings.append(f"{live.get('name')}：板框没建成（{err}）")
        moved_total += len(value.get("moved") or [])
        missing_total.extend(value.get("missing") or [])
        if value.get("netlist"):
            netlists.append(str(value.get("netlist")))
        drc_records.append({
            "board": live.get("name"),
            "pass": bool(value.get("drcPass")),
            "violations": _count_violations(value.get("drcDetail")),
            "groups": _drc_groups(value.get("drcDetail")),
        })

    job.counts = {
        "boards": len(live_boards),
        "outline_ready": outline_ready,
        "components_on_pcb": comps_total,
        "moved": moved_total,
        "missing_on_pcb": len(missing_total),
    }
    if missing_total:
        job.warnings.append(
            f"{len(missing_total)} 个元件导入后找不到（位号对不上）：{', '.join(missing_total[:8])}")
    # 空板的 DRC 通过**没有意义**（一块元件都没有的板当然没有违规）。
    # 这正是 AGENTS §0.1 的形状：缺失被写成通过。所以先看有没有东西可查。
    drc_status = "ok" if drc_records else "not_ready"
    drc_note = "立创的 DRC 是**外部证据**，进 handoff 的 external 类，不写进 ladder"
    if drc_records and comps_total == 0:
        drc_status = "not_ready"
        drc_note = "板上一块元件都没有，DRC 通过不算数（空板当然没有违规）"
    report.drc = {
        "status": drc_status,
        "pass": all(r["pass"] for r in drc_records) if (drc_records and comps_total) else False,
        "checked": len(drc_records) if comps_total else 0,
        "boards": drc_records,
        "note": drc_note,
    }
    report.parity = parity_from_netlists(design, netlists,
                                         exclude_refs=_failed_refs(report))

    if not live_boards:
        job.status = "not_ready"
        job.note = "没有可用的 PCB 文档"
        return
    if moved_total == 0 and comps_total:
        job.status = "failed"
        job.note = "一个元件都没摆进板内（位号对不上或 modify 全失败）"
        return
    job.status = "ok"
    job.note = (f"{comps_total} 个元件在 PCB 上，{moved_total} 个按设计坐标摆放；"
                f"板框 {outline_ready}/{len(live_boards)} 块建成")


# ---------------------------------------------------------------------------
# 段 4：布线
# ---------------------------------------------------------------------------

_JS_ROUTE = r"""
const P = __CTX__;
const steps = [];
async function attempt(name, fn) {
  try { const v = await fn(); steps.push({step: name, ok: true}); return v; }
  catch (e) { steps.push({step: name, ok: false, error: String((e && e.message) || e)}); return null; }
}
await attempt('openPcb', () => eda.dmt_EditorControl.openDocument(P.pcbUuid));
await new Promise(r => setTimeout(r, P.settleMs));
const t0 = Date.now();
let result = null;
let error = '';
try {
  // 零参数 = 「对所有未布线的网络自动布线」（官方 remarks 原话）。
  result = await eda.pcb_Document.autoRouting({});
} catch (e) { error = String((e && e.message) || e); }
return {ok: !error, ms: Date.now() - t0, result: result, error: error, steps: steps};
"""

#: 布完**自己去量**。`IPCB_AutoRoutingResult` 不可信 —— 实测同一块板
#: `successNetsCount: 0 / failedNets: [全部]`，而盘上明明有 9 段走线。
#: 手册 §21/§22 的立场是「工具不可信时自己写独立判定器」，这里只做最小版：
#: 逐网络数走线 + 数过孔 + 跑一次 DRC（分组只作证据，不作唯一判据）。
_JS_ROUTE_MEASURE = r"""
const out = {ok: true};
try {
  const ids = (await eda.pcb_PrimitivePolyline.getAllPrimitiveId()) || [];
  const pls = (await eda.pcb_PrimitivePolyline.get(ids)) || [];
  out.outlines = pls.length;
} catch (e) { out.outlinesErr = String((e && e.message) || e); }
try {
  out.tracksPerNet = {};
  out.tracks = 0;
  for (const n of ((await eda.pcb_Net.getAllNetName()) || [])) {
    let c = 0;
    try { c = ((await eda.pcb_PrimitiveLine.getAll(n)) || []).length; } catch (e) {}
    out.tracksPerNet[n] = c;
    out.tracks += c;
  }
} catch (e) { out.tracksErr = String((e && e.message) || e); }
try { out.vias = ((await eda.pcb_PrimitiveVia.getAll()) || []).length; }
catch (e) { out.viasErr = String((e && e.message) || e); }
try { out.components = ((await eda.pcb_PrimitiveComponent.getAllPrimitiveId()) || []).length; }
catch (e) {}
try { out.save = await eda.pcb_Document.save(); }
catch (e) { out.save = null; out.saveErr = String((e && e.message) || e); }
try { out.drc = await eda.pcb_Drc.check(true, false, true); }
catch (e) { out.drc = null; out.drcErr = String((e && e.message) || e); }
return out;
"""


def _expected_nets(design: DesignModel) -> list[str]:
    """设计的**真实布线目标**：≥2 个焊盘的网络。

    单个焊盘的网络没有"连通"可言，不该进分母（否则 1 脚网络会让分母虚高）。
    """
    out: list[str] = []
    for entry in design.nets:
        name = str(entry.get("net") or "")
        pins = [p for p in (entry.get("pins") or []) if p.get("ref") != "board"]
        if name and len(pins) >= 2:
            out.append(name)
    return out


def _run_route(session: lceda_mod.LcedaSession, design: DesignModel,
               report: EmitReport, *, route: str) -> None:
    """布线。四档：``none`` / ``native`` / ``gui`` / ``ours``。

    ## `native` 现在是**可用**的（2026-11-21 更正）

    之前这里写着「引擎拒绝执行」，后来又改写成「headless 没有布线的执行环境」。
    **两条都是错的**，真因是我自己的板框 bug：

        _outline_source 里 R 模式写成 ['R', x0, y0, w, h]，
        而 R 模式的 (x, y) 是**左上角**、矩形向 −Y 延伸（手册 §19）⇒
        板框落在 y∈[-h, 0]，元件在 y>0 —— **整块板在板框外**。

    只把那个 y 从 `y0` 改成 `y0 + h`，同一块板立刻布通：

    | 量 | 修前 | 修后 |
    |---|---|---|
    | `successNetsCount` | 0 / 2 | **2 / 2**（failedNets 空） |
    | 盘上走线 | 0 | **9 段**（GND 5 + MID 4） |
    | 过孔 | 0 | 0（引擎自己选了单层 45°） |
    | DRC | — | `[]`（零违规） |

    关掉会话重开后再读，元件 3 个、板框 `['R',0,1574.8,...,1574.8]`、
    走线 9 段全在盘上 —— 所以这条不是"内存里假通过"。

    ## 判据不看 `successNetsCount`

    实测同一个工程：修好板框后第一次调用返回 `successNetsCount: 2`，
    紧接着再调一次（网络已布完）返回 `0 / 2`，而盘上 9 段走线一段没少。
    换句话说这个字段描述的是**这一次调用做了什么**，不是**板子通不通**。

    所以判定改量几何：**每个 ≥2 焊盘的网络都要有走线**，再看 DRC。
    """
    job = report.job("route")
    if route not in ROUTES:
        job.status = "failed"
        job.note = f"未知 route={route!r}；可用 {ROUTES}"
        return
    if route == "none":
        job.status = "ok"
        job.note = "只放置不布线（route=none）"
        job.counts = {"tracks": 0}
        return
    if route == "ours":
        job.status = "not_ready"
        job.note = ("route=ours 需要自研路由器（未做）；"
                    "立创自己的引擎现在能用了，优先 --route=native")
        return
    if route == "gui":
        # 原来自动布线不通时这条路是"唯一能出线"的；现在 native 已能跑通，
        # gui 退化成「让人的眼睛先看一遍」的选项，措辞必须跟着改。
        live = report.project.get("boards") or []
        if not live:
            job.status = "not_ready"
            job.note = "没有 PCB 可布线"
            return
        session.invoke("await eda.pcb_Document.save(); return {ok:true};",
                       require_ok=False, timeout_ms=60_000, label="save")
        job.status = "not_ready"
        job.counts = {"router": "gui", "boards": len(live)}
        job.note = ("工程已建好并保存，布线留给人点一次：「布线 → 自动布线」。"
                    "engine 在 CLI 会话里也能用（实测 route=native 已布通），"
                    "选 gui 只是要人在场确认。")
        return

    # native
    live = report.project.get("boards") or []
    if not live:
        job.status = "not_ready"
        job.note = "没有 PCB 可布线"
        return
    expected = _expected_nets(design)
    if not expected:
        job.status = "not_ready"
        job.note = ("设计里没有 ≥2 个焊盘的网络 —— 没有可布的对象，"
                    "「0 条违规」在这种输入上不算通过")
        job.counts = {"expected_nets": 0}
        return

    routed: dict[str, int] = {}
    tracks = vias = 0
    violations = 0
    groups: list[str] = []
    saved: Any = None
    for board in live:
        ctx = {"pcbUuid": board.get("pcbUuid") or "", "settleMs": 900}
        value = session.invoke(
            _JS_ROUTE.replace("__CTX__", json.dumps(ctx, ensure_ascii=False)),
            require_ok=False, timeout_ms=900_000, label="route")
        if not value.get("ok"):
            job.status = "not_ready"
            job.note = f"自动布线调用失败：{value.get('error')}"
            job.counts = {"router": "error"}
            return
        measured = session.invoke(_JS_ROUTE_MEASURE, require_ok=False,
                                  timeout_ms=180_000, label="routeMeasure")
        if not measured.get("ok"):
            job.status = "not_ready"
            job.note = f"布完读不回几何：{measured.get('error')}"
            return
        for name, count in (measured.get("tracksPerNet") or {}).items():
            routed[str(name)] = routed.get(str(name), 0) + int(count or 0)
        tracks += int(measured.get("tracks") or 0)
        vias += int(measured.get("vias") or 0)
        violations += _count_violations(measured.get("drc"))
        groups = _drc_groups(measured.get("drc")) or groups
        saved = measured.get("save")

    missing = [n for n in expected if routed.get(n, 0) <= 0]
    job.counts = {"tracks": tracks, "vias": vias, "expected_nets": len(expected),
                  "routed_nets": len(expected) - len(missing),
                  "missing": missing[:8], "drc_violations": violations,
                  "save": bool(saved)}
    if missing:
        job.status = "not_ready"
        job.note = (f"布通 {len(expected) - len(missing)}/{len(expected)} 条网，"
                    f"未布：{missing[:6]}（走线 {tracks} 段 / 过孔 {vias}）。"
                    "先看板框是否闭合、元件是否都在板框内 —— "
                    "板框坐标错（R 模式原点在左上角）会让布线 0 段而**不报错**")
        return
    if violations:
        job.status = "not_ready"
        job.note = (f"{len(expected)} 条网都已布通（{tracks} 段 / {vias} 过孔），"
                    f"但 DRC 报 {violations} 条违规：{groups[:6]}")
        job.warnings.append(
            "DRC 的 Connection Error 不可信：实测它不认 API 创建的过孔构成的"
            "跨层连接（手册 §18.1）。Clearance Error 才是可信的几何判据。")
        return
    job.status = "ok"
    job.note = (f"{len(expected)} 条网全部布通（{tracks} 段走线 / {vias} 过孔），"
                f"DRC 零违规")
    if vias == 0:
        job.warnings.append("零过孔：引擎自己选了单层 45° 走线")



# ---------------------------------------------------------------------------
# 段 5：产出文件
# ---------------------------------------------------------------------------

_JS_OUTPUTS = r"""
const P = __CTX__;
const steps = [];
const files = [];
async function attempt(name, fn) {
  try { const v = await fn(); steps.push({step: name, ok: true}); return v; }
  catch (e) { steps.push({step: name, ok: false, error: String((e && e.message) || e)}); return null; }
}

await attempt('openPcb', () => eda.dmt_EditorControl.openDocument(P.pcbUuid));
await new Promise(r => setTimeout(r, P.settleMs));

const wanted = [
  ['gerber', () => eda.pcb_ManufactureData.getGerberFile(P.basename + '_gerber')],
  ['bom', () => eda.pcb_ManufactureData.getBomFile(P.basename + '_bom', 'xlsx')],
  ['pickplace', () => eda.pcb_ManufactureData.getPickAndPlaceFile(P.basename + '_pnp', 'xlsx', 'mm')],
];
for (const [kind, make] of wanted) {
  const file = await attempt(kind, make);
  if (!file) continue;
  const record = {kind: kind, name: file.name || '', size: file.size || 0, saved: false};
  if (P.saveDir) {
    try {
      const ok = await eda.sys_FileSystem.saveFileToFileSystem(P.saveDir, file, record.name + P.ext[kind]);
      record.saved = !!ok;
      if (!ok) record.saveError = 'saveFileToFileSystem 返回 false';
    } catch (e) {
      record.saveError = String((e && e.message) || e);
    }
  }
  files.push(record);
}
return {ok: true, steps: steps, files: files};
"""

_EXT = {"gerber": ".zip", "bom": ".xlsx", "pickplace": ".xlsx", "step": ".step"}


def _run_outputs(session: lceda_mod.LcedaSession, design: DesignModel,
                 report: EmitReport, *, save_dir: str = "") -> None:
    """导出 Gerber / BOM / 坐标文件。

    生成是好的；**落盘默认被拒**（要人去设置里开外部交互权限，见
    `lceda` 模块的 F7）。所以这里区分「生成成功」与「落盘成功」，
    落盘失败是 `not_ready` 而不是 `failed`：产物确实生成了。
    """
    job = report.job("outputs")
    live = report.project.get("boards") or []
    if not live:
        job.status = "not_ready"
        job.note = "没有 PCB 可导出"
        return
    total_files = 0
    saved = 0
    blocked_reason = ""
    for board in live:
        base = _sanitize_project_name(f"{report.project.get('name') or 'hwv'}-{board.get('pcbName') or 'pcb'}")
        ctx = {
            "pcbUuid": board.get("pcbUuid") or "",
            "basename": base,
            "saveDir": save_dir,
            "ext": _EXT,
            "settleMs": 800,
        }
        script = _JS_OUTPUTS.replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
        try:
            value = session.invoke(script, expect=("files",), timeout_ms=300_000,
                                   label="outputs")
        except lceda_mod.LcedaError as exc:
            job.status = "failed"
            job.note = f"导出失败：{exc}"
            return
        for record in value.get("files") or []:
            total_files += 1
            if record.get("saved"):
                saved += 1
                job.artifacts.append({
                    "kind": record.get("kind"),
                    "name": record.get("name"),
                    "size": record.get("size"),
                    "saved": True,
                })
            else:
                err = str(record.get("saveError") or "")
                if err and not blocked_reason:
                    blocked_reason = err
                job.artifacts.append({
                    "kind": record.get("kind"),
                    "name": record.get("name"),
                    "size": record.get("size"),
                    "saved": False,
                    "why": err[:200] if err else "未给 saveDir",
                })
    job.counts = {"generated": total_files, "saved": saved}
    if total_files == 0:
        job.status = "failed"
        job.note = "一个产物都没生成"
        return
    if saved == total_files:
        job.status = "ok"
        job.note = f"{saved} 个产物已落盘"
        return
    job.status = "not_ready"
    if not save_dir:
        job.note = (f"{total_files} 个产物在内存里生成成功，但没有给 save_dir，"
                    "所以只报了名称与大小")
    else:
        job.note = (f"{total_files} 个产物生成成功，{saved} 个落盘；"
                    f"落盘被拒：{blocked_reason[:200]}")
        job.warnings.append(
            "立创默认禁止脚本写盘（「未启用扩展和独立脚本的外部交互权限」）。"
            "在立创设置里打开该权限后重跑即可落盘；不是发射失败。")


# ---------------------------------------------------------------------------
# 回读与 parity
# ---------------------------------------------------------------------------


def _drc_groups(detail: Any) -> list[str]:
    """把 DRC 分组树拍平成「类别 / 规则名」字符串。"""
    out: list[str] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        name = node.get("name")
        if isinstance(name, str) and name:
            out.append(name)
        if "list" in node:
            walk(node.get("list"), depth + 1)

    walk(detail)
    return out[:20]


def _count_violations(detail: Any) -> int:
    """数叶子（有 `explanation` 的节点就是一条违规）。"""
    count = 0

    def walk(node: Any, depth: int = 0) -> None:
        nonlocal count
        if depth > 6:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        if "explanation" in node:
            count += 1
            return
        if "list" in node:
            walk(node.get("list"), depth + 1)

    walk(detail)
    return count


def _netlist_pins(netlist_text: str) -> dict[tuple[str, str], str]:
    """JLCEDA 网表 → ``{(ref, pin): net}``。

    结构（实测）：``components[uid].pinInfoMap[pin].net``，
    而 ``components[uid].props.Designator`` 是位号。
    """
    try:
        parsed = json.loads(netlist_text)
    except (TypeError, ValueError):
        return {}
    out: dict[tuple[str, str], str] = {}
    components = parsed.get("components") or {}
    if not isinstance(components, dict):
        return out
    for entry in components.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") or {}
        ref = str(props.get("Designator") or "").strip()
        if not ref:
            continue
        for pin_number, info in (entry.get("pinInfoMap") or {}).items():
            if not isinstance(info, dict):
                continue
            net = str(info.get("net") or "").strip()
            if net:
                out[(ref, str(pin_number))] = net
    return out


def _failed_refs(report: EmitReport) -> set[str]:
    """**没装进**原理图的位号（parity 要排除它们）。

    名字按「排除集」理解：装不上去的器件不该被算成设计错。
    """
    refs = {str(item.get("ref")) for item in report.unresolved
            if item.get("reason") in ("no_symbol", "add_failed")}
    return refs


def parity_from_netlists(design: DesignModel, netlists: Iterable[str],
                         *, exclude_refs: set[str] | None = None) -> dict[str, Any]:
    """设计的网表 vs 立创回读的网表，逐引脚比较。

    * 只在**双方都有**的位号上比较（装不上去的器件不该算设计错）；
    * 设计里 ``board`` 这种伪元件跳过（它不是器件）；
    * 结果分三类：缺的、多的、网名不一致的。
    """
    excluded = set(exclude_refs or set())
    design_pins: dict[tuple[str, str], str] = {}
    design_refs: set[str] = set()
    for entry in design.nets:
        net = str(entry.get("net") or "").strip()
        if not net:
            continue
        for pin in entry.get("pins") or []:
            ref = str(pin.get("ref") or "").strip()
            if not ref or ref == "board":
                continue
            design_refs.add(ref)
            if ref in excluded:
                continue
            design_pins[(ref, str(pin.get("pin")))] = net

    live_pins: dict[tuple[str, str], str] = {}
    parsed_any = False
    for text in netlists:
        if not text:
            continue
        parsed = _netlist_pins(text)
        if parsed:
            parsed_any = True
        live_pins.update(parsed)

    if not parsed_any:
        return {"status": "not_ready", "checked": 0,
                "note": "没有拿到立创 PCB 网表（回读失败），无法比较"}

    comparable = {k: v for k, v in live_pins.items() if k[0] in design_refs}
    missing = [{"ref": r, "pin": p, "expected": n}
               for (r, p), n in sorted(design_pins.items())
               if (r, p) not in comparable]
    wrong = [{"ref": r, "pin": p, "design": n, "lceda": comparable[(r, p)]}
             for (r, p), n in sorted(design_pins.items())
             if (r, p) in comparable and comparable[(r, p)] != n]
    extra = [{"ref": r, "pin": p, "lceda": n}
             for (r, p), n in sorted(comparable.items())
             if (r, p) not in design_pins]
    checked = len(design_pins)
    ok = not missing and not wrong and not extra
    return {
        "status": "ok" if ok and checked else "not_ready",
        "checked": checked,
        "lceda_pins": len(comparable),
        "missing": missing[:40],
        "wrong": wrong[:40],
        "extra": extra[:40],
        "counts": {"missing": len(missing), "wrong": len(wrong), "extra": len(extra)},
        "note": "" if ok else (
            "回读的网表与 design.json 不一致——两个系统已分叉，"
            "以设计为准，先查装配（unresolved）再看布线"),
        "not_compared_refs": sorted(excluded)[:40],
    }


def read_back(session: lceda_mod.LcedaSession, design: DesignModel,
              *, boards: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """从立创读回 PCB 网表 + DRC，供 parity 与 handoff 用。

    独立于发射流程：已经落盘的工程也能单独回读（审查的输入）。
    """
    live = boards
    if live is None:
        current = session.current_project()
        live = [b for b in (current.get("boards") or []) if b.get("pcbUuid")]
    netlists: list[str] = []
    drc: list[dict[str, Any]] = []
    for board in live:
        script = (_JS_READBACK
                  .replace("__PCB__", json.dumps(board.get("pcbUuid") or "")))
        try:
            value = session.invoke(script, expect=("netlist",), label="read_back")
        except lceda_mod.LcedaError:
            continue
        if value.get("netlist"):
            netlists.append(str(value.get("netlist")))
        drc.append({
            "board": board.get("name"),
            "pass": bool(value.get("drcPass")),
            "violations": _count_violations(value.get("drcDetail")),
            "groups": _drc_groups(value.get("drcDetail")),
            "components": int(value.get("componentCount") or 0),
        })
    # 空板的 DRC 通过同样不算数（与 `_run_pcb` 同一条纪律）。
    usable = [r for r in drc if r.get("components")]
    drc_status = "ok" if usable else "not_ready"
    drc_note = "" if usable else "板上一块元件都没有，DRC 通过不算数（空板当然没有违规）"
    return {
        "parity": parity_from_netlists(design, netlists),
        "drc": {"status": drc_status,
                "pass": all(r["pass"] for r in usable) if usable else False,
                "checked": len(usable), "boards": drc,
                "note": drc_note},
    }


_JS_READBACK = r"""
const pcbUuid = __PCB__;
const opened = await eda.dmt_EditorControl.openDocument(pcbUuid);
if (!opened) throw new Error('openDocument 返回空');
await new Promise(r => setTimeout(r, 1200));
const netlist = await eda.pcb_Net.getNetlist('JLCEDA');
const onBoard = ((await eda.pcb_PrimitiveComponent.getAll()) || []).length;
const drcPass = await eda.pcb_Drc.check(true, false, false);
const drcDetail = await eda.pcb_Drc.check(true, false, true);
return {ok: true, netlist: netlist, drcPass: drcPass, drcDetail: drcDetail,
        componentCount: onBoard};
"""


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def emit(
    design: DesignModel,
    *,
    project_name: str = "",
    jobs: Iterable[str] = JOBS,
    route: str = "native",
    session: lceda_mod.LcedaSession | None = None,
    reuse: bool = False,
    save_dir: str = "",
    max_parts: int = MAX_PARTS,
) -> EmitReport:
    """发射主入口。

    ``jobs`` 选段（默认全跑）；``route`` 选布线三条路之一。
    任何一步出问题都**落成 status**，不往外抛异常——调用方（CLI/工具）
    只需要读报告。

    **重复发射是新建工程，不是覆盖。** 立创不允许重名工程，而覆盖一个
    已有工程意味着「删掉旧器件再装」——那条路要处理残留位号、残留导线、
    残留网络标签，风险远大于收益。每次发射产出一个带序号的新工程，
    本身就是一次可对照的构建产物。
    ``reuse=True`` 时改为接管磁盘上已有的同名工程（**会重复装器件**，
    只适合「只跑 outputs / pcb 段、不再重装原理图」的场合）。
    """
    started = time.time()
    report = EmitReport(version=lceda_mod.version_of(session.exe) if session and session.exe else "")
    report.project["name"] = project_name or _design_project_name(design)

    # -- 闸门 1：设计不合法就不发射（出口：改 design.json 后重跑）--------
    problems = design.validate()
    if problems:
        job = report.job("project")
        job.status = "failed"
        job.note = "design.json 校验失败，未发射"
        report.notes.extend(problems[:20])
        report.recompute_status()
        return report

    wanted = [j for j in jobs if j in JOBS]
    if not wanted:
        report.job("project").note = f"没有有效段；可用 {JOBS}"
        report.recompute_status()
        return report
    # 提前建好结果槽位：没跑到的那一段才能如实留在 not_ready，
    # 而不是「报告里根本没有这一项」（AGENTS §0.2 空表问题）。
    for name in wanted:
        report.job(name)

    # -- 闸门 2：没有 lceda → not_ready，各段留空（不抛异常）-------------
    own_session = session is None
    if session is None:
        session = lceda_mod.LcedaSession(exe=lceda_mod.find_exe())
    if not session.exe or not Path(session.exe).exists():
        for name in wanted:
            job = report.job(name)
            job.status = "not_ready"
            job.note = "没有 lceda-pro 可执行文件"
        report.notes.append(
            f"设环境变量 {lceda_mod.EXE_ENV} 指向 lceda-pro.exe，或安装立创EDA专业版")
        report.recompute_status()
        return report

    try:
        if own_session or not session.session_id:
            session.open(path=session.project_path or "", headless=True)
        report.version = session.version or lceda_mod.version_of(session.exe)

        # -- 决定工程名与落盘路径 -------------------------------------------
        # 名字冲突靠「试着建，失败就加序号」解决（`_JS_PROJECT_NEW`），
        # 不依赖文件系统 API —— 它被外部交互权限挡着。
        base = _sanitize_project_name(report.project["name"])
        root = _projects_root(session)
        report.project["root"] = root
        reopened = False
        if reuse and root:
            guess = Path(root) / base / f"{base}.eprj3"
            if guess.exists():
                session.close()
                session.open(path=str(guess), headless=True)
                reopened = True
        report.project["name"] = base

        if "project" in wanted:
            try:
                _run_project(session, design, report, name=base,
                             reuse=reopened)
            except lceda_mod.LcedaError as exc:
                job = report.job("project")
                job.status = "failed"
                job.note = str(exc)
                report.recompute_status()
                return report
            if report.job("project").status != "ok":
                report.recompute_status()
                return report

        report.project["path"] = _project_path(report.project.get("name") or "",
                                               report.project.get("root") or "")
        report.stackup = stackup_spec.derive(design).to_dict()
        if "schematic" in wanted:
            _run_schematic(session, design, report, max_parts=max_parts)
        if "pcb" in wanted:
            _run_pcb(session, design, report)
        if "route" in wanted:
            _run_route(session, design, report, route=route)
        if "outputs" in wanted:
            _run_outputs(session, design, report, save_dir=save_dir)
    except lceda_mod.LcedaError as exc:
        report.notes.append(f"会话层面失败：{exc}")
        for name in wanted:
            job = report.job(name)
            if job.status == "not_ready" and not job.note:
                job.status = "failed"
                job.note = str(exc)
    finally:
        if own_session:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - 关会话失败不该盖住结论
                pass

    if not report.project.get("path") and session.project_path:
        report.project["path"] = session.project_path
    report.project["elapsed_s"] = round(time.time() - started, 1)
    report.recompute_status()
    return report


#: 工程根目录的环境变量（`getProjectsPaths` 被权限拦时的替代）。
PROJECTS_ENV = "HWV_LCEDA_PROJECTS"


def _candidate_roots() -> list[Path]:
    """可能的工程根目录。**实测**：默认在 `~/Documents/LCEDA-Pro/projects`。"""
    roots: list[Path] = []
    env = os.environ.get(PROJECTS_ENV, "").strip()
    if env:
        roots.append(Path(env))
    home = Path.home()
    roots.extend([
        home / "Documents" / "LCEDA-Pro" / "projects",
        home / "LCEDA-Pro" / "projects",
        home / "Documents" / "LCEDA" / "projects",
    ])
    return roots


def _projects_root(session: lceda_mod.LcedaSession) -> str:
    """工程根目录：环境变量 → API（常被权限拦）→ 已知路径里**存在**的那个。"""
    env = os.environ.get(PROJECTS_ENV, "").strip()
    if env:
        return env
    try:
        api_root = session.projects_root()
    except Exception:  # noqa: BLE001 - 权限错误不该影响发射
        api_root = ""
    if api_root:
        return api_root
    for candidate in _candidate_roots():
        if candidate.exists():
            return str(candidate)
    return ""


def _project_path(name: str, root: str) -> str:
    """`.eprj3` 的绝对路径（工程是**文件夹格式**，里面是 JSON，能进 git）。"""
    if not name or not root:
        return ""
    folder = Path(root) / name
    eprj3 = folder / f"{name}.eprj3"
    if eprj3.exists():
        return str(eprj3)
    return str(folder) if folder.exists() else ""


def _design_project_name(design: DesignModel) -> str:
    raw = str(getattr(design, "title", "") or getattr(design, "name", "") or "")
    if raw:
        return raw
    board = getattr(design, "board", None)
    return str(getattr(board, "name", "") or "hwv-design")


def describe() -> dict[str, Any]:
    """自省：CLI / 工具用来告诉模型「这层能干什么」。"""
    return {
        "jobs": list(JOBS),
        "routes": list(ROUTES),
        "default_route": "native",
        "units": {"design": "mm", "lceda_pcb": "mil",
                  "mm_to_mil": round(lceda_mod.MM_TO_MIL, 6)},
        "grid": {"cols": GRID_COLS, "dx": GRID_DX, "dy": GRID_DY},
        "max_parts": MAX_PARTS,
        "stub_len": STUB_LEN,
        "measured_limits": [
            "route=native：本机自动布线引擎拒绝执行（headless 与可见窗口相同）",
            "outputs 落盘：默认被拒（未启用扩展和独立脚本的外部交互权限）",
            "原理图网表读回来是陈旧的：parity 以 PCB 网表为准",
        ],
        "coordinate_caveat": (
            "立创 PCB 的 Y 轴方向未做对照实验：非对称板框的上下镜像可能反了。"
            "矩形板框不受影响。"
        ),
    }