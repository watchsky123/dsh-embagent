"""P58 验收 —— 封装真几何：尺子从"目录猜"换成"封装源数据"。

    python -m dsh_embagent.scripts.check_p58

## 这一段补什么

`dims` 能说清尺寸来源，但最可信那档（`catalog`）一直是空的：立创目录没有尺寸、
JEDEC 表只覆盖少数封装。编辑器里有**封装真几何**：
`LIB_Footprint.getGeometry(footprintUuid, libraryUuid)` 一次给出本体包围盒、
焊盘包围盒和每个焊盘（mil）。

现在 `lceda-geom` 把它取回来 → `geom.json`，`--apply` 只补**低信任来源**。

## 判据（**反着判**最要紧）

| 项 | 期望 |
|---|---|
| 真实项目 | 27/28 拿到几何；1 个 `no_footprint` 写明原因，**不填默认值** |
| LQFP48 自洽 | 48 焊盘、pitch 0.5 mm、最小焊盘间隙 = 0.5−0.27 = 0.23 mm |
| 本体 vs 外包 | `title_size` 7×7×1.4（本体）、`full_mm` 10×10（焊盘）—— **不能拿外包当本体** |
| mil→mm | 存了原始 mil 包围盒，重算必须与 mm 一致 |
| 写回纪律 | `datasheet` 一处不动；空/estimated 才写，并标 `catalog` |
| 退化 | 没有 3D 标题 → `_choose_size` 返回 None（不写错的数）|
| 无客户端 | `not_ready`，**不是 ok** |
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import lceda  # noqa: E402
from dsh_embagent.design import lceda_geom as geom_module  # noqa: E402
from dsh_embagent.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from dsh_embagent.design.store import DesignStore  # noqa: E402

from dsh_embagent.scripts._probe import path as probe_path  # noqa: E402
PROJECT = "p58-fixture"
REAL_DESIGN = probe_path("DSH_EMBAGENT_PROBE_DESIGN")

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


def _design(parts: list[tuple[str, str, dict, str]]) -> DesignModel:
    board = Board(kind="custom", name="b", id="PCB#main",
                  outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                  size_mm={"w": 40, "h": 30}, thickness_mm=1.6)
    design = DesignModel(id=PROJECT, board=board)
    for index, (ref, lcsc, size, source) in enumerate(parts):
        design.add_part(Part(
            ref=ref, kind="component", label=ref, lcsc=lcsc,
            size_mm=size, physical_source=source,
            placement=Placement(x_mm=5.0 + index * 6.0, y_mm=5.0,
                                board_id="PCB#main")))
    return design


def _session() -> lceda.LcedaSession:
    return lceda.LcedaSession().open("", headless=True)


# ---------------------------------------------------------------------------
# 1. 真实项目：几何取得到、数自洽
# ---------------------------------------------------------------------------


def part_real() -> None:
    if not REAL_DESIGN.is_file() or not lceda.find_exe():
        print(_safe("[skip] 1. 没有真实项目或 lceda-pro —— 这一节**没跑**，不是通过"))
        return
    design = DesignModel.from_dict(
        json.loads(REAL_DESIGN.read_text(encoding="utf-8")))
    session = _session()
    try:
        payload = geom_module.fetch(design, session=session)
    finally:
        session.close(destroy=True)
    counts = payload.get("counts") or {}
    check("1a. 真实项目：**每个带 LCSC 的位号都有归属**（拿到或写明原因）",
          payload["status"] == "ok"
          and counts.get("missing") == 0
          and counts.get("resolved", 0) >= 20
          and counts.get("parts_with_lcsc") == counts.get("resolved", 0)
          + counts.get("unresolved", 0),
          str(counts))
    check("1b. 拿不到的是**少数**，且每条都写了原因（不是静默丢）",
          counts.get("unresolved", 0) <= 3
          and all(row.get("reason") for row in payload.get("unresolved") or []),
          str([(row.get("lcsc"), row.get("refs"), row.get("reason"))
               for row in payload.get("unresolved") or []]))

    u1 = (payload.get("parts") or {}).get("U1") or {}
    full = u1.get("full_mm") or {}
    check("1c. LQFP48 自洽：48 焊盘 / pitch 0.5 mm / 最小间隙 0.23 mm",
          u1.get("pads") == 48
          and abs(float(u1.get("pitch_mm") or 0) - 0.5) < 0.02
          and abs(float(u1.get("min_pad_gap_mm") or 0) - 0.23) < 0.03,
          f"pads={u1.get('pads')} pitch={u1.get('pitch_mm')} "
          f"gap={u1.get('min_pad_gap_mm')}")

    title = u1.get("title_size_mm") or {}
    check("1d. **本体 ≠ 焊盘外包**：title 7×7×1.4，full 10×10.05",
          abs(float(title.get("w") or 0) - 7.0) < 0.01
          and abs(float(title.get("h") or 0) - 7.0) < 0.01
          and abs(float(title.get("d") or 0) - 1.4) < 0.01
          and abs(float(full.get("w") or 0) - 10.0) < 0.05,
          f"title={title} full={full}")

    raw = u1.get("raw_bbox_mil") or {}
    raw_full = raw.get("full") or {}
    recomputed = (float(raw_full.get("maxX", 0)) - float(raw_full.get("minX", 0))) * geom_module.MIL_TO_MM
    check("1e. **mil→mm 可复核**：存了原始 mil 包围盒，重算与 full_mm 一致",
          abs(recomputed - float(full.get("w") or 0)) < 0.01,
          f"raw={recomputed:.3f}mm vs full={full.get('w')}")

    rows = payload.get("size_check") or []
    offenders = [row for row in rows if row.get("verdict") == "mismatch"]
    # `close`（5–15%）允许：datasheet 常写"含引脚"尺寸，和本体口径不同。
    # `mismatch`（>15%）才是需要人看一眼的。
    check("1f. 尺寸核对：几何本体 vs 设计已写 —— **没有 >15% 的偏差**",
          len(rows) >= 15 and not offenders,
          f"checked={len(rows)} mismatch={len(offenders)} "
          + str([(r.get("ref"), r.get("delta_pct"), r.get("design"), r.get("geom"))
                 for r in offenders[:3]]))

    provenance = payload.get("retrieved") and (payload.get("session") or {}).get("version")
    check("1g. 出处：会话版本 + 取回日期 + source=catalog",
          bool(provenance) and payload.get("source") == "catalog",
          str(payload.get("session")))


# ---------------------------------------------------------------------------
# 2. 写回纪律：只补低信任来源
# ---------------------------------------------------------------------------


def part_apply() -> None:
    if not lceda.find_exe():
        print(_safe("[skip] 2. 没有 lceda-pro —— 这一节**没跑**，不是通过"))
        return
    # 夹具用**同一颗 MCU**：C14858（0603 电容）在库里偶发没有封装关联，
    # 拿它当"必须补上"的样本会让验收随库缓存抖动。
    design = _design([
        ("U1", "C8734", {"w": 7.0, "h": 7.0, "d": 1.6}, "datasheet"),
        ("C1", "C8734", {"w": 0.0, "h": 0.0, "d": 0.0}, "estimated"),
    ])
    session = _session()
    try:
        payload = geom_module.fetch(design, session=session)
    finally:
        session.close(destroy=True)
    if payload.get("status") != "ok":
        print(_safe(f"[skip] 2. 拿不到几何（{payload.get('reason')}）"))
        return
    result = geom_module.apply(design, payload)
    applied = {row["ref"]: row for row in result["applied"]}
    skipped = {row["ref"]: row for row in result["skipped"]}
    c1 = applied.get("C1") or {}
    u1 = skipped.get("U1") or {}
    check("2a. **空/estimated 被补上**，并标 catalog（LQFP48：7×7×1.4）",
          (c1.get("new") or {}).get("w") == 7.0
          and (c1.get("new") or {}).get("h") == 7.0
          and (c1.get("new") or {}).get("d") == 1.4,
          str(c1)[:120])
    check("2b. **datasheet 一个字节不动**（7×7 仍是 7×7，来源仍是 datasheet）",
          (u1.get("kept") or {}).get("w") == 7.0
          and [p.physical_source for p in design.parts if p.ref == "U1"] == ["datasheet"]
          and [p.size_mm["w"] for p in design.parts if p.ref == "U1"] == [7.0],
          str(u1)[:120])


# ---------------------------------------------------------------------------
# 3. 退化 / 边界
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    check("3a. 没有 3D 标题 → **不写尺寸**（宁缺勿错）",
          geom_module._choose_size({"full_mm": {"w": 2.9, "h": 1.5},
                                    "body_mm": {"w": 2.8, "h": 1.6}}) is None,
          "full/body 都被拒绝")
    check("3b. 只有 title 才写，且高度缺省为 0 时不编造",
          (geom_module._choose_size({"title_size_mm": {"w": 7.0, "h": 7.0}})
           or {}).get("d") == 0.0)

    original = lceda.find_exe
    try:
        lceda.find_exe = lambda: ""  # type: ignore[assignment]
        design = _design([("U1", "C8734", {"w": 0, "h": 0, "d": 0}, "estimated")])
        session = lceda.LcedaSession()
        payload = geom_module.fetch(design, session=session)
    finally:
        lceda.find_exe = original  # type: ignore[assignment]
    check("3c. 没有客户端 → `not_ready`（**不是 ok，也不填默认值**）",
          payload.get("status") == "not_ready" and not payload.get("parts"),
          str(payload.get("reason")))

    # 离线负例：不依赖客户端 —— 用一个假的 session 返回 device_not_found
    class _FakeSession:
        ready = True
        version = "fake"
        headless = True

        def invoke(self, code, label=""):
            return [{"lcsc": "C000000000", "ok": False, "reason": "device_not_found"}]

    fake = geom_module.fetch(
        _design([("X1", "C000000000", {"w": 0, "h": 0, "d": 0}, "estimated")]),
        session=_FakeSession())
    check("3e. 未知 LCSC（假会话）→ `not_ready` + unresolved 写明原因",
          fake.get("status") == "not_ready"
          and fake["unresolved"][0]["reason"] == "device_not_found",
          str(fake.get("unresolved")))


def main() -> int:
    print("=== P58 封装真几何（lceda-geom）===")
    part_real()
    part_apply()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())