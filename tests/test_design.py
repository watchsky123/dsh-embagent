"""``hw_velxio.design`` 的自测（标准库 unittest，无第三方依赖）。

从仓库根运行：

    python -m unittest hw_velxio.tests.test_design -v

覆盖：模型校验/旋转、mm↔px 往返、KiCad/Wokwi 导入、物理检查、预算、
交付闸门、轨迹哈希链、制造包。测试**不写** ``project/`` 以外的目录，
每个用例结束清理自己的临时工程。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from hw_velxio.design import budgets, crosscheck, discipline, fabrication, physical
from hw_velxio.design.boards import board_preset, preset_info
from hw_velxio.design.importers import (
    apply_bom,
    import_bom,
    import_kicad_pcb,
    parse_sexpr,
    wokwi_to_legacy,
)
from hw_velxio.design.model import (
    Board,
    DesignError,
    DesignModel,
    Part,
    Placement,
    Wire,
)
from hw_velxio.design.projection import from_legacy_canvas, to_2d
from hw_velxio.design.store import DesignStore

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TEMP_PROJECTS: list[str] = []

# **测试不碰用户的工作区。** 工程根由 `HWV_WORKSPACE` 决定，这里指到临时目录；
# 不隔离的话，跑一次测试就会在用户真实工作区里留下 `_test_*` 工程。
_TEST_WORKSPACE = Path(tempfile.mkdtemp(prefix="hwv-test-ws-"))
os.environ["HWV_WORKSPACE"] = str(_TEST_WORKSPACE)


def make_project(name: str) -> DesignStore:
    store = DesignStore(name)
    TEMP_PROJECTS.append(name)
    return store


def tearDownModule() -> None:
    for name in TEMP_PROJECTS:
        try:
            DesignStore(name).delete()
        except Exception:  # noqa: BLE001
            pass
    shutil.rmtree(_TEST_WORKSPACE, ignore_errors=True)


class TestModel(unittest.TestCase):
    def test_board_preset_is_mm(self) -> None:
        board = board_preset("arduino-uno")
        self.assertAlmostEqual(board.size_mm["w"], 72.58, places=2)
        self.assertGreater(board.px_per_mm, 3.0)
        info = preset_info("arduino-uno")
        assert info is not None
        self.assertEqual(info["source"], "datasheet")

    def test_roundtrip_json(self) -> None:
        design = DesignModel(id="x", board=board_preset("esp32"))
        design.add_part(Part(ref="U1", kind="esp32", size_mm={"w": 28.2, "h": 53, "d": 12},
                             physical_source="datasheet"))
        restored = DesignModel.from_dict(design.to_dict())
        self.assertEqual(restored.to_dict(), design.to_dict())

    def test_rotation_swaps_footprint(self) -> None:
        part = Part(ref="R1", kind="resistor", size_mm={"w": 6.3, "h": 2.3, "d": 2.3},
                    placement=Placement(x_mm=10, y_mm=10, rotation_z=90))
        x0, y0, x1, y1 = part.footprint_mm()
        self.assertAlmostEqual(x1 - x0, 2.3, places=3)
        self.assertAlmostEqual(y1 - y0, 6.3, places=3)

    def test_bad_source_rejected(self) -> None:
        with self.assertRaises(DesignError):
            Part(ref="R1", kind="resistor", physical_source="guess")

    def test_duplicate_ref_rejected(self) -> None:
        design = DesignModel()
        design.add_part(Part(ref="R1", kind="resistor"))
        with self.assertRaises(DesignError):
            design.add_part(Part(ref="R1", kind="led"))

    def test_validate_flags_out_of_board(self) -> None:
        design = DesignModel(id="v", board=board_preset("arduino-nano"))
        design.add_part(Part(ref="X1", kind="led",
                             size_mm={"w": 5, "h": 5, "d": 8},
                             placement=Placement(x_mm=100, y_mm=0)))
        problems = design.validate()
        self.assertTrue(any("超出板轮廓" in p for p in problems))


class TestProjection(unittest.TestCase):
    def test_pixels_to_mm_and_back(self) -> None:
        design = DesignModel(id="p", board=board_preset("arduino-uno"))
        design.add_part(Part(ref="LED1", kind="led",
                             size_mm={"w": 5, "h": 5, "d": 8.6},
                             physical_source="datasheet",
                             placement=Placement(x_mm=20.0, y_mm=10.0)))
        projection = to_2d(design)
        ppm = projection["px_per_mm"]
        led = next(p for p in projection["parts"] if p["ref"] == "LED1")
        self.assertAlmostEqual(led["x_px"], 20.0 * ppm, places=2)
        # Y 翻转：模型 y=10，板高 53.34 -> 画布 y = (53.34-15)*ppm
        self.assertAlmostEqual(led["y_px"], (53.34 - 15.0) * ppm, places=2)

    def test_legacy_canvas_roundtrip(self) -> None:
        diagram = json.loads((EXAMPLES / "wokwi" / "blink.json").read_text(encoding="utf-8"))
        legacy = wokwi_to_legacy(diagram)
        design = from_legacy_canvas(legacy, design_id="rt")
        self.assertGreaterEqual(len(design.parts), 3)
        ppm = design.board.px_per_mm
        led = design.require("led1")
        # 画布 left=200px -> 约 52.98mm；再投影回去应回到 200px（±1.5）
        projection = {p["ref"]: p for p in to_2d(design)["parts"]}
        self.assertAlmostEqual(projection["led1"]["x_px"], 200.0, delta=1.5)
        self.assertAlmostEqual(led.placement.x_mm, 200.0 / ppm, delta=0.01)
        self.assertTrue(any("px/mm" in w for w in design.warnings))

    def test_netlist_from_connections(self) -> None:
        diagram = json.loads((EXAMPLES / "wokwi" / "blink.json").read_text(encoding="utf-8"))
        design = from_legacy_canvas(wokwi_to_legacy(diagram), design_id="nets")
        self.assertGreaterEqual(len(design.nets), 2)
        nets = {n["net"] for n in design.nets}
        self.assertEqual(len(nets), len(design.nets), "网表名必须唯一")
        # 板卡端点映射到 "board"，不是被静默丢弃
        board_pins = [p for n in design.nets for p in n["pins"] if p["ref"] == "board"]
        self.assertTrue(board_pins)
        self.assertTrue(any("板卡引脚" in w for w in design.warnings))


class TestImporters(unittest.TestCase):
    def test_sexpr_parser(self) -> None:
        tree = parse_sexpr('(a (b "x y") (c 1))')
        self.assertEqual(tree[0], "a")
        self.assertEqual(tree[1], ["b", "x y"])

    def test_kicad_outline_and_parts(self) -> None:
        design = import_kicad_pcb(EXAMPLES / "kicad" / "sensor-node.kicad_pcb")
        self.assertAlmostEqual(design.board.size_mm["w"], 40.0, places=2)
        self.assertAlmostEqual(design.board.size_mm["h"], 30.0, places=2)
        self.assertEqual({p.ref for p in design.parts}, {"R1", "Q1", "J1"})
        r1 = design.require("R1")
        self.assertEqual(r1.placement.rotation_z, 90)
        self.assertGreater(r1.size_mm["w"], 0)
        # 高度未知必须如实标出，不能编一个数
        self.assertTrue(any("高度未知" in w for w in design.warnings))

    def test_kicad_bom_attach(self) -> None:
        design = import_kicad_pcb(EXAMPLES / "kicad" / "sensor-node.kicad_pcb")
        bom = import_bom(EXAMPLES / "kicad" / "sensor-node-bom.csv")
        unmatched = apply_bom(design, bom)
        self.assertEqual(unmatched, [])
        self.assertEqual(design.require("R1").lcsc, "C25804")
        self.assertEqual(design.require("Q1").label, "2N7002")

    def test_missing_edge_cuts_raises(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".kicad_pcb", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("(kicad_pcb (version 1))")
            name = fh.name
        try:
            with self.assertRaises(Exception) as ctx:
                import_kicad_pcb(name)
            self.assertIn("Edge.Cuts", str(ctx.exception))
        finally:
            Path(name).unlink(missing_ok=True)


class TestPhysical(unittest.TestCase):
    def _design(self) -> DesignModel:
        design = DesignModel(id="ph", board=board_preset("arduino-uno"))
        design.add_part(Part(ref="A", kind="module", size_mm={"w": 20, "h": 10, "d": 5},
                             physical_source="datasheet",
                             placement=Placement(x_mm=5, y_mm=5)))
        return design

    def test_overlap_detected_in_mm(self) -> None:
        design = self._design()
        design.add_part(Part(ref="B", kind="module", size_mm={"w": 10, "h": 10, "d": 5},
                             physical_source="datasheet",
                             placement=Placement(x_mm=20, y_mm=8)))
        findings = physical.check_overlap(design, physical.build_space(design))
        failed = [f for f in findings if not f["ok"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["refs"], ["A", "B"])
        self.assertGreater(failed[0]["measured"]["overlap_mm3"], 0)

    def test_touching_is_not_overlap(self) -> None:
        design = self._design()
        design.add_part(Part(ref="B", kind="module", size_mm={"w": 10, "h": 10, "d": 5},
                             physical_source="datasheet",
                             placement=Placement(x_mm=25, y_mm=5)))
        findings = physical.check_overlap(design, physical.build_space(design))
        self.assertTrue(all(f["ok"] for f in findings))

    def test_estimated_size_is_reported(self) -> None:
        design = DesignModel(id="est")
        design.add_part(Part(ref="X", kind="unknown", physical_source="estimated",
                             size_mm={"w": 0, "h": 0, "d": 0}))
        result = physical.run_checks(design)
        self.assertFalse(result["ok"])
        messages = " ".join(f["message"] for f in result["findings"] if not f["ok"])
        self.assertIn("没有", messages)

    def test_height_limit_only_when_declared(self) -> None:
        design = self._design()
        design.add_part(Part(ref="TALL", kind="can", size_mm={"w": 5, "h": 5, "d": 40},
                             physical_source="datasheet",
                             placement=Placement(x_mm=40, y_mm=5)))
        # 没声明限高 -> info，不判失败
        undeclared = [f for f in physical.run_checks(design)["findings"]
                      if f["check"] == "space.height"][0]
        self.assertTrue(undeclared["ok"])
        self.assertEqual(undeclared["severity"], "info")
        # 声明 12mm 限高之后必须失败
        design.meta["max_height_mm"] = 12
        declared = [f for f in physical.run_checks(design)["findings"]
                    if f["check"] == "space.height"][0]
        self.assertFalse(declared["ok"])
        self.assertEqual(declared["measured"], 40.0)

    def test_connector_overhang_allowed(self) -> None:
        design = DesignModel(id="conn", board=board_preset("arduino-uno"))
        design.add_part(Part(ref="J1", kind="usb", role="connector",
                             size_mm={"w": 9, "h": 7, "d": 3},
                             physical_source="datasheet",
                             placement=Placement(x_mm=-1.0, y_mm=20)))
        findings = physical.check_bounds(design, physical.build_space(design))
        self.assertTrue(all(f["ok"] for f in findings))


class TestBudget(unittest.TestCase):
    def test_no_claims_is_not_ready(self) -> None:
        design = DesignModel(id="b")
        design.add_part(Part(ref="R1", kind="resistor", size_mm={"w": 6, "h": 2, "d": 2}))
        result = budgets.evaluate(design)
        self.assertEqual(result["confidence"], "none")
        self.assertEqual(result["status"], "not_ready")
        self.assertFalse(result["ok"])

    def test_claim_over_limit(self) -> None:
        design = DesignModel(id="b2")
        design.meta["budget"] = {"limits": {"power_w": 1.0}}
        design.add_part(Part(ref="U1", kind="mcu", size_mm={"w": 20, "h": 20, "d": 5},
                             electrical={"power_w": 3.0}))
        result = budgets.evaluate(design)
        self.assertEqual(result["confidence"], "full")
        self.assertTrue(any(v["kind"] in ("OVER_BUDGET", "COUPLING_OVER_BUDGET")
                            for v in result["violations"]))
        self.assertEqual(result["heatmap"]["U1"]["power_w"], 3.0)
        # 再加一个没有 claim 的元件 -> 覆盖率变成 partial
        design.add_part(Part(ref="R1", kind="resistor",
                             size_mm={"w": 6, "h": 2, "d": 2}))
        self.assertEqual(budgets.evaluate(design)["confidence"], "partial")


class TestDiscipline(unittest.TestCase):
    def test_gate_blocks_when_open(self) -> None:
        ledger = discipline.new_ledger()
        ledger.lock("mcu_family", "uno")
        verdict = discipline.gate(ledger)
        self.assertFalse(verdict["deliverable"])
        self.assertIn("package_family", verdict["open_irreversible"])

    def test_auto_lock_only_mcu(self) -> None:
        design = DesignModel(id="d", board=board_preset("esp32"))
        ledger = discipline.new_ledger()
        locked = discipline.auto_lock(design, ledger)
        self.assertEqual(locked, ["mcu_family"])
        self.assertIn("package_family", discipline.gate(ledger)["open_irreversible"])

    def test_trace_detects_payload_tamper(self) -> None:
        store = make_project("_test_trace")
        store.save(DesignModel(id="_test_trace"))
        discipline.append(store, "test", "a", {"n": 1})
        discipline.append(store, "test", "b", {"n": 2})
        self.assertTrue(discipline.verify(store)["ok"])
        rows = store.read("trace.json")
        rows[0]["payload"]["n"] = 999  # 改一条历史事件的 payload
        store.write("trace.json", rows)
        verdict = discipline.verify(store)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["tampered_seqs"], [1])
        self.assertIn("内容被改动", verdict["message"])


class TestFabrication(unittest.TestCase):
    def test_bundle_written(self) -> None:
        design = import_kicad_pcb(EXAMPLES / "kicad" / "sensor-node.kicad_pcb")
        # 不用系统临时目录：DSH 沙箱下 %TEMP% 不可写，测试要在工作区内自洽
        tmp_root = Path(__file__).resolve().parent.parent / "_tmp" / "fab-test"
        tmp_root.mkdir(parents=True, exist_ok=True)
        tmp = str(tmp_root)
        if True:
            result = fabrication.export_bundle(design, tmp, gate_message="测试闸门")
            self.assertTrue(result["ok"])
            for name in ("bom.csv", "positions.csv", "outline.dxf",
                         "design.json", "README.txt"):
                self.assertIn(name, result["written"])
                self.assertTrue((Path(tmp) / name).is_file())
            dxf = (Path(tmp) / "outline.dxf").read_text(encoding="utf-8")
            self.assertIn("LWPOLYLINE", dxf)
            self.assertIn("40.0000", dxf)
            # 不伪造 Gerber
            self.assertTrue(any("Gerber" in m for m in result["missing"]))


class TestEndToEnd(unittest.TestCase):
    def test_new_import_snapshot_export(self) -> None:
        from hw_velxio import design_cli

        store = make_project("_test_e2e")
        payload = design_cli.cmd_new(["_test_e2e", "arduino-uno", "E2E"])
        self.assertEqual(payload["status"], "ok")

        imported = design_cli.cmd_import(
            ["_test_e2e", str(EXAMPLES / "wokwi" / "blink.json")])
        self.assertEqual(imported["status"], "ok")
        self.assertGreaterEqual(imported["summary"]["parts"], 3)

        snapshot = design_cli.snapshot("_test_e2e")
        self.assertEqual(snapshot["status"], "ok")
        self.assertIn("projection2d", snapshot)
        self.assertIn("scene3d", snapshot)
        self.assertIn("checks", snapshot)

        # 未闭合不可逆决策时导出必须被拦
        blocked = design_cli.cmd_export(["_test_e2e"])
        self.assertEqual(blocked["status"], "not_ready")
        self.assertTrue(blocked["blocked"])

        # 全部闭合后导出成功
        for name, value in (("package_family", "SMD 0603"),
                            ("connector_family", "JST-PH"),
                            ("power_architecture", "5V USB"),
                            ("bootloader_layout", "single app, no OTA")):
            result = design_cli.cmd_deferred(["_test_e2e", "lock", name, value])
            self.assertEqual(result["status"], "ok")
        exported = design_cli.cmd_export(["_test_e2e"])
        self.assertEqual(exported["status"], "ok")
        self.assertTrue(store.path("fab").is_dir())
        self.assertTrue((store.path("fab") / "bom.csv").is_file())

        evidence = design_cli.cmd_evidence(["_test_e2e"])
        self.assertEqual(evidence["status"], "ok")
        self.assertEqual(len(evidence["design_hash"]), 16)


class TestScene3DPayload(unittest.TestCase):
    """3D 载荷必须让渲染层**只转一次**。

    真实事故：`Assembly3D` 拿 `bbox_mm` 建盒子、又按 `rotation_z` 转一次，
    而 `bbox_mm` 已经是旋转后的 AABB —— 于是 0/90/180/270 四个朝向画出来
    一模一样。载荷里补 `size_mm`（未旋转足迹）就是为这件事。
    """

    def test_size_mm_is_unrotated_while_bbox_is_rotated(self) -> None:
        from hw_velxio import design_cli

        make_project("_test_scene3d")
        design_cli.cmd_new(["_test_scene3d", "arduino-uno", "3D 载荷"])
        design = design_cli._store("_test_scene3d").load()
        design.parts = [Part(ref="u1", kind="qfp", role="module",
                             size_mm={"w": 6.0, "h": 2.0, "d": 1.0},
                             placement=Placement(x_mm=10.0, y_mm=10.0,
                                                 rotation_z=90))]
        design_cli._store("_test_scene3d").save(design)

        payload = design_cli.scene3d(design_cli._store("_test_scene3d").load())
        part = payload["parts"][0]

        # 未旋转足迹原样带出：渲染层建几何用它
        self.assertEqual(part["size_mm"], {"w": 6.0, "h": 2.0, "d": 1.0})
        # bbox 是旋转后的 AABB：x/y 已经交换
        x0, y0, z0, x1, y1, z1 = part["bbox_mm"]
        self.assertAlmostEqual(x1 - x0, 2.0, places=3)
        self.assertAlmostEqual(y1 - y0, 6.0, places=3)
        # 高度两个字段必须一致，否则 3D 盒子和判定用的 AABB 会打架
        self.assertAlmostEqual(z1 - z0, part["size_mm"]["d"], places=3)
        self.assertEqual(part["rotation_z"], 90)

    def test_every_part_carries_size_mm(self) -> None:
        from hw_velxio import design_cli

        make_project("_test_scene3d_all")
        design_cli.cmd_new(["_test_scene3d_all", "arduino-uno", "全量"])
        design_cli.cmd_import(["_test_scene3d_all",
                               str(EXAMPLES / "wokwi" / "blink.json")])
        payload = design_cli.scene3d(
            design_cli._store("_test_scene3d_all").load())
        self.assertTrue(payload["parts"])
        for part in payload["parts"]:
            self.assertIn("size_mm", part, f"{part['ref']} 缺 size_mm")
            self.assertEqual(set(part["size_mm"]), {"w", "h", "d"})


class TestRatlines(unittest.TestCase):
    """飞线：从网表推导，**不是铜线**。

    真实事故：`.epro2` 工程的 `wires` 是空的（没有布线数据），于是 2D 画布上
    **一根线都没有**，119 条网络完全看不见。飞线用**焊盘真实坐标**补上，
    并用 `routed: False` 明确它不是铜线。
    """

    def _design(self) -> DesignModel:
        from hw_velxio.design.model import Pad

        design = DesignModel(id="p", title="t", board=board_preset("arduino-uno"))
        design.parts = [
            Part(ref="U1", kind="mcu", role="module",
                 size_mm={"w": 10.0, "h": 10.0, "d": 1.4},
                 placement=Placement(x_mm=20.0, y_mm=20.0),
                 pads=[Pad(num="1", x_mm=17.0, y_mm=18.0, w_mm=1.0, h_mm=0.4),
                       Pad(num="2", x_mm=23.0, y_mm=22.0, w_mm=1.0, h_mm=0.4)]),
            Part(ref="C1", kind="capacitor", role="component",
                 size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
                 placement=Placement(x_mm=40.0, y_mm=40.0),
                 pads=[Pad(num="1", x_mm=39.2, y_mm=40.0, w_mm=0.8, h_mm=0.9),
                       Pad(num="2", x_mm=40.8, y_mm=40.0, w_mm=0.8, h_mm=0.9)]),
        ]
        design.nets = [{"net": "GND", "pins": [{"ref": "U1", "pin": "1"},
                                                {"ref": "C1", "pin": "2"}]}]
        return design

    def test_ratline_uses_pad_positions_not_part_centers(self) -> None:
        from hw_velxio.design.ratlines import build_ratlines

        design = self._design()
        lines = build_ratlines(design)
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line["net"], "GND")
        # 端点必须是**焊盘**坐标：U1.1 在 (17,18)，C1.2 在 (40.8,40)。
        self.assertEqual(sorted(line["pins"]), ["C1.2", "U1.1"])
        xs = [point[0] for point in line["segments"]]
        ys = [point[1] for point in line["segments"]]
        self.assertIn(17.0, xs)
        self.assertIn(18.0, ys)
        # 元件中心会是 (15,15) —— 与焊盘坐标不同，断言确实用了焊盘
        self.assertNotIn(15.0, xs)

    def test_ratline_is_never_marked_routed(self) -> None:
        from hw_velxio.design.ratlines import build_ratlines

        design = self._design()
        for line in build_ratlines(design):
            self.assertFalse(line["routed"],
                             "飞线不是铜线，不能标成已布线")

    def test_ratline_count_is_n_minus_one(self) -> None:
        """一条 N 端网络画 N−1 条线（最小生成树），不是 N 条。"""
        from hw_velxio.design.model import Pad
        from hw_velxio.design.ratlines import build_ratlines

        design = self._design()
        design.parts.append(Part(
            ref="R1", kind="resistor", role="component",
            size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
            placement=Placement(x_mm=10.0, y_mm=50.0),
            pads=[Pad(num="1", x_mm=9.2, y_mm=50.0, w_mm=0.8, h_mm=0.9),
                  Pad(num="2", x_mm=10.8, y_mm=50.0, w_mm=0.8, h_mm=0.9)]))
        design.nets[0]["pins"].append({"ref": "R1", "pin": "2"})
        lines = build_ratlines(design)
        gnd = [line for line in lines if line["net"] == "GND"]
        self.assertEqual(len(gnd), 2, "3 端网络应画 2 条飞线")

    def test_projection_carries_ratlines(self) -> None:
        payload = to_2d(self._design())
        self.assertIn("ratlines", payload)
        self.assertTrue(payload["ratlines"])
        self.assertFalse(payload["ratlines"][0]["routed"])

    def test_projection_carries_pads(self) -> None:
        payload = to_2d(self._design())
        u1 = next(p for p in payload["parts"] if p["ref"] == "U1")
        self.assertEqual(len(u1["pads"]), 2)
        self.assertEqual(u1["pads"][0]["num"], "1")


class TestBoardSide(unittest.TestCase):
    """安装面（顶/底）—— 决定重叠判定是否成立。

    真实事故：双面贴装的板上，顶层的 C80 与底层的 C82 **XY 重叠是正常的**
    （它们在板的两侧）。没有这一项，重叠检查把真板报成 9 条假错误。
    """

    def test_bottom_part_extends_below_board(self) -> None:
        part = Part(ref="c1", kind="capacitor", role="component",
                    size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
                    placement=Placement(x_mm=10.0, y_mm=10.0, side="bottom"))
        x0, y0, z0, x1, y1, z1 = part.bbox_mm()
        self.assertLess(z0, 0.0, "底层元件必须在板面之下")
        self.assertAlmostEqual(z1, 0.0, places=6)

    def test_top_part_sits_above_board(self) -> None:
        part = Part(ref="c1", kind="capacitor", role="component",
                    size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
                    placement=Placement(x_mm=10.0, y_mm=10.0, side="top"))
        _x0, _y0, z0, _x1, _y1, z1 = part.bbox_mm()
        self.assertAlmostEqual(z0, 0.0, places=6)
        self.assertAlmostEqual(z1, 0.8, places=6)

    def test_opposite_sides_do_not_overlap(self) -> None:
        """顶层的件与底层的件在同一个 XY 位置 —— **不算重叠**。"""
        design = DesignModel(id="p", title="t", board=board_preset("arduino-uno"))
        design.parts = [
            Part(ref="a", kind="capacitor", role="component",
                 size_mm={"w": 2.0, "h": 2.0, "d": 0.8},
                 placement=Placement(x_mm=20.0, y_mm=20.0, side="top")),
            Part(ref="b", kind="capacitor", role="component",
                 size_mm={"w": 2.0, "h": 2.0, "d": 0.8},
                 placement=Placement(x_mm=20.0, y_mm=20.0, side="bottom")),
        ]
        findings = physical.check_overlap(design, {})
        bad = [f for f in findings if not f.get("ok")]
        self.assertEqual(bad, [], "顶/底同位置不该报重叠")

    def test_same_side_does_overlap(self) -> None:
        design = DesignModel(id="p", title="t", board=board_preset("arduino-uno"))
        design.parts = [
            Part(ref="a", kind="capacitor", role="component",
                 size_mm={"w": 2.0, "h": 2.0, "d": 0.8},
                 placement=Placement(x_mm=20.0, y_mm=20.0, side="top")),
            Part(ref="b", kind="capacitor", role="component",
                 size_mm={"w": 2.0, "h": 2.0, "d": 0.8},
                 placement=Placement(x_mm=20.0, y_mm=20.0, side="top")),
        ]
        findings = physical.check_overlap(design, {})
        bad = [f for f in findings if not f.get("ok")]
        self.assertEqual(len(bad), 1, "同面同位置必须报重叠")

    def test_side_is_rejected_when_illegal(self) -> None:
        with self.assertRaises(DesignError):
            Placement(x_mm=0, y_mm=0, side="middle")


class TestPackageSizes(unittest.TestCase):
    """封装类别表 —— 本体尺寸的兜底来源。"""

    def test_size_code_wins(self) -> None:
        from hw_velxio.design.packages import package_size

        result = package_size("C0603")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["w"], 1.6, places=3)
        self.assertAlmostEqual(result["h"], 0.8, places=3)

    def test_series_prefix(self) -> None:
        from hw_velxio.design.packages import package_size

        result = package_size("SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["w"], 2.9, places=3)

    def test_unknown_returns_none(self) -> None:
        from hw_velxio.design.packages import package_size

        self.assertIsNone(package_size("完全没见过的封装"))

    def test_two_pad_body_is_pad_center_distance_plus_quarter(self) -> None:
        """两端贴片：本体长 ≈ 焊盘中心距 + 焊盘长/4（实测 C0603 → 1.6mm）。"""
        from hw_velxio.design.packages import size_from_pads

        pads = [{"cx": 0.0, "cy": 0.0, "w": 0.8, "h": 0.9},
                {"cx": 1.4, "cy": 0.0, "w": 0.8, "h": 0.9}]
        result = size_from_pads(pads)
        self.assertAlmostEqual(result["w"], 1.6, places=2)   # 1.4 + 0.8/4
        self.assertAlmostEqual(result["h"], 0.9, places=2)

    def test_array_body_is_outer_extent_times_075(self) -> None:
        """四周阵列：本体 ≈ 焊盘外廓 × 0.75，但**不得小于外廓 − 1.6mm**。

        下界的理由：本体绝不能比自己的焊盘还小，否则焊盘会飘到本体外面
        （实测 Lora1 的 19 个焊盘跨 31mm，纯 0.75 折算只有 23mm，
        15 个焊盘落到框外，看起来像布局错乱）。
        """
        from hw_velxio.design.packages import size_from_pads

        pads = []
        for index in range(16):
            pads.append({"cx": 0.0, "cy": index * 0.8, "w": 2.0, "h": 0.3})
        outer = 15 * 0.8 + 0.3      # 12.3
        result = size_from_pads(pads)
        self.assertAlmostEqual(result["h"], max(outer * 0.75, outer - 1.6),
                               places=2)
        self.assertGreaterEqual(result["h"], outer - 1.6 - 1e-6,
                                "本体不能比焊盘外廓小太多，否则焊盘飘出")


class TestTraceParsing(unittest.TestCase):
    """走线（`LINE`）—— 2D 画布上"一根线都没有"的真正原因。

    真实事故：`wires` 一直是空的，因为**走线从来没解析过**。走线一直在
    文件里（实测主板 PCB 文档 1119 条 `LINE`、105 个网络、628 顶 / 491 底）。
    """

    def test_line_record_becomes_wire(self) -> None:
        from hw_velxio.design.easyeda import MIL_TO_MM

        # 直接验换算：`LINE` 用 mil，落盘用 mm。
        self.assertAlmostEqual(1600 * MIL_TO_MM, 40.64, places=4)

    def test_wire_requires_two_points(self) -> None:
        with self.assertRaises(DesignError):
            Wire(net="GND", path_mm=[(0.0, 0.0, 0.0)])

    def test_wire_survives_roundtrip(self) -> None:
        wire = Wire(net="GND", path_mm=[(1.0, 2.0, 0.0), (3.0, 4.0, 0.0)])
        again = Wire.from_dict(wire.to_dict())
        self.assertEqual(again.net, "GND")
        self.assertEqual(len(again.path_mm), 2)
        self.assertAlmostEqual(again.path_mm[1][0], 3.0)

    def test_projection_marks_real_wires_as_routed(self) -> None:
        """真实走线必须 `routed: True`；飞线必须 `routed: False`。

        两者在界面上**颜色与线型都不同**（实线 vs 虚线）。混在一起会让人
        以为没布线的网络也布好了。
        """
        design = DesignModel(id="p", title="t", board=board_preset("arduino-uno"))
        design.parts = [Part(ref="R1", kind="resistor", role="component",
                             size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
                             placement=Placement(x_mm=10.0, y_mm=10.0))]
        design.wires = [Wire(net="GND",
                             path_mm=[(5.0, 5.0, 0.0), (20.0, 20.0, 0.0)])]
        design.nets = [{"net": "GND",
                        "pins": [{"ref": "R1", "pin": "1"},
                                 {"ref": "R1", "pin": "2"}]}]
        payload = to_2d(design)
        self.assertTrue(payload["wires"][0]["routed"], "真实铜线必须标 routed")
        self.assertTrue(all(line["routed"] is False
                            for line in payload["ratlines"]),
                        "飞线不能标 routed")


class TestPlacementSemantics(unittest.TestCase):
    """`placement.x_mm/y_mm` 是**左下角**，不是中心。

    真实事故：立创 EDA 的 `COMPONENT.x/y` 是元件**中心**，而规范模型存
    **左下角**（KiCad / Wokwi 导入器都减掉了 w/2）。不换算的后果实测是
    "焊盘画在元件框外面" —— 每个元件都偏右上 w/2、h/2，整个画面错位。
    """

    def test_footprint_centres_on_placement_plus_half(self) -> None:
        part = Part(ref="R1", kind="resistor", role="component",
                    size_mm={"w": 4.0, "h": 2.0, "d": 1.0},
                    placement=Placement(x_mm=10.0, y_mm=20.0))
        _x0, _y0, _x1, _y1 = part.footprint_mm()
        centre = part.center_mm()
        # 中心 = 左下角 + 半尺寸（这是"左下角"语义的定义）
        self.assertAlmostEqual(centre[0], 12.0, places=6)
        self.assertAlmostEqual(centre[1], 21.0, places=6)

    def test_rotation_swaps_about_centre_not_corner(self) -> None:
        """旋转 90° 必须绕**中心**转，不能绕左下角转。

        绕角转会让元件在旋转时发生平移（实测表现是"右键转一下就跑了"）。
        """
        base = dict(ref="R1", kind="resistor", role="component",
                    size_mm={"w": 4.0, "h": 2.0, "d": 1.0},
                    placement=Placement(x_mm=10.0, y_mm=20.0))
        flat = Part(**base)
        turned = Part(**{**base,
                         "placement": Placement(x_mm=10.0, y_mm=20.0,
                                                rotation_z=90)})
        flat_centre = flat.center_mm()
        turned_centre = turned.center_mm()
        self.assertAlmostEqual(flat_centre[0], turned_centre[0], places=6)
        self.assertAlmostEqual(flat_centre[1], turned_centre[1], places=6)
        # 而足迹确实换了（90° 时 w/h 互换）
        _x0, _y0, x1, y1 = turned.footprint_mm()
        self.assertAlmostEqual(x1 - _x0, 2.0, places=6)
        self.assertAlmostEqual(y1 - _y0, 4.0, places=6)


class TestRoleInference(unittest.TestCase):
    """角色推断只信**封装名**，不信 `3D Model Title`。

    真实事故：U12 的真实封装是 `ESOP-8`（一颗芯片），但它的
    `3D Model Title` 记着 `CONN-TH_4P-P1.25_WAFER-MX1.25-4PZZ`
    （那是另一个元件的模型）。拿模型标题判角色会把它误判成连接器，
    于是报"连接器距板边 10.1mm" —— 而它根本不是连接器。
    """

    def test_connector_word_in_footprint(self) -> None:
        from hw_velxio.design.easyeda_import import _role_of

        self.assertEqual(_role_of("U1047", "CONN-TH_4P-P1.25_WAFER-MX1.25-4PZZ"),
                         "connector")
        self.assertEqual(_role_of("USB1", "USB-C-SMD_TYPE-C-6PIN"), "connector")
        self.assertEqual(_role_of("RF1", "SMA-TH_KH-SMA-KWE17-G"), "connector")

    def test_chip_footprint_is_not_connector(self) -> None:
        from hw_velxio.design.easyeda_import import _role_of

        self.assertEqual(_role_of("U12", "ESOP-8_L4.9-W3.9-P1.27"), "module")
        self.assertEqual(_role_of("U10", "SOP-8"), "module")
        self.assertEqual(_role_of("C1", "C0603"), "component")

    def test_prefix_fallback_when_no_footprint(self) -> None:
        from hw_velxio.design.easyeda_import import _role_of

        self.assertEqual(_role_of("J1", ""), "connector")
        self.assertEqual(_role_of("R1", ""), "component")


class TestBomFill(unittest.TestCase):
    """BOM 补全 —— 让"元件清单"变成视图里看得见的元件。

    真实场景：`示例-无人机` 的 `design.json` 是 `parts: []`，而工作区里有
    一份 45 个设计号的 `BOM_drone.csv`。视图显示 0 个元件，用户以为坏了。
    """

    def _board(self) -> Board:
        return board_preset("arduino-uno")

    def test_bom_fills_empty_project(self) -> None:
        from hw_velxio.design.bom_fill import fill_from_bom

        design = DesignModel(id="p", title="t", board=self._board())
        bom = {
            "C1": {"value": "100nF", "footprint": "C0603", "lcsc": "C1"},
            "R1": {"value": "10k", "footprint": "R0603", "lcsc": "C2"},
            "U1": {"value": "STM32", "footprint": "LQFP-64_L10.0-W10.0", "lcsc": "C3"},
        }
        note = fill_from_bom(design, bom)
        self.assertEqual(note["added"], 3)
        self.assertEqual(len(design.parts), 3)
        self.assertEqual(note["placement"], "grid-estimate")

    def test_filled_parts_have_real_sizes(self) -> None:
        """尺寸来自封装表，不是 0 —— 否则 3D 里全是看不见的点。"""
        from hw_velxio.design.bom_fill import fill_from_bom

        design = DesignModel(id="p", title="t", board=self._board())
        fill_from_bom(design, {
            "C1": {"value": "100nF", "footprint": "C0603"},
            "U1": {"value": "MCU", "footprint": "LQFP-64_L10.0-W10.0"},
        })
        c1 = design.part("C1")
        self.assertIsNotNone(c1)
        self.assertAlmostEqual(c1.size_mm["w"], 1.6, places=2)
        self.assertEqual(c1.physical_source, "derived")
        u1 = design.part("U1")
        self.assertIsNotNone(u1)
        self.assertGreater(u1.size_mm["w"], 5.0)

    def test_unknown_footprint_is_estimated_not_invented(self) -> None:
        """封装查不到 → 0 尺寸 + estimated，**绝不编造**。"""
        from hw_velxio.design.bom_fill import fill_from_bom

        design = DesignModel(id="p", title="t", board=self._board())
        note = fill_from_bom(design, {"X9": {"value": "?", "footprint": "完全没见过的封装"}})
        part = design.part("X9")
        self.assertIsNotNone(part)
        self.assertEqual(part.size_mm["w"], 0.0)
        self.assertEqual(part.physical_source, "estimated")
        self.assertEqual(note["unsized"], 1)

    def test_existing_parts_are_never_overwritten(self) -> None:
        """PCB 导入的真实布局优先于 BOM 排布。"""
        from hw_velxio.design.bom_fill import fill_from_bom

        design = DesignModel(id="p", title="t", board=self._board())
        design.parts = [Part(ref="C1", kind="C0603", role="component",
                             size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
                             placement=Placement(x_mm=50.0, y_mm=30.0))]
        note = fill_from_bom(design, {
            "C1": {"value": "100nF", "footprint": "C0603"},
            "R1": {"value": "10k", "footprint": "R0603"},
        })
        self.assertEqual(note["added"], 1)
        self.assertEqual(note["skipped_existing"], 1)
        c1 = design.part("C1")
        self.assertIsNotNone(c1)
        self.assertAlmostEqual(c1.placement.x_mm, 50.0, places=3,
                               msg="BOM 不能覆盖真实坐标")

    def test_filled_parts_do_not_overlap_existing(self) -> None:
        """补进来的元件必须**避开**已有元件。

        实测事故：141 个真元件已在板上，直接从头铺网格会让补进来的 29 个
        压在真元件上，检查器立刻报 6 条 `space.overlap` —— 那 6 条是我们
        自己造的假问题，把真正的重叠淹掉了。

        测试数据必须**铺满**网格第一个位置：网格从 `MARGIN_MM`(4.0) 起，
        所以占位块要盖住 (4.0, 4.0) 附近。只覆盖 y=2.0 的一条窄带
        **测不出**问题（第一版就是这么写的，变异测试直接漏过）。
        """
        from hw_velxio.design.bom_fill import MARGIN_MM, fill_from_bom

        design = DesignModel(id="p", title="t", board=self._board())
        # 用大块占位把 (MARGIN, MARGIN) 附近完全盖住
        design.parts = [
            Part(ref=f"U{i}", kind="LQFP-64", role="module",
                 size_mm={"w": 20.0, "h": 20.0, "d": 1.4},
                 placement=Placement(x_mm=MARGIN_MM - 2.0 + i * 22.0,
                                     y_mm=MARGIN_MM - 2.0))
            for i in range(4)
        ]
        fill_from_bom(design, {
            "C1": {"value": "100nF", "footprint": "C0603"},
            "C2": {"value": "100nF", "footprint": "C0603"},
        })
        filled = [p for p in design.parts if p.ref in ("C1", "C2")]
        self.assertEqual(len(filled), 2, "两个都该补进来（板上有空位）")
        findings = physical.check_overlap(design, {})
        bad = [f for f in findings if not f.get("ok")]
        self.assertEqual(bad, [], "BOM 补的元件不该和已有元件重叠")

    def test_filled_part_moves_when_first_grid_slot_is_taken(self) -> None:
        """第一格被占 → 必须挪位置，而不是原地叠上去。

        直接断言坐标：只断言"没有重叠发现"是不够的 —— 变异掉避让逻辑后
        `check_overlap` 仍可能因为别的原因通过（第一版测试就是这样漏掉的）。
        """
        from hw_velxio.design.bom_fill import PACK_GAP_MM, fill_from_bom

        design = DesignModel(id="p", title="t", board=self._board())
        blocker = Part(ref="U1", kind="LQFP-64", role="module",
                       size_mm={"w": 30.0, "h": 10.0, "d": 1.4},
                       placement=Placement(x_mm=0.0, y_mm=0.0))
        design.parts = [blocker]
        block_box = blocker.footprint_mm()

        fill_from_bom(design, {"C1": {"value": "100nF", "footprint": "C0603"}})
        c1 = design.part("C1")
        self.assertIsNotNone(c1)
        x0, y0, x1, y1 = c1.footprint_mm()
        # 补进来的元件**不能**落在占位块里
        inside_block = (x0 < block_box[2] and x1 > block_box[0]
                        and y0 < block_box[3] and y1 > block_box[1])
        self.assertFalse(inside_block,
                         f"C1 落在 U1 的足迹里（{x0:.1f},{y0:.1f}），避让没生效")
        self.assertGreater(PACK_GAP_MM, 0.0)

    def test_filled_parts_avoid_mount_holes(self) -> None:
        """安装孔也要避开。

        实测事故：004 补完 BOM 后 `R5` 正好压住 #2 安装孔，检查器报
        "螺钉无法进入" —— 那条是我们自己造的。
        """
        from hw_velxio.design.bom_fill import fill_from_bom

        board = board_preset("arduino-uno")
        self.assertTrue(board.mount_holes, "预设板应有安装孔")
        design = DesignModel(id="p", title="t", board=board)
        fill_from_bom(design, {
            f"R{i}": {"value": "10k", "footprint": "R0603"} for i in range(20)
        })
        findings = physical.check_mount_holes(design, {})
        bad = [f for f in findings if not f.get("ok")]
        self.assertEqual(bad, [], "BOM 补的元件不该压住安装孔")

    def test_warning_states_placement_is_not_layout(self) -> None:
        """**位置是排布、不是布局** 这句话必须在 warnings 里。

        这是纪律问题：把网格排布当真实布局展示，用户会照着布线。
        """
        from hw_velxio.design.bom_fill import fill_from_bom

        design = DesignModel(id="p", title="t", board=self._board())
        fill_from_bom(design, {"C1": {"value": "100nF", "footprint": "C0603"}})
        joined = " ".join(design.warnings)
        self.assertIn("网格排布", joined)
        self.assertIn("不是真实布局", joined)
        meta = design.meta.get("bom_fill") or {}
        self.assertEqual(meta.get("placement"), "grid-estimate")

    def test_role_from_footprint_beats_prefix(self) -> None:
        from hw_velxio.design.bom_fill import _role_of

        self.assertEqual(_role_of("U1047", "CONN-TH_4P-P1.25_WAFER-MX1.25"),
                         "connector")
        self.assertEqual(_role_of("USB1", "USB-C-SMD_TYPE-C-6PIN"), "connector")
        self.assertEqual(_role_of("U12", "ESOP-8_L4.9-W3.9-P1.27"), "module")
        self.assertEqual(_role_of("C1", "C0603"), "component")


class TestSkippedBoards(unittest.TestCase):
    """多板工程：只导一块是**设计选择**，静默丢弃是 bug。

    真实场景：立创工程有 8 块 PCB / 316 个元件，导入器只取元件最多的一块，
    另外 175 个（55%）被丢掉，而 `meta` 里没有任何字段提到这件事。
    """

    def test_other_boards_is_reported(self) -> None:
        from hw_velxio.design.easyeda_import import build_design

        parsed = {
            "status": "ok", "title": "multi", "units": {},
            "main_doc": "PCB#aaa",
            "components": [
                {"ref": "C1", "doc": "PCB#aaa", "x_mm": 10.0, "y_mm": 10.0,
                 "angle_deg": 0, "pads": ["1", "2"], "pad_geometry": [],
                 "pad_local": [], "footprint_title": "C0603"},
                {"ref": "C2", "doc": "PCB#bbb", "x_mm": 5.0, "y_mm": 5.0,
                 "angle_deg": 0, "pads": ["1", "2"], "pad_geometry": [],
                 "pad_local": [], "footprint_title": "C0603"},
                {"ref": "C3", "doc": "PCB#bbb", "x_mm": 8.0, "y_mm": 8.0,
                 "angle_deg": 0, "pads": ["1", "2"], "pad_geometry": [],
                 "pad_local": [], "footprint_title": "C0603"},
            ],
            "nets": {},
            "outlines_full": [
                {"doc": "PCB#aaa", "w": 50.0, "h": 40.0,
                 "points": [[0, 0], [50, 0], [50, 40], [0, 40]]},
                {"doc": "PCB#bbb", "w": 20.0, "h": 15.0,
                 "points": [[0, 0], [20, 0], [20, 15], [0, 15]]},
            ],
        }
        design = build_design(parsed, design_id="multi")
        meta = design.meta
        self.assertEqual(meta.get("source_boards"), 2)
        self.assertEqual(meta.get("parts_skipped"), 2)
        skipped = meta.get("boards_skipped") or []
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["doc"], "PCB#bbb")
        self.assertEqual(skipped[0]["parts"], 2)
        self.assertEqual(skipped[0]["size_mm"]["w"], 20.0)

    def test_warning_says_how_many_were_dropped(self) -> None:
        from hw_velxio.design.easyeda_import import build_design

        parsed = {
            "status": "ok", "title": "multi", "units": {},
            "main_doc": "PCB#aaa",
            "components": [
                {"ref": "C1", "doc": "PCB#aaa", "x_mm": 10.0, "y_mm": 10.0,
                 "angle_deg": 0, "pads": ["1"], "pad_geometry": [],
                 "pad_local": [], "footprint_title": "C0603"},
                {"ref": "C2", "doc": "PCB#bbb", "x_mm": 5.0, "y_mm": 5.0,
                 "angle_deg": 0, "pads": ["1"], "pad_geometry": [],
                 "pad_local": [], "footprint_title": "C0603"},
            ],
            "nets": {},
            "outlines_full": [
                {"doc": "PCB#aaa", "w": 50.0, "h": 40.0,
                 "points": [[0, 0], [50, 0], [50, 40], [0, 40]]},
                {"doc": "PCB#bbb", "w": 20.0, "h": 15.0,
                 "points": [[0, 0], [20, 0], [20, 15], [0, 15]]},
            ],
        }
        design = build_design(parsed, design_id="multi")
        joined = " ".join(design.warnings)
        self.assertIn("2 块板", joined)
        self.assertIn("未导入 1 个元件", joined)

    def test_single_board_has_no_skip_warning(self) -> None:
        from hw_velxio.design.easyeda_import import build_design

        parsed = {
            "status": "ok", "title": "single", "units": {},
            "main_doc": "PCB#aaa",
            "components": [
                {"ref": "C1", "doc": "PCB#aaa", "x_mm": 10.0, "y_mm": 10.0,
                 "angle_deg": 0, "pads": ["1"], "pad_geometry": [],
                 "pad_local": [], "footprint_title": "C0603"},
            ],
            "nets": {},
            "outlines_full": [
                {"doc": "PCB#aaa", "w": 50.0, "h": 40.0,
                 "points": [[0, 0], [50, 0], [50, 40], [0, 40]]},
            ],
        }
        design = build_design(parsed, design_id="single")
        self.assertEqual(design.meta.get("parts_skipped"), 0)
        self.assertNotIn("未导入", " ".join(design.warnings))


class TestMultiBoard(unittest.TestCase):
    """多板：一个真实项目分布在多块 PCB 上。

    真实场景：立创工程有 8 块 PCB / 316 个元件，而模型原本是单板的
    （`DesignModel.board` 是单数），只导入 141 个（44.6%），其余 55% 被丢。
    """

    def _multi(self) -> DesignModel:
        from hw_velxio.design.model import Board

        main = Board(id="PCB#A", kind="easyeda:x", name="主板",
                     size_mm={"w": 60.0, "h": 40.0},
                     outline=[(0, 0), (60, 0), (60, 40), (0, 40)])
        sub = Board(id="PCB#B", kind="easyeda:x", name="子板",
                    size_mm={"w": 20.0, "h": 15.0},
                    outline=[(0, 0), (20, 0), (20, 15), (0, 15)])
        design = DesignModel(id="m", title="multi", board=main, boards=[main, sub])
        design.parts = [
            Part(ref="R1", kind="R0603", role="component",
                 size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
                 placement=Placement(x_mm=10.0, y_mm=10.0, board_id="PCB#A")),
            # 子板上的元件：局部坐标 (5,5) —— 在**子板**内，但在主板坐标系里
            # 也是合法点。这正是"不按板分组就查不出问题"的地方。
            Part(ref="C1", kind="C0603", role="component",
                 size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
                 placement=Placement(x_mm=5.0, y_mm=5.0, board_id="PCB#B")),
        ]
        return design

    def test_board_list_grows(self) -> None:
        design = self._multi()
        self.assertTrue(design.is_multi_board())
        self.assertEqual(len(design.board_list()), 2)

    def test_board_is_identity_in_boards(self) -> None:
        """`board` 必须是 `boards` 里的**同一个对象**，不是值相等的副本。

        真实事故：从磁盘读回后 `board` 与 `boards[0]` 值相等但不是同一对象，
        而 `panel_layout` 用 `is not main` 过滤 → 主板被排两次
        （8 块板排出 9 块，多出来的那块 0 个元件）。
        """
        design = self._multi()
        self.assertIs(design.board_list()[0], design.board)

    def test_roundtrip_preserves_identity(self) -> None:
        design = self._multi()
        again = DesignModel.from_dict(design.to_dict())
        self.assertIs(again.board_list()[0], again.board,
                      "从 dict 读回后 board 必须是 boards[0] 同一对象")
        self.assertEqual(len(again.board_list()), 2)

    def test_board_of_resolves_owner(self) -> None:
        design = self._multi()
        self.assertEqual(design.board_of(design.part("R1")).id, "PCB#A")
        self.assertEqual(design.board_of(design.part("C1")).id, "PCB#B")

    def test_empty_board_id_falls_back_to_main(self) -> None:
        design = self._multi()
        design.part("R1").placement.board_id = ""
        self.assertEqual(design.board_of(design.part("R1")).id, "PCB#A")

    def test_dangling_board_id_is_reported(self) -> None:
        design = self._multi()
        design.part("C1").placement.board_id = "PCB#NOPE"
        problems = design.validate()
        self.assertTrue(any("board_id" in p for p in problems))

    def test_bounds_uses_owning_board(self) -> None:
        """边界检查必须用**所属板**尺寸，不能拿主板的。

        子板 20×15，主板 60×40。子板上 (18, 13) 的元件在**子板外**、
        但在主板内 —— 只有按板分组才查得出来。
        """
        design = self._multi()
        design.part("C1").placement.x_mm = 19.0   # 子板宽 20，元件宽 1.6
        design.part("C1").placement.y_mm = 14.0
        findings = physical.check_bounds(design, {})
        bad = [f for f in findings if not f.get("ok")]
        self.assertTrue(bad, "子板上的越界元件必须被报出来")
        self.assertIn("PCB#B", " ".join(str(f.get("message")) for f in bad))

    def test_no_false_overlap_across_boards(self) -> None:
        """不同板上的元件**不可能**实体重叠。

        真实事故：元件的 bbox 是各自板的**局部坐标**，直接两两相比时
        8 板工程报出 68 条重叠，**全部**是跨板的假碰撞（同板真碰撞 0 条）。

        **这条测试必须让两块板在平面上真的靠近**才有效：拼板偏移会把
        它们分开，如果只把元件放在相同的局部坐标，即使去掉跨板短路也
        测不出问题（第一版就是这样，变异测试直接漏过）。
        所以这里用 `monkeypatch` 式的手工 `board_offsets` 把两块板叠到
        同一个位置，强制制造"平移后仍重叠"的情形。
        """
        design = self._multi()
        # 让两块板上的元件落在相同的局部坐标
        design.part("C1").placement.x_mm = 10.0
        design.part("C1").placement.y_mm = 10.0
        built = physical.build_space(design)
        # **把子板的偏移强行设成主板的位置** —— 模拟"两块板叠在一起"
        built["board_offsets"] = {"PCB#A": (0.0, 0.0), "PCB#B": (0.0, 0.0)}
        findings = physical.check_overlap(design, built)
        bad = [f for f in findings if not f.get("ok")]
        self.assertEqual(bad, [], "跨板元件即使平移后同位，也不该报重叠")

    def test_same_board_overlap_still_detected(self) -> None:
        """短路不能把同板的重叠也吞掉。"""
        design = self._multi()
        design.parts.append(Part(
            ref="R2", kind="R0603", role="component",
            size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
            placement=Placement(x_mm=10.0, y_mm=10.0, board_id="PCB#A")))
        built = physical.build_space(design)
        findings = physical.check_overlap(design, built)
        bad = [f for f in findings if not f.get("ok")]
        self.assertEqual(len(bad), 1, "同板同位置必须报重叠")

    def test_projection_lays_boards_side_by_side(self) -> None:
        from hw_velxio.design.projection import panel_layout

        design = self._multi()
        panel = panel_layout(design)
        self.assertEqual(len(panel["boards"]), 2)
        placed = {e["board"].id: (e["x"], e["y"]) for e in panel["boards"]}
        self.assertEqual(placed["PCB#A"], (0.0, 0.0), "主板在原点")
        # 子板不能和主板重叠（至少在一个轴上分开）
        ax, ay = placed["PCB#B"]
        self.assertTrue(ax >= 60.0 or ay >= 40.0,
                        f"子板位置 ({ax},{ay}) 与主板重叠")
        self.assertGreater(panel["size_mm"]["w"], 60.0)

    def test_projection_parts_are_panel_coordinates(self) -> None:
        """2D 载荷里的坐标必须是**拼板坐标**，不是板局部坐标。

        不转换的话，子板元件会画在主板范围内 —— 看起来像"元件跑到别的板上"。
        """
        from hw_velxio.design.projection import panel_layout, to_2d

        design = self._multi()
        panel = panel_layout(design)
        sub = next(e for e in panel["boards"] if e["board"].id == "PCB#B")
        payload = to_2d(design)
        c1 = next(p for p in payload["parts"] if p["ref"] == "C1")
        ppm = payload["px_per_mm"]
        # C1 局部 (5,5) → 拼板 (5 + sub.x, 5 + sub.y)
        expect_x = (5.0 + sub["x"]) * ppm
        self.assertAlmostEqual(c1["x_px"], expect_x, places=2)
        self.assertEqual(c1["board_id"], "PCB#B")

    def test_y_flip_uses_panel_height_not_board_height(self) -> None:
        """Y 翻转必须用**拼板总高**。

        用主板高度翻转的话，子板上的元件会翻到拼板外。**关键是子板必须
        在主板上方**（拼板 y 更大）：拼板高 40+8+15=63mm，若误用主板高 40mm
        翻转，子板元件（拼板 y≈43..58）的 y_px 会变成负值 —— 翻到画布外。
        第一版只断言"落在范围内"，但子板被排在右侧（y 相同），翻转后
        恰好也在范围内，于是变异测试漏过。
        """
        from hw_velxio.design.model import Board
        from hw_velxio.design.projection import panel_layout, to_2d

        # 子板窄而主板宽 → panel_layout 只能把子板排到**下一行**（y 方向）
        main = Board(id="A", kind="easyeda:x", name="主板",
                     size_mm={"w": 100.0, "h": 40.0},
                     outline=[(0, 0), (100, 0), (100, 40), (0, 40)])
        sub = Board(id="B", kind="easyeda:x", name="子板",
                    size_mm={"w": 90.0, "h": 15.0},
                    outline=[(0, 0), (90, 0), (90, 15), (0, 15)])
        design = DesignModel(id="m", title="multi", board=main, boards=[main, sub])
        design.parts = [
            Part(ref="R1", kind="R0603", role="component",
                 size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
                 placement=Placement(x_mm=10.0, y_mm=10.0, board_id="A")),
            Part(ref="C1", kind="C0603", role="component",
                 size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
                 placement=Placement(x_mm=10.0, y_mm=10.0, board_id="B")),
        ]
        panel = panel_layout(design)
        subEntry = next(e for e in panel["boards"] if e["board"].id == "B")
        self.assertGreater(subEntry["y"], 40.0,
                           "子板应被排到主板上方（下一行），本测试才有意义")
        panelH = panel["size_mm"]["h"]
        payload = to_2d(design)
        ppm = payload["px_per_mm"]
        c1 = next(p for p in payload["parts"] if p["ref"] == "C1")
        # 子板在本设计中位于上方 → 翻转后 y_px 应该是**小的正数**
        self.assertGreaterEqual(c1["y_px"], -1.0,
                                f"C1 翻到画布上方外了（y_px={c1['y_px']:.1f}）")
        self.assertLessEqual(c1["y_px"], panelH * ppm + 1.0)

    def test_projection_skips_cross_board_ratlines(self) -> None:
        """跨板网络不画飞线：那不是 PCB 铜线。"""
        from hw_velxio.design.projection import to_2d

        design = self._multi()
        design.parts[0].pads = [__import__(
            "hw_velxio.design.model", fromlist=["Pad"]
        ).Pad(num="1", x_mm=10.0, y_mm=10.0, w_mm=1.0, h_mm=0.5)]
        design.parts[1].pads = [__import__(
            "hw_velxio.design.model", fromlist=["Pad"]
        ).Pad(num="1", x_mm=5.0, y_mm=5.0, w_mm=1.0, h_mm=0.5)]
        design.nets = [{"net": "GND", "pins": [{"ref": "R1", "pin": "1"},
                                               {"ref": "C1", "pin": "1"}]}]
        payload = to_2d(design)
        self.assertEqual(payload["ratlines"], [],
                         "跨板网络不该画飞线")
        self.assertEqual(payload["cross_board_ratlines_skipped"], 1)

    def test_panel_layout_is_deterministic(self) -> None:
        from hw_velxio.design.projection import panel_layout

        design = self._multi()
        first = [(e["board"].id, e["x"], e["y"])
                 for e in panel_layout(design)["boards"]]
        second = [(e["board"].id, e["x"], e["y"])
                  for e in panel_layout(design)["boards"]]
        self.assertEqual(first, second)


class TestRoleMapping(unittest.TestCase):
    """R2 —— 角色 → 元件的确定性映射。

    之前这一格是空的：`spec` 产出角色（camera / gimbal_pan），
    `assemble` 要模板（camera_module），中间没有任何代码连接，
    于是"需求 → 设计"只能靠手工写死一个参考设计。
    """

    def test_hwforge_templates_are_read_at_runtime(self) -> None:
        """模板 key 必须**从 hwforge 读**，不能抄一份清单。

        抄一份就是第二个事实源：hwforge 改了名字，这边静默失效。
        """
        from hw_velxio.design import roles

        keys = roles.template_keys()
        if not keys:
            self.skipTest("hwforge 不可用")
        # 抽查几个必须存在的模板
        for expected in ("camera_module", "gimbal_servo", "mcu_board",
                         "psu_5v", "wall_bracket"):
            self.assertIn(expected, keys)

    def test_validate_ok_on_current_table(self) -> None:
        from hw_velxio.design import roles

        check = roles.validate()
        if check.hwforge_unavailable:
            self.skipTest("hwforge 不可用")
        self.assertTrue(check.ok, f"映射表应全部对上：{check.summary()}")
        self.assertEqual(check.missing_templates, [])
        self.assertEqual(check.unmatched, [])

    def test_every_spec_role_has_a_mapping(self) -> None:
        """`spec.py` 能产出的**每一个** role 都必须在表里。

        漏一个 = 那个需求会被静默丢掉一部分。角色清单是从 hwforge 源码
        grep 出来的（`role="…"`），硬编码在这里是有意的：新增角色时
        这条测试会失败，逼着人更新映射表。
        """
        from hw_velxio.design import roles

        spec_roles = ["camera", "display_peer", "encoder", "gimbal_pan",
                      "gimbal_tilt", "mount", "radio", "servo_power",
                      "undefined_hw"]
        check = roles.validate(spec_roles)
        if check.hwforge_unavailable:
            self.skipTest("hwforge 不可用")
        self.assertEqual(check.unmatched, [],
                         f"这些角色没有映射：{check.unmatched}")

    def test_unknown_role_is_reported_not_ignored(self) -> None:
        """表里没有的角色必须报出来，**不能静默跳过**。"""
        from hw_velxio.design import roles

        check = roles.validate(["camera", "totally_made_up_role"])
        self.assertFalse(check.ok)
        self.assertIn("totally_made_up_role", check.unmatched)
        kinds = {p.kind for p in check.problems}
        self.assertIn("role_unmapped", kinds)

    def test_external_role_is_explicit_not_absent(self) -> None:
        """外部件（不占元件）必须**显式**映射到 None。

        "查不到"和"故意不占元件"必须能区分开，否则分不清是设计还是遗漏。
        """
        from hw_velxio.design import roles

        mapping = roles.BY_ROLE.get("display_peer")
        self.assertIsNotNone(mapping, "display_peer 必须有显式映射")
        self.assertIsNone(mapping.template)
        self.assertTrue(mapping.why, "外部件必须写清理由")

    def test_capability_check_uses_real_pins(self) -> None:
        """`needs` 是**电气能力**，校验方式是"造一遍看引脚"。

        第一版拿 `needs=("pwm",)` 去对工厂签名，一次报 5 条假问题
        （签名里只有 w_mm/d_mm/axis）。错的是"消费"的定义，不是检查本身。
        """
        from hw_velxio.design import roles

        pins = roles.template_pins("gimbal_servo")
        if not pins:
            self.skipTest("hwforge 不可用")
        self.assertIn("PWM", pins, f"舵机模板应有 PWM 脚，实际：{sorted(pins)}")
        self.assertTrue(roles.capability_available("gimbal_servo", "pwm"))
        # 舵机没有视频能力
        self.assertFalse(roles.capability_available("gimbal_servo", "video"))

    def test_capability_is_three_state(self) -> None:
        """无法判断的能力返回 `None`，**不是 False 也不是 True**。

        把"不知道"混进"通过"是这套系统一直在防的事。
        """
        from hw_velxio.design import roles

        if not roles.template_keys():
            self.skipTest("hwforge 不可用")
        # 不在 CAPABILITY_PINS 表里的能力 → 无法核对
        self.assertIsNone(roles.capability_available("gimbal_servo", "量子纠缠"))

    def test_plan_builds_one_part_per_role(self) -> None:
        from hw_velxio.design import roles

        if not roles.template_keys():
            self.skipTest("hwforge 不可用")
        plan = roles.plan(["camera", "gimbal_pan", "gimbal_tilt", "servo_power"])
        self.assertEqual(len(plan.parts), 4)
        templates = {p.template for p in plan.parts}
        self.assertEqual(templates,
                         {"camera_module", "gimbal_servo", "psu_5v"})
        # 两个舵机是同一个模板的两个实例
        self.assertEqual(sum(1 for p in plan.parts if p.template == "gimbal_servo"), 2)

    def test_plan_separates_external_roles(self) -> None:
        from hw_velxio.design import roles

        if not roles.template_keys():
            self.skipTest("hwforge 不可用")
        plan = roles.plan(["camera", "display_peer", "encoder"])
        roles_seen = [e["role"] for e in plan.external]
        self.assertIn("display_peer", roles_seen)
        self.assertIn("encoder", roles_seen)
        self.assertEqual([p.role for p in plan.parts], ["camera"])

    def test_plan_reports_unmatched_without_guessing(self) -> None:
        """未映射的角色进 `unmatched`，**不猜一个最像的模板**。"""
        from hw_velxio.design import roles

        if not roles.template_keys():
            self.skipTest("hwforge 不可用")
        plan = roles.plan(["camera", "no_such_role"])
        self.assertEqual(plan.unmatched, ["no_such_role"])
        self.assertEqual([p.role for p in plan.parts], ["camera"])
        self.assertIn("没有映射", plan.summary())

    def test_plan_merges_radio_into_mcu(self) -> None:
        """`radio` 与 `mcu` 都是 mcu_board，应合并成一个元件。

        ESP32-S3 自带 Wi-Fi，装两块板是浪费且会引入第二份电源/天线。
        """
        from hw_velxio.design import roles

        if not roles.template_keys():
            self.skipTest("hwforge 不可用")
        plan = roles.plan(["radio"])
        self.assertEqual(len(plan.parts), 1)
        self.assertEqual(plan.parts[0].template, "mcu_board")

    def test_plan_carries_reason_through(self) -> None:
        from hw_velxio.design import roles

        if not roles.template_keys():
            self.skipTest("hwforge 不可用")
        plan = roles.plan(["camera"], reasons={"camera": "采集实时图像"})
        self.assertEqual(plan.parts[0].why, "采集实时图像")

    def test_size_comes_from_template(self) -> None:
        from hw_velxio.design import roles

        if not roles.template_keys():
            self.skipTest("hwforge 不可用")
        plan = roles.plan(["camera"])
        self.assertIsNotNone(plan.parts[0].size_mm,
                             "尺寸应取自模板，不是编的")
        self.assertGreater(plan.parts[0].size_mm[0], 0.0)

    def test_validate_always_runs(self) -> None:
        """**校验永远能跑** —— 模板表在本地，不存在"校验不了"的状态。

        早先模板表从 hwforge 读，取不到就返回 `hwforge_unavailable=True`
        并把 `ok` 置 False。现在那个状态从根上消失了，`hwforge_unavailable`
        字段恒为 False（保留只为兼容快照格式）。
        """
        from hw_velxio.design import roles

        check = roles.validate(["camera", "mcu", "gimbal_pan"])
        self.assertFalse(check.hwforge_unavailable)
        self.assertTrue(check.ok, f"映射表应全部通过：{check.summary()}")
        # 哪怕在一个完全没有 hwforge 的环境里也要有模板
        self.assertTrue(roles.template_keys(), "模板表必须非空")

class TestR3Assemble(unittest.TestCase):
    """R3 —— 装配计划 → 可渲染的设计（元件 + 网表 + 板框）。

    R2 之前只有"要装哪些元件"；这里把它变成真实坐标、真实网表、真实板框。
    """

    def _needs_hwforge(self) -> None:
        from hw_velxio.design import roles
        if not roles.template_keys():
            self.skipTest("hwforge 不可用")

    def test_roles_assemble_produces_design(self) -> None:
        from hw_velxio.design import assemble

        self._needs_hwforge()
        result = assemble.assemble_roles(
            ["camera", "gimbal_pan", "gimbal_tilt", "servo_power"],
            design_id="t1")
        self.assertTrue(result.ok, f"装配失败：{result.problems}")
        design = result.design
        self.assertIsNotNone(design)
        self.assertEqual(len(design.parts), 4)
        self.assertGreater(len(design.nets), 0)

    def test_pin_names_come_from_templates_not_guessed(self) -> None:
        """引脚名必须是模板**实际产出**的，不能猜。

        猜一个"应该叫 VCC"的引脚名会让 `pins.audit()` 报它不存在 ——
        但那时错误已经离需求很远了。
        """
        from hw_velxio.design import assemble

        self._needs_hwforge()
        result = assemble.assemble_roles(
            ["camera", "gimbal_pan", "servo_power"], design_id="t1")
        self.assertIsNotNone(result.design)
        # 相机模板的引脚是 5V/GND/DATA（实测）
        cam = result.pin_names.get("CAM1", [])
        self.assertIn("DATA", cam, f"相机应有 DATA 脚，实际：{cam}")
        self.assertIn("GND", cam)
        # 舵机模板的引脚是 PWM/VCC/GND
        sv = result.pin_names.get("SV1", [])
        self.assertIn("PWM", sv, f"舵机应有 PWM 脚，实际：{sv}")

    def test_nets_are_built_from_real_pins(self) -> None:
        """网表里的每个引脚都必须在元件的 `pins` 里真实存在。"""
        from hw_velxio.design import assemble

        self._needs_hwforge()
        result = assemble.assemble_roles(
            ["camera", "gimbal_pan", "servo_power"], design_id="t1")
        design = result.design
        self.assertIsNotNone(design)
        for entry in design.nets:
            for pin in entry["pins"]:
                part = design.part(pin["ref"])
                self.assertIsNotNone(part, f"网表指向不存在的元件 {pin['ref']}")
                self.assertIn(pin["pin"], part.pins,
                              f"{pin['ref']}.{pin['pin']} 不在该元件的引脚表里")

    def test_design_passes_validate(self) -> None:
        """装出来的设计必须 `validate()` 干净 —— 否则它不该被落盘。"""
        from hw_velxio.design import assemble

        self._needs_hwforge()
        result = assemble.assemble_roles(
            ["camera", "gimbal_pan", "gimbal_tilt", "servo_power"],
            design_id="t1")
        self.assertIsNotNone(result.design)
        problems = result.design.validate()
        self.assertEqual(problems, [], f"装配出的设计有问题：{problems[:4]}")

    def test_design_passes_physical_checks(self) -> None:
        from hw_velxio.design import assemble

        self._needs_hwforge()
        result = assemble.assemble_roles(
            ["camera", "gimbal_pan", "gimbal_tilt", "servo_power"],
            design_id="t1")
        self.assertIsNotNone(result.design)
        findings = physical.run_checks(result.design).get("findings") or []
        failed = [f for f in findings if not f.get("ok")]
        self.assertEqual(failed, [],
                         f"物理检查失败：{[f.get('message') for f in failed][:3]}")

    def test_layout_is_banded_not_random(self) -> None:
        """布局按功能带：电源在下（y 小），执行在上（y 大）。

        这是**确定性**的：同一份计划永远得到同一组坐标。
        """
        from hw_velxio.design import assemble

        self._needs_hwforge()
        first = assemble.assemble_roles(
            ["camera", "gimbal_pan", "servo_power"], design_id="t1")
        second = assemble.assemble_roles(
            ["camera", "gimbal_pan", "servo_power"], design_id="t2")
        coords = lambda r: sorted(
            (p.ref, p.placement.x_mm, p.placement.y_mm) for p in r.design.parts)
        self.assertEqual(coords(first), coords(second), "布局必须可复现")
        ps = next(p for p in first.design.parts if p.kind == "psu_5v")
        cam = next(p for p in first.design.parts if p.kind == "camera_module")
        self.assertLess(ps.placement.y_mm, cam.placement.y_mm,
                        "电源带应在执行带下方")

    def test_board_grows_to_fit_parts(self) -> None:
        """板子必须装得下所有元件（预设不够就扩）。"""
        from hw_velxio.design import assemble

        self._needs_hwforge()
        result = assemble.assemble_roles(
            ["camera", "gimbal_pan", "gimbal_tilt", "servo_power"],
            design_id="t1")
        design = result.design
        self.assertIsNotNone(design)
        bw = design.board.size_mm["w"]
        bh = design.board.size_mm["h"]
        for part in design.parts:
            x0, y0, x1, y1 = part.footprint_mm()
            self.assertLessEqual(x1, bw + 0.01, f"{part.ref} 横向超出板")
            self.assertLessEqual(y1, bh + 0.01, f"{part.ref} 纵向超出板")

    def test_unrouted_is_reported_not_silently_dropped(self) -> None:
        """接不上的线进 `unrouted`，不是静默丢掉。

        **一个脚不构成网**：只装一个相机时，`5V` / `GND` / `DATA` 各只有一个
        端点。它们必须进 `unrouted`（"这条连接本该存在，但没有对方"），
        而不是被当成一条网画出来 —— 画出来会让人以为它接好了。

        这条测试第一版只断言"没有单脚网"，把 `unrouted.append` 删掉
        照样通过（变异测试当场揭穿）。现在同时断言 `unrouted` **非空**。
        """
        from hw_velxio.design import assemble

        self._needs_hwforge()
        result = assemble.assemble_roles(["camera"], design_id="t1")
        self.assertIsNotNone(result.design)
        # 单件的每个脚都是悬空的
        self.assertEqual(result.wiring.nets, [],
                         "单件不该产生任何网")
        self.assertTrue(result.wiring.unrouted,
                        "单件的悬空脚必须进 unrouted，不能静默丢掉")
        # 三个脚都要有交代，且每条都带原因（不断言具体措辞，
        # 只断言"说清了为什么"，否则改文案就挂）
        joined = " ".join(result.wiring.unrouted)
        for pin in ("CAM1.5V", "CAM1.GND", "CAM1.DATA"):
            self.assertIn(pin, joined,
                          f"{pin} 应在 unrouted 里被点名，实际：{result.wiring.unrouted}")

    def test_two_servos_share_ground_net(self) -> None:
        """两个舵机 + 电源：GND 必须把三者的 GND 脚连成一网。"""
        from hw_velxio.design import assemble

        self._needs_hwforge()
        result = assemble.assemble_roles(
            ["gimbal_pan", "gimbal_tilt", "servo_power"], design_id="t1")
        design = result.design
        self.assertIsNotNone(design)
        gnd = next((n for n in result.wiring.nets if n["net"] == "GND"), None)
        self.assertIsNotNone(gnd, "应该有一个 GND 网")
        # 两个舵机 + 电源 = 3 个 GND 脚
        self.assertGreaterEqual(len(gnd["pins"]), 3,
                                f"GND 网应含至少 3 个脚，实际：{gnd['pins']}")

    def test_design_renders_in_2d_and_3d(self) -> None:
        """装出来的设计必须能出 2D 投影与 3D 场景（否则视图又是一片空）。"""
        from hw_velxio.design import assemble
        from hw_velxio.design.projection import to_2d

        self._needs_hwforge()
        result = assemble.assemble_roles(
            ["camera", "gimbal_pan", "servo_power"], design_id="t1")
        self.assertIsNotNone(result.design)
        payload = to_2d(result.design)
        self.assertEqual(len(payload["parts"]), 3)
        # 每个元件都必须有正尺寸 —— 0 尺寸在 2D 里不可见
        for part in payload["parts"]:
            self.assertGreater(part["w_px"], 0, f"{part['ref']} 宽度为 0")
            self.assertGreater(part["h_px"], 0, f"{part['ref']} 高度为 0")

    def test_meta_states_layout_is_not_optimised(self) -> None:
        """`meta.layout_note` 必须说明这是**分带排布**，不是优化布局。

        把网格排布当优化布局看，会高估这个设计的成熟度。
        """
        from hw_velxio.design import assemble

        self._needs_hwforge()
        result = assemble.assemble_roles(["camera", "servo_power"],
                                         design_id="t1")
        design = result.design
        self.assertIsNotNone(design)
        self.assertEqual(design.meta.get("layout"), "band-grid")
        self.assertIn("不是优化过的布局", design.meta.get("layout_note", ""))

    def test_assembles_without_hwforge(self) -> None:
        """**装配不依赖 hwforge**：模板库在 `design/library.py` 里。

        早先 `assemble` 调 `hwforge.assembly_build.build_from_spec()`，
        没有 checkout 就整条链路 `not_ready`。迁移后这条测试守住它。
        """
        from hw_velxio.design import assemble, library

        result = assemble.assemble_roles(["camera", "mcu", "servo_power"],
                                         design_id="t")
        self.assertTrue(result.ok, f"装配失败：{result.problems}")
        self.assertIsNotNone(result.design)
        # 模板来自本地库
        self.assertIn("camera_module", library.keys())
        self.assertTrue(result.provenance.get("template_source", "").startswith(
            "design/library.py"))

class TestPinInvariant(unittest.TestCase):
    """P0-a：**每个引脚都必须有交代**（在某个网里，或在 `unrouted` 里）。

    实测事故：`CAM1.DATA` 和 `SV1.PWM` 既不在网表、也不在 `unrouted` ——
    凭空消失。因为最初只处理 GND/电源/MCU 信号三类脚，落不进这三类的被
    静默忽略。那正是这套系统一直在防的：**缺失被渲染成"没有问题"**。
    """

    def _needs(self) -> None:
        from hw_velxio.design import roles
        if not roles.template_keys():
            self.skipTest("hwforge 不可用")

    def _accounting(self, result: object) -> tuple[int, int, int]:
        """返回 (引脚数, 网表引脚数, unrouted 数)。"""
        design = result.design
        total = sum(len(v) for v in result.pin_names.values())
        in_net = sum(len(n["pins"]) for n in design.nets)
        return total, in_net, len(result.wiring.unrouted)

    def test_every_pin_is_accounted_for(self) -> None:
        from hw_velxio.design import assemble

        self._needs()
        for roles_list in (
            ["camera"],
            ["camera", "gimbal_pan", "servo_power"],
            ["camera", "gimbal_pan", "gimbal_tilt", "mcu", "servo_power"],
            ["mcu", "sensor"],
        ):
            result = assemble.assemble_roles(roles_list, design_id="t")
            total, in_net, unrouted = self._accounting(result)
            self.assertEqual(
                total, in_net + unrouted,
                f"{roles_list}: 引脚 {total} 条，但只有 {in_net} 在网表 + "
                f"{unrouted} 在 unrouted —— 有引脚凭空消失了")

    def test_pin_with_no_rule_is_named(self) -> None:
        """没有规则能处理的脚，`unrouted` 要指名道姓。"""
        from hw_velxio.design import assemble

        self._needs()
        # 电源的 AC 输入脚没有源，必然悬空
        result = assemble.assemble_roles(["camera", "servo_power"],
                                         design_id="t")
        joined = " ".join(result.wiring.unrouted)
        self.assertIn("ACIN", joined,
                      f"ACIN 应该出现在 unrouted 里，实际：{result.wiring.unrouted}")

    def test_mcu_connects_camera_data(self) -> None:
        """补了 mcu 之后，`CAM1.DATA` 必须真的接到 `U1.CAM_DATA`。"""
        from hw_velxio.design import assemble

        self._needs()
        result = assemble.assemble_roles(
            ["camera", "mcu", "servo_power"], design_id="t")
        design = result.design
        found = any(
            {("CAM1", "DATA"), ("U1", "CAM_DATA")}
            <= {(p["ref"], p["pin"]) for p in net["pins"]}
            for net in design.nets)
        self.assertTrue(found,
                        f"CAM1.DATA 应接到 U1.CAM_DATA，实际网表：{design.nets}")

    def test_a_pin_belongs_to_exactly_one_net(self) -> None:
        """**一个脚不能属于两个网** —— 那是电气错误。

        实测事故：`CAM_PWR` 要接相机的 `5V`，于是 `CAM1.5V` 同时出现在
        `5V` 和 `SIG_CAM_PWR` 两个网里。这条测试是被上面那条引脚守恒
        （17 != 16+2）暴露出来的。
        """
        from hw_velxio.design import assemble

        self._needs()
        for roles_list in (
            ["camera", "mcu", "servo_power"],
            ["camera", "gimbal_pan", "gimbal_tilt", "mcu", "servo_power"],
            ["mcu", "sensor"],
        ):
            result = assemble.assemble_roles(roles_list, design_id="t")
            seen: dict[tuple[str, str], str] = {}
            for net in result.design.nets:
                for pin in net["pins"]:
                    key = (pin["ref"], pin["pin"])
                    self.assertNotIn(
                        key, seen,
                        f"{roles_list}: {key[0]}.{key[1]} 同时在 "
                        f"{seen.get(key)} 和 {net['net']} 两个网里")
                    seen[key] = net["net"]


class TestRoleEnhancement(unittest.TestCase):
    """P0-b：`spec` 不产出 `mcu`，由 hw_velxio 侧补。"""

    def test_mcu_added_when_signals_need_one(self) -> None:
        from hw_velxio.design import roles

        enhanced = roles.enhance(["camera", "gimbal_pan", "servo_power"])
        self.assertIn("mcu", enhanced.roles)
        self.assertEqual(enhanced.added, ["mcu"])
        self.assertIn("悬空", enhanced.reason)

    def test_mcu_added_when_goal_names_one(self) -> None:
        from hw_velxio.design import roles

        enhanced = roles.enhance([], "做一个 STM32 主控的板子")
        self.assertIn("mcu", enhanced.roles)

    def test_no_mcu_when_radio_already_provides_it(self) -> None:
        """`radio` 已映射到 `mcu_board`，不该再补一个主控（否则两块板）。"""
        from hw_velxio.design import roles

        enhanced = roles.enhance(["camera", "radio", "servo_power"])
        self.assertNotIn("mcu", enhanced.roles)
        self.assertEqual(enhanced.added, [])

    def test_undefined_hw_blocks_inference_but_not_explicit(self) -> None:
        """`undefined_hw` 挡住"推断补 mcu"，但不挡用户明说。"""
        from hw_velxio.design import roles

        # 需求没说主控、也没说硬件形态 → 不推断
        suppressed = roles.enhance(["undefined_hw"], "做个好玩的东西")
        self.assertNotIn("mcu", suppressed.roles)
        # 用户明说 STM32 → 照补
        explicit = roles.enhance(["undefined_hw"], "做个 STM32 温湿度板")
        self.assertIn("mcu", explicit.roles)

    def test_enhance_is_idempotent(self) -> None:
        from hw_velxio.design import roles

        once = roles.enhance(["camera", "servo_power"]).roles
        twice = roles.enhance(once).roles
        self.assertEqual(once, twice)


class TestCapabilityTable(unittest.TestCase):
    """P1-b：能力需求表 —— 换个需求也要能认出来。"""

    def test_three_different_requirements_all_resolve(self) -> None:
        """三种**完全不同**的需求都必须产出角色（不能只剩 undefined_hw）。"""
        from hw_velxio.design import requirements

        cases = {
            "做一个 STM32 主控的温湿度采集板": ("mcu", "sensor"),
            "壁挂云台相机 手机看实时视频": ("camera", "gimbal_pan"),
            "四轮小车 电机 电池 蓝牙遥控": ("motor", "power_in", "radio"),
        }
        for goal, expected in cases.items():
            result = requirements.analyse(goal)
            self.assertTrue(result.ok, f"{goal} 应能识别：{result.summary()}")
            for role in expected:
                self.assertIn(role, result.roles,
                              f"{goal} 应产出 {role}，实际 {result.roles}")

    def test_unknown_requirement_says_so(self) -> None:
        """认不出时明确说"没认出来"，并列出试过的说法。"""
        from hw_velxio.design import requirements

        result = requirements.analyse("做个好玩的东西")
        self.assertIn("undefined_hw", result.roles)
        self.assertTrue(result.tried, "应记录试过哪些说法")
        self.assertFalse(result.ok)

    def test_hits_name_the_word(self) -> None:
        """命中的是**哪个词**要能看见 —— 便于人核对是不是误判。"""
        from hw_velxio.design import requirements

        result = requirements.analyse("带电池的四轮小车")
        words = {h.word for h in result.hits}
        self.assertIn("电池", words)
        self.assertTrue(words, "命中必须记录具体词")

    def test_merge_drops_undefined_when_specific_exists(self) -> None:
        from hw_velxio.design import requirements

        merged = requirements.merge(["undefined_hw"], ["mcu", "sensor"])
        self.assertNotIn("undefined_hw", merged)
        # 两边都没具体角色时保留占位符
        self.assertEqual(requirements.merge(["undefined_hw"], []), ["undefined_hw"])

    def test_capability_roles_are_all_mapped(self) -> None:
        """能力表产出的**每一个**角色都必须在 roles 映射表里。

        否则 P1-b 会让"认得出来"和"装得出来"之间出现新的断层。
        """
        from hw_velxio.design import requirements, roles

        for capability in requirements.CAPABILITIES:
            for role in capability.roles:
                self.assertIn(role, roles.BY_ROLE,
                              f"能力 {capability.key} 产出角色 {role}，"
                              f"但 roles.BY_ROLE 里没有它")


class TestCoverify(unittest.TestCase):
    """P2-a：需求数值 vs 硬件能力的矛盾扫描。"""

    def _needs(self) -> None:
        from hw_velxio.design import roles
        if not roles.template_keys():
            self.skipTest("hwforge 不可用")

    def test_runs_and_gives_both_numbers(self) -> None:
        """每条检查都必须**同时给出两个数字**（硬件能力 + 软件需求）。"""
        from hw_velxio.design import assemble, coverify

        self._needs()
        result = assemble.assemble_roles(
            ["camera", "gimbal_pan", "gimbal_tilt", "mcu", "servo_power"],
            design_id="t")
        co = coverify.run(result.design)
        self.assertGreater(len(co.checks), 0, "coverify 应产出检查")
        for check in co.checks:
            self.assertIn("hardware", check)
            self.assertIn("software_needs", check)
            self.assertNotEqual(check["hardware"], None)
        for finding in co.findings:
            self.assertTrue(finding.get("measured"), "findings 要带实测值")
            self.assertTrue(finding.get("limit"), "findings 要带限值")

    def test_undeclared_numbers_are_not_verified(self) -> None:
        """需求数值没给就不算"验过"—— 默认值不代表需求。"""
        from hw_velxio.design import assemble, coverify

        self._needs()
        result = assemble.assemble_roles(["camera", "mcu"], design_id="t")
        co = coverify.run(result.design)
        self.assertTrue(co.not_verified,
                        "没声明需求数值时必须有 not_verified，不能静默按默认值通过")

    def test_declared_numbers_clear_the_notice(self) -> None:
        from hw_velxio.design import assemble, coverify, tiers

        self._needs()
        result = assemble.assemble_roles(["camera", "gimbal_pan", "mcu"],
                                         design_id="t")
        numbers = {key: meta["default"]
                   for key, meta in coverify.NUMERIC_REQUIREMENTS.items()}
        co = coverify.run(result.design, numbers=numbers)
        self.assertEqual(co.not_verified, [],
                         f"全部声明后不该有 not_verified：{co.not_verified}")
        self.assertTrue(co.provenance.get("servo"), "舵机事实要标出处")


class TestTiers(unittest.TestCase):
    """P3：验收阶梯 —— 哪些验过、哪些没工具、哪些没跑、哪些要人。"""

    def _needs(self) -> None:
        from hw_velxio.design import roles
        if not roles.template_keys():
            self.skipTest("hwforge 不可用")

    def test_ladder_is_read_from_hwforge(self) -> None:
        from hw_velxio.design import tiers

        self._needs()
        table = tiers.describe()
        self.assertEqual(len(table), 22,
                         f"阶梯应有 22 级（21 级 + O2.18 固件源码），实际 {len(table)}")
        ids = {t["id"] for t in table}
        for expected in ("O0", "O2.5", "O2.12", "O3.0", "O5"):
            self.assertIn(expected, ids)

    def test_four_outcomes_are_distinguished(self) -> None:
        """**四种"没验"必须分开**：验过 / 失败 / 有检查没跑 / 无检查或需人。

        混成一句"未验证"就等于没说 —— 这正是 P3 要解决的事。
        """
        from hw_velxio.design import assemble, coverify, physical, tiers

        self._needs()
        result = assemble.assemble_roles(
            ["camera", "gimbal_pan", "mcu", "servo_power"], design_id="t")
        checks = physical.run_checks(result.design)
        co = coverify.run(result.design)
        report = tiers.report(checks=checks, coverify=co.to_dict())
        counts = report.counts()
        self.assertIn("verified", counts)
        self.assertIn("not_run", counts, "有检查但这次没跑的层要单列")
        self.assertGreater(counts.get("no_checker", 0)
                           + counts.get("needs_human", 0), 0,
                           "没有检查/需要人的层要单列")

    def test_overclaim_is_not_made(self) -> None:
        """`net_reach` 不能证明 O1（引脚语义），budget 不能证明 O2（降额）。

        实测第一版就是这样错的：报告宣称"O1 引脚语义验过"，而工作台根本
        没有类型格检查。
        """
        from hw_velxio.design import tiers

        self._needs()
        self.assertNotIn("space.net_reach", tiers.CHECK_TIERS,
                         "net_reach 只量距离，证明不了引脚语义")
        report = tiers.report(
            checks={"findings": [{"check": "space.net_reach", "ok": True}]},
            coverify={"findings": []})
        verified = {t.id for t in report.tiers if t.outcome == "verified"}
        self.assertNotIn("O1", verified, "net_reach 通过不该让 O1 变成 verified")

    def test_ladder_always_available(self) -> None:
        """**阶梯表在本地** —— 不存在"取不到阶梯"的状态。"""
        from hw_velxio.design import tiers

        self.assertEqual(len(tiers.describe()), 22)
        report = tiers.report()
        self.assertTrue(report.available)
        self.assertGreater(len(report.tiers), 0)

class TestStandalone(unittest.TestCase):
    """**不依赖 hwforge** —— 整条链路在完全没有 hwforge 的环境里也要跑通。

    迁移前，需求分析、模板、装配、体素空间、预算、账本、协同验证、阶梯
    全部来自 hwforge 的 4366 行代码。没有它，除了 2D 视图什么都做不了。

    这些测试用一个上下文把 `hwforge` 从 `sys.modules` 里彻底挡掉，
    比"看环境变量"可靠：环境变量只影响路径解析，而这个直接让
    `import hwforge` 失败。
    """

    class _BlockHwforge:
        """让 `import hwforge` 抛 ImportError。"""

        def __enter__(self) -> None:
            import sys
            self._saved = {name: sys.modules[name]
                           for name in list(sys.modules)
                           if name == "hwforge" or name.startswith("hwforge.")}
            for name in self._saved:
                del sys.modules[name]
            # `None` 让 import 直接失败（比删掉更硬）
            sys.modules["hwforge"] = None  # type: ignore[assignment]

        def __exit__(self, *exc: object) -> None:
            import sys
            sys.modules.pop("hwforge", None)
            sys.modules.update(self._saved)

    def test_import_hwforge_really_fails(self) -> None:
        """先证明这个上下文**真的**挡住 hwforge，否则后面全是假通过。"""
        with self._BlockHwforge():
            with self.assertRaises(ImportError):
                __import__("hwforge")

    def test_library_is_local(self) -> None:
        """模板库是本地数据，不需要外部 checkout。"""
        from hw_velxio.design import library

        with self._BlockHwforge():
            keys = library.keys()
            self.assertEqual(len(keys), 16, f"应有 16 个模板，实际 {len(keys)}")
            self.assertEqual(library.size_mm("camera_module"), (8.0, 25.0, 25.0))
            self.assertEqual(library.pin_names("gimbal_servo"),
                             {"PWM", "VCC", "GND"})
            # 机械件没有电气引脚 —— 事实，不是缺失
            self.assertEqual(library.pin_names("wall_bracket"), set())

    def test_full_chain_without_hwforge(self) -> None:
        """需求 → 角色 → 装配 → 检查 → 协同 → 阶梯，一环不少。"""
        from hw_velxio.design import (assemble, coverify, physical,
                                      requirements, roles, tiers)

        with self._BlockHwforge():
            # R1 需求
            found = requirements.analyse("壁挂云台相机 手机看实时视频")
            self.assertTrue(found.ok, found.summary())
            self.assertIn("camera", found.roles)

            # R2 角色校验（永远能跑）
            check = roles.validate(found.roles)
            self.assertTrue(check.ok, check.summary())
            self.assertFalse(check.hwforge_unavailable)

            # R3 装配
            result = assemble.assemble_roles(found.roles, design_id="t")
            self.assertTrue(result.ok, f"装配失败：{result.problems}")
            self.assertGreater(len(result.design.parts), 0)

            # R4 物理检查
            findings = physical.run_checks(result.design).get("findings") or []
            self.assertGreater(len(findings), 0, "物理检查必须有结论")

            # R4 协同验证
            co = coverify.run(result.design)
            self.assertEqual(len(co.checks), 8)

            # P3 阶梯
            report = tiers.report(checks={"findings": findings},
                                  coverify=co.to_dict())
            self.assertTrue(report.available)
            self.assertEqual(len(report.tiers), 22)

    def test_budget_and_ledger_are_local(self) -> None:
        from hw_velxio.design import budget, ledger

        with self._BlockHwforge():
            tree = budget.BudgetTree()
            tree.add_node("system", None, {"power_w": 5.0})
            tree.add_node("modules", "system", {"power_w": 3.0},
                          coupling_group="shared")
            tree.add_claim("modules", "power_w", 4.0)
            verdict = tree.evaluate()
            self.assertFalse(verdict["feasible"], "4W > 3W 应判不可行")

            book = ledger.Ledger()
            self.assertEqual(len(book.open_irreversible()), 5)
            book.lock("mcu_family", "ESP32-S3")
            self.assertEqual(len(book.open_irreversible()), 4)
            with self.assertRaises(ledger.LedgerError):
                book.lock("mcu_family", "STM32")   # 不可逆，不能改

    def test_scene_roundtrip_is_local(self) -> None:
        """`DesignModel → scene.json → DesignModel` 不需要 hwforge。"""
        from hw_velxio.design import assemble, scene, scene_import

        with self._BlockHwforge():
            built = assemble.assemble_roles(["camera", "mcu"], design_id="t")
            self.assertIsNotNone(built.design)
            as_scene = scene.to_scene(built.design)
            self.assertIn("space", as_scene)
            again = scene_import.from_scene(as_scene, design_id="t2")
            self.assertEqual(len(again.parts), len(built.design.parts))


class TestElectrical(unittest.TestCase):
    """DC 电学：压降 / 轨跌 / 结温。降额表补不了的那一块。"""

    def test_wire_drop_matches_ohms_law(self) -> None:
        from hw_velxio.design import electrical

        # 26AWG = 0.133 Ω/m；500mm、1.4A → 0.093 V
        result = electrical.wire_drop(26, 500.0, 1.4)
        self.assertAlmostEqual(result.drop_v, 0.093, places=3)
        self.assertAlmostEqual(result.resistance_mohm, 66.5, places=1)
        self.assertTrue(result.ok)

    def test_thin_wire_fails_the_budget(self) -> None:
        """同样长度电流，28AWG 超预算而同规格 26AWG 不超。

        **实测数值**（不是估计）：1.4A / 1000mm 下 26AWG 是 0.186V（过），
        28AWG 是 0.298V（超 0.25V 预算）。这就是"只看数据手册"会漏的地方 ——
        两种线都标着"能过 2A"。
        """
        from hw_velxio.design import electrical

        thin = electrical.wire_drop(28, 1000.0, 1.4)
        thick = electrical.wire_drop(26, 1000.0, 1.4)
        self.assertAlmostEqual(thick.drop_v, 0.186, places=3)
        self.assertAlmostEqual(thin.drop_v, 0.298, places=3)
        self.assertTrue(thick.ok, "26AWG 应通过")
        self.assertFalse(thin.ok, "28AWG 应超 0.25V 预算")
        self.assertTrue(thin.note, "失败必须给修复方向")

    def test_ldo_cooks_at_full_load(self) -> None:
        """5→3.3V @1A 在 SOT-223 里 Tj 135°C —— 会热关断。"""
        from hw_velxio.design import electrical

        hot = electrical.regulator_thermal("linear", 5.0, 3.3, 1.0)
        cool = electrical.regulator_thermal("linear", 5.0, 3.3, 0.5)
        self.assertAlmostEqual(hot.power_w, 1.7, places=2)
        self.assertFalse(hot.ok, f"Tj {hot.tj_c:.0f}°C 应判失败")
        self.assertTrue(cool.ok, f"半载 Tj {cool.tj_c:.0f}°C 应通过")

    def test_rail_sag_and_over_rating_are_separate(self) -> None:
        """**过载是独立失效**，不只是"压降没超"。

        2A 电源带 2.1A：压降只有 0.19V（看着正常），但电源在过载工作。
        两种失效的修法不同，所以不能折进同一个布尔。
        """
        from hw_velxio.design import electrical

        over = electrical.rail_under_load("5V", 5.0, 2.1, 90.0, 4.8,
                                          rated_a=2.0)
        self.assertGreater(over.v_at_load, 4.8, "电压确实还在最低之上")
        self.assertTrue(over.over_rated, "但必须标出过载")
        self.assertFalse(over.ok, "过载不该判通过")
        self.assertIn("额定", over.note)

    def test_no_data_is_not_a_pass(self) -> None:
        """没有负载数据时报未验证，不拿默认值冒充通过。"""
        from hw_velxio.design import electrical
        from hw_velxio.design.model import Board, DesignModel, Part, Placement

        design = DesignModel(id="t", board=Board(kind="x",
                                                 size_mm={"w": 40, "h": 30}),
                             parts=[Part(ref="U1", kind="k",
                                         size_mm={"w": 5, "h": 5, "d": 1},
                                         placement=Placement(x_mm=1, y_mm=1))])
        report = electrical.run(design, inputs={"rails": [], "regulators": [],
                                                "wires": []})
        # 完全没输入 → 没有 findings，也不该说 ok
        self.assertFalse(report.ok, "没有可评估的数据时 ok 必须为 False")

    def test_zero_load_is_not_a_pass(self) -> None:
        """**零负载不是"负载很轻"** —— 它是"没申报"，必须判不通过。

        这是空过的原始现场：`load_a = total_load` 在没有任何元件申报电流时
        等于 `0.0`，而 `0.0 is None` 为假，于是"没数据"分支永远进不去，
        `5.0 >= 4.8` 直接算出 `ok=True`。整个 141 元件的工程因此拿到
        一条绿色 5V 轨。
        """
        from hw_velxio.design import electrical

        got = electrical.rail_under_load("5V", 5.0, 0.0, 90.0, 4.8,
                                         rated_a=2.0)
        self.assertFalse(got.ok, "没申报电流不能判通过")
        self.assertIn("未申报", got.note)
        self.assertFalse(got.over_rated, "零负载谈不上过载")

    def test_zero_load_design_reports_not_verified(self) -> None:
        """整条 `run()` 路径上零负载也要落到 `not_verified`，不只是单元函数。"""
        from hw_velxio.design import electrical
        from hw_velxio.design.model import Board, DesignModel, Part, Placement

        design = DesignModel(
            id="t", board=Board(kind="x", size_mm={"w": 80, "h": 60}),
            parts=[Part(ref=f"R{i}", kind="k",
                        size_mm={"w": 2, "h": 1, "d": 1},
                        placement=Placement(x_mm=1 + i * 3, y_mm=1))
                   for i in range(10)])
        report = electrical.run(design)
        self.assertFalse(report.ok)
        self.assertEqual(report.rails, [], "零负载不该产出 rails 条目")
        self.assertTrue(any("负载电流" in m for m in report.not_verified),
                        f"必须说明缺的是负载电流：{report.not_verified}")

    def test_regulator_default_input_is_not_assumed(self) -> None:
        """工程没申报稳压器时**不拿 `DEFAULT_INPUTS` 顶**，报未验证。

        `DEFAULT_INPUTS` 里那条 SOT-223 是示例数字。先前无条件用它算，
        任何工程都会得到一个 `Tj=82.7°C → ok:true` 的热结论 ——
        而这个工程里可能一颗稳压器都没有。
        """
        from hw_velxio.design import electrical
        from hw_velxio.design.model import Board, DesignModel

        design = DesignModel(id="t", board=Board(kind="x",
                                                 size_mm={"w": 40, "h": 30}),
                             parts=[])
        report = electrical.run(design)
        self.assertEqual(report.regulators, [], "没申报就不该有稳压器结论")
        self.assertEqual(report.provenance.get("regulators"), "未申报")
        self.assertTrue(any("稳压器" in m for m in report.not_verified),
                        f"必须说明缺的是稳压器申报：{report.not_verified}")

    def test_declared_electrical_still_passes(self) -> None:
        """补上数据之后必须能真的通过 —— 否则这个修法只是把绿灯换成红灯。"""
        from hw_velxio.design import electrical
        from hw_velxio.design.model import Board, DesignModel, Part, Placement

        design = DesignModel(
            id="t", board=Board(kind="x", size_mm={"w": 80, "h": 60}),
            parts=[Part(ref=f"SV{i}", kind="gimbal_servo",
                        size_mm={"w": 20, "h": 20, "d": 20},
                        placement=Placement(x_mm=1 + i * 22, y_mm=1),
                        electrical={"stall_current_a": 0.2})
                   for i in range(2)])
        report = electrical.run(design, inputs={
            "rails": [{"name": "5V", "nominal_v": 5.0, "source_r_mohm": 90.0,
                       "min_required_v": 4.8, "rated_a": 2.0}],
            "regulators": [], "wires": []})
        self.assertTrue(report.rails, "有负载就该有一条轨结论")
        self.assertTrue(report.rails[0].ok, "0.4A@2A 轨不该被判失败")
        self.assertEqual(report.not_verified, [])
        self.assertTrue(report.ok)


class TestFirmware(unittest.TestCase):
    """固件源码检查：文本与结构，不是语义分析。"""

    def _tree(self) -> "object":
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp()) / "src"
        root.mkdir()
        (root / "app.cpp").write_text(
            "void OnLinkDown(void) { printf(\"down\"); }\n"
            "void mat_mul_4x4(int *a) { }\n"
            "void never_called(void) { }\n"
            "int armed = 0;\n"
            "void loop(void) { armed = true; mat_mul_4x4(0); }\n"
            "// TODO: fix\n",
            encoding="utf-8")
        return root

    def test_uncalled_rule_does_not_flag_called_functions(self) -> None:
        """**计数规则的关键**：被调用的函数不能被报成"没人调"。

        第一版只看"以 `;` 结尾"就当成声明并减掉，于是
        `mat_mul_4x4(F, kf->P, F);` 这种调用语句被算成声明，
        函数看起来没人调 —— 计数膨胀、真结果被埋。
        """
        from hw_velxio.design import firmware

        report = firmware.run(self._tree(), scope=["uncalled"])
        names = {f.symbol for f in report.findings}
        self.assertIn("OnLinkDown", names, "没人调的函数要报出来")
        self.assertIn("never_called", names)
        self.assertNotIn("mat_mul_4x4", names,
                         "mat_mul_4x4 被调用了，不该报")
        self.assertNotIn("loop", names, "入口点不该报")

    def test_every_finding_says_what_it_cannot_see(self) -> None:
        """每条 finding 必须写明"看不见什么" —— 否则会被当成结论。"""
        from hw_velxio.design import firmware

        report = firmware.run(self._tree())
        self.assertGreater(len(report.findings), 0)
        for finding in report.findings:
            self.assertTrue(finding.missing.strip(),
                            f"{finding.check} 没写 missing："
                            f"{finding.message[:50]}")

    def test_timeout_path_is_detected(self) -> None:
        from hw_velxio.design import firmware

        report = firmware.run(self._tree(), scope=["timeout"])
        self.assertTrue(any(f.symbol == "armed" for f in report.findings),
                        f"armed 置 true 无清除，应被报出："
                        f"{[f.message for f in report.findings]}")

    def test_bom_zero_ohm_vs_10k_is_caught(self) -> None:
        """**0Ω 是真实的值**，不是"没填"。

        实测案例：R290 标 0Ω，型号 RS-05K1002FT → 1002 解码 10kΩ。
        早先正则锚在结尾（`(\d{4})[A-Z]?$`），型号后面还有 `FT` 后缀，
        一个都没匹配上 —— 检查看起来在跑，实际永远 0 条。
        """
        from hw_velxio.design import firmware

        rows = [{"ref": "R290", "value": "0Ω", "mpn": "RS-05K1002FT"},
                {"ref": "R1", "value": "10k", "mpn": "RS-05K1002FT"}]
        report = firmware.run(self._tree(), scope=["bom"], bom_rows=rows)
        symbols = [f.symbol for f in report.findings]
        self.assertIn("R290", symbols, f"0Ω vs 10kΩ 必须报：{symbols}")
        self.assertNotIn("R1", symbols, "10k 与 1002 一致，不该报")

    def test_empty_dir_is_not_a_pass(self) -> None:
        """没有源码 → 不是"没有问题"，是"没读到东西"。"""
        import tempfile
        from pathlib import Path
        from hw_velxio.design import firmware

        empty = Path(tempfile.mkdtemp())
        report = firmware.run(empty)
        self.assertTrue(report.not_verified,
                        "空目录必须报未验证，不能静默通过")
        self.assertIn("没读到东西", " ".join(report.not_verified))

    def test_never_writes(self) -> None:
        """**只读**：跑完检查后文件内容不变。"""
        from hw_velxio.design import firmware

        root = self._tree()
        before = (root / "app.cpp").read_text(encoding="utf-8")
        firmware.run(root)
        after = (root / "app.cpp").read_text(encoding="utf-8")
        self.assertEqual(before, after, "检查不得改写文件")


class TestSemantics(unittest.TestCase):
    """语义树：判定链的**解释层**，不是另一个判定器。

    它唯一的目的：让 coverify 的失败项说出"这个硬件下限由内部哪个子系统
    决定、换什么能解决、什么层面修不好"。
    """

    def test_mapping_is_validated_not_guessed(self) -> None:
        """模板 key → 语义树 key 是**显式表**，且能校验。

        实测：直接拿模板 key 查语义树，16 个里只有 7 个命中 ——
        `camera_module` 查不到 `camera`。靠字符串启发式猜不准
        （`ultrasonic_ranger` 猜不出 `ranger`）。
        """
        from hw_velxio.design import semantics

        check = semantics.validate()
        self.assertTrue(check.ok, f"映射表应通过：{[p.detail for p in check.problems]}")
        # 多对一也要能查到
        self.assertIsNotNone(semantics.tree_for_template("mcu_board"))
        self.assertIsNotNone(semantics.tree_for_template("mcu_block"))
        self.assertIs(
            semantics.tree_for_template("mcu_board"),
            semantics.tree_for_template("mcu_block"),
            "两个模板应共用同一棵树（同一对象）")

    def test_every_template_has_an_explicit_decision(self) -> None:
        """**每个**装配模板都必须在映射表里有条目 —— 包括"没有树"。

        "没建模"和"忘了填"必须能分开。
        """
        from hw_velxio.design import library, semantics

        for template in library.keys():
            self.assertIn(template, semantics.TEMPLATE_TO_SEMANTIC,
                          f"模板 {template} 没有显式决定有没有语义树")

    def test_unmodelled_is_reported_not_hidden(self) -> None:
        """没建模的模板要如实列出 —— 不是"这个件简单"。"""
        from hw_velxio.design import semantics

        cov = semantics.coverage()
        self.assertGreater(cov["modelled"], 0)
        self.assertGreater(len(cov["unmodelled_list"]), 0)
        self.assertIn("未考证", cov["note"])
        # 完全查不到的模板返回 None，而不是空树
        self.assertIsNone(semantics.tree_for_template("ultrasonic_ranger"))

    def test_range_does_not_invent_a_representative_value(self) -> None:
        """**范围不取中值**。

        `(1.0, 2.0)ms` 的脉宽取中值 1.5ms 算出来的角度是编的 ——
        判定链下游没法知道它是编的。
        """
        from hw_velxio.design import semantics

        q = semantics.Quantity(value=(1.0, 2.0), unit="ms", source="datasheet")
        self.assertIsNone(q.scalar(), "范围必须返回 None，不能取中值")
        self.assertIsNone(q.to_si(), "范围转基本单位也是 None")
        self.assertEqual(q.low(), 1.0)
        self.assertEqual(q.high(), 2.0)
        # 标量正常
        scalar = semantics.Quantity(value=5.0, unit="us", source="datasheet")
        self.assertEqual(scalar.scalar(), 5.0)

    def test_trust_is_the_weakest_link(self) -> None:
        """可信度取**子系统与数值中较低的那个**。

        不能因为数值来自 datasheet，就把"它在哪个子系统里"这个推断
        也当成可信 —— 那条链路的强度是最弱一环。
        """
        from hw_velxio.design import semantics

        tree = semantics.tree_for_template("gimbal_servo")
        gear = next(s for _d, s in tree.flat() if s.name == "gear_train")
        # gear_train 的 ratio_est 是 inferred（0.3）
        self.assertAlmostEqual(gear.trust, 0.3, places=2,
                               msg="含 inferred 数值的子系统可信度应为 0.3")
        bridge = next(s for _d, s in tree.flat() if s.name == "h_bridge")
        self.assertAlmostEqual(bridge.trust, 0.3, places=2,
                               msg="子系统本身是 inferred 就该是 0.3")

    def test_every_spec_is_a_quantity(self) -> None:
        """结构化树里不能有裸字符串 —— 判定层拿到字符串没法比较。"""
        from hw_velxio.design import semantics

        for key, tree in semantics.TREES.items():
            for _depth, subsystem in tree.flat():
                for spec_key, value in subsystem.specs.items():
                    self.assertIsInstance(
                        value, semantics.Quantity,
                        f"{key}.{subsystem.name}.{spec_key} 不是 Quantity")

    def test_fields_cover_what_coverify_needs(self) -> None:
        """树里必须有 coverify 真正消费的字段。

        这条守的是"树成为判定链上游数据源"，而不是一份平行文档。
        """
        from hw_velxio.design import coverify, semantics

        tree = semantics.tree_for_template("gimbal_servo")
        for axis_field in ("deadband_us", "stall_current_ma"):
            quantity = tree.spec(axis_field)
            self.assertIsNotNone(quantity,
                                 f"树里缺 {axis_field}（coverify 要它）")
            self.assertIsNotNone(quantity.scalar(),
                                 f"{axis_field} 必须是标量，判定层才用得上")


class TestRootCause(unittest.TestCase):
    """coverify 失败项 → 语义树里的物理来源。"""

    def _design(self) -> "object":
        from hw_velxio.design import assemble
        result = assemble.assemble_roles(
            ["camera", "gimbal_pan", "mcu", "servo_power"], design_id="t")
        return result.design

    def test_failed_check_gets_root_cause(self) -> None:
        """收紧指向精度让 cv01 失败 → 必须带 root_cause。"""
        from hw_velxio.design import coverify

        design = self._design()
        if design is None:
            self.skipTest("装配不可用")
        co = coverify.run(design, numbers={"pointing_accuracy_deg": 0.5})
        failed = [f for f in co.findings
                  if not f["ok"] and f["check"].endswith("servo_resolution")]
        self.assertTrue(failed, "0.5° 要求下 cv01 应失败")
        cause = failed[0].get("root_cause")
        self.assertIsNotNone(cause, "失败项应带 root_cause")

    def test_root_cause_names_the_subsystem_and_the_culprit(self) -> None:
        """root_cause 必须指出**哪个子系统**、**哪个数值**。"""
        from hw_velxio.design import semantics

        tree = semantics.tree_for_template("gimbal_servo")
        from hw_velxio.design import coverify

        cause = coverify.root_cause_for("cv.servo_resolution", tree)
        self.assertIsNotNone(cause)
        self.assertEqual(cause["subsystem"], "control_board",
                         f"死区由控制板决定，实际：{cause['subsystem']}")
        self.assertEqual(cause["field"], "deadband_us")
        self.assertIn("5", cause["value"])

    def test_root_cause_says_what_cannot_fix_it(self) -> None:
        """**`cannot_fix_by` 是最有价值的字段。**

        它把"硬件下限"变成一条可执行判断，让调用方不在错误的层浪费时间。
        """
        from hw_velxio.design import coverify, semantics

        tree = semantics.tree_for_template("gimbal_servo")
        cause = coverify.root_cause_for("cv.servo_resolution", tree)
        self.assertIsNotNone(cause)
        self.assertTrue(cause["cannot_fix_by"])
        self.assertIn("固件", cause["cannot_fix_by"],
                      "舵机分辨率是硬件下限，固件修不好")
        self.assertTrue(cause["fix_options"], "必须给可执行的修法")

    def test_unmapped_check_gets_no_root_cause(self) -> None:
        """**不在映射里的检查不给 root_cause** —— 硬套会给出错误解释。

        `cv.vision_resolution` 考的是相机，不在舵机树里。
        """
        from hw_velxio.design import coverify, semantics

        tree = semantics.tree_for_template("gimbal_servo")
        self.assertIsNone(
            coverify.root_cause_for("cv.vision_resolution", tree),
            "视觉分辨率不在舵机树里，不该硬套")

    def test_root_cause_carries_the_tree_caveats(self) -> None:
        """root_cause 要带出树的 caveats —— 决定这条解释能被引用到什么程度。"""
        from hw_velxio.design import coverify, semantics

        tree = semantics.tree_for_template("gimbal_servo")
        cause = coverify.root_cause_for("cv.servo_resolution", tree)
        self.assertIsNotNone(cause)
        self.assertTrue(cause["caveats"],
                        "树的不可引用声明必须跟着解释一起走")


class TestPsuSemantics(unittest.TestCase):
    """psu_5v 树 —— `electrical` 的直接上游。

    电源树的特殊之处：它是**唯一同时被两条判定链消费**的树。
    `electrical.rail_under_load()` 从它取 `rated_a`/`r_out_mohm`，
    `coverify` 的 `cv.current` 从它取电源侧根因。
    """

    def test_tree_exists_and_is_validated(self) -> None:
        from hw_velxio.design import semantics

        tree = semantics.tree_for_template("psu_5v")
        self.assertIsNotNone(tree, "psu_5v 必须有语义树")
        self.assertTrue(semantics.validate().ok)

    def test_electrical_reads_rating_from_tree(self) -> None:
        """**树是上游：额定与内阻只在树里写一处。**

        早先 `DEFAULT_INPUTS` 和树里各写一份 2.0A / 90mΩ —— 改一处不
        改另一处就漂移。`rails_from_semantics()` 让 `electrical` 来取。
        """
        from hw_velxio.design import electrical, semantics

        rails = electrical.rails_from_semantics("psu_5v")
        self.assertEqual(len(rails), 1)
        rail = rails[0]
        tree = semantics.tree_for_template("psu_5v")
        self.assertEqual(rail["rated_a"], tree.spec("rated_a").scalar())
        self.assertEqual(rail["source_r_mohm"],
                         tree.spec("r_out_mohm").scalar())
        self.assertEqual(rail["nominal_v"], tree.spec("v_out").scalar())
        # 来源要标出来 —— 决定这次分析有多少份量
        self.assertIn("semantics", rail["_source"])

    def test_electrical_run_uses_tree_by_default(self) -> None:
        from hw_velxio.design import electrical
        from hw_velxio.design.model import Board, DesignModel, Part, Placement

        design = DesignModel(
            id="t", board=Board(kind="x", size_mm={"w": 80, "h": 60}),
            parts=[Part(ref=f"SV{i}", kind="gimbal_servo",
                        size_mm={"w": 20, "h": 20, "d": 20},
                        placement=Placement(x_mm=1 + i * 22, y_mm=1),
                        electrical={"stall_current_a": 0.7})
                   for i in range(4)])
        report = electrical.run(design)
        self.assertIn("rails", report.provenance)
        self.assertIn("semantics", report.provenance["rails"],
                      "默认应从语义树取参数，不是 DEFAULT_INPUTS")

    def test_overload_and_sag_are_both_reported(self) -> None:
        """4 个舵机堵转（2.8A）超过 2A 额定 —— **两种失效都要报**。"""
        from hw_velxio.design import electrical
        from hw_velxio.design.model import Board, DesignModel, Part, Placement

        design = DesignModel(
            id="t", board=Board(kind="x", size_mm={"w": 80, "h": 60}),
            parts=[Part(ref=f"SV{i}", kind="gimbal_servo",
                        size_mm={"w": 20, "h": 20, "d": 20},
                        placement=Placement(x_mm=1 + i * 22, y_mm=1),
                        electrical={"stall_current_a": 0.7})
                   for i in range(4)])
        rail = electrical.run(design).rails[0]
        self.assertTrue(rail.over_rated, "2.8A > 2A 必须标过载")
        self.assertFalse(rail.ok)
        self.assertIn("额定", rail.note, "过载要单独说明")

    def test_coverage_grew(self) -> None:
        """加了 psu_5v 后覆盖率要真的变大，且 `unmodelled` 里不再有它。"""
        from hw_velxio.design import semantics

        cov = semantics.coverage()
        self.assertIn("psu_5v", cov["modelled_list"])
        self.assertNotIn("psu_5v", cov["unmodelled_list"])
        self.assertEqual(len(semantics.TREES), 5)


class TestPowerSideRootCause(unittest.TestCase):
    """`cv.current` 失败可能有两边原因：负载太大 或 电源太小。

    只报一边会误导 —— "换舵机还是换电源"取决于哪边是瓶颈。
    """

    def test_power_side_root_cause_exists(self) -> None:
        from hw_velxio.design import coverify

        cause = coverify.power_root_cause_for("cv.current")
        self.assertIsNotNone(cause)
        self.assertEqual(cause["side"], "power")
        self.assertEqual(cause["subsystem"], "converter")
        self.assertEqual(cause["field"], "rated_a")

    def test_both_sides_are_reported(self) -> None:
        """同一条 `cv.current` 失败要同时带负载侧和电源侧原因。"""
        from hw_velxio.design import assemble, coverify

        result = assemble.assemble_roles(
            ["camera", "gimbal_pan", "gimbal_tilt", "mcu", "servo_power"],
            design_id="t")
        if result.design is None:
            self.skipTest("装配不可用")
        co = coverify.run(result.design, numbers={"supply_ma": 1000})
        failed = [f for f in co.findings
                  if not f["ok"] and f["check"].endswith("current")]
        self.assertTrue(failed, "1A 电源带 2 个舵机应失败")
        finding = failed[0]
        self.assertIn("root_cause", finding, "负载侧原因")
        self.assertIn("root_cause_power_side", finding, "电源侧原因")
        self.assertEqual(finding["root_cause"]["subsystem"], "dc_motor")
        self.assertEqual(finding["root_cause_power_side"]["subsystem"],
                         "converter")

    def test_unrelated_check_gets_no_power_cause(self) -> None:
        from hw_velxio.design import coverify

        self.assertIsNone(
            coverify.power_root_cause_for("cv.vision_resolution"),
            "视觉分辨率与电源无关，不该硬套电源根因")


class TestPhase(unittest.TestCase):
    """阶段推导：**确定性**判断"空/半成品/成品"。

    这组用例存在的原因是一个真实事故：`示例-无人机` 的 `parts: []`，
    9 条物理检查**全部 ok**（空集里没有东西可以违反），界面显示绿灯。
    阶段推导是唯一能揭穿它的东西。
    """

    def _design(self, parts: list[Part], open_irreversible=None) -> DesignModel:
        from hw_velxio.design import phase as phase_module
        design = DesignModel(id="p", title="t", board=board_preset("arduino-uno"))
        design.parts = parts
        return design, phase_module

    def test_empty_project_is_empty_not_complete(self) -> None:
        design, phase = self._design([])
        result = phase.evaluate(design)
        self.assertEqual(result["phase"], "empty")
        self.assertIn("假绿灯", result["reason"])
        self.assertTrue(result["veto"], "必须说明差什么，而不是只给一个形容词")

    def test_full_sized_parts_without_open_decisions_is_complete(self) -> None:
        part = Part(ref="r1", kind="resistor", role="component",
                    size_mm={"w": 1.6, "h": 0.8, "d": 0.45},
                    physical_source="datasheet")
        design, phase = self._design([part])
        result = phase.evaluate(design, open_irreversible=[])
        self.assertEqual(result["phase"], "complete", result["veto"])

    def test_estimated_size_keeps_it_partial(self) -> None:
        # 估算尺寸不许被当成成品：那会把"我没量过"说成"我量过"。
        part = Part(ref="r1", kind="resistor", role="component",
                    size_mm={"w": 1.6, "h": 0.8, "d": 0.45},
                    physical_source="estimated")
        design, phase = self._design([part])
        result = phase.evaluate(design, open_irreversible=[])
        self.assertEqual(result["phase"], "partial")
        self.assertTrue(any("估算" in v for v in result["veto"]), result["veto"])

    def test_zero_height_keeps_it_partial(self) -> None:
        part = Part(ref="r1", kind="resistor", role="component",
                    size_mm={"w": 1.6, "h": 0.8, "d": 0.0},
                    physical_source="datasheet")
        design, phase = self._design([part])
        result = phase.evaluate(design, open_irreversible=[])
        self.assertEqual(result["phase"], "partial")
        self.assertTrue(any("高度未知" in v for v in result["veto"]), result["veto"])

    def test_open_irreversible_blocks_complete(self) -> None:
        part = Part(ref="r1", kind="resistor", role="component",
                    size_mm={"w": 1.6, "h": 0.8, "d": 0.45},
                    physical_source="datasheet")
        design, phase = self._design([part])
        result = phase.evaluate(design, open_irreversible=["package_family"])
        self.assertEqual(result["phase"], "partial")
        self.assertTrue(any("package_family" in v for v in result["veto"]))

    def test_vacuous_lists_空集通过(self) -> None:
        from hw_velxio.design import phase as phase_module
        design = DesignModel(id="p", title="t", board=board_preset("arduino-uno"))
        findings = [{"check": "space.overlap", "ok": True},
                    {"check": "space.net_reach", "ok": True}]
        listed = phase_module.vacuous(design, findings)
        self.assertIn("space.overlap", listed)
        self.assertIn("space.net_reach", listed)

    def test_vacuous_shrinks_when_parts_exist(self) -> None:
        """有元件之后，不该再把所有检查都算成空集通过。"""
        from hw_velxio.design import phase as phase_module
        design = DesignModel(id="p", title="t", board=board_preset("arduino-uno"))
        design.parts = [Part(ref="r1", kind="resistor",
                             size_mm={"w": 1.6, "h": 0.8, "d": 0.45})]
        findings = [{"check": "space.overlap", "ok": True},
                    {"check": "space.connector_edge", "ok": True}]
        listed = phase_module.vacuous(design, findings)
        self.assertNotIn("space.overlap", listed)
        # 没有连接器元件，这条仍然是空集通过。
        self.assertIn("space.connector_edge", listed)


class TestSurvey(unittest.TestCase):
    """勘察：初审.md 的**事实**段必须由机器生成，且判断段要保住。"""

    def test_survey_lists_projects_with_phase(self) -> None:
        from hw_velxio import design_cli
        from hw_velxio.design import survey as survey_module

        design_cli.cmd_new(["_test_survey", "arduino-uno", "勘察"])
        payload = survey_module.survey()
        ids = [row["id"] for row in payload["projects"]]
        self.assertIn("_test_survey", ids)
        row = next(r for r in payload["projects"] if r["id"] == "_test_survey")
        self.assertEqual(row["phase"], "empty")
        self.assertTrue(payload["fingerprint"].startswith("sha256:"))

    def test_initial_review_has_machine_facts(self) -> None:
        from hw_velxio import design_cli
        from hw_velxio.design import survey as survey_module

        design_cli.cmd_new(["_test_review", "arduino-uno", "初审"])
        payload = survey_module.survey()
        path = survey_module.write_initial_review(payload)
        text = path.read_text(encoding="utf-8")
        self.assertIn("hwv-survey:begin", text)
        self.assertIn("fingerprint: sha256:", text)
        self.assertIn("## 判断", text, "必须留出 AI 撰写的位置")
        self.assertIn("_test_review", text)

    def test_rewrite_preserves_judgement_section(self) -> None:
        """重写事实段不能冲掉 AI 写的判断——否则每轮初审都从零开始。"""
        from hw_velxio import design_cli
        from hw_velxio.design import survey as survey_module

        design_cli.cmd_new(["_test_keep", "arduino-uno", "保留"])
        payload = survey_module.survey()
        path = survey_module.write_initial_review(payload)
        text = path.read_text(encoding="utf-8")
        marker = "## 判断（AI 撰写）\n\n这里是我上一轮的结论：LED 缺限流电阻。"
        path.write_text(text.split("## 判断")[0] + marker, encoding="utf-8")

        survey_module.write_initial_review(survey_module.survey())
        after = path.read_text(encoding="utf-8")
        self.assertIn("LED 缺限流电阻", after, "判断段被覆盖了")
        self.assertIn("hwv-survey:begin", after, "事实段没刷新")

    def test_stale_detection(self) -> None:
        from hw_velxio import design_cli
        from hw_velxio.design import survey as survey_module

        design_cli.cmd_new(["_test_stale", "arduino-uno", "过期"])
        survey_module.write_initial_review(survey_module.survey())
        fresh = survey_module.read_initial_review()
        self.assertFalse(fresh["stale"], "刚写完不该判过期")

        # 新加一个工程 → 指纹变 → 旧初审必须被判定为过期。
        design_cli.cmd_new(["_test_stale2", "arduino-uno", "新增"])
        after = survey_module.read_initial_review()
        self.assertTrue(after["stale"], "工作区变了却仍说不过期")
        self.assertNotEqual(after["fingerprint"], after["current_fingerprint"])


class TestPutValidation(unittest.TestCase):
    """`put` 是唯一**受校验**的设计写入路径（hw_put 的底座）。"""

    def test_put_rejects_invalid_model(self) -> None:
        from hw_velxio import design_cli
        payload = design_cli.cmd_put(["_test_put", "missing-file.json"])
        self.assertEqual(payload["status"], "failed")

    def test_put_reports_validate_problems(self) -> None:
        """非法设计**允许落盘**但必须回报 problems（面板要能显示）。"""
        from hw_velxio.design.model import DesignModel
        from hw_velxio.design.boards import board_preset

        design = DesignModel(id="_test_put2", title="t",
                             board=board_preset("arduino-uno"))
        design.parts = [Part(ref="r1", kind="resistor",
                             size_mm={"w": 0.0, "h": 0.0, "d": 0.0},
                             physical_source="estimated")]
        problems = design.validate()
        self.assertTrue(any("尺寸缺失" in p for p in problems), problems)


class TestEntries(unittest.TestCase):
    """工作区条目识别：把"真实项目"从文件堆里认出来。

    这组用例的存在原因是一次误判：一个装了 7 个 CubeMX 工程、423 个源文件、
    9 个固件的工作区，在评审里显示成「一个空的 Arduino Uno」——因为只有
    含 design.json 的目录才算工程。
    """

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="hwv-entries-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def test_ioc_gives_real_mcu(self) -> None:
        from hw_velxio.design import entries
        project = self.base / "F103-GPS-master"
        (project / "Core" / "Src").mkdir(parents=True)
        (project / "app.ioc").write_text(
            "Mcu.UserName=STM32F103C8Tx\n"
            "PA10.Signal=USART1_RX\nPA13.Signal=SYS_JTMS-SWDIO\n",
            encoding="utf-8")
        (project / "Core" / "Src" / "main.c").write_text("int main(){}\n",
                                                         encoding="utf-8")
        result = entries.scan(self.base)
        row = next(e for e in result["entries"] if e["id"] == "F103-GPS-master")
        self.assertEqual(row["mcu"], "STM32F103C8Tx")
        self.assertEqual(row["kind"], "cubemx")
        self.assertGreaterEqual(row["source_total"], 1)
        # 未接入 ≠ 空
        self.assertFalse(row["adopted"])
        self.assertEqual(row["phase"], "not_adopted")

    def test_core_dir_is_counted(self) -> None:
        """`Core/` 是 CubeMX 的标准源码目录，**不能**当第三方残留跳过。"""
        from hw_velxio.design import entries
        project = self.base / "p1"
        (project / "Core" / "Src").mkdir(parents=True)
        (project / "app.ioc").write_text("Mcu.UserName=X\n", encoding="utf-8")
        for name in ("a.c", "b.c", "c.h"):
            (project / "Core" / "Src" / name).write_text("x\n", encoding="utf-8")
        result = entries.scan(self.base)
        row = next(e for e in result["entries"] if e["id"] == "p1")
        self.assertEqual(row["source_total"], 3,
                         "Core/ 被当成第三方目录跳过了")

    def test_bench_project_is_an_entry(self) -> None:
        """工作台自己的工程也是条目（否则它们掉出条目总数）。"""
        from hw_velxio.design import entries
        project = self.base / "bench-proj"
        project.mkdir()
        (project / "design.json").write_text("{}", encoding="utf-8")
        result = entries.scan(self.base)
        row = next(e for e in result["entries"] if e["id"] == "bench-proj")
        self.assertTrue(row["adopted"])
        self.assertEqual(row["kind"], "bench")
        self.assertEqual(row["phase"], "adopted")

    def test_loose_bom_and_firmware_detected(self) -> None:
        from hw_velxio.design import entries
        (self.base / "BOM_board.csv").write_text(
            "Designator,Value\nR1,10k\n", encoding="utf-8")
        (self.base / "firmware.bin").write_bytes(b"\x00" * 64)
        (self.base / "config.param").write_text(
            "pid_in_roll_p=0.05\npid_in_roll_i=0.01\n", encoding="utf-8")
        result = entries.scan(self.base)
        kinds = sorted(e["kind"] for e in result["entries"])
        self.assertIn("bom", kinds)
        self.assertIn("firmware", kinds)
        self.assertIn("fc-params", kinds)


class TestLog(unittest.TestCase):
    """工程式日志：只追加 + 如实记录"没看"的文件。"""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="hwv-log-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def test_activity_is_append_only(self) -> None:
        from hw_velxio.design import log
        log.append_activity(self.base, step="survey", outcome="ok", detail="第一次")
        log.append_activity(self.base, step="check", outcome="ok", detail="第二次")
        rows = log.read_activity(self.base)["rows"]
        self.assertEqual(len(rows), 2)
        self.assertIn("check", rows[0], "新记录必须在顶部")
        self.assertIn("survey", rows[1], "旧记录必须还在")
        self.assertIn("第一次", rows[1], "旧记录内容被改写了")

    def test_coverage_reports_unread(self) -> None:
        from hw_velxio.design import log
        files = [self.base / "a.c", self.base / "b.h"]
        for path in files:
            path.write_text("x\n", encoding="utf-8")
        entries_payload = {"read_paths": [str(files[0].resolve())]}
        coverage = log.build_coverage(entries_payload, files, self.base)
        self.assertEqual(coverage["total_files"], 2)
        self.assertEqual(coverage["read_files"], 1)
        self.assertEqual(coverage["unread_files"], 1)
        self.assertEqual(coverage["unread"][0]["path"], "b.h")
        self.assertIn("未读取内容", coverage["note"])

    def test_report_written_and_listed(self) -> None:
        from hw_velxio.design import log
        payload = {
            "workspace": str(self.base), "fingerprint": "sha256:x",
            "elapsed_ms": 1, "projects": [], "entries": [],
            "entries_by_kind": {}, "counts": {},
        }
        coverage = log.build_coverage({"read_paths": []},
                                      [self.base / "x.c"], self.base)
        (self.base / "x.c").write_text("x\n", encoding="utf-8")
        path = log.write_survey_report(self.base, payload, coverage)
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("覆盖度", text)
        self.assertIn("未读", text)
        names = [row["name"] for row in log.list_reports(self.base)]
        self.assertIn(path.name, names)

    def test_reports_do_not_overwrite(self) -> None:
        """两份报告不能互相覆盖（时间戳命名 + 只追加）。"""
        from hw_velxio.design import log
        payload = {"workspace": str(self.base), "fingerprint": "sha256:y",
                   "elapsed_ms": 1, "projects": [], "entries": [],
                   "entries_by_kind": {}, "counts": {}}
        coverage = log.build_coverage({"read_paths": []}, [], self.base)
        first = log.write_survey_report(self.base, payload, coverage, when=1_700_000_000)
        second = log.write_survey_report(self.base, payload, coverage, when=1_700_000_060)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file() and second.is_file())


class TestPhaseNotAdopted(unittest.TestCase):
    def test_entry_with_content_is_not_adopted_not_empty(self) -> None:
        from hw_velxio.design import phase
        result = phase.evaluate_entry({
            "adopted": False, "kind": "cubemx",
            "source_total": 30, "mcu": "STM32F103C8Tx",
        })
        self.assertEqual(result["phase"], "not_adopted")
        self.assertIn("design.json", "；".join(result["veto"]))
        self.assertIn("30 个源文件", result["reason"])

    def test_empty_entry_says_so(self) -> None:
        from hw_velxio.design import phase
        result = phase.evaluate_entry({"adopted": False, "kind": "cmake"})
        self.assertEqual(result["phase"], "not_adopted")
        self.assertTrue(result["veto"])


def _make_epro2(path: Path, *, components: list[tuple[str, str]],
                nets: list[str], title: str = "测试工程",
                designators: dict[str, str] | None = None) -> None:
    """造一个**符合 V3 规范**的最小 `.epro2`。

    真实格式（`docs/easyeda-format.md`）：

        {"type":"DOCHEAD","ticket":1}||{"docType":"PCB","uuid":"u1"}|
        {"type":"COMPONENT","ticket":1,"id":"e0"}||{"x":1000,"y":500,...}|
        {"type":"ATTR","ticket":2,"id":"e0-a0"}||{"parentId":"e0","key":"Designator","value":"R1"}|
        {"type":"PAD_NET","ticket":3,"id":"[\\"PAD_NET\\",\\"e0\\",\\"1\\",\\"p1\\"]"}||{"padNet":"GND"}|
        {"type":"POLY","ticket":4,"id":"e9"}||{"polyType":"BOARD_OUTLINE","path":["R",0,0,2700,1800]}|

    每行两个 JSON 用 `||` 连接、行尾一个 `|`。**行与行之间是 `|\n`**，
    按 `|` 切分会错位（第一版正是如此）。
    """
    import zipfile
    lines: list[str] = ['{"type":"DOCHEAD","ticket":1}||{"docType":"PCB","uuid":"u1"}|']
    ticket = 100
    for index, (ref, value) in enumerate(components):
        component_id = f"e{index}"
        # 坐标单位是 mil：1000 mil = 25.4 mm
        x, y = 1000 + index * 200, 500 + index * 100
        lines.append(
            f'{{"type":"COMPONENT","ticket":{ticket},"id":"{component_id}"}}'
            f'||{{"x":{x},"y":{y},"angle":90,"layerId":1}}|')
        ticket += 1
        lines.append(
            f'{{"type":"ATTR","ticket":{ticket},"id":"{component_id}-a0"}}'
            f'||{{"parentId":"{component_id}","key":"Designator","value":"{ref}"}}|')
        ticket += 1
        if value:
            lines.append(
                f'{{"type":"ATTR","ticket":{ticket},"id":"{component_id}-a1"}}'
                f'||{{"parentId":"{component_id}","key":"Value","value":"{value}"}}|')
            ticket += 1
    # 原理图侧：Value 与 3D Model Transform（真实值与尺寸的来源）。
    # 用**另一个文档**（SCH_PAGE），验证跨文档按 Designator 桥接。
    lines.append('{"type":"DOCHEAD","ticket":2}||{"docType":"SCH_PAGE","uuid":"s1"}|')
    for index, (ref, value) in enumerate(components):
        if value:
            lines.append(
                f'{{"type":"ATTR","ticket":{ticket},"id":"s{index}-v"}}'
                f'||{{"parentId":"s{index}","key":"Value","value":"{value}"}}|')
            ticket += 1
        lines.append(
            f'{{"type":"ATTR","ticket":{ticket},"id":"s{index}-d"}}'
            f'||{{"parentId":"s{index}","key":"Designator","value":"{ref}"}}|')
        ticket += 1
        # 3D Model Transform：sizeX,sizeY,sizeZ 单位 mil
        # 62.992 mil = 1.6mm，31.496 = 0.8mm
        lines.append(
            f'{{"type":"ATTR","ticket":{ticket},"id":"s{index}-t"}}'
            f'||{{"parentId":"s{index}","key":"3D Model Transform",'
            f'"value":"62.992,31.496,0,0,0,0,0,0,0"}}|')
        ticket += 1
        lines.append(
            f'{{"type":"ATTR","ticket":{ticket},"id":"s{index}-m"}}'
            f'||{{"parentId":"s{index}","key":"3D Model Title",'
            f'"value":"C0603_L1.6-W0.8-H0.8"}}|')
        ticket += 1
    # 回到 PCB 文档继续写网表
    lines.append('{"type":"DOCHEAD","ticket":3}||{"docType":"PCB","uuid":"u1"}|')

    # 网表：PAD_NET 的 id 是键式数组串
    for index, net in enumerate(nets):
        component_id = f"e{index % max(1, len(components))}"
        pad = "1"
        key = json.dumps(["PAD_NET", component_id, pad, f"p{index}"])
        lines.append(
            f'{{"type":"PAD_NET","ticket":{ticket},"id":'
            f'{json.dumps(key)}}}||{{"padNet":"{net}"}}|')
        ticket += 1
    # 板框：POLY + polyType=BOARD_OUTLINE，path 是紧凑数组（mil）
    lines.append(
        f'{{"type":"POLY","ticket":{ticket},"id":"e900"}}'
        f'||{{"layerId":11,"polyType":"BOARD_OUTLINE",'
        f'"path":["R",0,0,4000,3000]}}|')

    payload = "\n".join(lines) + "\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("project2.json",
                         json.dumps({"title": title, "editorVersion": "4.1.100"}))
        archive.writestr("test.epru", payload)
        archive.writestr("IMAGE/x.webp", b"RIFF")


class TestEasyEda(unittest.TestCase):
    """立创 EDA（`.epro2`）解析 —— 唯一能给出**真实坐标与网表**的材料。

    这组用例的存在原因：工作区里 2085 个文件没有一个能同时提供"元件在哪"
    和"谁连到谁"。`.epro2` 补上了这个缺口，A（坐标）与 B（网表）都在这里
    验证。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="hwv-eda-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_extracts_components_and_nets(self) -> None:
        from hw_velxio.design import easyeda
        path = self.tmp / "p.epro2"
        _make_epro2(path,
                    components=[("R1", "10k"), ("C1", "100nF"), ("U1", "STM32")],
                    nets=["GND", "VCC3V3", "GND"],
                    title="我的工程")
        result = easyeda.parse(path)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["title"], "我的工程")
        self.assertEqual(result["counts"]["components"], 3)
        # 网表结构是 {net: [ "REF.pad", ... ]}
        self.assertIn("GND", result["nets"])
        self.assertIn("VCC3V3", result["nets"])
        self.assertTrue(all("." in pin for pin in result["nets"]["GND"]))

    def test_coords_converted_from_mil(self) -> None:
        """坐标单位是 mil；1000 mil = 25.4 mm。这是 A 的核心断言。"""
        from hw_velxio.design import easyeda
        path = self.tmp / "coord.epro2"
        _make_epro2(path, components=[("R1", "10k")], nets=[])
        result = easyeda.parse(path)
        component = result["components"][0]
        self.assertEqual(result["counts"]["with_coords"], 1)
        # 夹具里第一个元件 x=1000 mil, y=500 mil
        self.assertAlmostEqual(component["x_mm"], 1000 * 0.0254, places=3)
        self.assertAlmostEqual(component["y_mm"], 500 * 0.0254, places=3)
        self.assertEqual(component["angle_deg"], 90)

    def test_board_outline_read_from_poly(self) -> None:
        """板框来自 `POLY.polyType=BOARD_OUTLINE`，不是板卡预设。"""
        from hw_velxio.design import easyeda
        path = self.tmp / "outline.epro2"
        _make_epro2(path, components=[("R1", "10k")], nets=[])
        result = easyeda.parse(path)
        self.assertEqual(result["outline_count"], 1)
        # 夹具板框 4000x3000 mil = 101.6 x 76.2 mm
        self.assertAlmostEqual(result["board_size_mm"]["w"], 101.6, places=2)
        self.assertAlmostEqual(result["board_size_mm"]["h"], 76.2, places=2)

    def test_pad_net_keyed_id_gives_pins(self) -> None:
        """`["PAD_NET", 元件id, 焊盘号, 焊盘id]` → 真实引脚归属。"""
        from hw_velxio.design import easyeda
        path = self.tmp / "padnet.epro2"
        _make_epro2(path, components=[("R1", ""), ("R2", "")],
                    nets=["GND", "GND"])
        result = easyeda.parse(path)
        self.assertEqual(result["counts"]["with_nets"], 2)
        pins = result["nets"]["GND"]
        self.assertTrue(any(pin.startswith("R1.") for pin in pins), pins)
        self.assertTrue(any(pin.startswith("R2.") for pin in pins), pins)

    def test_placeholder_designator_counted_honestly(self) -> None:
        """`U?`/`C?` 是工程未标号的状态，要如实计数而不是当失败。"""
        from hw_velxio.design import easyeda
        path = self.tmp / "ph.epro2"
        _make_epro2(path, components=[("R1", "10k"), ("C?", "100nF")],
                    nets=["GND"])
        result = easyeda.parse(path)
        self.assertEqual(result["counts"]["placeholders"], 1)
        self.assertEqual(result["counts"]["named"], 2)

    def test_missing_epru_is_reported(self) -> None:
        from hw_velxio.design import easyeda
        import zipfile
        path = self.tmp / "bad.epro2"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("project2.json", "{}")
        result = easyeda.parse(path)
        self.assertEqual(result["status"], "failed")
        self.assertIn(".epru", "；".join(result["findings"]))

    def test_not_a_zip_is_reported(self) -> None:
        from hw_velxio.design import easyeda
        path = self.tmp / "notzip.epro2"
        path.write_bytes(b"not a zip at all")
        result = easyeda.parse(path)
        self.assertEqual(result["status"], "failed")

    def test_import_uses_real_coords_not_grid(self) -> None:
        """导入必须用真实坐标；尺寸未知要标 estimated，不编造数值。"""
        from hw_velxio.design import easyeda_import
        path = self.tmp / "imp.epro2"
        _make_epro2(path,
                    components=[("R1", "10k"), ("C1", "100nF"), ("C?", "")],
                    nets=["GND", "VCC3V3"])
        design = easyeda_import.import_epro2(path, design_id="t")
        self.assertEqual(len(design.parts), 3, "占位符标号也应导入（有坐标）")
        # 夹具带 `3D Model Transform`，所以尺寸是真实推导值（derived），
        # 不是 0/estimated —— 这本身是"值 + 尺寸"通路可用的证据。
        for part in design.parts:
            self.assertGreater(part.size_mm["w"], 0, "有 3D 模型却没解出尺寸")
            self.assertEqual(part.physical_source, "derived")
        self.assertTrue(design.meta["coords_are_real"])
        # 网表真的进来了
        self.assertGreater(len(design.nets), 0)
        self.assertGreater(design.meta["nets_imported"], 0)

    def test_import_uses_project_outline(self) -> None:
        """板框来自工程，而不是 Arduino Uno 预设。"""
        from hw_velxio.design import easyeda_import
        path = self.tmp / "board.epro2"
        _make_epro2(path, components=[("R1", "10k")], nets=[])
        design = easyeda_import.import_epro2(path, design_id="t")
        self.assertAlmostEqual(design.board.size_mm["w"], 101.6, places=1)
        self.assertEqual(design.board.mount_holes, [], "不该带预设的安装孔")
        self.assertIn("EasyEDA", design.board.name)

    def test_roles_from_ref_prefix(self) -> None:
        from hw_velxio.design import easyeda_import
        path = self.tmp / "roles.epro2"
        _make_epro2(path,
                    components=[("R1", ""), ("U1", ""), ("J1", ""), ("LED1", "")],
                    nets=[])
        design = easyeda_import.import_epro2(path, design_id="t")
        roles = {p.ref: p.role for p in design.parts}
        self.assertEqual(roles["R1"], "component")
        self.assertEqual(roles["U1"], "module")
        self.assertEqual(roles["J1"], "connector")

    def test_value_bridged_from_schematic(self) -> None:
        """值来自原理图 `Value`，按 Designator 桥接到 PCB 元件。"""
        from hw_velxio.design import easyeda
        path = self.tmp / "value.epro2"
        _make_epro2(path, components=[("C1", "1uF"), ("R1", "10k")], nets=[])
        result = easyeda.parse(path)
        values = {c["ref"]: c["value"] for c in result["components"]}
        self.assertEqual(values.get("C1"), "1uF", values)
        self.assertEqual(values.get("R1"), "10k", values)
        self.assertEqual(result["counts"]["valued"], 2)

    def test_size_from_3d_model_transform(self) -> None:
        """尺寸来自 `3D Model Transform`：sizeX/Y/Z 单位 mil。"""
        from hw_velxio.design import easyeda
        path = self.tmp / "size.epro2"
        _make_epro2(path, components=[("C1", "1uF")], nets=[])
        result = easyeda.parse(path)
        component = result["components"][0]
        self.assertIsNotNone(component["size_mm"], "没有解出尺寸")
        # 62.992 mil = 1.6mm；31.496 mil = 0.8mm
        self.assertAlmostEqual(component["size_mm"]["w"], 1.6, places=2)
        self.assertAlmostEqual(component["size_mm"]["h"], 0.8, places=2)
        # 高度来自标题里的 H0.8（Transform 的 sizeZ 恒为 0）
        self.assertAlmostEqual(component["size_mm"]["d"], 0.8, places=2)

    def test_import_marks_derived_vs_estimated(self) -> None:
        """有真实尺寸 → derived；没有 → estimated。**不能一律标 estimated**。"""
        from hw_velxio.design import easyeda_import
        import zipfile
        path = self.tmp / "mixed.epro2"
        # 一个有尺寸（有 SCH 3D Transform），一个没有。
        lines = ['{"type":"DOCHEAD","ticket":1}||{"docType":"PCB","uuid":"u1"}|',
                 '{"type":"COMPONENT","ticket":2,"id":"e0"}'
                 '||{"x":1000,"y":500,"angle":0}|',
                 '{"type":"ATTR","ticket":3,"id":"e0-d"}'
                 '||{"parentId":"e0","key":"Designator","value":"C1"}|',
                 '{"type":"COMPONENT","ticket":4,"id":"e1"}'
                 '||{"x":1200,"y":600,"angle":0}|',
                 '{"type":"ATTR","ticket":5,"id":"e1-d"}'
                 '||{"parentId":"e1","key":"Designator","value":"C2"}|',
                 '{"type":"DOCHEAD","ticket":6}||{"docType":"SCH_PAGE","uuid":"s1"}|',
                 '{"type":"ATTR","ticket":7,"id":"s0-d"}'
                 '||{"parentId":"s0","key":"Designator","value":"C1"}|',
                 '{"type":"ATTR","ticket":8,"id":"s0-m"}'
                 '||{"parentId":"s0","key":"3D Model Transform",'
                 '"value":"62.992,31.496,0,0,0,0,0,0,0"}|',
                 '{"type":"POLY","ticket":9,"id":"e900"}'
                 '||{"layerId":11,"polyType":"BOARD_OUTLINE",'
                 '"path":["R",0,0,4000,3000]}|']
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("project2.json", '{"title":"混合"}')
            archive.writestr("x.epru", "\n".join(lines) + "\n")
        design = easyeda_import.import_epro2(path, design_id="t")
        by_ref = {p.ref: p for p in design.parts}
        self.assertEqual(by_ref["C1"].physical_source, "derived")
        self.assertAlmostEqual(by_ref["C1"].size_mm["w"], 1.6, places=2)
        self.assertEqual(by_ref["C2"].physical_source, "estimated")
        self.assertEqual(by_ref["C2"].size_mm["w"], 0.0, "无尺寸却给了数值")

    def test_connector_recognised_from_footprint(self) -> None:
        """`U1` 可能是排针：角色靠**封装名**判断，不是只看标号前缀。"""
        from hw_velxio.design import easyeda_import
        path = self.tmp / "conn.epro2"
        _make_epro2(path, components=[("U1", ""), ("RF1", ""), ("U2", "")],
                    nets=[])
        design = easyeda_import.import_epro2(path, design_id="t")
        roles = {p.ref: p.role for p in design.parts}
        # 夹具的 3D Model Title 是 C0603（不是连接器），所以按标号前缀：
        # U* → module。真正的连接器判定见下面两行（靠封装名）。
        self.assertEqual(roles["U1"], "module")
        self.assertEqual(easyeda_import._role_of("U9", "CONN-TH_4P-P1.25"), "connector")
        self.assertEqual(easyeda_import._role_of("RF1", "SMA-TH_5P"), "connector")
        self.assertEqual(easyeda_import._role_of("U1", "LQFP-64"), "module")

    def test_import_any_dispatches_epro2(self) -> None:
        from hw_velxio.design.importers import import_any, supported_formats
        path = self.tmp / "dispatch.epro2"
        _make_epro2(path, components=[("R1", "10k")], nets=["GND"])
        design = import_any(path, design_id="d")
        self.assertEqual(len(design.parts), 1)
        exts = [f["ext"] for f in supported_formats()]
        self.assertIn(".epro2", exts)


class TestPins(unittest.TestCase):
    """引脚校验：从 0 搭的网表能不能被信任。

    在没有引脚表之前，`{"ref":"U1","pin":"NOT_A_PIN"}` 会**静默通过**——
    AI 写出的网表只是声明，不是被验证的电气事实。
    """

    def _design(self, *, kind: str, pins: list[str] | None,
                net_pins: list[tuple[str, str]]) -> DesignModel:
        design = DesignModel(id="p", title="t", board=board_preset("arduino-uno"))
        design.parts = [Part(ref="U1", kind=kind, role="module",
                             size_mm={"w": 10, "h": 10, "d": 2},
                             physical_source="datasheet",
                             pins=list(pins or []))]
        design.nets = [{"net": "X",
                        "pins": [{"ref": r, "pin": p} for r, p in net_pins]}]
        return design

    def test_declared_pin_must_exist(self) -> None:
        from hw_velxio.design import pins
        design = self._design(kind="mcu", pins=["1", "2", "3"],
                              net_pins=[("U1", "99")])
        audit = pins.audit(design)
        self.assertEqual(audit["sources"]["declared"], 1)
        self.assertEqual(len(audit["missing"]), 1)
        self.assertIn("U1.99", audit["missing"][0])
        # validate() 也要报
        self.assertTrue(any("引脚不存在" in p for p in design.validate()))

    def test_declared_pin_case_insensitive(self) -> None:
        from hw_velxio.design import pins
        design = self._design(kind="mcu", pins=["D13"], net_pins=[("U1", "d13")])
        audit = pins.audit(design)
        self.assertEqual(audit["missing"], [], "大小写不同不该判成缺引脚")
        self.assertEqual(audit["pins_checked"], 1)

    def test_convention_pins_used_when_not_declared(self) -> None:
        from hw_velxio.design import pins
        design = self._design(kind="led", pins=None, net_pins=[("U1", "A")])
        audit = pins.audit(design)
        self.assertEqual(audit["sources"]["convention"], 1)
        self.assertEqual(audit["missing"], [])

    def test_unknown_pin_table_is_unverified_not_passed(self) -> None:
        """没有引脚表时必须报"未核对"，**不能**静默算通过。"""
        from hw_velxio.design import pins
        design = self._design(kind="mystery-chip", pins=None,
                              net_pins=[("U1", "WHATEVER")])
        audit = pins.audit(design)
        self.assertEqual(audit["sources"]["unknown"], 1)
        self.assertEqual(audit["pins_unverified"], 1)
        self.assertEqual(audit["pins_checked"], 0)
        line = pins.summary_line(audit)
        self.assertIn("无法核对", line)
        self.assertIn("不是通过", line)

    def test_pin_check_appears_in_not_verified(self) -> None:
        from hw_velxio.design import physical
        design = self._design(kind="mystery-chip", pins=None,
                              net_pins=[("U1", "WHATEVER")])
        result = physical.run_checks(design)
        self.assertTrue(any("引脚校验" in x for x in result["not_verified"]),
                        result["not_verified"])
        self.assertEqual(result["pins"]["unverified"], 1)


class TestScene(unittest.TestCase):
    """design.json → scene.json：工作台的板子要能进 hwforge 的判定/仿真链。"""

    def _design(self) -> DesignModel:
        design = DesignModel(id="s", title="t", board=board_preset("arduino-uno"))
        design.parts = [
            Part(ref="U1", kind="arduino-nano", role="module",
                 size_mm={"w": 40, "h": 16, "d": 8},
                 physical_source="datasheet",
                 placement=Placement(x_mm=4, y_mm=4)),
            Part(ref="R1", kind="resistor", role="component",
                 size_mm={"w": 6, "h": 2, "d": 2},
                 physical_source="datasheet",
                 placement=Placement(x_mm=50, y_mm=4)),
        ]
        design.nets = [{"net": "D13", "pins": [{"ref": "U1", "pin": "D13"},
                                               {"ref": "R1", "pin": "1"}]}]
        return design

    def test_placements_and_dims(self) -> None:
        from hw_velxio.design import scene
        payload = scene.to_scene(self._design(), pitch_mm=0.5)
        space = payload["space"]
        self.assertEqual(len(space["placements"]), 2)
        # 板 72.58x53.34 → 145x107 体素
        self.assertEqual(space["dims"][0], 145)
        self.assertEqual(space["dims"][1], 107)
        first = space["placements"][0]
        self.assertEqual(first["ref"], "U1")
        # x=4mm → 8 体素；w=40mm → 80 体素
        self.assertEqual(first["box"]["x0"], 8)
        self.assertEqual(first["box"]["nx"], 80)

    def test_meta_role_present(self) -> None:
        """`inventory.bom` 只认 `meta.role`，缺了整件会被静默跳过。"""
        from hw_velxio.design import scene
        payload = scene.to_scene(self._design())
        for placement in payload["space"]["placements"]:
            self.assertTrue(placement["meta"]["role"],
                            f"{placement['ref']} 没有 meta.role")

    def test_wires_are_marked_approximate(self) -> None:
        """端点只到元件中心，必须标注，不能让人当真实走线长度。"""
        from hw_velxio.design import scene
        payload = scene.to_scene(self._design())
        wires = payload["wires"]
        self.assertEqual(len(wires["runs"]), 1)
        self.assertTrue(wires["estimated"])
        self.assertIn("元件中心", wires["route_note"])
        self.assertEqual(wires["runs"][0]["approx"], "component_centre")

    def test_rail_missing_is_reported(self) -> None:
        from hw_velxio.design import scene
        payload = scene.to_scene(self._design())
        self.assertTrue(any("rail" in x for x in payload["unverified"]),
                        payload["unverified"])

    def test_empty_design_is_reported_not_silent(self) -> None:
        from hw_velxio.design import scene
        design = DesignModel(id="e", title="t", board=board_preset("arduino-uno"))
        payload = scene.to_scene(design)
        self.assertEqual(payload["space"]["placements"], [])
        self.assertTrue(any("没有任何元件" in x for x in payload["unverified"]))


class TestSynthesize(unittest.TestCase):
    """需求 → 设计：模板组装出的网表必须**引脚都真实存在**。"""

    def test_all_templates_produce_valid_pins(self) -> None:
        from hw_velxio.design import pins, synthesize
        for template in sorted(synthesize.TEMPLATES):
            with self.subTest(template=template):
                design = synthesize.synthesize({"template": template},
                                               design_id=f"t_{template}")
                self.assertGreaterEqual(len(design.parts), 2)
                self.assertGreaterEqual(len(design.nets), 2)
                audit = pins.audit(design)
                self.assertEqual(audit["missing"], [],
                                 f"{template} 写出了不存在的引脚：{audit['missing']}")
                self.assertEqual(audit["sources"]["unknown"], 0,
                                 f"{template} 用了没有引脚表的元件")

    def test_blink_binds_real_d13(self) -> None:
        from hw_velxio.design import synthesize
        design = synthesize.synthesize({"template": "blink", "pin": "D13"},
                                       design_id="b")
        names = {entry["net"] for entry in design.nets}
        self.assertIn("D13", names)
        self.assertIn("GND", names)

    def test_missing_pin_is_rejected_with_options(self) -> None:
        from hw_velxio.design import synthesize
        with self.assertRaises(synthesize.SynthError) as caught:
            synthesize.synthesize({"template": "blink", "pin": "D99"},
                                  design_id="bad")
        self.assertIn("没有引脚", str(caught.exception))

    def test_unknown_template_rejected(self) -> None:
        from hw_velxio.design import synthesize
        with self.assertRaises(synthesize.SynthError):
            synthesize.synthesize({"template": "quantum-radio"},
                                  design_id="bad")

    def test_layout_fits_inside_board(self) -> None:
        """模板默认要落在板内，而不是每张都把板子撑宽。"""
        from hw_velxio.design import synthesize
        design = synthesize.synthesize({"template": "blink"}, design_id="fit")
        width = design.board.size_mm["w"]
        for part in design.parts:
            x0, _y0, x1, _y1 = part.footprint_mm()
            self.assertLessEqual(x1, width + 0.01,
                                 f"{part.ref} 超出板宽 {width}mm")

    def test_estimated_catalog_part_marked(self) -> None:
        """catalog 里标 estimated 的件必须保留这个标签，不能升格成 datasheet。"""
        from hw_velxio.design import synthesize
        design = synthesize.synthesize({"template": "i2c-sensor",
                                        "sensor": "bme280"}, design_id="e")
        sensor = next(p for p in design.parts if p.ref == "U2")
        self.assertEqual(sensor.physical_source, "estimated")


def _claims_design() -> DesignModel:
    """最小可判设计：MCU + 上拉 + LED，三个元件身份都清楚。"""
    from hw_velxio.design.model import Part, Placement, Wire

    design = DesignModel(id="_claims", title="claims",
                         board=board_preset("arduino-uno"))
    design.parts = [
        Part(ref="U1", kind="arduino-uno", role="mcu", label="Uno",
             size_mm={"w": 68.6, "h": 53.4, "d": 1.6},
             physical_source="datasheet",
             placement=Placement(x_mm=0, y_mm=0, rotation_z=0, board_id=""),
             electrical={"max_v": 5.5, "v_in": 5.0},
             pins=["D2", "5V", "GND"]),
        Part(ref="R1", kind="resistor", role="pullup", label="10k",
             size_mm={"w": 2.0, "h": 1.25, "d": 0.5},
             physical_source="datasheet",
             placement=Placement(x_mm=20, y_mm=20, rotation_z=0, board_id=""),
             electrical={"max_v": 50}, pins=["1", "2"]),
        Part(ref="D1", kind="led", role="indicator", label="LED",
             size_mm={"w": 3.2, "h": 1.6, "d": 1.0},
             physical_source="datasheet",
             placement=Placement(x_mm=30, y_mm=20, rotation_z=0, board_id=""),
             electrical={"max_v": 5.0}, pins=["A", "K"]),
    ]
    design.nets = [
        {"net": "5V", "pins": [{"ref": "U1", "pin": "5V"},
                               {"ref": "R1", "pin": "1"}]},
        {"net": "D2", "pins": [{"ref": "U1", "pin": "D2"},
                               {"ref": "R1", "pin": "2"},
                               {"ref": "D1", "pin": "A"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": "GND"},
                                {"ref": "D1", "pin": "K"}]},
    ]
    return design


class TestClaims(unittest.TestCase):
    """断言层：**删除即失败**。这是整个审查机的支点。"""

    def test_requires_missing_is_fail_not_unknown(self) -> None:
        """依赖缺失必须是 **fail**，不能是 unknown。

        这条是全部机制的要害：如果删掉元件后断言变成"没法判断"，
        那"删除即失败"就不成立，系统只是变安静。
        """
        from hw_velxio.design import claims

        claim = claims.Claim(
            id="claim.X.powered", type="capability",
            requires=["part.NOPE"], check={"kind": "powered"})
        outcome = claims.evaluate(claim, _claims_design())
        self.assertEqual(outcome.status, claims.FAIL)
        self.assertIn("part.NOPE", outcome.missing)
        self.assertIn("不存在", outcome.basis)

    def test_removal_reddens_exactly_its_own_claims(self) -> None:
        """删 R1 → 只有 R1 的断言变红，别人的不受影响。"""
        from hw_velxio.design import claims

        impact = claims.removal_impact(_claims_design(), "R1")
        self.assertIsNotNone(impact)
        self.assertGreater(impact["count"], 0, "删元件必须至少红一条")
        self.assertTrue(impact["reddened"])
        for cid in impact["reddened"]:
            self.assertIn("R1", cid, f"{cid} 不该因 R1 缺失而红")
        self.assertNotIn("claim.U1.powered", impact["reddened"])
        self.assertNotIn("claim.D1.connected", impact["reddened"])

    def test_removal_does_not_mutate_the_original(self) -> None:
        """`removal_impact` 在内存副本上做，原设计一个字节都不改。"""
        from hw_velxio.design import claims

        design = _claims_design()
        before = len(design.parts)
        claims.removal_impact(design, "R1")
        self.assertEqual(len(design.parts), before)
        self.assertIsNotNone(design.part("R1"))

    def test_removing_unknown_ref_returns_none(self) -> None:
        from hw_velxio.design import claims
        self.assertIsNone(claims.removal_impact(_claims_design(), "NOPE"))

    def test_missing_data_is_unknown_never_pass(self) -> None:
        """元件没申报 max_v → rated 断言是 unknown，不是 pass。

        这正是 `budgets.py:149` 那条纪律：缺失的值不许渲染成通过。
        """
        from hw_velxio.design import claims

        claim = claims.Claim(
            id="claim.R1.rated_voltage", type="numeric",
            requires=["part.R1"],
            check={"kind": "numeric", "subject": "part.R1",
                   "param": "min_v", "op": ">", "value": 0})
        outcome = claims.evaluate(claim, _claims_design())
        self.assertEqual(outcome.status, claims.UNKNOWN)
        self.assertIn("缺数据", outcome.basis)

    def test_numeric_failure_is_fail(self) -> None:
        from hw_velxio.design import claims

        claim = claims.Claim(
            id="claim.U1.rated", type="numeric", requires=["part.U1"],
            check={"kind": "numeric", "subject": "part.U1",
                   "param": "max_v", "op": ">", "value": 99.0})
        self.assertEqual(claims.evaluate(claim, _claims_design()).status,
                         claims.FAIL)

    def test_isolated_part_fails_attached(self) -> None:
        """存在但不接任何网络的元件必须红——物理检查不会点名它。"""
        from hw_velxio.design import claims
        from hw_velxio.design.model import Part, Placement

        design = _claims_design()
        design.parts.append(Part(
            ref="FL1", kind="led", role="indicator",
            size_mm={"w": 1.0, "h": 1.0, "d": 1.0},
            physical_source="estimated",
            placement=Placement(x_mm=40, y_mm=40, rotation_z=0, board_id="")))

        claim = claims.Claim(
            id="claim.FL1.connected", type="presence",
            requires=["part.FL1"],
            check={"kind": "attached", "subject": "part.FL1"})
        outcome = claims.evaluate(claim, design)
        self.assertEqual(outcome.status, claims.FAIL)
        self.assertIn("孤立元件", outcome.basis)

    def test_unknown_ref_prefix_is_false_not_lenient(self) -> None:
        """写错前缀（`par.R1`）必须算不存在，否则拼错会让断言静默失效。"""
        from hw_velxio.design import claims

        design = _claims_design()
        self.assertTrue(claims.exists(design, "part.R1"))
        self.assertFalse(claims.exists(design, "par.R1"))
        self.assertFalse(claims.exists(design, "part."))
        self.assertFalse(claims.exists(design, "part.NOPE"))
        self.assertTrue(claims.exists(design, "net.D2"))

    def test_default_role_is_not_identity(self) -> None:
        """导入器的兜底角色不算语义身份 —— 否则会拿默认值冒充知识。

        实测背景：`示例-单板` 141 个元件 role 全是 component/module/connector，
        3 个按钮模块因此被误判成"没接电源"。
        """
        from hw_velxio.design import claims
        from hw_velxio.design.model import Part

        blob = Part(ref="U9", kind="015c378003ee92b1", role="module",
                    size_mm={"w": 5.0, "h": 5.0, "d": 1.0},
                    physical_source="estimated")
        self.assertFalse(claims.identifiable(blob)[0])

        known = Part(ref="U8", kind="arduino-nano", role="mcu",
                     size_mm={"w": 18.0, "h": 43.0, "d": 1.6},
                     physical_source="datasheet")
        self.assertTrue(claims.identifiable(known)[0])

    def test_coverage_exposes_green_but_empty(self) -> None:
        """全绿但零语义断言时，覆盖率必须把这件事说出来。"""
        from hw_velxio.design import claims
        from hw_velxio.design.model import Part, Placement

        design = DesignModel(id="_cov", title="cov",
                             board=board_preset("arduino-uno"))
        design.parts = [Part(
            ref="U1", kind="6949c283c434a76e", role="component",
            size_mm={"w": 5.0, "h": 5.0, "d": 1.0},
            physical_source="estimated",
            placement=Placement(x_mm=10, y_mm=10, rotation_z=0, board_id=""))]
        design.nets = [{"net": "GND", "pins": [{"ref": "U1", "pin": "1"}]},
                       {"net": "SIG", "pins": [{"ref": "U1", "pin": "2"}]}]

        report = claims.check(design)
        cov = report.coverage()
        self.assertEqual(cov["semantic"], 0, "身份不可判就不该有语义断言")
        self.assertGreater(cov["structural"], 0)
        self.assertEqual(cov["semantic_ratio"], 0.0)
        self.assertIn("不代表功能正确", cov["note"])

    def test_claims_are_deterministic(self) -> None:
        """同输入必须同输出 —— 断言顺序变了 diff 就没法比。"""
        from hw_velxio.design import claims

        first = [c.to_dict() for c in claims.derive(_claims_design())]
        second = [c.to_dict() for c in claims.derive(_claims_design())]
        self.assertEqual(first, second)

    def test_derive_binds_every_claim_to_its_part(self) -> None:
        """每条断言的第一条 requires 必须是它自己 —— 机制的结构保证。"""
        from hw_velxio.design import claims

        for claim in claims.derive(_claims_design()):
            self.assertTrue(claim.requires, f"{claim.id} 没有 requires")
            self.assertTrue(claim.requires[0].startswith("part."),
                            f"{claim.id} 首条 requires 不是元件引用")
            self.assertIn(claim.scope.get("part"),
                          claim.requires[0],
                          f"{claim.id} 的 requires 与 scope 不自洽")

    def test_derive_consumes_justification(self) -> None:
        """归因判出的作用必须变成断言 —— 否则"删除即失败"是假的。

        ## 这条测试防的具体退化

        第一版 `derive()` 只给每个元件一条 `connected`。那是真的
        "删掉 R1，`claim.R1.connected` 变红"，但那条断言说的是
        "R1 还在网表里"，不是"R1 是 D2 的上拉"。后果是：
        **删掉上拉电阻，系统只是变安静，没有变红**——没有任何一条
        断言说"D2 现在悬空了"。

        `justify` 从拓扑算出"R1 是 5V 到 D2 的上拉"，这是真知识。
        这条测试要求它被 `derive()` 消费，出现在 `requires` 里
        （`part.R1` + `net.D2` 同时出现才算真消费了）。
        """
        from hw_velxio.design import claims, justify

        design = _claims_design()
        report = justify.run(design)
        kinds = {}
        for row in report["parts"]:
            j = row["justification"]
            if j["confidence"] != "unknown":
                kinds[row["ref"]] = row["group"]["kind"]
        self.assertTrue(kinds, "夹具里没有任何元件被判出作用")

        derived = claims.derive(design)
        ids = {c.id for c in derived}

        # 判成上拉的元件，必须有一条不是 `connected` 的断言
        for ref, kind in kinds.items():
            extra = [i for i in ids
                     if i.startswith(f"claim.{ref}.")
                     and not i.endswith(".connected")
                     and not i.endswith(".rated_voltage")]
            self.assertTrue(
                extra,
                f"{ref} 被判为 {kind}，但 derive() 只给了它 connected —— "
                f"归因结论没有被断言消费，「删除即失败」对它是假的")

        # 并且那条断言要同时绑定元件与信号网
        #
        # 只查**归因驱动**的那批（上一步算出的 `extra` 里的），
        # 不查全部非 connected 断言：`claim.U1.powered` 是原来就有的
        # 能力断言，它只绑 `part.U1`、不绑任何网——那是正确的，
        # 因为"U1 得到供电"这件事跟某一条具体网络无关。
        # 把规则套到它身上是测试写过头了。
        checked = 0
        for claim in derived:
            if claim.id.endswith((".connected", ".rated_voltage",
                                  ".powered")):
                continue
            checked += 1
            self.assertTrue(
                any(r.startswith("part.") for r in claim.requires)
                and any(r.startswith("net.") for r in claim.requires),
                f"{claim.id} 没有同时绑定元件与网络：{claim.requires}")
        self.assertGreater(checked, 0, "没有检查到任何归因驱动的断言")

    def test_remove_reddens_a_functional_claim(self) -> None:
        """删掉元件后，变红的不该只有 `connected`。

        这是上面那条的**行为级**版本：不检查推导结果，而是直接看
        `removal_impact` 报了什么。只断言 `connected` 的话，
        `hw_assert` 的输出对人几乎没有信息量。
        """
        from hw_velxio.design import claims

        design = _claims_design()
        for ref in ("R1", "D1"):
            impact = claims.removal_impact(design, ref)
            self.assertIsNotNone(impact, f"{ref} 不在设计里")
            reddened = impact["reddened"]
            self.assertTrue(reddened, f"删掉 {ref} 没有任何断言变红")
            functional = [i for i in reddened if not i.endswith(".connected")]
            self.assertTrue(
                functional,
                f"删掉 {ref} 只让 connected 变红（{reddened}）—— "
                f"断言机制退化成了「还在不在」检查")


class TestUnits(unittest.TestCase):
    """P5 单元层：分组必须来自 board_id，角色必须给证据。"""

    @staticmethod
    def _multi() -> DesignModel:
        from hw_velxio.design.model import Board, Part, Placement

        boards = [
            Board(kind="custom", name="主板", size_mm={"w": 60, "h": 60},
                  id="PCB#main"),
            Board(kind="custom", name="子板", size_mm={"w": 40, "h": 40},
                  id="PCB#sub"),
        ]
        design = DesignModel(id="_units", title="units",
                             board=boards[0], boards=boards)
        design.parts = [
            Part(ref="U1", kind="mcu", role="module",
                 size_mm={"w": 5, "h": 5, "d": 1},
                 physical_source="datasheet",
                 placement=Placement(x_mm=5, y_mm=5, board_id="PCB#main"),
                 pins=["1", "2", "3"]),
            Part(ref="U2", kind="sensor", role="module",
                 size_mm={"w": 5, "h": 5, "d": 1},
                 physical_source="datasheet",
                 placement=Placement(x_mm=5, y_mm=5, board_id="PCB#sub"),
                 pins=["1", "2"]),
        ]
        return design

    def test_derive_groups_by_board_id(self) -> None:
        from hw_velxio.design import units

        design = self._multi()
        found = units.derive(design)
        self.assertEqual(len(found), 2)
        parts = sorted(p for u in found for p in u.parts)
        self.assertEqual(parts, ["U1", "U2"])

    def test_role_needs_evidence(self) -> None:
        from hw_velxio.design import units

        design = self._multi()
        design.nets = [
            {"net": "OSC_IN", "pins": [{"ref": "U1", "pin": "1"}]},
            {"net": "$1N1", "pins": [{"ref": "U2", "pin": "1"}]},
        ]
        found = {u.board_id: u for u in units.derive(design)}
        main = found["PCB#main"]
        self.assertEqual(main.role, "mcu")
        self.assertTrue(main.evidence, "判出角色必须给证据")
        # 子板只有自动网名 → 必须 unknown
        sub = found["PCB#sub"]
        self.assertEqual(sub.confidence, "unknown")
        self.assertEqual(sub.named_nets, [])

    def test_no_netlist_is_not_pass(self) -> None:
        from hw_velxio.design import units

        design = self._multi()
        report = units.report(design)
        self.assertFalse(report["ok"])
        self.assertEqual(report["known"], 0)
        self.assertEqual(report["ratio_judgeable"], 0.0)
        self.assertEqual(report["missing_data"]["no_netlist_count"], 2)

    def test_apply_writes_units_list(self) -> None:
        from hw_velxio.design import units

        design = self._multi()
        units.apply(design)
        self.assertEqual(len(design.units_list), 2)
        self.assertTrue(all("role" in row for row in design.units_list))


class TestLinks(unittest.TestCase):
    """P5 链路：inferred 不参与硬检查，空表不是通过。"""

    @staticmethod
    def _design() -> DesignModel:
        from hw_velxio.design.model import Part, Placement

        design = DesignModel(id="_links", title="links",
                             board=Board(kind="custom",
                                         size_mm={"w": 60, "h": 60}))
        design.parts = [
            Part(ref="U1", kind="mcu", role="module",
                 size_mm={"w": 5, "h": 5, "d": 1},
                 physical_source="datasheet",
                 placement=Placement(x_mm=5, y_mm=5),
                 pins=["1", "2", "3"], electrical={"v_in": 3.3}),
            Part(ref="U2", kind="module", role="module",
                 size_mm={"w": 5, "h": 5, "d": 1},
                 physical_source="datasheet",
                 placement=Placement(x_mm=20, y_mm=5),
                 pins=["1", "2", "3"], electrical={"v_in": 3.3}),
        ]
        return design

    def test_protocol_mismatch(self) -> None:
        from hw_velxio.design import links

        design = self._design()
        table = [{
            "id": "l1",
            "a": {"part": "U1", "peripheral": "USART1"},
            "b": {"part": "U2", "peripheral": "SPI1"},
            "protocol": "uart", "params": {"level_v": 3.3},
            "source": "declared",
        }]
        report = links.validate(design, table)
        self.assertIn("link.protocol_match",
                      [f["check"] for f in report["findings"]])
        self.assertFalse(report["ok"])

    def test_inferred_never_fails(self) -> None:
        from hw_velxio.design import links

        design = self._design()
        table = [{
            "id": "l1",
            "a": {"peripheral": "USART1"},
            "b": {"peripheral": "SPI1"},
            "protocol": "uart", "source": "inferred",
        }]
        report = links.validate(design, table)
        self.assertTrue(report["ok"], "inferred 不该判 fail")
        self.assertEqual(report["checked"]["declared_links"], 0)
        self.assertIn("l1", report["skipped_inferred"])

    def test_empty_table_is_not_ready(self) -> None:
        from hw_velxio.design import links

        report = links.validate(self._design(), [])
        self.assertEqual(report["status"], "not_ready")
        self.assertIn("不是通过", report["reason"])

    def test_baud_uses_fractional_prescaler(self) -> None:
        from hw_velxio.design import links

        # 8MHz 跑 115200：整数分频算不通（125000，偏差 8.5%），
        # 但 STM32 的 BRR 有 4 位小数分频，实际 115942，偏差 0.6% → 通。
        ok, actual = links.baud_reachable(8_000_000, 115200)
        self.assertTrue(ok, f"8MHz/115200 应当可达，实际 {actual}")
        self.assertLessEqual(abs(actual - 115200) / 115200, 0.02)

        ok, _ = links.baud_reachable(8_000_000, 3_000_000)
        self.assertFalse(ok, "8MHz 跑 3M 波特应当不可达")
        ok, _ = links.baud_reachable(None, 115200)
        self.assertIsNone(ok, "时钟未知必须是 None，不是 True")

    def test_orphan_declared_peripheral(self) -> None:
        from hw_velxio.design import links

        design = self._design()
        design.meta = {"interfaces": {"USART2": "unit:main"}}
        report = links.validate(design, [])
        self.assertIn("link.orphan",
                      [f["check"] for f in report["findings"]])
        self.assertEqual(
            report["orphans"][0]["source"] if report["orphans"] else "",
            "declared")

    def test_family_tokens(self) -> None:
        from hw_velxio.design import links

        self.assertEqual(links.family_of("USART2"), "uart")
        self.assertEqual(links.family_of("SPI1"), "spi")
        self.assertEqual(links.family_of("I2C1"), "i2c")
        # `RF_RXEN` 是射频使能脚，不是 UART —— 词元匹配挡住它
        self.assertIsNone(links.family_of("RF_RXEN"))


class TestCapability(unittest.TestCase):
    """P6 能力表：来源不可信就不选；fills 挡住语义荒谬。"""

    def test_only_trusted_sources(self) -> None:
        from hw_velxio.design import capability

        saved = dict(capability.CAPABILITY)
        try:
            capability.CAPABILITY["guessy"] = {
                "fills": {"mcu"}, "provides": {"uart", "spi", "gpio"},
                "footprint_mm2": 1.0, "source": "guess",
            }
            found = capability.candidates({"uart", "spi"}, role="mcu")
            self.assertNotIn("guessy", found)
        finally:
            capability.CAPABILITY.clear()
            capability.CAPABILITY.update(saved)

    def test_fills_blocks_wrong_category(self) -> None:
        from hw_velxio.design import capability

        radio = capability.candidates({"spi"}, role="radio")
        self.assertNotIn("bmp280", radio, "气压计不该当电台")
        self.assertIn("esp32s3", radio)

    def test_explain_miss_distinguishes(self) -> None:
        from hw_velxio.design import capability

        miss = capability.explain_miss({"eth", "wifi"})
        self.assertIn(miss["reason"], ("filtered_out", "table_empty"))
        self.assertTrue(miss["rejected"])
        self.assertTrue(miss["hint"])


class TestGenerate(unittest.TestCase):
    """P6 生成：确定性、有上限、不凑数、不选荒谬组合。"""

    @staticmethod
    def _req(**kw):
        from hw_velxio.design import generate

        base = {
            "id": "t", "roles": ["mcu", "radio", "sensor", "power_in"],
            "interfaces": [{"a": "mcu", "b": "radio", "protocol": "spi",
                            "level_v": 3.3}],
        }
        base.update(kw)
        return generate.Requirement.from_dict(base)

    def test_deterministic(self) -> None:
        import json

        from hw_velxio.design import generate

        first = json.dumps(generate.generate(self._req(), max_candidates=10),
                           ensure_ascii=False, sort_keys=True)
        second = json.dumps(generate.generate(self._req(), max_candidates=10),
                            ensure_ascii=False, sort_keys=True)
        self.assertEqual(first, second, "同一需求两次结果必须一致")

    def test_respects_budget(self) -> None:
        from hw_velxio.design import generate

        report = generate.generate(self._req(), max_candidates=4)
        self.assertLessEqual(len(report["candidates"]), 4)

    def test_candidates_are_distinct(self) -> None:
        from hw_velxio.design import generate

        report = generate.generate(self._req(), max_candidates=12)
        signatures = []
        for cand in report["candidates"]:
            part_key = "|".join(
                f"{','.join(p.get('units') or [p['unit']])}:{p['model']}"
                for p in cand["parts"])
            signatures.append(part_key)
        self.assertEqual(len(signatures), len(set(signatures)),
                         "候选签名必须互不相同，不能靠改名凑数")

    def test_shared_part_collapsed(self) -> None:
        from hw_velxio.design import generate

        report = generate.generate(self._req(), max_candidates=20)
        shared = [c for c in report["candidates"]
                  if any(len(p.get("units") or []) > 1 for p in c["parts"])]
        self.assertTrue(shared, "esp32s3 兼 mcu+radio 的候选应当存在")
        for cand in shared:
            models = [p["model"] for p in cand["parts"]]
            self.assertEqual(len(models), len(set(models)),
                             "同一颗料不能列两行（面积会重复计数）")

    def test_no_self_loop_nets(self) -> None:
        from hw_velxio.design import generate

        report = generate.generate(self._req(), max_candidates=20)
        req = self._req()
        for cand in report["candidates"]:
            obj = generate.Candidate(
                id=cand["id"], parts=cand["parts"], links=cand["links"],
                units=cand["units"])
            design = generate.to_design(obj, req)
            for net in design.nets:
                refs = [p["ref"] for p in net["pins"]]
                self.assertNotEqual(refs[0], refs[1],
                                    f"{net['net']} 是自环网（同一颗料连自己）")

    def test_no_candidates_is_not_ready(self) -> None:
        from hw_velxio.design import generate

        report = generate.generate(self._req(roles=[]), max_candidates=4)
        self.assertEqual(report["status"], "not_ready")
        self.assertEqual(report["candidates"], [])


class TestVectorTable(unittest.TestCase):
    """P7：从 .bin 头推加载地址。猜地址比不给地址更危险。"""

    @staticmethod
    def _bin(tmp, sp, reset):
        import struct
        from pathlib import Path
        path = Path(tmp) / "fw.bin"
        path.write_bytes(struct.pack("<II", sp, reset) + b"\x00" * 64)
        return path

    def test_valid_vector_table(self) -> None:
        import tempfile
        from hw_velxio.design import renode

        with tempfile.TemporaryDirectory() as tmp:
            info = renode.vector_table(self._bin(tmp, 0x20005000, 0x080033F5))
            self.assertTrue(info["ok"], info.get("reason"))
            self.assertEqual(info["load_address"], "0x08000000")
            self.assertEqual(info["ram_hint_kb"], 20)

    def test_rejects_sp_outside_sram(self) -> None:
        import tempfile
        from hw_velxio.design import renode

        with tempfile.TemporaryDirectory() as tmp:
            info = renode.vector_table(self._bin(tmp, 0x12345000, 0x08000101))
            self.assertFalse(info["ok"])
            self.assertIn("SP", info["reason"])

    def test_rejects_non_thumb_reset(self) -> None:
        import tempfile
        from hw_velxio.design import renode

        with tempfile.TemporaryDirectory() as tmp:
            info = renode.vector_table(self._bin(tmp, 0x20005000, 0x08000100))
            self.assertFalse(info["ok"])
            self.assertIn("Thumb", info["reason"])

    def test_rejects_short_file(self) -> None:
        import tempfile
        from pathlib import Path
        from hw_velxio.design import renode

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.bin"
            path.write_bytes(b"\x00\x01")
            info = renode.vector_table(path)
            self.assertFalse(info["ok"])

    def test_platform_mapping(self) -> None:
        from hw_velxio.design import renode

        self.assertEqual(renode._platform_for("STM32F103C8T6"), "stm32f103")
        self.assertEqual(renode._platform_for("STM32H743IIT6"), "stm32h743")
        self.assertIsNone(renode._platform_for("ESP32"),
                          "判不出必须返回 None，不能猜")


class TestRenodeScript(unittest.TestCase):
    """P7：.resc 结构与纪律。"""

    @staticmethod
    def _two_board(link_source: str = "declared") -> DesignModel:
        from hw_velxio.design.model import Board, Part, Placement

        b0 = Board(kind="custom", name="A", size_mm={"w": 40, "h": 40}, id="A")
        b1 = Board(kind="custom", name="B", size_mm={"w": 40, "h": 40}, id="B")
        design = DesignModel(id="t", title="t", board=b0, boards=[b0, b1])
        design.parts = [
            Part(ref="U1", kind="STM32F103C8T6", label="a", role="mcu",
                 size_mm={"w": 7, "h": 7, "d": 1},
                 physical_source="datasheet",
                 placement=Placement(x_mm=5, y_mm=5, board_id="A"),
                 pins=["1"]),
            Part(ref="U2", kind="STM32F103C8T6", label="b", role="mcu",
                 size_mm={"w": 7, "h": 7, "d": 1},
                 physical_source="datasheet",
                 placement=Placement(x_mm=5, y_mm=5, board_id="B"),
                 pins=["1"]),
        ]
        design.links_list = [{
            "id": "link:SPI1:A->B",
            "a": {"unit": "A", "peripheral": "SPI1"},
            "b": {"unit": "B", "peripheral": "SPI1"},
            "protocol": "spi", "source": link_source,
        }]
        return design

    def test_declared_link_becomes_connector(self) -> None:
        from hw_velxio.design import renode

        report = renode.build_resc(self._two_board("declared"))
        self.assertEqual(len(report["connectors"]), 1)
        self.assertTrue(report["connectors"][0].startswith("connector Connect "))
        self.assertIn("m0.sysbus.spi1", report["connectors"][0])

    def test_inferred_link_not_connected(self) -> None:
        from hw_velxio.design import renode

        report = renode.build_resc(self._two_board("inferred"))
        self.assertEqual(report["connectors"], [],
                         "推断的链路不能接线：接错了比不接更糟")
        self.assertTrue(any("inferred" in w for w in report["warnings"]))

    def test_connector_before_start(self) -> None:
        from hw_velxio.design import renode

        script = renode.build_resc(self._two_board())["script"]
        self.assertGreater(script.find("connector Connect"),
                           -1, "没有 connector")
        self.assertLess(script.find("connector Connect"),
                        script.find("\nstart"),
                        "connector 必须在 start 之前")

    def test_runnable_here_is_honest(self) -> None:
        from hw_velxio.design import renode

        report = renode.report(self._two_board())
        self.assertIn("runnable_here", report)
        self.assertIs(report["runnable_here"], False,
                      "本机沙箱拦截，必须如实报 False")
        self.assertTrue(any("DSH 外" in n for n in report["notes"]))


class TestIocLinks(unittest.TestCase):
    """P8：.ioc 解析与配对纪律。"""

    IOC = """#MicroXplorer Configuration settings - do not modify
Mcu.UserName=STM32F401RCTx
PA2.Mode=Asynchronous
PA2.Signal=USART2_TX
PA3.Mode=Asynchronous
PA3.Signal=USART2_RX
PB6.Mode=I2C
PB6.Signal=I2C1_SCL
PB7.Mode=I2C
PB7.Signal=I2C1_SDA
PA15.GPIO_Label=VL53-PWR-EN
PA15.Signal=GPIO_Output
"""

    def _write(self, tmp, name, body=None):
        from pathlib import Path
        path = Path(tmp) / f"{name}.ioc"
        path.write_text(body or self.IOC, encoding="utf-8")
        return path

    def test_parse_endpoints(self) -> None:
        import tempfile
        from hw_velxio.design import ioc_links

        with tempfile.TemporaryDirectory() as tmp:
            row = ioc_links.parse_ioc(self._write(tmp, "A"))
            self.assertEqual(row["mcu"], "STM32F401RCTx")
            roles = {(ep.peripheral, ep.role) for ep in row["endpoints"]}
            self.assertIn(("USART2", "tx"), roles)
            self.assertIn(("USART2", "rx"), roles)
            self.assertIn(("I2C1", "scl"), roles)
            # 纯 GPIO 不算链路端点
            self.assertNotIn("GPIO_Output",
                             {ep.peripheral for ep in row["endpoints"]})

    def test_tx_pairs_with_rx(self) -> None:
        import tempfile
        from hw_velxio.design import ioc_links

        # B 板把 TX/RX 交换（正常接法）
        swapped = self.IOC.replace("PA2.Signal=USART2_TX", "PA2.Signal=USART2_RX") \
                           .replace("PA3.Signal=USART2_RX", "PA3.Signal=USART2_TX")
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "A")
            self._write(tmp, "B", swapped)
            report = ioc_links.extract(tmp)
            links = report["draft"]["links"]
            uart = [l for l in links if l["protocol"] == "uart"]
            self.assertTrue(uart, f"没配出 UART 链路：{links}")
            for link in uart:
                roles = {(link["a"]["role"]), (link["b"]["role"])}
                self.assertEqual(roles, {"tx", "rx"},
                                 "UART 必须 TX↔RX，不能 TX↔TX")

    def test_inferred_never_lands_by_default(self) -> None:
        import tempfile
        from hw_velxio.design import ioc_links
        from hw_velxio.design.model import Board

        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "A")
            self._write(tmp, "B", self.IOC.replace(
                "PA2.Signal=USART2_TX", "PA2.Signal=USART2_RX"))
            report = ioc_links.extract(tmp)
            draft = ioc_links.Draft(links=report["draft"]["links"])
            design = DesignModel(id="t", title="t",
                                 board=Board(kind="custom",
                                             size_mm={"w": 10, "h": 10}))
            added = ioc_links.apply_draft(design, draft, only_declared=True)
            self.assertEqual(added, 0, "inferred 默认不能落地")
            self.assertEqual(design.links_list, [])

    def test_empty_dir_is_not_ready(self) -> None:
        import tempfile
        from hw_velxio.design import ioc_links

        with tempfile.TemporaryDirectory() as tmp:
            report = ioc_links.extract(tmp)
            self.assertEqual(report["status"], "not_ready")
            self.assertIn("没有", report["reason"])

    def test_all_links_are_inferred(self) -> None:
        import tempfile
        from hw_velxio.design import ioc_links

        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "A")
            self._write(tmp, "B", self.IOC.replace(
                "PA2.Signal=USART2_TX", "PA2.Signal=USART2_RX"))
            report = ioc_links.extract(tmp)
            for link in report["draft"]["links"]:
                self.assertEqual(link["source"], "inferred")
                self.assertTrue(link.get("evidence"), "每条都要能追溯")


class TestPutAutoLock(unittest.TestCase):
    """`put` 必须和 `new`/`import` 一样自动锁板卡型号。"""

    def test_put_locks_mcu_family(self) -> None:
        from hw_velxio import design_cli
        from hw_velxio.design import discipline

        design = DesignModel(id="_test_putlock", title="t",
                             board=board_preset("arduino-nano"))
        payload = design.to_dict()
        import io as _io
        import json as _json
        stdin = _io.StringIO(_json.dumps(payload))
        # 直接调 cmd_put 的路径：它从 stdin 读
        import sys as _sys
        original = _sys.stdin
        _sys.stdin = stdin
        try:
            result = design_cli.cmd_put(["_test_putlock", "-"])
        finally:
            _sys.stdin = original
        self.assertEqual(result["status"], "ok", result.get("findings"))
        self.assertIn("mcu_family", result.get("auto_locked") or [],
                      "put 没有自动锁 mcu_family，导出闸门会一直拦着")
        store = DesignStore("_test_putlock")
        ledger = discipline.ledger_from_json(store.read("deferred.json"))
        self.assertIn("mcu_family", ledger.locked_names())


if __name__ == "__main__":
    unittest.main(verbosity=2)
