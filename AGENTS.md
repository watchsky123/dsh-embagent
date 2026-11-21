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

写中文注释/文案时，`"通过"` 这类中文引号如果进了字符串字面量，
会提前结束字符串：

```python
risk="不是"通过"，是"没算""     # SyntaxError
```

实测多次触发。**约定**：文档字符串、注释随便用中文引号；
但**字符串值里**改用「」或去掉引号。

### 3.4 Windows 文件锁：Renode 在 DSH 沙箱内跑不起来

Renode 在 `TranslationCPU.Init()` 时对 `%APPDATA%\renode\cache_lock`
做独占打开，DSH 作业对象下被拒：

```
System.UnauthorizedAccessException: Access to the path '...\cache_lock' is denied.
```

**关键对照**：不带 CPU 的 `mach create "x"` 能成功，
一加 `LoadPlatformDescription` 就失败。

试过并失败的旁路（不要重复）：
`--config` 指向别处、重定向 `APPDATA`/`TEMP`、`cmd` 包装、
Windows 计划任务、预建目录、预建非空锁文件。

ACL 确认当前用户有完全控制权、无 Deny，Python 子进程
`CreateFileW(share=0)` 独占打开也成功 → 是 DSH 对 Renode 进程的特定拦截，
**不是文件权限问题**。

**现状**：`renode.report()` 如实报 `runnable_here: False`，
产物（`.resc` + `run.cmd`）交人在 DSH 外执行。
`run.cmd` 里预建 `%APPDATA%\renode`——Renode **只会写、不会建**该目录。

### 3.5 Velxio 后端存活要靠 Windows 计划任务

DSH 的 Job Object 有 `KILL_ON_JOB_CLOSE`，bash 调用结束时整棵进程树被回收。

试过并失败的方案：`Popen`、`CREATE_BREAKAWAY_FROM_JOB`、`DETACHED_PROCESS`、
WMI `Win32_Process.Create`。

**可用方案**：`schtasks`（`hw_velxio/serve.py` 已封装）。
三个必须注意的点：
* 不要加 `/RL HIGHEST`（Access denied）
* 包装 `.cmd` 必须写成**二进制 CRLF**，否则 `\r\r\n` 会破坏 `--port 3080`
* 直接 exec uvicorn，不要用 `start`

### 3.6 后端导入慢 77 秒的根因是模块级网络调用

`compile.py:35` 在模块级构造 `ArduinoCLIService()`，触发
`_ensure_board_urls()` + `_ensure_core_installed()`，三个不可达的
board-manager URL 各耗一次超时。

实测：清除后 `core list` 从 76.0s → 0.14s。

修法：venv 的 `sitecustomize.py`（`hw_velxio/patch.py` 是唯一生成处）。
digest **是运行时算的**（`sha256(PATCH_BODY)[:16]`，见 `_expected_digest()`），
不是写死的常量——改了 `PATCH_BODY` 就会自动重算，`status` 会报不匹配。
**必须在模块 exec 之前打补丁**——第一版在 exec 之后替换
`module.arduino_cli`，构造已经发生了，无效。

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

### 3.13 宿主给了字段、客户端不读 → 那 14 处调用全是死代码

`service.ts` 早就在 hint 里放了 `view`（`hintState.view`），
但客户端 `poll()` **只读 `project`**：

```ts
if (hint.project !== undefined && hint.project !== '') { … }   // view 从来没被读
```

后果不是报错，是**功能静默缺失**：`tools.ts` 里 14 处
`viewHint(project, 'checks')` 全成了装饰，用户跑完 `hw_ladder`
还得自己点页签去找结论。

**判别**：一个字段在**发送侧**有赋值、在**接收侧**没有读取，就是这种形状。
grep 一次即可——`grep -rn "hint\.view" src/`，只有发送侧命中就是死的。

**修法**：读它，并且**只认已知值**（未知字符串忽略而不是猜）：

```ts
if (typeof view === 'string' && isSectionId(view)) { … }
```

切分区必须同时切分组，否则会出现"选中了不在当前分组里的分区"这种错位状态。

**同类风险**：任何"宿主塞进 payload、客户端按需取"的契约字段
（`presentationMeta`、`phase`、`verdict`）都可能是这个形状。
加字段时**同时写一条断言它被读到**——`check_p0` 的 6b/6c/6d 就是干这个的
（读 TS 源码断言"已 import 并渲染"，因为 tsc 抓不到"只定义不接线"）。

### 3.14 `fix.need` 是一串散在十几个文件里的字面量 → 漏一个就静默失去按钮

`physical.py` / `links.py` / `coverify.py` 各自写 `"need": "xxx"`，
由 `nextstep.NEEDS` 翻成可点的动作。**新加一个 need 而忘了进表**的后果是：

    那条 finding 没有按钮 —— 不报错、不失败、只是没了

和 §1.2 的闸门是同一个形状。所以 `check_p1` 的 1a **扫源码**收集所有
`"need": "..."` 再比对表，把"漏了"变成红的。加 need 时必须同时加表项。

三条同时记住：

* **降级不是填空，是换一句话**。命令模板缺 `{lcsc}` 时不要把占位符填空
  然后显示「查  的尺寸」——读起来像 bug，人会怀疑工具而不是怀疑参数。
* **没有靶子就不给按钮**。"整个设计一个申报都没有"这类，挑第一个元件当靶子
  是猜；点"去改"跳到一个随机件，比没按钮更糟。
* **refs 可能在 finding 顶层**（`physical` 的惯用法），不在 `fix` 里。
  两者都要接受，否则下一步会显示「把 ? 分开」。

---

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

---

### 3.16 「没人读」被报成「不敏感」= 最难发现的假绿

P3 查清的事实：**136 个仿真参数里只有 4 个真的被代码读**（`mjcf.build` 的
`params.get`），另 1 个（`density_kg_m3`）被读但不在表里，**其余 132 个没有任何
代码读过它们**。

如果把 132 个"没人读"的参数报成"敏感度低"，读的人会理解成
**「评估过，不重要」**——而真相是**从未接入**。这是 AGENTS §0.1 在
「一个字段是否被消费」这个维度上的复刻，而且比它更隐蔽：被读的参数
至少有输出可比，没被读的参数**没有任何可观察差异**。

**约定**：报四类，各报各的，措辞必须不同：

| 类别 | 措辞必须说清 |
|---|---|
| `wired` | 改了模型真的变 |
| `wired_noop` | **被代码读取**，但本设计里没有对应机制 |
| `unwired` | **没有任何代码读它**（不是"低敏感"） |
| `missing` | 代码读了但**表里没有这一项** |

**判别**：任何"某维度敏感度低/影响小"的结论，先问一句——
**这条链路上有人读它吗？** 读的入口是 `grep -rn "params\.get("`。

**顺带一条**：接线的事实来源要**扫源码**，不要手写一张表
（手写的表和代码分叉的方式是"表里多一个、代码里没有"）。
`(?<![\w.])` 这个后向断言是必要的——`joint.params.get("range_deg")` 是
**关节私有参数**，不是 `ParamSet`，不加断言会把它误报成"表里缺项"。

---

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
npm test                       # 729 项单测（test_design + test_phases）
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
| 7 块板没网表 | 175 个元件判不出角色 | 用户补网表，或 `.ioc` + 手工确认 |
| MCU 映射 | 板名 vs `.ioc` 无法自动对应 | 用户给 `--board-mcu` |
| Renode 不能在本机跑 | 沙箱拦截锁文件 | 需 DSH 侧放宽，或在外部执行 `.resc` |
| ARM 工具链未装 | 只影响重编 C 源码 | 用户按需装 |
| 能力表只有 14 个型号 | 表外型号 `explain_miss()` 会解释 | 用户扩表（我不替他猜 datasheet 数值） |
| 哈希 `kind` 不为生成器补映射 | 生成器只对新工程有效 | **用户已决定如此，不要改** |
| 立创自动布线**已通**（曾记错两次） | `route=native` → `2/2` 网布通、9 段 45° 单层、0 过孔、DRC 零违规。真因见 §3.20 | 已修 |
| 立创产物落盘被权限拦 | 生成正常，写盘报「未启用扩展和独立脚本的外部交互权限」 | 人在立创设置里开一次 |
| PCB 的 Y 轴（**已对照**） | 板框 `R` 模式原点是**左上角**、向 −Y 延伸；写错不报错、只让布线 0 段（见 §3.20） | 已修 |
| 立创只认 lcsc/mpn | `r3demo` 4 个元件全 `no_symbol`（EasyEDA 哈希 kind） | **P19 已给数据源**，但回填要人指定 lcsc（导入器不补） |
| 能力表 `fills`/`provides` 只能人给 | 目录里有尺寸/封装，没有「它是什么」「它能干什么」 | `parts-promote --role --needs`（猜语义 = 假通过） |
| BGA 等特殊封装没尺寸 | `package_sizes` 查不到 → `unknown` 且尺寸全 0 | 需要时人工录一行 |
| MCU 的 AF 复用表没录 | `pins.h` 只给引脚，不给"这个脚能做 I2C 吗" | 逐芯片从 datasheet AF 表录入（没抄就不写） |
| 固件抽取只认 STM32 HAL | Arduino/AVR/ESP-IDF 报 `not_ready` | 加一族就加一张 `_API_KINDS` 表 |
| 固件抽取是单行匹配 | 跨行写法抽不到，进 `unpaired` | 需要 AST 级分析（目前不做，不猜） |
| `contact_sheet` 没跑过 | 本机没有 ffmpeg → 返回可执行命令行 | 装 ffmpeg 后粘那条命令 |
| 校准默认用 V7 判 | V7 只判翻倒，不判具体场景（爬台阶…） | 传自己的 `Runner` |
| 校准是贪心、不回溯 | 一条失败最多动 2 个参数 | 参数空间大时要做实验设计 |
| 结论页只显示层结论 | 点横条跑的是全量判定，不定位到具体 finding | P2（按对象聚合）|
| `edit_field` 只聚焦 `electrical.power_w` | 其它字段能跳到 Inspector，但没专门聚焦 | 按需加 |
| `external` 类只能"定位" | 要人做的（装 ffmpeg、立创布线）没有勾选记录 | P2/P4 |
| 2D 画布没按 `entities.status` 着色 | 索引已有，画布没消费 | 下一版 |
| 132 个仿真参数未接线 | **这是结论不是 bug**：接哪个由人定，现在至少看得见 | 人定优先级 |
| 敏感度扰动只有 ×0.5 / ×2 | 非线性参数可能两点都迟钝 | 需要时加 ±10% 细扫 |
| 固件矩阵是面板不是画布 | 表格能回答"配没配、用没用"；画布要先改 Layout | 需要时再做 |
| 快照里的矩阵每次扫源码 | 工程大时会慢 | 按 mtime 缓存 |
| 面板看不到工作区草稿 | `snapshot` 按工程走；工程不存在时面板不渲染 `req`。CLI `req-draft --list` 与 `hw_req_draft(action="list")` 能列 | 前端空态里加一栏 |
| 草稿只能整体替换 | 没有"改一个字段"的动作 | 模型重发整份（小） |
| `Requirement.numbers` 无消费方 | 填了不影响任何结论（已在校验里报） | 给 coverify 接上 |
| 画像里没接 `nextstep` | 断言失败只给 `on_fail` 文案 | 可接但非必须 |
| 点板没有板级聚合 | P2 的另一半（单元/链路按 board 聚合）没做 | 按需 |
| 徽章只算阻断 + 失败层 | 缺数据/默认值常驻，进徽章等于没信号 | 要看得进「判定」分区 |

`示例-多板` 当前 `handoff` 结论：**不可打板**，
12 阻断 / 6 缺数据 / 7 默认值 / 2 不会验 / 175 未接线。

（阻断数从 2 变 12 是 **P1** 的结果：blocking 原来把一层里 10 个几何问题
拼成一个总条目，现在一条 finding 一条待办。**更准，不是变多**——
同一批问题没有报两遍。）

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

## 8. 操作手册（照着做，不是读着懂）

前面七章讲"为什么"，这章是**能直接照做的命令与判别流程**。
遇到对应症状时跳到这里，别从头推理。

### 8.1 Access denied 但权限看起来正常 → 三步判别

**症状**：`System.UnauthorizedAccessException: Access to the path '...' is denied`，
但你能删能建该目录。

```bash
# 第 1 步：查 ACL。当前用户有 (F) 且无 (DENY) → 不是权限问题
icacls "C:\Users\<u>\AppData\Roaming\renode"

# 第 2 步：用同语义的 Win32 调用复现独占打开
python -c "
import ctypes
from ctypes import wintypes
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.CreateFileW.restype=wintypes.HANDLE
k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
  ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
h=k.CreateFileW(r'C:\Users\<u>\AppData\Roaming\renode\config.lock',
                0xC0000000,0,None,2,0,None)
print('OK' if h!=ctypes.c_void_p(-1).value else 'FAIL err=%d'%ctypes.get_last_error())
"

# 第 3 步（最关键）：最小命令对照实验
# 只做不加载 CPU 的那一步 → 成功；加上 CPU 加载 → 被拒。
# 两步结果不同 = 功能级拦截，不是文件系统问题。
```

**判定后立刻停止**：不要再去试 `--config` 换位、重定向 `APPDATA`/`TEMP`、
`cmd` 包装、计划任务、预建目录、预建锁文件、`chmod`。这七条实测全无效。

改成：代码返回 `not_ready` + 附被拒路径；生成宿主外可执行脚本。

### 8.2 中文引号 / 反引号导致语法错，报错位置在下游

**症状**：`SyntaxError` 指向的行看起来完全正常。

```bash
# 30 秒定位
grep -n '[“”‘’]' file.py                 # 中文引号
grep -n '`' src/client/styles.ts         # TS 模板字面量里的反引号
```

**改法**：字符串值里的中文引号换「」；docstring 里可以保留。
TS 模板字面量内不写反引号、不写正则（`\d` 会被吃掉）。

### 8.3 退化验证：确认断言真的能红

**每条断言写完立刻做，90 秒。** 不做这一步的断言很可能是恒真的。

```bash
cp impl.py impl.bak
python - <<'PY'
import io
p='impl.py'; s=io.open(p,encoding='utf-8').read()
s=s.replace('<关键判断>', 'pass  # TEMP: 模拟退化')
io.open(p,'w',encoding='utf-8').write(s)
PY
python -m hw_velxio.scripts.check_xxx        # 必须 FAIL
cp impl.bak impl.py && rm impl.bak
python -m hw_velxio.scripts.check_xxx        # 必须 PASS
```

**特别注意**：所有 `if ...: continue` 形式的闸门，去掉后测试**天然还是绿的**
（"什么都没发生"正是期望）。这类必须专写正面断言（`assert added == 0`）。

本次实测有效的三个退化：
| 改什么 | 期望失败信息 |
|---|---|
| `_attribution_map` → `return {}` | `D1 被判为 signal_diode，但 derive() 只给了它 connected` |
| 去掉 `only_declared` 闸门 | `added=13`（期望 0） |
| 读 `budget.json` 而非 `coverify.json` | `0 条`（期望 7） |

### 8.3b `s.replace(a, b)` 里 `a` 是空串 → 整份文件被插成 50 万行

**症状**：一个 400 行的文档变成 528867 行，每条 `### 标题` 前面多出一个字符
（前缀拼出来正是被插入的那段文本的首字母序列）。

**根因**：用切片当 `old`——

```python
i = s.index(start); j = s.index(end)     # ← end 在文件里出现在 start **之前**
s = s.replace(s[i:j], block)             # i>j → 切片是 '' → 见下
```

`str.replace('', X)` 会在**每个字符之间**插入 X，长度变成
`len(s) * (len(X) + 1)`。不报错，文件看着还在，内容全毁。

**判别**：`wc -l` 突然到十万量级；`grep -c '^### '` 异常大。

**改法**（三条一起用）：

```python
assert s.count(start) == 1          # 锚点唯一
i = s.index(start)
j = s.index(end, i)                 # ← 从 i 之后找，不是全文第一个
assert i < j and j - i > 100        # 空/过短切片就是上面那个形状
s = s[:i] + block + s[j:]           # 用拼接，不用 replace
```

**可恢复**：`R` 被插在每两个原字符之间，所以原串是
`S[k] = text[len(R) + k*(len(R)+1)]`，`len(R)` 由「第二次出现 R 的位置 − 1」
给出。实测 528867 行原样还原成 541 行，一个字不差。

**顺带一条**：这个仓库的 `CHANGES.md` / `docs/execution-plan-v3.md` 当时是
**未跟踪文件**（`git status` 显示 `??`），所以 `git checkout` 救不了。
改长文档前先 `git add`（或先 `cp` 一份）。

### 8.4 Windows 沙箱内启动长驻服务

**症状**：启动成功、同一次调用能连上，下次调用进程已消失。

试过无效：`Popen(start_new_session)`、`DETACHED_PROCESS`、
`CREATE_BREAKAWAY_FROM_JOB`、WMI、`nohup &`。原因：Job Object 带
`KILL_ON_JOB_CLOSE`，命令结束整棵树被回收。

**可用做法**：

```python
# 1) 写 .cmd —— 必须用二进制写 CRLF，不要用 echo 重定向
import io
s = ("@echo off\r\n"
     "cd /d D:\\proj\r\n"
     '"C:\\path\\python.exe" -m uvicorn app:main --port 3080\r\n')
io.open("run.cmd", "wb").write(s.encode("ascii"))

# 2) 注册并启动（不要加 /RL HIGHEST，会 Access denied）
#    schtasks /Create /TN "my-backend" /TR "D:\\proj\\run.cmd" /SC ONCE /ST 23:59 /F
#    schtasks /Run /TN "my-backend"
```

**验证**：隔 30 秒后在**另一次调用**里 `curl http://127.0.0.1:3080/health`。
同一次调用内能连上不算。

### 8.5 参数不生效（输出与输入无关）

**症状**：换 3 个不同参数，产物指纹完全相同。

**判别**：打印实际发出的 body，与 schema 逐字比对键名。

**修复**：服务端加 `model_config = ConfigDict(extra="forbid")`，
或客户端发送前做字段白名单守卫（本仓库用后者，见
`velxio_client.py:136` 的 `_compile_body()`）。

**验证**：同一接口跑 3 组参数，断言产物**两两不同**。
本仓库实测 uno/mega/leonardo 的 hex md5 = `6cd87fb1` / `2df5402f` / `2478f26c`。
单次调用通过不算验证。

### 8.6 跑命令前先确认这 6 个前提

跑任何 `design_cli` 命令前，先确认这 6 个前提，能省掉大半排查：

| 前提 | 怎么确认 |
|---|---|
| 在仓库根跑 | `pwd` 应是含 `hw_velxio/` 的目录 |
| 工程存在 | `python -m hw_velxio.design_cli list` |
| 产物已生成 | 看 `<工程>/ladder.json` 等是否在 |
| 判定器返回 dataclass | 用 `.to_dict()`，不是下标访问 |
| 中文限制 | 字符串值里不嵌中文引号 |
| 闸门有出口 | 写 `continue` 时同时写确认通道 |

### 8.7 验收脚本清单（一条命令验一层）

```bash
cd <repo-root>                    # 必须在这里，不在 hw_velxio/

npm test                                         # 729 项单测
python -m hw_velxio.scripts.check_m1             # M1 四条硬验收
python -m hw_velxio.scripts.check_units_links    # P5
python -m hw_velxio.scripts.check_generate       # P6
python -m hw_velxio.scripts.check_m3             # M3 P7+P8
python -m hw_velxio.scripts.check_ladder         # 阶梯
python -m hw_velxio.scripts.check_justify        # 归因
python -m hw_velxio.scripts.check_handoff        # 交接
python -m hw_velxio.scripts.check_m18            # P18 统一判定
python -m hw_velxio.scripts.check_p17            # P17 立创发射（真机那几条要装 lceda）
python -m hw_velxio.scripts.check_p19            # P19 元件目录（真机那两条要联网）
python -m hw_velxio.scripts.check_p20            # P20 固件（真编译那条要 gcc）
python -m hw_velxio.scripts.check_p21            # P21 闭环（真 MuJoCo 那条可选）
python -m hw_velxio.scripts.check_p22            # P22 agent 循环（全假模型，0 token）
python -m hw_velxio.scripts.check_p23            # P23 测试规模与降级路径（子进程真跑）
python -m hw_velxio.scripts.check_p0             # P0 结论层（前端契约 + 排序 + 过期）
python -m hw_velxio.scripts.check_p1             # P1 下一步动作（映射表 + 前端接线）
python -m hw_velxio.scripts.check_p2             # P2 按对象聚合（索引 + 画像 + 三态）
python -m hw_velxio.scripts.check_p3             # P3 参数敏感度（接线扫描 + 两层扰动）
python -m hw_velxio.scripts.check_p4             # P4 固件矩阵（每脚一行 + 四类状态）
python -m hw_velxio.scripts.check_p5             # P5 需求草稿 + 从零开局（38 条）
```

**立创侧诊断脚本**（不是验收，是「说不清就量一遍」用的）：

```bash
python -m hw_velxio.scripts.probe_route_geom     --session <sid>   # 元件/焊盘/板框/走线 + autoRouting
python -m hw_velxio.scripts.probe_route_session  --path x.eprj3    # 同一工程不同窗口来源对照
python -m hw_velxio.scripts.probe_migrate                          # §15.1 云槽位/save 真值 + §15.3 迁移配方
python -m hw_velxio.scripts.lceda_probe                            # 版本/能力表/会话
```

布线 0 段时**先跑 `probe_route_geom`**：它会同时打出板框源数组与焊盘坐标，
一眼就能看出「板框 y 区间有没有覆盖焊盘」（AGENTS §3.20）。

**注意**：脚本名不要加 `check_` 前缀重复（`$s` 循环时容易写成 `check_check_m1`）。

改动 Python 后至少跑 M1 + 改到的那个阶段；改 TS 后跑
`npm run check && npm run build && node scripts/check_tools.mjs`。

### 8.8 清理验收残留

验收会往用户工作区写夹具与产物。收尾三件事：

```bash
# 1) 删夹具工程（先确认它确实是夹具）
python -c "
import sys; sys.path.insert(0,'.')
from hw_velxio.design.store import DesignStore
d=DesignStore('m1_p2_accept').load()
print([p.ref for p in d.parts])   # 应等于 ['U1','R1','D1']
"

# 2) 恢复被验收改动的真实工程（links_list 必须清空）
python -c "
import sys; sys.path.insert(0,'.')
from hw_velxio.design.store import DesignStore
s=DesignStore('示例-多板'); d=s.load()
d.links_list=[]; s.save(d); print('restored')
"

# 3) 删临时文件
rm -f *.json.tmp
```

**最容易忘的是第 2 条**：`promote` 过的 inferred 链路会留在 `links_list` 里，
等于替用户做了决定。

**P23 的新约定**：合成夹具放在 `tests/fixtures/designs/*.json`，**每个都要在
`test_phases.FIXTURE_EXPECT` 里登记它存在的理由**（`clean` / `validate` /
`geometry`）。没登记的夹具只是躺在磁盘上的 JSON——`check_p23` 的 3b 条会点名。
