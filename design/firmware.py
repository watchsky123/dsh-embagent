"""固件源码检查 —— **hw_velxio 自己的**：读代码，不生成代码。

取代 `hwforge.fwcheck`（1239 行）。

## 它补的是哪个洞

其他检查判的都是**我们建的模型**：体素场、走线场、网表。它们对
`App/Src/application.cpp`（别人写的 1703 行、用我们没有的工具链编译）
一句话都说不出来。

实测在无人机树上：9 块板、381 个用户源码文件、1442 个厂商文件。
工具一个都看不见。

## 这些检查是什么，不是什么

**是文本与结构检查，不是语义分析。** 没有 C 解析器、没有类型推导、
没有数据流。每条检查回答一个"细心的读者 grep 一下就能回答"的问题，
并给出**文件和行号**让他几秒钟内确认。

**是保守的。** 一个 finding 是**关于文本的事实**，就这么写：
"这个函数被定义了、但文本里没人调用" —— 不是"这是死代码"。
区别很重要：函数可能通过指针、中断向量、本模块不展开的宏被调用。
报告会说明这一点，每条检查的 `missing` 说清它看不见什么。

**只读。** 从不改写任何文件。

## 六条检查

| 检查 | 抓什么 | 无人机树上的实测 |
|---|---|---|
| `fw_uncalled` | 定义了但没人调 | `RadioUpdateEvent()` |
| `fw_timeout` | 失联处理路径 | `armed` 保持 true 10s |
| `fw_param` | 配置表 vs 实际读取 | 声明 207，读了 86 |
| `fw_bom` | BOM 值 vs 解码的型号 | R290 标 0Ω 实为 1002 |
| `fw_dup` | 近乎相同的副本 | 两块 TOF 板差 4 行 |
| `fw_marker` | 调试残留 / TODO | 飞控里 87 个 printf |

## 关于 `fw_uncalled` 的计数规则（踩过三个坑）

```
mentions = 文本里所有 `name(` 出现（含限定调用）
decls    = 以 `;` 结尾、不是定义的语句
defs     = 定义本身
calls    = mentions - decls - defs
```

`calls <= 0` 才报。**为什么用 mentions 而不是模式匹配调用**：C++ 成员
调用 `m.mat_mul_4x4(a,b)` 里含 `mat_mul_4x4(`，形状仍然匹配，这是对的。
函数指针赋值 `ev.onLinkDown = OnLinkDown` 不匹配，所以算作没人调 ——
**这是已知的假阳性类别**，所以 finding 说"文本里没人调"，不说"死代码"。

**为什么 decls 判定要两个条件**：第一版只看"以 `;` 结尾"，于是
`mat_mul_4x4(F, kf->P, F_P);` 这种**调用语句**被算成声明并减掉，
函数看起来就没人调 —— 计数膨胀到 507，把唯一真实的结果埋了。
所以还要判"从上一个语句边界到名字之间的前缀像不像类型表达式"。

@module hw_velxio.design.firmware
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


#: 不参与"未被调用"检查的文件（向量表里的名字是按字符串引用的）。
VECTOR_TABLE_FILES: frozenset[str] = frozenset({
    "startup_stm32h743xx.s", "startup_stm32f4xx.s", "startup.s",
    "vectors.c", "vector_table.c", "isr_vectors.c",
})

#: 入口点 —— 没人调是正常的。
ENTRY_POINTS: frozenset[str] = frozenset({"main", "SystemInit", "_start",
                                          "Reset_Handler", "setup", "loop"})

#: 视为"控制流关键字"而不是返回类型（用来排除 if/while/for）。
_CONTROL_KEYWORDS: frozenset[str] = frozenset({
    "if", "while", "for", "switch", "else", "do", "return", "case"})

#: 源码扩展名。
SOURCE_SUFFIXES: frozenset[str] = frozenset({".c", ".cc", ".cpp", ".cxx",
                                             ".h", ".hh", ".hpp", ".hxx"})

#: 厂商/第三方目录 —— 默认不扫（它们的"未调用"是正常的）。
VENDOR_HINTS: tuple[str, ...] = ("Drivers/", "Middlewares/", "CMSIS/",
                                 "vendor/", "third_party/", "HAL_Driver/",
                                 "STM32H7xx_HAL_Driver/")

#: 宏/函数定义的正则（保守：只认行首开始的、带返回类型的）。
_DEF_RE = re.compile(
    r"^[ \t]*(?:(?P<cls>[A-Za-z_]\w*)::)?"
    r"(?P<ret>[A-Za-z_][\w:<>,\*& \t]*?)[ \t]+"
    r"(?P<name>[A-Za-z_]\w*)[ \t]*\([^;{]*\)[ \t]*"
    r"(?:const[ \t]*)?(?:noexcept[ \t]*)?\{",
    re.MULTILINE)

#: 调试残留标记。
_MARKER_RE = re.compile(
    r"\b(TODO|FIXME|XXX|HACK|TEMP|DEBUG|printf|puts|HAL_UART_Transmit)\b")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    """一条 finding：**关于文本的事实**，带文件与行号。"""

    check: str
    file: str
    line: int
    symbol: str
    message: str
    #: 这条检查看不见什么 —— 必须写，否则会被当成结论。
    missing: str = ""
    #: `info` / `warning` / `error`
    severity: str = "warning"

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.check, "file": self.file, "line": self.line,
                "symbol": self.symbol, "message": self.message,
                "missing": self.missing, "severity": self.severity}

    def to_finding(self) -> dict[str, Any]:
        """转成与 `physical.py` 同格式（面板统一渲染）。"""
        return {
            "check": f"fw.{self.check}",
            "ok": False,
            "message": f"{self.file}:{self.line} {self.message}",
            "measured": {"file": self.file, "line": self.line},
            "limit": "见 missing",
            "refs": [self.symbol] if self.symbol else [],
            "severity": self.severity,
            "fix": {},
        }


@dataclass
class SourceTree:
    """源码树：读文件、按目录过滤、缓存内容。"""

    root: Path
    #: 是否包含厂商目录
    include_vendor: bool = False
    _cache: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def _skip(self, path: Path) -> bool:
        text = str(path).replace("\\", "/")
        if not self.include_vendor:
            for hint in VENDOR_HINTS:
                if hint in text:
                    return True
        return any(part.startswith(".") for part in path.parts)

    def iter_sources(self) -> Iterator[Path]:
        """用户源码文件（排除厂商目录）。"""
        if not self.root.is_dir():
            return
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            if self._skip(path):
                continue
            yield path

    def iter_all(self) -> Iterator[Path]:
        """**全部**源码（含厂商）—— 用于"谁调用了谁"的计数。"""
        if not self.root.is_dir():
            return
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            if any(part.startswith(".") for part in path.parts):
                continue
            yield path

    def read(self, path: Path) -> str:
        key = str(path)
        if key in self._cache:
            return self._cache[key]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        self._cache[key] = text
        return text

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def stats(self) -> dict[str, Any]:
        user = list(self.iter_sources())
        all_files = list(self.iter_all())
        return {"root": str(self.root), "user_files": len(user),
                "all_files": len(all_files),
                "vendor_files": len(all_files) - len(user)}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _strip_comment(text: str) -> str:
    """去掉 `//` 与 `/* */` 注释（够用即可，不做完整词法）。"""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", text)


def _looks_like_type_expression(prefix: str) -> bool:
    """`prefix` 像不像一个返回类型/限定符（而不是调用参数）。

    这是 `fw_uncalled` 计数规则的关键：`mat_mul_4x4(F, kf->P, F);` 里的
    前缀是 `(` 之后的内容（含逗号、`->`），不像类型表达式 —— 那是一条
    **调用语句**，不是声明。
    """
    text = prefix.strip()
    if text == "":
        return False
    if not re.match(r"^[A-Za-z_]", text):
        return False      # 以 `(`, `,`, `=`, `->` 开头 → 调用或赋值
    for bad in (",", "->", "=", "+", "-", "/", "*", "(", ")", "[", "]"):
        if bad in text:
            return False
    return True


# ---------------------------------------------------------------------------
# 六条检查
# ---------------------------------------------------------------------------

def scan_uncalled(tree: SourceTree) -> list[Finding]:
    """用户代码里定义了、但**文本里**没人调用的函数。

    计数规则见模块文档。已知假阳性：函数指针赋值、中断向量、
    宏展开产生的调用。
    """
    findings: list[Finding] = []
    defs: list[tuple[Path, str, int, str]] = []

    for path in tree.iter_sources():
        if path.name in VECTOR_TABLE_FILES:
            continue
        text = _strip_comment(tree.read(path))
        for match in _DEF_RE.finditer(text):
            name = match.group("name")
            ret = match.group("ret").strip()
            if name in ENTRY_POINTS:
                continue
            if ret in _CONTROL_KEYWORDS:
                continue
            cls = match.group("cls")
            symbol = f"{cls}::{name}" if cls else name
            line = text.count("\n", 0, match.start()) + 1
            defs.append((path, symbol, line, name))

    if not defs:
        return []

    wanted = {name for _f, _s, _l, name in defs}
    mentions: dict[str, int] = {n: 0 for n in wanted}
    decls: dict[str, int] = {n: 0 for n in wanted}

    for path in tree.iter_all():
        text = _strip_comment(tree.read(path))
        for match in re.finditer(r"([A-Za-z_]\w*)\s*\(", text):
            name = match.group(1)
            if name not in wanted:
                continue
            mentions[name] += 1
            # 判原型要**两个**条件都满足（见模块文档的踩坑记录）
            start = max(text.rfind(";", 0, match.start()),
                        text.rfind("}", 0, match.start()),
                        text.rfind("{", 0, match.start())) + 1
            prefix = text[start:match.start()]
            end_semi = text.find(";", match.start())
            end_brace = text.find("{", match.start())
            if not (end_semi != -1 and (end_brace == -1 or end_semi < end_brace)):
                continue      # 是定义，不是原型
            if _looks_like_type_expression(prefix):
                decls[name] += 1

    for path, symbol, line, name in defs:
        calls = mentions[name] - decls[name] - 1     # 减掉定义本身
        if calls > 0:
            continue
        findings.append(Finding(
            check="uncalled", file=tree.rel(path), line=line, symbol=symbol,
            message=(f"{symbol}() 定义了，但文本里没找到调用"
                     f"（mentions={mentions[name]} decls={decls[name]}）"),
            missing="通过函数指针表、中断向量、或宏展开的调用看不见",
        ))
    return findings


def scan_timeout_paths(tree: SourceTree) -> list[Finding]:
    """失联处理：有没有"断链后仍保持某状态"的路径。

    保守做法：找同时出现 `armed`/`alive` 类标志与超时判断的文件，
    报出**超时后没有清除标志**的位置。
    """
    findings: list[Finding] = []
    flag_re = re.compile(r"\b(armed|alive|linked|connected|online)\s*=\s*true",
                         re.IGNORECASE)
    clear_re = re.compile(r"\b(armed|alive|linked|connected|online)\s*=\s*false",
                          re.IGNORECASE)
    for path in tree.iter_sources():
        text = tree.read(path)
        if not flag_re.search(text):
            continue
        # 有置位、但全文没有对应的清除
        if clear_re.search(text):
            continue
        match = flag_re.search(text)
        assert match is not None
        line = text.count("\n", 0, match.start()) + 1
        findings.append(Finding(
            check="timeout", file=tree.rel(path), line=line,
            symbol=match.group(1),
            message=(f"{match.group(1)} 被置 true，但同文件里没有置 false 的地方"
                     f" —— 断链后状态不会被清除"),
            missing="跨文件的清除看不见；由别的模块负责清除时是假阳性",
        ))
    return findings


def scan_param_table(tree: SourceTree) -> list[Finding]:
    """配置表声明了多少项、实际被读了多少项。"""
    findings: list[Finding] = []
    for path in tree.iter_sources():
        text = tree.read(path)
        # 形如 `const param_t table[] = { {"name", ...}, ... };`
        match = re.search(
            r"(?:const\s+)?\w+\s+(\w+)\s*\[\s*\]\s*=\s*\{(.*?)\}\s*;",
            text, re.DOTALL)
        if match is None:
            continue
        table = match.group(1)
        body = match.group(2)
        if "{" not in body:
            continue
        declared = len(re.findall(r"\{[^{}]*\}", body))
        if declared < 5:
            continue      # 太小的表不像配置表
        # 实际读取：全文出现 `table[i].name` 或 `get_param("name")`
        names = re.findall(r'"([^"]{2,})"', body)
        read = sum(1 for name in names if text.count(f'"{name}"') > 1)
        if declared == 0 or read >= declared:
            continue
        line = text.count("\n", 0, match.start()) + 1
        findings.append(Finding(
            check="param", file=tree.rel(path), line=line, symbol=table,
            message=(f"配置表 {table} 声明 {declared} 项，"
                     f"只有 {read} 项在文本里被再次引用"),
            missing="通过下标/循环间接读取的看不见（可能全是假阳性）",
            severity="info",
        ))
    return findings


def scan_bom_consistency(rows: Sequence[dict[str, Any]]) -> list[Finding]:
    """BOM 的值 vs 型号里解码出的值。

    典型：R290 标 `0Ω`，型号是 `RS-05K1002FT` —— 1002 = 10kΩ。
    """
    findings: list[Finding] = []
    # 电阻型号里的 EIA 编码：4 位数字 = 前 3 位有效数字 + 10^末位
    #
    # **不能锚在结尾**：实测型号 `RS-05K1002FT` 的编码在中间，后面还有
    # 封装/公差后缀（`FT`）。早先写 `(\d{4})[A-Z]?$`，一个都没匹配上 ——
    # 检查看起来在跑，实际永远是 0 条。
    #
    # 现在：找**被非数字包围的四位数字**，且前一位是字母或分隔符
    # （避开 `05` 这种两位、`1002` 之外的纯数字段）。
    code_re = re.compile(r"(?<![0-9])([1-9]\d{3})(?![0-9])")
    for index, row in enumerate(rows):
        ref = str(row.get("ref") or row.get("designator") or f"row{index + 1}")
        value = str(row.get("value") or "").strip()
        mpn = str(row.get("mpn") or row.get("part") or "").strip()
        if value == "" or mpn == "":
            continue
        match = code_re.search(mpn)
        if match is None:
            continue
        digits = match.group(1)
        ohms = int(digits[:3]) * (10 ** int(digits[3]))
        # 把标称值解析成欧姆
        declared = _parse_ohms(value)
        if declared is None:
            continue
        # 允许 5% 误差
        if ohms == 0:
            continue
        # **0Ω 是一个真实的值**（跳线电阻），不是"没填"。拿它和 10kΩ 比
        # 必须报 —— 这正是实测里那个案例（R290 标 0Ω，型号 RS-05K1002FT，
        # 1002 解码是 10kΩ）。早先这里 `declared == 0` 直接跳过，
        # 把最该抓的一条放过去了。
        if declared == 0:
            findings.append(Finding(
                check="bom", file="(BOM)", line=index + 1, symbol=ref,
                message=(f"{ref} 标称 {value}（0Ω 跳线），但型号 {mpn} "
                         f"解码为 {ohms}Ω —— 型号说的不是跳线"),
                missing="型号编码规则因厂商而异；非 EIA 四位编码不判",
            ))
            continue
        ratio = max(declared, ohms) / min(declared, ohms)
        if ratio <= 1.05:
            continue
        findings.append(Finding(
            check="bom", file="(BOM)", line=index + 1, symbol=ref,
            message=(f"{ref} 标称 {value}（≈{declared:.0f}Ω），"
                     f"但型号 {mpn} 解码为 {ohms}Ω —— 差 {ratio:.0f} 倍"),
            missing="型号编码规则因厂商而异；非 EIA 四位编码不判",
        ))
    return findings


def _parse_ohms(text: str) -> float | None:
    """`10kΩ` / `4.7k` / `0R` / `1002` → 欧姆。"""
    cleaned = text.strip().upper().replace("Ω", "").replace("OHM", "")
    cleaned = cleaned.rstrip("R").strip()
    if cleaned in ("", "0", "0R", "0Ω"):
        return 0.0
    match = re.match(r"^([0-9.]+)\s*([KMG]?)$", cleaned)
    if match is None:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    unit = match.group(2)
    scale = {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}[unit]
    return number * scale


def scan_duplicates(paths: Iterable[Path], *, min_lines: int = 40,
                    similarity: float = 0.9) -> list[Finding]:
    """近乎相同的源码副本。

    用**行集合的 Jaccard 相似度**，不用 diff —— 代码挪了位置也算相同。
    """
    findings: list[Finding] = []
    items: list[tuple[Path, set[str], int]] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        trimmed = {line.strip() for line in lines if line.strip()}
        if len(lines) < min_lines or not trimmed:
            continue
        items.append((path, trimmed, len(lines)))

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a_path, a_lines, a_len = items[i]
            b_path, b_lines, b_len = items[j]
            union = a_lines | b_lines
            if not union:
                continue
            score = len(a_lines & b_lines) / len(union)
            if score < similarity:
                continue
            findings.append(Finding(
                check="dup", file=str(a_path).replace("\\", "/"), line=1,
                symbol=b_path.name,
                message=(f"{a_path.name}（{a_len} 行）与 {b_path.name}"
                         f"（{b_len} 行）有 {score*100:.0f}% 的行相同"),
                missing="重构过的等价代码不算重复",
                severity="info",
            ))
    return findings


def scan_markers(tree: SourceTree) -> list[Finding]:
    """调试残留与 TODO 标记。"""
    findings: list[Finding] = []
    counts: dict[str, dict[str, Any]] = {}
    for path in tree.iter_sources():
        text = tree.read(path)
        for match in _MARKER_RE.finditer(text):
            marker = match.group(1)
            entry = counts.setdefault(marker, {"count": 0, "files": set(),
                                               "first_line": 0,
                                               "first_file": ""})
            entry["count"] += 1
            entry["files"].add(tree.rel(path))
            if entry["first_line"] == 0:
                entry["first_line"] = text.count("\n", 0, match.start()) + 1
                entry["first_file"] = tree.rel(path)
    for marker, entry in sorted(counts.items()):
        severity = "info" if marker in ("TODO", "FIXME", "XXX", "HACK") \
            else "warning"
        findings.append(Finding(
            check="marker", file=entry["first_file"],
            line=entry["first_line"], symbol=marker,
            message=(f"{marker}：{entry['count']} 处，"
                     f"分布在 {len(entry['files'])} 个文件"),
            missing="注释里的标记也被计入",
            severity=severity,
        ))
    return findings


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

@dataclass
class FirmwareReport:
    """一次固件源码检查的结果。"""

    findings: list[Finding] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    #: 跑了哪几条检查（`--scope` 可以只跑一部分）
    ran: list[str] = field(default_factory=list)
    not_verified: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """**没有 error 级 finding 才算通过**（info/warning 不算失败）。"""
        return not [f for f in self.findings if f.severity == "error"]

    def by_check(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for finding in self.findings:
            out[finding.check] = out.get(finding.check, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok,
                "stats": dict(self.stats),
                "ran": list(self.ran),
                "counts": self.by_check(),
                "findings": [f.to_dict() for f in self.findings],
                "not_verified": list(self.not_verified),
                "note": "文本与结构检查，不是语义分析；每条 finding 都写明看不见什么"}


ALL_CHECKS: tuple[str, ...] = ("uncalled", "timeout", "param", "bom",
                               "dup", "marker")


def run(root: str | Path, *, scope: Sequence[str] | None = None,
        include_vendor: bool = False,
        bom_rows: Sequence[dict[str, Any]] | None = None) -> FirmwareReport:
    """对一个源码目录跑检查。

    `scope`：只跑这几条（默认全跑）。`bom_rows` 给了才跑 `bom`。
    """
    report = FirmwareReport()
    tree = SourceTree(Path(root), include_vendor=include_vendor)
    if not tree.root.is_dir():
        report.not_verified.append(f"源码目录不存在：{root}")
        return report

    report.stats = tree.stats()
    if report.stats.get("user_files", 0) == 0:
        report.not_verified.append(
            f"{root} 下没有找到用户源码（.c/.cpp/.h）—— "
            f"不是「没有发现」，是「没读到东西」")
        return report

    wanted = tuple(scope) if scope else ALL_CHECKS
    if "uncalled" in wanted:
        report.findings.extend(scan_uncalled(tree))
        report.ran.append("uncalled")
    if "timeout" in wanted:
        report.findings.extend(scan_timeout_paths(tree))
        report.ran.append("timeout")
    if "param" in wanted:
        report.findings.extend(scan_param_table(tree))
        report.ran.append("param")
    if "bom" in wanted:
        if bom_rows:
            report.findings.extend(scan_bom_consistency(bom_rows))
            report.ran.append("bom")
        else:
            report.not_verified.append("没有 BOM 数据，fw_bom 未跑")
    if "dup" in wanted:
        report.findings.extend(scan_duplicates(tree.iter_sources()))
        report.ran.append("dup")
    if "marker" in wanted:
        report.findings.extend(scan_markers(tree))
        report.ran.append("marker")
    return report


def describe() -> dict[str, Any]:
    return {"module": "hw_velxio.design.firmware",
            "checks": list(ALL_CHECKS),
            "note": "文本/结构检查，只读，每条都写明看不见什么"}
