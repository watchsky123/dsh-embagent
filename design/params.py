"""飞控参数 → 能力映射（`params`）—— 把"黑盒参数集"变成**可判的证据**。

## 这一层解决什么问题

P49 的 `plan` 对软件能力（S1–S6）一律 `unknown`，理由写着"增益来源不明"。
而工作区里其实**躺着真证据**：

| 文件 | 是什么 | 实测 |
|---|---|---|
| `config_20261021_102258.param` | 飞控导出的当前参数 | **48 个**（`pid_*` 40 / `alt_*` 2 / `cal_*` 3 / `sys_*` `imu_*` 各 1）|
| `H743PilotX-master/App/Inc/Config.h` | `MF_PARAM_LIST` 参数表（**带中文注释**）| 240+ 条，注释就是语义 |

在此之前这些**一个都没被读过**。`params` 做三件事：

1. 解析两种**真实格式**（`key=value` 的 `.param`；`MF_PARAM(name, def, type, 'X') /*注释*/`）
2. 把参数**按族**映射到能力，每条映射带 `basis`（凭什么这么映射）
3. 算**族完整度**：某能力需要的参数**齐不齐**（这是唯一能判的东西）

## 纪律（三条）

1. **参数存在 ≠ 能力达标**。有 `pid_in_roll_p` 只说明"增益有值"，不说明"角速率环稳定"
   —— 所以参数只进 `evidence`，**能力状态仍是 `unknown`**（`plan` 的判据不受影响）。
2. **映射必须有 `basis`**：写不出"这个参数为什么属于这条能力"就不映射，进 `unmapped`。
3. **缺必需参数 = `missing`**，不是"没问题"。`readiness` 只报 `ready` / `partial` / `absent`。

## 判据

| 结论 | 条件 |
|---|---|
| `ok` | 所有涉及的族都 `ready`（必需项齐全） |
| `not_ready` | 至少一族 `partial`/`absent` —— **有缺口就要说** |
| `failed` | 同一参数在两个来源里值冲突（说明哪份是真的没说清） |
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

__all__ = ["FAMILIES", "parse_param_file", "parse_mf_param", "scan", "build",
           "render", "describe", "FILE"]

FILE = "params.json"

#: 参数族 → (能力, 它在能力里的角色, **为什么这么映射**)。
#: 名字前缀来自真实数据（`config_20261021_102258.param` + `Config.h`），
#: 不是我编的分类。
FAMILIES: dict[str, dict[str, str]] = {
    "pid_in": {
        "capability": "S2", "what": "角速率内环 PID（P/I/D/I 限幅）",
        "basis": "飞控内环增益直接决定角速率环带宽；H2 的 1 kHz 判据靠它实现",
    },
    "pid_ou": {
        "capability": "S2", "what": "姿态外环 PID（角度→角速率给定）",
        "basis": "外环把期望角度转成角速率给定，串在外环",
    },
    "pid_yaw": {
        "capability": "S2", "what": "偏航角速率环 PID",
        "basis": "偏航单独一套增益（与 roll/pitch 不同轴）",
    },
    "pid_yaw_rate": {
        "capability": "S2", "what": "偏航角速率内环 PID",
        "basis": "偏航内环，与 `pid_yaw` 串联",
    },
    "pid_pmw": {
        "capability": "S5", "what": "位置/航向模式 PID",
        "basis": "`pmw` 是位置模式通道（航点/悬停位置环）",
    },
    "pid_gps": {
        "capability": "S5", "what": "GPS 位置环 PID",
        "basis": "GPS 位置误差 → 速度/位置给定",
    },
    "alt": {
        "capability": "H3", "what": "定高：悬停油门与高度滤波",
        "basis": "`alt_hover_thr` 给悬停基线油门、`alt_thr_lp` 滤高度噪声",
    },
    "sys": {
        "capability": "S1", "what": "系统模式（手动/油门居中）",
        "basis": "`sys_fly_mode` 决定有无自动稳定环 → 决定风控怎么接管",
    },
    "imu": {
        "capability": "S3", "what": "IMU 校准与零偏",
        "basis": "零偏不校，融合出来的姿态就是错的",
    },
    "cal": {
        "capability": "S3", "what": "姿态角校准偏差",
        "basis": "安装角偏差进融合前必须补掉",
    },
    "mag": {
        "capability": "S3", "what": "磁罗盘校准",
        "basis": "偏航观测来源",
    },
    "acc": {
        "capability": "S3", "what": "加速度计低通/量程",
        "basis": "融合的观测输入之一；`acc_*_alpha` 是它的低通系数",
    },
    "gyr": {
        "capability": "S3", "what": "陀螺低通/量程",
        "basis": "角速率观测输入；低通系数直接影响 S2 的环带宽上限",
    },
    "bar": {
        "capability": "H3", "what": "气压计总线/速率/滤波",
        "basis": "定高的高度观测来源（`alt_*` 是控制侧，`bar_*` 是观测侧）",
    },
    "bat": {
        "capability": "H8", "what": "电池电压/电流标定",
        "basis": "`bat_cal_v`/`bat_cal_i` 是分压比与电流系数，低压保护门限靠它",
    },
    "pid_acc": {
        "capability": "H3", "what": "高度/垂速加速度环 PID",
        "basis": "`pid_acc_alt_*` 是定高的内环（加速度→油门）",
    },
}

#: 每条能力的**必需参数**（真名，取自真实参数集）。
#: 判据：缺一个就是 `partial`，一个都没有是 `absent`。
REQUIRED: dict[str, tuple[str, ...]] = {
    "S2": (
        "pid_in_roll_p", "pid_in_roll_i", "pid_in_roll_d",
        "pid_in_pitch_p", "pid_in_pitch_i", "pid_in_pitch_d",
        "pid_ou_roll_p", "pid_ou_pitch_p",
        "pid_yaw_p", "pid_yaw_i",
    ),
    "S3": ("imu_cal_gx", "imu_cal_gy", "imu_cal_gz", "cal_roll", "cal_pitch"),
    "S1": ("sys_fly_mode",),
    "H3": ("alt_hover_thr",),
    "S5": ("pid_gps_p",),
    "H8": ("bat_cal_v", "bat_cal_i"),
}

#: **已知缺口**：这几条能力的参数在本工作区**根本不存在**（不是没解析到）。
#: 写在这里而不是"猜一个默认值"，是为了让报告能说"缺的是失效判据本身"。
KNOWN_ABSENT: dict[str, str] = {
    "S1": "失效判据参数（角度阈值 / 下降率阈值 / 链路超时）在参数集里**一条都没有**",
    "H4": "光流/视觉参数未进参数集",
    "H5": "GNSS 参数（TTFF/星数门限）未进参数集",
}

_PARAM_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([-+0-9.eE]+)\s*$")
#: `MF_PARAM(name, default, type, 'flag'[, 枚举项...]) /*注释*/`
#: 实测有两种：普通参数 4 个字段；**枚举参数**第 5 段是枚举列表
#: （`'e', mf_MAHONY, mf_MAHONY_BF`）—— 第一版正则只认 4 个字段，
#: 于是 2 条枚举参数被记成"不完整"（不是解析失败，是我的形状假设太窄）。
_MF_PARAM = re.compile(
    r"MF_PARAM\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([^,]+)\s*,\s*([A-Za-z0-9_]+)"
    r"\s*,\s*'([^']*)'(?:\s*,\s*([^)]*))?\s*\)\s*(?:/\*\s*([^*]*?)\s*\*/)?")


def _family_of(name: str) -> str | None:
    """参数名 → 族。**最长前缀优先**（`pid_yaw_rate` 不能落进 `pid_yaw`）。"""
    best: str | None = None
    for family in FAMILIES:
        if name == family or name.startswith(family + "_") or name.startswith(family):
            if best is None or len(family) > len(best):
                best = family
    return best


def parse_param_file(path: str | Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """解析 `name=value` 的 `.param`（**实测格式**：一行一个，`#` 注释）。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    out: dict[str, dict[str, Any]] = {}
    bad: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _PARAM_LINE.match(line)
        if not match:
            bad.append({"file": str(path), "line": number, "text": stripped[:60],
                        "why": "不是 name=value 形状"})
            continue
        name, raw = match.group(1), match.group(2)
        try:
            value = float(raw)
        except ValueError:
            bad.append({"file": str(path), "line": number, "text": stripped[:60],
                        "why": "值不是数"})
            continue
        out[name] = {"name": name, "value": value, "source": "param",
                     "file": str(path), "line": number, "comment": ""}
    return out, bad


def parse_mf_param(path: str | Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """解析 `MF_PARAM(name, default, type, 'x') /*注释*/`（`Config.h` 的参数表）。

    注释是**语义来源**（如 `/*飞行模式 0=手动 1=油门居中悬停*/`），照抄进 `comment`。
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    out: dict[str, dict[str, Any]] = {}
    bad: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        # 跳过**宏定义**和列表名本身：`#define MF_PARAM(...)` 里也有 `MF_PARAM(`
        # —— 第一版把它当参数条目，于是 2 行被记成"不完整"（§0.2 的"空表"变体：
        # 报的是"解析失败"，其实是"这行根本不是参数"）。
        stripped = line.lstrip()
        if (stripped.startswith("#define") or stripped.startswith("#undef")
                or "MF_PARAM(" not in line or "MF_PARAM_LIST" in line):
            continue
        match = _MF_PARAM.search(line)
        if not match:
            bad.append({"file": str(path), "line": number, "text": line.strip()[:60],
                        "why": "MF_PARAM 参数不完整"})
            continue
        name, raw, _ctype, _flag, enums, comment = match.groups()
        try:
            value = float(str(raw).strip().rstrip("f"))
        except ValueError:
            value = None
        out[name] = {"name": name, "value": value, "source": "config_table",
                     "file": str(path), "line": number,
                     "comment": (comment or "").strip(),
                     "enums": [e.strip() for e in (enums or "").split(",") if e.strip()]}
    return out, bad


def scan(roots: list[str | Path]) -> dict[str, Any]:
    """在若干目录/文件里找参数。**两种格式都认，只读不猜。**"""
    files: list[Path] = []
    for raw in roots:
        root = Path(raw)
        if root.is_file():
            files.append(root)
            continue
        if not root.is_dir():
            continue
        files.extend(sorted(root.glob("*.param")))
        files.extend(sorted(root.glob("*.params")))
        files.extend(sorted(root.glob("**/Config.h"))[:4])
        files.extend(sorted(root.glob("**/*param*.h"))[:4])

    merged: dict[str, dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    for path in files:
        if path.suffix.lower() in (".param", ".params"):
            found, bad = parse_param_file(path)
            kind = "param"
        elif path.name.lower().endswith(".h"):
            found, bad = parse_mf_param(path)
            kind = "config_table"
        else:
            continue
        problems.extend(bad)
        sources.append({"file": str(path), "kind": kind, "count": len(found)})
        for name, row in found.items():
            previous = merged.get(name)
            if previous is not None and kind != "config_table":
                # 两个"当前值"来源冲突 → 记下来（不是覆盖）
                if (previous["source"] == kind
                        and previous["value"] is not None
                        and row["value"] is not None
                        and abs(previous["value"] - row["value"]) > 1e-9):
                    problems.append({"file": str(path), "line": row["line"],
                                     "text": name, "why": (
                                         f"与 {previous['file']} 的值冲突："
                                         f"{row['value']} vs {previous['value']}")})
                    continue
            if previous is None or kind != "config_table":
                merged[name] = row
    return {"params": merged, "sources": sources, "problems": problems,
            "files_scanned": len(files)}


def _readiness(capability: str, params: dict[str, dict[str, Any]]) -> dict[str, Any]:
    need = REQUIRED.get(capability) or ()
    if not need:
        return {"capability": capability, "status": "absent", "have": 0, "need": 0,
                "missing": [], "note": "没有为该能力登记必需参数"}
    have = [n for n in need if n in params]
    missing = [n for n in need if n not in params]
    status = "ready" if not missing else ("absent" if not have else "partial")
    return {"capability": capability, "status": status, "have": len(have),
            "need": len(need), "missing": missing,
            "note": "" if status == "ready" else (
                "缺 " + "、".join(missing) if status == "partial"
                else "必需参数一个都没有")}


def build(*, roots: list[str | Path], requirement: dict[str, Any] | None = None) -> dict[str, Any]:
    """扫描 + 映射 + 族完整度。**不修改任何能力状态**（那归 `plan`）。"""
    scanned = scan(roots)
    params = scanned["params"]

    families: dict[str, list[str]] = {}
    mapped: list[dict[str, Any]] = []
    unmapped: list[str] = []
    for name in sorted(params):
        family = _family_of(name)
        if family is None:
            unmapped.append(name)
            continue
        meta = FAMILIES[family]
        families.setdefault(family, []).append(name)
        mapped.append({"param": name, "family": family,
                       "capability": meta["capability"],
                       "what": meta["what"], "basis": meta["basis"],
                       "value": params[name]["value"],
                       "comment": params[name]["comment"]})

    capabilities = sorted({row["capability"] for row in mapped})
    readiness = [_readiness(cap, params) for cap in capabilities]
    absent_known = [{"capability": cap, "why": why}
                    for cap, why in KNOWN_ABSENT.items() if cap not in capabilities]

    bad = [r for r in readiness if r["status"] != "ready"]
    conflicts = [p for p in scanned["problems"] if "冲突" in str(p.get("why"))]
    if conflicts:
        status = "failed"
    elif not readiness or bad or absent_known:
        # `absent_known`（参数集里根本没有这条能力的参数）也是缺口：
        # 报 ok 等于说"没参数的能力也没问题"，那是 §0.1。
        status = "not_ready"
    else:
        status = "ok"

    return {
        "status": status,
        "counts": {
            "params": len(params),
            "mapped": len(mapped),
            "unmapped": len(unmapped),
            "families": len(families),
            "capabilities": len(capabilities),
            "ready": sum(1 for r in readiness if r["status"] == "ready"),
            "not_ready": len(bad),
        },
        "sources": scanned["sources"],
        "mapped": mapped,
        "families": {k: {"count": len(v), "params": v[:8]} for k, v in sorted(families.items())},
        "readiness": readiness,
        "known_absent": absent_known,
        "unmapped": unmapped,
        "problems": scanned["problems"],
        "note": ("参数只提供**证据**：它说明「增益有值」，不说明「环稳定」。"
                 "能力状态仍由 `plan` 按判据定（仍是 unknown），这里只报族完整度。"),
    }


def render(payload: dict[str, Any]) -> str:
    counts = payload.get("counts") or {}
    lines = ["# 飞控参数 → 能力映射", "", f"> {payload.get('note', '')}", "",
             f"参数 {counts.get('params')}（映射 {counts.get('mapped')} / "
             f"未映射 {counts.get('unmapped')}），族 {counts.get('families')}，"
             f"涉及能力 {counts.get('capabilities')}（族齐 {counts.get('ready')}）", "",
             "## 来源", ""]
    for row in payload.get("sources") or []:
        lines.append(f"* `{row['file']}`（{row['kind']}）：{row['count']} 条")
    lines += ["", "## 族完整度（**这是唯一能判的**）", "",
              "| 能力 | 状态 | 有/需 | 缺什么 |", "|---|---|---|---|"]
    for row in payload.get("readiness") or []:
        lines.append(f"| {row['capability']} | {row['status']} | "
                     f"{row['have']}/{row['need']} | {'、'.join(row['missing']) or '—'} |")
    if payload.get("known_absent"):
        lines += ["", "## 已知缺口（参数集里根本不存在）", ""]
        for row in payload["known_absent"]:
            lines.append(f"* **{row['capability']}**：{row['why']}")
    if payload.get("unmapped"):
        lines += ["", "## 未映射（**不硬塞**）", "",
                  ", ".join(f"`{n}`" for n in payload["unmapped"])]
    return "\n".join(lines) + "\n"


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.params",
        "file": FILE,
        "formats": ["key=value（.param）", "MF_PARAM(name, def, type, 'x') /*注释*/"],
        "families": sorted(FAMILIES),
        "required": {k: len(v) for k, v in REQUIRED.items()},
        "discipline": ("参数只进 evidence，**不改能力状态**；映射必须有 basis；"
                       "缺必需参数 = missing（不是通过）"),
    }