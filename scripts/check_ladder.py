"""验证阶梯的自检 —— 证明"截断"和"没跑不等于通过"是真的。

从仓库根运行：

    python -m hw_velxio.scripts.check_ladder

## 为什么这几条断言值得写

阶梯最容易犯的错不是"算错"，而是**把状态渲染错**：

* 上游硬失败后，下游记成 `pass` —— 报告上出现过"极性反接，时序通过"；
* 空设计记成 `pass` —— 没有反例不等于没有问题（`phase.vacuous()` 的病因）；
* 判定器抛异常记成 `pass` —— 崩了但显示绿灯，比报错糟糕得多。

这三条都不会在正常路径上暴露。所以这里**故意造坏设计**，
assert 状态字符串本身。

## 反例同时也是文档

`polarity_bad` / `polarity_rev` / `polarity_good` 三份夹具留在仓库里：
它们既是回归基准，也是"什么叫硬失败"的说明。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hw_velxio.design import ladder                            # noqa: E402
from hw_velxio.design.model import (Board, DesignModel, Part,  # noqa: E402
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


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _part(ref: str, kind: str, role: str, x: float, y: float, *,
          w: float = 10, h: float = 10, d: float = 2,
          pins: list[str] | None = None, **elec) -> Part:
    return Part(ref=ref, kind=kind, role=role,
                size_mm={"w": w, "h": h, "d": d},
                physical_source="datasheet",
                placement=Placement(x_mm=x, y_mm=y),
                pins=list(pins or []), electrical=dict(elec))


def _design(pid: str, parts: list[Part],
            nets: list[dict]) -> DesignModel:
    return DesignModel(id=pid, board=Board(kind="custom",
                                           size_mm={"w": 90, "h": 70}),
                       parts=parts, nets=nets)


def overvoltage() -> DesignModel:
    """5V 灌 STM32（绝对最大值 4.0V）。V1 必须 fail。"""
    return _design("polarity_bad", [
        _part("U1", "stm32f103c8t6", "module", 5, 5, w=40, h=40, d=3),
        _part("C1", "cap_10u", "component", 55, 5, w=6, h=6, d=3,
              pins=["+", "-"], max_v=10.0),
    ], [
        {"net": "5V", "pins": [{"ref": "U1", "pin": "VDD"},
                               {"ref": "C1", "pin": "+"}]},
        {"net": "GND", "pins": [{"ref": "U1", "pin": "GND"},
                                {"ref": "C1", "pin": "-"}]},
    ])


def reverse_polarity() -> DesignModel:
    """电解电容反装：`+` 落地，`-` 接 5V。V1 必须 fail。"""
    return _design("polarity_rev", [
        _part("C5", "cap_470u_elec", "component", 10, 10, w=10, h=10, d=10,
              pins=["+", "-"], max_v=16.0),
        _part("U2", "pro_3v3", "module", 30, 10, w=20, h=20, d=3),
    ], [
        {"net": "GND", "pins": [{"ref": "C5", "pin": "+"},
                                {"ref": "U2", "pin": "GND"}]},
        {"net": "5V", "pins": [{"ref": "C5", "pin": "-"}]},
        {"net": "3V3", "pins": [{"ref": "U2", "pin": "3V3"}]},
    ])


def healthy() -> DesignModel:
    """3.3V 喂 STM32 —— 合规对照。V1 必须 pass（否则就是把好事报成坏事）。"""
    return _design("polarity_good", [
        _part("U3", "stm32f103c8t6", "module", 5, 5, w=40, h=40, d=3),
        _part("C9", "cap_10u", "component", 55, 5, w=6, h=6, d=3,
              pins=["+", "-"], max_v=10.0),
    ], [
        {"net": "3V3", "pins": [{"ref": "U3", "pin": "VDD"},
                                {"ref": "C9", "pin": "+"}]},
        {"net": "GND", "pins": [{"ref": "U3", "pin": "GND"},
                                {"ref": "C9", "pin": "-"}]},
    ])


def dangling() -> DesignModel:
    """网表指向不存在的元件。V0 必须 fail。"""
    design = _design("dangling", [
        _part("U4", "stm32f103c8t6", "module", 5, 5, w=40, h=40, d=3),
    ], [
        {"net": "3V3", "pins": [{"ref": "U4", "pin": "VDD"},
                                {"ref": "R99", "pin": "1"}]},
    ])
    return design


# ---------------------------------------------------------------------------
# 断言
# ---------------------------------------------------------------------------

def check_warning_is_not_failure(f: "Failures") -> None:
    """warning 不能把整层判成 fail（实测踩过的 bug）。

    ## 这个 bug 长什么样

    `_finish()` 第一版只看 `ok`、不看 `severity`：

        if any(not f.get("ok", True) for f in res.findings):
            res.status = "fail"

    于是 `consistency.provenance` 那条「元件尺寸是估算值」的 **warning**
    把整个 V2 判成 `fail`。实测 `001-blink`（3 个元件）：

        V2 = fail，但失败项 error=0 / warning=1
        ——几何上什么都没错，只是 btn1 的尺寸是估算的。

    后果不只是显示难看：它污染交接单的**阻断项**，让人以为布局有问题，
    去改本来正确的东西。

    warning 的语义是"结论有保留"，对应 `unknown`；不是"结论是否定"。
    """
    from hw_velxio.design.ladder import LevelResult, _finish

    # 只有 warning
    only_warning = LevelResult("V2")
    only_warning.findings = [{
        "check": "consistency.provenance", "ok": False,
        "severity": "warning",
        "message": "1 个元件的物理尺寸是估算值",
    }]
    _finish(only_warning, checked=5)
    f.check("只有 warning → unknown（不是 fail）",
            only_warning.status == "unknown", only_warning.status)
    f.check("warning 被记进 unknown 让人看见",
            any("估算" in u for u in only_warning.unknown),
            str(only_warning.unknown))

    # 只有 error
    only_error = LevelResult("V2")
    only_error.findings = [{
        "check": "space.overlap", "ok": False, "severity": "error",
        "message": "C77 与 U1 实体重叠",
    }]
    _finish(only_error, checked=5)
    f.check("有 error → fail", only_error.status == "fail", only_error.status)

    # error + warning 混合
    mixed = LevelResult("V2")
    mixed.findings = [
        {"check": "consistency.provenance", "ok": False,
         "severity": "warning", "message": "尺寸是估算值"},
        {"check": "space.overlap", "ok": False, "severity": "error",
         "message": "重叠"},
    ]
    _finish(mixed, checked=5)
    f.check("error + warning → fail（error 优先）",
            mixed.status == "fail", mixed.status)

    # 全通过
    clean = LevelResult("V2")
    clean.findings = [{"check": "space.overlap", "ok": True,
                       "severity": "info", "message": "没有重叠"}]
    _finish(clean, checked=5)
    f.check("全通过 → pass", clean.status == "pass", clean.status)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    fail = Failures()

    # -- 1. V0：悬空引用 ------------------------------------------------
    res = ladder.run_ladder(dangling(), levels=["V0", "V1"])
    fail.check("V0 抓到悬空引脚并 fail", res.level("V0").status == "fail",
               f"V0={res.level('V0').status}")
    fail.check("V0 硬失败 → stopped_at=V0", res.stopped_at == "V0",
               f"stopped_at={res.stopped_at!r}")
    fail.check("V0 硬失败 → V1 是 not_run（不是 pass）",
               res.level("V1").status == "not_run",
               f"V1={res.level('V1').status}")

    # -- 2. V1：绝对最大值超限 ------------------------------------------
    res = ladder.run_ladder(overvoltage(), levels=["V0", "V1", "V2", "V3"])
    fail.check("V1 抓到 5V 灌 STM32 并 fail",
               res.level("V1").status == "fail",
               f"V1={res.level('V1').status}")
    fail.check("V1 硬失败 → stopped_at=V1", res.stopped_at == "V1",
               f"stopped_at={res.stopped_at!r}")
    fail.check("V1 硬失败 → downstream=truncated",
               res.downstream == "truncated", res.downstream)
    # ★ 这条是本文件的核心：下游绝不能是 pass
    fail.check("V1 硬失败 → V2 是 not_run（不是 pass）",
               res.level("V2").status == "not_run",
               f"V2={res.level('V2').status}")
    fail.check("V1 硬失败 → V3 是 not_run（不是 pass）",
               res.level("V3").status == "not_run",
               f"V3={res.level('V3').status}")
    fail.check("超压 finding 指明元件与限值",
               any(f["check"] == "hard.abs_max" and "U1" in f["refs"]
                   for f in res.level("V1").findings))
    fail.check("超压 finding 带 measured/limit 数字",
               any(f.get("measured") == 5.0 and f.get("limit") == 4.0
                   for f in res.level("V1").findings
                   if f["check"] == "hard.abs_max"))

    # -- 3. V1：极性反接 -------------------------------------------------
    res = ladder.run_ladder(reverse_polarity(), levels=["V0", "V1", "V2"])
    fail.check("V1 抓到电解电容反装并 fail",
               res.level("V1").status == "fail",
               f"V1={res.level('V1').status}")
    fail.check("极性反接 → stopped_at=V1", res.stopped_at == "V1",
               f"stopped_at={res.stopped_at!r}")
    fail.check("极性 finding 指明是反接",
               any(f["check"] == "hard.reverse_polarity"
                   for f in res.level("V1").findings))

    # -- 4. 合规设计不被误报（反向对照）---------------------------------
    res = ladder.run_ladder(healthy(), levels=["V0", "V1", "V2"])
    fail.check("合规设计 V1 pass（不误报）",
               res.level("V1").status == "pass",
               f"V1={res.level('V1').status} "
               f"findings={res.level('V1').findings}")
    fail.check("合规设计不被截断", res.stopped_at == "",
               f"stopped_at={res.stopped_at!r}")

    # -- 5. 空设计：没有对象 ≠ 通过 -------------------------------------
    empty = DesignModel(id="empty", board=Board(kind="x"))
    res = ladder.run_ladder(empty)
    fail.check("空设计 V0 不是 pass", res.level("V0").status != "pass",
               f"V0={res.level('V0').status}")
    fail.check("空设计整体 ok=False", res.ok is False, f"ok={res.ok}")
    fail.check("空设计 V2 报 unknown 而非 pass",
               res.level("V2").status == "unknown",
               f"V2={res.level('V2').status}")

    # -- 6. 未实现的层如实报 not_run ------------------------------------
    res = ladder.run_ladder(healthy(), levels=["V5", "V6"])
    fail.check("V5 是 not_run（没接）", res.level("V5").status == "not_run",
               f"V5={res.level('V5').status}")
    fail.check("V6 是 not_run（没有 SPICE）",
               res.level("V6").status == "not_run",
               f"V6={res.level('V6').status}")
    fail.check("V5/V6 不是 pass", not res.ok, f"ok={res.ok}")

    # -- 7. 判定器抛异常 → unknown，绝不是 pass -------------------------
    class Exploding(DesignModel):
        pass

    saved = ladder.RUNNERS["V0"]

    def boom(design, **_kw):
        raise RuntimeError("故意炸")

    ladder.RUNNERS["V0"] = boom
    try:
        res = ladder.run_ladder(healthy(), levels=["V0"])
        fail.check("判定器抛异常 → unknown（不是 pass）",
                   res.level("V0").status == "unknown",
                   f"V0={res.level('V0').status}")
        fail.check("异常不会被吞成 ok", res.ok is False, f"ok={res.ok}")
    finally:
        ladder.RUNNERS["V0"] = saved

    # -- 8. 未知层名报错而不是静默跳过 ----------------------------------
    try:
        ladder.run_ladder(healthy(), levels=["V9"])
        fail.check("未知层名抛 ValueError", False, "没有抛异常")
    except ValueError:
        fail.check("未知层名抛 ValueError", True)

    # -- 9. 波特率计算本身 ----------------------------------------------
    # 16MHz / 115200 是 AVR 的经典组合：UBRR=8，实际 111111bps，误差 3.5%
    err = ladder.baud_error_pct(16e6, 115200)
    fail.check("16MHz@115200 误差约 3.5%（真实缺陷）",
               err is not None and 3.0 < err < 4.0, f"err={err}")
    # 8MHz / 9600 是标准组合，误差应接近 0
    err = ladder.baud_error_pct(8e6, 9600)
    fail.check("8MHz@9600 误差接近 0",
               err is not None and err < 0.5, f"err={err}")
    fail.check("时钟太低算不出（返回 None 而不是 0）",
               ladder.baud_error_pct(1000, 115200) is None)

    # -- 10. 电压解析：判不出来就是 None ---------------------------------
    fail.check("VIN 判不出电压（通用电源名）",
               ladder.net_volts(healthy(), "VIN") is None)
    fail.check("$1N8009 判不出电压（自动网名）",
               ladder.net_volts(healthy(), "$1N8009") is None)
    fail.check("3V3 判出 3.3V",
               (ladder.net_volts(healthy(), "3V3") or None) is not None
               and ladder.net_volts(healthy(), "3V3").volts == 3.3)
    fail.check("GND 判出 0V 且不会被当成未知",
               (ladder.net_volts(healthy(), "GND") or None) is not None
               and ladder.net_volts(healthy(), "GND").volts == 0.0)
    fail.check("VBAT_2S 按满电 8.4V 判",
               (ladder.net_volts(healthy(), "VBAT_2S") or None) is not None
               and ladder.net_volts(healthy(), "VBAT_2S").volts == 8.4)

    # -- 11. 哈希 kind 判不出家族 → 不猜 --------------------------------
    hashed = _part("U9", "101005049f85ad2f", "component", 0, 0)
    fail.check("立创哈希 kind 判不出家族（不给最近似）",
               ladder.family_of(hashed) is None,
               str(ladder.family_of(hashed)))
    fail.check("判不出家族 → pin_limit 为 None",
               ladder.pin_limit(hashed) is None)
    fail.check("元件申报 max_v 时优先用申报值",
               (ladder.pin_limit(_part("U8", "101005049f85ad2f",
                                       "component", 0, 0,
                                       max_v=5.5)) or (None,))[0] == 5.5)

    # -- 12. describe() 自洽 --------------------------------------------
    info = ladder.describe()
    # P18 加了 V7（运动）与 V8（制造），所以从 7 层变 9 层。
    # 断言写实际层数而不是"7"，因为层数是可扩展的——
    # 写死 7 会让每次加层都要改测试。
    fail.check("describe 列出 9 层", len(info["levels"]) == 9,
               str(len(info["levels"])))
    # V8 实现为 not_run（缺 P17 的发射层），所以它也在"未实现"里。
    # V7 在 MuJoCo 可用时是真跑的，所以不算"未实现"。
    not_impl = [lv["level"] for lv in info["levels"] if not lv["implemented"]]
    fail.check("describe 标注未实现的层",
               not_impl in (["V5", "V6"], ["V5", "V6", "V8"]),
               str(not_impl))
    fail.check("describe 标 V2/V3 委托给现有判定器",
               info["levels"][2]["delegates_to"] == "physical.run_checks"
               and info["levels"][3]["delegates_to"] == "electrical.run")

    # -- 13. warning 不得把整层判成 fail --------------------------------
    check_warning_is_not_failure(fail)

    if fail.items:
        print(f"\n❌ {len(fail.items)} 项失败")
        return 1
    print("\n✅ 验证阶梯检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
