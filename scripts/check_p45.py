"""P45 验收 —— 制造可行性：DRC 分级 + DFM 三态。

    python -m hw_velxio.scripts.check_p45

## 这一段补的是什么

立创的 DFM 有一个**和直觉相反**的契约（API 文档原文）：

> 仅私有化构建可用
> 结果可能不完整：未配置检查规则或存在检查失败项时，将以 `INCOMPLETE_RESULT`
> 错误抛出，**不得视为检查通过**

也就是说：**它是抛错，不是返回一个失败列表**。所以三态必须是

* `getDfmAvailability()` 说不可用 → `not_ready`
* 抛 `INCOMPLETE_RESULT` → **`unknown`**（跑了但结果不完整，绝不等于 ok）
* 正常返回 → `ok`

再加上 DRC 的分级（`clearance/netlist/physical` 可信，`connection` 不可信），
合成一条"能不能打板"的制造结论。

## 判据

| 项 | 期望 |
|---|---|
| DFM 不可用 | `not_ready` |
| 抛 `INCOMPLETE_RESULT` | `unknown`，且原话写出来 |
| `failedItems>0`（防御分支）| `unknown`（不能因为"没抛"就变 ok）|
| `DFM_NOT_CONFIGURED` | `not_ready`（没查 ≠ 通过）|
| DRC 可信违规 > 0 | 合成结论 `failed` |
| 只有 `connection` 违规 | 合成结论 **`ok`**，但带一句说明 |
| 退化：清空 `TRUSTWORTHY_DRC` | "可信违规 > 0 → failed" 必须变红 |
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio import design_cli  # noqa: E402
from hw_velxio.design import dfm as dfm_module  # noqa: E402
from hw_velxio.design.store import DesignStore  # noqa: E402

PROJECT = "p45-fixture"

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


# ---------------------------------------------------------------------------
# 1. DFM 的四种输入 → 三态
# ---------------------------------------------------------------------------


def part_dfm_states() -> None:
    unavailable = dfm_module.dfm_verdict(None, available=False)
    check("1a. `getDfmAvailability()` 说不可用 → `not_ready`（不是 ok）",
          unavailable["status"] == "not_ready"
          and "私有化" in str(unavailable.get("reason")),
          f"{unavailable['status']}：{unavailable.get('reason')}")

    incomplete = dfm_module.dfm_verdict(
        None, error="Error: INCOMPLETE_RESULT: 部分规则未执行")
    check("1b. 抛 `INCOMPLETE_RESULT` → **`unknown`**（文档：不得视为通过）",
          incomplete["status"] == "unknown"
          and "不完整" in str(incomplete.get("reason")),
          f"{incomplete['status']}：{incomplete.get('reason')}")

    failed_items = dfm_module.dfm_verdict({"failedItems": 2, "issues": []})
    check("1c. `failedItems>0`（防御分支）→ `unknown`（不能因为没抛就变 ok）",
          failed_items["status"] == "unknown",
          f"{failed_items['status']}：{failed_items.get('reason')}")

    not_configured = dfm_module.dfm_verdict(
        {"failedItems": 0, "issues": [], "warnings": ["DFM_NOT_CONFIGURED"]})
    check("1d. `DFM_NOT_CONFIGURED` → `not_ready`（没查 ≠ 通过）",
          not_configured["status"] == "not_ready",
          f"{not_configured['status']}：{not_configured.get('reason')}")

    clean = dfm_module.dfm_verdict({"failedItems": 0, "issues": [{"x": 1}]})
    check("1e. 正常返回 → `ok`，且带出问题条数",
          clean["status"] == "ok" and len(clean.get("issues") or []) == 1,
          f"{clean['status']}：{clean.get('reason')}")

    # **本机实测**（4.1.97，非私有化构建）：`checkDfm` 抛的就是这句。
    observed = dfm_module.dfm_verdict(
        None, error="DFM 仅在私有化构建可用")
    check("1g. 真机实测的错误文本（「仅在私有化构建可用」）→ `not_ready`"
          "（没这能力 ≠ 结果不完整）",
          observed["status"] == "not_ready",
          f"{observed['status']}：{observed.get('reason')}")

    dict_avail = dfm_module.dfm_verdict(None, available={"available": False})
    check("1h. 可用性返回对象 `{available:false}` 也要认成 `not_ready`",
          dict_avail["status"] == "not_ready", f"{dict_avail['status']}")

    missing = dfm_module.dfm_verdict(None)
    check("1f. 根本没跑（没有结果也没错误）→ `not_ready`",
          missing["status"] == "not_ready", f"{missing['status']}")


# ---------------------------------------------------------------------------
# 2. 三路证据合成
# ---------------------------------------------------------------------------


def _drc(**categories: int) -> dict:
    base = {"clearance": 0, "connection": 0, "netlist": 0, "physical": 0,
            "other": 0}
    base.update(categories)
    return {"status": "ok", "categories": base, "boards": []}


def part_merge() -> None:
    ok_dfm = {"status": "ok", "issues": []}
    clean = dfm_module.merge(_drc(), ok_dfm)
    check("2a. 可信零违规 + DFM ok → 合成 `ok`",
          clean["status"] == "ok",
          f"{clean['status']} note={clean['note']}")

    bad = dfm_module.merge(_drc(clearance=2), ok_dfm)
    check("2b. DRC 可信违规 > 0 → 合成 `failed`（打板必炸）",
          bad["status"] == "failed" and bad["trustworthy_violations"] == 2,
          f"{bad['status']} trustworthy={bad['trustworthy_violations']}")

    conn = dfm_module.merge(_drc(connection=4), ok_dfm)
    check("2c. **只有** `connection` 违规 → 合成仍是 `ok`，但带说明",
          conn["status"] == "ok" and "过孔" in str(conn.get("connection_note")),
          f"{conn['status']} note={conn.get('connection_note')}")

    unknown_dfm = dfm_module.merge(
        _drc(), {"status": "unknown", "reason": "结果不完整"})
    check("2d. DFM 不完整 → 合成 `not_ready`（不能拿 DRC 干净就当可打板）",
          unknown_dfm["status"] == "not_ready"
          and any("DFM" in r for r in unknown_dfm["reasons"]),
          f"{unknown_dfm['status']} reasons={unknown_dfm['reasons']}")

    geo = dfm_module.merge(_drc(), ok_dfm, geometry={"violations": 1})
    check("2e. 自建几何违规也计入可信违规",
          geo["status"] == "failed" and geo["trustworthy_violations"] == 1,
          f"{geo['status']} trustworthy={geo['trustworthy_violations']}")


# ---------------------------------------------------------------------------
# 3. 命令层：对着 emit.json 重判
# ---------------------------------------------------------------------------


def part_command() -> None:
    store = DesignStore(PROJECT)
    store.save(type(DesignStore("x").load())() if False else _minimal_design())
    try:
        missing = design_cli.cmd_dfm([PROJECT])
        check("3a. 没有 emit.json → `not_ready` 并说清先跑 emit",
              missing["status"] == "not_ready"
              and any("emit.json" in f for f in (missing.get("findings") or [])),
              f"{missing.get('findings')}")

        store.write("emit.json", {
            "drc": {**_drc(clearance=1),
                    "boards": [{"board": "Board1"}],
                    "dfm": [{"status": "unknown", "reason": "INCOMPLETE_RESULT"}]},
        })
        got = design_cli.cmd_dfm([PROJECT])
        check("3b. 有 emit.json → 重判出 `failed`（可信违规），并报 DFM 不完整",
              got["status"] == "failed"
              and (got.get("dfm") or {}).get("status") == "unknown"
              and (got.get("checked") or {}).get("drc_boards") == 1,
              f"status={got['status']} checked={got.get('checked')}")

        store.write("emit.json", {
            "drc": {**_drc(connection=3),
                    "boards": [{"board": "Board1"}],
                    "dfm": [{"status": "ok", "issues": []}]},
        })
        got2 = design_cli.cmd_dfm([PROJECT])
        check("3c. 只 3 条 connection → 重判 `ok`（不误判成打不了）",
              got2["status"] == "ok"
              and got2["connection_violations"] == 3,
              f"status={got2['status']} conn={got2.get('connection_violations')}")
    finally:
        store.delete()


def _minimal_design():
    from hw_velxio.design.model import Board, DesignModel

    return DesignModel(
        id=PROJECT,
        board=Board(kind="generic", name="b",
                    outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                    size_mm={"w": 40, "h": 30}, thickness_mm=1.6))


# ---------------------------------------------------------------------------
# 4. 退化
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    """清空 `TRUSTWORTHY_DRC` → 2b 必须变红（可信类别是表驱动的）。"""
    drc = _drc(clearance=2)
    ok_dfm = {"status": "ok", "issues": []}
    baseline = dfm_module.merge(drc, ok_dfm)["status"]
    original = dfm_module.TRUSTWORTHY_DRC
    dfm_module.TRUSTWORTHY_DRC = ()  # type: ignore[assignment]
    try:
        degenerated = dfm_module.merge(drc, ok_dfm)["status"]
    finally:
        dfm_module.TRUSTWORTHY_DRC = original  # type: ignore[assignment]
    check("4. 退化验证：清空可信类别表 → 2b 变绿（证明分级是表驱动、不是硬编码）",
          baseline == "failed" and degenerated == "ok",
          f"baseline={baseline} degenerated={degenerated}")


def main() -> int:
    print("=== P45 制造可行性（DFM + DRC 分级）===")
    part_dfm_states()
    part_merge()
    part_command()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())