"""P51 验收 —— 软件能力的**任务分解**（写 → 测 → 验）。

    python -m hw_velxio.scripts.check_p51

## 这一段解决什么

P50 之前，软件能力（S1–S6）在 `plan` 里只有 **2 条任务**：
"测一个数" + "一条验收"。中间那一大段"**写**"完全不出现，
于是 12 条软件任务看着像把软件干完了。

P51 把每个软件能力拆成一条链（`SW_BREAKDOWN`）：

```
S1 风控  spec(设计判据) → impl(写状态机) → inject(注入测试)
        → threshold(阈值来源) → latency(测延迟) → V(验收)
```

## 判据

| 项 | 期望 |
|---|---|
| 层数 | 每个软件能力 **≥ 3** 条子任务（原来 2 条）|
| 第一层 | 必须是**写**（`design` / `code`），不能是"测" |
| 依赖 | 链式：每条依赖前一条（排序真的存在，不是并列）|
| 验收 | **每条子任务都有 `verify`**；能力级 `-V` 依赖**链尾** |
| 产物 | 带 `artifact` 的任务有 `evidence.present`；**文件在 ≠ 通过** |
| 退化 | 清空 `SW_BREAKDOWN` → 层数掉回 2（证明分解挂在这张表上）|
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import plan as plan_module  # noqa: E402
from hw_velxio.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from hw_velxio.design.store import DesignStore  # noqa: E402

PROJECT = "p51-fixture"
SOFTWARE = ("S1", "S2", "S3", "S4", "S5", "S6")

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


def _design() -> DesignModel:
    design = DesignModel(id=PROJECT, board=Board(
        kind="custom", name="b", id="PCB#main",
        outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
        size_mm={"w": 40, "h": 30}))
    design.add_part(Part(
        ref="U1", kind="stm32f103c8t6", label="MCU", lcsc="C8734",
        role="module", size_mm={"w": 7.0, "h": 7.0, "d": 1.6},
        physical_source="datasheet", pins=[str(i) for i in range(1, 49)],
        placement=Placement(x_mm=10.0, y_mm=10.0, board_id="PCB#main")))
    design.nets = [{"net": "3V3", "pins": [{"ref": "U1", "pin": "24"}]},
                   {"net": "GND", "pins": [{"ref": "U1", "pin": "23"}]}]
    return design


def _payload(store: DesignStore) -> dict:
    return plan_module.derive(store, _design(), {})


# ---------------------------------------------------------------------------
# 1. 层数与顺序
# ---------------------------------------------------------------------------


def part_depth() -> None:
    store = DesignStore(PROJECT)
    store.delete()
    try:
        payload = _payload(store)
        tasks = payload["tasks"]
        by_cap: dict[str, list[dict]] = {}
        for task in tasks:
            by_cap.setdefault(task["capability"], []).append(task)

        counts = {cap: len(by_cap.get(cap) or []) for cap in SOFTWARE}
        check("1a. 每个软件能力 **≥3** 条任务（原来只有 2 条：测 + 验）",
              all(n >= 3 for n in counts.values()), str(counts))
        sw = payload["counts"]["software_tasks"]
        hw = payload["counts"]["hardware_tasks"]
        check("1b. 平均每个软件能力 ≥3 条、每个硬件能力 ≤2.5 条",
              sw / 6 >= 3 and hw / 8 <= 2.5,
              f"sw={sw}（{sw / 6:.1f}/能力） hw={hw}（{hw / 8:.1f}/能力）")

        # 第一层必须是"写"（design/code），不能是"测"
        first = {}
        for cap in SOFTWARE:
            chain = [t for t in (by_cap.get(cap) or [])
                     if t["id"] != f"T-{cap}-V" and t["id"] != f"T-{cap}-P"
                     and t["id"] != f"T-{cap}-M"]
            first[cap] = chain[0]["kind"] if chain else ""
        check("1c. 第一层是「写」（design/code），不是「测」",
              all(k in ("design", "code") for k in first.values()), str(first))
        check("1d. 链里同时有「写」和「测」（code/test 各至少一条）",
              all({t["kind"] for t in (by_cap.get(cap) or [])}
                  & {"code", "test"}
                  for cap in ("S1", "S2", "S6")),
              str({cap: sorted({t["kind"] for t in by_cap.get(cap) or []})
                   for cap in ("S1", "S2", "S6")}))
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 2. 依赖链与验收
# ---------------------------------------------------------------------------


def part_chain() -> None:
    store = DesignStore(PROJECT)
    store.delete()
    try:
        payload = _payload(store)
        tasks = {t["id"]: t for t in payload["tasks"]}

        # S1 的链：spec → impl → inject → threshold → latency
        keys = ["spec", "impl", "inject", "threshold", "latency"]
        ids = [f"T-S1-{k}" for k in keys]
        edges = [(ids[i], ids[i + 1]) for i in range(len(ids) - 1)]
        # 每条子任务都挂了**能力级前置**（S1 依赖 H2/H8 → 那两条验收）。
        # 所以判"链式"要先把能力级前置剥掉，再看剩下的**恰好是前一条**。
        upstream = {"T-H2-V", "T-H8-V"}
        tail = [set(tasks[ids[i + 1]]["depends_on"]) - upstream
                for i in range(1, len(ids) - 1)]
        head_has_prev = "T-S1-M" in tasks[ids[0]]["depends_on"]
        check("2a. 剥掉能力级前置后，链式依赖**恰好是前一条**（不是并列）",
              all(t == {ids[i + 1]} for i, t in enumerate(tail))
              and head_has_prev,
              f"head={tasks[ids[0]]['depends_on']} tail={tail}")
        check("2b. 能力级 `-V` 依赖「链尾」（不是和「写」同时可做）",
              tasks["T-S1-V"]["depends_on"]
              and tasks["T-S1-V"]["depends_on"][-1] == "T-S1-latency",
              str(tasks["T-S1-V"]["depends_on"]))
        check("2c. **每条子任务都有 verify**（没有验收方式的不生成）",
              all(str(t.get("verify") or "") for t in payload["tasks"]))
        check("2d. 任务 id 唯一",
              len(tasks) == len(payload["tasks"]))
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 3. 产物证据（存在 ≠ 通过）
# ---------------------------------------------------------------------------


def part_artifact() -> None:
    store = DesignStore(PROJECT)
    store.delete()
    try:
        before = _payload(store)
        spec = next(t for t in before["tasks"] if t["id"] == "T-S1-spec")
        check("3a. 带 `artifact` 的任务有 `evidence.present`",
              spec.get("artifact") == "fw/failsafe/failsafe.h"
              and spec["evidence"]["present"] is False,
              str(spec.get("evidence")))
        check("3b. 文件不存在时 `present=False`（不是默认 True）",
              before["counts"]["with_artifact"] > 0
              and before["counts"]["artifact_present"] == 0,
              f"with={before['counts']['with_artifact']} "
              f"present={before['counts']['artifact_present']}")

        # 造一个**空文件**：present 必须变 True，但 status 仍是 open
        target = store.path("fw/failsafe/failsafe.h")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("/* empty stub */\n", encoding="utf-8")
        after = _payload(store)
        spec2 = next(t for t in after["tasks"] if t["id"] == "T-S1-spec")
        check("3c. 文件在 → `present=True`，但任务状态仍是 open"
              "（存在 ≠ 通过，§0.1）",
              spec2["evidence"]["present"] is True
              and spec2["status"] == "open",
              f"present={spec2['evidence']['present']} status={spec2['status']}")
        check("3d. counts 分开报：有产物的任务数 / 产物已存在的任务数",
              after["counts"]["with_artifact"] == before["counts"]["with_artifact"]
              and after["counts"]["artifact_present"] >= 1,
              str({k: after["counts"][k] for k in
                   ("with_artifact", "artifact_present")}))
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 4. 退化：分解挂在 `SW_BREAKDOWN` 上
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    store = DesignStore(PROJECT)
    store.delete()
    try:
        original = dict(plan_module.SW_BREAKDOWN)
        try:
            plan_module.SW_BREAKDOWN = {}
            flat = _payload(store)
        finally:
            plan_module.SW_BREAKDOWN = original  # type: ignore[assignment]
        deep = _payload(store)
        flat_s1 = [t for t in flat["tasks"] if t["capability"] == "S1"]
        deep_s1 = [t for t in deep["tasks"] if t["capability"] == "S1"]
        check("4. 退化：清空 `SW_BREAKDOWN` → S1 从 7 条掉回 2 条"
              "（证明分解真的挂在那张表上）",
              len(deep_s1) >= 5 and len(flat_s1) <= 2,
              f"deep={len(deep_s1)} flat={len(flat_s1)}")
        check("4b. 退化后**没有 artifact 字段**（分解才带产物）",
              not any(t.get("artifact") for t in flat["tasks"]))
    finally:
        store.delete()


def main() -> int:
    print("=== P51 软件任务分解（写 → 测 → 验）===")
    part_depth()
    part_chain()
    part_artifact()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())