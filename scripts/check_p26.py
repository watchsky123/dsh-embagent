"""P26/P27 验收 —— 几何回读、连通性判定、自研布线器（**全部离线**）。

    python -m hw_velxio.scripts.check_p26

这一层的价值全在「不依赖 DRC 也能判连通」，所以验收也**不需要立创在线**：
`pcb_geo.parse` 吃的是 JS 返回的 JSON，`router.plan` 吃的是几何对象，两者都是
纯函数。真机那一段（读回、落笔、铺铜）在 `check_p17` 和 `emit` 的实跑里验。

## 为什么这些断言值得写

* 手册 §22 第三轮的两个 bug：POLYGON 焊盘取半宽得 `NaN` → 明明连着报断开；
  分支点差 39 mil 没搭上 → 真的断线。两条都必须能被判定器抓到。
* AGENTS §3.20：板框 `R` 模式原点是左上角。这里用「板框 y 区间必须包含全部
  焊盘」把它变成一个**可执行断言**，而不是一句注释。
* AGENTS §0.1：空输入不能算出 `ok`。所以「没有网络」「读不到板框」都要红。
"""

from __future__ import annotations

import sys
from typing import Any
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from hw_velxio.design import lceda_channel, pcb_geo, pcb_layout, router  # noqa: E402

_failures: list[str] = []
_checks = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    if ok:
        print(f"[OK ] {name}")
        if detail:
            print(f"       {detail}")
    else:
        _failures.append(name)
        print(f"[FAIL] {name}")
        if detail:
            print(f"       {detail}")


# ---------------------------------------------------------------------------
# 夹具（合成：证明判据本身对；真实工程证明在脏数据上不崩）
# ---------------------------------------------------------------------------


def _payload(**over) -> dict:
    """一份模拟立创返回的几何。默认：两个焊盘 + 一条走线，网络 N1 连通。"""
    base = {
        "ok": True,
        "docType": 3,
        "docTries": 2,
        "nets": ["N1"],
        "ratlines": 0,
        "outlines": [["R", 0.0, 1574.8, 1968.5, 1574.8, 0, 0]],
        "components": [{
            "d": "R1", "x": 400.0, "y": 500.0, "layer": 1, "rot": 0,
            "pads": [{"net": "N1", "num": "1", "x": 400.0, "y": 500.0,
                      "layer": 1, "hole": False,
                      "hw": 16.0, "hh": 17.0, "shape": "RECT", "assumed": False}],
        }, {
            "d": "R2", "x": 1000.0, "y": 500.0, "layer": 1, "rot": 0,
            "pads": [{"net": "N1", "num": "1", "x": 1000.0, "y": 500.0,
                      "layer": 1, "hole": False,
                      "hw": 16.0, "hh": 17.0, "shape": "RECT", "assumed": False}],
        }],
        "tracks": [{"net": "N1", "x1": 416.0, "y1": 500.0,
                    "x2": 984.0, "y2": 500.0, "layer": 1, "width": 10.0}],
        "vias": [],
        "errors": {},
    }
    base.update(over)
    return base


def _geometry(nets: list[tuple[str, list[tuple[float, float]]]], *,
              outline: list | None = None,
              tracks: list[pcb_geo.Track] | None = None,
              ) -> pcb_geo.BoardGeometry:
    geo = pcb_geo.BoardGeometry(board="probe", pcb_uuid="p")
    geo.outlines = [outline] if outline else [["R", 0.0, 1574.8, 1968.5, 1574.8, 0, 0]]
    for net, pads in nets:
        for i, (x, y) in enumerate(pads):
            geo.pads.append(pcb_geo.Pad(net=net, number=str(i + 1), ref=f"P{i}",
                                        x=x, y=y, layer=1, hw=16.0, hh=17.0))
        geo.nets.append(net)
    for t in tracks or []:
        geo.tracks.append(t)
    return geo


# ---------------------------------------------------------------------------
# 1. 解析
# ---------------------------------------------------------------------------


def part_parse() -> None:
    geo = pcb_geo.parse(_payload(), board="B1", pcb_uuid="pcb-1")
    check("1a. parse：焊盘/走线/板框都读进来，counts 对得上",
          len(geo.pads) == 2 and len(geo.tracks) == 1 and len(geo.outlines) == 1,
          f"pads={len(geo.pads)} tracks={len(geo.tracks)} outlines={len(geo.outlines)}")

    # **AGENTS §3.20 的判据**：R 模式原点是左上角、向 -Y 延伸 → y 区间 [y-h, y]
    box = geo.outline_box
    inside = box is not None and box[1] <= 0.0 and box[3] >= 1574.8 - 1e-6
    check("1b. 板框 R 模式：y 区间是 [y-h, y]，覆盖 [0, 1574.8]",
          inside, f"box={box}")

    pads_inside = all(box[0] <= p.x <= box[2] and box[1] <= p.y <= box[3]
                      for p in geo.pads) if box else False
    check("1c. 全部焊盘都落在板框内（板框写反时这条会红）", pads_inside,
          f"pads={[(p.x, p.y) for p in geo.pads]}")

    # POLYGON 焊盘：手册 §22 踩过 NaN
    poly = _payload()
    poly["components"][0]["pads"][0].update(
        {"shape": "POLYGON", "hw": 20.0, "hh": 10.0, "assumed": False})
    geo2 = pcb_geo.parse(poly)
    pad = geo2.pads[0]
    check("1d. POLYGON 焊盘：半宽是有限数，不是 NaN（手册 §22 的坑）",
          pad.hw == 20.0 and pad.hh == 10.0 and pad.half == 10.0,
          f"hw={pad.hw} hh={pad.hh} half={pad.half}")

    # 形状查不出来：不许当成"没有形状"，要记 assumed 并给兜底半宽
    unknown = _payload()
    unknown["components"][0]["pads"][0].update(
        {"shape": "UNKNOWN", "hw": 0.0, "hh": 0.0, "assumed": True})
    geo3 = pcb_geo.parse(unknown)
    check("1e. 形状查不到 → assumed 计数 + 兜底半宽（不静默按 0 算）",
          geo3.assumed_shape == 1
          and geo3.pads[0].hw == pcb_geo.DEFAULT_HALF_MIL,
          f"assumed={geo3.assumed_shape} hw={geo3.pads[0].hw}")

    check("1f. read_js 生成的代码里没有未替换的占位符",
          "__" not in pcb_geo.read_js("abc"),
          f"chars={len(pcb_geo.read_js('abc'))}")


# ---------------------------------------------------------------------------
# 2. 连通性判定
# ---------------------------------------------------------------------------


def part_connectivity() -> None:
    geo = pcb_geo.parse(_payload())
    got = pcb_geo.connectivity(geo)
    check("2a. 两焊盘 + 一条走线 → connected",
          got["status"] == "ok" and got["connected"] == ["N1"], f"{got['status']}")

    # 走线只走一半 → 必须判 split（手册 §22 的「差 39 mil 没搭上」）
    broken = _payload()
    broken["tracks"][0]["x2"] = 700.0
    got2 = pcb_geo.connectivity(pcb_geo.parse(broken))
    check("2b. 走线差一截没搭上 → split（不是 connected）",
          got2["status"] == "not_ready" and got2["split"] == ["N1"]
          and got2["detail"]["N1"]["groups"] == 2,
          f"split={got2['split']} groups={got2['detail']['N1']['groups']}")

    # 跨层：同一条网在两层各一段、没有过孔 → 断开。
    # 第二个焊盘必须是**通孔**（hole=True），否则底层走线本来就连不上它
    # —— 那不是判定器的问题，是夹具画错了（判定器按层判是对的）。
    two_layer = _geometry([("N1", [(400.0, 500.0), (1000.0, 500.0)])],
                          tracks=[pcb_geo.Track(net="N1", x1=400, y1=500,
                                                x2=700, y2=500, layer=1),
                                  pcb_geo.Track(net="N1", x1=700, y1=500,
                                                x2=1000, y2=500, layer=2)])
    two_layer.pads[1].hole = True
    got3 = pcb_geo.connectivity(two_layer)
    check("2c. 跨层但没过孔 → split（层不同不算连）",
          got3["status"] == "not_ready", f"{got3['status']} split={got3['split']}")

    # 同一份几何 + 一个过孔 → 必须变 connected
    two_layer.vias.append(pcb_geo.Via(net="N1", x=700, y=500, diameter=20))
    got4 = pcb_geo.connectivity(two_layer)
    check("2d. 加了过孔 → connected（过孔是跨层的唯一通路）",
          got4["status"] == "ok", f"{got4['status']}")

    # 空输入：不能是 ok（AGENTS §0.1）
    empty = pcb_geo.connectivity(pcb_geo.BoardGeometry())
    check("2e. 板上 0 条网络 → not_ready（空输入不算通过）",
          empty["status"] == "not_ready", empty["basis"][:60])

    # 单焊盘网络不进分母，单列到 single
    single = _geometry([("N1", [(400.0, 500.0), (1000.0, 500.0)]),
                        ("ONLY1", [(1500.0, 900.0)])],
                       tracks=[pcb_geo.Track(net="N1", x1=400, y1=500,
                                             x2=1000, y2=500, layer=1)])
    got5 = pcb_geo.connectivity(single)
    check("2f. 单焊盘网络不进分母，单列进 single",
          got5["status"] == "ok" and got5["single"] == ["ONLY1"]
          and got5["checked"] == 1,
          f"checked={got5['checked']} single={got5['single']}")

    merged = pcb_geo.summarize([got4, got2])
    check("2g. summarize：任一块板不 OK → 整体 not_ready",
          merged["status"] == "not_ready" and merged["boards"] == 2, str(merged["note"]))


# ---------------------------------------------------------------------------
# 3. 自研布线器
# ---------------------------------------------------------------------------


def _angle_ok(segments: list[router.Segment]) -> bool:
    """只允许 0/45/90/135 度（8 邻域 A* 的自然结果）。"""
    for s in segments:
        if (s.x1, s.y1) == (s.x2, s.y2):
            return False
        if abs(s.x2 - s.x1) < 1e-6 or abs(s.y2 - s.y1) < 1e-6:
            continue
        if abs(abs(s.x2 - s.x1) - abs(s.y2 - s.y1)) > 1e-3:
            return False
    return True


def part_router() -> None:
    geo = _geometry([("N1", [(400.0, 500.0), (1000.0, 500.0)]),
                     ("GND", [(420.0, 600.0), (1020.0, 600.0)])])
    planned = router.plan(geo)
    check("3a. 两条网都能规划出来",
          not planned.unrouted and set(planned.routed) == {"N1", "GND"},
          f"routed={planned.routed} unrouted={planned.unrouted}")
    check("3b. 线段角度只有 0/45/90/135（不需要再倒角）",
          _angle_ok(planned.segments), f"segments={len(planned.segments)}")
    box = geo.outline_box
    inside = all(box[0] <= s.x1 <= box[2] and box[0] <= s.x2 <= box[2]
                 and box[1] <= s.y1 <= box[3] and box[1] <= s.y2 <= box[3]
                 for s in planned.segments) if box else False
    check("3c. 线段全部落在板框内（跑到板外就是板框/坐标系算错了）",
          inside, f"box={box}")

    # 规划出来的线**喂回判定器必须判通** —— 这是"规划对不对"的唯一硬判据
    replay = pcb_geo.BoardGeometry(board="probe")
    replay.pads = list(geo.pads)
    replay.nets = list(geo.nets)
    replay.outlines = list(geo.outlines)
    for s in planned.segments:
        replay.tracks.append(pcb_geo.Track(net=s.net, x1=s.x1, y1=s.y1,
                                           x2=s.x2, y2=s.y2, layer=s.layer,
                                           width=s.width))
    for v in planned.vias:
        replay.vias.append(pcb_geo.Via(net=v.net, x=v.x, y=v.y, diameter=v.diameter))
    verdict = pcb_geo.connectivity(replay)
    check("3d. 布线结果过自己的几何判定器 → 全连通",
          verdict["status"] == "ok", f"split={verdict['split']}")

    # 没有板框：不许硬编一个坐标，要报"没有边界"
    no_outline = _geometry([("N1", [(400.0, 500.0), (1000.0, 500.0)])])
    no_outline.outlines = []
    planned2 = router.plan(no_outline)
    check("3e. 没有板框 → unrouted + 说明（不硬编坐标）",
          bool(planned2.unrouted) and bool(planned2.notes),
          f"{planned2.notes[:1]}")

    # 板子过大：栅格超限要明说，不是静默返回空
    big = _geometry([("N1", [(400.0, 500.0), (1000.0, 500.0)])],
                    outline=["R", 0.0, 40000.0, 40000.0, 40000.0, 0, 0])
    planned3 = router.plan(big, rules=router.Rules(max_cells=5000))
    check("3f. 栅格超限 → 明确报超限并给尺寸（不是静默空结果）",
          bool(planned3.unrouted) and any("超限" in n for n in planned3.notes),
          f"{planned3.notes[:1]}")

    # 缝合过孔：不许压在别人的走线上
    crowded = _geometry([("GND", [(400.0, 500.0), (1000.0, 500.0)]),
                         ("SIG", [(300.0, 900.0), (1700.0, 900.0)])],
                        tracks=[pcb_geo.Track(net="SIG", x1=280, y1=760,
                                              x2=1700, y2=760, layer=1, width=10)])
    sites = router.stitch_sites(crowded, "GND", count=6, inset_mil=40.0)
    too_close = [s for s in sites
                 if abs(s[1] - 760.0) < 20.0]
    check("3g. 缝合过孔避开别的网络的走线",
          bool(sites) and not too_close,
          f"sites={len(sites)} 距 SIG 过近={len(too_close)}")


def _layout_design(*, close: bool, connector_inside: bool) -> Any:
    from hw_velxio.design.model import Board, DesignModel, Part, Placement

    d = DesignModel(title="layout")
    d.board = Board(kind="generic", name="b",
                    outline=[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)],
                    size_mm={"w": 40, "h": 30}, thickness_mm=1.6)
    gap = 0.6 if close else 4.0
    d.add_part(Part(ref="R1", kind="resistor", lcsc="C1523",
                    size_mm={"w": 1.0, "h": 0.5},
                    placement=Placement(x_mm=10.0, y_mm=10.0), pins=["1", "2"]))
    d.add_part(Part(ref="R2", kind="resistor", lcsc="C1523",
                    size_mm={"w": 1.0, "h": 0.5},
                    placement=Placement(x_mm=10.0 + gap, y_mm=10.0),
                    pins=["1", "2"]))
    d.add_part(Part(ref="J1", kind="connector", lcsc="C1523",
                    size_mm={"w": 6.0, "h": 4.0},
                    placement=Placement(
                        x_mm=20.0 if connector_inside else 1.8, y_mm=15.0),
                    pins=["1", "2"]))
    return d


def part_layout() -> None:
    """P30 布局规则：连接器出板、中心距、包围盒重叠。"""
    clean = pcb_layout.audit(_layout_design(close=False, connector_inside=False))
    check("4a. 正常摆放松 = ok", clean.status == "ok",
          f"blockers={[f.code for f in clean.blockers]}")

    close = pcb_layout.audit(_layout_design(close=True, connector_inside=False))
    check("4b. 两个无源件挨太近 → overlap/too_close（不是 ok）",
          close.status != "ok" and {f.code for f in close.blockers} & {"overlap", "too_close"},
          f"{[f.code for f in close.blockers]}")

    inside = pcb_layout.audit(_layout_design(close=False, connector_inside=True))
    check("4c. 连接器整个在板内 → connector_in_board（线缆插不进去）",
          "connector_in_board" in {f.code for f in inside.blockers},
          f"{[f.code for f in inside.blockers]}")

    d = _layout_design(close=True, connector_inside=True)
    for board in d.board_list():
        plan = pcb_layout.autoplace(d, board=board)
        for part in d.parts:
            if d.board_of(part) is not board:
                continue
            pos = plan.get(str(part.ref))
            if pos and part.placement is not None:
                part.placement.x_mm, part.placement.y_mm = pos
    after = pcb_layout.audit(d)
    check("4d. autoplace 之后 → ok（重排真的守规则，不是把违规挪个位置）",
          after.status == "ok", f"blockers={[f.code for f in after.blockers]}")

    from hw_velxio.design.model import Board, DesignModel

    empty = DesignModel(title="empty")
    empty.board = Board(kind="generic", name="b", size_mm={"w": 10, "h": 10})
    check("4e. 空设计 → not_ready（空输入不算通过）",
          pcb_layout.audit(empty).status == "not_ready", "")


def part_channel() -> None:
    """P24 通道：两条腿都要能如实报不可用。"""
    got = lceda_channel.probe(exe=[])
    check("5a. probe 同时报 cli 与 bridge，且 missing 是列表",
          "cli" in got and "bridge" in got
          and isinstance(got["bridge"]["missing"], list),
          f"recommended={got['recommended']!r}")

    bridge_ok = bool(got["bridge"]["edaConnected"])
    if bridge_ok:
        check("5b. Bridge 已连上（本机环境）", True, "跳过不可用分支")
    else:
        raised = False
        msg = ""
        try:
            lceda_channel.open_channel("bridge")
        except lceda_channel.ChannelError as exc:
            raised = True
            msg = str(exc)
        check("5b. Bridge 不可用时 open_channel('bridge') **抛错**并给指引"
              "（不偷偷降级成 CLI）",
              raised and "Bridge" in msg, msg[:120])

    raised = False
    try:
        lceda_channel.open_channel("carrier-pigeon")
    except lceda_channel.ChannelError:
        raised = True
    check("5c. 未知通道名 → 抛 ChannelError（不猜）", raised, "")


def main() -> int:
    print("=== P26/P27 几何回读 + 连通性 + 自研布线 + 布局 + 通道（离线） ===")
    part_parse()
    part_connectivity()
    part_router()
    part_layout()
    part_channel()
    print(f"\n=== {_checks - len(_failures)} 通过 / {len(_failures)} 失败 "
          f"/ {_checks} 项 ===")
    if _failures:
        for name in _failures:
            print(f"  [FAIL] {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())