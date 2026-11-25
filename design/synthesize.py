"""需求 → 设计：把"想做什么"组装成**引脚经过校验**的 元件+网表+板框。

## 设计立场：模板，不是自由发挥

在这之前，"从需求到网表"这一步只能靠模型自由写 JSON，而 `validate()` 当时
连引脚都不查——写错了没人拦。补了引脚库（`design/pins.py`）之后可以做正确
的事：**把常见接法固化成模板**，模型只负责"选模板 + 填空"，而不是自己
发明连线。

模板的价值在于：每条网表的引脚名都在 `PIN_LIBRARY` 里存在，组装完立刻用
`pins.audit()` 自检。模型写错的引脚会在这里被拦住，而不是流到检查器。

## 覆盖范围（诚实标注）

这是**一小撮常见组合**，不是通用综合器：

| 模板 | 组成 | 用途 |
|---|---|---|
| `blink` | MCU + R + LED | 数字输出驱动指示灯 |
| `button` | MCU + 按键 + R（下拉） | 数字输入 |
| `led-bar` | MCU + n×(R+LED) | 多路指示 |
| `i2c-sensor` | MCU + 模块（VCC/GND/SCL/SDA） | BME280/SSD1306/MPU6050 |
| `ultrasonic` | MCU + HC-SR04 | 测距 |
| `servo` | MCU + 舵机（VCC/GND/SIG） | PWM 输出 |
| `buzzer` | MCU + 蜂鸣器 | 声音 |

它**不做**：模拟电路设计、电源树综合、多板互联、阻抗/EMC 相关布局。
超出这些模板的需求必须回到手工，且眼下没有自动路径——这一点写在这里，
而不是让调用方以为"输个需求就能出板子"。

## 布局

按功能块分行摆：控制器在第 0 行，外设按顺序往后排。间距固定、可复现，
**不是优化过的布局**——`space.*` 检查会如实指出重叠或越界。

@module dsh_embagent.design.synthesize
"""

from __future__ import annotations

from typing import Any

from . import pins as pins_module
from .boards import board_preset
from .catalog import part_size
from .model import DesignModel, DesignError, Part, Placement

# 每个部件的落位间距（mm）。够放进 DIP/模块且不重叠，可复现。
ROW_GAP_MM = 26.0
COL_GAP_MM = 30.0
MARGIN_MM = 4.0
# 紧密排列时元件之间的缝隙。只留够丝印，不做布线空间预留
# （本工具本来就不布线，见 scene.py 的 route_note）。
PACK_GAP_MM = 2.0

# 模板 → 组装函数名。加模板就在这里加一行。
TEMPLATES = {
    "blink": "LED 指示灯：MCU 数字脚 → 限流电阻 → LED → GND",
    "button": "按键输入：MCU 数字脚 ↕ 按键 + 下拉电阻",
    "led-bar": "多路指示灯：若干个 MCU 数字脚各接一组 R+LED",
    "i2c-sensor": "I2C 模块：MCU 的 3V3/GND/SCL/SDA 接到模块同名脚",
    "ultrasonic": "超声波测距：HC-SR04 的 TRIG/ECHO 接 MCU 两个数字脚",
    "servo": "舵机：MCU 数字脚给 SIG，VCC/GND 接电源与地",
    "buzzer": "蜂鸣器：MCU 数字脚驱动，另一端接地",
}

# MCU kind → 它的 3V3/5V/GND/SCL/SDA 引脚名（不同板叫法不同）。
SUPPLY_PINS = {
    "arduino-uno": {"vcc": "5V", "gnd": "GND", "scl": "A5", "sda": "A4"},
    "arduino-nano": {"vcc": "5V", "gnd": "GND", "scl": "A5", "sda": "A4"},
    "arduino-mega": {"vcc": "5V", "gnd": "GND", "scl": "D21", "sda": "D20"},
    "stm32-bluepill": {"vcc": "3V3", "gnd": "GND", "scl": "PB6", "sda": "PB7"},
}


class SynthError(Exception):
    """需求无法用现有模板组装。带一句"为什么"和可选值。"""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SynthError(message)


def _make_part(ref: str, kind: str, *, role: str, label: str,
               x_mm: float, y_mm: float, rail: str = "") -> Part:
    """按 catalog 查尺寸建元件。查不到就是 0 尺寸 + estimated。"""
    info = part_size(kind)
    size = info.get("size_mm") or {}
    electrical: dict[str, Any] = {}
    if rail:
        electrical["rail"] = rail
    return Part(
        ref=ref, kind=kind, label=label, role=role,
        size_mm={"w": float(size.get("w", 0.0)),
                 "h": float(size.get("h", 0.0)),
                 "d": float(size.get("d", 0.0))},
        physical_source=str(info.get("physical_source") or "estimated"),
        placement=Placement(x_mm=round(x_mm, 3), y_mm=round(y_mm, 3),
                            rotation_z=0, mount="board"),
        electrical=electrical,
    )


def _place(parts: list[Part], board_w: float, board_h: float) -> None:
    """把元件按**实际尺寸**紧密排进板内：放不下就换行。

    第一版用固定 30mm 列距/26mm 行距，三个小元件就要 85mm 宽，而 Nano 预设
    只有 44.9mm——于是每张"需求 → 设计"都被撑成畸形板。改用真实足迹宽度
    排队，间距只留 `PACK_GAP_MM`，结果才是"能放进板子就放"。
    """
    cursor_x = MARGIN_MM
    cursor_y = MARGIN_MM
    row_height = 0.0
    usable_w = max(1.0, board_w - MARGIN_MM)
    for part in parts:
        w = float(part.size_mm.get("w", 0.0)) or 1.0
        h = float(part.size_mm.get("h", 0.0)) or 1.0
        if cursor_x + w > usable_w and cursor_x > MARGIN_MM:
            cursor_x = MARGIN_MM
            cursor_y += row_height + PACK_GAP_MM
            row_height = 0.0
        part.placement.x_mm = round(cursor_x, 3)
        part.placement.y_mm = round(cursor_y, 3)
        cursor_x += w + PACK_GAP_MM
        row_height = max(row_height, h)


def _fits(parts: list[Part], board_w: float, board_h: float) -> bool:
    for part in parts:
        x0, y0, x1, y1 = part.footprint_mm()
        if x1 > board_w - MARGIN_MM / 2 or y1 > board_h - MARGIN_MM / 2:
            return False
    return True


def _board_for(board_kind: str, *, min_w: float, min_h: float) -> Any:
    """选板：预设够大就用预设，不够就按需扩到"能放下"。"""
    preset = board_preset(board_kind)
    width = float(preset.size_mm.get("w", 0.0))
    height = float(preset.size_mm.get("h", 0.0))
    if width >= min_w and height >= min_h:
        return preset
    width = max(width, min_w)
    height = max(height, min_h)
    # 扩板：保留 kind/名字，但轮廓按新尺寸重算，明确标这是推导出来的。
    outline = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    return type(preset)(
        kind=preset.kind, name=f"{preset.name}（扩到 {width:.0f}×{height:.0f}mm）",
        outline=outline, size_mm={"w": width, "h": height},
        thickness_mm=preset.thickness_mm,
        mount_holes=[], px_per_mm=preset.px_per_mm)


def _digital_pin(mcu_kind: str, wanted: str, *, reserved: set[str]) -> str:
    """校验并归一化一个数字脚名。**引脚必须真的存在于该 MCU 的引脚表里。**"""
    table, source = pins_module.resolve(type("P", (), {"kind": mcu_kind})())
    _require(source != "unknown",
             f"没有 {mcu_kind} 的引脚表，无法校验引脚名")
    index = pins_module.index_of(type("P", (), {"kind": mcu_kind})())
    key = pins_module.normalise(wanted)
    _require(key in index,
             f"{mcu_kind} 没有引脚 {wanted!r}；可用："
             + "、".join(sorted(index.values())[:22]) + " …")
    if key in reserved:
        raise SynthError(
            f"引脚 {wanted} 已被占用（供电/I2C 专用），换一个数字脚："
            + "、".join(p for p in ("D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9")
                        if pins_module.normalise(p) not in reserved))
    return index[key]


def _supply_for(mcu_kind: str) -> dict[str, str]:
    _require(mcu_kind in SUPPLY_PINS,
             f"没有 {mcu_kind} 的供电脚约定；支持的："
             + "、".join(sorted(SUPPLY_PINS)))
    return SUPPLY_PINS[mcu_kind]


def _build_blink(spec: dict[str, Any]) -> tuple[list[Part], list[dict[str, Any]]]:
    mcu_kind = str(spec.get("mcu") or "arduino-nano")
    supply = _supply_for(mcu_kind)
    reserved = {pins_module.normalise(v) for v in supply.values()}
    pin = _digital_pin(mcu_kind, str(spec.get("pin") or "D13"), reserved=reserved)
    ohm = str(spec.get("ohm") or "220")

    mcu = _make_part("U1", mcu_kind, role="module", label="MCU",
                     x_mm=MARGIN_MM, y_mm=MARGIN_MM, rail="logic")
    res = _make_part("R1", f"resistor-{ohm}" if f"resistor-{ohm}" in
                     {"resistor-220", "resistor-1k", "resistor-10k"} else "resistor",
                     role="component", label=f"{ohm}Ω",
                     x_mm=MARGIN_MM + COL_GAP_MM * 1.6, y_mm=MARGIN_MM, rail="")
    led = _make_part("D1", "led", role="component", label="LED",
                     x_mm=MARGIN_MM + COL_GAP_MM * 2.4, y_mm=MARGIN_MM, rail="")
    nets = [
        {"net": pin, "pins": [{"ref": "U1", "pin": pin}, {"ref": "R1", "pin": "1"}]},
        {"net": "LED_A", "pins": [{"ref": "R1", "pin": "2"},
                                  {"ref": "D1", "pin": "A"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": supply["gnd"]},
                                {"ref": "D1", "pin": "K"}]},
    ]
    return [mcu, res, led], nets


def _build_button(spec: dict[str, Any]) -> tuple[list[Part], list[dict[str, Any]]]:
    mcu_kind = str(spec.get("mcu") or "arduino-nano")
    supply = _supply_for(mcu_kind)
    reserved = {pins_module.normalise(v) for v in supply.values()}
    pin = _digital_pin(mcu_kind, str(spec.get("pin") or "D2"), reserved=reserved)

    mcu = _make_part("U1", mcu_kind, role="module", label="MCU",
                     x_mm=MARGIN_MM, y_mm=MARGIN_MM, rail="logic")
    res = _make_part("R1", "resistor-10k", role="component", label="10kΩ 下拉",
                     x_mm=MARGIN_MM + COL_GAP_MM * 1.6, y_mm=MARGIN_MM)
    btn = _make_part("SW1", "pushbutton", role="component", label="按键",
                     x_mm=MARGIN_MM + COL_GAP_MM * 2.4, y_mm=MARGIN_MM)
    nets = [
        {"net": pin, "pins": [{"ref": "U1", "pin": pin},
                              {"ref": "SW1", "pin": "1"},
                              {"ref": "R1", "pin": "1"}]},
        {"net": "GND", "pins": [{"ref": "SW1", "pin": "2"},
                                {"ref": "R1", "pin": "2"},
                                {"ref": "U1", "pin": supply["gnd"]}]},
    ]
    return [mcu, res, btn], nets


def _build_led_bar(spec: dict[str, Any]) -> tuple[list[Part], list[dict[str, Any]]]:
    mcu_kind = str(spec.get("mcu") or "arduino-nano")
    supply = _supply_for(mcu_kind)
    reserved = {pins_module.normalise(v) for v in supply.values()}
    count = int(spec.get("count") or 3)
    _require(1 <= count <= 8, "led-bar 的 count 取 1..8")
    wanted = spec.get("pins") or ["D4", "D5", "D6", "D7", "D8", "D9", "D10", "D11"][:count]
    _require(len(wanted) == count, "pins 数量必须等于 count")

    mcu = _make_part("U1", mcu_kind, role="module", label="MCU",
                     x_mm=MARGIN_MM, y_mm=MARGIN_MM, rail="logic")
    parts: list[Part] = [mcu]
    nets: list[dict[str, Any]] = []
    for index, wanted_pin in enumerate(wanted):
        pin = _digital_pin(mcu_kind, str(wanted_pin), reserved=reserved)
        row_y = MARGIN_MM + index * 8.0
        ref_r, ref_d = f"R{index + 1}", f"D{index + 1}"
        res = _make_part(ref_r, "resistor", role="component", label="220Ω",
                         x_mm=MARGIN_MM + COL_GAP_MM * 1.6, y_mm=row_y)
        led = _make_part(ref_d, "led", role="component", label="LED",
                         x_mm=MARGIN_MM + COL_GAP_MM * 2.4, y_mm=row_y)
        parts += [res, led]
        nets += [
            {"net": pin, "pins": [{"ref": "U1", "pin": pin},
                                  {"ref": ref_r, "pin": "1"}]},
            {"net": f"{ref_d}_A", "pins": [{"ref": ref_r, "pin": "2"},
                                           {"ref": ref_d, "pin": "A"}]},
            {"net": "GND", "pins": [{"ref": ref_d, "pin": "K"}]},
        ]
    nets.append({"net": "GND", "pins": [{"ref": "U1", "pin": supply["gnd"]}]})
    return parts, nets


def _build_i2c_sensor(spec: dict[str, Any]) -> tuple[list[Part], list[dict[str, Any]]]:
    mcu_kind = str(spec.get("mcu") or "arduino-nano")
    supply = _supply_for(mcu_kind)
    sensor_kind = str(spec.get("sensor") or "bme280")
    table, source = pins_module.resolve(type("P", (), {"kind": sensor_kind})())
    _require(source != "unknown",
             f"没有 {sensor_kind} 的引脚表；支持的模块："
             + "、".join(k for k in ("bme280", "ssd1306", "mpu6050", "lcd1602")))

    mcu = _make_part("U1", mcu_kind, role="module", label="MCU",
                     x_mm=MARGIN_MM, y_mm=MARGIN_MM, rail="logic")
    sensor = _make_part("U2", sensor_kind, role="module", label=sensor_kind.upper(),
                        x_mm=MARGIN_MM + COL_GAP_MM * 1.8, y_mm=MARGIN_MM, rail="periph")
    nets = [
        {"net": f"{supply['vcc']}", "pins": [{"ref": "U1", "pin": supply["vcc"]},
                                             {"ref": "U2", "pin": "VCC"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": supply["gnd"]},
                                {"ref": "U2", "pin": "GND"}]},
        {"net": "SCL", "pins": [{"ref": "U1", "pin": supply["scl"]},
                                {"ref": "U2", "pin": "SCL"}]},
        {"net": "SDA", "pins": [{"ref": "U1", "pin": supply["sda"]},
                                {"ref": "U2", "pin": "SDA"}]},
    ]
    return [mcu, sensor], nets


def _build_ultrasonic(spec: dict[str, Any]) -> tuple[list[Part], list[dict[str, Any]]]:
    mcu_kind = str(spec.get("mcu") or "arduino-nano")
    supply = _supply_for(mcu_kind)
    reserved = {pins_module.normalise(v) for v in supply.values()}
    trig = _digital_pin(mcu_kind, str(spec.get("trig") or "D9"), reserved=reserved)
    echo = _digital_pin(mcu_kind, str(spec.get("echo") or "D10"),
                        reserved=reserved | {pins_module.normalise(trig)})

    mcu = _make_part("U1", mcu_kind, role="module", label="MCU",
                     x_mm=MARGIN_MM, y_mm=MARGIN_MM, rail="logic")
    sonar = _make_part("U2", "hc-sr04", role="module", label="HC-SR04",
                       x_mm=MARGIN_MM + COL_GAP_MM * 1.8, y_mm=MARGIN_MM, rail="periph")
    nets = [
        {"net": "5V", "pins": [{"ref": "U1", "pin": supply["vcc"]},
                               {"ref": "U2", "pin": "VCC"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": supply["gnd"]},
                                {"ref": "U2", "pin": "GND"}]},
        {"net": "TRIG", "pins": [{"ref": "U1", "pin": trig},
                                 {"ref": "U2", "pin": "TRIG"}]},
        {"net": "ECHO", "pins": [{"ref": "U1", "pin": echo},
                                 {"ref": "U2", "pin": "ECHO"}]},
    ]
    return [mcu, sonar], nets


def _build_servo(spec: dict[str, Any]) -> tuple[list[Part], list[dict[str, Any]]]:
    mcu_kind = str(spec.get("mcu") or "arduino-nano")
    supply = _supply_for(mcu_kind)
    reserved = {pins_module.normalise(v) for v in supply.values()}
    pin = _digital_pin(mcu_kind, str(spec.get("pin") or "D9"), reserved=reserved)

    mcu = _make_part("U1", mcu_kind, role="module", label="MCU",
                     x_mm=MARGIN_MM, y_mm=MARGIN_MM, rail="logic")
    servo = _make_part("SV1", "servo-sg90", role="actuator", label="SG90",
                       x_mm=MARGIN_MM + COL_GAP_MM * 1.8, y_mm=MARGIN_MM,
                       rail="servo_5v")
    nets = [
        {"net": "5V", "pins": [{"ref": "U1", "pin": supply["vcc"]},
                               {"ref": "SV1", "pin": "VCC"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": supply["gnd"]},
                                {"ref": "SV1", "pin": "GND"}]},
        {"net": "PWM", "pins": [{"ref": "U1", "pin": pin},
                                {"ref": "SV1", "pin": "SIG"}]},
    ]
    return [mcu, servo], nets


def _build_buzzer(spec: dict[str, Any]) -> tuple[list[Part], list[dict[str, Any]]]:
    mcu_kind = str(spec.get("mcu") or "arduino-nano")
    supply = _supply_for(mcu_kind)
    reserved = {pins_module.normalise(v) for v in supply.values()}
    pin = _digital_pin(mcu_kind, str(spec.get("pin") or "D8"), reserved=reserved)

    mcu = _make_part("U1", mcu_kind, role="module", label="MCU",
                     x_mm=MARGIN_MM, y_mm=MARGIN_MM, rail="logic")
    buz = _make_part("BZ1", "buzzer", role="component", label="蜂鸣器",
                     x_mm=MARGIN_MM + COL_GAP_MM * 1.8, y_mm=MARGIN_MM)
    nets = [
        {"net": pin, "pins": [{"ref": "U1", "pin": pin},
                              {"ref": "BZ1", "pin": "1"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": supply["gnd"]},
                                {"ref": "BZ1", "pin": "2"}]},
    ]
    return [mcu, buz], nets


BUILDERS = {
    "blink": _build_blink,
    "button": _build_button,
    "led-bar": _build_led_bar,
    "i2c-sensor": _build_i2c_sensor,
    "ultrasonic": _build_ultrasonic,
    "servo": _build_servo,
    "buzzer": _build_buzzer,
}


def synthesize(spec: dict[str, Any], *, design_id: str,
               title: str = "") -> DesignModel:
    """按需求组装一个设计。**引脚由模板保证存在**，组装完还自检一遍。

    `spec` 至少要有 `template`；其余键看模板（`mcu`/`pin`/`count`/`sensor`…）。

    失败抛 `SynthError`，带"为什么"和可选值——而不是静默产出一个错设计。
    """
    template = str(spec.get("template") or "").strip()
    _require(template != "",
             "需要 template；可用：" + "、".join(sorted(TEMPLATES)))
    _require(template in BUILDERS,
             f"没有模板 {template!r}；可用：" + "、".join(sorted(TEMPLATES)))

    parts, nets = BUILDERS[template](spec)

    # 板框：**先用预设板摆位**，摆不下才扩板。这样"需求 → 设计"的默认结果
    # 是一块尺寸合理的现成板，而不是每张都被撑宽的畸形板。
    board = board_preset(str(spec.get("board") or spec.get("mcu") or "arduino-nano"))
    _place(parts, float(board.size_mm.get("w", 0.0)),
           float(board.size_mm.get("h", 0.0)))
    if not _fits(parts, float(board.size_mm.get("w", 0.0)),
                 float(board.size_mm.get("h", 0.0))):
        need_w = max((p.placement.x_mm + p.size_mm["w"] for p in parts), default=0.0)
        need_h = max((p.placement.y_mm + p.size_mm["h"] for p in parts), default=0.0)
        board = _board_for(board.kind, min_w=need_w + MARGIN_MM,
                           min_h=need_h + MARGIN_MM)
        _place(parts, float(board.size_mm.get("w", 0.0)),
               float(board.size_mm.get("h", 0.0)))

    design = DesignModel(
        id=design_id,
        title=title or f"{template}：{TEMPLATES[template]}",
        board=board,
        parts=parts,
        nets=nets,
        wires=[],
        meta={
            "synthesized": True,
            "template": template,
            "spec": dict(spec),
            # 布局是**按功能块分行**的固定排布，不是优化结果。
            "layout_note": (
                f"按功能块固定排布（行距 {ROW_GAP_MM}mm / 列距 {COL_GAP_MM}mm），"
                "没有做布局优化；`space.*` 会如实指出重叠或越界。"),
            "scope_note": (
                "模板只覆盖常见组合；模拟电路、电源树、多板互联、"
                "阻抗/EMC 相关布局都没有自动路径。"),
        },
    )

    # 自检：模板写出的引脚必须都能在引脚表里找到。
    audit = pins_module.audit(design)
    _require(not audit["missing"],
             "模板自检失败（这是我这边模板的 bug，不是你的输入问题）："
             + "；".join(audit["missing"][:3]))
    return design


def describe() -> list[dict[str, Any]]:
    """给工具层的能力清单。"""
    return [{"template": name, "description": text,
             "params": sorted(set(_template_params(name)))}
            for name, text in sorted(TEMPLATES.items())]


def _template_params(name: str) -> list[str]:
    base = ["template", "mcu", "board", "pin"]
    if name == "led-bar":
        return base + ["count", "pins"]
    if name == "i2c-sensor":
        return base + ["sensor"]
    if name == "ultrasonic":
        return base + ["trig", "echo"]
    if name == "blink":
        return base + ["ohm"]
    return base