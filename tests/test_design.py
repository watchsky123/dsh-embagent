"""``hw_velxio.design`` 的自测（标准库 unittest，无第三方依赖）。

从仓库根运行：

    python -m unittest hw_velxio.tests.test_design -v

覆盖：模型校验/旋转、mm↔px 往返、KiCad/Wokwi 导入、物理检查、预算、
交付闸门、轨迹哈希链、制造包。测试**不写** ``project/`` 以外的目录，
每个用例结束清理自己的临时工程。
"""

from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
