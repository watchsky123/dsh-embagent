"""从 `.ioc` 里抽出板间链路 —— 把固件作者脑子里的接法变成数据。

## 为什么这一层比 P7 更值得先做

实测你的 7 个 `.ioc`：

    F103-GPS.ioc                引脚 17  通信端点  7  label  7
    F401-Bottom.ioc             引脚 22  通信端点 11  label  9
    F401-Front.ioc              引脚 13  通信端点  8  label  3
    F405-Joystick-Screen.ioc    引脚 33  通信端点 11  label 20
    F405Joystick.ioc            引脚 31  通信端点  7  label 22
    H743PilotX.ioc              引脚 59  通信端点 27  label 18
    f103c6-car.ioc              引脚 11  通信端点  4  label  5
                                ────────────────────────────────
                                合计    186        75         84

（**计划文档 §11.2 写的「258 引脚 / 32 串口 / 0 label」是错的**——
那是没实测就写下的估计。真实数据有 75 个通信端点、84 个 label，
足以做出有依据的配对。）

而 `示例-多板` 的 `links_list` 是**空的**：8 块板之间一条链路都没声明。
于是 P5 的链路检查从未真正执行过。这一层就是补那个空缺。

## 配对的三档可信度（对应计划 §11.1）

| 档 | 依据 | source | 能不能算硬结论 |
|---|---|---|---|
| 1 | 用户写的 `links.yaml` 显式规则 | `declared` | 能 |
| 2 | 同名外设 + 引脚**交叉**（PA2→PA3 / PA3→PA2） | `inferred` | **不能** |
| 3 | label 语义（`RF_*`/`VL53`/`BMM`/`LPN` 跨文件同名） | `inferred` | **不能** |
| — | 对不上 | 进 `unpaired`，**不猜** |

第 2 档的"交叉"是真实的物理约束：UART 的 TX 必须接对方 RX。
同时还要**电平/协议族一致**（都用 STM32 的 USART → 一致）。

## 一条硬纪律：inferred 不写进 links_list

本模块输出的 `draft` 里每条都带 `source`，`source="inferred"` 的
**不直接落到 `design.links_list`**（那是给 `links.validate` 做硬检查用的）。
要落地必须先由人确认 → `apply_draft(only_declared=True)`。

理由与 `links.py` 开头那条一致：推断会错，不能让它判人 fail。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: `.ioc` 里 `PA2.Signal=USART2_TX` 这一行。引脚名固定是 `P<端口><数字>`。
_SIGNAL_RE = re.compile(r"^(P[A-Z]\d+)\.Signal=(\S+)\s*$", re.M)
#: `PA15.GPIO_Label=VL53-PWR-EN`
_LABEL_RE = re.compile(r"^(P[A-Z]\d+)\.GPIO_Label=(.+?)\s*$", re.M)
#: 芯片型号：`Mcu.UserName=STM32F401CCUx` 或 `Mcu.CPN=...`
_MCU_RE = re.compile(r"^Mcu\.(?:UserName|CPN|Name)=(\S+)\s*$", re.M)
#: 引脚模式：`PA2.Mode=Asynchronous` / `PA5.Mode=Output` / `PB6.Mode=I2C`。
#: P20 的方向冲突检查要用它（把 Output 的脚当输入读 = 报错）。
_MODE_RE = re.compile(r"^(P[A-Z]\d+)\.Mode=(\S+)\s*$", re.M)

#: 通信外设的信号名形状：`USART2_TX` / `SPI1_SCK` / `I2C3_SDA` / `UART7_RX`
_PERIPHERAL_SIGNAL_RE = re.compile(
    r"^(USART\d+|UART\d+|LPUART\d+|SPI\d+|I2S\d+|I2C\d+|FDCAN\d+|CAN\d+|"
    r"USB_OTG_\w+|SDMMC\d+)_(\w+)$")

#: 引脚名 → 信号角色。只收通信相关的；`GPIO_Output` 之类不算链路端点。
_COMM_ROLES = {
    "TX": "tx", "RX": "rx",
    "SCK": "sck", "MISO": "miso", "MOSI": "mosi", "NSS": "nss",
    "SCL": "scl", "SDA": "sda",
    "CAN_TX": "tx", "CAN_RX": "rx",
    "CK": "ck", "D0": "d0", "D1": "d1", "CMD": "cmd",
}

#: 信号角色 → 对方的对应角色。**这是配对的物理依据。**
_PAIR_OF = {
    "tx": "rx", "rx": "tx",
    "sck": "sck", "miso": "mosi", "mosi": "miso", "nss": "nss",
    "scl": "scl", "sda": "sda",
    "ck": "ck", "d0": "d0", "d1": "d1", "cmd": "cmd",
}

#: 外设名 → 协议族。用于判断两个端点能不能接。
_FAMILY_OF_PERIPHERAL = {
    "USART": "uart", "UART": "uart", "LPUART": "uart",
    "SPI": "spi", "I2S": "spi",
    "I2C": "i2c",
    "FDCAN": "can", "CAN": "can",
    "USB_OTG_FS": "usb", "USB_OTG_HS": "usb",
    "SDMMC": "sdmmc",
}

#: label 里的模块关键词 → 模块类型。用于第 3 档（语义配对）。
#:
#: 只收**真有对应关系**的：这些词在多个 .ioc 里出现，说明那个模块
#: 是跨板共用的（比如 RF_* 在 H743PilotX 是主机侧，
#: `示例-单板` 的网表里 Lora1 是模块侧）。
_LABEL_MODULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lora", ("rf_dio", "rf_busy", "rf_rxen", "rf_nrst", "rf_nss", "rf-")),
    ("vl53", ("vl53",)),
    ("bmm", ("bmm_",)),
    ("pmw", ("pmw_",)),
    ("gps", ("gps_",)),
    ("gyro", ("gyro",)),
    ("qmc", ("qmc_",)),
    ("bmp", ("bmp_",)),
    ("led", ("led",)),
    ("lpn", ("lpn",)),
)


@dataclass
class Endpoint:
    """一个外设端点 = 某块板上某个通信外设的某个引脚。"""

    board: str
    pin: str
    peripheral: str          # USART2 / SPI1 / I2C3
    family: str              # uart / spi / i2c / can / usb / sdmmc
    role: str                # tx / rx / sck / scl / sda
    label: str = ""
    source_file: str = ""

    @property
    def key(self) -> str:
        return f"{self.board}:{self.peripheral}.{self.role}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "board": self.board,
            "pin": self.pin,
            "peripheral": self.peripheral,
            "family": self.family,
            "role": self.role,
            "label": self.label,
            "evidence": f"{Path(self.source_file).name}: {self.pin}={self.peripheral}_{self.role.upper()}",
        }


def _family_of(peripheral: str) -> str:
    base = re.sub(r"\d+$", "", peripheral.upper())
    if base.startswith("USB_OTG"):
        return "usb"
    return _FAMILY_OF_PERIPHERAL.get(base, "unknown")


def _module_of(label: str) -> str | None:
    low = (label or "").lower()
    for module, words in _LABEL_MODULES:
        if any(w in low for w in words):
            return module
    return None


def parse_ioc(path: str | Path) -> dict[str, Any]:
    """解析一个 `.ioc`，返回 `{board, mcu, endpoints, labels, skipped}`。

    `skipped` 记下"看着像通信引脚但没解析出来"的行，
    **不静默丢弃**：解析器漏掉的东西必须可见，否则覆盖率是假的。
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    board = path.stem

    mcu_match = _MCU_RE.search(text)
    mcu = mcu_match.group(1) if mcu_match else ""

    signals = dict(_SIGNAL_RE.findall(text))
    labels = dict(_LABEL_RE.findall(text))
    # P20：引脚的 Mode（Input/Output/Alternate/Analog…）。**附加键**，
    # 不改既有返回形状，P8 的调用方不用动。
    modes = dict(_MODE_RE.findall(text))

    endpoints: list[Endpoint] = []
    skipped: list[str] = []
    for pin, signal in sorted(signals.items()):
        match = _PERIPHERAL_SIGNAL_RE.match(signal)
        if match is None:
            continue
        peripheral, suffix = match.group(1), match.group(2)
        role = _COMM_ROLES.get(suffix.upper())
        if role is None:
            skipped.append(f"{pin}={signal}（外设信号但角色不认识）")
            continue
        endpoints.append(Endpoint(
            board=board, pin=pin, peripheral=peripheral,
            family=_family_of(peripheral), role=role,
            label=labels.get(pin, ""), source_file=str(path)))

    return {
        "board": board,
        "path": str(path),
        "mcu": mcu,
        "endpoints": endpoints,
        "labels": labels,
        "signals": signals,
        "modes": modes,
        "skipped": skipped,
    }


def discover(root: str | Path) -> list[Path]:
    """在 `root` 下找所有 `.ioc`。排序保证确定性。"""
    root = Path(root)
    found: list[Path] = []
    for current, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            if name.endswith(".ioc"):
                found.append(Path(current) / name)
    return found


# ---------------------------------------------------------------------------
# 配对
# ---------------------------------------------------------------------------

@dataclass
class Draft:
    """一次抽取的完整结果。"""

    links: list[dict[str, Any]] = field(default_factory=list)
    unpaired: list[dict[str, Any]] = field(default_factory=list)
    boards: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "links": list(self.links),
            "unpaired": list(self.unpaired),
            "boards": list(self.boards),
            "counts": dict(self.counts),
        }


def _rule_of(role: str) -> str | None:
    return _PAIR_OF.get(role)


def pair(parsed: list[dict[str, Any]]) -> Draft:
    """按可信度配对。**同一条端点只配一次。**

    三档依次尝试，前两档能配上就不走后一档：
      1. 显式规则（本函数不吃，由 `apply_declared` 处理）
      2. 同名外设 + 角色交叉
      3. label 模块语义
    """
    draft = Draft()
    used: set[str] = set()
    board_index = {row["board"]: row for row in parsed}

    # 按 (family, peripheral) 建索引，便于第 2 档查找
    by_family: dict[str, list[Endpoint]] = {}
    for row in parsed:
        for ep in row["endpoints"]:
            by_family.setdefault(ep.family, []).append(ep)

    for family in sorted(by_family):
        if family in ("unknown",):
            continue
        endpoints = by_family[family]
        # 按板分组
        per_board: dict[str, list[Endpoint]] = {}
        for ep in endpoints:
            per_board.setdefault(ep.board, []).append(ep)
        boards = sorted(per_board)

        for i in range(len(boards)):
            for j in range(i + 1, len(boards)):
                a_board, b_board = boards[i], boards[j]
                for a in sorted(per_board[a_board], key=lambda e: (e.peripheral, e.role)):
                    if a.key in used:
                        continue
                    want = _rule_of(a.role)
                    if want is None:
                        continue
                    # 第 2 档：对方同名外设 + 角色交叉
                    candidates = [
                        b for b in per_board[b_board]
                        if b.role == want and b.key not in used
                        and b.peripheral == a.peripheral]
                    if candidates:
                        b = candidates[0]
                        used.add(a.key)
                        used.add(b.key)
                        draft.links.append(_make_link(a, b, tier=2))
                        continue
                    # 第 3 档：label 模块语义（不同外设名也算）
                    module = _module_of(a.label)
                    if module is None:
                        continue
                    semantic = [
                        b for b in per_board[b_board]
                        if b.role == want and b.key not in used
                        and _module_of(b.label) == module]
                    if semantic:
                        b = semantic[0]
                        used.add(a.key)
                        used.add(b.key)
                        link = _make_link(a, b, tier=3)
                        link["note"] = f"按 label 模块 {module!r} 推断"
                        draft.links.append(link)

    # 合并同一对板上同一外设的 SCL+SDA / TX+RX 成一条链路。
    #
    # 第一版把 I2C1 的 SCL 和 SDA 各记一条"链路"，于是
    # F401-Bottom↔F401-Front 之间出现 4 条"链路"——而物理上
    # 那是**一条 I2C 总线**。虚增数量的后果是：人看到 27 条链路
    # 以为工作量很大，实际只有十来个真实的板间接口。
    #
    # 合并键用（a.unit, b.unit, peripheral），保留全部引脚证据。
    draft.links = _merge_by_interface(draft.links)

    # 没配上的端点如实列出
    for row in parsed:
        for ep in row["endpoints"]:
            if ep.key in used:
                continue
            draft.unpaired.append({
                **ep.to_dict(),
                "why": ("同角色对端没有出现在其它 .ioc 里"
                        if _rule_of(ep.role) else
                        f"角色 {ep.role!r} 没有定义配对规则"),
            })

    draft.boards = [
        {"board": row["board"], "mcu": row["mcu"],
         "endpoints": len(row["endpoints"]),
         "labels": len(row["labels"]),
         "skipped": len(row["skipped"]),
         "path": row["path"]}
        for row in parsed]

    tiers = [str(link.get("tier") or "") for link in draft.links]
    draft.counts = {
        "boards": len(parsed),
        "endpoints": sum(len(row["endpoints"]) for row in parsed),
        "links": len(draft.links),
        "tier2": tiers.count("2"),
        "tier3": tiers.count("3"),
        "unpaired": len(draft.unpaired),
        "skipped": sum(len(row["skipped"]) for row in parsed),
    }
    return draft


def _make_link(a: Endpoint, b: Endpoint, *, tier: int) -> dict[str, Any]:
    """造一条链路。**source 一律 inferred** —— 推断不是声明。"""
    return {
        "id": f"link:{a.peripheral}:{a.board}->{b.board}",
        "a": {"unit": a.board, "peripheral": a.peripheral,
              "pin": a.pin, "role": a.role},
        "b": {"unit": b.board, "peripheral": b.peripheral,
              "pin": b.pin, "role": b.role},
        "protocol": a.family,
        "params": {"level_v": None, "baud": None},
        "source": "inferred",
        "tier": tier,
        "evidence": (f"{Path(a.source_file).name}: {a.pin}={a.peripheral}_{a.role.upper()}"
                     f" <-> {Path(b.source_file).name}: {b.pin}={b.peripheral}_{b.role.upper()}"),
    }


# ---------------------------------------------------------------------------
# 落地
# ---------------------------------------------------------------------------

def apply_draft(design: Any, draft: Draft, *,
                only_declared: bool = True) -> int:
    """把 draft 写进 `design.links_list`。返回写入条数。

    `only_declared=True`（默认）时**只写 `source="declared"` 的**，
    推断的一条都不写——理由见模块开头那条纪律。

    要落地推断链路，必须人来决定：`only_declared=False`，
    并且那些链路进 `links.validate` 时仍会被 `source=inferred` 拦住
    做硬检查（`links.py` 里那条 `continue`）。
    """
    existing = list(getattr(design, "links_list", []) or [])
    seen = {(str(l.get("id") or "")) for l in existing
            if isinstance(l, dict)}
    added = 0
    for link in draft.links:
        if only_declared and str(link.get("source") or "") != "declared":
            continue
        link_id = str(link.get("id") or "")
        if link_id in seen:
            continue
        existing.append(dict(link))
        seen.add(link_id)
        added += 1
    design.links_list = existing
    return added


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def extract(root: str | Path) -> dict[str, Any]:
    """完整流程：发现 → 解析 → 配对 → 报告。

    **空结果要说清是"没有 .ioc" 还是 "有 .ioc 但配不上"。**
    """
    paths = discover(root)
    if not paths:
        return {
            "ok": False, "status": "not_ready",
            "root": str(root),
            "reason": f"{root} 下没有任何 .ioc 文件",
            "draft": Draft().to_dict(),
        }

    parsed = [parse_ioc(p) for p in paths]
    draft = pair(parsed)
    counts = draft.counts

    summary = (
        f"{counts['boards']} 个 .ioc、{counts['endpoints']} 个通信端点里，"
        f"配出 {counts['links']} 条链路"
        f"（同名外设 {counts['tier2']} 条 / label 语义 {counts['tier3']} 条）；"
        f"{counts['unpaired']} 个端点没配上，如实列出。"
    )
    if counts["skipped"]:
        summary += f" 另有 {counts['skipped']} 个外设信号角色不认识（见 skipped）。"
    summary += " **全部为 inferred，不参与硬检查。**"

    return {
        "ok": counts["links"] > 0,
        "status": "ok" if counts["links"] > 0 else "not_ready",
        "root": str(root),
        "draft": draft.to_dict(),
        "counts": counts,
        "summary": summary,
        "skipped": [
            {"board": row["board"], "items": row["skipped"]}
            for row in parsed if row["skipped"]],
    }


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.ioc_links",
        "input": ".ioc（STM32CubeMX 工程文件）",
        "tiers": {
            "1": "显式 links.yaml 规则 → source=declared（可参与硬检查）",
            "2": "同名外设 + 角色交叉（TX↔RX）→ inferred",
            "3": "label 模块语义（RF_*/VL53/BMM）→ inferred",
        },
        "discipline": ("inferred 默认不写进 links_list；"
                       "要落地必须人确认（only_declared=False）"),
        "honest": "配不上的端点进 unpaired 并说明原因，不猜",
    }


def _merge_by_interface(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把同一条总线/同一条串口的多个信号合成一条链路。

    `pins` 里保留每个信号的引脚与角色，所以合并**不丢证据**——
    只是把"4 条各带 1 个引脚的链路"变成"1 条带 4 个引脚的链路"。
    """
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        a = link.get("a") or {}
        b = link.get("b") or {}
        key = (str(a.get("unit") or ""), str(b.get("unit") or ""),
               str(a.get("peripheral") or ""))
        if key not in grouped:
            merged = dict(link)
            merged["pins"] = []
            merged["evidence_list"] = []
            merged.pop("evidence", None)
            grouped[key] = merged
            order.append(key)
        target = grouped[key]
        target["pins"].append({
            "a": {"pin": a.get("pin"), "role": a.get("role")},
            "b": {"pin": b.get("pin"), "role": b.get("role")},
        })
        target["evidence_list"].append(str(link.get("evidence") or ""))

    out: list[dict[str, Any]] = []
    for key in order:
        merged = grouped[key]
        merged["evidence"] = "; ".join(merged.pop("evidence_list"))
        merged["signal_count"] = len(merged["pins"])
        out.append(merged)
    return out


def promote(draft: Draft, ids: list[str] | None = None) -> int:
    """把选定的推断链路提升为 `declared`（**人确认后的动作**）。

    ## 为什么需要这个函数

    P8 抽出 13 条链路，但全部是 `inferred`，
    `apply_draft(only_declared=True)` 一条都不写——链路检查仍是
    `not_ready`。这个纪律是对的（推断不能判人 fail），
    但如果**没有出口**，抽出来的结果就永远用不上，等于白做。

    `promote()` 就是那个出口：**人看过之后**，指定哪些 id 可信，
    把它们标成 `declared`。之后 `apply_draft` 才会写进 `links_list`，
    `links.validate` 也才会真的检查它们。

    `ids=None` 表示全部提升——只在你确定推断全对时用。
    """
    wanted = set(ids) if ids is not None else None
    count = 0
    for link in draft.links:
        if not isinstance(link, dict):
            continue
        link_id = str(link.get("id") or "")
        if wanted is not None and link_id not in wanted:
            continue
        if str(link.get("source") or "") == "declared":
            continue
        link["source"] = "declared"
        link["promoted_from"] = "inferred"
        count += 1
    return count


#: 不同协议"配对成立"的物理解释。写成 dict 而不是 if 链，
#: 因为这张表要能被 rST/文档直接引用。
_PAIR_REASON = {
    "uart": "同名 USART 且 TX↔RX 交叉——UART 必须交叉才能通",
    "i2c": "同名 I2C 且 SCL/SDA 对同名——I2C 是总线，同名连同名",
    "spi": "同名 SPI 且 SCK/MISO/MOSI 按主从对应",
    "can": "同名 CAN 且 CANH/CANL 同名相连",
    "sdmmc": "同名 SDMMC 且数据线对应",
}


def _action_of(protocol: str, tier: str) -> str:
    if tier != "2":
        return "需人工核对（按 label 语义推断，置信度低）"
    reason = _PAIR_REASON.get(protocol, "同名外设 + 引脚角色对应")
    return f"可直接确认：{reason}"


def review_list(draft: Draft) -> list[dict[str, Any]]:
    """给人看的待确认清单：每条链路一行，带证据与建议。

    **按可信度排序**，tier=2（同名外设+引脚交叉）在前，
    tier=3（label 语义）在后——人从上往下看，先确认可信的。
    """
    rows: list[dict[str, Any]] = []
    for link in draft.links:
        if not isinstance(link, dict):
            continue
        a = link.get("a") or {}
        b = link.get("b") or {}
        tier = str(link.get("tier") or "?")
        rows.append({
            "id": str(link.get("id") or ""),
            "a": f"{a.get('unit')}.{a.get('peripheral')}",
            "b": f"{b.get('unit')}.{b.get('peripheral')}",
            "protocol": str(link.get("protocol") or ""),
            "signals": int(link.get("signal_count") or 1),
            "tier": tier,
            "confidence": "高" if tier == "2" else "低",
            "evidence": str(link.get("evidence") or ""),
            "action": (_action_of(str(link.get("protocol") or ""), tier)),
        })
    rows.sort(key=lambda r: (r["tier"], r["id"]))
    return rows
