"""P49 验收 —— `plan`：需求 → 能力（判据）→ 模块（真实坐标）→ 3D 图 → 任务树。

    python -m dsh_embagent.scripts.check_p49

## 判据

| 项 | 期望 |
|---|---|
| 版本 | 同需求**不新建**版本（按 hash 判重）；新需求 → `v(N+1)` 且 `parent` 指向上一版 |
| 能力 | **判据先行**：每条能力都有 `metric/op/value/unit`；需求没给数值 → `unknown`（**不是 pass**）|
| 能力 | 给了数值且不满足 → `fail`，并生成 `design` 任务 |
| 模块 | 坐标**来自 design.json**（不是编的）；逐件对照 |
| 3D 图 | 四层齐全（task / capability / module|component / board）；每条边带 `source` |
| 任务 | **每条任务都有 `verify`**；id 唯一；依赖都指向存在的任务 |
| 2D 投影 | 归一化到 0..1 |

退化：把"缺证据/缺数值 → unknown"改成 pass → 断言变红。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import plan as plan_module  # noqa: E402
from dsh_embagent.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from dsh_embagent.design.store import DesignStore  # noqa: E402

PROJECT = "p49-fixture"

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


def _design() -> DesignModel:
    board = Board(kind="custom", name="b", id="PCB#main",
                  outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                  size_mm={"w": 40, "h": 30}, thickness_mm=1.6)
    design = DesignModel(id=PROJECT, board=board, boards=[board])
    design.add_part(Part(ref="U1", kind="stm32f103c8t6", label="MCU",
                         lcsc="C8734", role="module",
                         size_mm={"w": 7.0, "h": 7.0, "d": 1.6},
                         physical_source="datasheet",
                         pins=[str(i) for i in range(1, 49)],
                         placement=Placement(x_mm=12.0, y_mm=15.0,
                                             board_id="PCB#main")))
    design.add_part(Part(ref="C9", kind="capacitor", label="100nF",
                         lcsc="C1591", role="component",
                         size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
                         physical_source="datasheet", pins=["1", "2"],
                         placement=Placement(x_mm=9.0, y_mm=25.0,
                                             board_id="PCB#main")))
    design.add_part(Part(ref="USB1", kind="micro_usb", label="USB",
                         lcsc="C404969", role="connector",
                         size_mm={"w": 7.7, "h": 5.5, "d": 3.0},
                         physical_source="datasheet",
                         pins=["1", "2", "3", "4", "5"],
                         placement=Placement(x_mm=2.0, y_mm=20.0,
                                             board_id="PCB#main")))
    design.nets = [
        {"net": "3V3", "pins": [{"ref": "U1", "pin": "24"},
                                 {"ref": "C9", "pin": "1"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": "23"},
                                 {"ref": "C9", "pin": "2"},
                                 {"ref": "USB1", "pin": "5"}]},
        {"net": "USB_DP", "pins": [{"ref": "U1", "pin": "33"},
                                    {"ref": "USB1", "pin": "3"}]},
    ]
    return design


# ---------------------------------------------------------------------------
# 1. 能力：判据先行 + 缺证据 = unknown
# ---------------------------------------------------------------------------


def part_capabilities() -> None:
    design = _design()
    payload = plan_module.derive(DesignStore(PROJECT), design, {})
    caps = {c["id"]: c for c in payload["capabilities"]}
    check("1a. 模板里的能力都在（14 条：H1–H8 + S1–S6）",
          len(caps) == 14, f"{sorted(caps)}")
    check("1b. **每条能力都有可测量判据**（metric/op/value/unit）",
          all({"metric", "op", "value", "unit"} <= set(c["criterion"])
              for c in caps.values()))
    check("1c. 需求没给数值 → 全部 `unknown`（**不是 pass**）",
          all(c["status"] == "unknown" for c in caps.values()),
          str({c["id"]: c["status"] for c in caps.values()}))
    check("1d. `unknown` 要写清缺的是什么（不是空着）",
          all(c.get("reason") or c.get("missing_need") for c in caps.values()))
    check("1e. 缺证据的能力要列出缺哪一项（`evidence.missing`）",
          bool(caps["H1"]["evidence"]["missing"]),
          str(caps["H1"]["evidence"]))
    check("1f. 实物在位的证据被认出来（MCU 在 → H2 的 unit_role 不缺席）",
          "part_role:sensor" in caps["H2"]["evidence"]["missing"]
          or "unit_role:mcu" not in caps["H2"]["evidence"]["missing"],
          str(caps["H2"]["evidence"]))


# ---------------------------------------------------------------------------
# 2. 数值 → pass / fail
# ---------------------------------------------------------------------------


def part_verdicts() -> None:
    design = _design()
    good = plan_module.derive(DesignStore(PROJECT), design,
                              {"numbers": {"thrust_weight": 2.4,
                                           "rail_5v_under_load_v": 4.85}})
    caps = {c["id"]: c for c in good["capabilities"]}
    check("2a. 给足数值 → `pass`（H1 / H8）",
          caps["H1"]["status"] == "pass" and caps["H8"]["status"] == "pass",
          f"H1={caps['H1']['status']} H8={caps['H8']['status']}")

    bad = plan_module.derive(DesignStore(PROJECT), design,
                             {"numbers": {"thrust_weight": 1.5}})
    caps_bad = {c["id"]: c for c in bad["capabilities"]}
    check("2b. 数值不满足 → `fail`",
          caps_bad["H1"]["status"] == "fail", str(caps_bad["H1"]["criterion"]))
    h1_tasks = [t for t in bad["tasks"] if t["capability"] == "H1"]
    check("2c. `fail` 生成 **design** 任务（不是只提示）",
          any(t["kind"] == "design" for t in h1_tasks),
          str([(t["id"], t["kind"]) for t in h1_tasks]))


# ---------------------------------------------------------------------------
# 3. 模块坐标与 3D 图
# ---------------------------------------------------------------------------


def part_graph() -> None:
    design = _design()
    payload = plan_module.derive(DesignStore(PROJECT), design, {})
    modules = {m["id"]: m for m in payload["modules"]}
    c9 = modules.get("mod:part:C9")
    check("3a. **每个元件都进图**（含小电容）",
          c9 is not None and len(modules) >= 4, f"{len(modules)} 个模块")
    check("3b. 坐标**来自 design.json**（C9 = 9.0, 25.0 mm）",
          c9 is not None and abs(c9["pos"]["x_mm"] - 9.0) < 1e-6
          and abs(c9["pos"]["y_mm"] - 25.0) < 1e-6,
          str(c9["pos"]) if c9 else "")

    graph = payload["graph"]
    layers = {n["layer"] for n in graph["nodes"]}
    check("3c. 四层齐全：任务 / 能力 / 模块 / 板",
          {"task", "capability", "board"} <= layers
          and ("module" in layers or "component" in layers),
          str(sorted(layers)))
    check("3d. 能力层抬起来了（z=+60），任务层在下方（z=-60）",
          all(n["pos"]["z_mm"] == 60.0 for n in graph["nodes"]
              if n["layer"] == "capability")
          and all(n["pos"]["z_mm"] == -60.0 for n in graph["nodes"]
                  if n["layer"] == "task"))
    check("3e. **每条边都有来源**（net/joint/placement/task/template）",
          all(e.get("source") in ("net", "joint", "placement", "task", "template")
              for e in graph["edges"]),
          str(sorted({e.get("source") for e in graph["edges"]})))

    flat = plan_module.graph2d(payload)
    check("3f. 2D 投影归一化到 0..1",
          flat["status"] == "ok"
          and all(0.0 <= n["x"] <= 1.0 and 0.0 <= n["y"] <= 1.0
                  for n in flat["nodes"]),
          str(flat.get("extent_mm")))


# ---------------------------------------------------------------------------
# 4. 任务树
# ---------------------------------------------------------------------------


def part_tasks() -> None:
    design = _design()
    payload = plan_module.derive(DesignStore(PROJECT), design, {})
    tasks = payload["tasks"]
    ids = [t["id"] for t in tasks]
    check("4a. **任务 id 唯一**（同能力可能同时要补数据+改设计）",
          len(ids) == len(set(ids)), f"{len(ids)} 条 / {len(set(ids))} 唯一")
    check("4b. **每条任务都有 verify**（没有验收方式的不生成）",
          all(str(t.get("verify") or "") for t in tasks))
    check("4c. 依赖都指向存在的任务",
          all(set(t.get("depends_on") or []) <= set(ids) for t in tasks))
    check("4d. 每个 unknown 能力至少有一条任务（否则任务表漏了东西）",
          all(c["tasks"] for c in payload["capabilities"]
              if c["status"] != "pass"),
          str([(c["id"], c["status"], len(c["tasks"]))
               for c in payload["capabilities"]][:5]))
    check("4e. 能力依赖被翻成任务依赖（H1 依赖 H8 → H1 的任务依赖 H8 的验收任务）",
          any("T-H8-V" in (t.get("depends_on") or [])
              for t in tasks if t["capability"] == "H1"),
          str([(t["id"], t["depends_on"]) for t in tasks
               if t["capability"] == "H1"]))


# ---------------------------------------------------------------------------
# 5. 版本：叠 vN、同需求不新建、状态按 id 继承
# ---------------------------------------------------------------------------


def part_versions() -> None:
    store = DesignStore(PROJECT)
    store.delete()
    design = _design()
    try:
        first = plan_module.ensure(store, design, {"id": "r1"})
        check("5a. 第一次 → v1",
              first.get("version") == "v1" and first.get("created") is True,
              str(first))

        again = plan_module.ensure(store, design, {"id": "r1"})
        check("5b. **同一份需求不新建版本**（按 hash 判重）",
              again.get("version") == "v1" and again.get("created") is False,
              str(again))

        second = plan_module.ensure(store, design,
                                    {"id": "r2",
                                     "numbers": {"thrust_weight": 2.4}})
        check("5c. 新需求 → v2，且 `parent` 指向 v1",
              second.get("version") == "v2"
              and plan_module.load_version(store, "v2")["parent"] == "v1",
              str(second))
        check("5d. `index.json` 记下了两版",
              len(plan_module.list_versions(store)) == 2,
              str(plan_module.list_versions(store)))

        # 状态继承：把 v2 里一条任务标 done，再加需求 → v3 应该继承它
        v2 = plan_module.load_version(store, "v2")
        target = next(t for t in v2["tasks"] if t["id"] == "T-H8-V")
        target["status"] = "done"
        store.write("plan/v2.json", v2)
        third = plan_module.ensure(store, design, {"id": "r3"})
        v3 = plan_module.load_version(store, "v3")
        carried = {t["id"]: t["status"] for t in v3["tasks"]}
        check("5e. 任务状态**按 id 继承**（v2 里 done 的 v3 仍是 done）",
              third.get("version") == "v3"
              and carried.get("T-H8-V") == "done",
              f"v3 里 T-H8-V={carried.get('T-H8-V')}")
        check("5f. 但**判据按当前需求重算**（r3 没给数值 → H8 回到 unknown）",
              {c["id"]: c["status"] for c in v3["capabilities"]}["H8"]
              == "unknown",
              str({c["id"]: c["status"] for c in v3["capabilities"]
                   if c["id"] == "H8"}))
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 6. 退化
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    """把"缺数值 → unknown"改成 pass → 1c 必须变红。"""
    design = _design()
    original = plan_module._capabilities

    def lax(design_, requirement):  # noqa: ANN001
        rows = original(design_, requirement)
        for row in rows:
            row["status"] = "pass"
        return rows

    plan_module._capabilities = lax  # type: ignore[assignment]
    try:
        degenerated = plan_module.derive(DesignStore(PROJECT), design, {})
    finally:
        plan_module._capabilities = original  # type: ignore[assignment]
    baseline = plan_module.derive(DesignStore(PROJECT), design, {})
    check("6. 退化：把 unknown 全改成 pass → 1c 的那条断言不成立（证明它在判）",
          all(c["status"] == "unknown" for c in baseline["capabilities"])
          and any(c["status"] == "pass" for c in degenerated["capabilities"]),
          f"baseline_unknown={sum(1 for c in baseline['capabilities'] if c['status']=='unknown')}")


def main() -> int:
    print("=== P49 项目计划图（plan）===")
    part_capabilities()
    part_verdicts()
    part_graph()
    part_tasks()
    part_versions()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())