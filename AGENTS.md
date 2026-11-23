# AGENTS.md — 给接手这个仓库的 AI / 人

> 本文件记录**开发过程中真实踩过的坑**。每条都注明是"实测"还是"推断"，
> 以及错了会怎样。没有实测依据的条目不该写在这里。
>
> 面向读者：要改这个插件的 AI agent，或第一次接手的人。
> 读它能省掉重复踩坑；不读会重复犯错，而其中几个错误**看起来像成功**。

---

## 0. 三条最容易犯的错（先看这三条）

### 0.1 「缺失」被写成「通过」

这是本项目反复出现、且**最危险**的错误形状：没有数据时算出 `ok=True`。

已发生的实例：

| 位置 | 症状 | 根因 |
|---|---|---|
| `electrical.py` | 零负载轨算出 `ok=True` | `load_a = total_load` 赋成 `0.0` 后，`if not loads and load_a is None` 永远不成立 |
| `handoff.py` | 「机器用了默认值」永远是 0 条 | 去 `budget.json` 找 `coverify` 字段，那个字段从来不存在 |
| `claims.derive()` | 删掉元件只报 `connected` | 断言没消费 `justify` 的结论，删了只是"变安静" |
| `links.validate()` | 链路表为空时 `ok=True` | 空表被当成"检查通过"而不是"没检查" |

**写代码时的自检**：任何 `ok` / `pass` 的计算，问一句——
"如果输入是空的，这里会返回什么？" 如果返回 `True`，就是 bug。

**约定**：三态 `ok` / `not_ready` / `failed`，或 `pass` / `fail` / `unknown`。
`not_ready` 与 `unknown` **不是** `pass`。`design/budgets.py:149` 是正确写法的样板：

```python
"status": "ok" if confidence != "none" else "not_ready"
```

### 0.2 空表看起来像「没问题」

比 0.1 更隐蔽：报告里某一类空着，人以为"这类没有"，真相是"没人去看"。

实例：`coverify` 有 7 条 `not_verified`（指向精度 1.0°、跟踪角速度 45°/s
全是默认值），但因为 `_assumed()` 读错了位置，交接单第三类一条都没报。
**空表 + 无说明 = 假绿。**

**约定**：每类检查必须同时输出 `checked`（查了几项）与 `basis`（凭据在哪）。
`handoff` 的做法：产物缺失时 `sources` 里标 `missing`，渲染成 `_无。_`
而不是省略整节。

### 0.3 挑了错的夹具，把「正确行为」当成 bug

验 P1（硬失败截断）时用了真实工程 `示例-单板`，看到 `stopped_at=""`
就以为截断坏了。**其实是对的**：它的 V2 是 `fail`，而 `HARD_LEVELS`
只有 `V0`/`V1`——几何重叠不影响电气判决，不该截断。

同类问题：验 P1 必须用**极性反接**的合成夹具（12V 接进额定 3.6V 的 MCU 脚）。

**约定**：每条硬判据要区分两种夹具——
* **合成夹具**证明「判据本身对」（真实数据恰好不触发某个失败时，用它验不了）
* **真实工程**证明「在脏数据上不崩、结论有用」

这个区分写死在 `scripts/check_m1.py` 的文件头注释里。

---

## 1. 本项目的核心纪律

### 1.1 判定只在 Python 一份

TS 只做编排，不做判定。所有结论来自 `design_cli.py` 的一行 JSON。

**违反的后果**：同一件事有两个事实源，早晚分叉。这个仓库已经有过
"TS 侧按猜的字段名读 Python 产物"的事故（见 §3.2）。

### 1.2 `inferred` 永不参与硬检查

从推断得到的结论（链路、能力值、归因）**只提示，不判 fail**。

理由：推断会错。如果推断能让人 fail，人会开始怀疑检查器而不是自己的设计
——那比没有检查更糟。

**已实现的位置**（行号为写此文时）：
* `links.py:318`：`if source == "inferred": continue`
* `capability.py:203`：`TRUSTED_SOURCES = {"datasheet","measured"}`，
  `candidates()` 里 `if not trusted(key): continue`
* `renode.py:560`：`inferred` 链路不生成 `connector Connect`
* `ioc_links.py:379`：`if only_declared and source != "declared": continue`;

**每条都有回归测试**，且都做过退化验证（把闸门去掉，确认测试真的红）。

**警告**：这些闸门是 `if ... continue` 一行，去掉它不会有任何测试失败
——因为「什么都没发生」正是期望结果。**必须专门写「闸门生效」的断言**
（如 `added == 0`），否则退化后无人发现。P8 就是这么踩过一次（见 §1.6）。

### 1.3 不为导入的哈希 kind 补映射（用户决定）

工程里 `kind` 是 EasyEDA 哈希（`5e2c0505ab5b0d1e` 这类），判不出型号。
**不要**为它们补一张映射表来"提高覆盖率"——那是过拟合单个工程。

生成器（`generate.py`）**只对新工程有效**。这是用户的明确决定。

### 1.4 生成器是有限枚举，不是收敛循环

```
枚举 ≤ max_candidates → 批量验证 → 按 (硬失败, 软失败, unknown, 成本) 排序
```

**不要**改成 `while not ok: 改设计`。收敛性无法证明，而枚举天然终止。
代价是不保证全局最优——对"AI 生成可运行初版、工程师二次开发"这个目标，
**可行解比最优解重要**。

### 1.5 推导结果不自动写回 design.json

`justify` / `units` / `ioc_links` 默认只读，`--write` / `--apply` 才落盘。
理由：推导结果混进规范模型后，分不清哪条是人说的。

### 1.6 闸门必须有出口，否则功能成了死胡同

这是 §1.2 那套 `inferred` 闸门**配套的必要条件**，单独列出来因为它踩过。

P8 第一版：从 `.ioc` 抽出 13 条链路（可信度高的，同名外设 + TX↔RX 交叉），
全部标 `inferred`；`apply_draft(only_declared=True)` 一条都不写。
结果：链路写不进设计，`links.validate` 仍返回 `not_ready`，
**用户看到的和「根本没做抽取」一模一样**。

错在把纪律实现成纯闸门：纪律说「推断不能自动生效」，
没说「那要怎样才生效」。缺了后者，严格执行纪律 = 功能完全不可用。

**修法**：闸门 + 确认通道，两者成对出现。

| 闸门 | 出口 |
|---|---|
| `links.validate` 跳过 `inferred` | 人把 `source` 改成 `declared` |
| `capability` 跳过非 `datasheet` | 扩表（补真实型号与来源） |
| `ioc_links.apply_draft(only_declared=True)` | `promote(draft, ids)` 逐条确认 |
| `renode` 不为 `inferred` 链路接线 | 同上（先 promote 才会生成 connector） |

**注意这个陷阱**：闸门写成 `if ... continue` 一行，去掉它不会有任何测试失败
——因为「什么都没发生」正是期望结果。所以每条闸门**必须专写一条
「闸门生效」的断言**（`added == 0`、`connectors == []`），
否则退化后无人发现。这一条我在 M2/M3 各踩了一次。

---

## 2. 数据与格式的实测事实（别再重新试一遍）

### 2.1 工程实测数字

工作区 `C:\Users\<user>\Desktop\示例工作区`，5 个工程全部 `partial`：

| 工程 | 元件 | 网表覆盖 | boards |
|---|---|---|---|
| `示例-单板` | 141 | 141/141 | 1 |
| `示例-多板` | 316 | **141/316**（175 个没网表） | 8 |
| `示例-无人机` | 44 | **0/44** | 1 |
| `001-blink` | 3 | 3/3 | 1 |
| `r3demo` | 4 | 4/4 | 1 |

`示例-多板` 的 8 块板里**只有 1 块有具名网络**（64 条），
其余 7 块 0 条。这个差别本身就是结论，不要试图"补齐"它。

### 2.2 `.ioc` 的真实内容（计划里写错了）

`docs/execution-plan.md` §11.2 原写「258 引脚 / 32 个串口 / **0 label**」。
**实测是 186 / 77 / 84**。84 个 label 是配对的真实线索
（`RF_*` / `VL53` / `BMM_*` / `PMW_*`）。

解析形状：
```
PA2.Mode=Asynchronous
PA2.Signal=USART2_TX          ← 端点
PA15.GPIO_Label=VL53-PWR-EN   ← 语义线索
Mcu.UserName=STM32F401RCTx    ← 型号
```

### 2.3 板名无法与 `.ioc` 自动对应

板名是 `EasyEDA 96.1×85.9mm`（尺寸串），`.ioc` 是 `F401-Front`。
**两者无任何交集**，硬按子串匹配只会静默失配。

所以 MCU 映射要**显式给**：
```bash
--board-mcu '{"PCB#a22effc90c196540":"STM32H743IIT6", ...}'
```
`--ioc-root` 会列出 7 个可选型号及对应平台，照着填。

### 2.4 Cortex-M `.bin` 前 8 字节就是向量表

```
offset 0: 初始 SP        （落在 0x2000_0000 区，4 字节对齐）
offset 4: Reset_Handler  （落在 0x0800_0000 区，最低位=1 表示 Thumb）
```

实测 6 个固件全部可定位，**不需要 ARM 工具链**：

| 固件 | SP | Reset |
|---|---|---|
| `F103C8T6-GPS.bin` | `0x20005000`（20K） | `0x080033F5` |
| `F401RCT6-TOF光流.bin` | `0x20010000`（64K） | `0x080055E5` |
| `F405RGT6遥控器V1.bin` | `0x20020000`（128K） | `0x08002C1D` |

`wifi.bin` 例外（SP=`0x3F0204E9` 不在 SRAM），`vector_table()` 会如实拒绝。

### 2.5 自动网名的多种写法

`$1N8009`（立创 EDA）、`w1`/`w12`（Wokwi）。判据：

```python
_AUTO_NET_RE = re.compile(r"^\$|^[wW]\d+$")
```

`justify` / `units` / `links` 都用这一套。**新增模块时复用它**，
不要各自写一份。

---

## 3. 具体踩过的技术坑

### 3.1 ES 模板字面量会吞掉反引号和反斜杠

`src/client/styles.ts` 是 TS 模板字面量。里面的 CSS 注释**不能含反引号**
——会截断整个字面量，报错位置离真凶很远。

同理，在 `check_*.mjs` 的模板字面量里写正则，`\d` 会被吃掉。
实测症状：`__probe is not a function`，因为正则被破坏成乱码。

**约定**：`styles.ts` 里不出现反引号；测试脚本里避免在模板字面量内写正则，
改用字符串方法。

### 3.2 TS 读 Python 产物时，字段名必须对着真实输出核

`hw_coverify` 第一版按 `id` / `status` / `counts.passed` 读，实际字段是
`check_id` / `passed`（布尔）/ `summary.total`。

`tsc` 抓不到（都是 `Record<string, unknown>`），运行时静默读成空，
工具报「通过 0 / 失败 0」而 `ok=false`——自相矛盾。

**约定**：新增工具时，先跑一次 CLI 看真实 JSON 的键名，再写 `execute`。

### 3.3 中文引号会破坏 Python 字符串

`"通过"` 这类中文引号进了字符串字面量会提前结束字符串（实测多次）。
**约定**：注释/文档字符串随便用；**字符串值里**改用「」。（详见 runbook §9）

### 3.4–3.6 环境类坑（Renode 锁 / Velxio 计划任务 / 后端 77 秒）

> 全文在 [`docs/runbook.md`](docs/runbook.md)「§9」。要点：Renode 在 DSH 沙箱内
> 因 `cache_lock` 被拒而**跑不起来**（旁路全试过）；Velxio 后端要靠 `schtasks`
> 存活；后端导入 77 秒的根因是模块级网络调用（修法：venv 的 `sitecustomize.py`）。

### 3.7 pydantic v2 的 `extra="ignore"` 会静默吃掉错参数

`velxio_compile` 曾把 `board` 发成 `board_fqbn`，pydantic 默认
`extra="ignore"` 静默丢弃，于是永远编译到 uno。

修法：`_compile_body()` 做字段守卫。验证方式：三个 board 必须产出
三个不同的 hex md5。

### 3.8 Python imports 的两种错误

* `ModuleNotFoundError: No module named 'hw_velxio'` → 不在仓库根跑。
  `sys.path.insert(0, str(REPO_ROOT))`，`REPO_ROOT` 从 `__file__` 推。
* `TypeError: 'X' object is not subscriptable` → 判定器返回的是 dataclass，
  要 `.to_dict()`，不是 dict。

### 3.9 目录数据不是契约 API：形状变了要报 not_ready，不是 0 条

`jlcpcb.com` 的搜索接口返回是 `data.componentPageInfo.list[]`，
**不是**文档/计划里写的 `data.componentList[]`。按错键取会得到
`[]`，而 `[]` 看起来和"真的没搜到"一模一样。

**约定**：解析后先判**外层形状**在不在（`componentPageInfo` 是不是对象），
不在就 `not_ready` + 打印实际的键名。这条和 AGENTS §0.1 是同一件事：
"接口变了"不能伪装成"搜不到"。

另外两条实测：

* `encapsulationNumber` 是**每盘颗数**（ATtiny85 上 = 95），既不是尺寸也不是
  引脚数。目录里凡是"名字像数"的字段，用之前先确认它是什么。
* 停产不能只看 `componentStatus`（ATtiny85 是 `yes`）。要看 `noBuyReason`
  （"no longer manufactured"）与库存。**反过来也一样**：字段全缺时生命周期是
  `unknown`，不是 `normal` —— 否则空对象会算出"正常"。

### 3.10 `.ioc` 里 GPIO 输出**没有** `.Mode` 行——只看 Mode 会漏掉所有带 label 的脚

CubeMX 的写法不统一：

```
PA2.Mode=Asynchronous          ← 外设脚有 Mode
PA2.Signal=USART2_TX

PA15.GPIOParameters=PinState,GPIO_Label
PA15.GPIO_Label=VL53-PWR-EN    ← 普通 GPIO 输出**没有** PA15.Mode 行
PA15.Signal=GPIO_Output
```

第一版 `configured` 只看 `Mode`，于是 PA15/PB9/PC0 全被当成"没配置"漏掉——
而它们恰恰是 `.ioc` 里**唯一带人起名字**的那几个脚，也是 `pins.h` 里最有用的宏。

**判据**：`configured = bool(mode or signal or label)`；方向从 `Mode` **或**
`Signal`（`GPIO_Output` / `GPXT`）推，推不出才是 `"io"`。
方向冲突检查就靠这一项，推成 `"io"` 只是少一条检查，推错会造假错误。

同一份 `.ioc` 还有两种键：`PC13-ANTI_TAMP`、`PH0 - OSC_IN`（带后缀/空格）。
`parse_pins` 用 `^(P[A-K]\d{1,2})(?![0-9])` 取前面的端口名，取不出来的 `VP_*`
虚拟脚直接跳过——**不猜**。

### 3.11 dataclass 里「派生状态」写成字段 → 会和 `to_dict()` 自相矛盾

**症状**：`cal.accepted` 打印 `False`，但同一个对象 `cal.to_dict()["accepted"]`
是 `True`。两个都"对"，因为它们是两个事实源。

```python
@dataclass
class Calibration:
    accepted: bool = False          # ← 字段：构造时 False，之后没人更新它
    adopted: dict = field(...)

    def to_dict(self):
        return {"accepted": bool(self.adopted)}   # ← 另一个算法
```

**根因**：`accepted` 显然**派生自** `adopted`，但写成了独立字段。
构造之后校验中途 `adopted.update(...)`，字段不跟着变。

**约定**：派生状态一律写成 `@property`，不进 `__init__`：

```python
@property
def accepted(self) -> bool:
    return bool(self.adopted)
```

**同类**：`new_status: str = "unknown"` —— 默认值是**另一个语义**
（"判不出"），而"没评估过"是空。默认值选了 `"unknown"`，
报告里两者长得一模一样。**三态字段的默认值必须是"空"**，不是其中一态。

### 3.12 同一个 CLI 里两种 flag 风格并存 → 参数被静默忽略

本仓库实测：

```
P0–P16 的命令   cmd_generate / cmd_ladder / cmd_sim …   解析 "--key value"
P17–P22 的命令  cmd_emit / cmd_parts_* …                解析 "--key=value"
```

`--max_candidates`（下划线 + 等号）传给 `generate`，它找不到 `--max-candidates`，
于是**用了默认上限 50** —— 传 2 却生成 36 个候选。参数被静默忽略，
而调用方以为生效了。这正是 §3.7 那个形状的又一例。

**约定**（`design/agent_tools.py`）：

* 工具参数用下划线（JSON 习惯），`build_argv` 统一把 `_` 转成 `-`
* `ToolSpec.arg_style` 显式声明 `"space"`（老命令）或 `"eq"`（新命令）
* **不要**"统一成一种"——那会让另一半全部失效，且失效方式是静默的

**判别**：新增工具后，把参数设成一个**可观察的值**（2 个候选、1 个域），
跑一次看产物里那个值是否真变了。

---

### 3.13–3.14 客户端接线类坑（字段没人读 / need 漏表）

> 全文在 [`docs/runbook.md`](docs/runbook.md) 的「§10 客户端接线类坑」。要点：
>
> * 宿主给了 `hint.view` 而客户端**只读 `project`** → 14 处 `viewHint` 全成死代码
> * `fix.need` 是散在十几个文件里的字面量，**漏一个 = 那条 finding 悄悄失去按钮**
>   （`check_p1` 扫源码字面量比对 `nextstep.NEEDS`，把"漏了"变红）

### 3.15 「再写一份允许值清单」= 迟早分叉

P2 的验收里我写了：

```python
profile["physical_source"] in ("datasheet", "measured", "catalog", "estimated", "")
```

而 `model.py` 里真正的事实源是 `PHYSICAL_SOURCES = ("datasheet", "measured",
"estimated", "derived")`。两处不一样：我漏了 `derived`，还多编了个 `catalog`。
于是一个**完全合法**的件被判成"来源非法"。

这和 §1.1（判定只有一份）是同一条纪律，只是落在**常量**上：

* 验收要断言"值合法"，就 `from ... import PHYSICAL_SOURCES` 用它
* 要断言"枚举完整"，就比对 `len()` 与来源，**不要抄字面量**

**判别**：验收侧出现一串看起来像枚举的字符串字面量 → 去源码找有没有同名常量。

**升级版（P46 撞到的）**：把整条**数组/列表**按字面量钉死，比抄枚举更脆。
`check_p4` / `check_p5` 原来写的是

```python
"delivery: ['export', 'firmware'" in store_src     # ← 前缀匹配
```

P46 在 delivery 组里加了 `emit`/`brief`，两条立刻红 —— 而"firmware 属于
delivery 组"这个**意图根本没变**。修法：把数组**解出来**再判成员：

```python
delivery = _delivery_sections(store_src)     # 从 `delivery: [...]` 里切
assert "firmware" in delivery
```

---

### 3.16 「没人读」被报成「不敏感」= 最难发现的假绿

> 全文在 [`docs/runbook.md`](docs/runbook.md) 的「§11 未被消费的字段/参数」。
> 要点：136 个仿真参数里**只有 4 个真被读**，其余 132 个**没有任何代码读过**；
> 报成"低敏感"会让人以为是"评估过不重要"，而真相是"从未接入"。
> 四类措辞必须不同：`wired` / `wired_noop` / `unwired` / `missing`。

### 3.17 往 `COMMANDS` 里加命令前，**先 grep 那个名字**

P4 我要加一个"固件矩阵"命令，按直觉注册成 `"firmware"`。而 **P20 早就有一个
`cmd_firmware`**（固件源码的 6 条文本检查），也注册成 `"firmware"`。

后果不是报错，是：

```python
COMMANDS = {
    "firmware": cmd_firmware,      # P20 的
    ...
    "firmware": cmd_fw_matrix,     # 我加的 —— 字典同键，**后者静默胜出**
}
```

原命令**被顶掉**，`python -m hw_velxio.design_cli firmware` 变成跑矩阵。
不 warning、不 TypeError、测试也未必红（除非恰好有一条断言原命令的行为）。

这和 §3.13（字段没人读）、§3.16（参数没人读）是同一形状的**静默替换**，
只是这次发生在**注册表**上。

**约定**：

* 加命令前 `grep -n '"<名字>"' design_cli.py`，也 `grep -n 'def cmd_'` 看函数名
* 名字重复的验收写一条**正面断言**："老命令还是老行为"（如 `check_p4` 的 6a：
  断言 `firmware` 的 describe 里**没有** `columns`）
* 更一般地：**任何"用同键覆盖"的注册表**（`COMMANDS`、`TOOLS`、`NEEDS`、
  `SECTIONS`）都该有一条"键唯一"的检查

---

### 3.18 照着内部实现猜外部契约 → 字段名全错

P5 初版方案写"需求字段是 `units` / `links`"，那是照 `generate.py` **内部**的
`unit_list` / `candidate.links` 猜的。**真契约是 `Requirement` 的七个字段**：

```python
id / title / roles[] / interfaces[] / constraints{} / numbers{} / notes
```

一条 `sed -n '53,112p' design/generate.py` 就能避免。这和 §3.2
（TS 读产物要先跑一次 CLI 看键名）是同一条纪律，只是方向相反：
**不是"我这边发出去的"，而是"那边要吃进来的"。**

同类错误：`extract_uses` 返回的键是 `files` 我猜成 `scanned`（P4）。

**判别**：任何"我要产出给 X 消费"的代码，先 `import X` 并
`print(list(X.某个dataclass.__dataclass_fields__))`——**别读自己的记忆**。

### 3.19 「先有工程才谈需求」这个顺序，会卡住从零开局（**已补**）

`req-draft` 原本把草稿写进**工程目录**（`<project>/req_draft.json`），而
`snapshot()` 要 `design.json`。于是"从零一句话开局"走不通：还没有工程，
草稿无处可放（`check_p5` 的 `4f` 崩在 `FileNotFoundError`）。

**已补**（P5b）：草稿按"工程在不在"决定落点——

```
工程已存在 → <project>/req_draft.json
工程还没有 → <工作区>/_drafts/<id>.json
```

`apply --confirm` 时工程不存在会**顺手建一个空工程**，再写 `req.json`。

**补的时候撞上第二个坑，值得单独记**：

> **apply 之后必须把草稿搬进工程、删掉工作区那份。**

第一版只写了 `req.json`，工作区草稿留在原地 → 两份副本（工程级 + 工作区级）。
之后改一份，另一份不跟着变，而"改的是哪一份"在报告里分不出来。
这就是**两个事实源**，和 §1.1 同源。验收 `7h` 抓的就是它
（断言 `draft_scope=="project"` 且工作区那份**已删**）。

还有两条容易漏的：建出来的工程要过 `validate()`（否则是空壳），
也要走 `discipline.auto_lock`（否则导出闸门会问「mcu_family 未闭合」，
用户以为自己漏了什么——`cmd_put` 的注释里记着同一个坑）。
验收 `7f` / `7g` 分别守着。

### 3.20 板框 `R` 模式原点是**左上角**、向 −Y 延伸 → 写反了布线会 0 段而**不报错**

**症状**：`pcb_Document.autoRouting()` 返回

```json
{"success": true, "totalNetsCount": 2, "successNetsCount": 0,
 "failedNets": ["GND","MID"], "duration": 815}
```

板子看起来完全合规：焊盘带 net、飞线 3 条、DRC 规则 `JLCPCB Capability(Two
Layers Board)` 已装载、TOP/BOTTOM 都是 `SHOW`、元件明明在画布里。

**真因**（本仓库第 N 次「先怀疑工具」翻车）：`_outline_source` 把板框写成

```python
["R", mm_to_mil(x0), mm_to_mil(y0), mm_to_mil(w), mm_to_mil(h), 0, 0]
```

而 `TPCB_PolygonSourceArray` 的 `R` 是 `R x y width height rot round`，
**`(x, y)` 是左上角，矩形向 −Y 延伸 height**（手册 §19）。于是板框落在
`y ∈ [−h, 0]`，元件在 `y > 0` —— **整块板都在板框外**，布线器无处可布。

**修法**：左上角 y 写 `y0 + h`：

```python
["R", mm_to_mil(x0), mm_to_mil(y0 + h), mm_to_mil(w), mm_to_mil(h), 0, 0]
```

**修后实测**（同一块板、同一个 CLI headless 会话）：`successNetsCount: 2/2`、
盘上 **9 段 45° 单层走线**（GND 5 / MID 4）、0 过孔、DRC `[]`；
关会话重开后元件 3 个、板框 `['R',0,1574.8,...,1574.8]`、走线 9 段全在。

**连带纠正三条错结论**（都曾写进文档）：

| 当时记的 | 真相 |
|---|---|
| 「引擎拒绝执行」 | 引擎一直在跑，是我的板在板框外 |
| 「headless 没有布线的执行环境」 | 与窗口来源（cli/user）、登录态、headless 全无关；`origin=user` 的登录态 GUI 窗口同样 0/2 |
| 「矩形板框不受 Y 轴影响」 | 恰恰是矩形板框的**位置**被写反了 |

**判别**：布线 0 段时，先按顺序查三样，**别先怀疑引擎**——

```javascript
// 1) 板框存不存在、坐标是多少（注意是 Polyline，不是 PrimitiveLine）
const ids = await eda.pcb_PrimitivePolyline.getAllPrimitiveId();
const pls = await eda.pcb_PrimitivePolyline.get(ids);
return pls.map(p => p.getState_Polygon().getSource());
// 2) 元件/焊盘的实际坐标
// 3) 两者是否真的重叠（板框 y 区间要覆盖焊盘 y 区间）
```

**另一条**：`IPCB_AutoRoutingResult.success` 只表示「已启动」，
`successNetsCount` 只描述**这一次调用做了什么**——布完再调一次就是 `0/N`
而走线一段没少。所以 `route` 段的判定必须**量几何**（逐网络数走线），
不能读这个字段（`_run_route` 已改成这样）。

---

### 3.21 `createProject` 在会话刚开时会抛 `username undefined`（竞态，不是权限）

**症状**：发射第一步就 `failed`：

```
TypeError: Cannot read properties of undefined (reading 'username')
```

**根因**：`lceda-pro open` 返回 sessionId 只代表"窗口起来了"，**用户态是异步装载的**。
同一句 `createProject` 在 `open` 之后等 5 秒再调就正常（实测）。

**修法**（`_JS_PROJECT_NEW`）：先轮询 `dmt_Team.getAllTeamsInfo()` 直到非空（最多 6 秒），
再把 `createProject` **包进 try/catch 并重试 24 次**。第一版没包 catch，
于是"偶发的竞态"表现成"立创不让建工程"。

**判别**：单独跑一句 `createProject` 能成功，而整条发射在第一步失败 → 就是它。

---

### 3.22 `openDocument` 返回 true ≠ 文档已激活（多板时会把线布到上一块板）

**症状**：多板发射报「布通 2/3」，缺的那条网络只属于**第一块板**；
`successNetsCount` 却是 2；盘上第二块板的线全在、第一块板一条没有。

**根因**：`getCurrentDocumentInfo()` 要到**几秒后**才变成 `documentType === 3`。
第一版只 `setTimeout(settleMs=900)` 就调 `autoRouting` —— 那次调用跑在了
**上一块板**的文档上。

**修法**（`_DOC_READY_JS`）：轮询 docType 最多 12 秒，再等 400ms。
路由/测量/落笔/铺铜**四个段共用同一段前奏**。

**判别**：单板永远看不出这个坑；**必须用 ≥2 块板**的夹具跑一遍。

---

### 3.23 只铺底层会「没铜」——孤岛被丢弃，要缝合过孔

**症状**：双面铺铜，顶层 `getCopperRegion().pourFills = 7`，
底层 **0**、`rebuildCopperRegion()` 也返回 false。坐标明明是对的。

**根因**：底层那片铜和**任何焊盘都没有电气连接**（SMD 焊盘只在顶层），
立创按 `preserveSilos:false` 把孤岛丢掉。手册 §17.15/§17.16 说的就是这件事。

**修法**：先放**缝合过孔**（GND 网络、20/12 mil），再建铺铜，最后统一 rebuild。
过孔位置要避开别的网络的铜（`router.stitch_sites` 按几何挑，挑不到就返回空并照实说）。

**修后实测**：顶层 7 fills、底层 **1 fill**，两块板都 `hasCopper`。

---

### 3.24 DRC 分类：分组树里的**网络名**会把父节点的类别覆盖掉

**症状**：写了 `_drc_categories()`，结果 4 条 `Connection Error` 全落进 `other`。

**根因**：DRC 的树是 `Connection Error → GND → A1 → {explanation}`。
第一版对**每个**带 `name` 的节点都重算类别，于是走到 `GND` 时把
`connection` 改写成了 `other`（网络名当然不是类别）。

**修法**：只有**认得出来**的类别名才继承（`if guess != "other": kind = guess`）。

**判别**：分类结果 `other` 占大头 = 类别没生效，不是"这些违规不属于任何类别"。

---

### 3.25 分批发射的批大小不是拍脑袋：器件 6、导线 12、摆放 6、走线 14

单次 `invoke` 有 **30 秒硬上限**（`lceda.py` 的 F1）。实测耗时（手册 §13.3 +
本机复测）：放器件 ≈2.5s/个、接线 ≈0.2s/条、`pcb_PrimitiveComponent.modify`
≈0.2~0.5s/个、`pcb_PrimitiveLine.create` ≈0.1s/条。于是：

| 操作 | 批大小 | 常量 |
|---|---|---|
| 装器件 | 6 | `SCH_PART_BATCH` |
| 接线（每根还带一个网络标签）| 12 | `SCH_WIRE_BATCH` |
| PCB 摆放 | 6 | `PCB_MOVE_BATCH` |
| 走线落笔 | 14 | `_emit_plan(batch=14)` |

**装器件与接线必须分成两段**：两者单次耗时差 10 倍，混在一个脚本里
批大小就没法各自最优。分批的代价是每批多一次 `openDocument`（≈1s），
用户允许长时间运行时这笔开销换"大板不会整段失败"是值得的。

**判别**：整板一次装时，元件数一多就报超时；而**超时后 EDA 侧可能已经做了
一半**（手册 §17.7），重跑会看到"有的装了有的没装"。

---

### 3.26 同层多张铺铜会**互相吃光**（2 层板只能地铺铜 + 电源宽走线）

**症状**：先铺整板 GND、再给 3V3 铺一块小矩形 → 3V3 的
`getCopperRegion().pourFills = 0`（铜被地平面的间距规则整片吃掉）；
把顺序反过来 → 地变成 0 fills。**两种顺序各输一次**，说明不是顺序问题。

**真因**：两张不同网络的铺铜在同一层重叠时，后建的那张会被按间距规则
裁掉；面积小的必然全灭。

**正解**（两层板）：

* **GND 铺整板**（双面）+ 缝合过孔（见 §3.23）
* **其它电源网不铺铜**，按 **20 mil 宽走线**（`router.Rules.width_for()` 对
  `GND/VCC/VDD/+3V3/VBUS/…` 自动加宽）
* **≥4 层板**才有意义：`_run_pour` 会把额外电源网铺到**内层**（layer ≥3），
  并且明确记 `power_nets` 与 `copper_layers`

**判别**：`pourFills == 0` 而坐标明明对 → 先看**同层有没有别的铺铜**，
再怀疑坐标（手册 §19.2 的口诀要补这一条）。

---

### 3.27 「铺铜前判连通」= 用错时机，会把能打板的板判成断开

**症状**：12 焊盘的 GND，引擎只布了一部分，布线段判 `split`（4 个孤岛）；
用户看到 `route: not_ready`，以为板子没做出来。

**真相**：真实板上 **GND 靠铺铜连，不靠走线**。铺铜一上，那 4 个孤岛
并成一片 —— 最终 `verify` 判 4/4 全连通。

**约定**：`route` 段只报"布了多少段 / 铺铜前连通几条"，**最终结论由
`verify` 段（铺铜之后）下**。这就是 §0.3 的同一条教训换了个位置：
**用错的时机验，得到的是错的结论**。

---

### 3.28 几何判定对**矩形焊盘**不能用 `min(hw, hh)`

**症状**：判定器说断开，而 EDA 里明明连着。

**根因**：0402 的焊盘是 16×17 mil。拿短边当半径（8 mil），从**长边方向**
贴上来的走线（距离 17 mil）就被判成没接触。手册 §22 第一轮踩的是 POLYGON
取到 `NaN`，这里是"取短边"——同一类错误的另一种形态。

**修法**：`pcb_geo._seg_rect_gap()` 算**线段到矩形**的真实距离（端点在内、
边相交都算 0），焊盘-焊盘用矩形间距，过孔用"矩形距离减半径"。

---

### 3.29 布局检查的单位必须**全程 mil**（我在报告里把 mil 标成 mm）

**症状**：报告写 `(1535.4, 236.2) mm 的封装占位超出板内 40 mil 边距` ——
1535 mm 的板显然荒唐。

**根因**：`_size_mil()` 返回的是 **mil**，而 `Placement.x_mm` 是 **mm**。
第一版把两者混在一个表达式里（`pw / _MM_TO_MIL / 2.0` 得 mm，又拿去和
mil 的 `EDGE_MARGIN_MIL` 比），判据永远成立 → 每个件都"超出边距"。

**约定**：进 `pcb_layout` 的坐标**先统一乘 `_MM_TO_MIL` 换成 mil**，
只在拼接给人看的字符串时换回 mm。验收 `check_p26` 的 4a/4d 守着这条
（正常摆放必须 ok）。

---

### 3.30 原理图**没 save()** 就去导入 PCB → 立创会静默漏器件

**症状**：3 个器件，`sch_SourcePrimitiveComponent.add` 全部 ok、`placed` 报了 3 个、
`sch_Document.save()` 之后也返回 true —— 但 PCB 上只有 **2 个**，
`parity.missing = 2`（缺的是同一个器件的两个引脚）。

**真因**：`pcb_Document.importChanges()` 读的是**已提交的原理图源码**。
分批发射把「装器件」「接线」拆成了两次 `invoke`，两次之间**没有 save**，
于是最后一次改动还没提交；`importChanges` 就按"老版本"导入，少一个器件。

**两处修法（都要）**：

1. 接线之后显式 `sch_Document.save()`，并把返回值当闸门：
   返假 → `pcb` 段 `not_ready`（不许假装导入成功）
2. `save()` 返回 true 之后**再等 1.2 秒**（`settleMs`）—— 实测不等这一下
   仍会偶发少导一个（`save` 是同步返回、提交是异步的）

**判别**：`placed` 数对、`components_on_pcb` 少、`parity.missing` 非空 ——
三者凑齐就是它。**只有 parity 能抓到**（其它三项都"成功"）。

---

### 3.31 图元/标签接口「返回 ok」≠「生效」：写完必须回读

**症状**：`sch_SourcePrimitiveWire.add` 和 `addNetLabel` 全部返回 `ok`，
`wired` 也报了 4 条；但**第一根**线对应的引脚在网络表里仍是空 net
（实测 `R1.1` 的 `A1` 丢了，PCB 上只剩 1 个 A1 焊盘，parity `missing=1`）。

**根因**：`openDocument` 之后**立刻**发第一条 mutation，EDA 会吞掉它。
先后两次 `add` 都"成功"只是因为返回值来自中间层。

**修法（两件都要）**：

1. 接线脚本在 `openDocument` 之后**再等 600ms** 才发第一条
2. 接线完**回读** `sch_Netlist.getNetlist('JLCEDA')`，逐引脚核对；
   不一致的引脚**重试一次**，仍不对就 `not_ready`（不许记 ok）

**判别**：`wired` 数对、`parity.missing` 非空 → 就是它。
**只有回读能抓到**（所有写接口都说 ok）。

---

### 3.32 Bridge 通道的真墙：单次 `/execute` 30 秒（实测能跑/不能跑什么）

官方 `Run API Gateway 1.0.8` 的行为（从扩展源码读到、也在真机验过）：
扫 49620–49629、握手 `service === "easyeda-bridge"`、随后发
`{type:"register", windowId}`、重试 3s→30s **无限次**——和
`easyeda-api` skill 的 `bridge-server.mjs` **协议完全对得上**。

**本机实测（2026-11-22）**：

| 操作 | 走 Bridge | 说明 |
|---|---|---|
| `/health`、`/eda-windows` | ✅ | `edaConnected: true`，1 个窗口 |
| 单次 `invoke`（读工程/读网表） | ✅ | 毫秒级 |
| **整条 8 段发射** | ✅ **一次**（76s） | 窗口空闲时成功，`channel.kind=bridge` |
| 重复发射（窗口里已开着工程） | ❌ | `openProject` 变慢 → 撞 30 秒 → HTTP 500 |
| 叠层/网络类/差分对 | ❌ | 单次就超 30 秒 |

**约定**：Bridge 用来做「**必须在用户窗口里做的短操作**」；整条发射与长任务
用 `--channel=cli`。遇到 HTTP 500 先把 body 读出来（已改）——真因通常在那
（`openProject 连续两次返回 false`、`createProject 一直返回 undefined`…）。

---

### 3.33–3.34 参数形状 / MuJoCo 中文路径

> 全文在 [`docs/runbook.md`](docs/runbook.md) 的「§12 参数形状与 MuJoCo 路径」。要点：
>
> * `REQUIRED_PARAMS` 只给键名不够：`range_deg` 要**一对数**；
>   标量（`180`）既可能 `±180` 也可能 `0..180` → **不猜，跳过该关节并说明**
> * `MjModel.from_xml_path` 在**非 ASCII 路径**上必然失败 → 先 Python 读文件再喂字符串

### 3.35 允许值清单漏一项 → 目录回填的元件过不了 validate（P41 实测）

`parts_catalog.py:650` 一直在写 `physical_source = "catalog"`（JEDEC 尺寸可信），
而 `model.PHYSICAL_SOURCES` 里**没有** `catalog`。于是"目录回填过的元件"
在 `validate()` / `from_dict` 里直接抛 `DesignError`。

这和 §3.15（验收侧抄了一份枚举）是同一个形状，只是这次**生产和校验各抄了一份**。

**修法**：`PHYSICAL_SOURCES` 补 `catalog` 与 `proposed`，并加**唯一的排序表**
`SOURCE_ORDER`（低 → 高）+ `source_at_least()`。判"这个来源够不够可信"时
**只读这张表**，不要写 if/elif。

**判别**：一个库自己写进去的值，读回来校验炸了 → 先查两边的枚举是不是同一份。

---

### 3.36 幂等判据要连"读取"一起验：`from_dict` 会把 `mechanical.joints` 搬走

P41 让 `mechanical.joints` 可以写在机械域里，但**存储只有一处**（`joints_list`），
所以 `from_dict` 会把它**搬过去并从 `mechanical` 里删掉**。

这意味着"读进来再写回去"**不可能与原文件逐字节相同**（`mechanical.joints`
没了）。所以幂等断言必须是 `to_dict → from_dict → to_dict`（写出的形状稳定），
**不是** `from_dict(原文件) == 原文件`。

判据写错会怎样：用一个"应该被规范化"的夹具去断言"往返逐字节一致"，
会得到一条**永远红**的断言，然后有人去把规范化删掉。

---

### 3.37 状态只数"查了几项"，不数"查出几个错" → 缺参数被报成 ok（P42 实测）

**症状**：`hinge` 缺 `range_deg`（P16 根本生成不出关节），
`joints.validate()` 里躺着一行 `severity=error` 的 finding，而
`report["status"]` 是 **`ok`**：

```python
"status": "ok" if checked else "not_ready"     # ← 只看"查了几项"
```

这是 §0.1 的又一个变体：**"有输入"被当成了"没问题"**。下游 `cmd_joints`
用 `status != "ok"` 决定要不要报错，于是这条 error 只出现在 `findings` 里，
总状态一路绿。

**修法**：三档按"有没有 error"定，而不是按"查没查"：

```python
status = ("not_ready" if not checked
          else ("failed" if any(f["severity"] == "error" for f in findings) else "ok"))
```

**判别**：`findings` 里有 `severity=error`，而 `status` 是 `ok` → 就是它。
写这类断言时**两个都要断**（既有 error、又 status 不 ok），只断 status 会漏。

---

### 3.38 新加一个 `need` 就有一条 finding 失去按钮（`check_p1` 扫源码抓）

P42 的提案 `unresolved` 里写了四个 `need`：`hinge_deg` / `hinge_partner` /
`gear_ratio` / `geometry`。`nextstep.NEEDS` 里没有它们 →
`check_p1` 的 1a 立刻红（"源码 31 个，表 31 条；缺：..."）。

这就是 §3.14 说的形状：**漏一个 = 那条 finding 悄悄变回纯文字**，不报错。
好在 1a 是扫**源码字面量**（不是手写表），所以新文件一写就被抓。

**约定**：任何地方写 `"need": "xxx"`，同一次提交里必须进 `nextstep.NEEDS`，
并给出四类之一（`edit_field` / `move_part` / `run_command` / `external`）。

---

### 3.39 「编排器」会开第二个写入口 —— 别把它放进 agent 白名单（P43 实测）

`check_p22` 的不变式是：**agent 循环里写 `design.json` 的入口只有一个
`hw_put`**（另有 `hw_emit` 产出工程但不改 design.json）。

P43 的 `scaffold` 会写 design.json（建工程 + 写 pinmap + 写关节），
我第一版把它按 `tier="write"` 加进白名单，`check_p22` 的 1b 立刻红：

```
write 档=['hw_emit', 'hw_put', 'hw_scaffold']
```

**修法**：`scaffold` **只做 CLI + DSH 工具**，不进 `agent_tools.TOOLS`。
理由是那条不变式的目的：模型在受限循环里只能通过**一个受校验的写入口**
改设计。多开一条就绕过了审批与 `validate()` 的一致性。

**顺带一条**：这类"某档只能有 N 个工具"的断言，比"某档必须有某工具"更容易
被后来的功能撞坏；新增工具时先看它属于哪一档。

---

### 3.40 「缺件不中止」要写成**可验证的开关**，否则那条断言是恒真的

用户的要求是"所有项目都是半成品，缺 `.ioc`/缺关节不该让产出归零"。
于是 `scaffold` 缺引脚表时**继续跑后面几段**，缺的东西进 `open_items.md`。

这类"继续跑"的行为最容易写成**恒真断言**：不管实现怎样，脚本都能跑完、
都能返回六段。所以 `scaffold` 里加了一个**唯一开关**：

```python
CONTINUE_ON_GAP = True   # check_p43 会把它关掉
```

`check_p43` 的退化用例关掉它，断言"firmware 段消失"——证明
"后面的段照样跑"真的挂在这个开关上，而不是碰巧。

**约定**：任何"降级/继续跑"的分支，都要有一个能被测试翻过来的开关；
没有开关的降级路径**无法退化验证**，那条断言就永远绿。

---

### 3.41 机械判据的"缺数据"必须单独一档，不能并入通过（P44）

四条机械判据（干涉/重心/扭矩/行程）里，**三条在缺数据时算不出来**：

* 重心：零件没有 `mass_g`、尺寸又是 0 → 质量算不出
* 扭矩：舵机没写 `electrical.stall_torque_kgcm` → 没有标称值可比
* 行程：`mechanical.limits.required_deg` 没给 → 不知道要转多大

这时**唯一正确的输出是 `unknown`**，并且要带上"已经算出来的那部分"
（比如"你要的是 0.05 kg·cm，缺的是舵机标称值"）。第一版差点写成
"没有扭矩数据就跳过这一条" —— 那等于说"这条没问题"，正是 §0.1。

三档在交接单上的去向也不同：

| 结论 | handoff 类 | 给人的话 |
|---|---|---|
| `fail` | `blocking` | 打板必炸，修设计 |
| `unknown` | `missing_data` | 补数据，**不能当成通过** |
| `pass` | 不报 | — |

**判别**：一条判据"没数据"时返回了什么？返回 `pass` 或干脆不返回 → 就是它。

---

### 3.42 DFM 是**抛错**，不是"返回一个失败列表"（P45 实测 + API 原文）

`pcb_Dfm.checkDfm` 的元数据写着：

> 仅私有化构建可用
> 结果可能不完整：未配置检查规则或存在检查失败项时，将以 `INCOMPLETE_RESULT`
> 错误抛出，**不得视为检查通过**
> ADD since EDA v4.1.93

所以把它当"返回 `issues` 列表"的接口来接，会得到一个**空列表 + 异常**，
然后很容易写成"没有 issues = 通过"。正确的三态：

| 输入 | 结论 |
|---|---|
| `getDfmAvailability()` 说不可用 | `not_ready`（这里没这能力）|
| 抛 `INCOMPLETE_RESULT` | **`unknown`**（跑了，结果不完整）|
| 返回但带 `DFM_NOT_CONFIGURED` | `not_ready`（没配置 = 没查）|
| `failedItems > 0`（防御分支：万一某版本改回返回）| `unknown` |
| 正常返回 | `ok` |

**本机实测（4.1.97，非私有化构建）**：`checkDfm` 抛的是
`"DFM 仅在私有化构建可用"`，**不是** `INCOMPLETE_RESULT`。所以错误文本也要分档：

| 错误文本 | 结论 |
|---|---|
| 含「私有化 / not available / 不可用」 | `not_ready`（这里没这能力）|
| 含 `INCOMPLETE_RESULT` | `unknown`（有能力，结果不完整）|
| 其它 | `unknown`（保守）|

**判别**：一个接口"报错"时，你的代码把它当"没问题"了吗？

---

### 3.43 写新模块前先 `ls` / `git status` —— 我覆盖了一个同名旧文件

P45 我要新建"制造可行性"模块，想当然地写了 `design/fabrication.py`。
**那个文件早就存在**（制造包导出：BOM/坐标/DXF/README），被整份覆盖，
`git status` 立刻显示 `M design/fabrication.py`（-194 行）。

**恢复**：`git show HEAD:design/fabrication.py` 拿出来写回，新逻辑改放
`design/dfm.py`。损失为零，但纯属运气——如果它当时是**未提交的新文件**，
`git checkout` 就救不回来了（§8.3b 记过同一类事）。

**约定**（三条，按顺序）：

1. 新建模块前 `ls design/` 或 `git status --short` 看目标名在不在
2. 用 `write` 覆盖任何已存在文件前，先 `read` 它（工具的提示里也这么说）
3. 长文档/新模块先 `git add`，出事了才有得恢复

**判别**：`write` 返回"Updated file"而不是"Created file" → 你刚覆盖了东西。

---

### 3.44 「必需项」写成一串行上的 `required=True/False` → 改表不生效（P47 实测）

`doctor` 的 `REQUIRED = ("workspace", "lceda")` 是**表**，但第一版在
`_check_bin(name)` 里写死 `required=False`、在 workspace/lceda 里写死 `True`。
于是验收里把 `ffmpeg` 临时提为必需时**整体照样 ok** —— 表改了，行还挂旧值。

又是 §1.1/§3.15 那个形状，只是这次落在**布尔标记**上：

**约定**：`required` 只由那张表推导（`name in REQUIRED`），行里不写死；
判"这项是不是必需"的地方**只读表**。

**判别**：改常量表后，依赖它的结论没变 → 说明有人在别处抄了一份。

---

### 3.45 一个字符串前缀，让**所有生成的设计 0 网表**（P48 实测）

`generate.to_design()` 里 `ref_of` 的键是**角色名**（`mcu`），
而链路对象里 `unit` 字段带前缀（`unit:mcu`，取自 `unit.id`）：

```python
ref_a = ref_of.get(str(link["a"]["unit"]))   # "unit:mcu" → None → continue
```

于是**每条链路都被跳过**，`design.nets` 恒为 `[]`。后果链：

```
0 网表 → V0 无对象可查 → pass
       → check 9 项全过 → ok
       → handoff blocking=0 → **deliverable=True（可打板）**
       → 而同一份设计 emit 说「没有可布的对象」
```

**实测**：`materialize(cand)` → `nets=0`；把 link 的 `unit:` 前缀去掉 → `nets=2`。

**修法两条**（缺一不可）：

1. `ref_of` 同时以 `role` 与 `unit:<role>` 为键（两种写法都认）
2. 网表引脚名**必须真实存在**：按协议族去能力表的外设组里挑
   （`spi`→`SPI1`/`SPI2`…），挑不到就记 `unresolved_links` 而不是写 `""`

**判别**：生成器的输出"看起来没问题"时，先数 `len(design.nets)`。

---

### 3.46 空输入要在**最便宜的一层**就判死（P48）

V0（符号层）原来只查"引用是否成立"——0 网表时**没有任何引用可查**，
于是 pass。现在 V0 先查两件事：

| 判据 | 级别 | 理由 |
|---|---|---|
| `parts ≥2 且 nets == 0` | **error** | 不是"没有违规"，是"没有东西可判" |
| 某条网引脚 < 2 | **warning** | 部分模型里常见（对面在别的板/未建模的连接器）；判 fail 会把 V1 截断（实测把 `check_ladder` 三条断言打红）|

**同时**：`handoff.deliverable` 加第二个条件 —— **必须有最终连通性证据**
（`emit.json` 的 `verify == ok`）。老规矩"无阻断即可打板"会把
"没查到问题"当成"能打板"。

**判别**：任何 `ok` 都问一句"这条结论依赖的对象存在吗？"

---

### 3.47 判定类命令的顶层 `status` 不能写死（P48 真机实测）

```python
return {"status": "ok", **result}   # ← result 里没有 status，写死的活了
```

于是 `check` 报 `status=ok` 而同一份 payload 里 `ok=false, failed=2`。
人读顶层、工具读顶层，全都以为过了。这是 §1.1 在**命令包装层**的复刻。

**修法**：`_judge_status(payload)` 从 payload 自己的结论推，并且**阶梯要按层看**
（全是 `unknown` → `not_ready`，不是 `failed`）。

**顺带一个坑**：`handoff` 的 `summary` 是**一句话（str）**，
`summary.get()` 直接抛 `AttributeError: 'str' object has no attribute 'get'` ——
所以取 `summary` 前必须 `isinstance(summary, dict)`。

---

### 3.48 「计划」不是又一个判定器：它只做三件事（P49）

`plan` 把需求翻成**能力（判据先行）→ 模块（真实坐标）→ 3D 图 → 任务树**。
纪律三条，都是踩过的：

1. **判据先写**：能力模板里带 `metric/op/value/unit`；写不出量化判据的能力
   **不进表**（进 `missing_need`），否则"能飞"永远没法验收。
2. **只有需求给了数值才判 pass/fail**；否则 `unknown` 并写"缺哪个数"。
   第一版想按"设计里有没有这个件"判 pass —— 那等于把"买了件"当成"能飞"。
3. **任务 id 必须带动作后缀**（`T-H1-P/M/D/V`）。实测：同一能力同时缺数据
   又数值不达标时，`procure` 和 `design` 都叫 `T-H1-1` → 依赖与状态继承
   指到两条里随机一条（§3.17 的同键覆盖，换到任务表上）。

**版本规则**：同需求（`requirement_hash`）不新建；新需求 → `v(N+1)`；
任务**状态**按 id 继承，**判据按当前需求重算**（否则"以前过了"会盖住"现在不成立"）。

**`snapshot` 只 bootstrap 不建版**：没有版本时建 v1，其余只报最新；
建版本是"加需求"这个动作（`plan --req=`）。否则前端轮询会刷出 v3/v4/v5。

---

### 3.49 多板的局部坐标不能直接两两比（P49 实测）

3 板工程第一次跑 `mech`：**7 条 `fail`**。逐条看，**5 条是"
遥控器板的 U1 与 GNSS 板的 U2 实体重叠"** —— 而这两件根本不在同一块板上。
多板的 `Placement.x_mm/y_mm` 是**各自的板内局部坐标**，直接比较 = 拿两个
不同原点系里的数相减。

**修法**：按 `placement.board_id` 分组，只在组内两两比。
修后同一工程 **fail 7 → 0**（剩下 2 条是同板真重叠，改了布局）。

**判别**：多板工程里出现"跨板元件重叠"这类结论，先看判据有没有按板分组。
`check_p44` 的 1d/1e 守着这条（1d 跨板 pass；1e 同板同坐标必须 fail，
证明分组不是恒真判断）。

**同类风险**：`space.*`（physical）已经按板算了；但**任何新写的几何判据**
都要问一句"坐标系是哪块板的"。

---

### 3.50 「软件」不能只有判据，必须有能跑的东西（P50）

用户问"你做任务时考虑到软件了吗"。诚实盘点：`plan` 里 6 条软件能力（S1–S6）
只是**判据 + 任务**，我造的项目里 `.c/.h` 文件数 = **0**，`fw-matrix` 报
"没有扫到任何源文件"。也就是说：**硬件能做出来、软件一行没有**。

补两块，都能在本机**验证**：

| 命令 | 产出 | 为什么能验 |
|---|---|---|
| `interface` | `interface.json`：引脚↔网络契约 + 外设分组 | 逐引脚一致（唯一硬判据）|
| `fw-algo` | 纯 C 算法 + 单元测试 | **本机 gcc 真编译真跑** |

**为什么只做"纯 C"**：目标板 STM32 无板可跑，Renode 在本机被沙箱拦
（§3.4）。把不依赖硬件的部分（状态机/滤波/限幅）抽出来，用 gcc 验**逻辑**，
不假装验"飞得稳"。

**两条纪律**：

1. 每个算法**必须带一条"该触发的失败路径"用例**（角度超限→进 FAILSAFE）。
   只测正常路径 = 没测。`fw_algo` 的 `drop=` 参数就是给退化验证用的：
   删掉角度判断 → 5 条用例红（实测）。
2. 测试**期望写错**比实现写错更常见。实测：`lpf` 的 `alpha0_hold` 用例
   初始化成 alpha=0.1 却期望 alpha=0 的结果 → 报 `got 90 want 100`。
   **先怀疑用例，再怀疑实现**——但要能分清（这里实现是对的）。

**`interface` 的边界**：只剩**时序** `unknown`。

---

### 3.51–3.53 软件三连：三层分解 / 真编译 / 参数映射

> 全文在 [`docs/runbook.md`](docs/runbook.md)「§14 三层分解」「§15 真编译」「§16 参数映射」。
>
> * **§14**：软件任务必须「写→测→验」三层（28→50，软件 12→34）；`artifact`
>   只报**存在性**，不判 done。踩过 `or True` 恒真子句（断言永远绿）。
> * **§15**：`fw-build` 真编出 `.elf`(9548 B)+`.bin`(172 B)，Entry 0x8000045；
>   表外 MCU → `not_ready`。**main.c 是骨架，不证明功能对**。
> * **§16**：`.param` 是 `name=value`（不是 ArduPilot 的 `NAME,VALUE`）；
>   `MF_PARAM` 有**枚举型**和 `#define` 两类别混进来；**参数只进 evidence，
>   不改能力状态**（S2 仍 `unknown`）；缺必需参数 = `partial`，不是通过。
> * **§17**：引脚契约进固件：三条负例必须**编不过**。
> * **§18**：`.ioc` → 配置（时钟/中断/AF/参数）；给错 = 硬冲突。
> * **§19**：引脚表（LCSC 符号）→ `PA9↔30`；符号不完整就拒绝。
> * **§20**：AF 表（ST 官方）：F4/H7 有 AF0–15，F1 只有重映射。
> * **§21–§24**：立创扩产；spec/总线；块库；装配/约束；长程见 `docs/roadmap.md`。

---

## 4. 测试与验收的约定

### 4.1 每条硬判据都要有退化验证

写完断言后，**故意把实现改回错的**，确认断言真的红。

实测例子：把 `_attribution_map` 改成恒返回 `{}`，测试报
`D1 被判为 signal_diode，但 derive() 只给了它 connected`。
没做这一步的断言，很可能永远为真。

### 4.2 验收脚本要能一条命令跑

```
python -m hw_velxio.scripts.check_m1            # M1 硬验收
python -m hw_velxio.scripts.check_units_links   # P5
python -m hw_velxio.scripts.check_generate      # P6
python -m hw_velxio.scripts.check_m3            # M3
npm test                       # 756 项单测（test_design + test_phases）
```

或 `npm run verify` / `verify:m2` / `verify:m3`。

### 4.3 不要虚增数量

P8 第一版把 I2C1 的 SCL 和 SDA 各记一条"链路"，
`F401-Bottom↔F401-Front` 之间出现 4 条，物理上是**一条 I2C 总线**。
27 条虚增，合并后 13 条真实接口。

同理 `generate` 的候选要去重（按零件清单+链路签名），不能靠改名字凑数。

### 4.4 验收用的夹具不要留在用户工作区

实测教训：在 `C:\Users\<user>\Desktop\示例工作区` 下建过
`m1_p2_accept`、`p1_accept`，清理时要确认它确实是夹具（
`[p.ref for p in d.parts]==['U1','R1','D1']`）再删。

更严重的一次：验收时把 13 条 inferred 链路 `promote` 进了
`示例-多板` 的 `links_list`，**之后必须清空恢复**——
是否提升该由用户决定。

---

## 5. 代码风格与文件约定

### 5.1 文档字符串写"为什么"，不写"是什么"

```python
"""单元层 —— 把一份设计切成「功能单元」，并把每个单元的角色说出来。

【这一层解决什么问题】
多板工程的 design.json 是一张平铺的元件表（316 个元件）。人能看出
"这块是主控板"，但机器看不出来。
"""
```

注释里要写**实测数字**，不写形容词。"覆盖率低"没用，
"8 块板里只有 1 块有具名网络（64 条），其余 7 块 0 条"有用。

### 5.2 修改一个模块时，同步它的 `describe()`

`describe()` 是 CLI / 工具的自省入口。改了行为不改 `describe()`，
模型会按过期描述调用。

### 5.3 `design/store.py` 的 `ARTIFACTS` 要登记新产物

新增产物文件时加进去，否则 `list_projects` 的 `has_*` 判断会漏。

### 5.4 前端颜色跟随系统，紫色是唯一强调色

见 `DESIGN.md`。两层 CSS 变量：`--hwv-m-*`（材质，一个值一个 hex）
+ `--hwv-p-*`（引用，全是 `var()`）。深色块**只覆盖材质层**。

---

## 6. 当前状态的诚实清单

不要以为"全部完成"就没问题。以下是**仍存在的缺口**：

| 缺口 | 现状 | 谁能解 |
|---|---|---|
| 7 块板没网表 | 175 个元件判不出角色 | 补网表，或 `.ioc` + 手工确认 |
| MCU 映射 | 板名 vs `.ioc` 无法自动对应 | 用户给 `--board-mcu` |
| Renode 不能在本机跑 | 沙箱拦截锁文件 | 需 DSH 侧放宽，或在外部执行 `.resc` |
| ARM 工具链（仓库内；解压的 exe 带 **Low IL**）| 能编 M4/M7、链接 ELF；**装法/修法见 runbook §13** | 删目录即卸载 |
| 能力表只有 14 个型号 | 表外型号 `explain_miss()` 会解释 | 用户扩表（我不替他猜 datasheet 数值） |
| 哈希 `kind` 不为生成器补映射 | 生成器只对新工程有效 | **用户已决定如此，不要改** |
| 立创自动布线**已通**（曾记错两次） | `route=native` → `2/2` 网布通、9 段 45° 单层、0 过孔、DRC 零违规。真因见 §3.20 | 已修 |
| 立创产物落盘**已解决** | `saveFileToFileSystem` 被拦时改读 `File.arrayBuffer()` → base64 → Python 写盘；实测 7 个产物全落盘 | 已修 |
| PCB 的 Y 轴（**已对照**） | 板框 `R` 模式原点是**左上角**、向 −Y 延伸；写错不报错、只让布线 0 段（见 §3.20） | 已修 |
| 立创只认 lcsc/mpn | `r3demo` 4 个元件全 `no_symbol`（EasyEDA 哈希 kind） | **P19 已给数据源**，但回填要人指定 lcsc（导入器不补） |
| 工程只存在立创库里 | `getProjectsPaths` 被外部权限拦着 → 磁盘上没有 `.eprj3` 文件夹 | 已补 `.epro2` 备份（合法 zip） |
| 电源网不铺铜（**物理约束**） | 2 层板同层多张铺铜互相吃光（§3.26）→ 地铺整板、电源按 20 mil 宽走线；≥4 层板自动铺内层 | 若真要电源平面：改 4 层板 |
| 能力表 `fills`/`provides` 只能人给 | 目录里有尺寸/封装，没有「它是什么」「它能干什么」 | `parts-promote --role --needs`（猜语义 = 假通过） |
| BGA 等特殊封装没尺寸 | `package_sizes` 查不到 → `unknown` 且尺寸全 0 | 需要时人工录一行 |
| **AF 表只有 4 个型号**（P57） | 表外型号的功能一律 `unknown`；F1 没有 AF 编号 | `afmap build --part=` 扩表 |
| **拼板不可调**（P59） | `PNL_*` 的 doc api 返回 null；只建容器 | 面板里手工拼 |
| 固件抽取只认 STM32 HAL | Arduino/AVR/ESP-IDF 报 `not_ready` | 加一族就加一张 `_API_KINDS` 表 |
| 固件抽取是单行匹配 | 跨行写法抽不到，进 `unpaired` | 需要 AST 级分析（目前不做，不猜） |
| 布局自动重排只保证不短路 | 不做电气分区/散热/装配顺序（那是人做的事） | 手工微调 |
| 机械模型**已暴露**（P40） | `mjcf` 命令：构建 + **MuJoCo 真加载**；`--build` 落 model.xml。没装 MuJoCo 时 `not_ready` | 已修 |
| 机械关节**已有提案层**（P42） | `joints-propose`：几何 stack/axle/slide 算得出来，hinge 行程取 `mechanical.limits.hinge_deg`；齿轮一律 unresolved。`joints` 本身仍拒绝编参数 | 已修 |
| 关节 `status` **曾把 error 报成 ok**（P42 修） | 见 §3.37 | 已修 |
| 四域 spec **已落地**（P41） | `power_nets`/`firmware`/`mechanical` 可写；`proposed` 是最低信任层；`lcsc`+`jlc_class` 进模型 | 已修 |
| 电源网**仍会猜**（未声明时） | `power_nets` 没写就按网名正则，报告里标 `source: inferred` | 写进 design.json 即精确 |
| 编排器**已落地**（P43） | `scaffold` 一条命令：需求 → design + pinmap + joints 提案 + fw 骨架 + MJCF + `open_items.md`；缺件不中止 | 已修 |
| `scaffold` **不进 agent 白名单** | 它会写 design.json，而 agent 的写入口只有一个 `hw_put`（见 §3.39） | 有意为之 |
| MCU 表只有 7 个型号 | 开发板（arduino-nano 等）不是裸 MCU → `pinmap` 会 `not_ready` 并列出这 7 个 | 给 `.ioc` 或 `--mcu` |
| 机械判据**已落地**（P44） | `mech`：干涉/重心/扭矩/行程；fail→blocking、unknown→missing_data。**质量默认用体积×密度估**（`basis` 会写明 estimated）| 填 `mass_g` 即精确 |
| 机械判据不做运动包络 | 只算**静态**包围盒；摆臂扫过的空间没算（那要轨迹仿真 V7）| 需要时跑 `ladder --with-sim` |
| DFM **已接入**（P45） | `emit` 的 pcb 段同一次调用里跑 DRC + DFM；`dfm` 命令可对着 `emit.json` 重判。DFM 不可用/不完整 → `not_ready`/`unknown`，**从不等于 ok** | 已修 |
| DFM 在本机**大概率不可用** | API 注明"仅私有化构建可用"；`getDfmAvailability()` 说了才跑 | 私有化构建 |
| Bridge 通道已验（**限短操作**） | 单次 invoke ✅；整条 8 段发射成功过一次（76s）；重复发射/叠层段撞 30s 上限 → 用 `--channel=cli` | 长任务走 cli |
| **前端/交互类小缺口**（结论页只给层结论、`external` 只能定位、面板看不到草稿、草稿只能整体替换、画像没接 `nextstep`、点板没有板级聚合、徽章只算阻断+失败层）| 都是"能看见但点不进去"，不是假绿；细节与优先级见 runbook | 按需 |
| **未接线的分析**（132 个仿真参数没被读、`Requirement.numbers` 无消费方、敏感度只 ×0.5/×2）| **这是结论不是 bug**：接哪个由人定 | 人定优先级 |
| 前端**已能看到发射与半成品**（P46） | 新增 `emit`（8 段状态 + 产物 + verify 计数 + 制造结论）与 `brief`（段状态 + `open_items`）两个分区；`emitRun`/`emitProgress` 两个 RPC | 已修 |
| `emitRun` 是**同步长任务** | 一次 RPC 跑完 8 段（超时给 15 分钟）；**没有中间进度流**，`emitProgress` 只报"上一次结果" | 要真进度得把 emit 改成 job 模式 |
| 产物目录只能**复制路径** | 不替人打开资源管理器（那是宿主动作） | 需要时加宿主 `openPath` |
| **六段 `audit` 已落地**（P47） | `audit`：需求→单元→选料→原理图→PCB→固件→机械→交接；缺产物 = `not_ready`，每段带 `checked`/`basis`；写 `audit.json`/`audit.md` | 已修 |
| **`doctor` 已落地**（P47） | 8 项探测（workspace/lceda/bridge/lcsc/mujoco/arduino-cli/gcc/ffmpeg）；只探测不安装不联网；必需项与可选项分开 | 已修 |
| **`examples` 已落地**（P47） | 三个可抄的半成品：blink / button / gimbal；`copy` **不覆盖**已有工程 | 已修 |
| **`fixloop` 是"有界"的**（P47） | 默认 3 轮、连续 2 轮无改善即停；**不改设计**（`stop=needs_editor` 只出计划）| 要真改走 agent 循环 |
| `audit` 里 `release` 段**现场聚合 handoff** | 每次跑都重算交接单（几秒）；不读旧 `handoff.json` | 需要时改读缓存 |
| **生成器网表已修**（P48） | `ref_of` 认 `role` 与 `unit:<role>` 两种键；链路按协议族挑真实引脚名；挑不到记 `unresolved_links` | 已修 |
| **空网表已判死**（P48） | V0 `symbol.empty_netlist`（error）；`deliverable` 还要求 `emit.verify == ok` | 已修 |
| 生成器只做**外设级示意连接** | 每条链路 1 个引脚（真实 SPI 要 4 根）；网表标 `level=peripheral` | 人补真实连接 |
| 布线器布不通的网**是真的不通** | 21 件板实测：引擎 `failedNets=['VBUS','USB_DP','USB_DM']`，`verify` 独立判几何也确认不通 | 布线靠引擎；不通就报 |
| **项目计划图已落地**（P49） | `plan/vN.json`：14 条能力（判据先行）+ 模块（真实坐标）+ 四层 3D 图 + 任务树；`snapshot` 自动 bootstrap v1 | 已修 |
| 能力模板只覆盖**飞行器类**（H1–H8/S1–S6） | 换领域（机器人/仪器）要新加模板；模板是数据不是代码 | 按需加模板 |
| `plan` 的判据不自己测 | 它只认需求里给的 `numbers`；实测值要靠人/仪器填 | 人填 `--req` 的 numbers |
| **多板干涉已修**（P49） | `mech` 按 `board_id` 分组比；跨板不再误报（实测 fail 7 → 0）| 已修 |
| **多板 `parity` 回读失败** | 3 板工程 `checked=0`（没拿到立创 PCB 网表）；单板 21 件是 62/62 | 多板要逐板取网表并合并 |
| **跨板网必须放连接器** | 3 板工程的 `3V3` 在板间断开（17/18）——没有板间连接器，物理上就不该连通 | 设计里加连接器；`plan` 模板缺这条任务 |
| **接口契约已落地**（P50） | `interface.json`：48 脚 / 19 已连 / 2 外设组；逐引脚一致是唯一硬判据 | 已修 |
| **纯 C 算法可验**（P50） | `fw-algo`：`failsafe` 9/9、`lpf` 9/9，gcc 真编译真跑；删掉角度判断 → 5 条用例红 | 已修 |
| 只有 2 个算法模板 | `failsafe` / `lpf`；PID / 限幅 / EKF 还没写 | 按需加模板 |
| 算法验的是**逻辑**不是**飞行** | 纯 C 在 PC 上跑，不进目标板、不进闭环 | 要真验上台架/真机 |
| **固件骨架可真编译**（P52） | `fw-build` → `.elf`(9548 B)+`.bin`(172 B)；Machine=ARM、Entry 0x8000045、容量装得下；表外 MCU → not_ready。**main.c 是骨架，不证明功能对** | 功能靠 fw-algo 用例 + 台架 |

`示例-多板` 当前 `handoff` 结论：**不可打板**，
12 阻断 / 6 缺数据 / 7 默认值 / 2 不会验 / 175 未接线。

（阻断数 2→12 是 P1 的结果：一条 finding 一条待办，**更准不是变多**。）

---

## 7. 什么时候该说「做不到」

本项目的立场：**说清差在哪，比假装做到了更有用**。

如果某个判据依赖环境能力而当前环境不具备（如 §3.4 的 Renode），
正确做法是：

1. 如实返回 `not_ready` 并写明**具体哪一步被拒**（附错误行）
2. 把能做到的那部分做成**可外部执行的产物**
3. 在验收脚本里明确写出"这一条没做到，原因是什么"

反面做法：把 `not_run` 记成 `pass`，或写一个永远为真的断言来"通过"验收。
这个仓库里每一个 `unknown` / `not_ready` / `not_verified` 都是**有意为之**，
不是没写完。

---

## 8. 操作手册

> 原 8.1–8.8 全文在 [`docs/runbook.md`](docs/runbook.md)（一字未改）——
> 它曾把本文件顶到 67 KB、**尾部被静默截断**而 agent 以为读全了。
> 跑验收/清理/排查环境去那里查；0–7 章是"为什么"。
