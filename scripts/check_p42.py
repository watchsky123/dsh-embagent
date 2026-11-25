"""P42 验收 —— 关节提案（`joints-propose`）。

    python -m dsh_embagent.scripts.check_p42

## 这一段补的是什么

`joints` 对 `hinge`/`axle`/`slide`/`gear` 缺参数**一律报 error，不编默认值**
（纪律，不能改）。但没有机械关系的半成品什么也验不了。

所以插一层**提案**：有几何依据的算出来（每条带 `evidence`），没依据的写进
`unresolved` 并说清要补什么。`--apply` 才写进 `design.joints_list`，
`source` 保持 `proposed`。

## 判据

| 项 | 期望 |
|---|---|
| 有依据的 | 进提案，`source=proposed` 且**每条有 evidence** |
| 推不出的 | **不进提案**，进 `unresolved` 并写 `need`（不编数字）|
| `joints` 老行为 | **一字不改**：缺 `<range_deg>` 仍报 error |
| `--apply` 之后 | `joints` 能过、`mjcf` 能出关节（提案真的进得了物理模型）|

退化验证：让 proposer 在缺 `hinge_deg` 时编一个 ±180，用例 2a 必须变红。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent import design_cli  # noqa: E402
from dsh_embagent.design import joint_propose as propose_module  # noqa: E402
from dsh_embagent.design import joints as joints_module  # noqa: E402
from dsh_embagent.design import mjcf as mjcf_module  # noqa: E402
from dsh_embagent.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from dsh_embagent.design.store import DesignStore  # noqa: E402

PROJECT = "p42-fixture"

_failures: list[str] = []
_checks = 0


def _safe(text: str) -> str:
    """控制台是 GBK 时，任何一条 detail 都不该让整段验收崩掉。"""
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


# ---------------------------------------------------------------------------
# 夹具：一块板 + 叠放一对 + 舵机/摆臂（有 unit）+ 可选 limits
# ---------------------------------------------------------------------------


def _design(*, with_limits: bool = True, with_gear: bool = False) -> DesignModel:
    d = DesignModel(
        id="p42", title="p42 fixture",
        board=Board(kind="generic", name="base",
                    outline=[(0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0)],
                    size_mm={"w": 60, "h": 40}, thickness_mm=1.6),
    )
    d.add_part(Part(ref="BASE", kind="pcb", label="base pcb",
                    size_mm={"w": 20, "h": 20, "d": 2},
                    physical_source="catalog",
                    placement=Placement(x_mm=10, y_mm=10, side="top")))
    d.add_part(Part(ref="TOP", kind="pcb", label="top pcb",
                    size_mm={"w": 20, "h": 20, "d": 2},
                    physical_source="catalog",
                    placement=Placement(x_mm=10, y_mm=10, z_mm=5, side="top")))
    d.add_part(Part(ref="SERVO", kind="servo", label="mg90s", unit="gimbal",
                    size_mm={"w": 15, "h": 15, "d": 10},
                    physical_source="catalog",
                    placement=Placement(x_mm=40, y_mm=10, side="top")))
    d.add_part(Part(ref="ARM", kind="link", role="link", label="arm", unit="gimbal",
                    size_mm={"w": 15, "h": 15, "d": 3},
                    physical_source="proposed",
                    placement=Placement(x_mm=40, y_mm=10, side="top")))
    if with_gear:
        d.add_part(Part(ref="GEAR", kind="gear", label="spur", unit="gimbal",
                        size_mm={"w": 10, "h": 10, "d": 3},
                        physical_source="proposed",
                        placement=Placement(x_mm=40, y_mm=10, side="top")))
    if with_limits:
        d.mechanical = {"limits": {"hinge_deg": [-45.0, 45.0]}}
    return d


def _kinds(report: dict) -> dict[str, int]:
    return dict(report.get("counts") or {})


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def part_rules() -> None:
    report = propose_module.propose(_design())
    kinds = _kinds(report)
    proposals = report.get("proposals") or []
    check("1a. 有依据的关系进了提案（stack / axle / hinge 各至少一条）",
          kinds.get("stack", 0) >= 1 and kinds.get("axle", 0) >= 1
          and kinds.get("hinge", 0) >= 1,
          f"counts={kinds}")
    check("1b. 每条提案都是 `source=proposed` 且有 evidence（能复算）",
          proposals and all(p.get("source") == "proposed"
                            and str(p.get("evidence") or "").strip() != ""
                            for p in proposals),
          f"n={len(proposals)} example={str((proposals or [{}])[0].get('evidence'))[:90]}")
    axle = next((p for p in proposals if p["kind"] == "axle"), {})
    check("1c. axle 的轴径来自几何（=轴类零件短边 15.0）",
          abs(float((axle.get("params") or {}).get("diameter_mm", 0)) - 15.0) < 0.01,
          f"params={axle.get('params')}")


def part_no_limit() -> None:
    report = propose_module.propose(_design(with_limits=False))
    kinds = _kinds(report)
    needs = {row.get("need") for row in (report.get("unresolved") or [])}
    check("2a. 没给 `hinge_deg` → **不提** hinge，而是进 unresolved 并写 need",
          kinds.get("hinge", 0) == 0 and "hinge_deg" in needs,
          f"counts={kinds} needs={sorted(n for n in needs if n)}")

    report2 = propose_module.propose(_design(with_gear=True))
    needs2 = {row.get("need") for row in (report2.get("unresolved") or [])}
    gears = [p for p in (report2.get("proposals") or []) if p["kind"] == "gear"]
    check("2b. 齿轮一律不提案（齿数不在模型里 → 不编 ratio）",
          gears == [] and "gear_ratio" in needs2,
          f"gear_proposals={len(gears)} needs={sorted(n for n in needs2 if n)}")


def part_joints_unchanged() -> None:
    design = _design()
    design.joints_list = [{"a": "SERVO", "b": "ARM", "kind": "hinge",
                           "source": "declared",
                           "params": {"axis": [0, 0, 1]}}]   # 故意缺 range_deg
    report = joints_module.report(design)
    errors = [f for f in (report.get("findings") or [])
              if f.get("check") == "joint.params" and f.get("severity") == "error"]
    check("3a. `joints` 老行为不变：hinge 缺 range_deg 仍报 error（不编默认值）",
          report.get("status") != "ok" and len(errors) >= 1,
          f"status={report.get('status')} errors={len(errors)}")
    check("3b. 提案不碰 design.json 的 joints_list（默认只写提案文件）",
          len(design.joints_list) == 1
          and design.joints_list[0]["source"] == "declared")


def part_apply_and_mjcf() -> None:
    store = DesignStore(PROJECT)
    store.save(_design())
    try:
        got = design_cli.cmd_joints_propose([PROJECT])
        proposal_file = store.path("joints.proposed.json")
        check("4a. 默认动作写出 `joints.proposed.json`（且没动 joints.json）",
              proposal_file.is_file() and not store.path("joints.json").exists(),
              f"proposals={len(got.get('proposals') or [])}")

        applied = design_cli.cmd_joints_propose([PROJECT, "--apply"])
        design = store.load()
        proposed = [j for j in design.joints_list
                    if str(j.get("source")) == "proposed"]
        check("4b. `--apply` 把提案写进 joints_list，source 仍是 proposed",
              len(proposed) >= 3 and len(applied.get("applied") or []) >= 3,
              f"proposed={len(proposed)} applied={len(applied.get('applied') or [])}")

        report = joints_module.report(design)
        check("4c. 应用之后 `joints` 能过（参数真的齐了）",
              report.get("status") == "ok",
              f"status={report.get('status')} findings={len(report.get('findings') or [])}")

        built = mjcf_module.build(design)
        load = mjcf_module.load_check(xml=built.xml)
        check("4d. 提案真的进得了物理模型：MJCF 有 hinge 关节且 MuJoCo 编译通过",
              load.get("status") == "ok" and int(load.get("njnt") or 0) >= 1,
              f"njnt={load.get('njnt')} skipped={len(built.skipped)}")
    finally:
        store.delete()


def part_degeneration() -> None:
    """让 proposer 在缺 hinge_deg 时编一个 ±180，2a 必须变红。"""
    original = propose_module._lim_deg

    def always_default(design):  # noqa: ANN001
        return original(design) or [-180.0, 180.0]

    baseline = _kinds(propose_module.propose(_design(with_limits=False))).get("hinge", 0)
    propose_module._lim_deg = always_default  # type: ignore[assignment]
    try:
        degenerated = _kinds(
            propose_module.propose(_design(with_limits=False))).get("hinge", 0)
    finally:
        propose_module._lim_deg = original  # type: ignore[assignment]
    check("5. 退化验证：缺 limits 时编 ±180 → 2a 必须变红（证明'不猜'是真的）",
          baseline == 0 and degenerated >= 1,
          f"baseline={baseline} degenerated={degenerated}")


def main() -> int:
    print("=== P42 关节提案（joints-propose） ===")
    part_rules()
    part_no_limit()
    part_joints_unchanged()
    part_apply_and_mjcf()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(f"  [FAIL] {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())