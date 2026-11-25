"""M3 验收：P7 Renode 接入 + P8 `.ioc` → links 抽取。

从仓库根运行：

    python -m dsh_embagent.scripts.check_m3

## 这个脚本守的四件事

1. **计划 §11.2 的硬判据**：7 个 `.ioc` → ≥10 条 inferred links。
2. **向量表推理必须能拒绝坏输入**：`wifi.bin` 的 SP 不在 SRAM、
   Reset 不在 flash，必须 `ok=False` 并说明是哪几条不成立。
   **猜一个加载地址比不给地址更危险**——错地址会让 Renode
   从垃圾数据开始执行，看起来"跑起来了"但结果是假的。
3. **inferred 不能变成 declared**：`.ioc` 抽出的链路默认
   `apply_draft(only_declared=True)` 一条都不写。这条纪律若破了，
   推断结果会被 `links.validate` 当硬结论，人会被自己的推断判 fail。
4. **P7 必须诚实报 `runnable_here=False`**：本机实测无法在 DSH 内
   加载 CPU 平台（Renode 要独占打开锁文件）。假装能跑比不能跑更糟。

## 为什么 P7 的验收不要求"真跑出串口"

计划 §10.4 写的是"串口输出被捕获到 stdout"。本机做不到：
DSH 沙箱拦截了 Renode 的 CPU 初始化（实测，见 `renode.py` 开头）。
所以验收改成**验证产物正确性**：脚本结构、加载地址、connector 顺序、
外部执行指引齐全。真跑需要在 DSH 外双击 `run.cmd`。
这不是放宽标准，是把标准落在能做到的事上——并明确说清差在哪。
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dsh_embagent.design import ioc_links, renode            # noqa: E402
from dsh_embagent.design.model import (Board, DesignModel, Part,   # noqa: E402
                                    Placement)

#: 用户工作区（.ioc 与固件都在这里）。
from dsh_embagent.scripts._probe import path as probe_path  # noqa: E402
WORKSPACE = probe_path("DSH_EMBAGENT_PROBE_WORKSPACE")


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}{f' — {detail}' if detail else ''}")
            self.items.append(name)


def _have_workspace() -> bool:
    return WORKSPACE.is_dir()


# ---------------------------------------------------------------------------
# P8
# ---------------------------------------------------------------------------

def check_ioc(f: Failures) -> None:
    print("== P8 .ioc 解析 ==")

    if not _have_workspace():
        print(f"  skip 找不到 {WORKSPACE}")
        return

    paths = ioc_links.discover(WORKSPACE)
    f.check("发现 7 个 .ioc", len(paths) == 7, str(len(paths)))

    report = ioc_links.extract(WORKSPACE)
    counts = report["counts"]
    f.check("计划 §11.2 判据：≥10 条 inferred links",
            counts["links"] >= 10, str(counts["links"]))
    f.check("每条链路都是 inferred（不是 declared）",
            all(str(l.get("source")) == "inferred"
                for l in report["draft"]["links"]),
            str({str(l.get("source")) for l in report["draft"]["links"]}))
    f.check("每条链路带 evidence（能追溯到哪个 .ioc 的哪个引脚）",
            all(l.get("evidence") for l in report["draft"]["links"]))
    f.check("每条链路带 tier（可信度档位）",
            all(str(l.get("tier")) in ("2", "3")
                for l in report["draft"]["links"]),
            str({str(l.get("tier")) for l in report["draft"]["links"]}))

    print("\n== P8 端点抽取的具体正确性 ==")

    parsed = {row["board"]: row for row in
              [ioc_links.parse_ioc(p) for p in paths]}
    front = parsed.get("F401-Front")
    f.check("F401-Front 解析出 MCU 型号",
            bool(front and front["mcu"].startswith("STM32F401")),
            str(front["mcu"] if front else "缺失"))
    if front:
        pins = {ep.pin: ep for ep in front["endpoints"]}
        f.check("PA2 被认成 USART2 tx",
                pins.get("PA2") is not None and pins["PA2"].role == "tx",
                str(pins.get("PA2")))
        f.check("PA10 被认成 USART1 rx",
                pins.get("PA10") is not None and pins["PA10"].role == "rx",
                str(pins.get("PA10")))

    print("\n== P8 配对不能瞎配 ==")

    # TX 必须接 RX，不能接 TX
    for link in report["draft"]["links"]:
        a = link.get("a") or {}
        b = link.get("b") or {}
        if link.get("protocol") == "uart":
            pair = {str(a.get("role")), str(b.get("role"))}
            f.check(f"{link['id']} 的 UART 是 TX↔RX 而不是 TX↔TX",
                    pair == {"tx", "rx"}, str(pair))
            break

    # 合并后信号数 >1 说明 SCL+SDA 被合成一条总线
    merged = [l for l in report["draft"]["links"]
              if int(l.get("signal_count") or 0) > 1]
    f.check("多信号接口被合并成一条链路（不是虚增条数）",
            bool(merged), f"{len(merged)} 条合并")

    # 配不上的要如实列出
    f.check("配不上的端点进 unpaired 并给原因",
            all(u.get("why") for u in report["draft"]["unpaired"]),
            str(report["draft"]["unpaired"][:1]))

    print("\n== P8 纪律：inferred 不落地 ==")

    design = _two_board()
    draft = ioc_links.Draft(links=[dict(l) for l in report["draft"]["links"]],
                            unpaired=[], boards=[], counts=counts)
    added = ioc_links.apply_draft(design, draft, only_declared=True)
    f.check("only_declared=True 时一条 inferred 都不写",
            added == 0 and not design.links_list, f"added={added}")

    added = ioc_links.apply_draft(design, draft, only_declared=False)
    f.check("only_declared=False 时才会写入", added > 0, f"added={added}")
    f.check("写入的仍标 inferred（仍会被 validate 跳过）",
            all(str(l.get("source")) == "inferred"
                for l in design.links_list),
            str({str(l.get("source")) for l in design.links_list}))

    print("\n== P8 闭环：确认后才变成 declared ==")

    # 抽出来 -> 人确认（promote）-> 落地 -> 链路检查真的执行。
    # 没有这个出口的话，抽出来的 13 条永远用不上，P8 等于白做。
    design = _two_board()
    draft = ioc_links.Draft(links=[dict(l) for l in report["draft"]["links"]])
    f.check("promote 之前没有 declared 链路",
            all(str(l.get("source")) == "inferred" for l in draft.links))

    promoted = ioc_links.promote(draft, None)
    f.check("promote 后全部变成 declared",
            promoted > 0 and all(str(l.get("source")) == "declared"
                                 for l in draft.links), str(promoted))
    f.check("promote 记下来源（可追溯）",
            all(l.get("promoted_from") == "inferred" for l in draft.links))

    added = ioc_links.apply_draft(design, draft, only_declared=True)
    f.check("promote 后 apply_draft 真的写入了",
            added > 0, f"added={added}")

    from dsh_embagent.design import links as links_module
    result = links_module.validate(design, design.links_list, [])
    f.check("写入后 links.validate 真的检查了这些链路",
            result["checked"]["declared_links"] > 0,
            str(result["checked"]))

    print("\n== P8 待确认清单 ==")

    review = ioc_links.review_list(
        ioc_links.Draft(links=report["draft"]["links"]))
    f.check("清单里每条都有证据", all(r["evidence"] for r in review))
    f.check("清单按可信度排序（tier 小的在前）",
            [r["tier"] for r in review] == sorted(r["tier"] for r in review),
            str([r["tier"] for r in review][:6]))
    f.check("清单给出可执行动作", all(r["action"] for r in review))

    print("\n== P8 空目录要说清 ==")

    with tempfile.TemporaryDirectory() as tmp:
        empty = ioc_links.extract(tmp)
        f.check("没有 .ioc 时 status=not_ready", empty["status"] == "not_ready")
        f.check("没有 .ioc 时说明原因",
                "没有" in str(empty.get("reason")), str(empty.get("reason")))


def _two_board() -> DesignModel:
    b0 = Board(kind="custom", name="A", size_mm={"w": 40, "h": 40}, id="A")
    b1 = Board(kind="custom", name="B", size_mm={"w": 40, "h": 40}, id="B")
    design = DesignModel(id="t", title="t", board=b0, boards=[b0, b1])
    design.parts = [
        Part(ref="U1", kind="STM32F103C8T6", label="a", role="mcu",
             size_mm={"w": 7, "h": 7, "d": 1}, physical_source="datasheet",
             placement=Placement(x_mm=5, y_mm=5, board_id="A"), pins=["1"]),
        Part(ref="U2", kind="STM32F103C8T6", label="b", role="mcu",
             size_mm={"w": 7, "h": 7, "d": 1}, physical_source="datasheet",
             placement=Placement(x_mm=5, y_mm=5, board_id="B"), pins=["1"]),
    ]
    return design


# ---------------------------------------------------------------------------
# P7
# ---------------------------------------------------------------------------

def check_vector_table(f: Failures) -> None:
    print("\n== P7 向量表推理 ==")

    # 合成一个好的 .bin
    with tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp) / "good.bin"
        good.write_bytes(struct.pack("<II", 0x20005000, 0x080033F5)
                         + b"\x00" * 64)
        info = renode.vector_table(good)
        f.check("合法向量表 → ok=True", info["ok"] is True, info.get("reason"))
        f.check("加载地址推成 0x08000000",
                info["load_address"] == "0x08000000", info.get("load_address"))
        f.check("能反推 RAM 容量",
                info["ram_hint_kb"] == 20, str(info.get("ram_hint_kb")))

        # SP 不在 SRAM
        bad_sp = Path(tmp) / "bad_sp.bin"
        bad_sp.write_bytes(struct.pack("<II", 0x12345000, 0x08000101)
                           + b"\x00" * 64)
        info = renode.vector_table(bad_sp)
        f.check("SP 不在 SRAM → ok=False", info["ok"] is False)
        f.check("说明是哪一条不成立",
                "SP" in info["reason"], info["reason"][:80])

        # Reset 不是 Thumb
        bad_thumb = Path(tmp) / "bad_thumb.bin"
        bad_thumb.write_bytes(struct.pack("<II", 0x20005000, 0x08000100)
                              + b"\x00" * 64)
        info = renode.vector_table(bad_thumb)
        f.check("Reset 最低位不是 1 → ok=False", info["ok"] is False)
        f.check("说明 Thumb 那条不成立",
                "Thumb" in info["reason"], info["reason"][:80])

    # 真实固件
    if _have_workspace():
        gps = WORKSPACE / "F103C8T6-GPS.bin"
        if gps.is_file():
            info = renode.vector_table(gps)
            f.check("真实 F103 GPS 固件能定位",
                    info["ok"] is True, info.get("reason"))
            f.check("F103 的 RAM=20KB 与芯片规格一致",
                    info.get("ram_hint_kb") == 20,
                    str(info.get("ram_hint_kb")))
        wifi = WORKSPACE / "wifi.bin"
        if wifi.is_file():
            info = renode.vector_table(wifi)
            f.check("wifi.bin 被如实拒绝（不是所有 .bin 都是 Cortex-M）",
                    info["ok"] is False, "居然判成 ok=True")


def check_resc(f: Failures) -> None:
    print("\n== P7 .resc 生成 ==")

    design = _two_board()
    design.links_list = [{
        "id": "link:SPI1:A->B",
        "a": {"unit": "A", "peripheral": "SPI1"},
        "b": {"unit": "B", "peripheral": "SPI1"},
        "protocol": "spi", "source": "declared",
    }]
    report = renode.build_resc(design)
    script = report["script"]

    f.check("两台机台都生成了", len(report["machines"]) == 2,
            str(len(report["machines"])))
    f.check("脚本里有 mach create",
            script.count("mach create") == 2, str(script.count("mach create")))
    f.check("脚本里有 LoadPlatformDescription",
            "LoadPlatformDescription" in script)
    f.check("F103 映射到 stm32f103 平台",
            report["platforms"]["A"] == "stm32f103",
            str(report["platforms"]))

    f.check("声明链路生成 connector Connect",
            len(report["connectors"]) == 1, str(report["connectors"]))
    f.check("connector 用 Renode 原生命令格式",
            report["connectors"] and
            report["connectors"][0].startswith("connector Connect "),
            str(report["connectors"]))

    # 顺序：connector 必须在 start 之前
    idx_conn = script.find("connector Connect")
    idx_start = script.find("\nstart")
    f.check("connector Connect 出现在 start 之前",
            idx_conn > 0 and idx_start > idx_conn,
            f"conn={idx_conn} start={idx_start}")

    print("\n== P7 纪律：inferred 链路不接线 ==")

    design2 = _two_board()
    design2.links_list = [{
        "id": "link:SPI1:A->B",
        "a": {"unit": "A", "peripheral": "SPI1"},
        "b": {"unit": "B", "peripheral": "SPI1"},
        "protocol": "spi", "source": "inferred",
    }]
    report2 = renode.build_resc(design2)
    f.check("inferred 链路不生成 connector",
            len(report2["connectors"]) == 0, str(report2["connectors"]))
    f.check("inferred 链路进 warnings 说明原因",
            any("inferred" in w for w in report2["warnings"]),
            str(report2["warnings"]))

    print("\n== P7 诚实性：不能跑就说不能跑 ==")

    probe = renode.probe(check_cpu=False)
    f.check("能探测到 Renode 可执行文件",
            probe.renode.available is True, probe.renode.detail[:80])
    f.check("报告里带版本号",
            bool(probe.renode.version), probe.renode.version[:60])

    full = renode.report(design)
    f.check("report 有 runnable_here 字段",
            "runnable_here" in full, str(sorted(full)))
    f.check("本机 runnable_here=False（沙箱拦截，如实报）",
            full["runnable_here"] is False, str(full["runnable_here"]))
    f.check("不能跑时给出外部执行指引",
            any("DSH 外" in n for n in full["notes"]),
            str(full["notes"][-1:]))
    f.check("生成了 run.cmd（外部可执行）",
            "run.cmd" in (full.get("runner") or {}),
            str(list(full.get("runner") or {})))
    f.check("run.cmd 预建 Renode 配置目录（它只会写不会建）",
            "mkdir" in (full.get("runner") or {}).get("run.cmd", ""),
            "缺少 mkdir")


def check_real_project(f: Failures) -> None:
    print("\n== P7 在真实多板工程上 ==")

    if not _have_workspace():
        print("  skip")
        return
    from dsh_embagent.design.store import DesignStore, list_projects

    want = project_id()
    if not want or not any(p.get("id") == want and p.get("has_design")
                           for p in list_projects()):
        print("  skip 没有指定真实工程（DSH_EMBAGENT_PROBE_PROJECT_NAME）"
              " —— 这一节没跑，不是通过")
        return

    design = DesignStore(want).load()
    report = renode.build_resc(design, ioc_root=WORKSPACE)
    f.check("8 块板都建了机台",
            len(report["machines"]) == 8, str(len(report["machines"])))
    f.check("哈希 kind 判不出型号时如实进 warnings",
            bool(report["warnings"]),
            f"{len(report['warnings'])} 条")

    options = renode.ioc_mcu_options(WORKSPACE)
    f.check(".ioc 提供 7 个型号选项供显式映射",
            len(options) == 7, str(len(options)))
    f.check("每个选项都能映射到 Renode 平台",
            all(o["platform"] for o in options),
            str([o for o in options if not o["platform"]]))

    # 显式映射后应该全部成功
    explicit = {}
    for board in design.board_list():
        explicit[str(board.id or "")] = "STM32F103C8T6"
    report = renode.build_resc(design, explicit_mcu=explicit)
    f.check("显式映射后 8 块板都拿到平台",
            all(report["platforms"].values()),
            str([k for k, v in report["platforms"].items() if not v]))
    f.check("显式映射后没有 warnings",
            not report["warnings"], str(report["warnings"][:2]))


# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    f = Failures()

    check_ioc(f)
    check_vector_table(f)
    check_resc(f)
    check_real_project(f)

    print()
    if f.items:
        print(f"FAILED {len(f.items)} 项：")
        for name in f.items:
            print(f"  - {name}")
        return 1
    print("M3 验收通过（P7 产物正确性 + P8 抽取与纪律）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
