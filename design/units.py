"""单元层 —— 把一份设计切成「功能单元」，并把每个单元的角色说出来。

## 这一层解决什么问题

多板工程的 `design.json` 是一张**平铺的元件表**（316 个元件）。人能看出
"这块是主控板、那块是图传板"，但机器看不出来。于是：

* 板间链路检查无从下手（"USART2 接谁"没有"谁"这个概念）
* 交接单不能说"这块板没验证"，只能说"141 个元件里有些没验证"

单元层补的就是"谁"这个中间概念。

## 为什么不用 AI，用纯结构

`Placement.board_id` 已经在导入时把元件归属到板上了（实测
`示例-多板` 有 8 块板，元件分布 142/52/32/29/24/22/11/4，
加起来正好 316）。分组是**已有事实**，不需要推断。

需要推断的只有一件事：**这块板是干什么的**。

## 角色怎么判 —— 只看结构，并且必须给证据

不查元件型号（实测 `kind` 全是 EasyEDA 哈希），只看这块板上的
**具名网络**。理由：网络名是人写下的意图，是这份数据里唯一还带语义
的东西。实测 `示例-多板` 的主板有 64 条具名网，其余 7 块板
**0 条**——这个差别本身就是结论：

* 有具名网的板 → 可以判角色，并给出"是哪些网判出来的"
* 没有网表的板 → **如实 `unknown`**，并说明"这块板没有任何网表，
    判不出是缺数据"

**不许因为 8 块板里 7 块判不出，就把它们凑一个默认角色。**
那会让交接单看起来"8 个单元都识别了"，而实际只有 1 个有依据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .model import Board, DesignModel, Part

#: 自动生成的网名，**没有语义**。与 `justify` 保持同一套判据。
_AUTO_NET_RE = re.compile(r"^\$|^[wW]\d+$")

#: 网络名关键词 → 单元角色。**顺序即优先级**：一条网命中多个时取先命中的。
#:
#: 每个词条都要能说出"为什么这个词说明这种板"，否则就是猜。写在 `why` 里。
_ROLE_HINTS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("mcu", ("osc_in", "osc_out", "boot0", "boot1", "swdio", "swclk",
             "nrst", "reset"),
     "板上出现晶振/BOOT/SWD/复位网络 —— 只有 MCU 需要这些"),
    ("radio", ("rf-", "rf_", "ant", "lora", "wifi", "rf_nrst"),
     "板上有射频相关网络（RF/ANT/LORA/WIFI）"),
    ("power", ("vbat", "b+", "b-", "vin", "lgs_vcc", "chrg", "charge",
               "battery", "vbus"),
     "板上有电池/充电/输入电源网络"),
    ("storage", ("flash", "sd_", "sdcard", "eeprom", "nand"),
     "板上有存储器件网络（FLASH/SD/EEPROM）"),
    ("motor", ("motor", "esc", "servo", "pwm_out"),
     "板上有电机/电调/舵机驱动网络"),
    ("sensor", ("imu", "gyro", "accel", "mag_", "rssi", "adc_", "bat_"),
     "板上有传感器信号网络（IMU/GYRO/ADC/RSSI）"),
    ("user_io", ("btn_", "led_", "key_", "beeper", "buzzer"),
     "板上有按键/指示灯/蜂鸣器网络"),
)

#: 角色优先级（同样的命中数量下，谁更像"这块板的主职能"）。
#: 主控板常常同时有 radio/power/user_io 的网，但它的主职能是 mcu。
_ROLE_PRIORITY = ("mcu", "radio", "power", "sensor", "motor",
                  "storage", "user_io")


@dataclass
class Unit:
    """一个功能单元 = 一块板（+ 它上面的元件集合）。

    `role` 只有一个（主职能），`roles` 是全部命中的标签。分开是因为
    交接单上要能写"这是主控板"这一句话，同时不能丢掉"它也带用户接口"。
    """

    id: str
    board_id: str = ""
    role: str = "unknown"
    #: 全部命中的角色标签（按优先级排序）
    roles: list[str] = field(default_factory=list)
    #: 命中证据：`net:OSC_IN→mcu`
    evidence: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    named_nets: list[str] = field(default_factory=list)
    #: 有网表的元件数 / 元件总数
    parts_with_nets: int = 0
    #: `ok` / `inferred_by_topology` / `unknown`
    confidence: str = "unknown"
    why: str = ""
    size_mm: dict[str, float] = field(default_factory=dict)
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "board_id": self.board_id,
            "name": self.name,
            "role": self.role,
            "roles": list(self.roles),
            "confidence": self.confidence,
            "why": self.why,
            "evidence": list(self.evidence),
            "parts": list(self.parts),
            "part_count": len(self.parts),
            "parts_with_nets": self.parts_with_nets,
            "named_nets": list(self.named_nets),
            "size_mm": dict(self.size_mm),
        }


# ---------------------------------------------------------------------------
# 推导
# ---------------------------------------------------------------------------

def named_nets_of(design: DesignModel, parts: list[Part]) -> list[str]:
    """这组元件接触到的**具名**网络（排除自动名）。

    只看具名网，因为 `$1N394` / `w1` 是导入器编的序号，读不出意图。
    """
    from . import claims as claims_module

    found: set[str] = set()
    for part in parts:
        for net in claims_module.nets_of(design, part.ref):
            if _AUTO_NET_RE.match(str(net)):
                continue
            found.add(str(net))
    return sorted(found)


def _unit_role(named_nets: list[str]) -> tuple[str, list[str], list[str]]:
    """从具名网判这块板的角色。返回 (主角色, 全部标签, 证据)。"""
    lowered = [(n, n.lower()) for n in named_nets]
    hits: dict[str, list[str]] = {}
    reasons: dict[str, str] = {}
    for role, words, why in _ROLE_HINTS:
        for original, low in lowered:
            if any(word in low for word in words):
                hits.setdefault(role, []).append(original)
                reasons.setdefault(role, why)
    if not hits:
        return "unknown", [], []
    ordered = [r for r in _ROLE_PRIORITY if r in hits]
    ordered += [r for r in hits if r not in ordered]
    evidence = [f"net:{net}→{role}" for role in ordered
                for net in hits[role][:4]]
    return ordered[0], ordered, evidence


def _board_unit_id(board: Board, index: int) -> str:
    """单元 id。优先用板 id（它是导入器给的稳定标识）。"""
    raw = str(board.id or "").strip()
    if raw:
        # `PCB#a22effc90c196540` → `unit:a22effc90c196540`
        tail = raw.split("#")[-1]
        return f"unit:{tail}" if tail else f"unit:{raw}"
    return "main" if index == 0 else f"unit{index}"


def derive(design: DesignModel) -> list[Unit]:
    """从 `board_id` 分组反推单元。**纯结构，不用 AI。**

    每块板一个 `Unit`；角色从具名网推导，推不出就 `unknown`。
    绝不因为"板多、角色少"而给某块板补一个默认角色。
    """
    from . import claims as claims_module

    boards = design.board_list()
    units: list[Unit] = []
    for index, board in enumerate(boards):
        members = design.parts_of(board)
        with_nets = sum(1 for p in members
                        if claims_module.nets_of(design, p.ref))
        named = named_nets_of(design, members)
        role, roles, evidence = _unit_role(named)

        if not members:
            confidence = "unknown"
            why = "这块板上没有任何元件，判不出它做什么"
        elif not named:
            confidence = "unknown"
            why = (f"{len(members)} 个元件里只有 {with_nets} 个有网表，"
                   f"且没有任何具名网络——判不出是**缺数据**，"
                   f"不是算法判不出")
        elif role == "unknown":
            confidence = "unknown"
            why = (f"有 {len(named)} 条具名网，但没有一条命中角色词表"
                   f"（{', '.join(named[:6])}）")
        else:
            confidence = "inferred_by_topology"
            why = _role_why(role, evidence)

        units.append(Unit(
            id=_board_unit_id(board, index),
            board_id=str(board.id or ""),
            role=role,
            roles=roles,
            evidence=evidence,
            parts=[p.ref for p in members],
            named_nets=named,
            parts_with_nets=with_nets,
            confidence=confidence,
            why=why,
            size_mm=dict(board.size_mm or {}),
            name=str(board.name or ""),
        ))
    return units


def _role_why(role: str, evidence: list[str]) -> str:
    reason = next((why for r, _w, why in _ROLE_HINTS if r == role), "")
    shown = ", ".join(evidence[:4])
    return f"{reason}；证据：{shown}"


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def report(design: DesignModel) -> dict[str, Any]:
    """给 CLI / 工具用的完整报告。

    ## 两个比例，不是一个（与 `justify` 同一套纪律）

    `ratio` 用全部单元做分母，`ratio_judgeable` 只用**有具名网**的单元。
    实测 `示例-多板`：8 块板里只有 1 块有具名网，于是

        全部口径  1/8 = 12.5%    ← 看起来算法很差
        可判口径  1/1 = 100%     ← 算法其实没问题，差的是数据

    只报一个数会让人去改算法，而真正该做的是补网表。
    """
    units = derive(design)
    total = len(units)
    known = sum(1 for u in units if u.confidence != "unknown")
    ratio = (known / total) if total else 0.0

    judgeable = [u for u in units if u.named_nets]
    judgeable_known = [u for u in judgeable if u.confidence != "unknown"]
    ratio_judgeable = (len(judgeable_known) / len(judgeable)) if judgeable else 0.0

    no_netlist = [u.id for u in units
                  if u.parts and u.parts_with_nets == 0]
    by_role: dict[str, int] = {}
    for unit in units:
        by_role[unit.role] = by_role.get(unit.role, 0) + 1

    summary = (
        f"{total} 块板分成了 {total} 个单元；"
        f"判出角色的 {known} 个（{ratio * 100:.1f}%），"
        f"其中有具名网的 {len(judgeable)} 个里判出 "
        f"{len(judgeable_known)} 个（{ratio_judgeable * 100:.1f}%）。"
    )
    if no_netlist:
        summary += (f"**{len(no_netlist)} 个单元完全没有网表**，"
                    "它们判不出角色是缺数据，不是算法判不出。")

    return {
        "ok": ratio_judgeable > 0.20 and len(judgeable) > 0,
        "multi_board": design.is_multi_board(),
        "total": total,
        "known": known,
        "ratio": round(ratio, 4),
        "judgeable": len(judgeable),
        "judgeable_known": len(judgeable_known),
        "ratio_judgeable": round(ratio_judgeable, 4),
        "threshold": 0.20,
        "by_role": by_role,
        "units": [u.to_dict() for u in units],
        "missing_data": {
            "no_netlist_units": no_netlist,
            "no_netlist_count": len(no_netlist),
            "note": ("这些单元上的元件没有任何网表连接，"
                     "角色无法从拓扑推断。"),
        },
        "summary": summary,
    }


def apply(design: DesignModel) -> list[Unit]:
    """把推导结果写进 `design.units_list`（内存对象）。

    调用方决定要不要落盘。**默认不自动写**：与 `justify` 一致——
    推导结果混进规范模型后，分不清哪条是人说的。
    """
    units = derive(design)
    design.units_list = [u.to_dict() for u in units]
    return units


# ---------------------------------------------------------------------------
# 需求侧：从需求推单元（P6 的 generate 用）
# ---------------------------------------------------------------------------

#: 需求里的角色 → 单元角色。与 `roles.py` 的角色名保持同一套写法，
#: 但映射到**单元**粒度（一块板上可以承载多个需求角色）。
_REQ_ROLE_TO_UNIT: dict[str, str] = {
    "mcu": "mcu",
    "radio": "radio",
    "camera": "sensor",
    "gimbal_pan": "motor",
    "gimbal_tilt": "motor",
    "servo_power": "power",
    "power_in": "power",
    "sensor": "sensor",
    "motor": "motor",
    "display": "user_io",
    "display_peer": "user_io",
    "mount": "mount",
}


def derive_from_req(req: Any) -> list[dict[str, Any]]:
    """从需求推单元划分。**确定性**：同需求同输出。

    规则（刻意简单，因为复杂的规则没法证明确定性）：

      1. 需求里每个角色映射到单元角色（`_REQ_ROLE_TO_UNIT`）
      2. 同单元角色的需求角色**合并**成一个单元（"mcu + radio" 一块板）
      3. `mount` 不占电路板，单独作为机械单元
      4. 没映射到的角色留在 `unresolved`，**不猜**

    返回 `[{id, role, from_roles, needs}]`，按 id 排序，稳定。
    """
    roles = list(getattr(req, "roles", []) or [])
    if isinstance(req, dict):
        roles = list(req.get("roles") or [])

    grouped: dict[str, list[str]] = {}
    unresolved: list[str] = []
    for role in roles:
        text = str(role).strip()
        if not text:
            continue
        unit_role = _REQ_ROLE_TO_UNIT.get(text)
        if unit_role is None:
            unresolved.append(text)
            continue
        grouped.setdefault(unit_role, []).append(text)

    out: list[dict[str, Any]] = []
    for unit_role in _ROLE_PRIORITY + ("mount",):
        if unit_role not in grouped:
            continue
        owners = sorted(grouped[unit_role])
        out.append({
            "id": f"unit:{unit_role}",
            "role": unit_role,
            "from_roles": owners,
            "needs": _unit_needs(unit_role),
        })
    for unit_role in sorted(set(grouped) - set(out_id_role(out))):
        owners = sorted(grouped[unit_role])
        out.append({
            "id": f"unit:{unit_role}",
            "role": unit_role,
            "from_roles": owners,
            "needs": _unit_needs(unit_role),
        })
    return sorted(out, key=lambda u: u["id"])


def out_id_role(units: list[dict[str, Any]]) -> list[str]:
    return [str(u["role"]) for u in units]


#: 单元角色需要的能力清单。P6 的 `capability.candidates` 拿它做过滤。
#:
#: ## 为什么这里比以前短得多

#: 第一版写得很细（`motor: ("pwm","power")`），结果 `servo-sg90` 被
#: 过滤掉了——因为它 `provides={"pwm"}`，`power` 在 `requires` 里。
#: 错在把"消耗什么"（requires）当成了"提供什么"（provides）。
#: 舵机**用** 5V，它不**给** 5V。

#: 现在 `capability` 里有 `fills` 做语义闸门（"这是什么"），
#: 所以 `needs` 只写"必须能接的接口"，能写多短写多短：
#:   * `sensor` 留空 —— 它没有固定的接口要求（I2C 传感器、UART 的 GPS、
#:     SPI 的气压计都存在），靠 `fills` 保证不会选出 MCU
#:   * `motor` 只要 `pwm` —— 供电是 `electrical`/`budget` 该管的事，
#:     不该混进选型过滤
_UNIT_NEEDS: dict[str, tuple[str, ...]] = {
    "mcu": ("uart", "spi", "gpio"),
    "radio": ("spi",),
    "power": ("power",),
    "sensor": (),
    "motor": ("pwm",),
    "storage": ("spi",),
    "user_io": ("gpio",),
    "mount": (),
}


def _unit_needs(role: str) -> list[str]:
    return list(_UNIT_NEEDS.get(role, ()))


# ---------------------------------------------------------------------------
# 自省
# ---------------------------------------------------------------------------

def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.units",
        "basis": "Placement.board_id（导入器已分组，不是推断）",
        "role_source": "板上的具名网络；自动名（$1N394 / w1）不算",
        "roles": [r for r, _w, _why in _ROLE_HINTS],
        "honest": ("没有具名网的板如实记 unknown，"
                   "并说明是缺数据而不是判不出"),
        "writes": ["units_list（仅在显式 apply 时）"],
    }
