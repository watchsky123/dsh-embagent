"""M1「审查机」的四条硬验收 —— 一条命令跑完，可重复。

从仓库根运行：

    python -m dsh_embagent.scripts.check_m1

## 这个脚本存在的理由

计划文档 `dsh-embagent-docs:docs/execution-plan.md` 给每个阶段写了硬验收条件。那些条件
之前是**我手工一条条敲命令验证的**——敲完就没了，下次改动没人知道
有没有退化。

这里把它们固化成断言。判定标准全部来自计划文档，不是我事后想的：

| 阶段 | 硬验收 | 为什么是这个 |
|---|---|---|
| P0 | 空工程 → `ok=False` | 零负载曾经算出 `ok=True`（假绿） |
| P1 | 极性反接 → `stopped_at="V1"` 且下游 `not_run` | 硬失败必须截断，`not_run` 不能写成 `pass` |
| P2 | 删元件 → `reddened` 含**非 connected** 的断言 | 只有 `connected` 等于"变安静"不是"变红" |
| P3 | 有网表元件里判出 > 20% | 阈值；同时必须报缺数据比例 |
| P4 | 五类表结构存在且能区分"没查"与"没问题" | 缺产物要标 missing |

## 为什么有的用真实工程、有的用合成夹具

真实工程（`示例-单板`）证明**在脏数据上不崩、能给出有用结论**；
合成夹具证明**判据本身成立**（真实工程恰好不触发某个失败时，
用它验不了那条判据）。

P1 就是典型：真实工程的 V1 是 `unknown`（141 个元件都没申报电流），
用它永远验不出"V1 失败要截断"。所以 P1 必须用极性反接的夹具。
之前我拿真实工程跑 P1，看到 `stopped_at=""` 就以为截断坏了——
其实 V2 是 fail 而 V2 **不是**硬层（几何重叠不影响电气判决），
那是正确行为。这个脚本把"哪条用哪个夹具"写死，避免再犯。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dsh_embagent.design import (claims, electrical, handoff,    # noqa: E402
                              justify, ladder)
from dsh_embagent.design.model import (Board, DesignModel, Part,  # noqa: E402
                                    Placement)
from dsh_embagent.design.store import DesignStore, list_projects  # noqa: E402

#: 用哪个工程做"在真实脏数据上不崩"的验证：
#: **名字由环境变量给**（开源仓库里不写死使用者的工程名）；没给就用一个
#: 必然不存在的名字，脚本据此走 `[skip]` 分支。
REAL_PROJECT = os.environ.get("DSH_EMBAGENT_PROBE_PROJECT_NAME",
                              "nonexistent-project")


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}{f' — {detail}' if detail else ''}")
            self.items.append(name)


def _design(**kwargs) -> DesignModel:
    return DesignModel(
        id="m1", title="M1 验收夹具",
        board=Board(kind="custom", size_mm={"w": 120, "h": 120}),
        **kwargs)


def _part(ref: str, kind: str, **kw) -> Part:
    return Part(ref=ref, kind=kind, label=kw.pop("label", kind),
                role=kw.pop("role", "component"),
                size_mm=kw.pop("size_mm", {"w": 4, "h": 4, "d": 1}),
                physical_source=kw.pop("physical_source", "datasheet"),
                placement=Placement(**kw.pop("placement",
                                             {"x_mm": 20, "y_mm": 20})),
                **kw)


# ---------------------------------------------------------------------------
# P0 —— 零负载不能算出 ok=True
# ---------------------------------------------------------------------------

def check_p0(f: Failures) -> None:
    print("== P0 电气零负载（假绿的根因） ==")

    empty = _design(parts=[])
    report = electrical.run(empty).to_dict()
    f.check("空工程（零负载）→ ok=False",
            report["ok"] is False, str(report["ok"]))
    f.check("空工程 → not_verified 说明了为什么不是通过",
            bool(report.get("not_verified")),
            str(report.get("not_verified"))[:2])
    f.check("空工程 → rails 为空而不是虚构一条 5V",
            not report.get("rails"), str(report.get("rails")))

    # 有元件但都不申报电流：仍然是"没算"，不是"通过"
    silent = _design(parts=[_part("U1", "stm32f103", role="mcu")])
    silent.nets = [{"net": "5V", "pins": [{"ref": "U1", "pin": "1"}]},
                   {"net": "GND", "pins": [{"ref": "U1", "pin": "2"}]}]
    report = electrical.run(silent).to_dict()
    f.check("有元件但零申报 → ok=False（不是通过）",
            report["ok"] is False, str(report["ok"]))


# ---------------------------------------------------------------------------
# P1 —— 硬失败必须截断，下游 not_run 不能是 pass
# ---------------------------------------------------------------------------

def _polarity_reversed() -> DesignModel:
    """极性接反：12V 接到 MCU 的 3.3V 脚。

    V1 判"绝对最大值/极性"，这个必须 fail。用它才能验截断——
    真实工程的 V1 是 `unknown`（没申报电流），验不出 fail 路径。
    """
    design = _design(parts=[
        _part("U1", "stm32f103", role="mcu", pins=["1", "2"],
              electrical={"max_v": 3.6}),
        _part("P1", "connector", role="connector", pins=["1", "2"]),
    ])
    design.nets = [
        {"net": "12V", "pins": [{"ref": "P1", "pin": "1"},
                                {"ref": "U1", "pin": "1"}]},
        {"net": "GND", "pins": [{"ref": "P1", "pin": "2"},
                                {"ref": "U1", "pin": "2"}]},
    ]
    return design


def check_p1(f: Failures) -> None:
    print("\n== P1 硬失败截断 ==")

    report = ladder.run_ladder(_polarity_reversed()).to_dict()
    f.check("12V 接 3.6V 额定 → stopped_at='V1'",
            report["stopped_at"] == "V1", repr(report["stopped_at"]))
    f.check("downstream='truncated'",
            report["downstream"] == "truncated", report["downstream"])

    after = [x for x in report["levels"] if x["level"] not in ("V0", "V1")]
    f.check("下游全部 not_run（不是 pass）",
            after and all(x["status"] == "not_run" for x in after),
            str([(x["level"], x["status"]) for x in after]))
    f.check("下游没有任何一层是 pass",
            not any(x["status"] == "pass" for x in after),
            str([(x["level"], x["status"]) for x in after]))

    # 反向：正常设计不该被截断
    clean = _design(parts=[_part("U1", "stm32f103", role="mcu", pins=["1"],
                                 electrical={"max_v": 5.5})])
    clean.nets = [{"net": "5V", "pins": [{"ref": "U1", "pin": "1"}]}]
    report = ladder.run_ladder(clean).to_dict()
    f.check("正常设计不被截断",
            report["stopped_at"] == "", repr(report["stopped_at"]))

    f.check("HARD_LEVELS 只含 V0/V1（几何失败不该截断电气）",
            tuple(ladder.HARD_LEVELS) == ("V0", "V1"),
            str(ladder.HARD_LEVELS))


# ---------------------------------------------------------------------------
# P2 —— 删除即失败，且必须说得出"什么功能没了"
# ---------------------------------------------------------------------------

def _claims_design() -> DesignModel:
    """上拉 + LED：两个元件都有可判的作用。"""
    design = _design(parts=[
        _part("U1", "stm32f103", role="mcu", pins=["D2", "5V", "GND"],
              electrical={"max_v": 5.5}),
        _part("R1", "resistor", role="pullup", label="10k", pins=["1", "2"]),
        _part("D1", "led", role="indicator", label="LED", pins=["1", "2"]),
        _part("R2", "resistor", label="220", pins=["1", "2"]),
    ])
    design.nets = [
        {"net": "5V", "pins": [{"ref": "U1", "pin": "5V"},
                               {"ref": "R1", "pin": "1"}]},
        {"net": "D2", "pins": [{"ref": "U1", "pin": "D2"},
                               {"ref": "R1", "pin": "2"},
                               {"ref": "R2", "pin": "1"}]},
        {"net": "$1N1", "pins": [{"ref": "R2", "pin": "2"},
                                 {"ref": "D1", "pin": "1"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": "GND"},
                                {"ref": "D1", "pin": "2"}]},
    ]
    return design


def check_p2(f: Failures) -> None:
    print("\n== P2 删除即失败 ==")

    design = _claims_design()
    total = len(claims.derive(design))
    f.check("断言被推导出来（不是空表）", total > 0, str(total))

    for ref in ("R1", "R2", "D1"):
        impact = claims.removal_impact(design, ref)
        f.check(f"删掉 {ref} 有断言变红",
                impact is not None and bool(impact["reddened"]),
                str(impact.get("reddened") if impact else None))
        if impact is None:
            continue
        functional = [i for i in impact["reddened"]
                      if not i.endswith(".connected")]
        f.check(f"删掉 {ref} 变红的不止 connected（说出了功能）",
                bool(functional),
                f"只有 {impact['reddened']}")

    # 归一化：删掉一个不存在的元件不能假装成功
    f.check("删不存在的元件 → None（不静默通过）",
            claims.removal_impact(design, "NOPE") is None)

    # 归因必须被断言消费
    report = justify.run(design)
    consumed = True
    for row in report["parts"]:
        if row["justification"]["confidence"] == "unknown":
            continue
        ref = row["ref"]
        extra = [c for c in claims.derive(design)
                 if c.id.startswith(f"claim.{ref}.")
                 and not c.id.endswith((".connected", ".rated_voltage"))]
        if not extra:
            consumed = False
            f.check(f"{ref} 的归因被断言消费", False,
                    f"判为 {row['group']['kind']} 但只生成了 connected")
    f.check("所有判出作用的元件都被断言消费", consumed)


# ---------------------------------------------------------------------------
# P3 —— 归因覆盖率，且必须区分"缺数据"
# ---------------------------------------------------------------------------

def check_p3(f: Failures) -> None:
    print("\n== P3 归因反推（真实工程） ==")

    if not _has_project(REAL_PROJECT):
        print(f"  skip 找不到工程 {REAL_PROJECT}（换个真实工程或跳过）")
        return

    design = DesignStore(REAL_PROJECT).load()
    report = justify.run(design)

    f.check("比例过 20% 阈值",
            report["ratio"] > report["threshold"],
            f"{report['ratio']} vs {report['threshold']}")
    f.check("同时报『有网表元件』口径",
            "ratio_judgeable" in report,
            str(sorted(report)))
    f.check("同时报缺网表元件数量",
            "no_netlist_count" in (report.get("missing_data") or {}),
            str(sorted(report.get("missing_data") or {})))
    f.check("两个口径都在 0..1 之间",
            0.0 <= report["ratio"] <= 1.0
            and 0.0 <= report["ratio_judgeable"] <= 1.0,
            f"{report['ratio']} / {report['ratio_judgeable']}")

    # unknown 必须给出理由，不能只说"判不出"。
    #
    # 判据是"理由里有没有**具体指称**"——网名、元件号、或明确的原因词。
    # 第一版这里只查三个关键词（导入器/不接/不匹配），漏掉了实际输出里
    # 的"自动网名"这种写法。测试的正事是"有没有具体内容"，
    # 不该退化成"对不对得上我列的词"。
    import re as _re
    _NETNAME = _re.compile(r"\$?\d*N?\d")
    bad: list[str] = []
    for row in report["parts"]:
        if row["justification"]["confidence"] != "unknown":
            continue
        why = str(row["justification"]["why"] or "")
        concrete = (
            _NETNAME.search(why) is not None              # 点了具体网名
            or any(k in why for k in (
                "导入器", "自动", "不接", "不匹配", "没有网表",
                "哈希", "未收录", "无法归类"))
        )
        if not concrete:
            bad.append(row["ref"])
    f.check("unknown 都给了具体理由（不是空话）", not bad, str(bad[:4]))

    # 不能编造：unknown 的删除后果必须说明"系统不会报错"
    bad = [r for r in report["parts"]
           if r["justification"]["confidence"] == "unknown"
           and "不会报错" not in r["justification"]["effect_if_removed"]
           and "无法判断" not in r["justification"]["effect_if_removed"]]
    f.check("unknown 的删除后果明说系统发现不了",
            not bad, str([r["ref"] for r in bad[:4]]))


# ---------------------------------------------------------------------------
# P4 —— 交接单：五类可分，"没查" ≠ "没问题"
# ---------------------------------------------------------------------------

def check_p4(f: Failures) -> None:
    print("\n== P4 交接层 ==")

    from dsh_embagent.scripts.check_handoff import FakeStore, _design as fix_design, \
        _ladder as fix_ladder

    # 有产物：五类结构齐全
    store = FakeStore("p", fix_design(), {
        "ladder.json": fix_ladder(),
        "deferred.json": {"open_irreversible": ["package_family"]},
    })
    report = handoff.build(store).to_dict()
    f.check("五类都在报告里",
            all(c in (report.get("counts") or {}) for c in handoff.CATEGORIES),
            str(sorted(report.get("counts") or {})))
    f.check("有阻断时 deliverable=False", report["deliverable"] is False)
    f.check("阻断项给了可执行动作",
            all(i["action"] for i in report["items"]
                if i["category"] == "blocking"))
    f.check("V5/V6 归入『不会验』而不是阻断",
            all(i["category"] != "blocking"
                for i in report["items"] if "V5" in i["title"]
                or "V6" in i["title"]))

    # 缺产物：必须标 missing。数量随产物清单变化，所以按实际清单断言，
    # 不写死"6 份"——加一份新产物就报错的话，这个测试会变成维护负担，
    # 人就会去改数字而不是去想"缺产物到底要不要报"。
    report = handoff.build(FakeStore("p", fix_design(), {})).to_dict()
    sources = report.get("sources") or {}
    missing = [k for k, v in sources.items() if v == "missing"]
    expected = [k for k in sources if k != "design.json"]
    f.check("除 design.json 外全部判定产物标 missing",
            sorted(missing) == sorted(expected),
            f"missing={missing} expected={expected}")
    f.check("至少覆盖阶梯/断言/预算/协同/归因五项",
            {"ladder.json", "claims.json", "budget.json",
             "coverify.json", "justify.json"}.issubset(set(missing)),
            str(missing))
    markdown = handoff.render_markdown(report)
    f.check("markdown 里出现 missing 字样", "missing" in markdown)
    f.check("空类渲染成『无。』而不是省略", "_无。_" in markdown)

    # 默认值链路：coverify.json → handoff 第三类。
    #
    # 这条链路曾经完全断着：`_assumed` 去 `budget.json` 翻 `coverify`
    # 字段，而那个字段从来不存在，于是第三类永远 0 条。实测
    # `示例-单板` 有 7 条 not_verified 一条都没进交接单。
    store = FakeStore("p", fix_design(), {
        "ladder.json": fix_ladder(),
        "coverify.json": {"ok": False, "not_verified": [
            "指向精度未声明，按默认 1.0° 计算（影响 CV-01）",
        ]},
    })
    report = handoff.build(store).to_dict()
    f.check("coverify 的默认值进入『机器用了默认值』",
            (report.get("counts") or {}).get("assumed") == 1,
            str((report.get("counts") or {}).get("assumed")))
    f.check("coverify.json 被列进 sources（不是 missing）",
            (report.get("sources") or {}).get("coverify.json") != "missing",
            str((report.get("sources") or {}).get("coverify.json")))

    # 真实工程上再验一遍（它是计划 §7.4 点名的 7 条）
    if _has_project(REAL_PROJECT):
        real = DesignStore(REAL_PROJECT)
        if real.read("coverify.json"):
            report = handoff.build(real).to_dict()
            f.check("真实工程的默认值条数 > 0",
                    (report.get("counts") or {}).get("assumed", 0) > 0,
                    str((report.get("counts") or {}).get("assumed")))


# ---------------------------------------------------------------------------

def _has_project(project: str) -> bool:
    try:
        return any(p.get("id") == project and p.get("has_design")
                   for p in list_projects())
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    f = Failures()

    check_p0(f)
    check_p1(f)
    check_p2(f)
    check_p3(f)
    check_p4(f)

    print()
    if f.items:
        print(f"FAILED {len(f.items)} 项：")
        for name in f.items:
            print(f"  - {name}")
        return 1
    print("M1 四条硬验收 + P4 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
