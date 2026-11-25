"""仿真参数 —— 136 项物理参数的**全量体系**，按物理域分组。

## 为什么全搬（而不只搬无人机需要的）

RoboBuilder 的 136 项参数是有结构的：

| 域 | 项数 | 含义 |
|---|---|---|
| mesh | 18 | 齿轮啮合（齿力上限/跳齿/打滑/离合器） |
| stud | 17 | 凸柱连接（抗拔/滑移/剪切/摇摆/剥离） |
| tyre | 13 | 轮胎与地面（摩擦/滚阻/软度/圆角） |
| axle | 12 | 轴与轴承（摩擦/滑动/止挡） |
| motor | 11 | 电机（堵转/空载/摩擦/内阻） |
| cable | 10 | 线缆（质量/弯曲/扭转/接触） |
| cord | 10 | 绳索（张力/下垂/动态） |
| servo | 8 | 舵机（扭矩/速度/死区/堵转） |
| chain | 6 | 链条（跳齿/卡死/啮合窗口） |
| solver | 5 | 求解器（步长/刚化/生成间隙） |
| course | 5 | 场地（接触软度/动态件/静置） |
| pullback | 5 | 发条（扭矩/转速/圈数） |
| tread | 5 | 履带 |
| rack | 4 | 齿条 |
| mechanism | 4 | 差速/等速万向节/减震 |
| program | 3 | 程序与判定扰动 |

**通用性来自"标记"而不是"裁剪"**：
`universal=True` 的 31 项任何刚体仿真都用（dt / mu / 接触软度 / 电机）；
`universal=False` 的 105 项只有对应机构存在时才有意义。

无人机用不到 `stud_*`，但**不删**——因为：
1. 同一套代码要能仿真乐高式结构（你的 `示例-单板` 有接插件、有外壳）
2. 删了以后要加回来时，"这个参数原来是什么含义"就得重新查源码
3. `requires` 字段让"该不该读这组"变成**可判断的**，而不是靠人记

## 三档来源（与 `capability.py` 的信任级同一套）

```
public(=estimated) < catalog < datasheet < measured
```

* `public` —— 从 RoboBuilder 公开零件数据搬来的初始估计，**不是我们实测的**
* `datasheet` —— 某型号手册上的值
* `measured` —— 我们自己量的（P21 闭环产出）

**闸门**：`public` 级的参数可以让仿真跑起来（给个起点），
但 P18 判定时要把"这批结论基于哪一档参数"标出来——
用 public 参数跑出的 `pass` 是 **`pass(public)`**，不是 `pass`。
这是 P21 那条"重放旧失败"校验存在的原因。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 默认参数表（136 项，由 `scripts/gen_sim_params.py` 从 RoboBuilder 全量搬运）
DEFAULT_FILE = Path(__file__).resolve().parent / "sim_params_default.json"

#: 信任级（数字越大越可信）。与 `capability.TRUSTED_SOURCES` 同一套思想。
TRUST_LEVEL = {"public": 1, "catalog": 1, "datasheet": 2, "measured": 3}

#: 参数表里可以出现的域。用于校验"分组没拼错"。
GROUPS = (
    "solver", "motor", "mesh", "stud", "axle", "tyre", "chain",
    "cord", "cable", "servo", "pullback", "tread", "rack",
    "course", "mechanism", "program",
)


@dataclass
class Param:
    """一个物理参数。

    `value` 是数字/数字列表/布尔/字符串（MuJoCo 的 solref 之类是元组）。
    `note` 写**物理含义**——这一项是全搬的核心价值：没有含义的参数名
    没人敢改，而改错一个 `stud_pull_n` 会让整个装配体散架。
    """

    key: str
    value: Any
    group: str
    unit: str = ""
    note: str = ""
    universal: bool = True
    source: str = "public"

    @property
    def trust(self) -> int:
        return TRUST_LEVEL.get(str(self.source), 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value, "group": self.group, "unit": self.unit,
            "note": self.note, "universal": self.universal,
            "source": self.source,
        }


@dataclass
class ParamSet:
    """一套完整参数。可以有多个版本（initial / learned / 设计级覆盖）。"""

    label: str = "initial"
    origin: str = ""
    note: str = ""
    params: dict[str, Param] = field(default_factory=dict)

    # -- 读取 --------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        param = self.params.get(key)
        return param.value if param is not None else default

    def __contains__(self, key: str) -> bool:
        return key in self.params

    def group(self, name: str) -> dict[str, Any]:
        if name not in GROUPS:
            raise ValueError(f"未知物理域 {name!r}（可用：{', '.join(GROUPS)}）")
        return {k: p.value for k, p in self.params.items() if p.group == name}

    def groups_present(self) -> list[str]:
        return sorted({p.group for p in self.params.values()})

    # -- 变更 --------------------------------------------------------------

    def with_params(self, overrides: dict[str, Any], *,
                    source: str = "measured",
                    label: str = "") -> "ParamSet":
        """返回**新**参数集：覆盖若干项并提升它们的来源。

        不原地改：参数集要能和旧版本对比（`diff()`），
        原地改就没有"改之前是什么"了。
        """
        merged = {
            key: Param(key=key, value=p.value, group=p.group, unit=p.unit,
                       note=p.note, universal=p.universal, source=p.source)
            for key, p in self.params.items()
        }
        for key, value in overrides.items():
            if key in merged:
                merged[key].value = value
                merged[key].source = source
            else:
                merged[key] = Param(
                    key=key, value=value, group="unknown",
                    note="新增项（来源表里没有，需人工补分组）",
                    source=source)
        return ParamSet(
            label=label or f"{self.label}+{len(overrides)}项",
            origin=self.origin, note=self.note, params=merged)

    def diff(self, other: "ParamSet") -> list[dict[str, Any]]:
        """对比两套参数，返回**值不同或来源不同**的项。

        用途：`initial` vs `learned` 的差异就是校准成果——
        RoboBuilder 实测 136 项里 135 项被校准改变，其中多项是
        **从 0 变非 0**（= 初始仿真器根本没建模这项物理）。
        """
        rows: list[dict[str, Any]] = []
        for key in sorted(set(self.params) | set(other.params)):
            a = self.params.get(key)
            b = other.params.get(key)
            if a is None and b is not None:
                rows.append({"key": key, "from": None, "to": b.value,
                             "kind": "added", "group": b.group})
                continue
            if b is None and a is not None:
                rows.append({"key": key, "from": a.value, "to": None,
                             "kind": "removed", "group": a.group})
                continue
            if a is None or b is None:            # pragma: no cover
                continue
            if a.value == b.value and a.source == b.source:
                continue
            kind = "value"
            if a.value in (0, 0.0, False, [], None) and b.value not in (
                    0, 0.0, False, [], None):
                # **这一类最要紧**：初始是 0（= 没有这项物理），校准后非 0
                # （= 加进去了）。RoboBuilder 的 0/15 失败就出在这里：
                # `mu_tyre_rod=0`、`mesh_mu=0`、`supply_r=0`、`stud_k_n_m=0`
                kind = "turned_on"
            rows.append({
                "key": key, "from": a.value, "to": b.value, "kind": kind,
                "group": a.group, "unit": a.unit, "note": a.note,
            })
        return rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "hwv-sim-params/1",
            "label": self.label, "origin": self.origin, "note": self.note,
            "groups": self.groups_present(),
            "params": {k: p.to_dict() for k, p in self.params.items()},
        }

    # -- 报告 --------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        by_group: dict[str, int] = {}
        by_trust: dict[str, int] = {}
        for param in self.params.values():
            by_group[param.group] = by_group.get(param.group, 0) + 1
            by_trust[param.source] = by_trust.get(param.source, 0) + 1
        trusted = sum(1 for p in self.params.values() if p.trust >= 2)
        return {
            "label": self.label,
            "total": len(self.params),
            "by_group": by_group,
            "by_trust": by_trust,
            "trusted": trusted,
            # **这个比例必须报**：全 public 意味着仿真结论全是"基于估计"
            "trusted_ratio": round(trusted / len(self.params), 4)
            if self.params else 0.0,
            "universal": sum(1 for p in self.params.values() if p.universal),
        }


# ---------------------------------------------------------------------------
# 加载 / 保存
# ---------------------------------------------------------------------------

def load(path: str | Path | None = None, *, label: str = "") -> ParamSet:
    """从 JSON 读一套参数。默认读 `sim_params_default.json`。

    文件不存在时**报错而不是返回空表**：空参数集会让 MJCF 生成器
    用代码默认值，那等于"悄悄用了一套不是表里的物理"。
    """
    target = Path(path) if path is not None else DEFAULT_FILE
    if not target.is_file():
        raise FileNotFoundError(
            f"参数表不存在：{target}\n"
            f"默认表应由 scripts/gen_sim_params.py 生成")
    data = json.loads(target.read_text(encoding="utf-8"))
    raw = data.get("params") or {}
    params: dict[str, Param] = {}
    for key, row in raw.items():
        if not isinstance(row, dict):
            raise ValueError(f"参数 {key} 必须是对象（含 value/group）")
        group = str(row.get("group") or "")
        if group not in GROUPS:
            raise ValueError(
                f"参数 {key} 的 group={group!r} 不在已知域里"
                f"（可用：{', '.join(GROUPS)}）")
        params[key] = Param(
            key=key,
            value=row.get("value"),
            group=group,
            unit=str(row.get("unit") or ""),
            note=str(row.get("note") or ""),
            universal=bool(row.get("universal", True)),
            source=str(row.get("source") or "public"),
        )
    return ParamSet(
        label=label or str(data.get("label") or "custom"),
        origin=str(data.get("origin") or ""),
        note=str(data.get("note") or ""),
        params=params)


def save(param_set: ParamSet, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(param_set.to_dict(), ensure_ascii=False,
                                 indent=1) + "\n", encoding="utf-8")
    return target


def effective(design: Any, *, base: ParamSet | None = None) -> ParamSet:
    """**设计级覆盖**后的有效参数。

    `design.sim_params` 里存的覆盖项（P15 加的字段）叠在基准表上。
    这样"这个工程用了哪些与默认不同的参数"是**可查的**，
    而不是散在代码里的常量。
    """
    base_set = base if base is not None else load()
    overrides = dict(getattr(design, "sim_params", {}) or {})
    if not overrides:
        return base_set
    # 设计级覆盖同样要过一遍 schema：值可能是 {"value": x, "source": y}
    flat: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for key, value in overrides.items():
        if isinstance(value, dict) and "value" in value:
            flat[key] = value["value"]
            sources[key] = str(value.get("source") or "measured")
        else:
            flat[key] = value
            sources[key] = "measured"
    merged = base_set.with_params(flat, source="measured", label="design-override")
    for key, source in sources.items():
        if key in merged.params:
            merged.params[key].source = source
    return merged


def describe() -> dict[str, Any]:
    return {
        "module": "dsh_embagent.design.sim_params",
        "default_file": DEFAULT_FILE.name,
        "groups": list(GROUPS),
        "trust_levels": TRUST_LEVEL,
        "ported_from": "RoboBuilder v1 (MIT) initial.json / sim/csim.py Params",
        "count": len(load().params) if DEFAULT_FILE.is_file() else 0,
        "discipline": (
            "public 级参数让仿真能跑起来，但判定要标出结论基于哪一档；"
            "设计级覆盖存 design.sim_params，与默认表的差异可查"),
    }