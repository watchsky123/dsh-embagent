# plan-v6：从"读得懂"到"复现得起"

> 目标（用户提的测试）：**读 `示例工作区` → 写一份完整规格 md → 不看原工程，
> 从 0 建一个新工程 → 比对"是不是差不多"。**
>
> 这份文件先回答"现在能不能做"，再给"缺什么、怎么做"。
> 结论：**能做，但目前只能做"手工版"** —— 缺 3 个能力（spec 抽取 / 双工程比对 /
> 隔离验证）。这三块就是 v6 的 P48–P50。

---

## 1. 现状核查（逐条给证据，不是感觉）

用 `audit` 与直接读盘跑出来的一手数字：

| 事实 | 数字 | 来源 |
|---|---|---|
| 原工程元件 | 316 | `示例-多板/design.json` |
| 网络 | 119 条，其中**具名 64** 条 | 同上（其余是 `$1N*` 自动名） |
| 板 | 8 块，**只有 1 块有具名网** | `units.json` |
| 元件 `lcsc` | **0/316** | 同上 |
| 元件 `kind` | **316/316 是 EasyEDA 哈希** | 同上（§1.3：不给哈希补映射） |
| 单元角色 | 8 个单元，`mcu` 1 + `unknown` 7 | `units.json` |
| CubeMX 工程 | **6 个 `.ioc`**（在**工作区根**，不在工程里） | `*.ioc` |
| 固件二进制 | 7 个 `.bin` | 根目录 |
| BOM | `BOM_drone.csv` / `BOM_...xlsx` | 根目录 |
| `audit` 结论 | `failed`（release 12 阻断 / 6 缺数据），6 段 `not_ready` | `audit 示例-多板` |

### 1.1 已经能做到的（直接可用）

| 能力 | 命令 | 对这个实验的用处 |
|---|---|---|
| 结构抽取 | `units` / `justify` / `snapshot` | 板→单元→角色；元件为什么在 |
| 引脚事实 | `fwgen`（`.ioc` 解析）+ `ioc-links` | 84 个 label、186 个引脚，可写进规格 |
| 固件事实 | `firmware` / `fw-matrix` | "哪些脚真的被用到" |
| 判据 | `audit` / `handoff` | 原工程的**可打板结论**（12 阻断）|
| 从 0 造 | `req-draft`→`generate` / `synth` / `scaffold` | 新工程那一半 |
| 出板 | `emit`（8 段）| 新工程能不能真的出板 |

### 1.2 做不到的（这就是 v6 要补的）

| 缺口 | 现状 | 后果 |
|---|---|---|
| **工程 → 规格 md** | 只有 `handoff.md`/`audit.md`（**结论**文档，不是规格）| 写不出"这份设计要做什么" |
| **需求承载能力** | `Requirement` 只有 7 个字段（id/title/roles/interfaces/constraints/numbers/notes）| 装不下元件清单、引脚、网表、板框 |
| **双工程比对** | 无 `compare`。`parity` 只比"同一工程内 网表 vs 设计" | "差不多"只能靠人眼看 |
| **隔离验证** | 无工具保证"不看原工程" | 实验可被污染而不自知 |

### 1.3 一个必须先说清的前提

原工程 **0/316 有 lcsc、316/316 的 kind 是哈希**。所以：

> **规格里能写清"这是什么功能、分几个单元、板多大、哪些网络连着"，
> 但写不出"用哪一颗料"**（型号不可知，且用户已决定不猜哈希 kind，§1.3）。

于是"复现得差不多"只能定义在**结构层**：

```
✅ 可比：板数 / 单元数与角色 / 网络数与连接关系 / 接口协议族 / 引脚分配 /
        板框尺寸 / 电源拓扑（几条轨、谁给谁）
⚠️ 不可比：具体型号、成本、封装 —— 原工程没有这些数据
```

这条要写在规格 md 的第一节，否则比对分数会被误读成"还原度 90%"。

---

## 2. 外部参考（为什么这样设计）

| 来源 | 关键结论 | 对本项目的用法 |
|---|---|---|
| [MultModLM（arXiv 2606.27666）](https://arxiv.org/abs/2606.27666) | ①"同一功能有多种正确画法"（表示不唯一）；②**LLM 当裁判与人类评分近乎零一致**，在结构精确的领域不可靠 | **比对必须是确定性算法**（规范化后逐项打分）；`compare` 里不许出现"AI 说像" |
| [Automated PCB Reverse Engineering (WOOT'17)](https://www.usenix.org/system/files/conference/woot17/woot17-paper-kleber.pdf) | 逆向从**图像/制造数据**重建网表，靠拓扑而不是靠命名 | 规格里要写**连接签名**（pin-set），不是网络名 |
| [AI agents for reverse-engineering legacy boards](https://suhasbhairav.com/blog/ai-agents-for-reverse-engineering-and-modernizing-legacy-circuit-boards) | 流程 = 识别 → 分块 → 画连接 → 复核；每步留证据 | `spec` 的 provenance 设计：每行标"来自哪个产物/字段" |

**结论**：`compare` 的核心不是"打分"，是**先规范化再比对**。两次生成不会同名、
同 pin 序、同 ref —— 直接比字符串得到的是噪音。

---

## 3. 实验协议（v6 跑的就是它）

### Phase A —— 抽取（在原工作区）
1. `spec 示例-多板 --out=<w>/spec.md --json=<w>/spec.json`
2. 人只读这份 md（**不看原工程目录**），确认规格描述对不对

### Phase B —— 隔离重建（新工作区）
1. 把 `spec.md` 拷到**另一个工作区**（`示例复现区`），`workspace --path=...`
2. `req-draft`（由 spec 生成草稿）→ 人点确认 → `req-apply`
3. `generate` 出候选 → 选一个 → `scaffold route=generate`（或 `synth`）
4. `emit` 出板；`mech`/`dfm`/`audit` 走完

### Phase C —— 比对（确定性）
`compare 示例-多板 复现工程` 输出六级分数 + **逐条不可比项**。
判"差不多"的线（写在 `compare` 的 describe 里，不写死成魔法阈值）：

| 级 | 指标 | 权重建议 |
|---|---|---|
| L1 结构 | 板数、单元数、角色集合（Jaccard）| 高 |
| L2 网络 | 网络数、**连接签名**集合的 Jaccard、最大连通分支 | 高 |
| L3 接口 | 协议族集合（UART/SPI/I2C/CAN/USB…）| 高 |
| L4 引脚 | 同一 MCU 型号下，占用的引脚集合 | 中 |
| L5 尺寸 | 板框长宽比、面积比 | 中 |
| L6 电源 | 电源轨集合 + "谁给谁供电"的边集 | 中 |

**分数不是"还原度"**，是"结构一致度"；报告里必须同时列出 `not_comparable`。

---

## 4. P48 `spec` —— 工程 → 规格（md + json）

### 4.1 输出结构（md 章节 = 人读顺序）

```
0. 这份规格是怎么来的（产物 + 指纹 + 覆盖度）
1. 功能（从 units/roles + units 的具名网推断，标 inferred）
2. 板与单元（8 块板、每块的角色、元件数、具名网数）
3. 网络与连接（按板列：签名 = 排序后的 (ref,pin) 集合；名字只作为标签）
4. 接口与协议（从 .ioc 的 USART/SPI/I2C + 网络名推断；标 source）
5. 引脚分配（.ioc → 每个 MCU 的引脚表，含 label）
6. 固件事实（.bin 向量表、源码里用到的脚、目标平台）
7. 电源拓扑（power.classify 的声明/推断两列）
8. 板框与机械（尺寸、厚度、joints）
9. **已知缺口**（0 lcsc / 哈希 kind / 175 件无网表 / 无支撑面…）
```

### 4.2 纪律

* **provenance 必填**：每条都带 `source=units.json|design.json|.ioc|<文件>`，没有来源的不写
* **不猜型号**：`kind` 是哈希就写"未知器件（哈希 kind）"，不映射
* **inferred 只作提示**：功能/协议这类推断标 `inferred`，人可改
* **覆盖度必报**：`covered = 有数据项 / 全部项`，像 `audit` 的 `checked`

### 4.3 新命令

```
spec <id> [--out=spec.md] [--json=spec.json] [--board=<PCB#…>]
```

---

## 5. P49 `compare` —— 两个工程的结构一致度

### 5.1 先规范化（这步决定分数有没有意义）

| 原始 | 规范化 |
|---|---|
| 元件 `ref` | 不参与比对；按 `(unit, role, kind-family, 邻接签名)` 匹配 |
| 网络名 | 不参与；用 **pin-set 签名**（排序后的 `(ref,pin)`，ref 换成"匹配到的同类元件序号"）|
| 引脚名 | 同一 MCU 型号下原样比；型号不同 → `not_comparable` |
| 板 | 按"元件数 + 具名网数"排序后配对（不是按名字）|

### 5.2 输出

```json
{"status":"ok",
 "levels":[{"level":"L2","name":"网络拓扑","score":0.71,"checked":119,
            "detail":{"matched":42,"only_a":77,"only_b":15}}],
 "not_comparable":[{"what":"型号/封装/成本","why":"原工程 0 lcsc、kind 全哈希"}],
 "score": 0.63, "verdict":"结构上近似，但原工程有 175 件无网表，分数被高估"}
```

**`verdict` 必须由分数 + 不可比项共同决定**，不许只给一个数。

### 5.3 为什么不用"AI 判像不像"

[MultModLM](https://arxiv.org/abs/2606.27666) 的实测结论是 LLM 裁判与人类
评分近乎零一致。本仓库的纪律（§1.1 判定只在 Python）本来也不允许。

---

## 6. P50 闭环：把 spec 变成可执行的输入

1. `spec --json` 产出里的 `roles/interfaces/numbers` 直接喂 `req-draft`
   （**7 字段不够就扩 `Requirement`**：加 `units[]`、`nets[]`、`board` 三块，
   并明确"这三块只作 scaffold 的提示，不参与判定"）
2. `scaffold route=generate` 读 spec.json → 候选
3. 全链跑 `audit`，确认新工程的 `audit` 里 `intake/architecture/parts` 三段
   不再是 `not_ready`（证明规格真的进去了）

---

## 7. P51 验收

| 脚本 | 断言 |
|---|---|
| `check_p48` | 真工程上 `spec` 能出 md；**章节齐全**；每条有 `source`；哈希 kind 不被猜成型号；`covered` 落在合理区间；退化：删掉 provenance → 断言红 |
| `check_p49` | 两个合成工程（同构但 ref/网名全不同）→ 分数必须高；改一条网 → 分数必须降且 `only_b` 出现；退化：把规范化关掉 → 同构工程分数变低 |
| `check_p50` | spec.json → req → scaffold 三段 `not_ready` 变成 ok（用 ATtiny85 小样例）|

---

## 8. 风险与"不许做的事"

| 风险 | 处理 |
|---|---|
| 原工程 175 件无网表 → 规格天然残缺 | 规格里**显式**写缺多少；比对时 `only_a` 里剔掉"无网表件"，并单独列 `blind` 数 |
| 两次生成必然不同名 | 规范化先行；比对只认签名 |
| 想把"像"说成"还原度 90%" | 报告同时给 `not_comparable` 与 `blind`；**不给单一百分比** |
| 为了让分数好看而给哈希 kind 编映射 | **禁止**（§1.3 用户决定）|

---

## 9. 排期（一段一提交）

| 段 | 内容 | 估体量 |
|---|---|---|
| **P48** | `spec`（md/json + provenance + 覆盖度）+ `check_p48` | M |
| **P49** | `compare`（规范化 + 六级 + 不可比）+ `check_p49` | M |
| **P50** | `Requirement` 扩三块 + spec→scaffold 闭环 + `check_p50` | M |
| **P51** | 端到端 demo（Phase A/B/C 各跑一次）+ `docs/spec-demo.md` | S |

做完这四段，用户那个测试就变成**一条命令可复现**的实验，而不是人工流程。