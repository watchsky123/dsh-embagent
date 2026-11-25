#!/usr/bin/env python3
"""死样式规则扫描：styles.ts 里定义的每个 .hwv-* 类必须被某个组件用到。

    python dsh_embagent/scripts/check_dead_css.py

为什么值得单独一个脚本：CSS 是字符串，typecheck 看不见它，tsdown 也不管。
"改了行为忘了删规则"（例如 .hwv-pane.hidden：布局从 display:none 改成条件
挂载之后它就永远不会再被加上）在这套单文件注入样式里会**无声累积**。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CLIENT = Path(__file__).resolve().parent.parent / "src" / "client"
STYLES = CLIENT / "styles.ts"

# 选择器里的基类名（不含修饰类）：`.hwv-btn.primary` → hwv-btn 与 primary
SELECTOR = re.compile(r"\.(hwv-[a-z0-9-]+)")
USAGE = re.compile(r"(hwv-[a-z0-9-]+)")
# 复合选择器的**修饰类**（`.hwv-tab.active` 里的 active）：它们不带 hwv-
# 前缀，只查基类会漏掉一整类死规则（例如改成条件挂载后没人再用的
# `.hwv-pane.hidden`）。按词边界在组件源码里找这个名字。
# 修饰类允许下划线（`.hwv-ladder-cell.not_run`）：只写 `[a-z0-9-]` 会把
# `not_run` 截成 `not`，报出一条不存在的"死规则 .not"（实测）。
MODIFIER = re.compile(r"\.(hwv-[a-z0-9-]+)((?:\.[a-z][a-z0-9_-]*)+)")


def _word_present(text: str, word: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])", text) is not None


# 组件里出现的 `--hwv-*` 是 CSS 自定义属性（var()/setProperty），**不是类名**。
# 不先剥掉就会把 `var(--hwv-err)` 误报成"类 hwv-err 没有样式"。
VARIABLE = re.compile(r"--hwv-[a-z0-9-]+")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # 样式已拆到 `src/client/styles/*.ts`（P0）：只读 styles.ts 会把全部规则
    # 判成"缺失"。这里把所有分片拼起来，等价于最终注入的那份 CSS。
    parts = [STYLES] + sorted((CLIENT / "styles").glob("*.ts"))
    css = "\n".join(f.read_text(encoding="utf-8") for f in parts)
    # 去掉注释块，否则注释里提到的类名会被当成定义
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    defined = set(SELECTOR.findall(body))

    sources = {}
    for file in sorted(CLIENT.glob("*.tsx")) + sorted(CLIENT.glob("*.ts")):
        if file.name == "styles.ts":
            continue
        sources[file.name] = file.read_text(encoding="utf-8", errors="replace")

    used: set[str] = set()
    for text in sources.values():
        used.update(USAGE.findall(VARIABLE.sub("", text)))

    dead = sorted(defined - used)

    # 修饰类：`.hwv-tab.active` 的 active 必须在任一组件源码里作为词出现。
    joined = VARIABLE.sub("", "".join(sources.values()))
    dead_modifiers: list[str] = []
    for base, tail in MODIFIER.findall(body):
        for modifier in tail.lstrip(".").split("."):
            if not _word_present(joined, modifier):
                dead_modifiers.append(f".{base}.{modifier}")

    missing = sorted(n for n in used if n not in defined)

    print(f"定义 {len(defined)} 基类 / {len(MODIFIER.findall(body))} 条复合，"
          f"组件引用 {len(used)} 类")
    for name in dead:
        print(f"  死规则（基类无人用）：.{name}")
    for name in sorted(set(dead_modifiers)):
        print(f"  死规则（修饰类无人用）：{name}")
    for name in missing:
        print(f"  样式缺失：.{name}（组件在用，styles.ts 没定义）")

    if dead or dead_modifiers or missing:
        print("\n❌ 样式与组件不同步")
        return 1
    print("\n✅ 样式与组件同步")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
