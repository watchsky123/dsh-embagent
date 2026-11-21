"""叠层与网络类约束 —— 「传约束，不搬算法」。

Traceforge 有 ~1200 行（`impedance.py` + `stackup.py` + `diffpair.py`）算阻抗、
等长、差分对。立创内置了这些（`PCB_Drc.createDifferentialPair` /
`createEqualLengthNetGroup` / `createNetClass` / `setTheNumberOfCopperLayers`），
所以这一层只做一件事：**把设计里的意图翻译成立创的入参**。

## 为什么默认只有 2 层且标 ``assumed``

`design.json` 目前**没有**叠层字段（`model.py` 里 grep 不到 stackup /
layer_count）。没有字段就没有数据来源，而「不知道就按 2 层」是立创新建 PCB
的默认值——这属于**机器替人做主**，必须在报告里写出来（AGENTS §0.2），
不能装作 4 层板被正确识别成 2 层。

设计里一旦有 `stackup` 字段（哪怕是导入器给的），就走 `declared` 路径。

## 约束的三种来源

| 来源 | 含义 | 进报告 |
|---|---|---|
| `declared` | design.json 里明确写了 | 直接用 |
| `derived` | 从网名/元件推出来（如 `USB_D+/D-` → 差分对） | 用，但标 derived |
| `assumed` | 没有数据，取默认值 | 用，但**明确列为「机器替你做主」** |
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import DesignModel

__all__ = ["StackupSpec", "apply", "derive", "describe"]

#: 立创支持的铜层数（`setTheNumberOfCopperLayers` 的枚举）。
COPPER_LAYERS = (2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32)

#: 差分对命名约定。只认**明确**的命名，不猜。
DIFF_PAIR_HINTS = (
    ("_P", "_N"),
    ("+", "-"),
    ("_DP", "_DN"),
    ("_D+", "_D-"),
)


@dataclass
class StackupSpec:
    """要传给立创的约束集合。每项都带来源，缺数据不填 0。"""

    copper_layers: int = 2
    layers_source: str = "assumed"
    thickness_mm: float = 1.6
    thickness_source: str = "assumed"
    net_classes: list[dict[str, Any]] = field(default_factory=list)
    diff_pairs: list[dict[str, Any]] = field(default_factory=list)
    equal_length_groups: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "copper_layers": self.copper_layers,
            "layers_source": self.layers_source,
            "thickness_mm": round(self.thickness_mm, 3),
            "thickness_source": self.thickness_source,
            "net_classes": [dict(x) for x in self.net_classes],
            "diff_pairs": [dict(x) for x in self.diff_pairs],
            "equal_length_groups": [dict(x) for x in self.equal_length_groups],
            "notes": list(self.notes),
        }

    @property
    def assumed(self) -> list[str]:
        """哪些项是机器替人做的决定 —— 交接单要看见。"""
        out: list[str] = []
        if self.layers_source == "assumed":
            out.append(f"铜层数按 {self.copper_layers} 层（design.json 没有 stackup 字段）")
        if self.thickness_source == "assumed":
            out.append(f"板厚按 {self.thickness_mm} mm（同上）")
        return out


def _declared_stackup(design: DesignModel) -> dict[str, Any]:
    """读可选的 `design.stackup`。没有就返回空 dict（不编默认字段）。"""
    raw = getattr(design, "stackup", None)
    if isinstance(raw, dict):
        return raw
    return {}


def derive(design: DesignModel) -> StackupSpec:
    """从设计推出约束。**不猜**：没有数据就标 assumed 并说清。"""
    spec = StackupSpec()
    declared = _declared_stackup(design)

    layers = declared.get("copper_layers")
    if isinstance(layers, int) and layers in COPPER_LAYERS:
        spec.copper_layers = layers
        spec.layers_source = "declared"
    elif isinstance(layers, int):
        spec.notes.append(
            f"stackup.copper_layers={layers} 不在立创支持范围 {COPPER_LAYERS}，按 2 层")
    else:
        spec.notes.append("design.json 没有 stackup.copper_layers，按立创新建 PCB 的默认 2 层")

    thickness = declared.get("thickness_mm")
    if isinstance(thickness, (int, float)) and thickness > 0:
        spec.thickness_mm = float(thickness)
        spec.thickness_source = "declared"
    else:
        board_thickness = getattr(getattr(design, "board", None), "thickness_mm", None)
        if isinstance(board_thickness, (int, float)) and board_thickness > 0:
            spec.thickness_mm = float(board_thickness)
            spec.thickness_source = "derived"

    # -- 网络类：只按**已声明的**分组键走 ---------------------------------
    for entry in declared.get("net_classes") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        nets = [str(n) for n in (entry.get("nets") or []) if str(n).strip()]
        if not name or not nets:
            continue
        spec.net_classes.append({
            "name": name,
            "nets": nets,
            "source": "declared",
            # 立创的规则名/取值各自有自己的单位，这里只传分组，不猜数值
        })

    # -- 差分对：按网名成对，且**两条网都在设计里**才算 -------------------
    known_nets = {str(e.get("net")) for e in design.nets if e.get("net")}
    seen: set[str] = set()
    for suffix_p, suffix_n in DIFF_PAIR_HINTS:
        for net in sorted(known_nets):
            if net in seen or not net.endswith(suffix_p):
                continue
            base = net[: -len(suffix_p)]
            partner = base + suffix_n
            if partner in known_nets:
                spec.diff_pairs.append({
                    "positive": net,
                    "negative": partner,
                    "source": "derived",
                })
                seen.update({net, partner})
    return spec


# ---------------------------------------------------------------------------
# 应用到立创
# ---------------------------------------------------------------------------

_JS_APPLY = r"""
const P = %(ctx)s;
const S = %(spec)s;
const steps = [];
async function attempt(name, fn) {
  try { const v = await fn(); steps.push({step: name, ok: true, value: v}); }
  catch (e) { steps.push({step: name, ok: false, error: String((e && e.message) || e)}); }
}

await attempt('openPcb', () => eda.dmt_EditorControl.openDocument(P.pcbUuid));
await new Promise(r => setTimeout(r, P.settleMs));

if (S.copperLayers) {
  await attempt('setTheNumberOfCopperLayers', () => eda.pcb_Layer.setTheNumberOfCopperLayers(S.copperLayers));
  await attempt('readCopperLayers', async () => {
    const ls = await eda.pcb_Layer.getAllLayers();
    return (ls || []).filter(l => l.type === 'SIGNAL')
      .filter(l => l.layerStatus !== 0 && l.layerStatus !== undefined)
      .length;
  });
}

for (const nc of (S.netClasses || [])) {
  await attempt('createNetClass:' + nc.name, () => eda.pcb_Drc.createNetClass(nc.name));
  for (const net of (nc.nets || [])) {
    await attempt('addNet:' + nc.name + '/' + net,
      () => eda.pcb_Drc.addNetToNetClass(nc.name, net));
  }
}

for (const dp of (S.diffPairs || [])) {
  await attempt('createDiffPair:' + dp.positive, async () => {
    const name = dp.positive + '_' + dp.negative;
    await eda.pcb_Drc.createDifferentialPair(name);
    await eda.pcb_Drc.modifyDifferentialPairPositiveNet(name, dp.positive);
    await eda.pcb_Drc.modifyDifferentialPairNegativeNet(name, dp.negative);
    return name;
  });
}

return {ok: true, steps: steps};
"""


def apply(session: Any, spec: StackupSpec, ctx: dict[str, Any]) -> dict[str, Any]:
    """把约束写进立创。**逐条记账**：哪一步成、哪一步没成，都在 `steps` 里。

    段与段只通过工程状态耦合，所以这里只需要 ``ctx`` 里的 ``pcbUuid``。
    """
    import json as _json

    payload = {"copperLayers": spec.copper_layers,
               "netClasses": spec.net_classes,
               "diffPairs": spec.diff_pairs}
    script = _JS_APPLY % {
        "ctx": _json.dumps(ctx, ensure_ascii=False),
        "spec": _json.dumps(payload, ensure_ascii=False),
    }
    value = session.invoke(script, expect=("steps",), label="stackup")
    steps = value.get("steps") or []
    failed = [s for s in steps if not s.get("ok")]
    return {
        "status": "ok" if not failed else "not_ready",
        "steps": steps,
        "failed": [f"{s.get('step')}：{s.get('error')}" for s in failed],
        "spec": spec.to_dict(),
    }


def describe() -> dict[str, Any]:
    """自省：这个模块支持什么、默认值怎么来的。"""
    return {
        "copper_layers_supported": list(COPPER_LAYERS),
        "default_copper_layers": 2,
        "default_source": "assumed（design.json 无 stackup 字段，取立创新建 PCB 默认值）",
        "diff_pair_hints": [f"{a}…{b}" for a, b in DIFF_PAIR_HINTS],
        "why_no_impedance": (
            "阻抗/等长/差分对的数值规则由立创内置；我们只传分组与层数。"
            "搬 Traceforge 那 1200 行等于和立创的规则引擎赛跑。"
        ),
    }