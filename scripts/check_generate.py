"""P6 验收：能力窄表 + 有限枚举生成。

从仓库根运行：

    python -m hw_velxio.scripts.check_generate

## 这个脚本守的三件事

1. **计划 §9.4 的硬验收**：同一需求跑两次结果一致（确定性），
   且 `--max-candidates 20` 能给出 ≥3 个候选。
2. **候选之间必须真的不同**：按签名去重，不能靠改名字凑数量。
3. **不能选出语义荒谬的组合**：第一版按"能力集合"过滤，
   于是"radio 单元"选出了气压计 `bmp280`（它的 SPI/GPIO 能力
   覆盖了 radio 的需求）。这类错误不会让程序崩，只会让输出变蠢，
   而变蠢的输出没人会去用。

## 为什么不证收敛

本方案**没有** `while not ok: 改设计` 的循环。断言里专门有一条
"枚举数量不超过 max_candidates"，它守的是"天然终止"这件事。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hw_velxio.design import capability, generate     # noqa: E402


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}{f' — {detail}' if detail else ''}")
            self.items.append(name)


def _req(**kw) -> generate.Requirement:
    base = {
        "id": "t", "title": "夹具需求",
        "roles": ["mcu", "radio", "sensor", "power_in"],
        "interfaces": [{"a": "mcu", "b": "radio", "protocol": "spi",
                        "level_v": 3.3},
                       {"a": "mcu", "b": "sensor", "protocol": "i2c",
                        "level_v": 3.3}],
    }
    base.update(kw)
    return generate.Requirement.from_dict(base)


# ---------------------------------------------------------------------------
# 能力表
# ---------------------------------------------------------------------------

def check_capability(f: Failures) -> None:
    print("== 能力表：可信来源是硬条件 ==")

    f.check("表里全是可信来源",
            all(capability.trusted(k) for k in capability.CAPABILITY),
            str([k for k in capability.CAPABILITY if not capability.trusted(k)]))

    # 不可信来源必须被跳过
    saved = dict(capability.CAPABILITY)
    try:
        capability.CAPABILITY["bogus"] = {
            "fills": {"mcu"}, "provides": {"uart", "spi", "gpio"},
            "footprint_mm2": 1.0, "source": "guess",
        }
        found = capability.candidates({"uart", "spi", "gpio"}, role="mcu")
        f.check("source=guess 的型号不参与选择",
                "bogus" not in found, str(found))
        capability.CAPABILITY["bogus"]["source"] = "datasheet"
        found = capability.candidates({"uart", "spi", "gpio"}, role="mcu")
        f.check("同一型号改成 datasheet 后参与选择",
                "bogus" in found, str(found))
    finally:
        capability.CAPABILITY.clear()
        capability.CAPABILITY.update(saved)

    print("\n== 能力表：fills 挡住语义荒谬 ==")

    radio = capability.candidates({"spi"}, role="radio")
    f.check("radio 候选里没有气压计 bmp280",
            "bmp280" not in radio, str(radio))
    f.check("radio 候选里有真正带射频的型号",
            "esp32s3" in radio or "lora-sx1278" in radio, str(radio))

    sensor = capability.candidates(set(), role="sensor")
    f.check("sensor 候选里没有 MCU（fills 挡住）",
            "atmega328p" not in sensor and "stm32f103c8t6" not in sensor,
            str(sensor))
    f.check("sensor 候选非空", bool(sensor), str(sensor))

    print("\n== 能力表：空结果必须解释 ==")

    miss = capability.explain_miss({"eth", "wifi"})
    f.check("空结果给出 reason",
            miss.get("reason") in ("filtered_out", "table_empty"), str(miss))
    f.check("空结果列出每个型号的淘汰原因",
            bool(miss.get("rejected")), str(miss.get("rejected"))[:80])
    f.check("空结果给出下一步建议", bool(miss.get("hint"),), str(miss))

    miss_role = capability.explain_miss({"spi"}, role="radio")
    f.check("按 role 过滤时淘汰原因说明'不是 radio'",
            any("不是 radio" in v for v in miss_role.get("rejected", {}).values())
            or miss_role.get("candidates", 0) > 0,
            str(miss_role.get("rejected"))[:80])


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------

def check_generate(f: Failures) -> None:
    print("\n== 生成：计划 §9.4 硬验收 ==")

    report = generate.generate(_req(), max_candidates=20)
    f.check("同一需求能出候选", bool(report["candidates"]),
            str(report.get("reason")))
    f.check("候选数 >= 3（计划要求）",
            len(report["candidates"]) >= 3, str(len(report["candidates"])))
    f.check("状态 ok（有可交付候选）", report["status"] == "ok",
            str(report["status"]))

    first = json.dumps(report, ensure_ascii=False, sort_keys=True)
    second = json.dumps(generate.generate(_req(), max_candidates=20),
                        ensure_ascii=False, sort_keys=True)
    f.check("同一需求跑两次结果完全一致（确定性）",
            first == second, f"长度 {len(first)} vs {len(second)}")

    print("\n== 生成：数量上限与去重 ==")

    report = generate.generate(_req(), max_candidates=5)
    f.check("枚举不超过 max_candidates",
            len(report["candidates"]) <= 5, str(len(report["candidates"])))

    ids = [c["id"] for c in report["candidates"]]
    f.check("候选 id 不重复", len(ids) == len(set(ids)), str(ids))

    sigs = []
    for cand in report["candidates"]:
        part_key = "|".join(f"{','.join(p.get('units') or [p['unit']])}"
                            f":{p['model']}" for p in cand["parts"])
        link_key = "|".join(sorted(
            f"{l['a'].get('unit')}->{l['b'].get('unit')}"
            for l in cand["links"]))
        sigs.append(f"{part_key}||{link_key}")
    f.check("候选签名互不相同（不是靠改名凑数）",
            len(sigs) == len(set(sigs)), f"{len(sigs)} 个签名")

    print("\n== 生成：语义正确性 ==")

    report = generate.generate(_req(), max_candidates=20)
    for cand in report["candidates"]:
        for row in cand["parts"]:
            if "radio" not in (row.get("units") or []):
                continue
            model = row["model"]
            f.check(f"radio 单元选的是带射频的型号（{model}）",
                    "wifi" in (capability.get(model) or {}).get("provides", set())
                    or "rf" in (capability.get(model) or {}).get("provides", set()),
                    str((capability.get(model) or {}).get("provides")))

    print("\n== 生成：一颗料兼两职要合并 ==")

    report = generate.generate(_req(), max_candidates=20)
    shared = [c for c in report["candidates"]
              if any(len(p.get("units") or []) > 1 for p in c["parts"])]
    f.check("存在合并的候选（esp32s3 兼 mcu+radio）", bool(shared),
            f"{len(shared)} 个")
    for cand in shared[:1]:
        dup = [p for p in cand["parts"] if len(p.get("units") or []) > 1]
        f.check("合并的料只出现一次",
                len(dup) == len({p["model"] for p in dup}),
                str([p["model"] for p in dup]))

    print("\n== 生成：排序遵守硬失败优先 ==")

    report = generate.generate(_req(), max_candidates=20)
    keys = [(c["hard_failures"], c["soft_failures"], c["unknowns"], c["cost"])
            for c in report["candidates"]]
    f.check("排序键单调不减", keys == sorted(keys), str(keys[:5]))

    print("\n== 生成：没候选时解释原因 ==")

    impossible = _req(roles=["mcu", "storage"],
                      constraints={"area_mm2": 1.0})
    report = generate.generate(impossible, max_candidates=5)
    f.check("约束过紧 → 不给候选", report["candidates"] == [],
            str(len(report.get("candidates") or [])))
    f.check("约束过紧 → status=not_ready", report["status"] == "not_ready",
            str(report["status"]))
    f.check("约束过紧 → 说清哪个单元没型号",
            bool(report.get("misses")), str(list((report.get("misses") or {}))))

    empty = generate.generate(generate.Requirement(id="e", roles=[]),
                              max_candidates=5)
    f.check("没有角色 → not_ready 而不是空成功",
            empty["status"] == "not_ready" and not empty["candidates"],
            str(empty["status"]))

    print("\n== 生成：未映射的角色不静默丢弃 ==")

    report = generate.generate(_req(roles=["mcu", "display", "undefined_hw"]),
                               max_candidates=5)
    f.check("未映射角色被列进 unresolved_roles",
            bool(report.get("unresolved_roles")), str(report.get("unresolved_roles")))


# ---------------------------------------------------------------------------
# 需求文件
# ---------------------------------------------------------------------------

def check_requirement_io(f: Failures) -> None:
    print("\n== 需求文件读写 ==")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "req.json"
        path.write_text(json.dumps({
            "id": "io", "roles": ["mcu"], "constraints": {"area_mm2": 500},
        }, ensure_ascii=False), encoding="utf-8")
        req = generate.load_requirement(path)
        f.check("能读 JSON 需求", req.id == "io", req.id)
        f.check("constraints 被读进来",
                req.constraints.get("area_mm2") == 500, str(req.constraints))

        bad = Path(tmp) / "bad.json"
        bad.write_text("[1,2,3]", encoding="utf-8")
        try:
            generate.load_requirement(bad)
            f.check("非对象需求文件应当报错", False, "没有抛异常")
        except ValueError:
            f.check("非对象需求文件报 ValueError", True)


# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    f = Failures()

    check_capability(f)
    check_generate(f)
    check_requirement_io(f)

    print()
    if f.items:
        print(f"FAILED {len(f.items)} 项：")
        for name in f.items:
            print(f"  - {name}")
        return 1
    print("P6 验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
