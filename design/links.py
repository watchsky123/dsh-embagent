"""板间链路 —— 把「USART2 接谁」这件事变成一个能被检查的东西。

## 为什么这一层最重要

实测你的 7 个 `.ioc`：**258 引脚 / 32 个串口 / 0 个 label**。
意思是：固件里配了 32 个串口，但**没有一处写下它接什么**。
这类"设计者的隐性假设"是所有检查都抓不到的——网表上它是通的，
电气上它是合规的，几何上它不重叠，但两端的协议/电平/波特率可能
根本不匹配。

`links` 把它显式化，于是可以检查。

## 四条检查

| 检查 | 抓什么 |
|---|---|
| `link.protocol_match` | UART 接 SPI —— 根本通不了 |
| `link.level_match` | 3.3V 接 5V —— 能通但会烧 |
| `link.baud_feasible` | 目标波特率超出时钟能分出的范围 |
| `link.orphan` | **声明了外设但没说接谁** —— 隐性假设 |

## 一条纪律：`inferred` 不参与硬检查

从网表推出来的链路（`source="inferred"`）**只做提示，不做判决**。
理由：推断会错。一个 `$1N399` 恰好连了两个有多个引脚的元件，
不代表它们之间是"链路"。如果推断能让人 fail，人会开始怀疑
检查器而不是自己的设计——那比没有检查更糟。

所以：`validate()` 跳过 `inferred`，但会单独报出
"有多少条是推断的、它们是什么"。人看着推断结果决定要不要
把它提升成 `declared`。
"""

from __future__ import annotations

import re
from typing import Any

from .model import DesignModel

#: 自动网名。与 `units` / `justify` 同一套判据。
_AUTO_NET_RE = re.compile(r"^\$|^[wW]\d+$")

#: 协议族。键是族名，值是匹配**外设名/网名**的关键词。
#:
#: 只收这几种，因为它们的电平/时序语义明确。`gpio` 不进表——
#: 任意一条网都可能是 GPIO，把它算进来等于没有协议检查。
PROTOCOL_FAMILIES: dict[str, tuple[str, ...]] = {
    "uart": ("uart", "usart", "tx", "rx", "uart_tx", "uart_rx"),
    "i2c": ("i2c", "iic", "scl", "sda"),
    "spi": ("spi", "mosi", "miso", "sck", "nss", "cs"),
    "can": ("can", "canh", "canl"),
    "usb": ("usb",),
}

#: 协议族 → 典型电平（V）。用于"没申报电平"时的**默认值**，
#: 但用了默认值必须记进 `assumed`，不能当已验证。
_FAMILY_LEVEL: dict[str, float] = {
    "uart": 3.3, "i2c": 3.3, "spi": 3.3, "can": 3.3, "usb": 3.3,
}

#: 网名 → 协议族。比外设名匹配更严格：**按词元**，不按子串。
#:
#: 为什么必须按词元：第一版用子串 `"_rx"` 匹配，于是 `RF_RXEN`
#: （射频收发使能，一个 GPIO）被判成 UART 链路。实测它立刻出现在
#: `示例-单板` 的推断结果里。虽然 `inferred` 不参与硬检查、
#: 误判不会判人 fail，但**提示里出现明显错误会让人开始不信整个列表**，
#: 那比不给提示更糟。
#:
#: 词元判据：`U1_RX` → `['u','1','rx']` → `rx` 命中；
#: `RF_RXEN` → `['rf','rxen']` → `rxen` 不是 `rx`，不命中。
_NET_FAMILY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("i2c", ("scl", "sda")),
    ("spi", ("mosi", "miso", "sck", "nss", "spi")),
    ("can", ("canh", "canl", "can")),
    ("usb", ("dp1", "dn1", "usb", "usbdp", "usbdm")),
    ("uart", ("uart", "usart", "tx", "rx")),
)

#: 这些词元出现时，整条网**不是**链路（是使能/中断/状态脚）。
_NOT_LINK_TOKENS = frozenset({
    "en", "en1", "en2", "rxen", "txen", "int", "irq", "busy", "dio",
    "irq1", "irq2", "nrst", "reset", "boot", "boot0", "boot1",
})


def _tokens(name: str) -> list[str]:
    """把名字切成词元。`U1_RX` → `['u','1','rx']`；`RF-3.3V` → `['rf','3','3v']`。"""
    return [t for t in re.split(r"[^a-z0-9]+", str(name or "").lower()) if t]


def _token_matches(tokens: list[str], words: tuple[str, ...]) -> bool:
    """词元里有没有命中。

    除了精确相等，还接受**词元 + 纯数字**：`USART2` 是一个词元
    `['usart2']`（`USART` 与 `2` 之间没有分隔符），精确相等抓不到它。
    第一版因此判不出 `USART2` 是 uart——而 `.ioc` 里的外设名
    全是 `USART1`/`SPI1`/`I2C1` 这种写法，漏掉这一类等于漏掉全部。
    """
    for token in tokens:
        if token in words:
            return True
        for word in words:
            if token.startswith(word) and token[len(word):].isdigit():
                return True
    return False


def family_of(name: str) -> str | None:
    """从外设名/网名判协议族。判不出返回 `None`（**不猜**）。"""
    tokens = _tokens(name)
    if not tokens:
        return None
    for family, words in PROTOCOL_FAMILIES.items():
        if _token_matches(tokens, words):
            return family
    return None


def _net_family(net: str) -> str | None:
    tokens = _tokens(net)
    if not tokens or _AUTO_NET_RE.match(str(net or "")):
        return None
    if any(t in _NOT_LINK_TOKENS for t in tokens):
        return None
    for family, words in _NET_FAMILY:
        if _token_matches(tokens, words):
            return family
    return None


# ---------------------------------------------------------------------------
# 从网表推导链路（只做提示）
# ---------------------------------------------------------------------------

def _interface_parts(design: DesignModel) -> set[str]:
    """哪些元件"有资格"当链路的一端。

    判据是结构性的：引脚数 >= 3（两脚无源件不可能是链路端点），
    或者角色是 module/connector。**不看型号**——型号是哈希。
    """
    out: set[str] = set()
    for part in design.parts:
        if len(part.pins or []) >= 3 or part.role in ("module", "connector"):
            out.add(part.ref)
    return out


def derive_links(design: DesignModel) -> list[dict[str, Any]]:
    """从网表推**候选**链路（`source="inferred"`）。

    规则：一条具名网，如果
      * 网名能判出协议族（SCL/SDA/USART/U1_TX/...）
      * 且连着 >= 2 个"有资格当端点"的元件

    就产出一条候选。**同一条网只产生一条**（多端点的记为 hub）。

    `source="inferred"` 意味着它**不参与硬检查**——推断会错，
    不能让它判人 fail。
    """
    from . import claims as claims_module

    capable = _interface_parts(design)
    unit_of = _part_to_unit(design)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for net_row in design.nets:
        net = str(net_row.get("net") or "")
        family = _net_family(net)
        if family is None:
            continue
        pins = [p for p in claims_module.pins_on_net(design, net)
                if isinstance(p, dict)]
        refs = sorted({str(p.get("ref") or "") for p in pins} - {""})
        endpoints = [r for r in refs if r in capable]
        if len(endpoints) < 2:
            continue
        key = f"{net}|{'|'.join(endpoints)}"
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "id": f"link:{net}",
            "a": {"unit": unit_of.get(endpoints[0], ""),
                  "part": endpoints[0], "peripheral": net, "pin": ""},
            "b": {"unit": unit_of.get(endpoints[1], ""),
                  "part": endpoints[1], "peripheral": net, "pin": ""},
            "protocol": family,
            "params": {"level_v": _FAMILY_LEVEL.get(family),
                       "baud": None},
            "source": "inferred",
            "evidence": f"net:{net} 连接 {', '.join(endpoints)}",
            "extra_endpoints": endpoints[2:],
        })
    return out


def _part_to_unit(design: DesignModel) -> dict[str, str]:
    """`ref` → 单元 id。与 `units.derive` 的 id 规则保持一致。"""
    from . import units as units_module

    mapping: dict[str, str] = {}
    for unit in units_module.derive(design):
        for ref in unit.parts:
            mapping[ref] = unit.id
    return mapping


# ---------------------------------------------------------------------------
# 四条检查
# ---------------------------------------------------------------------------

def _fail(check: str, link: dict[str, Any], message: str,
          **extra: Any) -> dict[str, Any]:
    row = {
        "check": check,
        "ok": False,
        "severity": "error",
        "link": str(link.get("id") or ""),
        "message": message,
    }
    row.update(extra)
    return row


def _same_family(a: str, b: str) -> bool:
    fa, fb = family_of(a), family_of(b)
    if fa is None or fb is None:
        return True          # 判不出就不判失败（不猜）
    return fa == fb


def _declared_level(design: DesignModel, side: dict[str, Any]) -> float | None:
    """某一端的电平。三个来源，优先级从高到低：link 参数 → 元件申报 → 板级。"""
    part_ref = str(side.get("part") or "")
    part = next((p for p in design.parts if p.ref == part_ref), None)
    if part is not None:
        electrical = part.electrical or {}
        for key in ("v_in", "v_out", "level_v"):
            value = electrical.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    board_level = (design.meta or {}).get("io_level_v")
    if isinstance(board_level, (int, float)) and not isinstance(board_level, bool):
        return float(board_level)
    return None


def baud_reachable(clock_hz: float | None, baud: float | None,
                   oversampling: int = 16,
                   fraction_bits: int = 4) -> tuple[bool | None, float | None]:
    """目标波特率能否从时钟分出来。

    返回 `(可否, 实际可达波特率)`。时钟或目标未知时返回 `(None, None)`
    ——**未知不是通过**。

    ## 为什么必须用小数分频

    第一版按整数分频算：`divisor = clock/(16*baud)`，取整后算实际值。
    实测 `8MHz + 115200` 得出 divisor=4.34 → 取整 4 → 实际 125000
    → 偏差 8.5% → 判"不可达"。**这个结论是错的**：STM32 的 USART
    BRR 有 4 位小数分频，4.34 可以写成 4 + 5/16 = 4.3125，
    实际 115942，偏差 0.6%，完全可用。

    假失败和假通过一样坏：人看到"8MHz 跑不了 115200"会去改时钟，
    而本来不需要改。所以这里按 STM32 参考手册的 BRR 结构算：

        divisor = clock / (oversampling * baud)
        mantissa = floor(divisor)        # BRR[15:4]
        fraction = round((divisor - mantissa) * 2^fraction_bits)
        # fraction 满进位置 1，硬件不允许 mantissa < 1
        effective = mantissa + fraction / 2^fraction_bits
        actual = clock / (oversampling * effective)

    2% 容差：UART 容忍度约 ±3%，留一半余量。
    """
    if clock_hz is None or baud is None:
        return None, None
    if clock_hz <= 0 or baud <= 0:
        return False, None

    steps = 2 ** fraction_bits
    divisor = clock_hz / (oversampling * baud)
    mantissa = int(divisor)
    fraction = round((divisor - mantissa) * steps)

    if fraction >= steps:
        mantissa += 1
        fraction = 0
    # BRR 的整数部分最小是 1（硬件限制）
    if mantissa < 1:
        mantissa, fraction = 1, 0

    effective = mantissa + fraction / steps
    actual = clock_hz / (oversampling * effective)
    return (abs(actual - baud) / baud <= 0.02), actual


def validate(design: DesignModel,
             links: list[dict[str, Any]] | None = None,
             inferred: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """对链路表跑四条检查。

    `links` 省略时用 `design.links_list`。**空的链路表不是"通过"**，
    而是"没声明任何链路"——这一点必须在 `checked` 里说清楚。
    """
    table = list(links if links is not None else (design.links_list or []))
    findings: list[dict[str, Any]] = []
    skipped_inferred: list[str] = []
    squared = 0
    assumed: list[str] = []

    for link in table:
        if not isinstance(link, dict):
            continue
        source = str(link.get("source") or "")
        if source == "inferred":
            # ★ 推断不参与硬检查（提案 I2 / 本模块开头那条纪律）
            skipped_inferred.append(str(link.get("id") or ""))
            continue
        squared += 1

        a = link.get("a") or {}
        b = link.get("b") or {}
        params = link.get("params") or {}
        name = str(link.get("id") or "")

        # 1. 协议族一致
        pa = str(a.get("peripheral") or "")
        pb = str(b.get("peripheral") or "")
        if not _same_family(pa, pb):
            findings.append(_fail(
                "link.protocol_match", link,
                f"{pa}（{family_of(pa)}）与 {pb}（{family_of(pb)}）协议族不同",
                a=pa, b=pb))

        # 2. 电平兼容
        va = params.get("level_v")
        if not isinstance(va, (int, float)) or isinstance(va, bool):
            va = _declared_level(design, a)
        vb = params.get("level_v_b")
        if not isinstance(vb, (int, float)) or isinstance(vb, bool):
            vb = _declared_level(design, b)
        if va is None or vb is None:
            assumed.append(
                f"{name}: 电平未申报（a={va}, b={vb}），不判通过")
        elif abs(float(va) - float(vb)) > 0.05:
            findings.append(_fail(
                "link.level_match", link,
                f"{va}V ↔ {vb}V 不匹配：需电平转换", a=float(va), b=float(vb)))

        # 3. 波特率可达（只对串口类链路有意义）
        family = str(link.get("protocol") or family_of(pa) or "")
        if family == "uart":
            clock = params.get("clock_hz")
            if not isinstance(clock, (int, float)) or isinstance(clock, bool):
                clock = _clock_of(design, a, b)
            target = params.get("baud")
            ok, actual = baud_reachable(
                float(clock) if isinstance(clock, (int, float)) else None,
                float(target) if isinstance(target, (int, float)) else None)
            if ok is None:
                assumed.append(f"{name}: 时钟或波特率未申报，可达性未知")
            elif not ok:
                findings.append(_fail(
                    "link.baud_feasible", link,
                    f"目标 {target}，实际可达 {actual:.0f}",
                    target=target, actual=actual))

    orphans = _orphans(design, table, inferred)
    findings.extend(orphans["findings"])

    return {
        "ok": not any(f.get("severity") == "error" for f in findings),
        "checked": {
            "declared_links": squared,
            "inferred_links": len(skipped_inferred),
            "total": len(table),
        },
        "findings": findings,
        "skipped_inferred": skipped_inferred,
        "assumed": assumed,
        "orphans": orphans["items"],
        "covered_by_inferred": orphans.get("covered_by_inferred") or [],
        "status": "ok" if table else "not_ready",
        "reason": ("" if table else
                   "链路表为空：没有声明任何板间链路，"
                   "三条链路检查无从执行（不是通过）"),
    }


def _clock_of(design: DesignModel, a: dict[str, Any],
              b: dict[str, Any]) -> float | None:
    """找这条链路所属 MCU 的时钟。**找不到返回 None，不默认 72MHz。**"""
    for side in (a, b):
        ref = str(side.get("part") or "")
        part = next((p for p in design.parts if p.ref == ref), None)
        if part is None:
            continue
        electrical = part.electrical or {}
        for key in ("clock_hz", "f_cpu_hz"):
            value = electrical.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    meta_clock = (design.meta or {}).get("clock_hz")
    if isinstance(meta_clock, (int, float)) and not isinstance(meta_clock, bool):
        return float(meta_clock)
    return None


def _orphans(design: DesignModel,
             table: list[dict[str, Any]],
             inferred: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """第 4 条：声明了外设但没说接谁。

    "声明"的三种来源，按可信度：
      1. `meta["interfaces"]` / `meta["ioc_peripherals"]` —— 人/工具显式写的
      2. `links_list` 里的端点
      3. 从网表看出来的外设信号网（只作**提示**，因为它可能是板内的）

    只把 1、2 算作"已声明"，第 3 类报成 `observed_unlinked`
    ——它们是"看着像外设但没人说是链路的网"，让人自己判断。

    ## `inferred` 也要算"已覆盖"，但要标出来

    第一版把 `observed_unlinked` 只对**声明**的链路做差集，于是
    `SPI_MISO_1` 同时出现在"推断出的 6 条链路"和"21 条没有链路的网"里。
    同一份报告里前后矛盾，读的人会以为推断失效。

    正确做法：推断出来的链路**确实覆盖了那些网**，只是覆盖它的
    证据是推断不是声明——所以记 `covered_by: inferred`，
    并单独统计"只被推断覆盖"的数量。
    """
    declared: dict[str, str] = {}
    meta = design.meta or {}
    for key in ("interfaces", "ioc_peripherals", "peripherals"):
        raw = meta.get(key)
        if isinstance(raw, dict):
            for name, owner in raw.items():
                declared[str(name)] = str(owner)
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    name = str(item.get("peripheral") or item.get("name") or "")
                    if name:
                        declared[name] = str(item.get("unit") or item.get("owner") or "")

    covered: set[str] = set()
    covered_by_inferred: set[str] = set()
    for link in table:
        if not isinstance(link, dict):
            continue
        for side in (link.get("a") or {}, link.get("b") or {}):
            name = str(side.get("peripheral") or "")
            if name:
                covered.add(name.lower())
    for link in (inferred or []):
        if not isinstance(link, dict):
            continue
        for side in (link.get("a") or {}, link.get("b") or {}):
            name = str(side.get("peripheral") or "")
            if name:
                covered_by_inferred.add(name.lower())

    items: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    for name in sorted(declared):
        if name.lower() in covered:
            continue
        items.append({
            "peripheral": name,
            "owner": declared[name],
            "source": "declared",
            "why": "声明了这个外设，但链路表里没有任何一条以它为端点",
        })
        findings.append({
            "check": "link.orphan",
            "ok": False,
            "severity": "error",
            "link": "",
            "message": f"{name} 已声明但没说接谁（隐性假设）",
            "peripheral": name,
            "fix": {"need": "declare_link", "peripheral": name},
        })

    # 第 3 类只提示。推断覆盖过的不再重复报（那是自相矛盾）。
    observed = _observed_unlinked(
        design, covered | covered_by_inferred)
    if observed:
        items.extend(observed)

    # 没有网表的单元：它们的对外接口完全未知
    unknown_units = _units_without_nets(design)
    for unit_id in unknown_units:
        items.append({
            "peripheral": "",
            "owner": unit_id,
            "source": "missing_data",
            "why": ("这个单元的元件没有任何网表，"
                    "它的对外接口完全无从判断"),
        })

    return {"items": items, "findings": findings,
            "covered_by_inferred": sorted(covered_by_inferred)}


def _observed_unlinked(design: DesignModel,
                       covered_nets: set[str]) -> list[dict[str, Any]]:
    """看着像外设、但没被任何链路覆盖的具名网（**只提示**）。

    `covered_nets` 由调用方算好（声明的 + 推断的），这里只做差集。
    第一版从 `evidence` 字符串里抠 `net:` 前缀，只对声明链路有效——
    推断链路没有 `evidence` 之外的端点信息，于是差集永远漏掉它们。
    """
    from . import claims as claims_module

    covered_nets = {str(n).lower() for n in covered_nets}

    out: list[dict[str, Any]] = []
    for net_row in design.nets:
        net = str(net_row.get("net") or "")
        if _net_family(net) is None:
            continue
        if net.lower() in covered_nets:
            continue
        pins = [p for p in claims_module.pins_on_net(design, net)
                if isinstance(p, dict)]
        refs = sorted({str(p.get("ref") or "") for p in pins} - {""})
        if not refs:
            continue
        out.append({
            "peripheral": net,
            "owner": refs[0],
            "parts": refs,
            "source": "observed",
            "why": (f"网名像 {_net_family(net)} 信号，"
                    "但没有声明它连到哪个单元"),
        })
    return out


def _units_without_nets(design: DesignModel) -> list[str]:
    from . import units as units_module

    return [u.id for u in units_module.derive(design)
            if u.parts and u.parts_with_nets == 0]


# ---------------------------------------------------------------------------
# 给 P6 用：按接口需求接线
# ---------------------------------------------------------------------------

def wire(units: list[dict[str, Any]],
         interfaces: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """按需求里声明的接口，把单元两两接起来。**确定性**。

    `interfaces` 形如 `[{"a":"mcu","b":"radio","protocol":"spi",
    "baud":null,"level_v":3.3}]`。

    不做"智能匹配"：需求说接哪两个角色就接哪两个，找不到对应单元
    就返回带 `unresolved` 的条目——**不猜**。
    """
    by_role: dict[str, list[dict[str, Any]]] = {}
    for unit in units:
        by_role.setdefault(str(unit.get("role") or ""), []).append(unit)

    links: list[dict[str, Any]] = []
    for index, spec in enumerate(interfaces or []):
        if not isinstance(spec, dict):
            continue
        role_a = str(spec.get("a") or "")
        role_b = str(spec.get("b") or "")
        a = (by_role.get(role_a) or [None])[0]
        b = (by_role.get(role_b) or [None])[0]
        if a is None or b is None:
            links.append({
                "id": f"link:unresolved{index}",
                "a": {"unit": "", "peripheral": role_a},
                "b": {"unit": "", "peripheral": role_b},
                "protocol": str(spec.get("protocol") or ""),
                "params": {},
                "source": "declared",
                "unresolved": (f"没有 {role_a} 或 {role_b} 角色对应的单元"),
            })
            continue
        links.append({
            "id": f"link:{a.get('role')}→{b.get('role')}:{index}",
            "a": {"unit": str(a.get("id") or ""),
                  "peripheral": str(spec.get("protocol") or "")},
            "b": {"unit": str(b.get("id") or ""),
                  "peripheral": str(spec.get("protocol") or "")},
            "protocol": str(spec.get("protocol") or ""),
            "params": {k: spec.get(k) for k in ("baud", "level_v", "clock_hz")
                       if spec.get(k) is not None},
            "source": "declared",
        })
    return links


# ---------------------------------------------------------------------------
# 报告与自省
# ---------------------------------------------------------------------------

def report(design: DesignModel) -> dict[str, Any]:
    """完整报告：声明的链路 + 推断的候选 + 四条检查结果。"""
    declared = list(design.links_list or [])
    inferred = derive_links(design)
    result = validate(design, declared, inferred)

    from . import units as units_module
    unit_report = units_module.report(design)

    items = result["orphans"]
    severe = [i for i in items if i.get("source") == "declared"]
    observed = [i for i in items if i.get("source") == "observed"]
    missing = [i for i in items if i.get("source") == "missing_data"]

    summary = description_of(design, result, inferred, severe,
                             observed, missing, unit_report)
    return {
        **result,
        "project": design.id,
        "units": unit_report["total"],
        "units_without_nets": unit_report["missing_data"]["no_netlist_count"],
        "inferred": inferred,
        "orphans": {
            "declared_unlinked": severe,
            "observed_unlinked": observed,
            "units_without_nets": missing,
            "counts": {
                "declared_unlinked": len(severe),
                "observed_unlinked": len(observed),
                "units_without_nets": len(missing),
            },
            "covered_by_inferred": result.get("covered_by_inferred") or [],
        },
        "summary": summary,
    }


def description_of(design: DesignModel, result: dict[str, Any],
                   inferred: list[dict[str, Any]],
                   severe: list[dict[str, Any]],
                   observed: list[dict[str, Any]],
                   missing: list[dict[str, Any]],
                   unit_report: dict[str, Any]) -> str:
    declared = result["checked"]["declared_links"]
    parts = [f"声明链路 {declared} 条，从网表推断候选 {len(inferred)} 条"]
    if result["findings"]:
        parts.append(f"{len(result['findings'])} 项检查失败")
    if severe:
        parts.append(f"{len(severe)} 个外设声明了但没说接谁")
    if observed:
        parts.append(f"{len(observed)} 条外设信号网没有对应链路（仅提示）")
    if missing:
        parts.append(f"{len(missing)} 个单元没有网表，对外接口无从判断")
    if declared == 0:
        parts.append("**链路表为空，三条链路检查未执行（不是通过）**")
    return "；".join(parts) + "。"


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.links",
        "checks": ["link.protocol_match", "link.level_match",
                   "link.baud_feasible", "link.orphan"],
        "protocols": sorted(PROTOCOL_FAMILIES),
        "discipline": ("source=inferred 的链路不参与硬检查——"
                       "推断会错，不能让它判人 fail"),
        "honest": ("链路表为空 = not_ready，不是通过；"
                   "时钟/电平未申报 = 未知，不默认"),
    }
