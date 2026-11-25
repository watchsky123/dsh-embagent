"""交接层的自检 —— 证明它聚合的是**真事实**，不是把产物抄一遍。

从仓库根运行：

    python -m dsh_embagent.scripts.check_handoff

## 这个脚本防的是什么

交接层没有自己的判定逻辑，它只做聚合与排序。这类代码的失败模式很特别：

* **静默丢项** —— 某类产物存在但没被读进去，报告少了一行，
  而"少一行"和"本来就没有"长得一样。
* **重复计数** —— 同一个问题出现在两张表里（实测踩过：V2 与
  checks.json 是同一批重叠，被报成两项），人以为有 2 倍工作量。
* **把"没查"说成"没问题"** —— 缺产物时输出空表且不说明，
  最危险：看起来全绿。

所以这里的断言都针对这三类，而不是"输出的字符串长什么样"。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dsh_embagent.design import handoff                        # noqa: E402
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


class FakeStore:
    """最小的 store 替身：只实现 handoff 用到的四个方法。"""

    def __init__(self, project: str, design: DesignModel,
                 artifacts: dict[str, object]) -> None:
        self.id = project
        self._design = design
        self._artifacts = artifacts
        self.written: dict[str, object] = {}

    def exists(self) -> bool:
        return True

    def load(self) -> DesignModel:
        return DesignModel.from_dict(self._design.to_dict())

    def read(self, name: str):
        if name == "design.json":
            return self._design.to_dict()
        return self._artifacts.get(name)

    def write(self, name: str, payload) -> None:
        self.written[name] = payload


def _design(parts: int = 3, estimated: int = 0) -> DesignModel:
    rows = []
    for i in range(parts):
        rows.append(Part(
            ref=f"R{i + 1}", kind="resistor", label="10k",
            size_mm={"w": 2, "h": 1, "d": 1},
            physical_source="estimated" if i < estimated else "datasheet",
            placement=Placement(x_mm=5 + i * 5, y_mm=5)))
    d = DesignModel(id="fixture", title="交接夹具",
                    board=Board(kind="custom", name="夹具",
                                size_mm={"w": 100, "h": 100}),
                    parts=rows)
    return d


def _ladder(*, stopped: str = "", v2: str = "pass",
            v5: str = "not_run", v6: str = "not_run") -> dict:
    levels = [
        {"level": "V0", "title": "符号", "status": "pass"},
        {"level": "V1", "title": "约束", "status": "pass"},
        {"level": "V2", "title": "几何", "status": v2},
        {"level": "V3", "title": "电气", "status": "pass"},
        {"level": "V5", "title": "数字仿真", "status": v5},
        {"level": "V6", "title": "SPICE", "status": v6},
    ]
    if v2 == "fail":
        levels[2]["findings"] = [{
            "check": "space.overlap", "ok": False,
            "refs": ["C77", "U1"],
            "message": "C77 与 U1 实体重叠 0.90×1.60×0.80 mm",
        }]
    return {"stopped_at": stopped, "levels": levels}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    f = Failures()

    print("== 阻断项的来源 ==")

    h = handoff.build(FakeStore("p", _design(), {
        "ladder.json": _ladder(),
        "deferred.json": {"open_irreversible": ["package_family"]},
    }))
    blocking = h.by_category("blocking")
    f.check("未闭合的不可逆决策进阻断", len(blocking) == 1,
            f"{len(blocking)} 条")
    f.check("阻断里的动作点名了具体决策",
            "package_family" in blocking[0].action if blocking else False)

    print("\n== V2 与 checks 不能重复计数 ==")

    def checks_payload() -> dict:
        return {"summary": {"total": 14, "failed": 6},
                "checks": [{"check": "space.overlap", "ok": False,
                            "message": "C77 与 U1 实体重叠 0.90×1.60×0.80 mm"}]}

    # V2 失败 + checks 也失败 → **只报一次**
    h = handoff.build(FakeStore("p", _design(), {
        "ladder.json": _ladder(v2="fail"),
        "checks.json": checks_payload(),
    }))
    titles = [i.title for i in h.by_category("blocking")]
    f.check("V2 失败时不再单列 checks（避免重复计数）",
            not any("物理/机械检查" in t for t in titles), str(titles))
    f.check("V2 失败本身在阻断里",
            any("V2" in t for t in titles), str(titles))
    f.check("V2 的动作是可执行的话而不是 dict 字面量",
            all("{" not in i.action for i in h.by_category("blocking")),
            str([i.action for i in h.by_category("blocking")]))

    # V2 没跑（截断）+ checks 失败 → checks 兜底
    ladder_trunc = _ladder(stopped="V1")
    ladder_trunc["levels"][2]["status"] = "not_run"
    h = handoff.build(FakeStore("p", _design(), {
        "ladder.json": ladder_trunc,
        "checks.json": checks_payload(),
    }))
    titles = [i.title for i in h.by_category("blocking")]
    f.check("V2 被截断时 checks 兜底报出",
            any("物理/机械检查" in t for t in titles), str(titles))
    f.check("截断本身也进阻断",
            any("未运行" in t for t in titles), str(titles))

    print("\n== 缺数据 vs 默认值 ==")

    h = handoff.build(FakeStore("p", _design(), {"ladder.json": _ladder()}))
    missing = h.by_category("missing_data")
    f.check("零电气申报进缺数据",
            any("电气参数" in i.title for i in missing),
            str([i.title for i in missing]))
    f.check("零 budget claim 进缺数据",
            any("预算" in i.title for i in missing),
            str([i.title for i in missing]))

    h = handoff.build(FakeStore("p", _design(parts=3, estimated=3),
                                {"ladder.json": _ladder()}))
    f.check("估算尺寸进缺数据",
            any("估算值" in i.title for i in h.by_category("missing_data")))

    print("\n== 不会验 vs 没通过 ==")

    h = handoff.build(FakeStore("p", _design(), {"ladder.json": _ladder()}))
    unverifiable = h.by_category("unverifiable")
    # **别按总数钉死**：这一类里还会有别的"没法验"（如"没有最终连通性证据"）。
    # 只数 V5/V6 那两条（P46 的教训：整表计数一加东西就红）。
    v56 = [i for i in unverifiable
           if i.title.strip().startswith(("V5", "V6"))]
    f.check("V5/V6 的 not_run 进'不会验'而不是阻断",
            len(v56) == 2, f"{len(v56)} 条 / 全类 {len(unverifiable)} 条")
    f.check("V5 的动作说明要接固件",
            any("固件" in i.action for i in unverifiable),
            str([i.action for i in unverifiable]))
    f.check("'不会验'不阻止打板",
            not any(i.category == "blocking" for i in unverifiable))

    print("\n== 默认值必须读得到（曾经静默丢项） ==")

    # 回归：`_assumed` 原来去 `budget.json` 里翻 `coverify` 字段，
    # 而那个字段从来不存在 —— 第三类永远是 0 条。实测 `示例-单板`
    # 有 7 条 not_verified，交接单一条都没报。
    with_coverify = dict(_ladder())
    h = handoff.build(FakeStore("p", _design(), {
        "ladder.json": _ladder(),
        "coverify.json": {"ok": False, "not_verified": [
            "指向精度未声明，按默认 1.0° 计算（影响 CV-01）",
            "链路带宽未声明，按默认 8.0Mbps 计算（影响 CV-05）",
        ]},
    }))
    assumed = h.by_category("assumed")
    f.check("coverify.json 里的默认值被报成『机器用了默认值』",
            len(assumed) == 2, f"{len(assumed)} 条")
    f.check("默认值条目带可执行动作",
            all(i.action for i in assumed), str([i.action for i in assumed]))
    f.check("默认值进 assumed 而不是 blocking",
            not any(i.category == "blocking" for i in assumed))

    # 反向：没有 coverify.json 时不能虚构默认值
    h = handoff.build(FakeStore("p", _design(), {"ladder.json": _ladder()}))
    f.check("没有 coverify.json 时不虚构默认值",
            len(h.by_category("assumed")) == 0,
            str([i.title for i in h.by_category("assumed")]))
    f.check("没有 coverify.json 时 sources 记 missing",
            h.sources.get("coverify.json") == "missing",
            str(h.sources.get("coverify.json")))

    print("\n== P5 产物必须被消费（否则是死胡同） ==")

    # 回归：单元层与链路检查是本轮新加的。交接单如果读不到它们，
    # 它们就是"没人消费的报告" —— 正是 M1 修过的病。
    h = handoff.build(FakeStore("p", _design(), {
        "ladder.json": _ladder(),
        "links.json": {
            "checked": {"declared_links": 0, "inferred_links": 6},
            "orphans": {
                "counts": {"declared_unlinked": 2, "observed_unlinked": 15,
                           "units_without_nets": 7},
                "declared_unlinked": [{"peripheral": "USART2"},
                                      {"peripheral": "SPI1"}],
                "observed_unlinked": [{"peripheral": "SCL"}],
            },
        },
        "units.json": {"total": 8, "judgeable": 1,
                       "missing_data": {"no_netlist_count": 7}},
    }))
    titles = [i.title for i in h.by_category("missing_data")]
    f.check("链路表为空 → 交接单里有『一条都没声明』",
            any("一条都没声明" in t for t in titles), str(titles))
    f.check("声明未接的外设 → 交接单报出数量",
            any("声明了但没说接谁" in t for t in titles), str(titles))
    f.check("没有网表的单元 → 交接单报出数量",
            any("完全没有网表" in t for t in titles), str(titles))
    f.check("这四类都归入 missing_data（不是 blocking）",
            all(i.category == "missing_data" for i in h.items
                if "链路" in i.title or "外设" in i.title
                or "没有网表" in i.title))
    f.check("links.json / units.json 进 sources",
            h.sources.get("links.json") not in (None, "missing")
            and h.sources.get("units.json") not in (None, "missing"),
            str({k: v for k, v in h.sources.items()
                 if "units" in k or "links" in k}))

    # 反向：没有这些产物时不能虚构
    h = handoff.build(FakeStore("p", _design(), {"ladder.json": _ladder()}))
    f.check("没有 links.json 时不虚构链路问题",
            not any("链路" in i.title for i in h.items),
            str([i.title for i in h.items if "链路" in i.title]))
    f.check("没有 links.json 时 sources 记 missing",
            h.sources.get("links.json") == "missing",
            str(h.sources.get("links.json")))

    print("\n== 缺产物不等于没问题 ==")

    h = handoff.build(FakeStore("p", _design(), {}))
    # `design.json` **本来就存在**（没有它 build 会抛），所以指纹是有的；
    # 其余 6 份判定产物缺失。断言要按这个事实写，不能要求全部 missing。
    derived = {k: v for k, v in h.sources.items() if k != "design.json"}
    f.check("完全没有判定产物时其余 sources 全部记 missing",
            all(v == "missing" for v in derived.values()),
            str(h.sources))
    f.check("design.json 指纹不为 missing",
            h.sources.get("design.json") not in (None, "missing"),
            str(h.sources.get("design.json")))
    markdown = handoff.render_markdown(h.to_dict())
    f.check("渲染结果含『missing』标记",
            "missing" in markdown)
    f.check("空表渲染成『无。』而不是被省略",
            "_无。_" in markdown)

    print("\n== 交付判定：阻断 + 最终连通性证据（两条都要） ==")

    # **新规矩**（P47 真实验收逼出来的）：没有 `emit` 的最终连通性证据时，
    # "无阻断"只能说明**没查到问题**，不能说明能打板。
    # 实测反例：5 元件 0 网表的设计，blocking=0 曾经直接报"可打板"，
    # 而同一份设计的 emit 说"没有可布的对象"。
    no_emit = handoff.build(FakeStore("p", _design(), {"ladder.json": _ladder()}))
    f.check("无阻断但**没有 emit 证据** → deliverable=False",
            no_emit.deliverable is False,
            f"final={no_emit.final_status}")
    f.check("并写清缺的是最终连通性证据（不是沉默）",
            any("最终连通性证据" in i.title for i in no_emit.items),
            str([i.title for i in no_emit.items]))

    emit_ok = {"jobs": {"route": {"status": "ok"},
                        "pour": {"status": "ok"},
                        "verify": {"status": "ok"}}}
    with_emit = handoff.build(FakeStore("p", _design(), {
        "ladder.json": _ladder(), "emit.json": emit_ok}))
    f.check("无阻断 + verify=ok → deliverable=True（旧语义仍然成立）",
            with_emit.deliverable is True, str(with_emit.final_status))

    emit_bad = {"jobs": {"route": {"status": "not_ready"},
                         "pour": {"status": "not_ready"},
                         "verify": {"status": "not_ready"}}}
    with_bad = handoff.build(FakeStore("p", _design(), {
        "ladder.json": _ladder(), "emit.json": emit_bad}))
    f.check("emit 判 verify=not_ready → deliverable=False",
            with_bad.deliverable is False, str(with_bad.final_status))

    h = handoff.build(FakeStore("p", _design(), {
        "ladder.json": _ladder(),
        "deferred.json": {"open_irreversible": ["package_family"]},
        "emit.json": emit_ok,
    }))
    f.check("有阻断时 deliverable=False（**即使**有 emit 证据）",
            h.deliverable is False)

    print("\n== 产物指纹 ==")

    a = handoff.build(FakeStore("p", _design(), {"ladder.json": _ladder()}))
    b = handoff.build(FakeStore("p", _design(), {"ladder.json": _ladder()}))
    f.check("同内容同指纹", a.sources == b.sources, str(a.sources))
    other = _ladder()
    other["levels"][0]["status"] = "fail"
    c = handoff.build(FakeStore("p", _design(), {"ladder.json": other}))
    f.check("内容变了指纹跟着变",
            c.sources["ladder.json"] != a.sources["ladder.json"])

    print("\n== Markdown 表格不被内容破坏 ==")

    payload = {
        "project": "p", "deliverable": False,
        "counts": {c: 0 for c in handoff.CATEGORIES},
        "checked": {"parts": 3}, "sources": {}, "items": [{
            "category": "blocking", "title": "带|竖线的\ntitle",
            "status": "fail", "action": "有|管道\n换行", "risk": "x",
        }],
    }
    text = handoff.render_markdown(payload)
    row = [l for l in text.splitlines() if "带/竖线的 title" in l]
    f.check("管道与换行被转义（表格不会断）", len(row) == 1,
            f"{len(row)} 行匹配")

    print()
    if f.items:
        print(f"FAILED {len(f.items)} 项：")
        for name in f.items:
            print(f"  - {name}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
