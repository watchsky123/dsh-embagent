"""一次性生成 sim_params_default.json —— 从 RoboBuilder 全搬 136 项。

为什么用脚本生成而不是手写 JSON：136 项手抄必错，而源文件是机器可读的。
生成后 JSON 成为**我们自己的数据**（可改可扩），不再是外部依赖。

分组依据（`GROUP`）不是按前缀硬切，而是按**物理域**——
同一域的参数一起调、一起失效，这是校准时的最小单位。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

SRC = Path(r"C:\Users\<user>\Desktop\RoboBuilder-main\robobuilder\sim\params\initial.json")
OUT = Path(__file__).resolve().parent.parent / "design" / "sim_params_default.json"

#: 参数 → (物理域, 单位, 一句话物理含义, 是否通用)
#:
#: `universal=True` 指"任何刚体仿真都用得上"；False 指"只有该机构存在时才有意义"。
#: 这个区分是**通用化的关键**：无人机用不到 `stud_*`，但参数表里保留它们，
#: 只是按 `universal` 与 `requires` 标注——**不删，标记**。
SPEC: dict[str, tuple[str, str, str, bool]] = {
    # ---- 求解器 ----
    "dt":                       ("solver", "s", "仿真步长；越小越准越慢", True),
    "seed":                     ("solver", "-", "随机种子（凸柱强度分布用）", True),
    "stiff_tc_min_dt":          ("solver", "步", "刚化关节的时间常数下限（防轻件失稳）", True),
    "spawn_touch_m":            ("solver", "m", "生成时这么近的件不加接触（贴合装配避免互顶）", True),
    "stiff_imp":                ("solver", "-", "刚化约束的阻抗（近 1 表示靠参考弹簧给刚度）", True),
    # ---- 驱动（电机）----
    "m_stall_nm":               ("motor", "N·m", "M 电机满占空比堵转扭矩", True),
    "m_no_load_rad_s":          ("motor", "rad/s", "M 电机空载转速", True),
    "l_stall_nm":               ("motor", "N·m", "L 电机满占空比堵转扭矩", True),
    "motor_friction":           ("motor", "-", "运行摩擦（占堵转扭矩的比例）", True),
    "motor_static":             ("motor", "-", "启动静摩擦（占堵转扭矩的比例）", True),
    "level_exp":                ("motor", "-", "档位→驱动比曲线指数 (L/7)**this", True),
    "rotor_j":                  ("motor", "kg·m²", "折算到输出轴的转子惯量", True),
    "motor_i_max":              ("motor", "-", "每路输出电流上限（占堵转电流比例，0=无限制）", True),
    "supply_r":                 ("motor", "-", "电池+接收器内阻/单电机绕组电阻（>0 时共用电源会互相拖垮）", True),
    "pwm_beta":                 ("motor", "-", "反电动势随占空比的指数修正 duty**this", False),
    "ir_latency_s":             ("motor", "s", "红外指令延迟", False),
    # ---- 齿轮啮合 ----
    "mesh_drag_nm":             ("mesh", "N·m", "啮合齿轮副的空载阻力（作用在慢轴）", False),
    "mesh_mu":                  ("mesh", "-", "齿面滑动损耗 drag += mu·齿间力·半径", False),
    "mesh_mixed_k":             ("mesh", "-", "双斜齿与直齿啮合时的齿力承载系数", False),
    "mesh_axial_k":             ("mesh", "-", "双斜齿当直齿用时的轴向推力/齿力", False),
    "mesh_cap_n":               ("mesh", "N", "完全啮合时跳齿前的齿力上限", False),
    "mesh_cap_z_exp":           ("mesh", "-", "跳齿力随(较小齿数/12)**this 缩放", False),
    "mesh_slip_frac":           ("mesh", "-", "打滑时的平均齿力（占跳齿力比例）", False),
    "mesh_reengage":            ("mesh", "-", "脱开后重新啮合是棘轮式还是直接锁死", False),
    "mesh_clutch":              ("mesh", "-", "把啮合当隐式滑动离合器（防一步内传递无界冲量）", False),
    "mesh_clutch_imp":          ("mesh", "-", "离合粘着摩擦的阻抗", False),
    "mesh_lock_v":              ("mesh", "m/s", "棘轮重新锁死所需的齿面滑动速度下限", False),
    "mesh_slip_v":              ("mesh", "m/s", "超过此齿面速度算棘轮", False),
    "mesh_mixed_gap_mm":        ("mesh", "mm", "双斜齿与直齿的间隙→齿力衰减", False),
    "mesh_solref":              ("mesh", "s", "啮合约束的软度", False),
    "addendum_mm":              ("mesh", "mm", "齿顶高（决定节圆与传动比）", False),
    "pressure_deg":             ("mesh", "°", "压力角（齿形）", False),
    "engage_off":               ("mesh", "-", "啮合度低于此值脱开", False),
    # ---- 凸柱连接（乐高式 stud 连接）----
    "stud_pull_n":              ("stud", "N", "单个凸柱的抗拔力", False),
    "stud_pull_brick_n":        ("stud", "N", "板与砖之间的抗拔力（管更紧）", False),
    "stud_push_n":              ("stud", "N", "把拔出的凸柱压回去的力", False),
    "stud_height_mm":           ("stud", "mm", "滑出这么远就脱开", False),
    "stud_slip_k":              ("stud", "N/m", "滑移增量 = 超出力/此值", False),
    "stud_slip_weaken":         ("stud", "-", "部分滑出后强度按比例保留（0=全强度直到脱开）", False),
    "stud_slip_vmax":           ("stud", "m/s", "凸柱滑出的最快速度（冲击尖峰不会弹出）", False),
    "stud_shear_n":             ("stud", "N", "剪断凸柱的剪切载荷", False),
    "stud_rock_nmm":            ("stud", "N·mm", "单个凸柱在孔内抗摇摆的力矩", False),
    "stud_r_m":                 ("stud", "m", "剥离力臂", False),
    "stud_peel_k":              ("stud", "-", "被撬开时能承受直线拉力的倍数", False),
    "stud_spread":              ("stud", "-", "凸柱之间的强度离散度（标准差，比例）", False),
    "stud_load_tau":            ("stud", "s", "凸柱载荷的平滑时间（冲击只有毫秒）", False),
    "stud_k_n_m":               ("stud", "N/m", ">0 时每个凸柱点约束用此刚度（否则按同步长）", False),
    "stud_solref":              ("stud", "s", "凸柱点约束的软度（弯曲）", False),
    "stud_solimp":              ("stud", "-", "凸柱约束阻抗", False),
    "stud_torquescale":         ("stud", "-", "旋转行按凸柱半径缩放", False),
    # ---- 轴承 / 轴 ----
    "axle_fric_nm":             ("axle", "N·m", "轴在孔内转动的摩擦", True),
    "axle_slide":               ("axle", "-", "轴能否沿孔滑动", True),
    "axle_slide_fric_n":        ("axle", "N", "轴滑动摩擦", True),
    "axle_load_fric_m":         ("axle", "N·m/N", "自由轮轴随径向载荷增加的摩擦", False),
    "axle_load_tau_s":          ("axle", "s", "径向载荷的低通时间（防冲击尖峰锁死自由轮）", False),
    "shaft_grip_n":             ("axle", "N", "十字孔里的轴超过此力被拔出", False),
    "slide_stop_solref":        ("axle", "s", "轴端止挡的软度", False),
    "bearing_k_n_m":            ("axle", "N/m", ">0 时轴/销在孔内用此刚度", True),
    "bearing_solref":           ("axle", "s", "轴承约束软度", True),
    "bearing_solimp":           ("axle", "-", "轴承约束阻抗", True),
    "pin_slide_n":              ("axle", "N", "把摩擦销沿孔推动的力", False),
    "pin_friction_nm":          ("axle", "N·m", "转动摩擦销的力矩", False),
    # ---- 轮胎与地面 ----
    "mu_tyre_floor":            ("tyre", "-", "轮胎对地面摩擦系数", True),
    "mu_tyre_ramp":             ("tyre", "-", "轮胎对斜坡摩擦系数", True),
    "mu_tyre_rod":              ("tyre", "-", "轮胎对打印凸条（0=用 mu_tyre_floor）", False),
    "mu_board":                 ("tyre", "-", "硬塑料对泡芯板（板面滑移角）", True),
    "mu_rod":                   ("tyre", "-", "塑料对打印 PLA 凸条（0=mu_board）", False),
    "mu_abs":                   ("tyre", "-", "ABS 摩擦系数", True),
    "tyre_torsion_m":           ("tyre", "m", "轮胎扭转摩擦长度 torque = this·法向力", False),
    "tyre_fillet_mm":           ("tyre", "mm", "轮胎肩部圆角半径（0=锐边圆柱）", False),
    "tyre_solref":              ("tyre", "s", "橡胶轮胎接触软度（冲击下几 mm 压缩）", True),
    "tyre_mu_exp":              ("tyre", "-", "轮胎摩擦随实测 mu 缩放的指数（0=所有轮胎一样）", False),
    "tyre_mu_ref":              ("tyre", "-", "参考轮胎的 mu 值", False),
    "tyre_crr":                 ("tyre", "-", "滚阻系数（阻力=crr·载荷）", True),
    "shrink_mm":                ("tyre", "mm", "碰撞盒收缩量（贴合件不互顶）", True),
    # ---- 链条 ----
    "chain_cap_n":              ("chain", "N", "链条跳齿的张力上限", False),
    "chain_cap_n_per_tooth":    ("chain", "N/齿", ">0 时按较小链轮齿数算跳齿张力", False),
    "chain_engage_mm":          ("chain", "mm", "(近/远/轴向) 链轮距离在窗口内才传动", False),
    "chain_jam_slack_mm":       ("chain", "mm", "松弛超过此值的链条卡死", False),
    "chain_jam_nm":             ("chain", "N·m", "卡死链条对链轮的摩擦力矩", False),
    # ---- 绳索 / 线缆 ----
    "cord_tension_n":           ("cord", "N", "绳的张力（点载荷下的下垂量由它决定）", False),
    "cord_dynamic":             ("cord", "-", "绳是否在载荷下变形", False),
    "cord_sag0_mm":             ("cord", "mm", "空载中跨下垂量", False),
    "cord_tau":                 ("cord", "s", "载荷平滑时间", False),
    "cord_tau_s":               ("cord", "s", "（链式）铰链阻尼/刚度", False),
    "cord_damp_ns_m":           ("cord", "N·s/m", "（链式）节点滑动阻尼", False),
    "cord_nodes":               ("cord", "-", "按节点分配载荷（多点）而非质心单点", False),
    "cord_vertical_load":       ("cord", "-", "按垂直接触合力变形", False),
    "cord_bodies":              ("cord", "-", "绳作为动态链段而非 mocap", False),
    "cord_tyre_solref":         ("cord", "s", "轮胎-绳接触软度（空=tyre_solref）", False),
    "cables":                   ("cable", "-", "是否仿真线缆", False),
    "cable_taped":              ("cable", "-", "线缆沿车身固定（无自由环）", False),
    "cable_mass_g":             ("cable", "g", "单根电机线质量", False),
    "cable_r_m":                ("cable", "m", "扁线等效圆半径", False),
    "cable_bend":               ("cable", "Pa", "线的弯曲模量", False),
    "cable_twist":              ("cable", "-", "线的扭转模量", False),
    "cable_damping":            ("cable", "-", "线的阻尼", False),
    "cable_contact":            ("cable", "-", "自由线是否与场地接触", False),
    "cable_armature":           ("cable", "kg·m²", "每个线关节的额外惯量（防数值爆炸）", False),
    "plug_pull_n":              ("cable", "N", "电源插头拔脱力", False),
    # ---- 舵机 ----
    "servo_pos":                ("servo", "-", "舵机按位置伺服模型驱动", False),
    "servo_torque_nm":          ("servo", "N·m", "舵机驱动扭矩", False),
    "servo_w0_rad_s":           ("servo", "rad/s", "舵机空载角速度", False),
    "servo_dead_s":             ("servo", "s", "指令→动作延迟", False),
    "servo_block_s":            ("servo", "s", "堵转这么久后放弃（直到下条指令）", False),
    "servo_tol_deg":            ("servo", "°", "距目标这么近就停止驱动", False),
    "servo_kp_s":               ("servo", "1/s", "接近段：速度指令 = kp·角度误差", False),
    "servo_kv":                 ("servo", "-", "速度环：扭矩比例/速度误差比例", False),
    # ---- 发条 / 棘轮 ----
    "pullback_model":           ("pullback", "-", "发条电机模型启用", False),
    "pullback_stall_nm":        ("pullback", "N·m", "发条全绕紧扭矩", False),
    "pullback_no_load_rad_s":   ("pullback", "rad/s", "发条空载转速", False),
    "pullback_turns":           ("pullback", "圈", "满绕输出圈数", False),
    "pullback_hold_nm":         ("pullback", "N·m", "释放前保持扭矩", False),
    # ---- 履带 / 齿条 ----
    "tread_mu_3711":            ("tread", "-", "履带与地面摩擦（链节型号 3711）", False),
    "tread_mu_3873":            ("tread", "-", "履带摩擦（3873）", False),
    "tread_mu_57518":           ("tread", "-", "履带摩擦（57518）", False),
    "tread_spacing_mm":         ("tread", "mm", "履带节点间距", False),
    "tread_model":              ("tread", "-", "履带模型启用", False),
    "rack_slide":               ("rack", "-", "齿条滑块模型（x = r·θ）", False),
    "rack_lock_v":              ("rack", "-", "齿条自锁速度", False),
    "rack_pinion_armature":     ("rack", "kg·m²", "齿条小齿轮惯量", False),
    "idler_hinge":              ("rack", "-", "圆孔齿轮绕轴自有铰链", False),
    "worm_lock_k":              ("mesh", "-", "蜗轮蜗杆的自锁系数（>0 时反向不驱动）", False),
    "chain_link_mass_g":        ("chain", "g", "单个链节质量", False),
    # ---- 场地 ----
    "board_solref":             ("course", "s", "场地件接触软度", True),
    "course_dynamic":           ("course", "-", "动态场地件（斜坡/跷跷板）", False),
    "step_nose_mm":             ("course", "mm", "台阶前缘圆角半径", False),
    "settle_s":                 ("course", "s", "开跑前静置时间", True),
    "judge_at_goal_s":          ("course", "s", "到达后多久判终点状态（是否立住）", True),
    # ---- 程序 / 判定 ----
    "program_late_s":           ("program", "s", "（可靠性扰动）首条之后的指令全部延后这么多", True),
    "no_program_all7":          ("program", "-", "无程序时所有电机从 t=0 起 +7", False),
    "all_skips_pullback":       ("program", "-", "\"all\" 不指挥发条电机", False),
    # ---- 双击 / 别的 ----
    "cv_model":                 ("mechanism", "-", "等速万向节模型", False),
    "diff_model":               ("mechanism", "-", "差速器模型", False),
    "shock_model":              ("mechanism", "-", "减震模型", False),
    "knob_r_mm":                ("mechanism", "mm", "旋钮半径", False),
    "pullback_model_on":        ("mechanism", "-", "（别名）发条启用", False),
}

#: 这些键在源 JSON 里存在但语义与上面重复，生成时跳过（保持一一对应）
ALIASES = {"pullback_model_on"}

#: 值的类型归一：元组 → 列表（JSON 友好），并记下"是元组"以还原 MuJoCo 需要的形状
def normalise(value):
    if isinstance(value, tuple):
        return list(value)
    return value


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raw = json.loads(SRC.read_text(encoding="utf-8"))
    params: dict = raw["params"]

    out_params: dict[str, dict] = {}
    missing: list[str] = []
    for key, value in params.items():
        spec = SPEC.get(key)
        if spec is None:
            missing.append(key)
            continue
        group, unit, note, universal = spec
        out_params[key] = {
            "value": normalise(value),
            "group": group,
            "unit": unit,
            "note": note,
            "universal": universal,
            # 默认全部标 public：它们的来源是 RoboBuilder 的公开零件数据，
            # **不是我们实测的**。要参与硬判定必须走 promote（P18 的信任级）。
            "source": "public",
        }

    unknown = sorted(set(SPEC) - set(out_params) - ALIASES)
    payload = {
        "schema": "hwv-sim-params/1",
        "label": "initial",
        "origin": "ported from RoboBuilder v1 (MIT) initial.json — public part data, not measured by us",
        "note": (
            "**全量搬运**：RoboBuilder 的 136 项物理参数，按物理域分组。"
            "universal=True 的项任何刚体仿真都用得上（dt/mu/dt 之类）；"
            "universal=False 的项只有对应机构存在时才有意义（stud 只对凸柱式连接、"
            "mesh 只对齿轮、chain 只对链条）。**不删，标记** —— "
            "无人机用不到 stud_*，但参数表保留它们，P16 只在设计里真的出现 "
            "stud 关系时才读那组。"
            "全部 source=public：这不是我们实测的值，参与硬判定前必须 promote。"
        ),
        "groups": sorted({v["group"] for v in out_params.values()}),
        "params": out_params,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")

    print(f"写出 {OUT.name}：{len(out_params)} 项，{len(payload['groups'])} 个域")
    if missing:
        print(f"**SPEC 未覆盖 {len(missing)} 项**（这些会被丢弃）：")
        for key in missing:
            print(f"  - {key}")
    if unknown:
        print(f"**SPEC 里有 {len(unknown)} 项源里没有**（拼写错？）：{unknown}")
    return 0 if not missing and not unknown else 1


if __name__ == "__main__":
    raise SystemExit(main())