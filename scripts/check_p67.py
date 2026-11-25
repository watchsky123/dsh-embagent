"""P67 验收 —— 发射前预检（LCSC 可解析 + 封装关联）。

    python -m dsh_embagent.scripts.check_p67

## 判据

| 情况 | 期望 |
|---|---|
| 有件没 lcsc | **not_ready**（硬拦，emit 前）|
| 编号解析不到 | **not_ready** 并列出编号 |
| 解析到但无封装 | 默认 `ok` + warning；`strict_footprint` 才拦 |
| 问不到系统库 | `not_ready` + 明说「没验」，**不是通过、也不假装失败** |
| `cmd_emit` | 预检不过 → 不调用 emit；`--no-preflight` 才放行 |
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import emit_preflight  # noqa: E402
from dsh_embagent.design.model import Board, DesignModel, Part, Placement  # noqa: E402

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


def _design(codes: list[str]) -> DesignModel:
    board = Board(kind="custom", id="PCB#main", size_mm={"w": 40, "h": 30},
                  outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)])
    design = DesignModel(id="p67", board=board, boards=[board])
    design.parts = [
        Part(ref=f"U{i + 1}", kind="mcu", physical_source="datasheet",
             size_mm={"w": 7, "h": 7, "d": 1.4},
             pins=["1"], lcsc=code,
             placement=Placement(x_mm=5 + i * 8, y_mm=5, board_id="PCB#main"))
        for i, code in enumerate(codes)]
    design.nets = [{"net": "3V3", "class": "power",
                    "pins": [{"ref": p.ref, "pin": "1"} for p in design.parts]}]
    return design


def _fake(mapping: dict[str, tuple[bool, bool]]):
    def lookup(codes: list[str]) -> list[dict]:
        return [{"lcsc": c, "ok": mapping.get(c, (False, False))[0],
                 "footprint": mapping.get(c, (False, False))[1], "name": c}
                for c in codes]
    return lookup


def part_preflight() -> None:
    missing = emit_preflight.check(_design(["C1", ""]), lookup=_fake({"C1": (True, True)}))
    check("A1. 有件没 lcsc → **not_ready**，并列出位号",
          missing["status"] == "not_ready" and missing["missing_code"] == ["U2"],
          str(missing["missing_code"]))

    bad = emit_preflight.check(_design(["C1", "C9"]),
                               lookup=_fake({"C1": (True, True), "C9": (False, False)}))
    check("A2. 编号解析不到 → **not_ready**，并列出编号",
          bad["status"] == "not_ready"
          and [row["lcsc"] for row in bad["unresolved"]] == ["C9"],
          str(bad["unresolved"]))

    soft = emit_preflight.check(_design(["C1", "C2"]),
                                lookup=_fake({"C1": (True, True), "C2": (True, False)}))
    check("A3. 无封装关联 → 默认 **ok** + warning（缓存异步）",
          soft["status"] == "ok" and soft["counts"]["no_footprint"] == 1,
          soft["reason"])

    strict = emit_preflight.check(_design(["C1", "C2"]),
                                  lookup=_fake({"C1": (True, True), "C2": (True, False)}),
                                  strict_footprint=True)
    check("A4. `strict_footprint=True` 时无封装也拦",
          strict["status"] == "not_ready" and strict["counts"]["no_footprint"] == 1,
          strict["reason"])

    def boom(_codes):
        raise RuntimeError("library offline")

    unknown = emit_preflight.check(_design(["C1"]), lookup=boom)
    check("A5. 问不到系统库 → not_ready + 明说「没验」（不是通过）",
          unknown["status"] == "not_ready" and "没验" in unknown.get("reason", ""),
          str(unknown.get("reason"))[:70])


def part_gate() -> None:
    from dsh_embagent import design_cli  # noqa: PLC0415
    from dsh_embagent.design import emit_lceda  # noqa: PLC0415
    from dsh_embagent.design.store import DesignStore  # noqa: PLC0415

    root = Path(tempfile.mkdtemp(prefix="p67-ws-"))
    old = os.environ.get("HWV_WORKSPACE")
    os.environ["HWV_WORKSPACE"] = str(root)
    store = DesignStore("p67")
    store.save(_design(["C1"]))
    calls: list[int] = []
    orig_check = emit_preflight.check
    orig_emit = emit_lceda.emit

    class _Report:
        def to_dict(self):
            return {"status": "ok", "jobs": {}, "project": {}}

    try:
        emit_preflight.check = lambda *a, **k: {
            "status": "not_ready", "reason": "stub: 解析不到"}
        emit_lceda.emit = lambda *a, **k: calls.append(1) or _Report()
        out = design_cli.cmd_emit(["p67"])
        check("B1. 预检不过 → `cmd_emit` 返回 not_ready，且**没有调用 emit**",
              out["status"] == "not_ready" and not calls,
              f"calls={len(calls)} reason={out.get('reason')}")

        out2 = design_cli.cmd_emit(["p67", "--no-preflight"])
        check("B2. `--no-preflight` 才放行（调用了一次 emit）",
              out2.get("status") == "ok" and len(calls) == 1,
              f"calls={len(calls)}")
    finally:
        emit_preflight.check = orig_check
        emit_lceda.emit = orig_emit
        if old is None:
            os.environ.pop("HWV_WORKSPACE", None)
        else:
            os.environ["HWV_WORKSPACE"] = old
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    print("=== P67 发射前预检 ===")
    part_preflight()
    part_gate()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())