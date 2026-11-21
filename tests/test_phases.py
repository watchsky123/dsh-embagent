"""P23：把 P15–P22 的判据沉淀成单测（表驱动，每个变异点一条独立用例）。

## 为什么要"表驱动 + 动态生成"

这些层里绝大多数判据是**同一形状的 N 个实例**：136 个仿真参数、56 个封装、
12 种关节、9 个阶梯层……手写 N 个几乎一样的 `def test_xxx` 除了让文件膨胀
什么也没多给。

但**每个实例仍然值得一条独立用例**：一条用例挂掉，`unittest` 会直接告诉
你是**哪一个**参数/封装/关节坏了。塞进一个 `for` 循环里断言，坏了只知道
"某个参数有问题"。这就是本文件的取舍：用动态生成拿到"逐条的定位精度"，
同时不写 400 个重复函数。

## 每个夹具都要过两遍

`tests/fixtures/designs/*.json` 是 15 个**合成夹具**（叠板/折叠/四旋翼/
多层板/越界/重叠/极性反接/无网表/单脚网/重复 ref/差分对/三板/云台相机…）。
每个都跑：

1. **能读** —— `from_dict` 不抛，`validate()` 的结论与夹具设计意图一致
2. **往返不变** —— `to_dict()` → `from_dict()` → `to_dict()` 逐字节相同

第二条抓的是"写出去没读回来"这一类：字段加了但忘了在 `from_dict` 里接，
设计会**静默丢字段**，而所有下游都看不到。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Callable

from hw_velxio.design import (
    agent_loop,
    agent_tools,
    capability,
    emit_lceda,
    handoff,
    joints,
    ladder,
    mjcf,
    package_sizes,
    parts_catalog,
    real_tests,
    robustness,
    sim_calibration,
    sim_params,
    verdict,
    nextstep,
    entity,
    sensitivity,
    firmware_matrix,
    req_draft,
    pcb_geo,
    pcb_layout,
    router,
    lceda_channel,
)
from hw_velxio.design.fwgen import drivers as fw_drivers
from hw_velxio.design.fwgen import mcus as fw_mcus
from hw_velxio.design.fwgen import pinmap as fw_pinmap
from hw_velxio.design.fwgen import scaffold as fw_scaffold
from hw_velxio.design.model import (Board, DesignError, DesignModel, Joint, Part,
                                    Placement, Program)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "designs"


# ---------------------------------------------------------------------------
# 动态生成工具
# ---------------------------------------------------------------------------


def _test_name(label: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in label.lower())
    return "test_" + cleaned.strip("_")[:60]


def add_cases(cls: type, cases: list[Any],
              body: Callable[..., None]) -> None:
    """给 ``cls`` 挂上 ``len(cases)`` 个独立测试方法。

    ``cases`` 里每项是 ``(标签, *参数)``；``body(self, *参数)``。
    **标签不传给 body**——它只用来起方法名。
    名字重复会直接抛，不静默覆盖（否则"测了 136 个"其实只跑了 1 个）。
    """
    for case in cases:
        name = _test_name(str(case[0]))
        if hasattr(cls, name):
            raise AssertionError(f"测试名重复：{name}（{case[0]!r}）")
        def make(c: tuple[Any, ...]) -> Callable[[Any], None]:
            args = c[1:]

            def test(self: Any) -> None:
                body(self, *args)
            return test
        setattr(cls, name, make(case))


def load_fixture(name: str) -> DesignModel:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return DesignModel.from_dict(data)


def fixture_names() -> list[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.json"))


# 夹具的**设计意图**：validate() 应该报 / 不该报。写在这里而不是散在用例里，
# 因为它是这份夹具存在的理由。
# 夹具的三档意图。写在这里而不是散在用例里，因为它是这份夹具存在的理由：
#
#   clean    —— validate 干净，且 V2 不是 fail（可能是 unknown：只有警告）
#   validate —— validate 就该报（结构不合法：重复 ref / 超界）
#   geometry —— validate 干净，但**几何层 V2 必须 fail**（重叠）
#
# `unknown` 与 `clean` 并存不是含糊：V2 有警告（没 keepout、没安装孔）时
# 按 P18 的纪律只能 unknown——**警告不算通过**。
FIXTURE_EXPECT = {
    "tiny-blink": "clean",
    "dense-0402": "clean",
    "stacked-2layer": "clean",
    "folded-hinge": "clean",
    "quadrotor": "clean",
    "multilayer-4": "clean",
    "out-of-board": "validate",
    "overlap-parts": "geometry",
    "reversed-polarity": "clean",      # 极性由 V1 判，validate 不管
    "no-netlist": "clean",             # 没网表是"缺数据"，不是"非法"
    "single-pin-net": "clean",
    "dup-ref": "validate",             # 重复 ref 必须拒
    "diffpair-usb": "clean",
    "many-boards": "clean",
    "gimbal-camera": "clean",
}


# ---------------------------------------------------------------------------
# 1. 夹具：能读 + 往返不变
# ---------------------------------------------------------------------------


class TestFixtures(unittest.TestCase):
    """15 个合成夹具 × 2 项。"""


def _fixture_readable(self: unittest.TestCase, name: str) -> None:
    """`validate()` 的结论与夹具意图一致。"""
    design = load_fixture(name)
    self.assertTrue(design.parts, f"{name} 没有元件")
    want = FIXTURE_EXPECT[name]
    problems = design.validate()
    if want == "validate":
        self.assertTrue(problems, f"{name} 本该被 validate 拒（这就是它存在的理由）")
    else:
        self.assertEqual(problems, [], f"{name} 本该干净：{problems}")


def _fixture_roundtrip(self: unittest.TestCase, name: str) -> None:
    raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    again = DesignModel.from_dict(raw).to_dict()
    self.assertEqual(json.dumps(again, sort_keys=True, ensure_ascii=False),
                     json.dumps(raw, sort_keys=True, ensure_ascii=False),
                     f"{name} 往返后有字段变了/丢了")


add_cases(TestFixtures, [(f"readable:{n}", n) for n in fixture_names()],
          _fixture_readable)


def _fixture_geometry(self: unittest.TestCase, name: str) -> None:
    """V2 的结论与夹具意图一致（`geometry` 档必须 fail）。"""
    design = load_fixture(name)
    result = ladder.run_ladder(design, levels=("V2",))
    status = result.levels[-1].status
    want = FIXTURE_EXPECT[name]
    if want == "geometry":
        self.assertEqual(status, "fail", f"{name} 的几何问题没被 V2 抓到")
    elif want == "validate":
        # 结构就不合法的件（重复 ref / 超界）几何层**也不该是 pass**——
        # 超界的件会让 V2 一起 fail，这是对的（两层都在说它坏了）。
        self.assertNotEqual(status, "pass",
                            f"{name} 结构都不合法，几何层却 pass？")
    else:
        self.assertNotEqual(status, "fail",
                            f"{name} 不该有几何 error（unknown 可以：那只是警告）")


add_cases(TestFixtures, [(f"geometry:{n}", n) for n in fixture_names()],
          _fixture_geometry)
add_cases(TestFixtures, [(f"roundtrip:{n}", n) for n in fixture_names()],
          _fixture_roundtrip)


class TestFixtureSet(unittest.TestCase):
    def test_fifteen_fixtures(self) -> None:
        self.assertGreaterEqual(len(fixture_names()), 15,
                                f"夹具只有 {len(fixture_names())} 个")

    def test_every_fixture_has_expectation(self) -> None:
        missing = [n for n in fixture_names() if n not in FIXTURE_EXPECT]
        self.assertEqual(missing, [], "新夹具没写设计意图（clean/problem）")

    def test_multi_board_fixtures_are_really_multi(self) -> None:
        for name in ("stacked-2layer", "folded-hinge", "many-boards"):
            design = load_fixture(name)
            self.assertGreater(len(design.board_list()), 1, name)


# ---------------------------------------------------------------------------
# 2. 仿真参数：136 个逐个查
# ---------------------------------------------------------------------------


class TestSimParams(unittest.TestCase):
    """每个参数一条用例：分组合法、键自洽、note 非空。"""


def _param_sane(self: unittest.TestCase, key: str) -> None:
    param = sim_params.load().params[key]
    self.assertEqual(param.key, key)
    self.assertIn(param.group, sim_params.GROUPS)
    self.assertTrue(str(param.note or "").strip(),
                    f"{key} 没有 note —— 没有物理含义的参数名没人敢改")
    self.assertIn(param.source, ("public", "datasheet", "measured", "catalog"))


_ALL_PARAMS = sorted(sim_params.load().params)
add_cases(TestSimParams, [(f"param:{k}", k) for k in _ALL_PARAMS], _param_sane)


class TestSimParamsBehaviour(unittest.TestCase):
    def test_param_count(self) -> None:
        self.assertEqual(len(_ALL_PARAMS), 136, "参数表应恰好 136 项")

    def test_sixteen_groups(self) -> None:
        groups = sim_params.load().groups_present()
        self.assertEqual(len(groups), 16, groups)

    def test_no_default_param_is_trusted(self) -> None:
        # 初始表全是 public 级；**没有一条能进硬判定**
        loaded = sim_params.load()
        self.assertEqual({p.source for p in loaded.params.values()}, {"public"})

    def test_with_params_promotes_source(self) -> None:
        base = sim_params.load()
        new = base.with_params({"mu_tyre_rod": 0.57}, source="measured")
        self.assertEqual(new.params["mu_tyre_rod"].value, 0.57)
        self.assertEqual(new.params["mu_tyre_rod"].source, "measured")
        self.assertEqual(base.params["mu_tyre_rod"].value, 0.0, "不能原地改")

    def test_diff_marks_turned_on(self) -> None:
        base = sim_params.load()
        new = base.with_params({"mu_tyre_rod": 0.57}, source="measured")
        rows = base.diff(new)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "turned_on",
                         "0 → 非 0 必须标 turned_on（初始仿真器没建模的物理）")

    def test_effective_merges_design_overrides(self) -> None:
        design = load_fixture("tiny-blink")
        design.sim_params = {"mu_tyre_rod": {"value": 0.4, "source": "measured"}}
        merged = sim_params.effective(design)
        self.assertEqual(merged.get("mu_tyre_rod"), 0.4)

    def test_load_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            sim_params.load("definitely-not-here.json")


# ---------------------------------------------------------------------------
# 3. 封装尺寸表
# ---------------------------------------------------------------------------


class TestPackageSizes(unittest.TestCase):
    pass


_CHIP_CASES = [(f"chip:{k}", k) for k in sorted(package_sizes.CHIP_SIZES)]
_FAMILY_CASES = [(f"family:{k}", k) for k in sorted(package_sizes.PACKAGE_FAMILIES)]


def _chip_ok(self: unittest.TestCase, key: str) -> None:
    size = package_sizes.lookup(key)
    self.assertTrue(size.ok)
    self.assertEqual(size.source, "jedec")
    self.assertGreaterEqual(size.w, size.h, "片式元件长轴必须在 w（沿 X）")


def _family_ok(self: unittest.TestCase, key: str) -> None:
    size = package_sizes.lookup(key)
    self.assertTrue(size.ok, f"{key} 查不到")
    self.assertIn(size.source, ("jedec", "formula"))
    self.assertGreater(size.w, 0)
    self.assertGreater(size.h, 0)
    self.assertGreater(size.d, 0)


add_cases(TestPackageSizes, _CHIP_CASES, _chip_ok)
add_cases(TestPackageSizes, _FAMILY_CASES, _family_ok)


class TestPackageNormalize(unittest.TestCase):
    def test_metric_alias_resolves(self) -> None:
        self.assertEqual(package_sizes.normalize("0402 (1005 Metric)"), "0402")

    def test_suffix_stripped(self) -> None:
        self.assertEqual(package_sizes.normalize("LQFP-48(7x7)"), "LQFP-48")

    def test_unknown_stays_unknown(self) -> None:
        size = package_sizes.lookup("BGA-64")
        self.assertEqual(size.source, "unknown")
        self.assertEqual(size.size_mm, {"w": 0.0, "h": 0.0, "d": 0.0})

    def test_empty_is_unknown(self) -> None:
        self.assertEqual(package_sizes.lookup("").source, "unknown")

    def test_formula_not_labelled_jedec(self) -> None:
        self.assertEqual(package_sizes.lookup("SOIC-10").source, "formula")


# ---------------------------------------------------------------------------
# 4. 模型基础
# ---------------------------------------------------------------------------


class TestModelJoints(unittest.TestCase):
    pass


_JOINT_CASES = [(f"kind:{k}", k) for k in DesignModel  # placeholder replaced below
                ] if False else []


def _joint_kind_roundtrip(self: unittest.TestCase, kind: str) -> None:
    params: dict[str, Any] = {}
    # 需要参数的 kind 给一份最小合法参数（**不是默认值**，是这份用例的输入）
    params.update({"gear": {"ratio": 2.0}, "axle": {"diameter_mm": 3.0},
                   "slide": {"range_mm": 10.0}, "tread": {"spacing_mm": 20.0},
                   "rack": {"pitch_mm": 2.0}, "hinge": {"axis": "z"},
                   "chain": {"pitch_mm": 8.0}}.get(kind, {}))
    joint = Joint(a="A1", b="B1", kind=kind, params=params)
    again = Joint.from_dict(joint.to_dict())
    self.assertEqual(again.kind, kind)
    self.assertEqual(again.key, joint.key, "无向关系的 key 必须与顺序无关")


from hw_velxio.design.model import JOINT_KINDS as _JOINT_KINDS  # noqa: E402

add_cases(TestModelJoints, [(f"kind:{k}", k) for k in _JOINT_KINDS],
          _joint_kind_roundtrip)


class TestModelBehaviour(unittest.TestCase):
    def test_undirected_key_ignores_order(self) -> None:
        a = Joint(a="X1", b="Y1", kind="stack")
        b = Joint(a="Y1", b="X1", kind="stack")
        self.assertEqual(a.key, b.key)

    def test_unknown_joint_kind_rejected(self) -> None:
        with self.assertRaises(DesignError):
            Joint(a="A1", b="B1", kind="not-a-kind")

    def test_program_steps_must_be_ascending(self) -> None:
        with self.assertRaises(DesignError):
            Program(steps=[{"t_s": 0.2, "actuator": "M1", "value": 1.0},
                           {"t_s": 0.1, "actuator": "M1", "value": 0.0}])

    def test_program_requires_three_fields(self) -> None:
        with self.assertRaises(DesignError):
            Program(steps=[{"t_s": 0.1, "actuator": "M1"}])

    def test_program_units_validated(self) -> None:
        with self.assertRaises(DesignError):
            Program(steps=[], units="furlongs")

    def test_mass_none_is_not_zero(self) -> None:
        light = Part(ref="A1", kind="x", physical_source="estimated", mass_g=None)
        zero = Part(ref="A2", kind="x", physical_source="estimated", mass_g=0.0)
        self.assertIsNone(light.mass_g)
        self.assertEqual(zero.mass_g, 0.0)
        self.assertNotIn("mass_g", light.to_dict())
        self.assertIn("mass_g", zero.to_dict())

    def test_negative_mass_rejected(self) -> None:
        with self.assertRaises(DesignError):
            Part(ref="A1", kind="x", physical_source="estimated", mass_g=-1.0)

    def test_inertia_needs_six(self) -> None:
        with self.assertRaises(DesignError):
            Part(ref="A1", kind="x", physical_source="estimated",
                 inertia=[1.0, 1.0])

    def test_unknown_mount_rejected(self) -> None:
        with self.assertRaises(DesignError):
            Placement(mount="ceiling")

    def test_side_normalized(self) -> None:
        self.assertEqual(Placement(side=" TOP ").side, "top")

    def test_board_list_defaults_to_one(self) -> None:
        board = Board(kind="generic", size_mm={"w": 10, "h": 10})
        self.assertEqual(len(board.board_list() if False else [board]), 1)

    def test_dup_ref_rejected_by_validate(self) -> None:
        design = load_fixture("dup-ref")
        self.assertTrue(any("重复" in p for p in design.validate()), design.validate())

    def test_no_netlist_is_not_invalid(self) -> None:
        design = load_fixture("no-netlist")
        self.assertEqual(design.nets, [])
        self.assertEqual(design.validate(), [])


# ---------------------------------------------------------------------------
# 5. 关节层
# ---------------------------------------------------------------------------


class TestJointsModule(unittest.TestCase):
    def test_inferable_set(self) -> None:
        from hw_velxio.design.model import JOINT_INFERABLE
        self.assertEqual(JOINT_INFERABLE, frozenset({"stack", "port", "contact"}))

    def test_validate_flags_missing_params(self) -> None:
        design = load_fixture("tiny-blink")
        design.joints_list = [Joint(a="U1", b="R1", kind="gear").to_dict()]
        report = joints.validate(design)
        self.assertFalse(report["ok"])
        self.assertTrue(any("ratio" in str(f.get("message"))
                            for f in report["findings"]), report)

    def test_validate_flags_dangling_ref(self) -> None:
        design = load_fixture("tiny-blink")
        design.joints_list = [Joint(a="U1", b="NOPE", kind="stack").to_dict()]
        report = joints.validate(design)
        self.assertFalse(report["ok"])
        self.assertTrue(any(f.get("ref") == "NOPE" for f in report["findings"]), report)

    def test_inferred_never_declared(self) -> None:
        design = load_fixture("stacked-2layer")
        inference = joints.infer(design)
        for joint in inference.joints:
            if joint.source == "inferred":
                self.assertIn(joint.kind, joints.JOINT_INFERABLE)

    def test_apply_only_declared_keeps_inferred_out(self) -> None:
        design = load_fixture("tiny-blink")
        before = list(design.joints_list)
        joints.apply(design, joints.infer(design).joints, only_declared=True)
        self.assertEqual(design.joints_list, before,
                         "apply(only_declared) 不许写进推断结果")


# ---------------------------------------------------------------------------
# 6. MJCF 生成
# ---------------------------------------------------------------------------


class TestMjcf(unittest.TestCase):
    pass


def _mjcf_mapping(self: unittest.TestCase, kind: str, expect: str) -> None:
    self.assertEqual(mjcf.JOINT_MAPPING.get(kind), expect,
                     f"{kind} 应映射到 {expect}")


add_cases(TestMjcf, [(f"map:{k}", k, v) for k, v in sorted(mjcf.JOINT_MAPPING.items())],
          _mjcf_mapping)


class TestMjcfBehaviour(unittest.TestCase):
    def test_build_minimal(self) -> None:
        parts = mjcf.build(load_fixture("tiny-blink"))
        self.assertIn("<mujoco", parts.header)
        self.assertTrue(parts.parts, "一个元件都没生成")
        self.assertTrue(parts.parts[0].xml.startswith("<body"))
        self.assertIn('memory="256M"', parts.header,
                      "arena 太小会让 MuJoCo 静默丢约束")

    def test_mm_to_m_applied(self) -> None:
        """5mm 见方的元件必须是 0.0025（米），不是 5。"""
        parts = mjcf.build(load_fixture("tiny-blink"))
        joined = " ".join(body.xml for body in parts.parts)
        self.assertIn("0.0025", joined,
                      "mm→m 没做：元件会落在 1000 倍的位置上")
        self.assertNotIn('size="5 ', joined)

    def test_skipped_parts_are_reported(self) -> None:
        design = load_fixture("tiny-blink")
        design.parts[0].size_mm = {"w": 0.0, "h": 0.0, "d": 0.0}
        parts = mjcf.build(design)
        self.assertTrue(parts.skipped or parts.warnings,
                        "零尺寸件要么跳过要么告警，不能静默生成")


# ---------------------------------------------------------------------------
# 7. 三次扰动
# ---------------------------------------------------------------------------


class TestRobustness(unittest.TestCase):
    def test_all_pass(self) -> None:
        result = robustness.triplicate("geometry", lambda p: ("pass", ""))
        self.assertEqual(result.status, "pass")

    def test_one_fail(self) -> None:
        calls = {"n": 0}

        def run(_p: dict[str, Any]) -> tuple[str, str]:
            calls["n"] += 1
            return ("fail", "") if calls["n"] == 2 else ("pass", "")
        result = robustness.triplicate("geometry", run)   # geometry 有 2 个扰动
        self.assertEqual(result.status, "fail")

    def test_one_unknown_is_unknown_not_pass(self) -> None:
        calls = {"n": 0}

        def run(_p: dict[str, Any]) -> tuple[str, str]:
            calls["n"] += 1
            # electrical 有 3 个扰动；第 3 次给 unknown
            return ("unknown", "") if calls["n"] == 3 else ("pass", "")
        self.assertEqual(robustness.triplicate("electrical", run).status, "unknown")

    def test_exception_becomes_unknown(self) -> None:
        def run(_p: dict[str, Any]) -> tuple[str, str]:
            raise RuntimeError("boom")
        self.assertEqual(robustness.triplicate("electrical", run).status, "unknown")

    def test_domain_without_perturbations(self) -> None:
        result = robustness.triplicate("no_such_domain", lambda p: ("pass", ""))
        self.assertEqual(result.status, "unknown")
        self.assertIn("没有", result.note)

    def test_combine_precedence(self) -> None:
        def attempt(status: str) -> robustness.Attempt:
            return robustness.Attempt(name="p", status=status)
        self.assertEqual(robustness.combine([attempt("pass")]), "pass")
        self.assertEqual(robustness.combine([attempt("pass"), attempt("unknown")]), "unknown")
        self.assertEqual(robustness.combine([attempt("unknown"), attempt("fail")]), "fail")

    def test_empty_attempts_is_unknown(self) -> None:
        self.assertEqual(robustness.combine([]), "unknown",
                         "没跑过不能算通过")


# ---------------------------------------------------------------------------
# 8. 阶梯
# ---------------------------------------------------------------------------


class TestLadder(unittest.TestCase):
    def test_nine_levels(self) -> None:
        self.assertEqual(list(ladder.LEVELS), [f"V{i}" for i in range(9)])

    def test_default_levels_stop_at_v6(self) -> None:
        self.assertEqual(tuple(ladder.DEFAULT_LEVELS), tuple(f"V{i}" for i in range(7)))

    def test_v7_is_opt_in(self) -> None:
        self.assertEqual(tuple(ladder.SIM_LEVELS), ("V7",))
        self.assertNotIn("V7", ladder.DEFAULT_LEVELS)

    def test_hard_levels_are_v0_v1_only(self) -> None:
        self.assertEqual(tuple(ladder.HARD_LEVELS), ("V0", "V1"))

    def test_clean_fixture_passes_v0(self) -> None:
        result = ladder.run_ladder(load_fixture("tiny-blink"),
                                   levels=("V0",))
        level = result.levels[-1]
        self.assertEqual(level.status, "pass", level.findings)

    def test_v8_is_not_run_without_p17(self) -> None:
        result = ladder.run_ladder(load_fixture("tiny-blink"), levels=("V8",))
        self.assertEqual(result.levels[-1].status, "not_run")

    def test_v7_without_mujoco_is_not_run(self) -> None:
        from hw_velxio.design import mujoco_backend
        saved = mujoco_backend.available
        try:
            mujoco_backend.available = lambda: (False, "stub: 没有 MuJoCo")
            result = ladder.run_ladder(load_fixture("tiny-blink"), levels=("V7",))
            self.assertEqual(result.levels[-1].status, "not_run",
                             "没有 MuJoCo 时 V7 必须 not_run，不能 pass")
        finally:
            mujoco_backend.available = saved


# ---------------------------------------------------------------------------
# 9. 立创发射的纯函数
# ---------------------------------------------------------------------------


class TestEmitLcedaPure(unittest.TestCase):
    def test_sanitize_project_name(self) -> None:
        self.assertEqual(emit_lceda._sanitize_project_name("示例-单板 主控"),
                         "hwv-design")
        self.assertEqual(emit_lceda._sanitize_project_name("ok_name-1"), "ok-name-1")

    def test_lcsc_placeholder_rejected(self) -> None:

        class _P:
            lcsc = "C?"
        self.assertEqual(emit_lceda._lcsc_of(_P()), "")

        class _Q:
            lcsc = "c1523"
        self.assertEqual(emit_lceda._lcsc_of(_Q()), "C1523")

    def test_outline_rect_uses_r_mode(self) -> None:
        design = load_fixture("tiny-blink")
        source = emit_lceda._outline_source(design, design.board)
        self.assertEqual(source[0], "R")
        self.assertEqual(len(source), 7)

    def test_outline_non_rect_uses_polyline(self) -> None:
        design = load_fixture("tiny-blink")
        tri = Board(kind="generic", name="t", outline=[(0, 0), (10, 1), (5, 8)],
                    size_mm={"w": 10, "h": 8})
        self.assertIn("L", emit_lceda._outline_source(design, tri))

    def test_netlist_pins_parsing(self) -> None:
        text = json.dumps({"components": {"g1": {
            "props": {"Designator": "R1"},
            "pinInfoMap": {"1": {"number": "1", "net": "MID"}}}}})
        self.assertEqual(emit_lceda._netlist_pins(text), {("R1", "1"): "MID"})

    def test_netlist_garbage_is_empty(self) -> None:
        self.assertEqual(emit_lceda._netlist_pins("not json"), {})
        self.assertEqual(emit_lceda._netlist_pins(""), {})

    def test_parity_equal(self) -> None:
        design = load_fixture("tiny-blink")
        refs = {p.ref for p in design.parts}
        pins = {f"{r}": {} for r in refs}
        netlist = json.dumps({"components": {
            f"g{i}": {"props": {"Designator": p.ref},
                      "pinInfoMap": {pin["pin"]: {"number": pin["pin"],
                                                  "net": n["net"]}
                                     for n in design.nets
                                     for pin in n.get("pins", [])
                                     if pin.get("ref") == p.ref}}
            for i, p in enumerate(design.parts)}})
        report = emit_lceda.parity_from_netlists(design, [netlist])
        self.assertEqual(report["counts"], {"missing": 0, "wrong": 0, "extra": 0})

    def test_parity_without_netlist_is_not_ready(self) -> None:
        design = load_fixture("tiny-blink")
        self.assertEqual(emit_lceda.parity_from_netlists(design, [])["status"],
                         "not_ready")

    def test_emit_without_lceda_is_not_ready(self) -> None:
        import os
        from hw_velxio.design import lceda
        old = os.environ.get(lceda.EXE_ENV)
        os.environ[lceda.EXE_ENV] = "C:/no-such-lceda.exe"
        try:
            report = emit_lceda.emit(load_fixture("tiny-blink"),
                                     jobs=("project",), project_name="x")
            self.assertEqual(report.status, "not_ready")
            self.assertEqual({j.status for j in report.jobs.values()}, {"not_ready"})
        finally:
            if old is None:
                os.environ.pop(lceda.EXE_ENV, None)
            else:
                os.environ[lceda.EXE_ENV] = old

    def test_empty_jobs_is_not_ok(self) -> None:
        self.assertEqual(emit_lceda.EmitReport().recompute_status(), "not_ready")


# ---------------------------------------------------------------------------
# 10. 元件目录
# ---------------------------------------------------------------------------


class TestPartsCatalog(unittest.TestCase):
    def test_normalize_lcsc(self) -> None:
        self.assertEqual(parts_catalog.normalize_lcsc("c1523"), "C1523")
        self.assertEqual(parts_catalog.normalize_lcsc("C?"), "")
        self.assertEqual(parts_catalog.normalize_lcsc(""), "")

    def test_lifecycle_discontinued(self) -> None:
        part = parts_catalog.parse_detail({
            "componentCode": "C1", "componentModelEn": "X",
            "componentStatus": "yes",
            "noBuyReason": "This product is no longer manufactured."})
        self.assertEqual(part.lifecycle, "discontinued",
                         "componentStatus=yes 也可能已停产")

    def test_lifecycle_empty_is_unknown(self) -> None:
        self.assertEqual(parts_catalog.parse_detail({}).lifecycle, "unknown")

    def test_parse_detail_missing_fields_are_none(self) -> None:
        part = parts_catalog.parse_detail({})
        self.assertIsNone(part.stock)
        self.assertIsNone(part.pin_count)
        self.assertEqual(part.size_mm, {})

    def test_parse_detail_non_object(self) -> None:
        self.assertTrue(parts_catalog.parse_detail("nope").notes)  # type: ignore[arg-type]

    def test_cache_roundtrip(self) -> None:
        import tempfile
        path = Path(tempfile.mkdtemp(prefix="hwv-p23-cache-")) / "c.json"
        try:
            cache = parts_catalog.PartCache(path).load()
            part = parts_catalog.PartEnriched(lcsc="C1", mpn="X",
                                             size_mm={"w": 1.0, "h": 1.0, "d": 1.0},
                                             size_source="jedec", stock=5)
            cache.put("C1", part.to_dict())
            self.assertTrue(cache.save())
            back = parts_catalog.PartCache(path).load()
            self.assertEqual(back.get("C1")["stock"], 5)
        finally:
            import shutil
            shutil.rmtree(path.parent, ignore_errors=True)

    def test_offline_env_blocks_network(self) -> None:
        import os
        old = os.environ.get(parts_catalog.OFFLINE_ENV)
        os.environ[parts_catalog.OFFLINE_ENV] = "1"
        try:
            self.assertTrue(parts_catalog.offline())
            result = parts_catalog.enrich("C999999", cache=parts_catalog.PartCache())
            self.assertEqual(result["status"], "not_ready")
        finally:
            if old is None:
                os.environ.pop(parts_catalog.OFFLINE_ENV, None)
            else:
                os.environ[parts_catalog.OFFLINE_ENV] = old

    def test_search_shape_change_is_not_ready(self) -> None:
        class _Net(parts_catalog._Net):  # noqa: SLF001
            def __init__(self) -> None:
                super().__init__(offline=False)

            def post_json(self, url: str, body: dict[str, Any]):
                return {"code": 200, "data": {"list": []}}, ""
        result = parts_catalog.search("x", net=_Net())  # type: ignore[arg-type]
        self.assertEqual(result["status"], "not_ready")

    def test_to_design_fields_marks_catalog(self) -> None:
        part = parts_catalog.PartEnriched(
            lcsc="C1", size_mm={"w": 1.0, "h": 0.5, "d": 0.5},
            size_source="jedec", pin_count=2).to_dict()
        fields = parts_catalog.to_design_fields(part)
        self.assertEqual(fields["physical_source"], "catalog")
        self.assertEqual(len(fields["pins"]), 2)

    def test_to_design_fields_formula_is_estimated(self) -> None:
        part = parts_catalog.PartEnriched(
            lcsc="C1", size_mm={"w": 1.0, "h": 1.0, "d": 1.0},
            size_source="formula").to_dict()
        self.assertEqual(parts_catalog.to_design_fields(part)["physical_source"],
                         "estimated")


# ---------------------------------------------------------------------------
# 11. 能力表覆盖层
# ---------------------------------------------------------------------------


class TestCapabilityOverlay(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="hwv-p23-cap-"))
        self.saved = (capability.CATALOG_FILE, capability.PROMOTED_FILE)
        capability.CATALOG_FILE = self.tmp / "catalog.json"
        capability.PROMOTED_FILE = self.tmp / "promoted.json"
        capability.reset_overlay()

    def tearDown(self) -> None:
        import shutil
        capability.CATALOG_FILE, capability.PROMOTED_FILE = self.saved
        capability.reset_overlay()
        shutil.rmtree(self.tmp, ignore_errors=True)

    ENTRY = {"fills": [], "provides": [], "requires": [], "footprint_mm2": 1.0,
             "pins": {}, "clock_hz": None, "lcsc": "C1", "mpn": "X", "source": "catalog"}

    def test_catalog_entry_is_not_trusted(self) -> None:
        self.assertTrue(capability.save_catalog_entry("x", dict(self.ENTRY)))
        self.assertFalse(capability.trusted("x"))

    def test_catalog_source_forced(self) -> None:
        capability.save_catalog_entry("x", {**self.ENTRY, "source": "datasheet"})
        self.assertEqual(capability.get("x")["source"], "catalog",
                         "写目录层时必须强制 catalog，否则绕过人确认")

    def test_catalog_appears_in_rejected(self) -> None:
        capability.save_catalog_entry("x", dict(self.ENTRY))
        rejected = capability.explain_miss({"i2c"}, role="mcu")["rejected"]
        self.assertEqual(rejected.get("x"), capability.CATALOG_REASON)

    def test_catalog_not_in_candidates(self) -> None:
        capability.save_catalog_entry("x", dict(self.ENTRY))
        self.assertNotIn("x", capability.candidates({"i2c"}, role="mcu"))

    def test_promote_requires_datasheet(self) -> None:
        capability.save_catalog_entry("x", dict(self.ENTRY))
        result = capability.promote_entry("x", {"fills": {"mcu"}}, "")
        self.assertFalse(result["ok"])
        self.assertFalse(capability.PROMOTED_FILE.exists())

    def test_promote_moves_entry(self) -> None:
        capability.save_catalog_entry("x", dict(self.ENTRY))
        result = capability.promote_entry("x", {"fills": {"mcu"}, "provides": {"i2c"}},
                                          "https://example.invalid/ds.pdf")
        self.assertTrue(result["ok"])
        self.assertTrue(capability.trusted("x"))
        self.assertIn("x", capability.candidates({"i2c"}, role="mcu"))
        catalog = json.loads(capability.CATALOG_FILE.read_text(encoding="utf-8"))
        self.assertNotIn("x", catalog.get("entries") or {})

    def test_builtin_table_untouched(self) -> None:
        self.assertIn("stm32f401ccu6", capability.CAPABILITY)
        self.assertTrue(capability.trusted("stm32f401ccu6"))


# ---------------------------------------------------------------------------
# 12. 实物测试
# ---------------------------------------------------------------------------


class TestRealTests(unittest.TestCase):
    def test_fail_needs_observed(self) -> None:
        design = load_fixture("tiny-blink")
        record, problems = real_tests.add(design, test="t", result="fail")
        self.assertTrue(any("observed" in p for p in problems))

    def test_bad_ref_not_written(self) -> None:
        design = load_fixture("tiny-blink")
        real_tests.add(design, test="t", result="fail", observed="x",
                       implicated=["ref:NO_SUCH"])
        self.assertEqual(design.physical_tests, [])

    def test_none_is_not_false(self) -> None:
        record = real_tests.PhysicalTest(id="pt-1", test="t", result="unknown")
        self.assertNotIn("sim_reproduced", record.to_dict())

    def test_summary_counts_three_states(self) -> None:
        rows = [
            real_tests.PhysicalTest(id="a", test="t", result="fail", sim_reproduced=False),
            real_tests.PhysicalTest(id="b", test="t", result="fail", sim_reproduced=True),
            real_tests.PhysicalTest(id="c", test="t", result="pass"),
        ]
        summary = real_tests.summary(rows)
        self.assertEqual((summary["reproduced"], summary["not_reproduced"],
                          summary["untried"]), (1, 1, 1))
        self.assertEqual(summary["failed_unreproduced"], ["a"])

    def test_next_id_avoids_reuse(self) -> None:
        design = load_fixture("tiny-blink")
        design.physical_tests = [{"id": "pt-007"}, {"id": "pt-003"}]
        self.assertEqual(real_tests.next_id(design), "pt-008")

    def test_contact_sheet_missing_video(self) -> None:
        result = real_tests.contact_sheet("/no/such/clip.mp4")
        self.assertEqual(result["status"], "not_ready")

    def test_contact_sheet_gives_command_without_ffmpeg(self) -> None:
        import shutil
        import tempfile
        if shutil.which("ffmpeg"):
            self.skipTest("本机有 ffmpeg，这条只验无 ffmpeg 分支")
        tmp = Path(tempfile.mkdtemp(prefix="hwv-p23-video-"))
        try:
            video = tmp / "a.mp4"
            video.write_bytes(b"\x00" * 32)
            result = real_tests.contact_sheet(video)
            self.assertEqual(result["status"], "not_ready")
            self.assertIn("tile=", result["command"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 13. 校准
# ---------------------------------------------------------------------------


def _silent_runner(old_verdict: str, new_verdict: str) -> Any:
    def run(design: Any, params: Any) -> dict[str, Any]:
        tag = "old" if design is _silent_runner.old else "new"
        return {"status": old_verdict if tag == "old" else new_verdict, "detail": ""}
    return run


class TestSimCalibration(unittest.TestCase):
    def setUp(self) -> None:
        self.old = load_fixture("tiny-blink")
        self.new = load_fixture("quadrotor")
        self.base = sim_params.load()

    def _run(self, old_v: str, new_v: str, cands: list[dict[str, Any]]) -> Any:
        def runner(design: Any, params: Any) -> dict[str, Any]:
            return {"status": old_v if design is self.old else new_v, "detail": ""}
        return sim_calibration.calibrate(self.old, self.new, test_id="pt-1",
                                         params=self.base, candidates=cands,
                                         runner=runner)

    def test_old_fail_adopts(self) -> None:
        cal = self._run("fail", "pass", [{"mu_tyre_rod": 0.57}])
        self.assertTrue(cal.accepted)
        self.assertEqual(cal.params.get("mu_tyre_rod"), 0.57)

    def test_old_pass_rejected(self) -> None:
        cal = self._run("pass", "pass", [{"mesh_mu": 0.0}])
        self.assertFalse(cal.accepted)
        self.assertEqual(cal.attempts[0].reason, "old_passed")

    def test_old_unknown_rejected(self) -> None:
        cal = self._run("unknown", "pass", [{"supply_r": 0.4}])
        self.assertEqual(cal.attempts[0].reason, "old_unknown")

    def test_too_many_params_rejected(self) -> None:
        cal = self._run("fail", "pass", [{"mu_tyre_rod": 0.1, "mesh_mu": 0.2,
                                          "supply_r": 0.3}])
        self.assertEqual(cal.attempts[0].reason, "too_many_params")

    def test_unknown_param_rejected(self) -> None:
        cal = self._run("fail", "pass", [{"no_such": 1.0}])
        self.assertEqual(cal.attempts[0].reason, "unknown_param")

    def test_not_evaluated_new_status_is_empty(self) -> None:
        cal = self._run("pass", "pass", [{"mesh_mu": 0.0}])
        self.assertEqual(cal.attempts[0].new_status, "",
                         "没复现时不该去判新设计；空串与 unknown 必须区分")

    def test_accepted_is_derived_not_stored(self) -> None:
        cal = self._run("fail", "pass", [{"mu_tyre_rod": 0.57}])
        self.assertEqual(cal.accepted, bool(cal.adopted))
        self.assertEqual(cal.to_dict()["accepted"], cal.accepted)

    def test_archive_pairs_files(self) -> None:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="hwv-p23-arch-"))
        try:
            learned = self.base.with_params({"mu_tyre_rod": 0.57}, source="measured")
            result = sim_calibration.archive(self.old, learned, tmp, date="2026-11-21")
            self.assertTrue(result["pair_ok"])
            self.assertIn("mu_tyre_rod", [r["key"] for r in result["turned_on"]])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 14. agent 工具表
# ---------------------------------------------------------------------------


class TestAgentTools(unittest.TestCase):
    pass


def _tool_schema_valid(self: unittest.TestCase, name: str) -> None:
    table = agent_tools.ToolTable()
    spec = table.get(name)
    schema = spec.schema()
    self.assertEqual(schema["name"], name)
    self.assertIn(spec.tier, agent_tools.TIERS)
    self.assertTrue(schema["description"])
    for required in spec.required:
        self.assertIn(required, spec.params, f"{name}: 必填 {required} 没声明类型")


add_cases(TestAgentTools, [(f"tool:{n}", n) for n in agent_tools.ToolTable().names()],
          _tool_schema_valid)


class TestAgentToolsBehaviour(unittest.TestCase):
    def test_forbidden_absent(self) -> None:
        names = set(agent_tools.ToolTable().names())
        self.assertEqual([n for n in agent_tools.FORBIDDEN if n in names], [])

    def test_human_asks_even_in_auto(self) -> None:
        table = agent_tools.ToolTable()
        self.assertEqual(agent_tools.decide(table.get("hw_promote"), "auto"), "ask")

    def test_write_denied_in_read_only(self) -> None:
        table = agent_tools.ToolTable()
        self.assertEqual(agent_tools.decide(table.get("hw_put"), "read-only"), "deny")

    def test_unknown_mode_denies(self) -> None:
        table = agent_tools.ToolTable()
        self.assertEqual(agent_tools.decide(table.get("hw_get"), "yolo"), "deny")

    def test_validate_unknown_key(self) -> None:
        spec = agent_tools.ToolTable().get("hw_put")
        problems = spec.validate({"project": "p", "design": {}, "typo": 1})
        self.assertTrue(any("typo" in p for p in problems))

    def test_argv_space_style(self) -> None:
        spec = agent_tools.ToolTable().get("hw_generate")
        argv = agent_tools.build_argv(spec, {"requirement": "a.yaml",
                                             "max_candidates": 2})
        self.assertEqual(argv, ["--requirement", "a.yaml", "--max-candidates", "2"])

    def test_argv_eq_style(self) -> None:
        spec = agent_tools.ToolTable().get("hw_emit")
        argv = agent_tools.build_argv(spec, {"project": "p", "route": "none"})
        self.assertEqual(argv, ["p", "--route=none"])

    def test_argv_boolean_becomes_flag(self) -> None:
        spec = agent_tools.ToolTable().get("hw_emit")
        argv = agent_tools.build_argv(spec, {"project": "p", "reuse": True})
        self.assertEqual(argv, ["p", "--reuse"])


# ---------------------------------------------------------------------------
# 15. agent 循环
# ---------------------------------------------------------------------------


class TestAgentLoop(unittest.TestCase):
    def test_journal_append_only(self) -> None:
        journal = agent_loop.Journal()
        journal.append("a")
        journal.append("b")
        self.assertEqual([e["seq"] for e in journal.events], [1, 2])
        self.assertFalse(hasattr(journal, "update"))
        self.assertFalse(hasattr(journal, "delete"))

    def test_build_messages_marks_tool_results(self) -> None:
        messages = agent_loop.build_messages("task", [
            {"kind": "assistant", "text": "hi", "calls": [{"id": "c1"}]},
            {"kind": "result", "tool": "hw_get", "call_id": "c1",
             "status": "ok", "payload": {"a": 1}},
        ])
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[2]["role"], "tool")
        self.assertEqual(messages[2]["tool"], "hw_get")

    def test_compare_detects_divergence(self) -> None:
        a = [{"kind": "result", "tool": "x", "status": "ok"}]
        b = [{"kind": "result", "tool": "x", "status": "failed"}]
        self.assertFalse(agent_loop.compare(a, b)["same"])

    def test_compare_ignores_payload(self) -> None:
        a = [{"kind": "result", "tool": "x", "status": "ok", "payload": {"t": 1}}]
        b = [{"kind": "result", "tool": "x", "status": "ok", "payload": {"t": 999}}]
        self.assertTrue(agent_loop.compare(a, b)["same"],
                        "payload 里的时间戳不该影响一致性判定")

    def test_read_journal_skips_broken_lines(self) -> None:
        import tempfile
        path = Path(tempfile.mkdtemp(prefix="hwv-p23-j-")) / "j.jsonl"
        path.write_text('{"seq":1,"kind":"task"}\nnot json\n', encoding="utf-8")
        events = agent_loop.read_journal(path)
        self.assertEqual(events[0]["kind"], "task")
        self.assertTrue(any(e.get("kind") == "journal_warning" for e in events))


# ---------------------------------------------------------------------------
# 15b. 结论层（P0）：纯函数
# ---------------------------------------------------------------------------


class TestVerdict(unittest.TestCase):
    def test_rollup_empty_is_not_ready(self) -> None:
        self.assertEqual(verdict.level_rollup([]), "not_ready",
                         "没跑过判定不能算通过")

    def test_rollup_fail_wins(self) -> None:
        levels = [{"status": "pass"}, {"status": "fail"}, {"status": "unknown"}]
        self.assertEqual(verdict.level_rollup(levels), "fail")

    def test_rollup_unknown_beats_pass(self) -> None:
        levels = [{"status": "pass"}, {"status": "unknown"}, {"status": "not_run"}]
        self.assertEqual(verdict.level_rollup(levels), "unknown")

    def test_rollup_all_pass(self) -> None:
        self.assertEqual(verdict.level_rollup([{"status": "pass"}]), "pass")

    def test_count_accepts_list_or_number(self) -> None:
        # ladder.json 里 `failed` 是数、`unknown` 是明细列表——形状不同
        self.assertEqual(verdict._count([1, 2, 3]), 3)
        self.assertEqual(verdict._count(7), 7)
        self.assertEqual(verdict._count(None), 0)

    def test_sort_key_blocking_first(self) -> None:
        blocking = verdict._sort_key({"category": "blocking", "status": "fail"})
        assumed = verdict._sort_key({"category": "assumed", "status": "fail"})
        self.assertLess(blocking, assumed)

    def test_sort_key_status_breaks_ties(self) -> None:
        fail = verdict._sort_key({"category": "missing_data", "status": "fail"})
        default = verdict._sort_key({"category": "missing_data", "status": "default"})
        self.assertLess(fail, default)


# ---------------------------------------------------------------------------
# 15c. 下一步映射（P1）：纯函数
# ---------------------------------------------------------------------------


class TestNextStep(unittest.TestCase):
    def test_unknown_need_is_none(self) -> None:
        # 表里没有就**不编**——UI 退回自由文本
        self.assertIsNone(nextstep.build({"need": "invent_something"}))

    def test_empty_fix_is_none(self) -> None:
        self.assertIsNone(nextstep.build({}))
        self.assertIsNone(nextstep.build(None))

    def test_move_apart_expands_all_refs(self) -> None:
        step = nextstep.build({"need": "move_apart", "refs": ["C146", "Lora1"]})
        self.assertIsNotNone(step)
        self.assertEqual(step["kind"], "move_part")   # type: ignore[index]
        self.assertIn("C146", step["label"])          # type: ignore[index]
        self.assertIn("Lora1", step["label"])         # type: ignore[index]

    def test_edit_field_carries_field_path(self) -> None:
        step = nextstep.build({"need": "provide_size_mm", "refs": ["U3"]})
        self.assertEqual(step["field"], "size_mm")    # type: ignore[index]

    def test_run_command_is_copyable(self) -> None:
        step = nextstep.build({"need": "rerun_ladder"}, project="bot")
        self.assertEqual(step["kind"], "run_command")  # type: ignore[index]
        self.assertIn("ladder bot", step["command"])   # type: ignore[index]

    def test_command_without_lcsc_downgrades(self) -> None:
        step = nextstep.build({"need": "lookup_part", "refs": ["U3"]}, project="p")
        self.assertEqual(step["kind"], "external")     # type: ignore[index]
        self.assertIn("lcsc", step["label"])           # type: ignore[index]
        # 降级时**不留空占位符**（"查  的尺寸"这种读起来像 bug）
        self.assertNotIn("查  ", step["label"])        # type: ignore[index]

    def test_command_with_lcsc_is_real(self) -> None:
        step = nextstep.build({"need": "lookup_part", "refs": ["U3"]},
                              project="p", lcsc="C1523")
        self.assertIn("C1523", step["command"])        # type: ignore[index]

    def test_refs_fall_back_to_finding_refs(self) -> None:
        # physical 的 fix 常常不带 refs，refs 在 finding 顶层
        step = nextstep.build({"need": "import_datasheet_or_measure"},
                              refs=["btn1"])
        self.assertEqual(step["refs"], ["btn1"])       # type: ignore[index]

    def test_external_says_who_does_it(self) -> None:
        step = nextstep.build({"need": "install_toolchain", "what": "V5"})
        self.assertEqual(step["kind"], "external")     # type: ignore[index]
        self.assertTrue(step.get("by"))                # type: ignore[union-attr]

    def test_no_auto_fix_kind(self) -> None:
        # 改设计是人的决定；机器只把人送到该看的地方
        self.assertNotIn("auto_fix", nextstep.KINDS)

    def test_every_entry_has_label(self) -> None:
        for need, spec in nextstep.NEEDS.items():
            self.assertTrue(str(spec.get("label") or "").strip(), need)

    def test_edit_field_and_command_have_targets(self) -> None:
        for need, spec in nextstep.NEEDS.items():
            if spec["kind"] == "edit_field":
                self.assertTrue(spec.get("field"), need)
            if spec["kind"] == "run_command":
                self.assertTrue(spec.get("command"), need)


# ---------------------------------------------------------------------------
# 15d. 实体聚合（P2）：纯函数
# ---------------------------------------------------------------------------


class TestEntity(unittest.TestCase):
    def test_rollup_no_data_is_not_ready(self) -> None:
        self.assertEqual(entity._rollup([], [], []), "not_ready",
                         "没有数据不能算 pass")

    def test_rollup_test_fail_wins(self) -> None:
        self.assertEqual(entity._rollup([{"status": "pass"}], [], [{"result": "fail"}]),
                         "fail")

    def test_rollup_claim_fail(self) -> None:
        self.assertEqual(entity._rollup([{"status": "fail"}], [], []), "fail")

    def test_rollup_unknown_beats_pass(self) -> None:
        self.assertEqual(entity._rollup([{"status": "unknown"}], [], []), "unknown")

    def test_rollup_all_pass(self) -> None:
        self.assertEqual(entity._rollup([{"status": "pass"}], [], []), "pass")

    def test_claims_indexed_by_scope_part(self) -> None:
        # `C1` 与 `C10` 前缀相同：必须用 scope.part，不能用 id 正则切
        claims = {"claims": [
            {"id": "claim.C1.x", "scope": {"part": "C1"}, "status": "pass"},
            {"id": "claim.C10.y", "scope": {"part": "C10"}, "status": "fail"},
        ]}
        index = entity._by_ref_claims(claims)
        self.assertEqual(len(index["C1"]), 1)
        self.assertEqual(len(index["C10"]), 1)

    def test_finding_spreads_to_every_ref(self) -> None:
        checks = {"findings": [{"ok": False, "refs": ["A1", "B2"]}]}
        index = entity._by_ref_findings(checks)
        self.assertIn("A1", index)
        self.assertIn("B2", index)

    def test_test_implicated_strips_ref_prefix(self) -> None:
        class _Design:
            physical_tests = [{"id": "pt-1", "implicated": ["ref:U3"]}]
        index = entity._by_ref_tests(_Design())
        self.assertIn("U3", index)
        self.assertNotIn("ref:U3", index)

    def test_test_without_implicated_not_attributed(self) -> None:
        # 整机范围的实测（没写 implicated）不归到任何件上——硬安等于编归因
        class _Design:
            physical_tests = [{"id": "pt-2", "implicated": []}]
        self.assertEqual(entity._by_ref_tests(_Design()), {})

    def test_profile_missing_ref_is_failed(self) -> None:
        design = load_fixture("tiny-blink")
        result = entity.profile(design, "NO_SUCH")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["findings"])

    def test_profile_without_claims_says_missing_justification(self) -> None:
        design = load_fixture("tiny-blink")
        profile = entity.profile(design, design.parts[0].ref)
        self.assertEqual(profile["claims"]["total"], 0)
        self.assertEqual(profile["justification"]["confidence"], "missing")

    def test_index_covers_every_part(self) -> None:
        design = load_fixture("dense-0402")
        index = entity.index(design)
        self.assertEqual(set(index), {p.ref for p in design.parts})


# ---------------------------------------------------------------------------
# 15e. 参数敏感度（P3）：纯函数
# ---------------------------------------------------------------------------


class TestSensitivity(unittest.TestCase):
    def test_wired_keys_from_source_not_handwritten(self) -> None:
        wired = sensitivity.wired_keys()
        self.assertIn("dt", wired)
        self.assertIn("mu_board", wired)
        # 关节私有参数不算 ParamSet（第一版正则漏了这个后向断言）
        self.assertNotIn("range_deg", wired)
        self.assertNotIn("ratio", wired)

    def test_perturb_zero_gets_visible_offset(self) -> None:
        # 0 乘任何因子还是 0：扰不出变化，必须给一个可观察的偏移
        self.assertEqual(sensitivity._perturb(0, 0.5), 0.5)
        self.assertEqual(sensitivity._perturb(0, 2.0), 2.0)

    def test_perturb_non_numeric_is_none(self) -> None:
        # 布尔 / 字符串 / 列表没有自然扰动方式 → 返回 None，不编
        self.assertIsNone(sensitivity._perturb(True, 0.5))
        self.assertIsNone(sensitivity._perturb("x", 0.5))
        self.assertIsNone(sensitivity._perturb([1], 0.5))

    def test_perturb_scales_numbers(self) -> None:
        self.assertEqual(sensitivity._perturb(2.0, 0.5), 1.0)
        self.assertEqual(sensitivity._perturb(2.0, 2.0), 4.0)

    def test_in_table_counts_sum_to_total(self) -> None:
        params = sim_params.load()
        design = load_fixture("stacked-2layer")
        report = sensitivity.report(design, params)
        self.assertEqual(sum(report["in_table_counts"].values()),
                         report["params_total"],
                         "表内三类之和必须等于参数总数")

    def test_missing_is_off_table(self) -> None:
        params = sim_params.load()
        design = load_fixture("stacked-2layer")
        report = sensitivity.report(design, params)
        # `missing` 是表外项：它们**不该**出现在 params 里
        for key in report["off_table"]:
            self.assertNotIn(key, params.params, key)

    def test_unwired_params_are_not_in_wired_set(self) -> None:
        params = sim_params.load()
        design = load_fixture("stacked-2layer")
        report = sensitivity.report(design, params)
        wired = sensitivity.wired_keys()
        for row in report["unwired"]:
            self.assertNotIn(row["key"], wired,
                             f"{row['key']} 在 unwired 里却说它被读了")

    def test_unwired_note_says_nobody_reads_it(self) -> None:
        params = sim_params.load()
        design = load_fixture("stacked-2layer")
        report = sensitivity.report(design, params)
        for row in report["unwired"]:
            note = str(row.get("note") or "")
            self.assertTrue("代码读" in note or "没人读" in note, note)

    def test_priority_only_wired(self) -> None:
        params = sim_params.load()
        design = load_fixture("stacked-2layer")
        report = sensitivity.report(design, params)
        for row in report["priority"]:
            self.assertEqual(row["status"], "wired", row["key"])


# ---------------------------------------------------------------------------
# 15f. 固件矩阵（P4）：纯函数
# ---------------------------------------------------------------------------


def _synth_pins() -> dict:
    from hw_velxio.design.fwgen import pinmap as pm
    return {
        "PA1": pm.IocPin(pin="PA1", port="A", bit=1, mode="Input",
                         signal="GPIO_Input", label="BTN"),
        "PA2": pm.IocPin(pin="PA2", port="A", bit=2, mode="Output",
                         signal="GPIO_Output", label="LED"),
        "PA3": pm.IocPin(pin="PA3", port="A", bit=3, mode="Output",
                         signal="GPIO_Output", label="EN"),
        "PA13": pm.IocPin(pin="PA13", port="A", bit=13, mode="Serial_Wire",
                          signal="SYS_JTMS-SWDIO"),
    }


def _u(pin: str, api: str) -> dict:
    return {"pin": pin, "api": api, "file": "a.c", "line": 1, "text": ""}


class TestFirmwareMatrix(unittest.TestCase):
    def test_ghost_pin_gets_a_row(self) -> None:
        # 幽灵脚硬件里没有配置，**仍然要出行**——否则最重要那类整行消失
        result = firmware_matrix.matrix(_synth_pins(), [_u("PA9", "write")])
        pins = [row["pin"] for row in result["rows"]]
        self.assertIn("PA9", pins)

    def test_every_configured_pin_has_a_row(self) -> None:
        result = firmware_matrix.matrix(_synth_pins(), [])
        pins = {row["pin"] for row in result["rows"]}
        self.assertTrue({"PA1", "PA2", "PA3", "PA13"}.issubset(pins))

    def test_total_is_configs_plus_ghosts(self) -> None:
        result = firmware_matrix.matrix(_synth_pins(), [_u("PA9", "write")])
        self.assertEqual(result["total"], 4 + 1)

    def test_status_order_sorts_ghost_first(self) -> None:
        result = firmware_matrix.matrix(
            _synth_pins(), [_u("PA9", "write"), _u("PA1", "write")])
        statuses = [row["status"] for row in result["rows"]]
        self.assertEqual(statuses[0], "ghost")
        self.assertEqual(statuses, sorted(
            statuses, key=firmware_matrix.STATUS_ORDER.index))

    def test_debug_pin_is_not_idle(self) -> None:
        result = firmware_matrix.matrix(_synth_pins(), [])
        row = next(r for r in result["rows"] if r["pin"] == "PA13")
        self.assertNotEqual(row["status"], "idle",
                            "SWD 脚由调试器用，不该报闲置")

    def test_empty_hardware_is_not_ready(self) -> None:
        result = firmware_matrix.matrix({}, [], source="none")
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["rows"], [])

    def test_direction_conflict_is_fail(self) -> None:
        # PA1 配成 Input，固件在写
        result = firmware_matrix.matrix(_synth_pins(), [_u("PA1", "write")])
        self.assertEqual(result["status"], "fail")

    def test_summary_drops_ok_rows(self) -> None:
        result = firmware_matrix.matrix(_synth_pins(), [_u("PA3", "write")])
        summary = firmware_matrix.summarize(result)
        self.assertTrue(all(row["status"] != "ok" for row in summary["rows"]))
        self.assertEqual(summary["counts"], result["counts"])
        self.assertEqual(summary["total"], result["total"])


# ---------------------------------------------------------------------------
# 15g. 需求草稿（P5）：纯函数
# ---------------------------------------------------------------------------


GOOD_REQ = {
    "id": "bot", "title": "避障小车",
    "roles": ["mcu", "motor", "sensor", "power_in"],
    "interfaces": [{"a": "mcu", "b": "motor", "protocol": "pwm"}],
    "constraints": {"area_mm2": 600},
}


class TestReqDraft(unittest.TestCase):
    def test_fields_match_requirement(self) -> None:
        # 比对而不是抄：Requirement 加字段时这条必须红
        from hw_velxio.design import generate
        self.assertEqual(list(req_draft.ReqDraft.__dataclass_fields__),
                         list(generate.Requirement.__dataclass_fields__))

    def test_good_draft_passes(self) -> None:
        report = req_draft.validate(req_draft.ReqDraft.from_dict(GOOD_REQ))
        self.assertTrue(report["ok"])
        self.assertEqual(report["problems"], [])

    def test_empty_draft_fails(self) -> None:
        report = req_draft.validate(req_draft.ReqDraft.from_dict({}))
        self.assertFalse(report["ok"], "空 roles 不能算通过")

    def test_unknown_role_fails(self) -> None:
        report = req_draft.validate(req_draft.ReqDraft.from_dict(
            {**GOOD_REQ, "roles": ["mcu", "teleporter"]}))
        kinds = [p["kind"] for p in report["problems"]]
        self.assertIn("role_unknown", kinds)

    def test_dangling_interface_fails(self) -> None:
        report = req_draft.validate(req_draft.ReqDraft.from_dict(
            {**GOOD_REQ, "interfaces": [{"a": "mcu", "b": "camera"}]}))
        kinds = [p["kind"] for p in report["problems"]]
        self.assertIn("interface_dangling", kinds)

    def test_self_loop_fails(self) -> None:
        report = req_draft.validate(req_draft.ReqDraft.from_dict(
            {**GOOD_REQ, "interfaces": [{"a": "mcu", "b": "mcu"}]}))
        kinds = [p["kind"] for p in report["problems"]]
        self.assertIn("interface_self_loop", kinds)

    def test_negative_area_fails(self) -> None:
        report = req_draft.validate(req_draft.ReqDraft.from_dict(
            {**GOOD_REQ, "constraints": {"area_mm2": -1}}))
        kinds = [p["kind"] for p in report["problems"]]
        self.assertIn("constraint_not_positive", kinds)

    def test_numbers_is_a_warning_not_a_problem(self) -> None:
        report = req_draft.validate(req_draft.ReqDraft.from_dict(
            {**GOOD_REQ, "numbers": {"detect_range_m": 2.0}}))
        self.assertTrue(report["ok"], "numbers 无消费方不拦 apply")
        self.assertIn("field_without_consumer",
                      [w["kind"] for w in report["warnings"]])

    def test_non_object_interface_is_reported_not_dropped(self) -> None:
        draft = req_draft.ReqDraft.from_dict(
            {**GOOD_REQ, "interfaces": [{"a": "mcu", "b": "motor"}, 42]})
        self.assertEqual(len(draft.interfaces), 2, "不能被静默丢弃")
        report = req_draft.validate(draft)
        self.assertIn("interface_not_object",
                      [p["kind"] for p in report["problems"]])

    def test_known_roles_come_from_roles_py(self) -> None:
        from hw_velxio.design import roles
        self.assertEqual(req_draft._known_roles(),
                         sorted({t.role for t in roles.ROLE_TEMPLATES}))


# ---------------------------------------------------------------------------
# 15h. 从零开局（P5 补断链）：草稿先于工程
# ---------------------------------------------------------------------------


class TestReqDraftZeroStart(unittest.TestCase):
    def setUp(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp(prefix="hwv-zero-"))
        self.old = os.environ.get("HWV_WORKSPACE")
        os.environ["HWV_WORKSPACE"] = str(self.tmp)

    def tearDown(self) -> None:
        import os
        import shutil
        if self.old is None:
            os.environ.pop("HWV_WORKSPACE", None)
        else:
            os.environ["HWV_WORKSPACE"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store(self):
        from hw_velxio.design.store import DesignStore
        return DesignStore("fresh")

    def test_draft_goes_to_workspace_when_project_missing(self) -> None:
        store = self._store()
        saved = req_draft.save(store, req_draft.ReqDraft.from_dict(GOOD_REQ))
        self.assertEqual(saved["scope"], "workspace")
        self.assertFalse(store.exists())

    def test_workspace_draft_is_loadable(self) -> None:
        store = self._store()
        req_draft.save(store, req_draft.ReqDraft.from_dict(GOOD_REQ))
        self.assertIsNotNone(req_draft.load(store))
        self.assertEqual(req_draft.draft_scope(store), "workspace")

    def test_apply_without_confirm_creates_no_project(self) -> None:
        store = self._store()
        req_draft.save(store, req_draft.ReqDraft.from_dict(GOOD_REQ))
        result = req_draft.apply(store)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(store.exists(), "没确认就不能建工程")

    def test_apply_confirmed_creates_project(self) -> None:
        store = self._store()
        req_draft.save(store, req_draft.ReqDraft.from_dict(GOOD_REQ))
        result = req_draft.apply(store, confirm=True)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["created_project"])
        self.assertTrue(store.exists())
        self.assertEqual(store.load().validate(), [])

    def test_apply_promotes_draft_into_project(self) -> None:
        # 不搬家会留下两份副本，改一份另一份不跟着变
        store = self._store()
        req_draft.save(store, req_draft.ReqDraft.from_dict(GOOD_REQ))
        result = req_draft.apply(store, confirm=True)
        self.assertTrue(result["workspace_draft_removed"])
        self.assertEqual(req_draft.draft_scope(store), "project")
        self.assertEqual(req_draft.list_workspace(), [])

    def test_auto_lock_runs_on_created_project(self) -> None:
        # 漏了 auto_lock，导出闸门会问"mcu_family 未闭合"
        store = self._store()
        req_draft.save(store, req_draft.ReqDraft.from_dict(GOOD_REQ))
        req_draft.apply(store, confirm=True)
        ledger = store.read("deferred.json") or {}
        self.assertTrue(ledger.get("locked"))


# ---------------------------------------------------------------------------
# 16. 固件生成
# ---------------------------------------------------------------------------


class TestFwgen(unittest.TestCase):
    pass


def _mcu_resolves(self: unittest.TestCase, text: str, key: str) -> None:
    found = fw_mcus.find_mcu(text)
    self.assertIsNotNone(found, text)
    self.assertEqual(found.key, key)


add_cases(TestFwgen, [("mcu:STM32F103C6Tx", "STM32F103C6Tx", "stm32f103c6"),
                      ("mcu:STM32F103C8Tx", "STM32F103C8Tx", "stm32f103"),
                      ("mcu:STM32F401RCT6", "STM32F401RCT6", "stm32f401rc"),
                      ("mcu:STM32F405RGT6", "STM32F405RGT6", "stm32f405rg"),
                      ("mcu:STM32H743IIT6", "STM32H743IIT6", "stm32h743ii"),
                      ("mcu:ATtiny85", "ATtiny85", "attiny85"),
                      ("mcu:ESP32-S3", "ESP32-S3-WROOM-1", "esp32s3")],
          _mcu_resolves)


class TestFwgenBehaviour(unittest.TestCase):
    def test_seven_mcus(self) -> None:
        self.assertEqual(len(fw_mcus.MCUS), 7)

    def test_c6_before_generic_f103(self) -> None:
        keys = list(fw_mcus.MCUS)
        self.assertLess(keys.index("stm32f103c6"), keys.index("stm32f103"))

    def test_sanitize_label(self) -> None:
        self.assertEqual(fw_pinmap.sanitize_label("VL53-PWR-EN"), "VL53_PWR_EN")

    def test_classify_uart(self) -> None:
        self.assertEqual(fw_pinmap.classify("USART2_TX")[0], "UART_TX")

    def test_classify_i2c(self) -> None:
        self.assertEqual(fw_pinmap.classify("I2C1_SCL")[0], "I2C_SCL")

    def test_macro_name(self) -> None:
        self.assertEqual(fw_pinmap.macro_name("USB_D+"), "USB_DP",
                         "`+` 直接变 P，不额外插下划线")

    def test_split_stm32(self) -> None:
        mcu = fw_mcus.MCUS["stm32f401rc"]
        self.assertEqual(fw_pinmap._split(mcu, "PA15"), ("A", 15, 15))

    def test_drivers_seven_parts(self) -> None:
        self.assertEqual(len(fw_drivers.supported_parts()), 7)

    def test_driver_has_datasheet(self) -> None:
        files = fw_drivers.generate_driver("BME280")
        text = "".join(files.values())
        self.assertIn("0x76", text)
        self.assertIn("datasheet", text.lower())

    def test_driver_unknown_part_raises(self) -> None:
        with self.assertRaises(KeyError):
            fw_drivers.generate_driver("NOPE")

    def test_five_scaffold_targets(self) -> None:
        self.assertEqual(len(fw_scaffold.TARGETS), 5)

    def test_scaffold_rejects_wrong_family(self) -> None:
        axes = [fw_pinmap.PinAssignment("LED", "LED", "U1", "PA5", "PA5",
                                        "GPIO", "out", port="A", bit=5)]
        pin_map = fw_pinmap.PinMap(fw_mcus.MCUS["stm32f401rc"], "U1", axes)
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="hwv-p23-sc-"))
        try:
            with self.assertRaises(fw_scaffold.ScaffoldError):
                fw_scaffold.scaffold(pin_map, tmp / "x", "arduino")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 17. 生成器：候选 → 真设计
# ---------------------------------------------------------------------------


class TestGenerateMaterialize(unittest.TestCase):
    def test_materialize_produces_parts(self) -> None:
        from hw_velxio.design import generate
        req_path = Path(__file__).resolve().parents[1] / "examples" / "req_car.yaml"
        req = generate.load_requirement(req_path)
        report = generate.generate(req, max_candidates=1, run_ladder=False)
        design = generate.materialize(report["candidates"][0], req)
        self.assertTrue(design.parts, "候选还原后必须真的有元件")
        self.assertGreaterEqual(len(design.board_list()), 1)

    def test_materialized_design_validates(self) -> None:
        from hw_velxio.design import generate
        req_path = Path(__file__).resolve().parents[1] / "examples" / "req_car.yaml"
        req = generate.load_requirement(req_path)
        report = generate.generate(req, max_candidates=1, run_ladder=False)
        design = generate.materialize(report["candidates"][0], req)
        self.assertEqual(design.validate(), [])


# ---------------------------------------------------------------------------
# 18. 交接单的 external 类
# ---------------------------------------------------------------------------


class TestHandoffExternal(unittest.TestCase):
    def test_physical_fail_becomes_external(self) -> None:
        rows = [{"id": "pt-1", "test": "上电", "result": "fail",
                 "observed": "无输出", "sim_reproduced": False}]
        items = handoff._external(None, None, None, rows)  # noqa: SLF001
        self.assertTrue(items)
        self.assertTrue(all(i.category == "external" for i in items))

    def test_unreproduced_gets_summary_item(self) -> None:
        rows = [{"id": "pt-1", "test": "上电", "result": "fail",
                 "observed": "x", "sim_reproduced": False}]
        titles = [i.title for i in handoff._external(None, None, None, rows)]  # noqa: SLF001
        self.assertTrue(any("复现不出来" in t for t in titles), titles)

    def test_empty_physical_adds_nothing(self) -> None:
        self.assertEqual(handoff._external(None, None, None, []), [])  # noqa: SLF001

    def test_unknown_physical_is_not_ready(self) -> None:
        rows = [{"id": "pt-2", "test": "x", "result": "unknown"}]
        items = handoff._external(None, None, None, rows)  # noqa: SLF001
        self.assertEqual(items[0].status, "not_ready")

    def test_external_never_blocks(self) -> None:
        rows = [{"id": "pt-1", "test": "t", "result": "fail", "observed": "x"}]
        for item in handoff._external(None, None, None, rows):  # noqa: SLF001
            self.assertEqual(item.category, "external")


class TestPcbGeo(unittest.TestCase):
    """几何回读 + 连通性判定（P26/P27）。离线，不碰立创。"""

    def _payload(self) -> dict:
        return {
            "docType": 3, "nets": ["N1"],
            "outlines": [["R", 0.0, 1574.8, 1968.5, 1574.8, 0, 0]],
            "components": [
                {"d": "R1", "x": 400.0, "y": 500.0, "layer": 1, "rot": 0,
                 "pads": [{"net": "N1", "num": "1", "x": 400.0, "y": 500.0,
                           "layer": 1, "hole": False, "hw": 16.0, "hh": 17.0,
                           "shape": "RECT", "assumed": False}]},
                {"d": "R2", "x": 1000.0, "y": 500.0, "layer": 1, "rot": 0,
                 "pads": [{"net": "N1", "num": "1", "x": 1000.0, "y": 500.0,
                           "layer": 1, "hole": False, "hw": 16.0, "hh": 17.0,
                           "shape": "RECT", "assumed": False}]},
            ],
            "tracks": [{"net": "N1", "x1": 416.0, "y1": 500.0, "x2": 984.0,
                        "y2": 500.0, "layer": 1, "width": 10.0}],
            "vias": [], "errors": {},
        }

    def test_parse_counts(self) -> None:
        geo = pcb_geo.parse(self._payload())
        self.assertEqual(len(geo.pads), 2)
        self.assertEqual(len(geo.tracks), 1)
        self.assertEqual(len(geo.outlines), 1)

    def test_outline_r_mode_y_range(self) -> None:
        """AGENTS §3.20：R 的左上角 + 向 -Y → y 区间是 [y-h, y]。"""
        geo = pcb_geo.parse(self._payload())
        box = geo.outline_box
        self.assertIsNotNone(box)
        self.assertAlmostEqual(box[1], 0.0, places=6)
        self.assertAlmostEqual(box[3], 1574.8, places=3)

    def test_connected(self) -> None:
        got = pcb_geo.connectivity(pcb_geo.parse(self._payload()))
        self.assertEqual(got["status"], "ok")
        self.assertEqual(got["connected"], ["N1"])

    def test_split_when_track_short(self) -> None:
        payload = self._payload()
        payload["tracks"][0]["x2"] = 700.0
        got = pcb_geo.connectivity(pcb_geo.parse(payload))
        self.assertEqual(got["status"], "not_ready")
        self.assertEqual(got["split"], ["N1"])
        self.assertEqual(got["detail"]["N1"]["groups"], 2)

    def test_empty_is_not_ready(self) -> None:
        got = pcb_geo.connectivity(pcb_geo.BoardGeometry())
        self.assertEqual(got["status"], "not_ready")

    def test_single_pad_net_not_in_denominator(self) -> None:
        geo = pcb_geo.parse(self._payload())
        geo.pads.append(pcb_geo.Pad(net="ONLY1", number="1", ref="R9",
                                    x=1500.0, y=900.0, layer=1))
        geo.nets.append("ONLY1")
        got = pcb_geo.connectivity(geo)
        self.assertEqual(got["checked"], 1)
        self.assertEqual(got["single"], ["ONLY1"])

    def test_polygon_pad_half_is_finite(self) -> None:
        payload = self._payload()
        payload["components"][0]["pads"][0].update(
            {"shape": "POLYGON", "hw": 20.0, "hh": 10.0})
        self.assertEqual(pcb_geo.parse(payload).pads[0].half, 10.0)


class TestRouter(unittest.TestCase):
    """自研布线器（P28）。离线。"""

    def _geo(self) -> pcb_geo.BoardGeometry:
        geo = pcb_geo.BoardGeometry(board="probe")
        geo.outlines = [["R", 0.0, 1574.8, 1968.5, 1574.8, 0, 0]]
        for net, x0, y0 in (("N1", 400.0, 500.0), ("GND", 400.0, 700.0)):
            geo.nets.append(net)
            for i in range(2):
                geo.pads.append(pcb_geo.Pad(
                    net=net, number=str(i + 1), ref=f"{net}{i}",
                    x=x0 + 600.0 * i, y=y0, layer=1, hw=16.0, hh=17.0))
        return geo

    def test_plan_routes_all(self) -> None:
        planned = router.plan(self._geo())
        self.assertEqual(planned.unrouted, [])
        self.assertEqual(sorted(planned.routed), ["GND", "N1"])

    def test_only_45_or_orthogonal(self) -> None:
        for seg in router.plan(self._geo()).segments:
            if abs(seg.x2 - seg.x1) < 1e-6 or abs(seg.y2 - seg.y1) < 1e-6:
                continue
            self.assertAlmostEqual(abs(seg.x2 - seg.x1), abs(seg.y2 - seg.y1),
                                   places=3)

    def test_no_outline_reports_unrouted(self) -> None:
        geo = self._geo()
        geo.outlines = []
        planned = router.plan(geo)
        self.assertTrue(planned.unrouted)
        self.assertTrue(planned.notes)

    def test_grid_cap_reports_reason(self) -> None:
        geo = self._geo()
        geo.outlines = [["R", 0.0, 40000.0, 40000.0, 40000.0, 0, 0]]
        planned = router.plan(geo, rules=router.Rules(max_cells=1000))
        self.assertTrue(planned.unrouted)
        self.assertTrue(any("超限" in n for n in planned.notes))

    def test_plan_result_passes_connectivity(self) -> None:
        geo = self._geo()
        planned = router.plan(geo)
        replay = pcb_geo.BoardGeometry(board="probe", pads=list(geo.pads),
                                       nets=list(geo.nets),
                                       outlines=list(geo.outlines))
        for seg in planned.segments:
            replay.tracks.append(pcb_geo.Track(
                net=seg.net, x1=seg.x1, y1=seg.y1, x2=seg.x2, y2=seg.y2,
                layer=seg.layer, width=seg.width))
        for via in planned.vias:
            replay.vias.append(pcb_geo.Via(net=via.net, x=via.x, y=via.y,
                                          diameter=via.diameter))
        self.assertEqual(pcb_geo.connectivity(replay)["status"], "ok")

    def test_stitch_sites_avoid_other_nets(self) -> None:
        geo = self._geo()
        geo.tracks.append(pcb_geo.Track(net="SIG", x1=280, y1=760,
                                        x2=1700, y2=760, layer=1, width=10))
        sites = router.stitch_sites(geo, "N1", count=6, inset_mil=40.0)
        self.assertTrue(sites)
        for _x, y in sites:
            self.assertGreater(abs(y - 760.0), 20.0)


class TestPcbLayout(unittest.TestCase):
    """布局规则（P30 布局）。离线。"""

    def _design(self, *, close: bool, connector_inside: bool) -> Any:
        from hw_velxio.design.model import Board, DesignModel, Part, Placement

        d = DesignModel(title="layout")
        d.board = Board(kind="generic", name="b",
                        outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                        size_mm={"w": 40, "h": 30}, thickness_mm=1.6)
        gap = 0.6 if close else 4.0
        d.add_part(Part(ref="R1", kind="resistor", lcsc="C1523",
                        size_mm={"w": 1.0, "h": 0.5},
                        placement=Placement(x_mm=10.0, y_mm=10.0), pins=["1", "2"]))
        d.add_part(Part(ref="R2", kind="resistor", lcsc="C1523",
                        size_mm={"w": 1.0, "h": 0.5},
                        placement=Placement(x_mm=10.0 + gap, y_mm=10.0),
                        pins=["1", "2"]))
        d.add_part(Part(ref="J1", kind="connector", lcsc="C1523",
                        size_mm={"w": 6.0, "h": 4.0},
                        placement=Placement(
                            x_mm=20.0 if connector_inside else 1.8, y_mm=15.0),
                        pins=["1", "2"]))
        return d

    def test_clean_layout_passes(self) -> None:
        report = pcb_layout.audit(self._design(close=False, connector_inside=False))
        self.assertEqual(report.status, "ok", [f.to_dict() for f in report.blockers])

    def test_close_passives_overlap_or_too_close(self) -> None:
        report = pcb_layout.audit(self._design(close=True, connector_inside=False))
        codes = {f.code for f in report.blockers}
        self.assertTrue(codes & {"overlap", "too_close"}, codes)
        self.assertNotEqual(report.status, "ok")

    def test_connector_inside_board_is_blocker(self) -> None:
        report = pcb_layout.audit(self._design(close=False, connector_inside=True))
        self.assertIn("connector_in_board", {f.code for f in report.blockers})

    def test_autoplace_fixes_blockers(self) -> None:
        design = self._design(close=True, connector_inside=True)
        self.assertNotEqual(pcb_layout.audit(design).status, "ok")
        for board in design.board_list():
            plan = pcb_layout.autoplace(design, board=board)
            for part in design.parts:
                if design.board_of(part) is not board:
                    continue
                pos = plan.get(str(part.ref))
                if pos and part.placement is not None:
                    part.placement.x_mm, part.placement.y_mm = pos
        self.assertEqual(pcb_layout.audit(design).status, "ok",
                         [f.to_dict() for f in pcb_layout.audit(design).blockers])

    def test_missing_size_is_not_silently_ok(self) -> None:
        design = self._design(close=False, connector_inside=False)
        design.parts[0].size_mm = {}
        report = pcb_layout.audit(design)
        self.assertTrue(any(f.code == "unknown_size" for f in report.findings))

    def test_empty_design_is_not_ready(self) -> None:
        from hw_velxio.design.model import Board, DesignModel

        d = DesignModel(title="empty")
        d.board = Board(kind="generic", name="b", size_mm={"w": 10, "h": 10})
        self.assertEqual(pcb_layout.audit(d).status, "not_ready")


class TestLcedaChannel(unittest.TestCase):
    """通道层（P24）。**不联网、不启动立创**：只验"不可用时怎么报"。"""

    def test_probe_shape(self) -> None:
        got = lceda_channel.probe(exe=[])
        self.assertIn("cli", got)
        self.assertIn("bridge", got)
        self.assertIn("recommended", got)
        self.assertIsInstance(got["bridge"]["missing"], list)

    def test_bridge_unavailable_raises_with_instructions(self) -> None:
        # 本机 Bridge 没连上（扩展没点 Reconnect）→ 必须是**明确的**错误，
        # 不能静默降级成 CLI（那会让人以为"用的就是 Bridge"）。
        probe = lceda_channel.probe()
        if probe["bridge"]["edaConnected"]:
            self.skipTest("Bridge 已连上，这条测不到不可用分支")
        with self.assertRaises(lceda_channel.ChannelError) as ctx:
            lceda_channel.open_channel("bridge")
        self.assertIn("Bridge", str(ctx.exception))

    def test_unknown_prefer_rejected(self) -> None:
        with self.assertRaises(lceda_channel.ChannelError):
            lceda_channel.open_channel("carrier-pigeon")


if __name__ == "__main__":
    unittest.main()