"""制造包扩产（`lceda-fab`）—— 从 3 个产物到 7 个：STEP / DXF / PDF / 网表 / 测试点 / IPC-2581。

## 这一层补什么

`emit` 的第 5 段只导 3 样：Gerber(zip) / BOM(xlsx) / 坐标(xlsx)。立创的
`pcb_ManufactureData` 还有一大排 `get*File`，实测可用的：

| kind | 调用 | 实测大小（`复刻-示例-单板`）| 头 |
|---|---|---|---|
| `step` | `get3DFile(name, 'step')` | 14.1 MB | `ISO-10303-21` |
| `dxf` | `getDxfFile(name)` | 1.18 MB | DXF 文本 |
| `pdf` | `getPdfFile(name)` | 452 KB | `%PDF` |
| `netlist` | `getNetlistFile(name)` | 36.7 KB | JSON（`.enet`）|
| `testpoint` | `getTestPointFile(name)` | 8.3 KB | `PK`（zip）|
| `ipc2581` | `getIpc2581CFile(name)` | **2 B（`{}`）** | **不可用** |

## 两个硬限制（实测，决定了实现方式）

1. **`invoke` 返回上限 8 MB**（`RESULT_TOO_LARGE`）。STEP 的 base64 是 18.8 MB
   → 必须**分块**：先 `get3DFile` 把 base64 存进 `globalThis`，再逐块取回
   （4 MB/块，5 次）。实测可行。
2. **`saveFileToFileSystem` 默认被拒**（外部交互权限）。所以走"取字节 → Python 落盘"。

## 判据

* 每个文件都验**头字节**（STEP=ISO-10303-21、PDF=%PDF、zip=PK…）——
  头不对说明"接口返回了个别的东西"，不能记 ok
* `ipc2581` 只有 2 字节（`{}`）→ **not_ready**，并写"这一版返回空对象"，
  **不产出一个假文件**
* 一个文件都拿不到 → `not_ready`；部分拿到 → `ok` + `missing` 列表
"""

from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path
from typing import Any

from . import lceda

__all__ = ["FILE", "OUT_DIR", "KINDS", "export", "describe"]

FILE = "fab.json"
OUT_DIR = "artifacts/fab"

#: 单次 `invoke` 的结果上限（CLI 实测报 `RESULT_TOO_LARGE`）。
RESULT_LIMIT = 8 * 1024 * 1024

#: 分块大小：留足余量（base64 之外还有 JSON 包装）。
CHUNK = 4 * 1024 * 1024

#: kind → (显示名, 建议扩展名, 头字节)
KINDS: dict[str, dict[str, Any]] = {
    "step": {"label": "3D (STEP)", "ext": ".step", "magic": b"ISO-10303-21"},
    "dxf": {"label": "板框 DXF", "ext": ".dxf", "magic": b"0\nSECTION"},
    "pdf": {"label": "装配图 PDF", "ext": ".pdf", "magic": b"%PDF"},
    "netlist": {"label": "网表 (.enet)", "ext": ".enet", "magic": b"{"},
    "testpoint": {"label": "测试点表", "ext": ".zip", "magic": b"PK"},
}

_JS_OPEN = """
const P = __CTX__;
for (let i = 0; i < 25; i++) {
  try { const t = await eda.dmt_Team.getAllTeamsInfo(); if (t && t.length) break; } catch (e) {}
  await new Promise(r => setTimeout(r, 300));
}
if (!P.projectUuid) throw new Error('没有 projectUuid（先 emit）');
const opened = await eda.dmt_Project.openProject(P.projectUuid);
if (!opened) throw new Error('openProject 返回 false');
await new Promise(r => setTimeout(r, P.settleMs));
await eda.dmt_EditorControl.openDocument(P.pcbUuid);
await new Promise(r => setTimeout(r, P.settleMs));
const cur = await eda.dmt_SelectControl.getCurrentDocumentInfo();
if (!cur || cur.documentType !== 3) throw new Error('当前不是 PCB 文档');
return {ok: true};
"""

_JS_SMALL = """
const P = __CTX__;
const f = await (%s);
if (!f) return {ok: false, reason: '返回 null'};
const record = {ok: true, name: f.name || '', size: f.size || 0};
if ((record.size || 0) > 0 && record.size <= P.maxBytes && typeof f.arrayBuffer === 'function') {
  const b = new Uint8Array(await f.arrayBuffer());
  let bin = '';
  const ch = 0x8000;
  for (let i = 0; i < b.length; i += ch) bin += String.fromCharCode.apply(null, b.subarray(i, i + ch));
  record.b64 = btoa(bin);
}
return record;
"""

_JS_STEP_STASH = """
const P = __CTX__;
const f = await eda.pcb_ManufactureData.get3DFile(P.name, 'step');
if (!f) return {ok: false, reason: '返回 null'};
const b = new Uint8Array(await f.arrayBuffer());
let bin = '';
const ch = 0x8000;
for (let i = 0; i < b.length; i += ch) bin += String.fromCharCode.apply(null, b.subarray(i, i + ch));
const b64 = btoa(bin);
globalThis.__hwv_fab = {name: f.name || '', size: f.size || b.length, b64: b64};
return {ok: true, name: f.name || '', size: f.size || b.length, len: b64.length,
        head: b64.slice(0, 24)};
"""

_JS_STEP_CHUNK = """
const s = (globalThis.__hwv_fab && globalThis.__hwv_fab.b64) || '';
const a = %d, b = %d;
return s.slice(a, b);
"""


def _open_project(session: lceda.LcedaSession, ctx: dict[str, Any]) -> dict[str, Any]:
    code = _JS_OPEN.replace("__CTX__", json.dumps(ctx, ensure_ascii=False))
    return session.invoke(code, label="open-project", timeout_ms=280_000)


def _decode(b64: str) -> bytes:
    return base64.b64decode(b64.encode("ascii"), validate=False)


def _head_ok(kind: str, data: bytes) -> bool:
    magic = KINDS[kind]["magic"]
    head = data[:64]
    if kind == "dxf":
        return b"SECTION" in data[:2000]
    if kind == "netlist":
        return head.lstrip().startswith(b"{")
    if kind == "step":
        return head.lstrip().startswith(b"ISO-10303-21")
    return head.startswith(magic)


def _fetch_step(session: lceda.LcedaSession, ctx: dict[str, Any],
                name: str) -> dict[str, Any]:
    """STEP 走分块：生成一次（存 globalThis），再 4MB/块取回。"""
    code = _JS_STEP_STASH.replace("__CTX__", json.dumps({**ctx, "name": name}))
    meta = session.invoke(code, label="step:stash", timeout_ms=280_000)
    if not isinstance(meta, dict) or not meta.get("ok"):
        return {"ok": False, "reason": str((meta or {}).get("reason") or "生成失败")}
    total = int(meta.get("len") or 0)
    if total <= 0:
        return {"ok": False, "reason": "STEP 为空"}
    chunks: list[str] = []
    for start in range(0, total, CHUNK):
        piece = session.invoke(_JS_STEP_CHUNK % (start, min(start + CHUNK, total)),
                               label=f"step:{start // CHUNK}", timeout_ms=280_000)
        if not isinstance(piece, str):
            return {"ok": False, "reason": "分块取回失败"}
        chunks.append(piece)
    b64 = "".join(chunks)
    if len(b64) != total:
        return {"ok": False, "reason": f"分块长度不符（{len(b64)} != {total}）"}
    return {"ok": True, "name": str(meta.get("name") or name), "size": int(meta.get("size") or 0),
            "data": _decode(b64)}


def export(project_dir: Path, *, kinds: list[str] | None = None,
           session: lceda.LcedaSession | None = None,
           ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """把制造文件取回并落盘到 `artifacts/fab/`。**不打开工程就不猜 uuid**。"""
    wanted = list(kinds or KINDS)
    if not session or not session.ready:
        return {"status": "not_ready", "reason": "没有可用的会话（先 open）"}
    if not ctx:
        return {"status": "not_ready", "reason": "缺工程上下文（projectUuid/pcbUuid）"}
    try:
        _open_project(session, ctx)
    except Exception as first:  # noqa: BLE001
        # 会话刚起时 openProject 偶发失败（用户态异步装载）—— 重试一次
        try:
            _open_project(session, ctx)
        except Exception as exc:  # noqa: BLE001
            return {"status": "not_ready",
                    "reason": f"打不开工程：{type(exc).__name__}: {exc}"
                              f"（第一次 {type(first).__name__}）"}

    target = Path(project_dir) / OUT_DIR
    target.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for kind in wanted:
        spec = KINDS.get(kind)
        if spec is None:
            rows.append({"kind": kind, "status": "skipped", "reason": "不认识的类型"})
            continue
        name = f"{Path(project_dir).name}_{kind}"
        try:
            if kind == "step":
                got = _fetch_step(session, ctx, name)
            else:
                call = {
                    "dxf": f"eda.pcb_ManufactureData.getDxfFile({json.dumps(name)})",
                    "pdf": f"eda.pcb_ManufactureData.getPdfFile({json.dumps(name)})",
                    "netlist": f"eda.pcb_ManufactureData.getNetlistFile({json.dumps(name)})",
                    "testpoint": f"eda.pcb_ManufactureData.getTestPointFile({json.dumps(name)})",
                }[kind]
                code = (_JS_SMALL.replace("__CTX__", json.dumps(
                    {**ctx, "maxBytes": RESULT_LIMIT - 65536}, ensure_ascii=False))
                    % call)
                got = session.invoke(code, label=f"fab:{kind}", timeout_ms=280_000)
            if not isinstance(got, dict) or not got.get("ok"):
                rows.append({"kind": kind, "status": "not_ready",
                             "reason": str((got or {}).get("reason") or "取不到")})
                continue
            if kind == "step":
                data = got["data"]
            else:
                b64 = got.get("b64") or ""
                if not b64:
                    rows.append({"kind": kind, "status": "not_ready",
                                 "reason": f"文件 {got.get('size')} B 超过单次返回上限，"
                                           f"且没有分块实现"})
                    continue
                data = _decode(b64)
            if len(data) <= 4:
                rows.append({"kind": kind, "status": "not_ready",
                             "reason": f"只返回了 {len(data)} 字节（{data[:20]!r}）——"
                                       f"这一版接口不可用"})
                continue
            if not _head_ok(kind, data):
                rows.append({"kind": kind, "status": "failed",
                             "reason": f"头字节不是 {spec['magic']!r}：{data[:24]!r}"})
                continue
            ext = spec["ext"]
            raw_name = str(got.get("name") or name)
            if not raw_name.lower().endswith(ext):
                raw_name += ext
            path = target / Path(raw_name).name
            path.write_bytes(data)
            rows.append({"kind": kind, "status": "ok", "file": str(path),
                         "name": path.name, "bytes": len(data),
                         "head": data[:16].decode("latin-1", "replace")})
        except Exception as exc:  # noqa: BLE001
            rows.append({"kind": kind, "status": "failed",
                         "reason": f"{type(exc).__name__}: {exc}"})
    ok = [row for row in rows if row["status"] == "ok"]
    bad = [row for row in rows if row["status"] in ("failed", "not_ready")]
    return {
        "status": "ok" if ok else "not_ready",
        "dir": OUT_DIR,
        "files": ok,
        "rows": rows,
        "missing": [{"kind": row["kind"], "reason": row["reason"]} for row in bad],
        "counts": {"ok": len(ok), "missing": len(bad)},
        "source": "catalog",
        "retrieved": date.today().isoformat(),
        "note": ("头字节不对不算 ok；`ipc2581` 这一版返回空对象 → not_ready；"
                 "STEP 走 4MB 分块（单次返回上限 8MB）"),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.lceda_fab",
        "file": FILE,
        "out_dir": OUT_DIR,
        "kinds": {key: spec["label"] for key, spec in KINDS.items()},
        "needs": "先 emit（fab 从 emit.json 里的 projectUuid/pcbUuid 打开工程）",
        "limits": {"result_bytes": RESULT_LIMIT,
                   "step_chunk": CHUNK,
                   "ipc2581": "这一版返回 2 字节 {}，不产出假文件"},
        "discipline": ("头字节验证；拿不到就 missing；一个都没有 → not_ready"),
    }