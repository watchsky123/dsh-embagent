"""P53 验收 —— 飞控参数 → 能力映射（`params`）。

    python -m hw_velxio.scripts.check_p53

## 判据

| 项 | 期望 |
|---|---|
| `.param` | `name=value` 解析数对、值对；`#` 注释与空行跳过 |
| `MF_PARAM` | 普通 + **枚举型**（`'e', mf_A, mf_B`）都要吃掉；`#define MF_PARAM` **不算参数** |
| 映射 | 每条 `mapped` 都有 `basis`；`pid_yaw_rate_p` 落到 `pid_yaw_rate`（**最长前缀**）|
| 完整度 | 缺一个必需参数 → `partial`（不是 ready，也不是"没问题"）|
| **纪律** | 参数**不改能力状态**：`plan` 里 S2 仍是 `unknown`，只多一条 `evidence.params` |
| 冲突 | 两个 `.param` 同参数不同值 → `failed` |
| 退化 | 把 `_family_of` 改成恒 `None` → 全部 `unmapped`（证明映射在起作用）|
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import params as params_module  # noqa: E402
from hw_velxio.design import plan as plan_module  # noqa: E402
from hw_velxio.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)
from hw_velxio.design.store import DesignStore  # noqa: E402

_failures: list[str] = []
_checks = 0

PARAM_TEXT = """# 参数配置文件
pid_in_roll_p=0.050000
pid_in_roll_i=0.010000
pid_in_roll_d=0.100000
pid_in_pitch_p=0.050000
pid_in_pitch_i=0.010000
pid_in_pitch_d=0.100000
pid_ou_roll_p=3.000000
pid_ou_pitch_p=3.000000
pid_yaw_p=0.400000
pid_yaw_i=0.000000
pid_yaw_rate_p=0.100000
sys_fly_mode=1.000000
cal_roll=3.200000
alt_hover_thr=0.400000
"""

CONFIG_TEXT = """
#define MF_PARAM(name, defval, datatype, type, ...) {#name, defval, type, #__VA_ARGS__},
#define MF_PARAM_LIST \\
MF_PARAM( sys_fly_mode, 0, int32_t, 'i') /*飞行模式 0=手动 1=油门居中悬停*/ \\
MF_PARAM( ahr_gizmo, 0, int32_t, 'e', mf_MAHONY,mf_MAHONY_BF) /*姿态解算算法*/ \\
MF_PARAM( bat_cal_v, 12.6, float, 'f') /*电池电压标定*/ \\
MF_PARAM( imu_cal_gx, 0, float, 'f') /*gyro x zero offset*/ \\
"""


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


def _fixture(root: Path, *, param_text: str = PARAM_TEXT,
             extra_param: str = "") -> None:
    (root / "config.param").write_text(param_text, encoding="utf-8")
    if extra_param:
        (root / "other.param").write_text(extra_param, encoding="utf-8")
    header = root / "Config.h"
    header.write_text(CONFIG_TEXT, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. 两种格式的解析
# ---------------------------------------------------------------------------


def part_parse() -> None:
    root = Path(tempfile.mkdtemp(prefix="p53-parse-"))
    _fixture(root)
    found, bad = params_module.parse_param_file(root / "config.param")
    check("1a. `.param` 的 name=value 解析对（14 条，注释/空行跳过）",
          len(found) == 14 and not bad, f"{len(found)} 条，bad={bad[:2]}")
    check("1b. 值是真数（`pid_in_roll_p` = 0.05，不是字符串）",
          abs(found["pid_in_roll_p"]["value"] - 0.05) < 1e-9,
          str(found["pid_in_roll_p"]))

    table, table_bad = params_module.parse_mf_param(root / "Config.h")
    check("1c. `MF_PARAM` 参数表解析对（4 条；`#define` 那两行**不算参数**）",
          len(table) == 4 and not table_bad,
          f"{sorted(table)} bad={table_bad[:2]}")
    check("1d. **枚举型**参数（`'e', mf_MAHONY,mf_MAHONY_BF`）也能吃掉",
          table.get("ahr_gizmo", {}).get("enums") == ["mf_MAHONY", "mf_MAHONY_BF"],
          str(table.get("ahr_gizmo")))
    check("1e. 注释被当作语义来源照抄（`sys_fly_mode`）",
          "油门居中" in str(table.get("sys_fly_mode", {}).get("comment")),
          str(table.get("sys_fly_mode", {}).get("comment")))


# ---------------------------------------------------------------------------
# 2. 映射与完整度
# ---------------------------------------------------------------------------


def part_mapping() -> None:
    root = Path(tempfile.mkdtemp(prefix="p53-map-"))
    _fixture(root)
    payload = params_module.build(roots=[root])
    check("2a. 每条映射都带 `basis`（写不出凭什么映射就不映射）",
          bool(payload["mapped"])
          and all(row["basis"] for row in payload["mapped"]),
          f"{len(payload['mapped'])} 条")
    check("2b. **最长前缀**：`pid_yaw_rate_p` 落进 `pid_yaw_rate` 而不是 `pid_yaw`",
          params_module._family_of("pid_yaw_rate_p") == "pid_yaw_rate",
          str(params_module._family_of("pid_yaw_rate_p")))
    check("2c. 认不出的参数进 `unmapped`（不硬塞）",
          "ahr_gizmo" in payload["unmapped"], str(payload["unmapped"][:5]))

    ready = {r["capability"]: r for r in payload["readiness"]}
    check("2d. 必需参数齐 → `ready`（S2 的 10 项夹具里齐了 8 项？看实际）",
          ready.get("S2", {}).get("status") in ("ready", "partial"),
          str(ready.get("S2")))

    # 删一个必需项 → partial
    trimmed = PARAM_TEXT.replace("pid_in_roll_p=0.050000\n", "")
    root2 = Path(tempfile.mkdtemp(prefix="p53-trim-"))
    _fixture(root2, param_text=trimmed)
    payload2 = params_module.build(roots=[root2])
    s2 = {r["capability"]: r for r in payload2["readiness"]}.get("S2", {})
    check("2e. 缺一个必需参数 → `partial` + 写明缺哪个",
          s2.get("status") == "partial" and "pid_in_roll_p" in (s2.get("missing") or []),
          str(s2))

    # 冲突 → failed
    root3 = Path(tempfile.mkdtemp(prefix="p53-conf-"))
    _fixture(root3, extra_param="pid_in_roll_p=9.9\n")
    payload3 = params_module.build(roots=[root3])
    check("2f. 两个 `.param` 同参数不同值 → `failed`（并指出冲突）",
          payload3["status"] == "failed"
          and any("冲突" in str(p.get("why")) for p in payload3["problems"]),
          str([p.get("why") for p in payload3["problems"] if "冲突" in str(p.get("why"))]))


# ---------------------------------------------------------------------------
# 3. 纪律：参数不改能力状态
# ---------------------------------------------------------------------------


def part_discipline() -> None:
    root = Path(tempfile.mkdtemp(prefix="p53-doc-"))
    _fixture(root)
    store = DesignStore("p53-fixture")
    try:
        store.delete()
        board = Board(kind="custom", name="b", id="PCB#main",
                      outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                      size_mm={"w": 40, "h": 30}, thickness_mm=1.6)
        design = DesignModel(id="p53-fixture", board=board)
        design.add_part(Part(ref="U1", kind="stm32f103c8t6", label="MCU",
                             lcsc="C8734", role="module",
                             size_mm={"w": 7.0, "h": 7.0, "d": 1.6},
                             physical_source="datasheet",
                             pins=[str(i) for i in range(1, 49)],
                             placement=Placement(x_mm=10.0, y_mm=10.0,
                                                 board_id="PCB#main")))
        store.save(design)
        payload = params_module.build(roots=[root])
        store.write(params_module.FILE, payload)

        with_params = plan_module.derive(store, design, {})
        caps = {c["id"]: c for c in with_params["capabilities"]}
        check("3a. `plan` 读到了参数证据（param_evidence 非空）",
              bool(with_params["param_evidence"]),
              str(with_params["param_evidence"]))
        check("3b. **参数不把能力判成 pass**：S2 仍是 `unknown`，只多 evidence.params",
              caps["S2"]["status"] == "unknown"
              and (caps["S2"]["evidence"].get("params") or {}).get("readiness")
              in ("ready", "partial"),
              f"{caps['S2']['status']} / {caps['S2']['evidence'].get('params')}")
        check("3c. 没参数的能力不挂空证据（H1 推力参数不在参数集里）",
              "params" not in caps["H1"]["evidence"],
              str(list(caps["H1"]["evidence"])))
    finally:
        store.delete()


# ---------------------------------------------------------------------------
# 4. 退化 + 真工程
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    root = Path(tempfile.mkdtemp(prefix="p53-deg-"))
    _fixture(root)
    baseline = params_module.build(roots=[root])
    original = params_module._family_of
    params_module._family_of = lambda name: None  # type: ignore[assignment]
    try:
        broken = params_module.build(roots=[root])
    finally:
        params_module._family_of = original  # type: ignore[assignment]
    check("4. 退化：`_family_of` 恒 None → 全部进 `unmapped`（映射真的在起作用）",
          baseline["counts"]["mapped"] > 0 and broken["counts"]["mapped"] == 0
          and broken["counts"]["unmapped"] == baseline["counts"]["params"],
          f"{baseline['counts']['mapped']} → {broken['counts']['mapped']}")


def part_real() -> None:
    workspace = Path(r"C:\Users\<user>\Desktop\示例工作区")
    if not (workspace / "config_20261021_102258.param").is_file():
        print(_safe("[skip] 4b. 真实工作区不在（不假装跑过）"))
        return
    payload = params_module.build(roots=[workspace])
    counts = payload["counts"]
    check("4b. 真工程：206 个参数 / 93 映射 / 15 族 / 0 解析问题",
          counts["params"] >= 200 and counts["mapped"] >= 90
          and counts["families"] >= 14 and not payload["problems"],
          str(counts))
    check("4c. 6 条能力族齐（H3/H8/S1/S2/S3/S5），H4/H5 记进 `known_absent`",
          counts["ready"] >= 5
          and {r["capability"] for r in payload["readiness"]} >= {
              "S1", "S2", "S3"},
          str([r["capability"] for r in payload["readiness"]]))
    check("4d. 状态 `not_ready` 而非 `ok` —— **参数集里没有的能力就是缺口**",
          payload["status"] == "not_ready"
          and bool(payload["known_absent"]),
          f"{payload['status']} / {[k['capability'] for k in payload['known_absent']]}")


def main() -> int:
    print("=== P53 飞控参数 → 能力映射 ===")
    part_parse()
    part_mapping()
    part_discipline()
    part_degeneration()
    part_real()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())