"""P41 验收 —— spec 扩四域（`power_nets` / `firmware` / `mechanical` / 来源分级）。

    python -m dsh_embagent.scripts.check_p41

## 这一段补的是什么

1. **来源分级多两层**：`catalog`（`parts_catalog` 一直在写，但 `validate()`
   不认识 —— 老 bug）与 `proposed`（AI 提案，比 estimated 更低）。
   外加唯一的排序表 `SOURCE_ORDER`，别处不许再写 if/elif。
2. **电源/地网可以显式声明**（`power_nets`）：没声明时仍按网名正则走，
   但必须把 `source: inferred` 说出来。
3. **固件 / 机械两域有落点**：`firmware{target,mcu,pinmap}`、
   `mechanical{joints,limits}`；**`joints` 仍只存 `joints_list`**（一个事实源）。
4. **`lcsc` 升为主键 + `jlc_class`**（basic/preferred/extended 决定贴片费）。

## 判据

| 项 | 期望 |
|---|---|
| 旧文件（没有新键）| 读得进、`to_dict()` **不新增键**、往返逐字节一致 |
| `proposed` | **不参与硬判据**（`dims` 判不可信、`source_at_least` 表里最低）|
| `power_nets` 显式 / 缺省 | 两条路径结果不同，且都把来源写上 |
| `mechanical.joints` | 归并进 `joints_list`，`mechanical` 里不再留 `joints` |
| `jlc_class` | 非法值必须抛（不是静默接受）|

退化验证在脚本内做：把 `proposed` 塞进"可信来源"表，用例 3 必须变红。
"""

from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import dims as dims_module  # noqa: E402
from dsh_embagent.design import model as model_module  # noqa: E402
from dsh_embagent.design import power as power_module  # noqa: E402
from dsh_embagent.design.model import (  # noqa: E402
    PHYSICAL_SOURCES, SOURCE_ORDER, Board, DesignError, DesignModel, Part,
    source_at_least,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "designs"

_failures: list[str] = []
_checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if ok:
        print(f"[OK ] {name}")
    else:
        _failures.append(name)
        print(f"[FAIL] {name}")
    if detail:
        print(f"       {detail}")


# ---------------------------------------------------------------------------
# 1. 来源分级
# ---------------------------------------------------------------------------



def _design(project_id: str) -> DesignModel:
    """一个最小可构造的设计（`DesignModel` 要求 board.size_mm 非空）。"""
    return DesignModel(
        id=project_id,
        board=Board(kind="generic", name="b",
                    outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                    size_mm={"w": 40, "h": 30}, thickness_mm=1.6),
    )

def part_sources() -> None:
    check("1a. `catalog` 是合法来源（parts_catalog 一直在写它 —— 老 bug 已修）",
          "catalog" in PHYSICAL_SOURCES,
          f"sources={PHYSICAL_SOURCES}")
    check("1b. `proposed` 是合法来源，且**排在最低**",
          PHYSICAL_SOURCES[-1] == "proposed" and SOURCE_ORDER[0] == "proposed",
          f"order={SOURCE_ORDER}")
    check("1c. `proposed` 低于 estimated/datasheet（表驱动，不靠 if/elif）",
          not source_at_least("proposed", "estimated")
          and not source_at_least("proposed", "datasheet")
          and source_at_least("datasheet", "estimated"),
          "source_at_least 只读 SOURCE_ORDER")
    try:
        Part(ref="X1", kind="resistor", physical_source="catalog")
        ok_catalog = True
    except DesignError:
        ok_catalog = False
    try:
        Part(ref="X2", kind="resistor", physical_source="proposed")
        ok_proposed = True
    except DesignError:
        ok_proposed = False
    check("1d. 两种来源都能真的构造出 Part（不是只在常量表里）",
          ok_catalog and ok_proposed)


def jlc_class() -> None:
    try:
        Part(ref="X3", kind="resistor", jlc_class="bogus")
        raised = False
    except DesignError:
        raised = True
    check("2a. 非法 `jlc_class` 必须抛（不静默接受）", raised)
    part = Part(ref="R1", kind="resistor", lcsc="C1523", jlc_class="basic")
    again = Part.from_dict(part.to_dict())
    check("2b. `lcsc` + `jlc_class` 往返不丢",
          again.lcsc == "C1523" and again.jlc_class == "basic",
          f"lcsc={again.lcsc} class={again.jlc_class}")


# ---------------------------------------------------------------------------
# 2. 向后兼容：旧文件不该长出新键
# ---------------------------------------------------------------------------


def backward_compat() -> None:
    if not FIXTURES.is_dir():
        check("3. 夹具目录存在", False, str(FIXTURES))
        return
    bad: list[str] = []
    grew: list[str] = []
    for path in sorted(FIXTURES.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        design = DesignModel.from_dict(copy.deepcopy(raw))
        once = design.to_dict()
        twice = DesignModel.from_dict(copy.deepcopy(once)).to_dict()
        if once != twice:
            bad.append(path.name)
        for key in ("power_nets", "firmware", "mechanical"):
            if key not in raw and key in once:
                grew.append(f"{path.name}:{key}")
        design.validate()  # 不许抛
    check("3a. 所有夹具 `to_dict → from_dict → to_dict` 逐字节一致",
          not bad, f"不一致：{bad[:4]}")
    check("3b. 旧文件不会被写上新键（power_nets/firmware/mechanical）",
          not grew, f"多出来的：{grew[:4]}")


# ---------------------------------------------------------------------------
# 3. 显式声明 vs 正则推断
# ---------------------------------------------------------------------------


def power_paths() -> None:
    nets = ["GND", "+3V3", "PWR_EN", "AGND_REF"]
    inferred = power_module.classify(nets=nets)
    declared = power_module.classify(declared=["+3V3", "GND"], nets=nets)
    check("4a. 没声明 → `source: inferred`（并且把这件事写出来）",
          inferred["source"] == "inferred" and inferred["notes"],
          f"power={inferred['power']}")
    check("4b. 声明后 → `source: declared`，且结果**不同**于推断",
          declared["source"] == "declared"
          and declared["power"] != inferred["power"],
          f"declared={declared['power']} inferred={inferred['power']}")
    check("4c. 只声明电源、没声明地 → 地仍按名字兜底，但来源如实写出来",
          power_module.classify(declared=["+3V3"], nets=nets)["source"]
          == "declared+inferred-ground",
          "source=declared+inferred-ground")

    design = _design("p41-power")
    design.power_nets = ["+3V3", "GND"]
    once = design.to_dict()
    again = DesignModel.from_dict(copy.deepcopy(once))
    check("4d. `power_nets` 进 design.json 并能读回", again.power_nets == ["+3V3", "GND"],
          f"read={again.power_nets}")
    problems = [p for p in design.validate() if "power_nets" in p]
    check("4e. 网表为空时**不**把 power_nets 判成「不在网表里」",
          problems == [], f"problems={problems}")


# ---------------------------------------------------------------------------
# 4. 机械域归并（一个事实源）
# ---------------------------------------------------------------------------


def mechanical_merge() -> None:
    raw = {
        "id": "p41-mech",
        "board": {"kind": "generic", "name": "b",
                  "size_mm": {"w": 40, "h": 30},
                  "outline": [[0, 0], [40, 0], [40, 30], [0, 30]]},
        "mechanical": {
            "joints": [{"a": "B1", "b": "B2", "kind": "hinge",
                        "source": "proposed",
                        "params": {"range_deg": [0, 90]}}],
            "limits": {"max_mass_g": 250},
        },
    }
    design = DesignModel.from_dict(copy.deepcopy(raw))
    check("5a. `mechanical.joints` 被归并进 `joints_list`",
          len(design.joints_list) == 1
          and design.joints_list[0]["kind"] == "hinge",
          f"joints_list={design.joints_list}")
    check("5b. `mechanical` 里不再留 `joints`（否则是两个事实源）",
          "joints" not in (design.mechanical or {})
          and (design.mechanical or {}).get("limits") == {"max_mass_g": 250},
          f"mechanical={design.mechanical}")
    once = design.to_dict()
    twice = DesignModel.from_dict(copy.deepcopy(once)).to_dict()
    check("5c. 归并后往返仍逐字节一致（不会每读一次就多一条）",
          once == twice and len(twice.get("joints_list") or []) == 1,
          f"joints={len(twice.get('joints_list') or [])}")


# ---------------------------------------------------------------------------
# 5. dims：3D 尺寸来源
# ---------------------------------------------------------------------------


def dims_sources() -> None:
    size, basis = dims_module.size_from_attrs(
        {"transform_3d": "100,60,40,0,0,0,0,0,0"})
    check("6a. `3D Model Transform`（mil）→ mm",
          bool(size) and abs(size["w"] - 2.54) < 0.01 and basis == "transform",
          f"size={size} basis={basis}")
    size2, basis2 = dims_module.size_from_attrs({"model_title": "C0402_L1.0-W0.5-H0.5"})
    check("6b. `3D Model Title` 的 L/W/H → mm（Transform 缺失时兜底）",
          bool(size2) and abs(size2["w"] - 1.0) < 0.01
          and abs(size2["d"] - 0.5) < 0.01 and basis2 == "title",
          f"size={size2} basis={basis2}")
    none_size, _ = dims_module.size_from_attrs({})
    check("6c. 两个都没有 → `None`（不填 0）", none_size is None)

    design = _design("p41-dims")
    design.parts = [
        Part(ref="A1", kind="resistor", size_mm={"w": 1, "h": 0.5, "d": 0.5},
             physical_source="datasheet"),
        Part(ref="B1", kind="capacitor", size_mm={"w": 1, "h": 0.5, "d": 0.5},
             physical_source="proposed"),
        Part(ref="C1", kind="ic", size_mm={"w": 0, "h": 0, "d": 0}),
    ]
    report = dims_module.for_design(design)
    rows = {r["ref"]: r for r in report["rows"]}
    check("6d. proposed 尺寸**不可信**、unknown 被点名（proposed 不进硬判据）",
          rows["A1"]["trusted"] is True
          and rows["B1"]["trusted"] is False
          and rows["C1"]["category"] == "unknown"
          and report["status"] == "not_ready",
          f"status={report['status']} counts={report['counts']}")


# ---------------------------------------------------------------------------
# 6. 退化验证
# ---------------------------------------------------------------------------


def degeneration() -> None:
    """把 `proposed` 塞进"可信尺寸来源"，6d 必须变红。"""
    original = dims_module.TRUSTED_SIZE_SOURCES
    design = _design("p41-degen")
    design.parts = [Part(ref="B1", kind="capacitor",
                         size_mm={"w": 1, "h": 0.5, "d": 0.5},
                         physical_source="proposed")]
    baseline = dims_module.for_design(design)["rows"][0]["trusted"]
    dims_module.TRUSTED_SIZE_SOURCES = tuple(original) + ("proposed",)
    try:
        degenerated = dims_module.for_design(design)["rows"][0]["trusted"]
    finally:
        dims_module.TRUSTED_SIZE_SOURCES = original
    check("7. 退化验证：把 proposed 当可信 → 6d 必须变红（证明闸门在这张表上）",
          baseline is False and degenerated is True,
          f"baseline={baseline} degenerated={degenerated}")


def main() -> int:
    print("=== P41 spec 扩四域 ===")
    part_sources()
    jlc_class()
    backward_compat()
    power_paths()
    mechanical_merge()
    dims_sources()
    degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(f"  [FAIL] {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())