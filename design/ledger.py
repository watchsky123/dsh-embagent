"""过程纪律 —— **dsh_embagent 自己的**：未决决策账本 + 哈希链轨迹。

取代 `hwforge.deferred` 与 `hwforge.trace`。两个东西，一个目的：
**让"还没定的事"和"已经做过的事"都无法被悄悄忽略。**

## 一、未决决策账本（`Ledger`）

物理层是**晚**的（拓扑先定、物理后定），所以有些事实必须**先记下来、
以后再闭合**。账本把这类事实分成两类：

* `deferred`（**可推迟**）—— 布局、朝向、叠层、外壳、走线、热、机械；
* `locked` + 不可逆（**必须早定**）—— 封装族、连接器族、电源架构、
  MCU 族、启动分区。晚改一次就是一次重投。

规则：
1. **不可逆决策没闭合 → 拒绝导出**（`gate`）。不是警告，是拒绝；
2. **只自动闭合"设计里已经有答案"的那一项**。自动锁一个没依据的值，
   等于伪造一个决策 —— 比留着未闭合更糟；
3. `why` 必填：一个没有理由的推迟，三个月后没人知道能不能动。

## 二、哈希链轨迹（`Trace`）

每次改动追加一条事件，`payload_hash` + `parent_hash` 串成链。
**append-only，没有删除接口** —— 能删的审计不是审计。

`verify()` 除了查链，还会**重算每条 payload 的哈希**：只查链的话，
有人改了内容却没改哈希，链仍然连续。

@module dsh_embagent.design.ledger
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# 未决决策
# ---------------------------------------------------------------------------

#: 可以推迟到物理层的事实类别。
KINDS: tuple[str, ...] = (
    "placement",      # 元件在板上的位置
    "orientation",    # 旋转 / 哪一面朝外
    "stackup",        # 层数与铜厚
    "enclosure",      # 腔体、壁厚、密封
    "routing",        # 实际走线几何
    "thermal_mesh",   # 铜面积、过孔、散热器
    "mechanical",     # 安装孔、连接器高度、禁布区
)

#: **必须早定**的决策 —— 晚改一次就是一次重投。
IRREVERSIBLE_DECISIONS: dict[str, str] = {
    "package_family": "锁定层数、扇出方式、返修手段",
    "connector_family": "锁定外壳、线束、装配顺序",
    "power_architecture": "锁定下游每一级变换器的拓扑",
    "mcu_family": "锁定引脚数、外设、固件架构",
    "bootloader_layout": "存储分区一旦定了，改起来牵一发动全身",
}


class LedgerError(ValueError):
    """账本操作错误。"""


@dataclass
class Deferred:
    """一条被推迟的事实。"""

    kind: str
    ref: str
    why: str
    at: str = "O4"

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise LedgerError(
                f"未知的推迟类别 {self.kind!r}；可选：{KINDS}")
        if not str(self.why).strip():
            raise LedgerError(
                f"{self.ref}: 推迟必须写 why —— "
                f"没有理由的推迟三个月后没人知道能不能动")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref,
                "why": self.why, "at": self.at}


@dataclass
class Locked:
    """一条已闭合的决策。"""

    name: str
    value: str
    at: str = "L1"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "at": self.at}


class Ledger:
    """未决决策账本。**只追加**，`lock` 没有逆操作。"""

    def __init__(self) -> None:
        self.deferred: list[Deferred] = []
        self.locked: list[Locked] = []

    # -- 写 ----------------------------------------------------------------

    def defer(self, kind: str, ref: str, why: str, at: str = "O4") -> Deferred:
        item = Deferred(kind=kind, ref=ref, why=why, at=at)
        self.deferred.append(item)
        return item

    def lock(self, name: str, value: str, at: str = "L1") -> Locked:
        if name not in IRREVERSIBLE_DECISIONS:
            raise LedgerError(
                f"未知的不可逆决策 {name!r}；可选："
                f"{tuple(IRREVERSIBLE_DECISIONS)}")
        if not str(value).strip():
            raise LedgerError(f"{name}: 闭合值不能为空")
        existing = next((x for x in self.locked if x.name == name), None)
        if existing is not None:
            if existing.value == value:
                return existing
            raise LedgerError(
                f"{name} 已经锁成 {existing.value!r}，不能改成 {value!r} —— "
                f"不可逆决策只追加，没有 unlock")
        item = Locked(name=name, value=value, at=at)
        self.locked.append(item)
        return item

    # -- 读 ----------------------------------------------------------------

    def locked_names(self) -> set[str]:
        return {x.name for x in self.locked}

    def open_irreversible(self) -> list[str]:
        """还没闭合的不可逆决策（**按声明顺序**，可复现）。"""
        have = self.locked_names()
        return [name for name in IRREVERSIBLE_DECISIONS if name not in have]

    def report(self) -> dict[str, Any]:
        return {
            "deferred": [d.to_dict() for d in self.deferred],
            "locked": [x.to_dict() for x in self.locked],
            "open_irreversible": self.open_irreversible(),
            "irreversible_total": len(IRREVERSIBLE_DECISIONS),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.report()


# ---------------------------------------------------------------------------
# 哈希链轨迹
# ---------------------------------------------------------------------------

GENESIS = "genesis"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_payload(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


@dataclass
class Event:
    """一条轨迹事件。"""

    seq: int
    ts: str
    actor: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    parent_hash: str = GENESIS
    payload_hash: str = ""

    def __post_init__(self) -> None:
        if not self.payload_hash:
            self.payload_hash = _hash_payload(self.payload)

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "ts": self.ts, "actor": self.actor,
                "type": self.type, "payload": self.payload,
                "parent_hash": self.parent_hash,
                "payload_hash": self.payload_hash}


class Trace:
    """append-only 的哈希链。"""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self._last_hash: str = GENESIS

    def append(self, actor: str, type_: str,
               payload: dict[str, Any]) -> Event:
        event = Event(
            seq=len(self.events) + 1, ts=_now(), actor=str(actor),
            type=str(type_), payload=dict(payload),
            parent_hash=self._last_hash)
        self.events.append(event)
        self._last_hash = event.payload_hash
        return event

    def chain_ok(self) -> bool:
        """相邻事件的 `parent_hash` 是否首尾相接。"""
        prev = GENESIS
        for event in self.events:
            if event.parent_hash != prev:
                return False
            prev = event.payload_hash
        return True

    def to_json(self) -> str:
        return json.dumps([e.to_dict() for e in self.events], ensure_ascii=False)


def verify_trace(trace: Trace) -> dict[str, Any]:
    """查链 + **重算每条 payload 的哈希**。

    只查链是不够的：改了 payload 却没改 `payload_hash` 时，链仍然连续。
    """
    chain_ok = trace.chain_ok()
    tampered: list[int] = []
    for event in trace.events:
        if _hash_payload(event.payload) != event.payload_hash:
            tampered.append(event.seq)
    ok = chain_ok and not tampered
    if not chain_ok:
        message = "哈希链断裂：事件被删改或顺序被打乱"
    elif tampered:
        message = f"内容被改动：事件 {tampered} 的 payload 哈希对不上"
    else:
        message = "哈希链完整，内容未被改动"
    return {"ok": ok, "events": len(trace.events), "chain_ok": chain_ok,
            "tampered": tampered, "message": message}


def describe() -> dict[str, Any]:
    return {"module": "dsh_embagent.design.ledger",
            "kinds": list(KINDS),
            "irreversible": dict(IRREVERSIBLE_DECISIONS),
            "note": "append-only；不可逆决策只 lock，没有 unlock"}
