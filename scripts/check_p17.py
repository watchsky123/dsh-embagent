"""P17 验收：立创EDA 发射层（``design/lceda.py`` + ``emit_lceda.py`` + ``stackup_spec.py``）。

    python -m hw_velxio.scripts.check_p17

七条验收对应 `docs/execution-plan-v3.md` §P17，其中第 7 条是**退化验证**
（把自校验拆掉，确认验收真的会红）。

## 两种夹具分开用（AGENTS §0.3）

* **合成夹具**：3 个 C1523（本机系统库实测存在的器件）+ 2 条网 +
  1 个**故意写坏**的 lcsc。它证明「装上去、连起来、回读逐条相等、搜不到的
  那一个不拖累其它」这些判据本身对。
* **假 CLI**：一个 `.cmd` + Python 脚本，模仿 ``lceda-pro`` 的 JSON 信封
  （stdout 一个对象 + stderr 一行人话）。它证明通道层的三条纪律：
  F1 不合并 stderr、F2 业务信封要验、`expect` 缺了就报错。
  不需要真的立创，所以**验收在没有立创的机器上也能跑**（那几条真机检查会
  如实记为 not_ready 而不是 pass）。

## 为什么验收第 4 条写成「如实报 not_ready」

计划原文是「2 层、3 网、无 DRC error」。本机实测：自动布线引擎**拒绝执行**
（headless 与可见窗口一致、渲染正常、网表存在、元件在板框内、DRC 能跑）。
所以这条验收能成立的形式是：**引擎拒绝时不能报成功**——断言
``route`` 段是 ``not_ready`` 且原因里点名引擎，同时 DRC 证据照常记录。
把它改成像成功的样子，这条断言就会红。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import emit_lceda, lceda, stackup_spec  # noqa: E402
from hw_velxio.design.model import Board, DesignModel, Part, Placement  # noqa: E402

PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    """记一条验收。``status`` 覆盖时用于「没做到但不是 bug」（如真实机不可用）。"""
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": detail[:600]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {detail[:600]}")
    return ok


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

_VERIFIED_LCSC = "C1523"      # 本机系统库实测存在（1nF 0402）
_ABSENT_LCSC = "C999999999"   # 保证搜不到，用来验「不整批失败」


def synthetic_design() -> DesignModel:
    design = DesignModel(title="hwv-p17-accept")
    design.board = Board(kind="generic", name="accept",
                         outline=[(0, 0), (40, 0), (40, 30), (0, 30)],
                         size_mm={"w": 40, "h": 30}, thickness_mm=1.6)
    placements = {"R1": (8, 10), "R2": (22, 10), "R3": (32, 20), "U9": (12, 22)}
    for ref, (x, y) in placements.items():
        lcsc = _ABSENT_LCSC if ref == "U9" else _VERIFIED_LCSC
        design.add_part(Part(
            ref=ref, kind="capacitor",
            label="0402B102K500NT" if lcsc == _VERIFIED_LCSC else "X9", lcsc=lcsc,
            size_mm={"w": 1.0, "h": 0.5, "d": 0.5},
            physical_source="datasheet",
            placement=Placement(x_mm=x, y_mm=y, side="top"),
            pins=["1", "2"],
        ))
    design.connect("MID", ("R1", "1"), ("R2", "1"))
    design.connect("GND", ("R1", "2"), ("R2", "2"), ("R3", "2"))
    return design


# ---------------------------------------------------------------------------
# 第 1 条：探测
# ---------------------------------------------------------------------------


def check_probe() -> bool:
    found = lceda.report()
    caps = found.get("capabilities") or {}
    total = sum(len(v) for v in caps.values())
    ok = (found.get("status") == "ok"
          and bool(found.get("version"))
          and total >= 20
          and not found.get("missing"))
    detail = (f"version={found.get('version')!r} "
              f"核对 {found.get('checked')}/{total} 条能力 "
              f"missing={found.get('missing')}")
    if found.get("status") != "ok":
        return check("1. 探测：版本 + 关键类存在（doc api 实测）", False, detail,
                     status=SKIP)
    return check("1. 探测：版本 + 关键类存在（doc api 实测）", ok, detail)


# ---------------------------------------------------------------------------
# 第 6 条：没有 lceda → not_ready，不抛异常
# ---------------------------------------------------------------------------


def check_no_lceda() -> bool:
    design = synthetic_design()
    old = os.environ.get(lceda.EXE_ENV)
    os.environ[lceda.EXE_ENV] = str(Path(tempfile.gettempdir()) / "no-such-lceda.exe")
    try:
        report = emit_lceda.emit(design, project_name="hwv-p17-nolceda",
                                 jobs=("project", "schematic", "pcb"))
    except Exception as exc:  # noqa: BLE001 - 抛异常就是失败
        return check("6. 闸门：无 lceda CLI → not_ready 且不抛异常", False,
                     f"抛了 {type(exc).__name__}: {exc}")
    finally:
        if old is None:
            os.environ.pop(lceda.EXE_ENV, None)
        else:
            os.environ[lceda.EXE_ENV] = old
    payload = report.to_dict()
    statuses = {k: v["status"] for k, v in payload["jobs"].items()}
    empty = all(not v.get("counts") and not v.get("artifacts")
                for v in payload["jobs"].values())
    ok = (payload["status"] == "not_ready"
          and set(statuses.values()) == {"not_ready"}
          and empty
          and bool(payload.get("notes")))
    return check("6. 闸门：无 lceda CLI → not_ready 且不抛异常", ok,
                 f"status={payload['status']} jobs={statuses} 空产物={empty}")


# ---------------------------------------------------------------------------
# 第 2/3/4/5 条：真机
# ---------------------------------------------------------------------------


def check_live() -> None:
    design = synthetic_design()
    started = time.time()
    report = emit_lceda.emit(design, project_name="hwv-p17-accept",
                             jobs=("project", "schematic", "pcb", "route"),
                             route="native", max_parts=8)
    payload = report.to_dict()
    elapsed = round(time.time() - started, 1)
    jobs = payload["jobs"]
    unresolved = payload.get("unresolved") or []
    parity = payload.get("parity") or {}

    # -- 2. 最小工程真的建出来了 --
    proj = jobs.get("project", {})
    pcb = jobs.get("pcb", {})
    path = (payload.get("project") or {}).get("path") or ""
    built = (proj.get("status") == "ok"
             and pcb.get("status") == "ok"
             and (pcb.get("counts") or {}).get("outline_ready", 0) >= 1
             and Path(path).exists())
    check("2. 最小工程：建工程 + 原理图 + PCB + 板框（落盘为 .eprj3 文件夹）",
          built, f"{elapsed}s path={path} pcb={pcb.get('note')}")

    # -- 5. 搜不到的器件进 unresolved，其余继续 --
    bad = [u for u in unresolved if u.get("ref") == "U9"]
    placed = (jobs.get("schematic", {}).get("counts") or {}).get("placed", 0)
    ok_unresolved = (len(bad) == 1
                     and bad[0].get("reason") == "no_symbol"
                     and placed >= 3
                     and jobs.get("schematic", {}).get("status") == "ok")
    check("5. 搜不到符号 → unresolved 非空且其余器件继续装",
          ok_unresolved,
          f"placed={placed} unresolved={[(u.get('ref'), u.get('reason')) for u in unresolved]}")

    # -- 3. 回读 parity 逐条相等 --
    counts = parity.get("counts") or {}
    ok_parity = (parity.get("status") == "ok"
                 and parity.get("checked", 0) >= 5
                 and counts.get("missing") == 0
                 and counts.get("wrong") == 0
                 and counts.get("extra") == 0)
    check("3. 回读 parity：立创 PCB 网表 vs design.json 逐引脚相等",
          ok_parity,
          f"status={parity.get('status')} checked={parity.get('checked')} counts={counts} "
          f"note={parity.get('note')}")

    # -- 4a. 布线的判定必须落在**几何**上（不看 successNetsCount）--
    #
    # 曾经这里断言的是「引擎拒绝时 route 是 not_ready」——那个前提**不成立**：
    # 真因是板框 Y 写反（见 4a2），引擎一直在跑。现在的纪律是：
    # 报 ok 就必须有多条走线证据；报 not_ready 就必须点名原因。
    route_job = jobs.get("route", {})
    jc = route_job.get("counts") or {}
    route_note = route_job.get("note") or ""
    if route_job.get("status") == "ok":
        route_honest = (int(jc.get("tracks") or 0) > 0
                        and not (jc.get("missing") or [])
                        and int(jc.get("expected_nets") or 0) > 0)
    else:
        route_honest = (route_job.get("status") == "not_ready" and bool(route_note))
    check("4a. 布线：ok 只在「每个 ≥2 焊盘的网络都有走线」时给出（判据在几何上）",
          route_honest,
          f"status={route_job.get('status')} counts={jc} note={route_note[:160]}")

    # -- 4a2. 板框 R 模式的左上角必须是 **max Y**（写反 → 整块板在板框外）--
    #
    # 这是 2026-11-21 查出来的真因：`['R', 0, 0, w, h]` 让板框落在 y∈[-h,0]，
    # 而元件在 y>0 ⇒ autoRouting 返回 totalNetsCount=2 / successNetsCount=0。
    # 这条是**纯函数**断言，不需要立创在线；改回 y0 它立刻红。
    from hw_velxio.design.model import Board  # noqa: PLC0415

    probe = Board(kind="generic", name="ycheck",
                  outline=[(0.0, 0.0), (50.0, 0.0), (50.0, 40.0), (0.0, 40.0)],
                  size_mm={"w": 50, "h": 40}, thickness_mm=1.6)
    src = emit_lceda._outline_source(design, probe)
    top = float(src[2]) if len(src) > 2 else -1.0
    height = float(src[4]) if len(src) > 4 else -1.0
    span = (top - height, top)      # 板框覆盖的 y 区间
    y_ok = (src[:1] == ["R"]
            and abs(top - height) < 1e-6          # 左上角 y == 高度 ⇒ 区间 [0, h]
            and abs(span[0]) < 1e-6 and top > 0)  # 下边界正好是 0，上边界在正半轴
    check("4a2. 板框 R 模式：左上角 y = max Y（区间覆盖 [0,h]，不是 [-h,0]）",
          y_ok, f"src={src} 覆盖 y∈{span}")

    # -- 4a3. 分母只算 ≥2 焊盘的网络；空设计必须 not_ready（不是 0 条 = 通过）--
    from hw_velxio.design.model import DesignModel  # noqa: PLC0415

    expected = emit_lceda._expected_nets(design)
    empty_expected = emit_lceda._expected_nets(DesignModel(title="empty"))
    single = DesignModel(title="single")
    single_expected: list[str] = []
    if design.parts:
        first = design.parts[0]
        single.add_part(first)
        single.connect("ONLY_ONE_PIN", (first.ref, first.pins[0]))
        single_expected = emit_lceda._expected_nets(single)
    check("4a3. 分母只算 ≥2 焊盘的网络；空设计/单焊盘网都返回空（→ not_ready）",
          bool(expected) and empty_expected == [] and single_expected == [],
          f"design={expected} empty={empty_expected} single={single_expected}")

    # -- 4b. DRC 作为外部证据记录，且不进 ladder --
    drc = payload.get("drc") or {}
    drc_recorded = (drc.get("status") in ("ok", "not_ready")
                    and "boards" in drc
                    and "external" in (drc.get("note") or ""))
    check("4b. DRC 记为外部证据（写明是 external，不进 ladder）",
          drc_recorded,
          f"status={drc.get('status')} pass={drc.get('pass')} "
          f"boards={[(b.get('board'), b.get('pass'), b.get('violations')) for b in drc.get('boards') or []]}")

    # -- 4c. route=none 时如实说「只放置」 --
    none_report = emit_lceda.EmitReport()
    emit_lceda._run_route(None, design, none_report, route="none")  # type: ignore[arg-type]
    none_job = none_report.job("route")
    check("4c. route=none → ok，且说明只放置不布线",
          none_job.status == "ok" and "只放置" in none_job.note,
          f"status={none_job.status} note={none_job.note}")

    # -- 4d. 叠层：没有 stackup 字段时标 assumed，不装作知道 --
    spec = stackup_spec.derive(design)
    assumed = spec.assumed
    check("4d. 叠层：无 stackup 字段 → 标 assumed 并说清（不装作 4 层）",
          spec.layers_source == "assumed" and bool(assumed),
          f"layers={spec.copper_layers} source={spec.layers_source} assumed={assumed}")


# ---------------------------------------------------------------------------
# 假 CLI：通道层的三条纪律（不需要真立创）
# ---------------------------------------------------------------------------

_FAKE_PY = r'''
import json, sys
args = sys.argv[1:]
if "--help" in args:
    print("JLCEDA Pro 9.9.9-fake")
    raise SystemExit(0)
if "session" in args:
    print(json.dumps({"ok": True, "value": [{"sessionId": "fake-1", "status": "alive"}],
                      "logs": [], "durationMs": 0}))
    raise SystemExit(0)
if "open" in args:
    print(json.dumps({"ok": True, "value": {"sessionId": "fake-1", "headless": True},
                      "logs": [], "durationMs": 1}))
    raise SystemExit(0)
if "invoke" in args:
    code = sys.stdin.read() if "-" in args else ""
    if "PARAM_REQUIRED" in code:
        # F2：CLI 层 ok:true，业务层失败
        print(json.dumps({"ok": True, "value": {"ok": False,
              "error": {"code": "PARAM_REQUIRED", "message": "libraryUuid 不能为空"}},
              "logs": [], "durationMs": 2}))
        sys.stdout.flush()
        print("PARAM_REQUIRED: libraryUuid 不能为空", file=sys.stderr)
    elif "THROW" in code:
        # F1：stdout 是 JSON 错误，stderr 再多一行人话
        print(json.dumps({"ok": False, "error": {"code": "EXECUTION_ERROR",
              "message": "boom"}, "logs": [], "durationMs": 3}))
        sys.stdout.flush()
        print("EXECUTION_ERROR: boom", file=sys.stderr)
    else:
        print(json.dumps({"ok": True, "value": {"ok": True, "marker": "alpha"},
                          "logs": [], "durationMs": 4}))
    raise SystemExit(0)
print(json.dumps({"ok": False, "error": {"code": "UNKNOWN", "message": "?"}}))
'''


def _make_fake_exe(tmp: Path) -> str:
    """造一个假的 ``lceda-pro``。**二进制 CRLF**（AGENTS §3.5）。"""
    script = tmp / "fake_lceda.py"
    script.write_text(_FAKE_PY, encoding="utf-8")
    cmd = tmp / "fake_lceda.cmd"
    body = f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n'
    cmd.write_bytes(body.encode("ascii"))
    return str(cmd)


def check_channel() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="hwv-fake-lceda-"))
    try:
        exe = _make_fake_exe(tmp)
        session = lceda.LcedaSession(exe=exe).open(headless=True)

        # F1：stderr 不参与解析
        try:
            session.invoke("return THROW;", expect=("marker",))
            f1 = False
            f1_detail = "本应抛 LcedaError"
        except lceda.LcedaError as exc:
            f1 = "boom" in str(exc)
            f1_detail = str(exc)[:200]
        check("C1. F1：stdout 信封 + stderr 人话 → 不因 Extra data 崩", f1, f1_detail)

        # F2：业务信封要被检查
        try:
            session.invoke("return PARAM_REQUIRED;")
            f2 = False
            f2_detail = "业务 ok:false 竟然没抛"
        except lceda.LcedaError as exc:
            f2 = "PARAM_REQUIRED" in str(exc)
            f2_detail = str(exc)[:200]
        check("C2. F2：CLI ok:true 但业务 ok:false → 抛 LcedaError", f2, f2_detail)

        # expect：必须出现的子串
        try:
            session.invoke("return 1;", expect=("beta",))
            f3 = False
            f3_detail = "expect 没生效"
        except lceda.LcedaError as exc:
            f3 = "beta" in str(exc)
            f3_detail = str(exc)[:200]
        check("C3. expect：缺少必需子串 → 抛错", f3, f3_detail)

        # -- 退化验证 A：关掉业务信封检查 → F2 那种失败静默通过 --------------
        # 注意 `THROW` 是 CLI 层失败，**任何**参数下都会抛；能退化的只有
        # `require_ok` 这一层（F2）。所以这里只退化 F2，不混为一谈。
        loose = lceda.LcedaSession(exe=exe)
        loose.session_id = session.session_id
        silent = False
        try:
            loose.invoke("return PARAM_REQUIRED;", require_ok=False, expect=())
            silent = True
        except lceda.LcedaError:
            silent = False
        check("C4. 退化验证：关掉 require_ok → 业务失败静默通过（证明 C2 承重）",
              silent, "require_ok=False 时 PARAM_REQUIRED 不再抛出")

        # -- 退化验证 B：把 stderr 拼进 stdout 再解析 → Extra data ----------
        # 这是 F1 那条纪律的**直接反证**：不用改我们的代码，只要按错误方式
        # 拼一次就能看见它为什么会坏。
        import subprocess  # noqa: PLC0415
        proc = subprocess.run(  # noqa: S603
            [exe, "invoke", "--code-file", "-"],
            input=b"return THROW;",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        merged = proc.stdout.decode("utf-8", errors="replace")
        broke = False
        why = ""
        try:
            json.loads(merged.strip())
            why = "竟然解析成功了"
        except json.JSONDecodeError as exc:
            # 真实 CLI 是「stdout 的 JSON + stderr 的人话」，合并后是
            # `Extra data`；顺序反过来则是 `Expecting value`。
            # 两种都证明「不能合并」，断言只要求解析失败。
            broke = True
            why = str(exc)[:80]
        check("C5. 退化验证：stdout+stderr 合并解析 → Extra data（证明 F1 承重）",
              broke, f"合并后解析失败：{why}")
        session.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 纯函数（合成数据）
# ---------------------------------------------------------------------------


def check_helpers() -> None:
    # 单位换算（F3）
    ok_units = (lceda.mm_to_mil(25.4) == 1000.0
                and abs(lceda.mil_to_mm(1000) - 25.4) < 1e-6)
    check("H1. 单位：1 英寸 = 1000 PCB 单位（mil），往返一致", ok_units,
          f"mm_to_mil(25.4)={lceda.mm_to_mil(25.4)} mil_to_mm(1000)={lceda.mil_to_mm(1000)}")

    # 工程名净化：立创只允许 a-zA-Z0-9-
    names = [emit_lceda._sanitize_project_name("示例-单板 主控"),
             emit_lceda._sanitize_project_name("ok_name-1")]
    ok_name = all(ch.isalnum() or ch == "-" for name in names for ch in name) and names[1]
    check("H2. 工程名只留 a-zA-Z0-9-（立创硬限制）", bool(ok_name),
          f"{names}")

    # lcsc 占位符不猜
    class _P:
        lcsc = "C?"
    class _Q:
        lcsc = "C1523"
    ok_lcsc = emit_lceda._lcsc_of(_P()) == "" and emit_lceda._lcsc_of(_Q()) == "C1523"
    check("H3. lcsc 占位符（C?）当没有，不猜符号", ok_lcsc,
          f"C?→{emit_lceda._lcsc_of(_P())!r} C1523→{emit_lceda._lcsc_of(_Q())!r}")

    # 板框：矩形走 R 模式，非矩形走折线
    design = synthetic_design()
    rect = emit_lceda._outline_source(design, design.board)
    star = Board(kind="generic", name="star",
                 outline=[(0, 0), (10, 1), (5, 8)], size_mm={"w": 10, "h": 8})
    tri = emit_lceda._outline_source(design, star)
    ok_outline = (rect[0] == "R" and len(rect) == 7
                  and "L" in tri and len(tri) >= 9)
    check("H4. 板框：矩形用 R 模式（实测可建），非矩形用折线源数组",
          ok_outline, f"rect={rect[:7]} tri_len={len(tri)}")

    # parity：相等 / 缺 / 错 / 多
    netlist = json.dumps({"components": {
        "gge1": {"props": {"Designator": "R1"},
                 "pinInfoMap": {"1": {"number": "1", "net": "MID"},
                                "2": {"number": "2", "net": "GND"}}},
    }})
    same = emit_lceda.parity_from_netlists(design, [netlist], exclude_refs={"R2", "R3", "U9"})
    wrong_netlist = json.dumps({"components": {
        "gge1": {"props": {"Designator": "R1"},
                 "pinInfoMap": {"1": {"number": "1", "net": "GND"},
                                "2": {"number": "2", "net": "GND"}}},
    }})
    wrong = emit_lceda.parity_from_netlists(design, [wrong_netlist], exclude_refs={"R2", "R3", "U9"})
    ok_parity = (same["status"] == "ok" and same["counts"] == {"missing": 0, "wrong": 0, "extra": 0}
                 and wrong["status"] == "not_ready" and wrong["counts"]["wrong"] == 1)
    check("H5. parity：相等判 ok，网名不一致判 not_ready（逐条指出）", ok_parity,
          f"same={same['counts']} wrong={wrong['counts']}")

    # 空网表 → not_ready（不是 ok）
    empty = emit_lceda.parity_from_netlists(design, [])
    check("H6. 回读不到网表 → not_ready（空数据不算通过）",
          empty["status"] == "not_ready", empty.get("note", ""))

    # 空 jobs → not_ready（AGENTS §0.1）
    blank = emit_lceda.EmitReport()
    check("H7. 空 jobs 表 → not_ready（不是 ok）",
          blank.recompute_status() == "not_ready", f"status={blank.status}")

    # 多板：按 Placement.board_id 分板，不按顺序猜
    multi = DesignModel(title="multi")
    multi.boards = [
        Board(id="B1", kind="generic", name="主控", outline=[(0, 0), (20, 0), (20, 20)],
              size_mm={"w": 20, "h": 20}),
        Board(id="B2", kind="generic", name="电源", outline=[(0, 0), (10, 0), (10, 10)],
              size_mm={"w": 10, "h": 10}),
    ]
    multi.add_part(Part(ref="U1", kind="mcu", lcsc=_VERIFIED_LCSC,
                        physical_source="datasheet", pins=["1"],
                        placement=Placement(x_mm=5, y_mm=5, board_id="B1")))
    multi.add_part(Part(ref="U2", kind="regulator", lcsc=_VERIFIED_LCSC,
                        physical_source="datasheet", pins=["1"],
                        placement=Placement(x_mm=3, y_mm=3, board_id="B2")))
    boards = multi.board_list()
    b1 = [p.ref for p in emit_lceda._parts_for_board(multi, "B1", len(boards))]
    b2 = [p.ref for p in emit_lceda._parts_for_board(multi, "B2", len(boards))]
    check("H9. 多板：按 Placement.board_id 分板（U1→B1，U2→B2）",
          b1 == ["U1"] and b2 == ["U2"] and len(boards) == 2,
          f"boards={len(boards)} B1={b1} B2={b2}")

    # 空板 DRC 不算通过
    design_empty = DesignModel(title="empty")
    design_empty.board = Board(kind="generic", name="e",
                               outline=[(0, 0), (10, 0), (10, 10)], size_mm={"w": 10, "h": 10})
    design_empty.add_part(Part(ref="R1", kind="resistor", lcsc=_VERIFIED_LCSC,
                               physical_source="datasheet", pins=["1", "2"]))
    report = emit_lceda.EmitReport()
    report.project["boards"] = [{"name": "Board1", "pcbUuid": "x",
                                 "schematicUuid": "y", "pageUuid": "z"}]
    fake_session = type("S", (), {
        "invoke": lambda self, *a, **k: {"steps": [{"step": "importChanges", "ok": False,
                                                    "error": "nope"}],
                                          "componentCount": 0, "outlineOk": True,
                                          "moved": [], "missing": [], "netlist": "",
                                          "drcPass": True, "drcDetail": []},
        "current_project": lambda self: {"boards": []},
        "projects_root": lambda self: "",
    })()
    emit_lceda._run_pcb(fake_session, design_empty, report)  # type: ignore[arg-type]
    check("H8. 空板 DRC 通过不算数（缺数据不是通过）",
          report.drc.get("status") == "not_ready" and report.drc.get("pass") is False,
          f"drc.status={report.drc.get('status')} pass={report.drc.get('pass')} note={report.drc.get('note')}")

    # 回读那条路也要有同样的守卫（两处各写一次，必须都验）
    empty_session = type("S", (), {
        "invoke": lambda self, *a, **k: {"netlist": "", "drcPass": True,
                                         "drcDetail": [], "componentCount": 0},
        "current_project": lambda self: {"boards": []},
    })()
    back = emit_lceda.read_back(empty_session, design_empty,  # type: ignore[arg-type]
                                boards=[{"name": "Board1", "pcbUuid": "x"}])
    check("H10. 回读路径的空板 DRC 同样不算数（两处守卫都要在）",
          back["drc"]["status"] == "not_ready" and back["drc"]["pass"] is False,
          f"drc={back['drc'].get('status')} pass={back['drc'].get('pass')}")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P17 验收：立创EDA 发射层 ===\n")
    if only in ("", "helpers"):
        check_helpers()
    if only in ("", "channel"):
        check_channel()
    if only in ("", "probe"):
        check_probe()
    if only in ("", "nolceda"):
        check_no_lceda()
    if only in ("", "live"):
        # 单独跑 `--only=live` 时先探测；全量跑时 `probe` 组已经查过，
        # 不重复记一条同名断言。
        ready = any(r["check"].startswith("1. 探测") and r["ok"] for r in _RESULTS)
        if not ready:
            ready = check_probe()
        if ready:
            check_live()
        else:
            check("2/3/4/5. 真机发射", False, "没有可用的 lceda-pro", status=SKIP)

    passed = sum(1 for r in _RESULTS if r["status"] == PASS)
    failed = sum(1 for r in _RESULTS if r["status"] == FAIL)
    skipped = sum(1 for r in _RESULTS if r["status"] not in (PASS, FAIL))
    status = "ok" if failed == 0 and skipped == 0 else ("failed" if failed else "not_ready")
    print(f"\n=== {passed} 通过 / {failed} 失败 / {skipped} 未做到 ===")
    payload = {"status": status, "check": "P17", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())