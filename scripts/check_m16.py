"""P16 验收：MuJoCo 后端 + 全量参数表 + 域分派。

从仓库根运行：

    python -m hw_velxio.scripts.check_m16

## 两条止损线（计划里写死的）

1. **单件落地**：100g 方块从 0.1m 落下，停在半边长附近（不穿地、不弹飞）
2. **四旋翼悬停**：推力 = 重力时高度保持

**任一不通就停，不接 P18 的运动域。** 这个脚本会先跑这两条，
不过就直接返回 1，后面的断言不掩盖它们。

## 其余守的六件事

3. **参数表全量**：136 项都在、每个有 group/unit/note、分组合法
4. **通用性标记**：`universal=True/False` 的划分（31 通用 + 105 专用）
5. **diff 能识别"从 0 变非 0"** —— 这是 RoboBuilder 0/15 的根因，
   我们的 `turned_on` 分类就是为它做的
6. **闸门**：`inferred` 关节不进 MJCF；缺参数/缺尺寸的进 skipped 且说明
7. **确定性**：同一模型跑两次，结束位姿逐位相等
8. **单位**：mm → m 的转换（漏过一次，所有零件位置放大 1000 倍）

## 为什么"跳过"也要断言

`skipped` 为空才算"全部零件进了仿真"。一个只仿真了 3/141 个零件的
模型如果报 `ok`，就是假通过。所以每条跳过路径都要有一个夹具。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hw_velxio.design import (mjcf, mujoco_backend,   # noqa: E402
                              sim_dispatch, sim_params)
from hw_velxio.design.model import (Board, DesignModel, Joint,   # noqa: E402
                                    Part, Placement)


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}{f' — {detail}' if detail else ''}")
            self.items.append(name)


def _design(parts: list[Part], **kw) -> DesignModel:
    design = DesignModel(
        id="fixture", title="P16 夹具",
        board=Board(kind="custom", size_mm={"w": 200, "h": 200}))
    design.parts = parts
    design.nets = kw.pop("nets", [])
    return design


def _box(ref: str, *, size: float = 20.0, z: float = 100.0,
         mass_g: float | None = 100.0, x: float = 0.0,
         y: float = 0.0) -> Part:
    return Part(
        ref=ref, kind="box", label=ref,
        size_mm={"w": size, "h": size, "d": size},
        physical_source="datasheet",
        placement=Placement(x_mm=x, y_mm=y, z_mm=z),
        mass_g=mass_g,
    )


# ---------------------------------------------------------------------------
# 止损线
# ---------------------------------------------------------------------------

def check_stop_lines(f: Failures) -> bool:
    print("== 止损线 1：单件落地 ==")

    result = mujoco_backend.drop_test(size_mm=20.0, mass_g=100.0,
                                      start_z_mm=100.0, duration_s=1.0)
    if not result.ok:
        f.check("单件落地能跑起来", False, result.error)
        return False
    f.check("单件落地能跑起来", True)

    box = result.final.get("BOX") or {}
    z_end = (box.get("pos") or [None, None, None])[2]
    # 半边长 10mm = 0.01m；允许 2mm 的接触软度误差
    expected = 0.010
    f.check("落地后高度 ≈ 半边长（不穿地、不弹飞）",
            z_end is not None and abs(z_end - expected) < 0.002,
            f"z_end={z_end} 期望 {expected}±0.002")
    f.check("落地后没有翻倒（倾角 ≈ 0）",
            result.max_tilt_deg.get("BOX", 999) < 1.0,
            str(result.max_tilt_deg))
    f.check("物理量不是 NaN",
            z_end is not None and not math.isnan(z_end), str(z_end))

    print("\n== 止损线 2：四旋翼悬停 ==")

    hover = mujoco_backend.hover_test(mass_g=1000.0, duration_s=2.0)
    if not hover.get("ok"):
        f.check("悬停能跑起来", False, hover.get("error", ""))
        return False
    f.check("悬停能跑起来", True)
    f.check("推力 = 重力（自动配平）",
            abs(hover["thrust_n"] - hover["gravity_n"]) < 1e-6,
            f"{hover['thrust_n']} vs {hover['gravity_n']}")
    f.check("高度保持（2 秒内波动 < 2cm）",
            hover["z_range_m"] is not None and hover["z_range_m"] < 0.02,
            f"波动 {hover['z_range_m']}m")
    f.check("结束高度 ≈ 起始高度",
            abs(hover["z_end_m"] - hover["z_start_m"]) < 0.02,
            f"{hover['z_end_m']} vs {hover['z_start_m']}")
    return True


# ---------------------------------------------------------------------------
# 参数表
# ---------------------------------------------------------------------------

def check_params(f: Failures) -> None:
    print("\n== 3. 参数表全量（136 项）==")

    ps = sim_params.load()
    f.check("136 项全部搬过来", len(ps.params) == 136, str(len(ps.params)))

    bad_group = [k for k, p in ps.params.items()
                 if p.group not in sim_params.GROUPS]
    f.check("每项的 group 都合法", not bad_group, str(bad_group[:5]))

    no_note = [k for k, p in ps.params.items() if not p.note.strip()]
    f.check("每项都有物理含义说明", not no_note, str(no_note[:5]))

    no_unit = [k for k, p in ps.params.items()
               if not p.unit.strip() and not isinstance(p.value, bool)]
    f.check("非布尔项都有单位", not no_unit, str(no_unit[:5]))

    f.check("16 个物理域都在",
            len(ps.groups_present()) == 16, str(ps.groups_present()))

    print("\n== 4. 通用性标记 ==")

    summary = ps.summary()
    f.check("通用项数合理（31 项）", summary["universal"] == 31,
            str(summary["universal"]))
    undecided = [k for k, p in ps.params.items() if p.universal is None]
    f.check("每项都标了 universal", not undecided, str(undecided[:5]))
    # 乐高专用项必须是 universal=False
    for key in ("stud_pull_n", "mesh_cap_n", "chain_cap_n", "cord_tension_n"):
        f.check(f"{key} 标为专用（universal=False）",
                ps.params[key].universal is False,
                str(ps.params[key].universal))

    print("\n== 5. diff 能识别「从 0 变非 0」==")

    learned = ps.with_params(
        {"mu_tyre_rod": 0.570, "mesh_mu": 0.387, "supply_r": 0.394},
        source="measured", label="learned")
    rows = ps.diff(learned)
    f.check("diff 出 3 项差异", len(rows) == 3, str(len(rows)))
    kinds = {r["kind"] for r in rows}
    f.check("全部识别为 turned_on（初始是 0 = 没建模这项物理）",
            kinds == {"turned_on"}, str(kinds))
    f.check("diff 带单位与含义",
            all(r.get("unit") and r.get("note") for r in rows),
            str(rows[:1]))

    print("\n== 5b. 设计级覆盖可查 ==")

    design = _design([_box("B1")])
    design.sim_params = {"dt": {"value": 0.001, "source": "measured"}}
    effective = sim_params.effective(design)
    f.check("设计级覆盖生效",
            abs(float(effective.get("dt")) - 0.001) < 1e-9,
            str(effective.get("dt")))
    f.check("覆盖项来源变成 measured",
            effective.params["dt"].source == "measured",
            effective.params["dt"].source)
    f.check("未覆盖项保持 public",
            effective.params["mu_board"].source == "public",
            effective.params["mu_board"].source)


# ---------------------------------------------------------------------------
# MJCF
# ---------------------------------------------------------------------------

def check_mjcf(f: Failures) -> None:
    print("\n== 6. 单位：mm → m ==")

    design = _design([_box("B1", size=20.0, z=100.0, mass_g=100.0)])
    built = mjcf.build(design, free_bodies=True)
    xml = built.xml
    f.check("位置用米（0.1 而不是 100）",
            'pos="0 0 0.1"' in xml, "位置单位错")
    f.check("尺寸用米（0.01 而不是 10）",
            'size="0.01 0.01 0.01"' in xml, "尺寸单位错")
    f.check("质量用 kg（0.1 而不是 100）",
            'mass="0.1"' in xml, "质量单位错")

    print("\n== 6b. 生成物可编译 ==")

    import mujoco
    try:
        mujoco.MjModel.from_xml_string(xml)
        f.check("MJCF 能被 MuJoCo 编译", True)
    except Exception as exc:                              # noqa: BLE001
        f.check("MJCF 能被 MuJoCo 编译", False, str(exc))

    print("\n== 7. 闸门：inferred 关节不进 MJCF ==")

    design = _design([_box("A"), _box("B", z=10.0)])
    design.joints_list = [
        Joint(a="A", b="B", kind="hinge", source="inferred",
              params={"range_deg": [-45, 45]}).to_dict(),
    ]
    built = mjcf.build(design)
    f.check("inferred 关节不生成 XML 关节",
            "j_A_B_hinge" not in built.xml, "inferred 关节进了仿真")
    f.check("inferred 关节进 skipped 并说明",
            any("inferred" in s.get("why", "") for s in built.skipped),
            str([s.get("why", "")[:40] for s in built.skipped]))

    # promoted 之后才进
    design.joints_list = [
        Joint(a="A", b="B", kind="hinge", source="declared",
              params={"range_deg": [-45, 45]}).to_dict(),
    ]
    built = mjcf.build(design)
    f.check("declared 关节生成 XML 关节",
            "j_A_B_hinge" in built.xml, "declared 关节没进仿真")

    print("\n== 7b. 缺参数/缺尺寸要进 skipped ==")

    design = _design([_box("A"), _box("B", z=10.0)])
    design.joints_list = [
        Joint(a="A", b="B", kind="gear", source="declared").to_dict(),  # 缺 ratio
    ]
    built = mjcf.build(design)
    f.check("gear 缺 ratio → 跳过并说明",
            any("缺参数" in s.get("why", "") or "ratio" in s.get("why", "")
                for s in built.skipped),
            str([s.get("why", "")[:50] for s in built.skipped]))
    f.check("跳过的关节不生成 XML",
            "polycoef" not in built.xml, "缺参数的关节还是生成了")

    # 零尺寸零件
    design = _design([Part(ref="Z1", kind="x", size_mm={"w": 0, "h": 0, "d": 0},
                           physical_source="datasheet",
                           placement=Placement())])
    built = mjcf.build(design)
    f.check("零尺寸零件进 skipped",
            any("尺寸" in s.get("why", "") for s in built.skipped),
            str([s.get("why", "")[:40] for s in built.skipped]))
    f.check("skipped 非空时 complete=False",
            built.stats()["complete"] is False, str(built.stats()))

    print("\n== 7c. 质量估算要标出来 ==")

    design = _design([_box("N1", mass_g=None)])
    built = mjcf.build(design)
    f.check("无 mass_g 的件质量被估算",
            built.stats()["mass_estimated"] == 1,
            str(built.stats()))
    f.check("估算触发 warning 说明依据",
            any("密度" in w for w in built.warnings),
            str(built.warnings[:2]))


# ---------------------------------------------------------------------------
# 确定性 + 分派
# ---------------------------------------------------------------------------

def check_determinism(f: Failures) -> None:
    print("\n== 8. 确定性：同一模型两次跑结果一致 ==")

    design = _design([_box("D1", z=80.0)])
    a = mujoco_backend.run(design, duration_s=0.5, free_bodies=True)
    b = mujoco_backend.run(design, duration_s=0.5, free_bodies=True)
    if not (a.ok and b.ok):
        f.check("两次都能跑", False, f"{a.error} / {b.error}")
        return
    f.check("两次结束位置逐位相等",
            a.final["D1"]["pos"] == b.final["D1"]["pos"],
            f"{a.final['D1']['pos']} vs {b.final['D1']['pos']}")
    f.check("两次步数相同", a.steps == b.steps, f"{a.steps} vs {b.steps}")


def check_dispatch(f: Failures) -> None:
    print("\n== 9. 域分派：没有模型就是 unknown ==")

    statuses = {s.domain: s for s in sim_dispatch.domains()}
    f.check("六个域都在", len(statuses) == 6, str(sorted(statuses)))
    f.check("热域标为无模型", statuses["thermal"].available is False)
    f.check("EMC 域标为无模型", statuses["emc"].available is False)
    f.check("无模型的原因说「做不到」而不是「待做」",
            "做不到" in statuses["thermal"].reason,
            statuses["thermal"].reason)

    design = _design([_box("B1")])
    for domain in ("thermal", "emc"):
        row = sim_dispatch.run_domain(design, domain)
        f.check(f"{domain} 域返回 unknown（不是 ok）",
                row["status"] == "unknown", row["status"])

    row = sim_dispatch.run_domain(design, "kinematic", duration_s=0.3)
    f.check("kinematic 域能跑", row["status"] in ("ok", "unknown"),
            row["status"])

    # 汇总时"可用但没跑"要单列
    summary = sim_dispatch.report(design, only=("geometry",))
    f.check("可用但没跑的域被单列",
            summary["not_run"], str(summary["not_run"]))
    f.check("没有模型的域被单列",
            set(summary["no_model"]) >= {"thermal", "emc"},
            str(summary["no_model"]))


# ---------------------------------------------------------------------------

def check_weld(f: Failures) -> None:
    """weld_parts：PCB 上的元件是**焊死的**。

    ## 这条为什么必须有验收

    实测：141 个元件的 `示例-单板` 用 `free_bodies=True`（每个零件自由）
    跑 1 秒，`QACC` 爆炸、末速冲到 **61574 m/s**。仿真"跑完了 2000 步"
    却全是垃圾——这正是"看起来跑通"的假象。

    物理上 PCB 元件焊在板上。`weld_parts=True` 让**每块板成一个刚体**，
    零件是它的子 geom。同一模型再跑：末速 0.0、倾角 1.9°。
    """
    print("\n== 10. weld_parts：整板一个刚体 ==")

    design = _design([_box("A", size=10.0, z=5.0, mass_g=10.0),
                      _box("B", size=10.0, z=5.0, mass_g=10.0, x=20.0)])
    plain = mjcf.build(design, free_bodies=True)
    welded = mjcf.build(design, free_bodies=True, weld_parts=True)

    f.check("不 weld 时每个零件一个刚体",
            plain.stats()["parts_simulated"] == 2,
            str(plain.stats()["parts_simulated"]))
    f.check("weld 后只有 1 个刚体（板）",
            welded.stats()["parts_simulated"] == 1,
            str(welded.stats()["parts_simulated"]))
    f.check("weld 后零件的 geom 仍都在",
            "A_g" in welded.xml and "B_g" in welded.xml,
            "零件 geom 丢了")

    print("\n== 10b. 数值稳定性（爆炸与否）==")

    result = mujoco_backend.run(design, duration_s=0.5, free_bodies=True,
                                weld_parts=True)
    if not result.ok:
        f.check("weld 模式能跑", False, result.error)
        return
    f.check("weld 模式能跑", True)
    speeds = list(result.final_speed.values())
    f.check("末速不是天文数字（< 10 m/s）",
            all(v < 10.0 for v in speeds), str([round(v, 2) for v in speeds]))
    f.check("末速接近静止（< 1 m/s，说明落稳了）",
            all(v < 1.0 for v in speeds), str([round(v, 3) for v in speeds]))

    print("\n== 10c. 最小质量下限（防奇异矩阵）==")

    tiny = Part(ref="T1", kind="c", size_mm={"w": 0.4, "h": 0.2, "d": 0.2},
                physical_source="datasheet", placement=Placement(z_mm=10.0))
    built = mjcf.build(_design([tiny]))
    f.check("超小件质量被抬到下限",
            any("下限" in w for w in built.warnings),
            str(built.warnings[:1]))
    mass = built.parts[0].mass_g if built.parts else None
    f.check("下限质量 ≥ 0.01g",
            mass is not None and mass >= 0.009, str(mass))

    print("\n== 10d. 真实工程：weld 后能跑且稳定 ==")

    from hw_velxio.design.store import DesignStore, list_projects
    row = next((p for p in list_projects()
                if p.get("id") == "示例-单板" and p.get("has_design")), None)
    if row is None:
        print("  skip 找不到 示例-单板")
        return
    real = DesignStore("示例-单板").load()
    result = mujoco_backend.run(real, duration_s=0.5, free_bodies=True,
                                weld_parts=True)
    f.check("141 元件的板 weld 后能跑", result.ok, result.error)
    if result.ok:
        speeds = list(result.final_speed.values())
        f.check("真实工程末速不是天文数字",
                all(v < 10.0 for v in speeds),
                str([round(v, 2) for v in speeds]))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    f = Failures()

    ok, why = mujoco_backend.available()
    print(f"MuJoCo: {why}\n")
    if not ok:
        print("MuJoCo 不可用，P16 无法验收。")
        print("装法（只在本项目内）：")
        print(f"  python -m pip install --target {mujoco_backend.LOCAL_LIB} "
              f"<mujoco wheel>")
        return 1

    stop_ok = check_stop_lines(f)
    if not stop_ok:
        print("\n**止损线未通过** —— 按计划不接 P18 的运动域。")
        for name in f.items:
            print(f"  - {name}")
        return 1

    check_params(f)
    check_mjcf(f)
    check_determinism(f)
    check_dispatch(f)
    check_weld(f)

    print()
    if f.items:
        print(f"FAILED {len(f.items)} 项：")
        for name in f.items:
            print(f"  - {name}")
        return 1
    print("P16 验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())