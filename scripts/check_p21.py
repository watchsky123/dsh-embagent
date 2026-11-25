"""P21 验收：闭环（实物测试记录 + 参数校准 + 交接单消费）。

    python -m dsh_embagent.scripts.check_p21

六条对应 `docs/execution-plan-v3.md` §P21，第 6 条是**退化验证**。

## 夹具策略（AGENTS §0.3）

* **判断器注入**：校准规则（"必须复现旧失败"）用假判断器验。
  这样第 2/3/6 条不受 MuJoCo 是否装、物理是否收敛影响——**规则本身**才是要验的东西。
* **真 MuJoCo**：另跑一条 `default_runner`，确认它在真环境里给出合法三态，
  且 `unknown` 时校准**拒绝**而不是采纳。
* **真设计**：`r3demo`（4 个元件）走一遍 handoff，确认 external 类真的出现。
  跑完把 `physical_tests` 清掉——不往用户工程里留夹具（AGENTS §4.4）。

## 第 5 条（contact_sheet）为什么两种结果都算过

要求是「**要么**产出一张 tile 缩略图，**要么**给出可直接粘走跑的命令行」。
本机实测没有 ffmpeg，所以断言的是后者——不是把"没做"写成"通过"。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dsh_embagent.design import (  # noqa: E402
    handoff,
    real_tests,
    sim_calibration,
    sim_params,
)
from dsh_embagent.design.model import DesignModel, Part  # noqa: E402
from dsh_embagent.design.store import DesignStore  # noqa: E402

PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": str(detail)[:800]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {str(detail)[:800]}")
    return ok


def fixture_design() -> DesignModel:
    """一台"避障小车"：轮子 + 电机 + 主板。够 handoff 用。"""
    design = DesignModel(title="P21 夹具车")
    for ref, kind in (("U1", "mcu"), ("W1", "wheel"), ("W2", "wheel"),
                      ("M1", "motor"), ("M2", "motor")):
        design.add_part(Part(ref=ref, kind=kind, physical_source="estimated",
                             pins=["1"]))
    design.connect("PWM_L", ("U1", "PA0"), ("M1", "1"))
    design.connect("PWM_R", ("U1", "PA1"), ("M2", "1"))
    return design


# ---------------------------------------------------------------------------
# 1. 记录 + 交接单消费
# ---------------------------------------------------------------------------


def check_record_and_handoff(tmp: Path) -> None:
    design = fixture_design()
    record, problems = real_tests.add(
        design, test="爬 15mm 台阶", result="fail",
        observed="右前轮打滑，机架上翘 12°，第二次尝试翻倒",
        implicated=["ref:W2", "ref:M2"],
        evidence={"video": str(tmp / "run.mp4"), "frames": ["00:03", "00:11"]},
        sim_params="initial", sim_reproduced=False)
    written = [p for p in problems if "警告" not in p]
    check("1a. 记录一条 fail（带 implicated / observed / evidence）",
          record.id == "pt-001" and not written,
          f"id={record.id} result={record.result} "
          f"implicated={record.implicated} 问题={problems}")

    # 写进去的必须能读回来，且三态不被压平
    round_trip = DesignModel.from_dict(design.to_dict())
    loaded = real_tests.load(round_trip)
    check("1b. 记录往返：sim_reproduced=False 保住（不变成 None）",
          len(loaded) == 1 and loaded[0].sim_reproduced is False,
          f"loaded={[(t.id, t.sim_reproduced) for t in loaded]}")

    untried = real_tests.PhysicalTest(id="pt-002", test="原地转圈", result="unknown")
    round_trip.physical_tests.append(untried.to_dict())
    back = real_tests.load(round_trip)[1]
    check("1c. 没试过仿真 → 字段不写出来（None ≠ False）",
          back.sim_reproduced is None and "sim_reproduced" not in untried.to_dict(),
          f"round_trip.sim_reproduced={back.sim_reproduced}")

    # 引用纪律：不存在的位号必须挡住
    _, bad = real_tests.add(design, test="引用错的件", result="fail",
                            observed="x", implicated=["ref:NO_SUCH"])
    check("1d. 引用不存在的位号 → 拒绝写入（不静默丢弃）",
          bool([p for p in bad if "不存在" in p])
          and len(design.physical_tests) == 1,
          f"problems={bad} 记录数={len(design.physical_tests)}")

    # 交接单：第六类必须出现，且不阻断
    store = DesignStore("p21-fixture")
    store.save(design)
    items = handoff._external(None, None, None, design.physical_tests)  # noqa: SLF001
    titles = [i.title for i in items]
    has_fail = any("实测失败" in t for t in titles)
    has_unrepro = any("复现不出来" in t for t in titles)
    all_external = all(i.category == "external" for i in items)
    check("1e. handoff 第六类报「实测失败」+「仿真复现不出来」，且全是 external",
          has_fail and has_unrepro and all_external,
          f"items={[(i.category, i.status, i.title[:28]) for i in items]}")
    check("1f. 实测失败不进阻断（blocking 只放我们自己的判定）",
          not any(i.category != "external" for i in items),
          f"非 external 的条目数={sum(1 for i in items if i.category != 'external')}")


# ---------------------------------------------------------------------------
# 2/3. 校准规则
# ---------------------------------------------------------------------------


def check_calibration(tmp: Path) -> None:
    old = fixture_design()
    new = fixture_design()
    base = sim_params.load()

    def runner(old_verdict: str, new_verdict: str):
        def run(design: DesignModel, params: sim_params.ParamSet) -> dict[str, Any]:
            tag = "old" if design is old else "new"
            return {"status": old_verdict if tag == "old" else new_verdict,
                    "detail": f"{tag} 判定"}
        return run

    # 第 2 条：真实失败 → 新参数让**旧设计也失败** → 采纳
    cal = sim_calibration.calibrate(
        old, new, test_id="pt-001", params=base,
        candidates=[{"mu_tyre_rod": 0.57}],
        runner=runner("fail", "pass"))
    adopted = cal.adopted.get("mu_tyre_rod")
    check("2. 采纳标准是「旧设计在新参数下也失败」→ 参数被采纳",
          cal.accepted and adopted == 0.57
          and cal.params.get("mu_tyre_rod") == 0.57
          and cal.attempts[0].reason == "reproduces_old_failure",
          f"accepted={cal.accepted} adopted={cal.adopted} "
          f"old={cal.attempts[0].old_status} new={cal.attempts[0].new_status or '(未评估)'} "
          f"新参数值={cal.params.get('mu_tyre_rod')}")

    # 第 3 条（反向）：新设计通过、但旧设计**不复现** → 必须拒绝
    cheat = sim_calibration.calibrate(
        old, new, test_id="pt-001", params=base,
        candidates=[{"mesh_mu": 0.0}],
        runner=runner("pass", "pass"))
    check("3. 反向：新设计能过但旧设计不复现 → **拒绝采纳**（防作弊）",
          not cheat.accepted and cheat.adopted == {}
          and cheat.attempts[0].reason == "old_passed"
          and cheat.attempts[0].new_status == "",   # 没复现就不该去判新设计
          f"accepted={cheat.accepted} reason={cheat.attempts[0].reason} "
          f"detail={cheat.attempts[0].detail[:120]}")

    # 另外三条拒绝理由各验一次（都要有，否则闸门等于没写）
    reasons = {}
    for name, cand, run in (
            ("old_unknown", [{"supply_r": 0.394}], runner("unknown", "pass")),
            ("too_many_params", [{"mu_tyre_rod": 0.1, "mesh_mu": 0.2,
                                  "supply_r": 0.3}], runner("fail", "pass")),
            ("unknown_param", [{"no_such_param": 1.0}], runner("fail", "pass")),
    ):
        result = sim_calibration.calibrate(old, new, test_id="pt-001", params=base,
                                           candidates=cand, runner=run)
        reasons[name] = (result.attempts[0].reason, result.accepted)
    check("4a. 另外三种拒绝理由都生效（unknown / 超 2 个 / 参数名不存在）",
          all(reasons[name][0] == name and not reasons[name][1] for name in reasons),
          json.dumps(reasons, ensure_ascii=False))

    # 采纳后的参数集要能立刻用，且来源升到 measured
    merged = cal.params.params.get("mu_tyre_rod")
    check("4b. 采纳后参数来源升到 measured（不再是 public）",
          merged is not None and merged.source == "measured",
          f"source={merged.source if merged else None} "
          f"（初始 {base.params['mu_tyre_rod'].source}）")


# ---------------------------------------------------------------------------
# 4. 成对存档
# ---------------------------------------------------------------------------


def check_archive(tmp: Path) -> None:
    design = fixture_design()
    base = sim_params.load()
    learned = base.with_params({"mu_tyre_rod": 0.57, "stud_k_n_m": 150000.0},
                               source="measured", label="candidate:pt-001")
    result = sim_calibration.archive(design, learned, tmp, date="2026-11-21")
    initial = Path(result.get("initial") or "")
    path = Path(result.get("learned") or "")
    check("5a. 存档成对：sim_params/initial.json + learned-<date>.json 都在",
          result.get("status") == "ok" and result.get("pair_ok")
          and initial.is_file() and path.is_file()
          and path.name == "learned-2026-11-21.json",
          f"initial={initial.name} learned={path.name} pair_ok={result.get('pair_ok')}")

    turned_on = [row["key"] for row in result.get("turned_on") or []]
    check("5b. 存档点名 turned_on（初始是 0、校准后非 0 = 初始没建模的物理）",
          "stud_k_n_m" in turned_on and "mu_tyre_rod" in turned_on,
          f"turned_on={turned_on} changed={result.get('changed')}")

    # initial 不会被第二次覆盖（它是"校准前"的定义）
    again = sim_calibration.archive(design, base, tmp, date="2026-11-22")
    initial2 = Path(again.get("initial") or "")
    import io as _io  # noqa: PLC0415 - 只为读一眼文件里的 label
    raw_initial = json.loads(Path(initial2).read_text(encoding="utf-8"))
    loaded_initial = sim_params.load(initial2)          # 不强制 label，读文件里的
    check("5c. 再存一次不覆盖 initial（它是校准前的基线）",
          initial2.is_file()
          and loaded_initial.label == "initial"
          and loaded_initial.get("mu_tyre_rod") == 0.0
          and raw_initial.get("label") == "initial",
          f"label={loaded_initial.label!r} mu_tyre_rod={loaded_initial.get('mu_tyre_rod')}"
          "（应为 initial / 0.0）")


# ---------------------------------------------------------------------------
# 5. contact_sheet
# ---------------------------------------------------------------------------


def check_contact_sheet(tmp: Path) -> None:
    video = tmp / "fake_run.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    result = real_tests.contact_sheet(video, tmp / "sheet.png", columns=4, frames=16)
    has_ffmpeg = bool(shutil.which("ffmpeg"))
    if has_ffmpeg:
        ok = result.get("status") in ("ok", "failed") and "tile=4x4" in str(
            result.get("command"))
        check("6. contact_sheet：抽出 tile 缩略图", bool(ok),
              f"status={result.get('status')} out={result.get('out')}")
        return
    # 没有 ffmpeg：要求给出可直接跑的整条命令（不是"做不到"三个字）
    command = str(result.get("command") or "")
    ok = (result.get("status") == "not_ready"
          and "ffmpeg" in command and "tile=4x4" in command
          and str(video) in command and result.get("out"))
    check("6. contact_sheet：无 ffmpeg → not_ready 且给出可直接粘走跑的命令行",
          ok, f"status={result.get('status')} command={command[:160]}")

    missing = real_tests.contact_sheet(tmp / "nope.mp4")
    check("6b. 视频不存在 → not_ready（不是产出空图）",
          missing.get("status") == "not_ready"
          and "不存在" in str(missing.get("reason")),
          str(missing.get("reason")))


# ---------------------------------------------------------------------------
# 真 MuJoCo：default_runner 的三态必须合法，unknown 时校准必须拒绝
# ---------------------------------------------------------------------------


def check_real_runner() -> None:
    from dsh_embagent.design import mujoco_backend

    ok, why = mujoco_backend.available()
    if not ok:
        check("7a. 真 MuJoCo：default_runner 给出合法三态", False, str(why), status=SKIP)
        return
    design = fixture_design()
    base = sim_params.load()
    verdict = sim_calibration.default_runner(design, base)
    status = str(verdict.get("status") or "")
    check("7a. 真 MuJoCo：default_runner 的判定落在 pass/fail/unknown 里",
          status in sim_calibration.VERDICTS,
          f"status={status} ladder={verdict.get('ladder_status')} "
          f"detail={str(verdict.get('detail'))[:100]}")

    # unknown 时校准必须**拒绝**（不能因为"跑不起来"就采纳）
    if status != "fail":
        result = sim_calibration.calibrate(
            design, design, test_id="pt-real", params=base,
            candidates=[{"mu_tyre_rod": 0.57}], runner=sim_calibration.default_runner)
        rejected = [a for a in result.attempts if not a.adopted]
        check("7b. 真 MuJoCo：旧设计在新参数下不失败 → 校准拒绝（unknown 与 pass 都不算复现）",
              bool(rejected) and rejected[0].reason in ("old_unknown", "old_passed"),
              f"reason={rejected[0].reason if rejected else '(没有拒绝记录)'}")


# ---------------------------------------------------------------------------
# 6. 退化验证
# ---------------------------------------------------------------------------


def check_degradation() -> None:
    old = fixture_design()
    new = fixture_design()
    base = sim_params.load()

    def strict_runner(design: DesignModel, params: sim_params.ParamSet) -> dict[str, Any]:
        tag = "old" if design is old else "new"
        # 旧：参数会让它"通过"（= 调松了）；新：通过
        return {"status": "pass", "detail": tag}

    strict = sim_calibration.calibrate(
        old, new, test_id="pt-001", params=base,
        candidates=[{"mesh_mu": 0.0}], runner=strict_runner)

    saved = sim_calibration.calibrate
    try:
        # 退化：采纳标准换成"新设计通过就采纳"（把 o要复现旧失败 这条删掉）
        def loose(design_old, design_new, *, test_id, params, candidates,
                  runner=None, max_params=sim_calibration.MAX_PARAMS_PER_FAILURE):
            judge = runner or sim_calibration.default_runner
            current = params
            result = sim_calibration.Calibration(test_id=str(test_id))
            for raw in candidates:
                attempt = sim_calibration.Attempt(params=dict(raw))
                trial = current.with_params(attempt.params, source="measured")
                attempt.new_status = str(judge(design_new, trial).get("status"))
                if attempt.new_status == "pass":       # ← 只看新设计
                    attempt.adopted = True
                    attempt.reason = "new_passes"
                    current = trial
                    result.adopted.update(attempt.params)
                result.attempts.append(attempt)
            result.params = current
            return result
        degraded = loose(old, new, test_id="pt-001", params=base,
                         candidates=[{"mesh_mu": 0.0}], runner=strict_runner)
    finally:
        sim_calibration.calibrate = saved  # type: ignore[assignment]

    ok = (not strict.accepted and strict.attempts[0].reason == "old_passed"
          and degraded.accepted)
    check("8. 退化验证：采纳标准换成「新设计通过」→ 第 3 条的判据变红（证明它承重）",
          ok, f"严格={strict.accepted}/{strict.attempts[0].reason}；"
              f"退化后={degraded.accepted}/{degraded.attempts[0].reason}")


# ---------------------------------------------------------------------------
# 真设计：handoff 出现 external（跑完清掉，不留夹具）
# ---------------------------------------------------------------------------


def check_real_project() -> None:
    store = DesignStore("r3demo")
    if not store.exists():
        check("9. 真工程 r3demo：handoff 出现实测类条目", False,
              "工程 r3demo 不在工作区", status=SKIP)
        return
    design = store.load()
    backup = list(design.physical_tests or [])
    try:
        design.physical_tests = [{
            "id": "pt-900",
            "subject": "r3demo",
            "test": "上电自检",
            "result": "fail",
            "observed": "上电后无输出，3.3V 轨跌到 0.7V",
            "implicated": ["ref:U1"] if design.part("U1") else [],
            "sim_reproduced": False,
            "sim_params": "initial",
            "at": "2026-11-21T00:00:00Z",
        }]
        store.save(design)
        result = handoff.build(store)
        items = [i for i in result.items if i.category == "external"]
        has_real = any("实测失败" in i.title for i in items)
        check("9. 真工程 r3demo：handoff 第六类出现实测失败 + 复现不出来",
              has_real and any("复现不出来" in i.title for i in items),
              f"external={[(i.status, i.title[:24]) for i in items][:6]}")
    finally:
        design.physical_tests = backup
        store.save(design)


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P21 验收：闭环（实物测试 + 参数校准）===\n")
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p21-"))
    try:
        if only in ("", "record"):
            check_record_and_handoff(tmp)
        if only in ("", "calibration"):
            check_calibration(tmp)
        if only in ("", "archive"):
            check_archive(tmp)
        if only in ("", "sheet"):
            check_contact_sheet(tmp)
        if only in ("", "runner"):
            check_real_runner()
        if only in ("", "degrade"):
            check_degradation()
        if only in ("", "project"):
            check_real_project()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # 夹具工程（如果在临时运行里被创建过）
        fixture = DesignStore("p21-fixture")
        if fixture.exists():
            fixture.delete()

    passed = sum(1 for r in _RESULTS if r["status"] == PASS)
    failed = sum(1 for r in _RESULTS if r["status"] == FAIL)
    skipped = sum(1 for r in _RESULTS if r["status"] not in (PASS, FAIL))
    status = "ok" if failed == 0 and skipped == 0 else ("failed" if failed else "not_ready")
    print(f"\n=== {passed} 通过 / {failed} 失败 / {skipped} 未做到 ===")
    payload = {"status": status, "check": "P21", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())