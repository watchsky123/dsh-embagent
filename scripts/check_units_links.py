"""P5 验收：单元层 + 板间链路。

从仓库根运行：

    python -m hw_velxio.scripts.check_units_links

## 为什么一半夹具是合成的

真实工程 `示例-多板` 的 `links_list` 是**空的**——没有任何声明的
板间链路。用它只能验出"报告了 not_ready"，验不出四条检查本身是否
成立。所以：

* **合成夹具**证明「检查本身对」：构造协议不匹配/电平不匹配/
  波特率不可达/孤儿外设四种情形，要求四种都被抓到。
* **真实工程**证明「在脏数据上不崩、结论有用」：8 块板分成 8 个
  单元、7 个如实记 unknown、15 条外设信号网报成提示。

只测前者会漏掉真实数据的问题；只测后者会让检查器在无人验证时腐烂。

## 一条必须反向验证的纪律

`source="inferred"` 的链路**不参与硬检查**。所以夹具里专门造一条
"推断的、协议明显不匹配"的链路，断言它**不产生 fail**。
如果哪天有人把 `if source == "inferred": continue` 删了，
这条会立刻变红。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hw_velxio.design import links, units                # noqa: E402
from hw_velxio.design.model import (Board, DesignModel, Part,   # noqa: E402
                                    Placement)
from hw_velxio.design.store import DesignStore, list_projects   # noqa: E402

MULTI = "示例-多板"


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}{f' — {detail}' if detail else ''}")
            self.items.append(name)


def _part(ref: str, *, board: str = "", pins: int = 4,
          role: str = "component", **kw) -> Part:
    return Part(
        ref=ref, kind=kw.pop("kind", "generic"), label=kw.pop("label", ref),
        role=role, size_mm={"w": 5, "h": 5, "d": 1},
        physical_source="datasheet",
        placement=Placement(x_mm=kw.pop("x", 10), y_mm=kw.pop("y", 10),
                            board_id=board),
        pins=[str(i + 1) for i in range(pins)],
        **kw)


def _two_board_design(*, boards: int = 2) -> DesignModel:
    """两块板的设计，元件够"有资格当链路端点"。"""
    ids = [f"PCB#b{i}" for i in range(boards)]
    board_objs = [Board(kind="custom", name=f"板 {i}",
                        size_mm={"w": 60, "h": 60},
                        id=bid) for i, bid in enumerate(ids)]
    design = DesignModel(id="fixture", title="P5 夹具",
                         board=board_objs[0], boards=board_objs)
    design.parts = []
    for i, bid in enumerate(ids):
        design.parts.append(_part(f"U{i + 1}", board=bid, pins=8,
                                  role="module"))
    design.nets = []
    return design


def _with_named_nets(design: DesignModel, names: list[str]) -> DesignModel:
    """给每块板各接一条具名网（让单元角色可判）。"""
    design.nets = [{"net": n, "pins": [{"ref": f"U{i + 1}", "pin": str(i + 1)}]}
                   for i, n in enumerate(names)]
    return design


# ---------------------------------------------------------------------------
# 单元层
# ---------------------------------------------------------------------------

def check_units(f: Failures) -> None:
    print("== 单元层：分组与角色 ==")

    d = _with_named_nets(_two_board_design(), ["OSC_IN", "SCL"])
    report = units.report(d)
    f.check("两块板 → 两个单元", report["total"] == 2, str(report["total"]))
    f.check("multi_board 为 True", report["multi_board"] is True)

    roles = {u["id"]: u["role"] for u in report["units"]}
    f.check("晶振网络 → 判成 mcu",
            "mcu" in roles.values(), str(roles))
    f.check("SCL 网络 → 判成 sensor 或 i2c 相关",
            any(r in ("sensor", "mcu", "radio", "power") for r in roles.values()),
            str(roles))

    # 角色必须给证据
    for u in report["units"]:
        if u["confidence"] == "unknown":
            continue
        f.check(f"{u['id']} 判出角色时带证据",
                bool(u["evidence"]), str(u.get("evidence")))

    print("\n== 单元层：没有网表必须如实 unknown ==")

    d = _two_board_design()          # 完全不给 nets
    report = units.report(d)
    f.check("无网表 → 全部 unknown", report["known"] == 0, str(report["known"]))
    f.check("无网表 → 报出 missing_data 数量",
            report["missing_data"]["no_netlist_count"] == 2,
            str(report["missing_data"]["no_netlist_count"]))
    f.check("无网表 → 可判口径为 0（不是 1）",
            report["ratio_judgeable"] == 0.0, str(report["ratio_judgeable"]))
    f.check("无网表 → ok 为 False", report["ok"] is False)
    f.check("无网表 → summary 明说是缺数据",
            "缺数据" in report["summary"] or "没有网表" in report["summary"],
            report["summary"])

    # 自动网名不算语义
    d = _two_board_design()
    d.nets = [{"net": "$1N394", "pins": [{"ref": "U1", "pin": "1"},
                                         {"ref": "U2", "pin": "1"}]}]
    report = units.report(d)
    f.check("自动网名（$1N394）不当语义",
            report["units"][0]["named_nets"] == [],
            str(report["units"][0]["named_nets"]))

    print("\n== 单元层：单板不假装多板 ==")

    single = DesignModel(id="s", title="单板",
                         board=Board(kind="custom", size_mm={"w": 60, "h": 60}))
    single.parts = [_part("U1", pins=8, role="module")]
    single.nets = [{"net": "SCL", "pins": [{"ref": "U1", "pin": "1"}]}]
    report = units.report(single)
    f.check("单板 → 1 个单元", report["total"] == 1, str(report["total"]))
    f.check("单板 → multi_board 为 False", report["multi_board"] is False)


# ---------------------------------------------------------------------------
# 链路：四条检查
# ---------------------------------------------------------------------------

def _link(**kw) -> dict:
    base = {
        "id": "link:t",
        "a": {"unit": "unit:b0", "part": "U1", "peripheral": "USART2"},
        "b": {"unit": "unit:b1", "part": "U2", "peripheral": "USART3"},
        "protocol": "uart",
        "params": {"level_v": 3.3},
        "source": "declared",
    }
    base.update(kw)
    return base


def check_link_checks(f: Failures) -> None:
    print("\n== 链路检查 1：协议不匹配 ==")

    d = _two_board_design()
    d.links_list = [_link(a={"unit": "unit:b0", "part": "U1",
                             "peripheral": "USART2"},
                          b={"unit": "unit:b1", "part": "U2",
                             "peripheral": "SPI1"})]
    r = links.validate(d, d.links_list)
    checks = [x["check"] for x in r["findings"]]
    f.check("UART↔SPI 被抓出协议不匹配",
            "link.protocol_match" in checks, str(checks))
    f.check("协议不匹配 → ok=False", r["ok"] is False)

    # 反向：同族不该报
    d.links_list = [_link(a={"unit": "unit:b0", "part": "U1",
                             "peripheral": "USART2"},
                          b={"unit": "unit:b1", "part": "U2",
                             "peripheral": "USART3"})]
    r = links.validate(d, d.links_list)
    f.check("UART↔UART 不报协议错",
            "link.protocol_match" not in [x["check"] for x in r["findings"]],
            str([x["check"] for x in r["findings"]]))

    print("\n== 链路检查 2：电平不匹配 ==")

    d = _two_board_design()
    d.links_list = [_link()]
    d.parts[0].electrical = {"v_in": 3.3}
    d.parts[1].electrical = {"v_in": 5.0}
    r = links.validate(d, d.links_list)
    f.check("3.3V↔5.0V 被抓出电平不匹配",
            "link.level_match" in [x["check"] for x in r["findings"]],
            str([x["check"] for x in r["findings"]]))
    f.check("电平不匹配的提示含两端的电压值",
            any("3.3" in x["message"] and "5.0" in x["message"]
                for x in r["findings"] if x["check"] == "link.level_match"),
            str([x["message"] for x in r["findings"]]))

    # 未申报电平 → 未知，不判通过
    d = _two_board_design()
    d.links_list = [_link(params={})]
    r = links.validate(d, d.links_list)
    f.check("电平未申报 → 进 assumed 而不是通过",
            bool(r["assumed"]), str(r["assumed"]))
    f.check("电平未申报 → 不报 level_match 失败",
            "link.level_match" not in [x["check"] for x in r["findings"]])

    print("\n== 链路检查 3：波特率可达 ==")

    ok, actual = links.baud_reachable(8_000_000, 3_000_000)
    f.check("8MHz 时钟跑 3M 波特 → 不可达", ok is False, f"actual={actual}")
    ok, actual = links.baud_reachable(8_000_000, 115200)
    f.check("8MHz 跑 115200 → 可达", ok is True, f"actual={actual}")
    ok, actual = links.baud_reachable(None, 115200)
    f.check("时钟未知 → 返回 None（不是 True）", ok is None, str(ok))

    d = _two_board_design()
    d.parts[0].electrical = {"clock_hz": 8_000_000}
    d.links_list = [_link(params={"level_v": 3.3, "baud": 3_000_000})]
    r = links.validate(d, d.links_list)
    f.check("目标波特率不可达被抓出",
            "link.baud_feasible" in [x["check"] for x in r["findings"]],
            str([x["check"] for x in r["findings"]]))

    print("\n== 链路检查 4：孤儿外设（隐性假设） ==")

    d = _two_board_design()
    d.meta = {"interfaces": {"USART2": "unit:b0", "SPI1": "unit:b0"}}
    r = links.validate(d, [])          # 链路表为空
    orphan = [x for x in r["findings"] if x["check"] == "link.orphan"]
    f.check("声明了两个外设但没链路 → 两条 orphan",
            len(orphan) == 2, str(len(orphan)))
    f.check("orphan 提示说了是隐性假设",
            any("没说接谁" in x["message"] for x in orphan),
            str([x["message"] for x in orphan]))

    # 有链路覆盖的就不该报
    d.links_list = [_link(params={"level_v": 3.3})]
    r = links.validate(d, d.links_list)
    orphan = [x for x in r["findings"] if x["check"] == "link.orphan"]
    f.check("被链路覆盖的外设不再报 orphan",
            len(orphan) == 1, f"{len(orphan)} 条（USART2 被覆盖，SPI1 仍孤）")

    print("\n== 纪律：inferred 不参与硬检查 ==")

    d = _two_board_design()
    bad_inferred = _link(a={"peripheral": "USART2"},
                         b={"peripheral": "SPI1"}, source="inferred")
    r = links.validate(d, [bad_inferred])
    f.check("推断的错协议链路**不**产生 fail",
            r["ok"] is True, str([x["check"] for x in r["findings"]]))
    f.check("推断链路被记进 skipped_inferred",
            bad_inferred["id"] in r["skipped_inferred"],
            str(r["skipped_inferred"]))
    f.check("声明链路数为 0（推断不算声明）",
            r["checked"]["declared_links"] == 0,
            str(r["checked"]))

    print("\n== 链路表为空 ≠ 通过 ==")

    d = _two_board_design()
    r = links.validate(d, [])
    f.check("空链路表 → status=not_ready", r["status"] == "not_ready")
    f.check("空链路表 → reason 明说检查未执行",
            "不是通过" in r["reason"], r["reason"])


# ---------------------------------------------------------------------------
# 真实工程（计划 §8.3）
# ---------------------------------------------------------------------------

def check_real(f: Failures) -> None:
    print("\n== 真实工程（计划 §8.3 验收） ==")

    if not any(p.get("id") == MULTI and p.get("has_design")
               for p in list_projects()):
        print(f"  skip 找不到 {MULTI}")
        return

    design = DesignStore(MULTI).load()

    unit_report = units.report(design)
    f.check(f"hw_units {MULTI} → 分出 ≥2 个 unit",
            unit_report["total"] >= 2, str(unit_report["total"]))
    f.check("每个单元都有 id 与元件清单",
            all(u["id"] and u["part_count"] >= 0
                for u in unit_report["units"]))
    f.check("单元元件总数 = 设计的元件数",
            sum(u["part_count"] for u in unit_report["units"]) == len(design.parts),
            f"{sum(u['part_count'] for u in unit_report['units'])} vs {len(design.parts)}")

    link_report = links.report(design)
    f.check("hw_links 报告了未声明的外设",
            (link_report["orphans"]["counts"]["observed_unlinked"]
             + link_report["orphans"]["counts"]["declared_unlinked"]
             + link_report["orphans"]["counts"]["units_without_nets"]) > 0,
            str(link_report["orphans"]["counts"]))
    f.check("链路表为空时状态是 not_ready（不是 ok）",
            link_report["status"] == "not_ready",
            link_report["status"])
    f.check("从网表推出了候选链路（真实存在 SPI/UART）",
            len(link_report["inferred"]) > 0,
            str(len(link_report["inferred"])))
    f.check("没有网表的单元被单独报出",
            link_report["orphans"]["counts"]["units_without_nets"] > 0,
            str(link_report["orphans"]["counts"]["units_without_nets"]))


# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    f = Failures()

    check_units(f)
    check_link_checks(f)
    check_real(f)

    print()
    if f.items:
        print(f"FAILED {len(f.items)} 项：")
        for name in f.items:
            print(f"  - {name}")
        return 1
    print("P5 验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
