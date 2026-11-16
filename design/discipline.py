"""过程纪律：未决/不可逆决策闸门 + append-only 轨迹。

账本与轨迹在 ``design/ledger.py``（**本地实现**，不依赖 hwforge）。
这里只做两件事：

1. 把两个对象落盘/读回（JSON），让"打开工程"能恢复上一次的账本；
2. 把闸门接到**导出**动作上：不可逆决策未闭合时，导出/交付必须失败，
   且失败原因要能指出是哪几项、为什么不可逆。
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import DesignModel


# ---------------------------------------------------------------------------
# 未决决策账本（本地实现）
# ---------------------------------------------------------------------------

def new_ledger() -> Any:
    from .ledger import Ledger
    return Ledger()


def ledger_from_json(data: dict[str, Any] | None) -> Any:
    from .ledger import KINDS, Ledger
    ledger = Ledger()
    if not data:
        return ledger
    for fact in data.get("deferred") or []:
        try:
            ledger.defer(str(fact.get("kind")), str(fact.get("ref")),
                         str(fact.get("why") or ""), str(fact.get("at") or "O4"))
        except Exception:  # noqa: BLE001 — 旧工程可能有不合法条目，跳过而不是崩
            continue
    for lock in data.get("locked") or []:
        try:
            ledger.lock(str(lock.get("name")), str(lock.get("value")),
                        str(lock.get("at") or "L1"))
        except Exception:  # noqa: BLE001
            continue
    return ledger


def ledger_to_json(ledger: Any) -> dict[str, Any]:
    from .ledger import IRREVERSIBLE_DECISIONS, KINDS
    return {
        **ledger.report(),
        "kinds": list(KINDS),
        "irreversible": dict(IRREVERSIBLE_DECISIONS),
    }


def auto_lock(design: DesignModel, ledger: Any) -> list[str]:
    """只自动锁**已经由设计决定**的那一项：MCU 家族 = 板卡型号。

    其余不可逆决策（封装族/连接器族/电源架构/启动分区）不替用户拍板——
    自动锁一个没有依据的值，等于伪造一个决策。
    """
    locked: list[str] = []
    if "mcu_family" not in ledger.locked_names() and design.board.kind:
        ledger.lock("mcu_family", f"{design.board.name}（{design.board.kind}）", "L1")
        locked.append("mcu_family")
    # bootloader_layout 只在设计里明确有固件布局时才自动闭合
    layout = design.meta.get("bootloader_layout")
    if isinstance(layout, str) and layout.strip() and \
            "bootloader_layout" not in ledger.locked_names():
        ledger.lock("bootloader_layout", layout.strip(), "L1")
        locked.append("bootloader_layout")
    return locked


def gate(ledger: Any) -> dict[str, Any]:
    """交付闸门：不可逆决策未闭合 → 拒绝导出，并说明原因。"""
    from .ledger import IRREVERSIBLE_DECISIONS
    report = ledger.report()
    open_names = list(report["open_irreversible"])
    reasons = {name: IRREVERSIBLE_DECISIONS[name] for name in open_names}
    return {
        "deliverable": not open_names,
        "open_irreversible": open_names,
        "reasons": reasons,
        "message": (
            "不可逆决策未闭合，禁止导出/交付："
            + "；".join(f"{n}（{r}）" for n, r in reasons.items())
            if open_names else "不可逆决策已闭合，可以导出"),
    }


# ---------------------------------------------------------------------------
# 轨迹
# ---------------------------------------------------------------------------

def new_trace() -> Any:
    from .ledger import Trace
    return Trace()


def trace_from_json(rows: list[dict[str, Any]] | None) -> Any:
    from .ledger import Event, Trace
    trace = Trace()
    for row in rows or []:
        event = Event(
            seq=int(row.get("seq") or len(trace.events) + 1),
            ts=str(row.get("ts") or ""),
            actor=str(row.get("actor") or ""),
            type=str(row.get("type") or ""),
            payload=dict(row.get("payload") or {}),
            parent_hash=str(row.get("parent_hash") or "genesis"),
            payload_hash=str(row.get("payload_hash") or ""),
        )
        trace.events.append(event)
        trace._last_hash = event.payload_hash
    return trace


def trace_to_json(trace: Any) -> list[dict[str, Any]]:
    return json.loads(trace.to_json())


def append(store: Any, actor: str, type_: str, payload: dict[str, Any]) -> dict[str, Any]:
    """追加一条事件并立即落盘（append-only，不提供删除接口）。"""
    trace = trace_from_json(store.read("trace.json"))
    event = trace.append(actor, type_, payload)
    store.write("trace.json", trace_to_json(trace))
    return {
        "seq": event.seq,
        "ts": event.ts,
        "type": event.type,
        "payload_hash": event.payload_hash,
        "parent_hash": event.parent_hash,
    }


def verify(store: Any) -> dict[str, Any]:
    """校验轨迹：查链 + **重算每条 payload 的哈希**。

    只查链是不够的：改了 payload 却没改 `payload_hash` 时，链仍然连续。
    证据面板要能发现这种情况。
    """
    from .ledger import verify_trace
    trace = trace_from_json(store.read("trace.json"))
    verdict = verify_trace(trace)
    return {
        "ok": verdict["ok"],
        "events": verdict["events"],
        "chain_ok": verdict["chain_ok"],
        "tampered_seqs": verdict["tampered"],
        "message": verdict["message"],
        "head": trace.events[-1].payload_hash if trace.events else "genesis",
    }


def evidence(store: Any, design: DesignModel) -> dict[str, Any]:
    """证据面板的一条：设计内容哈希 + 轨迹头 + 闸门状态。"""
    blob = json.dumps(design.to_dict(), sort_keys=True, ensure_ascii=False).encode()
    digest = hashlib.sha256(blob).hexdigest()[:16]
    trace = trace_from_json(store.read("trace.json"))
    ledger = ledger_from_json(store.read("deferred.json"))
    verdict = verify(store)
    return {
        "design_hash": digest,
        "trace": {
            "events": len(trace.events),
            "chain_ok": verdict["chain_ok"],
            "content_ok": verdict["ok"],
            "tampered_seqs": verdict["tampered_seqs"],
            "head": verdict["head"],
            "last": (trace.events[-1].to_dict() if hasattr(trace.events[-1], "to_dict")
                     else {
                         "seq": trace.events[-1].seq,
                         "ts": trace.events[-1].ts,
                         "type": trace.events[-1].type,
                     }) if trace.events else None,
        },
        "gate": gate(ledger),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def assert_deliverable(store: Any) -> None:
    """导出前的硬闸门。抛 ``RuntimeError``，消息即拒绝原因。"""
    ledger = ledger_from_json(store.read("deferred.json"))
    verdict = gate(ledger)
    if not verdict["deliverable"]:
        raise RuntimeError(verdict["message"])
