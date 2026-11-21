"""网表的**只读适配层**：把 `design.json` 说成 Traceforge 那套 `Netlist` 的样子。

## 为什么是适配层，不是搬它的 `netlist.py`

它那份 `load_netlist()` 读的是 `.yaml` / `.kicad_sch` / `.net`——三个我们都不产。
搬过来只会多一条永远走不到的分支。这里只保留**它下游真正用到的形状**：

```
nets  : {net name: [(ref, pin), ...]}
parts : {ref: "<kind> <label> <lcsc/mpn>"}    ← 只用于识别 MCU 型号
```

于是 `fwgen/pinmap.py` 的 `build_pinmap()` 可以原样工作，而输入换成我们自己的
`DesignModel`。**这不是"移植了一半"，是把接口对齐到我们的唯一事实源。**

## 第二个用途：给「引脚交叉核对」提供同一份网表

核对要回答「软件用了一个硬件没配的脚吗」，其中「硬件配了哪些脚」有两种来源：
`.ioc`（首选，它带 label 与信号名）或 `design.json` 的网表。两者都通过这里
变成同一个 `Netlist`，核对逻辑因此只有一份。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = ["Netlist", "NetlistError", "from_design", "from_pairs"]


class NetlistError(Exception):
    """网表不可用。调用方把它翻成 ``not_ready``，**不猜**。"""


@dataclass
class Netlist:
    """``nets[name] = [(ref, pin), ...]``；``parts[ref] = "型号线索"``。"""

    nets: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    parts: dict[str, str] = field(default_factory=dict)
    source: str = ""

    def pins_of(self, ref: str) -> list[str]:
        out: list[str] = []
        for members in self.nets.values():
            for member_ref, pin in members:
                if member_ref == ref and pin not in out:
                    out.append(pin)
        return out

    def net_of(self, ref: str, pin: str) -> str | None:
        for net, members in self.nets.items():
            for member_ref, member_pin in members:
                if member_ref == ref and member_pin == pin:
                    return net
        return None


def _part_hint(part: Any) -> str:
    """给 MCU 识别用的型号线索：``kind label mpn lcsc`` 拼起来。

    `kind` 在导入工程里是哈希（判不出型号），所以后面几项都要拼上——
    少拼一项就少一次识别机会，而识别错了会走错 MCU 表。
    """
    pieces = [
        str(getattr(part, "kind", "") or ""),
        str(getattr(part, "label", "") or ""),
        str(getattr(part, "mpn", "") or ""),
        str(getattr(part, "lcsc", "") or ""),
        str(getattr(part, "role", "") or ""),
    ]
    return " ".join(p for p in pieces if p).strip()


def from_design(design: Any, *, source: str = "design.json") -> Netlist:
    """`DesignModel` → `Netlist`。网表为空时抛 `NetlistError`（不是返回空表）。"""
    nets: dict[str, list[tuple[str, str]]] = {}
    for entry in (design.nets or []):
        name = str(entry.get("net") or "").strip()
        if not name:
            continue
        members = nets.setdefault(name, [])
        for pin in (entry.get("pins") or []):
            ref = str(pin.get("ref") or "").strip()
            number = str(pin.get("pin") or "").strip()
            if ref and number:
                members.append((ref, number))
    if not nets:
        raise NetlistError("design.json 里没有网表（nets 为空），无法建立引脚映射")
    parts = {part.ref: _part_hint(part) for part in (design.parts or [])}
    return Netlist(nets=nets, parts=parts, source=source)


def from_pairs(pairs: Iterable[tuple[str, str, str]], *,
               parts: dict[str, str] | None = None,
               source: str = ".ioc") -> Netlist:
    """``[(net, ref, pin), ...]`` → `Netlist`（给 `.ioc` 与合成夹具用）。"""
    nets: dict[str, list[tuple[str, str]]] = {}
    for net, ref, pin in pairs:
        nets.setdefault(str(net), []).append((str(ref), str(pin)))
    if not nets:
        raise NetlistError("没有可用连接")
    return Netlist(nets=nets, parts=dict(parts or {}), source=source)