"""发射前预检（P67）—— **没料可装就别开工程**。

## 为什么放在 emit 前面

`emit` 的失败有很贵的形状：工程建好了、原理图装了一半、PCB 段才报
"4 个元件找不到"。43 件的板，一次白跑 10 分钟。

预检只做两件事，都是在**开工程之前**问系统库：

| 判据 | 不过会怎样 |
|---|---|
| `lcsc` 能解析成器件（`lib_Device.getByLcscIds`）| emit 段报 `no_symbol`，装不上 |
| 解析出的器件**有封装关联** | 能装符号、摆不进 PCB |

## 三态

* `ok`：每条都能解析；有封装的缺失只当 warning（**异步缓存会跳**，见 P58）
* `not_ready`：有编号解析不到 → **硬拦**（`cmd_emit` 直接返回，不建工程）
* 没 lceda：`not_ready` 且说明"没验"，emit 可继续（它自己会报）

`strict_footprint=True` 时"无封装关联"也拦——只在确认缓存已稳定时用。
"""

from __future__ import annotations

import json
from typing import Any, Callable

__all__ = ["FILE", "check", "describe"]

FILE = "preflight.json"

Lookup = Callable[[list[str]], list[dict[str, Any]]]


def _eda_lookup(codes: list[str], *, attempts: int = 3) -> list[dict[str, Any]]:
    """批量问立创。**重试是为了等缓存**（`footprintUuid` 会先空后有）。"""
    from . import lceda

    exe = lceda.find_exe()
    if not exe:
        raise RuntimeError("没有 lceda-pro 可执行文件")
    session = lceda.LcedaSession(exe=exe).open("", headless=True)
    try:
        last: list[dict[str, Any]] = []
        for _ in range(attempts):
            last = session.invoke("""
const L = %s; const out = [];
for (const c of L) {
  try {
    const d = await eda.lib_Device.getByLcscIds(c);
    out.push({lcsc: c, ok: !!d, name: d ? (d.name || '') : '',
              footprint: !!(d && d.footprint && d.footprint.uuid)});
  } catch (e) {
    out.push({lcsc: c, ok: false, name: '', footprint: false});
  }
}
return out;
""" % json.dumps(codes), timeout_ms=280_000)
            if all(row.get("ok") and row.get("footprint") for row in last):
                break
        return last
    finally:
        session.close(destroy=True)


def check(design: Any, *, lookup: Lookup | None = None,
          strict_footprint: bool = False) -> dict[str, Any]:
    """按元件逐条判：有编号 / 能解析 / 有封装。**不写盘**。"""
    rows: list[dict[str, Any]] = []
    missing_code = [part.ref for part in design.parts if not str(part.lcsc or "")]
    want = [part for part in design.parts if str(part.lcsc or "")]
    codes = sorted({str(part.lcsc) for part in want})

    resolved: dict[str, dict[str, Any]] = {}
    if codes:
        try:
            got = (lookup or _eda_lookup)(codes)
        except Exception as exc:  # noqa: BLE001 - 没验成不是失败
            return {
                "status": "not_ready", "file": FILE,
                "reason": f"没法问系统库（{type(exc).__name__}: {exc}）—— 没验，不是通过",
                "counts": {"parts": len(design.parts), "with_lcsc": len(want),
                           "codes": len(codes), "checked": 0},
                "missing_code": missing_code, "parts": [],
                "note": "预检没跑成；emit 自己会再报一次",
            }
        for row in got:
            resolved[str(row.get("lcsc"))] = row

    unresolved: list[dict[str, Any]] = []
    no_footprint: list[dict[str, Any]] = []
    for part in want:
        code = str(part.lcsc)
        row = resolved.get(code) or {}
        entry = {"ref": part.ref, "lcsc": code,
                 "resolved": bool(row.get("ok")),
                 "footprint": bool(row.get("footprint")),
                 "name": str(row.get("name") or "")}
        rows.append(entry)
        if not entry["resolved"]:
            unresolved.append(entry)
        elif not entry["footprint"]:
            no_footprint.append(entry)

    if missing_code:
        status = "not_ready"
        reason = f"{len(missing_code)} 件没有 lcsc/mpn（emit 会报 no_symbol）"
    elif unresolved:
        status = "not_ready"
        reason = f"{len(unresolved)} 个编号在系统库里解析不到（装不上符号）"
    elif strict_footprint and no_footprint:
        status = "not_ready"
        reason = f"{len(no_footprint)} 个器件没有封装关联（strict 模式拦下）"
    else:
        status = "ok"
        reason = "每条编号都能解析" + (
            f"；{len(no_footprint)} 条暂无封装关联（缓存异步，作 warning）"
            if no_footprint else "")

    return {
        "status": status, "file": FILE, "reason": reason,
        "counts": {"parts": len(design.parts), "with_lcsc": len(want),
                   "codes": len(codes), "checked": len(rows),
                   "unresolved": len(unresolved),
                   "no_footprint": len(no_footprint),
                   "missing_code": len(missing_code)},
        "missing_code": missing_code,
        "unresolved": unresolved,
        "no_footprint": no_footprint,
        "parts": rows,
        "strict_footprint": bool(strict_footprint),
        "basis": "lib_Device.getByLcscIds（逐编号；3 轮等缓存）",
        "note": ("解析不到 = 硬拦；无封装关联默认只 warning —— "
                 "`footprintUuid` 会先空后有（P58 实测）"),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.emit_preflight",
        "file": FILE,
        "checks": ["lcsc present", "resolves via getByLcscIds", "footprint uuid"],
        "status": "ok / not_ready（解析不到或没编号）/ not_ready（没验成）",
        "strict": "strict_footprint=True 时无封装也拦",
        "discipline": "解析不到硬拦；无封装默认 warning（异步缓存会跳）",
    }