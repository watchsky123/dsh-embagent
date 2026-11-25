"""归因反推的自检 —— 证明结论**来自结构**，不是靠对着工程调词表。

从仓库根运行：

    python -m dsh_embagent.scripts.check_justify

## 这个脚本防的是什么

归因层最容易犯的错不是"判错某个元件"，而是**过拟合**：
对着某个工程的元件号加规则，直到它全绿。那样在那一份工程上看着很漂亮，
换一份就全垮，而且规则里的条件句读起来像是真的领域知识。

所以这里的夹具是**合成的小电路**，每个只考一条规则，且刻意做到：
  * 用不同风格的 ref 前缀（`R1` / `r1` / `C1` / `btn1`）
  * 混用三种网名风格（具名 `3.3V`/`SCL`、立创 `$1N394`、Wokwi `w1`）
  * 有的 kind 是哈希、有的是可读类型名

另外**必须**验证反向的东西：判不出时返回 `unknown`，而且
`missing_data` 与 `ratio_judgeable` 不能把缺数据混进算法成效里。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dsh_embagent.design import justify                        # noqa: E402
from dsh_embagent.design.model import (Board, DesignModel, Part,   # noqa: E402
                                    Placement)


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}{f' — {detail}' if detail else ''}")
            self.items.append(name)


def _part(ref: str, kind: str, *, label: str = "", role: str = "component",
          x: float = 0.0, y: float = 0.0, pins: int = 2) -> Part:
    return Part(ref=ref, kind=kind, label=label or kind, role=role,
                size_mm={"w": 2, "h": 2, "d": 1},
                physical_source="datasheet",
                placement=Placement(x_mm=x, y_mm=y),
                pins=[str(i + 1) for i in range(pins)])


def _design(parts: list[Part], nets: dict[str, list[str]],
            board_w: float = 200, board_h: float = 200) -> DesignModel:
    design = DesignModel(
        id="fixture", title="归因夹具",
        board=Board(kind="custom", name="夹具板",
                    size_mm={"w": board_w, "h": board_h}),
        parts=parts)
    for i, (net, pinrefs) in enumerate(nets.items()):
        pins = []
        for pinref in pinrefs:
            ref, _, pin = pinref.partition(".")
            pins.append({"ref": ref, "pin": pin or "1"})
        design.nets.append({"net": net, "pins": pins})
    return design


# ---------------------------------------------------------------------------
# 夹具：每条只考一条规则
# ---------------------------------------------------------------------------

def f_decoupling() -> DesignModel:
    """3.3V 轨上的去耦电容。

    夹具必须把电容的**两只脚**接在 3.3V 与 GND 上。第一版这里写成了
    `{"3.3V": [...], ...} | {"C1A": [...]}`，电容一脚落在 `C1A` 上，
    于是它被判成"信号滤波电容"——**夹具错了，代码是对的**。
    这类错误恰恰说明断言有用：它把"我以为我构造的是去耦电路"和
    "我实际构造的电路"区分开了。
    """
    return _design(
        [_part("U1", "stm32f103", role="mcu", pins=8),
         _part("C1", "0a1e481971f1b77d", label="100nF")],
        {"3.3V": ["U1.1", "C1.1"], "GND": ["U1.2", "C1.2"]})


def f_pullup() -> DesignModel:
    """I2C 上拉：3.3V 与 SCL 之间。"""
    return _design(
        [_part("R1", "c718c72ce64d8c03", label="4.7k"),
         _part("U1", "stm32f103", role="mcu", pins=8)],
        {"3.3V": ["U1.1", "R1.1"], "SCL": ["U1.2", "R1.2"],
         "GND": ["U1.3"]})


def f_shunt() -> DesignModel:
    """82mΩ 采样电阻：单位是 mΩ，不是普通串联。"""
    return _design(
        [_part("R2", "101005049f85ad2f", label="82mΩ"),
         _part("U1", "stm32f103", role="mcu", pins=8)],
        {"A": ["R2.1", "U1.1"], "B": ["R2.2", "U1.2"], "GND": ["U1.3"]})


def f_trace() -> DesignModel:
    """无名网穿透：SCL --R91--> $1N399 <-- U10。"""
    return _design(
        [_part("U10", "0deda055e6014e4e", role="module", pins=4),
         _part("R91", "2114616e9e190976"),
         _part("U1", "stm32f103", role="mcu", pins=8)],
        {"SCL": ["R91.2", "U1.1"], "$1N399": ["R91.1", "U10.1"],
         "3.3V": ["U10.2", "U1.2"], "GND": ["U10.3", "U1.3"],
         "$1N394": ["U10.4"]})


def f_switch() -> DesignModel:
    """按键：有名信号 + 经无源件到地。

    注意 `$1N10582` 在 U1026 与 R272 上**必须是同一个网名**——
    第一版写成了 `$1N10582` 与 `$1N10583` 两个不同的网，
    等于造了个断开的电路，穿透自然追不到 GND。
    """
    return _design(
        [_part("U1026", "015c378003ee92b1", role="module", pins=2),
         _part("R272", "611c3d3932db1684"),
         _part("U1", "stm32f103", role="mcu", pins=8)],
        {"BTN_CHANG_MODE": ["U1026.1", "U1.1"], "$1N10582": ["U1026.2", "R272.1"],
         "GND": ["R272.2", "U1.2"],
         "3.3V": ["U1.3"]})


def f_high_side() -> DesignModel:
    """高端 MOS：VIN / VBAT_2S / 无名网，没有地。"""
    return _design(
        [_part("Q2", "ad2977c3f4ef2595"),
         _part("U1", "stm32f103", role="mcu", pins=8)],
        {"VIN": ["Q2.1", "U1.1"], "VBAT_2S": ["Q2.2"],
         "$1N10410": ["Q2.3", "U1.2"], "GND": ["U1.3"]})


def f_snubber() -> DesignModel:
    """SW 节点上的缓冲电容。"""
    return _design(
        [_part("C189", "507537214f1516dc"),
         _part("U1", "stm32f103", role="mcu", pins=8)],
        {"SW": ["C189.1", "U1.1"], "$1N17132": ["C189.2", "U1.2"],
         "GND": ["U1.3"]})


def f_unknown_hash() -> DesignModel:
    """哈希 ref + 哈希 kind + 全自动网名 → **必须** unknown。"""
    return _design(
        [_part("U1027", "015c378003ee92b1", role="module", pins=2),
         _part("D1", "5d0ff6261ad992f3"),
         _part("D2", "5d0ff6261ad992f3")],
        {"$1N10408": ["U1027.1", "D1.1", "D2.1"],
         "$1N10762": ["U1027.2"]})


def f_no_netlist() -> DesignModel:
    """完全没有网表的元件（`示例-无人机` 的样子）。"""
    return _design(
        [_part("C1", "C0603", label="C0603"),
         _part("C2", "C0603", label="C0603")],
        {})


def f_wokwi() -> DesignModel:
    """Wokwi 风格：`w1`/`w2` 是自动名，`kind` 是可读类型名。"""
    return _design(
        [_part("led1", "wokwi-led", label="wokwi-led"),
         _part("r1", "wokwi-resistor", label="wokwi-resistor"),
         _part("btn1", "wokwi-pushbutton", label="wokwi-pushbutton")],
        {"w1": ["r1.1"], "w2": ["r1.2", "led1.1"],
         "w3": ["led1.2"], "w4": ["btn1.1"], "w5": ["btn1.2"]})


def f_module_port() -> DesignModel:
    """连接器与模块独占一条无名网。"""
    return _design(
        [_part("RF1", "5e2c0505ab5b0d1e", role="connector", pins=5),
         _part("Lora1", "15e0527ceae8007b", pins=19)],
        {"GND": ["RF1.1", "Lora1.1"], "$1N43000": ["RF1.5", "Lora1.10"]})


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    f = Failures()

    print("== 单条规则 ==")

    r = justify.run(f_decoupling())
    c1 = _one(r, "C1")
    f.check("去耦电容判为 decoupling",
            c1["group"]["kind"] == "decoupling", str(c1["group"]))
    f.check("去耦电容理由含网名 3.3V", "3.3V" in c1["justification"]["why"])
    f.check("哈希 label 不当作容值呈现",
            "0a1e481971f1b77d" not in c1["justification"]["why"])

    r = justify.run(f_pullup())
    r1 = _one(r, "R1")
    f.check("上拉电阻判为 pullup", r1["group"]["kind"] == "pullup",
            str(r1["group"]))
    f.check("上拉理由含阻值与目标网",
            "4.7k" in r1["justification"]["why"]
            and "SCL" in r1["justification"]["why"])

    r = justify.run(f_shunt())
    r2 = _one(r, "R2")
    f.check("82mΩ 判为 current_sense（不是 series）",
            r2["group"]["kind"] == "current_sense",
            f"实际 {r2['group']['kind']}")

    r = justify.run(f_trace())
    u10 = _one(r, "U10")
    f.check("U10 用于穿透追踪（traced_node）",
            u10["group"]["kind"] in ("traced_node", "protocol_node"),
            f"实际 {u10['group']['kind']}")
    f.check("U10 理由提到追踪路径或目标 SCL",
            "SCL" in u10["justification"]["why"],
            u10["justification"]["why"])

    r = justify.run(f_switch())
    u = _one(r, "U1026")
    f.check("按键判为对地开关",
            u["group"]["kind"] == "switch_to_ground",
            f"实际 {u['group']['kind']}")

    r = justify.run(f_high_side())
    q2 = _one(r, "Q2")
    f.check("高端 MOS 判为 high_side_switch（不是 rail_only）",
            q2["group"]["kind"] == "high_side_switch",
            f"实际 {q2['group']['kind']}")
    f.check("高端 MOS 的删除后果不是'少一个负载'",
            "少一个负载" not in q2["justification"]["effect_if_removed"])

    r = justify.run(f_snubber())
    c189 = _one(r, "C189")
    f.check("SW 节点电容判为 snubber",
            c189["group"]["kind"] == "snubber",
            f"实际 {c189['group']['kind']}")

    r = justify.run(f_module_port())
    rf1 = _one(r, "RF1")
    f.check("连接器独占网判为 module_port",
            rf1["group"]["kind"] == "module_port",
            f"实际 {rf1['group']['kind']}")
    f.check("module_port 理由点名了对方模块",
            "Lora1" in rf1["justification"]["why"])

    print("\n== 必须诚实的地方 ==")

    r = justify.run(f_unknown_hash())
    u1027 = _one(r, "U1027")
    f.check("哈希 unknown 保持 unknown",
            u1027["justification"]["confidence"] == "unknown",
            u1027["justification"]["confidence"])
    f.check("unknown 的删除后果明说'系统不会报错'",
            "不会报错" in u1027["justification"]["effect_if_removed"])

    r = justify.run(f_no_netlist())
    f.check("无网表 → 全部 unknown", r["known"] == 0, str(r["known"]))
    f.check("无网表 → no_netlist_count 如实报出",
            r["missing_data"]["no_netlist_count"] == 2,
            str(r["missing_data"]["no_netlist_count"]))
    f.check("无网表 → ratio_judgeable 为 0 而不是 1",
            r["ratio_judgeable"] == 0.0, str(r["ratio_judgeable"]))
    f.check("无网表 → ok 为 False", r["ok"] is False, str(r["ok"]))
    f.check("无网表 → summary 明说缺数据",
            "没有网表" in r["summary"], r["summary"])

    print("\n== 网名风格无关性 ==")

    r = justify.run(f_wokwi())
    f.check("Wokwi 的 w1/w2 被当作无名网（不算有语义）",
            not justify.is_named("w1") and justify.is_named("SCL"))
    f.check("Wokwi 三元件的 kind 被识别（不只是哈希才算 kind）",
            r["known"] == 3, f"{r['known']}/{r['total']}")
    btn = _one(r, "btn1")
    f.check("btn1 判为 bare_switch", btn["group"]["kind"] == "bare_switch",
            f"实际 {btn['group']['kind']}")

    print("\n== 套话过滤 ==")

    # 一条不含具体指称的理由必须被降级
    fake = justify._j("该元件起到滤波作用", "影响不大")
    f.check("无具体指称的理由被识别为不可引用",
            justify._cited(fake.why, ["3.3V"], "C1") is False)
    f.check("含网名的理由算可引用",
            justify._cited("接在 3.3V 上", ["3.3V"], "C1") is True)
    f.check("含带单位数值的理由算可引用",
            justify._cited("10kΩ 上拉", [], "R1") is True)

    print("\n== 稳定性 ==")

    d = f_decoupling()
    a1 = justify.run(d)
    a2 = justify.run(d)
    f.check("同输入同输出",
            [x["justification"]["why"] for x in a1["parts"]]
            == [x["justification"]["why"] for x in a2["parts"]])

    print("\n== 不写盘 ==")

    d = f_pullup()
    justify.run(d)
    f.check("run() 默认不写回 part.justification",
            all(p.justification is None for p in d.parts))

    print()
    if f.items:
        print(f"FAILED {len(f.items)} 项：")
        for name in f.items:
            print(f"  - {name}")
        return 1
    print("全部通过")
    return 0


def _one(report: dict, ref: str) -> dict:
    for row in report["parts"]:
        if row["ref"] == ref:
            return row
    raise AssertionError(f"报告里没有 {ref}")


if __name__ == "__main__":
    raise SystemExit(main())
