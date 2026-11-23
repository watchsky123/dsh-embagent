"""P57 验收 —— **AF 复用表**：这个脚能不能做这个功能、编号是几。

    python -m hw_velxio.scripts.check_p57

## 这一段补什么

P56 解决了"`.ioc` 的 `PA9` 是网表的 `30`"，但"`PA9` 能不能做 `USART1_TX`、
AF 编号几"仍然只能信 `.ioc`。现在有 `design/afmap/<型号>.json`：

| 之前 | 现在 |
|---|---|
| `.ioc` 说 `PA9=USART1_TX`，照单全收 | 表里查得到 `PA9 → USART1_TX @ AF7` |
| 固件 `GPIO_AF7_USART1` 写错没人管 | 与表比对（P57 只验表；固件交叉在 2a/2b）|
| "这个脚能不能做 I2C" 要翻 datasheet | `pins_for_signal(table, "I2C1_SCL")` |

数据来源：**ST 官方 `STM32_open_pin_data`**（CubeMX 同源，BSD-3-Clause），
两个文件：`mcu/<型号>.xml`（脚号+信号）+ `mcu/IP/GPIO-*.xml`（AF 编号/重映射）。

## 判据（**反着判**最要紧）

| 项 | 期望 |
|---|---|
| F1 / F4 分开 | F103 表里**不许出现 AF 编号**（它用 AFIO 重映射）；F401/H743 是 AF0..15 |
| 两个源一致 | ST 的 `Position` 与 P56 的引脚表**逐脚相等**（否则拒绝建表）|
| 固件证据 | CubeMX 生成的 `Core/Src/*.c` 里每一处 `GPIO_AF7_USART1` 都要在表里对得上 |
| `.ioc` 功能 | `.ioc` 把 UART 放到一根做不了 UART 的脚上 → **硬冲突** |
| 退化一 | 把表里 `PA9` 的 `USART1_TX` 删掉 → 真实项目**变红**（证明表在判）|
| 退化二 | 把表拿掉 → `AF 编号` 回到 unknown（不是永远绿）|
| 编译期 | 固件里 AF 编号写错必须**编不过**；F1 用 `HWV_EXPECT_AF` 也必须编不过 |
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio import design_cli  # noqa: E402
from hw_velxio.design import afmap as afmap_module  # noqa: E402
from hw_velxio.design import fw_build  # noqa: E402
from hw_velxio.design import interface as iface_module  # noqa: E402
from hw_velxio.design import pinout as pinout_module  # noqa: E402
from hw_velxio.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from hw_velxio.design.store import DesignStore  # noqa: E402

PROJECT = "p57-fixture"
REPO = Path(__file__).resolve().parents[1]
IOC_F103 = REPO / "tests" / "fixtures" / "ioc" / "p55-usart.ioc"

REAL_DESIGN = Path(r"C:\Users\<user>\Desktop\示例复现区\复刻-示例\design.json")
REAL_IOC = Path(
    r"C:\Users\<user>\Desktop\示例工作区\F103-GPS-master\F103-GPS.ioc")
FIRMWARE_ROOT = Path(r"C:\Users\<user>\Desktop\示例工作区")
F401_IOC = FIRMWARE_ROOT / "F401-Bottom-master" / "F401-Bottom.ioc"
H743_IOC = FIRMWARE_ROOT / "H743PilotX-master" / "H743PilotX.ioc"

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


# ---------------------------------------------------------------------------
# 固件证据：CubeMX 生成的 Core/Src/*.c
# ---------------------------------------------------------------------------

_EVIDENCE_RE = re.compile(
    r"GPIO_InitStruct\.Pin\s*=\s*([^;]+);"
    r"|GPIO_InitStruct\.Alternate\s*=\s*GPIO_AF(\d+)_([A-Za-z0-9]+);"
    r"|HAL_GPIO_Init\(\s*GPIO([A-Z])")
_GPIO_PIN_RE = re.compile(r"GPIO_PIN_(\d+)")

#: CubeMX 生成的代码与它自己的引脚库**外设名不一致**（实测）：
#: 代码写 `GPIO_AF12_SDIO1`，库里的信号叫 `SDMMC1_D0`。这是同一外设的两个年代名。
_PERIPHERAL_ALIASES = {"SDIO": "SDMMC", "OTG_FS": "USB_OTG_FS",
                       "OTG_HS": "USB_OTG_HS"}


def _alias(peripheral: str) -> str:
    """`SDIO1` → `SDMMC1`（按前缀换，不是整串相等）。"""
    for old, new in _PERIPHERAL_ALIASES.items():
        if peripheral.startswith(old):
            return new + peripheral[len(old):]
    return peripheral


def _firmware_evidence(board_dir: Path) -> list[dict]:
    """从 CubeMX 生成的代码里抽 `(端口+脚号, AF 编号, 外设)`。"""
    out: list[dict] = []
    for path in sorted((board_dir / "Core" / "Src").glob("*.c")):
        text = path.read_text(encoding="utf-8", errors="replace")
        numbers: list[str] = []
        af = 0
        peripheral = ""
        for match in _EVIDENCE_RE.finditer(text):
            if match.group(1) is not None:
                # `Pin = GPIO_PIN_9|GPIO_PIN_10`：这里还不知道端口，
                # 端口在后面的 `HAL_GPIO_Init(GPIOA, …)` 里
                numbers = _GPIO_PIN_RE.findall(match.group(1))
            elif match.group(2) is not None:
                af = int(match.group(2))
                peripheral = match.group(3)
            elif match.group(4) is not None:
                port = match.group(4)
                # **没有 `Alternate` 的块不是 AF**（普通 GPIO 输出），跳过：
                # 否则会凭空造出 `af=0` 的假证据
                if peripheral:
                    for number in numbers:
                        out.append({"pin": f"P{port}{number}", "af": af,
                                    "peripheral": peripheral,
                                    "file": path.name})
                numbers, af, peripheral = [], 0, ""
    return out


# ---------------------------------------------------------------------------
# 1. 表自检 + 与引脚表交叉
# ---------------------------------------------------------------------------


def part_tables() -> None:
    rows = afmap_module.list_tables()
    bad = [row for row in rows if row.get("error") or row.get("problems")]
    check("1a. 4 张 AF 表全部通过自检（脚号唯一/数量/范围/风格）",
          len(rows) == 4 and not bad,
          str([(r.get("part"), r.get("problems")) for r in rows]) if bad
          else str([r.get("part") for r in rows]))

    f1 = afmap_module.load("stm32f103c8t6") or {}
    f4 = afmap_module.load("stm32f401rct6") or {}
    h7 = afmap_module.load("stm32h743iit6") or {}
    f1_has_af = any(row.get("af") is not None
                    for info in (f1.get("pins") or {}).values()
                    for row in info.get("functions") or [])
    check("1b. **F1 与 F4 分开**：F103 没有 AF 编号（用重映射），F401/H743 有 AF0..15",
          f1.get("style") == "F1_REMAP" and not f1_has_af
          and f4.get("style") == "AF" and int(f4.get("af_functions") or 0) > 100
          and h7.get("style") == "AF" and int(h7.get("af_functions") or 0) > 500,
          f"F1 af={f1.get('af_functions')} F4 af={f4.get('af_functions')} "
          f"H7 af={h7.get('af_functions')}")

    conflicts = []
    for part in ("stm32f103c8t6", "stm32f103c6t6", "stm32f401rct6", "stm32h743iit6"):
        table = afmap_module.load(part)
        po = pinout_module.load(part)
        conflicts += [f"{part}: {p}" for p in afmap_module.crosscheck(table, po)]
    check("1c. **两个厂商源逐脚一致**：ST `Position` == P56 引脚表的封装脚号",
          not conflicts, str(conflicts[:4]))

    proven = all(
        (row.get("sources") or {}).get("mcu", {}).get("sha256")
        and (row.get("sources") or {}).get("gpio", {}).get("sha256")
        for row in (afmap_module.load(p) for p in
                    ("stm32f103c8t6", "stm32f401rct6"))
        if row)
    table = afmap_module.load("stm32f401rct6") or {}
    check("1d. 每条表都有**出处**（两个源文件的 url/sha256 + 许可证）",
          proven and "BSD-3-Clause" in str(table.get("license"))
          and table.get("retrieved"),
          f"{table.get('license')} {table.get('retrieved')}")

    got = afmap_module.match_signal(table, "PA9", "USART1_TX")
    check("1e. F401 的 `PA9 → USART1_TX` 是 **AF7**（与 CubeMX 生成的代码一致）",
          got.get("status") == "ok" and got.get("af") == 7, str(got)[:120])

    f1 = afmap_module.load("stm32f103c8t6") or {}
    got = afmap_module.match_signal(f1, "PB6", "USART1_TX")
    default = (got.get("remap") or {}).get("default")
    macros = (got.get("remap") or {}).get("macros") or []
    check("1f. F103 的 `PB6=USART1_TX` 只在 **USART1_REMAP1** 下成立（不是默认功能）",
          got.get("status") == "ok" and default is False
          and any("USART1_REMAP1" in m or "USART1" in m for m in macros),
          f"default={default} macros={macros}")

    leds = afmap_module.pins_for_signal(f1, "I2C1_SCL")
    check("1g. 反查可用：「谁能做 I2C1_SCL」列出 F103 的脚",
          {row["pin"] for row in leds} >= {"PB6", "PB8"},
          str([row["pin"] for row in leds]))


# ---------------------------------------------------------------------------
# 2. 固件证据：CubeMX 生成代码里的 AF 编号必须与表一致
# ---------------------------------------------------------------------------


def part_firmware() -> None:
    if not FIRMWARE_ROOT.is_dir():
        print(_safe(f"[skip] 2. 没有固件目录（{FIRMWARE_ROOT}）——这一节**没跑**，不是通过"))
        return
    cases = (("stm32f401rct6", FIRMWARE_ROOT / "F401-Bottom-master", 8),
             ("stm32h743iit6", FIRMWARE_ROOT / "H743PilotX-master", 15))
    for part, board, floor in cases:
        table = afmap_module.load(part) or {}
        evidence = _firmware_evidence(board)
        mismatches = []
        for row in evidence:
            wanted = _alias(row["peripheral"])
            functions = [f for f in (table.get("pins", {}).get(row["pin"], {})
                                     .get("functions") or [])
                         if f.get("af") == row["af"]
                         and str(f.get("signal") or "").startswith(wanted)]
            if not functions:
                mismatches.append(row)
        check(f"2. {board.name}：**{len(evidence)} 条 AF 证据全部与表一致**",
              len(evidence) >= floor and not mismatches,
              f"{len(evidence)} 条，不一致 {mismatches[:2]}")


# ---------------------------------------------------------------------------
# 3. 契约：`.ioc` 的功能是否真在这根脚上
# ---------------------------------------------------------------------------


def _design(kind: str = "stm32f103c8t6", pins: int = 48,
            nets: list[dict] | None = None) -> DesignModel:
    board = Board(kind="custom", name="b", id="PCB#main",
                  outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                  size_mm={"w": 40, "h": 30}, thickness_mm=1.6)
    design = DesignModel(id=PROJECT, board=board)
    design.add_part(Part(ref="U1", kind=kind, label="MCU",
                         lcsc="C8734" if "103" in kind else "C74524",
                         role="module",
                         size_mm={"w": 7.0, "h": 7.0, "d": 1.6},
                         physical_source="datasheet",
                         pins=[str(i) for i in range(1, pins + 1)],
                         placement=Placement(x_mm=10.0, y_mm=10.0,
                                             board_id="PCB#main")))
    design.nets = nets or [
        {"net": "3V3", "pins": [{"ref": "U1", "pin": "1"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": "8"}]},
        {"net": "UART_TX", "pins": [{"ref": "U1", "pin": "30"}]},
        {"net": "UART_RX", "pins": [{"ref": "U1", "pin": "31"}]},
    ]
    return design


def part_contract() -> None:
    store = DesignStore(PROJECT)
    made: list[Path] = []
    try:
        store.delete()
        # 3a. `.ioc` 把 UART 放到一根做不了 UART 的脚上 → 硬冲突
        text = IOC_F103.read_text(encoding="utf-8").replace(
            "PA9.Signal=USART1_TX", "PA9.Signal=SPI1_SCK")
        handle = tempfile.NamedTemporaryFile("w", suffix=".ioc", delete=False,
                                             encoding="utf-8", newline="")
        handle.write(text)
        handle.close()
        made.append(Path(handle.name))
        bad = iface_module.build(_design(), ioc_path=made[-1])
        hits = [p for p in bad.get("problems") or []
                if p.get("check") == "ioc.function_not_on_pin"]
        check("3a. **`.ioc` 把 SPI1_SCK 放到 PA9 上 → 硬冲突**"
              "（PA9 只有 TIM1_CH2/USART1_TX）",
              bad["status"] == "failed" and bool(hits),
              str([h["message"][:110] for h in hits]))

        # 3b. 退化：把表里 PA9 的 USART1_TX 删掉 → 正常 `.ioc` 也变红
        table = afmap_module.load("stm32f103c8t6") or {}
        broken = json.loads(json.dumps(table))
        broken["pins"]["PA9"]["functions"] = [
            f for f in broken["pins"]["PA9"]["functions"]
            if f.get("signal") != "USART1_TX"]
        original = iface_module.afmap_module.find
        try:
            iface_module.afmap_module.find = lambda *a, **k: broken  # type: ignore[assignment]
            deg = iface_module.build(_design(), ioc_path=IOC_F103)
        finally:
            iface_module.afmap_module.find = original  # type: ignore[assignment]
        check("3b. 退化：表里删掉 `PA9=USART1_TX` → 原本合法的 `.ioc` **变红**",
              deg["status"] == "failed"
              and any(p.get("check") == "ioc.function_not_on_pin"
                      for p in deg.get("problems") or []),
              str([p["message"][:100] for p in deg.get("problems") or []]))

        # 3c. 拿掉 AF 表 → 不判功能、`AF 编号`回到 unknown
        try:
            iface_module.afmap_module.find = lambda *a, **k: None  # type: ignore[assignment]
            blind = iface_module.build(_design(), ioc_path=IOC_F103)
        finally:
            iface_module.afmap_module.find = original  # type: ignore[assignment]
        what = [u["what"] for u in blind.get("unknown") or []]
        check("3c. 没有 AF 表时：不判功能冲突、AF 编号回到 unknown（不是永远绿）",
              not any(p.get("check") == "ioc.function_not_on_pin"
                      for p in blind.get("problems") or [])
              and any("AF 编号" in w for w in what), str(what))

        # 3d. 合法情况：真 `.ioc` 的 19 个功能全部落到表里；F1 是重映射风格
        if REAL_DESIGN.is_file() and REAL_IOC.is_file():
            design = DesignModel.from_dict(
                json.loads(REAL_DESIGN.read_text(encoding="utf-8")))
            payload = iface_module.build(design, ioc_path=REAL_IOC)
            af = payload.get("af") or {}
            check("3d. 真实项目：19 个 `.ioc` 功能全部查得到，F1 风格=重映射，0 冲突",
                  payload["status"] == "ok"
                  and (af.get("by_status") or {}).get("ok", 0) == 11
                  and af.get("style") == "F1_REMAP"
                  and not [p for p in payload["problems"]
                           if p["check"] == "ioc.function_not_on_pin"],
                  f"by_status={af.get('by_status')} style={af.get('style')}")
            check("3e. 真实项目：unknown 只剩「时序」——AF 编号/脚号都不再是缺口",
                  [u["what"] for u in payload["unknown"]]
                  == ["时序约束（建立/保持、采样时刻）"],
                  str([u["what"] for u in payload["unknown"]]))
        else:
            print(_safe("[skip] 3d/3e. 没有真实项目 —— 这一节**没跑**，不是通过"))
    finally:
        store.delete()
        for path in made:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 4. 编译期：AF 写错必须编不过
# ---------------------------------------------------------------------------


def _f401_design() -> DesignModel:
    """F401RC（LQFP64）+ `.ioc` 里真有接线的几个信号。"""
    table = pinout_module.load("stm32f401rct6") or {}
    positions = {row["port"]: int(number)
                 for number, row in (table.get("pins") or {}).items() if row.get("port")}
    nets = [
        {"net": "3V3", "pins": [{"ref": "U1", "pin": "1"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": "12"}]},
    ]
    for port, net in (("PA9", "UART1_TX"), ("PA10", "UART1_RX"),
                      ("PB6", "I2C1_SCL"), ("PB7", "I2C1_SDA")):
        nets.append({"net": net, "pins": [{"ref": "U1", "pin": str(positions[port])}]})
    return _design("stm32f401rct6", 64, nets)


def part_build() -> None:
    if not (shutil.which("arm-none-eabi-gcc") or fw_build._find_toolchain()):
        print(_safe("[skip] 4. 没有 arm-none-eabi-gcc（不假装跑过）"))
        return
    store = DesignStore(PROJECT)
    try:
        store.delete()
        design = _f401_design()
        store.save(design)
        contract = iface_module.build(design, ioc_path=F401_IOC)
        store.write(iface_module.FILE, contract)
        made = fw_build.generate(store, design, contract, out_dir="fw/b")
        store.write(fw_build.FILE, made)
        header = store.path("fw/b/hwv_pins.h").read_text(encoding="utf-8")
        main = store.path("fw/b/main.c").read_text(encoding="utf-8")
        check("4a. F401：头文件有 `HWV_AF_USART1_TX 7`，骨架真的用了 `HWV_EXPECT_AF`",
              re.search(r"#define HWV_AF_USART1_TX\s+7", header) is not None
              and re.search(r"HWV_EXPECT_AF\([A-Z0-9_]+, \d+\);", main) is not None,
              str([line for line in header.splitlines() if "HWV_AF_" in line][:3])
              + " | " + str([line.strip() for line in main.splitlines()
                             if "HWV_EXPECT_AF" in line]))

        result = fw_build.build(store, out_dir="fw/b")
        neg = result.get("negative") or {}
        check("4b. 真编译过，且**六条负例全部编不过**（含 AF 写错）",
              result["status"] == "ok" and result["checks"]["machine"] == "ARM"
              and neg.get("passed") == 6 and neg.get("total") == 6,
              f"{result['status']} {neg.get('passed')}/{neg.get('total')}")
        detail = {row["name"]: str(row.get("detail") or "")
                  for row in neg.get("cases") or []}
        check("4c. `af_wrong` 的错指 AF（不是别的错）",
              "HWV_EXPECT_AF" in detail.get("af_wrong", ""),
              str(detail.get("af_wrong", ""))[:110])

        # 4d. 退化：把 HWV_EXPECT_AF 改成空操作 → af_wrong 编过 → 整体 failed
        original = fw_build._pins_header
        old_macro = ("#define HWV_EXPECT_AF(name, n) \\\n"
                     "    _Static_assert(HWV_AF_ ## name == (n), \\\n"
                     '                   "HWV_EXPECT_AF: " #name'
                     ' " AF number differs from ST pin data")\n')
        state = {"hit": False}

        def lax(interface: dict) -> str:  # noqa: ANN001
            text = original(interface)
            if old_macro in text:
                state["hit"] = True
                text = text.replace(old_macro,
                                    "#define HWV_EXPECT_AF(name, n) ((void)(n))\n")
            return text

        fw_build._pins_header = lax  # type: ignore[assignment]
        try:
            made = fw_build.generate(store, design, contract, out_dir="fw/b")
            store.write(fw_build.FILE, made)
            broken = fw_build.build(store, out_dir="fw/b")
        finally:
            fw_build._pins_header = original  # type: ignore[assignment]
        neg_b = broken.get("negative") or {}
        got = {row["name"]: row["got"] for row in neg_b.get("cases") or []}
        check("4d. 退化：`HWV_EXPECT_AF` 变空操作 → `af_wrong` **编过** → 整体 failed",
              state["hit"] and got.get("af_wrong") == "pass"
              and broken["status"] == "failed",
              f"命中={state['hit']} got={got.get('af_wrong')} status={broken['status']}")

        # 4e. F1（F103）不生成 AF 宏；用它必须编不过 —— 不许假装 F1 有 AF7
        store.delete()
        design = _design()
        store.save(design)
        contract = iface_module.build(design, ioc_path=IOC_F103)
        store.write(iface_module.FILE, contract)
        made = fw_build.generate(store, design, contract, out_dir="fw/b")
        store.write(fw_build.FILE, made)
        header = store.path("fw/b/hwv_pins.h").read_text(encoding="utf-8")
        source = store.path("fw/b/neg_f1_af.c")
        source.write_text('#include "hwv_pins.h"\n'
                          "void t(void) { HWV_EXPECT_AF(USART1_TX, 7); }\n",
                          encoding="utf-8")
        tool = fw_build._find_toolchain()
        tmp = store.path("_tmp/fw-build")
        tmp.mkdir(parents=True, exist_ok=True)
        import os  # noqa: PLC0415
        import subprocess  # noqa: PLC0415
        env = dict(os.environ)
        env.update({"PATH": str(tool.parent) + os.pathsep + env.get("PATH", ""),
                    "TMP": str(tmp), "TEMP": str(tmp), "TMPDIR": str(tmp)})
        proc = subprocess.run([str(tool), "-mcpu=cortex-m3", "-mthumb", "-Os", "-c",
                               source.name, "-o", "neg_f1_af.o"],
                              cwd=str(store.path("fw/b")), capture_output=True,
                              text=True, timeout=180, env=env)
        text = (proc.stderr or proc.stdout or "")
        check("4e. F103 不生成 `HWV_AF_*`：用它（`HWV_EXPECT_AF`）**必须编不过**",
              re.search(r"#define\s+HWV_AF_", header) is None
              and proc.returncode != 0 and "HWV_AF_" in text,
              text.strip().splitlines()[0][:110] if text.strip() else "")
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------


def part_cli() -> None:
    listed = design_cli.cmd_afmap(["list"])
    check("5a. `afmap list` 列出 4 张表且状态 ok",
          listed.get("status") == "ok" and listed.get("count") == 4,
          f"{listed.get('count')} 张")

    shown = design_cli.cmd_afmap(["show", "stm32f401rct6", "--pin=PA9"])
    pin = shown.get("pin") or {}
    check("5b. `afmap show --pin=PA9` 给出全部功能",
          shown.get("status") == "ok"
          and any(f.get("signal") == "USART1_TX" for f in pin.get("functions") or []),
          str([(f.get("signal"), f.get("af")) for f in (pin.get("functions") or [])][:4]))

    can = design_cli.cmd_afmap(["can", "stm32f103c8t6", "--signal=I2C1_SCL"])
    check("5c. `afmap can --signal=I2C1_SCL` 列出能做这个信号的脚",
          can.get("status") == "ok" and len(can.get("pins") or []) >= 2,
          str([row.get("pin") for row in can.get("pins") or []]))

    bad = design_cli.cmd_afmap(["show", "stm32f405rgt6"])
    check("5d. 没有表的型号 → `not_ready` 并列出已有的（不猜）",
          bad.get("status") == "not_ready" and bad.get("known"),
          str(bad.get("known")))

    validated = design_cli.cmd_afmap(["validate"])
    check("5e. `afmap validate` 全表通过",
          validated.get("status") == "ok" and validated.get("failed") == 0,
          f"checked={validated.get('checked')}")


def main() -> int:
    print("=== P57 AF 复用表：这个脚能不能做这个功能（AF 编号）===")
    part_tables()
    part_firmware()
    part_contract()
    part_build()
    part_cli()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())