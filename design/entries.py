"""工作区**条目**识别 —— 把"一个真实项目"从文件堆里认出来。

## 为什么需要这一层

上一版只把"含 `design.json` 的子目录"当工程，于是这种工作区：

    F103-GPS-master/     29 个源文件 + .ioc(STM32F103C8Tx)
    H743PilotX-master/   85 个源文件 + .ioc(STM32H743IITx)
    BOM_….xlsx           30 行真实元件
    *.bin                9 个固件

在评审里变成了「一个空的 Arduino Uno」——**388 个源文件、7 个 MCU 型号、
9 个固件一个字都没进 prompt**。问题不是信息少，是识别层错了。

所以分三层，每层判据不同：

| 层 | 判据 | 含义 |
|---|---|---|
| 工作区条目 | 有 `.ioc` / `.kicad_pcb` / `BOM*.xlsx` / 固件 / `.param` | **一个真实项目**（可能还没接进工作台） |
| 工程 | 有 `design.json` | 已接进工作台、可 2D/3D 可视化 |
| 适配缺口 | 是条目但不是工程 | `not_adopted`：有东西，但还没接入 |

## 只提取确定性事实

`.ioc` 是 CubeMX 的 `key=value` 配置，`Mcu.UserName` 就是芯片型号——这是
**硬事实**，比 `board.kind` 可靠。同理 `RCC.APB1Freq_Value` 是时钟、
`P<port>.Signal` 是引脚复用。这些都不需要 AI 猜。

@module dsh_embagent.design.entries
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# 识别一个"项目目录"的标志物。顺序 = 优先级（.ioc 最强：它直接说芯片型号）。
MARKERS: tuple[tuple[str, str], ...] = (
    ("*.ioc", "cubemx"),
    ("*.kicad_pcb", "kicad"),
    ("*.kicad_pro", "kicad"),
    ("*.sch", "eeschema"),
    ("*.brd", "eagle"),
    ("platformio.ini", "platformio"),
    ("Makefile", "make"),
    ("CMakeLists.txt", "cmake"),
)

# 工作区根上的"散件"：它们本身就是一个条目（BOM / 固件 / 配置）。
LOOSE_KINDS: tuple[tuple[str, str], ...] = (
    ("BOM*.xlsx", "bom"),
    ("BOM*.csv", "bom"),
    ("*.epro2", "easyeda-pro"),   # 立创 EDA 专业版工程（zip：project2.json + .epru）
    ("*.epro", "easyeda"),        # 立创 EDA 标准版
    ("*.param", "fc-params"),
    ("*.bin", "firmware"),
    ("*.hex", "firmware"),
)

# 这些目录名不是项目，是第三方/构建残留。
#
# **注意 `Core` / `App` / `Src` / `Inc` 不在这个名单里**：它们正是 CubeMX 与
# STM32 工程的标准源码目录。第一版把它们当成"第三方残留"跳过了，结果
# `F103-GPS-master` 的 29 个源文件被数成 1 个 —— 排除规则把真正的信息排掉了。
NOT_A_PROJECT = {
    "Drivers", "Middlewares", "build", "dist",
    "obj", "Debug", "Release", "node_modules", ".git", "__pycache__",
    "CMSIS", "HAL_Driver", "Startup",
}

# 单个项目里最多读多少个源文件来数规模（不是全读，是数）。
MAX_SOURCE_SCAN = 4000

# `.ioc` 里我们真正关心的键（其余 200 多行是界面状态，不进事实）。
IOC_KEYS = {
    "Mcu.Family": "mcu_family",
    "Mcu.Name": "mcu_name",
    "Mcu.UserName": "mcu",
    "ProjectManager.DeviceId": "device_id",
    "RCC.AHBFreq_Value": "ahb_hz",
    "RCC.APB1Freq_Value": "apb1_hz",
    "RCC.APB2Freq_Value": "apb2_hz",
}


def read_ioc(path: Path) -> dict[str, Any]:
    """读一个 CubeMX `.ioc`，返回确定性事实。

    引脚用 `PA10.Signal=USART1_RX` 这种形式记录，这里按外设归类——但**只列
    事实**（哪个外设出现在哪些引脚），不推断"这是什么模块"。
    """
    facts: dict[str, Any] = {}
    pins: dict[str, list[str]] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": f"读不出：{exc}"}

    for raw in text.splitlines():
        line = raw.strip()
        if line == "" or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key in IOC_KEYS and value != "":
            facts[IOC_KEYS[key]] = value
        # 引脚：`PA10.Signal=USART1_RX` / `PA10.Mode=Asynchronous`
        head, _, tail = key.partition(".")
        if len(head) >= 3 and head[0] in "P" and head[1] in "ABCDEFGH" \
                and head[2].isdigit() and tail == "Signal" and value != "":
            peripheral = value.split("_")[0]
            pins.setdefault(peripheral, []).append(head)
    if pins:
        facts["peripherals"] = {name: sorted(set(refs))
                                for name, refs in sorted(pins.items())}
        facts["pin_count"] = sum(len(set(refs)) for refs in pins.values())
    return facts


def read_params(path: Path) -> dict[str, Any]:
    """读飞控参数文件（`key=value` 每行一个）。只统计，不解释。"""
    facts: dict[str, Any] = {"count": 0, "prefixes": {}}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": f"读不出：{exc}"}
    prefixes: dict[str, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line == "" or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key == "":
            continue
        facts["count"] += 1
        prefix = key.split("_")[0]
        prefixes[prefix] = prefixes.get(prefix, 0) + 1
    facts["prefixes"] = dict(sorted(prefixes.items(), key=lambda kv: -kv[1])[:10])
    return facts


def read_bom_count(path: Path) -> dict[str, Any]:
    """数 BOM 有多少行元件（xlsx 用 openpyxl，csv 用标准库）。

    只数**行数**，不解析价值/封装——那要 importers 的完整流程，勘察阶段
    一次扫几十个文件时太重，而且错了会污染初审。
    """
    facts: dict[str, Any] = {"rows": 0}
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            import csv as csv_module
            with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                facts["rows"] = max(0, sum(1 for _ in csv_module.reader(handle)) - 1)
        elif suffix in (".xlsx", ".xls"):
            import openpyxl  # 可选依赖：没装就如实说数不出来
            book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            sheet = book.worksheets[0]
            rows = 0
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index == 0:
                    continue
                if any(cell not in (None, "") for cell in row):
                    rows += 1
            book.close()
            facts["rows"] = rows
    except ImportError:
        facts["error"] = "openpyxl 未安装，数不出 xlsx 行数"
    except Exception as exc:  # noqa: BLE001
        facts["error"] = f"{type(exc).__name__}: {exc}"
    return facts


def _count_sources(directory: Path) -> dict[str, int]:
    """数一个项目里的源码规模（按扩展名）。不读内容，只数。"""
    counts: dict[str, int] = {}
    total = 0
    stack = [directory]
    while stack and total < MAX_SOURCE_SCAN:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if total >= MAX_SOURCE_SCAN:
                break
            try:
                if child.is_dir():
                    if child.name in NOT_A_PROJECT or child.name.startswith("."):
                        continue
                    stack.append(child)
                elif child.is_file():
                    ext = child.suffix.lower()
                    if ext in (".c", ".h", ".cpp", ".hpp", ".s", ".py", ".ino",
                               ".qml", ".ld"):
                        counts[ext] = counts.get(ext, 0) + 1
                        total += 1
            except OSError:
                continue
    return counts


def scan(base: Path) -> dict[str, Any]:
    """识别工作区里的全部**条目**。

    返回 ``{"entries": [...], "by_kind": {...}, "unread": [...]}``。

    `unread` 是**如实清单**：哪些文件没有被任何识别规则覆盖。这是
    "哪些看了、哪些没看"的机器答案——不能只报看过的。
    """
    entries: list[dict[str, Any]] = []
    read_paths: set[str] = set()

    if not base.is_dir():
        return {"entries": [], "by_kind": {}, "unread": [],
                "error": f"工作区不存在：{base}"}

    # -- 1. 目录型条目（一个目录 = 一个项目）--------------------------------
    try:
        children = sorted(base.iterdir())
    except OSError as exc:
        return {"entries": [], "by_kind": {}, "unread": [],
                "error": f"列不出目录：{exc}"}

    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in NOT_A_PROJECT:
            continue
        kind = ""
        evidence: list[str] = []
        for pattern, marker_kind in MARKERS:
            matches = sorted(child.glob(pattern))
            if matches:
                kind = marker_kind
                evidence = [m.name for m in matches[:4]]
                break
        # **工作台自己的工程也是条目**：它有 design.json，是"已接入"的条目。
        # 不把这条加进来，entries 只统计"外部项目"，工作台工程就掉出了
        # 条目总数——那是另一种"看不见"。
        if (child / "design.json").is_file():
            kind = kind or "bench"
            evidence = evidence or ["design.json"]
        if kind == "":
            continue

        entry: dict[str, Any] = {
            "id": child.name,
            "kind": kind,
            "path": str(child),
            "evidence": evidence,
        }
        # .ioc 里的芯片型号是硬事实，优先读它。
        ioc_files = sorted(child.glob("*.ioc"))
        if ioc_files:
            facts = read_ioc(ioc_files[0])
            entry["mcu"] = facts.get("mcu") or facts.get("mcu_name") or ""
            entry["device"] = facts.get("device_id", "")
            entry["clock_hz"] = facts.get("ahb_hz", "")
            entry["peripherals"] = list((facts.get("peripherals") or {}).keys())[:12]
            entry["pin_count"] = facts.get("pin_count", 0)
            entry["read"] = [str(ioc_files[0].relative_to(base).as_posix())]
            read_paths.add(str(ioc_files[0].resolve()))
        sources = _count_sources(child)
        entry["sources"] = sources
        entry["source_total"] = sum(sources.values())
        # 有 design.json 的才是"已接入工作台"的工程。
        entry["adopted"] = (child / "design.json").is_file()
        # 阶段：`not_adopted` 与 `empty` 必须分开——前者有内容没接入，
        # 后者是真的空。混成一个词会让 423 个源文件显示成"什么都没有"。
        from . import phase as phase_module
        stage = phase_module.evaluate_entry(entry)
        entry["phase"] = stage["phase"]
        entry["phase_reason"] = stage["reason"]
        entry["veto"] = stage["veto"]
        # 也报告"未被任何识别规则读过"的源文件数，作为盲区的量化。
        entry["unread_sources"] = entry["source_total"]
        entries.append(entry)

    # -- 2. 根上的散件（BOM / 固件 / 参数）----------------------------------
    try:
        root_files = sorted(base.iterdir())
    except OSError:
        root_files = []

    loose: list[dict[str, Any]] = []
    for child in root_files:
        if not child.is_file():
            continue
        matched = ""
        for pattern, kind in LOOSE_KINDS:
            if child.match(pattern):
                matched = kind
                break
        if matched == "":
            continue
        entry = {
            "id": child.name,
            "kind": matched,
            "path": str(child),
            "size": child.stat().st_size if child.exists() else 0,
            "adopted": False,
        }
        if matched == "bom":
            entry.update(read_bom_count(child))
        elif matched == "fc-params":
            entry.update(read_params(child))
        elif matched == "easyeda-pro":
            # 立创 EDA 工程：这里能拿到**真实网表**（元件/网络/引脚），
            # 是 2D 电气权威的唯一来源。其余材料（.ioc/BOM/固件）都给不出。
            from . import easyeda as easyeda_module
            facts = easyeda_module.summary(child)
            if facts.get("status") == "ok":
                counts = facts.get("counts") or {}
                entry["components"] = counts.get("components", 0)
                entry["nets_count"] = counts.get("nets", 0)
                # 新版解析器给的是 pads（焊盘连接数），不是 pins。
                entry["pins_count"] = counts.get("pads", 0)
                entry["with_coords"] = counts.get("with_coords", 0)
                entry["with_nets"] = counts.get("with_nets", 0)
                entry["named"] = counts.get("named", 0)
                entry["sized"] = counts.get("sized", 0)
                entry["valued"] = counts.get("valued", 0)
                entry["with_3d"] = counts.get("with_3d", 0)
                total = counts.get("components", 0)
                entry["designated_ratio"] = (
                    round(counts.get("named", 0) / total, 4) if total else 0.0)
                entry["nets"] = dict(list((facts.get("nets") or {}).items())[:12])
                entry["title"] = facts.get("title", "")
                entry["easyeda_note"] = facts.get("note", "")
            else:
                entry["easyeda_error"] = "；".join(facts.get("findings") or [])
        # 散件也走同一条阶段推导：它们同样是"有内容但没接入"。
        # 不推导的话，表格里的阶段列就是空的——空白比"未知"更容易被误读。
        from . import phase as phase_module
        stage = phase_module.evaluate_entry(entry)
        entry["phase"] = stage["phase"]
        entry["phase_reason"] = stage["reason"]
        entry["veto"] = stage["veto"]
        loose.append(entry)
        read_paths.add(str(child.resolve()))

    entries.extend(loose)

    by_kind: dict[str, int] = {}
    for entry in entries:
        by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1

    return {
        "entries": entries,
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "read_paths": sorted(read_paths),
        "adopted": sum(1 for e in entries if e.get("adopted")),
        "not_adopted": sum(1 for e in entries if not e.get("adopted")),
    }
