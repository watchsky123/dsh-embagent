# 前端重构（P0–P2）—— 方案与边界

> 依据：2026-11-24 的代码盘点（`panels.tsx` 1410 行 / 16 面板；`HwIdeView`
> 1056；`store.ts` 1025 / 27 方法；`styles.ts` 814 / 481 个 `hwv-*`；
> `WorkbenchFace` ~30 方法；60 个 `hw_*` 工具；UI 覆盖约 15）。
> P3/P4 不在本轮。

## 目标

| 目标 | 判据 |
|---|---|
| 加能力只改一个注册点 | 新增分区 = `features.ts` 一行 + 一个面板组件 |
| 长任务有进度 | 发射 8 段在 UI 上逐段可见；每段可单独重跑 |
| 样式可维护 | `check_dead_css` 转绿；样式拆成 tokens/layout/panels/dark |
| 信息按任务 | 结论条 + 三态徽章 + 板级汇总 + 分区深链 |
| 不退化 | 767 单测 + 57 check + 9 个 .mjs 运行时检查 |

## P0（基建，不改行为）

1. `features.ts`：分区注册表（id/label/group/order/badge）**唯一事实源**；
   `store.ts` 从它派生 `SECTIONS`/`SECTION_LABEL`/`DEFAULT_SECTION`，不再手写第二份。
2. `api.ts`：把 `WorkbenchFace` 按域收成 `project/design/verify/make/workspace`，
   store 只跟 api 说话（不再直接散调 face）。
3. `queries.ts`：按域缓存 + 订阅（`useSyncExternalStore`），替代"任何动作全量刷"。
4. `styles/`：按现有注释段切成 `tokens/layout/panels/dark` 四个字符串模块，
   `styles.ts` 只做拼接与注入；修 `check_dead_css`（动态 `hwv-fw-` 改用显式映射）。

## P1（Shell 与信息架构）

1. **结论条**：工具栏下方一行三态计数（pass/fail/unknown）+ 发射/parity 摘要。
2. **三态徽章**：`verdict` 徽章从"阻断+失败"扩到含 `unknown`（缺数据不能看起来像通过）。
3. **板级汇总**：按 `board_id` 聚合 findings → 板 chips（点击选中该板并切到判定）。
4. **深链**：`store.openSection(section, ref?)`；键盘 `[`/`]` 切分区，`?` 查看快捷键。
5. 保留现有 group/segmented 结构（不重命名分组），避免打断 9 个运行时检查。

## P2（Job 模型）

1. Python：`emit(..., on_progress=cb)` 每段结束回调 → `cmd_emit` 增量写
   `emit.json`（此前只在结束时写，UI 看不到进行中状态）。
2. `jobs.ts`：客户端 Job 记录（running / stages / error），`store.startEmit(jobs)`
   驱动；运行期间 store 轮询已有 snapshot（2.5s）即可逐段刷新。
3. Emit 面板：8 段时间线（流水中/ok/not_ready/failed），每段一个"只重跑这段"按钮
   （`emitRun(project, stage)`），跑完显示 parity/预检摘要。
4. 边界：**没有 SSE**；进度粒度 = 段（不是每颗器件）。Python 侧只写状态，不推流。

## 验收

- `npm run build` + `npm run check` 通过；
- `check_dead_css` 绿；
- `node scripts/check_client_runtime.mjs` / `check_layout_runtime.mjs` /
  `check_layout_browser.mjs` / `check_canvas_draw.mjs` / `check_assembly3d.mjs` 通过；
- 新增 `scripts/check_p70.py`：注册表唯一、store 走 api、样式分片齐全、
  发射面板有逐段按钮（静态 + 结构断言）。
