# 长程方案 —— 从「验证器」长成「设计器」

> 这份文件回答三个问题：**怎么从 0 迭代**、**现在到哪**、**下一步做什么**。
> 它是活文档：每完成一层就更新「状态」，不另起一份。
>
> 起点判词（用户）："现在做的太简单，达不到真实项目的复杂度，后续工程师想优化
> 几乎等于重新做"。诊断：差距不在件数（28 vs 316），在**中间三层缺失**——
> 块（复用边界）、约束（为什么这些数对）、变更（改一个谁受影响）。

---

## 0. 目标与验收口径

| 目标 | 验收（机器可判） |
|---|---|
| 需求 → 可打板的半成品 | `audit` 六段有 `checked/basis`；`handoff` 五类待办逐条可执行 |
| 工程师能接着改，不是重做 | 四件套齐全：`spec/vN.md`、`design/vN.json`、`blocks.json`、`ADR/*.md` |
| 每颗元件都有存在理由 | `justify` 归因 + `claims` 断言；删掉会红（`assert --remove`）|
| 每个数是可复核的 | 尺寸/电流/时序都带 `source`（datasheet/measured/catalog/proposed）|

**基准 ≠ 验收**：验收只看需求（`req.json`/`spec`）+ 硬判据；参考工程只做能力回归，
指标只作 `reference`（C2 已降级为可选基线）。

**不做的事**（避免假绿）：不把"生成出来"当"能用"；不把 `unknown` 并进 `pass`；
不替人做不可逆决策（下单/改封装/选关键器件）。

---

## 1. 迭代方法：六层，每层可回退

```
L0 功能    一句话+指标 → req.json + 功能清单 + 约束账本
L1 子系统  功能 → blocks（电源/时钟/存储/通信/传感/执行/保护/调试/机械）
L2 拓扑    块 → 层次网表（总线级），每条网有 class 与规则
L3 选型    能力表+AF+供货+封装+裕量 → BOM/引脚分配
L4 实现    → design.json + interface + fw + mech
L5 验证    → ladder/electrical/mech/fw/emit/audit/handoff；不达标回 L2/L3
```

**回退规则**：失败先回**上一层**，不改结果。每次回退记一条 `ADR`
（决定 / 被否方案 / 触发它的证据），这样"边做边调整"是产物而不是记忆。

**迭代节奏**：一个里程碑只长**一类块**（如"电源树"），长完就跑全链；
不允许一次堆 300 个件再修。

---

## 2. 现状（2026-11-23）

| 层 | 状态 | 证据 / 缺口 |
|---|---|---|
| L0 需求 | ✅ 可用 | `req-draft` → `req.json`（人确认才 apply）|
| L1 子系统 | 🟢 有电路 | `spec` + `design/blocks/*.json`（6 块可实例化）；**6 块全 unverified** |
| L2 拓扑 | 🟡 总线级 | `bus.py` + 块内网表；**规则仍是文字**（A5 才进检查）|
| L3 选型 | 🟡 单型号 | 能力表 14 型号；AF 表 4 型号；**无供货/价格/裕量** |
| L4 实现 | ✅ 骨架 | `emit` 8 段真跑；`generate` 仍是"每链路 1 脚"退化路径存在 |
| L5 验证 | ✅ 强 | 48 个 check、767 单测、阶梯 V0–V6、六段 audit |
| 变更传播 | ❌ 无 | 改 MCU/电源不会联动重算网表/固件/机械/BOM |
| 约束 | 🟢 有账本 | 5 类规则可求值；**阻值/时序还没查** |
| 布局/预算修正（P65） | ✅ | 块布局按尺寸（77→0 重叠）；预算上限读三个来源 |
| 块库扩产（P66） | ✅ | 11 块 / 6 新角色 / 43 件装配；48 个块零件全有 LCSC（23 编号可解析）|
| 发射预检（P67） | ✅ | 23 编号可解析；预检不过不建工程；`--jobs=project` 16–18s |
| 引脚契约（P68） | ✅ | 块 `pin_map`；符号引脚表实测；预检三层（编号/器件/引脚）|
| 导入完整性（P69） | ✅ | 轮询按件数放大；0/半件 → `not_ready`；`expected_on_pcb` |

**上一轮（P60 / A1+A2）新增**：`spec`（块/契约/版本）、`bus`（总线级网表，
引脚按 AF 表挑，凑不出就退回并标 `bus_partial`）。

**本轮（P61 / A3）新增**：`design/blocks/*.json`（6 个块）+ `blocks` 命令
（list/show/validate/compose）；实例化保留 `block=` 来源、全局网合并、
局部网加前缀、单脚网 = 块端口；3 块组合 19 件、6 块 33 件、V0 阶梯 pass。

---

## 3. 路线：三档，按投入产出排序

### A 档（地基，必须先做）

| 编号 | 内容 | 产物 | 判据 | 状态 |
|---|---|---|---|---|
| A1 | 规格层：需求→块/契约/约束 | `spec/vN.json` + `spec.md` | 角色 100% 落块；缺模板进 missing | ✅ P60 |
| A2 | 总线级网表 | `design/bus.py` | 网名 == 脚的功能名（AF 核对）| ✅ P60 |
| A3 | **块库**：块=网表片段+参数+验证记录 | `design/blocks/*.json` + `blocks` 命令 | 每个块能实例化、过 validate；`verified_by` 指向真实工程 | ✅ P61 |
| A4 | **装配**：spec+块 → 设计（`assemble`）；`generate` 保留为无块角色的枚举 | `design/assemble.py` + 命令 | 件数随块增长；块外角色进 uncovered；旧枚举回归不退化 | ✅ P62 |
| A5 | **约束账本**：去耦/上拉/终端/预算/接口 | `constraints.json` + 命令 | 三态求值；`unknown` 不算 pass；`audit` 已消费 | ✅ P62（`ladder/coverify/plan` 待接）|

### B 档（模型扩展）

| 编号 | 内容 | 判据 |
|---|---|---|
| B1 | `Part` 加 `mpn/footprint/alternates/cost/supply/derating` | 往返不丢；非法值报错；空字段不写 | ✅ P63 |
| B2 | `Net` 加 `class/constraints`（单一 `NET_CLASSES`）| `validate` 拦未知 class/坏 constraints/重复引脚 | ✅ P63 |
| B3 | 层次化：`blocks_list` 元数据 + flatten（恒等）| 引用存在；一件只属一块；graph 可见 | ✅ P63 |
| B4 | 变更图 `graph.json`：part/net/block/firmware | 两跳影响面；目标不存在 → not_ready | ✅ P63（不含机械/BOM）|
| B5 | 引脚分配器：AF + 冲突 + 换组 | 不重复用脚；分不出来进 unassigned | ✅ P63 |

### C 档（闭环与回归）

| 编号 | 内容 | 判据 |
|---|---|---|
| C1 | `readback --diff [--apply]`：逐引脚对账；只回写 moved/eda_only | 删除留给人；回写后过 validate | ✅ P64 |
| C2 | **可选基线**（降级）：人显式 record；对比 gate=false、只报告 | 没有基线 → not_ready；不绑定工程名 | ✅ P64 |
| C3 | 成熟度六维（需求/规格/块/约束/证据/发射）进 audit release 段 | 只读本工程；没 req/spec → not_ready | ✅ P64 |
| C4 | `ADR/`：决定/理由/被否方案/证据四节 | 缺节 = failed；空决策拒收 | ✅ P64 |

---

## 4. 里程碑：28 → 316 件

| 里程碑 | 目标 | 新增内容 | 必须过的门 |
|---|---|---|---|
| M1 | ≈60 件 / 1 板（遥控器）| 电源树（USB/BAT→5V→3.3V、LDO、去耦×8、TVS、保险丝）、MCU+双晶振+调试口、USB、2 路摇杆 ADC+RC、6 按键、LED、蜂鸣器 | ladder V0–V3 + mech + fw-build + emit |
| M2 | ≈150 件 / 3 板 | GNSS（模组+LNA+馈电+超级电容）、电源板（DCDC+电流监测+保护）、**板间连接器**（pinout/线序/协议）| 多板 emit + 跨板网连通 + 连接器契约 |
| M3 | ≈316 件 / 8 板 | 图传/云台/数传/避障/存储 | 逐板 emit+audit；对照 golden project 覆盖率 |

每个里程碑的**入口条件**：spec 里对应块已存在且 `verified_by` 非空。

---

## 5. 交付物契约（防"接手=重做"）

| 产物 | 读者 | 必须含 |
|---|---|---|
| `spec/vN.md` / `spec/vN.json` | 人 / 机器 | 功能→块→接口契约→约束；缺的数写 null |
| `blocks.json`（或 `design.blocks`）| 两者 | 块 id、参数、实例位置、`verified_by` |
| `design/vN.json` | 机器 | 版本化；`to_dict→from_dict` 稳定 |
| `ADR/NNN-*.md` | 人 | 决定 / 被否方案 / 触发证据 |
| `open_items.md` / `handoff.md` | 人 | 缺什么、谁补、为什么现在不能打板 |

**回归**：任何改动后跑 `npm test` + 48 个 check + golden project 的 audit 分数，
三项不许同时退化。

---

## 6. 本轮之后（A3）的具体形状

块库一个块：

```json
{
  "id": "power_ldo_3v3",
  "family": "power",
  "title": "5V → 3.3V LDO + 去耦",
  "params": {"vin": 5.0, "vout": 3.3, "iout_ma": 500},
  "parts": [
    {"key": "ldo", "kind": "ams1117-3.3", "size_mm": {"w": 6.5, "h": 3.5, "d": 1.6}},
    {"key": "cin", "kind": "capacitor", "label": "10uF"},
    {"key": "cout", "kind": "capacitor", "label": "22uF"}
  ],
  "nets": [
    {"net": "VIN", "pins": [{"key": "ldo", "pin": "IN"}, {"key": "cin", "pin": "1"}]},
    {"net": "VOUT", "pins": [{"key": "ldo", "pin": "OUT"}, {"key": "cout", "pin": "1"}]},
    {"net": "GND", "pins": [{"key": "ldo", "pin": "GND"}, {"key": "cin", "pin": "2"},
                            {"key": "cout", "pin": "2"}]}
  ],
  "rules": ["输入 10µF + 输出 22µF", "压差 < 1.3V 时校核热耗"],
  "verified_by": [{"project": "...", "ladder": "V3", "date": "..."}]
}
```

**实例化**时 `key → ref`（`U3`、`C21`…），网络名加实例前缀（`PWR1_VOUT`），
`verified_by` 为空就标 `unverified`，**不许进"已验证"统计**。

---

## 7. 怎么更新这份文件

1. 每完成一个编号（A3/B1/…）改「状态」列，并补一行"实测数字"。
2. 新增一档需求时**先加判据**（没有量化判据的能力不进表）。
3. 路线变了不删旧条目，标 `dropped` 并写被什么替代——历史也是证据。