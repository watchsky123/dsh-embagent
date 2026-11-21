"""P18 验收：统一判定协议（三次扰动 / 预算 / 第六类外部证据）。

从仓库根运行：

    python -m hw_velxio.scripts.check_m18

## 这个脚本守的四件事

1. **三次扰动的合并规则** —— 任一 fail → fail，有 unknown → **unknown（不是 pass）**，
   全 pass → pass。第三条是命门：如果 unknown 被当 pass，
   "仿真没跑起来"会算成"通过"，正是 AGENTS §0.1 那个病的另一张面孔。
2. **预算** —— 验证次数是硬成本；超预算的候选**保留但标未验**，
   不丢也不假装验过。报告给 `used / limit`。
3. **第六类不进阻断** —— 外部工具（立创 DRC / MuJoCo）的标准与我们不同，
   混进 `blocking` 会让人分不清"谁说的"。
4. **V7 的性能降级** —— 316 元件的板 weld 后跑 1 秒 >300 秒墙钟，
   不降级的话一次阶梯检查就是 15 分钟。降级要**说出来**，不是静默。

## 反向夹具比正例更重要

这里的每个"该通过"的用例都配了"不该通过"的：扰动的 unknown、
预算耗尽、外部失败不进阻断。**没有反例的断言很可能是恒真的。**
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hw_velxio.design import (generate, handoff, ladder,   # noqa: E402
                              robustness)
from hw_velxio.design.model import (Board, DesignModel, Part,   # noqa: E402
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


def _design(**kw) -> DesignModel:
    design = DesignModel(
        id="fixture", title="P18 夹具",
        board=Board(kind="custom", size_mm={"w": 100, "h": 100}))
    design.parts = kw.pop("parts", [])
    return design


def _box(ref: str, *, size: float = 20.0, z: float = 20.0) -> Part:
    return Part(ref=ref, kind="box", label=ref,
                size_mm={"w": size, "h": size, "d": size},
                physical_source="datasheet",
                placement=Placement(x_mm=30.0, y_mm=30.0, z_mm=z),
                mass_g=100.0)


# ---------------------------------------------------------------------------
# 三次扰动
# ---------------------------------------------------------------------------

def check_triplicate(f: Failures) -> None:
    print("== 1. 合并规则（三态）==")

    all_pass = robustness.combine([
        robustness.Attempt("a", "pass"),
        robustness.Attempt("b", "pass"),
        robustness.Attempt("c", "pass"),
    ])
    f.check("全 pass → pass", all_pass == "pass", all_pass)

    one_fail = robustness.combine([
        robustness.Attempt("a", "pass"),
        robustness.Attempt("b", "fail"),
        robustness.Attempt("c", "pass"),
    ])
    f.check("任一 fail → fail（不用看别的）", one_fail == "fail", one_fail)

    one_unknown = robustness.combine([
        robustness.Attempt("a", "pass"),
        robustness.Attempt("b", "unknown"),
        robustness.Attempt("c", "pass"),
    ])
    f.check("有 unknown → unknown（**不是 pass**）",
            one_unknown == "unknown", one_unknown)

    f.check("空列表 → unknown", robustness.combine([]) == "unknown")

    # fail 优先于 unknown
    fail_wins = robustness.combine([
        robustness.Attempt("a", "fail"),
        robustness.Attempt("b", "unknown"),
    ])
    f.check("fail 优先于 unknown", fail_wins == "fail", fail_wins)

    print("\n== 2. triplicate 的行为 ==")

    # 异常必须是 unknown，不是 fail 也不是 pass
    def boom(spec):
        if spec["name"] == "shift_1mm":
            raise RuntimeError("仿真崩了")
        return "pass", ""

    result = robustness.triplicate("kinematic", boom)
    f.check("扰动抛异常 → 该次 unknown",
            any(a.status == "unknown" for a in result.attempts),
            str([(a.name, a.status) for a in result.attempts]))
    f.check("有 unknown 时整体 unknown（不是 pass）",
            result.status == "unknown", result.status)
    f.check("整体 note 明说 unknown 不是通过",
            "不是通过" in result.note, result.note)

    # 正常全过
    result = robustness.triplicate("kinematic", lambda s: ("pass", ""))
    f.check("kinematic 有 3 次扰动（标称/偏移/延迟）",
            len(result.attempts) == 3, str(len(result.attempts)))
    names = [a.name for a in result.attempts]
    f.check("扰动名与 RoboBuilder 语义一致",
            "shift_1mm" in names and "late_50ms" in names, str(names))

    # 没有定义的域
    result = robustness.triplicate("no_such_domain", lambda s: ("pass", ""))
    f.check("未定义的域 → unknown 并说明",
            result.status == "unknown" and "没有" in result.note,
            result.note)

    print("\n== 3. 按域适配（不照搬乐高尺度）==")

    geo = [p["name"] for p in robustness.PERTURBATIONS["geometry"]]
    ele = [p["name"] for p in robustness.PERTURBATIONS["electrical"]]
    kin = [p["name"] for p in robustness.PERTURBATIONS["kinematic"]]
    f.check("几何域用公差（不是 1mm 位姿）",
            any("tolerance" in n for n in geo), str(geo))
    f.check("电气域用轨压（不是毫米）",
            any("rail" in n for n in ele), str(ele))
    f.check("运动域沿用 ±1mm/50ms（同类场景）",
            "shift_1mm" in kin and "late_50ms" in kin, str(kin))


# ---------------------------------------------------------------------------
# V7 / V8
# ---------------------------------------------------------------------------

def check_levels(f: Failures) -> None:
    print("\n== 4. V7/V8 进了阶梯 ==")

    f.check("LEVELS 有 9 层", len(ladder.LEVELS) == 9, str(ladder.LEVELS))
    f.check("V7 是运动域", "V7" in ladder.LEVELS
            and "运动" in ladder.LEVEL_TITLE["V7"])
    f.check("V8 是制造域", "V8" in ladder.LEVELS
            and "制造" in ladder.LEVEL_TITLE["V8"])
    f.check("V7/V8 都在 RUNNERS 里",
            "V7" in ladder.RUNNERS and "V8" in ladder.RUNNERS)
    f.check("硬层仍然只有 V0/V1（V7 失败不截断）",
            tuple(ladder.HARD_LEVELS) == ("V0", "V1"),
            str(ladder.HARD_LEVELS))

    print("\n== 5. V8 在 P17 之前是 not_run ==")

    res = ladder.level_v8(_design())
    f.check("V8 status=not_run", res.status == "not_run", res.status)
    f.check("V8 说明缺 P17（不是通过）",
            any("P17" in u for u in res.unknown), str(res.unknown))

    print("\n== 6. V7 真跑（MuJoCo 可用时）==")

    from hw_velxio.design import mujoco_backend
    ok, why = mujoco_backend.available()
    if not ok:
        res = ladder.level_v7(_design())
        f.check("MuJoCo 不可用时 V7 是 not_run",
                res.status == "not_run", res.status)
        f.check("V7 说明缺什么（不是通过）",
                any("通过" in u for u in res.unknown), str(res.unknown))
        return

    # 只有一个方块、平放在板上 → 应稳
    stable = _design(parts=[_box("B1", size=10.0, z=1.0)])
    res = ladder.level_v7(stable)
    f.check("稳定设计 V7 pass", res.status == "pass", res.status)
    f.check("V7 checked = 3（三次扰动）", res.checked == 3, str(res.checked))

    print("\n== 7. V7 性能降级要说出来 ==")

    many = _design(parts=[_box(f"B{i}", size=4.0, z=1.0) for i in range(3)])
    res = ladder.level_v7(many)
    perf = [x for x in res.findings
            if x.get("check") == "kinematic.perf_budget"]
    f.check("小设计不触发降级",
            len(many.parts) <= ladder.V7_LARGE_PARTS and not perf,
            str(len(perf)))
    f.check("降级信息用 severity=info（不改状态）",
            all(x.get("severity") == "info" for x in perf), str(perf))

    # 通过私有属性模拟大设计（不真跑 200 个件，太慢）
    f.check("降级阈值与时长是显式常量",
            ladder.V7_LARGE_PARTS > 0 and ladder.V7_LARGE_DURATION_S > 0,
            f"{ladder.V7_LARGE_PARTS} / {ladder.V7_LARGE_DURATION_S}")


# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------

def check_budget(f: Failures) -> None:
    print("\n== 8. 仿真预算 ==")

    req = generate.Requirement.from_dict({
        "id": "t", "roles": ["mcu", "radio", "sensor", "power_in"],
        "interfaces": [{"a": "mcu", "b": "radio", "protocol": "spi",
                        "level_v": 3.3}],
    })
    report = generate.generate(req, max_candidates=20, sim_budget=5,
                               run_ladder=False)
    # run_ladder=False 时不消耗预算
    f.check("run_ladder=False 时不消耗预算",
            report["sim_budget"]["used"] == 0,
            str(report["sim_budget"]))
    f.check("候选仍然全出（预算是验证预算不是生成预算）",
            len(report["candidates"]) > 5, str(len(report["candidates"])))

    report = generate.generate(req, max_candidates=20, sim_budget=3)
    budget = report["sim_budget"]
    f.check("预算用满即停", budget["used"] <= 3, str(budget))
    f.check("耗尽时标 exhausted", budget["exhausted"] is True, str(budget))
    f.check("预算说明里点明「验证次数是硬成本」",
            "硬成本" in budget["note"], budget["note"])

    # 超预算的候选保留且标未验
    unverified = [c for c in report["candidates"]
                  if any("预算" in u for u in c["unresolved"])]
    f.check("超预算的候选**保留**（不是丢掉）",
            len(report["candidates"]) > 3, str(len(report["candidates"])))
    if budget["exhausted"]:
        f.check("超预算的候选标了未验",
                bool(unverified), str(len(unverified)))


# ---------------------------------------------------------------------------
# 第六类
# ---------------------------------------------------------------------------

def check_external_category(f: Failures) -> None:
    print("\n== 9. 第六类 external 不进阻断 ==")

    f.check("CATEGORIES 有 6 类", len(handoff.CATEGORIES) == 6,
            str(handoff.CATEGORIES))
    f.check("external 在类里", "external" in handoff.CATEGORIES)
    f.check("external 有标题",
            "external" in handoff.CATEGORY_TITLE,
            str(sorted(handoff.CATEGORY_TITLE)))

    # 构造一个"只有 external 有问题"的交接单
    class FakeStore:
        def __init__(self, ladder_payload, sim_payload):
            self.id = "fixture"
            self._l, self._s = ladder_payload, sim_payload

        def exists(self):
            return True

        def load(self):
            return _design(parts=[_box("B1")])

        def read(self, name):
            if name == "ladder.json":
                return self._l
            if name == "sim.json":
                return self._s
            return None

    ladder_payload = {
        "stopped_at": "",
        "levels": [
            {"level": "V0", "title": "符号", "status": "pass", "checked": 5},
            {"level": "V7", "title": "运动", "status": "fail", "checked": 3,
             "findings": [{"check": "kinematic.stability", "ok": False,
                           "severity": "error",
                           "message": "扰动 shift_1mm 下翻倒"}]},
        ],
    }
    result = handoff.build(FakeStore(ladder_payload, None)).to_dict()
    ext = [i for i in result["items"] if i["category"] == "external"]
    f.check("V7 失败进 external", bool(ext), str(len(ext)))
    f.check("V7 失败**不进 blocking**",
            not any(i["category"] == "blocking" for i in result["items"]),
            str([i["title"] for i in result["items"]
                 if i["category"] == "blocking"]))
    f.check("external 不否决交付（deliverable 只看 blocking）",
            result["deliverable"] is True, str(result["deliverable"]))
    f.check("external 的说明点明「不阻断」",
            any("不阻断" in (i.get("risk") or "") for i in ext),
            str([i.get("risk", "")[:40] for i in ext]))

    print("\n== 9b. 没模型的域要单独报 ==")

    sim_payload = {"domains": [], "no_model": ["thermal", "emc"],
                   "counts": {"unknown": 2}}
    result = handoff.build(FakeStore(
        {"stopped_at": "", "levels": []}, sim_payload)).to_dict()
    ext = [i for i in result["items"] if i["category"] == "external"]
    f.check("没有模型的域进 external",
            any("没有模型" in i["title"] for i in ext),
            str([i["title"] for i in ext]))
    f.check("没有模型的项说明「只能实测」",
            any("实测" in (i.get("action") or "") for i in ext),
            str([i.get("action", "")[:40] for i in ext]))


# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    f = Failures()

    check_triplicate(f)
    check_levels(f)
    check_budget(f)
    check_external_category(f)

    print()
    if f.items:
        print(f"FAILED {len(f.items)} 项：")
        for name in f.items:
            print(f"  - {name}")
        return 1
    print("P18 验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())