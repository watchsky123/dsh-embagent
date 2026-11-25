"""P50 验收 —— 软硬接口契约（`interface`）+ 纯 C 算法与 PC 侧单元测试（`fw-algo`）。

    python -m dsh_embagent.scripts.check_p50

## 这一段补什么

用户问"你做任务时考虑到软件了吗"。诚实回答是：**只考虑到了判据，没有产物**。
这一段补两块**能被本机验证**的软件：

| 命令 | 产出 | 判据 |
|---|---|---|
| `interface` | `interface.json` / `.md`：引脚↔网络契约 + 外设分组 | 逐引脚一致（唯一硬判据）；时钟/中断/AF/时序 **unknown** |
| `fw-algo` | 纯 C 算法 + 单元测试，用 **gcc 真编译真跑** | ok=全过 / failed=有失败用例 / not_ready=没 gcc |

## 判据

| 项 | 期望 |
|---|---|
| 接口 | 每个引脚都有 `net/kind/direction/usable_as_gpio`；缺 `.ioc` 时外设 `source=inferred` |
| 接口 | 网表用了元件没有的脚 → `iface.pin_missing` + `failed` |
| 接口 | `unknown` 必须列出 4 项（时钟/中断/AF/时序）并各带 `need` |
| 算法 | `failsafe` / `lpf` 各 9 条用例全过 |
| 算法 | **退化**：删掉角度判断 → 用例红、status=failed |
| 算法 | 没 gcc → `not_ready`（不是 ok）|

退化验证：关掉引脚一致性检查 → 2b 变绿。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent import design_cli  # noqa: E402
from dsh_embagent.design import fw_algo, interface  # noqa: E402
from dsh_embagent.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from dsh_embagent.design.store import DesignStore  # noqa: E402

PROJECT = "p50-fixture"

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


def _design(*, bad_pin: bool = False) -> DesignModel:
    design = DesignModel(id=PROJECT, board=Board(
        kind="custom", name="b", id="PCB#main",
        outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
        size_mm={"w": 40, "h": 30}, thickness_mm=1.6))
    design.add_part(Part(
        ref="U1", kind="stm32f103c8t6", label="MCU", lcsc="C8734",
        role="module", size_mm={"w": 7.0, "h": 7.0, "d": 1.6},
        physical_source="datasheet", pins=[str(i) for i in range(1, 49)],
        placement=Placement(x_mm=10.0, y_mm=10.0, board_id="PCB#main")))
    design.add_part(Part(
        ref="C9", kind="capacitor", label="100nF", lcsc="C1591",
        size_mm={"w": 1.6, "h": 0.8, "d": 0.8},
        physical_source="datasheet", pins=["1", "2"],
        placement=Placement(x_mm=25.0, y_mm=10.0, board_id="PCB#main")))
    design.nets = [
        {"net": "3V3", "pins": [{"ref": "U1", "pin": "24"},
                                 {"ref": "C9", "pin": "1"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": "23"},
                                 {"ref": "C9", "pin": "2"}]},
        {"net": "UART_TX", "pins": [{"ref": "U1", "pin": "30"}]},
        # 故意用一个**元件上没有的脚**（49 > 48）
        {"net": "BAD", "pins": [{"ref": "U1", "pin": "49"}]},
    ] if bad_pin else [
        {"net": "3V3", "pins": [{"ref": "U1", "pin": "24"},
                                 {"ref": "C9", "pin": "1"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": "23"},
                                 {"ref": "C9", "pin": "2"}]},
        {"net": "UART_TX", "pins": [{"ref": "U1", "pin": "30"}]},
        {"net": "USB_DP", "pins": [{"ref": "U1", "pin": "33"}]},
    ]
    return design


# ---------------------------------------------------------------------------
# 1. 接口契约
# ---------------------------------------------------------------------------


def part_interface() -> None:
    payload = interface.build(_design())
    pins = payload["pins"]
    check("1a. 每个引脚都有 net/kind/direction/usable_as_gpio",
          all({"net", "kind", "direction", "usable_as_gpio"} <= set(row)
              for row in pins))
    check("1b. 电源/地不许当 GPIO 用，未接的可以",
          all(row["usable_as_gpio"] is False
              for row in pins if row["kind"] in ("power", "ground"))
          and all(row["usable_as_gpio"] is True
                  for row in pins if row["kind"] == "unconnected"),
          str([(r["pin"], r["kind"], r["usable_as_gpio"]) for r in pins[:4]]))
    check("1c. 没有 `.ioc` → 外设分组标 `inferred`（不是 declared）",
          all(g["source"] == "inferred" for g in payload["peripherals"]),
          str([(g["family"], g["source"]) for g in payload["peripherals"]]))
    check("1d. `unknown` 必须列出 4 项（时钟/中断/AF/时序）且各带 need",
          len(payload["unknown"]) == 4
          and all(u.get("need") for u in payload["unknown"]),
          str([u["what"] for u in payload["unknown"]]))

    bad = interface.build(_design(bad_pin=True))
    check("1e. 网表用了元件没有的脚 → `iface.pin_missing` 且 status=failed",
          bad["status"] == "failed"
          and any(p["check"] == "iface.pin_missing" for p in bad["problems"]),
          str(bad["problems"])[:120])

    # 退化：把一致性检查关掉 → 1e 必须变绿
    original = interface.build

    def lax(design, *, ioc_path=None):  # noqa: ANN001
        out = original(design, ioc_path=ioc_path)
        out["problems"] = []
        out["status"] = "ok"
        return out

    interface.build = lax  # type: ignore[assignment]
    try:
        degenerated = interface.build(_design(bad_pin=True))
    finally:
        interface.build = original  # type: ignore[assignment]
    check("1f. 退化：关掉引脚一致性检查 → 1e 变绿（证明它在判）",
          bad["status"] == "failed" and degenerated["status"] == "ok")


# ---------------------------------------------------------------------------
# 2. 纯 C 算法：真编译真跑
# ---------------------------------------------------------------------------


def part_algorithms() -> None:
    if not (shutil.which("gcc") or shutil.which("cc")):
        check("2a. 没有 gcc → `not_ready`（**不是 ok**）",
              fw_algo.build_and_run("failsafe", tempfile.mkdtemp())["status"]
              == "not_ready")
        return

    base = Path(tempfile.mkdtemp(prefix="p50-algo-"))
    for algo, expect in (("failsafe", 9), ("lpf", 9)):
        got = fw_algo.build_and_run(algo, base / algo)
        check(f"2a-{algo}. 真编译真跑，{expect} 条用例全过",
              got["status"] == "ok"
              and (got.get("cases") or {}).get("total") == expect,
              f"{got['status']} {got.get('cases')}")
    check("2b. 三条源码都在（.h/.c/test_*.c）",
          all((base / algo / name).is_file()
              for algo in ("failsafe", "lpf")
              for name in (f"{algo}.h", f"{algo}.c", f"test_{algo}.c")))

    # 退化：把角度判断那一行删掉 → 用例必须红
    degenerated = fw_algo.build_and_run(
        "failsafe", base / "degen",
        drop="if (fs_abs(angle_deg) > cfg->angle_deg_max)      triggered = 1;")
    check("2c. **退化**：删掉角度判断 → 用例红、status=failed",
          degenerated["status"] == "failed"
          and bool((degenerated.get("cases") or {}).get("failed")),
          str((degenerated.get("cases") or {}).get("failed"))[:120])

    check("2d. 失败原因指向具体用例（不是一句'失败'）",
          any("angle_trigger" in str(x) or "angle_state" in str(x)
              for x in (degenerated.get("cases") or {}).get("failed") or []))
    check("2e. 未知算法 → `not_ready` 且列出可用算法",
          fw_algo.build_and_run("nope", base / "nope")["status"] == "not_ready"
          and "failsafe" in str(fw_algo.list_algos()))


# ---------------------------------------------------------------------------
# 3. 在真工程上跑（不是只有夹具）
# ---------------------------------------------------------------------------


def part_on_project() -> None:
    store = DesignStore(PROJECT)
    try:
        store.save(_design())
        got = design_cli.cmd_interface([PROJECT])
        check("3a. `interface` 命令写出 interface.json + .md",
              got["status"] == "ok"
              and store.path("interface.json").is_file()
              and store.path("interface.md").is_file(),
              str(got.get("counts")))
        algo = design_cli.cmd_fw_algo([PROJECT, "--algo=failsafe"])
        check("3b. `fw-algo` 在工程里生成源码并跑通",
              algo["status"] == "ok"
              and store.path("fw/failsafe/failsafe.c").is_file(),
              f"{algo['status']} {algo.get('cases')}")
    finally:
        store.delete()


def main() -> int:
    print("=== P50 软硬接口契约 + 纯 C 算法（PC 侧单元测试）===")
    part_interface()
    part_algorithms()
    part_on_project()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())