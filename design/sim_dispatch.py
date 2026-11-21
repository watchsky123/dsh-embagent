"""仿真域分派 —— 一个设计，多个物理域，各自的后端。

## 为什么需要分派层，而不是一个大 `run()`

不同域**回答的是不同问题**，而且**可用性不同**：

| 域 | 后端 | 现在能不能跑 |
|---|---|---|
| `electrical` | 本地 `electrical.run()` | 能（P0–P4 已交付） |
| `geometry` | 本地 `physical.run_checks()` | 能 |
| `kinematic` | MuJoCo（项目内 vendor/pylib） | 能（P16） |
| `manufacturing` | 立创EDA CLI | 能（本机实测），但属于 P17 |
| `thermal` / `emc` | **无** | 永远 `unknown` |

分派层的价值就是**把"没有模型"变成一等状态**：
`domains()` 返回每个域的 `available`，没有后端就 `unknown`，
不是 `pass`，也不是假装没这一项。

## 一条纪律：域之间不互相借用结论

电气域的 `pass` 不意味着运动域能跑。
分派层只**汇总**，不做跨域推理——跨域推理（"电气没问题所以机械也没问题"）
正是 RoboBuilder 那 15 个机器人失败的模式。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from .model import DesignModel

#: 域 → (中文名, 这个域回答什么问题)
DOMAINS: dict[str, tuple[str, str]] = {
    "electrical": ("电气", "电压/电流/压降/轨跌是否成立"),
    "geometry": ("几何", "零件是否重叠/越界/超出限高/走线可达"),
    "kinematic": ("运动", "装配体在重力/接触/驱动下会不会翻、能不能动"),
    "manufacturing": ("制造", "能不能做出来（DRC/DFM/装配）"),
    "thermal": ("热", "温升是否超限"),
    "emc": ("EMC", "辐射/传导是否合规"),
}

#: **永久无模型**的域。它们不是"待做"，是"我们没有这套模型"。
NO_MODEL = ("thermal", "emc")


@dataclass
class DomainStatus:
    domain: str
    label: str
    available: bool
    reason: str = ""
    backend: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"domain": self.domain, "label": self.label,
                "available": self.available, "reason": self.reason,
                "backend": self.backend}


def _probe_lceda() -> tuple[bool, str]:
    """制造域的后端在不在。

    **只探测 CLI 是否可执行，不假装发射层已实现**（那属 P17）。
    这可区分两种"不可用"：
      * 没装立创EDA → 用户要装
      * 装了但发射层没写 → 我们要写
    """
    import shutil
    from pathlib import Path

    env = os.environ.get("HWV_LCEDA", "").strip()
    candidates = [Path(env)] if env else []
    candidates += [
        Path("D:/IE/ES/PCB/lceda-pro/lceda-pro.exe"),
        Path("C:/Program Files/lceda-pro/lceda-pro.exe"),
        Path("C:/Program Files/easyeda-pro/easyeda-pro.exe"),
    ]
    for name in ("lceda-pro", "easyeda-pro"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for path in candidates:
        if path.is_file():
            return True, (f"找到 CLI：{path}。"
                          f"但**发射层尚未实现**（P17）——"
                          f"现在只能探测，不能发射")
    return False, "找不到立创EDA 专业版 CLI（设 HWV_LCEDA 指向可执行文件）"


def domains() -> list[DomainStatus]:
    """每个域的可用性。**先问"有没有模型"，再问"跑不跑得动"。**"""
    out: list[DomainStatus] = []

    out.append(DomainStatus("electrical", DOMAINS["electrical"][0], True,
                            backend="electrical.run"))
    out.append(DomainStatus("geometry", DOMAINS["geometry"][0], True,
                            backend="physical.run_checks"))

    from . import mujoco_backend
    ok, why = mujoco_backend.available()
    out.append(DomainStatus("kinematic", DOMAINS["kinematic"][0], ok, why,
                            backend="mujoco 3.12 (vendor/pylib)"))

    ok2, why2 = _probe_lceda()
    out.append(DomainStatus("manufacturing", DOMAINS["manufacturing"][0], ok2,
                            why2, backend="lceda-pro CLI"))

    for name in NO_MODEL:
        out.append(DomainStatus(
            name, DOMAINS[name][0], False,
            f"**没有 {DOMAINS[name][0]} 模型**——这不是待做，是做不到",
            backend=""))
    return out


def run_domain(design: DesignModel, domain: str, *,
               params: Any = None, **kw) -> dict[str, Any]:
    """跑一个域。返回统一形状：

    `{"domain", "available", "status", "result"|"reason"}`

    状态三态：`ok` / `unknown`（没模型或没跑起来）/ `failed`（跑出错误结论）。
    **`unknown` 不是 `ok`。**
    """
    if domain not in DOMAINS:
        raise ValueError(f"未知域 {domain!r}（可用：{', '.join(DOMAINS)}）")

    if domain in NO_MODEL:
        return {"domain": domain, "available": False, "status": "unknown",
                "reason": (f"没有 {DOMAINS[domain][0]} 模型——"
                           f"这项只能实测，不能仿真")}

    if domain == "electrical":
        from . import electrical
        report = electrical.run(design).to_dict()
        return {"domain": domain, "available": True,
                "status": ("ok" if report.get("ok") else "failed"),
                "result": report}

    if domain == "geometry":
        from . import physical
        report = physical.run_checks(design)
        failed = int((report.get("summary") or {}).get("failed") or 0)
        return {"domain": domain, "available": True,
                "status": ("ok" if not failed else "failed"),
                "result": report}

    if domain == "kinematic":
        from . import mujoco_backend
        ok, why = mujoco_backend.available()
        if not ok:
            return {"domain": domain, "available": False,
                    "status": "unknown", "reason": why}
        duration = float(kw.pop("duration_s", 3.0))
        free = bool(kw.pop("free_bodies", True))
        result = mujoco_backend.run(design, duration_s=duration,
                                    params=params, free_bodies=free, **kw)
        if not result.ok:
            return {"domain": domain, "available": True,
                    "status": "unknown",           # 没跑起来 ≠ 通过
                    "reason": result.error,
                    "result": result.to_dict()}
        return {"domain": domain, "available": True, "status": "ok",
                "result": result.to_dict()}

    if domain == "manufacturing":
        return {"domain": domain, "available": False, "status": "unknown",
                "reason": ("制造域要靠立创EDA 的 DRC，属 P17；"
                           "现在没有后端，不假装")}

    return {"domain": domain, "available": False, "status": "unknown",
            "reason": "未实现"}                     # pragma: no cover


def report(design: DesignModel, *,
           only: tuple[str, ...] = ("electrical", "geometry", "kinematic")
           ) -> dict[str, Any]:
    """把几个域跑一遍，汇总。**不跨域推理。**"""
    rows: list[dict[str, Any]] = []
    for status in domains():
        if status.domain not in only:
            continue
        row = run_domain(design, status.domain)
        row["label"] = status.label
        rows.append(row)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    # 可用但没跑的域要显式列出——否则"没跑"看起来像"没问题"
    not_run = [s.domain for s in domains()
               if s.available and s.domain not in only]

    return {
        "ok": all(r["status"] == "ok" for r in rows) and bool(rows),
        "domains": rows,
        "counts": counts,
        "not_run": not_run,
        "no_model": [s.domain for s in domains() if not s.available],
        "summary": _summary(rows, counts, not_run),
    }


def _summary(rows: list[dict[str, Any]], counts: dict[str, int],
             not_run: list[str]) -> str:
    parts = [f"{r['label']} {r['status']}" for r in rows]
    text = "；".join(parts)
    if not_run:
        text += f"。**{len(not_run)} 个可用域没跑**（{', '.join(not_run)}）"
    if counts.get("unknown"):
        text += f"。{counts['unknown']} 个域是 unknown（不是通过）"
    return text + "。"


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.sim_dispatch",
        "domains": {k: v[0] for k, v in DOMAINS.items()},
        "no_model": list(NO_MODEL),
        "discipline": ("没有模型的域返回 unknown；域之间不互相借用结论；"
                       "可用但没跑的域单独列出"),
    }