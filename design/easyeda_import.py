"""把立创 EDA 工程（`.epro2`）导入成工作台设计。

## 这个模块现在能给什么（A + B 都通了）

单位与归属按官方规范（`docs/easyeda-format.md` + `lceda-pro doc format`）：

| 数据 | 状态 | 依据 |
|---|---|---|
| 元件标号 | ✅ 真实（264/264） | PCB 的 `ATTR` `Designator` |
| **元件坐标** | ✅ **真实**（mil → mm） | `COMPONENT.x/y`，`MIL_TO_MM` |
| **旋转角度** | ✅ 真实（逆时针为正） | `COMPONENT.angle` |
| **网络名 + 引脚归属** | ✅ 真实（232 元件 / 406 网络） | `PAD_NET` 键式 id |
| 元件值（1uF/10K） | ⚠️ 拿不到 | PCB 只引用 `Device` 哈希；值在 DEVICE 库 |
| 元件尺寸 | ⚠️ 拿不到 | 封装是哈希 id，名字在 FOOTPRINT 文档 |

**坐标是真的**，所以 3D 现在反映真实布局；尺寸仍未知，按纪律标
`estimated` 而**不编造数值**。`space.overlap` 这类检查会因为没有尺寸而
无法真正判定——`checks.json` 的 `vacuous`/`not_verified` 会如实标出。

@module hw_velxio.design.easyeda_import
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import easyeda, packages
from .boards import board_preset
from .easyeda import MIL_TO_MM
from .model import Board, DesignModel, DesignError, Part, Pad, Placement, Wire

# 标号前缀 → 角色（影响 2D 着色与预算分组）。这是 EDA 通用约定。
ROLE_BY_PREFIX = {
    "R": "component", "C": "component", "L": "component",
    "D": "component", "Q": "component", "U": "module",
    "J": "connector", "P": "connector", "X": "component",
    "SW": "component", "F": "component", "Y": "component",
    "LED": "component", "BT": "module", "CN": "connector",
    "CARD": "module", "LDO": "component", "RF": "component",
}


def _board_from_outline(outline: list[list[float]],
                        size: dict[str, float], *, kind: str) -> Any:
    """用工程的**真实板框**构造 Board（不用板卡预设）。

    预设的 Arduino Uno 有 4 个安装孔，而这块板没有——沿用预设会报"安装孔
    被遮挡"这种与项目无关的假问题。板框来自 `POLY.polyType=BOARD_OUTLINE`。
    """
    from .model import Board

    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    ox, oy = min(xs), min(ys)
    # 以板框左下角为原点，构造闭合多边形。
    normalized = [[round(p[0] - ox, 3), round(p[1] - oy, 3)] for p in outline]
    if normalized[0] != normalized[-1]:
        normalized.append(list(normalized[0]))
    return Board(
        kind=kind,
        name=f"EasyEDA {size.get('w', 0):.1f}×{size.get('h', 0):.1f}mm",
        outline=[tuple(p) for p in normalized],
        size_mm={"w": float(size.get("w", 0)), "h": float(size.get("h", 0))},
        thickness_mm=1.6,          # PCB 文件有真实板厚，这里先用常见值并标注
        mount_holes=[],            # 真实板框没有预设的安装孔
        px_per_mm=3.7751,
    )


# 封装名里出现这些词 → 它是**连接器**。比标号前缀可靠得多：
# 这份工程里 `U1` 实际是 WAFER-MX1.25 排针，`RF1` 是 SMA 座。
CONNECTOR_WORDS = ("CONN", "WAFER", "SMA", "USB", "HEADER", "SOCKET",
                   "PLUG", "JACK", "TERMINAL", "MX1.25", "PH2.0", "XH",
                   "P1.25", "P2.00", "P2.50", "TYPE-C")


def _role_of(ref: str, hint: str = "") -> str:
    """推角色：**先看封装名**（确定性），再看标号前缀（约定）。

    `space.connector_edge` 与 `space.bounds` 对连接器的处理不同（允许悬边），
    所以这个判断错了会直接产生假发现——实测 `RF1`（SMA 座）被判成普通元件，
    于是报"足迹超出板轮廓 3.92 mm"，而连接器悬在板边是正常设计。

    **只信封装名，不信 `3D Model Title`**：实测 U12 的真实封装是
    `ESOP-8`（一颗芯片），但它的 `3D Model Title` 记着
    `CONN-TH_4P-P1.25_WAFER-MX1.25-4PZZ`（那是另一个元件的模型）。
    拿模型标题判角色会把它误判成连接器，于是报"连接器距板边 10.1mm" ——
    而它根本不是连接器，不该靠边。
    """
    upper = hint.upper()
    if any(word in upper for word in CONNECTOR_WORDS):
        return "connector"
    match = re.match(r"^([A-Za-z]+)", ref)
    if match is None:
        return "component"
    prefix = match.group(1).upper()
    if prefix[:4] in ROLE_BY_PREFIX:
        return ROLE_BY_PREFIX[prefix[:4]]
    if prefix[:2] in ROLE_BY_PREFIX:
        return ROLE_BY_PREFIX[prefix[:2]]
    return ROLE_BY_PREFIX.get(prefix[0], "component")


def _pad_extent_mm(pad_geometry: list[dict[str, Any]]) -> dict[str, float]:
    """焊盘外廓（mm）：所有焊盘中心 + 焊盘尺寸的包围盒。

    这是"本体尺寸"的实测兜底 —— 焊盘伸出本体，所以外廓略大于本体。
    """
    if not pad_geometry:
        return {}
    xs: list[float] = []
    ys: list[float] = []
    for pad in pad_geometry:
        try:
            cx = float(pad["x"]) * MIL_TO_MM
            cy = float(pad["y"]) * MIL_TO_MM
            w = float(pad["w"]) * MIL_TO_MM
            h = float(pad["h"]) * MIL_TO_MM
        except (KeyError, TypeError, ValueError):
            continue
        xs.extend((cx - w / 2.0, cx + w / 2.0))
        ys.extend((cy - h / 2.0, cy + h / 2.0))
    if not xs or not ys:
        return {}
    return {"w": max(xs) - min(xs), "h": max(ys) - min(ys)}


def _pad_geometry_mm(pad_geometry: list[dict[str, Any]]) -> list[dict[str, float]]:
    """焊盘几何（mm，**封装本地**坐标系）：中心 + 尺寸。

    这是"本体尺寸"的实测依据 —— 焊盘是封装几何本身，对每个元件都一致；
    而 `3D Model Transform` 在同一个封装上会给出两个不同值（实测 C0603
    同时有 1.6×0.8 和 1.0×0.5），不能当真值。

    **必须是封装本地坐标**：元件转了 90° 之后，绝对坐标里的长宽会互换，
    本体尺寸就会算反（实测 C0805 拿到 1.41×2.34，正确是 2.0×1.25）。
    """
    out: list[dict[str, float]] = []
    for pad in pad_geometry or []:
        try:
            out.append({
                "cx": float(pad["cx"]) * MIL_TO_MM,
                "cy": float(pad["cy"]) * MIL_TO_MM,
                "w": float(pad["w"]) * MIL_TO_MM,
                "h": float(pad["h"]) * MIL_TO_MM,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _natural_key(ref: str) -> tuple[str, int]:
    match = re.match(r"^([A-Za-z]+)(\d*)", ref)
    if match is None:
        return (ref, 0)
    return (match.group(1).upper(), int(match.group(2) or 0))


def build_design(parsed: dict[str, Any], *, design_id: str,
                 board_kind: str = "arduino-uno",
                 origin_mm: tuple[float, float] | None = None,
                 board_filter: str = "main") -> DesignModel:
    """把解析结果转成 `DesignModel`。

    **坐标是真的**（来自 PCB），但需要平移到板坐标系：EDA 的坐标原点是
    任意的，板轮廓从 (0,0) 开始。这里按所有元件的包围盒左下角对齐到板
    内边距 —— 这是**确定性的平移**，不改变相对布局。

    `origin_mm` 可由调用方指定（例如已知板框原点），否则自动对齐。

    ## `board_filter`（多板）

    * `"main"`（默认）—— 只导入元件最多的那块板。其余板写进
      `meta.boards_skipped` 与 `warnings`，**不静默丢弃**。
    * `"all"` —— 导入**全部**板：每块板成为 `design.boards` 的一项，
      元件用 `placement.board_id` 归属，坐标是**各自板的局部坐标**。
      板间相对位置由 `projection.panel_layout()` 确定性排布。
    * 其它值 —— 当作 `doc` id 精确匹配（只导入那一块）。

    `"all"` 时 `parts_skipped` 为 0（不丢任何元件）。
    """
    # **板框与坐标必须按文档分组**：这个工程是**拼板**（11 个 PCB 文档、
    # 7 块板框、273 个元件分布在各板上）。把全部元件塞进一块板框，会让
    # 大多数元件"跑到板外"——那不是布局错误，是建模错误。
    components_all = [c for c in (parsed.get("components") or [])
                      if c.get("x_mm") is not None and c.get("ref")]
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for component in components_all:
        by_doc.setdefault(str(component.get("doc") or ""), []).append(component)
    # 用 parse() 算好的主板（两处各算一次会漂移，见 design/easyeda.py）。
    main_doc = str(parsed.get("main_doc") or "")
    if main_doc == "" and by_doc:
        main_doc = max(by_doc.items(), key=lambda kv: len(kv[1]))[0]

    # -- 选哪些板 ----------------------------------------------------------
    wanted = str(board_filter or "main").strip()
    if wanted in ("", "main"):
        selected_docs = [main_doc] if main_doc else list(by_doc)
    elif wanted == "all":
        # 主板在前，其余按元件数降序（与 panel_layout 的排布一致，
        # 这样"第一块板"就是主板）
        rest = sorted((d for d in by_doc if d != main_doc),
                      key=lambda d: (-len(by_doc[d]), d))
        selected_docs = ([main_doc] if main_doc else []) + rest
    else:
        selected_docs = [wanted] if wanted in by_doc else []
        if not selected_docs:
            raise DesignError(
                f"没有 doc 为 {wanted!r} 的板；可选："
                + "、".join(sorted(by_doc)[:12]))

    components = [c for d in selected_docs for c in by_doc.get(d, [])]
    components.sort(key=lambda c: _natural_key(str(c["ref"])))
    imported_docs = {str(c.get("doc") or "") for c in components}
    other_boards = {doc: len(items) for doc, items in by_doc.items()
                    if doc not in imported_docs}

    # -- 丢弃的板必须**说出来** -------------------------------------------
    #
    # 这个工程（以及任何拼板/多硬件项目）的真实内容分布在多块 PCB 上。
    # 模型是单板的（`DesignModel.board` 是单数），所以只导入元件最多的一块。
    # 但"只导一块"和"静默丢掉另外 7 块"是两件完全不同的事：
    # 实测这份工程丢掉了 175/316 = 55% 的元件，而 `meta` 里没有任何字段
    # 提到这件事，`warnings` 也是空的 —— 用户只能看到"元件怎么这么少"。
    #
    # 所以这里把 it 算清楚并向下传：`meta["boards_skipped"]` 给工具看，
    # `design.warnings` 给界面看。
    outline_by_doc = {str(o.get("doc") or ""): o
                      for o in (parsed.get("outlines_full") or [])}
    boards_skipped: list[dict[str, Any]] = []
    for doc, count in sorted(other_boards.items(), key=lambda kv: -kv[1]):
        outline = outline_by_doc.get(doc) or {}
        boards_skipped.append({
            "doc": doc,
            "parts": int(count),
            "size_mm": {"w": float(outline.get("w") or 0.0),
                        "h": float(outline.get("h") or 0.0)},
        })
    parts_skipped = sum(item["parts"] for item in boards_skipped)
    total_parts = len(components_all)

    # -- 坐标平移（**按板各算一份**）-------------------------------------
    #
    # 每块板的元件坐标是**那块板的局部坐标**，所以平移量必须按板算。
    # 用全局包围盒的话，一块板上的元件会被挪到另一块板的范围里。
    outline_by_doc_raw = {str(o.get("doc") or ""): o
                          for o in (parsed.get("outlines_full") or [])}
    shift_by_doc: dict[str, tuple[float, float]] = {}
    outline_by_doc: dict[str, list[list[float]]] = {}
    size_by_doc: dict[str, dict[str, float]] = {}
    for doc in selected_docs:
        board_components = by_doc.get(doc, [])
        bxs = [float(c["x_mm"]) for c in board_components]
        bys = [float(c["y_mm"]) for c in board_components
               if c.get("y_mm") is not None]
        outline = list((outline_by_doc_raw.get(doc) or {}).get("points") or [])
        if not outline:
            outline = list(parsed.get("outline_mm") or [])
        if not outline:
            outline = []
        if origin_mm is not None:
            shift_by_doc[doc] = origin_mm
        else:
            # 用**本板元件**的包围盒对齐（元件与板框不在同一坐标系：
            # 实测某板元件 y=2.79..42.67，而板框 y=45.97..90.97）。
            dx = -min(bxs) + 2.0 if bxs else 0.0
            dy = max(bys) + 2.0 if bys else 0.0
            shift_by_doc[doc] = (dx, dy)
        outline_by_doc[doc] = outline
        if outline:
            xs_ = [p[0] for p in outline]
            ys_ = [p[1] for p in outline]
            size_by_doc[doc] = {"w": round(max(xs_) - min(xs_), 3),
                                "h": round(max(ys_) - min(ys_), 3)}

    # 主板（兼容旧单板路径）：用 `main_doc` 的平移/轮廓
    primary_doc = selected_docs[0] if selected_docs else main_doc
    dx, dy = shift_by_doc.get(primary_doc, (0.0, 0.0))
    real_outline = outline_by_doc.get(primary_doc) or []
    real_size = size_by_doc.get(primary_doc) or {}

    if real_outline and real_size:
        board = _board_from_outline(real_outline, real_size,
                                    kind=f"easyeda:{design_id}")
    else:
        board = board_preset(board_kind)
    board.id = primary_doc
    # -- 全部板（多板导入时每块板各一项）----------------------------------
    #
    # `boards` 为空 = 单板（等价于 `[board]`）。只有真的导入多块时才填，
    # 这样单板工程的 design.json 与旧版本逐字节一致。
    board_list: list[Board] = [board]
    if len(selected_docs) > 1:
        board_list = [board]
        for doc in selected_docs[1:]:
            outline = outline_by_doc.get(doc) or []
            size = size_by_doc.get(doc) or {}
            if outline and size:
                extra = _board_from_outline(outline, size,
                                            kind=f"easyeda:{design_id}")
            else:
                # 没有板框的板：按元件包围盒兜一个矩形，**标 estimated**
                bcomps = by_doc.get(doc, [])
                bxs = [float(c["x_mm"]) for c in bcomps]
                bys = [float(c["y_mm"]) for c in bcomps]
                w = (max(bxs) - min(bxs) + 4.0) if bxs else 20.0
                h = (max(bys) - min(bys) + 4.0) if bys else 20.0
                extra = Board(
                    kind=f"easyeda:{design_id}",
                    name=f"{doc}（无板框，按元件包围盒估算）",
                    size_mm={"w": round(w, 3), "h": round(h, 3)},
                    thickness_mm=1.6, mount_holes=[],
                    px_per_mm=board.px_per_mm)
            extra.id = doc
            board_list.append(extra)
    board_w = float(board.size_mm.get("w", 72.58))
    board_h = float(board.size_mm.get("h", 53.34))
    skipped = [c for c in (parsed.get("components") or [])
               if c.get("x_mm") is None]
    placeholders = [c for c in components
                    if "?" in str(c.get("designator") or "")]

    parts: list[Part] = []
    issues: list[str] = []
    sized = 0
    pads_total = 0
    for component in components:
        ref = str(component["ref"])
        # **每块板有自己的平移量**：坐标是那块板的局部坐标。
        comp_doc = str(component.get("doc") or "")
        cdx, cdy = shift_by_doc.get(comp_doc, (dx, dy))
        # **立创的 `COMPONENT.x/y` 是元件的**中心**（实测：与焊盘包围盒中心
        # 相差 0.000mm，140/140 个元件吻合）。
        #
        # 而规范模型的 `placement.x_mm/y_mm` 语义是**足迹的左下角**
        # （KiCad 与 Wokwi 导入器都减掉了 w/2/h/2 才存）。不换算的话，
        # 2D 里每个元件都画得偏右上 w/2、h/2 —— 实测焊盘画到了元件框外面。
        cx = float(component["x_mm"]) + cdx
        cy = cdy - float(component.get("y_mm") or 0.0)   # y 轴翻转
        # **尺寸来源决定 provenance**：
        #   有 3D Model Transform → 库里的真实包围盒（mil）→ `derived`
        #   没有 → 留 0 且标 `estimated`，绝不填猜测值
        size = component.get("size_mm") or None
        footprint_title = str(component.get("footprint_title") or "")
        # 焊盘几何（mm，**封装本地**）—— 本体尺寸的**实测**来源。
        pads_mm = _pad_geometry_mm(component.get("pad_local") or [])
        # 尺寸来源的优先级（实测校准，见 design/packages.py）：
        #
        #   1. **焊盘几何** —— 封装本身，每个元件都一致。最可信。
        #   2. 封装名标准尺寸 —— 公开标准系列（0603/LQFP-64…）。
        #   3. `3D Model Transform` —— **不可信**：同一个 C0603 在这份工程里
        #      同时出现 1.6×0.8 和 1.0×0.5 两个值。所以它只用来取**高度**。
        #
        # 全部标 `derived`（实测推导），查不到才 `estimated`。
        size_mm = {"w": 0.0, "h": 0.0, "d": 0.0}
        source = "estimated"
        guessed = packages.package_size(footprint_title)
        if pads_mm:
            from_pads = packages.size_from_pads(pads_mm)
            if from_pads is not None:
                size_mm = {"w": from_pads["w"], "h": from_pads["h"], "d": 0.0}
                source = "derived"
        if size_mm["w"] <= 0 and guessed is not None:
            size_mm = {"w": float(guessed["w"]), "h": float(guessed["h"]),
                       "d": float(guessed["d"])}
            source = "derived"
            sized += 1
        # 高度：焊盘推不出，用封装标准高度；再不行用 3D 模型的 sizeZ。
        if size_mm["d"] <= 0:
            height = packages.height_for(footprint_title)
            if height <= 0:
                height = packages.height_for_keywords(footprint_title)
            if height > 0:
                size_mm["d"] = height
        if size_mm["d"] <= 0 and size is not None:
            size_mm["d"] = float(size.get("d", 0) or 0)
        if size is not None and size.get("w", 0) > 0 and size.get("h", 0) > 0:
            # 3D 模型尺寸只作**高度**来源；平面尺寸以焊盘为准。
            if size_mm["w"] <= 0:
                size_mm = {"w": float(size["w"]), "h": float(size["h"]),
                           "d": float(size.get("d", 0) or 0)}
                source = "derived"
                sized += 1
            elif size_mm["d"] <= 0:
                size_mm["d"] = float(size.get("d", 0) or 0)
        if size_mm["w"] > 0 and source == "estimated":
            source = "derived"
        # 焊盘几何：EDA 坐标（mil，y 向上）→ 板坐标（mm，y 向上）。
        # 与元件走**同一条**平移/翻转路径，两者不会漂移。
        # **先建焊盘**：下面要用它们的包围盒中心定位本体。
        pads: list[Pad] = []
        for pad in (component.get("pad_geometry") or []):
            try:
                pads.append(Pad(
                    num=str(pad["num"]),
                    x_mm=round(float(pad["x"]) * MIL_TO_MM + cdx, 4),
                    y_mm=round(cdy - float(pad["y"]) * MIL_TO_MM, 4),
                    w_mm=round(float(pad["w"]) * MIL_TO_MM, 4),
                    h_mm=round(float(pad["h"]) * MIL_TO_MM, 4),
                    shape=str(pad.get("shape") or "RECT"),
                    rotation_z=float(pad.get("angle") or 0.0),
                    layer=int(pad.get("layer") or 1),
                    hole_mm=round(float(pad.get("hole") or 0.0) * MIL_TO_MM, 4),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                issues.append(f"{ref}: 焊盘几何无效（{exc}）")
        pads_total += len(pads)
        # -- 位置：必须以**焊盘包围盒中心**为准，不是元件原点 ----------------
        #
        # 封装原点在 EDA 里常是**1 脚**或任意锚点，不在本体中心。实测 Lora1：
        # 元件原点是 (43.8, -101.1)mil，而 19 个焊盘的包围盒中心偏了
        # 16.83×7.05mm —— 直接用原点会把本体画到焊盘左边一大截，15 个焊盘
        # 落到框外。
        #
        # 焊盘是**实测几何**，它的包围盒中心就是本体中心。所以：
        #   本体中心 = 焊盘包围盒中心（有焊盘时）
        #   没有焊盘 → 退回元件原点
        origin_x = cx
        origin_y = cy
        if pads:
            pad_xs = [q.x_mm for q in pads]
            pad_ys = [q.y_mm for q in pads]
            origin_x = (min(pad_xs) + max(pad_xs)) / 2.0
            origin_y = (min(pad_ys) + max(pad_ys)) / 2.0
        # 规范模型的 `placement` 存**左下角**（与 KiCad / Wokwi 导入器一致）。
        x = origin_x - size_mm["w"] / 2.0
        y = origin_y - size_mm["h"] / 2.0
        try:
            parts.append(Part(
                ref=ref,
                kind=str(component.get("device") or "component"),
                label=str(component.get("value") or ""),
                role=_role_of(ref, footprint_title),
                size_mm=size_mm,
                physical_source=source,
                placement=Placement(
                    x_mm=round(x, 3), y_mm=round(y, 3),
                    rotation_z=int(round(float(component.get("angle_deg") or 0))) % 360,
                    mount="board",
                    # 安装面（顶/底）。缺了它，双面板上顶层的件与底层的件
                    # 会被误判成实体重叠（实测主 PCB 71 顶 / 70 底）。
                    side=str(component.get("side") or ""),
                    # 归属哪块板：多板设计里坐标是那块板的局部坐标。
                    board_id=comp_doc),
                lcsc=str(component.get("lcsc") or "") or None,
                electrical={},
                # 实测焊盘号 = 这个元件的引脚表。有了它，网表里的引脚名
                # 就能被真校验（而不是标成"未验证"）。
                pins=[str(x) for x in (component.get("pads") or [])],
                pads=pads,
            ))
        except DesignError as exc:
            issues.append(f"{ref}: {exc}")

    # -- 网表：PAD_NET 给出真实的 "net → [REF.pad]" ----------------------
    by_ref = {p.ref: p for p in parts}
    nets: list[dict[str, Any]] = []
    dangling = 0
    for name, pins in (parsed.get("nets") or {}).items():
        entries: list[dict[str, Any]] = []
        for pin in pins:
            ref, _, pad = str(pin).partition(".")
            if ref in by_ref:
                entries.append({"ref": ref, "pin": pad})
            else:
                dangling += 1
        if len(entries) >= 1:
            nets.append({"net": name, "pins": entries})

    # -- 走线：LINE 段 → Wire（**真实铜线**，不是飞线）--------------------
    #
    # 与元件走**同一条**平移/翻转路径（`dx` / `dy`），否则铜线和焊盘会错位。
    # 每一条 `LINE` 是一段直线，把同网络的相邻段串成 `Wire.path_mm`。
    wires: list[Wire] = []
    trace_count = 0
    for trace in (parsed.get("traces") or []):
        try:
            x1 = float(trace["x1"]) + dx
            y1 = dy - float(trace["y1"])
            x2 = float(trace["x2"]) + dx
            y2 = dy - float(trace["y2"])
        except (KeyError, TypeError, ValueError):
            continue
        wires.append(Wire(
            net=str(trace.get("net") or ""),
            path_mm=[(round(x1, 4), round(y1, 4), 0.0),
                     (round(x2, 4), round(y2, 4), 0.0)],
            layer=int(trace.get("layer") or 1),
            width_mm=float(trace.get("width_mm") or 0.25),
        ))
        trace_count += 1

    # -- 丢弃的板写进 warnings：界面直接能看到 -----------------------------
    # `meta` 是给工具/脚本看的，`warnings` 是给**人**看的。
    # 两者都要写：只写 meta，用户在界面上仍然什么都不知道。
    warnings: list[str] = []
    if boards_skipped:
        preview = "、".join(
            f"{item['doc'].partition('#')[2][:8] or item['doc']}"
            f"（{item['parts']} 元件，{item['size_mm']['w']:.0f}×{item['size_mm']['h']:.0f}mm）"
            for item in boards_skipped[:4])
        more = f" 等 {len(boards_skipped)} 块" if len(boards_skipped) > 4 else ""
        warnings.append(
            f"本工程共 {len(by_doc)} 块板 / {total_parts} 个元件；"
            f"当前模型是单板的，只导入了主板的 {len(components)} 个元件。"
            f"未导入 {parts_skipped} 个元件（{parts_skipped / max(total_parts, 1) * 100:.0f}%）："
            f"{preview}{more}。这些元件不在视图里，也不参与任何检查。")
    if issues:
        warnings.append(f"{len(issues)} 个元件在导入时被跳过（几何/属性不完整），"
                        f"详见 meta.import_issues。")

    design = DesignModel(
        id=design_id,
        title=str(parsed.get("title") or design_id),
        board=board,
        # 单板时为空列表（design.json 与旧版本一致）；多板时每块板一项。
        boards=board_list if len(board_list) > 1 else [],
        parts=parts,
        nets=nets,
        wires=wires,
        warnings=warnings,
        meta={
            "imported_from": "easyeda-pro",
            "coords_are_real": True,
            "units": parsed.get("units", {}),
            "source_components": len(parsed.get("components") or []),
            "skipped_without_coords": len(skipped),
            "placeholder_designators": len(placeholders),
            "nets_imported": len(nets),
            "pins_declared": sum(1 for p in parts if p.pins),
            "pads_placed": pads_total,
            "traces_imported": trace_count,
            "dangling_pins": dangling,
            "import_issues": issues,
            # **被丢弃的板**：模型是单板的，只导入元件最多的一块。
            # 但丢弃多少必须如实上报 —— 实测这份工程丢掉 175/316 = 55%。
            "source_boards": len(by_doc),
            "parts_skipped": parts_skipped,
            "boards_skipped": boards_skipped,
            "model_note": (
                "当前模型是**单板**的（design.board 是单数），所以只导入了"
                "元件最多的一块板。其余板在 boards_skipped 里如实列出，"
                "未导入、也未参与任何检查。"),
            "origin_shift_mm": [round(dx, 3), round(dy, 3)],
            "layout_note": (
                "坐标来自 PCB（真实布局，mil→mm，y 轴已翻转对齐板坐标）。"),
            "size_note": (
                f"{sized}/{len(components)} 个元件有实测尺寸"
                f"（3D 模型包围盒 / 封装名标准尺寸 / 焊盘外廓反推，均标 derived）；"
                f"其余 {len(components) - sized} 个无尺寸，标 estimated。"),
            "value_note": (
                "值来自原理图侧 ATTR 的 Value（按 Designator 桥接）；"
                "PCB 只引用 Device 哈希，而这份工程的 DEVICE 文档只有 1 个带 "
                "META，器件库没有导出，所以值覆盖率有限。"),
        },
    )
    return design


def import_epro2(path: str | Path, *, design_id: str,
                 board_kind: str = "arduino-uno",
                 board_filter: str = "main",
                 bom_path: str | Path | None = None) -> DesignModel:
    """解析并构建设计。失败抛 `DesignError`（与其它导入器一致）。

    `board_filter`：`"main"`（只导主板）/ `"all"`（全部板）/ 具体 `doc` id。
    `"all"` 时每块板成为 `design.boards` 的一项，元件按 `board_id` 归属。

    `bom_path`：给了就用 BOM **补全**元件（只补 PCB 里没有的 ref）。
    这让"只有物料清单、没有完整 PCB 工程"的项目也能在视图里看到该有的
    元件 —— 位置是网格排布，`meta.bom_fill.placement == "grid-estimate"`。
    """
    parsed = easyeda.parse(path)
    if parsed.get("status") != "ok":
        detail = "；".join(parsed.get("findings") or []) or "解析失败"
        raise DesignError(f"读不了 {Path(path).name}：{detail}")
    design = build_design(parsed, design_id=design_id, board_kind=board_kind,
                          board_filter=board_filter)
    if bom_path is not None:
        from .bom_fill import fill_from_bom
        from .importers import import_bom

        bom = import_bom(bom_path)
        if bom:
            fill_from_bom(design, bom)
        else:
            design.warnings.append(
                f"BOM {Path(bom_path).name} 没读出任何条目（列名不匹配？），"
                "未补元件。")
    return design