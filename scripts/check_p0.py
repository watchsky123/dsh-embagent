"""P0 验收：结论层（一屏结论）。

    python -m hw_velxio.scripts.check_p0

P0 只做一件事：**让"这版行不行、下一步做什么"出现在首屏**。
所以这 11 条只验三件事——数据对不对、排序对不对、过期了说不说。

## 为什么每一条都要有"正例 + 反例"

单看"levels 有 9 层"很容易永远为真（夹具恰好有 9 层）。所以每条都配一个
**反例**：没有产物时不能算通过、排序反了要能测出来、过期不能装成新鲜。

## 这一层不判定

`verdict.compose()` 只投影已有产物。验收里有一条专门证明它**没有自己判**：
把 `handoff.json` 的六类计数与 `verdict.counts` 逐类比对，必须一致。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import handoff, verdict  # noqa: E402
from hw_velxio.design.store import DesignStore  # noqa: E402

PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []
REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "hw_velxio" / "tests" / "fixtures" / "designs"


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": str(detail)[:900]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {str(detail)[:900]}")
    return ok


def _workspace(tmp: Path) -> Path:
    """把一个真夹具拷成工程，供临时工作区使用（不污染用户目录）。"""
    root = tmp / "ws"
    project = root / "p0-fixture"
    project.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "tiny-blink.json", project / "design.json")
    return root


def _env(root: Path) -> dict[str, str]:
    return {**os.environ, "HWV_WORKSPACE": str(root)}


# ---------------------------------------------------------------------------
# 1. 空态：没跑过判定 ≠ 通过
# ---------------------------------------------------------------------------


def check_empty(tmp: Path) -> None:
    root = _workspace(tmp)
    old = os.environ.get("HWV_WORKSPACE")
    os.environ["HWV_WORKSPACE"] = str(root)
    try:
        payload = verdict.compose(DesignStore("p0-fixture"))
    finally:
        if old is None:
            os.environ.pop("HWV_WORKSPACE", None)
        else:
            os.environ["HWV_WORKSPACE"] = old

    check("1a. 没有 ladder.json → rollup=not_ready，levels 为空",
          payload.get("rollup") == "not_ready" and payload.get("levels") == [],
          f"rollup={payload.get('rollup')} levels={len(payload.get('levels') or [])}")

    check("1b. 空态 summary **明说**'不代表通过'（不是留白）",
          "不代表通过" in str(payload.get("summary")),
          str(payload.get("summary"))[:160])


# ---------------------------------------------------------------------------
# 2. 真产物：九层 + 六类 + 排序
# ---------------------------------------------------------------------------


def _real() -> dict[str, Any]:
    return verdict.compose(DesignStore("示例-多板"))


def check_real() -> None:
    payload = _real()
    levels = payload.get("levels") or []
    artifact = (DesignStore("示例-多板").read("ladder.json") or {}).get("levels") or []
    want = [str(row.get("level")) for row in artifact]
    want.sort(key=lambda name: verdict.LEVEL_ORDER.index(name)
              if name in verdict.LEVEL_ORDER else 99)
    check("2a. 真工程：层名与顺序和 ladder.json **逐项相同**（V0→V8）",
          [row["level"] for row in levels] == want and bool(want),
          f"产物={want} 结论={[r['level'] for r in levels]}")

    counts = payload.get("counts") or {}
    check("2b. `unknown` 折成计数（原字段是明细列表）",
          all(isinstance(row.get("unknown"), int) for row in levels)
          and max((row.get("unknown") or 0) for row in levels) > 0,
          f"V1 unknown={[r['unknown'] for r in levels]}")

    check("2c. rollup 由九层压成：有 fail 就是 fail",
          payload.get("rollup") == verdict.level_rollup(levels)
          and (payload.get("rollup") != "pass" or not any(
              r["status"] == "fail" for r in levels)),
          f"rollup={payload.get('rollup')}")


def check_order() -> None:
    payload = _real()
    next_rows = payload.get("next") or []
    check("3a. 下一步 ≤5 条（一屏放得下）",
          0 < len(next_rows) <= verdict.MAX_NEXT,
          f"{len(next_rows)} 条 / 共 {payload.get('next_total')} 条")

    ranks = [verdict.CATEGORY_RANK.get(str(r.get("category")), 99)
             for r in next_rows]
    check("3b. 类别按紧急度升序（blocking 在最前）",
          ranks == sorted(ranks), f"{[r.get('category') for r in next_rows]}")

    # 3c 验**判据本身能不能分辨乱序**（真退化见第 7 条）。
    # 用合成序列而不是真实 ranks：真实项目恰好只含 0 和 1 两类时，
    # 任何交换都不改变序列，那条断言就永远是绿的。
    ordered = [0, 1, 2]
    scrambled = [2, 0, 1]
    check("3c. 3b 用的判据 `x == sorted(x)` 对乱序输入为假（不是恒真）",
          ordered == sorted(ordered) and scrambled != sorted(scrambled),
          f"有序={ordered} 乱序={scrambled}")


# ---------------------------------------------------------------------------
# 3. 不自己判定
# ---------------------------------------------------------------------------


def check_no_judgement() -> None:
    store = DesignStore("示例-多板")
    payload = verdict.compose(store)
    reference = handoff.build(store).to_dict()
    check("4a. 六类计数与 handoff 逐类一致（**只投影、不重判**）",
          payload.get("counts") == reference.get("counts"),
          f"verdict={payload.get('counts')} handoff={reference.get('counts')}")

    # 再现性：连跑两次结果一样（排序稳定，没有随机/字典序抖动）
    again = verdict.compose(store)
    check("4b. 两次 compose 结果一致（排序稳定，不是字典序抖动）",
          json.dumps(payload.get("next"), sort_keys=True, ensure_ascii=False)
          == json.dumps(again.get("next"), sort_keys=True, ensure_ascii=False),
          f"{len(payload.get('next') or [])} 条一致")


# ---------------------------------------------------------------------------
# 4. 过期
# ---------------------------------------------------------------------------


def check_stale(tmp: Path) -> None:
    root = tmp / "ws2"
    project = root / "stale-fixture"
    project.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "tiny-blink.json", project / "design.json")
    # 造一份 ladder.json（内容随便，形状要真）
    ladder = {"ok": True, "stopped_at": "",
              "levels": [{"level": "V0", "title": "符号", "status": "pass",
                          "checked": 3, "failed": 0, "unknown": [], "ms": 1.0}],
              "summary": "x"}
    (project / "ladder.json").write_text(
        json.dumps(ladder, ensure_ascii=False), encoding="utf-8")

    old = os.environ.get("HWV_WORKSPACE")
    os.environ["HWV_WORKSPACE"] = str(root)
    try:
        fresh = verdict.compose(DesignStore("stale-fixture"))
        # 把 design.json mtime 推到 ladder 之后 = 设计改过但没重跑判定
        design_path = project / "design.json"
        stamp = design_path.stat().st_mtime + 120
        os.utime(design_path, (stamp, stamp))
        stale = verdict.compose(DesignStore("stale-fixture"))
    finally:
        if old is None:
            os.environ.pop("HWV_WORKSPACE", None)
        else:
            os.environ["HWV_WORKSPACE"] = old

    check("5a. design.json 比 ladder.json 新 → stale=true",
          stale.get("stale") is True and fresh.get("stale") is False,
          f"fresh.stale={fresh.get('stale')} stale.stale={stale.get('stale')}")

    check("5b. stale 时 summary **明说**'下面是上次判定'",
          "上次判定" in str(stale.get("summary")),
          str(stale.get("summary"))[:160])


# ---------------------------------------------------------------------------
# 5. 契约：snapshot 带 verdict；前端分区表把结论放首位
# ---------------------------------------------------------------------------


def check_contract() -> None:
    code, out = _run([sys.executable, "-m", "hw_velxio.design_cli", "snapshot",
                      "001-blink"])
    try:
        payload = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        payload = {}
    check("6a. `snapshot` 载荷里带 verdict（前端首屏靠它）",
          isinstance(payload.get("verdict"), dict)
          and "levels" in (payload.get("verdict") or {}),
          f"exit={code} keys={sorted(payload)[:6]}…")

    # 前端契约：**读 TS 源码**而不是猜——分区表是单一事实源
    store_src = (REPO / "hw_velxio" / "src" / "client" / "store.ts").read_text(
        encoding="utf-8")
    health_ok = "health: ['verdict', " in store_src
    label_ok = "verdict: '结论'" in store_src
    default_ok = "health: 'verdict'" in store_src
    check("6b. 前端：verdict 是 health 组第一个分区、标签为「结论」、且为默认分区",
          health_ok and label_ok and default_ok,
          f"首位={health_ok} 标签={label_ok} 默认={default_ok}")

    panel_src = (REPO / "hw_velxio" / "src" / "client" / "panels.tsx").read_text(
        encoding="utf-8")
    view_src = (REPO / "hw_velxio" / "src" / "client" / "HwIdeView.tsx").read_text(
        encoding="utf-8")
    has_case = "case 'verdict':" in view_src
    has_panel = "export function VerdictPanel" in panel_src
    check("6c. 前端：VerdictPanel 存在，且 view 里有 case 'verdict'",
          has_panel and has_case,
          f"panel={has_panel} case={has_case}")

    # 面板真的被接进去（只定义不接线 = 死代码）
    check("6d. 前端：VerdictPanel 已 import 并渲染（不是只定义）",
          "VerdictPanel," in view_src and "return <VerdictPanel" in view_src,
          "import + 渲染都在")


# ---------------------------------------------------------------------------
# 6. 退化：把排序反过来，3b 必须红
# ---------------------------------------------------------------------------


def check_degradation() -> None:
    saved = dict(verdict.CATEGORY_RANK)
    payload = _real()
    normal = [str(r.get("category")) for r in (payload.get("next") or [])]

    try:
        # 退化：类别优先级整体倒过来
        verdict.CATEGORY_RANK.clear()
        verdict.CATEGORY_RANK.update({name: 99 - rank
                                     for name, rank in saved.items()})
        degraded = [str(r.get("category")) for r in
                    (verdict.compose(DesignStore("示例-多板")).get("next") or [])]
    finally:
        verdict.CATEGORY_RANK.clear()
        verdict.CATEGORY_RANK.update(saved)

    check("7. 退化验证：把类别优先级倒过来 → next 的顺序真的变了（3b 会红）",
          normal != degraded,
          f"正常={normal[:4]} 退化={degraded[:4]}")


def _run(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(  # noqa: S603 - argv 由本脚本拼
        argv, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=600)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P0 验收：结论层（一屏结论）===\n")
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p0-"))
    try:
        if only in ("", "empty"):
            check_empty(tmp)
        if only in ("", "real"):
            check_real()
            check_order()
        if only in ("", "nojudge"):
            check_no_judgement()
        if only in ("", "stale"):
            check_stale(tmp)
        if only in ("", "contract"):
            check_contract()
        if only in ("", "degrade"):
            check_degradation()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for r in _RESULTS if r["status"] == PASS)
    failed = sum(1 for r in _RESULTS if r["status"] == FAIL)
    skipped = sum(1 for r in _RESULTS if r["status"] not in (PASS, FAIL))
    status = "ok" if failed == 0 and skipped == 0 else ("failed" if failed else "not_ready")
    print(f"\n=== {passed} 通过 / {failed} 失败 / {skipped} 未做到 ===")
    payload = {"status": status, "check": "P0", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())