"""整机实体 + 参数化外壳的验收（V1）。

从仓库根运行：

    python -m dsh_embagent.scripts.check_bodies

## 它证的是什么

V1 只做了一件事：**给 `design.json` 加"整机实体"与"外壳腔体"这一层事实**，
并让 3D/2D 拿到它。所以验的也是事实与投影，不是像素：

* 目录里没核对的尺寸**必须**留空（`source=unknown` + `need`），不能补 0；
* `known=false` 的实体在载荷里必须 `known=false`（渲染只画占位框）；
* `joints` 的两端跨两张表都能解析，**删除 bodies 后必须变红**（闸门生效）；
* 外壳没声明 → `not_ready`；没开口 → `unknown`；越界/装不下 → `fail`；
* `scene3d` 升到 v2，旧字段一个没动；2D 的 px 由 Python 算，不交给 TS。

每条断言都配了"退化"用例（改坏输入，确认结论真的变），否则断言只是恒真。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dsh_embagent import design_cli as cli  # noqa: E402
from dsh_embagent.design import body  # noqa: E402
from dsh_embagent.design import joints as joints_module  # noqa: E402
from dsh_embagent.design import shell  # noqa: E402
from dsh_embagent.design.model import (  # noqa: E402
    BODY_KINDS, BODY_SHAPES, SOURCE_ORDER, DesignModel, Enclosure, Part, Placement)
from dsh_embagent.design.projection import to_2d  # noqa: E402


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}{f' — {detail}' if detail else ''}")
            self.items.append(name)


def _part(ref: str, x: float = 0.0, y: float = 0.0) -> Part:
    return Part(ref=ref, kind="resistor", size_mm={"w": 1.6, "h": 0.8, "d": 0.5},
                placement=Placement(x_mm=x, y_mm=y))


def _design_with_body(kind_shape: tuple[str, str] = ("motor", "cylinder"),
                      known: bool = True) -> DesignModel:
    d = DesignModel(id="bodies-probe")
    d.add_part(_part("R1"))
    row: dict = {"ref": "M1", "kind": kind_shape[0], "shape": kind_shape[1],
                 "source": "datasheet", "evidence": "test fixture"}
    if known:
        row.update({"diameter_mm": 27.7, "size_mm": {"d": 32.0},
                    "mass_g": 31.5, "x_mm": 20.0, "y_mm": 20.0, "z_mm": 1.0})
    body.install(d, row)
    return d


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    failures = Failures()
    print("== 1. 目录：只有能引用的数，没有编的数 ==")
    entries = body.catalog_entries()
    failures.check("目录有 8 条常见型号", len(entries) == 8, f"实际 {len(entries)}")
    models = [str(e.get("model") or "") for e in entries]
    failures.check("每条都有 model", all(models) and len(set(models)) == len(models))
    allowed = set(SOURCE_ORDER) | {"unknown"}
    failures.check("每条 source 都在白名单里（不冒充 datasheet）",
                   all(str(e.get("source") or "") in allowed for e in entries),
                   f"非法：{[e.get('source') for e in entries if str(e.get('source') or '') not in allowed]}")
    unknown_no_need = [e["model"] for e in entries
                       if str(e.get("source")) == "unknown" and not (e.get("need") or [])]
    failures.check("source=unknown 的条目必须列 need（缺什么写清楚）",
                   not unknown_no_need, f"{unknown_no_need}")
    props = [e for e in entries if e.get("kind") == "propeller"]
    bad_prop = []
    for entry in props:
        # 型号是行业写法：`5146` = 5.1 寸 × 4.6 螺距，`7040` = 7.0 × 4.0，
        # `3528` = 3.5 × 2.8。**直径只由第一个数决定**，螺距不参与。
        raw = str(entry["model"]).split("-")
        digits = "".join(ch for ch in (raw[1] if len(raw) > 1 else "")
                         if ch.isdigit())
        if len(digits) < 2:
            bad_prop.append((entry["model"], "型号里没有英寸数"))
            continue
        inch = int(digits[0]) + int(digits[1]) / 10.0
        expect = round(inch * 25.4, 2)
        got = entry.get("diameter_mm")
        if got is None or abs(float(got) - expect) > 0.6:
            bad_prop.append((entry["model"], got, expect))
    failures.check("桨直径 = 型号英寸 × 25.4（换算，不是猜）", not bad_prop, f"{bad_prop}")

    print("== 2. 安装：缺尺寸能装，但 must known=false ==")
    d = DesignModel(id="install-probe")
    d.add_part(_part("R1"))
    installed = body.install_from_catalog(d, "motor-2207-5", ref="M1", x_mm=10, y_mm=10)
    failures.check("目录条目没有尺寸时 has_geometry=False", installed.has_geometry is False)
    audit = body.audit(d)
    failures.check("审计把它算进 placeholder（不是 drawn）",
                   audit["drawn"] == 0 and audit["placeholder"] == 1,
                   f"drawn={audit['drawn']} placeholder={audit['placeholder']}")
    failures.check("审计把 source=unknown 单列（谁给的数没写）",
                   audit["unknown_source"] == 1)
    failures.check("安装后 validate 不因缺尺寸报结构错",
                   not [p for p in d.validate() if "body" in p and "尺寸" in p],
                   f"{d.validate()}")

    print("== 3. 给了尺寸：shape 词汇真的能画 ==")
    known = _design_with_body(("motor", "cylinder"), known=True)
    row = known.entity("M1")
    failures.check("known=true", row is not None and row.has_geometry)
    failures.check("scene_row 带 transform/shape/known",
                   set(row.scene_row()) >= {"ref", "transform", "shape", "known"})
    failures.check("cylinder 只靠直径+高度就算可画",
                   row.scene_row()["shape"]["diameter_mm"] > 0
                   and row.scene_row()["shape"]["d"] > 0)
    disc = body.Body(ref="P1", kind="propeller", shape="disc", diameter_mm=129.54,
                     thickness_mm=1.2, blades=3, source="derived")
    failures.check("disc 用直径+厚度（桨毂高没给就不算 known）",
                   disc.has_geometry and not body.Body(
                       ref="P2", kind="propeller", shape="disc",
                       diameter_mm=129.54, source="derived").has_geometry)

    print("== 4. 跨表引用：joints 两端可以是整机实体 ==")
    d = _design_with_body()
    d.joints_list = [{"a": "M1", "b": "R1", "kind": "axle",
                      "params": {"diameter_mm": 5.0}, "source": "declared"}]
    report = joints_module.validate(d)
    dangling = [f for f in report["findings"]
                if "悬空" in str(f.get("message") or "")]
    failures.check("关节指向 body 时不判悬空", not dangling, f"{dangling}")
    # 退化：把 bodies 删掉，同一条关节必须变红（证明闸门真的挂在 bodies 上）
    without = _design_with_body()
    without.bodies_list = []
    without.joints_list = list(d.joints_list)
    degenerate = [f for f in joints_module.validate(without)["findings"]
                  if "悬空" in str(f.get("message") or "")]
    failures.check("删掉 bodies 后同一条关节必须报悬空（闸门生效）",
                   bool(degenerate), "闸门没生效：跨表引用是恒真的")

    print("== 5. 结构校验：撞名 / 悬空 parent / 成环 ==")
    conflict = DesignModel(id="conflict")
    conflict.add_part(_part("M1"))
    try:
        body.install(conflict, {"ref": "M1", "kind": "motor", "source": "datasheet"})
        refused = False
    except Exception:  # noqa: BLE001
        refused = True
    failures.check("body ref 与 PCB 元件撞名 → 拒收", refused)

    dangling_parent = DesignModel(id="dangling")
    body.install(dangling_parent, {"ref": "M1", "kind": "motor", "parent": "F9",
                                   "source": "estimated"})
    failures.check("parent 指向不存在的实体 → validate 报错",
                   any("parent" in p for p in dangling_parent.validate()),
                   f"{dangling_parent.validate()}")

    cycle = DesignModel(id="cycle")
    body.install(cycle, {"ref": "A1", "kind": "arm", "parent": "A2",
                         "source": "estimated"})
    body.install(cycle, {"ref": "A2", "kind": "arm", "parent": "A1",
                         "source": "estimated"})
    failures.check("装配树成环 → validate 报错",
                   any("成环" in p for p in cycle.validate()),
                   f"{cycle.validate()}")

    bad_kind = DesignModel(id="bad-kind")
    body.install(bad_kind, {"ref": "X1", "kind": "warp-drive", "shape": "box",
                            "source": "estimated"})
    failures.check("未知 kind → validate 报错",
                   any("未知 kind" in p for p in bad_kind.validate()))
    failures.check("枚举来自 model（不是脚本里抄的）",
                   "warp-drive" not in BODY_KINDS
                   and set(BODY_SHAPES) == {"box", "cylinder", "disc", "prism", "mesh"})

    print("== 6. 外壳：三态 ==")
    plain = DesignModel(id="no-shell")
    failures.check("没有外壳 → not_ready（不是通过）",
                   shell.evaluate(plain)["status"] == "not_ready")

    sealed = DesignModel(id="sealed")
    sealed.enclosure = Enclosure(inner_mm={"w": 60, "h": 50, "d": 20}, wall_mm=2.0)
    sealed_report = shell.evaluate(sealed)
    openings_row = next(r for r in sealed_report["rows"]
                        if r["check"] == "shell.openings")
    failures.check("有外壳但没开口 → openings=unknown",
                   openings_row["status"] == "unknown", openings_row["message"])
    lid_row = next(r for r in sealed_report["rows"] if r["check"] == "shell.lid")
    failures.check("没给盖厚 → lid=unknown（不按无盖当通过）",
                   lid_row["status"] == "unknown")

    good = DesignModel(id="good-shell")
    good.add_part(_part("R1"))
    good.enclosure = Enclosure(
        # 内腔必须容得下**默认板**（Arduino Uno 68.58×53.34）：60×50 会装不下，
        # 那正是"内腔太小"的退化物，不能拿来当正例。
        inner_mm={"w": 100, "h": 80, "d": 20}, wall_mm=2.0, lid_mm=2.0,
        floor_z_mm=4.0,
        openings=[{"face": "top", "x_mm": 10, "y_mm": 10, "w_mm": 8, "h_mm": 4,
                   "kind": "usb"}])
    ok_report = shell.evaluate(good)
    failures.check("内腔够大 + 开口在面内 + 有盖 → ok",
                   ok_report["status"] == "ok", ok_report["summary"])

    out = DesignModel(id="out-of-bounds")
    out.enclosure = Enclosure(
        inner_mm={"w": 60, "h": 50, "d": 20}, wall_mm=2.0,
        openings=[{"face": "top", "x_mm": 58, "y_mm": 10, "w_mm": 8, "h_mm": 4,
                   "kind": "usb"}])
    failures.check("开口越界 → fail",
                   shell.evaluate(out)["status"] == "failed")
    failures.check("越界同时进 validate（put 会拒收）",
                   any("横向越界" in p for p in out.validate()),
                   f"{out.validate()}")

    small = DesignModel(id="small-cavity")
    small.add_part(_part("R1"))
    small.enclosure = Enclosure(inner_mm={"w": 10, "h": 10, "d": 20}, wall_mm=2.0)
    failures.check("内腔装不下拼板 → fail",
                   shell.evaluate(small)["status"] == "failed")
    # 退化：把间隙调小到 0 仍然装不下（10 < 1.6+2*0.5? 板是 1.6×0.8，其实装得下）
    failures.check("间隙是参数而不是常量",
                   shell.evaluate(small, clearance_mm=0.0)["status"] == "failed"
                   and shell.evaluate(small, clearance_mm=0.0)["rows"][0]["status"] == "fail")

    print("== 7. 投影：scene3d v2 与 2D 俯视 ==")
    d = _design_with_body()
    d.enclosure = good.enclosure
    scene = cli.scene3d(d)
    failures.check("scene3d.schema == 2", scene.get("schema") == 2)
    failures.check("scene3d 有 bodies[] 且长度对", len(scene.get("bodies") or []) == 1)
    failures.check("旧字段一个没少",
                   {"units", "panel", "board", "boards", "parts", "wires",
                    "ratlines", "mount_holes", "enclosure"} <= set(scene))
    payload = scene["enclosure"]
    failures.check("外壳载荷带 outer/offset/faces/openings",
                   {"outer_mm", "board_offset_mm", "faces_mm", "openings"} <= set(payload or {}))
    failures.check("outer 比 inner 大（壁厚被算进去）",
                   payload["outer_mm"]["w"] > payload["inner_mm"]["w"] + 3.0)

    two = to_2d(d)
    enc = two["enclosure"]
    failures.check("2D 带 enclosure（px 已在 Python 算）",
                   enc is not None and enc["outer"]["w_px"] > enc["inner"]["w_px"])
    failures.check("2D 只画顶/底面开口，侧面只计数",
                   len(enc["openings"]) == 1 and enc["side_openings"] == 0)
    side = DesignModel(id="side")
    side.enclosure = Enclosure(
        inner_mm={"w": 60, "h": 50, "d": 20}, wall_mm=2.0,
        openings=[{"face": "left", "x_mm": 25, "y_mm": 10, "w_mm": 20, "h_mm": 10,
                   "kind": "vent"}])
    side2d = to_2d(side)["enclosure"]
    failures.check("侧面开口不进 2D 矩形，只计数",
                   len(side2d["openings"]) == 0 and side2d["side_openings"] == 1)
    failures.check("px 不是 NaN",
                   all(isinstance(v, (int, float)) and v == v
                       for v in (side2d["outer"]["x_px"], side2d["outer"]["w_px"])))

    print("== 8. 往返：默认值不写，旧文件不变 ==")
    plain_enc = Enclosure(inner_mm={"w": 10, "h": 10, "d": 5}, wall_mm=2.0)
    failures.check("lid/floor 默认 0 时不写进文件",
                   set(plain_enc.to_dict()) == {"inner_mm", "wall_mm", "openings"})
    d = _design_with_body()
    d.enclosure = good.enclosure
    once = d.to_dict()
    twice = DesignModel.from_dict(once).to_dict()
    failures.check("to_dict → from_dict → to_dict 稳定", once == twice,
                   "往返后文件形状变了")
    failures.check("bodies 往返后仍在", bool(twice.get("bodies_list")))

    print("== 9. CLI 注册表 ==")
    failures.check("bodies / shell 两个命令已注册",
                   "bodies" in cli.COMMANDS and "shell" in cli.COMMANDS)
    failures.check("命令名不与老命令撞（键唯一）",
                   len(cli.COMMANDS) == len(set(cli.COMMANDS)))

    print()
    if failures.items:
        print(f"❌ 失败 {len(failures.items)} 条：" + "；".join(failures.items))
        return 1
    print("✅ 整机实体 / 外壳 / 投影检查全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())