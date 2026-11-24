"""整机实体 —— 电机 / 螺旋桨 / 机臂 / 电池 / 外壳的**几何事实**。

## 这一层解决什么问题

`design.json` 原来只有 PCB 域：`parts` 是「要焊在板上的元件」。电机、
桨、电池、机臂**不是**焊在板上的东西，它们有质量、有安装孔、有朝向，
却没有任何字段能表达。结果 3D 只能画出一块板和一排盒子。

## 为什么单列一张表，不塞进 `parts`

要塞也能塞（`Placement.mount` 早就有 `air` 这个口子），但代价是四件事同时
变脏：

1. `validate()` 的 **足迹超界** 会把"桨盘大于板"判成错误；
2. `physical` 的**重叠检查**会把"电机压在板上"判成干涉（真实装配里这是对的）；
3. `emit` 的**器件数**与 `parity` 对账会多出装不上的东西；
4. 网表/预检/回读都按 `parts` 扫，电机没有焊盘，只会变成一堆 `no_symbol`。

和 `joints_list` 单列同一条理由：**机械/整机不该污染电气判定**。

## 与 `joints_list` 的接口

两张表用的是**同一个 `ref` 命名空间**。`DesignModel.entity(ref)` 先查
`parts` 再查这里，所以「电机 M1 与桨 P1 是 axle 关系」能正常解析，
而不是被判成悬空引用。

## 信任级

`source` 取 `model.SOURCE_ORDER`（或 `unknown`）。三条硬规矩：

* **`source` 必填**（空串按 `unknown` 记，并在 `problems()` 里报出来）；
* `estimated` / `proposed` 的实体在 3D 里画**虚线框 + `?`**，不画成实心；
* **没有几何就 `known=False`**：渲染层据此画占位框，绝不按"看起来像"补尺寸。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import BODY_KINDS, BODY_SHAPES, MOUNTS, SOURCE_ORDER, DesignError

#: 目录文件与 `symbol_pins.json` 放在一起：都是"随仓库走的静态事实表"。
CATALOG_FILE = "bodies_catalog.json"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Body:
    """一个整机实体。**容错解析**：坏字段进 `problems()`，不抛异常。

    为什么不抛：`design.json` 是半成品，缺字段是常态；一抛异常整个工程
    就读不进来（连"哪里缺"都看不到）。**结构问题走 `problems()` 汇总**，
    由 `DesignModel.validate()` 一次性报出去。
    """

    ref: str
    kind: str = "generic"
    label: str = ""
    #: 装配树父节点（另一个 body 的 ref）。空 = 直接挂在板/机上。
    parent: str = ""
    #: `board` 装在板上、`air` 悬空、`frame` 装在机架节点上。
    mount: str = "air"
    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0
    #: 绕竖直轴（与 `Part.placement.rotation_z` 同一约定，单位度）。
    rz: float = 0.0
    shape: str = "box"
    size_mm: dict[str, float] = field(
        default_factory=lambda: {"w": 0.0, "h": 0.0, "d": 0.0})
    #: 圆柱/圆盘用直径；`size_mm.d` 仍然是高度。
    diameter_mm: float = 0.0
    #: 圆盘（螺旋桨/垫片）用厚度。
    thickness_mm: float = 0.0
    #: 桨叶数（只有 `disc` 有意义；0 = 没记）。
    blades: int = 0
    mass_g: float | None = None
    #: `mesh` 形状的资源路径（相对工程目录）。**空 = 没有模型文件**。
    model: str = ""
    source: str = "unknown"
    evidence: str = ""

    # -- 解析/序列化 -------------------------------------------------------

    @staticmethod
    def from_dict(row: dict[str, Any] | None) -> "Body":
        data = row or {}
        size = data.get("size_mm") or {}
        mass = data.get("mass_g")
        return Body(
            ref=str(data.get("ref") or "").strip(),
            kind=str(data.get("kind") or "generic").strip(),
            label=str(data.get("label") or ""),
            parent=str(data.get("parent") or "").strip(),
            mount=str(data.get("mount") or "air").strip(),
            x_mm=_f(data.get("x_mm")),
            y_mm=_f(data.get("y_mm")),
            z_mm=_f(data.get("z_mm")),
            rz=_f(data.get("rz")),
            shape=str(data.get("shape") or "box").strip(),
            size_mm={k: _f(size.get(k)) for k in ("w", "h", "d")},
            diameter_mm=_f(data.get("diameter_mm")),
            thickness_mm=_f(data.get("thickness_mm")),
            blades=_i(data.get("blades")),
            mass_g=(_f(mass) if mass is not None else None),
            model=str(data.get("model") or ""),
            source=str(data.get("source") or "unknown"),
            evidence=str(data.get("evidence") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """只写**有值**的东西：空字段不落盘，`design.json` 不被零淹没。"""
        out: dict[str, Any] = {"ref": self.ref, "kind": self.kind, "shape": self.shape}
        if self.label:
            out["label"] = self.label
        if self.parent:
            out["parent"] = self.parent
        if self.mount != "air":
            out["mount"] = self.mount
        if (self.x_mm, self.y_mm, self.z_mm, self.rz) != (0.0, 0.0, 0.0, 0.0):
            out["x_mm"] = round(self.x_mm, 4)
            out["y_mm"] = round(self.y_mm, 4)
            out["z_mm"] = round(self.z_mm, 4)
            if self.rz:
                out["rz"] = round(self.rz, 3)
        nonzero = {k: round(v, 4) for k, v in self.size_mm.items() if v}
        if nonzero:
            out["size_mm"] = nonzero
        if self.diameter_mm:
            out["diameter_mm"] = round(self.diameter_mm, 4)
        if self.thickness_mm:
            out["thickness_mm"] = round(self.thickness_mm, 4)
        if self.blades:
            out["blades"] = self.blades
        if self.mass_g is not None:
            out["mass_g"] = round(float(self.mass_g), 3)
        if self.model:
            out["model"] = self.model
        out["source"] = self.source or "unknown"
        if self.evidence:
            out["evidence"] = self.evidence
        return out

    # -- 几何可表达性 ------------------------------------------------------

    @property
    def has_geometry(self) -> bool:
        """**够不够画出实体**（不是"尺寸准不准"）。"""
        if self.shape in ("box", "prism"):
            return all(self.size_mm.get(k, 0.0) > 0 for k in ("w", "h", "d"))
        if self.shape == "cylinder":
            return self.diameter_mm > 0 and self.size_mm.get("d", 0.0) > 0
        if self.shape == "disc":
            return self.diameter_mm > 0 and self.thickness_mm > 0
        if self.shape == "mesh":
            return self.model != ""
        return False

    def missing_fields(self) -> list[str]:
        """缺哪些数字才画得出来。**给的是字段名，不是猜值**。"""
        need: list[str] = []
        if self.shape in ("box", "prism"):
            need = [f"size_mm.{k}" for k in ("w", "h", "d")
                    if self.size_mm.get(k, 0.0) <= 0]
        elif self.shape == "cylinder":
            if self.diameter_mm <= 0:
                need.append("diameter_mm")
            if self.size_mm.get("d", 0.0) <= 0:
                need.append("size_mm.d")
        elif self.shape == "disc":
            if self.diameter_mm <= 0:
                need.append("diameter_mm")
            if self.thickness_mm <= 0:
                need.append("thickness_mm")
        elif self.shape == "mesh":
            if self.model == "":
                need.append("model")
        return need

    # -- 结构校验 ----------------------------------------------------------

    def problems(self) -> list[str]:
        out: list[str] = []
        if self.ref == "":
            out.append("body: 缺 ref")
        if self.kind not in BODY_KINDS:
            out.append(f"body {self.ref}: 未知 kind {self.kind!r}"
                       f"（可用：{', '.join(BODY_KINDS)}）")
        if self.shape not in BODY_SHAPES:
            out.append(f"body {self.ref}: 未知 shape {self.shape!r}"
                       f"（可用：{', '.join(BODY_SHAPES)}）")
        if self.mount not in MOUNTS:
            out.append(f"body {self.ref}: mount 必须是 {'/'.join(MOUNTS)}"
                       f"，收到 {self.mount!r}")
        if self.source not in SOURCE_ORDER and self.source != "unknown":
            out.append(f"body {self.ref}: source 必须是 "
                       f"{'/'.join(SOURCE_ORDER)} 或 unknown，收到 {self.source!r}")
        if self.source == "unknown":
            out.append(f"body {self.ref}: 没有 source（谁给的数？）——"
                       "3D 只会画占位框")
        for key, value in self.size_mm.items():
            if value < 0:
                out.append(f"body {self.ref}: size_mm.{key} 不能为负")
        if self.diameter_mm < 0 or self.thickness_mm < 0:
            out.append(f"body {self.ref}: 直径/厚度不能为负")
        if self.blades < 0:
            out.append(f"body {self.ref}: blades 不能为负")
        if self.mass_g is not None and self.mass_g < 0:
            out.append(f"body {self.ref}: mass_g 不能为负")
        if self.parent == self.ref:
            out.append(f"body {self.ref}: parent 不能是自己")
        return out

    def scene_row(self) -> dict[str, Any]:
        """3D 载荷的一行。`known=False` 的那些，渲染层只画占位框。"""
        return {
            "ref": self.ref,
            "kind": self.kind,
            "label": self.label,
            "parent": self.parent,
            "mount": self.mount,
            "transform": {"x_mm": round(self.x_mm, 4), "y_mm": round(self.y_mm, 4),
                          "z_mm": round(self.z_mm, 4), "rz": round(self.rz, 3)},
            "shape": {"type": self.shape,
                      "w": round(self.size_mm.get("w", 0.0), 4),
                      "h": round(self.size_mm.get("h", 0.0), 4),
                      "d": round(self.size_mm.get("d", 0.0), 4),
                      "diameter_mm": round(self.diameter_mm, 4),
                      "thickness_mm": round(self.thickness_mm, 4),
                      "blades": self.blades,
                      "model": self.model},
            "mass_g": self.mass_g,
            "source": self.source,
            "evidence": self.evidence,
            "known": self.has_geometry,
        }


# ---------------------------------------------------------------------------
# 表级操作
# ---------------------------------------------------------------------------

def rows(design: Any) -> list[Body]:
    return [Body.from_dict(r) for r in (design.bodies_list or [])
            if isinstance(r, dict)]


def find(design: Any, ref: str) -> Body | None:
    return next((b for b in rows(design) if b.ref == ref), None)


def problems(design: Any) -> list[str]:
    """表级问题：重名、跨表撞名、悬空 parent、装配树成环。"""
    out: list[str] = []
    bodies = rows(design)
    part_refs = {p.ref for p in design.parts}
    seen: set[str] = set()
    for body in bodies:
        out.extend(body.problems())
        if body.ref in seen:
            out.append(f"body ref 重复：{body.ref}")
        seen.add(body.ref)
        if body.ref in part_refs:
            out.append(f"body {body.ref}: ref 与 PCB 元件同名（两张表共用命名空间）")
    by_ref = {b.ref: b for b in bodies}
    for body in bodies:
        if body.parent and body.parent not in by_ref:
            out.append(f"body {body.ref}: parent {body.parent!r} 不在整机实体表里")
    # 装配树成环：从每个节点往上走，最多走 N 步
    for body in bodies:
        walked = [body.ref]
        cursor = by_ref.get(body.parent)
        while cursor is not None:
            if cursor.ref in walked:
                out.append(f"body {body.ref}: 装配树成环（{ ' → '.join(walked + [cursor.ref]) }）")
                break
            walked.append(cursor.ref)
            cursor = by_ref.get(cursor.parent)
    return out


def install(design: Any, row: dict[str, Any] | Body, *, replace: bool = False) -> Body:
    """写入一个实体。**ref 与 PCB 元件同名直接拒收**（否则两张表分不清）。"""
    body = row if isinstance(row, Body) else Body.from_dict(row)
    if not body.ref:
        raise DesignError("body 缺 ref")
    if any(p.ref == body.ref for p in design.parts):
        raise DesignError(f"ref {body.ref!r} 已经是 PCB 元件；整机实体要换一个 ref")
    existing = {str(r.get("ref") or ""): at
                for at, r in enumerate(design.bodies_list or [])}
    payload = body.to_dict()
    if body.ref in existing:
        if not replace:
            raise DesignError(f"body {body.ref!r} 已存在（要覆盖请用 replace=True）")
        design.bodies_list[existing[body.ref]] = payload
    else:
        design.bodies_list.append(payload)
    return body


def remove(design: Any, ref: str) -> bool:
    before = len(design.bodies_list or [])
    design.bodies_list = [r for r in (design.bodies_list or [])
                          if str(r.get("ref") or "") != ref]
    return len(design.bodies_list) < before


# ---------------------------------------------------------------------------
# 目录（常见型号的一小批事实）
# ---------------------------------------------------------------------------

def catalog_path() -> Path:
    return Path(__file__).with_name(CATALOG_FILE)


def catalog() -> dict[str, Any]:
    path = catalog_path()
    if not path.is_file():
        return {"schema": 0, "entries": [], "note": "没有目录文件"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # noqa: BLE001
        return {"schema": 0, "entries": [], "note": f"目录读不出来：{exc}"}


def catalog_entries() -> list[dict[str, Any]]:
    rows_ = catalog().get("entries") or []
    return [dict(r) for r in rows_ if isinstance(r, dict)]


def lookup(model: str) -> dict[str, Any] | None:
    """按型号找目录条目（大小写无关，认 `aliases`）。找不到返回 None。"""
    key = str(model or "").strip().lower()
    if key == "":
        return None
    for entry in catalog_entries():
        names = [str(entry.get("model") or "")]
        names.extend(str(a) for a in (entry.get("aliases") or []))
        if any(name.strip().lower() == key for name in names if name):
            return entry
    return None


def from_catalog(model: str, *, ref: str = "", **placement: float) -> Body:
    """目录条目 + 摆放 → `Body`。**目录里缺的字段保持缺失**，不补 0。"""
    entry = lookup(model)
    if entry is None:
        raise DesignError(f"目录里没有型号 {model!r}（可用："
                          + "、".join(str(e.get('model') or '') for e in catalog_entries())
                          + "）")
    shape = str(entry.get("shape") or "box")
    size = dict(entry.get("size_mm") or {})
    body = Body(
        ref=ref or str(entry.get("default_ref") or "").strip()
        or str(entry.get("model") or ""),
        kind=str(entry.get("kind") or "generic"),
        label=str(entry.get("label") or entry.get("model") or ""),
        parent="",
        mount=str(entry.get("mount") or "air"),
        x_mm=float(placement.get("x_mm", 0.0)),
        y_mm=float(placement.get("y_mm", 0.0)),
        z_mm=float(placement.get("z_mm", 0.0)),
        rz=float(placement.get("rz", 0.0)),
        shape=shape,
        size_mm={k: _f(size.get(k)) for k in ("w", "h", "d")},
        diameter_mm=_f(entry.get("diameter_mm")),
        thickness_mm=_f(entry.get("thickness_mm")),
        blades=_i(entry.get("blades")),
        mass_g=(_f(entry.get("mass_g")) if entry.get("mass_g") is not None else None),
        source=str(entry.get("source") or "unknown"),
        evidence=str(entry.get("evidence") or ""),
    )
    return body


def install_from_catalog(design: Any, model: str, *, ref: str = "",
                         replace: bool = False, **placement: float) -> Body:
    body = from_catalog(model, ref=ref, **placement)
    return install(design, body, replace=replace)


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def audit(design: Any) -> dict[str, Any]:
    """整机实体的三态报告。**空表 = not_ready，不是通过。**"""
    bodies = rows(design)
    issues = problems(design)
    missing = [{"ref": b.ref, "kind": b.kind, "need": b.missing_fields()}
               for b in bodies if not b.has_geometry]
    unchecked = [{"ref": b.ref, "why": "source=unknown（没人给数）"}
                 for b in bodies if b.source == "unknown"]
    checked = len(bodies)
    if checked == 0:
        status = "not_ready"
    elif issues:
        status = "failed"
    else:
        status = "ok"
    return {
        "status": status,
        "checked": checked,
        "drawn": sum(1 for b in bodies if b.has_geometry),
        "placeholder": len(missing),
        "unknown_source": len(unchecked),
        "bodies": [b.to_dict() for b in bodies],
        "issues": issues,
        "missing": missing,
        "unchecked": unchecked,
        "summary": (f"{checked} 个整机实体，{sum(1 for b in bodies if b.has_geometry)} "
                    f"个有几何可画，{len(missing)} 个只能画占位框"
                    if checked else "没有整机实体（只有 PCB）—— 不是通过"),
    }


def describe() -> dict[str, Any]:
    return {
        "module": "hw_velxio.design.body",
        "basis": "整机实体表 `bodies_list`，与 `parts` 共用 ref 命名空间",
        "kinds": list(BODY_KINDS),
        "shapes": list(BODY_SHAPES),
        "catalog": str(catalog_path().name),
        "discipline": ("source 必填；没有几何 = known=False（渲染画占位框）；"
                       "缺字段不补 0"),
        "honest": ("空表 status=not_ready；catalog 里尺寸未收的条目 source=unknown，"
                   "不冒充 datasheet"),
    }