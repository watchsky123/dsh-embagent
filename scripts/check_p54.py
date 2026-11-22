"""P54 验收 —— `fw-build` 接 `interface.json`：**用错脚在编译期就炸**。

    python -m hw_velxio.scripts.check_p54

## 这一段补什么

P52 的骨架里，引脚是**两套来源**：`main.c` 里写死的 TODO 注释 vs
`interface.json` 的契约。于是"板子把 UART_TX 从 30 改到 31，固件不知道"
这种事，只能等台架才发现。

现在 `hwv_pins.h` 由 `interface.json` **生成**，并且提供四个编译期检查宏：

| 宏 | 判什么 | 期望的错 |
|---|---|---|
| `HWV_PIN(NAME)` | 名字在契约里吗 | 未声明标识符 |
| `HWV_CHECK_GPIO(NAME)` | 能当 GPIO 吗 | 电源/地 → `_Static_assert` 失败 |
| `HWV_CHECK_PIN_NUMBER(n)` | 脚号在范围内吗 | 超范围 → 断言失败 |
| `HWV_EXPECT(NAME, n)` | 契约把线挪走了吗 | 位置变了 → 断言失败 |

## 判据（**反着判**最要紧）

对负例来说，**编译通过才是失败**：闸门写了但没挂上去，就是 §0.1 的假绿。

| 项 | 期望 |
|---|---|
| 头文件 | 每个网的名字宏值 == interface 里那个脚；同网多脚只定义一次（注释写其余脚号）|
| 可用性 | 电源/地 → `HWV_PIN_GPIO_<n> 0`；信号/未接 → `1` |
| 骨架 | `main.c` include 契约头，并真的用 `HWV_CHECK_GPIO` / `HWV_EXPECT` |
| 负例 | 3 条**都编不过**，且错误信息指向真原因（不是无关 warning）|
| 退化 | 把 `GPIO_<n>` 全改成 1 → 负例 1 **编过** → 说明这条断言真的在判 |
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import fw_build  # noqa: E402
from hw_velxio.design import interface as iface_module  # noqa: E402
from hw_velxio.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from hw_velxio.design.store import DesignStore  # noqa: E402

PROJECT = "p54-fixture"

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
    """夹具：48 脚 MCU + 4 个电源脚 + 4 个地，两个信号网 + 一个外设网。"""
    board = Board(kind="custom", name="b", id="PCB#main",
                  outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                  size_mm={"w": 40, "h": 30}, thickness_mm=1.6)
    design = DesignModel(id=PROJECT, board=board)
    design.add_part(Part(ref="U1", kind="stm32f103c8t6", label="MCU",
                         lcsc="C8734", role="module",
                         size_mm={"w": 7.0, "h": 7.0, "d": 1.6},
                         physical_source="datasheet",
                         pins=[str(i) for i in range(1, 49)],
                         placement=Placement(x_mm=10.0, y_mm=10.0,
                                             board_id="PCB#main")))
    design.nets = [
        {"net": "3V3", "pins": [{"ref": "U1", "pin": "1"},
                                 {"ref": "U1", "pin": "24"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": "8"},
                                 {"ref": "U1", "pin": "23"}]},
        {"net": "UART_TX", "pins": [{"ref": "U1", "pin": "30"}]},
        {"net": "USB_DP", "pins": [{"ref": "U1", "pin": "33"}]},
    ]
    return design


def _prepare(store: DesignStore) -> tuple[dict, dict]:
    design = _design()
    store.save(design)
    contract = iface_module.build(design)
    store.write(iface_module.FILE, contract)
    made = fw_build.generate(store, design, contract, out_dir="fw/b")
    # `build()` 读的是 `fw_build.json`（CLI 会在 generate 之后写它）——
    # 验收要**走同一条路**，否则验的是一个 CLI 不会产生的状态。
    store.write(fw_build.FILE, made)
    return contract, made


# ---------------------------------------------------------------------------
# 1. 头文件生成（契约 → 宏）
# ---------------------------------------------------------------------------


def part_header() -> None:
    store = DesignStore(PROJECT)
    try:
        store.delete()
        contract, made = _prepare(store)
        check("1a. 骨架里多了 `hwv_pins.h`（契约的头文件版）",
              made["status"] == "ok" and store.path("fw/b/hwv_pins.h").is_file(),
              str([f.replace("\\", "/").split("/")[-1] for f in made.get("files") or []]))
        header = store.path("fw/b/hwv_pins.h").read_text(encoding="utf-8")

        nets = {str(row.get("net")): int(row["pin"]) for row in contract["pins"]
                if str(row.get("net") or "")}
        defs = dict(re.findall(r"#define\s+HWV_PIN_([A-Za-z_][A-Za-z0-9_]*)\s+(\d+)",
                               header))
        check("1b. **每个网的名字宏值 == interface 里那个脚**（UART_TX=30, USB_DP=33）",
              defs.get("UART_TX") == str(nets["UART_TX"])
              and defs.get("USB_DP") == str(nets["USB_DP"]),
              f"UART_TX={defs.get('UART_TX')} USB_DP={defs.get('USB_DP')}")

        check("1c. `3V3` 这种以数字开头的网名被改名（NET_3V3），不生成非法标识符",
              "NET_3V3" in defs and "3V3" not in defs, str(sorted(defs)))

        # 同网多脚：GND 两个脚 → 宏只能有一个值，注释要写"还有哪些"
        gnd_line = next((line for line in header.splitlines()
                         if "HWV_PIN_GND" in line and "#define" in line), "")
        check("1d. **同网多脚只定义一次**，并在注释里写其余脚号（否则宏重定义 + 语义含糊）",
              gnd_line.count("HWV_PIN_GND") == 1 and "还有脚" in gnd_line,
              gnd_line.strip())

        gpio = dict(re.findall(r"#define\s+HWV_PIN_GPIO_(\d+)\s+(\d)", header))
        check("1e. 可用性：电源/地 → 0，信号 → 1",
              gpio.get("1") == "0" and gpio.get("8") == "0"
              and gpio.get("30") == "1" and gpio.get("33") == "1",
              f"1={gpio.get('1')} 8={gpio.get('8')} 30={gpio.get('30')} 33={gpio.get('33')}")
        check("1f. `HWV_MCU_PINS` 记的是契约里的引脚总数（48）",
              "#define HWV_MCU_PINS 48" in header)

        main = store.path("fw/b/main.c").read_text(encoding="utf-8")
        check("1g. 骨架**真的用**了契约宏（include + CHECK_GPIO + EXPECT）",
              '#include "hwv_pins.h"' in main and "HWV_CHECK_GPIO(" in main
              and "HWV_EXPECT(" in main,
              str([line.strip() for line in main.splitlines() if "HWV_" in line][:3]))
        return_contract = contract
        del return_contract
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 2. 真编译 + 负例（**负例编过 = 失败**）
# ---------------------------------------------------------------------------


def part_build() -> None:
    if not shutil.which("arm-none-eabi-gcc") and not fw_build._find_toolchain():
        print(_safe("[skip] 2. 没有 arm-none-eabi-gcc（不假装跑过）"))
        return
    store = DesignStore(PROJECT)
    try:
        store.delete()
        _prepare(store)
        result = fw_build.build(store, out_dir="fw/b")
        neg = result.get("negative") or {}
        check("2a. 骨架真编译过（Machine=ARM / Entry 在 FLASH / 能出 .bin）",
              result["status"] == "ok"
              and result["checks"]["machine"] == "ARM"
              and (result.get("bin") or "").endswith(".bin"),
              f"{result['status']} {result['checks']}")
        check("2b. **三条负例全部编不过**（编过就是闸门没挂上）",
              neg.get("status") == "ok" and neg.get("passed") == 3,
              f"{neg.get('passed')}/{neg.get('total')}")
        detail = {row["name"]: str(row.get("detail") or "") for row in neg.get("cases") or []}
        check("2c. 错误信息指向**真原因**（电源脚/超范围/未声明），不是无关 warning",
              "power/ground" in detail.get("power_as_gpio", "")
              and "out of MCU range" in detail.get("pin_out_of_range", "")
              and "undeclared" in detail.get("name_not_in_contract", ""),
              str({k: v[:70] for k, v in detail.items()}))
        check("2d. 编译期就把问题挡在 `.o` 之前（负例没生成 .o）",
              not store.path("fw/b/neg_power_as_gpio.o").is_file())
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 3. 退化：把闸门拆掉，负例必须变"编过"
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    if not (shutil.which("arm-none-eabi-gcc") or fw_build._find_toolchain()):
        print(_safe("[skip] 3. 没有工具链（退化验证跳过）"))
        return
    store = DesignStore(PROJECT)
    original = fw_build._pins_header
    try:
        store.delete()
        _prepare(store)

        def lax(interface: dict) -> str:  # noqa: ANN001
            text = original(interface)
            # 把所有"不可当 GPIO"改回 1 —— 等于把闸门拆了
            return re.sub(r"(#define\s+HWV_PIN_GPIO_\d+\s+)0", r"\g<1>1", text)

        fw_build._pins_header = lax  # type: ignore[assignment]
        try:
            design = _design()
            contract = iface_module.build(design)
            made = fw_build.generate(store, design, contract, out_dir="fw/b")
            store.write(fw_build.FILE, made)
            broken = fw_build.build(store, out_dir="fw/b")
        finally:
            fw_build._pins_header = original  # type: ignore[assignment]
        neg = broken.get("negative") or {}
        got = {row["name"]: row["got"] for row in neg.get("cases") or []}
        check("3. 退化：电源脚的 GPIO 位改成 1 → `power_as_gpio` 编过、整体变 failed",
              got.get("power_as_gpio") == "pass" and neg.get("status") == "failed"
              and broken["status"] == "failed",
              f"got={got} neg_status={neg.get('status')}")
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 4. 没有契约 → not_ready（不猜一套引脚出来）
# ---------------------------------------------------------------------------


def part_no_contract() -> None:
    store = DesignStore(PROJECT)
    try:
        store.delete()
        design = _design()
        store.save(design)
        # **必须显式给 out_dir**：第一版漏了，generate 落到默认 `fw/build`，
        # 断言去 `fw/b` 找 → 空字符串 → 假失败（教训：夹具路径也要和被测同源）
        made = fw_build.generate(store, design, {}, out_dir="fw/b")
        header = store.path("fw/b/hwv_pins.h").read_text(encoding="utf-8") \
            if store.path("fw/b/hwv_pins.h").is_file() else ""
        check("4. 没有 interface 时：头文件里没有可用的脚宏（不会凭空造一套）",
              made["status"] == "ok"
              and "HWV_PIN_GPIO_1" not in header
              and "#define HWV_MCU_PINS 0" in header,
              str([line for line in header.splitlines() if "MCU_PINS" in line]))
    finally:
        store.delete()


def main() -> int:
    print("=== P54 fw-build 接 interface.json（用错脚 = 编译期错误）===")
    part_header()
    part_build()
    part_degeneration()
    part_no_contract()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())