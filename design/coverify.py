"""软硬件协同验证 —— **hw_velxio 自己的**：需求数值 vs 硬件能力。

取代 `hwforge.coverify` + `hwforge.software`。

## 它回答什么

其他检查回答"元件会不会撞""引脚连没连上"。这一层回答**完全不同的一个
问题**：

> 需求里写的那些数字，硬件真的能兑现吗？

每条检查都**同时给出两个数字**（硬件能力 + 软件需求）。一个只说"不够"
而不给两个数的结论不是协同验证，是带标签的猜测。

## 8 条检查

| ID | 检查 | 典型陷阱 |
|---|---|---|
| CV-01 | 舵机分辨率 vs 指向精度 | 死区 5µs / 1000µs 行程 = 0.9°，要求 0.5° —— **任何固件都修不好** |
| CV-02 | PWM 定时器 vs 舵机死区 | 一个 tick 比死区粗，两个角度落在同一格 |
| CV-03 | 视觉分辨率 vs 目标尺寸 | 3m 处 15cm 目标只有 173mm 可分辨 |
| CV-04 | 云台速度 vs 跟踪需求 | 跟踪高速目标时画面滞后 |
| CV-05 | 码率 vs 链路 | Wi-Fi 实际只能到名义 60% |
| CV-06 | 端到端延迟预算 | 最贵的一段通常是 transmit |
| CV-07 | 电流预算 vs 电源 | **用空载 4mA 估算，舵机顶到限位就跌压复位** |
| CV-08 | 引脚能力 vs 用途 | 3.3V 逻辑脚给 5V 舵机供电 |

## 纪律

1. **能算的算，不能算的报未验证。** 需求数值没给就不拿默认值冒充"验过"；
2. **数值来源要说清楚。** 舵机参数来自 datasheet，相机参数来自规格表 ——
   没有出处就标 `assumed`；
3. **CV-08 是反例检查。** 它在参考设计上**应该失败** —— 它记录的是那个
   陷阱本身，不是当前设计的缺陷。这一点必须写在输出里，否则会被误当
   成"设计有 bug"。

@module hw_velxio.design.coverify
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# 硬件事实（带出处）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServoSpec:
    """舵机参数。**全部标注来源** —— 没有来源的数字不能参与判定。"""

    pulse_min_us: float = 1000.0
    pulse_max_us: float = 2000.0
    travel_deg: float = 180.0
    deadband_us: float = 5.0
    frame_hz: float = 50.0
    stall_current_ma: float = 700.0
    idle_current_ma: float = 4.0
    #: 出处（datasheet 名称 + 来源类别）
    source: str = "SG90 datasheet"

    @property
    def span_us(self) -> float:
        return max(self.pulse_max_us - self.pulse_min_us, 1e-6)

    def angular_resolution_deg(self) -> float:
        """最小可分辨角度 = 死区折算到角度。

        这是**硬件下限**：控制环不可能比它更细。
        """
        return self.deadband_us * (self.travel_deg / self.span_us)

    def travel_time_s(self, angle_deg: float, speed_s_per_60: float) -> float:
        """按 datasheet 的 `s/60°` 折算转 `angle_deg` 所需时间。"""
        return abs(angle_deg) / 60.0 * speed_s_per_60


@dataclass(frozen=True)
class CameraSpec:
    """相机参数。"""

    width_px: int = 640
    height_px: int = 480
    fps: int = 25
    hfov_deg: float = 60.0
    source: str = "OV2640 类模组规格"

    def fov_coverage_mm(self, distance_mm: float) -> tuple[float, float]:
        """在给定距离上，视场覆盖的物理宽度/高度（mm）。"""
        half = math.radians(self.hfov_deg / 2.0)
        width_mm = 2.0 * distance_mm * math.tan(half)
        aspect = self.height_px / max(self.width_px, 1)
        return (width_mm, width_mm * aspect)

    def pixels_per_mm(self, distance_mm: float) -> tuple[float, float]:
        width_mm, height_mm = self.fov_coverage_mm(distance_mm)
        return (self.width_px / max(width_mm, 1e-6),
                self.height_px / max(height_mm, 1e-6))

    def min_detectable_size_mm(self, distance_mm: float,
                               min_feature_px: int = 32) -> float:
        """检测器能可靠分辨的最小目标尺寸（mm）。

        `min_feature_px=32` 是模板/Haar 类检测器的经验下限，**保守**：
        现代 CNN 能更小，但保守估计不会让人做出错的判断。
        """
        ppx, _ppy = self.pixels_per_mm(distance_mm)
        return min_feature_px / max(ppx, 1e-9)


@dataclass
class RequirementNumbers:
    """需求转成的数字。**没有这个，就没有可验证的东西。**"""

    pointing_accuracy_deg: float = 1.0
    max_tracking_speed_deg_per_s: float = 45.0
    detection_distance_mm: float = 3000.0
    min_target_size_mm: float = 150.0
    link_mbps: float = 8.0
    max_latency_ms: float = 300.0
    supply_ma: float = 2000.0
    servo_count: int = 2

    #: 哪些字段是**调用方真的给了**（其余是默认值，不代表需求）
    declared: set[str] = field(default_factory=set)

    def provenance(self, name: str) -> str:
        return "调用方声明" if name in self.declared else "默认值（未声明）"


# ---------------------------------------------------------------------------
# 检查结果
# ---------------------------------------------------------------------------

@dataclass
class CoCheck:
    """一条协同检查。**两个数字都在**。"""

    check_id: str
    title: str
    hardware_value: float | str
    software_needs: float | str
    unit: str
    passed: bool
    message: str
    fix: dict[str, Any] = field(default_factory=dict)
    #: 这条检查是"记录陷阱"用的反例（CV-08）—— 它失败不代表当前设计有问题
    counter_example: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"check_id": self.check_id, "title": self.title,
                "hardware": self.hardware_value,
                "software_needs": self.software_needs, "unit": self.unit,
                "passed": self.passed, "message": self.message,
                "fix": dict(self.fix),
                "counter_example": self.counter_example}


#: 位宽与位硬编码（PWM 定时器常见规格）
DEFAULT_CORE_CLOCK_HZ = 160e6
DEFAULT_TIMER_BITS = 16

#: 视频压缩比（比特/像素）。0.08 是中质量 H.264 的经验值。
BITS_PER_PIXEL = 0.08

#: 链路可用率。Wi-Fi **永远到不了名义速率**的持续码流，取 60%。
LINK_USABLE_RATIO = 0.6

#: 电流裕量。舵机堵转是瞬时大电流，留 30%。
CURRENT_MARGIN = 1.3


def _timer_resolution_us(core_hz: float, frame_hz: float,
                         bits: int) -> tuple[float, int, bool]:
    """PWM 定时器分辨率。

    需要在一个帧周期内容纳 1000..2000µs 的脉宽，且分辨率要细于舵机死区。
    返回 `(分辨率µs, 预分频, 是否能配出来)`。
    """
    if core_hz <= 0 or frame_hz <= 0:
        return (9999.0, 0, False)
    frame_s = 1.0 / frame_hz
    max_ticks = 2 ** bits - 1
    # 定时器必须在一个帧周期内不溢出
    needed_tick_s = frame_s / max_ticks
    prescaler = max(1, int(math.ceil(needed_tick_s * core_hz)))
    resolution_us = prescaler / core_hz * 1e6
    fits = prescaler * max_ticks / core_hz <= frame_s * 1.05
    return (round(resolution_us, 4), prescaler, fits)


class CoVerifier:
    """把软件算法与硬件事实配对，逐条报两个数字。"""

    def __init__(self, req: RequirementNumbers, servo: ServoSpec,
                 camera: CameraSpec, *,
                 speed_s_per_60: float = 0.10,
                 core_clock_hz: float = DEFAULT_CORE_CLOCK_HZ,
                 timer_bits: int = DEFAULT_TIMER_BITS,
                 extra_loads_ma: dict[str, float] | None = None) -> None:
        self.req = req
        self.servo = servo
        self.camera = camera
        self.speed_s_per_60 = speed_s_per_60
        self.core_clock_hz = core_clock_hz
        self.timer_bits = timer_bits
        self.extra_loads_ma = dict(extra_loads_ma or {})

    # -- CV-01 -------------------------------------------------------------

    def cv01_servo_resolution(self) -> CoCheck:
        """死区折算的角度 vs 软件要求的指向精度。"""
        res = self.servo.angular_resolution_deg()
        ok = res <= self.req.pointing_accuracy_deg
        message = (
            f"舵机死区 {self.servo.deadband_us}µs 在 "
            f"{self.servo.span_us}µs 行程上等价于 {res:.2f}° 的角分辨率；"
            f"要求 {self.req.pointing_accuracy_deg}°。")
        if not ok:
            message += (" 这是**硬件下限**，任何固件都无法突破 —— "
                        "要么放宽要求，要么换更高分辨率的舵机。")
        return CoCheck("cv.servo_resolution", "舵机分辨率 vs 指向精度",
                       round(res, 2), self.req.pointing_accuracy_deg, "°",
                       ok, message,
                       {} if ok else {"need": "finer_deadband_servo_or_relax_spec"})

    # -- CV-02 -------------------------------------------------------------

    def cv02_timer(self) -> CoCheck:
        """PWM 定时器必须比舵机死区更细。"""
        resolution_us, prescaler, fits = _timer_resolution_us(
            self.core_clock_hz, self.servo.frame_hz, self.timer_bits)
        ok = fits and resolution_us <= self.servo.deadband_us
        message = (
            f"{self.timer_bits} 位定时器 @ {self.core_clock_hz/1e6:.0f}MHz："
            f"预分频 {prescaler}，分辨率 {resolution_us}µs；"
            f"舵机死区 {self.servo.deadband_us}µs。")
        if not ok:
            message += (" 一个 tick 比死区粗，两个相邻角度会落在同一格 —— "
                        "死区无法表达。")
        return CoCheck("cv.pwm_timer", "PWM 定时器 vs 舵机死区",
                       resolution_us, self.servo.deadband_us, "µs",
                       ok, message,
                       {} if ok else {"prescaler": prescaler, "fits": fits})

    # -- CV-03 -------------------------------------------------------------

    def cv03_vision_resolution(self) -> CoCheck:
        """工作距离上的像素数 vs 检测器需要的最小特征。"""
        detectable = self.camera.min_detectable_size_mm(
            self.req.detection_distance_mm)
        ppx, _ = self.camera.pixels_per_mm(self.req.detection_distance_mm)
        ok = detectable <= self.req.min_target_size_mm
        message = (
            f"在 {self.req.detection_distance_mm/1000:.1f}m 处，"
            f"{self.camera.width_px}×{self.camera.height_px} / "
            f"{self.camera.hfov_deg:.0f}°HFOV 给出 {ppx:.1f} px/mm；"
            f"检测器需 32px 时最小可分辨目标 {detectable:.0f}mm，"
            f"要求 {self.req.min_target_size_mm:.0f}mm。")
        if not ok:
            message += " 要么拉近距离、要么提高分辨率、要么换更长焦镜头。"
        return CoCheck("cv.vision_resolution", "视觉分辨率 vs 目标尺寸",
                       round(detectable, 1), self.req.min_target_size_mm, "mm",
                       ok, message,
                       {} if ok else {"options": ["closer", "higher_res",
                                                  "longer_lens"]})

    # -- CV-04 -------------------------------------------------------------

    def cv04_slew_rate(self) -> CoCheck:
        """舵机能不能转得够快，跟上目标。"""
        travel_s = self.servo.travel_time_s(self.servo.travel_deg,
                                           self.speed_s_per_60)
        achievable = (self.servo.travel_deg / travel_s) if travel_s > 0 else 0.0
        need = self.req.max_tracking_speed_deg_per_s
        ok = achievable >= need
        message = (
            f"舵机 {self.speed_s_per_60:.2f}s/60° → 全程 {travel_s:.2f}s，"
            f"等效最大 {achievable:.0f}°/s；要求 {need:.0f}°/s。")
        if not ok:
            message += " 跟踪高速目标时会落后，画面滞后。"
        return CoCheck("cv.slew_rate", "云台速度 vs 跟踪需求",
                       round(achievable, 1), need, "°/s", ok, message,
                       {} if ok else {"need": "faster_servo_or_slower_target"})

    # -- CV-05 -------------------------------------------------------------

    def cv05_bitrate(self) -> CoCheck:
        """软件要推的码率 vs 链路的诚实吞吐。"""
        nominal = (self.camera.width_px * self.camera.height_px
                   * self.camera.fps * BITS_PER_PIXEL / 1e6)
        per_frame_bits = self.camera.width_px * self.camera.height_px * BITS_PER_PIXEL
        budget_s = max(self.req.max_latency_ms - 1000.0 / self.camera.fps,
                       1.0) / 1000.0
        need_mbps = per_frame_bits / budget_s / 1e6
        usable = min(self.req.link_mbps,
                     self.req.link_mbps * LINK_USABLE_RATIO)
        ok = usable >= need_mbps
        message = (
            f"{self.camera.width_px}×{self.camera.height_px}@{self.camera.fps}fps, "
            f"{BITS_PER_PIXEL} bpp → 名义 {nominal:.2f} Mbps；"
            f"要在 {self.req.max_latency_ms:.0f}ms 内到端，需 {need_mbps:.2f} Mbps；"
            f"可用链路 {usable:.2f} Mbps（名义 {self.req.link_mbps} 的 "
            f"{LINK_USABLE_RATIO*100:.0f}%）。")
        if not ok:
            message += " 必须降分辨率/降帧率/提压缩率。"
        return CoCheck("cv.bitrate", "码率 vs 链路",
                       round(usable, 2), round(need_mbps, 2), "Mbps", ok,
                       message,
                       {} if ok else {"options": ["lower_res", "lower_fps",
                                                  "more_compression"]})

    # -- CV-06 -------------------------------------------------------------

    def cv06_latency(self) -> CoCheck:
        """逐段延迟 vs 端到端预算。"""
        usable = min(self.req.link_mbps,
                     self.req.link_mbps * LINK_USABLE_RATIO)
        bits = (self.camera.width_px * self.camera.height_px * BITS_PER_PIXEL)
        stages = {
            "capture": 1000.0 / max(self.camera.fps, 1),
            "encode": 25.0,          # 硬件 JPEG/H.264，经验值
            "transmit": (bits / (usable * 1e6)) * 1000.0 if usable > 0 else 9999.0,
            "decode": 25.0,
            "display": 20.0,
        }
        total = sum(stages.values())
        ok = total <= self.req.max_latency_ms
        message = ("各段 " + " + ".join(f"{k} {v:.0f}" for k, v in stages.items())
                   + f" = {total:.0f}ms，预算 {self.req.max_latency_ms:.0f}ms。")
        if not ok:
            message += " 最贵的一段通常是 transmit；先降码率再看。"
        return CoCheck("cv.latency", "端到端延迟预算",
                       round(total, 1), self.req.max_latency_ms, "ms", ok,
                       message, {"stages_ms": stages} if not ok else {})

    # -- CV-07 -------------------------------------------------------------

    def cv07_current(self) -> CoCheck:
        """**堵转**合计（不是空载）vs 电源额定。"""
        stall_total = self.servo.stall_current_ma * max(self.req.servo_count, 0)
        extra = sum(max(v, 0.0) for v in self.extra_loads_ma.values())
        total = stall_total + extra
        required = total * CURRENT_MARGIN
        ok = required <= self.req.supply_ma
        message = (
            f"堵转合计 {total:.0f}mA ×{CURRENT_MARGIN} 裕量 = {required:.0f}mA；"
            f"电源 {self.req.supply_ma:.0f}mA。")
        if not ok:
            message += (" 电源会在舵机顶到限位时跌压复位 —— "
                        f"用空载 {self.servo.idle_current_ma:.0f}mA 估算就会掉进这个坑。")
        return CoCheck("cv.current", "电流预算 vs 电源",
                       round(required, 0), self.req.supply_ma, "mA", ok, message,
                       {} if ok else {"need": "bigger_psu_or_sequential_motion"})

    # -- CV-08 -------------------------------------------------------------

    def cv08_pin_capability(self) -> CoCheck:
        """引脚能力 vs 用途 —— **反例检查**。

        具体失效：用 3.3V 逻辑脚给 5V 舵机供电，且要从逻辑脚取 700mA。

        **这条在参考设计上就应该失败**：它记录的是那个陷阱本身。
        所以在结果里标 `counter_example=True`，避免被误读成"当前设计有 bug"。
        """
        vcc_pin_mv = 3300.0
        servo_v_min_mv = 4800.0        # SG90 级舵机的最低工作电压
        ok = servo_v_min_mv <= vcc_pin_mv
        message = (
            f"若用 {vcc_pin_mv/1000:.1f}V 逻辑脚给舵机供电，"
            f"低于舵机最低 {servo_v_min_mv/1000:.1f}V；"
            f"且该脚额定远小于堵转 {self.servo.stall_current_ma:.0f}mA。"
            + ("" if ok else " 必须用独立 5V 轨。"))
        return CoCheck("cv.pin_capability", "引脚能力 vs 用途（反例检查）",
                       servo_v_min_mv, vcc_pin_mv, "mV", ok, message,
                       {"need": "use_dedicated_5v_rail"},
                       counter_example=True)

    # -- 汇总 --------------------------------------------------------------

    def checks(self) -> list[CoCheck]:
        return [self.cv01_servo_resolution(), self.cv02_timer(),
                self.cv03_vision_resolution(), self.cv04_slew_rate(),
                self.cv05_bitrate(), self.cv06_latency(),
                self.cv07_current(), self.cv08_pin_capability()]


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.coverify",
        "checks": ["cv.servo_resolution", "cv.pwm_timer",
                   "cv.vision_resolution", "cv.slew_rate", "cv.bitrate",
                   "cv.latency", "cv.current", "cv.pin_capability"],
        "note": "cv.pin_capability 是反例检查，参考设计上应失败",
    }


# ---------------------------------------------------------------------------
# 接到设计上：`run(design)` —— 与 physical 的 findings 同格式
# ---------------------------------------------------------------------------

#: 需求里那些"必须由人给"的数值。缺了就无法做对应检查。
NUMERIC_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "pointing_accuracy_deg": {"label": "指向精度", "unit": "°",
                              "checks": ["CV-01"], "default": 1.0},
    "max_tracking_speed_deg_per_s": {"label": "最大跟踪角速度", "unit": "°/s",
                                     "checks": ["CV-04"], "default": 45.0},
    "detection_distance_mm": {"label": "检测距离", "unit": "mm",
                              "checks": ["CV-03"], "default": 3000.0},
    "min_target_size_mm": {"label": "最小目标尺寸", "unit": "mm",
                           "checks": ["CV-03"], "default": 150.0},
    "link_mbps": {"label": "链路带宽", "unit": "Mbps",
                  "checks": ["CV-05"], "default": 8.0},
    "max_latency_ms": {"label": "端到端延迟", "unit": "ms",
                       "checks": ["CV-06"], "default": 300.0},
    "supply_ma": {"label": "电源电流", "unit": "mA",
                  "checks": ["CV-07"], "default": 2000.0},
}

#: 舵机 datasheet 事实（SG90）。**不在运行时猜** —— 写死在这里并标出处，
#: 比"从设计里反推"可靠：设计里没有舵机的电气参数。
SERVO_FACTS: dict[str, Any] = {
    "pulse_min_us": 1000.0, "pulse_max_us": 2000.0, "travel_deg": 180.0,
    "deadband_us": 5.0, "frame_hz": 50.0, "stall_current_ma": 700.0,
    "idle_current_ma": 4.0,
    "source": "SG90 datasheet（OSEPP LS-SG90SR）",
}

#: 相机规格（OV2640 级）。
CAMERA_FACTS: dict[str, Any] = {
    "width_px": 640, "height_px": 480, "fps": 25, "hfov_deg": 60.0,
    "source": "OV2640 类模组规格（640×480 / 60°HFOV）",
}


@dataclass
class CoResult:
    """协同验证结果。"""

    ok: bool = True
    findings: list[dict[str, Any]] = field(default_factory=list)
    not_verified: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "findings": list(self.findings),
                "not_verified": list(self.not_verified),
                "checks": list(self.checks),
                "provenance": dict(self.provenance)}


def run(design: Any, *, numbers: dict[str, float] | None = None,
        servo_count: int | None = None) -> CoResult:
    """对一个 `DesignModel` 跑协同验证。

    `numbers`：需求数值（覆盖默认）。**给了哪个才算"用户声明了"** ——
    没给的用默认值参与计算，但会在 `not_verified` 里点名。
    """
    result = CoResult()
    numbers = dict(numbers or {})

    result.provenance["servo"] = SERVO_FACTS["source"]
    result.provenance["camera"] = CAMERA_FACTS["source"]

    # -- 需求数值 ----------------------------------------------------------
    req_kwargs: dict[str, Any] = {}
    declared: set[str] = set()
    for key, meta in NUMERIC_REQUIREMENTS.items():
        if key in numbers:
            req_kwargs[key] = float(numbers[key])
            declared.add(key)
            result.provenance[key] = "调用方声明"
        else:
            req_kwargs[key] = float(meta["default"])
            result.provenance[key] = (
                f"默认值 {meta['default']}{meta['unit']}（未声明）")
            result.not_verified.append(
                f"{meta['label']}未声明，按默认 {meta['default']}{meta['unit']} "
                f"计算（影响 {'/'.join(meta['checks'])}）")

    # 舵机数量：优先从设计里数
    if servo_count is None:
        servo_count = sum(1 for p in getattr(design, "parts", [])
                          if str(getattr(p, "kind", "")) == "gimbal_servo")
        if servo_count > 0:
            result.provenance["servo_count"] = (
                f"从设计里数出 {servo_count} 个舵机")
    if servo_count and servo_count > 0:
        req_kwargs["servo_count"] = int(servo_count)

    requirement = RequirementNumbers(**req_kwargs)
    requirement.declared = declared

    verifier = CoVerifier(requirement, ServoSpec(**SERVO_FACTS),
                          CameraSpec(**CAMERA_FACTS))
    try:
        checks = verifier.checks()
    except Exception as exc:  # noqa: BLE001
        result.ok = False
        result.not_verified.append(
            f"协同验证执行失败：{type(exc).__name__}: {exc}")
        return result

    # 语义树：给失败项补 root_cause。**树只是解释层，不参与判定。**
    tree = None
    try:
        from . import semantics as semantics_module
        tree = semantics_module.tree_for_template("gimbal_servo")
    except Exception:  # noqa: BLE001
        tree = None

    for check in checks:
        entry = check.to_dict()
        result.checks.append(entry)
        passed = bool(entry.get("passed"))
        if not passed:
            result.ok = False
        finding: dict[str, Any] = {
            "check": f"coverify.{entry.get('check_id', '?')}",
            "ok": passed,
            "message": str(entry.get("message") or entry.get("title") or ""),
            # **两个数字都在** —— 只有结论的协同验证是带标签的猜测
            "measured": f"{entry.get('hardware')} {entry.get('unit')}",
            "limit": f">= {entry.get('software_needs')} {entry.get('unit')}",
            "refs": [],
            "severity": "error" if not passed else "info",
            "fix": entry.get("fix") or {},
            "counter_example": bool(entry.get("counter_example")),
        }
        # 失败项补 root_cause —— 这是语义树**唯一**的消费点
        if not passed and tree is not None:
            check_id = str(entry.get("check_id") or "")
            cause = root_cause_for(check_id, tree)
            if cause is not None:
                finding["root_cause"] = cause
            # `cv.current` 在电源侧还有第二个原因 —— 两边都要报，
            # 否则"换舵机还是换电源"这个决定没有依据
            power_cause = power_root_cause_for(check_id)
            if power_cause is not None:
                finding["root_cause_power_side"] = power_cause
        result.findings.append(finding)
    return result


#: 检查 → (语义树字段, 解释模板, 修法列表, **谁修不了**)
#:
#: `cannot_fix_by` 是最有价值的字段：它把"硬件下限"变成一条可执行判断，
#: 让调用方不在错误的层浪费时间。
ROOT_CAUSE_MAP: dict[str, tuple[str, str, list[str], str]] = {
    "cv.servo_resolution": (
        "deadband_us",
        "舵机控制板的 PWM 解析死区 {value}，经 {travel}° 行程折算成角分辨率",
        ["换更低死区的舵机（数字舵机常见 1-2µs）",
         "放宽指向精度要求"],
        "固件 —— 这是模拟量分辨率，不是软件能改善的"),
    "cv.pwm_timer": (
        "deadband_us",
        "舵机的电气死区是 {value}，而定时器的一个 tick 比它粗",
        ["换更高主频/更宽定时器", "降低帧频以换取分辨率"],
        "控制算法 —— 硬件表达不出这个精度"),
    "cv.slew_rate": (
        "pwm_frame_hz",
        "舵机按 {value} 帧频更新，每帧只能修一次角度",
        ["换更快的舵机", "降低跟踪速度要求"],
        "固件 —— 更新率由舵机决定"),
    "cv.current": (
        "stall_current_ma",
        "堵转电流 {value}：舵机内部的 H 桥把全部电流走桥臂",
        ["换更大电源", "分时驱动多舵机，避免同时堵转",
         "加软启动/限流"],
        "软件 —— 堵转是物理负载导致的"),
}

#: 电源侧的对应说法。`cv.current` 失败可能有两边原因：
#: **负载太大**（舵机树）或**电源太小**（psu 树）。只报一边会误导。
POWER_SIDE_MAP: dict[str, tuple[str, str, list[str], str]] = {
    "cv.current": (
        "rated_a",
        "电源额定 {value}：这是散热条件下的连续值，不是峰值能力",
        ["换更大额定电源", "降低同时动作的舵机数量"],
        "固件 —— 额定是电源的物理上限"),
}


def power_root_cause_for(check_id: str) -> dict[str, Any] | None:
    """`cv.current` 的**电源侧**根因。

    同一条失败在负载侧（舵机堵转 700mA）和电源侧（额定 2A）各有一个原因。
    **只报一边会误导**：换舵机或换电源都是修法，取决于哪边是瓶颈。
    """
    entry = POWER_SIDE_MAP.get(check_id)
    if entry is None:
        return None
    try:
        from . import semantics as semantics_module
    except Exception:  # noqa: BLE001
        return None
    tree = semantics_module.tree_for_template("psu_5v")
    if tree is None:
        return None
    field_name, template, options, cannot_fix_by = entry
    subsystem, quantity = tree.find(field_name)
    if subsystem is None or quantity is None:
        return None
    return {
        "side": "power",
        "subsystem": subsystem.name,
        "subsystem_role": subsystem.role,
        "field": field_name,
        "value": quantity.text(),
        "why": template.format(value=quantity.text()),
        "fix_options": list(options),
        "cannot_fix_by": cannot_fix_by,
        "source": quantity.source,
        "trust": subsystem.trust,
        "caveats": list(getattr(tree, "caveats", []) or []),
    }


def root_cause_for(check_id: str, tree: Any) -> dict[str, Any] | None:
    """从语义树取出这条失败的**物理来源**。

    只对 `ROOT_CAUSE_MAP` 里列出的检查生效 —— 其余检查的原因不在
    舵机的内部结构里，硬套会给出错误的解释。
    """
    entry = ROOT_CAUSE_MAP.get(check_id)
    if entry is None:
        return None
    field_name, template, options, cannot_fix_by = entry
    subsystem, quantity = tree.find(field_name)
    if subsystem is None or quantity is None:
        return None
    cause: dict[str, Any] = {
        "subsystem": subsystem.name,
        "subsystem_role": subsystem.role,
        "field": field_name,
        "value": quantity.text(),
        "why": template.format(
            value=quantity.text(),
            travel=(tree.spec("travel_deg").text()
                    if tree.spec("travel_deg") else "180")),
        "fix_options": list(options),
        "cannot_fix_by": cannot_fix_by,
        "source": quantity.source,
        "trust": subsystem.trust,
        # **注意**：树里的 caveats 决定这条解释能被引用到什么程度
        "caveats": list(getattr(tree, "caveats", []) or []),
    }
    # 该子系统的失效模式一并带出 —— 它们是"换了这个还会坏什么"的线索
    if subsystem.failure_modes:
        cause["failure_modes"] = list(subsystem.failure_modes)
    return cause


def summarise(result: CoResult) -> dict[str, Any]:
    """给面板/快照的摘要。"""
    failed = [f for f in result.findings if not f.get("ok")]
    # 反例检查失败**不算设计缺陷** —— 它记录的是陷阱本身
    real_failed = [f for f in failed if not f.get("counter_example")]
    return {
        "ok": not real_failed,
        "total": len(result.findings),
        "failed": len(real_failed),
        "failed_ids": [f["check"] for f in real_failed],
        "counter_examples": [f["check"] for f in failed
                             if f.get("counter_example")],
        "not_verified": list(result.not_verified),
        "note": "counter_examples 里的检查失败是**已知陷阱的记录**，"
                "不代表当前设计有问题",
    }
