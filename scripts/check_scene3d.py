"""3D 装配场景的几何自检 —— 证明"盒子位置/朝向/层次"都对。

从仓库根运行：

    python -m dsh_embagent.scripts.check_scene3d

`check_canvas_render.py` 验的是 2D 像素；这里验 3D 载荷的**几何正确性**。
3D 视图出过的真实事故全是"看起来有东西、其实位置/朝向是错的"：

* 拿**旋转后**的 bbox 建盒子、再按 rotation_z 转一次 → 四个朝向画得一模一样；
* 顶层与底层的件都画在 z ≥ 0 → 双面板看起来是单面，重叠判定也跟着错；
* 焊盘不画 → 板子上没有焊点，看不出引脚；
* 网格挂在旋转过的根节点下 → 地面被转成一面竖墙。

所以这里验的是**数值**，不是像素。
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EPRO2 = Path(
    "C:/Users/<user>/Desktop/示例工作区/"
    "ProPrj_示例四旋翼飞行器_2026-11-15.epro2")


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}{f' — {detail}' if detail else ''}")
            self.items.append(name)


def _run(args: list[str], workspace: Path) -> str:
    env = {**os.environ, "HWV_WORKSPACE": str(workspace)}
    result = subprocess.run(
        [sys.executable, "-m", "dsh_embagent.design_cli", *args],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:400] or "命令失败")
    return result.stdout.strip()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    failures = Failures()
    workspace = Path(tempfile.mkdtemp(prefix="hwv-scene-"))
    try:
        if EPRO2.is_file():
            _run(["import", "scene-probe", str(EPRO2)], workspace)
        else:
            _run(["new", "scene-probe", "arduino-uno"], workspace)
            _run(["import", "scene-probe",
                  str(REPO_ROOT / "dsh_embagent" / "examples" / "wokwi" / "blink.json")],
                 workspace)
        payload = json.loads(_run(["snapshot", "scene-probe"], workspace))
        snapshot: dict[str, Any] = payload.get("snapshot") or payload
        scene = snapshot["scene3d"]
        board = scene["board"]
        parts = scene["parts"]
        thickness = max(float(board["thickness_mm"]), 0.4)

        print(f"  ..   {len(parts)} 实体 / {len(scene.get('wires') or [])} 铜线 / "
              f"{len(scene.get('ratlines') or [])} 飞线 / "
              f"板 {board['size_mm']['w']:.1f}×{board['size_mm']['h']:.1f}mm")

        # -- 1. 每个实体都有独立未旋转尺寸 --------------------------------
        failures.check("每个实体都带 size_mm（未旋转足迹）",
                       all("size_mm" in part for part in parts))
        failures.check("size_mm 三轴齐全",
                       all(set(part["size_mm"]) == {"w", "h", "d"}
                           for part in parts))
        failures.check("没有零尺寸实体（会画成不可见的点）",
                       all(part["size_mm"]["w"] > 0 and part["size_mm"]["h"] > 0
                           and part["size_mm"]["d"] > 0 for part in parts),
                       f"{sum(1 for p in parts if p['size_mm']['w'] <= 0)} 个零尺寸")

        # -- 2. bbox 与 size_mm 必须一致（旋转 90° 时才允许 w/h 互换）------
        mismatched: list[str] = []
        for part in parts:
            x0, y0, _z0, x1, y1, _z1 = part["bbox_mm"]
            size = part["size_mm"]
            bw, bh = x1 - x0, y1 - y0
            quarter = part["rotation_z"] % 180 != 0
            expect_w = size["h"] if quarter else size["w"]
            expect_h = size["w"] if quarter else size["h"]
            if abs(bw - expect_w) > 0.05 or abs(bh - expect_h) > 0.05:
                mismatched.append(
                    f"{part['ref']}(rot {part['rotation_z']}): "
                    f"bbox {bw:.2f}×{bh:.2f} vs size {expect_w:.2f}×{expect_h:.2f}")
        failures.check("bbox 与未旋转尺寸一致（旋转 90° 时 w/h 互换）",
                       not mismatched,
                       f"{len(mismatched)} 个不一致：{mismatched[:3]}")

        # -- 3. 安装面：底层必须在板面之下 ---------------------------------
        top_parts = [p for p in parts if p.get("side") == "top"]
        bottom_parts = [p for p in parts if p.get("side") == "bottom"]
        print(f"  ..   安装面：顶层 {len(top_parts)} / 底层 {len(bottom_parts)}")
        if bottom_parts:
            bad = [p["ref"] for p in bottom_parts if p["bbox_mm"][5] > 0.001]
            failures.check("底层元件的 z1 ≤ 0（贴板底面向下）", not bad,
                           f"{len(bad)} 个在板面上方：{bad[:5]}")
        if top_parts:
            bad = [p["ref"] for p in top_parts if p["bbox_mm"][2] < -0.001]
            failures.check("顶层元件的 z0 ≥ 0（贴板顶面向上）", not bad,
                           f"{len(bad)} 个在板面下方：{bad[:5]}")
        failures.check("顶层与底层都有元件（双面板）",
                       len(top_parts) > 0 and len(bottom_parts) > 0,
                       f"顶层 {len(top_parts)} / 底层 {len(bottom_parts)}")

        # -- 4. 焊盘 ---------------------------------------------------------
        pads = [pad for part in parts for pad in (part.get("pads") or [])]
        print(f"  ..   焊盘 {len(pads)} 个")
        failures.check("焊盘带几何", len(pads) > 0, "一个焊盘都没有")
        failures.check("焊盘尺寸为正",
                       all(pad["w_mm"] > 0 and pad["h_mm"] > 0 for pad in pads))
        failures.check("焊盘带形状",
                       all(isinstance(pad.get("shape"), str) and pad["shape"]
                           for pad in pads))

        # 焊盘必须落在**自己元件**的足迹附近（不能飘到别处）
        orphan: list[str] = []
        for part in parts:
            if not part.get("pads"):
                continue
            x0, y0, _z0, x1, y1, _z1 = part["bbox_mm"]
            margin = 3.0     # 焊盘可以略微伸出本体
            for pad in part["pads"]:
                if not (x0 - margin <= pad["x_mm"] <= x1 + margin
                        and y0 - margin <= pad["y_mm"] <= y1 + margin):
                    orphan.append(f"{part['ref']}.{pad['num']}")
        failures.check("焊盘都在自己元件的足迹范围内", not orphan,
                       f"{len(orphan)} 个飘出：{orphan[:5]}")

        # -- 5. 铜线 ---------------------------------------------------------
        wires = scene.get("wires") or []
        print(f"  ..   铜线 {len(wires)} 条")
        failures.check("铜线带层信息", all("layer" in wire for wire in wires))
        layers = {wire.get("layer") for wire in wires}
        failures.check("铜线分顶层与底层",
                       len(layers & {1, 2}) == 2 if len(layers) > 1 else True,
                       f"层：{sorted(layers)}")
        failures.check("铜线带宽度（真实线宽）",
                       all(float(wire.get("width_mm") or 0) > 0 for wire in wires))
        failures.check("铜线全部标 routed=true",
                       all(wire.get("routed") is True for wire in wires))
        outside = [
            wire["net"] for wire in wires
            if any(not (-2.0 <= x <= board["size_mm"]["w"] + 2.0
                        and -2.0 <= y <= board["size_mm"]["h"] + 2.0)
                   for x, y, _z in wire["path_mm"])
        ]
        failures.check("铜线都在板内（坐标没串）", not outside,
                       f"{len(outside)} 条出界：{outside[:3]}")

        # -- 6. 飞线 ---------------------------------------------------------
        ratlines = scene.get("ratlines") or []
        print(f"  ..   飞线 {len(ratlines)} 条")
        failures.check("飞线全部标 routed=false（飞线不是铜线）",
                       all(line.get("routed") is False for line in ratlines))
        failures.check("飞线有长度",
                       all(line["length_mm"] > 0 for line in ratlines))

        # -- 7. 板轮廓 -------------------------------------------------------
        outline = board["outline"]
        failures.check("板轮廓是闭合多边形（≥3 点）", len(outline) >= 3)
        area = 0.0
        for index in range(len(outline) - 1):
            x1, y1 = outline[index]
            x2, y2 = outline[index + 1]
            area += x1 * y2 - x2 * y1
        area = abs(area) / 2.0
        expect = board["size_mm"]["w"] * board["size_mm"]["h"]
        failures.check("板轮廓面积与板尺寸相符（不是退化多边形）",
                       area > expect * 0.5,
                       f"面积 {area:.1f} vs 外接矩形 {expect:.1f}")

        # -- 8. 相机取景能装下整块板 ----------------------------------------
        diagonal = math.hypot(board["size_mm"]["w"], board["size_mm"]["h"])
        failures.check("板对角线有限（相机能取景）",
                       diagonal > 1.0 and math.isfinite(diagonal),
                       f"对角线 {diagonal:.1f}mm")

        print(f"  ..   板厚 {thickness}mm，对角线 {diagonal:.1f}mm")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    if failures.items:
        print(f"\n❌ {len(failures.items)} 项失败")
        return 1
    print("\n✅ 3D 场景几何检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
