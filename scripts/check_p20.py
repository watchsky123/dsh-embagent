"""P20 验收：固件（引脚映射 / 驱动片段 / 项目骨架 / 交叉核对）。

    python -m dsh_embagent.scripts.check_p20

七条对应 `dsh-embagent-docs:docs/execution-plan-v3.md` §P20，第 7 条是**退化验证**。

## 夹具策略

* **合成 `.ioc`**：照 CubeMX 的真实写法手写一份（`PA15.Signal=GPIO_Output` 但
  **没有** `PA15.Mode=` 行——这正是实测踩到的坑）。用它保证验收不依赖
  用户目录里的文件。
* **真实 `.ioc`**（存在时）：`F401-Front.ioc` 额外跑一遍，证明解析器扛得住
  真文件的写法（`PC13-ANTI_TAMP`、`PH0 - OSC_IN` 这类带后缀的键）。
* **真编译器**：第 4 条用本机 gcc 做 `-fsyntax-only`。**不模拟编译**——
  模拟编译的断言永远为真。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design.fwgen import (  # noqa: E402
    drivers,
    mcus,
    netlist,
    pinmap,
    scaffold,
)

PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []

#: 真实 `.ioc` 的位置（存在才跑那条）。板名与工程名无交集，所以写死路径。
REAL_IOC = Path(r"C:/Users/<user>/Desktop/示例工作区/F401-Front-master/F401-Front.ioc")


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": str(detail)[:800]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {str(detail)[:800]}")
    return ok


# ---------------------------------------------------------------------------
# 合成 `.ioc`（照 CubeMX 真实写法）
# ---------------------------------------------------------------------------

_IOC = """\
#MicroXplorer Configuration settings - do not modify
Mcu.CPN=STM32F401RCT6
Mcu.Family=STM32F4
Mcu.IP0=I2C1
Mcu.IP1=USART2
Mcu.IP2=SYS
Mcu.IPNb=3
Mcu.Name=STM32F401R(B-C)Tx
Mcu.Package=LQFP64
Mcu.Pin0=PA2
Mcu.Pin1=PA3
Mcu.Pin2=PA13
Mcu.Pin3=PA14
Mcu.Pin4=PA15
Mcu.Pin5=PB6
Mcu.Pin6=PB7
Mcu.Pin7=PB9
Mcu.PinsNb=8
Mcu.UserName=STM32F401RCTx
PA13.Mode=Serial_Wire
PA13.Signal=SYS_JTMS-SWDIO
PA14.Mode=Serial_Wire
PA14.Signal=SYS_JTCK-SWCLK
PA15.GPIOParameters=PinState,GPIO_Label
PA15.GPIO_Label=VL53-PWR-EN
PA15.PinState=GPIO_PIN_RESET
PA15.Signal=GPIO_Output
PA2.Mode=Asynchronous
PA2.Signal=USART2_TX
PA3.Mode=Asynchronous
PA3.Signal=USART2_RX
PB6.Mode=I2C
PB6.Signal=I2C1_SCL
PB7.Mode=I2C
PB7.Signal=I2C1_SDA
PB9.GPIOParameters=GPIO_Label
PB9.GPIO_Label=LPN
PB9.Signal=GPIO_Output
"""

#: 一个用了 `.ioc` 里**没配**的 PA11 的固件。
_FW_GHOST = """\
#include "pins.h"
void main_fw(void) {
    /* 这一行是幽灵引脚：PA11 在 .ioc 里没有配置 */
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_11, 1);
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_15, 0);   /* 已配，正常使用 */
}
"""

#: 在 PA15（.ioc 配成 Output）上做读 → 方向冲突。
_FW_DIRECTION = """\
#include "pins.h"
void main_fw(void) {
    int level = HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_15);
    (void)level;
}
"""

#: 只出现引脚号、没有端口 → 记 unpaired，**不猜端口**。
_FW_UNPAIRED = """\
void main_fw(void) {
    uint16_t pins = GPIO_PIN_5 | GPIO_PIN_6;
    (void)pins;
}
"""

_FW_ARDUINO = """\
#include <Arduino.h>
void setup() { pinMode(PIN_LED, OUTPUT); }
void loop() { digitalWrite(PIN_LED, HIGH); delay(500); }
"""

_FW_AVR = """\
#include <avr/io.h>
int main(void) { DDRB |= _BV(PB5); PORTB |= _BV(PB5); for (;;) {} }
"""

_FW_IDF = """\
#include "driver/gpio.h"
void app_main(void) { gpio_set_level(4, 1); }
"""

#: 给普通 gcc 用的 HAL 替身：只补 `GPIO_PIN_n` 与 `GPIOx`。
_HAL_STUB = """\
#ifndef HWV_HAL_STUB_H
#define HWV_HAL_STUB_H
#define GPIOA 0u
#define GPIOB 1u
#define GPIOC 2u
#define GPIOD 3u
#define GPIOH 5u
/* 0..15 覆盖合成 .ioc 里的脚 */
#define GPIO_PIN_0 1u
#define GPIO_PIN_1 2u
#define GPIO_PIN_2 4u
#define GPIO_PIN_3 8u
#define GPIO_PIN_4 16u
#define GPIO_PIN_5 32u
#define GPIO_PIN_6 64u
#define GPIO_PIN_7 128u
#define GPIO_PIN_8 256u
#define GPIO_PIN_9 512u
#define GPIO_PIN_10 1024u
#define GPIO_PIN_11 2048u
#define GPIO_PIN_12 4096u
#define GPIO_PIN_13 8192u
#define GPIO_PIN_14 16384u
#define GPIO_PIN_15 32768u
#endif
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _gcc() -> str:
    for name in ("gcc", "cc", "clang"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _syntax_ok(compiler: str, include: Path, source: Path) -> tuple[bool, str]:
    proc = subprocess.run(  # noqa: S603
        [compiler, "-fsyntax-only", "-I", str(include), str(source)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return proc.returncode == 0, proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 1. `.ioc` → pins.h
# ---------------------------------------------------------------------------


def check_pins(tmp: Path) -> None:
    ioc = _write(tmp / "fixture.ioc", _IOC)
    pm, info = pinmap.from_ioc(ioc)
    if pm is None:
        check("1a. 合成 .ioc → PinMap（认出 MCU）", False, info.get("reason", ""))
        return
    header = pinmap.render_header(pm, "c", source="fixture.ioc", alias_prefix="HWV_")
    macros = [line for line in header.splitlines() if line.startswith("#define HWV_PIN_")]
    has_vl53 = any("HWV_PIN_VL53_PWR_EN" in m and "GPIO_PIN_15" in m for m in macros)
    has_usart = any("HWV_PIN_USART2_TX" in m and "GPIO_PIN_2" in m for m in macros)
    check("1a. 合成 .ioc → pins.h 含 VL53_PWR_EN 与 USART2_TX 宏（值是 HAL 字面量）",
          has_vl53 and has_usart,
          f"MCU={pm.mcu.name} 宏={len(macros)} 行；VL53={has_vl53} USART2_TX={has_usart}")

    # 实测坑：CubeMX 对 GPIO 输出**不写 .Mode 行**，只写 Signal/GPIO_Label。
    # 只看 Mode 会把 PA15/PB9 当成"没配置"漏掉。
    pa15 = next((a for a in pm.assignments if a.pin_name == "PA15"), None)
    check("1b. 无 `.Mode` 行、只有 `Signal=GPIO_Output` 的脚也算已配置",
          pa15 is not None and pa15.direction == "out",
          f"PA15={'缺' if pa15 is None else pa15.macro + '/' + pa15.direction} "
          f"（原始 .ioc 里没有 PA15.Mode 行）")

    # 调试脚标记出来，别让交叉核对称它们"闲置"
    reserved = info.get("reserved_debug") or []
    check("1c. SWD 调试脚（PA13/PA14）被标为 reserved，不算闲置 I/O",
          "PA13" in reserved and "PA14" in reserved,
          f"reserved={reserved}")

    # 真实 `.ioc`（存在才跑）：它带 `PC13-ANTI_TAMP` / `PH0 - OSC_IN` 这类键
    if REAL_IOC.is_file():
        real_pm, real_info = pinmap.from_ioc(REAL_IOC)
        ok = real_pm is not None and len(real_pm.assignments) >= 10
        labels = [a.macro for a in (real_pm.assignments if real_pm else [])
                  if a.macro in ("VL53_PWR_EN", "LPN", "I2C_RST")]
        check("1d. 真实 F401-Front.ioc：13 个配置脚、3 个人给的 label 都进宏",
              ok and len(labels) == 3,
              f"mcu={real_info.get('mcu')} assignments="
              f"{len(real_pm.assignments) if real_pm else 0} labels={labels}")
    else:
        check("1d. 真实 F401-Front.ioc", False, f"夹具不存在：{REAL_IOC}", status=SKIP)


# ---------------------------------------------------------------------------
# 2/3. 交叉核对
# ---------------------------------------------------------------------------


def check_crosscheck(tmp: Path) -> dict[str, Any]:
    ioc = _write(tmp / "cc.ioc", _IOC)
    pins = pinmap.parse_pins(ioc)
    fw_dir = tmp / "cc_fw"
    _write(fw_dir / "main.c", _FW_GHOST)
    _write(fw_dir / "unpaired.c", _FW_UNPAIRED)
    extracted = pinmap.extract_uses([fw_dir / "main.c", fw_dir / "unpaired.c"],
                                   root=tmp)
    report = pinmap.crosscheck(pins, extracted["uses"], source="cc.ioc")

    ghosts = [g for g in report["ghost"] if g["pin"] == "PA11"]
    idle_pins = {i["pin"] for i in report["idle"]}
    ghost_ok = (len(ghosts) == 1
                and "PA11" in ghosts[0]["why"]
                and re.search(r"main\.c:\d+$", ghosts[0]["firmware"]) is not None)
    check("2a. 合成固件用未配的 PA11 → 幽灵引脚，带 文件:行号",
          ghost_ok,
          f"ghost={report['ghost']}")

    check("2b. 配了但固件没用的 PB9 → 闲置（提示级，不拉低状态）",
          "PB9" in idle_pins and report["status"] == "fail",
          f"idle={sorted(idle_pins)} status={report['status']}"
          f"（fail 是幽灵引脚造成的，闲置本身不是 error）")

    check("2c. 只出现 GPIO_PIN_n 没有端口 → 记 unpaired，不猜端口",
          extracted.get("unpaired_count", 0) >= 2
          and all(u["pin"].startswith("?") for u in extracted.get("unpaired") or []),
          f"unpaired={extracted.get('unpaired')}")

    # 方向冲突：PA15 配 Output，固件读它
    _write(fw_dir / "dir.c", _FW_DIRECTION)
    extracted2 = pinmap.extract_uses([fw_dir / "dir.c"], root=tmp)
    report2 = pinmap.crosscheck(pins, extracted2["uses"], source="cc.ioc")
    conflict = [d for d in report2["direction"] if d["pin"] == "PA15"]
    check("3. 方向冲突：配成 Output 却被读 → error，带双方依据",
          len(conflict) == 1 and "Output" in conflict[0]["why"]
          and conflict[0].get("mode_basis") == "GPIO_Output",
          f"direction={report2['direction']}")
    return report


# ---------------------------------------------------------------------------
# 4. 改 .ioc → 重生成 → 编译失败于引用处（真 gcc）
# ---------------------------------------------------------------------------


def check_compile_break(tmp: Path) -> None:
    compiler = _gcc()
    if not compiler:
        check("4. 改 .ioc → 重生成 → 编译失败于引用处", False,
              "没有 C 编译器（gcc/cc/clang 都不在 PATH）", status=SKIP)
        return
    work = tmp / "compile"
    ioc = _write(work / "board.ioc", _IOC)
    pm, _info = pinmap.from_ioc(ioc)
    if pm is None:
        check("4. 改 .ioc → 重生成 → 编译失败于引用处", False, "PinMap 建不出来")
        return
    inc = work / "inc"
    _write(inc / "pins.h", pinmap.render_header(pm, "c", source="board.ioc",
                                                 alias_prefix="HWV_"))
    _write(inc / "hal_stub.h", _HAL_STUB)
    consumer = _write(work / "use_pin.c", """\
#include "hal_stub.h"
#include "pins.h"
int use_pin = HWV_PIN_VL53_PWR_EN;
""")
    before_ok, before_out = _syntax_ok(compiler, inc, consumer)

    # 改 .ioc：把 label 从 VL53-PWR-EN 改成 VL53-PWR-EN2 → 宏名跟着变
    changed = _IOC.replace("PA15.GPIO_Label=VL53-PWR-EN", "PA15.GPIO_Label=VL53-PWR-EN2")
    ioc2 = _write(work / "board2.ioc", changed)
    pm2, _ = pinmap.from_ioc(ioc2)
    after_header = pinmap.render_header(pm2, "c", source="board2.ioc", alias_prefix="HWV_")
    _write(inc / "pins.h", after_header)
    after_ok, after_out = _syntax_ok(compiler, inc, consumer)
    mentions = "HWV_PIN_VL53_PWR_EN" in after_out and "undeclared" in after_out.lower()
    check("4. 改 .ioc 的 label → 重生成 pins.h → 引用处编译失败（真 gcc -fsyntax-only）",
          before_ok and (not after_ok) and mentions,
          f"改之前编译={before_ok}；改之后编译={after_ok}；"
          f"错误提到旧宏={mentions}｜{after_out.strip().splitlines()[0][:160] if after_out.strip() else ''}")


# ---------------------------------------------------------------------------
# 5. 驱动片段
# ---------------------------------------------------------------------------


def check_drivers() -> None:
    parts = drivers.supported_parts()
    check("5a. 驱动表 7 个器件", len(parts) == 7, f"parts={parts}")
    files = drivers.generate_driver("SHT31")
    header = files.get("sht31.h", "")
    source = files.get("sht31.c", "")
    has_addr = "0x44" in header or "0x44" in source
    has_cmd = "0x2400" in source or "0x2400" in header
    has_crc = "crc" in source.lower()
    check("5b. SHT31 → init/read C 代码，含 I2C 地址 0x44 与命令 0x2400（含 CRC-8）",
          has_addr and has_cmd and has_crc,
          f"h={len(header.splitlines())} 行 c={len(source.splitlines())} 行 "
          f"地址={has_addr} 命令={has_cmd} crc={has_crc}")
    try:
        drivers.generate_driver("NOT_A_PART")
        unknown_ok = False
        detail = "竟然生成出来了"
    except KeyError:
        unknown_ok = True
        detail = "KeyError → CLI 翻成 not_ready"
    check("5c. 表外器件不生成半成品代码", unknown_ok, detail)


# ---------------------------------------------------------------------------
# 6. 5 个 target 都能出骨架
# ---------------------------------------------------------------------------


def _fake_map(mcu_key: str) -> pinmap.PinMap:
    mcu = mcus.MCUS[mcu_key]
    axes = []
    if mcu.family == "stm32":
        axes = [
            pinmap.PinAssignment("LED", "LED", "U1", "PA5", "PA5", "GPIO", "out",
                                 port="A", bit=5, number=5, value="GPIO_PIN_5"),
            pinmap.PinAssignment("BTN", "BTN", "U1", "PB3", "PB3", "GPIO", "in",
                                 port="B", bit=3, number=19, value="GPIO_PIN_3"),
        ]
    elif mcu.family == "avr":
        axes = [
            pinmap.PinAssignment("LED", "LED", "U1", "5", "PB0", "GPIO", "out",
                                 port="B", bit=0, number=0),
            pinmap.PinAssignment("BTN", "BTN", "U1", "7", "PB2", "GPIO", "in",
                                 port="B", bit=2, number=2),
        ]
    else:
        axes = [
            pinmap.PinAssignment("LED", "LED", "U1", "4", "IO4", "GPIO", "out",
                                 number=4),
            pinmap.PinAssignment("BTN", "BTN", "U1", "5", "IO5", "GPIO", "in",
                                 number=5),
        ]
    return pinmap.PinMap(mcu, "U1", axes, source="fixture")


def check_scaffold(tmp: Path) -> None:
    compat = {"avr": "attiny85", "arduino": "attiny85", "platformio": "esp32s3",
              "esp-idf": "esp32s3", "stm32": "stm32f401rc"}
    results: dict[str, Any] = {}
    ok = True
    for target, mcu_key in compat.items():
        dest = tmp / "sc" / target
        try:
            files = scaffold.scaffold(_fake_map(mcu_key), dest, target, "fixture")
            results[target] = {"files": len(files), "ok": bool(files)}
            ok = ok and bool(files)
        except scaffold.ScaffoldError as exc:
            results[target] = {"error": str(exc)}
            ok = False
    check("6a. 5 个 target 都出骨架（arduino/platformio/esp-idf/stm32/avr）",
          ok, json.dumps(results, ensure_ascii=False))

    # target 与 MCU 家族不匹配 → 明确拒绝，不是生成一个跑不起来的骨架
    try:
        scaffold.scaffold(_fake_map("stm32f401rc"), tmp / "sc" / "bad", "arduino")
        mismatch_ok = False
        detail = "竟然生成了"
    except scaffold.ScaffoldError as exc:
        mismatch_ok = True
        detail = str(exc)
    check("6b. target 与 MCU 家族不匹配 → 拒绝（不生成跑不起来的骨架）",
          mismatch_ok, detail)

    # 骨架里的 pins.h 与 pinmap-gen 是同一套写法（含 HWV_ 别名）
    stm32_pins = (tmp / "sc" / "stm32" / "pins.h")
    text = stm32_pins.read_text(encoding="utf-8") if stm32_pins.is_file() else ""
    check("6c. 骨架的 pins.h 带 HWV_ 别名（与 pinmap-gen 同一套事实）",
          "HWV_PIN_LED" in text and "PIN_LED_PORT" in text,
          f"含 HWV_PIN_LED={'HWV_PIN_LED' in text} 含 PIN_LED_PORT={'PIN_LED_PORT' in text}")


# ---------------------------------------------------------------------------
# 7. 非 STM32 SDK → not_ready
# ---------------------------------------------------------------------------


def check_foreign(tmp: Path) -> None:
    cases = {"Arduino": _FW_ARDUINO, "AVR": _FW_AVR, "ESP-IDF": _FW_IDF}
    got: dict[str, Any] = {}
    for sdk, text in cases.items():
        path = _write(tmp / "foreign" / f"{sdk.lower().replace('-', '')}.c", text)
        result = pinmap.extract_uses([path], root=tmp)
        got[sdk] = {"status": result["status"],
                    "sdk": sorted(result.get("foreign_sdk") or {})}
    ok = all(got[sdk]["status"] == "not_ready" and sdk in got[sdk]["sdk"]
             for sdk in cases)
    check("7. 非 STM32 固件（Arduino/AVR/ESP-IDF）→ not_ready 并点名 SDK，不猜",
          ok, json.dumps(got, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 附加：7 个 MCU 与型号识别
# ---------------------------------------------------------------------------


def check_mcus(tmp: Path) -> None:
    check("8a. MCU 表 3 → 7（补上 7 个 .ioc 里真实出现的型号）",
          len(mcus.MCUS) == 7, f"keys={list(mcus.MCUS)}")
    cases = {"STM32F103C6Tx": "stm32f103c6", "STM32F103C8Tx": "stm32f103",
             "STM32F401RCT6": "stm32f401rc", "STM32F405RGT6": "stm32f405rg",
             "STM32H743IIT6": "stm32h743ii", "ATtiny85": "attiny85",
             "ESP32-S3-WROOM-1": "esp32s3"}
    bad = {text: (mcus.find_mcu(text).key if mcus.find_mcu(text) else None)
           for text, key in cases.items()
           if mcus.find_mcu(text) is None or mcus.find_mcu(text).key != key}
    check("8b. C6/C8 不混淆（C6 必须排在通用 f103 前面）", not bad,
          f"错的={bad}" if bad else "7 个型号全部识别到正确的 key")

    # 认不出 MCU → not_ready，不猜
    unknown = _IOC.replace("Mcu.UserName=STM32F401RCTx", "Mcu.UserName=ATMEGA2560")
    unknown = unknown.replace("Mcu.CPN=STM32F401RCT6", "Mcu.CPN=ATMEGA2560")
    path = _write(tmp / "unknown.ioc", unknown)
    pm, info = pinmap.from_ioc(path)
    check("8c. `.ioc` 的 MCU 认不出 → 返回 None + 原因，不猜型号",
          pm is None and "认不出 MCU 型号" in str(info.get("reason")),
          str(info.get("reason") or "")[:160])


# ---------------------------------------------------------------------------
# 10. CLI 端到端（临时工作区，不碰用户工程）
# ---------------------------------------------------------------------------


def check_cli(tmp: Path) -> None:
    """`pinmap-gen` / `pinmap-check` / `fw-scaffold` / `fw-driver` 各跑一次。

    用 `HWV_WORKSPACE` 把工作区指到临时目录——**验收不往用户的工程里写东西**
    （AGENTS §4.4）。这里测的是 CLI 接线，模块本身在 1–9 条里已经测过。
    """
    from dsh_embagent.design.model import Board, DesignModel, Part, Placement
    from dsh_embagent.design.store import DesignStore

    workspace = tmp / "ws"
    demo = workspace / "demo"
    demo.mkdir(parents=True, exist_ok=True)
    design = DesignModel(title="p20-cli")
    design.board = Board(kind="generic", name="demo",
                         outline=[(0, 0), (40, 0), (40, 30)], size_mm={"w": 40, "h": 30})
    design.add_part(Part(ref="U1", kind="mcu", label="STM32F401RCT6",
                         lcsc="C89852", physical_source="datasheet", pins=["1"]))
    design.add_part(Part(ref="D1", kind="led", physical_source="estimated", pins=["1"]))
    design.connect("VL53-PWR-EN", ("U1", "PA15"), ("D1", "1"))
    old_env = os.environ.get("HWV_WORKSPACE")
    os.environ["HWV_WORKSPACE"] = str(workspace)
    try:
        store = DesignStore("demo")
        store.save(design)
    finally:
        if old_env is None:
            os.environ.pop("HWV_WORKSPACE", None)
        else:
            os.environ["HWV_WORKSPACE"] = old_env
    _write(demo / "board.ioc", _IOC)
    _write(demo / "src" / "main.c", _FW_GHOST)

    def run(*argv: str) -> dict[str, Any]:
        env = {**os.environ, "HWV_WORKSPACE": str(workspace)}
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "dsh_embagent.design_cli", *argv],
            cwd=str(Path(__file__).resolve().parents[2]),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, check=False)
        text = proc.stdout.decode("utf-8", errors="replace").strip()
        try:
            return json.loads(text.splitlines()[-1])
        except (ValueError, IndexError):
            return {"status": "failed", "findings": [text[-300:]]}

    gen = run("pinmap-gen", "demo", "--ioc=board.ioc")
    header = demo / "firmware" / "pins.h"
    check("10a. CLI pinmap-gen：写出 pins.h 且含 label 宏",
          gen.get("status") == "ok" and header.is_file()
          and "HWV_PIN_VL53_PWR_EN" in header.read_text(encoding="utf-8"),
          f"status={gen.get('status')} header={header.name} "
          f"assignments={gen.get('assignments')} findings={gen.get('findings')}")

    chk = run("pinmap-check", "demo", "--ioc=board.ioc", "--src=src")
    counts = chk.get("counts") or {}
    check("10b. CLI pinmap-check：幽灵引脚让状态变 failed 并落盘",
          chk.get("status") == "failed" and counts.get("ghost", 0) >= 1
          and (demo / "pinmap_crosscheck.json").is_file(),
          f"status={chk.get('status')} counts={counts} findings={chk.get('findings')}")

    sc = run("fw-scaffold", "demo", "--ioc=board.ioc", "--target=stm32")
    out_dir = Path(str(sc.get("out") or ""))
    check("10c. CLI fw-scaffold：stm32 骨架落盘",
          sc.get("status") == "ok" and sc.get("count", 0) > 0
          and (out_dir / "main.c").is_file(),
          f"status={sc.get('status')} files={sc.get('count')} out={out_dir.name} "
          f"findings={sc.get('findings')}")

    # fw-driver 不属于任何工程，所以 --out 给绝对路径（相对路径会落到 cwd）
    drv = run("fw-driver", "SHT31", f"--out={demo / 'firmware'}")
    check("10d. CLI fw-driver：生成 sht31.c/.h",
          drv.get("status") == "ok"
          and (demo / "firmware" / "sht31.c").is_file()
          and (demo / "firmware" / "sht31.h").is_file(),
          f"status={drv.get('status')} files={drv.get('files')}")


# ---------------------------------------------------------------------------
# 9. 退化验证
# ---------------------------------------------------------------------------


def check_degradation(tmp: Path) -> None:
    ioc = _write(tmp / "deg.ioc", _IOC)
    pins = pinmap.parse_pins(ioc)
    fw = _write(tmp / "deg_fw" / "main.c", _FW_GHOST)
    uses = pinmap.extract_uses([fw], root=tmp)["uses"]
    strict = pinmap.crosscheck(pins, uses, source="deg.ioc")

    saved = pinmap.crosscheck
    try:
        # 退化：幽灵引脚只当提示，不拉低状态
        def loose(pins_arg, uses_arg, *, source=""):
            report = dict(saved(pins_arg, uses_arg, source=source))
            report["status"] = "ok"          # ← 把 error 降成提示
            return report
        pinmap.crosscheck = loose  # type: ignore[assignment]
        degraded = pinmap.crosscheck(pins, uses, source="deg.ioc")
    finally:
        pinmap.crosscheck = saved  # type: ignore[assignment]

    ok = (strict["status"] == "fail" and bool(strict["ghost"])
          and degraded["status"] == "ok")
    check("9. 退化验证：幽灵引脚降为提示 → 验收 2 的判据变红（证明它承重）",
          ok, f"严格={strict['status']}（{len(strict['ghost'])} 幽灵）；"
              f"降级后={degraded['status']}")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P20 验收：固件（引脚映射 / 驱动 / 骨架 / 交叉核对）===\n")
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p20-"))
    try:
        if only in ("", "pins"):
            check_pins(tmp)
        if only in ("", "crosscheck"):
            check_crosscheck(tmp)
        if only in ("", "compile"):
            check_compile_break(tmp)
        if only in ("", "drivers"):
            check_drivers()
        if only in ("", "scaffold"):
            check_scaffold(tmp)
        if only in ("", "foreign"):
            check_foreign(tmp)
        if only in ("", "mcus"):
            check_mcus(tmp)
        if only in ("", "degrade"):
            check_degradation(tmp)
        if only in ("", "cli"):
            check_cli(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for r in _RESULTS if r["status"] == PASS)
    failed = sum(1 for r in _RESULTS if r["status"] == FAIL)
    skipped = sum(1 for r in _RESULTS if r["status"] not in (PASS, FAIL))
    status = "ok" if failed == 0 and skipped == 0 else ("failed" if failed else "not_ready")
    print(f"\n=== {passed} 通过 / {failed} 失败 / {skipped} 未做到 ===")
    payload = {"status": status, "check": "P20", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())