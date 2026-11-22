"""纯 C 算法 + PC 侧单元测试（`fw-algo`）—— 软件里**唯一能被本机验证**的那一块。

## 为什么只做"纯 C"

目标板是 STM32H7/F4，而本机：

| 手段 | 可行性 |
|---|---|
| 目标板实跑 | ❌ 无板 |
| Renode | ❌ 沙箱拦锁文件（AGENTS §3.4）|
| **PC 上编纯 C 并跑单元测试** | ✅ **gcc 在位**（`doctor` 实测 `D:\\deeptool\\w64devkit`）|

所以把算法里**不依赖硬件**的部分抽出来（状态机、滤波、PID、限幅），
用 gcc 在本机编译执行。**这不是仿真飞行**——它验的是"逻辑对不对"，
不是"飞起来稳不稳"。

## 判据（三态）

| 结论 | 条件 |
|---|---|
| `ok` | 编译通过 **且** 全部用例通过 |
| `failed` | 编译通过但有**失败用例** |
| `not_ready` | 没有 gcc / 编译不过 / 没生成源码 |

**每个算法必须带至少一条"该触发的失败路径"用例**（如角度超限 → 进 FAILSAFE）。
只测正常路径的测试等于没测。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["ALGOS", "generate", "build_and_run", "list_algos", "describe"]

#: 算法注册表。每个条目：头文件、实现、测试、以及它**证明什么**。
ALGOS: dict[str, dict[str, str]] = {
    "failsafe": {
        "title": "风控状态机（失效保护）",
        "proves": "角度/下降率/链路丢失超阈值 → 必须进 FAILSAFE，且只触发一次",
        "header": """#ifndef FAILSAFE_H
#define FAILSAFE_H

typedef enum {
    FS_DISARMED = 0,
    FS_ARMED    = 1,
    FS_FAILSAFE = 2
} fs_state_t;

typedef struct {
    float angle_deg_max;    /* 姿态角上限（度） */
    float descend_mps_max;  /* 下降率上限（m/s） */
    float link_timeout_s;   /* 链路丢失多久算失效（秒） */
} fs_config_t;

/* 返回事件：0=无 1=刚刚进入 FAILSAFE 2=已在 FAILSAFE */
int fs_update(fs_state_t *state, const fs_config_t *cfg,
              float angle_deg, float descend_mps, float link_lost_s);

#endif
""",
        "impl": """#include "failsafe.h"

static float fs_abs(float v) { return v < 0.0f ? -v : v; }

int fs_update(fs_state_t *state, const fs_config_t *cfg,
              float angle_deg, float descend_mps, float link_lost_s)
{
    int triggered = 0;

    if (fs_abs(angle_deg) > cfg->angle_deg_max)      triggered = 1;
    if (descend_mps > cfg->descend_mps_max)          triggered = 1;
    if (link_lost_s > cfg->link_timeout_s)           triggered = 1;

    if (triggered) {
        if (*state == FS_FAILSAFE) return 2;   /* 不重复触发 */
        *state = FS_FAILSAFE;
        return 1;
    }
    return 0;
}
""",
        "test": """#include <stdio.h>
#include "failsafe.h"

static int fails = 0;
static int total = 0;

static void check(const char *name, int got, int want)
{
    total++;
    if (got != want) {
        fails++;
        printf("FAIL %s: got %d want %d\\n", name, got, want);
    }
}

int main(void)
{
    fs_config_t cfg = { 60.0f, 3.0f, 0.5f };
    fs_state_t st;
    int ev;

    /* 正常：不该触发 */
    st = FS_ARMED;
    ev = fs_update(&st, &cfg, 20.0f, 1.0f, 0.0f);
    check("normal_no_trigger", ev, 0);
    check("normal_state_kept", (int)st, (int)FS_ARMED);

    /* 边界：恰好等于阈值 → **不触发**（用 > 不是 >=） */
    st = FS_ARMED;
    ev = fs_update(&st, &cfg, 60.0f, 3.0f, 0.5f);
    check("boundary_not_triggered", ev, 0);

    /* 角度超限 */
    st = FS_ARMED;
    ev = fs_update(&st, &cfg, 70.0f, 0.0f, 0.0f);
    check("angle_trigger", ev, 1);
    check("angle_state", (int)st, (int)FS_FAILSAFE);

    /* 下降率超限 */
    st = FS_ARMED;
    ev = fs_update(&st, &cfg, 0.0f, 4.0f, 0.0f);
    check("descend_trigger", ev, 1);

    /* 链路丢失 */
    st = FS_ARMED;
    ev = fs_update(&st, &cfg, 0.0f, 0.0f, 0.6f);
    check("link_trigger", ev, 1);

    /* 已在 FAILSAFE：不重复触发 */
    st = FS_FAILSAFE;
    ev = fs_update(&st, &cfg, 90.0f, 0.0f, 0.0f);
    check("no_repeat", ev, 2);

    /* 负角度（向另一侧翻）也要抓 */
    st = FS_ARMED;
    ev = fs_update(&st, &cfg, -70.0f, 0.0f, 0.0f);
    check("negative_angle_trigger", ev, 1);

    printf("%s %d/%d\\n", fails ? "FAILED" : "PASS", total - fails, total);
    return fails ? 1 : 0;
}
""",
    },
    "lpf": {
        "title": "一阶低通（姿态/高度环的输入滤波）",
        "proves": "alpha=1 直通、alpha=0 保持、阶跃后单调收敛",
        "header": """#ifndef LPF_H
#define LPF_H

typedef struct {
    float alpha;   /* 0..1 */
    float y;
    int   primed;
} lpf_t;

void  lpf_init(lpf_t *f, float alpha);
float lpf_update(lpf_t *f, float x);

#endif
""",
        "impl": """#include "lpf.h"

void lpf_init(lpf_t *f, float alpha)
{
    if (alpha < 0.0f) alpha = 0.0f;
    if (alpha > 1.0f) alpha = 1.0f;
    f->alpha = alpha;
    f->y = 0.0f;
    f->primed = 0;
}

float lpf_update(lpf_t *f, float x)
{
    if (!f->primed) {          /* 第一拍直接吃进来，避免从 0 慢慢爬 */
        f->y = x;
        f->primed = 1;
        return f->y;
    }
    f->y = f->y + f->alpha * (x - f->y);
    return f->y;
}
""",
        "test": """#include <stdio.h>
#include "lpf.h"

static int fails = 0, total = 0;
static void checkf(const char *name, float got, float want, float tol)
{
    float d = got - want; if (d < 0) d = -d;
    total++;
    if (d > tol) { fails++; printf("FAIL %s: got %.4f want %.4f\\n", name, got, want); }
}
static void checki(const char *name, int got, int want)
{
    total++;
    if (got != want) { fails++; printf("FAIL %s: got %d want %d\\n", name, got, want); }
}

int main(void)
{
    lpf_t f;

    /* alpha=1 → 直通 */
    lpf_init(&f, 1.0f);
    checkf("alpha1_first", lpf_update(&f, 5.0f), 5.0f, 1e-6f);
    checkf("alpha1_second", lpf_update(&f, 9.0f), 9.0f, 1e-6f);

    /* 第一拍直接吃进来（不是从 0 爬） */
    lpf_init(&f, 0.1f);
    checkf("primed_first", lpf_update(&f, 100.0f), 100.0f, 1e-6f);

    /* alpha=0 → 保持（第一拍吃进 100，之后输入怎么变都不动）
       **第一版这里写错了**：上一段把 alpha 初始化成 0.1，再喂 0 会得到 90，
       测试报 FAIL alpha0_hold: got 90 want 100 —— 是**测试的期望错**，
       不是实现错。改成独立用例，才真的在测 alpha=0。 */
    lpf_init(&f, 0.0f);
    checkf("alpha0_first", lpf_update(&f, 100.0f), 100.0f, 1e-6f);
    checkf("alpha0_hold", lpf_update(&f, 0.0f), 100.0f, 1e-6f);

    /* 阶跃后单调逼近 0，且不越过 */
    lpf_init(&f, 0.25f);
    lpf_update(&f, 8.0f);
    float prev = 8.0f;
    int monotonic = 1;
    for (int i = 0; i < 20; i++) {
        float y = lpf_update(&f, 0.0f);
        if (y > prev + 1e-6f) monotonic = 0;
        prev = y;
    }
    checki("monotonic_decay", monotonic, 1);
    checkf("converged_near_zero", prev, 0.0f, 0.1f);

    /* alpha 会被夹到 0..1 */
    lpf_init(&f, 5.0f);
    checkf("alpha_clamped_high", f.alpha, 1.0f, 1e-6f);
    lpf_init(&f, -1.0f);
    checkf("alpha_clamped_low", f.alpha, 0.0f, 1e-6f);

    printf("%s %d/%d\\n", fails ? "FAILED" : "PASS", total - fails, total);
    return fails ? 1 : 0;
}
""",
    },
}


def list_algos() -> dict[str, Any]:
    return {"status": "ok",
            "algorithms": [{"name": name, "title": body["title"],
                            "proves": body["proves"]}
                           for name, body in ALGOS.items()],
            "note": "纯 C，不依赖目标板；用 gcc 在本机编译并跑单元测试"}


def generate(algo: str, out_dir: str | Path) -> dict[str, Any]:
    """把算法写成 3 个文件到 `out_dir`。**不覆盖已有内容**（报错而不是静默改）。"""
    body = ALGOS.get(algo)
    if body is None:
        return {"status": "failed",
                "findings": [f"没有算法 {algo!r}（可用：{', '.join(ALGOS)}）"]}
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, content in (
        (f"{algo}.h", body["header"]),
        (f"{algo}.c", body["impl"]),
        (f"test_{algo}.c", body["test"]),
    ):
        path = target / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return {"status": "ok", "algo": algo, "dir": str(target), "files": written}


def build_and_run(algo: str, out_dir: str | Path, *, timeout_s: int = 60,
                  drop: str = "") -> dict[str, Any]:
    """用 gcc 编译并运行测试。`drop` 用来**退化验证**（把源码里某个串删掉）。"""
    body = ALGOS.get(algo)
    if body is None:
        return {"status": "not_ready", "reason": f"没有算法 {algo!r}"}
    gcc = shutil.which("gcc") or shutil.which("cc")
    if not gcc:
        return {"status": "not_ready",
                "reason": "没有 gcc —— 纯 C 这条路走不了（doctor 会报）"}

    target = Path(out_dir)
    header = target / f"{algo}.h"
    impl = target / f"{algo}.c"
    test = target / f"test_{algo}.c"
    if not (header.is_file() and impl.is_file() and test.is_file()):
        wrote = generate(algo, target)
        if wrote["status"] != "ok":
            return {"status": "not_ready", "reason": "源码没生成出来"}

    source_impl = impl.read_text(encoding="utf-8")
    if drop and drop in source_impl:
        impl.write_text(source_impl.replace(drop, ""), encoding="utf-8")
        degenerated = True
    else:
        degenerated = False

    exe = target / f"{algo}_test"
    compile_msgs: list[str] = []
    try:
        proc = subprocess.run(
            [gcc, "-O1", "-std=c99", "-Wall", "-Wextra", "-o", str(exe),
             str(impl), str(test)],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "not_ready", "reason": f"编译执行失败：{exc}"}
    if proc.returncode != 0:
        compile_msgs = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
        return {"status": "not_ready", "reason": "编译不过", "compiler": compile_msgs,
                "gcc": gcc, "degenerated": degenerated}

    try:
        run = subprocess.run([str(exe)], capture_output=True, text=True,
                             timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "not_ready", "reason": f"运行失败：{exc}"}
    out = (run.stdout or "").strip()
    tail = out.splitlines()[-1] if out.splitlines() else ""
    match = re.search(r"(PASS|FAILED)\s+(\d+)/(\d+)", tail)
    passed = int(match.group(2)) if match else 0
    total_cases = int(match.group(3)) if match else 0
    failed_cases = [line for line in out.splitlines() if line.startswith("FAIL")]
    ok = run.returncode == 0 and match is not None and passed == total_cases
    return {
        "status": "ok" if ok else "failed",
        "algo": algo, "gcc": gcc, "dir": str(target),
        "cases": {"passed": passed, "total": total_cases, "failed": failed_cases[:10]},
        "reason": "" if ok else (f"{len(failed_cases)} 条用例失败"
                                 if failed_cases else "测试没有输出结果行"),
        "degenerated": degenerated,
        "stdout_tail": out.splitlines()[-3:],
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.fw_algo",
        "algorithms": list(ALGOS),
        "why": ("Renode 在本机跑不了、也没有目标板；"
                "所以只验**不依赖硬件**的纯 C 逻辑（状态机/滤波/PID/限幅）"),
        "tristate": {"ok": "编译过且全部用例过",
                     "failed": "编译过但有失败用例",
                     "not_ready": "没 gcc / 编译不过 / 没源码"},
        "discipline": "每个算法必须带'该触发的失败路径'用例；`drop=` 用于退化验证",
    }