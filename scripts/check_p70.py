"""P70 验收 —— 前端 P0/P1/P2（注册表 / 样式 / 板级汇总 / 发射时间线）。

    python -m hw_velxio.scripts.check_p70

## 判据

| 项 | 期望 |
|---|---|
| P0 注册表 | `features.ts` 是唯一事实源；store 不再手写 SECTIONS |
| P0 数据入口 | store 走 `createApi` / `QueryCache` |
| P0 样式 | tokens/layout/panels/dark 四分片；死样式脚本扫分片 |
| P1 Shell | 板级汇总条；三态计数含"未验证"；`openSection` 深链 |
| P2 Job | `emit(on_progress=...)` 每段回调；CLI 增量写 emit.json |
| P2 UI | EmitPanel 8 段时间线 + 每段"重跑"；jobs.ts 有 running 状态 |
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import emit_lceda  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "src" / "client"

_failures: list[str] = []
_checks = 0


def _safe(text: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(text).encode(encoding, "replace").decode(encoding, "replace")


def check(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if ok:
        print(_safe(f"[OK ] {name}"))
    else:
        _failures.append(name)
        print(_safe(f"[FAIL] {name}"))
    if detail:
        print(_safe(f"       {detail}"))


def _read(name: str) -> str:
    return (CLIENT / name).read_text(encoding="utf-8")


def part_p0() -> None:
    features = _read("features.ts")
    store = _read("store.ts")
    ids = re.findall(r"id: '([a-z_]+)'", features)
    check("A1. features.ts 收录全部 15 个分区",
          len(ids) == 15 and len(set(ids)) == 15, str(len(ids)))
    check("A2. store 不再手写 SECTIONS（从注册表派生 + 重导出）",
          "export const SECTIONS" not in store
          and "from './features.js'" in store)
    check("A3. store 走类型化 api 与查询缓存",
          "createApi" in store and "QueryCache" in store
          and "this.state.face" not in store.replace("if (this.state.face !== null) return", ""))
    styles = sorted((CLIENT / "styles").glob("*.ts"))
    check("A4. 样式四分片（tokens/layout/panels/dark）",
          {p.stem for p in styles} == {"tokens", "layout", "panels", "dark"},
          str([p.name for p in styles]))
    dead = (ROOT / "scripts" / "check_dead_css.py").read_text(encoding="utf-8")
    check("A5. 死样式脚本扫分片（不是只读 styles.ts）",
          '(CLIENT / "styles").glob("*.ts")' in dead)


def part_p1() -> None:
    view = _read("HwIdeView.tsx")
    store = _read("store.ts")
    check("B1. 板级汇总条 + 点击下钻",
          "hwv-boardstrip" in view and "hwv-board-chip" in view
          and "openSection('checks', board.firstRef)" in view)
    check("B2. 三态计数含「未验证」", "未验证" in view)
    check("B3. verdict 徽章把 unknown 算进来",
          "unknownChecks + (verdict?.counts" in view)
    check("B4. `openSection(section, ref)` 深链",
          "openSection(section: SectionId, ref = '')" in store)


def _design():
    from hw_velxio.design.model import Board, DesignModel, Part, Placement  # noqa: PLC0415
    board = Board(kind="custom", id="PCB#main", size_mm={"w": 40, "h": 30},
                  outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)])
    design = DesignModel(id="p70", board=board, boards=[board])
    design.parts = [Part(ref="R1", kind="resistor", physical_source="catalog",
                         size_mm={"w": 1.6, "h": 0.8, "d": 0.45}, pins=["1", "2"],
                         lcsc="C1",
                         placement=Placement(x_mm=5, y_mm=5, board_id="PCB#main"))]
    design.nets = [{"net": "GND", "class": "ground",
                    "pins": [{"ref": "R1", "pin": "2"}]}]
    return design


class _FakeSession:
    kind = "cli"
    exe = sys.executable
    session_id = "fake"
    project_uuid = "p"
    project_path = ""
    version = "fake"

    def open(self, **kwargs):
        return self

    def close(self, **kwargs):
        pass

    def projects_root(self):
        return ""


def part_p2() -> None:
    import inspect  # noqa: PLC0415
    params = inspect.signature(emit_lceda.emit).parameters
    check("C1. `emit(...)` 接受 on_progress", "on_progress" in params)
    cli = (ROOT / "design_cli.py").read_text(encoding="utf-8")
    check("C2. CLI 每段增量写 emit.json",
          "on_progress=lambda payload: store.write(" in cli)

    jobs_ts = _read("jobs.ts")
    check("C3. jobs.ts 提供 running 状态（useJob）",
          "class JobStore" in jobs_ts and "useJob" in jobs_ts)
    panels = _read("panels.tsx")
    check("C4. EmitPanel 8 段时间线 + 逐段重跑",
          "EMIT_STAGES = ['project', 'layout', 'schematic', 'pcb'," in panels
          and "onEmitRunStage" in panels and "hwv-emit-retry" in panels)
    view = _read("HwIdeView.tsx")
    check("C5. 视图把 onEmitRunStage / emitRunning 传进面板",
          "onEmitRunStage: (stage: string)" in view and "emitRunning: job.running" in view)

    # -- C6 行为：每段结束回调一次，回调抛错不改变结论 ------------------
    saved = {name: getattr(emit_lceda, name) for name in
             ("_run_project", "_run_layout")}
    try:
        def fake_project(session, design, report, *, name, reuse):
            report.job("project").status = "ok"
            report.project.update({"name": name, "uuid": "u"})

        def fake_layout(design, report, *, autolayout):
            report.job("layout").status = "ok"

        emit_lceda._run_project = fake_project
        emit_lceda._run_layout = fake_layout
        events: list[dict] = []
        report = emit_lceda.emit(_design(), jobs=["project", "layout"],
                                 session=_FakeSession(),
                                 on_progress=lambda payload: events.append(payload))
        check("C6. 每段回调一次（project → layout → 最终）",
              len(events) >= 3 and (report.job("project").status == "ok"),
              f"events={len(events)} status={report.status}")

        boom: list[int] = []

        def explode(payload: dict) -> None:
            boom.append(1)
            raise RuntimeError("progress 回调故意抛错")

        report2 = emit_lceda.emit(_design(), jobs=["project", "layout"],
                                  session=_FakeSession(), on_progress=explode)
        check("C7. 回调抛错**不改变**发射结论",
              len(boom) >= 1 and report2.job("project").status == "ok",
              f"boom={len(boom)} status={report2.status}")
    finally:
        for name, fn in saved.items():
            setattr(emit_lceda, name, fn)


def main() -> int:
    print("=== P70 前端 P0/P1/P2 ===")
    part_p0()
    part_p1()
    part_p2()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())