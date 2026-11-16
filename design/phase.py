"""项目所处阶段（phase）的**确定性**推导。

为什么要有这个模块：上一轮的 `示例-无人机` 里 `parts: []`，9 条物理检查
**全部 ok**——因为空集里没有东西可以违反。界面显示绿灯，用户以为板子没问题。
"空/半成品/成品"如果只是 AI 说的一句话，它无法驱动任何行为（不能着色、
不能挡导出、不能进 `checks.json`）。所以这里把它做成一个**纯函数**：

    parts == 0                                   → empty
    有元件，但存在尺寸缺失/估算、或未闭合决策     → partial
    无以上问题                                   → complete

它只看设计事实，不看 AI 判断。`veto` 里逐条写出**为什么**不是更高阶段，
让面板能直接显示"差什么"，而不是给一个形容词。

对应地，`vacuous` 列出"因为无对象而通过"的检查——那是**假绿灯**的来源，
必须与"真的通过了"分开呈现。

@module hw_velxio.design.phase
"""

from __future__ import annotations

from typing import Any

from .model import DesignModel

PHASES = ("empty", "partial", "complete", "broken", "not_adopted")

# `not_adopted` **不是**工程阶段，而是工作区条目的阶段：有真实项目（源码/
# 固件/BOM），但还没有 design.json，所以进不了 2D/3D。
#
# 为什么必须和 `empty` 分开：上一版把"还没接进工作台"和"什么都没有"混成
# 一个 `empty`，于是 7 个 CubeMX 工程（423 个源文件）在评审里显示成
# "没有任何元件" —— 字面为真（design.json 里确实 0 件），但意思完全错了。
NOT_ADOPTED = "not_adopted"

# 检查里只要**没有可检查的对象**就会自动 ok 的 ID。它们必须在有对象时才可信。
OBJECT_DEPENDENT_CHECKS = (
    "space.overlap",
    "space.keepout",
    "space.bounds",
    "space.height",
    "space.net_reach",
    "space.connector_edge",
    "mech.mount_holes",
    "consistency.legacy_px",
    "consistency.provenance",
    "statics.report",
)


def evaluate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """给**工作区条目**（不是工作台工程）推导阶段。

    条目能提供的事实比工程少（没有 design.json），但足以回答一个关键问题：
    这东西是"有真实内容但没接入"，还是"什么都没有"。

    - 已接入（有 design.json）→ 交给 `evaluate()` 按元件判断
    - 未接入但有内容（源码/BOM/固件/参数）→ `not_adopted`，并如实说明
      缺的是"接入"而不是"内容"
    """
    if entry.get("adopted"):
        return {"phase": "adopted", "reason": "已是工作台工程", "veto": []}

    evidence: list[str] = []
    if entry.get("source_total"):
        evidence.append(f"{entry['source_total']} 个源文件")
    if entry.get("mcu"):
        evidence.append(f"MCU {entry['mcu']}")
    kind = str(entry.get("kind", ""))
    if kind == "bom":
        evidence.append(f"BOM {entry.get('rows', 0)} 行")
    elif kind == "fc-params":
        evidence.append(f"{entry.get('count', 0)} 个飞控参数")
    elif kind == "firmware":
        evidence.append(f"固件 {int(entry.get('size', 0)) / 1024:.0f} KB")
    elif kind == "easyeda-pro" and entry.get("components"):
        # 立创 EDA 工程：**有网表**，这是唯一能支撑 2D 电气仿真的材料。
        evidence.append(
            f"{entry.get('components')} 元件 / {entry.get('nets_count')} 网络"
            f" / {entry.get('pins_count')} 引脚")

    if not evidence:
        return {
            "phase": "not_adopted",
            "reason": "识别为项目目录，但没有可提取的内容特征",
            "veto": ["没有源码 / BOM / 固件 / 参数可读取"],
        }

    veto = ["缺 design.json（未接入工作台）",
            f"已有内容：{'，'.join(evidence)}"]
    if kind == "easyeda-pro" and entry.get("components"):
        veto.append(
            "**这份工程带网表**：可以用 hw_import 接入（.epro2 → 元件 + 网络），"
            "是目前唯一能支撑 2D 电气仿真的材料")
    return {"phase": "not_adopted",
            "reason": f"有真实内容（{'，'.join(evidence)}），但缺 design.json，"
                      f"还进不了 2D/3D 可视化",
            "veto": veto}


def evaluate(design: DesignModel,
             open_irreversible: list[str] | None = None) -> dict[str, Any]:
    """推导阶段。返回 ``{"phase", "reason", "veto", "counts"}``。

    `open_irreversible` 由调用方从**决策账本**传入（账本是独立文件，
    不在 design.json 里）。不传则视为"没读到账本"，不因此降级——宁可按
    已知事实判断，也不给一个凭空悲观的结果。

    **不抛异常**：阶段是用来显示的，缺字段就按"最保守"处理，不能因为推导
    失败让整个面板读不出工程。
    """
    parts = list(design.parts or [])
    nets = list(design.nets or [])
    veto: list[str] = []

    unsized: list[str] = []
    estimated: list[str] = []
    zero_height: list[str] = []
    for part in parts:
        size = part.size_mm or {}
        width = float(size.get("w", 0) or 0)
        height = float(size.get("h", 0) or 0)
        depth = float(size.get("d", 0) or 0)
        if width <= 0 or height <= 0:
            unsized.append(part.ref)
        elif depth <= 0:
            # 有平面尺寸但没有高度：3D 按薄片显示，仍是半成品
            zero_height.append(part.ref)
        if part.physical_source == "estimated":
            estimated.append(part.ref)

    if not parts:
        veto.append("没有一个元件（parts 为空）")

    if unsized:
        veto.append(f"{len(unsized)} 个元件缺平面尺寸：{', '.join(unsized[:8])}"
                    + ("…" if len(unsized) > 8 else ""))
    if zero_height:
        veto.append(f"{len(zero_height)} 个元件高度未知（d=0，3D 按薄片显示）："
                    + ", ".join(zero_height[:8]) + ("…" if len(zero_height) > 8 else ""))
    if estimated:
        veto.append(f"{len(estimated)} 个元件尺寸来自估算："
                    + ", ".join(estimated[:8]) + ("…" if len(estimated) > 8 else ""))

    if not parts:
        return {
            "phase": "empty",
            "reason": "没有任何元件：所有物理检查都会因空集而通过（假绿灯）",
            "veto": veto,
            "counts": {"parts": 0, "nets": len(nets),
                       "unsized": 0, "estimated": 0, "zero_height": 0},
        }

    # 未闭合的不可逆决策也算"没完工"——它们决定板子能不能交付。
    if open_irreversible:
        veto.append(f"{len(open_irreversible)} 项不可逆决策未闭合："
                    + ", ".join(str(name) for name in open_irreversible))

    phase = "partial" if veto else "complete"
    reason = (
        "元件齐全、尺寸都有出处、不可逆决策已闭合"
        if phase == "complete"
        else f"{len(parts)} 个元件，但有未完成项"
    )
    return {
        "phase": phase,
        "reason": reason,
        "veto": veto,
        "counts": {
            "parts": len(parts),
            "nets": len(nets),
            "unsized": len(unsized),
            "estimated": len(estimated),
            "zero_height": len(zero_height),
        },
    }


def vacuous(design: DesignModel, findings: list[dict[str, Any]]) -> list[str]:
    """哪些"通过"是因为没有对象，而不是真的检查过。

    这是**假绿灯**的精确清单：用户在面板上看到 9 条 ok，其中 9 条可能全是
    空集产物。把它们单列出来，"通过"才有可信度。
    """
    parts = list(design.parts or [])
    nets = list(design.nets or [])
    passed = {str(f.get("check")) for f in findings if f.get("ok") is True}
    has_connector = any(
        str(getattr(part, "role", "")).lower() == "connector" for part in parts)
    out: list[str] = []
    for check in OBJECT_DEPENDENT_CHECKS:
        if check not in passed:
            continue
        # 没有任何元件：全部是空集通过。
        if not parts:
            out.append(check)
            continue
        # 有元件之后，只有"依赖某类对象"的那几条还可能是空集。
        if check == "space.net_reach" and not nets:
            out.append(check)
        elif check == "space.connector_edge" and not has_connector:
            out.append(check)
    return sorted(set(out))
