"""人工抽查归因结果 —— 打印指定配件（或全部）的理由，供逐条核对。

从仓库根运行：

    python -m hw_velxio.scripts.probe_justify 示例-单板
    python -m hw_velxio.scripts.probe_justify 示例-单板 R1 R96 C66

**这个脚本不判定对错**，它只是把理由摊开给人看。归因的部分正确性
机器验不了：`1kΩ 上拉到 3.3V` 这句话对不对，取决于那是不是真的
I2C 上拉——那是 datasheet 层面的事实，网表里没有。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hw_velxio.design import justify                     # noqa: E402
from hw_velxio.design.store import DesignStore           # noqa: E402


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not argv:
        print("用法：probe_justify <工程> [ref ...]")
        return 2
    store = DesignStore(argv[0])
    design = store.load()
    report = justify.run(design)
    wanted = [r.upper() for r in argv[1:]]
    rows = [p for p in report["parts"]
            if not wanted or p["ref"].upper() in wanted]

    print(f"== {argv[0]}：{report['known']}/{report['total']} "
          f"（{report['ratio'] * 100:.1f}%） ==")
    print(f"   问题最多的一类（元件数）：{report['by_kind']}")
    print()
    for row in rows:
        j = row["justification"]
        mark = "?" if j["confidence"] == "unknown" else "*"
        group = row["group"]
        print(f"{mark} {row['ref']:<9} [{row['category']:<10}] "
              f"{j['confidence']:<20} {group['kind']}/{group['target']}")
        print(f"    理由：{j['why']}")
        print(f"    删掉：{j['effect_if_removed']}")
        if j.get("evidence"):
            print(f"    依据：{j['evidence']}")
        print()

    if not wanted:
        print("== 分组（无源件读这一层） ==")
        for g in report["groups"]:
            print(f"  {g['count']:>3} × {g['label']:<14} {g['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
