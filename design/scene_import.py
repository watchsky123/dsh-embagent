"""R5 反向 —— **hwforge `scene.json` → `DesignModel`**（让 hwforge 装的东西能看）。

## 为什么需要这个方向

`design/scene.py` 只做了**正向**：工作台的设计 → `scene.json` → hwforge 的
仿真/判定链。反向是空的，于是：

* 用 `hw_block assemble` 装的装配，**2D/3D 视图看不到**；
* hwforge 自己的 `project/*/scene.json` 也进不来。

两个方向都是真需求，而且不对称：正向让 hwforge 能判工作台的板子，
反向让工作台能看 hwforge 的装配。缺一边，这套东西就只能用一半。

## 坐标与单位

| hwforge `scene.json` | `DesignModel` |
|---|---|
| `space.placements[].box{x0,y0,z0,nx,ny,nz}`（体素） | `placement` + `size_mm`（mm） |
| `space.pitch_mm` | 换算系数（默认 0.5） |
| `wires.runs[]`（起终点体素 + net） | `nets[]`（网 → 引脚） |
| `space.dims`（体素） | `board.size_mm` |
| `placements[].meta.role/template` | `Part.role` / `Part.kind` |

**体素 → mm 是 `v * pitch`**。hwforge 已经把负坐标平移到正空间并留了 2mm
pad（`build_from_spec` 里做的），所以这里**减掉 pad**，让元件的真实左下角
等于它在装配里的位置。

## 三条纪律

1. **引脚坐标拿不到就留空，不编。** `scene.json` 的 `placements[]` 里
   **没有引脚**（引脚在 `assembly.cables` 或 `wires.runs` 的 endpoints 上）。
   所以本模块只能从 `wires.runs` 的 endpoints 反推焊盘 —— 没被走线碰到的
   引脚**没有坐标**，`pads` 里就不该出现它。
2. **网表从 `wires.runs` 建，不从 `placements[].nets` 建。**
   后者只是网名列表，没有引脚；拿它建网表会得到一堆没有端点的空网。
3. **来源写进 `meta`。** `assembly.sources` / `unverified` 原样带过来，
   面板上能看出这是 hwforge 装的、哪些件没有可信来源。

@module hw_velxio.design.scene_import
"""

from __future__ import annotations

from typing import Any

from .model import Board, DesignModel, DesignError, Part, Pad, Placement


#: hwforge `build_from_spec` 把整装配平移了 `2mm / pitch` 个格。
DEFAULT_PAD_MM = 2.0


def _mm(voxels: float | int, pitch: float) -> float:
    return round(float(voxels) * pitch, 4)


def _part_from_placement(entry: dict[str, Any], pitch: float, pad_mm: float,
                         pads_by_ref: dict[str, list[Pad]]) -> Part | None:
    """把一条 `space.placements[]` 变成 `Part`。"""
    ref = str(entry.get("ref") or "").strip()
    if ref == "":
        return None
    box = entry.get("box") or {}
    try:
        x0 = float(box.get("x0", 0))
        y0 = float(box.get("y0", 0))
        z0 = float(box.get("z0", 0))
        nx = float(box.get("nx", 0))
        ny = float(box.get("ny", 0))
        nz = float(box.get("nz", 0))
    except (TypeError, ValueError):
        return None
    if nx <= 0 or ny <= 0:
        return None

    meta = entry.get("meta") or {}
    size = {"w": _mm(nx, pitch), "h": _mm(ny, pitch), "d": _mm(nz, pitch)}
    x_mm = _mm(x0, pitch) - pad_mm
    y_mm = _mm(y0, pitch) - pad_mm
    z_mm = max(0.0, _mm(z0, pitch) - pad_mm)

    # 角色：`meta.role` 是 hwforge 的说法（gimbal_pan/mcu…），
    # 直接当 `Part.role` 会破坏 `space.connector_edge` 等检查的语义
    # （它们只认 connector）。所以映射到元件级角色，原始值留在 label。
    raw_role = str(meta.get("role") or "")
    kind = str(meta.get("template") or entry.get("kind") or "component")
    role = _role_of(kind, raw_role)

    return Part(
        ref=ref,
        kind=kind,
        # 标签用模板名：`meta.role` 可能很长（`gimbal_pan`），
        # 而 `label` 是画在元件框里的。
        label=kind,
        role=role,
        size_mm=size,
        # 尺寸来自 hwforge 的体素盒 —— 那是**实测推导**，不是估算
        physical_source="derived",
        placement=Placement(x_mm=round(x_mm, 4), y_mm=round(y_mm, 4),
                            z_mm=round(z_mm, 4),
                            rotation_z=0, mount="board", side="top"),
        pads=list(pads_by_ref.get(ref, [])),
        pins=[p.num for p in pads_by_ref.get(ref, [])],
        notes=[raw_role] if raw_role else [],
    )


def _role_of(kind: str, raw_role: str) -> str:
    """元件级角色（影响连接器靠边、静力学等检查）。"""
    if "bracket" in kind or "chassis" in kind or "mechanical" in kind:
        return "structure"
    if "psu" in kind or "regulator" in kind or "battery" in kind:
        return "component"
    if "mcu" in kind:
        return "module"
    if "camera" in kind or "servo" in kind or "ranger" in kind:
        return "module"
    if "connector" in kind or "connector" in raw_role:
        return "connector"
    return "component"


def _pads_from_runs(runs: list[dict[str, Any]], pitch: float,
                    pad_mm: float) -> dict[str, list[Pad]]:
    """从走线端点反推焊盘。

    **只有被走线碰到的引脚才有坐标** —— `scene.json` 的 `placements[]`
    里没有引脚表。没被碰到的引脚不出现，这比编一个坐标诚实。
    """
    by_ref: dict[str, list[Pad]] = {}
    seen: dict[str, set[str]] = {}
    for run in runs:
        for end in ("from", "to"):
            endpoint = run.get(end) or {}
            ref = str(endpoint.get("ref") or "").strip()
            pin = str(endpoint.get("pin") or "").strip()
            voxel = endpoint.get("voxel")
            if ref == "" or pin == "" or not isinstance(voxel, (list, tuple)):
                continue
            if len(voxel) < 2:
                continue
            key = f"{ref}\u0000{pin}"
            if pin in seen.setdefault(ref, set()):
                continue
            seen[ref].add(pin)
            by_ref.setdefault(ref, []).append(Pad(
                num=pin,
                x_mm=round(_mm(voxel[0], pitch) - pad_mm, 4),
                y_mm=round(_mm(voxel[1], pitch) - pad_mm, 4),
                # 焊盘尺寸没有来源 —— 0 并保持 0，不编造
                w_mm=0.0, h_mm=0.0, shape="RECT", layer=1,
            ))
    return by_ref


def _nets_from_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从走线建网表。**不用 `placements[].nets`** —— 那只有网名，没有引脚。"""
    grouped: dict[str, list[dict[str, str]]] = {}
    seen: dict[str, set[tuple[str, str]]] = {}
    for run in runs:
        net = str(run.get("net") or "").strip()
        if net == "":
            continue
        for end in ("from", "to"):
            endpoint = run.get(end) or {}
            ref = str(endpoint.get("ref") or "").strip()
            pin = str(endpoint.get("pin") or "").strip()
            if ref == "" or pin == "":
                continue
            key = (ref, pin)
            if key in seen.setdefault(net, set()):
                continue
            seen[net].add(key)
            grouped.setdefault(net, []).append({"ref": ref, "pin": pin})
    # 只有一个脚的"网"不是网
    return [{"net": net, "pins": pins}
            for net, pins in sorted(grouped.items()) if len(pins) >= 2]


def from_scene(scene: dict[str, Any], *, design_id: str,
               title: str = "") -> DesignModel:
    """把一个 hwforge `scene.json` 读成 `DesignModel`。

    读不懂的部分**不抛异常** —— 收集到 `warnings` 里，返回能读的部分。
    抛异常会让"装配里有一个怪件"变成"整块板都看不了"。
    """
    if not isinstance(scene, dict):
        raise DesignError(f"scene 必须是对象，收到 {type(scene).__name__}")
    space = scene.get("space") or {}
    if not isinstance(space, dict):
        raise DesignError("scene.space 必须是对象")
    placements = space.get("placements") or []
    if not isinstance(placements, list) or not placements:
        raise DesignError(
            "scene.space.placements 为空：这不是一个装好的场景"
            "（hwforge 的 `assemble` 会产出它）")

    try:
        pitch = float(space.get("pitch_mm") or 0.5)
    except (TypeError, ValueError):
        pitch = 0.5
    if pitch <= 0:
        pitch = 0.5
    pad_mm = DEFAULT_PAD_MM

    wires = scene.get("wires") or {}
    runs = wires.get("runs") if isinstance(wires, dict) else None
    runs = runs if isinstance(runs, list) else []

    pads_by_ref = _pads_from_runs(runs, pitch, pad_mm)

    parts: list[Part] = []
    problems: list[str] = []
    for entry in placements:
        if not isinstance(entry, dict):
            problems.append(f"跳过一条不是对象的 placement：{type(entry).__name__}")
            continue
        try:
            part = _part_from_placement(entry, pitch, pad_mm, pads_by_ref)
        except (DesignError, KeyError, TypeError, ValueError) as exc:
            problems.append(f"{entry.get('ref')}: {exc}")
            continue
        if part is None:
            problems.append(f"跳过无 ref 或无尺寸的 placement：{entry.get('ref')}")
            continue
        parts.append(part)

    if not parts:
        raise DesignError(
            "scene 里的 placement 一条都读不出来：" + "；".join(problems[:4]))

    # 板框：`space.dims` 是体素三元组
    dims = space.get("dims") or []
    if isinstance(dims, (list, tuple)) and len(dims) >= 2:
        board_w = round(_mm(dims[0], pitch) - pad_mm * 2, 3)
        board_h = round(_mm(dims[1], pitch) - pad_mm * 2, 3)
    else:
        # 没有 dims 就按元件包围盒兜一个矩形 —— **标出来**
        board_w = round(max((p.placement.x_mm + p.size_mm["w"] for p in parts),
                            default=40.0) + 4.0, 3)
        board_h = round(max((p.placement.y_mm + p.size_mm["h"] for p in parts),
                            default=30.0) + 4.0, 3)
        problems.append("scene.space.dims 缺失，板框按元件包围盒估算")

    assembly = scene.get("assembly") or {}
    if not isinstance(assembly, dict):
        assembly = {}

    # **`space.dims` 是 hwforge 的工作空间，不是 PCB 板框。**
    # 它通常比元件包围盒大一圈（测试里 space_mm=(200,160) 而元件只占 90×30）。
    # 直接当板框会得到一个"空荡荡的大板"，`space.bounds` 检查也就失去意义。
    # 所以如实标注：板框是**推导**的，不是 scene 声明的。
    board_note = "board_from_space_dims"

    board = Board(
        kind=f"hwforge:{assembly.get('target') or design_id}",
        name=f"hwforge {assembly.get('target') or '装配'} "
             f"{board_w:.0f}×{board_h:.0f}mm",
        size_mm={"w": max(board_w, 10.0), "h": max(board_h, 10.0)},
        thickness_mm=1.6, mount_holes=[], px_per_mm=3.7751,
    )

    nets = _nets_from_runs(runs)
    if board_note == "board_from_space_dims":
        problems.append(
            "板框取自 scene.space.dims —— 那是 hwforge 的**工作空间**，"
            "不是 PCB 板框（通常比元件包围盒大一圈）。真实板框需要另行导入")
    design = DesignModel(
        id=design_id, title=title or design_id, board=board,
        parts=parts, nets=nets, wires=[],
        meta={
            "source": "hwforge-scene",
            "pitch_mm": pitch,
            "pad_mm": pad_mm,
            "target": assembly.get("target", ""),
            "install": assembly.get("install", ""),
            # 出处与未验证项原样带过来 —— 面板要能看出这是 hwforge 装的
            "hwforge_sources": dict(assembly.get("sources") or {}),
            "hwforge_unsourced_parts": list(assembly.get("unsourced_parts") or []),
            "hwforge_unverified": list(scene.get("unverified") or []),
            "cables_unrouted": list(assembly.get("cables_unrouted") or []),
            # **引脚只从走线端点来**，没被碰到的引脚没有坐标
            "pin_source": "wires.runs[].from/to 反推（placements 里没有引脚表）",
            "read_problems": problems,
        },
    )
    if problems:
        design.warnings.append(
            f"scene 读取有 {len(problems)} 处问题（见 meta.read_problems）："
            + "；".join(problems[:3]))
    if not nets:
        design.warnings.append(
            "scene 里没有可用的走线（wires.runs 为空）："
            "网表为空，引脚坐标也没有 —— 不是漏读，是源文件里就没有")
    unsourced = design.meta.get("hwforge_unsourced_parts") or []
    if unsourced:
        design.warnings.append(
            f"{len(unsourced)} 个元件的尺寸没有可信来源（hwforge 标的 "
            f"unsourced）：{'、'.join(str(x) for x in unsourced[:6])}")
    return design


def from_scene_file(path: str, *, design_id: str, title: str = "") -> DesignModel:
    """从磁盘读 `scene.json`。（读写分离，方便测试传 dict。）"""
    import json
    from pathlib import Path as _Path

    file = _Path(path)
    if not file.is_file():
        raise DesignError(f"找不到 scene 文件：{path}")
    try:
        scene = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DesignError(f"scene 文件不是合法 JSON：{exc}") from exc
    return from_scene(scene, design_id=design_id,
                      title=title or file.parent.name or design_id)
