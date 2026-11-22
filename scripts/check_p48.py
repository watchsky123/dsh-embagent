"""P48 验收 —— 三步闭环修复：网表不再丢、空输入不再绿、第二意见生效。

    python -m hw_velxio.scripts.check_p48

## 这一段修的是什么（都是真实验收逼出来的）

| # | 缺陷 | 修法 |
|---|---|---|
| F1 | `generate` 产出的设计**恒 0 条网表** | `ref_of` 同时以 `role` 与 `unit:<role>` 为键；链路按协议族挑**真实引脚名** |
| F2 | **0 网表的设计被判「可打板」** | V0 增 `symbol.empty_netlist`（error）；`handoff.deliverable` 要求 `emit.verify == ok` |
| F3 | 判定命令顶层写死 `status: ok` | `_judge_status()` 从 payload 自己的结论推 |
| F4 | `parity` 在"设计侧空"时容易读成没问题 | `design_pins` 为空 → `not_ready` 并写明原因 |

## 退化验证

每条断言都要能红：删掉对应闸门 → 脚本必须失败（本文件有 3 条退化用例）。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio import design_cli  # noqa: E402
from hw_velxio.design import generate, handoff, ladder, physical  # noqa: E402
from hw_velxio.design.model import (  # noqa: E402
    Board, DesignModel, Part, Placement,
)

_failures: list[str] = []
_checks = 0


def _safe(text: str) -> str:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    return str(text).encode(encoding, "replace").decode(encoding, "replace")


def check(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if ok:
        print(_safe(f"[OK ] {name}"))
    else:
        _failures.append(name)
        print(_safe(f"[FAIL] {name}"))
    if detail:
        print(_safe(f"       {detail}"))


def _board(w: float = 40, h: float = 32) -> Board:
    return Board(kind="custom", name="b", id="PCB#main",
                 outline=[(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)],
                 size_mm={"w": w, "h": h}, thickness_mm=1.6)


def _part(ref: str, pins: list[str], x: float, y: float,
          w: float = 2.0, h: float = 1.0) -> Part:
    return Part(ref=ref, kind="generic", size_mm={"w": w, "h": h, "d": 1.0},
                physical_source="datasheet", pins=list(pins),
                placement=Placement(x_mm=x, y_mm=y, side="top",
                                    board_id="PCB#main"))


# ---------------------------------------------------------------------------
# 1. F1：generate 的网表
# ---------------------------------------------------------------------------


def part_generate_nets() -> None:
    req = generate.Requirement(
        id="p48", title="验收", roles=["mcu", "radio"],
        interfaces=[{"a": "mcu", "b": "radio", "protocol": "spi",
                     "level_v": 3.3}],
        constraints={"area_mm2": 5000.0})
    report = generate.generate(req, max_candidates=1)
    check("1a. 需求能生成候选（否则后面的断言都无意义）",
          bool(report.get("candidates")), str(report.get("reason")))
    if not report.get("candidates"):
        return
    cand = report["candidates"][0]
    design = generate.materialize(cand, req)
    check("1b. **materialize 后网表不为空**（F1：曾恒为 0）",
          len(design.nets) >= 1,
          f"nets={len(design.nets)} links={len(cand.get('links') or [])}")
    first = (design.nets or [{}])[0]
    pins = [p for p in (first.get("pins") or []) if isinstance(p, dict)]
    check("1c. 网表的引脚名**非空**（空名会被 V0 判 `pin_unnamed`）",
          all(str(p.get("pin") or "") for p in pins) and len(pins) >= 2,
          str([(p.get("ref"), p.get("pin")) for p in pins]))
    check("1d. 每条网都标了 `level=peripheral`（承认它只是外设级示意）",
          all(n.get("level") == "peripheral" for n in design.nets),
          str([n.get("level") for n in design.nets]))
    check("1e. V0 在真候选上 pass（网表成立）",
          ladder.level_v0(design).status == "pass",
          str([f.get("check") for f in ladder.level_v0(design).findings]))


# ---------------------------------------------------------------------------
# 2. F2a：空网表必须被 V0 抓住
# ---------------------------------------------------------------------------


def part_empty_netlist() -> None:
    d = DesignModel(id="p48-empty", board=_board())
    d.add_part(_part("U1", ["1", "2"], 5.0, 5.0))
    d.add_part(_part("U2", ["1", "2"], 15.0, 5.0))
    res = ladder.level_v0(d)
    check("2a. 2 元件 + 0 网表 → V0 **fail**（不是 pass）",
          res.status == "fail",
          f"status={res.status} findings={[f.get('check') for f in res.findings]}")
    check("2b. 那条 finding 明确说「空输入不算通过」",
          any("空输入" in str(f.get("message"))
              for f in res.findings),
          str([f.get("message") for f in res.findings]))

    # 单引脚网：**warning**（部分模型里常见），不能把 V1 截断
    d2 = DesignModel(id="p48-thin", board=_board())
    d2.add_part(_part("U1", ["1", "2"], 5.0, 5.0))
    d2.add_part(_part("U2", ["1", "2"], 15.0, 5.0))
    d2.nets = [{"net": "A", "pins": [{"ref": "U1", "pin": "1"},
                                      {"ref": "U2", "pin": "1"}]},
               {"net": "STUB", "pins": [{"ref": "U1", "pin": "2"}]}]
    res2 = ladder.level_v0(d2)
    check("2c. 单引脚网 → **warning**（V0 变 unknown，不截断下游）",
          res2.status == "unknown"
          and any(f.get("check") == "symbol.thin_net"
                  and f.get("severity") == "warning" for f in res2.findings),
          f"status={res2.status}")


# ---------------------------------------------------------------------------
# 3. F2b：deliverable 必须有最终连通性证据
# ---------------------------------------------------------------------------


def _fake_store(design: DesignModel, files: dict) -> object:
    class _Store:
        id = design.id
        dir = Path(".")

        def exists(self) -> bool:
            return True

        def load(self) -> DesignModel:
            return design

        def read(self, name: str):
            return files.get(name)

    return _Store()


def part_deliverable() -> None:
    d = DesignModel(id="p48-deliv", board=_board())
    d.add_part(_part("U1", ["1", "2"], 5.0, 5.0))
    d.add_part(_part("U2", ["1", "2"], 15.0, 5.0))
    d.nets = [{"net": "A", "pins": [{"ref": "U1", "pin": "1"},
                                     {"ref": "U2", "pin": "1"}]}]
    ladder_ok = {"levels": [{"level": "V0", "status": "pass"}]}

    no_emit = handoff.build(_fake_store(d, {"ladder.json": ladder_ok}))
    check("3a. 无阻断 + **没有 emit 证据** → deliverable=False",
          no_emit.deliverable is False, str(no_emit.final_status))
    check("3b. 且这一条写进交接单（不是沉默）",
          any("最终连通性证据" in i.title for i in no_emit.items),
          str([i.title for i in no_emit.items]))

    verify_ok = {"jobs": {"route": {"status": "ok"}, "pour": {"status": "ok"},
                          "verify": {"status": "ok"}}}
    good = handoff.build(_fake_store(d, {"ladder.json": ladder_ok,
                                         "emit.json": verify_ok}))
    check("3c. 无阻断 + verify=ok → deliverable=True",
          good.deliverable is True, str(good.final_status))

    verify_bad = {"jobs": {"route": {"status": "not_ready"},
                           "pour": {"status": "not_ready"},
                           "verify": {"status": "not_ready"}}}
    bad = handoff.build(_fake_store(d, {"ladder.json": ladder_ok,
                                        "emit.json": verify_bad}))
    check("3d. verify=not_ready → deliverable=False（**不允许**「没查到问题就算过」）",
          bad.deliverable is False, str(bad.final_status))


# ---------------------------------------------------------------------------
# 4. F3：判定命令的顶层 status 必须与自己的结论一致
# ---------------------------------------------------------------------------


def part_command_status() -> None:
    d = DesignModel(id="p48-status", board=_board(20, 20))
    # 故意重叠 → physical 会报 error
    d.add_part(_part("U1", ["1", "2"], 5.0, 5.0, 4.0, 4.0))
    d.add_part(_part("U2", ["1", "2"], 5.5, 5.5, 4.0, 4.0))
    result = physical.run_checks(d)
    status = design_cli._judge_status(result)
    check("4a. 有 error 的检查结果 → `_judge_status` 不给 ok",
          result["summary"]["errors"] > 0 and status != "ok",
          f"errors={result['summary']['errors']} status={status}")
    check("4b. 阶梯按层看：全是 unknown 时是 `not_ready` 不是 `failed`",
          design_cli._judge_status(
              {"levels": [{"status": "unknown"}], "ok": False}) == "not_ready")
    check("4c. 阶梯里有 fail → `failed`",
          design_cli._judge_status(
              {"levels": [{"status": "fail"}], "ok": False}) == "failed")


# ---------------------------------------------------------------------------
# 5. F4：parity 的"设计侧空"守卫
# ---------------------------------------------------------------------------


def part_parity_guard() -> None:
    from hw_velxio.design.emit_lceda import parity_from_netlists

    d = DesignModel(id="p48-parity", board=_board())
    d.add_part(_part("U1", ["1", "2"], 5.0, 5.0))
    got = parity_from_netlists(d, ['{"components": {}}'])
    check("5a. 设计侧 0 引脚 → `not_ready` 并写明「对空输入没有意义」",
          got["status"] == "not_ready"
          and "空输入" in str(got.get("note")),
          f"{got.get('status')}：{got.get('note')}")

    d.nets = [{"net": "A", "pins": [{"ref": "U1", "pin": "1"}]}]
    got2 = parity_from_netlists(d, [])
    check("5b. 设计侧有引脚但没回读到网表 → 也说清是回读失败",
          got2["status"] == "not_ready"
          and got2.get("design_pins") == 1,
          str(got2.get("note")))


# ---------------------------------------------------------------------------
# 6. 退化验证
# ---------------------------------------------------------------------------


def part_degeneration() -> None:
    """三条：删掉对应闸门，前面的断言必须变红。"""
    # (1) 去掉 V0 的空网表检查 → 2a 变绿
    d = DesignModel(id="p48-degen", board=_board())
    d.add_part(_part("U1", ["1", "2"], 5.0, 5.0))
    d.add_part(_part("U2", ["1", "2"], 15.0, 5.0))
    baseline = ladder.level_v0(d).status
    original = ladder.level_v0

    def lax(design, **kw):  # noqa: ANN001
        res = original(design, **kw)
        res.findings = [f for f in res.findings
                        if f.get("check") != "symbol.empty_netlist"]
        from hw_velxio.design.ladder import _finish  # noqa: PLC0415
        res.status = "pass" if not res.findings else res.status
        return res

    ladder.level_v0 = lax  # type: ignore[assignment]
    try:
        degenerated = ladder.level_v0(d).status
    finally:
        ladder.level_v0 = original  # type: ignore[assignment]
    check("6a. 退化：去掉空网表检查 → 2a 变绿（证明它真的在拦）",
          baseline == "fail" and degenerated != "fail",
          f"baseline={baseline} degenerated={degenerated}")

    # (2) 去掉 deliverable 的证据要求 → 3a 变绿
    class _Lax(handoff.Handoff):  # type: ignore[misc]
        @property
        def deliverable(self) -> bool:
            return not self.by_category("blocking")

    d2 = DesignModel(id="p48-degen2", board=_board())
    d2.add_part(_part("U1", ["1", "2"], 5.0, 5.0))
    d2.add_part(_part("U2", ["1", "2"], 15.0, 5.0))
    fake = _fake_store(d2, {"ladder.json": {"levels": [{"level": "V0",
                                                       "status": "pass"}]}})
    strict = handoff.build(fake)
    lax_handoff = handoff.build(fake)
    lax_handoff.__class__ = _Lax          # 只为验证"老规矩会让 3a 变绿"
    check("6b. 退化：只看 blocking 的老规矩 → 3a 变绿",
          strict.deliverable is False and lax_handoff.deliverable is True,
          f"strict={strict.deliverable} lax={lax_handoff.deliverable}")

    # (3) 让 ref_of 只放 role 键 → 1b 变红（F1 复现）
    req = generate.Requirement(
        id="p48-degen3", roles=["mcu", "radio"],
        interfaces=[{"a": "mcu", "b": "radio", "protocol": "spi"}],
        constraints={"area_mm2": 5000.0})
    rep = generate.generate(req, max_candidates=1)
    if rep.get("candidates"):
        cand = dict(rep["candidates"][0])
        cand["links"] = [{"a": {"unit": "mcu"}, "b": {"unit": "radio"},
                          "protocol": "spi"}]
        strict_nets = len(generate.materialize(cand, req).nets)
        # 反过来：把 link 的 unit 改成**不存在**的名字 → 网表应为 0
        broken = dict(cand)
        broken["links"] = [{"a": {"unit": "nope"}, "b": {"unit": "nope2"},
                            "protocol": "spi"}]
        broken_nets = len(generate.materialize(broken, req).nets)
        check("6c. 退化：链路端点对不上 → 网表回到 0（证明 1b 不是恒真）",
              strict_nets >= 1 and broken_nets == 0,
              f"strict={strict_nets} broken={broken_nets}")


def main() -> int:
    print("=== P48 三步闭环修复 ===")
    part_generate_nets()
    part_empty_netlist()
    part_deliverable()
    part_command_status()
    part_parity_guard()
    part_degeneration()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 / {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(_safe(f"  [FAIL] {name}"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())