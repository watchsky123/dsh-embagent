"""P3 验收：参数敏感度。

    python -m dsh_embagent.scripts.check_p3

P3 的问题不是"哪个参数敏感"，而是**136 个参数里 132 个根本没人读**。

所以这 20 条只验四件事：

1. **分类对**：`wired` / `wired_noop` / `unwired` / `missing` 各归各位，
   且总数 = 136（一个不漏、不重复）
2. **未接线 ≠ 不敏感**：这是本层最容易做成的假绿，必须专写断言
3. **没有 MuJoCo 时判定层是 `unknown`**，不是"不敏感"
4. **前端接上了**：`params` 分区存在、面板渲染、排序不在 TS 侧

## 为什么 1a 要扫源码

`wired` 的事实来源是 `mjcf.py` 里的 `params.get("...")`。
**新加一个 get 而表里没有** → 那个参数被报成"没人读"，
而真相是"刚接上但表里缺项"。这会让人去补一行根本不需要的条目。
所以扫源码收集 + 比对，把分叉变成红的（和 `check_p1` 1a 同一套手法）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import sensitivity, sim_params  # noqa: E402
from dsh_embagent.design.store import DesignStore  # noqa: E402

PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []
REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "dsh_embagent"
FIXTURES = PKG / "tests" / "fixtures" / "designs"


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": str(detail)[:900]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {str(detail)[:900]}")
    return ok


def _design(name: str = "stacked-2layer") -> Any:
    from dsh_embagent.design.model import DesignModel
    return DesignModel.from_dict(json.loads(
        (FIXTURES / f"{name}.json").read_text(encoding="utf-8")))


def _report() -> dict[str, Any]:
    return sensitivity.report(_design(), sim_params.load())


# ---------------------------------------------------------------------------
# 1. 分类
# ---------------------------------------------------------------------------


def check_buckets() -> None:
    wired = sensitivity.wired_keys()
    # **不写魔数**：断言"扫出来的就是源码里的"，而不是"等于 4"——
    # 后者一接新参数就红，而红的原因是对的（那就该红），但它测的是
    # 我的记忆而不是代码。真正的不变量是"表随代码走"。
    src = (PKG / "design" / "mjcf.py").read_text(encoding="utf-8")
    from_src = sorted(set(re.findall(r"(?<![\w.])params\.get\(\s*[\"']([a-z0-9_]+)",
                                     src)))
    check("1a. `wired_keys` 就是 mjcf 源码里的 `params.get` 集合（表随代码走）",
          wired == from_src and len(wired) <= 8,
          f"{len(wired)} 个：{wired}")

    # 关节私有参数不能被误当成 ParamSet（第一版踩过）
    check("1b. `joint.params.get(...)` **不算** ParamSet 接线",
          "range_deg" not in wired and "ratio" not in wired and "net" not in wired,
          f"wired={wired}")

    report = _report()
    counts = report["counts"]
    in_table = report["in_table_counts"]
    # **表内三类之和 = params_total**；`missing` 是表外项，不能算进去。
    # 第一版这里直接 `sum(counts.values())`，得到 137 ≠ 136，
    # 看起来像漏算——其实是对的（多出来的一个是"代码读了但表里没有"）。
    check("1c. 表内三类之和 = 参数总数（`missing` 是表外项，不计入）",
          sum(in_table.values()) == report["params_total"]
          == len(sim_params.load().params)
          and report["off_table"] == [r["key"] for r in report["missing"]],
          f"表内 {in_table} = {sum(in_table.values())} / "
          f"{report['params_total']}；表外 {report['off_table']}")

    check("1d. 真工程上**未接线占绝大多数**（这正是 P3 要说的事）",
          counts["unwired"] > 100,
          f"unwired={counts['unwired']} / {report['params_total']}")

    check("1e. `density_kg_m3` 被代码读但**不在表里** → 报 `missing`（不是忽略）",
          any(row["key"] == "density_kg_m3" for row in report["missing"]),
          f"missing={[r['key'] for r in report['missing']]}")


def check_priority() -> None:
    report = _report()
    priority = report["priority"]
    check("2a. `priority` 只含 wired 类，且**已排序**（排序在 Python 算）",
          bool(priority) and all(row["status"] == "wired" for row in priority)
          and [len(r["changed"]) for r in priority] == sorted(
              [len(r["changed"]) for r in priority], reverse=True),
          f"{[(r['key'], r['changed']) for r in priority]}")

    check("2b. 每个 wired 参数都记了**哪些扰动方向**有响应",
          all(row["changed"] for row in priority),
          str([r["changed"] for r in priority]))


# ---------------------------------------------------------------------------
# 2. 「未接线」与「不敏感」必须分开（本层的核心）
# ---------------------------------------------------------------------------


def check_not_confused() -> None:
    report = _report()
    unwired_keys = {row["key"] for row in report["unwired"]}

    # tyres 参数在表里、值非零，但没有任何代码读它 → 必须进 unwired
    check("3a. `mu_tyre_rod` 在表里但没人读 → 归 `unwired`（不是 wired_noop）",
          "mu_tyre_rod" in unwired_keys,
          f"在 unwired 里：{('mu_tyre_rod' in unwired_keys)}")

    # 关键：unwired 的说明必须说"没人读"，而不是"敏感度低"
    notes = {row["key"]: str(row.get("note") or "") for row in report["unwired"]}
    # 措辞只要求"说清了没人读"这个意思，**不锁死具体字面量**——
    # 锁死字面量的断言在改文案时会红，而那时行为是对的。
    bad = [key for key, note in notes.items()
           if not ("没人读" in note or "代码读" in note)]
    check("3b. 每条 unwired 的说明都写「没人读」，**不写「不敏感」**",
          not bad, f"措辞不对的：{bad[:3]}")

    # wired_noop 的说明要说"读了但没效果"，与 unwired 区分开
    noop_notes = [str(row.get("note") or "") for row in report["wired_noop"]]
    check("3c. `wired_noop` 的说明**都说清「被读取了」**（与 unwired 区分开）",
          all("读取" in note for note in noop_notes),
          f"{noop_notes[:2]}")

    # 反证：把 unwired 的参数硬报成 wired_noop，3a/3b 会红
    row = next(r for r in report["unwired"] if r["key"] == "mu_tyre_rod")
    check("3d. 反证：unwired 的行 `status` 不是 `wired_noop`（两者不可混）",
          row["status"] == "unwired",
          f"status={row['status']}")


# ---------------------------------------------------------------------------
# 3. 判定层：没有 MuJoCo 时是 unknown，不是「不敏感」
# ---------------------------------------------------------------------------


def check_verdict_layer() -> None:
    design = _design()
    params = sim_params.load()
    result = sensitivity.sweep_verdict(design, params, keys=["dt"])
    rows = result.get("dt") or []
    check("4a. 判定层对每个扰动都返回一行（有 baseline 与 perturbed）",
          len(rows) == len(sensitivity.PERTURBATIONS)
          and all("baseline" in r and "perturbed" in r for r in rows),
          f"{rows}")

    # 有 MuJoCo：状态是 pass/fail/unknown 之一
    check("4b. 判定层的状态落在 pass/fail/unknown 里",
          all(r["baseline"] in ("pass", "fail", "unknown") for r in rows),
          f"{[r['baseline'] for r in rows]}")

    # 没 MuJoCo 时：unknown → unknown，flipped=False，**但这不是"不敏感"**
    env = {**os.environ, "HWV_NO_MUJOCO": "1"}
    code, out = _run([sys.executable, "-c",
                      "import sys,json;sys.path.insert(0,'.');"
                      "from pathlib import Path;"
                      "from dsh_embagent.design.model import DesignModel;"
                      "from dsh_embagent.design import sim_params, sensitivity;"
                      "d=DesignModel.from_dict(json.loads(Path("
                      "'dsh_embagent/tests/fixtures/designs/stacked-2layer.json'"
                      ").read_text(encoding='utf-8')));"
                      "r=sensitivity.sweep_verdict(d, sim_params.load(), keys=['dt']);"
                      "print(json.dumps(r['dt']))"], env=env)
    try:
        degraded = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        degraded = []
    check("4c. 没有 MuJoCo 时判定层是 `unknown`（**不是不敏感**）",
          degraded and all(r["baseline"] == "unknown" for r in degraded),
          f"exit={code} {[(r['baseline'], r['perturbed']) for r in degraded]}")


# ---------------------------------------------------------------------------
# 4. 契约：CLI / 快照 / 前端
# ---------------------------------------------------------------------------


def check_contract() -> None:
    code, out = _run([sys.executable, "-m", "dsh_embagent.design_cli", "sensitivity",
                      "示例-多板"])
    try:
        payload = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        payload = {}
    check("5a. CLI `sensitivity <id>` 返回四类计数",
          payload.get("status") == "ok"
          and set(payload.get("counts") or {}) ==
          {"wired", "wired_noop", "unwired", "missing"},
          f"exit={code} counts={payload.get('counts')}")

    code2, out2 = _run([sys.executable, "-m", "dsh_embagent.design_cli", "snapshot",
                        "001-blink"])
    try:
        snapshot = json.loads(out2.strip().splitlines()[-1])
    except (ValueError, IndexError):
        snapshot = {}
    params_payload = snapshot.get("params") or {}
    check("5b. 快照带 `params` 摘要（首屏一句话：136 里几个真的驱动模型）",
          params_payload.get("total") == 136
          and "wired" in (params_payload.get("counts") or {}),
          f"total={params_payload.get('total')} counts={params_payload.get('counts')}")

    # 摘要必须**轻**：不能把 136 行全表塞进快照
    check("5c. 快照里只带优先项（不是 136 行全表）",
          len(params_payload.get("priority") or []) <= 10,
          f"priority {len(params_payload.get('priority') or [])} 行")

    store_src = (PKG / "src" / "client" / "store.ts").read_text(encoding="utf-8")
    panels = (PKG / "src" / "client" / "panels.tsx").read_text(encoding="utf-8")
    view = (PKG / "src" / "client" / "HwIdeView.tsx").read_text(encoding="utf-8")

    # P70：分区表在 features.ts（store 派生 + 重导出）
    features = (PKG / "src" / "client" / "features.ts").read_text(encoding="utf-8")
    rows = re.findall(
        r"id: '(\w+)', label: '([^']+)', panel: '(\w+)', order: (\d+)", features)
    check("6a. 前端：`params` 是 health 组的一个分区，标签为「参数」",
          any(row[0] == 'params' and row[1] == '参数' and row[2] == 'health'
              for row in rows),
          "注册表里 分区+标签+组 都在")

    has_panel = "export function ParamsPanel" in panels
    has_case = "case 'params':" in view
    check("6b. `ParamsPanel` 存在，且 view 里有 case 'params'",
          has_panel and has_case, f"panel={has_panel} case={has_case}")

    # 四类都要在面板上出现（否则"未接线"看不见 = 假绿又回来了）
    check("6c. 面板上四类**各自可见**（未接线不会被并入某一类）",
          all(marker in panels for marker in
              ("驱动模型", "读了但无效果", "未接线", "表里缺项")),
          "四类标签都在")

    # 排序不在 TS 侧：面板不应对 priority 排序
    check("6d. 面板**不重排** priority（排序是判定，只在 Python 一份）",
          ".sort(" not in panels.split("export function ParamsPanel")[1]
          .split("export function ChecksPanel")[0],
          "ParamsPanel 里没有 .sort(")


def _run(argv: list[str], *, env: dict[str, str] | None = None) -> tuple[int, str]:
    proc = subprocess.run(  # noqa: S603
        argv, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env or os.environ.copy(), check=False, timeout=600)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 5. 退化
# ---------------------------------------------------------------------------


def check_degradation() -> None:
    saved = sensitivity._READ_RE

    # 退化：把后向断言去掉（`joint.params` 又被当成 ParamSet）
    try:
        sensitivity._READ_RE = re.compile(
            r"params\.get\(\s*[\"\']([a-zA-Z0-9_]+)")
        loose = sensitivity.wired_keys()
    finally:
        sensitivity._READ_RE = saved

    strict = sensitivity.wired_keys()
    check("7. 退化验证：去掉 `(?<![\\w.])` → 多抓出关节私有参数（1b 会红）",
          len(loose) > len(strict),
          f"严格 {len(strict)} 个，退化后 {len(loose)} 个："
          f"{sorted(set(loose) - set(strict))}")

    # 恢复后必须回到原值（测试自己不留副作用）
    check("7b. 恢复后 wired 集合回到原值",
          sensitivity.wired_keys() == strict,
          f"{sensitivity.wired_keys()}")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P3 验收：参数敏感度 ===\n")
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p3-"))
    try:
        if only in ("", "buckets"):
            check_buckets()
            check_priority()
        if only in ("", "confused"):
            check_not_confused()
        if only in ("", "verdict"):
            check_verdict_layer()
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
    payload = {"status": status, "check": "P3", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())