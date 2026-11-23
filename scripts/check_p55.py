"""P55 验收 —— `.ioc` 接进来，把「外设配置」这一层补上。

    python -m hw_velxio.scripts.check_p55

## 这一段补什么

P50 的 `interface.json` 把时钟/中断/复用功能/时序**四类全记 `unknown`**——
因为 `design.json` 只有"哪个脚接哪个网"。`.ioc`（CubeMX）才有"这个脚配成
什么外设、跑多快、哪个中断开着"。

现在 `.ioc` 由 `design/ioc.py` 读成事实，`interface.build(ioc_path=...)` 合并，
`hwv_pins.h` 再把时钟/外设参数变成**编译期可判**的宏：

| 层 | 判什么 | 手段 |
|---|---|---|
| `.ioc` 解析 | 时钟/中断/功能/参数 | 与 7 个真实文件逐项对 |
| `interface` | 芯片身份 vs 设计；配置进契约 | 族/封装/型号三级 |
| `hwv_pins.h` | `HWV_CHECK_CLOCK` / `HWV_EXPECT_CFG` | `_Static_assert` |

## 判据（**反着判**最要紧）

| 项 | 期望 |
|---|---|
| 真实文件 | 7/7 能读；**用户中断**与**内核异常**分开数（否则"12 个中断"是假的）|
| 不给 AF 编号 | `.ioc` 只说 `PA9=USART1_TX`，`af_number` 恒 `unknown`（不猜 AF7）|
| 没写的参数 | `BaudRate` 只在部分文件里有 → 没写就**不生成宏**，用它 = 未声明标识符 |
| 给错 `.ioc` | F103 的板配 H743 的文件 → `ioc.mcu_mismatch`、`failed` |
| 同族不同型号 | C8 的板配 C6 的文件 → 只 `ioc.mcu_variant`（引脚兼容，不判 fail）|
| 编译期 | 时钟写错 / 波特率写错 **必须编不过**（负例 5 条）|
| 退化 | 把 `HWV_CHECK_CLOCK` 改成空操作 → `clock_wrong` 编过 → 整体 `failed` |
| 无契约 | 没给 `.ioc` 时用 `HWV_CHECK_CLOCK(8000000)` **必须编不过**（不许假设频率）|

## 数据来源

* **夹具**（`tests/fixtures/ioc/`）：形状照抄实测的真实 `.ioc`
* **真实文件**（可选）：工作区里 7 个 `.ioc`；目录不在就**明确跳过**，不假装跑过
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio import design_cli  # noqa: E402
from hw_velxio.design import fw_build  # noqa: E402
from hw_velxio.design import interface as iface_module  # noqa: E402
from hw_velxio.design import ioc as ioc_module  # noqa: E402
from hw_velxio.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from hw_velxio.design.store import DesignStore  # noqa: E402

PROJECT = "p55-fixture"
REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "ioc"
IOC_GOOD = FIXTURES / "p55-usart.ioc"
IOC_C6 = FIXTURES / "p55-usart-c6.ioc"
IOC_WRONG = FIXTURES / "p55-h743-wrong.ioc"

#: 真实 `.ioc` 所在（可选）。**这 7 个文件是用户资产，只读不写。**
REAL_ROOT = Path(r"C:\Users\<user>\Desktop\示例工作区")

#: 实测表：文件 → (引脚, 用户外设, 用户中断, SYSCLK, 封装)。
#: 数字变了 = 解析器漏了/多了东西（**改过 .ioc 就改这里**）。
REAL_TABLE: dict[str, tuple[int, int, int, int, str]] = {
    "F103-GPS.ioc": (19, 4, 3, 64_000_000, "LQFP48"),
    "F401-Bottom.ioc": (25, 8, 7, 72_000_000, "LQFP64"),
    "F401-Front.ioc": (16, 4, 3, 72_000_000, "LQFP64"),
    "F405-Joystick-Screen.ioc": (36, 13, 10, 168_000_000, "LQFP64"),
    "F405Joystick.ioc": (35, 11, 12, 96_000_000, "LQFP64"),
    "H743PilotX.ioc": (63, 22, 17, 480_000_000, "LQFP176"),
    "f103c6-car.ioc": (14, 5, 4, 64_000_000, "LQFP48"),
}

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
    """48 脚 MCU + 电源/地 + UART + USB（USB 用来验"网表有、.ioc 没配"）。"""
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
        {"net": "UART_RX", "pins": [{"ref": "U1", "pin": "31"}]},
        {"net": "USB_DP", "pins": [{"ref": "U1", "pin": "33"}]},
    ]
    return design


def _prepare(store: DesignStore, ioc: Path | None = IOC_GOOD) -> dict:
    design = _design()
    store.save(design)
    contract = iface_module.build(design, ioc_path=ioc)
    store.write(iface_module.FILE, contract)
    made = fw_build.generate(store, design, contract, out_dir="fw/b")
    # `build()` 读 `fw_build.json`（CLI 在 generate 之后写它）—— 验收走同一条路
    store.write(fw_build.FILE, made)
    return contract


def _compile(store: DesignStore, body: str, name: str) -> subprocess.CompletedProcess:
    """把一段源码编成一个 `.o`（**不看结果，调用方自己判**）。"""
    tool = fw_build._find_toolchain()
    target = store.path("fw/b")
    tmp = store.path("_tmp/fw-build")
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({"PATH": str(tool.parent) + os.pathsep + env.get("PATH", ""),
                "TMP": str(tmp), "TEMP": str(tmp), "TMPDIR": str(tmp)})
    source = target / f"{name}.c"
    source.write_text('#include "hwv_pins.h"\n' + body + "\n", encoding="utf-8")
    return subprocess.run(
        [str(tool), "-mcpu=cortex-m3", "-mthumb", "-Os", "-c",
         source.name, "-o", f"{name}.o"],
        cwd=str(target), capture_output=True, text=True, timeout=180, env=env)


# ---------------------------------------------------------------------------
# 1. 真实 `.ioc`：解析器在脏数据上不崩、数字对得上
# ---------------------------------------------------------------------------


def part_real_files() -> None:
    if not REAL_ROOT.is_dir():
        print(_safe(f"[skip] 1. 没有真实 .ioc 目录（{REAL_ROOT}）——"
                    "这一节**没跑**，不是通过"))
        return
    files = {p.name: p for p in REAL_ROOT.rglob("*.ioc")}
    parsed: dict[str, dict] = {}
    for name in REAL_TABLE:
        path = files.get(name)
        if path is None:
            check(f"1. 找得到 {name}", False, "文件不在")
            continue
        parsed[name] = ioc_module.parse(path)

    rows: list[str] = []
    ok_all = True
    for name, (pins, ips, irqs, sysclk, pkg) in REAL_TABLE.items():
        facts = parsed.get(name)
        if facts is None:
            ok_all = False
            continue
        got = (facts["counts"]["pins"], facts["counts"]["peripherals"],
               facts["counts"]["irqs"], facts["clock"].get("sysclk_hz"),
               facts["mcu"].get("package"))
        rows.append(f"{name}: {got}")
        ok_all = ok_all and got == (pins, ips, irqs, sysclk, pkg)
    check("1a. **7 个真实 .ioc 的引脚/外设/中断/SYSCLK/封装与实测表一致**",
          ok_all, ("" if ok_all else "预期 " + str(REAL_TABLE) + " 实际 " + str(rows))
          or f"{len(parsed)}/7 个文件")

    # 用户中断 vs 内核异常：混在一起会把"3 个用户中断"说成"12 个中断"
    front = parsed.get("F401-Front.ioc") or {}
    nvic = front.get("nvic") or {}
    check("1b. **用户中断与内核异常分开数**（F401-Front：用户 3 / 内核 9）",
          len(nvic.get("irqs") or []) == 3 and len(nvic.get("core") or []) == 9,
          f"irqs={[r['irq'] for r in nvic.get('irqs') or []]} "
          f"core={len(nvic.get('core') or [])}")

    tim = [r for r in (nvic.get("irqs") or []) if r["irq"] == "TIM3_IRQn"]
    # PriorityGroup 4 下子优先级恒 0 —— 7 个文件全部符合，用它当不变量
    subs = [r["sub"] for facts in parsed.values()
            for r in (facts["nvic"].get("irqs") or [])]
    check("1c. 抢占/子优先级拆得对：TIM3=15，且 GROUP_4 下**所有**子优先级=0",
          bool(tim) and tim[0]["preempt"] == 15 and set(subs) == {0},
          f"TIM3={tim[0] if tim else None} subs={sorted(set(subs))}")

    # 带后缀的脚（晶振/SWD/BOOT）第一版被整条丢掉过
    suffixes = {
        "F401-Front.ioc": ("PH0", "PH1", "PC13"),
        "f103c6-car.ioc": ("PA0", "PD0", "PD1"),
        "H743PilotX.ioc": ("PH0", "PH1", "PB3", "PB4"),
    }
    missing = {name: [pin for pin in pins if pin not in (parsed.get(name) or {})
                      .get("pins", {})]
               for name, pins in suffixes.items()}
    check("1d. **带后缀的脚没被漏掉**（`PH0 - OSC_IN` / `PA0-WKUP` / `PC13-ANTI_TAMP`）",
          not any(missing.values()), str(missing))

    afs = {name: facts["af"].get("af_number") for name, facts in parsed.items()}
    check("1e. **不给 AF 编号**：`af_number` 七个文件全是 `unknown`（不猜 AF7）",
          set(afs.values()) == {"unknown"}, str(sorted(set(afs.values()))))

    # `.ioc` 只写非默认值：F401-Front 的 USART1 只写了 VirtualMode
    usart = (parsed.get("F401-Front.ioc") or {}).get("peripherals", {}).get("USART1", {})
    check("1f. **没写的参数不填默认值**（F401-Front 的 USART1 只有 VirtualMode，"
          "没有 BaudRate）",
          "BaudRate" not in (usart.get("params") or {}),
          str(usart.get("params")))

    # 分类：GPIO / 外设 / 定时器 / 晶振 各归各类
    c6 = parsed.get("f103c6-car.ioc") or {}
    kinds = {pin: (c6.get("pins") or {}).get(pin, {}).get("kind")
             for pin in ("PA9", "PB12", "PA0-WKUP", "PD0")}
    check("1g. 信号分类对得上（PA9=peripheral、PB12=gpio、PD0=clock）",
          kinds.get("PA9") == "peripheral" and kinds.get("PB12") == "gpio"
          and kinds.get("PD0") == "clock",
          str(kinds))


# ---------------------------------------------------------------------------
# 2. interface：配置进契约、缺的东西收窄、给错 .ioc 要炸
# ---------------------------------------------------------------------------


def part_contract() -> None:
    store = DesignStore(PROJECT)
    try:
        store.delete()
        contract = _prepare(store, IOC_GOOD)
        clock = contract["clock"]
        check("2a. `.ioc` 的时钟进契约（SYSCLK 72 MHz / APB1 36 MHz / 源 PLL）",
              clock.get("status") == "declared"
              and clock.get("sysclk_hz") == 72_000_000
              and clock.get("apb1_hz") == 36_000_000
              and clock.get("sysclk_source") == "RCC_SYSCLKSOURCE_PLLCLK",
              str({k: clock.get(k) for k in ("status", "sysclk_hz", "apb1_hz")}))

        nvic = contract["nvic"]
        check("2b. 中断进契约（USART1_IRQn 抢占 1 / GROUP_4）",
              nvic.get("status") == "declared"
              and [r["irq"] for r in nvic.get("irqs") or []] == ["USART1_IRQn"]
              and nvic.get("priority_group") == "NVIC_PRIORITYGROUP_4",
              str(nvic.get("irqs")))

        cfg = contract["peripheral_config"]
        check("2c. 外设参数进契约（USART1 的 BaudRate=115200）",
              (cfg.get("USART1") or {}).get("params", {}).get("BaudRate") == "115200"
              and (cfg.get("USART1") or {}).get("pins") ==
              {"PA9": "USART1_TX", "PA10": "USART1_RX"},
              str({k: v.get("params") for k, v in cfg.items()}))

        af = contract["af"]
        check("2d. 复用功能是**功能名**（PA9=USART1_TX），AF 编号仍 unknown",
              (af.get("functions") or {}).get("PA9") == "USART1_TX"
              and af.get("af_number") == "unknown", str(af.get("af_number")))

        uart = [g for g in contract["peripherals"] if g["family"] == "uart"]
        check("2e. 外设分组的来源变成 `ioc`，并带实例名（USART1）",
              bool(uart) and uart[0]["source"] == "ioc"
              and uart[0]["ioc_instances"] == ["USART1"], str(uart[:1]))

        what = [u["what"] for u in contract["unknown"]]
        check("2f. **unknown 收窄**：不再有\"时钟树\"与\"中断\"，"
              "但保留 AF 编号/时序/脚号对照",
              not any("时钟树" in w for w in what)
              and not any(w.startswith("中断") for w in what)
              and any("AF 编号" in w for w in what)
              and any("时序" in w for w in what)
              and any("封装脚号" in w for w in what), str(what))

        checks = [w["check"] for w in contract["warnings"]]
        check("2g. 两个方向都提示：`.ioc` 配了 I2C1 但没 i2c 网；网表有 USB 但没配",
              "ioc.peripheral_unrouted" in checks
              and "ioc.peripheral_unconfigured" in checks, str(checks))

        md = iface_module.render(contract)
        check("2h. 人读版有 时钟/中断/外设配置 段，且写明 AF 编号 unknown",
              "## 时钟" in md and "MHz" in md and "## 中断" in md
              and "## 外设配置" in md and "AF 编号" in md,
              str([line for line in md.splitlines() if line.startswith("## ")]))

        # 没有 .ioc：保持 P50 的四类 unknown（不能因为"新功能"把老契约改掉）
        bare = iface_module.build(_design())
        check("2i. 没有 `.ioc` 时仍是四类 unknown（P50 的形状不变）",
              len(bare["unknown"]) == 4
              and bare["clock"]["status"] == "unknown"
              and bare["nvic"]["status"] == "unknown",
              str([u["what"] for u in bare["unknown"]]))
    finally:
        store.delete()


def part_identity() -> None:
    store = DesignStore(PROJECT)
    try:
        store.delete()
        design = _design()
        store.save(design)

        wrong = iface_module.build(design, ioc_path=IOC_WRONG)
        problems = [p["check"] for p in wrong["problems"]]
        check("2j. **给错 `.ioc`（F103 的板配 H743 文件）→ failed + ioc.mcu_mismatch**",
              wrong["status"] == "failed" and "ioc.mcu_mismatch" in problems,
              (wrong["problems"][0]["message"][:120] if wrong["problems"] else "无"))

        variant = iface_module.build(design, ioc_path=IOC_C6)
        warns = [w["check"] for w in variant["warnings"]]
        check("2k. 同族同封装不同型号（C8 板 / C6 文件）→ 只提示 variant，不判 fail",
              variant["status"] == "ok" and "ioc.mcu_variant" in warns,
              str([w["message"][:90] for w in variant["warnings"]]))

        # 缺文件：不能当成"没给 .ioc"混过去
        broken = iface_module.build(design, ioc_path=FIXTURES / "no-such.ioc")
        check("2l. `.ioc` 读不了 → `ioc.present=false` + error（不静默当成没有）",
              (broken["ioc"].get("present") is False
               and broken["ioc"].get("error")),
              str(broken["ioc"].get("error", "")[:100]))
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 3. 编译期：配置契约进 hwv_pins.h，负例 5 条必须编不过
# ---------------------------------------------------------------------------


def part_build() -> None:
    if not (shutil.which("arm-none-eabi-gcc") or fw_build._find_toolchain()):
        print(_safe("[skip] 3. 没有 arm-none-eabi-gcc（不假装跑过）"))
        return
    store = DesignStore(PROJECT)
    try:
        store.delete()
        _prepare(store, IOC_GOOD)
        header = store.path("fw/b/hwv_pins.h").read_text(encoding="utf-8")
        main = store.path("fw/b/main.c").read_text(encoding="utf-8")
        check("3a. 头文件里有**时钟宏**与**外设参数宏**",
              re.search(r"#define HWV_SYSCLK_HZ\s+72000000u", header) is not None
              and re.search(r"#define HWV_APB1_HZ\s+36000000u", header) is not None
              and re.search(r"#define HWV_CFG_USART1_BAUDRATE\s+115200u",
                            header) is not None,
              str([line for line in header.splitlines()
                   if "HWV_SYSCLK" in line or "HWV_CFG_" in line]))
        check("3b. 骨架**真的用**了这两个契约宏，且 SystemCoreClock 取自契约",
              "HWV_CHECK_CLOCK(72000000u);" in main
              and "HWV_EXPECT_CFG(USART1, BAUDRATE, 115200u);" in main
              and "SystemCoreClock = HWV_SYSCLK_HZ;" in main,
              str([line.strip() for line in main.splitlines()
                   if "HWV_CHECK_CLOCK" in line or "HWV_EXPECT_CFG" in line]))

        result = fw_build.build(store, out_dir="fw/b")
        neg = result.get("negative") or {}
        check("3c. 真编译过（Machine=ARM）且**五条负例全部编不过**",
              result["status"] == "ok" and result["checks"]["machine"] == "ARM"
              and neg.get("passed") == 5 and neg.get("total") == 5,
              f"{result['status']} {neg.get('passed')}/{neg.get('total')}")
        detail = {row["name"]: str(row.get("detail") or "")
                  for row in neg.get("cases") or []}
        check("3d. 新增两条的错误信息指向**时钟/参数**（不是别的错）",
              "HWV_CHECK_CLOCK" in detail.get("clock_wrong", "")
              and "HWV_EXPECT_CFG" in detail.get("cfg_wrong", ""),
              str({k: v[:70] for k, v in detail.items() if k in
                   ("clock_wrong", "cfg_wrong")}))

        # 没给 .ioc：三条老的照跑，新的两条**不出现**（不假装跑过）
        store.delete()
        _prepare(store, None)
        header = store.path("fw/b/hwv_pins.h").read_text(encoding="utf-8")
        bare = fw_build.build(store, out_dir="fw/b")
        neg_bare = bare.get("negative") or {}
        names = [row["name"] for row in neg_bare.get("cases") or []]
        check("3e. 没有 `.ioc` → 只有三条负例，头文件里**没有**时钟宏",
              neg_bare.get("passed") == 3
              # 注意：`HWV_CHECK_CLOCK` 的**定义**里当然出现 HWV_SYSCLK_HZ；
              # 没有契约时缺的是**它的定义**，不是"这个名字一次都不出现"。
              and re.search(r"#define\s+HWV_SYSCLK_HZ\b", header) is None
              and "clock_wrong" not in names
              and "cfg_wrong" not in names,
              f"{neg_bare.get('passed')}/{neg_bare.get('total')} {names}")
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 4. 退化：把闸门拆掉，负例必须变"编过"
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    if not (shutil.which("arm-none-eabi-gcc") or fw_build._find_toolchain()):
        print(_safe("[skip] 4. 没有工具链（退化验证跳过）"))
        return
    store = DesignStore(PROJECT)
    original = fw_build._pins_header
    old_macro = ("#define HWV_CHECK_CLOCK(hz) \\\n"
                 "    _Static_assert((hz) == HWV_SYSCLK_HZ, \\\n"
                 '                   "HWV_CHECK_CLOCK: SYSCLK differs from .ioc'
                 ' (interface.json)")\n')
    state = {"hit": False}
    try:
        store.delete()
        _prepare(store, IOC_GOOD)

        def lax(interface: dict) -> str:  # noqa: ANN001
            text = original(interface)
            # 把时钟断言换成空操作 —— 等于把闸门拆了
            if old_macro in text:
                state["hit"] = True
                text = text.replace(old_macro, "#define HWV_CHECK_CLOCK(hz) ((void)(hz))\n")
            return text

        fw_build._pins_header = lax  # type: ignore[assignment]
        try:
            design = _design()
            contract = iface_module.build(design, ioc_path=IOC_GOOD)
            made = fw_build.generate(store, design, contract, out_dir="fw/b")
            store.write(fw_build.FILE, made)
            broken = fw_build.build(store, out_dir="fw/b")
        finally:
            fw_build._pins_header = original  # type: ignore[assignment]
        neg = broken.get("negative") or {}
        got = {row["name"]: row["got"] for row in neg.get("cases") or []}
        check("4a. 退化：`HWV_CHECK_CLOCK` 变空操作 → `clock_wrong` **编过** → 整体 failed",
              state["hit"] and got.get("clock_wrong") == "pass"
              and neg.get("status") == "failed" and broken["status"] == "failed",
              f"替换命中={state['hit']} got={got.get('clock_wrong')} "
              f"neg={neg.get('status')}")

        # 4b. **无契约时的诚实**：没给 .ioc，固件不许假设频率。
        #     这条不是"闸门"，而是"缺数据不能变成默认值"（AGENTS §0.1）。
        store.delete()
        _prepare(store, None)
        proc = _compile(store, "void t(void) { HWV_CHECK_CLOCK(8000000u); }",
                        "neg_clock_no_contract")
        text = (proc.stderr or proc.stdout or "")
        check("4b. 没有 `.ioc` 时 `HWV_CHECK_CLOCK(8000000)` **编不过**"
              "（缺契约 ≠ 默认 8 MHz）",
              proc.returncode != 0 and "HWV_SYSCLK_HZ" in text,
              text.strip().splitlines()[0][:120] if text.strip() else "")
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 5. CLI 接线：`interface --ioc=` 真的走同一条路
# ---------------------------------------------------------------------------


def part_cli() -> None:
    store = DesignStore(PROJECT)
    try:
        store.delete()
        store.save(_design())
        got = design_cli.cmd_interface([PROJECT, f"--ioc={IOC_GOOD}"])
        check("5a. CLI `interface --ioc=` 把 .ioc 读进契约（status ok + 时钟 declared）",
              got.get("status") == "ok"
              and (got.get("clock") or {}).get("sysclk_hz") == 72_000_000,
              f"status={got.get('status')} clock={((got.get('clock') or {}).get('status'))}")
        md = store.path("interface.md")
        text = md.read_text(encoding="utf-8") if md.is_file() else ""
        check("5b. `interface.md` 落盘且带 时钟/中断/外设配置 段",
              md.is_file() and "## 时钟" in text and "## 外设配置" in text,
              str([line for line in text.splitlines() if line.startswith("## ")]))
        saved = store.read(iface_module.FILE) or {}
        check("5c. `interface.json` 落盘的就是返回的那份（同一个事实源）",
              saved.get("clock") == got.get("clock")
              and saved.get("unknown") == got.get("unknown")
              and len(saved.get("unknown") or []) == 3,
              f"unknown={len(saved.get('unknown') or [])}")
    finally:
        store.delete()


def main() -> int:
    print("=== P55 .ioc → 外设配置层（时钟/中断/复用功能/外设参数）===")
    part_real_files()
    part_contract()
    part_identity()
    part_build()
    part_degeneration()
    part_cli()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())