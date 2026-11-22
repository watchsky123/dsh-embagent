"""P44 验收 —— 机械判据（干涉 / 重心 / 舵机扭矩 / 关节行程）并入 handoff。

    python -m hw_velxio.scripts.check_p44

## 这一段补的是什么

之前机械域**一条判据都没有**：`joints` 只查参数给没给，`mjcf` 只查能不能
编译。所以"两块板插在一起""重心在支撑外""9g 舵机推 300g 摆臂"这三类
必炸的问题全靠看图。

## 判据（三态，措辞必须不同）

| 项 | 期望 |
|---|---|
| 实体重叠 | `fail`，且指到两个位号 |
| 重心在支撑外 | `fail` |
| 缺舵机扭矩 | **`unknown`**（不是 pass 也不是 fail），并算出"要多少扭矩" |
| 行程不够 | `fail`；没给需求行程 → `unknown` |
| 并入交接单 | `fail`→blocking、`unknown`→missing_data，**pass 不报** |

退化验证：把重心判据换成恒 `pass`，用例 2 必须变红。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import handoff as handoff_module  # noqa: E402
from hw_velxio.design import mech_check  # noqa: E402
from hw_velxio.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from hw_velxio.design.store import DesignStore  # noqa: E402

PROJECT = "p44-fixture"

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


def _board() -> Board:
    return Board(kind="generic", name="b",
                 outline=[(0.0, 0.0), (80.0, 0.0), (80.0, 40.0), (0.0, 40.0)],
                 size_mm={"w": 80, "h": 40}, thickness_mm=1.6)


def _part(ref: str, kind: str, x: float, y: float, w: float, h: float, d: float,
          *, z: float = 0.0, mass: float | None = None,
          electrical: dict | None = None) -> Part:
    return Part(ref=ref, kind=kind, label=ref,
                size_mm={"w": w, "h": h, "d": d},
                physical_source="catalog", mass_g=mass,
                electrical=dict(electrical or {}),
                placement=Placement(x_mm=x, y_mm=y, z_mm=z, side="top"))


def _by_check(report: dict, name: str) -> list[dict]:
    return [c for c in (report.get("checks") or []) if c.get("check") == name]


# ---------------------------------------------------------------------------
# 1. 实体重叠
# ---------------------------------------------------------------------------


def part_interference() -> None:
    d = DesignModel(id="p44-overlap", board=_board())
    d.add_part(_part("A1", "ic", 10, 10, 10, 10, 2, mass=5))
    d.add_part(_part("A2", "ic", 12, 12, 10, 10, 2, mass=5))
    report = mech_check.report(d)
    rows = _by_check(report, "interference")
    fails = [r for r in rows if r["status"] == "fail"]
    check("1a. 两体重叠 → `fail`，且指到两个位号",
          bool(fails) and set(fails[0].get("refs") or []) == {"A1", "A2"},
          f"rows={[(r['status'], r.get('refs')) for r in rows]}")
    check("1b. 重叠的 fix 指向 `fix_interference`（交接单上是有按钮的）",
          bool(fails) and (fails[0].get("fix") or {}).get("need") == "fix_interference",
          f"fix={fails[0].get('fix') if fails else None}")

    clean = DesignModel(id="p44-clean", board=_board())
    clean.add_part(_part("B1", "ic", 10, 10, 10, 10, 2, mass=5))
    clean.add_part(_part("B2", "ic", 30, 10, 10, 10, 2, mass=5))
    rows2 = _by_check(mech_check.report(clean), "interference")
    check("1c. 不重叠就 `pass`（不误报）",
          all(r["status"] == "pass" for r in rows2),
          f"{[(r['status'], r.get('message')) for r in rows2]}")


# ---------------------------------------------------------------------------
# 2. 重心
# ---------------------------------------------------------------------------


def _cog_fixture() -> DesignModel:
    d = DesignModel(id="p44-cog", board=_board())
    # 支撑层：小底板（z=0，4×4）
    d.add_part(_part("BASE", "bracket", 10, 10, 4, 4, 1, mass=5))
    # 远处的高处重物（z=5）→ 重心被拉出支撑盒
    d.add_part(_part("MAST", "ballast", 60, 10, 6, 6, 2, z=5.0, mass=50))
    return d


def part_cog() -> None:
    rows = _by_check(mech_check.report(_cog_fixture()), "center_of_gravity")
    fails = [r for r in rows if r["status"] == "fail"]
    check("2a. 重心落在支撑盒外 → `fail`（会倾覆）",
          bool(fails) and "支撑" in str(fails[0].get("message")),
          f"{[(r['status'], r.get('message')) for r in rows]}")
    check("2b. 重心判据带 basis（质量来源 + 支撑取哪一层）",
          bool(rows) and "支撑" in str(rows[0].get("basis")),
          f"basis={rows[0].get('basis') if rows else None}")


# ---------------------------------------------------------------------------
# 3. 舵机扭矩 / 关节行程
# ---------------------------------------------------------------------------


def _servo_fixture(*, with_torque: bool, with_travel: bool = True) -> DesignModel:
    d = DesignModel(id="p44-servo", board=_board())
    d.add_part(_part("SERVO", "servo", 10, 10, 15, 15, 10, mass=9,
                     electrical=({"stall_torque_kgcm": 1.2}
                                 if with_torque else {})))
    d.add_part(_part("ARM", "link", 30, 10, 20, 3, 3, mass=20))
    d.joints_list = [{
        "a": "SERVO", "b": "ARM", "kind": "hinge", "source": "declared",
        "params": {"axis": [0, 0, 1], "range_deg": [0, 90]},
    }]
    if with_travel:
        d.mechanical = {"limits": {"required_deg": [0, 90]}}
    return d


def part_torque() -> None:
    rows = _by_check(mech_check.report(_servo_fixture(with_torque=False)),
                     "servo_torque")
    unknown = [r for r in rows if r["status"] == "unknown"]
    check("3a. 没有标称扭矩 → **`unknown`**（不是 pass，也不是 fail）",
          bool(unknown) and not [r for r in rows if r["status"] in ("pass", "fail")],
          f"{[(r['status'], r.get('message')) for r in rows]}")
    check("3b. 仍然算出「要多少扭矩」（人要补的是标称值，不是需求值）",
          bool(unknown) and float(unknown[0].get("required_kgcm") or 0) > 0,
          f"required_kgcm={unknown[0].get('required_kgcm') if unknown else None}")

    ok = _by_check(mech_check.report(_servo_fixture(with_torque=True)),
                   "servo_torque")
    check("3c. 标称够用 → `pass`（1.2 kg·cm × 0.5 = 0.6 ≥ 需求）",
          all(r["status"] == "pass" for r in ok),
          f"{[(r['status'], r.get('message')) for r in ok]}")


def part_range() -> None:
    no_travel = _by_check(mech_check.report(
        _servo_fixture(with_torque=True, with_travel=False)), "joint_range")
    check("4a. 没给需求行程 → `unknown`（不知道要转多大，就不能说够不够）",
          all(r["status"] == "unknown" for r in no_travel),
          f"{[(r['status'], r.get('message')) for r in no_travel]}")
    enough = _by_check(mech_check.report(_servo_fixture(with_torque=True)),
                       "joint_range")
    check("4b. 需求 90°、关节 0..90 → `pass`",
          all(r["status"] == "pass" for r in enough),
          f"{[(r['status'], r.get('message')) for r in enough]}")

    tight = _servo_fixture(with_torque=True)
    tight.mechanical = {"limits": {"required_deg": [0, 180]}}
    rows = _by_check(mech_check.report(tight), "joint_range")
    check("4c. 需求 180° 而关节只有 90° → `fail`",
          any(r["status"] == "fail" for r in rows),
          f"{[(r['status'], r.get('message')) for r in rows]}")


# ---------------------------------------------------------------------------
# 5. 并入 handoff
# ---------------------------------------------------------------------------


def part_handoff() -> None:
    store = DesignStore(PROJECT)
    design = _cog_fixture()
    design.joints_list = []
    store.save(design)
    try:
        report = mech_check.report(design)
        store.write("mech.json", report)
        payload = handoff_module.build(store).to_dict()
        items = payload.get("items") or []
        mech_items = [i for i in items if str(i.get("title") or "").startswith("机械·")]
        cats = {i.get("category") for i in mech_items}
        check("5a. `fail` 进 `blocking`（重心那一条）",
              "blocking" in cats,
              f"cats={sorted(cats)} n={len(mech_items)}")
        check("5b. `pass` 不进交接单（只列待办）",
              all(str(i.get("status")) != "pass" for i in mech_items),
              f"{[i.get('status') for i in mech_items]}")
        check("5c. 机械条目带可点动作（next 不是空的）",
              all(bool(i.get("next")) for i in mech_items),
              f"没有 next 的：{[i.get('title') for i in mech_items if not i.get('next')]}")
        check("5d. `mech.json` 进了 sources（指纹），并计入 checked",
              payload.get("sources", {}).get("mech.json") not in (None, "")
              and int((payload.get("checked") or {}).get("mech") or 0) > 0,
              f"sources.mech={payload.get('sources', {}).get('mech.json')} "
              f"checked.mech={(payload.get('checked') or {}).get('mech')}")
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 6. 退化
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    """把重心判据换成恒 `pass`，2a 必须变红。"""
    original = mech_check._center_of_gravity

    def always_pass(design):  # noqa: ANN001
        return [{"check": "center_of_gravity", "status": "pass", "refs": [],
                 "message": "退化版：永远通过", "basis": "", "fix": {}}]

    baseline = [r for r in _by_check(mech_check.report(_cog_fixture()),
                                     "center_of_gravity")
                if r["status"] == "fail"]
    mech_check._center_of_gravity = always_pass  # type: ignore[assignment]
    try:
        degenerated = [r for r in _by_check(mech_check.report(_cog_fixture()),
                                            "center_of_gravity")
                       if r["status"] == "fail"]
    finally:
        mech_check._center_of_gravity = original  # type: ignore[assignment]
    check("6. 退化验证：重心判据恒 pass → 2a 必须变红（证明它真的在算）",
          bool(baseline) and not degenerated,
          f"baseline_fail={len(baseline)} degenerated_fail={len(degenerated)}")


def main() -> int:
    print("=== P44 机械判据 ===")
    part_interference()
    part_cog()
    part_torque()
    part_range()
    part_handoff()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())