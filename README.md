# hw_velxio — 硬件工作台（DSH 插件）

一份 **mm 规范模型**，两个投影：**2D 电路是电气/拓扑权威，3D 装配是物理/机械权威**。
体素网格只做求解索引（重叠/keepout/限高），不参与渲染。这正是 [方案-v3.md](方案-v3.md)
的核心判断：hwforge 的 3D 不是"要做好的烂 3D"，而是一个被误当成渲染器的求解器。

落到 DSH 里，它是一个**会话视图**（与 Chat / Trajectory 并列的「硬件工作台」标签），
不是右侧边栏。

```
┌ 硬件工作台 ─────────────────────────────────────────────────────────────┐
│ [工程▾] [检查] [预算] [核对] [导出] [2D|并排|3D] [AI 评审]   [状态徽章]   │
├──────────────────────┬──────────────────────┬──────────────────────────┤
│ 2D 电路 · 电气权威    │ 3D 装配 · 物理权威    │ 检查器                    │
│ 拖拽移动 / 右键旋转   │ 轨道 / 爆炸 / 剖面    │ 尺寸/来源/位置/该件判定    │
├──────────────────────┴──────────────────────┴──────────────────────────┤
│ [判定] [预算] [未决] [核对] [证据/轨迹] [制造] [积木]                    │
└────────────────────────────────────────────────────────────────────────┘
```

## 快速开始

```bash
# 依赖（一次）
cd hw_velxio && npm install --legacy-peer-deps

# 构建：宿主半（tsc）+ 客户端半（tsdown，three.js 内联）
npm run build

# 类型检查（两半分别查：宿主用 node 类型，客户端用 DOM+JSX）
npm run check

# 客户端 bundle 冒烟：伪宿主里注册视图并渲染一次（不需要浏览器）
node scripts/check_client_runtime.mjs

# 自检：产物形状 + 客户端注册 + 宿主契约（重读 app.asar）+ Python 端到端
cd .. && python hw_velxio/scripts/check_plugin.py

# 单元测试（25 个用例，标准库 unittest）
python -m unittest hw_velxio.tests.test_design -v
```

仓库里已预置两个工程，打开视图即可看到内容：

| 工程 | 来源 | 内容 |
|---|---|---|
| `001-blink` | Wokwi diagram 导入 | 3 元件 / 5 网表（含板卡引脚） |
| `002-sensor-node` | KiCad PCB + BOM | 3 封装 / 板轮廓 40×30mm / LCSC |

## 装进 DSH

构建完成后，把这个包加入 DSH profile（与其它插件同样的方式：`file:` 安装或
链接到 profile 的插件目录），重启 Web 后会话视图标签栏会出现「硬件工作台」。

本仓库已验证的是：**bundle 能在伪造的宿主里注册成功并渲染出界面**（见
`scripts/check_client_runtime.mjs`）。真实 profile 的安装会写工作区外的
`~/.dsh`，属于需要单独确认的动作，没有在本次执行里做。

## 结构

```
hw_velxio/
├── design/                 规范模型（Python，唯一事实源）
│   ├── model.py            DesignModel / Part / Placement / Board（mm）
│   ├── boards.py           板卡预设（Velxio 像素 ↔ 毫米，含来源标签）
│   ├── catalog.py          常见元件尺寸表（datasheet / estimated）
│   ├── projection.py       mm ↔ px 投影；旧 Velxio 画布换算（Y 翻转只做一次）
│   ├── importers.py        KiCad PCB（Edge.Cuts+封装+焊盘）/ BOM / Wokwi
│   ├── physical.py         体素求解 + 9 类检查（重叠/轮廓/高度/跨度/孔/来源…）
│   ├── budgets.py          五轨预算（复用 hwforge.budget 代数）
│   ├── discipline.py       未决决策闸门 + append-only 轨迹（复用 hwforge）
│   ├── crosscheck.py       画图 vs 事实 + 2D↔3D 往返一致性
│   ├── fabrication.py      BOM/坐标/DXF/模型 + README（不伪造 Gerber）
│   └── store.py            project/<id>/ 原子写入
├── design_cli.py           Python 入口：一次调用、一行 JSON
├── bridge.py               hwforge 积木 + Velxio HTTP 能力
├── velxio_client.py        Velxio 后端薄客户端
├── src/                    宿主半（TypeScript）
│   ├── bridge.ts           进程调用 + 最后一行 JSON 解析
│   ├── service.ts          Typert Remote 端点（hwvelxio/*）
│   ├── tools.ts            8 个模型工具（hw_*）
│   └── shared/types.ts     宿主/客户端共享的纯类型
├── src/client/             客户端半（React）
│   ├── view.ts             conversation.view 注册（唯一的契约落点）
│   ├── HwIdeView.tsx       四栏 IDE 外壳
│   ├── Circuit2D.tsx       canvas 2D（拖拽/旋转/平移/缩放）
│   ├── Assembly3D.tsx      three.js 场景（挤出板/实体盒/线缆管/剖面）
│   ├── Inspector.tsx       选中对象属性与来源编辑
│   ├── panels.tsx          七个底部面板
│   └── store.ts            一个 store：2D/3D 共享 selectedRef
├── examples/               KiCad / Wokwi / BOM 夹具
├── project/                工程产物（design.json、trace.json、fab/…）
├── scripts/check_plugin.py 自检
└── tests/test_design.py    25 个用例
```

## 数据流（一次读 = 一次往返）

`hwvelxio/snapshot` 一次返回：规范模型、2D 投影、3D 场景、物理检查、预算、
未决闸门、交叉核对、证据哈希。客户端不重算任何判定——它只显示与操作。

编辑（移动/旋转/加删元件）也走 Python：`hwvelxio/movePart` 等端点会调
`design_cli`，校验只有一份。TS 侧再写一遍校验就是第二个事实源。

## 模型工具（模型可见，有调用卡）

| 工具 | 作用 | 写 |
|---|---|---|
| `hw_projects` | 工程列表与进度 | 否 |
| `hw_snapshot` | 判定/预算/闸门摘要 | 否 |
| `hw_check` | 跑物理检查 | 是 |
| `hw_import` | 导入 KiCad/Wokwi/BOM | 是 |
| `hw_budget` | 五轨预算 | 是 |
| `hw_crosscheck` | 交叉核对 + 2D↔3D | 是 |
| `hw_export` | 制造包（受闸门约束） | 是 |
| `hw_block` | 跑一个 hwforge 积木 | 是 |

## 实施状态（对照方案-v3 的 P1–P7）

| 阶段 | 状态 | 证据 |
|---|---|---|
| P0 桥接层 | ✅ | `bridge status/catalog/run` 通过 |
| P1 统一模型 + 投影 + 旧工程兼容 | ✅ | `design/model.py`、`projection.py`；用例 `TestProjection` |
| P2 3D 网格 + 2D/3D 选中联动 | ✅ | `Assembly3D.tsx`（three.js）、共享 `selectedRef` |
| P3 EDA 导入 | ✅ 部分 | KiCad PCB/BOM/Wokwi 已通；STEP、原理图网表未做（如实报） |
| P4 物理检查叠加 | ✅ | `physical.py` + 3D 冲突着色 + 面板定位 |
| P5 预算 + 过程纪律 | ✅ | `budgets.py` / `discipline.py`；闸门拦导出 |
| P6 制造 + 核对 | ✅ | `fabrication.py` / `crosscheck.py`；DXF/BOM/坐标 |
| P7 AI 动作 | ✅ | 「AI 评审」→ `session.prompt`，只递事实不递判定 |

## 边界（写死在代码里，不是口号）

- **不伪造数据**：缺尺寸 → `estimated` 并单列；缺高度 → 薄片 + 警告；
  预算无 claim → `confidence: none`（不是"通过"）；工具缺失 → `not_ready`。
- **不伪造 Gerber**：模型没有真实焊盘与布线，制造包只导出能自洽的内容，
  缺项写进 `README.txt` 与 `missing`。
- **不可绕过闸门**：不可逆决策未闭合时，导出端点与工具都直接失败并说明原因。
- **旧画布换算只做一次**：统一 `px_per_mm`，加载时提示"已按 X px/mm 归一"；
  视觉可能轻微缩放，换取 2D/3D 同源。
- **hwforge 无 LICENSE 文件**：商业/闭源分发前必须补齐。

## 配置

`~/.dsh/hw_velxio.json`（可选）：

```json
{ "hwforge_root": "workspace/hwforge", "velxio_url": "http://127.0.0.1:3080" }
```

环境变量：`HWFORGE_ROOT`、`HW_VELXIO_ROOT`（仓库根）、`HW_VELXIO_PYTHON`
（解释器）、`HW_VELXIO_VELXIO_URL`、`HW_VELXIO_VELXIO_TOKEN`。

## 许可

本插件 MIT。依赖的 Velxio 是 **AGPLv3 + 商业双许可**；hwforge 目前**没有
LICENSE 文件**——两者都影响分发方式，详见 [方案-v3.md](方案-v3.md) §7。
