"""MuJoCo 运行时 —— 编译 MJCF、跑、取轨迹。

## 环境隔离（本项目的要求）

`mujoco` **不装在全局 Python 里**：仓库同级的 `vendor/pylib` 就是为此准备的。
所以这个模块在 import 前先把它加进 `sys.path`，且**只在需要时加载**
（`_import_mujoco()`），这样：

* 没装 MuJoCo 的机器上，其它 300 多个功能全都能用
* `hw_sim --domain kinematic` 才会报 `not_ready`，其余判定不受影响

这就是"可选增强"的正确形态：**缺它时不是崩，是明确报出缺什么**。

## 确定性

MuJoCo 单线程 + 固定 dt + 固定初始状态 = 同一个模型两次跑结果一致。
验收要断言这一点（两次跑到同一 qpos）。**不依赖 seed**：
`seed` 参数只影响凸柱强度分布（P18 的鲁棒性扰动用），不影响基础动力学。

## 结果只报"能判的事"

`SimResult` 里不塞 `"passed": True` 这种口号。只给物理量：
结束位置、姿态、倾角、是否还在动、能量。**判定留给 P18**。
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 本项目的本地库目录（mujoco 装在这里，不进全局 Python）。
LOCAL_LIB = Path(__file__).resolve().parent.parent.parent / "vendor" / "pylib"


def _ensure_path() -> None:
    """把本地库目录加进 sys.path（只加一次）。"""
    target = str(LOCAL_LIB)
    if target not in sys.path and LOCAL_LIB.is_dir():
        sys.path.insert(0, target)


#: 测试用的关闭开关。设成 1 时**假装没装 MuJoCo**。
#:
#: 为什么要这个：P23 要求"没有 MuJoCo 整套也全绿"。而 MuJoCo 装在项目内
#: `vendor/pylib`，没法靠卸载来验。给一个显式开关，让降级路径**可测**——
#: 否则那条保证只能靠嘴说。
NO_MUJOCO_ENV = "HWV_NO_MUJOCO"


def available() -> tuple[bool, str]:
    """MuJoCo 可不可用。**返回 (可否, 说明)** —— 缺时要能说清缺什么。"""
    import os
    if str(os.environ.get(NO_MUJOCO_ENV, "")).strip().lower() in ("1", "true", "yes"):
        return False, (f"MuJoCo 被 {NO_MUJOCO_ENV}=1 关掉（测试降级路径用）")
    _ensure_path()
    try:
        import mujoco                                            # noqa: F401
    except ImportError as exc:
        return False, (f"MuJoCo 不可用：{exc}。"
                       f"它应装在 {LOCAL_LIB}（pip install --target）")
    import mujoco
    return True, f"MuJoCo {mujoco.__version__} @ {Path(mujoco.__file__).parent}"


def _import_mujoco():
    _ensure_path()
    import mujoco
    return mujoco


#: 摊平判定的倾角阈值（度）。超过它算"翻了"。
#: 这不是物理常量，是**判定口径**——P18 会把它变成可配的参数。
FLIP_ANGLE_DEG = 60.0


@dataclass
class SimResult:
    """一次仿真的物理结果。**只有量，没有结论。**"""

    ok: bool = False
    error: str = ""
    steps: int = 0
    sim_time_s: float = 0.0
    wall_time_s: float = 0.0
    #: 每个自由体的结束位姿：ref → {"pos": (x,y,z) m, "quat": (w,x,y,z)}
    final: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    #: 相对初始的最大位移（m），key 同上
    max_displacement: dict[str, float] = field(default_factory=dict)
    #: 最大倾角（度）—— 超过 FLIP_ANGLE_DEG 算翻了
    max_tilt_deg: dict[str, float] = field(default_factory=dict)
    #: 最后一个时间窗内的平均速度（m/s），用于判"是否还在动"
    final_speed: dict[str, float] = field(default_factory=dict)
    #: 轨迹采样（每隔 N 步一条），用于出图/视频
    trajectory: dict[str, list[list[float]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "error": self.error,
            "steps": self.steps,
            "sim_time_s": round(self.sim_time_s, 6),
            "wall_time_s": round(self.wall_time_s, 3),
            "final": self.final,
            "max_displacement": {k: round(v, 6)
                                 for k, v in self.max_displacement.items()},
            "max_tilt_deg": {k: round(v, 3)
                             for k, v in self.max_tilt_deg.items()},
            "final_speed": {k: round(v, 6)
                            for k, v in self.final_speed.items()},
            "trajectory_points": {k: len(v)
                                  for k, v in self.trajectory.items()},
            "warnings": list(self.warnings),
            "summary": self.summary(),
        }

    def summary(self) -> str:
        if not self.ok:
            return f"仿真没跑起来：{self.error}"
        if not self.final:
            return (f"跑了 {self.steps} 步（{self.sim_time_s:.3f}s），"
                    f"但没有任何自由体——检查是不是用了 free_bodies=False")
        bits = []
        for ref, tilt in sorted(self.max_tilt_deg.items()):
            bits.append(f"{ref} 最大倾角 {tilt:.1f}°")
        return (f"跑了 {self.steps} 步（{self.sim_time_s:.3f}s，"
                f"墙钟 {self.wall_time_s:.2f}s）；" + "；".join(bits[:5]))


def _tilt_deg(quat: list[float]) -> float:
    """四元数与"竖直"的夹角（度）。

    自由体"立住"= 它的局部 +z 仍大致指向世界 +z。
    局部 z 轴在世界系 = R·[0,0,1]，其 z 分量 = 1 - 2(x² + y²)。
    倾角 = acos(clamp(that))。
    """
    w, x, y, _z = quat
    up_z = 1.0 - 2.0 * (x * x + y * y)
    up_z = max(-1.0, min(1.0, up_z))
    return math.degrees(math.acos(up_z))


def run(design: Any, *,
        duration_s: float = 5.0,
        params: Any = None,
        free_bodies: bool = False,
        weld_parts: bool = False,
        include_floor: bool = True,
        sample_every: int = 50,
        max_steps: int = 200_000) -> SimResult:
    """编译 + 跑 + 取结果。

    `free_bodies=False` 时零件固定，仿真是"检查有没有穿模"级别的静态验证；
    **动态仿真必须 `free_bodies=True`**（否则零件不动，结果无意义）。
    这个参数没有默认成 True，因为默认自由会让"静态几何检查"变成
    "东西往下掉"，两者结论完全不同。
    """
    import time

    ok, why = available()
    if not ok:
        return SimResult(ok=False, error=why)

    from . import mjcf as mjcf_module
    built = mjcf_module.build(design, params, include_floor=include_floor,
                              free_bodies=free_bodies,
                              weld_parts=weld_parts)
    result = SimResult(warnings=list(built.warnings))
    if built.skipped:
        result.warnings.append(
            f"{len(built.skipped)} 个零件/关节没进仿真"
            f"（{built.skipped[0].get('why', '')[:60]}…）")

    mujoco = _import_mujoco()
    xml = built.xml
    try:
        model = mujoco.MjModel.from_xml_string(xml)
    except Exception as exc:                              # noqa: BLE001
        result.error = f"MJCF 编译失败：{type(exc).__name__}: {exc}"
        # 把 XML 的前几行带上，便于定位（不返回整份，可能很长）
        result.warnings.append("XML 前 5 行：" + xml.splitlines()[0][:120])
        return result

    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    # 记录初始状态。**只用 in_world 的**：weld 模式下板是唯一刚体，
    # 零件不是快照对象（它们在板里跟着动）。
    sim_refs = [p.ref for p in built.parts if p.xml and p.in_world]
    initial: dict[str, tuple[list[float], list[float]]] = {}
    for ref in sim_refs:
        try:
            body_id = model.body(ref).id
        except Exception:                                 # noqa: BLE001
            continue
        initial[ref] = (list(data.xpos[body_id]),
                        list(data.xquat[body_id]))
    if not initial:
        result.error = ("没有任何自由体被找到（body 名与零件 ref 对不上，"
                        "或 free_bodies=False 使零件焊接在世界里）")
        return result

    steps_target = int(duration_s / max(float(model.opt.timestep), 1e-9))
    steps_target = min(steps_target, max_steps)
    traj: dict[str, list[list[float]]] = {ref: [] for ref in initial}
    speed_window: dict[str, list[float]] = {ref: [] for ref in initial}

    wall0 = time.perf_counter()
    for step in range(steps_target):
        mujoco.mj_step(model, data)
        if step % sample_every == 0:
            for ref in initial:
                body_id = model.body(ref).id
                traj[ref].append([round(v, 6) for v in data.xpos[body_id]])
        if step >= steps_target - 100:      # 最后 100 步测"是否还在动"
            for ref in initial:
                body_id = model.body(ref).id
                speed_window[ref].append(float(
                    sum(v * v for v in data.cvel[body_id][:3]) ** 0.5))
    result.wall_time_s = time.perf_counter() - wall0
    result.steps = steps_target
    result.sim_time_s = steps_target * float(model.opt.timestep)

    for ref, (pos0, quat0) in initial.items():
        body_id = model.body(ref).id
        pos1 = [float(v) for v in data.xpos[body_id]]
        quat1 = [float(v) for v in data.xquat[body_id]]
        result.final[ref] = {"pos": [round(v, 6) for v in pos1],
                             "quat": [round(v, 6) for v in quat1]}
        result.max_displacement[ref] = math.dist(pos0, pos1)
        result.max_tilt_deg[ref] = _tilt_deg(quat1)
        window = speed_window[ref] or [0.0]
        result.final_speed[ref] = sum(window) / len(window)
    result.trajectory = traj
    result.ok = True
    return result


def drop_test(*, size_mm: float = 20.0, mass_g: float = 100.0,
              start_z_mm: float = 100.0, duration_s: float = 1.0) -> SimResult:
    """最小可用性证明：一个方块从高处落下，应停在地板上。

    **这是 P16 的第一个验收**（dsh-embagent-docs:docs/execution-plan-v3.md 的止损线）。
    判据（由验收脚本做，不在这个函数里）：
    * 结束高度 ≈ 半边长（不穿地、不弹飞）
    * 没有 NaN
    """
    from .model import Board, DesignModel, Part, Placement

    design = DesignModel(
        id="drop_test", title="单件落地",
        board=Board(kind="custom", size_mm={"w": 100, "h": 100}))
    design.parts = [Part(
        ref="BOX",
        kind="box",
        size_mm={"w": size_mm, "h": size_mm, "d": size_mm},
        physical_source="datasheet",
        placement=Placement(x_mm=0.0, y_mm=0.0, z_mm=start_z_mm),
        mass_g=mass_g,
    )]
    return run(design, duration_s=duration_s, free_bodies=True,
               include_floor=True)


def hover_test(*, mass_g: float = 1000.0, duration_s: float = 2.0,
               thrust_n: float | None = None) -> dict[str, Any]:
    """四旋翼悬停最小证明：一个自由体，恒定向上推力抵消重力。

    **不使用 MuJoCo 的多旋翼模型**，只验证"推力=重力时高度保持"这一条
    物理链条是否通。真正的四旋翼（桨的 C_T、力矩、姿态控制）在 P18/P20。

    `thrust_n` 不给时自动用 `mass × g`。
    """
    from .model import Board, DesignModel, Part, Placement

    ok, why = available()
    if not ok:
        return {"ok": False, "error": why}

    mass_kg = mass_g / 1000.0
    thrust = thrust_n if thrust_n is not None else mass_kg * 9.81

    design = DesignModel(
        id="hover_test", title="悬停",
        board=Board(kind="custom", size_mm={"w": 100, "h": 100}))
    design.parts = [Part(
        ref="DRONE", kind="quad", size_mm={"w": 100, "h": 100, "d": 20},
        physical_source="datasheet",
        placement=Placement(x_mm=0.0, y_mm=0.0, z_mm=500.0),
        mass_g=mass_g,
    )]
    base = run(design, duration_s=duration_s, free_bodies=True,
               include_floor=False)
    if not base.ok:
        return {"ok": False, "error": base.error}

    # 加推力需要改 XML：这里直接拼一个最小悬停模型，不走通用生成器
    # （通用生成器的 actuator 语义是"主控驱动谁"，不是"恒定推力"）
    xml = f'''<mujoco model="hover">
  <option timestep="5e-4" gravity="0 0 -9.81" integrator="implicitfast"/>
  <worldbody>
    <body name="DRONE" pos="0 0 0.5">
      <freejoint name="DRONE_free"/>
      <geom name="DRONE_g" type="box" size="0.05 0.05 0.01"
            mass="{mass_kg:.6g}" condim="3"/>
    </body>
  </worldbody>
  <actuator>
    <general name="lift" joint="DRONE_free" gear="0 0 1 0 0 0"
             ctrlrange="0 {thrust:.6g}" forcerange="-{thrust:.6g} {thrust:.6g}"/>
  </actuator>
</mujoco>'''
    # 注：freejoint 的 6 个自由度里，gear 的第 3 个分量是 z 平移
    mujoco = _import_mujoco()
    try:
        model = mujoco.MjModel.from_xml_string(xml)
        data = mujoco.MjData(model)
        data.ctrl[0] = thrust
        steps = int(duration_s / float(model.opt.timestep))
        zs: list[float] = []
        for step in range(steps):
            mujoco.mj_step(model, data)
            if step % 50 == 0:
                zs.append(float(data.qpos[2]))
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "mass_kg": mass_kg,
        "thrust_n": thrust,
        "gravity_n": mass_kg * 9.81,
        "z_start_m": 0.5,
        "z_end_m": zs[-1] if zs else None,
        "z_range_m": (max(zs) - min(zs)) if zs else None,
        "samples": len(zs),
    }


def describe() -> dict[str, Any]:
    ok, why = available()
    return {
        "module": "dsh_embagent.design.mujoco_backend",
        "available": ok,
        "detail": why,
        "local_lib": str(LOCAL_LIB),
        "flip_angle_deg": FLIP_ANGLE_DEG,
        "note": ("结果只有物理量，没有结论——判定留给 P18；"
                 "free_bodies=False 是静态检查，动态仿真必须显式打开"),
    }