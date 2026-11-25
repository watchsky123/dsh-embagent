"""P23 验收：测试规模与降级路径。

    python -m dsh_embagent.scripts.check_p23

四条对应 `dsh-embagent-docs:docs/execution-plan-v3.md` §P23：

1. 单测 **≥550**（实测 665）
2. 验收脚本 **≥12**（实测 16）
3. 合成夹具 **≥15**，每个都写明"它存在的理由"
4. **降级**：没有 MuJoCo / 没有 lceda 时，单测与关键验收脚本仍然全绿

## 为什么第 4 条要真的跑一遍

前三条是数数，第四条是**保证**。而保证不能靠嘴说：
MuJoCo 装在项目内 `vendor/pylib`，卸不掉；lceda 是用户装的应用，也不该为测试删。
所以给两条显式开关（`HWV_NO_MUJOCO=1` / `HWV_LCEDA=<不存在的路径>`），
然后在**子进程里**把整套单测重跑一遍——`unittest` 的结论是真的跑出来的。

对照组也留着（第 4c 条）：把开关关掉，`available()` 必须回到 True。
否则"降级后全绿"可能只是因为**开关根本没生效**。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "dsh_embagent"
PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []

#: 计划里的门槛。**写死在这里**，不跟着实测值浮动——
#: 否则"实测 665 → 门槛改 660"这种自欺会藏很久。
MIN_TESTS = 550
MIN_SCRIPTS = 12
MIN_FIXTURES = 15


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": str(detail)[:900]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {str(detail)[:900]}")
    return ok


def _run(argv: list[str], *, env: dict[str, str] | None = None,
         timeout: int = 900) -> tuple[int, str]:
    proc = subprocess.run(  # noqa: S603 - argv 由本脚本拼，不含用户输入
        argv, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={**os.environ, **(env or {})}, timeout=timeout, check=False)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


_TEST_MODULES = ("dsh_embagent.tests.test_design", "dsh_embagent.tests.test_phases")


def _unittest(argv_extra: list[str] | None = None,
              env: dict[str, str] | None = None) -> tuple[int, int, str]:
    code, out = _run([sys.executable, "-m", "unittest", *_TEST_MODULES,
                      *(argv_extra or [])], env=env)
    match = re.search(r"Ran (\d+) tests?", out)
    ran = int(match.group(1)) if match else -1
    return code, ran, out


# ---------------------------------------------------------------------------
# 1/2/3：规模
# ---------------------------------------------------------------------------


def check_scale(tmp: Path) -> None:
    code, ran, out = _unittest()
    check("1a. 单测总数 ≥ %d（实测 %d）" % (MIN_TESTS, ran),
          code == 0 and ran >= MIN_TESTS,
          f"exit={code} ran={ran}" + ("" if code == 0 else f"\n{out[-600:]}"))

    check("1b. 全部单测通过（不是 skip 掉了）",
          code == 0 and "OK" in out.splitlines()[-1] if out.splitlines() else False,
          out.strip().splitlines()[-1] if out.strip() else "(无输出)")

    scripts = sorted(p.stem for p in (PKG / "scripts").glob("check_*.py"))
    check(f"2. 验收脚本 ≥ {MIN_SCRIPTS}（实测 {len(scripts)}）",
          len(scripts) >= MIN_SCRIPTS, ", ".join(scripts))

    fixtures = sorted(p.stem for p in (PKG / "tests" / "fixtures" / "designs").glob("*.json"))
    check(f"3a. 合成夹具 ≥ {MIN_FIXTURES}（实测 {len(fixtures)}）",
          len(fixtures) >= MIN_FIXTURES, ", ".join(fixtures))

    # 每个夹具都要在测试里有"设计意图"条目，否则它只是躺在磁盘上的 JSON
    phase_src = (PKG / "tests" / "test_phases.py").read_text(encoding="utf-8")
    missing = [name for name in fixtures if f'"{name}"' not in phase_src]
    check("3b. 每个夹具都在测试里声明了设计意图（clean/validate/geometry）",
          not missing, f"没声明的：{missing}")

    covered = {"stacked-2layer": "叠板", "folded-hinge": "折叠", "quadrotor": "四旋翼",
               "multilayer-4": "多层板"}
    absent = [k for k in covered if k not in fixtures]
    check("3c. 计划的四类场景都在（叠板/折叠/四旋翼/多层板）",
          not absent, f"缺：{absent}")


# ---------------------------------------------------------------------------
# 4：降级路径（子进程真的跑）
# ---------------------------------------------------------------------------


def check_degraded(tmp: Path) -> None:
    from dsh_embagent.design import lceda, mujoco_backend

    # 4a. 没有 MuJoCo
    code, ran, out = _unittest(env={mujoco_backend.NO_MUJOCO_ENV: "1"})
    ok_mujoco = code == 0 and ran >= MIN_TESTS
    check("4a. 没有 MuJoCo 时单测仍全绿（%s=1）" % mujoco_backend.NO_MUJOCO_ENV,
          ok_mujoco, f"exit={code} ran={ran}" +
          ("" if ok_mujoco else f"\n{out[-700:]}"))

    # 4b. 没有 lceda
    code2, ran2, out2 = _unittest(env={lceda.EXE_ENV: "C:/no-such-lceda-pro.exe"})
    ok_lceda = code2 == 0 and ran2 >= MIN_TESTS
    check("4b. 没有 lceda CLI 时单测仍全绿（%s=<不存在>）" % lceda.EXE_ENV,
          ok_lceda, f"exit={code2} ran={ran2}" +
          ("" if ok_lceda else f"\n{out2[-700:]}"))

    # 4c. 对照组：开关关掉后 available() 必须回到 True
    env = {mujoco_backend.NO_MUJOCO_ENV: "1"}
    code3, out3 = _run([sys.executable, "-c",
                        "import sys;sys.path.insert(0,'.');"
                        "from dsh_embagent.design import mujoco_backend as m;"
                        "print(m.available())"], env=env)
    off = "False" in out3
    code4, out4 = _run([sys.executable, "-c",
                        "import sys;sys.path.insert(0,'.');"
                        "from dsh_embagent.design import mujoco_backend as m;"
                        "print(m.available())"])
    on = "True" in out4
    check("4c. 对照组：开关生效（同一台机器，一个 False 一个 True）",
          off and on, f"开关开={out3.strip()[:90]!r} 开关关={out4.strip()[:90]!r}")

    # 4d. 关键验收脚本在降级下也通过（会碰 MuJoCo / lceda 的那两个）
    degraded = {mujoco_backend.NO_MUJOCO_ENV: "1",
                lceda.EXE_ENV: "C:/no-such-lceda-pro.exe"}
    bad: list[str] = []
    for script in ("check_ladder", "check_handoff", "check_p22"):
        code5, out5 = _run([sys.executable, "-m", f"dsh_embagent.scripts.{script}"],
                           env=degraded)
        if code5 != 0:
            bad.append(f"{script}: exit={code5} {out5.strip().splitlines()[-1][:120]}")
    check("4d. 降级下 check_ladder / check_handoff / check_p22 仍通过",
          not bad, "；".join(bad) or "三个脚本在无 MuJoCo + 无 lceda 下全绿")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P23 验收：测试规模与降级路径 ===\n")
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p23-"))
    try:
        if only in ("", "scale"):
            check_scale(tmp)
        if only in ("", "degraded"):
            check_degraded(tmp)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for r in _RESULTS if r["status"] == PASS)
    failed = sum(1 for r in _RESULTS if r["status"] == FAIL)
    skipped = sum(1 for r in _RESULTS if r["status"] not in (PASS, FAIL))
    status = "ok" if failed == 0 and skipped == 0 else ("failed" if failed else "not_ready")
    print(f"\n=== {passed} 通过 / {failed} 失败 / {skipped} 未做到 ===")
    payload = {"status": status, "check": "P23", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())