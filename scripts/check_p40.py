"""P40 验收 —— 机械链打通：`mjcf` 命令（构建 + **真的用 MuJoCo 加载一次**）。

    python -m dsh_embagent.scripts.check_p40

## 这一段补的是什么

`design/mjcf.py` 早就能把 `design.json + joints` 拼成 MJCF，但**没有任何命令
调用它** —— 于是"机械仿真"在 CLI 和工具里根本不可达，是死代码。

P40 只做一件事：把它接出来，并且**判据落在"MuJoCo 真的编译过"**上，而不是
"我拼出了字符串"。`build()` 只保证 XML 看着对；仓库里已经有过"XML 合法、
关节其实全丢了"的教训（见 `build()` 里先拼 world 再插关节那段注释）。

## 三态判据（哪一条都不能缺）

| 场景 | 期望 |
|---|---|
| 关节齐、零件齐、MuJoCo 在位 | `ok`，且 `load.njnt >= 1` |
| 关节缺必需参数（如 hinge 没 `range_deg`）| `not_ready`，`stats.joints_skipped >= 1` |
| 没有关节表 | `not_ready`，note 说清"静几何不是可动结构" |
| MuJoCo 不在位（`HWV_NO_MUJOCO=1`）| `not_ready`（**没验证过 ≠ 通过**），且不抛异常 |

## 退化验证（脚本内做，不改文件）

把 `MjcfParts.stats` 临时改成"关节跳过数恒为 0"，用例 2 必须**不再是**
`not_ready` —— 这样才证明那条断言真的挂在闸门上，而不是恒真。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent import design_cli  # noqa: E402
from dsh_embagent.design import mjcf as mjcf_module  # noqa: E402
from dsh_embagent.design.model import Board, DesignModel, Part, Placement  # noqa: E402
from dsh_embagent.design.store import DesignStore  # noqa: E402

PROJECT = "p40-fixture"

_failures: list[str] = []
_checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if ok:
        print(f"[OK ] {name}")
    else:
        _failures.append(name)
        print(f"[FAIL] {name}")
    if detail:
        print(f"       {detail}")


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _design(*, with_joint: bool, joint_params: dict | None = None) -> DesignModel:
    """一块 40×30 的板 + 两个 10×10 的体 +（可选）一条 hinge。"""
    d = DesignModel(title="p40 fixture")
    d.board = Board(kind="generic", name="base",
                    outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                    size_mm={"w": 40, "h": 30}, thickness_mm=1.6)
    for ref, x in (("BASE1", 12.0), ("ARM1", 28.0)):
        d.add_part(Part(ref=ref, kind="structural", label=ref,
                        size_mm={"w": 10.0, "h": 10.0, "d": 2.0},
                        physical_source="estimated",
                        placement=Placement(x_mm=x, y_mm=15.0, side="top"),
                        pins=["1", "2"]))
    d.connect("N1", ("BASE1", "1"), ("ARM1", "1"))
    if with_joint:
        d.joints_list = [{
            "a": "BASE1", "b": "ARM1", "kind": "hinge",
            "source": "declared",
            "params": (joint_params if joint_params is not None
                       else {"axis": [0.0, 0.0, 1.0], "range_deg": [0.0, 180.0]}),
        }]
    return d


def _seed(design: DesignModel) -> DesignStore:
    store = DesignStore(PROJECT)
    store.save(design)
    return store


def _run(store: DesignStore, *extra: str) -> dict:
    return design_cli.cmd_mjcf([PROJECT, *extra])


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def part_describe() -> None:
    got = design_cli.cmd_mjcf([])
    check("1a. `mjcf` 无工程 id → 返回 describe（可自省）",
          got.get("status") == "ok" and "mapping" in got and "required_params" in got,
          f"required={list((got.get('required_params') or {}))[:4]}")

    shapes = (got.get("param_shapes") or {}).get("hinge") or {}
    check("1c. describe 说清 `range_deg` 的**形状**（[lo, hi]，不是一个数）",
          "[lo, hi]" in str(shapes.get("range_deg") or ""),
          f"param_shapes.hinge={shapes}")

    collision = design_cli.COMMANDS.get("mjcf")
    check("1b. COMMANDS 里注册了 `mjcf`，且没有覆盖别的命令",
          collision is design_cli.cmd_mjcf
          and len([k for k in design_cli.COMMANDS if k == "mjcf"]) == 1,
          f"cmd={getattr(collision, '__name__', collision)}")


def part_ok_path() -> None:
    store = _seed(_design(with_joint=True))
    try:
        got = _run(store, "--build")
        load = got.get("load") or {}
        check("2a. 关节齐全 → `ok`",
              got.get("status") == "ok",
              f"status={got.get('status')} notes={got.get('notes')}")
        check("2b. MuJoCo **真的编译过**，且至少 1 个关节在模型里",
              load.get("status") == "ok" and int(load.get("njnt") or 0) >= 1,
              f"load={load}")
        model = store.dir / "model.xml"
        written = len(model.read_text(encoding="utf-8")) if model.is_file() else 0
        check("2c. `--build` 落盘 model.xml，且内容与返回值一致",
              model.is_file() and written == int(
                  (got.get("xml") or {}).get("chars") or 0),
              f"chars={written}")
    finally:
        store.delete()


def part_missing_param() -> None:
    store = _seed(_design(with_joint=True, joint_params={"axis": [0.0, 0.0, 1.0]}))
    try:
        got = _run(store)
        stats = got.get("stats") or {}
        check("3a. hinge 缺 `range_deg` → `not_ready`（不编默认值）",
              got.get("status") == "not_ready",
              f"status={got.get('status')} notes={got.get('notes')}")
        check("3b. 被跳过的是**关节**，不是零件（数字对得上种类）",
              int(stats.get("joints_skipped") or 0) >= 1
              and int(stats.get("parts_skipped") or 0) == 0,
              f"parts_skipped={stats.get('parts_skipped')} "
              f"joints_skipped={stats.get('joints_skipped')}")
        reasons = stats.get("skipped_reasons") or []
        check("3c. 跳过原因里点名了缺哪个参数",
              any("range_deg" in str(item.get("why") or "") for item in reasons),
              str(reasons)[:160])
    finally:
        store.delete()


def part_scalar_range() -> None:
    """`range_deg: 180`（标量）**不猜语义**，跳过并说清。"""
    store = _seed(_design(with_joint=True,
                          joint_params={"axis": [0.0, 0.0, 1.0], "range_deg": 180.0}))
    try:
        got = _run(store)
        stats = got.get("stats") or {}
        reasons = " ".join(str(item.get("why") or "")
                           for item in (stats.get("skipped_reasons") or []))
        check("3d. 标量 range_deg → `not_ready`（±180 还是 0..180 有歧义，不猜）",
              got.get("status") == "not_ready" and "歧义" in reasons,
              f"status={got.get('status')} reasons={reasons[:120]}")
        check("3e. 标量不再抛 `TypeError`（P40 修掉的那个崩溃）",
              not any("TypeError" in str(x) for x in (got.get("findings") or [])),
              f"findings={got.get('findings')}")
    finally:
        store.delete()


def part_no_joints() -> None:
    store = _seed(_design(with_joint=False))
    try:
        got = _run(store)
        notes = " ".join(got.get("notes") or [])
        check("4a. 没有关节表 → `not_ready`，并说清是「静几何不是可动结构」",
              got.get("status") == "not_ready" and "没有关节表" in notes,
              f"notes={got.get('notes')}")
    finally:
        store.delete()


def part_mujoco_missing() -> None:
    old = os.environ.get(mjcf_module.__dict__.get("NO_MUJOCO_ENV", "HWV_NO_MUJOCO"))
    os.environ["HWV_NO_MUJOCO"] = "1"
    store = _seed(_design(with_joint=True))
    try:
        got = _run(store)
        load = got.get("load") or {}
        check("5a. MuJoCo 不在位 → `not_ready`（没验证过 ≠ 通过）",
              got.get("status") == "not_ready" and load.get("status") == "not_ready",
              f"status={got.get('status')} load={load}")
        check("5b. 这条路径**不抛异常**，且给出装在哪",
              "vendor" in str(load.get("reason") or "")
              or "mujoco" in str(load.get("reason") or "").lower(),
              str(load.get("reason"))[:140])
    finally:
        store.delete()
        if old is None:
            os.environ.pop("HWV_NO_MUJOCO", None)
        else:
            os.environ["HWV_NO_MUJOCO"] = old


def part_degeneration() -> None:
    """把「关节跳过」这道闸门拆掉，用例 3a 必须不再是 not_ready。"""
    original = mjcf_module.MjcfParts.stats

    def no_joint_skips(self):  # noqa: ANN001
        out = original(self)
        out["joints_skipped"] = 0
        return out

    store = _seed(_design(with_joint=True, joint_params={"axis": [0.0, 0.0, 1.0]}))
    try:
        baseline = _run(store).get("status")
        mjcf_module.MjcfParts.stats = no_joint_skips  # type: ignore[assignment]
        try:
            degenerated = _run(store).get("status")
        finally:
            mjcf_module.MjcfParts.stats = original  # type: ignore[assignment]
        check("6. 退化验证：拆掉「关节跳过」闸门 → 3a 必须变绿（证明断言挂在闸门上）",
              baseline == "not_ready" and degenerated != "not_ready",
              f"baseline={baseline} degenerated={degenerated}")
    finally:
        store.delete()


def main() -> int:
    print("=== P40 机械链打通（mjcf 命令） ===")
    part_describe()
    part_ok_path()
    part_missing_param()
    part_scalar_range()
    part_no_joints()
    part_mujoco_missing()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(f"  [FAIL] {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())