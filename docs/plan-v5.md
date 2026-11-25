# 执行方案 v5 —— 四域同构：从「判定 + 出板」到「一句需求出半成品」

> 这一版不是新方向，是把 v4（出板链）和 Traceforge 的四拍结构（spec → compile →
> deterministic checks → AI review/fix）合起来，**按立创生态重排**成可执行的分段。
>
> 前提约定（沿用仓库纪律）：
> * 判定只在 Python 一份（AGENTS §1.1）
> * 缺数据 ≠ 通过（§0.1）；推断不参与硬判据（§1.2）
> * 每个闸门都要有出口（§1.6）；每条断言都要做退化验证（§4.1）

---

## 0. 基线（2026-11-22 实测，不是估计）

### 0.1 已经能跑通的

| 能力 | 状态 | 实测证据 |
|---|---|---|
| `design.json` 规范模型 | ✅ | parts/nets/boards/placement，`validate()` |
| 立创发射 8 段 | ✅ | project/layout/schematic/pcb/route/pour/verify/outputs **全 ok**；2 板 24 件 185s；Bridge 通道也跑通过一次（76s） |
| 布线 | ✅ | `route=native` 2/2 网、9~16 段 45° 单层、0 过孔；`route=ours` 栅格 A* 兜底 |
| 几何/连通判定 | ✅ | `pcb_geo`（焊盘/走线/过孔/铺铜 + 并查集） |
| 布局规则 | ✅ | `pcb_layout`（连接器出板/中心距 120mil/重叠/边距）+ `--autolayout` |
| 判定层 | ✅ | ladder V0–V6、claims、budget、crosscheck、handoff、verdict、nextstep、entity、sensitivity |
| 固件 | ✅（一半） | `fwgen`：`build_pinmap(netlist)` **与** `from_ioc` 两条入口、scaffold 5 target、drivers、fw-matrix |
| 需求 | ✅ | `req-draft`（draft/validate/apply）、`generate`（有限枚举）、`roles`、`synth`（7 模板） |
| agent 循环 | ✅ | `agent_loop`（append-only journal + 四档审批） |
| 验收 | ✅ | `npm test` 751、check_p17 26/26、check_p26 28/28 |

### 0.2 缺口（本方案要填的）

| 缺口 | 现象 | 归属 |
|---|---|---|
| **机械没有命令** | `mjcf.build()` 存在，`design_cli` 里没有 `mjcf` 键 | P40 |
| **spec 只有三块** | 只有 parts/nets/boards；固件与机械无处落 | P41 |
| **电源/地靠名字猜** | `_power_nets`/`_ground_nets` 正则匹配 GND/+3V3，猜错会铺错网 | P41 |
| **`lcsc` 是可选字段** | 器件身份靠 lcsc 才对得上立创；`JLCPCB Part Class` 不可见 | P41 |
| **关节要人给参数** | `joints` 对 hinge/axle/gear 缺参数直接报错（纪律，不改） | P42（提案层） |
| **没有编排器** | 8 个命令各自为战，模型自己串，两次跑结果可能不同 | P43 |
| **没有机械 checks** | 干涉/重心/扭矩/可达全靠人看 | P44 |
| **DFM 没用上** | `pcb_Dfm.check()` 存在；`failedItems>0` 是"结果不完整"而不是"通过" | P45 |
| **前端零可见** | `@Remote` 37 个里没有 emit；8 段跑完 UI 不变 | P46 |
| **无 doctor / 无自修循环** | 环境不对要逐个试；findings 不会自动回到编辑 | P47 |

---

## 1. 命名与不撞车（AGENTS §3.17）

新增命令前**先 grep**，已确认以下键当前**不存在**：

```
mjcf   doctor   scaffold   joints-propose   dims   dfm
```

`validate` **已被占用**（1 处），所以六段总检叫 **`audit`**，不叫 `validate`。

新增后必须同时：
* `COMMANDS` 加键 + `cmd_*` 加函数（函数名不要和命令键同名混淆）
* `agent_tools.py` 加 `ToolSpec`（工具参数用下划线，`arg_style` 显式声明）
* `scripts/check_p4x.py` 里加一条**正面断言**"老命令还是老行为"

---

## 2. 阶段总览

| 段 | 目标 | 依赖 | 新命令/工具 | 验收脚本 | 规模 |
|---|---|---|---|---|---|
| **P40** ✅ | 机械链打通：能从 `joints.json` 出 MJCF 并被 MuJoCo 加载 | 无 | `mjcf` | `check_p40` | S |
| **P41** ✅ | spec 扩成四域：`firmware` / `mechanical` / `power_nets` / `lcsc` 主键 / `proposed` 分级 | 无 | `dims` | `check_p41` | M |
| **P42** ✅ | 关节提案：几何/limits 推关节，落 `joints.proposed.json`（`source: proposed`） | P40 P41 | `joints-propose` | `check_p42` | M |
| **P43** ✅ | 编排器：一句需求 → 半成品工程（design + pinmap + joints + fw + mjcf + `open_items.md`） | P40–P42 | `scaffold` | `check_p43` | M |
| **P44** ✅ | 机械判据：干涉 / 重心 / 舵机扭矩 / 关节行程，并入 `handoff` | P41 P42 | `mech` | `check_p44` | M |
| **P45** ✅ | 制造证据：`checkDfm` 接入（**它抛 `INCOMPLETE_RESULT`**）+ DRC 分级合并 | 无 | `dfm` | `check_p45` | S |
| **P46** ✅ | 前端两个分区：`emit`（8 段状态 + 产物）/ `brief`（open_items） | P43 | RPC `emitRun`/`emitProgress` | `check_p46` | M |
| **P47** ✅ | 自修循环 + 六段 `audit` + `doctor` + `examples copy` | P43–P46 | `audit`/`doctor`/`examples`/`fixloop` | `check_p47` | L |

**顺序理由**：P40 最小且立刻能验（"MJCF 能被 MuJoCo 加载"是硬判据）；P41 是数据地基（P42/P43 都往它写的块里落）；P43 出来立刻可用（半成品）；P44/P45 是质量；P46 是可见性；P47 才是自动化上限。

---

## 3. 分段详细

### P40 · 机械链打通（`mjcf` 命令）

**目标**：`joints.json` + `design.json` → MJCF → MuJoCo 能加载。现在 `mjcf.build()` 是死代码（没有任何命令调用）。

**改动**
* `design_cli.py`：新增 `cmd_mjcf`，键 `"mjcf"`；动作三档
  * `mjcf check <id>` —— 只构建并**尝试加载**（`mujoco.MjModel.from_xml_path`），不写盘
  * `mjcf build <id> --out=...` —— 落 `<工程>/model.xml`
  * `mjcf info` —— 返回 `mjcf.describe()`（关节种类/参数要求）
* `design/mjcf.py`：`build()` 已有，补一个 `load_check(path)`（MuJoCo 不在位时如实 `not_ready`，不假装通过）
* `design/store.py`：`ARTIFACTS` 登记 `model.xml`

**判据（三态）**
| 输入 | 期望 |
|---|---|
| 有 joints + 尺寸齐 | `build` ok；`load_check` ok |
| 有 joints 但缺参数 | `not_ready` + 点名是哪个关节缺什么（**不编默认值**） |
| 没有 joints | `not_ready`，`note` 写"没有关节表，机械模型无对象" |
| MuJoCo 未装 | `not_ready`，`note` 给安装方式（不记 pass） |

**验收 `check_p40`**
1. `mjcf info` 有 `describe()`
2. 合成设计（2 体 1 hinge，带参数）→ `build` ok + XML 能被 `mujoco` 加载
3. 缺参数 → `not_ready` 且 note 点名关节
4. **退化**：把 `load_check` 改成恒 `True` → 用例 3 必须变红
5. MuJoCo 缺席分支：`not_ready`，不抛异常

**风险**：`mjcf.build` 依赖 `params`（136 个仿真参数里只有 4 个被读，见 §3.16）——本段**不去补 132 个未接线参数**，只用 joints 需要的那些，其余照旧 `unwired`。

---

### P41 · spec 扩成四域（数据地基）

**目标**：让 `design.json` 能表达"固件怎么想"和"机械怎么连"，并把两个"靠名字猜"的地方变成**显式声明**。

**新增字段（全部可选，缺省=空，不猜）**

```jsonc
{
  "power_nets": ["GND", "+3V3"],          // 显式；缺省为空 → 铺铜/加宽退回正则并标 inferred
  "firmware": {
    "target": "stm32",                     // arduino|platformio|esp-idf|stm32|avr
    "mcu": "STM32F401RCT6",
    "pinmap": {                            // net -> 分配
      "I2C1_SCL": {"pin": "PB8", "function": "I2C_SCL", "source": "proposed"}
    },
    "peripherals": [{"kind": "i2c", "instance": "I2C1", "hz": 400000}]
  },
  "mechanical": {
    "joints": [{"a": "servo", "b": "horn", "kind": "hinge",
                "params": {"axis": [0,0,1], "range_deg": 180},
                "source": "proposed", "why": "..."}],
    "limits": {"max_mass_g": 250}
  }
}
```

**同时**
* `parts[]` 增加 `lcsc` 为**主键级**字段（缺就 `unknown`），并带 `jlc_class`（basic/preferred/extended，来自 P19 目录）
* 信任分级加一层 **`proposed`**：`proposed < estimated < catalog < datasheet < measured`
  * 位置：`model.PHYSICAL_SOURCES` / `TRUST_SOURCES`（单一事实源，别新写一份 —— §3.15）
  * `proposed` **不参与硬判据**，但**允许 pipeline 继续**（这是用户明确要求：先出半成品）
* `dims` 命令：从 LCSC 3D `3D Model Title`（如 `C0402_L1.0-W0.5-H0.5`）+ `package_sizes` 解析 L/W/H，缺就 `unknown`

**判据**
| 项 | 期望 |
|---|---|
| 旧 design.json（没有新块） | 仍能读、能发射，`validate()` 不新增硬失败 |
| `power_nets` 显式给 | 铺铜/加宽**只用它**，不再正则猜 |
| `power_nets` 缺省 | 退回正则，但结果标 `inferred` 并进 `sidewarnings` |
| `proposed` 字段参与硬判据 | **必须不参与**（断言：用它算出的 fail 不出现） |
| 往返 | `to_dict → from_dict → to_dict` 逐字节一致（新块不能丢） |

**验收 `check_p41`**
1. 新块缺省时向后兼容（用现有 15 个 fixture 全跑一遍往返）
2. `power_nets` 显式 / 缺省两条路径行为不同且**都标注来源**
3. `proposed` 不参与硬判据（构造一个"只有 proposed 数据"的设计，`ladder` 不许因它 fail）
4. `dims` 能从 3D Title 解出 L/W/H；给不出时 `unknown`（不填 0）
5. **退化**：把 `proposed` 当成 `datasheet` → 用例 3 必须变红

---

### P42 · 关节提案（`joints-propose`）

**目标**：把"人必须给关节参数"变成"先给一份提案，人再改"——同时**不动 `joints` 的纪律**。

**改动**
* `design/joint_propose.py`（新）：输入 `design.json`（parts + 尺寸 + roles 模板）→ 输出 `joints.proposed.json`
  * 依据优先级：`roles` 模板（camera_module/gimbal_servo…） > LCSC 3D 尺寸 > `package_sizes` > **放弃**
  * 每条带 `source: "proposed"` + `why`（"舵机轴→舵盘，按 25T 标准估"）
* `design_cli.py`：`joints-propose <id> [--apply]`
  * 默认只写 `joints.proposed.json`（**不覆盖** `joints.json`）
  * `--apply` 才把提案写进 `design.mechanical.joints`（仍标 `proposed`）

**判据**
| 项 | 期望 |
|---|---|
| 能推的关节 | 进提案，带 `why` |
| 推不出的 | **不进提案**（不编），进 `open_items` |
| `joints` 命令 | 行为**完全不变**（缺参数仍报 error）—— 验收要正面断言这条 |
| 提案被 `--apply` 后 | `joints` 能跑通（因为参数有了，来源是 `proposed`） |

**验收 `check_p42`**
1. 提案文件生成；每条有 `source/why`
2. 推不出的不编造（`len(proposals)` 等于"能推的"条数）
3. `joints` 老行为不变（删掉参数 → 仍报 error）
4. **退化**：让 proposer 对推不出的也编一个默认值 → 用例 2 必须变红

---

### P43 · 编排器（`scaffold`）

**目标**：一条命令把零件串起来，**永不因缺件中止**。

```
scaffold <req-file> --project=<id> [--route=generate|synth|roles]
                    [--template=...] [--to=design|fw|mech|pcb|all]
```

**流程（每步写一行进 `scaffold.json`）**
1. `req` 校验（`req_draft.validate`）
2. 选路（显式；不给就报错，**不猜**）
3. 出 `design.json`（走 `hw_put` 的 `validate()` 路径，**不绕过**）
4. `pinmap` 提案（`build_pinmap(netlist)`）→ `design.firmware.pinmap`（`proposed`）
5. `joints-propose` → `design.mechanical.joints`（`proposed`）
6. `fw-scaffold` + `fw-driver`
7. `mjcf build`
8. `emit`（仅 `--to pcb|all`）
9. 汇总 `scaffold.json` + `open_items.md`

**`open_items.md` 的规矩（不做闸门）**
* 只写"我没把握的"：`proposed` 的每条、`unknown` 的尺寸、被跳过的段
* **不影响 exit code / status**：半成品允许带 open items
* 每条一行、可执行：`核对 PA15 是否真的是 VL53 中断脚（proposed）`

**判据**
| 项 | 期望 |
|---|---|
| 缺 `.ioc` | 固件仍出（走网表入口），`open_items` 记一条 |
| 缺关节参数 | 机械仍出（走提案），`open_items` 记一条 |
| 两次同输入 | `scaffold.json` 除时间戳外**逐字节一致**（证明不是模型拼的） |
| req 非法 | `failed` + 具体原因，不产半成品 |

**验收 `check_p43`**
1. 一句 blink 需求 → 目录里有 design.json / pinmap / joints.proposed / fw 骨架 / model.xml / open_items.md
2. 两次跑一致（去掉时间戳比较）
3. `--to all` 在本机不装 lceda 时：`pcb` 段 `not_ready`，**其余照旧**（不整段失败）
4. **退化**：把"缺件即跳过"改成"缺件即中止" → 用例 1 必须变红（证明"不中止"是实现的而不是巧合）

---

### P44 · 机械判据（并入 handoff）

**新增四条（`design/mech_check.py`）**
1. **干涉**：两体 AABB/OBB 重叠（含运动包络）
2. **重心**：质量加权重心落在支撑多边形内（站不稳会翻）
3. **舵机扭矩**：`需求扭矩 = 质量 × 力臂`，与所选舵机的 `stall_torque` 比（缺舵机参数 → `unknown`）
4. **关节可达**：`range_deg` 覆盖需求行程（需求来自 `numbers`/`constraints`，缺就 `unknown`）

**判据**：每条都要 `checked`（查了几项）+ `basis`（凭据）；空输入 `not_ready`；`unknown` 不并入 blocking，进 `handoff` 的"缺数据/不会验"类。

**验收 `check_p44`**
1. 合成夹具：两体重叠 → 干涉 fail；重心移出支撑 → fail
2. 舵机参数缺 → `unknown`（不是 pass，也不是 fail）
3. **退化**：把"重心"判据删掉 → 对应用例必须变红

---

### P45 · 制造证据（DFM 接入）

**目标**：把立创自己的 DFM 也当证据，并且**正确理解它**。

**改动**
* `design/fabrication.py`：合并三路证据
  * `pcb_Drc.check` → 分类（已有 `_drc_categories`）
  * **`pcb_Dfm.check`**（新）→ `issues / failedItems / warnings`
  * 自建几何（`pcb_geo`）
* **关键映射**：`failedItems > 0` → **`unknown`（结果不完整）**，绝不当 pass；`warnings` 含 `DFM_NOT_CONFIGURED` → `not_ready` + "去立创里配规则"

**判据**：可信 = `clearance + DFM issues`；`connection` 只提示（沿用 §3.20/§3.27 的结论）。

**验收 `check_p45`**
1. 缺规则配置 → `not_ready`（不是 ok）
2. `failedItems>0` → `unknown`
3. **退化**：把 `failedItems>0` 当 0 → 用例 2 变红

---

### P46 · 前端两个分区

**目标**：让 P43 的产物在 UI 里看得见。

**后端**
* `snapshot` 增 `emit?: EmitSummary`（8 段状态 + verify + artifacts + channel）与 `scaffold?: ScaffoldSummary`（各段是否产出 + open_items 计数）
* RPC：`emitRun(project, opts)`、`emitProgress(project)`
* `ARTIFACTS` 登记 `scaffold.json` / `model.xml` / `open_items.md`

**前端**
* 分区 `emit`（交付组，"制造"之前）：8 段状态条 + 三个按钮（发射 / 只重验 / 打开产物目录）
* 分区 `brief`（半成品）：`open_items` 列表（可点 → 跳到对应位号/字段）
* 同步三处：`SectionId` `SECTIONS` `SECTION_LABEL` `isSectionId`；`hw_emit` 的 `viewHint` 从 `'checks'` 改成 `'emit'`
* 顺手修：`hw_roles` 的 `viewHint(project, '2d')` 是**无效分区**（`isSectionId('2d')===false`），改成 `blocks` 或删掉

**验收 `check_p46`**：TS 源码断言（新分区已 import 并渲染）+ RPC 存在于 `service.ts`/`rpc.ts` + `isSectionId('emit')===true` + 无 `'2d'` 遗留。

---

### P47 · 自修循环 + 六段 audit + doctor + examples

**① 六段 `audit`**（键 `"audit"`，不是 `validate`）
```
intake(req) → architecture(units/roles) → parts(lcsc/jlc_class)
→ schematic+parity(必过回读) → pcb(几何 + DRC 分级 + DFM)
→ firmware(fw-matrix) → mechanical(mjcf 加载 + 四条) → release(handoff)
```
每段输出 `{stage, status, checked, basis, findings[]}`；**确定性先行**，只有需要判断的字段才调模型。

**② fix loop**（扩 `agent_loop`）
* 输入：`audit`/`handoff` 的 findings（带 `fix.need`）
* 工具：已有的结构化写工具（`hw_setpart`/`hw_put`/`move`/`setBudgetLimits`…）
* **上限**：最多 N 轮（默认 3）且**连续 2 轮无改善即停**（与 §1.4 一致）
* 每轮落 `journal.jsonl`（append-only），可重放

**③ `doctor`**（借 Traceforge）
一条命令报：`lceda exe/version`、`channel(cli/bridge 连没连)`、`LCSC 可达`、`MuJoCo`、`arduino-cli`、`gcc`、`ffmpeg`、`workspace 可写`。每项三态。

**④ `examples copy`**
自带 3 个可抄的半成品：`blink`（最小闭环）/ `i2c-sensor`（含固件）/ `gimbal`（含机械关节）。`examples copy <name> --to <project>` 复制后即可 `emit`。

**验收 `check_p47`**：六段都产出 stage 行；fix loop 在"连续两轮无改善"时必须停（构造夹具）；`doctor` 在缺 lceda 时如实 `not_ready`；`examples copy` 后能直接 emit。

---

## 4. 完成定义（每段都必须满足）

1. `python -m dsh_embagent.design_cli <新命令>` 有 `describe()`（自省）
2. `scripts/check_p4x.py` 能一条命令跑，且**至少一条退化验证**
3. `npm test` 全绿 + 新用例覆盖三态
4. `AGENTS.md` 新增该段踩的坑（有实测才写）+ §6 缺口表更新
5. `CHANGES.md` 加一节（实测数字，不写形容词）
6. 清理夹具（AGENTS §4.4）

---

## 5. 全局不做（避免假绿）

| 不做 | 原因 |
|---|---|
| 让 `proposed` 参与硬判据 | 提案会错；那是 §1.2 的反面 |
| 替 `joints` 编默认参数 | 它现在报 error 是对的；提案走独立文件 |
| 把 `DFM.failedItems>0` 当 0 | 那是"结果不完整" |
| 给 132 个未接线仿真参数补默认值 | 那是"接哪个由人定" |
| 为哈希 `kind` 补映射 | 用户已决定 |
| 无限轮自动修 | 会震荡；上限 + 无改善即停 |
| 搬 KiCad 的 S-expr/ERC/symbol:footprint | 立创是 JSON+API，且**没有 ERC** |

---

## 6. 第一刀（P40）的开工清单

1. `design/mjcf.py`：加 `load_check(xml_path)`（MuJoCo 缺席 → `not_ready`）
2. `design_cli.py`：`cmd_mjcf`（check/build/info）+ `COMMANDS["mjcf"]`
3. `design/store.py`：`ARTIFACTS` 加 `model.xml`
4. `agent_tools.py`：`hw_mjcf` ToolSpec（action/build/info）
5. `scripts/check_p40.py`：5 条（含 1 条退化）
6. `AGENTS.md` §8.7 加 `check_p40`；`CHANGES.md` 加 P40 一节

**完成信号**：`python -m dsh_embagent.design_cli mjcf check <工程>` 在一份带关节的设计上返回 `ok`，且 `model.xml` 能被 MuJoCo 加载；缺参数时返回 `not_ready` 并点名关节。