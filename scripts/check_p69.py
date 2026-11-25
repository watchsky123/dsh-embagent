"""P69 验收 —— PCB 导入"没导全"不许报 ok。

    python -m dsh_embagent.scripts.check_p69

## 背景（实测）

示例一体-新2：schematic 43 器件 / 130 引线，pcb 段报 **ok** 但
`components_on_pcb=0`、parity `checked=0`。空板被当成了通过（AGENTS §0.1）。

## 判据

| 项 | 期望 |
|---|---|
| 导入后 0 件 | `not_ready`，说「0/N」|
| 导入后部分 | `not_ready`，说「x/N」|
| 全导入 | `ok`（不误报）|
| 轮询 | 按件数放大（20→40 秒上限）|
| 接线 | `_run_pcb` 真的用这两条（不是只写了函数）|
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import emit_lceda  # noqa: E402
from dsh_embagent.design.model import Board, DesignModel, Part, Placement  # noqa: E402

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


def part_helpers() -> None:
    check("A1. 轮询按件数放大：3 件 43 次（21.5s）、43 件 80 次（40s）",
          emit_lceda._poll_tries(3) == 43 and emit_lceda._poll_tries(43) == 80,
          f"{emit_lceda._poll_tries(3)} / {emit_lceda._poll_tries(43)}")
    check("A2. 0 件 → not_ready（说 0/N）",
          emit_lceda._import_verdict(0, 43)["status"] == "not_ready"
          and "0/43" in emit_lceda._import_verdict(0, 43)["note"],
          emit_lceda._import_verdict(0, 43)["note"])
    check("A3. 部分 → not_ready；全导 → ok",
          emit_lceda._import_verdict(20, 43)["status"] == "not_ready"
          and emit_lceda._import_verdict(43, 43)["status"] == "ok",
          str(emit_lceda._import_verdict(20, 43)))


def _design() -> DesignModel:
    board = Board(kind="custom", id="PCB#main", name="B",
                  size_mm={"w": 40, "h": 30},
                  outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)])
    design = DesignModel(id="p69", board=board, boards=[board])
    design.parts = [
        Part(ref=f"R{i + 1}", kind="resistor", physical_source="catalog",
             size_mm={"w": 1.6, "h": 0.8, "d": 0.45}, pins=["1", "2"],
             lcsc="C1",
             placement=Placement(x_mm=5 + i * 4, y_mm=5, board_id="PCB#main"))
        for i in range(2)]
    design.nets = [{"net": "GND", "class": "ground",
                    "pins": [{"ref": p.ref, "pin": "2"} for p in design.parts]}]
    return design


class _FakeSession:
    """只回 `_run_pcb` 会问的那几种结果。"""

    kind = "cli"
    exe = ""
    session_id = "fake"
    project_uuid = "p"
    project_path = ""
    version = "fake"

    def __init__(self, refs_on_board: list[str]):
        self.refs_on_board = list(refs_on_board)

    def invoke(self, script, *, expect=None, label="", timeout_ms=0, **kwargs):
        if label.endswith("/finish"):
            return {"saved": True, "netlist": "", "drcPass": True,
                    "drcDetail": {}, "dfmAvailable": False, "dfm": None,
                    "dfmError": ""}
        if "/move/" in label:
            return {"moved": [{"ref": "R1"}], "missing": []}
        if "/setup" in label:
            return {"steps": [{"step": "importChanges", "ok": True}],
                    "outlineOk": True,
                    "onBoard": {ref: 1 for ref in self.refs_on_board},
                    "componentCount": len(self.refs_on_board),
                    "missingExpected": []}
        return {"steps": [], "status": "ok", "applied": []}


def _report(design: DesignModel):
    report = emit_lceda.EmitReport()
    report.project = {"name": "B", "boards": [
        {"name": "B", "pcbUuid": "pcb1", "schematicUuid": "sch1"}]}
    report.placed_refs = [p.ref for p in design.parts]
    report.schematic_detail = {"boards": [{"board": "B", "placed": 2,
                                          "saved": True}]}
    return report


def part_behavior() -> None:
    design = _design()
    empty = _report(design)
    emit_lceda._run_pcb(_FakeSession([]), design, empty)
    job = empty.job("pcb")
    check("B1. 导入 0 件 → pcb 段 **not_ready**（不是 ok）",
          job.status == "not_ready" and "0/2" in str(job.note),
          f"{job.status} | {job.note}")
    check("B2. counts 里记着期望件数（expected_on_pcb=2）",
          int(job.counts.get("expected_on_pcb") or 0) == 2,
          str(job.counts.get("expected_on_pcb")))

    half = _report(design)
    emit_lceda._run_pcb(_FakeSession(["R1"]), design, half)
    check("B3. 导入一半 → not_ready，说「1/2」",
          half.job("pcb").status == "not_ready"
          and "1/2" in str(half.job("pcb").note),
          str(half.job("pcb").note))

    full = _report(design)
    emit_lceda._run_pcb(_FakeSession(["R1", "R2"]), design, full)
    note = str(full.job("pcb").note)
    check("B4. 全导入 → 不因“导入不足”报错（note 里没有 0/2、1/2）",
          "0/2" not in note and "1/2" not in note, note)


def part_wiring() -> str:
    path = Path(emit_lceda.__file__)
    src = path.read_text(encoding="utf-8")
    start = src.index("def _run_pcb(")
    body = src[start:start + 20000]
    return body


def main() -> int:
    print("=== P69 PCB 导入完整性 ===")
    part_helpers()
    part_behavior()
    body = part_wiring()
    check("C1. `_run_pcb` 里真的用了 `_import_verdict`（不是只写函数）",
          "_import_verdict(" in body, "调用点存在")
    check("C2. `_run_pcb` 的收尾会用 `expected_on_pcb` 再判一次",
          "expected_on_pcb" in body and "comps_total < expected_total" in body)
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())