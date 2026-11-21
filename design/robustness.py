"""鲁棒性判定 —— 一次通过不算通过，三次扰动都过才算。

## 为什么需要这一层（RoboBuilder 的实证）

RoboBuilder 的判定是：

    通过 = 从标称初始条件过 ∧ 偏移 1mm 过 ∧ 延后 50ms 过 ∧ 到达后立住 5s

**单次通过可能靠运气。** 一个机器人以一种精确的初始位姿冲过去，
换个 1mm 就卡住 —— 那不是"设计成功"，是"这次凑巧"。

这一点它的论文有实测支撑：15 个仿真通过的设计，真机全灭
（`tyres slipped, joints pulled apart, gears walked out of mesh`）。
**仿真里的"过"如果不带扰动，等于在优化一个特例。**

## 三态，不是二态

每次扰动返回 `pass` / `fail` / `unknown`。合并规则：

| 组合 | 结果 | 为什么 |
|---|---|---|
| 三次全 pass | **pass** | 唯一能说"稳"的情况 |
| 任一 fail | **fail** | 已经证明不稳 |
| 无 fail 但有 unknown | **unknown** | 没跑的部分不能当通过 |
| 全 unknown | unknown | 完全没结论 |

**"任一次 unknown 就整体 unknown"是这条协议的命门。**
如果 unknown 被当作 pass，那么"仿真没跑起来"会被算成"通过" ——
这正是 AGENTS §0.1 那个病的另一张面孔。

## 按域适配，不照搬数值

它的 1mm / 50ms 是**乐高小车**的尺度。我们按域给：

| 域 | 扰动 | 依据 |
|---|---|---|
| 几何 V2 | 公差 ±0.1mm | 常规 PCB/装配公差 |
| 电气 V3 | 轨压 ±5% / 负载阶跃 / 温度 ±10℃ | 电源容差 |
| 运动 V7 | 初始位姿 ±1mm / 时序 +50ms / 质量 ±5% | 沿用它的尺度（同类场景） |

这些值**不是物理常量，是判定口径**，所以放在可配的 dict 里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

#: 合并结果的取值
STATUSES = ("pass", "fail", "unknown")

#: 域 → 扰动清单。`kind` 是给调用方看的"该怎么施加"。
#:
#: **不照搬 RoboBuilder 的 1mm/50ms 到所有域**：那是地面小车的尺度。
#: 电气的 ±5% 轨压是电源容差，跟毫米无关。
PERTURBATIONS: dict[str, tuple[dict[str, Any], ...]] = {
    "geometry": (
        {"name": "nominal", "kind": "none", "desc": "标称几何"},
        {"name": "tolerance_0.1mm", "kind": "tolerance", "mm": 0.1,
         "desc": "装配公差 ±0.1mm"},
    ),
    "electrical": (
        {"name": "nominal", "kind": "none", "desc": "标称电压"},
        {"name": "rail_minus_5pct", "kind": "rail_scale", "factor": 0.95,
         "desc": "轨压 −5%"},
        {"name": "load_step", "kind": "load_scale", "factor": 1.2,
         "desc": "负载 +20%（阶跃）"},
    ),
    "kinematic": (
        # 这一组沿用 RoboBuilder 的尺度，因为场景同类（地面/机构运动）
        {"name": "nominal", "kind": "none", "desc": "标称初始位姿"},
        {"name": "shift_1mm", "kind": "pose_shift_mm", "mm": 1.0,
         "desc": "初始位姿偏移 1mm"},
        {"name": "late_50ms", "kind": "time_late_s", "s": 0.05,
         "desc": "时序延后 50ms"},
    ),
}


@dataclass
class Attempt:
    """一次扰动的结果。"""

    name: str
    status: str = "unknown"
    detail: str = ""
    #: 这次扰动用了哪些数值（便于追溯判据）
    applied: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status,
                "detail": self.detail, "applied": dict(self.applied)}


@dataclass
class RobustResult:
    """一组扰动的合并结果。"""

    domain: str
    attempts: list[Attempt] = field(default_factory=list)
    #: 合并后的三态
    status: str = "unknown"
    note: str = ""

    @property
    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for attempt in self.attempts:
            out[attempt.status] = out.get(attempt.status, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "status": self.status,
            "attempts": [a.to_dict() for a in self.attempts],
            "counts": self.counts,
            "note": self.note,
            "summary": self.summary(),
        }

    def summary(self) -> str:
        counts = self.counts
        bits = [f"{a.name}={a.status}" for a in self.attempts]
        if not self.attempts:
            return f"{self.domain}：没有跑任何扰动（unknown）"
        head = f"{self.domain}：{len(self.attempts)} 次扰动，" \
               f"pass {counts['pass']} / fail {counts['fail']} / " \
               f"unknown {counts['unknown']}"
        if self.note:
            head += f"。{self.note}"
        return head + "（" + ", ".join(bits) + "）"


def combine(attempts: list[Attempt]) -> str:
    """把多次扰动合成三态。

    **顺序要紧**：
      1. 任一 fail → fail（已经证明不稳，不用看别的）
      2. 没有 attempt → unknown
      3. 有任一 unknown（或不是全 pass）→ unknown
      4. 全 pass → pass
    """
    if not attempts:
        return "unknown"
    if any(a.status == "fail" for a in attempts):
        return "fail"
    if any(a.status != "pass" for a in attempts):
        return "unknown"
    return "pass"


def triplicate(domain: str,
               run: Callable[[dict[str, Any]], tuple[str, str]],
               *,
               perturbations: tuple[dict[str, Any], ...] | None = None
               ) -> RobustResult:
    """按域的扰动清单跑一遍。

    `run(perturbation)` 必须返回 `(status, detail)`，status 是
    `pass`/`fail`/`unknown` 之一。**不在这里替它判**——
    判定逻辑属于各域，这一层只管"跑几次、怎么合并"。

    为什么把 `run` 作为参数：不同域的"施加扰动"方式完全不同
    （几何改公差、电气改轨压、运动改初始位姿），
    硬编码会变成一个大 if。传进来更清楚，也更好测。
    """
    specs = perturbations or PERTURBATIONS.get(domain, ())
    if not specs:
        return RobustResult(
            domain=domain, status="unknown",
            note=f"没有 {domain} 域的扰动定义——这一域不做鲁棒性判定")

    result = RobustResult(domain=domain)
    for spec in specs:
        name = str(spec.get("name") or "?")
        try:
            status, detail = run(spec)
        except Exception as exc:                          # noqa: BLE001
            # **异常是 unknown，不是 fail**：没跑起来不等于设计坏了。
            # 但如果把它当 pass，"没跑"就变成了"通过"。
            status, detail = "unknown", f"{type(exc).__name__}: {exc}"
        if status not in STATUSES:
            status = "unknown"
        result.attempts.append(Attempt(
            name=name, status=status, detail=str(detail),
            applied={k: v for k, v in spec.items()
                     if k not in ("name", "kind", "desc")},
        ))

    result.status = combine(result.attempts)
    if result.status == "unknown":
        result.note = ("有扰动没跑起来或被跳过 —— "
                       "**unknown 不是通过**")
    elif result.status == "fail":
        failed = [a.name for a in result.attempts if a.status == "fail"]
        result.note = f"在 {', '.join(failed)} 下失效"
    return result


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.robustness",
        "domains": {k: [p["name"] for p in v]
                    for k, v in PERTURBATIONS.items()},
        "combine_rule": "任一 fail → fail；有 unknown → unknown；全 pass → pass",
        "discipline": ("unknown 不能当 pass；"
                       "扰动清单按域适配，不照搬乐高尺度"),
    }