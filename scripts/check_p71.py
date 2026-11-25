"""P71 验收 —— 前端 P3（治理面板）/ P4（空态 + 可访问性）。

    python -m dsh_embagent.scripts.check_p71

## 判据

| 项 | 期望 |
|---|---|
| 注册表 | `preflight/diff/maturity/adr` 都在 delivery，标签齐全 |
| 网关 | 5 个 `@Remote`（preflight/readbackDiff/maturity/adrList/adrValidate）|
| 契约 | `WorkbenchFace` 与 `createFace` 都有 5 个方法（不是只加一半）|
| store | 4 个状态字段 + 4 个动作，**not_ready 也留载荷**（不是当失败丢弃）|
| 面板 | 四个面板存在；没结果时写"空白不代表通过" |
| P4 | 长任务横幅 `role=status aria-live=polite`；分区切换有键盘提示 |
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "src" / "client"

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


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def part_registry() -> None:
    features = _read(CLIENT / "features.ts")
    rows = re.findall(
        r"id: '(\w+)', label: '([^']+)', panel: '(\w+)', order: (\d+)", features)
    by_id = {row[0]: row for row in rows}
    want = {"preflight": "预检", "diff": "回读", "maturity": "成熟度", "adr": "决策"}
    ok = all(by_id.get(k, ('', '', ''))[1] == label
             and by_id.get(k, ('', '', ''))[2] == 'delivery'
             for k, label in want.items())
    check("A1. 四个治理分区都在 delivery（标签齐全）", ok,
          str({k: by_id.get(k) for k in want}))
    check("A2. 注册表仍只有 19 个分区（15 + 4）", len(rows) == 19, str(len(rows)))


def part_gateway() -> None:
    service = _read(ROOT / "src" / "service.ts")
    methods = ("preflight", "readbackDiff", "maturity", "adrList", "adrValidate")
    missing = [m for m in methods if f"@Remote('{m}')" not in service]
    check("B1. 网关 5 个 @Remote 都在", not missing, str(missing))
    check("B2. 回读用 designLong（会开立创会话，默认 120s 不够）",
          "designLong(600_000, 'readback'" in service)

    rpc = _read(CLIENT / "rpc.ts")
    face_missing = [m for m in methods if f"\n  {m}(" not in rpc]
    created = [m for m in methods if f"{m}: (project) =>" not in rpc]
    check("B3. WorkbenchFace 与 createFace **两处都加了**", not face_missing and not created,
          f"face={face_missing} create={created}")


def part_store() -> None:
    store = _read(CLIENT / "store.ts")
    fields = ("preflight", "diff", "maturity", "adr")
    missing = [f for f in fields if f"readonly {f}:" not in store]
    check("C1. store 有四个结果字段", not missing, str(missing))
    actions = ("runPreflight", "runReadbackDiff", "runMaturity", "runAdrValidate")
    check("C2. store 有四个动作", all(f"async {a}(" in store for a in actions))
    check("C3. 治理动作**不走 edit()**（not_ready 也要留载荷）",
          "private async governance" in store
          and "'预检'" in store and "preflight: payload" not in store)
    # governance 里把 payload 写进字段（用变量名传，不是硬编码属性名）
    check("C4. 治理动作把载荷写回字段（[field]: payload）",
          "[field]: payload" in store)


def part_panels() -> None:
    panels = _read(CLIENT / "panels_governance.tsx")
    exports = re.findall(r"export function (\w+Panel)", panels)
    check("D1. 四个面板都导出",
          set(exports) >= {"PreflightPanel", "DiffPanel", "MaturityPanel", "AdrPanel"},
          str(exports))
    check("D2. 没结果时说「空白不代表通过」（空态必须说话）",
          panels.count("<Empty text=") >= 4 and "空白不代表通过" in panels,
          f"Empty 用了 {panels.count('<Empty text=')} 次")
    view = _read(CLIENT / "HwIdeView.tsx")
    cases = [f"case '{s}':" for s in ("preflight", "diff", "maturity", "adr")]
    check("D3. 视图把四个分区接上（不是只注册不渲染）",
          all(c in view for c in cases))
    check("D4. CSS 类有对应分片（死样式检查会扫）",
          ".hwv-gov-dim" in _read(CLIENT / "styles" / "panels.ts")
          and "hwv-gov-dim" in panels)


def part_p4() -> None:
    view = _read(CLIENT / "HwIdeView.tsx")
    panels = _read(CLIENT / "panels.tsx")
    check("E1. 长任务横幅是 role=status + aria-live=polite（读屏能听到）",
          'role="status" aria-live="polite"' in view
          and 'role="status" aria-live="polite"' in panels)
    check("E2. 分区切换有键盘提示（[ / ]）",
          '键盘 [ / ]' in view and "event.key !== '['" in view)
    check("E3. 新面板没有内联颜色/魔法 z-index（走令牌）",
          not re.search(r"color: '#|zIndex: \d", panels))


def main() -> int:
    print("=== P71 前端 P3/P4 ===")
    part_registry()
    part_gateway()
    part_store()
    part_panels()
    part_p4()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())