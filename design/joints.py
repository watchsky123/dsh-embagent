"""机械关系层 —— 把「零件之间怎么连」变成可检查的数据。

## 这一层解决什么问题

v2 的 `design.json` 只有**电气域**：`nets` 说"谁和谁通电"，
`links` 说"哪块板连哪块板"。但**没有任何东西说"两个零件是靠什么固定在一起的"**。

后果：
* 叠放的两块板（避障小车的传感器板叠在主板上）无法表达，`physical.py`
  的重叠检查会把它们当成错误
* 运动机构（折叠臂、云台、起落架）无法表达，P16 的 MuJoCo 没有输入
* "这个螺丝孔连着哪两个件"只能靠 `ref` 前缀猜

## 为什么学 RoboBuilder 的 `DesignConnection`

它的设计是：

    DesignConnection(child, parent, kind, studs)   # kind: stud/pin/axle/gear...

三条好处，我们照抄：

1. **关系是独立表，不是零件属性** —— 同一条关系属于两端，
   挂任一端都会让"另一端删了"变悬空引用。单列表 + 两端都是 `ref`，
   V0 的引用完整性检查能统一覆盖（和 `nets[].pins` 一样）。
2. **`kind` 决定物理模型** —— P16 按 kind 生成不同的 MJCF 关节。
   kind 猜错 = 物理模型错，所以 kind 是枚举，不是自由字符串。
3. **`source` 三档** —— declared / inferred / imported，与 `links` / `claims` 同语义。

## 我们比它多什么

**它有 132 种零件的完整库存（3000 件），每条连接必须由 agent 显式声明。**
我们有**网表**——很多关系能从电气拓扑反推出来：

| kind | 能不能从现有数据推 | 依据 |
|---|---|---|
| `port` | **能** | 网表里"控制器引脚 → 执行器/传感器" |
| `stack` | **能** | `Placement.z_mm` + 包围盒 xy 重叠 |
| `contact` | 半能 | 包围盒接触（**但板内接触是正常的，误报率高**） |
| `hinge`/`slide` | 不能 | 需要轴线/行程参数 |
| `axle`/`gear`/`chain`/`tread`/`rack`/`pin` | 不能 | 需要轴径/齿数/轨迹 |

**推断结果一律 `source="inferred"`，不参与硬处理**（AGENTS §1.2）。
要参与 P16 仿真必须 `promote()`——这是出口，不是自动生效。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import (JOINT_INFERABLE, DesignError, DesignModel, Joint,
                    Part)

#: 判定"z 相邻"的阈值（mm）。两块板叠放时，板厚+间隙通常 2–8 mm。
#: 实际叠板：排针 2.54–11 mm + 板厚 1.6 mm，间隙常在 4–9 mm。
#: 取 10 mm 覆盖常见叠放；更大的间距（如机架立柱 20+ mm）另行声明。
STACK_GAP_MM = 10.0

#: 判定"xy 重叠"的最小面积（mm²）。小于它算"擦边"，不算叠放。
#: 用面积而不是布尔重叠，避免 0.1mm 的边界误差产生误报。
STACK_MIN_OVERLAP_MM2 = 1.0

#: 控制器类角色的判定。`role` 是导入器的兜底值（实测 `示例-单板` 全是
#: component/module），所以还要看 `kind` 里有没有 MCU 家族词。
_CONTROLLER_ROLES = frozenset({"mcu", "controller", "radio"})
_CONTROLLER_KIND_WORDS = (
    "stm32", "esp32", "esp8266", "arduino", "atmega", "nrf52",
    "rp2040", "ch32", "hc32", "gd32", "mcu", "soc",
)

#: 执行器/传感器类。判断 `port` 关系的另一端。
_ACTUATOR_ROLES = frozenset({"motor", "servo", "actuator", "relay"})
_ACTUATOR_KIND_WORDS = (
    "servo", "motor", "esc", "stepper", "solenoid", "relay",
    "led", "buzzer", "speaker",
)
_SENSOR_KIND_WORDS = (
    "sensor", "imu", "gyro", "accel", "mag", "gps", "neo",
    "bmp", "bme", "sht", "vl53", "tof", "encoder", "adc",
)


#: **主控独有**的信号。一个元件只要命中其中任意 1 条，就极可能是 MCU。
#:
#: 为什么必须是"独有"：第一版用 `^SPI` 判主控，结果 `Lora1`（射频模块）
#: 被当成控制器——因为从设备也连 SPI。实测还出现过用 **GND 网**推 port，
#: 那会把任意两个元件都连起来。
#:
#: 只有主控才会连这些：启动模式、复位、晶振、调试接口。
_HARD_MCU_SIGNALS = (
    r"^BOOT[01]?$",                        # BOOT0 / BOOT1
    r"^NRST$",                             # 精确，不含 RF_NRST 之类
    r"^OSC_(IN|OUT)$",                     # 晶振
    r"^(SWDIO|SWCLK|JTMS|JTCK|JTAG)",      # 调试
    r"^RESET_N?$",
)

#: 引脚名（`PA9`/`PB6`/`PC13`）。单个不算证据，**>=3 个**才算。
_PIN_NAME = r"^P[A-H][0-9]{1,2}$"

#: 电源/地网。**不能作为 port 证据**——它们是公共节点，
#: 任何两个元件都在同一条 GND 上，用它推关系会产出 O(n²) 条假 port。
_POWER_NETS = (
    r"^GND\d*$", r"^VSS\d*$", r"^AGND$", r"^DGND$", r"^PGND$",
    r"^(VCC|VDD|VBUS|VIN|VBAT|VSYS)", r"^\d+(\.\d+)?V\d*$",
    r"^(3V3|5V|12V|24V|1V8|2V5|3V3A|3V3D)", r"^(RF-)?3\.3V$",
    r"^(B\+|B-)$", r"^(LGS_VCC|WIFI3V3|RF-3\.3V)$",
)

#: 外设典型网名。**>=2 条**才算（单条可能是巧合）。
_ACTUATOR_NETS = (r"^LED[_\d]", r"^MOTOR", r"^SERVO", r"^BEEP", r"^BUZZ",
                  r"^PWM_OUT", r"^ESC", r"^RELAY")
_SENSOR_NETS = (r"^ADC_", r"^IMU", r"^GYRO", r"^ACCEL", r"^MAG_",
                r"^VL53", r"^TOF", r"^ENC_", r"^CAM", r"^GPS")


def _is_power_net(net: str) -> bool:
    import re
    for pat in _POWER_NETS:
        if re.search(pat, net, re.IGNORECASE):
            return True
    return False


def _nets_of(design: DesignModel, ref: str) -> list[str]:
    from . import claims as claims_module
    return [str(n) for n in claims_module.nets_of(design, ref)]


def _match_count(nets: list[str], patterns: tuple[str, ...]) -> int:
    import re
    count = 0
    for net in nets:
        for pat in patterns:
            if re.search(pat, net, re.IGNORECASE):
                count += 1
                break
    return count


#: 断言/推断工厂只拿 `part`，但身份判定要看网表。
#: 模块级弱引用桥一下（`claims._DESIGN_OF` 同款）；
#: **不放进 dataclass**，否则不可序列化。
_DESIGN_OF: dict[int, DesignModel] = {}


def _declared_controller(part: Part) -> bool:
    """**明确声明**的主控：role 或 kind 能读出来。

    与网名推断分开，因为两者可信度不同：
    * role/kind 可读 —— 设计者写的意图，**第一档**
    * kind 是哈希 —— 只能靠网名，**第二档**

    实测 `示例-单板` 的 `U1` 是第二档（kind=`4becac96152c70`），
    而合成夹具里的 `role="mcu"` 是第一档。
    """
    if str(part.role).lower() in _CONTROLLER_ROLES:
        return True
    haystack = " ".join(str(x or "").lower() for x in
                        (part.kind, part.label, part.role))
    return any(word in haystack for word in _CONTROLLER_KIND_WORDS)


def find_controllers(design: DesignModel) -> tuple[set[str], list[dict[str, str]]]:
    """全局判定谁是主控。**返回 (主控 refs, 判不出的说明)。**

    ## 为什么必须全局，不能逐件

    实测两次踩坑：

    1. 用 `^SPI` 判主控 → `Lora1`（射频模块）被判成主控（从设备也连 SPI）
    2. 用 `^BOOT0$` 判主控 → `C89`（BOOT 电路里的电容）被判成主控

    第 2 条暴露了本质：**BOOT0 这条网上同时有 MCU、电阻、电容**，
    "连了 BOOT0" 不能证明"我是 MCU"。只有**连得最多**的那个才是。

    判据：统计每个元件连了几条「主控独有信号」网，
    **连得最多且 >=2 条**的那个（并列则都不算）才是主控。
    """
    import re
    from . import claims as claims_module

    notes: list[dict[str, str]] = []

    # **第一档：明确声明的身份**（role/kind 能读出来）。
    # 实测踩坑：只用网名判，夹具里"role=mcu 但没有 BOOT0 网"就判不出。
    # role 是设计者写下的意图，比网名推断更可靠，所以优先。
    declared_controllers = {
        p.ref for p in design.parts if _declared_controller(p)}
    if declared_controllers:
        return declared_controllers, notes

    hard_nets: set[str] = set()
    for net_row in design.nets or []:
        if not isinstance(net_row, dict):
            continue
        net = str(net_row.get("net") or "")
        if _is_power_net(net):
            continue
        if any(re.search(pat, net, re.IGNORECASE) for pat in _HARD_MCU_SIGNALS):
            hard_nets.add(net)

    # 第二档：网名全局判定（kind 是哈希、role 是兜底值时唯一可用）
    hits: dict[str, int] = {}
    for net in hard_nets:
        pins = [p for p in claims_module.pins_on_net(design, net)
                if isinstance(p, dict)]
        for ref in {str(p.get("ref") or "") for p in pins} - {""}:
            hits[ref] = hits.get(ref, 0) + 1

    if not hits:
        notes.append({
            "a": "", "b": "", "kind": "port",
            "why": ("没有任何「主控独有信号」网（BOOT/NRST/OSC/SWD）"
                    "——判不出主控，因此不推 port 关系"),
        })
        return set(), notes

    best = max(hits.values())
    if best < 2:
        notes.append({
            "a": "", "b": "", "kind": "port",
            "why": (f"最好的候选只命中 {best} 条硬特征网（需要 >=2）"
                    f"——证据不足，不推"),
        })
        return set(), notes

    winners = {ref for ref, count in hits.items() if count == best}
    if len(winners) > 1:
        notes.append({
            "a": "", "b": "", "kind": "port",
            "why": (f"{len(winners)} 个元件并列命中 {best} 条硬特征网"
                    f"（{'、'.join(sorted(winners))}）——分不清主控，不推"),
        })
        return set(), notes
    return winners, notes


#: 无源件的 ref 前缀。与 `justify._CATEGORY` 同一套判据。
_PASSIVE_PREFIXES = frozenset({"R", "C", "L", "FB", "D", "ZD", "TVS"})

#: 无源件的 kind 关键词（ref 判不出时用这条）。
_PASSIVE_KIND_WORDS = (
    "resistor", "capacitor", "inductor", "ferrite",
    " c0402", " c0603", " c0805", " r0402", " r0603", " r0805",
)


def _is_passive(part: Part) -> bool:
    """是不是纯无源件（电阻/电容/电感/磁珠/二极管）。

    判据与 `justify.category_of` 同源：先 ref 前缀，再看 kind 词。
    **语义**：无源件"不能被主控驱动"，所以不能当 `port` 的 b 端。
    """
    ref = str(part.ref or "")
    prefix = ""
    for ch in ref:
        if ch.isalpha():
            prefix += ch.upper()
        else:
            break
    if prefix in _PASSIVE_PREFIXES:
        return True
    haystack = " ".join(str(x or "").lower() for x in
                        (part.kind, part.label))
    return any(word in haystack for word in _PASSIVE_KIND_WORDS)


def _peripheral_kind_of_net(net: str) -> str | None:
    """从**网名**判外设类型。判不出返回 None（不猜）。

    用网名而不是元件：`LED_CAR` 这条网说明"这是车灯信号"，
    比推测"Q9 是什么"可靠——Q9 的 kind 是哈希。
    """
    import re
    for pat in _ACTUATOR_NETS:
        if re.search(pat, net, re.IGNORECASE):
            return "actuator"
    for pat in _SENSOR_NETS:
        if re.search(pat, net, re.IGNORECASE):
            return "sensor"
    return None


def _peripheral_kind(part: Part) -> str | None:
    """执行器/传感器的类型。判不出返回 None（**不猜**）。"""
    role = str(part.role).lower()
    if role in _ACTUATOR_ROLES:
        return "actuator"
    haystack = " ".join(str(x or "").lower() for x in
                        (part.kind, part.label, part.role))
    if any(word in haystack for word in _ACTUATOR_KIND_WORDS):
        return "actuator"
    if any(word in haystack for word in _SENSOR_KIND_WORDS):
        return "sensor"
    design = _DESIGN_OF.get(id(part))
    if design is None:
        return None
    nets = [n for n in _nets_of(design, part.ref) if not _is_power_net(n)]
    # **>=2 条**而不是 1：实测第一版用 1 条，把连在 `ADC_LEFT_X` 上的
    # 去耦电容 C90 判成了"传感器"——它只是连在那条网上，不是传感器。
    if _match_count(nets, _ACTUATOR_NETS) >= 2:
        return "actuator"
    if _match_count(nets, _SENSOR_NETS) >= 2:
        return "sensor"
    return None


# ---------------------------------------------------------------------------
# 推断
# ---------------------------------------------------------------------------

@dataclass
class Inference:
    """一次推断的结果：关系 + 没推出的原因。

    **`skipped` 和 `joints` 一样重要**：只报推出了什么，
    会让人以为"没提到的都不存在"。要报"哪些对本来可推但推不出"。
    """

    joints: list[Joint] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "joints": [j.to_dict() for j in self.joints],
            "skipped": list(self.skipped),
            "counts": dict(self.counts),
            "summary": self.summary(),
        }

    def summary(self) -> str:
        if not self.joints:
            return ("没有推出任何机械关系。"
                    "stack 需要 z 分离 + xy 重叠；port 需要网表里"
                    "「控制器引脚 → 执行器/传感器」且两端身份可判。")
        by_kind: dict[str, int] = {}
        for joint in self.joints:
            by_kind[joint.kind] = by_kind.get(joint.kind, 0) + 1
        parts = [f"{k} {v} 条" for k, v in sorted(by_kind.items())]
        out = f"推出 {len(self.joints)} 条关系（{'、'.join(parts)}）"
        if self.skipped:
            out += f"；{len(self.skipped)} 条候选推不出（见 skipped）"
        return out + "。**全部 inferred，不参与硬处理。**"


def _bbox(part: Part) -> tuple[float, float, float, float, float, float] | None:
    """包围盒。无尺寸（全 0）的件返回 None —— 零尺寸没有塞进仿真。"""
    try:
        x0, y0, z0, x1, y1, z1 = part.bbox_mm()
    except (DesignError, KeyError, TypeError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, z0, x1, y1, z1)


def infer_stack(design: DesignModel) -> tuple[list[Joint], list[dict[str, str]]]:
    """叠放：xy 重叠 + z 相邻但不同层。

    ## 为什么不是"任何 z 相邻"

    单板上的所有元件 z 都在同一个平面（板厚上），
    `Placement.z_mm` 对它们是**同一个值**。把它们两两判成"叠放"
    会产出 O(n²) 条假关系（141 件就是 ~1 万条）。

    所以判据是 **z 区间不相交且间隙 <= STACK_GAP_MM**：
    真正叠放的件在**不同的 z 层**，单层板元件 z 区间必然相交。
    """
    joints: list[Joint] = []
    skipped: list[dict[str, str]] = []
    boxes = {p.ref: _bbox(p) for p in design.parts}
    refs = sorted(r for r, b in boxes.items() if b is not None)

    for i, ref_a in enumerate(refs):
        for ref_b in refs[i + 1:]:
            a, b = boxes[ref_a], boxes[ref_b]
            # xy 重叠面积
            ox = min(a[3], b[3]) - max(a[0], b[0])
            oy = min(a[4], b[4]) - max(a[1], b[1])
            if ox <= 0 or oy <= 0:
                continue
            if ox * oy < STACK_MIN_OVERLAP_MM2:
                continue
            # z 区间间隙（不交叠时才有正间隙）
            gap = max(a[2] - b[5], b[2] - a[5])
            if gap <= 0:
                continue                      # z 相交 = 同一层，不是叠放
            if gap > STACK_GAP_MM:
                skipped.append({
                    "a": ref_a, "b": ref_b, "kind": "stack",
                    "why": (f"xy 重叠 {ox * oy:.1f}mm² 但 z 间隙 "
                            f"{gap:.1f}mm > {STACK_GAP_MM}mm"),
                })
                continue
            joints.append(Joint(
                a=ref_a, b=ref_b, kind="stack",
                params={"gap_mm": round(gap, 3),
                        "overlap_mm2": round(ox * oy, 2)},
                source="inferred",
                evidence=(f"xy 重叠 {ox * oy:.1f}mm²，"
                          f"z 间隙 {gap:.2f}mm"),
            ))
    return joints, skipped


def infer_port(design: DesignModel) -> tuple[list[Joint], list[dict[str, str]]]:
    """端口：网表里「控制器 → 执行器/传感器」。

    ## 为什么这条最有价值

    它就是 RoboBuilder 的 `ports: {port → motor}`，但我们**不用手写**——
    网表已经说了谁和谁通电，只要判断两端身份。

    这也是 P20 固件交叉核对的输入：`port` 关系 = "控制器哪个引脚驱动哪个件"。
    """
    from . import claims as claims_module

    by_ref = {p.ref: p for p in design.parts}
    joints: list[Joint] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()

    # 身份判定需要看网表（kind 是哈希时这是唯一线索）。
    # 用模块级桥递 design 进去，finally 清理——**不能泄漏到下一次调用**，
    # 否则会拿着旧设计的 part 引用去判新设计。
    _DESIGN_OF.clear()
    for part in design.parts:
        _DESIGN_OF[id(part)] = design
    try:
        return _infer_port_body(design, by_ref, joints, skipped, seen)
    finally:
        _DESIGN_OF.clear()


def _infer_port_body(design, by_ref, joints, skipped, seen):
    """infer_port 的主体。

    判据（三条同时成立）：
      1. 网不是电源/地
      2. 网在**全局主控**连着的那些网里（`find_controllers` 定的）
      3. **网名**能判出这是执行器/传感器信号（`LED_*`/`MOTOR_*`/`ADC_*`）

    为什么第 3 条必须用网名而不是元件：
    `LED_CAR` 这条网说明"这是车灯信号"，比推测 Q9 是什么可靠得多
    （Q9 的 kind 是哈希 `cf3b2d4e...`，判不出）。
    """
    from . import claims as claims_module

    controllers, notes = find_controllers(design)
    skipped.extend(notes)
    if not controllers:
        return joints, skipped

    for net_row in design.nets or []:
        if not isinstance(net_row, dict):
            continue
        net = str(net_row.get("net") or "")
        if _is_power_net(net):
            continue
        peer_type = _peripheral_kind_of_net(net)
        if peer_type is None:
            continue                      # 网名不像执行器/传感器信号
        pins = [p for p in claims_module.pins_on_net(design, net)
                if isinstance(p, dict)]
        refs = sorted({str(p.get("ref") or "") for p in pins} - {""})
        real = [r for r in refs if r in by_ref]
        controller_here = [r for r in real if r in controllers]
        if not controller_here:
            continue                      # 这条网没有主控
        others = [r for r in real if r not in controllers]
        if not others:
            skipped.append({
                "a": controller_here[0], "b": "", "kind": "port",
                "why": f"网 {net} 上只有主控，没有可驱动的对象",
            })
            continue
        # **b 端不能是纯无源件**：实测 `U1 -> C90` 把滤波电容当成了
        # "被驱动的对象"。用这条关系做 P20 的固件交叉核对会得出
        # "MCU 在驱动一个电容"的错误结论。
        #
        # 所以先挑非无源件；全是无源件就**不推**，记进 skipped 说清原因。
        # 这比推一条语义错误的 port 好——错误的 port 会污染下游。
        drivable = [r for r in others if not _is_passive(by_ref[r])]
        if not drivable:
            skipped.append({
                "a": controller_here[0], "b": others[0], "kind": "port",
                "why": (f"网 {net}（{peer_type} 信号）上除主控外只有无源件"
                        f"（{', '.join(others)}）——看不出被驱动的是什么"),
            })
            continue
        for ref_c in controller_here:
            for ref_x in drivable:
                joint = Joint(
                    a=ref_c, b=ref_x, kind="port",
                    params={"net": net, "peer_type": peer_type},
                    source="inferred",
                    evidence=(f"网 {net}（{peer_type} 信号）同时连 "
                              f"{ref_c}（主控）与 {ref_x}"),
                )
                if joint.key in seen:
                    continue
                seen.add(joint.key)
                joints.append(joint)
    return joints, skipped


def infer_contact(design: DesignModel) -> tuple[list[Joint], list[dict[str, str]]]:
    """接触：包围盒相交（含 z）。

    **默认不调用**——板内接触是正常装配，不是缺陷。见 `infer()` 的注释。
    """
    joints: list[Joint] = []
    skipped: list[dict[str, str]] = []
    boxes = {p.ref: _bbox(p) for p in design.parts}
    refs = sorted(r for r, b in boxes.items() if b is not None)
    for i, ref_a in enumerate(refs):
        for ref_b in refs[i + 1:]:
            a, b = boxes[ref_a], boxes[ref_b]
            ox = min(a[3], b[3]) - max(a[0], b[0])
            oy = min(a[4], b[4]) - max(a[1], b[1])
            oz = min(a[5], b[5]) - max(a[2], b[2])
            if ox <= 0 or oy <= 0 or oz <= 0:
                continue
            joints.append(Joint(
                a=ref_a, b=ref_b, kind="contact",
                params={"overlap_mm3": round(ox * oy * oz, 2)},
                source="inferred",
                evidence=f"包围盒相交 {ox:.2f}×{oy:.2f}×{oz:.2f}mm",
            ))
    return joints, skipped


def infer(design: DesignModel, *,
          kinds: tuple[str, ...] = ("stack", "port")) -> Inference:
    """推断可推的机械关系。**默认推 stack + port，不推 contact。**

    ## 为什么不推 contact

    板内相邻元件"接触"是**正常**的（它们本来就挨着）。把它们判成
    `contact` 关系会产出几百条无信息量的记录（141 件就是上万条），
    而真正的"松动配合"需要间隙数据才能判——现在没有。

    所以 `contact` 只在显式要求时推（`kinds=("contact",)`），
    且报告里必须说明"这可能是正常装配，不是缺陷"。
    """
    unknown = [k for k in kinds if k not in JOINT_INFERABLE]
    if unknown:
        raise DesignError(
            f"这些 kind 不可推断：{', '.join(unknown)}"
            f"（可推断的只有：{', '.join(sorted(JOINT_INFERABLE))}）")

    result = Inference()
    for kind in kinds:
        if kind == "stack":
            found, skipped = infer_stack(design)
        elif kind == "port":
            found, skipped = infer_port(design)
        elif kind == "contact":
            found, skipped = infer_contact(design)
        else:                                   # pragma: no cover
            continue
        result.joints.extend(found)
        result.skipped.extend(skipped)
        result.counts[kind] = len(found)

    # 与已声明的去重：人已经说过的关系不重复推
    declared = {Joint.from_dict(j).key
                for j in (design.joints_list or []) if isinstance(j, dict)}
    result.joints = [j for j in result.joints if j.key not in declared]
    result.counts["overlapping_declared"] = len(declared)
    return result


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def validate(design: DesignModel, joints: list[Joint] | None = None) -> dict[str, Any]:
    """校验关系表。**只校验声明过的（inferred 不判 fail）。**

    四条检查：
      1. `joint.part_ref` —— 两端 ref 必须存在（V0 的一致性，这里先做）
      2. `joint.unique`   —— 同一对零件同一 kind 不能重复
      3. `joint.self`     —— 不能自己连自己（`Joint.__post_init__` 已拦，兜底）
      4. `joint.params`   —— 需要参数的 kind 缺参数时报出来（**不编默认值**）
    """
    table = joints if joints is not None else [
        Joint.from_dict(j) for j in (design.joints_list or [])
        if isinstance(j, dict)]
    known = {p.ref for p in design.parts}
    # 整机实体与 PCB 元件**共用 ref 命名空间**（`design/body.py`）：
    # 「电机—桨」这类关系的两端可能都不是 PCB 元件。只查 `parts` 会把
    # 它们判成悬空引用——那是**假红**，会让人去改对的关节。
    known |= {str(b.get("ref") or "") for b in (design.bodies_list or [])
              if isinstance(b, dict)}
    findings: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    checked = 0

    #: 这些 kind 必须有 `params`，否则 P16 无法生成关节
    requires_params = {
        "gear": ("ratio",),
        "axle": ("diameter_mm",),
        "slide": ("range_mm",),
        "tread": ("spacing_mm",),
        "rack": ("pitch_mm",),
        "hinge": ("range_deg",),
    }

    for joint in table:
        if str(joint.source) == "inferred":
            # inferred 不参与硬检查（AGENTS §1.2）。但它仍然占去重名额。
            seen.setdefault(joint.key, joint.source)
            continue
        checked += 1
        for ref in (joint.a, joint.b):
            if ref not in known:
                findings.append({
                    "check": "joint.part_ref", "ok": False, "severity": "error",
                    "joint": f"{joint.a}-{joint.b}", "ref": ref,
                    "message": f"{ref} 不在设计里（关系悬空）",
                })
        if joint.key in seen and seen[joint.key] == "declared":
            findings.append({
                "check": "joint.unique", "ok": False, "severity": "warning",
                "joint": f"{joint.a}-{joint.b}",
                "message": (f"{joint.a} 与 {joint.b} 的 {joint.kind} 关系重复"
                            f"（无向去重键 {joint.key}）"),
            })
        seen[joint.key] = "declared"
        for name in requires_params.get(joint.kind, ()):
            if name not in joint.params:
                findings.append({
                    "check": "joint.params", "ok": False,
                    "severity": "error",
                    "joint": f"{joint.a}-{joint.b}",
                    "message": (f"{joint.kind} 关系缺少参数 {name}"
                                f"（P16 无法生成关节；不会替你编一个值）"),
                })

    errors = [f for f in findings if f.get("severity") == "error"]
    # **`checked > 0` 不等于"没问题"**：`hinge` 缺 `range_deg` 会产生一条
    # `severity=error` 的 finding（P16 生成不出关节），而第一版只看
    # `checked`，于是 status 仍是 `ok` —— 缺参数被报成了通过（AGENTS §0.1）。
    # 现在按"有没有 error"定档：没声明 → not_ready；有 error → failed。
    return {
        "ok": not errors,
        "checked": checked,
        "inferred_skipped": sum(1 for j in table
                                if str(j.source) == "inferred"),
        "findings": findings,
        "status": ("not_ready" if not checked
                   else ("failed" if errors else "ok")),
        "reason": ("" if checked else
                   "没有声明任何机械关系：四项检查无从执行（不是通过）"),
    }


# ---------------------------------------------------------------------------
# 落地
# ---------------------------------------------------------------------------

def promote(inference: Inference, keys: list[str] | None = None) -> int:
    """把推断结果提升为 `declared`（**人确认后的动作**）。

    `keys=None` 表示全部提升。返回提升条数。
    这是 AGENTS §1.6 要求的"闸门出口"：没有它，推断结果永远用不上。
    """
    wanted = set(keys) if keys is not None else None
    count = 0
    for joint in inference.joints:
        if wanted is not None and joint.key not in wanted:
            continue
        if joint.source == "declared":
            continue
        joint.source = "declared"
        joint.evidence = (joint.evidence + "；已人工确认").strip("；")
        count += 1
    return count


def apply(design: DesignModel, joints: list[Joint], *,
          only_declared: bool = True) -> int:
    """把关系写进 `design.joints_list`。返回写入条数。

    `only_declared=True`（默认）时**只写 declared 的**——
    与 `ioc_links.apply_draft` 同一条纪律：推断不能自动生效。
    """
    existing = list(design.joints_list or [])
    seen = {Joint.from_dict(j).key for j in existing if isinstance(j, dict)}
    added = 0
    for joint in joints:
        if only_declared and joint.source != "declared":
            continue
        if joint.key in seen:
            continue
        existing.append(joint.to_dict())
        seen.add(joint.key)
        added += 1
    design.joints_list = existing
    return added


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def report(design: DesignModel) -> dict[str, Any]:
    """完整报告：声明的关系 + 推断的候选 + 校验结果。"""
    declared = [Joint.from_dict(j) for j in (design.joints_list or [])
                if isinstance(j, dict)]
    inference = infer(design)
    validation = validate(design, declared)

    by_kind: dict[str, int] = {}
    for joint in declared:
        by_kind[joint.kind] = by_kind.get(joint.kind, 0) + 1

    summary = (
        f"声明 {len(declared)} 条机械关系"
        + (f"（{'、'.join(f'{k} {v}' for k, v in sorted(by_kind.items()))}）"
           if by_kind else "")
        + f"；推断出 {len(inference.joints)} 条候选（inferred，不参与硬处理）。"
    )
    if validation["status"] == "not_ready":
        summary += " **没有声明任何关系：检查未执行（不是通过）。**"

    return {
        "project": design.id,
        **validation,
        "declared": [j.to_dict() for j in declared],
        "by_kind": by_kind,
        "inference": inference.to_dict(),
        "summary": summary,
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.joints",
        "basis": "机械关系表（`Joint`），与 `nets`/`links` 并列",
        "kinds": list(JOINT_INFERABLE) + ["（其余必须显式声明）"],
        "inferable": sorted(JOINT_INFERABLE),
        "thresholds": {"stack_gap_mm": STACK_GAP_MM,
                       "stack_min_overlap_mm2": STACK_MIN_OVERLAP_MM2},
        "discipline": ("推断标 inferred、不进硬检查；"
                       "要参与仿真先 promote()"),
        "honest": ("没有声明关系时 status=not_ready，不是通过；"
                   "需要参数的 kind 缺参数报 error，不编默认值"),
    }