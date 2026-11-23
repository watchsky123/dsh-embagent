"""P64 验收 —— C 档：差异闭环 / 可选基线 / 成熟度 / ADR。

    python -m hw_velxio.scripts.check_p64

## 判据（**反着判**最要紧）

| 项 | 期望 |
|---|---|
| C1 差异 | 逐引脚对账：moved / eda_only / design_only / net 增删 / 件增删 |
| C1 回写 | 只做 moved 与 eda_only；**删除不自动做**；回写后过 validate |
| C1 空表 | EDA 网表为空 → `not_ready`（不是"没有差异 = ok"）|
| C2 基线 | 没记录 → `not_ready`；对比 `gate=false`；变差只报告 |
| C2 不绑定工程 | 命令只吃 `<id>`，代码里没有工程名 |
| C3 成熟度 | 六维只读本工程；没 req/spec → `not_ready`；约束 fail 不计分 |
| C4 ADR | 四节缺一 → `failed`；索引↔文件不一致 → `failed`；空决策拒收 |
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import adr as adr_module  # noqa: E402
from hw_velxio.design import baseline as baseline_module  # noqa: E402
from hw_velxio.design import maturity as maturity_module  # noqa: E402
from hw_velxio.design import readback_diff as diff_module  # noqa: E402
from hw_velxio.design.model import Board, DesignModel, Part, Placement  # noqa: E402
from hw_velxio.design.store import DesignStore  # noqa: E402

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


def _board(w: float = 40.0, h: float = 30.0) -> Board:
    return Board(kind="custom", name="b", id="PCB#main", size_mm={"w": w, "h": h},
                 outline=[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)])


def _design(pid: str = "p64") -> DesignModel:
    board = _board()
    design = DesignModel(id=pid, board=board, boards=[board])
    design.parts = [Part(ref="U1", kind="mcu", size_mm={"w": 7, "h": 7, "d": 1.4},
                         physical_source="datasheet", pins=["1", "2"],
                         placement=Placement(x_mm=5, y_mm=5, board_id="PCB#main"))]
    design.nets = [
        {"net": "3V3", "class": "power", "pins": [{"ref": "U1", "pin": "1"}]},
        {"net": "GND", "class": "power", "pins": [{"ref": "U1", "pin": "2"}]},
    ]
    return design


def _sandbox():
    root = Path(tempfile.mkdtemp(prefix="p64-ws-"))
    old = os.environ.get("HWV_WORKSPACE")
    os.environ["HWV_WORKSPACE"] = str(root)
    return root, old


def _restore(root: Path, old: str | None) -> None:
    if old is None:
        os.environ.pop("HWV_WORKSPACE", None)
    else:
        os.environ["HWV_WORKSPACE"] = old
    shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# C1 差异
# ---------------------------------------------------------------------------


def part_diff() -> None:
    design = _design()
    same = {(("U1", "1")): "3V3", (("U1", "2")): "GND"}
    clean = diff_module.diff(design, same)
    check("C1-1. 网表一致 → ok / 0 差异",
          clean["status"] == "ok" and clean["counts"].get("total") == 0,
          str(clean["counts"]))

    eda = {(("U1", "1")): "3V3", (("U1", "2")): "3V3", (("U2", "1")): "3V3"}
    payload = diff_module.diff(design, eda)
    kinds = {row["kind"] for row in payload["differences"]}
    check("C1-2. moved / eda_only / net 增删都能识别",
          {"pin_moved", "eda_only", "net_removed"} <= kinds
          and payload["counts"].get("pin_moved") == 1,
          str(payload["counts"]))

    applied = diff_module.apply(design, payload)
    moved = [row for row in applied["applied"] if row["kind"] == "pin_moved"]
    skipped = {row["kind"] for row in applied["skipped"]}
    pins_of_3v3 = [p for net in design.nets if net["net"] == "3V3"
                   for p in net["pins"]]
    check("C1-3. 回写只做 moved/eda_only；删除与新件留给人（skipped 里写原因）",
          moved and {"net_removed", "eda_only"} <= skipped
          and any(p["ref"] == "U1" and p["pin"] == "2" for p in pins_of_3v3),
          f"applied={applied['counts']} skipped={sorted(skipped)}")
    check("C1-4. 回写后设计仍能过 validate",
          not design.validate(), str(design.validate()[:3]))

    empty = diff_module.diff(design, {})
    check("C1-5. EDA 网表为空 → **not_ready**（不是“没有差异 = ok”）",
          empty["status"] == "not_ready", str(empty.get("reason")))


# ---------------------------------------------------------------------------
# C2 基线（可选，不判失败）
# ---------------------------------------------------------------------------


def part_baseline() -> None:
    sandbox, old = _sandbox()
    try:
        store = DesignStore("p64-base")
        store.delete()
        design = _design("p64-base")
        store.save(design)
        miss = baseline_module.compare(store, design)
        check("C2-1. 没记录基线 → not_ready（没记录 ≠ 没退化）",
              miss["status"] == "not_ready" and "基线" in str(miss.get("reason")),
              str(miss.get("reason")))

        baseline_module.record(store, design)
        same = baseline_module.compare(store, design)
        check("C2-2. 记录后对比：`gate=false`、无 regression",
              same["status"] == "ok" and same["gate"] is False
              and not same["regressions"],
              str(same["counts"]))

        thinner = _design("p64-base")
        thinner.nets = thinner.nets[:1]
        thinner.parts = thinner.parts[:1]
        worse = baseline_module.compare(store, thinner)
        metrics = {row["metric"] for row in worse["regressions"]}
        check("C2-3. 规模变小 / 阶段变差 → regression 只报告，status 仍 ok",
              {"nets", "parts"} & metrics and worse["status"] == "ok"
              and worse["gate"] is False,
              str(worse["counts"]))
        store.delete()
    finally:
        _restore(sandbox, old)


# ---------------------------------------------------------------------------
# C3 成熟度
# ---------------------------------------------------------------------------


def part_maturity() -> None:
    sandbox, old = _sandbox()
    try:
        from hw_velxio import design_cli  # noqa: PLC0415
        store = DesignStore("p64-mat")
        store.delete()
        store.save(_design("p64-mat"))
        empty = maturity_module.compute(store, store.load())
        dims0 = {row["id"]: row["status"] for row in empty["dimensions"]}
        check("C3-1. 没有 req/spec → **not_ready**（谈成熟度没意义）",
              empty["status"] == "not_ready"
              and dims0["requirements"] == dims0["spec"] == "missing",
              f"level={empty['level']} dims={dims0}")

        store.write("req.json", {"id": "p64-mat", "title": "t",
                                 "roles": ["mcu"], "interfaces": [],
                                 "constraints": {}, "numbers": {}, "notes": ""})
        cmd = design_cli.cmd_spec(["p64-mat", "--build"])
        store.write("constraints.json", {"counts": {"pass": 2, "fail": 0,
                                                    "unknown": 1}})
        payload = maturity_module.compute(store, store.load())
        dims = {row["id"]: row["status"] for row in payload["dimensions"]}
        check("C3-2. 六维都给出状态；需求/规格/约束 pass，成熟度 ≥3",
              len(payload["dimensions"]) == 6 and payload["status"] == "ok"
              and dims["requirements"] == dims["spec"] == dims["constraints"] == "pass"
              and payload["level"] >= 3,
              f"level={payload['level']} dims={dims}")

        store.write("constraints.json", {"counts": {"pass": 1, "fail": 2,
                                                    "unknown": 0}})
        failed = maturity_module.compute(store, store.load())
        bad = {row["id"]: row for row in failed["dimensions"]}
        check("C3-3. 约束有 fail → 该维 fail、**不计入 level**（比上一档少 1）",
              bad["constraints"]["status"] == "fail"
              and failed["level"] == payload["level"] - 1,
              f"level={failed['level']}（上一档 {payload['level']}）")
        store.delete()
    finally:
        _restore(sandbox, old)


# ---------------------------------------------------------------------------
# C4 ADR
# ---------------------------------------------------------------------------


def part_adr() -> None:
    sandbox, old = _sandbox()
    try:
        root = sandbox / "p64-adr"
        root.mkdir(parents=True, exist_ok=True)
        got = adr_module.add(root, title="电源选型", decision="用 LDO",
                             why="电流 300mA，压差 1.2V", alternatives="DCDC：效率高但贵",
                             evidence="runbook §21 实测温升")
        listing = adr_module.list_adrs(root)
        check("C4-1. add 写文件 + 更新索引（编号 0001）",
              got["id"] == "0001" and listing and listing[0]["id"] == "0001",
              str(listing))
        valid = adr_module.validate(root)
        check("C4-2. 四节齐全 → validate ok",
              valid["status"] == "ok" and not valid["problems"],
              str(valid["counts"]))

        path = Path(got["file"])
        text = path.read_text(encoding="utf-8")
        path.write_text(text.split("## 证据")[0], encoding="utf-8")
        broken = adr_module.validate(root)
        check("C4-3. 缺一节（证据）→ **failed**",
              broken["status"] == "failed"
              and any("证据" in p for p in broken["problems"]),
              str(broken["problems"]))

        try:
            adr_module.add(root, title="空决策", decision="", why="")
            raised = False
        except ValueError:
            raised = True
        check("C4-4. 空决策/理由 → ValueError（不收口号）", raised)

        from hw_velxio import design_cli  # noqa: PLC0415
        store = DesignStore("p64-adr-cli")
        store.delete()
        store.save(_design("p64-adr-cli"))
        cli = design_cli.cmd_adr(["p64-adr-cli", "add", "--title=测试",
                                  "--decision=", "--why="])
        check("C4-5. CLI 空决策 → failed（不写文件）",
              cli["status"] == "failed", str(cli.get("reason")))
        store.delete()
    finally:
        _restore(sandbox, old)


def main() -> int:
    print("=== P64 C 档：差异闭环 / 可选基线 / 成熟度 / ADR ===")
    part_diff()
    part_baseline()
    part_maturity()
    part_adr()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())