"""固件矩阵 —— 引脚 × (硬件配置 / 固件使用 / 结论) 一屏看完。

## 为什么这一层非有不可

`pinmap-check` 已经能判幽灵引脚、闲置、方向冲突，但它输出的是**三张问题表**：

```
ghost:     [ {pin: PA11, ...} ]
idle:      [ {pin: PB9, ...} ]
direction: [ {pin: PA5, ...} ]
counts:    {ghost: 1, idle: 2, direction: 1}
```

问题在于：**没有问题的引脚不在任何一张表里**。于是"这个引脚到底配没配、用没用"
只能靠人拿 `.ioc` 和源码对——而这正是矩阵该干的事。

## 三条纪律

1. **不重新判定**。矩阵的 `status` 全部来自 `pinmap.crosscheck` 的三张表 +
   硬件配置集合，这里只做**投影**。改动判定口径只改 `crosscheck` 一处。
2. **幽灵引脚必须有行**。它硬件里没有配置，所以不在"已配置引脚"里——
   如果矩阵只遍历已配置引脚，**最重要那一类问题会整行消失**。
3. **调试脚不算闲置**。`crosscheck` 已经排除了 SWD/JTAG；矩阵不能把它们加回来
   （那会凭空多出一堆"闲置"，人就不看了）。

## 状态顺序

```
ghost（硬件没配，固件在用）> direction（方向冲突）> idle（配了没用）> ok
```

前三类都会让人**改东西**，`ok` 只是"核对过"。`ghost` 排最前是因为它运行时
必炸（写一个不存在的端口），而 `idle` 只是浪费。
"""

from __future__ import annotations

from typing import Any

__all__ = ["STATUS_ORDER", "describe", "matrix", "summarize"]

#: 一行可能的结论。顺序 = 严重度（越小越急）。
STATUS_ORDER = ("ghost", "direction", "idle", "ok")

STATUS_LABEL = {
    "ghost": "幽灵引脚（硬件没配，固件在用）",
    "direction": "方向冲突（配的是输入，固件在写 / 反之）",
    "idle": "闲置（配了，固件没用到）",
    "ok": "核对过",
}

#: 快照里只放这几行。矩阵全量走 `firmware` 命令——
#: 一个 100 脚 MCU 的矩阵塞进快照会把它撑大，而首屏只需要"有没有炸"。
SUMMARY_ROWS = 6


def _config_of(item: Any) -> dict[str, Any]:
    """把 `IocPin` / `PinAssignment` 归一成矩阵要显示的那几格。

    两者字段不同（`IocPin` 有 label/signal/mode，`PinAssignment` 有
    net/function/direction），所以**逐个取、缺就是空**，不做 duck-typing 猜测。
    """
    def pick(*names: str) -> str:
        for name in names:
            value = getattr(item, name, None)
            if value not in (None, ""):
                return str(value)
        return ""

    return {
        "mode": pick("mode", "direction"),
        "signal": pick("signal", "function"),
        "label": pick("label", "net"),
        "macro": pick("macro"),
        "reserved_debug": bool(getattr(item, "reserved_debug", False)),
    }


def matrix(pins: Any, uses: list[dict[str, Any]], *,
           source: str = "") -> dict[str, Any]:
    """构建矩阵。``pins`` 可以是 `{pin: IocPin}` 或 `PinMap`。

    返回 ``{status, rows, counts, checked, used_pins, basis, note}``。
    `status` 沿用 `crosscheck` 的 `ok`/`fail`/`not_ready`——**不另立一套**。
    """
    from .fwgen import pinmap as pm

    report = pm.crosscheck(pins, uses, source=source)
    if report.get("status") == "not_ready":
        return {
            "status": "not_ready",
            "reason": report.get("reason") or "硬件侧没有可核对的引脚配置",
            "rows": [], "counts": {"ok": 0, "ghost": 0, "direction": 0, "idle": 0},
            "checked": 0, "used_pins": 0,
            "basis": report.get("basis") or source,
            "note": "",
        }

    # 硬件侧：只认 `configured` 的脚（和 crosscheck 同一口径）
    if isinstance(pins, pm.PinMap):
        configured = {a.pin_name: a for a in pins.assignments}
    else:
        configured = {pin: item for pin, item in pins.items() if item.configured}

    uses_by_pin: dict[str, list[dict[str, Any]]] = {}
    for row in uses:
        uses_by_pin.setdefault(str(row.get("pin")), []).append(row)

    ghost_by_pin: dict[str, list[dict[str, Any]]] = {}
    for row in report.get("ghost") or []:
        ghost_by_pin.setdefault(str(row.get("pin")), []).append(row)
    direction_by_pin: dict[str, dict[str, Any]] = {
        str(row.get("pin")): row for row in report.get("direction") or []}
    idle_by_pin: dict[str, dict[str, Any]] = {
        str(row.get("pin")): row for row in report.get("idle") or []}

    rows: list[dict[str, Any]] = []
    counts = {"ok": 0, "ghost": 0, "direction": 0, "idle": 0}

    # -- 已配置的脚：每脚一行 -----------------------------------------
    for pin in sorted(configured):
        item = configured[pin]
        config = _config_of(item)
        firmware = [{
            "api": str(row.get("api") or ""),
            "file": str(row.get("file") or ""),
            "line": int(row.get("line") or 0),
            "text": str(row.get("text") or "")[:120],
        } for row in uses_by_pin.get(pin, [])]

        if pin in direction_by_pin:
            status = "direction"
            why = str(direction_by_pin[pin].get("why") or "")
            extra = {"mode_basis": str(direction_by_pin[pin].get("mode_basis") or ""),
                     "api": str(direction_by_pin[pin].get("api") or "")}
        elif pin in idle_by_pin:
            status = "idle"
            why = str(idle_by_pin[pin].get("why") or "")
            extra = {}
        else:
            status = "ok"
            why = "配置与使用一致" if firmware else "外设脚（由驱动使用，不在应用代码里）"
            extra = {}

        counts[status] += 1
        rows.append({
            "pin": pin, "status": status, "why": why,
            "config": config, "firmware": firmware, **extra,
        })

    # -- 幽灵引脚：硬件里**没有**这些脚，但它们必须出现在矩阵里 ------
    #
    # 第一版只遍历 `configured`，于是幽灵引脚整行消失——而它恰恰是
    # 唯一"运行时必炸"的那类（往一个没配的端口写）。矩阵少了它就成了摆设。
    for pin in sorted(ghost_by_pin):
        counts["ghost"] += 1
        rows.append({
            "pin": pin, "status": "ghost",
            "why": str(ghost_by_pin[pin][0].get("why") or ""),
            "config": {"mode": "", "signal": "", "label": "",
                       "macro": "", "reserved_debug": False},
            "firmware": [{
                "api": "",
                "file": str(row.get("firmware") or ""),
                "line": 0,
                "text": str(row.get("text") or "")[:120],
            } for row in ghost_by_pin[pin]],
        })

    rows.sort(key=lambda row: (STATUS_ORDER.index(row["status"]),
                               row["pin"]))

    return {
        "status": "fail" if (counts["ghost"] or counts["direction"]) else "ok",
        "rows": rows,
        "counts": counts,
        "checked": int(report.get("checked") or len(configured)),
        "used_pins": int(report.get("used_pins") or len(uses_by_pin)),
        "basis": str(report.get("basis") or source),
        "note": str(report.get("note") or ""),
        "total": len(rows),
    }


def summarize(payload: dict[str, Any], *, limit: int = SUMMARY_ROWS
              ) -> dict[str, Any]:
    """快照用的摘要：计数 + 前几行**有问题的**。

    **不放 ok 行**：一个 100 脚 MCU 有 90 行 ok，塞进快照既无用又占宽。
    """
    rows = [row for row in payload.get("rows") or []
            if row.get("status") != "ok"]
    return {
        "status": payload.get("status"),
        "counts": dict(payload.get("counts") or {}),
        "total": int(payload.get("total") or 0),
        "checked": int(payload.get("checked") or 0),
        "used_pins": int(payload.get("used_pins") or 0),
        "basis": str(payload.get("basis") or ""),
        "rows": rows[:limit],
        "rows_total": len(rows),
        "reason": payload.get("reason") or "",
        "note": payload.get("note") or "",
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.firmware_matrix",
        "status_order": list(STATUS_ORDER),
        "labels": dict(STATUS_LABEL),
        "columns": ["引脚", "硬件配置(.ioc)", "固件使用(src/)", "结论"],
        "why": ("pinmap-check 只报三张问题表，没有问题的脚不在任何表里；"
                "矩阵按脚一行，才能回答'这个脚配没配、用没用'"),
        "ghost_rows": "幽灵引脚在硬件里没有配置，**仍然要出行**——否则最重要那类消失",
        "not_judging": "status 全部来自 pinmap.crosscheck，本层只做投影",
    }