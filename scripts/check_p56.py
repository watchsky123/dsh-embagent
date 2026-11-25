"""P56 验收 —— **引脚定义表**：`.ioc` 的 `PA9` ↔ 网表的 `30`，逐脚对账。

    python -m dsh_embagent.scripts.check_p56

## 这一段补什么

P55 把 `.ioc` 的配置读进了契约，但只能"按名字对"——`.ioc` 说 `PA9`，
网表说 `30`，两者之间差一张表。所以"`.ioc` 把这个脚配成 UART，板子却接到
SPI"这种事，仍然只能在台架上发现。

现在 `design/pinout/` 里有**每颗 MCU 一张表**（封装脚号 → 端口名），
`interface` 用它把两边接起来：`ioc.pin_conflict` 成为**硬判据**。

## 表从哪来、凭什么可信

来源是 **LCSC/EasyEDA 厂商符号**（`P~` 图元带 `pinNumber`/`pinName`），
`source="catalog"`，每条记 `source_url`/`retrieved`/`sha256`。

可信性靠三层，缺一层就不该拿它判人 fail：

| 层 | 判据 | 在哪 |
|---|---|---|
| 自检 | 脚号 1..N 连续、数量 == 封装脚数、端口名合法 | `pinout.validate()` |
| 交叉 | 非 GPIO 脚名 == `fwgen.mcus` 的 `power_pins` | 本脚本 1c |
| 端到端 | 真实项目（EasyEDA 网表 + CubeMX `.ioc`）**0 冲突**，且 `PA9↔30` 这些对应全对 | 本脚本 4a/4b |

## 判据（**反着判**最要紧）

| 项 | 期望 |
|---|---|
| 不完整符号 | STM32F405RGT6 的符号只有 55/64 个脚 → **拒绝建表**，不补猜的脚 |
| 协议族冲突 | `.ioc` 说 spi、网表说 uart → `ioc.pin_conflict`、`failed` |
| 电源地冲突 | `.ioc` 配了 UART，网表把那脚接 GND → `ioc.pin_conflict`、`failed` |
| 方向冲突 | `.ioc` 说 RX、网表是 TX 网 → **只软提示**（网名可能站在对方视角）|
| 退化一 | 把表里两个脚对调 → 真实项目**必须变红**（证明表真的在判）|
| 退化二 | 把表拿掉 → 冲突归零、脚号对照回到 `unknown`（不是永远绿）|
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent import design_cli  # noqa: E402
from dsh_embagent.design import interface as iface_module  # noqa: E402
from dsh_embagent.design import pinout as pinout_module  # noqa: E402
from dsh_embagent.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from dsh_embagent.design.store import DesignStore  # noqa: E402

PROJECT = "p56-fixture"
REPO = Path(__file__).resolve().parents[1]
IOC_GOOD = REPO / "tests" / "fixtures" / "ioc" / "p55-usart.ioc"

#: 真实项目（可选）：EasyEDA 网表 与 CubeMX `.ioc` 来自**两个独立来源**，
#: 它们在这张表上对得上，才说明表是对的。
from dsh_embagent.scripts._probe import path as probe_path  # noqa: E402
REAL_DESIGN = probe_path("DSH_EMBAGENT_PROBE_DESIGN")
REAL_IOC = probe_path("DSH_EMBAGENT_PROBE_IOC")

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


def _design(nets: list[dict] | None = None) -> DesignModel:
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
    design.nets = nets or [
        {"net": "3V3", "pins": [{"ref": "U1", "pin": "1"},
                                {"ref": "U1", "pin": "24"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": "8"},
                                {"ref": "U1", "pin": "23"}]},
        {"net": "UART_TX", "pins": [{"ref": "U1", "pin": "30"}]},
        {"net": "UART_RX", "pins": [{"ref": "U1", "pin": "31"}]},
    ]
    return design


def _ioc_with(pa9_signal: str, pa10_signal: str = "USART1_RX") -> Path:
    """把夹具 `.ioc` 的 PA9/PA10 信号换掉（临时文件，用完删）。"""
    text = IOC_GOOD.read_text(encoding="utf-8")
    text = text.replace("PA9.Signal=USART1_TX", f"PA9.Signal={pa9_signal}")
    text = text.replace("PA10.Signal=USART1_RX", f"PA10.Signal={pa10_signal}")
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
        "w", suffix=".ioc", delete=False, encoding="utf-8", newline="")
    handle.write(text)
    handle.close()
    return Path(handle.name)


def _conflicts(payload: dict) -> list[dict]:
    return [p for p in (payload.get("problems") or [])
            if str(p.get("check")) == "ioc.pin_conflict"]


# ---------------------------------------------------------------------------
# 1. 已落盘的表：自检 + 与型号表交叉核对
# ---------------------------------------------------------------------------


def part_tables() -> None:
    rows = pinout_module.list_tables()
    bad = [row for row in rows if row.get("error") or row.get("problems")]
    check("1a. 已落盘的表**全部通过自检**（脚号连续/数量等于封装/端口名合法）",
          bool(rows) and not bad,
          f"{len(rows)} 张：{[r.get('part') for r in rows]}" if not bad else str(bad))

    table = pinout_module.load("stm32f103c8t6")
    pins = (table or {}).get("pins") or {}
    check("1b. F103C8T6：48 脚 / 37 个 GPIO 端口 / 关键脚位对得上",
          (table or {}).get("count") == 48
          and len({p["port"] for p in pins.values() if p.get("port")}) == 37
          and pins.get("30", {}).get("port") == "PA9"
          and pins.get("33", {}).get("port") == "PA12"
          and pins.get("44", {}).get("name") == "BOOT0"
          and pins.get("1", {}).get("name") == "VBAT",
          f"30={pins.get('30')} 44={pins.get('44')}")

    from dsh_embagent.design.fwgen import mcus  # noqa: PLC0415

    # 两份资料（厂商符号 / 型号表）说同样的电源脚名。名字写法会有出入：
    # `VDDAVREF+` 把 VDDA 与 VREF+ 合成一个脚、H7 写 `PDR` 而 F4 写 `PDR_ON`、
    # `VCAP`/`VREF` 是型号表没列的额外电源脚 —— 所以判**前缀覆盖**，
    # 并把这些写法差异列成 allowlist，**不静默放宽**。
    extra = ("VCAP", "VREF")

    def covered(name: str, want: set[str]) -> bool:
        return any(name.startswith(w) or w.startswith(name) for w in want)

    rows_bad: list[str] = []
    for row in rows:
        part = str(row.get("part") or "")
        table = pinout_module.load(part) or {}
        mcu = mcus.find_mcu(part)
        want = {str(x).upper() for x in (mcu.power_pins if mcu else ())}
        names = {str(r.get("name") or "").split("_")[0].upper()
                 for r in (table.get("pins") or {}).values()
                 if r.get("kind") != "gpio"}
        missing = [w for w in want if not any(covered(n, {w}) for n in names)]
        unknown = [n for n in names if not covered(n, want)
                   and not any(n.startswith(x) for x in extra)]
        if missing or unknown:
            rows_bad.append(f"{part}: 缺 {missing} 多 {unknown}")
    check("1c. **交叉核对**：非 GPIO 脚名与型号表的 `power_pins` 前缀覆盖"
          "（4 张表逐一比；VDDAVREF+ / PDR 这类写法差异用 allowlist，不静默放宽）",
          not rows_bad, " | ".join(rows_bad) or f"{len(rows)} 张表全部对上")

    proven = all(table.get(k) for k in ("source", "source_url", "retrieved",
                                        "sha256", "lcsc", "mpn"))
    check("1d. 每张表都有**出处**（source/url/retrieved/sha256）",
          proven and all(r.get("source") == "catalog" for r in rows),
          f"{table.get('source')} {table.get('lcsc')} {table.get('retrieved')}")

    check("1e. 找不到的型号返回 None（**不猜**）",
          pinout_module.find("stm32f405rg", "C15742") is None
          and pinout_module.load("stm32f405rgt6") is None,
          str([r.get("part") for r in rows]))


# ---------------------------------------------------------------------------
# 2. 不完整的符号：拒绝建表（离线假 fetcher，不联网）
# ---------------------------------------------------------------------------


class _FakeNet:
    def __init__(self, doc: dict) -> None:
        self.doc = doc

    def get_json(self, url: str):  # noqa: ANN201
        return {"success": True, "result": self.doc}, ""


def _symbol(pins: list[tuple[str, str]], package: str) -> dict:
    """最小 EasyEDA 符号：`P~…` 图元 + 封装名。"""
    shapes = []
    for index, (number, name) in enumerate(pins):
        shapes.append(
            f"P~show~0~{number}~{300 + index}~200~180~gge{index}~0^^0~0"
            f"^^M0,0~#880000^^1~320~200~0~{name}~start^^1~0~0~0~{number}~end"
            f"^^0~0~0^^0~M 0 0 L 1 1")
    return {"title": "FAKE", "dataStr": {
        "head": {"c_para": {"Manufacturer Part": "STM32F405RGT6",
                            "package": package}},
        "shape": shapes,
    }}


def part_refusal() -> None:
    full = [(str(i), f"P{chr(65 + i % 4)}{i % 16}") for i in range(1, 65)]
    incomplete = full[:55]
    made = pinout_module.build_table(
        "C0000000", part="fake", fetcher=_FakeNet(_symbol(incomplete, "LQFP-64")))
    check("2a. 符号只有 55/64 个脚 → **拒绝建表**（不补 9 个猜的脚）",
          made.get("status") == "failed"
          and any("55" in str(p) for p in made.get("problems") or []),
          str((made.get("problems") or [""])[0])[:110])

    ok = pinout_module.build_table(
        "C0000001", part="fake", fetcher=_FakeNet(_symbol(full, "LQFP-64")))
    check("2b. 对照：64/64 的符号能建表（拒绝不是「永远失败」）",
          ok.get("status") == "ok" and (ok.get("table") or {}).get("count") == 64,
          str(ok.get("status")))

    broken = dict(ok["table"])
    broken["pins"] = {k: v for k, v in ok["table"]["pins"].items() if k != "7"}
    broken["count"] = 63
    problems = pinout_module.validate(broken)
    check("2c. `validate()` 抓得住缺脚（脚号不连续 + 数量不符）",
          any("不连续" in p for p in problems)
          and any("不符" in p for p in problems), str(problems))


# ---------------------------------------------------------------------------
# 3. 逐脚冲突：夹具（协议族 / 电源地 / 方向）
# ---------------------------------------------------------------------------


def part_conflicts() -> None:
    store = DesignStore(PROJECT)
    made: list[Path] = []
    try:
        store.delete()

        payload = iface_module.build(_design(), ioc_path=IOC_GOOD)
        join = payload.get("pin_join") or {}
        port_pin = {row["port"]: (row["pin"], row["status"]) for row in join["rows"]}
        check("3a. 对得上的情况：PA9↔30、PA10↔31，**0 冲突**",
              payload["status"] == "ok" and not _conflicts(payload)
              and port_pin.get("PA9") == ("30", "ok")
              and port_pin.get("PA10") == ("31", "ok"),
              f"PA9={port_pin.get('PA9')} PA10={port_pin.get('PA10')}")

        spi = _ioc_with("SPI1_SCK")
        made.append(spi)
        bad = iface_module.build(_design(), ioc_path=spi)
        msgs = [p["message"] for p in _conflicts(bad)]
        check("3b. **协议族冲突**：`.ioc` 把 PA9 配成 SPI1_SCK，网表是 UART_TX → failed",
              bad["status"] == "failed" and bool(msgs)
              and "spi" in msgs[0] and "uart" in msgs[0], str(msgs)[:130])

        # 网表把 30 脚接到 GND（短路/接错地）
        gnd = _design([
            {"net": "3V3", "pins": [{"ref": "U1", "pin": "1"}]},
            {"net": "GND", "pins": [{"ref": "U1", "pin": "8"},
                                    {"ref": "U1", "pin": "23"},
                                    {"ref": "U1", "pin": "30"}]},
        ])
        short = iface_module.build(gnd, ioc_path=IOC_GOOD)
        short_msgs = [p["message"] for p in _conflicts(short)]
        check("3c. **电源地冲突**：`.ioc` 配了 UART，网表把 30 脚接 GND → failed",
              short["status"] == "failed" and bool(short_msgs)
              and "电源/地" in short_msgs[0], str(short_msgs)[:130])

        # 方向反了用**网表**制造（`.ioc` 保持合法）：脚 30 是 PA9=USART1_TX，
        # 网表却把 30 叫 UART_RX。若改 `.ioc` 的 PA9=USART1_RX，那不是"方向反了"，
        # 而是"PA9 根本做不了 RX"——那是 AF 表的硬冲突（P57）。
        swapped = _design([
            {"net": "3V3", "pins": [{"ref": "U1", "pin": "1"}]},
            {"net": "GND", "pins": [{"ref": "U1", "pin": "8"}]},
            {"net": "UART_RX", "pins": [{"ref": "U1", "pin": "30"}]},
            {"net": "UART_TX", "pins": [{"ref": "U1", "pin": "31"}]},
        ])
        role = iface_module.build(swapped, ioc_path=IOC_GOOD)
        soft = [w["check"] for w in role.get("warnings") or []]
        check("3d. **方向只软提示**：网表把 PA9 的 TX 叫成 UART_RX → "
              "`ioc.pin_role_conflict`，不判 fail",
              role["status"] == "ok" and "ioc.pin_role_conflict" in soft
              and not _conflicts(role), str(soft))

        # 封装对不上：`.ioc` 里出现这颗芯片没有的端口
        original_find = iface_module.pinout_module.find
        try:
            small = dict(pinout_module.load("stm32f103c8t6") or {})
            small["pins"] = {k: v for k, v in (small.get("pins") or {}).items()
                             if v.get("port") != "PA9"}
            iface_module.pinout_module.find = lambda *a, **k: small  # type: ignore[assignment]
            missing = iface_module.build(_design(), ioc_path=IOC_GOOD)
        finally:
            iface_module.pinout_module.find = original_find  # type: ignore[assignment]
        checks = [w["check"] for w in missing.get("warnings") or []]
        check("3e. 表里没有的端口 → `ioc.port_not_in_package` 软提示（不静默跳过）",
              "ioc.port_not_in_package" in checks, str(checks))
    finally:
        store.delete()
        for path in made:
            path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 4. 真实项目：0 冲突 + 具体对应 + 退化（换表 / 拿掉表）
# ---------------------------------------------------------------------------


def part_real() -> None:
    if not (REAL_DESIGN.is_file() and REAL_IOC.is_file()):
        print(_safe(f"[skip] 4. 没有真实项目（{REAL_DESIGN}）—— 这一节**没跑**，不是通过"))
        return
    design = DesignModel.from_dict(
        json.loads(REAL_DESIGN.read_text(encoding="utf-8")))
    payload = iface_module.build(design, ioc_path=REAL_IOC)
    join = payload.get("pin_join") or {}
    rows = {row["port"]: row for row in join.get("rows") or []}

    # 这五对**来自两个独立来源**（CubeMX 的端口名 / EasyEDA 的封装脚号），
    # 表必须让它们对上——对不上说明表错了。
    expected = {"PA9": ("30", "UART_TX"), "PA10": ("31", "UART_RX"),
                "PD0": ("5", "OSC_IN"), "PD1": ("6", "OSC_OUT"),
                "PA8": ("29", "GPS_EN")}
    got = {port: (rows[port]["pin"], rows[port]["net"])
           for port in expected if port in rows}
    check("4a. **端到端**：19 个 `.ioc` 脚全部落到封装脚号上，0 冲突",
          payload["status"] == "ok" and len(rows) == 19 and not _conflicts(payload),
          f"{len(rows)} 行，冲突 {len(_conflicts(payload))}")
    check("4b. 具体对应全对（PA9↔30 UART_TX / PD0↔5 OSC_IN / PA8↔29 GPS_EN…）",
          got == expected, f"期望 {expected} 实际 {got}")

    unrouted = {row["port"] for row in join.get("rows") or []
                if row["status"] == "unrouted"}
    check("4c. `.ioc` 配了但网表没接的脚逐条列出（USART2/SPI2 共 5 个）",
          unrouted == {"PA2", "PA3", "PB13", "PB14", "PB15"},
          str(sorted(unrouted)))

    # 退化一：表里把 26（PB13）与 30（PA9）对调 → 必须变红
    table = pinout_module.load("stm32f103c8t6") or {}
    swapped = json.loads(json.dumps(table))
    a, b = swapped["pins"]["26"], swapped["pins"]["30"]
    swapped["pins"]["26"], swapped["pins"]["30"] = b, a
    original_find = iface_module.pinout_module.find
    try:
        iface_module.pinout_module.find = lambda *x, **k: swapped  # type: ignore[assignment]
        broken = iface_module.build(design, ioc_path=REAL_IOC)
    finally:
        iface_module.pinout_module.find = original_find  # type: ignore[assignment]
    bad = _conflicts(broken)
    check("4d. 退化：表里 26↔30 对调 → 真实项目**变红**（证明表真的在判）",
          broken["status"] == "failed" and bool(bad),
          str([p["message"][:110] for p in bad]))

    # 退化二：拿掉表 → 冲突归零，但"脚号对照"必须回到 unknown（不是永远绿）
    try:
        iface_module.pinout_module.find = lambda *x, **k: None  # type: ignore[assignment]
        blind = iface_module.build(design, ioc_path=REAL_IOC)
    finally:
        iface_module.pinout_module.find = original_find  # type: ignore[assignment]
    what = [u["what"] for u in blind.get("unknown") or []]
    check("4e. 没有表时：不判冲突、`pin_join` 为空、脚号对照回到 unknown",
          not _conflicts(blind) and not (blind.get("pin_join") or {}).get("rows")
          and any("封装脚号" in w for w in what), str(what))


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------


def part_cli() -> None:
    listed = design_cli.cmd_pinout(["list"])
    check("5a. `pinout list` 列出全部表且状态 ok",
          listed.get("status") == "ok" and listed.get("count", 0) >= 4,
          f"{listed.get('count')} 张")

    shown = design_cli.cmd_pinout(["show", "stm32f103c8t6"])
    check("5b. `pinout show` 给整张表（含 30=PA9）",
          shown.get("status") == "ok"
          and (shown.get("pins") or {}).get("30", {}).get("port") == "PA9")

    bad = design_cli.cmd_pinout(["show", "no-such-part"])
    check("5c. 没有的型号 → `not_ready` 并列出已有的（不是空表）",
          bad.get("status") == "not_ready" and bad.get("known"), str(bad.get("known")))

    nobuild = design_cli.cmd_pinout(["build"])
    check("5d. `pinout build` 缺 LCSC → `not_ready`（不联网碰运气）",
          nobuild.get("status") == "not_ready", str(nobuild.get("reason")))

    validated = design_cli.cmd_pinout(["validate"])
    check("5e. `pinout validate` 全表通过",
          validated.get("status") == "ok"
          and validated.get("checked", 0) == validated.get("count", -1)
          and validated.get("failed") == 0,
          f"checked={validated.get('checked')} failed={validated.get('failed')}")


def main() -> int:
    print("=== P56 引脚定义表：`.ioc` 的 PA9 ↔ 网表的 30（逐脚对账）===")
    part_tables()
    part_refusal()
    part_conflicts()
    part_real()
    part_cli()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())