"""PCB 几何回读与**几何连通性判定**（不依赖 DRC、不依赖立创内部网表）。

【这一层解决什么问题】

「这条网络通不通」本来有三个可能的答案来源，三个都不可靠：

1. **立创的 DRC `Connection Error`** —— 实测**不认 API 创建的过孔**构成的跨层
   连接（手册 §18.1 的对照实验：按「是否用过孔」给网络分组，结论一目了然）。
   拿它当判据会把"其实通了"判成"没通"，反过来也可能漏。
2. **`pcb_Net.getNetlist()`** —— 那是**原理图推出来的期望网表**，不是铜皮上
   实际连成什么样；手工摆放的 PCB 它还会报 Netlist Error（手册 §18.2）。
3. **布线器的自述** —— `successNetsCount` 只描述"这次调用做了什么"，
   布完再调一次就是 0/N 而走线一段没少（AGENTS §3.20）。

所以这里自己算：按**几何接触**建并查集。焊盘/走线/过孔两两之间是否接触，
用距离判（点-线段、线段-线段），跨层靠过孔和通孔焊盘。手册 §21 的 40 行 JS
是原型，这里移植到 Python 并补上了三处它踩过的坑：

* **焊盘形状有 RECT 也有 POLYGON**：一律拿 `shape[1]/2` 当半宽，POLYGON 会得到
  `NaN` → 明明连着却报断开（手册 §22 第三轮）。
* **同一器件多焊盘共享一个 designator**：报告里必须带焊盘号，否则"哪两个断开"
  说不清。
* **悬空网络（<2 焊盘）不算通**：没有可连的对象，"connected" 是假绿。

判定纪律：三态 `connected` / `split` / `unknown`，**空输入不返回 connected**。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = [
    "BoardGeometry",
    "Pad",
    "Track",
    "Via",
    "connectivity",
    "parse",
    "read_js",
    "summarize",
]

#: 接触判定容差（mil）。1 mil ≈ 0.0254 mm；比 PCB 单位精度大一档，
#: 用来吸收浮点噪声。**不要放大到"看起来接近就算连"**——那是造假连通。
TOUCH_EPS_MIL = 1.0

#: 焊盘形状兜底半宽（mil）。查不到形状时用它，并在报告里标 `assumed_shape`。
DEFAULT_HALF_MIL = 15.0

#: 立创的 PCB 单位是 1 mil（`lceda.PCB_UNIT_MM = 0.0254`）。
_MM_TO_MIL = 1.0 / 0.0254


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------


@dataclass
class Pad:
    """一个焊盘。坐标单位 mil。"""

    net: str
    number: str
    ref: str
    x: float
    y: float
    layer: int
    hole: bool = False
    hw: float = DEFAULT_HALF_MIL
    hh: float = DEFAULT_HALF_MIL
    shape: str = ""

    @property
    def half(self) -> float:
        """保守半宽：取 x/y 里**较小**的那个（接触判定宁可判不连，不判错连）。"""
        return min(self.hw, self.hh)

    @property
    def name(self) -> str:
        return f"{self.ref}.{self.number}" if self.ref else f"pad{self.number}"

    def to_dict(self) -> dict[str, Any]:
        return {"net": self.net, "ref": self.ref, "number": self.number,
                "x": round(self.x, 3), "y": round(self.y, 3), "layer": self.layer,
                "hole": self.hole, "hw": round(self.hw, 3), "hh": round(self.hh, 3),
                "shape": self.shape}


@dataclass
class Track:
    net: str
    x1: float
    y1: float
    x2: float
    y2: float
    layer: int
    width: float = 10.0

    def to_dict(self) -> dict[str, Any]:
        return {"net": self.net, "x1": round(self.x1, 2), "y1": round(self.y1, 2),
                "x2": round(self.x2, 2), "y2": round(self.y2, 2),
                "layer": self.layer, "width": self.width}


@dataclass
class Via:
    net: str
    x: float
    y: float
    diameter: float = 20.0

    def to_dict(self) -> dict[str, Any]:
        return {"net": self.net, "x": round(self.x, 2), "y": round(self.y, 2),
                "diameter": self.diameter}


@dataclass
class Pour:
    """一块铺铜（覆铜边框）。`rect` 是它覆盖的矩形 `(x0, y0, x1, y1)`，mil。

    为什么它必须进连通性判定：真实板上 **GND 是靠铺铜连的，不是靠走线**。
    实测：12 个焊盘的 GND 网，引擎只布了一部分（留下 4 个孤岛），而铺铜一上
    就整片连起来了。如果判定不看铺铜，就会把一块**能打板的板**判成断开。
    """

    net: str
    layer: int
    rect: tuple[float, float, float, float] | None = None
    has_copper: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"net": self.net, "layer": self.layer,
                "rect": [round(v, 1) for v in self.rect] if self.rect else None,
                "has_copper": self.has_copper}


@dataclass
class BoardGeometry:
    """一块板的全部几何事实。`pcb_geo` 的唯一产物形状。"""

    board: str = ""
    pcb_uuid: str = ""
    pads: list[Pad] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    vias: list[Via] = field(default_factory=list)
    pours: list[Pour] = field(default_factory=list)
    outlines: list[list[Any]] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)
    ratlines: int | None = None
    doc_type: int | None = None
    doc_tries: int = 0
    assumed_shape: int = 0
    errors: dict[str, str] = field(default_factory=dict)

    # -- 派生 ---------------------------------------------------------
    @property
    def outline_box(self) -> tuple[float, float, float, float] | None:
        """板框包围盒 `(x0, y0, x1, y1)`。

        `R` 模式的 `(x, y)` 是**左上角**、矩形向 −Y 延伸（AGENTS §3.20），
        所以这里把区间算成 `[y-h, y]`，而不是 `[y, y+h]`。折线模式直接取点。
        """
        xs: list[float] = []
        ys: list[float] = []
        for src in self.outlines:
            if not src:
                continue
            if str(src[0]).upper() == "R" and len(src) >= 5:
                try:
                    x, y, w, h = (float(src[1]), float(src[2]),
                                  float(src[3]), float(src[4]))
                except (TypeError, ValueError):
                    continue
                xs.extend([x, x + w])
                ys.extend([y - h, y])
            else:
                nums = [float(v) for v in src if isinstance(v, (int, float))]
                xs.extend(nums[0::2])
                ys.extend(nums[1::2])
        if not xs or not ys:
            return None
        return (min(xs), min(ys), max(xs), max(ys))

    def nets_with_pads(self, min_pads: int = 2) -> list[str]:
        counts: dict[str, int] = {}
        for pad in self.pads:
            if pad.net:
                counts[pad.net] = counts.get(pad.net, 0) + 1
        return sorted(n for n, c in counts.items() if c >= min_pads)

    def to_dict(self) -> dict[str, Any]:
        box = self.outline_box
        return {
            "board": self.board,
            "pcb_uuid": self.pcb_uuid,
            "doc_type": self.doc_type,
            "doc_tries": self.doc_tries,
            "nets": self.nets,
            "ratlines": self.ratlines,
            "outlines": self.outlines,
            "outline_box": [round(v, 2) for v in box] if box else None,
            "pads": [p.to_dict() for p in self.pads],
            "tracks": [t.to_dict() for t in self.tracks],
            "vias": [v.to_dict() for v in self.vias],
            "pours": [p.to_dict() for p in self.pours],
            "counts": {"pads": len(self.pads), "tracks": len(self.tracks),
                       "vias": len(self.vias), "pours": len(self.pours),
                       "outlines": len(self.outlines),
                       "assumed_shape": self.assumed_shape},
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# 读（JS 在立创里跑，解析在 Python）
# ---------------------------------------------------------------------------

#: 换板之后必须等文档真的激活（实测 `openDocument` 返回 true 之后还要几秒）。
_JS_DOC_POLL = r"""
const docPoll = {tries: 0, docType: null};
for (let i = 0; i < 48; i++) {
  await new Promise(r => setTimeout(r, 250));
  let d = null;
  try { d = await eda.dmt_SelectControl.getCurrentDocumentInfo(); } catch (e) { d = null; }
  docPoll.docType = d ? d.documentType : null;
  docPoll.tries = i + 1;
  if (docPoll.docType === 3) break;
}
"""

#: 焊盘半宽从**形状源数据**推。`RECT` 是 `[x, y]`，`POLYGON` 是点表，
#: `CIRCLE` 是半径。查不出来就落 `DEFAULT_HALF_MIL` 并记 `assumed`。
_JS_PAD_SHAPE = r"""
function padHalf(p) {
  const sh = p.pad;
  if (Array.isArray(sh)) {
    const kind = String(sh[0] || '').toUpperCase();
    if (kind === 'RECT' && typeof sh[1] === 'number' && typeof sh[2] === 'number') {
      return {hw: sh[1] / 2, hh: sh[2] / 2, shape: 'RECT', assumed: false};
    }
    if (kind === 'CIRCLE' && typeof sh[1] === 'number') {
      return {hw: sh[1] / 2, hh: sh[1] / 2, shape: 'CIRCLE', assumed: false};
    }
    if (kind === 'POLYGON' && Array.isArray(sh[1])) {
      const nums = sh[1].filter(v => typeof v === 'number');
      if (nums.length >= 6) {
        let a = Infinity, b = -Infinity, c = Infinity, d = -Infinity;
        for (let i = 0; i + 1 < nums.length; i += 2) {
          a = Math.min(a, nums[i]); b = Math.max(b, nums[i]);
          c = Math.min(c, nums[i + 1]); d = Math.max(d, nums[i + 1]);
        }
        return {hw: (b - a) / 2, hh: (d - c) / 2, shape: 'POLYGON', assumed: false};
      }
    }
  }
  return {hw: 0, hh: 0, shape: 'UNKNOWN', assumed: true};
}
"""


def read_js(pcb_uuid: str) -> str:
    """生成「把一块板的几何全读回来」的 JS。传 ``pcb_uuid`` 空串则读当前文档。

    纯函数（可单测）：只拼字符串，不碰立创。
    """
    open_line = ("await eda.dmt_EditorControl.openDocument(%s);"
                 % _json_str(pcb_uuid)) if pcb_uuid else ""
    return f"""
const out = {{ok: true, errors: {{}}}};
{open_line}
{_JS_DOC_POLL}
out.docType = docPoll.docType;
out.docTries = docPoll.tries;
_JS_PAD_SHAPE_placeholder
const comps = [];
try {{
  const ids = (await eda.pcb_PrimitiveComponent.getAllPrimitiveId()) || [];
  const list = (await eda.pcb_PrimitiveComponent.get(ids)) || [];
  for (const c of list) {{
    const row = {{d: c.designator || '', x: c.x, y: c.y, layer: c.layer, rot: c.rotation}};
    row.pads = [];
    let ps = [];
    try {{ ps = (await eda.pcb_PrimitiveComponent.getAllPinsByPrimitiveId(c.primitiveId)) || []; }}
    catch (e) {{ out.errors.pads = String((e && e.message) || e); }}
    for (const p of ps) {{
      const half = padHalf(p);
      row.pads.push({{net: p.net || '', num: p.padNumber, x: p.x, y: p.y,
                     layer: p.layer, hole: !!p.hole,
                     hw: half.hw, hh: half.hh, shape: half.shape,
                     assumed: half.assumed}});
    }}
    comps.push(row);
  }}
}} catch (e) {{ out.errors.comps = String((e && e.message) || e); }}
out.components = comps;

out.nets = [];
try {{ out.nets = (await eda.pcb_Net.getAllNetName()) || []; }}
catch (e) {{ out.errors.nets = String((e && e.message) || e); }}

out.tracks = [];
for (const n of out.nets) {{
  let ls = [];
  try {{ ls = (await eda.pcb_PrimitiveLine.getAll(n)) || []; }}
  catch (e) {{ out.errors.tracks = String((e && e.message) || e); continue; }}
  for (const l of ls) {{
    out.tracks.push({{net: n, x1: l.startX, y1: l.startY, x2: l.endX, y2: l.endY,
                     layer: l.layer, width: l.lineWidth}});
  }}
}}

out.vias = [];
try {{
  const vs = (await eda.pcb_PrimitiveVia.getAll()) || [];
  for (const v of vs) out.vias.push({{net: v.net || '', x: v.x, y: v.y, diameter: v.diameter}});
}} catch (e) {{ out.errors.vias = String((e && e.message) || e); }}

out.outlines = [];
try {{
  const ids = (await eda.pcb_PrimitivePolyline.getAllPrimitiveId()) || [];
  const pls = (await eda.pcb_PrimitivePolyline.get(ids)) || [];
  for (const p of pls) {{
    try {{
      const poly = p.getState_Polygon();
      out.outlines.push(poly && typeof poly.getSource === 'function'
        ? poly.getSource() : null);
    }} catch (e) {{ out.errors.outline = String((e && e.message) || e); }}
  }}
}} catch (e) {{ out.errors.outlines = String((e && e.message) || e); }}

out.pours = [];
try {{
  const ids = (await eda.pcb_PrimitivePour.getAllPrimitiveId()) || [];
  const list = (await eda.pcb_PrimitivePour.get(ids)) || [];
  for (const p of list) {{
    const row = {{net: '', layer: null, source: null, copper: false}};
    try {{ row.net = p.getState_Net() || ''; }} catch (e) {{}}
    try {{ row.layer = p.getState_Layer(); }} catch (e) {{}}
    try {{
      const cp = p.getState_ComplexPolygon();
      if (cp && typeof cp.getSource === 'function') row.source = cp.getSource();
      else if (cp) row.source = JSON.parse(JSON.stringify(cp));
    }} catch (e) {{ out.errors.pourSource = String((e && e.message) || e); }}
    try {{
      const cr = await p.getCopperRegion();
      row.copper = !!(cr && (Array.isArray(cr.pourFills)
        ? cr.pourFills.length : true));
    }} catch (e) {{}}
    out.pours.push(row);
  }}
}} catch (e) {{ out.errors.pours = String((e && e.message) || e); }}

try {{
  const page = await eda.pcb_Document.getRatlines({{}});
  out.ratlines = page && page.total !== undefined ? page.total
    : ((page && page.items) || []).length;
}} catch (e) {{ out.errors.ratlines = String((e && e.message) || e); }}
return out;
""".replace("_JS_PAD_SHAPE_placeholder", _JS_PAD_SHAPE)


def _source_rect(source: Any) -> tuple[float, float, float, float] | None:
    """铺铜/板框的源数据 → 覆盖矩形 `(x0, y0, x1, y1)`。

    注意 `R` 模式的 `(x, y)` 是**左上角**、向 -Y 延伸（AGENTS §3.20）——
    这条在铺铜上同样成立，写错会让铺铜落在板外（手册 §19.1 的后果链）。
    """
    if not source or not isinstance(source, list):
        return None
    node: Any = source
    # 复杂多边形可能包一层：`[['R', ...]]` / `[['x0','y0','L',...]]`
    if node and isinstance(node[0], list):
        node = node[0]
    if not node:
        return None
    if str(node[0]).upper() == "R" and len(node) >= 5:
        try:
            x, y, w, h = (float(node[1]), float(node[2]),
                          float(node[3]), float(node[4]))
        except (TypeError, ValueError):
            return None
        xs = sorted((x, x + w))
        ys = sorted((y - h, y))
        return (xs[0], ys[0], xs[1], ys[1])
    nums = [float(v) for v in node if isinstance(v, (int, float))]
    if len(nums) >= 6:
        xs = nums[0::2]
        ys = nums[1::2]
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def _rect_overlap(a: tuple[float, float, float, float],
                  b: tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _json_str(value: str) -> str:
    import json

    return json.dumps(str(value), ensure_ascii=False)


def parse(payload: dict[str, Any], *, board: str = "",
          pcb_uuid: str = "") -> BoardGeometry:
    """把 JS 的原始返回转成 `BoardGeometry`。

    **缺字段不抛异常**：几何读不全时返回已读到的部分 + `errors`，
    由调用方判 `not_ready`。抛异常会让"读失败"伪装成"发射失败"。
    """
    geo = BoardGeometry(board=board, pcb_uuid=pcb_uuid)
    raw = payload or {}
    geo.doc_type = raw.get("docType")
    geo.doc_tries = int(raw.get("docTries") or 0)
    geo.ratlines = raw.get("ratlines")
    geo.nets = [str(n) for n in (raw.get("nets") or [])]
    geo.errors = {str(k): str(v) for k, v in (raw.get("errors") or {}).items()}
    for src in (raw.get("outlines") or []):
        if isinstance(src, list):
            geo.outlines.append(src)
    for c in (raw.get("components") or []):
        ref = str(c.get("d") or "")
        for p in (c.get("pads") or []):
            assumed = bool(p.get("assumed"))
            if assumed:
                geo.assumed_shape += 1
            geo.pads.append(Pad(
                net=str(p.get("net") or ""),
                number=str(p.get("num") if p.get("num") is not None else ""),
                ref=ref,
                x=float(p.get("x") or 0.0),
                y=float(p.get("y") or 0.0),
                layer=int(p.get("layer") or 1),
                hole=bool(p.get("hole")),
                hw=float(p.get("hw") or 0.0) or DEFAULT_HALF_MIL,
                hh=float(p.get("hh") or 0.0) or DEFAULT_HALF_MIL,
                shape=str(p.get("shape") or ""),
            ))
    for t in (raw.get("tracks") or []):
        try:
            geo.tracks.append(Track(
                net=str(t.get("net") or ""),
                x1=float(t.get("x1") or 0.0), y1=float(t.get("y1") or 0.0),
                x2=float(t.get("x2") or 0.0), y2=float(t.get("y2") or 0.0),
                layer=int(t.get("layer") or 1),
                width=float(t.get("width") or 0.0) or 10.0,
            ))
        except (TypeError, ValueError):
            continue
    for p in (raw.get("pours") or []):
        try:
            rect = _source_rect(p.get("source"))
            geo.pours.append(Pour(
                net=str(p.get("net") or ""),
                layer=int(p.get("layer") or 1),
                rect=rect,
                has_copper=bool(p.get("copper")),
            ))
        except (TypeError, ValueError):
            continue
    for v in (raw.get("vias") or []):
        try:
            geo.vias.append(Via(
                net=str(v.get("net") or ""),
                x=float(v.get("x") or 0.0), y=float(v.get("y") or 0.0),
                diameter=float(v.get("diameter") or 0.0) or 20.0,
            ))
        except (TypeError, ValueError):
            continue
    return geo


# ---------------------------------------------------------------------------
# 几何工具
# ---------------------------------------------------------------------------


def _dist_point_segment(px: float, py: float, x1: float, y1: float,
                        x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    lsq = dx * dx + dy * dy
    if lsq <= 1e-12:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / lsq
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _segments_distance(a: Track, b: Track) -> float:
    return min(
        _dist_point_segment(a.x1, a.y1, b.x1, b.y1, b.x2, b.y2),
        _dist_point_segment(a.x2, a.y2, b.x1, b.y1, b.x2, b.y2),
        _dist_point_segment(b.x1, b.y1, a.x1, a.y1, a.x2, a.y2),
        _dist_point_segment(b.x2, b.y2, a.x1, a.y1, a.x2, a.y2),
    )


def _rect_gap(pad: Pad, px: float, py: float) -> float:
    """点到**矩形焊盘**的距离（落在矩形内是 0）。

    为什么不能只用 `min(hw, hh)`：0402 的焊盘是 16×17 mil，用短边当半径
    会把"从长边方向贴上来"的走线判成没接触 —— 手册 §22 第一轮就是这么
    把"其实连着"报成断开的（只不过那次是 POLYGON 取到 NaN）。矩形焊盘
    必须按**矩形**算距离。
    """
    dx = max(abs(px - pad.x) - pad.hw, 0.0)
    dy = max(abs(py - pad.y) - pad.hh, 0.0)
    return math.hypot(dx, dy)


def _seg_rect_gap(pad: Pad, x1: float, y1: float,
                  x2: float, y2: float) -> float:
    """线段到矩形焊盘的最短距离。0 表示相交/落在里面。"""
    if _rect_gap(pad, x1, y1) <= 1e-9 or _rect_gap(pad, x2, y2) <= 1e-9:
        return 0.0
    # 线段穿过矩形（四个角点都在线段两侧时才算穿过，用边相交判更直接）
    corners = [(pad.x - pad.hw, pad.y - pad.hh), (pad.x + pad.hw, pad.y - pad.hh),
               (pad.x + pad.hw, pad.y + pad.hh), (pad.x - pad.hw, pad.y + pad.hh)]
    for i in range(4):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % 4]
        if _segments_intersect(x1, y1, x2, y2, ax, ay, bx, by):
            return 0.0
    return min(_dist_point_segment(cx, cy, x1, y1, x2, y2) for cx, cy in corners)


def _segments_intersect(ax: float, ay: float, bx: float, by: float,
                        cx: float, cy: float, dx: float, dy: float) -> bool:
    def cross(ox: float, oy: float, px: float, py: float) -> float:
        return (px - ox) * (dy - cy) - (py - oy) * (dx - cx)

    d1 = cross(ax, ay, bx, by)
    d2 = cross(ax, ay, dx, dy)
    d3 = cross(cx, cy, ax, ay)
    d4 = cross(cx, cy, bx, by)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return False


def _rect_rect_gap(a: Pad, b: Pad) -> float:
    dx = max(abs(a.x - b.x) - (a.hw + b.hw), 0.0)
    dy = max(abs(a.y - b.y) - (a.hh + b.hh), 0.0)
    return math.hypot(dx, dy)


def _layers_touch(a: Any, b: Any) -> bool:
    """跨层是否可能接触：通孔（hole）两面都通，否则必须同层。"""
    if isinstance(a, Pad) and a.hole:
        return True
    if isinstance(b, Pad) and b.hole:
        return True
    return a.layer == b.layer


# ---------------------------------------------------------------------------
# 连通性判定（并查集）
# ---------------------------------------------------------------------------


def connectivity(geo: BoardGeometry, *, min_pads: int = 2,
                 eps: float = TOUCH_EPS_MIL) -> dict[str, Any]:
    """按几何接触判每条网络的连通性。

    返回::

        {
          "status": "ok" | "not_ready",
          "checked": 判了几条网,
          "connected": [...], "split": [...], "single": [...],
          "detail": {net: {"groups": n, "pads": [...], "members": [[...]]}},
          "basis": "...",
        }

    **三态**：`status` 只有"全都通了"才是 `ok`；一条网不通就是 `not_ready`；
    没有可判的网络（0 条 ≥min_pads 的网）也是 `not_ready` —— 空输入不算通过。
    """
    nets = geo.nets_with_pads(min_pads)
    single = [n for n in geo.nets if n and n not in nets]
    if not nets:
        return {
            "status": "not_ready",
            "checked": 0,
            "connected": [], "split": [], "single": single,
            "detail": {},
            "basis": "板上没有 ≥2 个焊盘的网络，连通性无从判定（空输入不是通过）",
        }

    items: list[tuple[str, Any]] = []
    for pad in geo.pads:
        if pad.net in nets:
            items.append((pad.net, pad))
    for track in geo.tracks:
        if track.net in nets:
            items.append((track.net, track))
    for via in geo.vias:
        if via.net in nets:
            items.append((via.net, via))
    # **铺铜也要进来**：真实板上 GND 靠铺铜连，不靠走线。
    # 有铜（`hasCopper`）的铺铜按"覆盖矩形 + 层"当一个平面节点，
    # 同网络的焊盘/走线/过孔只要落在矩形里就算接到这片平面上。
    planes: list[tuple[str, int, tuple[float, float, float, float]]] = []
    for pour in geo.pours:
        if pour.net in nets and pour.has_copper and pour.rect:
            planes.append((pour.net, pour.layer, pour.rect))

    n = len(items)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    def touch(a: Any, b: Any) -> bool:
        if type(a) is Pad and type(b) is Pad:
            return _layers_touch(a, b) and _rect_rect_gap(a, b) <= eps
        if type(a) is Pad and type(b) is Track:
            return _layers_touch(a, b) and (
                _seg_rect_gap(a, b.x1, b.y1, b.x2, b.y2) <= b.width / 2 + eps)
        if type(a) is Track and type(b) is Pad:
            return touch(b, a)
        if type(a) is Track and type(b) is Track:
            return _layers_touch(a, b) and (
                _segments_distance(a, b) <= max(a.width, b.width) / 2 + eps)
        if type(a) is Track and type(b) is Via:
            # 过孔跨层：走线和过孔只要平面上相交就算连上
            return _dist_point_segment(b.x, b.y, a.x1, a.y1, a.x2, a.y2) <= (
                b.diameter / 2 + a.width / 2 + eps)
        if type(a) is Via and type(b) is Track:
            return touch(b, a)
        if type(a) is Pad and type(b) is Via:
            # 过孔是圆，对矩形焊盘用矩形距离减半径
            return max(0.0, _rect_gap(a, b.x, b.y) - b.diameter / 2) <= eps
        if type(a) is Via and type(b) is Pad:
            return touch(b, a)
        if type(a) is Via and type(b) is Via:
            return math.hypot(a.x - b.x, a.y - b.y) <= a.diameter / 2 + b.diameter / 2 + eps
        return False

    for i in range(n):
        net_i, obj_i = items[i]
        for j in range(i + 1, n):
            net_j, obj_j = items[j]
            if net_i != net_j:
                continue
            if touch(obj_i, obj_j):
                union(i, j)

    # 平面节点接进来的两两合并：同网络的任意两个落在同一片平面上的图元，
    # 都通过这片铜连在一起。
    for net_name, layer, rect in planes:
        members = []
        for idx, (net_i, obj) in enumerate(items):
            if net_i != net_name:
                continue
            if isinstance(obj, Pad):
                if not (obj.hole or obj.layer == layer):
                    continue
                pad_rect = (obj.x - obj.hw, obj.y - obj.hh,
                            obj.x + obj.hw, obj.y + obj.hh)
                if _rect_overlap(pad_rect, rect):
                    members.append(idx)
            elif isinstance(obj, Track):
                if obj.layer != layer:
                    continue
                seg_rect = (min(obj.x1, obj.x2), min(obj.y1, obj.y2),
                            max(obj.x1, obj.x2), max(obj.y1, obj.y2))
                if _rect_overlap(seg_rect, rect):
                    members.append(idx)
            elif isinstance(obj, Via):
                if rect[0] <= obj.x <= rect[2] and rect[1] <= obj.y <= rect[3]:
                    members.append(idx)
        for other in members[1:]:
            union(members[0], other)

    connected: list[str] = []
    split: list[str] = []
    detail: dict[str, Any] = {}
    for net in nets:
        groups: dict[int, list[str]] = {}
        for idx, (net_i, obj) in enumerate(items):
            if net_i != net:
                continue
            name = obj.name if isinstance(obj, Pad) else (
                f"trk{idx}" if isinstance(obj, Track) else f"via{idx}")
            groups.setdefault(find(idx), []).append(name)
        buckets = list(groups.values())
        pads_in_net = len(groups)
        detail[net] = {
            "groups": len(buckets),
            "members": [sorted(b) for b in buckets],
        }
        if len(buckets) == 1:
            connected.append(net)
        else:
            split.append(net)
    return {
        "status": "ok" if not split else "not_ready",
        "checked": len(nets),
        "connected": connected,
        "split": split,
        "single": single,
        "detail": detail,
        "basis": (f"几何接触并查集 + 铺铜平面（eps={eps} mil）；"
                  f"有铜铺铜 {len(planes)} 片；"
                  f"焊盘 {sum(1 for p in geo.pads if p.net in nets)}、"
                  f"走线 {sum(1 for t in geo.tracks if t.net in nets)}、"
                  f"过孔 {sum(1 for v in geo.vias if v.net in nets)}；"
                  "不依赖 DRC 的 Connection Error（它不认 API 过孔）"),
    }


def summarize(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """多块板合并成一条结论。任何一块 `not_ready` → 整体 `not_ready`。"""
    rows = list(results)
    if not rows:
        return {"status": "not_ready", "boards": 0,
                "note": "没有板的几何，连通性无从判定"}
    bad = [r for r in rows if r.get("status") != "ok"]
    return {
        "status": "ok" if not bad else "not_ready",
        "boards": len(rows),
        "checked": sum(int(r.get("checked") or 0) for r in rows),
        "split": [n for r in rows for n in (r.get("split") or [])],
        "connected": [n for r in rows for n in (r.get("connected") or [])],
        "single": [n for r in rows for n in (r.get("single") or [])],
        "note": "" if not bad else f"{len(bad)}/{len(rows)} 块板有未连通网络",
    }