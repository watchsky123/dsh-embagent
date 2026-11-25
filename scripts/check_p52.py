"""P52 验收 —— 可编译骨架 + **真编译**（`fw-build`）。

    python -m dsh_embagent.scripts.check_p52

## 判据

| 项 | 期望 |
|---|---|
| 骨架 | 4 个文件（startup.c / link.ld / main.c / Makefile），link.ld 里带容量出处 |
| 真编译 | `firmware.elf` + `firmware.bin` 都出得来 |
| Machine | `ARM`（不是宿主 x86）|
| Entry | 落在 `0x08000000` 起的 FLASH 区 |
| 容量 | `text+data ≤ flash`、`data+bss ≤ ram` |
| MCU 不在表 | `not_ready` + 列出已知型号（**不猜容量**）|
| 无工具链 | `not_ready`（环境缺 ≠ 设计错）|

退化（证明判据真的在判）：
* 把 `link.ld` 的 FLASH 改到 `0x20000000` → Entry 不在 FLASH → `failed`
* 把 MCU 的 ram 改小 → `data+bss` 超限 → `failed`
* 在 `main.c` 里插语法错 → 编译不过 → `failed` + 编译器有输出
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import fw_build  # noqa: E402
from dsh_embagent.design import interface as iface_module  # noqa: E402
from dsh_embagent.design.model import Board, DesignModel, Part, Placement  # noqa: E402
from dsh_embagent.design.store import DesignStore  # noqa: E402

PROJECT = "p52-fixture"
_failures: list[str] = []
_checks = 0


def _safe(text: str) -> str:
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(text).encode(enc, "replace").decode(enc, "replace")


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


def _design(kind: str = "stm32f103c8t6") -> DesignModel:
    design = DesignModel(id=PROJECT, board=Board(
        kind="custom", name="b", id="PCB#main",
        outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
        size_mm={"w": 40, "h": 30}))
    design.add_part(Part(
        ref="U1", kind=kind, label="MCU", lcsc="C8734", role="module",
        size_mm={"w": 7.0, "h": 7.0, "d": 1.6}, physical_source="datasheet",
        pins=[str(i) for i in range(1, 49)],
        placement=Placement(x_mm=10.0, y_mm=10.0, board_id="PCB#main")))
    design.nets = [
        {"net": "3V3", "pins": [{"ref": "U1", "pin": "24"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": "23"}]},
        {"net": "UART_TX", "pins": [{"ref": "U1", "pin": "30"}]},
        {"net": "UART_RX", "pins": [{"ref": "U1", "pin": "31"}]},
    ]
    return design


def _prep(store: DesignStore, design: DesignModel) -> None:
    store.delete()
    store.save(design)
    contract = iface_module.build(design)
    store.write(iface_module.FILE, contract)
    made = fw_build.generate(store, design, contract)
    store.write(fw_build.FILE, made)


# ---------------------------------------------------------------------------
# 1. 骨架
# ---------------------------------------------------------------------------


def part_skeleton() -> None:
    store = DesignStore(PROJECT)
    try:
        _prep(store, _design())
        made = store.read(fw_build.FILE) or {}
        check("1a. 四个文件都生成了（startup / link.ld / main / Makefile）",
              made.get("status") == "ok"
              and all(store.path(f"fw/build/{n}").is_file()
                      for n in ("startup.c", "link.ld", "main.c", "Makefile")),
              str([p.split("/")[-1] for p in made.get("files") or []]))
        ld = store.read_text("fw/build/link.ld")
        check("1b. link.ld 的容量**带出处**（不是裸数字）",
              "flash_kb" not in ld and "数据手册" in ld,
              ld.splitlines()[1] if len(ld.splitlines()) > 1 else "")
        main_c = store.read_text("fw/build/main.c")
        check("1c. main.c 里外设初始化只有 TODO（骨架，不装功能对）",
              "TODO uart" in main_c and "for (;;)" in main_c)
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 2. 真编译 + 四条判据
# ---------------------------------------------------------------------------


def part_build() -> None:
    store = DesignStore(PROJECT)
    try:
        _prep(store, _design())
        result = fw_build.build(store)
        if result["status"] == "not_ready" and "arm-none-eabi-gcc" in str(result.get("reason")):
            check("2a. 没有工具链 → `not_ready`（不是 failed）", True,
                  str(result.get("reason"))[:80])
            return
        checks = result.get("checks") or {}
        check("2a. 真编译出 .elf + .bin",
              result["status"] == "ok"
              and store.path("fw/build/firmware.elf").is_file()
              and store.path("fw/build/firmware.bin").is_file(),
              f"{result['status']} {result.get('size')}")
        check("2b. Machine = ARM（不是宿主 x86）",
              checks.get("machine") == "ARM", str(checks.get("machine")))
        entry = str(checks.get("entry") or "")
        check("2c. Entry 落在 FLASH（0x08000000 起）",
              entry.startswith("0x8") or entry.startswith("0x0800"), entry)
        check("2d. 容量装得下（text+data ≤ flash、data+bss ≤ ram）",
              checks.get("text_b", 0) + checks.get("data_b", 0)
              <= checks.get("flash_limit_b", 0)
              and checks.get("data_b", 0) + checks.get("bss_b", 0)
              <= checks.get("ram_limit_b", 0),
              str(checks))
        check("2e. 报告里写明「骨架，不代表功能对」",
              "不代表功能对" in str(result.get("note") or ""))
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 3. 退化：三条判据各自能红
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    # 3a. MCU 不在表 → not_ready（不猜容量）
    store = DesignStore(PROJECT)
    try:
        _prep(store, _design(kind="stm32l476rgt6"))
        made = fw_build.generate(store, _design(kind="stm32l476rgt6"), {})
        check("3a. MCU 不在内存表 → `not_ready` + 列出已知型号（不猜）",
              made["status"] == "not_ready" and made.get("known"),
              str(made.get("reason"))[:80])

        # 3b..3d：需要工具链
        _prep(store, _design())
        if fw_build.build(store)["status"] == "not_ready":
            check("3b–3d. 无工具链时跳过退化（已断言 not_ready）", True)
            return

        # 3b. FLASH 改到 0x20000000 → Entry 不在 FLASH
        ld_path = store.path("fw/build/link.ld")
        original = ld_path.read_text(encoding="utf-8")
        ld_path.write_text(original.replace("0x08000000", "0x20000000"),
                           encoding="utf-8")
        bad_entry = fw_build.build(store)
        check("3b. 退化：把 FLASH 挪到 0x20000000 → Entry 不在 FLASH → failed",
              bad_entry["status"] == "failed"
              and any("Entry" in f for f in bad_entry.get("findings") or []),
              str(bad_entry.get("findings"))[:120])
        ld_path.write_text(original, encoding="utf-8")

        # 3c. RAM 容量改到 1KB → data+bss 超限
        saved = dict(fw_build.MCU_MEMORY["stm32f103c8t6"])
        try:
            fw_build.MCU_MEMORY["stm32f103c8t6"] = dict(saved, ram_kb=0.001)
            over = fw_build.build(store)
        finally:
            fw_build.MCU_MEMORY["stm32f103c8t6"] = saved  # type: ignore[assignment]
        check("3c. 退化：把 RAM 改到 1 B → data+bss 超限 → failed",
              over["status"] == "failed"
              and any("RAM" in f for f in over.get("findings") or []),
              str(over.get("findings"))[:120])

        # 3d. main.c 插语法错 → 编译不过
        main_path = store.path("fw/build/main.c")
        main_orig = main_path.read_text(encoding="utf-8")
        main_path.write_text(main_orig + "\nthis is not C\n", encoding="utf-8")
        broken = fw_build.build(store)
        check("3d. 退化：main.c 插语法错 → 编译不过 → failed + 编译器有输出",
              broken["status"] == "failed"
              and bool(broken.get("compiler")),
              str(broken.get("compiler"))[-160:])
        main_path.write_text(main_orig, encoding="utf-8")
    finally:
        store.delete()


def main() -> int:
    print("=== P52 可编译骨架 + 真编译 ===")
    part_skeleton()
    part_build()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())