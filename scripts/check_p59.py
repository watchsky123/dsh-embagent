"""P59 验收 —— 立创侧扩产：制造包（fab）/ 原理图 DRC（sch）/ 拼板容器（panel）。

    python -m dsh_embagent.scripts.check_p59

## 这一段补什么

| 命令 | 之前 | 现在 |
|---|---|---|
| `lceda-fab` | 只有 Gerber/BOM/坐标 3 样 | **+STEP/DXF/PDF/网表/测试点**（实测 5/5）|
| `lceda-sch-drc` | 原理图 DRC 从没跑过 | 逐页跑，按 `type` 过滤（warn 不挡门）|
| `lceda-panel` | 拼板 0 | 建容器/命名可做；**拼板本身不可调**，如实 `not_ready` |

## 判据（**反着判**最要紧）

| 项 | 期望 |
|---|---|
| 制造文件 | 5 个文件都落盘、头字节对（STEP=`ISO-10303-21`、PDF=`%PDF`…）|
| 8 MB 上限 | STEP 14 MB 走**分块**取回；取回长度必须等于声明长度 |
| 假文件 | 接口返回 ≤4 字节（如 IPC 的 `{}`）→ **not_ready**，不产文件 |
| 头字节 | 拿 `%PDF` 冒充 DXF → **failed**，不能记 ok |
| 原理图 DRC | 3 页都查；`error` 才判 failed，`warn` 不挡门 |
| 拼板 | 容器建出来 + 命名为真；但**总状态 not_ready**，带 blocked_by 与 `doc api` 证据 |
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import lceda  # noqa: E402
from dsh_embagent.design import lceda_fab as fab_module  # noqa: E402
from dsh_embagent.design import lceda_panel as panel_module  # noqa: E402
from dsh_embagent.design import lceda_sch as sch_module  # noqa: E402

REAL_PROJECT = Path(r"C:\Users\<user>\Desktop\示例复现区\复刻-示例")

_failures: list[str] = []
_checks = 0


def _safe(text: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(text).encode(encoding, "replace").decode(encoding, "replace")


def check(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if ok:
        print(_safe(f"[OK ] {name}"))
    else:
        _failures.append(name)
        print(_safe(f"[FAIL] {name}"))
    if detail:
        print(_safe(f"       {detail}"))


def _ctx() -> dict[str, str]:
    record = json.loads((REAL_PROJECT / "emit.json").read_text(encoding="utf-8"))
    project = record["project"]
    pcb = next(board["pcbUuid"] for board in project["boards"] if board.get("pcbUuid"))
    return {"projectUuid": project["uuid"], "pcbUuid": pcb, "settleMs": 900}


def _session() -> lceda.LcedaSession:
    return lceda.LcedaSession().open("", headless=True)


# ---------------------------------------------------------------------------
# A. 制造包
# ---------------------------------------------------------------------------


def part_fab() -> None:
    ready = (REAL_PROJECT / "emit.json").is_file()
    if not ready or not lceda.find_exe():
        print(_safe("[skip] A. 没有 emit.json 或 lceda-pro —— 这一节**没跑**，不是通过"))
        return
    session = _session()
    try:
        payload = fab_module.export(REAL_PROJECT, session=session, ctx=_ctx())
    finally:
        session.close(destroy=True)
    counts = payload.get("counts") or {}
    check("A1. 真实工程：**5 种制造文件全部落盘**（STEP/DXF/PDF/网表/测试点）",
          payload["status"] == "ok" and counts.get("ok") == 5
          and counts.get("missing") == 0,
          str([(row["kind"], row.get("bytes")) for row in payload["files"]]))
    sizes = {row["kind"]: int(row.get("bytes") or 0) for row in payload["files"]}
    check("A2. 大小合理（STEP >5 MB，DXF >0.5 MB，PDF >100 KB）",
          sizes.get("step", 0) > 5_000_000 and sizes.get("dxf", 0) > 500_000
          and sizes.get("pdf", 0) > 100_000, str(sizes))
    files = {row["kind"]: Path(row["file"]) for row in payload["files"]}
    step_head = files["step"].read_bytes()[:16] if "step" in files else b""
    pdf_head = files["pdf"].read_bytes()[:8] if "pdf" in files else b""
    check("A3. **头字节对**：STEP=ISO-10303-21，PDF=%PDF",
          step_head.startswith(b"ISO-10303-21") and pdf_head.startswith(b"%PDF"),
          f"step={step_head!r} pdf={pdf_head!r}")

    # 假文件防护：接口只回 {} 时，不能产出 2 字节的"IPC-2581"
    import base64  # noqa: PLC0415

    class _TinySession:
        ready = True
        version = "fake"
        headless = True

        def invoke(self, code, label="", **kw):
            if "openProject" in code:
                return {"ok": True}
            return {"ok": True, "name": "fake", "size": 2,
                    "b64": base64.b64encode(b"{}").decode()}

    tiny = fab_module.export(REAL_PROJECT, session=_TinySession(), ctx=_ctx(),
                             kinds=["netlist"])
    check("A4. 接口只回 2 字节 `{}` → **not_ready**，不产出假文件",
          tiny["status"] == "not_ready" and "字节" in str(tiny["missing"]),
          str(tiny["missing"])[:110])

    class _WrongMagic(_TinySession):
        def invoke(self, code, label="", **kw):
            if "openProject" in code:
                return {"ok": True}
            return {"ok": True, "name": "fake", "size": 8,
                    "b64": base64.b64encode(b"%PDF-1.4").decode()}

    wrong = fab_module.export(REAL_PROJECT, session=_WrongMagic(), ctx=_ctx(),
                              kinds=["dxf"])
    check("A5. 头字节不对（拿 %PDF 冒充 DXF）→ **failed**",
          wrong["status"] == "not_ready"
          and wrong["rows"][0]["status"] == "failed",
          str(wrong["rows"][0].get("reason"))[:110])


# ---------------------------------------------------------------------------
# B. 原理图 DRC
# ---------------------------------------------------------------------------


def part_sch() -> None:
    if not (REAL_PROJECT / "emit.json").is_file() or not lceda.find_exe():
        print(_safe("[skip] B. 没有 emit.json 或 lceda-pro —— 这一节**没跑**，不是通过"))
        return
    session = _session()
    try:
        payload = sch_module.run(REAL_PROJECT, session=session, ctx=_ctx())
    finally:
        session.close(destroy=True)
    counts = payload.get("counts") or {}
    check("B1. 真实工程：**每页都查过**，0 error / 0 fatal（warn 单列）",
          payload["status"] == "ok" and counts.get("checked") == counts.get("pages")
          and counts.get("error") == 0 and counts.get("fatalError") == 0
          and counts.get("pages", 0) >= 1,
          str(counts))

    class _Fake:
        ready = True
        version = "fake"
        headless = True
        mode = "error"

        def invoke(self, code, label="", **kw):
            if "getAllSchematicPagesInfo" in code:
                return {"opened": True, "pages": [{"uuid": "p1", "name": "P1"}]}
            if self.mode == "error":
                return {"opened": True, "grouped": 1,
                        "errors": [{"type": "error", "rule": "IN-IN", "net": "GND"}]}
            return {"opened": True, "grouped": 1,
                    "errors": [{"type": "warn", "rule": "CHECKFLOATPIN", "net": ""}]}

    fake = _Fake()
    bad = sch_module.run(REAL_PROJECT, session=fake, ctx={"projectUuid": "x"})
    check("B2. 有 `error` → **failed**（`warn` 不挡门）",
          bad["status"] == "failed" and bad["counts"]["error"] == 1,
          str(bad["counts"]))
    fake.mode = "warn"
    soft = sch_module.run(REAL_PROJECT, session=fake, ctx={"projectUuid": "x"})
    check("B3. 只有 `warn` → **ok**（刻意的悬空脚不该拦人）",
          soft["status"] == "ok" and soft["counts"]["warn"] == 1,
          str(soft["counts"]))


# ---------------------------------------------------------------------------
# C. 拼板容器
# ---------------------------------------------------------------------------


def part_panel() -> None:
    if not (REAL_PROJECT / "emit.json").is_file() or not lceda.find_exe():
        print(_safe("[skip] C. 没有 emit.json 或 lceda-pro —— 这一节**没跑**，不是通过"))
        return
    import os  # noqa: PLC0415
    name = f"p59-check-{os.getpid()}"
    session = _session()
    try:
        payload = panel_module.create(REAL_PROJECT, session=session, ctx=_ctx(),
                                      name=name)
        panel = payload.get("panel") or {}
        # **用完删掉**：这是验收夹具，不该留在用户工程里（AGENTS §4.4）。
        # 删除同时证明 `deletePanel` 也通。
        deleted = False
        restored = None
        if panel.get("uuid"):
            try:
                deleted = session.invoke(
                    "return await eda.dmt_Panel.deletePanel(%s);"
                    % json.dumps(panel["uuid"]), timeout_ms=120_000)
                listing = session.invoke(
                    "const ps = await eda.dmt_Panel.getAllPanelsInfo();"
                    "return (ps || []).length;", timeout_ms=120_000)
                restored = int(listing) if isinstance(listing, int) else None
            except Exception:  # noqa: BLE001
                deleted = False
    finally:
        session.close(destroy=True)
    check("C1. 容器真建出来了（uuid + 改名成功 + 面板数 +1），用完**删掉**",
          bool(panel.get("uuid")) and panel.get("renamed") is True
          and (payload.get("counts") or {}).get("panels_after", 0)
          == (payload.get("counts") or {}).get("panels_before", 0) + 1
          and deleted and restored == (payload.get("counts") or {}).get("panels_before"),
          f"{str(panel)[:100]} deleted={deleted} restored={restored}")
    check("C2. **拼板未完成 → 状态 not_ready**，并写清 blocked_by（不是 ok）",
          payload["status"] == "not_ready" and payload.get("panel_done") is False
          and len(payload.get("blocked_by") or []) >= 2,
          str(payload.get("blocked_by"))[:120])
    evidence = payload.get("api_evidence") or {}
    check("C3. 边界带**证据**：`doc api` 对 PNL_ManufactureData/Drc/Import 返回 null",
          evidence.get("PNL_ManufactureData") == "null"
          and evidence.get("PNL_Drc") == "null"
          and evidence.get("PNL_Import") == "null",
          str(evidence))

    class _NoPanel:
        ready = True
        version = "fake"
        headless = True

        def invoke(self, code, label="", **kw):
            return {"ok": False, "reason": "createPanel 返回 undefined"}

    fake = panel_module.create(REAL_PROJECT, session=_NoPanel(), ctx={"projectUuid": "x"})
    check("C4. `createPanel` 返回 undefined → not_ready（不假装建了）",
          fake["status"] == "not_ready", str(fake.get("reason")))


def main() -> int:
    print("=== P59 立创侧扩产：制造包 / 原理图 DRC / 拼板容器 ===")
    part_fab()
    part_sch()
    part_panel()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())