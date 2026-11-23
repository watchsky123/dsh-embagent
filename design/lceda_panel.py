"""拼板（`lceda-panel`）—— 生产阶段的**容器**，以及一个说清楚的边界。

## 实测到的能力（4.1.97）

| 调用 | 结果 |
|---|---|
| `DMT_Panel.createPanel()` | ✅ 返回面板 uuid（空面板）|
| `DMT_Panel.getAllPanelsInfo / getPanelInfo / modifyPanelName / copyPanel / deletePanel` | ✅ 文档里有、`doc api` 里也在 |
| `PNL_ManufactureData` / `PNL_Drc` / `PNL_Import` | ❌ `doc api --class-name` 返回 **null** |
| 把板子放进面板（拼板阵列） | ❌ **没有文档化 API**（`PNL_Document` 只有 `save()`）|

所以这一层能做的是：**建面板容器、命名、查/删/复制**；**不能**把板子排成阵列、
不能跑面板 DRC、不能导面板制造文件。后者必须人在面板编辑器里点。

## 纪律

* 建了容器 **≠** 拼板完成 → 总状态是 `not_ready`，并写清 `blocked_by`
* 边界要带**证据**（`doc api` 对这些类返回 null），不是"我觉得不行"
* 不伪造面板尺寸/阵列数（没数据就不写）
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from . import lceda

__all__ = ["FILE", "create", "describe"]

FILE = "panel.json"

#: 这些类在本版 `doc api` 里返回 null（实测），说明面板的 DRC/制造/导入未暴露。
_MISSING_CLASSES = ("PNL_ManufactureData", "PNL_Drc", "PNL_Import")

_JS_CREATE = """
const P = __CTX__;
for (let i = 0; i < 25; i++) {
  try { const t = await eda.dmt_Team.getAllTeamsInfo(); if (t && t.length) break; } catch (e) {}
  await new Promise(r => setTimeout(r, 300));
}
const opened = await eda.dmt_Project.openProject(P.projectUuid);
if (!opened) throw new Error('openProject 返回 false');
await new Promise(r => setTimeout(r, P.settleMs));
const before = await eda.dmt_Panel.getAllPanelsInfo();
const uuid = await eda.dmt_Panel.createPanel();
if (!uuid) return {ok: false, reason: 'createPanel 返回 undefined'};
await new Promise(r => setTimeout(r, 1500));
let renamed = false;
try { renamed = await eda.dmt_Panel.modifyPanelName(uuid, P.name); } catch (e) {}
const info = await eda.dmt_Panel.getPanelInfo(uuid);
const after = await eda.dmt_Panel.getAllPanelsInfo();
return {ok: true, opened, uuid, renamed,
        before: (before || []).length, after: (after || []).length,
        info: info || null};
"""

_JS_PANELS = """
const P = __CTX__;
const panels = await eda.dmt_Panel.getAllPanelsInfo();
return {panels: (panels || []).map(p => ({uuid: p.uuid, name: p.name || ''}))};
"""


def _missing_classes() -> dict[str, Any]:
    """用 CLI 的 `doc api` 证明哪些面板类在本版不存在。**只探测，不改任何东西。**"""
    out: dict[str, Any] = {}
    for name in _MISSING_CLASSES:
        try:
            env = lceda.run_cli(lceda.find_exe(), ["doc", "api", "--class-name", name],
                                timeout_ms=30_000)
            out[name] = "present" if env.get("value") else "null"
        except Exception as exc:  # noqa: BLE001
            out[name] = f"probe_failed:{type(exc).__name__}"
    return out


def create(project_dir: Path, *, session: lceda.LcedaSession | None = None,
           ctx: dict[str, Any] | None = None, name: str = "",
           settle_ms: int = 1200) -> dict[str, Any]:
    """建一个空面板容器。**结果如实标 not_ready（拼板未完成）**。"""
    if not session or not session.ready:
        return {"status": "not_ready", "reason": "没有可用的会话（先 open）"}
    if not ctx or not ctx.get("projectUuid"):
        return {"status": "not_ready", "reason": "缺 projectUuid（先 emit）"}
    panel_name = name or f"{Path(project_dir).name}-panel"
    try:
        got = session.invoke(
            _JS_CREATE.replace("__CTX__", json.dumps({**ctx, "name": panel_name,
                                                      "settleMs": settle_ms},
                                                     ensure_ascii=False)),
            label="panel:create", timeout_ms=280_000)
    except Exception as exc:  # noqa: BLE001
        return {"status": "not_ready",
                "reason": f"建面板失败：{type(exc).__name__}: {exc}"}
    if not isinstance(got, dict) or not got.get("ok"):
        return {"status": "not_ready",
                "reason": str((got or {}).get("reason") or "createPanel 失败")}
    panels = []
    try:
        listed = session.invoke(
            _JS_PANELS.replace("__CTX__", json.dumps(ctx, ensure_ascii=False)),
            label="panel:list", timeout_ms=120_000)
        panels = [row for row in (listed or {}).get("panels") or []
                  if isinstance(row, dict)]
    except Exception:  # noqa: BLE001
        panels = []
    missing = _missing_classes()
    return {
        "status": "not_ready",
        "file": FILE,
        "panel": {"uuid": got.get("uuid"), "name": panel_name,
                  "renamed": bool(got.get("renamed")),
                  "info": got.get("info")},
        "panels": panels,
        "counts": {"panels_before": got.get("before"), "panels_after": got.get("after")},
        "panel_done": False,
        "blocked_by": [
            "这一版没有把板子放进面板的 API（`PNL_Document` 只有 save()）",
            "面板 DRC / 制造文件未暴露（见 api_evidence）",
            "拼板阵列（行列/间距/工艺边）没有可调用接口",
        ],
        "api_evidence": missing,
        "next": ("在立创里手工拼板：面板编辑器 → 拼板 / 阵列；"
                 "本工具只建容器与登记名称"),
        "retrieved": date.today().isoformat(),
        "note": ("建了容器 ≠ 拼板完成 —— 状态是 not_ready，不是 ok"),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.lceda_panel",
        "file": FILE,
        "can": ["createPanel", "modifyPanelName", "getPanelInfo",
                "getAllPanelsInfo", "copyPanel", "deletePanel"],
        "cannot": ["把板子放进面板", "面板 DRC", "面板制造文件", "拼板阵列"],
        "evidence": f"`doc api --class-name {', '.join(_MISSING_CLASSES)}` 返回 null",
        "status": "not_ready（容器建了，拼板未完成）",
    }