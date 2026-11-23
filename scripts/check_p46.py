"""P46 验收 —— 前端两个分区：`emit`（8 段状态 + 产物）与 `brief`（半成品待办）。

    python -m hw_velxio.scripts.check_p46

## 这一段补的是什么

P40–P45 把出板链和机械/制造判据都做出来了，但**界面上一点都看不到**：
8 段发射跑完只回聊天记录，`open_items.md` 只有人自己去翻文件。
P46 把两者接进快照并各给一个分区。

## 判据（前端靠**读源码**，因为 tsc 抓不到"定义了但没渲染"）

| 项 | 期望 |
|---|---|
| 分区表 | `SectionId` 有 `emit`/`brief`；都在 `delivery` 组；**emit 排在 export 前面** |
| 标签 | `SECTION_LABEL` 有「发射」「半成品」 |
| 面板 | `panels.tsx` 导出两者；`HwIdeView` **import 并渲染**（`case 'emit'`）|
| 动作 | store 有 `emitRun`/`emitReverify`，且经 `common` 传进面板 |
| RPC | `service.ts` 有 `@Remote('emitRun')`/`('emitProgress')`；`rpc.ts` 接口+工厂都有 |
| 快照 | 有 emit.json/scaffold.json 就带摘要；**没有就 not_ready + 原因** |
| 跳转 | `hw_emit`/`hw_scaffold` 的 viewHint 指 `emit`；**`'2d'` 不能残留**（它一直被静默丢弃）|

退化验证：让 `emit_summary` 凭空返回 `ok` → 「没有 emit.json 必须 not_ready」变红。
"""

from __future__ import annotations

import re

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio import design_cli  # noqa: E402
from hw_velxio.design.model import Board, DesignModel  # noqa: E402
from hw_velxio.design.store import DesignStore  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "p46-fixture"

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


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. 分区表与标签
# ---------------------------------------------------------------------------


def part_sections() -> None:
    # P70：分区注册表在 `features.ts`（store 派生 + 重导出）
    features = _read("src", "client", "features.ts")
    rows = re.findall(
        r"id: '(\w+)', label: '([^']+)', panel: '(\w+)', order: (\d+)", features)
    check("1a. `SectionId` 里有 `emit` 与 `brief`",
          any(row[0] == 'emit' for row in rows)
          and any(row[0] == 'brief' for row in rows),
          "features.ts 的注册表")
    delivery = [row for row in rows if row[2] == 'delivery']
    check("1b. 两者都在 `delivery` 组里",
          {row[0] for row in delivery} >= {'emit', 'brief'},
          str([row[0] for row in delivery]))
    order = {row[0]: int(row[3]) for row in delivery}
    check("1c. **emit 排在 export 前面**（人先出板再打包）",
          order.get('emit', 999) < order.get('export', -1),
          f"emit={order.get('emit')} export={order.get('export')}")
    labels = {row[0]: row[1] for row in rows}
    check("1d. `SECTION_LABEL` 有「发射」与「半成品」",
          labels.get('emit') == '发射' and labels.get('brief') == '半成品',
          str({k: labels.get(k) for k in ('emit', 'brief')}))


# ---------------------------------------------------------------------------
# 2. 面板被渲染（不是只定义）
# ---------------------------------------------------------------------------


def part_panels() -> None:
    panels = _read("src", "client", "panels.tsx")
    view = _read("src", "client", "HwIdeView.tsx")
    check("2a. `panels.tsx` 导出 `EmitPanel` 与 `BriefPanel`",
          "export function EmitPanel(" in panels
          and "export function BriefPanel(" in panels)
    check("2b. `HwIdeView` **import 了它们**",
          "EmitPanel" in view and "BriefPanel" in view
          and "} from './panels.js'" in view)
    check("2c. 两个 case 都在 switch 里（`case 'emit'` / `case 'brief'`）",
          "case 'emit': return <EmitPanel" in view
          and "case 'brief': return <BriefPanel" in view,
          "只有 import 没有 case = 定义了但不渲染（§3.13 的形状）")
    check("2d. 8 段状态真的画出来了（面板里遍历 `emit.jobs`）",
          "emit.jobs" in panels and "job.status" in panels)
    check("2e. 产物列表画出来了（不只是一行字）",
          "artifacts" in panels and "a.name" in panels)
    check("2f. 待办列表画出来了（`open_items` 逐条）",
          "open_items" in panels and "item.what" in panels)
    check("2g. 复制失败**不假装成功**（catch 里把 copied 置回 false）",
          "setCopied(false)" in panels)


# ---------------------------------------------------------------------------
# 3. 动作与 RPC
# ---------------------------------------------------------------------------


def part_actions() -> None:
    store = _read("src", "client", "store.ts")
    view = _read("src", "client", "HwIdeView.tsx")
    check("3a. store 有 `emitRun` 与 `emitReverify` 两个动作",
          "async emitRun(" in store and "async emitReverify(" in store)
    check("3b. `只重验` 真的是只跑 verify（`emitRun('verify')`）",
          "emitRun('verify')" in store)
    check("3c. 两个动作经 `common` 传进面板（否则按钮永远 disabled）",
          "onEmitRun: () => { void store.emitRun() }" in view
          and "onEmitReverify:" in view)

    service = _read("src", "service.ts")
    rpc = _read("src", "client", "rpc.ts")
    check("3d. `service.ts` 有 `@Remote('emitRun')` 与 `@Remote('emitProgress')`",
          "@Remote('emitRun')" in service and "@Remote('emitProgress')" in service)
    check("3e. `rpc.ts` 的接口**与**工厂都实现了这两个方法",
          "emitRun(project: string" in rpc and "emitProgress(project: string" in rpc
          and "emitRun: (project, jobs, route) =>" in rpc
          and "emitProgress: (project) =>" in rpc)
    check("3f. 发射给了长超时（8 段要几分钟，默认超时会把成功的发射判成失败）",
          "designLong(900_000" in service)


# ---------------------------------------------------------------------------
# 4. 快照真的带摘要（Python 侧）
# ---------------------------------------------------------------------------


def part_snapshot() -> None:
    store = DesignStore(PROJECT)
    design = DesignModel(
        id=PROJECT,
        board=Board(kind="generic", name="b",
                    outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                    size_mm={"w": 40, "h": 30}, thickness_mm=1.6))
    store.save(design)
    try:
        snap = design_cli.snapshot(PROJECT)
        check("4a. 没有 emit.json → `not_ready` 且写了原因（不是 ok）",
              snap["emit"]["status"] == "not_ready"
              and "emit.json" in str(snap["emit"].get("reason")),
              f"{snap['emit'].get('reason')}")
        check("4b. 没有 scaffold.json → `not_ready` 且写了原因",
              snap["scaffold"]["status"] == "not_ready"
              and "scaffold" in str(snap["scaffold"].get("reason")),
              f"{snap['scaffold'].get('reason')}")

        store.write("emit.json", {
            "status": "ok",
            "jobs": {"project": {"status": "ok", "note": "新建工程"},
                     "route": {"status": "not_ready", "note": "布不通"}},
            "verify_detail": {"totals": {"nets": 4, "connected": 4, "split": 0}},
            "fabrication": {"status": "not_ready", "reasons": ["DFM 不可用"]},
            "channel": {"kind": "cli"},
        })
        store.write("scaffold.json", {
            "status": "ok", "route": "synth",
            "stages": [{"stage": "design", "status": "ok", "note": ""}],
            "open_items": [{"what": "引脚表是推的", "need": "核对 pinmap"}],
        })
        snap2 = design_cli.snapshot(PROJECT)
        emit = snap2["emit"]
        check("4c. 有 emit.json → 8 段列表 + 未通过段 + verify 计数都带出来",
              len(emit["jobs"]) == 2 and emit["bad_jobs"] == ["route"]
              and emit["verify"]["connected"] == 4,
              f"jobs={[j['name'] for j in emit['jobs']]} bad={emit['bad_jobs']}")
        check("4d. 制造结论也带出来（DFM 不可用时不能显示成可打板）",
              str((emit.get("fabrication") or {}).get("status")) == "not_ready")
        scaff = snap2["scaffold"]
        check("4e. 有 scaffold.json → 段状态 + 待办条数",
              scaff["count"] == 1 and scaff["open_items"][0]["need"] == "核对 pinmap",
              f"count={scaff['count']}")
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 5. 跳转提示
# ---------------------------------------------------------------------------


def part_hints() -> None:
    tools = _read("src", "tools.ts")
    check("5a. `hw_emit` 跳「发射」而不是「判定」",
          "name: 'hw_emit'" in tools
          and "viewHint(project, 'emit')" in tools)
    check("5b. **`'2d'` 这个无效分区没有残留**（它一直被 isSectionId 丢掉）",
          "viewHint(project, '2d')" not in tools,
          "findings/reasons: 修掉了 hw_roles 的旧提示")
    check("5c. `hw_roles` 现在指到 `blocks`（真实的装配计划页）",
          "viewHint(project, 'blocks')" in tools)


# ---------------------------------------------------------------------------
# 6. 退化
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    """让 `emit_summary` 凭空返回 ok → 4a 必须变红。"""
    store = DesignStore(PROJECT)
    design = DesignModel(
        id=PROJECT,
        board=Board(kind="generic", name="b",
                    outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                    size_mm={"w": 40, "h": 30}, thickness_mm=1.6))
    store.save(design)
    original = design_cli.emit_summary
    try:
        baseline = design_cli.snapshot(PROJECT)["emit"]["status"]
        design_cli.emit_summary = lambda _store: {   # type: ignore[assignment]
            "status": "ok", "jobs": [], "artifacts": [], "note": "退化版"}
        degenerated = design_cli.snapshot(PROJECT)["emit"]["status"]
    finally:
        design_cli.emit_summary = original  # type: ignore[assignment]
        store.delete()
    check("6. 退化验证：让 emit_summary 凭空说 ok → 4a 变红（证明'没发射过'真的在判）",
          baseline == "not_ready" and degenerated == "ok",
          f"baseline={baseline} degenerated={degenerated}")


def main() -> int:
    print("=== P46 前端：emit（8 段）与 brief（半成品） ===")
    part_sections()
    part_panels()
    part_actions()
    part_snapshot()
    part_hints()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())