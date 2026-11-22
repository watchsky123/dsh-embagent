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

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import lceda as lceda_mod
from . import dfm as dfm_mod
from . import power as power_mod
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
JOBS = ("project", "layout", "schematic", "pcb", "route", "pour",
        "verify", "outputs")

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
    #: 布线段的**逐板明细**（引擎自述、几何连通性判定、落笔统计）。
    #: 单列字段而不是塞进 `notes`，是因为验收脚本要按 key 读它。
    route_detail: dict[str, Any] = field(default_factory=dict)
    #: 原理图段的逐板明细（每块板装了几个件、几批）。
    schematic_detail: dict[str, Any] = field(default_factory=dict)
    #: PCB 段的逐板明细（板框、摆放批数、保存真值）。
    pcb_detail: dict[str, Any] = field(default_factory=dict)
    #: **最终连通性判定**（含铺铜）。这是"能不能打板"的结论所在。
    verify_detail: dict[str, Any] = field(default_factory=dict)
    #: 布局规则的检查与（可选）自动重排。
    layout_detail: dict[str, Any] = field(default_factory=dict)
    #: 这次用的是哪条通道（cli / bridge，以及是否从 bridge 降级）。
    channel: dict[str, Any] = field(default_factory=dict)
    #: **制造可行性结论**（DRC 分级 + DFM + 自建几何）。P45。
    fabrication: dict[str, Any] = field(default_factory=dict)

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
        if self.fabrication:
            payload["fabrication"] = dict(self.fabrication)
        if self.stackup:
            payload["stackup"] = dict(self.stackup)
        if self.notes:
            payload["notes"] = list(self.notes)
        if self.route_detail:
            payload["route_detail"] = dict(self.route_detail)
        if self.schematic_detail:
            payload["schematic_detail"] = dict(self.schematic_detail)
        if self.pcb_detail:
            payload["pcb_detail"] = dict(self.pcb_detail)
        if self.verify_detail:
            payload["verify_detail"] = dict(self.verify_detail)
        if self.layout_detail:
            payload["layout_detail"] = dict(self.layout_detail)
        if self.channel:
            payload["channel"] = dict(self.channel)
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
const P = __CTX__;
const base = __NAME__;
// 立创不允许重名工程，而**文件名查不了**（getProjectsPaths 也被外部交互权限
// 拦着，实测报「未启用扩展和独立脚本的外部交互权限」）。所以只能试：
// createProject 重名时返回 undefined，加序号再试，最多 20 次。
//
// **还要容忍一种抓不到就崩的竞态**：会话刚开（`open` 返回后立刻调）时
// `createProject` 会抛
//     TypeError: Cannot read properties of undefined (reading 'username')
// 实测同一句调用在 `open` 之后等 5 秒就正常（用户状态是异步装载的）。
// 第一版没包 try/catch → 整个发射在第一步就 failed，看起来像"立创不让建工程"。
const userWait = [];
const userWaitTries = P.userWaitTries || 20;
for (let i = 0; i < userWaitTries; i++) {
  await new Promise(r => setTimeout(r, 300));
  let ready = false;
  try {
    const teams = await eda.dmt_Team.getAllTeamsInfo();
    ready = !!(teams && teams.length);
  } catch (e) { ready = false; }
  userWait.push(ready);
  if (ready) break;
}
let projectUuid = null;
let want = base;
let lastError = '';
const maxTries = P.maxTries || 24;
for (let i = 1; i <= maxTries && !projectUuid; i++) {
  want = (i <= 20) ? ((i === 1) ? base : (base + '-' + i)) : base;
  try {
    projectUuid = await eda.dmt_Project.createProject(
      want, undefined, undefined, undefined, 'hw_velxio emit');
  } catch (e) {
    lastError = String((e && e.message) || e);
    projectUuid = null;
    await new Promise(r => setTimeout(r, 800));
    continue;
  }
  if (!projectUuid) await new Promise(r => setTimeout(r, 200));
}
if (!projectUuid) {
  throw new Error('连试 24 次都建不了工程（最后错误：'
                  + (lastError || 'createProject 一直返回 undefined') + '）');
}
// **先把当前工程存住**：用户窗口里已经开着一个工程时，`openProject`
// 会因为有未保存改动而返回 false（实测在 Bridge 通道上就是这样：
// `openProject 连续两次返回 false`，前面几次能成是因为窗口里没开工程）。
try { await eda.pcb_Document.save(); } catch (e) {}
try { await eda.sch_Document.save(); } catch (e) {}
const opened = await eda.dmt_Project.openProject(projectUuid);
let openedBy = opened ? 'open' : '';
if (!opened) {
  // 实测偶发一次 false，重试一次即成功
  await new Promise(r => setTimeout(r, 800));
  const again = await eda.dmt_Project.openProject(projectUuid);
  if (again) openedBy = 'open-retry';
}
if (!openedBy) {
  // **也可能是 `createProject` 已经把它打开了**（拿到的是同一个 uuid）。
  // 这时再调 openProject 反而返回 false —— 不能因此报"打不开工程"。
  try {
    const cur = await eda.dmt_Project.getCurrentProjectInfo();
    if (cur && cur.uuid === projectUuid) openedBy = 'created-and-current';
  } catch (e) {}
}
if (!openedBy) throw new Error('openProject 连续两次返回 false，且当前工程不是它');
return {ok: true, projectUuid: projectUuid, created: true, name: want,
        openedBy: openedBy, userReady: userWait.some(Boolean)};
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
        ctx = {"userWaitTries": 20, "maxTries": 24}
        # Bridge 的单次 `/execute` 有 **30 秒硬上限**：24 次重试 + 6 秒用户态等待
        # 会撞上去（实测直接 HTTP 500，且看不出来是哪一步）。
        if (report.channel or {}).get("kind") == "bridge":
            ctx = {"userWaitTries": 6, "maxTries": 6}
        script = (_JS_PROJECT_NEW
                  .replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                  .replace("__NAME__", json.dumps(name, ensure_ascii=False)))
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
# 段 1.5：布局规则（不碰 EDA，纯几何）
# ---------------------------------------------------------------------------


def _run_layout(design: DesignModel, report: EmitReport, *,
                autolayout: bool = False) -> None:
    """按手册 §14.3/§17.19 的规则检查摆放，可选自动重排。

    **为什么放在装器件之前**：连接器放错边、无源件挨太近，等布完线再改就是
    全盘重来（手册 §14.3 第 5 条："在布局阶段消灭交叉，远比在布线阶段绕线划算"）。
    """
    from . import pcb_layout

    job = report.job("layout")
    before = pcb_layout.audit(design)
    applied: list[dict[str, Any]] = []
    if autolayout and before.blockers:
        for board in design.board_list():
            plan = pcb_layout.autoplace(design, board=board)
            for part in design.parts:
                if design.board_of(part) is not board:
                    continue
                pos = plan.get(str(part.ref))
                if pos is None or part.placement is None:
                    continue
                applied.append({"ref": str(part.ref), "board": str(
                    getattr(board, "name", "")),
                    "from": [round(float(part.placement.x_mm), 2),
                             round(float(part.placement.y_mm), 2)],
                    "to": list(pos)})
                part.placement.x_mm = pos[0]
                part.placement.y_mm = pos[1]
    after = pcb_layout.audit(design) if applied else before
    report.layout_detail = {
        "before": before.to_dict(),
        "after": after.to_dict(),
        "autolayout": bool(autolayout),
        "applied": applied[:40],
    }
    job.counts = {
        "checked_parts": before.checked_parts,
        "blockers_before": len(before.blockers),
        "blockers_after": len(after.blockers),
        "warnings": len(before.findings) - len(before.blockers),
        "autolayout": bool(autolayout),
        "moved": len(applied),
    }
    if before.checked_parts == 0:
        job.status = "not_ready"
        job.note = "没有可检查的元件（或全都没有 size_mm）—— 空输入不算通过"
        return
    if after.blockers:
        codes = sorted({f.code for f in after.blockers})
        job.status = "not_ready"
        job.note = (f"{len(after.blockers)} 处布局违规：{codes}；"
                    "例：" + "；".join(f"{f.refs}:{f.detail[:60]}"
                                     for f in after.blockers[:3]))
        job.warnings.append(
            "加 `--autolayout` 可以按封装占位重排一次（只保证不短路，"
            "不做电气分区/散热/装配顺序）")
        return
    job.status = "ok"
    job.note = (f"{before.checked_parts} 个元件通过布局规则"
                + (f"（自动重排 {len(applied)} 个）" if applied else ""))


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
__DOC_READY_SCH__

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
return {ok: true, placed: placed, unresolved: unresolved};
"""

#: 接线段。**和装器件分开**：器件 ≈2.5s/个、导线 ≈0.2s/条，
#: 30 秒硬上限下两者的批量完全不同（手册 §13.3：器件 6/批、导线 14/批）。
_JS_SCHEMATIC_WIRE = r"""
const P = __CTX__;
const items = __WIRES__;
const id = {projectId: P.projectId, schematicName: P.schematicName, pageName: P.pageName};
const opened = await eda.dmt_EditorControl.openDocument(P.pageUuid);
if (!opened) throw new Error('openDocument 返回空（pageUuid 对不对？）');
__DOC_READY_SCH__
// **再等一拍**：实测第一根 `wire.add` + `addNetLabel` 在 openDocument 之后
// 立刻发，**返回 ok 但不生效**（那一脚在网络表里仍是空 net）。
await new Promise(r => setTimeout(r, 600));

// 引脚坐标只能从 get 拿；按 designator 缓存，一个器件只查一次。
const pinsByRef = {};
async function pinsOf(designator) {
  if (pinsByRef[designator]) return pinsByRef[designator];
  const g = await eda.sch_SourcePrimitiveComponent.get({...id, designator: designator});
  if (!g || !g.ok) return null;
  pinsByRef[designator] = (g.value && g.value.pins) || [];
  return pinsByRef[designator];
}

const wired = [];
const failed = [];
for (const it of items) {
  const pins = await pinsOf(it.designator);
  if (!pins) { failed.push({net: it.net, ref: it.ref, pin: it.pin, why: 'get_failed'}); continue; }
  const hit = pins.find(x => String(x.pinNumber) === String(it.pin));
  if (!hit) { failed.push({net: it.net, ref: it.ref, pin: it.pin, why: 'pin_not_found'}); continue; }
  // 方向必须**背离器件锚点**：两个引脚都往右拉时，同一器件的两根平行短线
  // 会在 y 相同处重叠，`wire.add` 的「连通分量规范化」把它们并成一组，
  // 一个网络名吃掉另一个（实测 MID 整条被并成 GND，只剩 1 个网）。
  const dir = (hit.x >= it.anchorX) ? 1 : -1;
  const endX = hit.x + dir * P.stubLen;
  const endY = hit.y;
  const w = await eda.sch_SourcePrimitiveWire.add(
    {...id, line: [hit.x, hit.y, endX, endY], net: it.net});
  if (!w || !w.ok) { failed.push({net: it.net, ref: it.ref, pin: it.pin, why: 'wire_failed', error: (w && w.error) || null}); continue; }
  const l = await eda.sch_SourcePrimitiveAttribute.addNetLabel(
    {...id, net: it.net, x: endX, y: endY});
  if (!l || !l.ok) { failed.push({net: it.net, ref: it.ref, pin: it.pin, why: 'label_failed', error: (l && l.error) || null}); continue; }
  wired.push({net: it.net, ref: it.ref, pin: it.pin});
}
return {ok: true, wired: wired, failed: failed};
"""

#: 原理图文档的就绪判定：`getCurrentDocumentInfo().documentType === 1`（原理图页）。
#: 与 PCB 段同一个坑（AGENTS §3.22）：`openDocument` 返回 true 只代表"打开了"。
_DOC_READY_SCH = r"""
const docPoll = {tries: 0, docType: null};
for (let i = 0; i < 48; i++) {
  let d = null;
  try { d = await eda.dmt_SelectControl.getCurrentDocumentInfo(); } catch (e) { d = null; }
  docPoll.docType = d ? d.documentType : null;
  docPoll.tries = i + 1;
  if (docPoll.docType === 1) break;
  await new Promise(r => setTimeout(r, 250));
}
await new Promise(r => setTimeout(r, 300));
"""

#: 接线之后**必须保存原理图**，否则 `importChanges` 会静默漏器件。
#:
#: 实测（3 个器件 + 5 根线）：不保存直接导入 → PCB 上只有 **2 个**器件，
#: 而 `sch_SourcePrimitiveComponent.add` 全部返回 ok、`placed` 也报了 3 个。
#: 加了 `sch_Document.save()` 之后 → 3/3 落地。
#: 这类"看起来成功"的漏件只能靠 parity 抓到（missing=2）。
_JS_SCH_SAVE = r"""
const P = __CTX__;
await eda.dmt_EditorControl.openDocument(P.pageUuid);
__DOC_READY_SCH__
let saved = null;
let error = '';
try { saved = await eda.sch_Document.save(); }
catch (e) { error = String((e && e.message) || e); }
// save() 返回 true 之后编辑器还要一小会儿才把源码提交给"导入变更"。
// 不等这一下，后面 importChanges 会**偶发地少导一个器件**（实测 3 个里少 1 个，
// 而 placed 报了 3 个、save 也返回 true —— 只有 parity 的 missing 能抓到）。
await new Promise(r => setTimeout(r, P.settleMs || 1200));
return {ok: true, saved: saved, error: error, docType: docPoll.docType};
"""

#: 接线**写完回读**：`sch_Netlist.getNetlist('JLCEDA')` 给出每个引脚的真实网络。
#:
#: 为什么必须这一步：`sch_SourcePrimitiveWire.add` / `addNetLabel` 都返回 ok，
#: 但实测**第一次调用可能不生效**（R1 的 1 脚在网络表里仍是空 net）。
#: "返回 ok" 不等于"生效"，只有回读能证明 —— 这也是 parity 当初抓到的形态。
_JS_SCH_NETLIST = r"""
const P = __CTX__;
await eda.dmt_EditorControl.openDocument(P.pageUuid);
__DOC_READY_SCH__
let text = '';
let error = '';
try { text = await eda.sch_Netlist.getNetlist('JLCEDA'); }
catch (e) { error = String((e && e.message) || e); }
return {ok: true, text: text || '', error: error};
"""

#: 单批上限（手册 §13.3 的实测节奏：器件 ≈2.5s/个、导线 ≈0.2s/条；上限 30s）
SCH_PART_BATCH = 6
SCH_WIRE_BATCH = 12
PCB_MOVE_BATCH = 6


def _board_context(design: DesignModel, live_board: dict[str, Any],
                   report: EmitReport) -> dict[str, Any]:
    return {
        "projectId": report.project.get("uuid") or "",
        "schematicName": live_board.get("schematicName") or "",
        "pageName": live_board.get("pageName") or "",
        "pageUuid": live_board.get("pageUuid") or "",
        "pcbUuid": live_board.get("pcbUuid") or "",
        "settleMs": 800,
        "stubLen": STUB_LEN,
        "nets": list(design.nets),
    }


def _schematic_ctx(design: DesignModel, live_board: dict[str, Any],
                   report: EmitReport) -> dict[str, Any]:
    return _board_context(design, live_board, report)


def _run_schematic(session: lceda_mod.LcedaSession, design: DesignModel,
                   report: EmitReport, *, max_parts: int = MAX_PARTS) -> None:
    """逐板装器件、拉线、贴网络标签。**装器件与接线分两段、各自分批**。

    为什么必须分批（手册 §13.3 + 本机实测）：

    * 单次 `invoke` 有 **30 秒硬上限**，器件 ≈2.5s/个 → 超过 12 个就开始超时
    * 接线 ≈0.2s/条，但每条还要一次 `addNetLabel` → 一批 14 条就到顶
    * 整板一次装时，141 个元件的工程必然超时；而超时后 **EDA 侧可能已经做了一半**
      （手册 §17.7「超时 ≠ 失败」），所以分批 + 可重入 比"一次装完"更可靠

    分批的代价是每批复用一次 `openDocument`（约 1s）。用户允许长时间运行，
    这笔开销换"大板不会整段失败"是值得的。
    """
    job = report.job("schematic")
    design_boards = design.board_list()
    live_boards = report.project.get("boards") or []
    total_boards = len(design_boards)

    placed_total = 0
    wired_total = 0
    resolve_how: dict[str, int] = {}
    failed: list[str] = []
    per_board: list[dict[str, Any]] = []

    for index, live in enumerate(live_boards):
        board = design_boards[index] if index < len(design_boards) else None
        if board is None:
            continue
        board_row: dict[str, Any] = {"board": live.get("name"), "placed": 0,
                                     "wired": 0, "batches": 0,
                                     "unresolved": 0}
        per_board.append(board_row)
        page_uuid = str(live.get("pageUuid") or "")
        if not page_uuid:
            job.warnings.append(
                f"{live.get('name')}：没有原理图页（pageUuid 空）—— "
                "立创新建的板子可能只有 PCB、没建原理图页")
            board_row["note"] = "no_page"
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
                "_anchor": x,
            })
        ctx = _schematic_ctx(design, live, report)
        designator_of: dict[str, str] = {}
        anchor_of: dict[str, float] = {}
        for start in range(0, len(payload), SCH_PART_BATCH):
            chunk = payload[start:start + SCH_PART_BATCH]
            board_row["batches"] += 1
            script = (_JS_SCHEMATIC
                      .replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                      .replace("__PARTS__", json.dumps(
                          [{k: v for k, v in p.items() if k != "_anchor"}
                           for p in chunk], ensure_ascii=False))
                      .replace("__DOC_READY_SCH__", _DOC_READY_SCH))
            try:
                value = session.invoke(script, expect=("placed", "unresolved"),
                                       timeout_ms=600_000,
                                       label=f"schematic/{live.get('name')}/{start}")
            except lceda_mod.LcedaError as exc:
                failed.append(f"{live.get('name')} 第 {start // SCH_PART_BATCH + 1} 批：{exc}")
                continue
            for item in value.get("placed") or []:
                ref = str(item.get("ref"))
                designator_of[ref] = str(item.get("designator") or ref)
                anchor_of[ref] = float(next((p["_anchor"] for p in chunk
                                             if p["ref"] == ref), 0.0))
                how = str(item.get("how") or "?")
                resolve_how[how] = resolve_how.get(how, 0) + 1
            placed = value.get("placed") or []
            placed_total += len(placed)
            board_row["placed"] += len(placed)
            report.placed_refs.extend(str(x.get("ref")) for x in placed)
            for item in value.get("unresolved") or []:
                report.unresolved.append({**item, "board": live.get("name")})
                board_row["unresolved"] = int(board_row["unresolved"]) + 1

        # -- 接线：只为**这块板已装上的**器件的引脚拉线 --
        wire_items: list[dict[str, Any]] = []
        board_refs = {p.ref for p in parts}
        for entry in design.nets:
            net = str(entry.get("net") or "")
            for pin in (entry.get("pins") or []):
                ref = str(pin.get("ref") or "")
                if ref not in designator_of or ref not in board_refs:
                    continue
                try:
                    part = design.part(ref)
                except Exception:  # noqa: BLE001
                    continue
                if design.board_of(part) is not board:
                    continue
                wire_items.append({"net": net, "ref": ref,
                                   "designator": designator_of[ref],
                                   "pin": str(pin.get("pin") or ""),
                                   "anchorX": anchor_of.get(ref, 0.0)})
        for start in range(0, len(wire_items), SCH_WIRE_BATCH):
            chunk = wire_items[start:start + SCH_WIRE_BATCH]
            script = (_JS_SCHEMATIC_WIRE
                      .replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                      .replace("__WIRES__", json.dumps(chunk, ensure_ascii=False))
                      .replace("__DOC_READY_SCH__", _DOC_READY_SCH))
            try:
                value = session.invoke(script, expect=("wired",),
                                       timeout_ms=600_000,
                                       label=f"wire/{live.get('name')}/{start}")
            except lceda_mod.LcedaError as exc:
                failed.append(f"{live.get('name')} 接线第 {start // SCH_WIRE_BATCH + 1} 批：{exc}")
                continue
            wired = value.get("wired") or []
            wired_total += len(wired)
            board_row["wired"] = int(board_row["wired"]) + len(wired)
            for item in value.get("failed") or []:
                report.unresolved.append({
                    "ref": item.get("ref"), "pin": item.get("pin"),
                    "net": item.get("net"), "reason": f"wire:{item.get('why')}",
                    "board": live.get("name"),
                })
        # **保存原理图**（不保存 → 导入 PCB 会静默漏件，见 `_JS_SCH_SAVE` 注释）
        save_value = session.invoke(
            _JS_SCH_SAVE.replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                        .replace("__DOC_READY_SCH__", _DOC_READY_SCH),
            require_ok=False, timeout_ms=240_000,
            label=f"schematic/{live.get('name')}/save")
        save_value = save_value or {}
        board_row["saved"] = save_value.get("saved")
        if save_value.get("saved") is not True:
            job.warnings.append(
                f"{live.get('name')}：`sch_Document.save()` 没返回 true"
                f"（{save_value.get('error') or save_value.get('saved')}）—— "
                "导入 PCB 时可能漏器件")
        # -- 写完回读：哪些引脚在网络表里仍没有网？ --
        def read_pin_nets() -> dict[tuple[str, str], str]:
            got = session.invoke(
                _JS_SCH_NETLIST.replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                               .replace("__DOC_READY_SCH__", _DOC_READY_SCH),
                require_ok=False, timeout_ms=120_000,
                label=f"schematic/{live.get('name')}/netlist")
            return _netlist_pins(str((got or {}).get("text") or ""))

        expected_pins = {(it["ref"], it["pin"]): it["net"] for it in wire_items}
        actual = read_pin_nets()
        missing = {k: v for k, v in expected_pins.items()
                   if k in {tuple(x) for x in actual.keys()} and actual.get(k) != v}
        never = {k: v for k, v in expected_pins.items() if k not in actual}
        broken = {**never, **missing}
        if broken:
            # 重试一次（同样的 wire+label；实测第二次就生效）
            retry = [it for it in wire_items
                     if (it["ref"], it["pin"]) in broken]
            for start in range(0, len(retry), SCH_WIRE_BATCH):
                chunk = retry[start:start + SCH_WIRE_BATCH]
                session.invoke(
                    _JS_SCHEMATIC_WIRE
                    .replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                    .replace("__WIRES__", json.dumps(chunk, ensure_ascii=False))
                    .replace("__DOC_READY_SCH__", _DOC_READY_SCH),
                    require_ok=False, timeout_ms=600_000,
                    label=f"wire/{live.get('name')}/retry")
            actual = read_pin_nets()
            broken = {k: v for k, v in expected_pins.items()
                      if actual.get(k) != v}
        board_row["pin_nets_ok"] = len(expected_pins) - len(broken)
        board_row["pin_nets_bad"] = {f"{r}.{p}": n for (r, p), n in broken.items()}
        if broken:
            # **不吞**：原理图少一个网的引脚，后面 PCB/parity 一定会红。
            # 这里先记下来，并在本段就降级 —— 让报告第一时间说清楚。
            job.warnings.append(
                f"{live.get('name')}：{len(broken)} 个引脚的网络没写进原理图 → "
                f"{list(f'{r}.{p}={n}' for (r, p), n in list(broken.items())[:6])}"
                "（`addNetLabel` 返回过 ok，但网络表里没有；已重试一次）")
            job.status = "not_ready"
            if not job.note:
                job.note = (f"原理图有 {len(broken)} 个引脚没连上网络（重试后仍失败）")
        if len(parts) > max_parts:
            job.warnings.append(
                f"{live.get('name')}：{len(parts)} 个元件只装了前 {max_parts} 个"
                f"（MAX_PARTS；分批已经不撞 30s 了，这个上限是另一回事）")

    job.counts = {
        "placed": placed_total,
        "wired": wired_total,
        "unresolved": len(report.unresolved),
        "batches": sum(int(b.get("batches") or 0) for b in per_board),
        "per_board": per_board,
        "resolved_by": resolve_how,
    }
    report.schematic_detail = {"boards": per_board,
                              "batch": {"parts": SCH_PART_BATCH,
                                        "wires": SCH_WIRE_BATCH}}
    if failed:
        job.status = "failed"
        job.note = "；".join(failed)[:600]
        return
    if placed_total == 0:
        job.status = "not_ready"
        job.note = "一个器件都没装上（缺少 lcsc/mpn，或系统库不可用）"
        return
    job.status = "ok"
    job.note = (f"装 {placed_total} 个器件、{wired_total} 个引脚接线"
                + (f"；{len(report.unresolved)} 个未解析" if report.unresolved else "")
                + f"（分批 {job.counts['batches']} 次）")


# ---------------------------------------------------------------------------
# 段 3：PCB
# ---------------------------------------------------------------------------

#: 板框 + 导入 + 等图元落地。**摆放另起一段**（`_JS_PCB_MOVE`），
#: 因为 141 个元件的 `modify` 一次跑不完（每次 ≈0.2~0.5s）。
_JS_PCB = r"""
const P = __CTX__;
const outline = __OUTLINE__;
const steps = [];
async function attempt(name, fn) {
  try { const v = await fn(); steps.push({step: name, ok: true, value: v}); return v; }
  catch (e) { steps.push({step: name, ok: false, error: String((e && e.message) || e)}); return null; }
}

await attempt('openPcb', () => eda.dmt_EditorControl.openDocument(P.pcbUuid));
__DOC_READY__
const docType = docPoll.docType;
const docTries = docPoll.tries;

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
// 实测：`importChanges` 返回 true 之后图元是**陆续落地**的，12 个元件里
// 偶尔有一个要 8 秒以上。第一版只等 6 秒 → 那块板少 1 个元件，报告只说
// "11/12"，看不出来是"还没落地"还是"根本没导入"。
const tries = P.pollTries || 40;
for (let i = 0; i < tries; i++) {
  await new Promise(r => setTimeout(r, 500));
  waited += 500;
  all = (await eda.pcb_PrimitiveComponent.getAll()) || [];
  const have = new Set(all.map(c => c.getState_Designator ? c.getState_Designator() : ''));
  if (!expected.length) { if (i >= 4) break; continue; }
  if (expected.every(ref => have.has(ref))) break;
}
const haveNow = new Set(all.map(c => c.getState_Designator ? c.getState_Designator() : ''));
const missingExpected = expected.filter(ref => !haveNow.has(ref));
const onBoard = {};
for (const c of all) {
  try { onBoard[c.getState_Designator()] = c.getState_PrimitiveId(); }
  catch (e) {}
}
return {
  ok: true,
  steps: steps,
  outlineOk: outlineOk,
  imported: imported,
  componentCount: all.length,
  onBoard: onBoard,
  missingExpected: missingExpected,
  waitedMs: waited,
  docType: docType,
  docTries: docTries,
};
"""

#: 按位号搬元件。批大小由 Python 控制（`PCB_MOVE_BATCH`）。
_JS_PCB_MOVE = r"""
const P = __CTX__;
const want = __COMPS__;
const steps = [];
const moved = [];
const missing = [];
async function attempt(name, fn) {
  try { const v = await fn(); steps.push({step: name, ok: true}); return v; }
  catch (e) { steps.push({step: name, ok: false, error: String((e && e.message) || e)}); return null; }
}
await attempt('openPcb', () => eda.dmt_EditorControl.openDocument(P.pcbUuid));
__DOC_READY__
const byDesignator = {};
for (const c of ((await attempt('all', () => eda.pcb_PrimitiveComponent.getAll())) || [])) {
  try { byDesignator[c.getState_Designator()] = c; } catch (e) {}
}
for (const w of want) {
  const obj = byDesignator[w.ref];
  if (!obj) { missing.push(w.ref); continue; }
  const r = await attempt('move:' + w.ref, () => eda.pcb_PrimitiveComponent.modify(
    obj.getState_PrimitiveId(),
    {x: w.x, y: w.y, layer: w.layer, rotation: w.rotation}));
  if (r) moved.push({ref: w.ref, x: r.getState_X(), y: r.getState_Y()});
}
return {ok: true, moved: moved, missing: missing, steps: steps.slice(0, 6)};
"""

#: 收尾：保存 + 网表 + DRC。
_JS_PCB_FINISH = r"""
const P = __CTX__;
const steps = [];
async function attempt(name, fn) {
  try { const v = await fn(); steps.push({step: name, ok: true}); return v; }
  catch (e) { steps.push({step: name, ok: false, error: String((e && e.message) || e)}); return null; }
}
await attempt('openPcb', () => eda.dmt_EditorControl.openDocument(P.pcbUuid));
__DOC_READY__
const saved = await attempt('save', () => eda.pcb_Document.save());
const nets = await attempt('nets', () => eda.pcb_Net.getAllNetName());
const netlist = await attempt('netlist', () => eda.pcb_Net.getNetlist('JLCEDA'));
const drcPass = await attempt('drc', () => eda.pcb_Drc.check(true, false, false));
const drcDetail = await attempt('drcDetail', () => eda.pcb_Drc.check(true, false, true));
// **DFM 是另一类证据**：只在私有化构建可用；未配置规则或有失败项时
// 它是**抛错**（`INCOMPLETE_RESULT`）而不是返回一个失败列表 —— API 文档原文
// "不得视为检查通过"。所以这里**不吞错**，把错误文本带回去给人看。
const dfmAvailable = await attempt('dfmAvailability',
  () => eda.pcb_Dfm.getDfmAvailability());
let dfmResult = null, dfmError = '';
try { dfmResult = await eda.pcb_Dfm.checkDfm({}); }
catch (e) { dfmError = String((e && e.message) || e); }
const onBoard = ((await attempt('all', () => eda.pcb_PrimitiveComponent.getAllId())) || []);
let componentCount = 0;
try { componentCount = ((await eda.pcb_PrimitiveComponent.getAllPrimitiveId()) || []).length; }
catch (e) { componentCount = Array.isArray(onBoard) ? onBoard.length : 0; }
return {ok: true, saved: saved, nets: nets, netlist: netlist,
        drcPass: drcPass, drcDetail: drcDetail, componentCount: componentCount,
        dfmAvailable: dfmAvailable, dfm: dfmResult, dfmError: dfmError,
        steps: steps};
"""


def _pcb_payload(design: DesignModel, parts: list[Any]) -> list[dict[str, Any]]:
    """元件 → 立创坐标 payload（mil + 层 + 旋转）。"""
    out = []
    for part in parts:
        x, y = _pcb_xy(design, part)
        out.append({
            "ref": part.ref,
            "x": x,
            "y": y,
            "layer": _pcb_layer(part),
            "rotation": int(getattr(getattr(part, "placement", None),
                                    "rotation_z", 0) or 0),
        })
    return out


def _run_pcb(session: lceda_mod.LcedaSession, design: DesignModel,
             report: EmitReport) -> None:
    """逐板：板框 + 导入 + **分批**摆放 + 收尾（保存/网表/DRC）。

    为什么把「摆放」单独切成一段（`_JS_PCB_MOVE`）：

    * 单次 `invoke` 有 30 秒硬上限；`pcb_PrimitiveComponent.modify` ≈0.2~0.5s/个
    * 141 个元件的工程一次搬完必然超时，而**超时后 EDA 侧可能已经搬了一半**
      （手册 §17.7），重跑会看到"有的搬了有的没搬"
    * 分批后每批 ≤6 个，失败只损失一批，且报告能说清"哪一批"

    板框与 `importChanges` 仍在 setup 里（它们必须**在摆放之前**一次做完：
    先画板框再导入，导入后按真实位号搬）。
    """
    job = report.job("pcb")
    design_boards = design.board_list()
    live_boards = report.project.get("boards") or []
    total_boards = len(design_boards)

    comps_total = 0
    moved_total = 0
    missing_total: list[str] = []
    outline_ready = 0
    drc_records: list[dict[str, Any]] = []
    dfm_rows: list[dict[str, Any]] = []
    netlists: list[str] = []
    per_board: list[dict[str, Any]] = []

    # 叠层/网络类/差分对：在摆放之前应用（改层数会让已有铜皮失效）。
    spec = stackup_spec.derive(design)
    applied_steps: list[dict[str, Any]] = []
    # Bridge 的 `/execute` 有 30 秒硬上限，而叠层/网络类这一步在 bridge 上实测超时。
    # **只有"没有东西要写"时才允许跳过**（2 层 + 无网络类 + 无差分对）；
    # 真有约束要写就必须走 CLI，否则等于静默丢约束。
    if (report.channel or {}).get("kind") == "bridge":
        if (not spec.net_classes and not spec.diff_pairs
                and int(spec.copper_layers or 2) <= 2):
            applied_steps.append({"step": "stackup", "ok": True,
                                  "skipped": "bridge 单次 30 秒上限；本次没有网络类/差分对要写"})
            job.warnings.append(
                "bridge 通道跳过了叠层段（无网络类/差分对要写）—— "
                "有约束的板子请用 --channel=cli")
            applied_steps = applied_steps  # 保持结构
            live_boards = list(live_boards)
            spec_skip = True
        else:
            job.status = "not_ready"
            job.note = ("叠层/网络类/差分对这一段在 Bridge 的 30 秒上限内跑不完 —— "
                        "请用 --channel=cli 发射这块板")
            report.stackup = {**spec.to_dict(), "skipped": "bridge-30s"}
            return
    for live in ([] if locals().get("spec_skip") else live_boards):
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
                      "applied": [s for s in applied_steps
                                  if s.get("step") != "openPcb"][:40]}
    if spec.assumed:
        report.notes.extend(spec.assumed)

    for index, live in enumerate(live_boards):
        board = design_boards[index] if index < len(design_boards) else None
        if board is None:
            continue
        row: dict[str, Any] = {"board": live.get("name"), "batches": 0}
        per_board.append(row)
        pcb_uuid = str(live.get("pcbUuid") or "")
        if not pcb_uuid:
            job.warnings.append(f"{live.get('name')}：立创里这块板没有 PCB（pcbUuid 空）")
            row["status"] = "no_pcb"
            continue
        sch_rows = (report.schematic_detail or {}).get("boards") or []
        sch_saved = next((r.get("saved") for r in sch_rows
                          if r.get("board") == live.get("name")), None)
        if sch_saved is False:
            job.status = "not_ready"
            job.note = ("原理图 `save()` 没成功，导入 PCB 会**静默漏器件** —— "
                        "先把原理图存住再发射（这是实测：3 个器件只进 2 个）")
            return
        parts = _parts_for_board(design, board.id or "", total_boards)
        payload = _pcb_payload(design, parts)
        ctx = _board_context(design, live, report)
        ctx["expected"] = [p.ref for p in parts if p.ref in set(report.placed_refs)]
        # Bridge 通道的 `/execute` 有 **30 秒硬上限**：importChanges + 20 秒轮询
        # 会撞上限（实测直接 HTTP 500）。这里在 bridge 下降轮询预算到 8 秒，
        # 真没落地就靠下面的"补导一次"兜底。
        if (report.channel or {}).get("kind") == "bridge":
            ctx["pollTries"] = 16
        setup = (_JS_PCB
                 .replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                 .replace("__OUTLINE__", json.dumps(_outline_source(design, board)))
                 .replace("__DOC_READY__", _DOC_READY_JS))
        try:
            value = session.invoke(setup, expect=("steps",), timeout_ms=600_000,
                                   label=f"pcb/{live.get('name')}/setup")
        except lceda_mod.LcedaError as exc:
            job.status = "failed"
            job.note = f"{live.get('name')}：{exc}"
            return
        steps = {s.get("step"): s for s in (value.get("steps") or [])}
        if value.get("outlineOk"):
            outline_ready += 1
        else:
            err = (steps.get("outline") or {}).get("error") or "板框源数组为空"
            job.warnings.append(f"{live.get('name')}：板框没建成（{err}）")
        if not steps.get("importChanges", {}).get("ok"):
            job.warnings.append(
                f"{live.get('name')}：importChanges 失败 → "
                f"{steps.get('importChanges', {}).get('error')}")
        on_board = set((value.get("onBoard") or {}).keys())
        lacking = [r for r in (value.get("missingExpected") or []) if r not in on_board]
        if lacking:
            # 再补一次 `importChanges` 并重等一轮：这是**幂等**的（已导入的
            # 不会重复创建），比重整段重跑便宜。
            job.warnings.append(
                f"{live.get('name')}：导入后 {len(lacking)} 个元件没落地，补导一次 → "
                f"{lacking[:6]}")
            try:
                value2 = session.invoke(setup, expect=("steps",),
                                        timeout_ms=600_000,
                                        label=f"pcb/{live.get('name')}/setup2")
                on_board |= set((value2.get("onBoard") or {}).keys())
                lacking2 = [r for r in lacking if r not in on_board]
                if lacking2:
                    job.warnings.append(
                        f"{live.get('name')}：补导后仍缺 {lacking2[:6]}")
                    missing_total.extend(lacking2)
            except lceda_mod.LcedaError as exc:
                job.warnings.append(f"{live.get('name')}：补导失败 {exc}")
        row["componentsOnPcb"] = len(on_board)
        row["componentsFirstRead"] = int(value.get("componentCount") or 0)
        # **用补导之后的并集**：第一版用第一次读到的快照，补导成功后仍报 23/24
        # （而 missing_on_pcb 是 0，两处自相矛盾）。
        comps_total += len(on_board)

        # -- 分批摆放 --
        moved: list[dict[str, Any]] = []
        for start in range(0, len(payload), PCB_MOVE_BATCH):
            chunk = payload[start:start + PCB_MOVE_BATCH]
            script = (_JS_PCB_MOVE
                      .replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                      .replace("__COMPS__", json.dumps(chunk, ensure_ascii=False))
                      .replace("__DOC_READY__", _DOC_READY_JS))
            row["batches"] = int(row["batches"]) + 1
            try:
                moved_value = session.invoke(
                    script, expect=("moved",), timeout_ms=600_000,
                    label=f"pcb/{live.get('name')}/move/{start}")
            except lceda_mod.LcedaError as exc:
                job.warnings.append(
                    f"{live.get('name')} 摆放第 {start // PCB_MOVE_BATCH + 1} 批失败：{exc}")
                continue
            moved.extend(moved_value.get("moved") or [])
            missing_total.extend(moved_value.get("missing") or [])
        moved_total += len(moved)
        row["moved"] = len(moved)

        # -- 收尾：保存 + 网表 + DRC --
        finish = (_JS_PCB_FINISH
                  .replace("__CTX__", json.dumps(
                      {"pcbUuid": pcb_uuid}, ensure_ascii=False))
                  .replace("__DOC_READY__", _DOC_READY_JS))
        try:
            done = session.invoke(finish, expect=("saved",), timeout_ms=600_000,
                                  label=f"pcb/{live.get('name')}/finish")
        except lceda_mod.LcedaError as exc:
            job.warnings.append(f"{live.get('name')} 收尾失败：{exc}")
            done = {}
        row["saved"] = done.get("saved")
        if done.get("netlist"):
            netlists.append(str(done.get("netlist")))
        dfm_rows.append({
            "board": live.get("name"),
            **dfm_mod.dfm_verdict(done.get("dfm"),
                                  error=str(done.get("dfmError") or ""),
                                  available=done.get("dfmAvailable")),
        })
        drc_records.append({
            "board": live.get("name"),
            "pass": bool(done.get("drcPass")),
            "violations": _count_violations(done.get("drcDetail")),
            "categories": _drc_categories(done.get("drcDetail")),
            "groups": _drc_groups(done.get("drcDetail")),
        })
        row["status"] = "ok"
        if not done.get("saved"):
            job.warnings.append(
                f"{live.get('name')}：`pcb_Document.save()` 返回假 —— 盘上没有")

    job.counts = {
        "boards": len(live_boards),
        "boards_processed": len(per_board),
        "outline_ready": outline_ready,
        "components_on_pcb": comps_total,
        "moved": moved_total,
        "missing_on_pcb": len(missing_total),
        "move_batches": sum(int(b.get("batches") or 0) for b in per_board),
        "per_board": per_board,
    }
    if missing_total:
        job.warnings.append(
            f"{len(missing_total)} 个元件导入后找不到（位号对不上）："
            f"{', '.join(missing_total[:8])}")
    # 空板的 DRC 通过**没有意义**（一块元件都没有的板当然没有违规）。
    # 这正是 AGENTS §0.1 的形状：缺失被写成通过。所以先看有没有东西可查。
    trustworthy = sum(sum(r.get("categories", {}).get(k, 0)
                          for k in ("clearance", "netlist", "physical"))
                      for r in drc_records)
    connection = sum(r.get("categories", {}).get("connection", 0)
                     for r in drc_records)
    drc_status = "ok" if drc_records else "not_ready"
    drc_note = "立创的 DRC 是**外部证据**，进 handoff 的 external 类，不写进 ladder"
    if drc_records and comps_total == 0:
        drc_status = "not_ready"
        drc_note = "板上一块元件都没有，DRC 通过不算数（空板当然没有违规）"
    elif drc_records and trustworthy:
        drc_status = "not_ready"
        drc_note = f"DRC 报 {trustworthy} 条**可信类别**违规（clearance/netlist/physical）"
    elif drc_records:
        drc_note = (
            f"可信类别零违规；另有 {connection} 条 Connection Error —— "
            "实测它不认 API 过孔构成的跨层连接（手册 §18.1），"
            "连通性以几何判定为准。这些都是**外部证据**，进 handoff 的 external 类，"
            "不写进 ladder")
    report.drc = {
        "status": drc_status,
        "pass": all(r["pass"] for r in drc_records) if (drc_records and comps_total) else False,
        "trustworthy_violations": trustworthy,
        "connection_violations": connection,
        "checked": len(drc_records) if comps_total else 0,
        "boards": drc_records,
        "dfm": dfm_rows,
        "note": drc_note,
    }
    # **制造结论 = DRC 可信类别 + DFM**。DFM 不可用或不完整时整条是
    # `not_ready`（不是 ok）；`connection` 类只提示（实测它不认 API 过孔）。
    worst_dfm = next((r for r in dfm_rows if r.get("status") != "ok"), None) \
        or (dfm_rows[0] if dfm_rows else None)
    report.fabrication = dfm_mod.merge(report.drc, worst_dfm)
    if report.fabrication["status"] != "ok":
        job.warnings.append(report.fabrication["note"])
    report.pcb_detail = {"boards": per_board, "move_batch": PCB_MOVE_BATCH}
    report.parity = parity_from_netlists(design, netlists,
                                         exclude_refs=_failed_refs(report))
    parity_counts = report.parity.get("counts") or {}
    bad_pins = (int(parity_counts.get("missing") or 0)
                + int(parity_counts.get("wrong") or 0)
                + int(parity_counts.get("extra") or 0))
    # **只 stat 在 parity 字段里是不够的**：总状态只看 jobs，于是
    # "PCB 网表比设计少一个引脚"会一路绿灯到底（实测 bridge 那次：
    # R1.1 少了 A1、8 段全 ok）。所以这里记一笔，在函数**末尾**改 pcb 的状态
    # （末尾那段会无条件把 job.status 置 ok，早改会被覆盖掉）。
    parity_note = ""
    if bad_pins:
        first = (report.parity.get("missing") or [{}])[0]
        parity_note = (f"PCB 网表与 design.json 不一致：缺 "
                       f"{parity_counts.get('missing')} / 错 "
                       f"{parity_counts.get('wrong')} / 多 "
                       f"{parity_counts.get('extra')}（例：{first}）")

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
                f"板框 {outline_ready}/{len(live_boards)} 块建成；"
                f"摆放分 {job.counts['move_batches']} 批")
    if parity_note:
        job.status = "not_ready"
        job.note = parity_note
        job.counts["parity_missing"] = int(parity_counts.get("missing") or 0)


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
__DOC_READY__
const t0 = Date.now();
let result = null;
let error = '';
const props = {};
// 显式给网络列表：多板/多网络时引擎默认只"布未布线的网络"，
// 一条已经布了一半的网络会被跳过（实测 12 焊盘的 GND 留下断口）。
// `RoutingNets` 大写 R —— 是 `IPCB_AutoRoutingProps` 里的真实字段名。
if (P.nets && P.nets.length) props.RoutingNets = P.nets;
props.optimization = 1;   // EPCB_AutoRoutingOptimization.COMPLETION = 优先布通率
props.cornerStyle = 0;    // EPCB_AutoRoutingCornerStyle.DEGREE_45
try {
  result = await eda.pcb_Document.autoRouting(props);
} catch (e) { error = String((e && e.message) || e); }
return {ok: !error, ms: Date.now() - t0, result: result, error: error,
        docType: docPoll.docType, docTries: docPoll.tries, steps: steps};
"""

#: **换板之后必须等 PCB 文档真的激活**。
#:
#: 实测：`openDocument` 返回 true 只代表"打开了"，`getCurrentDocumentInfo()`
#: 要到**几秒后**才变成 `documentType === 3`。第一版这里只 `setTimeout(900)`，
#: 多板发射时**第一块板的布线跑在了上一块板的文档上** —— 结果 `A1`（只属于
#: 第一块板）一条走线都没有，而第二块板的 `GND/VOUT` 正常，报告只说
#: "布通 2/3"，看不出来是换板没就绪。
_DOC_READY_JS = r"""
const docPoll = {tries: 0, docType: null};
for (let i = 0; i < 48; i++) {
  let d = null;
  try { d = await eda.dmt_SelectControl.getCurrentDocumentInfo(); } catch (e) { d = null; }
  docPoll.docType = d ? d.documentType : null;
  docPoll.tries = i + 1;
  if (docPoll.docType === 3) break;
  await new Promise(r => setTimeout(r, 250));
}
await new Promise(r => setTimeout(r, 400));
"""

#: 自研布线器的落笔：逐段 `create`（**只能用 8 参数标量形式**，手册 §17.12
#: 说折线数组形式一律报「无法创建直线图元」）+ 过孔。批大小由 Python 控制。
_JS_EMIT_PLAN = r"""
const P = __CTX__;
const errors = [];
async function attempt(name, fn) {
  try { const v = await fn(); return v; }
  catch (e) { errors.push({step: name, error: String((e && e.message) || e)}); return null; }
}
await attempt('openPcb', () => eda.dmt_EditorControl.openDocument(P.pcbUuid));
__DOC_READY__
let lines = 0;
for (const s of P.segments) {
  const r = await attempt('line', () => eda.pcb_PrimitiveLine.create(
    s.net, s.layer, s.x1, s.y1, s.x2, s.y2, s.width, false));
  if (r) lines += 1;
}
let vias = 0;
for (const v of P.vias) {
  const r = await attempt('via', () => eda.pcb_PrimitiveVia.create(
    v.net, v.x, v.y, v.hole, v.diameter));
  if (r) vias += 1;
}
return {ok: true, lines: lines, vias: vias, wantedLines: P.segments.length,
        wantedVias: P.vias.length, errors: errors.slice(0, 8),
        docType: docPoll.docType, docTries: docPoll.tries};
"""


#: 布完**自己去量**。`IPCB_AutoRoutingResult` 不可信（AGENTS §3.20）：
#: 同一块板 `successNetsCount: 0 / failedNets: [全部]` 而盘上明明有 9 段走线。
#: 这里量的是**铜皮事实**：逐网络数走线、数过孔、跑一次 DRC、读 `save()` 返回值。
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


def _expected_nets(design: DesignModel, board: Any = None) -> list[str]:
    """设计的**真实布线目标**：≥2 个焊盘的网络。

    单个焊盘的网络没有"连通"可言，不该进分母（否则 1 脚网络会让分母虚高）。

    `board` 给了就只算**落在这块板上**的网络（多板时同一网络可能跨板，
    但每块板只该按自己的引脚算）。
    """
    out: list[str] = []
    for entry in design.nets:
        name = str(entry.get("net") or "")
        pins = [p for p in (entry.get("pins") or []) if p.get("ref") != "board"]
        if board is not None:
            keep = []
            for p in pins:
                try:
                    part = design.part(str(p.get("ref") or ""))
                except Exception:  # noqa: BLE001 - 悬空引用由 validate 报，这里跳过
                    continue
                if design.board_of(part) is board:
                    keep.append(p)
            pins = keep
        if name and len(pins) >= 2:
            out.append(name)
    return out


def _read_geometry(session: lceda_mod.LcedaSession, board: dict[str, Any]) -> Any:
    """读一块板的几何（`pcb_geo`）。读失败返回 None，由调用方判 `not_ready`。"""
    from . import pcb_geo

    pcb_uuid = str(board.get("pcbUuid") or "")
    try:
        payload = session.invoke(pcb_geo.read_js(pcb_uuid), require_ok=False,
                                 timeout_ms=240_000,
                                 label=f"geo/{board.get('name')}")
    except lceda_mod.LcedaError:
        return None
    return pcb_geo.parse(payload or {}, board=str(board.get("name") or ""),
                         pcb_uuid=pcb_uuid)


def _emit_plan(session: lceda_mod.LcedaSession, board: dict[str, Any],
               planned: Any, *, batch: int = 14) -> dict[str, Any]:
    """把规划好的线段/过孔落进 PCB。**每批 ≤14 段**（手册 §13.3 的 30 秒上限）。"""
    ctx_base = {"pcbUuid": board.get("pcbUuid") or ""}
    lines = 0
    vias = 0
    errors: list[Any] = []
    segs = list(planned.segments)
    for start in range(0, len(segs), batch):
        ctx = dict(ctx_base)
        ctx["segments"] = [s.to_dict() for s in segs[start:start + batch]]
        ctx["vias"] = []
        value = session.invoke(
            _JS_EMIT_PLAN.replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                         .replace("__DOC_READY__", _DOC_READY_JS),
            require_ok=False, timeout_ms=180_000, label="plan/lines")
        lines += int((value or {}).get("lines") or 0)
        errors.extend((value or {}).get("errors") or [])
    if planned.vias:
        ctx = dict(ctx_base)
        ctx["segments"] = []
        ctx["vias"] = [v.to_dict() for v in planned.vias]
        value = session.invoke(
            _JS_EMIT_PLAN.replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                         .replace("__DOC_READY__", _DOC_READY_JS),
            require_ok=False, timeout_ms=180_000, label="plan/vias")
        vias += int((value or {}).get("vias") or 0)
        errors.extend((value or {}).get("errors") or [])
    return {"lines": lines, "vias": vias, "errors": errors[:6],
            "wantedLines": len(segs), "wantedVias": len(planned.vias)}


def _run_route(session: lceda_mod.LcedaSession, design: DesignModel,
               report: EmitReport, *, route: str) -> None:
    """布线。四档：``none`` / ``native`` / ``ours`` / ``gui``。

    ## `native`（立创引擎）现在可用

    之前这里写过两版错结论（「引擎拒绝执行」「headless 没有布线执行环境」）。
    真因见 AGENTS §3.20：板框 `R` 模式原点是左上角，我写成了 `y0` 而不是
    `y0 + h`，整块板落在板框外。修掉之后同一块板 `2/2` 布通、9 段 45° 单层。

    ## `ours`（自研，`design/router.py`）

    栅格 + 8 邻域 A*，天然只出 0/45/90 度；需要换层才放过孔。它**不下连通结论**：
    布完把真实几何读回来，交给 `pcb_geo.connectivity()` 建并查集判。

    ## 两档共用的三条纪律

    1. **换板之后等文档真的激活**（`_DOC_READY_JS`），否则会布在上一块板上
    2. **判定量几何，不读 `successNetsCount`**（它只描述这次调用做了什么）
    3. **`save()` 为假就是 `not_ready`** —— 打板的前提是线在盘上
    """
    from . import pcb_geo, router

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
    live = report.project.get("boards") or []
    if not live:
        job.status = "not_ready"
        job.note = "没有 PCB 可布线"
        return
    if route == "gui":
        session.invoke("await eda.pcb_Document.save(); return {ok:true};",
                       require_ok=False, timeout_ms=60_000, label="save")
        job.status = "not_ready"
        job.counts = {"router": "gui", "boards": len(live)}
        job.note = ("工程已建好并保存，布线留给人点一次：「布线 → 自动布线」。"
                    "引擎在 CLI 会话里也能用（实测 route=native 已布通），"
                    "选 gui 只是要人在场确认。")
        return

    design_boards = design.board_list()
    rows: list[dict[str, Any]] = []
    totals = {"segments": 0, "vias": 0, "tracks": 0, "routed": 0,
              "expected": 0, "split": 0}
    failed = ""
    for index, board_live in enumerate(live):
        board = design_boards[index] if index < len(design_boards) else None
        if board is None:
            continue
        name = str(board_live.get("name") or "")
        expected = _expected_nets(design, board)
        row: dict[str, Any] = {"board": name, "expected": expected}
        rows.append(row)
        if not expected:
            row["status"] = "not_ready"
            row["note"] = "这块板上没有 ≥2 焊盘的网络，没有可布的对象"
            continue

        if route == "native":
            ctx = {"pcbUuid": board_live.get("pcbUuid") or "",
                   "nets": expected}
            value = session.invoke(
                _JS_ROUTE.replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                         .replace("__DOC_READY__", _DOC_READY_JS),
                require_ok=False, timeout_ms=900_000, label=f"route/{name}")
            if not value.get("ok"):
                failed = f"{name}：{value.get('error')}"
                row["status"] = "not_ready"
                row["error"] = failed
                continue
            engine = value.get("result") or {}
            row["engine"] = {
                "success": bool(engine.get("success")),
                "totalNets": int(engine.get("totalNetsCount") or 0),
                "successNets": int(engine.get("successNetsCount") or 0),
                "failedNets": (engine.get("failedNets") or [])[:8],
            }
            row["docType"] = value.get("docType")
            row["docTries"] = value.get("docTries")
        else:
            # -- ours：读几何 → 规划 → 分批落笔 --
            geo = _read_geometry(session, board_live)
            if geo is None:
                row["status"] = "not_ready"
                row["note"] = "读不回板上的几何（板框/焊盘）"
                continue
            planned = router.plan(geo, nets=expected)
            row["plan"] = planned.to_dict()
            if planned.unrouted:
                failed = (f"{name}：A* 布不出来 {planned.unrouted[:6]}"
                          f"（栅格 {planned.grid.get('nx')}×{planned.grid.get('ny')}，"
                          f"格距 {planned.grid.get('cell')} mil）")
            if planned.segments or planned.vias:
                row["written"] = _emit_plan(session, board_live, planned)

        # -- 引擎布完可能留断口（实测 Board2 的 GND 分成 2 组）-------------
        # 两次尝试 + 自研布线兜底：用户要的是"多板能跑成"，
        # 而断口是 **几何事实**，能补就补、补不上就照实报。
        if route == "native":
            geo_mid = _read_geometry(session, board_live)
            if geo_mid is not None:
                split_now = (pcb_geo.connectivity(geo_mid).get("split") or [])
                if split_now:
                    ctx2 = {"pcbUuid": board_live.get("pcbUuid") or "",
                            "nets": split_now}
                    session.invoke(
                        _JS_ROUTE.replace("__CTX__",
                                          json.dumps(ctx2, ensure_ascii=False))
                                  .replace("__DOC_READY__", _DOC_READY_JS),
                        require_ok=False, timeout_ms=900_000,
                        label=f"route2/{name}")
                    geo_mid = _read_geometry(session, board_live)
                    split_now = (pcb_geo.connectivity(geo_mid).get("split") or [])
                if split_now:
                    planned = router.plan(geo_mid, nets=split_now)
                    row["fallback"] = {"nets": split_now,
                                       "segments": len(planned.segments),
                                       "unrouted": planned.unrouted}
                    if planned.segments or planned.vias:
                        row["fallbackWritten"] = _emit_plan(
                            session, board_live, planned)

        measured = session.invoke(_JS_ROUTE_MEASURE, require_ok=False,
                                  timeout_ms=240_000, label=f"measure/{name}")
        measured = measured or {}
        row["measured"] = {
            "tracks": measured.get("tracks"), "vias": measured.get("vias"),
            "save": measured.get("save"),
            "drcViolations": _count_violations(measured.get("drc")),
            "drcCategories": _drc_categories(measured.get("drc")),
        }
        totals["tracks"] += int(measured.get("tracks") or 0)
        totals["vias"] += int(measured.get("vias") or 0)
        totals["expected"] += len(expected)

        # 判定：拿**读回来的真实几何**建并查集，不看引擎自述
        geo_after = _read_geometry(session, board_live)
        conn = pcb_geo.connectivity(geo_after) if geo_after is not None else {
            "status": "not_ready", "checked": 0, "connected": [], "split": [],
            "single": [], "detail": {},
            "basis": "布完读不回几何，连通性未判定（不是通过）"}
        row["connectivity"] = conn
        row["status"] = conn.get("status")
        split = list(conn.get("split") or [])
        totals["split"] += len(split)
        # 口径只有一个：**判定器说通的**才计数
        totals["routed"] += len(conn.get("connected") or [])
        written = row.get("written") or {}
        if written and int(written.get("lines") or 0) > int(
                (measured or {}).get("tracks") or 0):
            job.warnings.append(
                f"{name}：落笔 {written.get('lines')} 段，盘上只读到 "
                f"{(measured or {}).get('tracks')} 段 —— 立创可能合并了重合段；"
                "连通性按读回来的几何判，不按落笔数判")

    report.route_detail = {"route": route, "boards": rows}
    job.counts = {"route": route, "boards": len(rows),
                  "expected_nets": totals["expected"],
                  "routed_nets": totals["routed"],
                  "tracks": totals["tracks"], "vias": totals["vias"],
                  "split_nets": totals["split"]}
    # **这一段的结论只关于"布线"**。整板连通性由 `verify` 段在铺铜之后下 ——
    # 真实板上 GND 靠铺铜连，铺铜前判会误报断开（见 `_run_verify` 的注释）。
    split_names = sorted({n for r in rows
                          for n in ((r.get("connectivity") or {}).get("split") or [])})
    split_by_board = {r.get("board"): (r.get("connectivity") or {}).get("split")
                      for r in rows if (r.get("connectivity") or {}).get("split")}
    job.counts["pre_pour_split"] = split_by_board
    job.status = "not_ready"
    if failed:
        job.note = failed
    elif totals["expected"] == 0:
        job.note = ("设计里没有 ≥2 个焊盘的网络 —— 没有可布的对象，"
                    "「0 条违规」在这种输入上不算通过")
    elif any((r.get("measured") or {}).get("save") is False for r in rows):
        job.note = ("线布出来了但 `pcb_Document.save()` 返回假 —— "
                    "盘上没有，不算成（P25 的纪律）")
    elif totals["tracks"] == 0:
        job.note = "一条走线都没落笔（不是「没试」，是调用后盘上仍然 0 段）"
    else:
        job.status = "ok"
        conn_now = max(0, totals["expected"] - totals["split"])
        job.note = (f"布出 {totals['tracks']} 段走线 / {totals['vias']} 过孔；"
                    f"**铺铜前**几何连通 {conn_now}/{totals['expected']} 条网"
                    + (f"（未连通 {split_names[:6]}，铺铜后由 verify 复判）"
                       if split_names else "")
                    + "；最终结论看 verify 段")
        if split_names:
            job.warnings.append(
                "铺铜前有断口的网络：" + str(split_names[:6])
                + " —— 若地网靠铺铜连通，这属正常；verify 段会给最终判定")
        if totals["vias"] == 0:
            job.warnings.append("零过孔：全程单层走线")

# ---------------------------------------------------------------------------
# 段 4.5：铺铜（P29）
# ---------------------------------------------------------------------------

#: 双面 GND 铺铜。**坐标是这一段的全部难点**（手册 §19）：
#:
#: `TPCB_PolygonSourceArray` 的 `R` 是 `R x y width height rot round`，
#: **`(x, y)` 是左上角、矩形向 −Y 延伸 height**。想要覆盖 `y ∈ [a, b]`：
#: 左上角写 `y = b`，高度写 `b − a`。写反的后果不是报错 —— 是铺铜被板框裁掉，
#: 0 铜皮，`getCopperRegion()` 返回 null，然后 DRC 报"该网络焊盘全部未连接"。
#:
#: 所以判定只认三条：`create` 返回对象、`rebuildCopperRegion()` 为真、
#: `getCopperRegion()` 非空。**"铺铜成功"不算通过**（手册 §19.2 的口诀）。
_JS_POUR = r"""
const P = __CTX__;
const steps = [];
const rows = [];
async function attempt(name, fn) {
  try { const v = await fn(); return v; }
  catch (e) { steps.push({step: name, ok: false, error: String((e && e.message) || e)}); return null; }
}
await attempt('openPcb', () => eda.dmt_EditorControl.openDocument(P.pcbUuid));
__DOC_READY__
const box = P.box;
const inset = P.inset || 8;
const x = box[0] + inset;
const yTop = box[3] - inset;
const w = (box[2] - box[0]) - 2 * inset;
const h = (box[3] - box[1]) - 2 * inset;
// **先放缝合过孔**：只铺底层时那片铜和任何焊盘都没有电气连接，
// 立创会当孤岛丢掉（preserveSilos:false）—— 实测底层 pourFills = 0、顶层 = 7。
// 手册 §17.16 的对策就是"用短线 + 过孔接到另一面"。
const stitch = [];
for (const v of (P.stitch || [])) {
  const made = await attempt('stitchVia', () => eda.pcb_PrimitiveVia.create(
    P.net, v[0], v[1], P.viaHole, P.viaDia));
  if (made) stitch.push([v[0], v[1]]);
}
const pours = [];
function rectOf(box, inset) {
  return {x: box[0] + inset, yTop: box[3] - inset,
          w: (box[2] - box[0]) - 2 * inset, h: (box[3] - box[1]) - 2 * inset};
}
// **其它电源网先铺**：每张网铺自己焊盘的包围盒（外扩一圈）。
// 顺序很重要 —— 实测"先铺地、再铺 3V3"时小铺铜被地平面的间距整片吃光
// （`pourFills = 0`）。先放小铜、再让地平面绕开它。
for (const item of (P.extra || [])) {
  const r = rectOf(item.box, 0);
  if (r.w <= 0 || r.h <= 0) continue;
  for (const layer of (item.layers || [])) {
    const row = {layer: layer, net: item.net, extra: true,
                 created: false, rebuilt: false, hasCopper: false, fills: 0};
    const cp = await attempt('polygonExtra', () =>
      eda.pcb_MathPolygon.createComplexPolygon(['R', r.x, r.yTop, r.w, r.h, 0, 0]));
    if (!cp) { rows.push(row); continue; }
    const pour = await attempt('pourExtra', () =>
      eda.pcb_PrimitivePour.create(item.net, layer, cp, 'solid'));
    row.created = !!pour;
    if (!pour) { rows.push(row); continue; }
    pours.push(pour);
    const rebuilt = await attempt('rebuildExtra', () => pour.rebuildCopperRegion());
    row.rebuilt = rebuilt === true;
    const cr = await attempt('copperExtra', () => pour.getCopperRegion());
    row.hasCopper = !!(cr && (Array.isArray(cr.pourFills) ? cr.pourFills.length : true));
    row.fills = (cr && Array.isArray(cr.pourFills)) ? cr.pourFills.length : 0;
    rows.push(row);
  }
}
for (const layer of P.layers) {
  const row = {layer: layer, created: false, rebuilt: false, hasCopper: false,
               fills: 0, extra: false};
  const cp = await attempt('polygon', () => eda.pcb_MathPolygon.createComplexPolygon(
    ['R', x, yTop, w, h, 0, 0]));
  if (!cp) { rows.push(row); continue; }
  const pour = await attempt('pour', () => eda.pcb_PrimitivePour.create(
    P.net, layer, cp, 'solid'));
  row.created = !!pour;
  if (!pour) { rows.push(row); continue; }
  const rebuilt = await attempt('rebuild', () => pour.rebuildCopperRegion());
  row.rebuilt = rebuilt === true;
  const cr = await attempt('copper', () => pour.getCopperRegion());
  row.hasCopper = !!(cr && (Array.isArray(cr.pourFills) ? cr.pourFills.length : true));
  row.fills = (cr && Array.isArray(cr.pourFills)) ? cr.pourFills.length : 0;
  pours.push(pour);
  rows.push(row);
}
// 两层都建好之后**再统一重建**：先建后重建的顺序实测会影响底层填铜
for (const pour of pours) {
  await attempt('rebuild2', () => pour.rebuildCopperRegion());
}
for (let i = 0; i < rows.length; i++) {
  const cr = await attempt('copper2', () => pours[i].getCopperRegion());
  rows[i].fills2 = (cr && Array.isArray(cr.pourFills)) ? cr.pourFills.length : 0;
  rows[i].hasCopper2 = !!(cr && (Array.isArray(cr.pourFills)
    ? cr.pourFills.length : true));
}
const saved = await attempt('save', () => eda.pcb_Document.save());
return {ok: true, rows: rows, saved: saved, stitch: stitch,
        steps: steps.slice(0, 8), box: box, rect: [x, yTop, w, h]};
"""

#: 地网络的判据。**只认名字**，不猜"哪个网络焊盘最多" —— 那会替人做决定。
#: 地网判据**不在这里另写一份**：见 `design/power.py`（P41 收拢）。
_GROUND_HINTS = power_mod.GROUND_HINTS

#: 铺铜内缩量（mil）。手工给的保守值：铺铜离板边 8 mil，避免被板框裁掉。
POUR_INSET_MIL = 8.0


def _power_nets(names: Iterable[str]) -> list[str]:
    """所有电源类网络（含地）。判据在 `design/power.py`（与布线器同一张表）。"""
    seen: list[str] = []
    for name in names:
        text = str(name or "")
        if text and text not in seen and power_mod.is_power(text):
            seen.append(text)
    return seen


def _ground_nets(names: Iterable[str]) -> list[str]:
    """**仅按网名推断**的地网。要精确请用 `power.classify(design.power_nets, ...)`。"""
    return [str(n) for n in names if n and power_mod.is_ground(str(n))]


def _net_kinds(design: DesignModel, names: Iterable[str]) -> dict[str, Any]:
    """电源/地分类：**声明优先**，没声明才按网名推断（并把来源写出来）。"""
    return power_mod.classify(declared=design.power_nets or (), nets=names)


def _run_pour(session: lceda_mod.LcedaSession, design: DesignModel,
              report: EmitReport) -> None:
    """给每块板铺**双面** GND 铜。

    单面铺铜连不上顶层的 SMD 焊盘（手册 §17.15 实测：只加底层时未连接焊盘
    只从 16 降到 14，补上顶层立刻降到 7），所以两面一起铺。
    """
    job = report.job("pour")
    live = report.project.get("boards") or []
    if not live:
        job.status = "not_ready"
        job.note = "没有 PCB 可铺铜"
        return
    design_boards = design.board_list()
    design_nets = [str(e.get("net") or "") for e in design.nets]
    rows: list[dict[str, Any]] = []
    ok_boards = 0
    for index, board_live in enumerate(live):
        board = design_boards[index] if index < len(design_boards) else None
        name = str(board_live.get("name") or "")
        row: dict[str, Any] = {"board": name}
        rows.append(row)
        geo = _read_geometry(session, board_live)
        if geo is None:
            row["status"] = "not_ready"
            row["note"] = "读不回板框，铺不了铜"
            continue
        box = geo.outline_box
        if box is None:
            row["status"] = "not_ready"
            row["note"] = "板框读不到坐标（outline 源数组为空）"
            continue
        kinds = _net_kinds(design, list(geo.nets) + design_nets)
        row["nets_source"] = kinds["source"]
        names = kinds["ground"]
        row["box"] = [round(v, 1) for v in box]
        if not names:
            row["status"] = "not_ready"
            row["note"] = ("板上没有叫 GND/AGND/VSS 的网络 —— 铺哪张网得由人定，"
                           "我不猜")
            continue
        net = names[0]
        row["net"] = net
        # **其它电源网也要铺**（用户要求 +3V3/VBUS 也覆盖）：
        # 地网铺整板；别的电源网铺自己焊盘的包围盒（外扩一圈），
        # 两张铜之间由立创按间距规则自动避让。
        power_nets = [n for n in _net_kinds(
            design, [p.net for p in geo.pads] + list(geo.nets) + design_nets
        )["power"] if n != net]
        copper_layers = int(stackup_spec.derive(design).copper_layers or 2)
        row["power_nets"] = power_nets
        extra: list[dict[str, Any]] = []
        if power_nets and copper_layers < 4:
            # **同层多张铺铜会互相吃光**（实测：先铺地再铺 3V3，小铜 0 fills；
            # 反过来先铺 3V3 再铺地，地变成 0 fills）。两层板没有第三层给电源，
            # 所以正确做法是：**地铺整板，其它电源网按宽走线**（布线器对电源网
            # 用 20 mil）。这不是"没做"，是两层板的物理约束。
            job.warnings.append(
                f"{name}：{power_nets} 不铺铜（{copper_layers} 层板：同层多张铺铜"
                "会互相吃光），改按 20 mil 宽走线；≥4 层板会自动给它们内层铺铜")
        for extra_net in (power_nets[:4] if copper_layers >= 4 else []):
            pads = [p for p in geo.pads if p.net == extra_net]
            if len(pads) < 2:
                continue
            xs = [p.x for p in pads]
            ys = [p.y for p in pads]
            margin = 40.0
            r = (max(box[0] + 8.0, min(xs) - margin),
                 max(box[1] + 8.0, min(ys) - margin),
                 min(box[2] - 8.0, max(xs) + margin),
                 min(box[3] - 8.0, max(ys) + margin))
            if r[2] - r[0] < 60 or r[3] - r[1] < 60:
                row.setdefault("skipped_nets", []).append(
                    {"net": extra_net, "why": "焊盘范围太小，铺不出可用铜"})
                continue
            inner = 3 + (len(extra) % max(1, copper_layers - 2))
            extra.append({"net": extra_net, "box": [round(v, 1) for v in r],
                          "layers": [inner]})
        row["extra_nets"] = [e["net"] for e in extra]
        from . import router as router_mod

        sites = router_mod.stitch_sites(geo, net, count=6, inset_mil=60.0)
        row["stitch_sites"] = len(sites)
        if not sites:
            row["note"] = ("一个缝合过孔都放不下（板子太挤）—— "
                           "底层铺铜会被当孤岛丢掉，这对 GND 等于没铺")
        ctx = {"pcbUuid": board_live.get("pcbUuid") or "", "net": net,
               "layers": [1, 2], "box": list(box), "inset": POUR_INSET_MIL,
               "stitch": [list(p) for p in sites],
               "extra": extra,
               "viaDia": 20.0, "viaHole": 12.0}
        value = session.invoke(
            _JS_POUR.replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
                    .replace("__DOC_READY__", _DOC_READY_JS),
            require_ok=False, timeout_ms=240_000, label=f"pour/{name}")
        value = value or {}
        poured = value.get("rows") or []
        row["layers"] = poured
        row["save"] = value.get("saved")
        copper = [r for r in poured if r.get("hasCopper2", r.get("hasCopper"))]
        lost = [r for r in poured if r not in copper]
        row["extra_poured"] = [r for r in poured if r.get("extra")]
        # 判据分两层：**每张网至少有一层有铜** → 这批铺铜算铺上了；
        # 某一层没铜只记 warning（多半是孤岛被丢，AGENTS §3.23）。
        # 为什么不再要求"两层都有铜"：顶层铺铜已经把顶层 SMD 焊盘连上了
        # （实测 Board2 顶层 7 fills、底层 0，而 `verify` 判 4/4 全连通）。
        # 强行要求两层会把一块**能打板**的板判成 not_ready —— 拿工程质量
        # 问题冒充"没铺上"，正是 AGENTS §0.1 的反面。
        by_net = {}
        for r in poured:
            by_net.setdefault(str(r.get("net") or net), []).append(r)
        dead_nets = [n for n, rs in by_net.items()
                     if not any(r.get("hasCopper2", r.get("hasCopper")) for r in rs)]
        row["poured_nets"] = sorted(by_net)
        row["dead_nets"] = dead_nets
        if lost:
            row["lost_layers"] = [{"net": str(r.get("net") or net),
                                   "layer": r.get("layer")} for r in lost]
            job.warnings.append(
                f"{name}：{len(lost)} 张铺铜没铜 → "
                f"{[(str(r.get('net') or net), r.get('layer')) for r in lost][:4]}"
                "（单层没铜多半是孤岛被丢，见 AGENTS §3.23）")
        row["stitched"] = len(value.get("stitch") or [])
        if dead_nets:
            row["status"] = "not_ready"
            row["note"] = (f"这些网一层铜都没铺上：{dead_nets} —— "
                           "先怀疑坐标（手册 §19.2），不要怀疑 API")
        elif not poured:
            row["status"] = "not_ready"
            row["note"] = "一张铺铜都没建出来（create 全部失败）"
        elif value.get("saved") is False:
            row["status"] = "not_ready"
            row["note"] = "铺铜建好了但 `save()` 返回假 —— 盘上没有"
        else:
            row["status"] = "ok"
            ok_boards += 1
    report.route_detail = dict(report.route_detail or {})
    report.route_detail["pour"] = rows
    job.counts = {"boards": len(rows), "poured": ok_boards,
                  "inset_mil": POUR_INSET_MIL}
    if not rows:
        job.status = "not_ready"
        job.note = "没有板"
    elif ok_boards == len(rows):
        job.status = "ok"
        extra_nets = sorted({n for r in rows for n in (r.get("extra_nets") or [])})
        part = [w for w in job.warnings if "没铜" in w]
        job.note = (f"{ok_boards} 块板 GND 铺铜，每张网至少一层有铜"
                    + (f"；另铺 {extra_nets}" if extra_nets else "")
                    + (f"；{len(part)} 处单层没铜（见 warnings）" if part else ""))
    else:
        job.status = "not_ready"
        bad = [r for r in rows if r.get("status") != "ok"]
        job.note = f"{len(bad)}/{len(rows)} 块板铺铜没成：" + str(
            [(r.get("board"), r.get("note")) for r in bad][:3])


# ---------------------------------------------------------------------------
# 段 4.75：**最终连通性判定**（含铺铜）
# ---------------------------------------------------------------------------

def _run_verify(session: lceda_mod.LcedaSession, design: DesignModel,
                report: EmitReport) -> None:
    """整块板（含铺铜）的最终几何连通性。

    **为什么单独成段**：布线段的判定发生在铺铜之前，而真实板上 **GND 是
    靠铺铜连的**。实测：12 焊盘的 GND，引擎只布了一部分（4 个孤岛），铺铜
    一上就整片连通。若在铺铜前下结论，会把一块能打板的板判成断开。

    这就是《AGENTS》§0.3 的教训：**用错的时机验，得到的是错的结论**。
    """
    from . import pcb_geo

    job = report.job("verify")
    live = report.project.get("boards") or []
    design_boards = design.board_list()
    if not live:
        job.status = "not_ready"
        job.note = "没有 PCB 可判定"
        return
    rows: list[dict[str, Any]] = []
    totals = {"nets": 0, "connected": 0, "split": 0}
    for index, board_live in enumerate(live):
        board = design_boards[index] if index < len(design_boards) else None
        name = str(board_live.get("name") or "")
        row: dict[str, Any] = {"board": name}
        rows.append(row)
        geo = _read_geometry(session, board_live)
        if geo is None:
            row["status"] = "not_ready"
            row["note"] = "读不回几何，连通性未判定（不是通过）"
            continue
        expected = _expected_nets(design, board) if board else []
        verdict = pcb_geo.connectivity(geo)
        row["status"] = verdict.get("status")
        row["checked"] = verdict.get("checked")
        row["connected"] = verdict.get("connected")
        row["split"] = verdict.get("split")
        row["single"] = verdict.get("single")
        row["basis"] = verdict.get("basis")
        row["counts"] = geo.to_dict()["counts"]
        if expected:
            # **单焊盘的网也要算**：设计说这条网有 ≥2 个焊盘、板上只剩 1 个，
            # 那是"少焊盘/漏网"，不是"没有这条网"。第一版把它排除掉，
            # 于是 verify 只判 1 条网还报 ok（bridge 那次就是这样）。
            connected_now = verdict.get("connected") or []
            extra = [n for n in expected if n not in connected_now]
            row["expected"] = expected
            row["not_connected_vs_design"] = extra
            row["single_in_design"] = [n for n in extra
                                       if n in (verdict.get("single") or [])]
        totals["nets"] += int(verdict.get("checked") or 0)
        totals["connected"] += len(verdict.get("connected") or [])
        totals["split"] += len(verdict.get("split") or [])

    report.verify_detail = {"boards": rows}
    job.counts = {"boards": len(rows), "nets": totals["nets"],
                  "connected": totals["connected"], "split": totals["split"],
                  "per_board": [{"board": r.get("board"), "status": r.get("status"),
                                 "split": r.get("split"),
                                 "not_connected_vs_design":
                                     r.get("not_connected_vs_design")}
                                for r in rows]}
    bad = [r for r in rows
           if r.get("status") != "ok" or r.get("not_connected_vs_design")]
    if not any(r.get("checked") for r in rows):
        job.status = "not_ready"
        job.note = "板上没有 ≥2 焊盘的网络，连通性无从判定（空输入不算通过）"
    elif bad:
        job.status = "not_ready"
        split_names = sorted({n for r in rows for n in (r.get("split") or [])})
        gaps = sorted({n for r in rows
                       for n in (r.get("not_connected_vs_design") or [])})
        job.note = (f"{totals['connected']}/{totals['nets']} 条网几何连通（含铺铜）；"
                    f"未连通：{split_names[:8]}"
                    + (f"；设计里有但板上没连上：{gaps[:8]}" if gaps else ""))
    else:
        job.status = "ok"
        job.note = (f"{totals['connected']}/{totals['nets']} 条网几何连通"
                    f"（含铺铜；{len(rows)} 块板）")


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
  // **整个工程也导一份**：API 建的工程现在只存在立创自己的库里，
  // 磁盘上没有 `.eprj3` 文件夹；这份 `.epro2` 是唯一可离线保存/交接的副本。
  ['project', () => eda.sys_FileManager.getProjectFileByProjectUuid(P.projectUuid, 'src')],
];
for (const [kind, make] of wanted) {
  const file = await attempt(kind, make);
  if (!file) continue;
  const rawName = file.name || (kind + P.ext[kind]);
  const want = P.ext[kind] || '';
  const record = {
    kind: kind,
    name: (want && rawName.toLowerCase().endsWith(want.toLowerCase()))
      ? rawName : (rawName + want),
    size: file.size || 0, saved: false};
  // 路线 1：直接写盘（要人在设置里开「外部交互」权限，否则报 EXTERNAL_PERMISSION）
  if (P.saveDir) {
    try {
      const ok = await eda.sys_FileSystem.saveFileToFileSystem(
        P.saveDir, file, record.name);
      record.saved = !!ok;
      if (!ok) record.saveError = 'saveFileToFileSystem 返回 false';
    } catch (e) {
      record.saveError = String((e && e.message) || e);
    }
  }
  // 路线 2：写不进去就把**字节**读回来，由 Python 落盘。
  // 实测 File 是浏览器 File 对象：`JSON.stringify` 得 {}，`await text()` 对
  // 二进制（Gerber zip / xlsx）没有意义 —— 必须走 arrayBuffer + base64。
  if (!record.saved && (record.size || 0) <= P.maxBytes) {
    try {
      if (typeof file.arrayBuffer === 'function') {
        const buf = await file.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let bin = '';
        const chunk = 0x8000;
        for (let i = 0; i < bytes.length; i += chunk) {
          bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
        }
        record.base64 = btoa(bin);
        record.bytes = bytes.length;
      }
    } catch (e) {
      record.readError = String((e && e.message) || e);
    }
  } else if (!record.saved) {
    record.tooBig = true;
  }
  files.push(record);
}
return {ok: true, steps: steps, files: files};
"""

_EXT = {"gerber": ".zip", "bom": ".xlsx", "pickplace": ".xlsx",
        "project": ".epro2", "step": ".step"}


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
    if save_dir:
        try:
            Path(save_dir).mkdir(parents=True, exist_ok=True)
        except OSError:
            save_dir = ""
    total_files = 0
    saved = 0
    blocked_reason = ""
    for board_index, board in enumerate(live):
        base = _sanitize_project_name(f"{report.project.get('name') or 'hwv'}-{board.get('pcbName') or 'pcb'}")
        ctx = {
            "pcbUuid": board.get("pcbUuid") or "",
            "basename": base,
            "saveDir": save_dir,
            # 读回字节的上限：超过就不读（base64 会撑爆 JSON，也会撑爆 CLI 的超时）
            "maxBytes": 8 * 1024 * 1024,
            # 工程备份每块板的循环都会跑一遍，但**只要一份**（多板是同一个工程）
            "projectUuid": (report.project.get("uuid") or "") if board_index == 0 else "",
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
            err = str(record.get("saveError") or "")
            wrote = ""
            # 路线 2 的落盘：JS 拿回 base64，Python 解码写文件。
            # **这条才是默认可用路径**：`saveFileToFileSystem` 要有外部交互权限。
            blob = record.get("base64")
            if not record.get("saved") and blob and save_dir:
                try:
                    data = base64.b64decode(str(blob))
                    target = Path(save_dir) / str(record.get("name") or "out.bin")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    record["saved"] = True
                    wrote = str(target)
                except Exception as exc:  # noqa: BLE001 - 写盘失败要落到报告里
                    record["readError"] = f"{type(exc).__name__}: {exc}"
            if record.get("saved"):
                saved += 1
                job.artifacts.append({
                    "kind": record.get("kind"),
                    "name": record.get("name"),
                    "size": record.get("size"),
                    "saved": True,
                    "path": wrote,
                })
            else:
                if err and not blocked_reason:
                    blocked_reason = err
                why = (err[:200] if err else
                       ("未给 save_dir，且字节读不回来" if not record.get("base64")
                        else "写盘失败"))
                if record.get("tooBig"):
                    why = "文件超过 8MB，没读回字节；给它 save_dir 走直接写盘"
                job.artifacts.append({
                    "kind": record.get("kind"),
                    "name": record.get("name"),
                    "size": record.get("size"),
                    "saved": False,
                    "why": why,
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


#: DRC 违规的**可信度分级**（手册 §18 的实测规律）。
#:
#: * `clearance` —— 纯几何计算，**可信**，可以当收敛判据
#: * `connection` —— 依赖 PCB 内部网表，实测**不认 API 创建的过孔**构成的跨层
#:   连接（手册 §18.1 的对照实验：按"是否用过孔"给网络分组，规律一目了然）
#: * `netlist` —— PCB 与原理图网表不匹配；手工摆放的 PCB 必然出现（§18.2）
#: * `physical` —— 过孔/线宽/环宽这类工艺规则
#: * `other` —— 认不出来的分组，一律进这里（**不冒充可信**）
_DRC_KINDS = (
    ("netlist", ("netlist", "net list", "mismatch", "未匹配")),
    ("connection", ("connection", "unconnected", "未连接", "开路")),
    ("clearance", ("clearance", "spacing", "distance", "间距", "短路")),
    ("physical", ("via", "hole", "annular", "物理", "线宽", "diameter")),
)


def _categorize(name: str) -> str:
    low = (name or "").lower()
    for kind, hints in _DRC_KINDS:
        if any(h in low for h in hints):
            return kind
    return "other"


def _drc_categories(detail: Any) -> dict[str, int]:
    """按类别数 DRC 违规。叶子（有 `explanation`）才计数，分组节点不算。"""
    out = {"clearance": 0, "connection": 0, "netlist": 0,
           "physical": 0, "other": 0}

    def walk(node: Any, kind: str = "other", depth: int = 0) -> None:
        if depth > 6 or node is None:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, kind, depth + 1)
            return
        if not isinstance(node, dict):
            return
        name = node.get("name")
        if isinstance(name, str) and name:
            # **只有认得出来的类别名才继承**。DRC 的分组树里网络名（GND/A1）
            # 也会是带 name 的节点，一律当成类别会把 parent 的
            # 「Connection Error」覆盖成 other —— 第一版就这么把 4 条
            # connection 违规全数成了 other（分类等于没做）。
            guess = _categorize(name)
            if guess != "other":
                kind = guess
        if "explanation" in node:
            out[kind] = out.get(kind, 0) + 1
            return
        if "list" in node:
            walk(node.get("list"), kind, depth + 1)

    walk(detail)
    return out


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
        categories = _drc_categories(value.get("drcDetail"))
        drc.append({
            "board": board.get("name"),
            "pass": bool(value.get("drcPass")),
            "violations": _count_violations(value.get("drcDetail")),
            "categories": categories,
            "trustworthy": categories["clearance"] + categories["netlist"]
                           + categories["physical"],
            "groups": _drc_groups(value.get("drcDetail")),
            "components": int(value.get("componentCount") or 0),
        })
    # 空板的 DRC 通过同样不算数（与 `_run_pcb` 同一条纪律）。
    usable = [r for r in drc if r.get("components")]
    trustworthy = sum(r.get("trustworthy") or 0 for r in usable)
    if not usable:
        drc_status = "not_ready"
        drc_note = "板上一块元件都没有，DRC 通过不算数（空板当然没有违规）"
    elif trustworthy:
        drc_status = "not_ready"
        drc_note = (f"DRC 报 {trustworthy} 条**可信类别**违规"
                    "（clearance/netlist/physical）")
    else:
        drc_status = "ok"
        drc_note = (
            "可信类别（clearance/netlist/physical）零违规；"
            f"另有 {sum(r['categories']['connection'] for r in usable)} 条 "
            "Connection Error —— 实测它不认 API 创建的过孔构成的跨层连接"
            "（手册 §18.1），连通性以几何判定为准（`pcb_geo.connectivity`）")
    return {
        "parity": parity_from_netlists(design, netlists),
        "drc": {"status": drc_status,
                "pass": all(r["pass"] for r in usable) if usable else False,
                # **可信判据**只算 clearance/netlist/physical；
                # connection 单列（手册 §18.1：它不认 API 过孔）
                "trustworthy_violations": sum(r.get("trustworthy") or 0
                                             for r in usable),
                "connection_violations": sum(r["categories"]["connection"]
                                             for r in usable),
                "checked": len(usable), "boards": drc,
                "note": drc_note or (
                    "connection 类违规不可信（不认 API 过孔），只看 "
                    "clearance/netlist/physical")},
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
    autolayout: bool = False,
    channel: str = "cli",
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
        if channel in ("auto", "bridge"):
            from . import lceda_channel

            try:
                session = lceda_channel.open_channel(channel, headless=True)
            except lceda_channel.ChannelError as exc:
                for name in wanted:
                    job = report.job(name)
                    job.status = "not_ready"
                    job.note = f"通道 {channel} 不可用：{exc}"
                report.notes.append(
                    f"通道 {channel} 不可用：{exc}。`channel=cli` 走 "
                    "`lceda-pro invoke`，`channel=bridge` 要在立创里点一次 "
                    "API Gateway → Reconnect")
                report.recompute_status()
                return report
            # Bridge 通道跟着**用户的窗口**走，不自己开窗；也没有 exe 可查
            if getattr(session, "kind", "") == "bridge":
                report.channel = {"kind": "bridge",
                                  "port": getattr(session, "port", 0),
                                  "window": getattr(session, "window_id", "")}
        else:
            session = lceda_mod.LcedaSession(exe=lceda_mod.find_exe())
    if getattr(session, "kind", "cli") != "bridge" and (
            not session.exe or not Path(session.exe).exists()):
        for name in wanted:
            job = report.job(name)
            job.status = "not_ready"
            job.note = "没有 lceda-pro 可执行文件"
        report.notes.append(
            f"设环境变量 {lceda_mod.EXE_ENV} 指向 lceda-pro.exe，或安装立创EDA专业版")
        report.recompute_status()
        return report

    try:
        if getattr(session, "kind", "cli") == "bridge":
            report.channel = {**(report.channel or {}),
                              "kind": "bridge",
                              "fallback": bool(getattr(session, "fallback_from", ""))}
            # **实测结论，写进报告而不是只留在脑子里**：
            # Bridge 每个 `/execute` 只有 30 秒，整条发射只在"窗口空闲"时
            # 跑通过一次（8 段 / 76s）；窗口里已有工程时 openProject/save 变慢，
            # 会接连撞 30 秒。重复发射就用 cli。
            report.notes.append(
                "Bridge 通道每个调用 ≤30 秒（实测整条发射 76s 跑通过一次）；"
                "窗口里已经开着工程时容易撞上限 —— 重复发射请用 --channel=cli")
        elif own_session or not session.session_id:
            session.open(path=session.project_path or "", headless=True)
            if own_session:
                report.channel = {"kind": "cli", "exe": session.exe,
                                  "fallback_from": getattr(session,
                                                           "fallback_from", "")}
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
        if "layout" in wanted:
            _run_layout(design, report, autolayout=autolayout)
        if not report.project.get("root"):
            report.notes.append(
                "工程没有落成磁盘文件夹（`getProjectsPaths` 被外部交互权限拦着）；"
                "`outputs` 段会把整个工程导出成 `.epro2` 当可离线保存的副本")
        report.stackup = stackup_spec.derive(design).to_dict()
        if "schematic" in wanted:
            _run_schematic(session, design, report, max_parts=max_parts)
        if "pcb" in wanted:
            _run_pcb(session, design, report)
        if "route" in wanted:
            _run_route(session, design, report, route=route)
        if "pour" in wanted:
            _run_pour(session, design, report)
        if "verify" in wanted:
            _run_verify(session, design, report)
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
            "route=native：可用（2/2 网布通、9 段 45° 单层、0 过孔、DRC 零可信违规）",
            "channel=cli 用 `lceda-pro invoke`（长任务）；channel=bridge 走用户窗口"
            "（30 秒硬上限，超时≠失败）",
            "layout 段按手册 §14.3/§17.19 查连接器出板与中心距；--autolayout 可重排",
            "route=ours：栅格 A*，8 邻域只出 0/45/90 度；换层才放过孔",
            "pour：双面 GND 铺铜 + 缝合过孔；只认 getCopperRegion() 有铜",
            "outputs：Gerber/BOM/坐标/`.epro2` 都能落盘（生成 File 后读字节，Python 写盘）",
            "DRC 的 Connection Error 不可信（不认 API 过孔），判据是 clearance/netlist/physical",
        ],
        "coordinate_caveat": (
            "PCB 单位是 mil（1/0.0254 mm）。板框 `R` 模式的 (x,y) 是**左上角**、"
            "矩形向 -Y 延伸 height —— 写反会让整块板落在板框外，布线 0 段且不报错。"
        ),
    }