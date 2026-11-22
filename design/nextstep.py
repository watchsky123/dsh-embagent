"""下一步 —— 把"哪条判据红了"变成"点一下就动手"。

## 这一层解决什么

P0 给出了一屏结论，但下一步那句话仍是自由文本：

    V2 几何失败 → "C146 与 Lora1 实体重叠 1.60×0.90×0.80 mm；……"

人读完知道**坏了**，但不知道**点哪**。而"点哪"这件事，机器恰好知道：
`physical.py` 早就给每条 finding 塞了 `fix={"need": "move_apart", "refs": [...]}`，
`links.py` 塞了 `fix={"need": "declare_link", "peripheral": ...}`。

问题不在数据，在**没有一层把 `need` 翻译成动作**。这一层就是那个翻译。

## 三条纪律

1. **一张表，不散在各处**。`NEEDS` 是唯一映射处；`fix.need` 出现新值时
   如果表里没有，结果是 `None`（UI 退回自由文本），**不是临时编一个**。
2. **参数不全就降级，不补**。命令模板里要 `{lcsc}` 而这个件没填 lcsc 时，
   降级成 `external`（"查 datasheet 后补尺寸"），**不替你猜一个编号**。
   猜错了会去查错的料，比不动更糟。
3. **做不到的要说清是谁做**。`external` 不是"待办"，是"这件事不在这个工具里"：
   装 ffmpeg、在立创界面手动布线、真机实测。给它一个假按钮才是最坏的。

## 四类动作

| kind | 含义 | UI 怎么接 |
|---|---|---|
| `edit_field` | 改设计里的某个字段 | 选中 `refs` 并定位到 Inspector 的该字段 |
| `move_part` | 元件位置问题 | 选中 `refs`（2D 里高亮，人自己拖） |
| `run_command` | 有确切命令能做 | 复制命令，粘到终端 |
| `external` | 本工具不做 | 显示"由谁做"，不给按钮 |

**没有 `auto_fix`**。改设计是人的决定，机器只把人送到该看的地方。
"""

from __future__ import annotations

from typing import Any

__all__ = ["KINDS", "NEEDS", "build", "describe", "for_item"]

#: 四类动作。顺序即 UI 上的优先级：能做 > 能看 > 能跑 > 做不了。
KINDS = ("edit_field", "move_part", "run_command", "external")

#: `fix.need` → 动作。模板里的 `{x}` 从 `fix` 里取值。
#:
#: `field` 是设计里的**实际路径**（Inspector 按它定位），不是描述。
#: `command` 是**能直接粘走的整条 CLI**，不是"请运行…"这种话——
#: 后者等于让人自己再拼一遍，而拼错正是这类工具最容易出的错。
NEEDS: dict[str, dict[str, str]] = {
    # -- 位置类：选中元件，让人自己拖 --------------------------------
    "move_apart": {
        "kind": "move_part",
        "label": "把 {refs} 分开（间距小于安全值）",
    },
    "move_out_of_keepout": {
        "kind": "move_part",
        "label": "把 {ref} 移出 keepout 区",
    },
    "move_inside_outline": {
        "kind": "move_part",
        "label": "把超界的元件移回板框内（{refs}）",
    },
    "clear_hole_area": {
        "kind": "move_part",
        "label": "让 {refs} 避开安装孔",
    },
    "move_to_board_edge": {
        "kind": "move_part",
        "label": "把连接器移到板边（{refs}）",
    },
    "shorten_net": {
        "kind": "move_part",
        "label": "缩短 {pair} 之间的走线（挪近或换层）",
    },
    "lower_or_raise_enclosure": {
        "kind": "edit_field",
        "field": "meta.max_height_mm",
        "label": "声明外壳限高（meta.max_height_mm），或降低最高的元件",
    },
    # -- 数据类：改字段 -----------------------------------------------
    "accept_normalisation_or_fix_size": {
        "kind": "edit_field",
        "field": "size_mm",
        "label": "确认归一化后的尺寸，或按 datasheet 改 size_mm（{refs}）",
    },
    "provide_size_mm": {
        "kind": "edit_field",
        "field": "size_mm",
        "label": "给 {refs} 填尺寸（size_mm）",
    },
    "provide_height_mm": {
        "kind": "edit_field",
        "field": "size_mm.d",
        "label": "给 {refs} 填高度（size_mm.d）",
    },
    "declare_electrical": {
        "kind": "edit_field",
        "field": "electrical.power_w",
        "label": "给 {refs} 申报电气参数（Inspector 里的功率 / 成本）",
    },
    "import_netlist": {
        "kind": "run_command",
        "command": "python -m hw_velxio.design_cli import {project} <源文件> --bom <BOM.csv>",
        "label": "重新导入带网表的源文件",
    },
    "declare_link": {
        "kind": "edit_field",
        "field": "links_list",
        "label": "在 links_list 里补 {peripheral} 的对端（协议 / 电平 / 波特率）",
    },
    # -- 机械域（P42 的提案 unresolved 会走到这里）-----------------------
    #
    # 为什么这几个必须进表：`check_p1` 的 1a 会扫源码里所有 `"need": "..."`
    # 并比对这张表 —— 漏一个，那条 finding 就**悄悄变回纯文字**（没有了按钮），
    # 不报错、不失败（AGENTS §3.14）。
    "hinge_deg": {
        "kind": "edit_field",
        "field": "mechanical.limits.hinge_deg",
        "label": "给 {refs} 的铰链写行程（mechanical.limits.hinge_deg = [lo, hi] 度）",
    },
    "hinge_partner": {
        "kind": "edit_field",
        "field": "unit",
        "label": "把执行器与从动件标成同一个 unit（{refs}），或直接声明一条 hinge",
    },
    "gear_ratio": {
        "kind": "edit_field",
        "field": "joints_list",
        "label": "给 {refs} 的齿轮补传动比（joints_list 里 params.ratio = 从动/主动）",
    },
    "geometry": {
        "kind": "external",
        "label": "几何推不出这条关系（{refs}）：补尺寸，或直接在 joints 里声明",
        "by": "人（补尺寸/声明关系）",
    },
    # -- 机械判据（P44）：每条 fail/unknown 都要有可点的动作 -------------
    "fix_interference": {
        "kind": "move_part",
        "label": "把 {refs} 分开（实体重叠，打板必炸）",
    },
    "move_cog": {
        "kind": "move_part",
        "label": "把重的东西往支撑面里挪（{refs}）",
    },
    "provide_mass": {
        "kind": "edit_field",
        "field": "mass_g",
        "label": "给 {refs} 填质量（mass_g）—— 没有质量算不了重心和扭矩",
    },
    "provide_torque": {
        "kind": "edit_field",
        "field": "electrical.stall_torque_kgcm",
        "label": "给 {refs} 填舵机标称扭矩（electrical.stall_torque_kgcm）",
    },
    "give_joint_travel": {
        "kind": "edit_field",
        "field": "mechanical.limits.required_deg",
        "label": "声明需求行程（mechanical.limits.required_deg），或改关节 params 的行程",
    },
    "give_support": {
        "kind": "external",
        "label": "说清哪些零件着地（{refs}）：支撑面判不出就没法算重心",
        "by": "人（标着地件/给支撑面）",
    },
    "run_emit": {
        "kind": "run_command",
        "label": "跑 `emit`（8 段）：拿到 verify 段才能说「可打板」",
        "command": "python -m hw_velxio.design_cli emit {project}",
    },
    "mech_unknown": {
        "kind": "external",
        "label": "机械判据缺数据（{refs}）：见 action 里缺什么",
        "by": "人（补数据）",
    },
    # -- 命令类：有确切命令 ---------------------------------------------
    "rerun_ladder": {
        "kind": "run_command",
        "command": "python -m hw_velxio.design_cli ladder {project}",
        "label": "重跑阶梯（设计改过，结论已过期）",
    },
    "rerun_checks": {
        "kind": "run_command",
        "command": "python -m hw_velxio.design_cli check {project}",
        "label": "重跑物理检查",
    },
    "apply_inferred_links": {
        "kind": "run_command",
        "command": "python -m hw_velxio.design_cli links {project} --infer --apply",
        "label": "把推断出的链路写进 links_list（仍标 inferred，需人确认）",
    },
    "lookup_part": {
        "kind": "run_command",
        "command": "python -m hw_velxio.design_cli parts-enrich {lcsc} --project {project}",
        "label": "按 LCSC 查 {lcsc} 的尺寸并回填",
    },
    # -- 做不了：说清是谁做 ---------------------------------------------
    "import_datasheet_or_measure": {
        "kind": "external",
        "label": "查 datasheet 或实测后补尺寸（{refs}）",
        "by": "人（手册 / 卡尺）",
    },
    "stiffer_supply": {
        "kind": "external",
        "label": "换更硬的电源或加粗走线（{refs}）",
        "by": "人（选型 / 改板）",
    },
    "wire_spectrogram": {
        "kind": "external",
        "label": "看波形确认压降（需要实测）",
        "by": "人（示波器）",
    },
    "install_toolchain": {
        "kind": "external",
        "label": "装工具链后才能验这一层（{what}）",
        "by": "人（装软件）",
    },
    "confirm_unconnected": {
        "kind": "external",
        "label": "确认 {refs} 是机械件（安装孔 / 支架）还是漏接线",
        "by": "人（看原理图意图）",
    },
    "declare_requirement": {
        "kind": "edit_field",
        "field": "meta.requirements",
        "label": "把需求里的实际数值写进 meta.requirements（别用默认值）",
    },
    "calibrate_sim": {
        "kind": "run_command",
        "command": "python -m hw_velxio.design_cli calibrate {project} --test=<pt-id> --candidates='[{...}]'",
        "label": "补候选参数并跑 calibrate（采纳标准是「旧设计复现」，不是「新设计通过」）",
    },
    "run_budget": {
        "kind": "run_command",
        "command": "python -m hw_velxio.design_cli budget {project}",
        "label": "跑一次五轨预算（此前完全没算过）",
    },
    "name_networks": {
        "kind": "edit_field",
        "field": "nets[].net",
        "label": "给关键网络起名字（SCL / USART1_TX 之类），不要留 $1N 自动名",
    },
    # -- 需求兑现不了：换硬件或放宽指标，都是人的选型决定 -------------
    "bigger_psu_or_sequential_motion": {
        "kind": "external",
        "label": "换更大的电源，或让动作改成顺序执行（峰值电流才是瓶颈）",
        "by": "人（选型 / 改时序）",
    },
    "faster_servo_or_slower_target": {
        "kind": "external",
        "label": "换更快的舵机，或把跟踪角速度指标降下来",
        "by": "人（选型 / 改需求）",
    },
    "finer_deadband_servo_or_relax_spec": {
        "kind": "external",
        "label": "换死区更小的舵机，或把指向精度指标降下来",
        "by": "人（选型 / 改需求）",
    },
    "fix_design_manually": {
        "kind": "external",
        "label": "{what} 失败要改设计（这一条没有机械可做的动作）",
        "by": "人（看 findings 里的 measured / limit）",
    },
    "manual_in_eda": {
        "kind": "external",
        "label": "在立创界面里手动做完（{what}）",
        "by": "人（立创EDA）",
    },
}

#: 模板里出现这些占位符时，`build()` 要求 `fix` 里真有值；
#: 缺值就**降级成 external**（不猜）。
_REQUIRED = ("{lcsc}", "{peripheral}", "{project}")


def _format(template: str, fix: dict[str, Any]) -> str:
    """把 `{refs}` / `{ref}` / `{pair}` 展开成人能读的字符串。

    缺值不抛异常——填一个「?」比让整条 finding 消失好。
    """
    refs = fix.get("refs")
    refs_text = "、".join(str(x) for x in refs) if isinstance(refs, (list, tuple)) else ""
    values = {
        "refs": refs_text or str(fix.get("ref") or "?"),
        "ref": str(fix.get("ref") or refs_text or "?"),
        "pair": "、".join(str(x) for x in (fix.get("pair") or [])) or "?",
        "peripheral": str(fix.get("peripheral") or "?"),
        "lcsc": str(fix.get("lcsc") or ""),
        "project": str(fix.get("project") or ""),
        "what": str(fix.get("what") or ""),
    }
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text.strip()


def build(fix: dict[str, Any] | None,
          *, project: str = "", lcsc: str = "",
          refs: Any = None) -> dict[str, Any] | None:
    """`fix` → 一步可执行的动作。**表里没有就返回 None，不临时编一个。**

    ``project`` / ``lcsc`` / ``refs`` 是从上下文补的（finding 自己不知道工程名，
    而 `physical` 的 fix 里常常不带 refs——它习惯把 refs 放在 finding 顶层）。
    """
    if not isinstance(fix, dict) or not fix:
        return None
    need = str(fix.get("need") or "").strip()
    if not need:
        return None
    spec = NEEDS.get(need)
    if spec is None:
        return None

    merged: dict[str, Any] = dict(fix)
    # finding 自己知道是哪个件（顶层的 `refs`），而 `fix` 里未必带。
    # **优先用 finding 的**：那是判定器算出来的，不是猜的。
    if refs and not merged.get("refs"):
        merged["refs"] = list(refs)
    if project:
        merged.setdefault("project", project)
    if lcsc:
        merged.setdefault("lcsc", lcsc)

    # 命令模板要的占位符缺值 → **降级**，不猜。
    # 降级时给一句**独立的**话，不要把原模板填空：模板里 `{lcsc}` 空掉后
    # 会得到「按 LCSC 查  的尺寸」，读起来像 bug，而人第一反应是怀疑工具。
    command = spec.get("command", "")
    if command:
        for token in ("{project}", "{lcsc}"):
            key = token.strip("{}")
            if token in command and not merged.get(key):
                return {
                    "kind": "external",
                    "need": need,
                    "label": f"先补 {key} 才能自动查（这条命令缺这个参数）",
                    "by": f"人（补 {key}）",
                }

    step: dict[str, Any] = {
        "kind": spec["kind"],
        "need": need,
        "label": _format(spec.get("label", ""), merged),
    }
    if spec.get("field"):
        step["field"] = spec["field"]
    if command:
        step["command"] = _format(command, merged)
    if spec.get("by"):
        step["by"] = spec["by"]
    refs = merged.get("refs")
    if isinstance(refs, (list, tuple)) and refs:
        step["refs"] = [str(x) for x in refs]
    elif merged.get("ref"):
        step["refs"] = [str(merged["ref"])]
    return step


def for_item(item: dict[str, Any], *, project: str = "") -> dict[str, Any] | None:
    """给交接单的一条 `Item` 求动作。

    `Item.fix` 有就给表查；没有就按 `category` 走**一条保守兜底**：

    * `blocking` 且状态是 `truncated` → 重跑阶梯（那确实是下一步）
    * 其余没有 `fix` 的 → `None`（UI 显示自由文本 `action`）

    兜底只认"下一步必然是重跑"这一种——它不需要猜，因为截断的定义
    就是"下游没跑"。别的情况下猜 = 编。
    """
    fix = item.get("fix")
    step = build(fix, project=project) if isinstance(fix, dict) else None
    if step is not None:
        return step
    if str(item.get("status")) == "truncated" and str(item.get("category")) == "blocking":
        return build({"need": "rerun_ladder"}, project=project)
    return None


def describe() -> dict[str, Any]:
    by_kind = {kind: [] for kind in KINDS}
    for need, spec in NEEDS.items():
        by_kind.setdefault(spec["kind"], []).append(need)
    return {
        "module": "hw_velxio.design.nextstep",
        "kinds": list(KINDS),
        "needs": len(NEEDS),
        "by_kind": {k: sorted(v) for k, v in by_kind.items()},
        "why": ("fix.need → 一步动作。表里没有就返回 None，"
                "UI 退回自由文本——不临时编一个"),
        "no_auto_fix": "机器只把人送到该看的地方；改设计是人的决定",
        "downgrade": ("命令模板缺 {lcsc}/{project} 时降级成 external，"
                      "不替你猜一个编号"),
    }