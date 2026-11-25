"""P2 验收：按对象聚合。

    python -m dsh_embagent.scripts.check_p2

P2 只做一件事：**点一个元件，五份产物里关于它的话一次看全**。

这 18 条验四件事：

1. **索引对**：`claims` / `justify` / `checks` / `tests` 都按 `ref` 归位，
   一个件不漏、不串（`C1` 与 `C10` 前缀相同，用 id 反推会错）
2. **缺数据说实话**：没断言不是"通过"，没归因不是"无作用"
3. **三态压得有理由**：实测失败 > 断言失败 > unknown > 无数据 > pass
4. **前端接上了**：`entity` RPC 存在、画像真的渲染、按需加载不是全量塞快照

## 为什么 2b 要用合成夹具

真实工程里 `示例-多板` 每个件都有断言（266 条 / 141 件），
"没断言"这个分支**真实数据测不到**。所以那一条必须自己造一个没有
`claims.json` 的工程——这是 AGENTS §0.3 那个区分的又一次应用。
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

from dsh_embagent.design import entity  # noqa: E402
from dsh_embagent.design.store import DesignStore  # noqa: E402

PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []
REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "dsh_embagent"
FIXTURES = PKG / "tests" / "fixtures" / "designs"
PROJECT = "示例-多板"


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": str(detail)[:900]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {str(detail)[:900]}")
    return ok


def _store() -> DesignStore:
    return DesignStore(PROJECT)


def _profile(ref: str) -> dict[str, Any]:
    store = _store()
    design = store.load()
    return entity.profile(design, ref, claims=store.read("claims.json"),
                          justify=store.read("justify.json"),
                          checks=store.read("checks.json"),
                          units=store.read("units.json"))


# ---------------------------------------------------------------------------
# 1. 索引：归位准确，不串不断
# ---------------------------------------------------------------------------


def check_index() -> None:
    store = _store()
    design = store.load()
    claims = store.read("claims.json")
    checks = store.read("checks.json")
    idx = entity.index(design, claims=claims, checks=checks)

    check("1a. 索引覆盖设计里的**每一个**位号（不多不少）",
          set(idx) == {p.ref for p in design.parts},
          f"索引 {len(idx)} / 设计 {len(design.parts)} 件")

    # claims 总数必须与 claims.json 自报的 count 对得上（归位没丢）
    total = sum(row["claims"] for row in idx.values())
    check("1b. 索引里 claim 计数之和 = claims.json 的 count（归位没丢）",
          total == int((claims or {}).get("count") or 0),
          f"索引合计 {total} / claims.count {(claims or {}).get('count')}")

    # 前缀陷阱：C1 与 C10 必须分开
    c1 = idx.get("C1") or {}
    c10 = idx.get("C10") or {}
    check("1c. `C1` 与 `C10` 不串（用 scope.part 索引，不用 id 正则切）",
          c1 != c10 or (c1.get("claims") == c10.get("claims") == 0),
          f"C1={c1.get('claims')} C10={c10.get('claims')}")

    # 一条 finding 牵涉两个件时，两边都要有
    multi = next((f for f in (checks or {}).get("findings") or []
                  if len(f.get("refs") or []) >= 2), None)
    if multi is not None:
        refs = [str(r) for r in multi["refs"][:2]]
        both = all((idx.get(r) or {}).get("findings", 0) >= 1 for r in refs)
        check("1d. 一条 finding 牵涉多个件时，**每个件都记到**",
              both, f"refs={refs}")
    else:
        check("1d. 一条 finding 牵涉多个件时，每个件都记到", True,
              "本工程没有多引用 finding（跳过）", status=SKIP)


# ---------------------------------------------------------------------------
# 2. 画像内容
# ---------------------------------------------------------------------------


def check_profile() -> None:
    profile = _profile("C146")
    check("2a. 画像带齐五份：断言 / 归因 / 检查 / 实测 / 单元",
          all(k in profile for k in
              ("claims", "justification", "checks", "tests", "unit")),
          f"keys={sorted(profile)}")

    check("2b. 归因不是空的（真工程上 C146 有 why）",
          bool(profile["justification"].get("why")),
          str(profile["justification"].get("why"))[:70])

    # 网表：C146 不接任何网络 → nets 为空是**结论**，不是缺数据
    check("2c. 没接网络的件 `nets` 为空，且归因说明「不接任何网络」",
          profile["nets"] == []
          and "不接任何网络" in str(profile["justification"].get("why")),
          f"nets={profile['nets']}")

    # **引用 model 的常量，不另写一份**：我第一版在这里写死了四个值，
    # 漏掉 `derived`，于是一个正确的件被判成"来源非法"。
    # 两个清单迟早会分叉，正确的做法是从唯一事实源取。
    from dsh_embagent.design.model import PHYSICAL_SOURCES
    check("2d. 尺寸来源原样带出（取值必须在 model.PHYSICAL_SOURCES 里）",
          profile["physical_source"] in PHYSICAL_SOURCES,
          f"{profile['physical_source']} ∈ {PHYSICAL_SOURCES}")

    exists = _profile("NO_SUCH_REF")
    check("2e. 位号不存在 → status=failed 并说明（不返回空壳）",
          exists.get("status") == "failed" and bool(exists.get("findings")),
          str(exists.get("findings"))[:80])


# ---------------------------------------------------------------------------
# 3. 缺数据说实话
# ---------------------------------------------------------------------------


def check_honest(tmp: Path) -> None:
    root = tmp / "ws"
    project = root / "p2-fixture"
    project.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "tiny-blink.json", project / "design.json")
    # **不写 claims.json / justify.json**：这就是"还没跑过"的形状

    old = os.environ.get("HWV_WORKSPACE")
    os.environ["HWV_WORKSPACE"] = str(root)
    try:
        from dsh_embagent import design_cli
        payload = design_cli.cmd_entity(["p2-fixture", "U1"])
    finally:
        if old is None:
            os.environ.pop("HWV_WORKSPACE", None)
        else:
            os.environ["HWV_WORKSPACE"] = old

    check("3a. 没有 claims.json → `claims.total = 0`（不是 0 条通过）",
          (payload.get("claims") or {}).get("total") == 0,
          f"total={(payload.get('claims') or {}).get('total')}")

    check("3b. 没有 justify.json → confidence 是 `missing`，不是 unknown/空",
          (payload.get("justification") or {}).get("confidence") == "missing",
          str((payload.get("justification") or {}).get("confidence")))

    check("3c. 这份画像的 status **不是 pass**（没数据不能算过）",
          payload.get("status") != "pass",
          f"status={payload.get('status')}")


# ---------------------------------------------------------------------------
# 4. 三态压得有理由
# ---------------------------------------------------------------------------


def check_rollup() -> None:
    fail_test = [{"result": "fail"}]
    check("4a. 实测失败压过断言通过 → fail",
          entity._rollup([{"status": "pass"}], [], fail_test) == "fail")

    check("4b. 断言 unknown 压过全过 → unknown",
          entity._rollup([{"status": "pass"}, {"status": "unknown"}], [], []) == "unknown")

    check("4c. **一条数据都没有 → not_ready**（不是 pass）",
          entity._rollup([], [], []) == "not_ready")

    check("4d. 有 pass 断言、无失败、无 unknown → pass",
          entity._rollup([{"status": "pass"}], [], []) == "pass")

    check("4e. 检查里 error 级失败 → fail",
          entity._rollup([], [{"ok": False, "severity": "error"}], []) == "fail")


# ---------------------------------------------------------------------------
# 5. 快照与前端接线
# ---------------------------------------------------------------------------


def check_contract() -> None:
    code, out = _run([sys.executable, "-m", "dsh_embagent.design_cli", "snapshot",
                      "001-blink"])
    try:
        snapshot = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        snapshot = {}
    entities = snapshot.get("entities") or {}
    check("5a. 快照里带 `entities` 索引（画布着色靠它）",
          bool(entities) and all("status" in v for v in entities.values()),
          f"{len(entities)} 件，exit={code}")

    # 索引必须**轻**：只放计数与三态，不放正文
    heavy = [ref for ref, row in entities.items()
             if any(k in row for k in ("claims_items", "items", "why"))]
    check("5b. 索引里没有正文（316 个件的正文会把快照撑爆）",
          not heavy, f"带正文的：{heavy[:3]}")

    service = (PKG / "src" / "service.ts").read_text(encoding="utf-8")
    rpc = (PKG / "src" / "client" / "rpc.ts").read_text(encoding="utf-8")
    inspector = (PKG / "src" / "client" / "Inspector.tsx").read_text(encoding="utf-8")
    store = (PKG / "src" / "client" / "store.ts").read_text(encoding="utf-8")
    view = (PKG / "src" / "client" / "HwIdeView.tsx").read_text(encoding="utf-8")

    check("6a. 宿主有 `entity` RPC，客户端 face 也有",
          "@Remote('entity')" in service and "entity: (project, ref)" in rpc,
          "service + rpc 都在")

    check("6b. Inspector 真的渲染画像（`EntitySections` 已挂上）",
          "function EntitySections" in inspector
          and "<EntitySections" in inspector,
          f"定义 + 渲染：{'<EntitySections' in inspector}")

    check("6c. 缺数据的三处说法**各不相同**（不是都用一句「无」）",
          all(marker in inspector for marker in
              ("没有归因产物", "没有断言", "不等于" )),
          "归因缺 / 断言缺各有说明")

    check("6d. 按需加载：选中才拉 `entity`，不在快照里塞正文",
          "loadEntity" in store and "face.entity(" in store
          and "entity={state.entity}" in view,
          "store.loadEntity → view 透传")


def _run(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(  # noqa: S603
        argv, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=600)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 5. 退化
# ---------------------------------------------------------------------------


def check_degradation() -> None:
    store = _store()
    design = store.load()
    claims = store.read("claims.json")
    checks = store.read("checks.json")
    before = entity.index(design, claims=claims, checks=checks)
    before_fail = sum(1 for r in before.values() if r["status"] == "fail")

    saved = entity._by_ref_findings

    def drop(ref_checks: Any) -> dict[str, list[dict[str, Any]]]:
        return {}          # 退化：检查项不归位

    try:
        entity._by_ref_findings = drop        # type: ignore[assignment]
        after = entity.index(design, claims=claims, checks=checks)
    finally:
        entity._by_ref_findings = saved       # type: ignore[assignment]

    after_fail = sum(1 for r in after.values() if r["status"] == "fail")
    check("7. 退化验证：检查项不归位 → 失败件数变少（1d 会红）",
          after_fail < before_fail,
          f"归位前 fail {before_fail} 件，归位后 {after_fail} 件")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P2 验收：按对象聚合 ===\n")
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p2-"))
    try:
        if only in ("", "index"):
            check_index()
        if only in ("", "profile"):
            check_profile()
        if only in ("", "honest"):
            check_honest(tmp)
        if only in ("", "rollup"):
            check_rollup()
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
    payload = {"status": status, "check": "P2", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())