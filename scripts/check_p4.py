"""P4 验收：固件矩阵。

    python -m hw_velxio.scripts.check_p4

P4 把 `pinmap-check` 的三张**问题表**变成 **每脚一行**的矩阵。
这一层最容易犯三个错，每个都对应一条断言：

1. **幽灵引脚整行消失** —— 它硬件里没有配置，所以不在"已配置引脚"里。
   只遍历已配置脚的矩阵会把**唯一运行时必炸**的那类漏掉。
2. **调试脚被算成闲置** —— SWD/JTAG 由调试器用，不该出现在应用代码里。
   把它加回 idle 会凭空多出一堆"闲置"，人就不看了。
3. **没有配置时报"全对"** —— 空集合不能算通过（AGENTS §0.1）。

## 为什么用合成数据做 1/2/3

真实 `.ioc` 里恰好没有方向冲突的脚，也没有"幽灵脚 + 调试脚"同框的样本。
所以那几条**必须自己造**——AGENTS §0.3 那个区分的第四次应用。
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

from hw_velxio.design import firmware_matrix as fm  # noqa: E402
from hw_velxio.design.fwgen import pinmap as pm  # noqa: E402

PASS = "pass"
FAIL = "fail"
SKIP = "not_ready"

_RESULTS: list[dict[str, Any]] = []
REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "hw_velxio"
WORKSPACE = Path(os.environ.get(
    "HWV_WORKSPACE", r"C:\Users\<user>\Desktop\示例工作区"))
IOC = WORKSPACE / "F401-Front-master" / "F401-Front.ioc"


def check(name: str, ok: bool, detail: str = "", *, status: str = "") -> bool:
    resolved = status or (PASS if ok else FAIL)
    _RESULTS.append({"check": name, "status": resolved,
                     "ok": resolved == PASS, "detail": str(detail)[:900]})
    flag = {"pass": "OK ", "fail": "FAIL", "not_ready": "SKIP"}[resolved]
    print(f"[{flag}] {name}")
    if detail:
        print(f"       {str(detail)[:900]}")
    return ok


def _use(pin: str, api: str, file: str = "app.c", line: int = 10) -> dict[str, Any]:
    return {"pin": pin, "api": api, "file": file, "line": line,
            "text": f"HAL_GPIO_{api}(... {pin} ...)"}


def _synthetic() -> tuple[dict[str, Any], dict[str, Any]]:
    """造一份"四类齐全"的硬件侧：ok / idle / direction / ghost 各一个。

    用 `IocPin` 直接构造，避免依赖某个真实 `.ioc` 恰好长成需要的形状。
    """
    pins = {
        # 配成输入，固件在写 → direction 冲突
        "PA1": pm.IocPin(pin="PA1", port="A", bit=1, mode="Input",
                         signal="GPIO_Input", label="BTN"),
        # 配成输出但固件没用到 → idle
        "PA2": pm.IocPin(pin="PA2", port="A", bit=2, mode="Output",
                         signal="GPIO_Output", label="LED"),
        # 配成输出且固件在写 → ok
        "PA3": pm.IocPin(pin="PA3", port="A", bit=3, mode="Output",
                         signal="GPIO_Output", label="EN"),
        # 调试脚：没被应用代码用到，但**不该算闲置**。
        #
        # `reserved_debug` 是 `IocPin` 的**派生属性**（由 mode/signal 推），
        # 不是可赋值字段——第一版把它当构造参数传，直接 TypeError。
        # 派生状态只能通过 `mode`/`signal` 表达，这也正好证明它在推导。
        "PA13": pm.IocPin(pin="PA13", port="A", bit=13, mode="Serial_Wire",
                          signal="SYS_JTMS-SWDIO", label=""),
    }
    uses = [
        _use("PA1", "write"),      # 输入却在写 → 冲突
        _use("PA3", "write"),      # 正常
        _use("PA9", "write"),      # 硬件没配 → 幽灵
    ]
    return pins, {"uses": uses}


# ---------------------------------------------------------------------------
# 1. 四类都出行（含幽灵）
# ---------------------------------------------------------------------------


def check_rows() -> None:
    pins, extracted = _synthetic()
    payload = fm.matrix(pins, extracted["uses"], source="synthetic.ioc")

    pins_in_rows = [row["pin"] for row in payload["rows"]]
    check("1a. 幽灵引脚**出行**（它硬件里没配置，但在固件里被用了）",
          "PA9" in pins_in_rows,
          f"行里的脚：{pins_in_rows}")

    check("1b. 已配置的脚**每脚一行**（ok 的也在，不是只列问题）",
          {"PA1", "PA2", "PA3", "PA13"}.issubset(set(pins_in_rows)),
          f"缺：{ {'PA1','PA2','PA3','PA13'} - set(pins_in_rows) }")

    check("1c. 行数 = 配置脚数 + 幽灵脚数（一个不多一个不少）",
          payload["total"] == len(pins) + 1,
          f"total={payload['total']} 配置={len(pins)} 幽灵=1")

    counts = payload["counts"]
    check("1d. 四类计数之和 = 总行数",
          sum(counts.values()) == payload["total"],
          f"{counts} = {sum(counts.values())} / {payload['total']}")

    check("1e. 状态判定与 `crosscheck` 一致（本层只投影，不另判）",
          payload["status"] == ("fail" if counts["ghost"] or counts["direction"]
                                else "ok")
          and counts["ghost"] == 1 and counts["direction"] == 1,
          f"status={payload['status']} counts={counts}")


# ---------------------------------------------------------------------------
# 2. 调试脚不算闲置
# ---------------------------------------------------------------------------


def check_debug() -> None:
    pins, extracted = _synthetic()
    payload = fm.matrix(pins, extracted["uses"])
    row = next(r for r in payload["rows"] if r["pin"] == "PA13")
    check("2a. 调试脚（SWD）**不算闲置**（`crosscheck` 已排除，矩阵不能加回来）",
          row["status"] != "idle",
          f"PA13 status={row['status']}")

    idle_pins = [r["pin"] for r in payload["rows"] if r["status"] == "idle"]
    check("2b. 真闲置的脚仍然是 idle（排除调试脚不等于全都不报）",
          idle_pins == ["PA2"], f"idle={idle_pins}")


# ---------------------------------------------------------------------------
# 3. 空集合不能算通过
# ---------------------------------------------------------------------------


def check_empty() -> None:
    empty = fm.matrix({}, [], source="nothing")
    check("3a. 硬件侧一个配置都没有 → `not_ready`（**不是 ok**）",
          empty["status"] == "not_ready" and empty["rows"] == [],
          f"status={empty['status']} reason={str(empty.get('reason'))[:50]}")

    payload = fm.matrix({}, [_use("PA9", "write")], source="x")
    check("3b. 只有固件用法、没有硬件配置 → 仍然 `not_ready`（不假装比过）",
          payload["status"] == "not_ready",
          f"status={payload['status']}")


# ---------------------------------------------------------------------------
# 4. 真实 .ioc
# ---------------------------------------------------------------------------


def check_real() -> None:
    if not IOC.is_file():
        check("4a. 真 .ioc 上构建矩阵", False, f"缺 {IOC}", status=SKIP)
        return
    pins = pm.parse_pins(IOC)
    payload = fm.matrix(pins, [_use("PA5", "write")], source=IOC.name)

    check("4a. 真 .ioc：矩阵行数 = 配置脚 + 幽灵脚",
          payload["total"] == len(pins) + payload["counts"]["ghost"],
          f"total={payload['total']} 配置={len(pins)} "
          f"ghost={payload['counts']['ghost']}")

    check("4b. 真 .ioc 里配了但固件没用的脚被报成 idle（有据可查）",
          payload["counts"]["idle"] > 0,
          f"counts={payload['counts']}")

    # PA5 不在 F401-Front.ioc 的配置里 → 必须报幽灵
    check("4c. 往不存在的脚写 → ghost（这是唯一「运行时必炸」的一类）",
          any(r["pin"] == "PA5" and r["status"] == "ghost"
              for r in payload["rows"]),
          f"PA5 行：{[r['status'] for r in payload['rows'] if r['pin']=='PA5']}")

    check("4d. 行已按严重度排序（ghost 在最前，ok 在最后）",
          [fm.STATUS_ORDER.index(r["status"]) for r in payload["rows"]]
          == sorted(fm.STATUS_ORDER.index(r["status"]) for r in payload["rows"]),
          f"{[r['status'] for r in payload['rows'][:5]]}…")


# ---------------------------------------------------------------------------
# 5. 摘要与契约
# ---------------------------------------------------------------------------


def check_contract() -> None:
    pins, extracted = _synthetic()
    payload = fm.matrix(pins, extracted["uses"])
    summary = fm.summarize(payload)

    check("5a. 摘要里**只有有问题的行**（ok 行不进快照）",
          all(row["status"] != "ok" for row in summary["rows"])
          and summary["rows_total"] == len(
              [r for r in payload["rows"] if r["status"] != "ok"]),
          f"摘要 {len(summary['rows'])} 行 / 有问题 {summary['rows_total']} 行")

    check("5b. 摘要保留计数与总数（不因截断丢失信息）",
          summary["counts"] == payload["counts"]
          and summary["total"] == payload["total"],
          f"counts={summary['counts']} total={summary['total']}")

    # CLI：**不能撞名**——`firmware` 已经在 P20 被占用（那次撞名会把原命令顶掉）
    code, out = _run([sys.executable, "-m", "hw_velxio.design_cli", "firmware"])
    try:
        legacy = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        legacy = {}
    check("6a. `firmware` 仍是 P20 的固件源码检查（没被矩阵命令顶掉）",
          "columns" not in legacy,
          f"keys={sorted(legacy)[:5]}")

    code2, out2 = _run([sys.executable, "-m", "hw_velxio.design_cli", "fw-matrix"])
    try:
        matrix_desc = json.loads(out2.strip().splitlines()[-1])
    except (ValueError, IndexError):
        matrix_desc = {}
    check("6b. `fw-matrix` 是矩阵命令（describe 带 columns）",
          matrix_desc.get("status") == "ok"
          and bool(matrix_desc.get("columns")),
          f"columns={matrix_desc.get('columns')}")

    # 快照：要么带 firmware 摘要，要么如实 not_ready——不能一条都不写
    code3, out3 = _run([sys.executable, "-m", "hw_velxio.design_cli", "snapshot",
                        "001-blink"])
    try:
        snapshot = json.loads(out3.strip().splitlines()[-1])
    except (ValueError, IndexError):
        snapshot = {}
    fw = snapshot.get("firmware")
    # 「扫了 0 个文件」不能和「扫到了但没有引脚用法」长得一样（AGENTS §0.2）
    code_f, out_f = _run([
        sys.executable, "-m", "hw_velxio.design_cli", "fw-matrix", "001-blink",
        f"--ioc={IOC}", f"--src={IOC.parent / 'App'}"])
    try:
        scanned = json.loads(out_f.strip().splitlines()[-1])
    except (ValueError, IndexError):
        scanned = {}
    files = scanned.get("scanned")
    check("6c. 报告里带**扫了几个文件**（`files` 计数，不是猜的键名）",
          isinstance(files, int) and files > 0,
          f"scanned={files}（exit={code_f}）")

    check("6d. 快照里**一定有** `firmware` 字段（没有配置时是 not_ready + 原因）",
          isinstance(fw, dict) and str(fw.get("status")) in
          ("ok", "fail", "not_ready"),
          f"status={(fw or {}).get('status')} reason={str((fw or {}).get('reason'))[:50]}")

    store_src = (PKG / "src" / "client" / "store.ts").read_text(encoding="utf-8")
    panels = (PKG / "src" / "client" / "panels.tsx").read_text(encoding="utf-8")
    view = (PKG / "src" / "client" / "HwIdeView.tsx").read_text(encoding="utf-8")

    delivery = _delivery_sections(store_src)
    check("7a. 前端：`firmware` 是 delivery 组分区，标签「固件」",
          "firmware" in delivery and "firmware: '固件'" in store_src,
          f"delivery={delivery}")

    has_panel = "export function FirmwarePanel" in panels
    has_case = "case 'firmware':" in view
    check("7b. `FirmwarePanel` 存在，且 view 里有 case 'firmware'",
          has_panel and has_case, f"panel={has_panel} case={has_case}")

    check("7c. 面板上四类**各自可见**（幽灵/冲突/闲置/核对过）",
          all(marker in panels for marker in
              ("幽灵引脚", "方向冲突", "闲置", "核对过")),
          "四类标签都在")

    check("7d. not_ready 时面板说清「这不是全对」",
          "不是「全对」" in panels or "没有核对" in panels,
          "有降级说明")


def _run(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(  # noqa: S603
        argv, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=False, timeout=600)
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 6. 退化
# ---------------------------------------------------------------------------


def check_degradation() -> None:
    pins, extracted = _synthetic()

    # 退化：只遍历已配置脚（幽灵脚整行消失）——第一版的真实错误形状
    saved = fm.matrix

    def only_configured(pins_arg: Any, uses: list[dict[str, Any]],
                        *, source: str = "") -> dict[str, Any]:
        payload = saved(pins_arg, uses, source=source)
        payload["rows"] = [r for r in payload["rows"] if r["status"] != "ghost"]
        payload["total"] = len(payload["rows"])
        return payload

    try:
        payload = only_configured(pins, extracted["uses"])
    finally:
        fm.matrix = saved        # type: ignore[assignment]

    rows = [r["pin"] for r in payload["rows"]]
    check("8. 退化验证：只遍历已配置脚 → 幽灵脚整行消失（1a 会红）",
          "PA9" not in rows,
          f"退化后行里的脚：{rows}")

    check("8b. 正常实现里它是在的（对照组，证明 8 不是恒真）",
          "PA9" in [r["pin"] for r in saved(pins, extracted["uses"])["rows"]],
          "正常实现有 PA9")


# ---------------------------------------------------------------------------


def _delivery_sections(store_src: str) -> list[str]:
    """从 `SECTIONS.delivery = [...]` 里取分区名。

    **不要按字面量钉死整条数组**：加一个分区就会让别的验收脚本红，
    而"firmware 属于 delivery 组"这个意图没变（AGENTS §3.15 同源）。
    """
    anchor = "delivery: ["
    i = store_src.find(anchor)
    if i < 0:
        return []
    j = store_src.find("]", i)
    if j < 0:
        return []
    raw = store_src[i + len(anchor):j]
    return [part.strip().strip("'").strip('"')
            for part in raw.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    only = ""
    for item in args:
        if item.startswith("--only="):
            only = item.split("=", 1)[1]

    print("=== P4 验收：固件矩阵 ===\n")
    tmp = Path(tempfile.mkdtemp(prefix="hwv-p4-"))
    try:
        if only in ("", "rows"):
            check_rows()
        if only in ("", "debug"):
            check_debug()
        if only in ("", "empty"):
            check_empty()
        if only in ("", "real"):
            check_real()
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
    payload = {"status": status, "check": "P4", "passed": passed,
               "failed": failed, "skipped": skipped, "results": _RESULTS}
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False, default=str)
                            .encode("utf-8") + b"\n")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())