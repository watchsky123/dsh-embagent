"""软硬接口契约（`interface`）—— 让固件和硬件**共用同一份引脚事实**。

## 这一层解决什么问题

在这之前，"软件"和"硬件"只有一条线连着：`fw-gen pins` 吐一个 `pins.h`
（引脚名）。除此之外全是**口头约定**：

| 固件需要的 | 原来从哪来 |
|---|---|
| 这个脚是哪路 SPI 的第几根线 | 人记 |
| 波特率 / 时钟 / 中断 | 人记，代码里写死 |
| 复用功能（这个脚能不能做 I2C）| 人查 datasheet |
| 哪些脚是电源/地（不能当 GPIO 用）| 人记 |

结果就是：**板子改了、固件不知道**。`fw-matrix` 只能事后发现"用了不存在的脚"，
发现不了"SPI 换了一组脚"。

`interface.json` 是那份**契约**：`design.json`（引脚 + 网表）+ `.ioc`（外设配置）
合成一份，固件侧读它、检查器也读它。

## 三态纪律（`.ioc` 会把中间那行添满）

| 项 | 没有 `.ioc` | 有 `.ioc` |
|---|---|---|
| 引脚 ↔ 网络 | ✅ 逐引脚对（**唯一硬判据**）| 同左 |
| 外设分组（SPI/I2C/UART…）| 按网名族**推断**（标 `inferred`）| 按 `.ioc`（标 `ioc`），并带实例名 |
| 时钟树 / 中断 / 复用功能 | `unknown` | **声明的**（`source=ioc`，见 `ioc.py`）|
| AF **编号** / 时序 / 封装脚号对照 | `unknown` | **仍然 `unknown`**（要 datasheet）|

`.ioc` 只解决"配置"，不解决"物理引脚对照"：它写 `PA9`，网表写 `30`，
中间那张 datasheet 的引脚定义表本仓库没有 —— 所以逐脚对账**做不到**，
`unknown` 里会照实写这一条，而不是编一张表。

## `.ioc` 与 design.json 的芯片身份也要对得上

给错 `.ioc`（比如 F103 的板配了 H743 的文件）是很常见的操作失误。
`ioc.mcu_mismatch` 是**硬判据**（族或封装不同 → `failed`）；
同族同封装但型号不同（C6 vs C8）只给 `ioc.mcu_variant` 提示——
它们引脚兼容，但闪存/外设不一样，判 fail 会误伤。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import afmap as afmap_module
from . import ioc as ioc_module
from . import pinout as pinout_module
from . import power as power_mod
from .model import DesignModel

__all__ = ["build", "render", "describe", "FILE"]

FILE = "interface.json"

#: 网名 → 协议族（**按词元**，不是子串）。和 `links.PROTOCOL_FAMILIES` 同一套
#: 思路：`RF_RXEN` 不该被判成 UART。这里只做**接口契约**用，不参与判决。
_NET_FAMILY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("usb", ("usb_dp", "usb_dm", "usbdp", "usbdm", "dp1", "dn1")),
    ("spi", ("spi", "sck", "mosi", "miso", "nss", "flash_cs")),
    ("i2c", ("scl", "sda", "i2c")),
    ("uart", ("uart", "usart", "txd", "rxd", "u1_tx", "u1_rx",
              "u3_tx", "u3_rx")),
    ("can", ("canh", "canl", "can")),
)

#: 这些词元的网**不是**接口（是使能/中断/状态脚）。命中就当 GPIO。
_NOT_INTERFACE = ("en", "en_", "int", "irq", "nrst", "reset", "busy", "dio",
                  "rxen", "detect", "rdy", "cs_", "pwr", "event", "armed",
                  "1pps", "pps")

#: 需要"接到外部"才有意义的族。配置了却没有对应网 = 值得看一眼。
_COMM_FAMILIES = ("uart", "spi", "i2c", "can", "usb", "sdmmc")


def _tokens(name: str) -> list[str]:
    out: list[str] = []
    cur = ""
    for ch in str(name or ""):
        if ch.isalnum():
            cur += ch.lower()
        elif cur:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def family_of(net: str) -> str | None:
    """网名 → 协议族。**判不出返回 None**（绝不猜）。"""
    tokens = _tokens(net)
    if not tokens:
        return None
    joined = "_".join(tokens)
    for bad in _NOT_INTERFACE:
        if any(t == bad or t.startswith(bad) and bad.endswith("_") for t in tokens):
            return None
    for family, words in _NET_FAMILY:
        if any(word in joined or word == joined for word in words):
            return family
    return None


def _direction(entry: dict[str, Any], family: str | None) -> str:
    """方向。**只能从"它在不在某条接口里"推**，推不出就 `unknown`。"""
    if family is None:
        return "unknown"
    return "io"


# ---------------------------------------------------------------------------
# .ioc 与 design.json 的芯片身份
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _mcu_family(kind: str) -> str:
    """`stm32f103c8t6` → `stm32f1`；`stm32h743ii` → `stm32h7`。

    认不出返回空串 —— 空串的语义是"判不出"，**不是**"对上了"。
    """
    match = re.match(r"stm32([a-z])(\d)", str(kind or "").lower())
    return f"stm32{match.group(1)}{match.group(2)}" if match else ""


def _mcu_table(kind: str) -> dict[str, str]:
    """design.json 的 kind → 型号表里的 `name` / `package`（**只读表，不编**）。"""
    try:
        from .fwgen import mcus  # noqa: PLC0415  延迟导入：表大且只有这里用
        mcu = mcus.find_mcu(str(kind or ""))
    except Exception:  # noqa: BLE001
        return {}
    if mcu is None:
        return {}
    return {"name": str(getattr(mcu, "name", "")),
            "package": str(getattr(mcu, "package", ""))}


def _identity_checks(mcu_kind: str, ref: str,
                     facts: dict[str, Any] | None
                     ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`.ioc` 的芯片身份 vs `design.json` → (硬冲突, 软提示)。

    能判的三级（从粗到细，**任一级不一致就停**，因为再往下比没意义）：

    1. 族：`.ioc` 的 `STM32F1` vs kind 的 `stm32f1`
    2. 封装：`LQFP48` vs 型号表的 `LQFP-48`
    3. 型号：`Mcu.CPN` vs 型号表的 `name`（同族同封装不同型号 = 变体）
    """
    problems: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not facts:
        return problems, warnings
    ioc_mcu = facts.get("mcu") or {}
    have_family = _norm(ioc_mcu.get("family"))
    ioc_name = ioc_mcu.get("cpn") or ioc_mcu.get("user_name") or ""
    ioc_pkg = _norm(ioc_mcu.get("package"))
    table = _mcu_table(mcu_kind)
    want_family = _mcu_family(mcu_kind)
    if not want_family:
        warnings.append({"check": "ioc.mcu_unknown", "refs": [ref],
                         "message": (f"design.json 的 MCU kind {mcu_kind!r} 认不出型号族，"
                                     f"无法和 .ioc（{ioc_name or have_family}）比对")})
        return problems, warnings
    if have_family and have_family != want_family:
        problems.append({
            "check": "ioc.mcu_mismatch", "refs": [ref],
            "message": (f".ioc 的芯片是 {ioc_name or have_family}"
                        f"（{ioc_mcu.get('family')}/{ioc_mcu.get('package')}），"
                        f"design.json 的 MCU 是 {mcu_kind}（{want_family}/"
                        f"{table.get('package') or '封装未知'}）—— 给错文件了？")})
        return problems, warnings
    design_pkg = _norm(table.get("package"))
    if ioc_pkg and design_pkg and ioc_pkg != design_pkg:
        problems.append({
            "check": "ioc.mcu_mismatch", "refs": [ref],
            "message": (f".ioc 是 {ioc_mcu.get('package')}，design.json 的 MCU"
                        f"（{table.get('name') or mcu_kind}）是 {table.get('package')}")})
        return problems, warnings
    if ioc_pkg and not design_pkg:
        warnings.append({"check": "ioc.mcu_unknown", "refs": [ref],
                         "message": (f".ioc 写的是 {ioc_mcu.get('package')}，"
                                     f"型号表里没有 {mcu_kind} 的封装 → 这一级没比")})
    design_name = _norm(table.get("name"))
    if _norm(ioc_name) and design_name and _norm(ioc_name) != design_name:
        warnings.append({
            "check": "ioc.mcu_variant", "refs": [ref],
            "message": (f".ioc 的芯片是 {ioc_name}，设计里是 {table.get('name')}"
                        f"（同族同封装：引脚兼容，闪存/外设可能不同）")})
    return problems, warnings


# ---------------------------------------------------------------------------
# 合成
# ---------------------------------------------------------------------------


def _join_ioc_pins(facts: dict[str, Any], table: dict[str, Any],
                   pin_net: dict[tuple[str, str], str], mcu_ref: str,
                   power_nets: list[str], ground_nets: list[str],
                   ) -> dict[str, Any]:
    """`.ioc` 的端口名 ↔ 网表的封装脚号 —— **逐脚对账**（P56）。

    没有这张表时 P55 只能"按名字对"；有了表，`PA9` 才真的等于网表的 `30`。

    判据（**硬**的只有两条）：
    * `.ioc` 配了通信外设，而网表把这个脚接去**别的协议族** → 冲突
    * `.ioc` 配了通信外设，而网表把这个脚接去**电源/地** → 冲突

    其余是**软**提示（可能只是网名没写族关键词，或者线没接）：
    通信外设脚在网表里没网、网名不含族关键词、TX 网接在 RX 脚上。
    """
    port_to_pin: dict[str, str] = {}
    for number, row in (table.get("pins") or {}).items():
        port = str((row or {}).get("port") or "")
        if port:
            port_to_pin[port] = str(number)
    rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    missing_ports: list[str] = []
    for port, info in sorted((facts.get("pins") or {}).items()):
        number = port_to_pin.get(port)
        if number is None:
            missing_ports.append(port)
            continue
        net = pin_net.get((mcu_ref, number), "")
        family = family_of(net) if net else None
        ioc_family = str(info.get("family") or "")
        kind = str(info.get("kind") or "")
        is_power = bool(net) and (net in power_nets or net in ground_nets)
        status = "ok"
        if is_power and ioc_family:
            status = "conflict"
            conflicts.append({
                "check": "ioc.pin_conflict", "refs": [mcu_ref],
                "message": (f".ioc 把 {port} 配成 {info.get('function')}（{ioc_family}），"
                            f"但网表把封装脚 {number} 接在电源/地 {net!r} 上"
                            f" —— 一个脚不可能既通信又接地")})
        elif family and ioc_family and family != ioc_family:
            status = "conflict"
            conflicts.append({
                "check": "ioc.pin_conflict", "refs": [mcu_ref],
                "message": (f".ioc 把 {port}（脚 {number}）配成 {info.get('function')}"
                            f"（{ioc_family}），网表的脚 {number} 是 {net!r}（{family}）")})
        elif ioc_family and not net:
            status = "unrouted"
            warnings.append({
                "check": "ioc.pin_unrouted", "refs": [mcu_ref],
                "message": (f".ioc 把脚 {number}（{port}）配成 {info.get('function')}"
                            f"（{ioc_family}），网表里这个脚**没接**")})
        elif ioc_family and not family:
            status = "family_unknown"
            warnings.append({
                "check": "ioc.pin_family_unknown", "refs": [mcu_ref],
                "message": (f".ioc 把脚 {number}（{port}）配成 {info.get('function')}"
                            f"（{ioc_family}），网表接的是 {net!r}"
                            f" —— 网名不含族关键词，**判不出对错**")})
        if status == "ok" and ioc_family and family:
            ioc_role = {"tx": "data_out", "rx": "data_in"}.get(str(info.get("role") or ""),
                                                              str(info.get("role") or ""))
            net_role = _signal_role(net, family)
            if ioc_role and net_role and ioc_role != net_role:
                status = "role_soft"
                warnings.append({
                    "check": "ioc.pin_role_conflict", "refs": [mcu_ref],
                    "message": (f"脚 {number}（{port}）：`.ioc` 是 {info.get('function')}"
                                f"（{ioc_role}），网表 {net!r}（{net_role}）"
                                f" —— 方向可能反了（TX 网接在 RX 脚）")})
        rows.append({"port": port, "pin": number, "function": info.get("function", ""),
                     "kind": kind, "family": ioc_family,
                     "net": net, "net_family": family or "", "status": status})
    if missing_ports:
        warnings.append({
            "check": "ioc.port_not_in_package", "refs": [mcu_ref],
            "message": (f"`.ioc` 里 {', '.join(missing_ports[:6])}"
                        f"{' 等' if len(missing_ports) > 6 else ''} 在这颗芯片的"
                        f"引脚表里没有 —— 封装/型号对不上？")})
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    return {"rows": rows, "conflicts": conflicts, "warnings": warnings,
            "missing_ports": missing_ports, "by_status": by_status,
            "table": {"part": table.get("part"), "lcsc": table.get("lcsc"),
                      "package": table.get("package"),
                      "count": table.get("count"),
                      "source": table.get("source"),
                      "retrieved": table.get("retrieved")}}


#: 这些族的信号"缺失"才判硬冲突 —— 模拟/EXTI/调试脚缺失只算 unknown。
_AF_HARD_FAMILIES = ("USART", "UART", "LPUART", "SPI", "I2S", "I2C", "FDCAN",
                     "CAN", "USB", "SDMMC", "QUADSPI", "OCTOSPI", "ETH",
                     "DCMI", "LTDC", "SAI")


def _resolve_af(facts: dict[str, Any], table: dict[str, Any]
                ) -> dict[str, Any]:
    """`.ioc` 的每个功能 → AF 表里的结论（P57）。

    **硬冲突只有一种**：`.ioc` 把某个通信/显示外设放到这个脚上，而这颗芯片的
    这个脚**明确没有这一类功能**（表里同族信号一个都没有）。模拟/EXTI/调试
    脚缺失只算 `unknown` —— 那是"表没收录"，不是"芯片不能"。
    """
    rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    af_numbers: dict[str, int] = {}
    details: dict[str, list[dict[str, Any]]] = {}
    for pin, info in sorted((facts.get("pins") or {}).items()):
        signal = str(info.get("signal") or "")
        if not signal:
            continue
        got = afmap_module.match_signal(table, pin, signal)
        status = str(got.get("status") or "unknown")
        row = {"pin": pin, "signal": signal, "status": status,
               "matched": got.get("signal", ""), "af": got.get("af"),
               "remap": got.get("remap"), "via": got.get("via", "")}
        rows.append(row)
        if status == "ok":
            details.setdefault(pin, []).append({
                "signal": got.get("signal"), "af": got.get("af"),
                "remap": got.get("remap"), "via": got.get("via", "")})
            if got.get("af") is not None:
                af_numbers[pin] = int(got["af"])
        elif status == "not_on_pin":
            family = str(got.get("family") or "")
            same = ", ".join(got.get("pin_functions") or []) or "没有同类功能"
            message = (f".ioc 把 {pin} 配成 {signal}，但这颗芯片（{table.get('part')}）"
                       f"的 {pin} 只有：{same} —— 这个功能不在这根脚上")
            if family in _AF_HARD_FAMILIES:
                conflicts.append({"check": "ioc.function_not_on_pin",
                                  "refs": [str(table.get("part") or "")],
                                  "message": message})
            else:
                warnings.append({"check": "ioc.function_not_in_table",
                                 "refs": [], "message": message + "（非通信族，只提示）"})
        elif status == "no_pin":
            warnings.append({"check": "ioc.pin_not_in_afmap", "refs": [],
                             "message": f"{pin} 不在这颗芯片的 AF 表里（{table.get('part')}）"})
        elif status == "unknown":
            warnings.append({"check": "ioc.function_unknown", "refs": [],
                             "message": (f"{pin}={signal}：AF 表里没有这个信号，"
                                         f"**判不出**（不是冲突）")})
        # `gpio` / `exti` 不需要查 AF 表 —— 不是缺口，也不该进 warning
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    return {"rows": rows, "conflicts": conflicts, "warnings": warnings,
            "af_numbers": af_numbers, "details": details,
            "by_status": by_status,
            "table": {"part": table.get("part"), "family": table.get("family"),
                      "style": table.get("style"), "source": table.get("source"),
                      "retrieved": table.get("retrieved"),
                      "license": table.get("license"),
                      "af_functions": table.get("af_functions")}}


def _unknown_items(clock: dict[str, Any], nvic: dict[str, Any],
                   af: dict[str, Any], facts: dict[str, Any] | None,
                   *extra: Any) -> list[dict[str, Any]]:
    """**缺的东西一律 unknown + 写明缺什么**，不猜（AGENTS §0.1）。

    没有 `.ioc` 时是四类老缺口（保持 P50 的形状）；有 `.ioc` 时只剩
    "它本来就给不出"的三类：AF 编号 / 时序 / 脚号对照。
    """
    joined = bool(extra[0]) if extra else False
    af_resolved = bool(extra[1]) if len(extra) > 1 else False
    if not facts:
        return [
            {"what": "时钟树（HCLK/PCLK/外设时钟）",
             "need": "MCU 型号对应的时钟配置或 .ioc"},
            {"what": "中断向量与优先级",
             "need": ".ioc 的 NVIC 配置或固件里的 IRQ handler 表"},
            {"what": "GPIO 复用功能表（AF map）",
             "need": "datasheet 的 AF 表（本仓库未录）"},
            {"what": "时序约束（建立/保持、采样时刻）",
             "need": "器件 datasheet 的时序章节"},
        ]
    items: list[dict[str, Any]] = []
    # 只报**派生时钟**的缺口；HSE/HSI/LSE 是源时钟，缺它们通常意味着"没用外部晶振"，
    # 那是配置选择而不是缺口——它们仍然会出现在 `## 时钟` 段的"没写的"一行里。
    derived = ("sysclk_hz", "hclk_hz", "apb1_hz", "apb2_hz")
    absent = [name for name in clock.get("absent") or [] if name in derived]
    if absent:
        items.append({"what": f"时钟量 {', '.join(absent)}",
                      "need": "RCC 里没有这些行（.ioc 只写非默认值），不替它填"})
    if nvic.get("status") != "declared":
        items.append({"what": "中断向量与优先级",
                      "need": ".ioc 里没有 NVIC 段（文件里确实没有）"})
    if not af_resolved:
        if af.get("status") == "unknown":
            items.append({"what": "GPIO 复用功能表（AF map）",
                          "need": "datasheet 的 AF 表（本仓库未录）"})
        else:
            items.append({"what": "复用功能的 **AF 编号**（PA9 做 USART1_TX 是 AF7 还是 AF8）",
                          "need": "datasheet 的复用功能表（.ioc 只给功能名，不给编号）"})
    items.append({"what": "时序约束（建立/保持、采样时刻）",
                  "need": "器件 datasheet 的时序章节"})
    if not joined:
        items.append({"what": "封装脚号 ↔ 端口名的对照（`.ioc` 的 PA9 ↔ 网表的 30）",
                      "need": "datasheet 的引脚定义表；缺它只能按名字对，不能逐脚对"})
    return items


def build(design: DesignModel, *, ioc_path: str | Path | None = None) -> dict[str, Any]:
    """合成契约。**唯一硬判据是"引脚 ↔ 网络"逐条一致。**"""
    nets = [e for e in design.nets if isinstance(e, dict)]
    parts = {p.ref: p for p in design.parts}
    declared_power = (design.power_nets
                      if isinstance(design.power_nets, dict) else {})
    classified = power_mod.classify(
        declared_power, [str(e.get("net") or "") for e in nets])
    power_nets = list(classified.get("power") or [])
    ground_nets = list(classified.get("ground") or [])

    # 1) 引脚 → 网络（design.json 是唯一事实源）
    pin_net: dict[tuple[str, str], str] = {}
    for entry in nets:
        net = str(entry.get("net") or "").strip()
        if not net:
            continue
        for pin in entry.get("pins") or []:
            if not isinstance(pin, dict):
                continue
            ref = str(pin.get("ref") or "").strip()
            name = str(pin.get("pin") or "").strip()
            if ref and name:
                pin_net[(ref, name)] = net

    mcus = [p for p in design.parts
            if str(getattr(p, "kind", "")).lower().startswith(
                ("stm32", "atmega", "esp32", "avr", "mcu"))
            or str(getattr(p, "role", "")) == "mcu"]
    mcu = mcus[0] if mcus else None

    # 2) `.ioc`：外设配置（时钟 / 中断 / 复用功能 / 外设参数）。
    #    **读失败不算通过**：记 error，其余照旧走 unknown。
    facts: dict[str, Any] | None = None
    ioc_info: dict[str, Any] = {"present": False}
    if ioc_path:
        try:
            facts = ioc_module.parse(ioc_path)
            ioc_info = {"present": True, "path": facts["path"],
                        "file": facts["file"], "counts": facts["counts"],
                        "mcu": facts["mcu"],
                        "peripheral_config": len(facts["peripherals"])}
        except Exception as exc:  # noqa: BLE001
            ioc_info = {"present": False, "path": str(ioc_path),
                        "error": f"{type(exc).__name__}: {exc}"}

    # 3) 外设分组：按 MCU 的引脚 + 协议族聚；有 `.ioc` 就带上实例名
    instances_by_family: dict[str, list[str]] = {}
    if facts:
        for name, info in facts["peripherals"].items():
            if info.get("family"):
                instances_by_family.setdefault(info["family"], []).append(name)
    peripherals: list[dict[str, Any]] = []
    if mcu is not None:
        groups: dict[str, list[dict[str, Any]]] = {}
        for (ref, name), net in sorted(pin_net.items()):
            if ref != mcu.ref:
                continue
            family = family_of(net)
            if family is None:
                continue
            groups.setdefault(family, []).append({
                "pin": name, "net": net,
                "role": _signal_role(net, family),
            })
        for family, pins in sorted(groups.items()):
            instances = sorted(instances_by_family.get(family) or [])
            if instances:
                source, note = "ioc", f".ioc 里配了 {'/'.join(instances)}"
            elif facts:
                source, note = "net", "网名族推断；.ioc 没配这类外设"
            else:
                source, note = "inferred", "从网名族推断（没有 .ioc）"
            peripherals.append({"family": family, "pins": pins,
                                "source": source, "ioc_instances": instances,
                                "note": note})

    # 4) 逐引脚契约
    pins: list[dict[str, Any]] = []
    for part in design.parts:
        if mcu is not None and part.ref != mcu.ref:
            continue
        for name in (part.pins or []):
            net = pin_net.get((part.ref, str(name)))
            family = family_of(net) if net else None
            is_power = bool(net) and net in power_nets
            is_ground = bool(net) and net in ground_nets
            pins.append({
                "ref": part.ref, "pin": str(name), "net": net or "",
                "kind": ("ground" if is_ground else "power" if is_power
                         else "signal" if net else "unconnected"),
                "family": family,
                "direction": ("-" if (is_power or is_ground)
                              else _direction({}, family)),
                "usable_as_gpio": (not net) or (
                    not is_power and not is_ground and family is None),
                "source": "design",
            })

    # 5) 一致性：**唯一硬判据** + `.ioc` 的芯片身份
    problems: list[dict[str, Any]] = []
    declared = set()
    for part in design.parts:
        for name in (part.pins or []):
            declared.add((part.ref, str(name)))
    for (ref, name) in pin_net:
        if ref not in parts:
            problems.append({"check": "iface.pin_dangling", "refs": [ref],
                             "message": f"网表里 {ref}.{name} 指向不存在的元件"})
        elif (ref, name) not in declared:
            problems.append({"check": "iface.pin_missing", "refs": [ref],
                             "message": f"网表里用了 {ref}.{name}，但元件没有这个脚"})
    warnings: list[dict[str, Any]] = []
    if facts:
        hard, soft = _identity_checks(
            str(getattr(mcu, "kind", "")) if mcu is not None else "",
            str(getattr(mcu, "ref", "")) if mcu is not None else "",
            facts)
        problems += hard
        warnings += soft
        # 通信外设两个方向都对一遍（**soft**：可能没接，也可能网名不在族里）
        comm_ioc: dict[str, list[str]] = {}
        for name, info in facts["peripherals"].items():
            if info.get("kind") == "comm" and info.get("pins"):
                comm_ioc.setdefault(str(info.get("family")), []).append(name)
        net_fams: dict[str, set[str]] = {}
        for (ref, _name), net in pin_net.items():
            if mcu is not None and ref != mcu.ref:
                continue
            family = family_of(net)
            if family:
                net_fams.setdefault(family, set()).add(net)
        for family, names in sorted(comm_ioc.items()):
            if family not in net_fams:
                warnings.append({
                    "check": "ioc.peripheral_unrouted", "families": [family],
                    "message": (f".ioc 配了 {'/'.join(sorted(names))}（{family}），"
                                f"网表里没有任何 {family} 网 —— 可能没接，"
                                f"也可能网名不含族关键词")})
        for family, nets_of in sorted(net_fams.items()):
            if family not in comm_ioc:
                warnings.append({
                    "check": "ioc.peripheral_unconfigured", "families": [family],
                    "message": (f"网表里有 {family} 网（"
                                f"{', '.join(sorted(nets_of)[:4])}），"
                                f"但 .ioc 没配这类外设 —— 固件里可能没初始化")})
    # 6) **逐脚对账**（P56）：需要引脚定义表（封装脚号 ↔ 端口名）
    joined: dict[str, Any] | None = None
    table: dict[str, Any] | None = None
    if facts and mcu is not None:
        table = pinout_module.find(str(getattr(mcu, "kind", "")),
                                   str(getattr(mcu, "lcsc", "") or ""))
        if table is not None:
            joined = _join_ioc_pins(facts, table, pin_net, mcu.ref,
                                    power_nets, ground_nets)
            problems += joined["conflicts"]
            warnings += joined["warnings"]
    # 7) **AF 复用表**（P57）：这个脚能不能做这个功能、编号几
    af_table: dict[str, Any] | None = None
    af_resolved: dict[str, Any] | None = None
    if facts and mcu is not None:
        af_table = afmap_module.find(str(getattr(mcu, "kind", "")))
        if af_table is not None:
            af_resolved = _resolve_af(facts, af_table)
            problems += af_resolved["conflicts"]
            warnings += af_resolved["warnings"]
    # 未接的 MCU 脚是正常的 —— 不报问题，只在 `pins` 里记 kind=unconnected
    status = "failed" if problems else (
        "ok" if any(p["kind"] in ("power", "signal") for p in pins) else "not_ready")

    clock = ({**facts["clock"], "file": facts["file"]} if facts else
             {"status": "unknown", "source": "none",
              "need": "MCU 型号对应的时钟配置或 .ioc"})
    nvic = ({**facts["nvic"], "file": facts["file"]} if facts else
            {"status": "unknown", "source": "none",
             "need": ".ioc 的 NVIC 配置或固件里的 IRQ handler 表"})
    af = (dict(facts["af"], status="partial") if facts else
          {"status": "unknown", "source": "none",
           "need": "datasheet 的 AF 表（本仓库未录）"})
    if joined:
        af = dict(af, join="pinout",
                  pin_numbers={row["port"]: int(row["pin"])
                               for row in joined["rows"]},
                  table=joined["table"])
    if af_resolved:
        af = dict(af, resolved=True, style=af_resolved["table"].get("style"),
                  af_numbers=af_resolved["af_numbers"],
                  details=af_resolved["details"],
                  by_status=af_resolved["by_status"],
                  af_table=af_resolved["table"])

    counts = {
        "pins": len(pins),
        "connected": sum(1 for p in pins if p["net"]),
        "unconnected": sum(1 for p in pins if not p["net"]),
        "power": sum(1 for p in pins if p["kind"] == "power"),
        "ground": sum(1 for p in pins if p["kind"] == "ground"),
        "peripherals": len(peripherals),
        "ioc_pins": len(facts["pins"]) if facts else 0,
        "ioc_peripherals": len(facts["peripherals"]) if facts else 0,
        "ioc_joined": len(joined["rows"]) if joined else 0,
        "af_resolved": len(af_resolved["af_numbers"]) if af_resolved else 0,
        "af_checked": len(af_resolved["rows"]) if af_resolved else 0,
    }
    return {
        "status": status,
        "mcu": ({"ref": mcu.ref, "kind": str(getattr(mcu, "kind", "")),
                 "pins": len(mcu.pins or [])} if mcu is not None else {}),
        "pins": pins,
        "peripherals": peripherals,
        "power_nets": sorted(power_nets),
        "ground_nets": sorted(ground_nets),
        "power_source": "declared" if (design.power_nets or {}) else "inferred",
        "ioc": ioc_info,
        "clock": clock,
        "nvic": nvic,
        "af": af,
        "peripheral_config": (facts["peripherals"] if facts else {}),
        "pin_join": (joined or {}),
        "problems": problems,
        "warnings": warnings,
        "counts": counts,
        "unknown": _unknown_items(clock, nvic, af, facts, bool(joined),
                                  bool(af_resolved)),
        "note": ("硬判据 = 引脚↔网络逐条一致 + `.ioc` 芯片身份 + "
                 "**逐脚协议族冲突**（有引脚表时）+ 功能是否真在这根脚上（有 AF 表时）；"
                 "有 .ioc 时时钟/中断/复用功能/外设参数是声明的（source=ioc），"
                 "AF 编号/时序仍 unknown；**配置对不证明功能对**"),
    }


def _signal_role(net: str, family: str) -> str:
    tokens = _tokens(net)
    if family == "spi":
        for key, role in (("sck", "clock"), ("mosi", "data_out"),
                          ("miso", "data_in"), ("nss", "chip_select"),
                          ("spi", "unknown")):
            if any(key in t for t in tokens):
                return role
        return "unknown"
    if family == "i2c":
        if any("scl" in t for t in tokens):
            return "clock"
        if any("sda" in t for t in tokens):
            return "data"
        return "unknown"
    if family == "uart":
        if any(t in ("tx", "txd") or t.endswith("tx") for t in tokens):
            return "data_out"
        if any(t in ("rx", "rxd") or t.endswith("rx") for t in tokens):
            return "data_in"
        return "unknown"
    if family == "usb":
        return "data"
    return "unknown"


def _clock_lines(clock: dict[str, Any]) -> list[str]:
    if clock.get("status") in (None, "unknown"):
        return [f"* unknown —— 需要：{clock.get('need', '')}"]
    rows = []
    for name, key in (("SYSCLK", "sysclk_hz"), ("HCLK", "hclk_hz"),
                      ("AHB", "ahb_hz"), ("APB1", "apb1_hz"),
                      ("APB2", "apb2_hz"), ("HSE", "hse_hz")):
        if clock.get(key):
            rows.append(f"{name} **{clock[key] / 1_000_000:g} MHz**")
    source = clock.get("sysclk_source") or ""
    dividers = ", ".join(f"{k}={v}" for k, v in
                         (clock.get("dividers") or {}).items())
    lines = ["* " + " · ".join(rows) + (f"（源 {source}）" if source else "")]
    if dividers:
        lines.append(f"* 分频/倍频：{dividers}")
    if clock.get("absent"):
        lines.append(f"* **没写的**：{', '.join(clock['absent'])}"
                     "（.ioc 只写非默认值，不替它填）")
    return lines


def _nvic_lines(nvic: dict[str, Any]) -> list[str]:
    if nvic.get("status") in (None, "unknown"):
        return [f"* unknown —— 需要：{nvic.get('need', '')}"]
    lines = [f"* 优先级分组 **{nvic.get('priority_group') or '?'}**，"
             f"用户中断 {len(nvic.get('irqs') or [])} 条，"
             f"内核异常 {len(nvic.get('core') or [])} 条"]
    for row in (nvic.get("irqs") or []):
        lines.append(f"* `{row['irq']}` 抢占 {row['preempt']} / 子 {row['sub']}")
    if nvic.get("time_base"):
        lines.append(f"* 时基 {nvic.get('time_base')}（{nvic.get('time_base_ip')}）")
    return lines


def _config_lines(config: dict[str, Any]) -> list[str]:
    if not config:
        return ["_无（没有 .ioc）_"]
    lines = []
    for name, info in sorted(config.items()):
        params = info.get("params") or {}
        shown = ", ".join(f"{k}={v}" for k, v in params.items()
                          if k != "VirtualMode") or "（只写了 VirtualMode）"
        pins_of = ", ".join(f"{p}={s}" for p, s in (info.get("pins") or {}).items())
        tag = ", ".join(str(x) for x in (info.get("family"), info.get("mode")) if x)
        lines.append(f"* **{name}**" + (f"（{tag}）" if tag else "")
                     + f"：{shown}" + (f"；脚 {pins_of}" if pins_of else ""))
        missing = info.get("tracked_not_written") or []
        if missing:
            lines.append(f"  * 跟踪了但没写值：{', '.join(missing)}"
                         "（**推断**：可能用了默认值）")
    return lines


def render(payload: dict[str, Any]) -> str:
    """人读版（`interface.md`）：引脚表 + 外设 + 配置 + 缺口。"""
    counts = payload.get("counts") or {}
    mcu = payload.get("mcu") or {}
    lines = [f"# 软硬接口契约（{mcu.get('ref', '?')} {mcu.get('kind', '')}）", "",
             f"> {payload.get('note', '')}", "",
             f"引脚 {counts.get('pins')}（连通 {counts.get('connected')} / "
             f"未接 {counts.get('unconnected')}），电源 {counts.get('power')}、"
             f"地 {counts.get('ground')}，外设组 {counts.get('peripherals')}"
             + (f"，`.ioc` 脚 {counts.get('ioc_pins')}" if counts.get("ioc_pins") else ""),
             ""]
    ioc = payload.get("ioc") or {}
    lines += ["## .ioc", ""]
    if ioc.get("present"):
        lines.append(f"* `{ioc.get('file')}`：脚 {ioc.get('counts', {}).get('pins')}、"
                     f"外设 {ioc.get('counts', {}).get('peripherals')}、"
                     f"中断 {ioc.get('counts', {}).get('irqs')}")
    elif ioc.get("error"):
        lines.append(f"* **读不了**：{ioc['error']}")
    else:
        lines.append("_没有 .ioc（外设分组按网名推断，时钟/中断/复用功能全 unknown）_")
    lines += ["", "## 外设", ""]
    if payload.get("peripherals"):
        for group in payload["peripherals"]:
            pins = ", ".join(f"{p['pin']}={p['net']}" for p in group["pins"])
            inst = group.get("ioc_instances") or []
            lines.append(f"* **{group['family']}**（{group['source']}"
                         + (f"：{'/'.join(inst)}" if inst else "") + f"）：{pins}")
    else:
        lines.append("_无（没有 .ioc，也没有可识别的接口网名）_")
    lines += ["", "## 时钟", ""] + _clock_lines(payload.get("clock") or {})
    lines += ["", "## 中断", ""] + _nvic_lines(payload.get("nvic") or {})
    af = payload.get("af") or {}
    af_head = "## 复用功能（功能名"
    if (af.get("af_numbers") or {}):
        af_head += " + AF 编号"
    elif af.get("style") == "F1_REMAP":
        af_head += " + AFIO 重映射（F1 没有 AF 编号）"
    elif af.get("resolved"):
        af_head += "）"
    lines += ["", af_head + "）", ""]
    functions = af.get("functions") or {}
    if functions:
        numbers = af.get("af_numbers") or {}
        details = af.get("details") or {}
        shown = []
        for pin, fn in sorted(functions.items()):
            tag = ""
            if pin in numbers:
                tag = f"（AF{numbers[pin]}）"
            elif pin in details:
                remaps = [d.get("remap") for d in details[pin] or []
                          if d.get("remap")]
                macros = [m for r in remaps for m in (r.get("macros") or [])]
                if macros:
                    tag = f"（重映射 {macros[0]}）"
                elif remaps:
                    tag = "（默认功能）"
            shown.append(f"{pin}={fn}{tag}")
        lines.append(", ".join(shown))
        lines.append("")
        table_info = af.get("af_table") or {}
        if table_info:
            lines.append(f"> 功能名/AF 来自 **{table_info.get('source_kind') or 'ST 官方引脚库'}**"
                         f"（{table_info.get('part')}，{table_info.get('license')}）；"
                         f"`.ioc` 的功能不在表里 = `unknown`，不在**这根脚**上 = 冲突")
        elif af.get("resolved"):
            lines.append(f"> {af.get('note') or ''}")
    join = payload.get("pin_join") or {}
    if join.get("rows"):
        meta = join.get("table") or {}
        lines += ["", "## 逐脚核对（`.ioc` ↔ 网表）", "",
                  f"> 引脚表 `{meta.get('part')}`（{meta.get('lcsc')}，"
                  f"{meta.get('package')}，{meta.get('source')}，"
                  f"取于 {meta.get('retrieved')}）—— 端口名 ↔ 封装脚号由它给",
                  "", "| 端口 | 封装脚 | `.ioc` 功能 | 网表 | 结论 |", "|---|---|---|---|---|"]
        for row in join["rows"]:
            lines.append(f"| {row['port']} | {row['pin']} | {row['function']} | "
                         f"{row['net'] or '—'} | {row['status']} |")
    lines += ["", "## 外设配置（`.ioc` 里写出来的值）", ""]
    lines += _config_lines(payload.get("peripheral_config") or {})
    lines += ["", "## 引脚", "", "| 脚 | 网络 | 类别 | 方向 | 可否当 GPIO |", "|---|---|---|---|---|"]
    for row in payload.get("pins") or []:
        lines.append(f"| {row['pin']} | {row['net'] or '—'} | {row['kind']} | "
                     f"{row['direction']} | {'是' if row['usable_as_gpio'] else '否'} |")
    lines += ["", "## 还不知道的（**unknown，不是通过**）", ""]
    for item in payload.get("unknown") or []:
        lines.append(f"* {item.get('what')} —— 需要：{item.get('need')}")
    if payload.get("problems"):
        lines += ["", "## 不一致（硬）", ""]
        for row in payload["problems"]:
            lines.append(f"* {row.get('check')}：{row.get('message')}")
    if payload.get("warnings"):
        lines += ["", "## 提示（软，不判 fail）", ""]
        for row in payload["warnings"]:
            lines.append(f"* {row.get('check')}：{row.get('message')}")
    return "\n".join(lines) + "\n"


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.interface",
        "file": FILE,
        "hard_checks": ["iface.pin_missing", "iface.pin_dangling",
                        "ioc.mcu_mismatch（族/封装不同 = 给错 .ioc）",
                        "ioc.pin_conflict（有引脚表时：.ioc 的外设脚被网表接到别的族/电源）"],
        "soft_checks": ["ioc.mcu_variant（同族同封装不同型号）",
                        "ioc.peripheral_unrouted（配了但网表没有）",
                        "ioc.peripheral_unconfigured（网表有但没配）",
                        "ioc.pin_unrouted / ioc.pin_family_unknown / "
                        "ioc.pin_role_conflict（逐脚对账的软结论）"],
        "af_table": ("AF 复用表（ST 官方 pin data）：F4/H7 给 AF 编号，"
                     "F1 给固定功能+重映射；`.ioc` 的功能不在这根脚上 → 硬冲突 "
                     "`ioc.function_not_on_pin`"),
        "with_ioc": {"clock": "RCC.* 原样引用", "nvic": "NVIC.* + 优先级",
                     "af": "每个脚的功能名（不给 AF 编号）",
                     "peripheral_config": "<IP>.* 只收写出来的值"},
        "inferred": ["peripherals（无 .ioc 时按网名族推断）", "power_nets（未声明时）"],
        "always_unknown": ["AF 编号", "时序约束"],
        "pin_table": ("封装脚号 ↔ 端口名来自 `pinout` 表（LCSC 厂商符号）；"
                      "表在 → 逐脚对账（ioc.pin_conflict 是硬判据），"
                      "表缺 → 该条仍 unknown"),
        "discipline": ("引脚↔网络逐条一致是唯一硬判据；"
                       "`.ioc` 给的是配置不是物理对照（PA9↔30 缺表，不编）；"
                       "缺的东西一律 unknown 并写明缺什么"),
    }