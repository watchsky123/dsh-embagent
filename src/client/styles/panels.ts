/**
 * 样式分片：各分区面板（AI 协作、工作区、结论、固件、勘察）的局部样式。
 *
 * 从原 `styles.ts` 按注释段原样切出；拼接顺序见 `styles.ts`。
 */

export const PANELS = `
/* ── AI 协作面板（一个入口，三个阶段）──────────────────────────────── */
.hwv-ai {
  flex: 0 0 auto; display: flex; flex-direction: column; gap: var(--hwv-space-3);
  padding: var(--hwv-space-4) var(--hwv-space-5);
  border-bottom: 1px solid var(--hwv-line);
  background: color-mix(in srgb, var(--hwv-accent) 6%, var(--hwv-panel));
}
.hwv-ai-row { display: flex; align-items: center; gap: var(--hwv-space-3); flex-wrap: wrap; }
.hwv-ai-input { flex: 1 1 260px; min-width: 0; width: auto; }
/* 「已发送」提示：按钮点完必须立刻有反馈（上一版点完界面毫无变化）。 */
.hwv-ai-sent { font-size: 11px; color: var(--hwv-ok); }


.hwv-btn {
  background: var(--hwv-panel-2); color: var(--hwv-text);
  border: 1px solid var(--hwv-line); border-radius: var(--hwv-radius-sm);
  padding: var(--hwv-space-2) 9px; font-size: 12px; cursor: pointer; line-height: 1.4;
  display: inline-flex; align-items: center; gap: var(--hwv-space-2);
  transition: var(--hwv-transition-color);
  flex-shrink: 0;
}
.hwv-btn:hover:not(:disabled) { border-color: var(--hwv-accent); background: var(--hwv-hover); }
.hwv-btn:disabled { opacity: .45; cursor: not-allowed; }
.hwv-btn.primary { border-color: var(--hwv-accent); color: var(--hwv-accent); }
.hwv-btn.danger { border-color: var(--hwv-err); color: var(--hwv-err); }

.hwv-select {
  background: var(--hwv-panel-2); color: var(--hwv-text);
  border: 1px solid var(--hwv-line); border-radius: var(--hwv-radius-sm);
  padding: var(--hwv-space-2) var(--hwv-space-3); font-size: 12px;
  transition: var(--hwv-transition-color);
  max-width: 220px;
}

/* segmented 视图组：一个控件四个态，aria-pressed（仿 EditorPage 的 view-mode toggle） */
.hwv-seg {
  display: flex; align-items: stretch; gap: 1px;
  background: var(--hwv-panel-2);
  border: 1px solid var(--hwv-line); border-radius: var(--hwv-radius-xs);
  overflow: hidden; flex-shrink: 0;
}
.hwv-seg-divider { width: 1px; background: var(--hwv-line); align-self: stretch; }
.hwv-seg-btn {
  background: transparent; border: none; cursor: pointer;
  color: var(--hwv-dim);
  height: 26px; padding: 0 var(--hwv-space-3);
  display: flex; align-items: center; gap: var(--hwv-space-2);
  font-size: 12px; font-family: inherit;
  transition: var(--hwv-transition-color);
}
.hwv-seg-btn:hover { color: var(--hwv-text); background: var(--hwv-hover); }
.hwv-seg-btn[aria-pressed="true"] {
  background: var(--hwv-accent); color: var(--hwv-bg);
}
.hwv-seg-btn svg { flex: 0 0 13px; }

/* 窄屏：视图组收成图标；徽章退场（信息仍在工作台里，只是不重复占宽） */
@media (max-width: 640px) {
  .hwv-seg-btn .vm-label { display: none; }
  .hwv-seg-btn { padding: 0 var(--hwv-space-4); }
  .hwv-tb-right .hwv-chip { display: none; }
  .hwv-title { display: none; }
}

/* ── 工作区设置块（工具栏按钮展开）──────────────────────────────────────
   它是设置，不是开始：收起时不渲染（不是 display:none），因此不会在
   空态里抢主操作的视觉权重。 */
.hwv-settings {
  flex: 0 0 auto; display: flex; flex-direction: column; gap: var(--hwv-space-3);
  padding: var(--hwv-space-4) var(--hwv-space-5);
  border-bottom: 1px solid var(--hwv-line);
  background: var(--hwv-panel-2);
}
.hwv-warn-line { font-size: 11px; color: var(--hwv-err); line-height: 1.7; }

/* ── 结论（首屏）────────────────────────────────────────────────────
 *
 * 这一屏的回答顺序是：**这版行不行（一句）→ 九层各是什么状态（横条）
 * → 六类各多少（计数）→ 先做什么（五条）**。
 *
 * 配色只用语义色（ok/warn/err/dim），**不用紫色**：紫是"估算/选中"的
 * 唯一强调色（DESIGN.md §1.2），结论页里没有任何东西是"估算"，
 * 借过来会稀释那个信号。 */
.hwv-verdict-head {
  display: flex; align-items: baseline; gap: var(--hwv-space-4);
  flex-wrap: wrap; margin-bottom: var(--hwv-space-4);
}
.hwv-verdict-rollup {
  flex: 0 0 auto; font-weight: 600; font-size: var(--hwv-fs-title);
  padding: var(--hwv-space-1) var(--hwv-space-3);
  border-radius: var(--hwv-radius-xs);
  border: 1px solid var(--hwv-line); color: var(--hwv-dim);
}
.hwv-verdict-head:not(.blocked) .hwv-verdict-rollup {
  color: var(--hwv-ok); border-color: color-mix(in srgb, var(--hwv-ok) 45%, transparent);
}
.hwv-verdict-head.blocked .hwv-verdict-rollup {
  color: var(--hwv-err); border-color: color-mix(in srgb, var(--hwv-err) 50%, transparent);
}
.hwv-verdict-summary { flex: 1 1 260px; min-width: 0; line-height: 1.7; }

/* 九层横条：每层一格，宽度等分。**不用饼图/环形图**——三态不是占比，
   unknown 画成扇区等于说它"占了三分之一"。 */
.hwv-ladder { display: flex; gap: var(--hwv-space-1); align-items: stretch; flex-wrap: wrap; margin-bottom: var(--hwv-space-2); }
.hwv-ladder-cell {
  flex: 1 1 44px; min-width: 44px;
  display: flex; flex-direction: column; align-items: center; gap: 1px;
  padding: var(--hwv-space-2) 0;
  background: var(--hwv-panel-2);
  border: 1px solid var(--hwv-line); border-radius: var(--hwv-radius-xs);
  color: var(--hwv-text); font-family: inherit; cursor: pointer;
  transition: var(--hwv-transition-color);
}
.hwv-ladder-cell:hover { border-color: var(--hwv-accent); }
.hwv-ladder-cell .lv { font-size: var(--hwv-fs-caption); color: var(--hwv-dim); }
.hwv-ladder-cell .st { font-size: var(--hwv-fs-title); line-height: 1.2; }
.hwv-ladder-cell.pass { border-color: color-mix(in srgb, var(--hwv-ok) 45%, transparent); }
.hwv-ladder-cell.pass .st { color: var(--hwv-ok); }
.hwv-ladder-cell.fail { border-color: color-mix(in srgb, var(--hwv-err) 55%, transparent); }
.hwv-ladder-cell.fail .st { color: var(--hwv-err); }
.hwv-ladder-cell.unknown { border-color: color-mix(in srgb, var(--hwv-warn) 50%, transparent); }
.hwv-ladder-cell.unknown .st { color: var(--hwv-warn); }
.hwv-ladder-cell.not_run { border-style: dashed; }
.hwv-ladder-cell.not_run .st { color: var(--hwv-dim); }
.hwv-ladder-cell.truncated .st { color: var(--hwv-err); }
.hwv-ladder-gap { font-size: var(--hwv-fs-caption); color: var(--hwv-dim); align-self: center; }

.hwv-verdict-counts { display: flex; gap: var(--hwv-space-2); flex-wrap: wrap; margin-bottom: var(--hwv-space-4); }
/* 计数 chip：为 0 时压暗（常驻的数字等于没信号），非 0 才给颜色。 */
.hwv-chip.on {
  color: var(--hwv-err);
  border-color: color-mix(in srgb, var(--hwv-err) 45%, transparent);
}
.hwv-mark-num {
  flex: 0 0 16px; width: 16px; text-align: center;
  font-size: var(--hwv-fs-caption); color: var(--hwv-dim);
  font-variant-numeric: tabular-nums;
}
.hwv-next-action { color: var(--hwv-dim); }

/* 一步动作的按钮（P1）。它比普通按钮**小一号**：一屏里有五条待办，
  正常尺寸的按钮会在视觉上盖过那句"为什么"。 */
.hwv-step-btn {
  flex: 0 0 auto; padding: 1px var(--hwv-space-3); font-size: 11px;
  border-radius: var(--hwv-radius-xs);
}
/* 「由谁做」：external 类的补充说明。用 dim 而不是警告色——
   它不是问题，是分工。 */
.hwv-next-by { color: var(--hwv-dim); }

/* ── 固件矩阵（P4）─────────────────────────────────────────────────
 *
 * 四类结论各一色。配色**只用语义色**：这里没有任何"估算/选中"的语义，
 * 借紫色会稀释那个唯一强调色（DESIGN.md §1.2）。 */
.hwv-fw-ghost { color: var(--hwv-err); font-weight: 600; }
.hwv-fw-direction { color: var(--hwv-err); }
.hwv-fw-idle { color: var(--hwv-warn); }
.hwv-fw-ok { color: var(--hwv-ok); }
/* 表格里的次要说明（标签、原因）：不抢主结论的颜色 */
.hwv-danger-dim {
  color: var(--hwv-dim);
  font-size: var(--hwv-fs-caption);
  line-height: 1.5;
}

/* ── 勘察摘要（条目数 / 读取覆盖度 / MCU）──────────────────────────────
   它的存在是为了回答"工作区里到底有多少东西、我看了多少"。
   上一版没有这块，用户只看到 2 个工程，就以为这就是全部资产。 */
.hwv-survey {
  display: flex; flex-direction: column; gap: var(--hwv-space-2);
  padding: var(--hwv-space-3) 0 0;
  border-top: 1px dashed var(--hwv-line);
}
.hwv-survey-row { display: flex; align-items: center; gap: var(--hwv-space-3); flex-wrap: wrap; }
/* 覆盖度进度条：宽度表达"读了多少"，不是装饰。 */
.hwv-meter {
  flex: 1 1 120px; max-width: 220px; height: 6px;
  background: var(--hwv-panel-2); border: 1px solid var(--hwv-line);
  border-radius: var(--hwv-radius-pill); overflow: hidden;
}
.hwv-meter-fill { display: block; height: 100%; background: var(--hwv-accent); }
.hwv-chip.dim { color: var(--hwv-dim); }

.hwv-input-inline { width: 180px; flex: 0 0 auto; }

/* 工具栏里的工作区入口：已设时就是一条可点路径，长路径省略不撑破工具栏 */
.hwv-ws-toggle { max-width: 260px; }
.hwv-ws-toggle .hwv-ws-path {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; direction: rtl;
}
.hwv-ws-toggle[aria-expanded="true"] { border-color: var(--hwv-accent); color: var(--hwv-accent); }

/* 工作区路径条：等宽字体 + 省略，长路径不撑破卡片 */
.hwv-path {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11px; color: var(--hwv-text);
  background: var(--hwv-panel-2); border: 1px solid var(--hwv-line);
  border-radius: var(--hwv-radius-xs); padding: var(--hwv-space-2) var(--hwv-space-3);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  direction: rtl; text-align: left;
}
.hwv-path-bad { border-color: var(--hwv-err); color: var(--hwv-err); }

/** 整机实体的图例色块：与 3D 材质同一套令牌（不写死颜色）。 */
.hwv-legend i.body-metal { background: var(--hwv-p-body-metal); }
.hwv-legend i.body-prop { background: var(--hwv-p-body-prop); }
.hwv-legend i.body-shell {
  background: var(--hwv-p-body-shell);
  outline: 1px dashed var(--hwv-p-warn);
}
.hwv-legend {
  /* 浮层 HUD：和 .hwv-pane-tools 一样是覆盖在画布上的，不能参与流布局
     ——否则 100% 高的画布会把它顶出 wrap，压到下一个面板上。 */
  position: absolute; left: 0; right: 0; bottom: 0;
  z-index: var(--hwv-z-actionbar);
  display: flex; gap: var(--hwv-space-5); flex-wrap: wrap;
  padding: var(--hwv-space-2) var(--hwv-space-4);
  color: var(--hwv-dim); font-size: 11px; border-top: 1px solid var(--hwv-line);
  background: color-mix(in srgb, var(--hwv-panel-2) 88%, transparent);
  backdrop-filter: blur(2px);
  pointer-events: none;
}
.hwv-legend i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: var(--hwv-space-2); }
/* 图例色块：引用命令式调色板（随主题翻转），组件里不写内联样式 */
.hwv-legend i.src-datasheet { background: var(--hwv-p-fill-datasheet); }
.hwv-legend i.src-measured { background: var(--hwv-p-fill-measured); }
.hwv-legend i.src-derived { background: var(--hwv-p-fill-derived); }
.hwv-legend i.src-estimated { background: var(--hwv-p-fill-estimated); }
.hwv-legend i.src-error { background: var(--hwv-p-fill-error); }
/* 铜线 / 飞线：线型不同（实线 vs 虚线），颜色也不同 —— 混看会误判"已布线" */
.hwv-legend i.wire-copper { background: var(--hwv-p-trace); }
.hwv-legend i.wire-ratline {
  background: repeating-linear-gradient(
    to right, var(--hwv-p-wire3d) 0 2px, transparent 2px 4px);
}
/* 3D 安装面：顶层实心、底层带描边（双面板一眼可辨） */
.hwv-legend i.face-top { background: var(--hwv-p-fill-derived); }
.hwv-legend i.face-bottom {
  background: var(--hwv-p-fill-estimated);
  outline: 1px solid var(--hwv-p-plain-stroke);
}

/* ── 发射时间线（P2）──────────────────────────────────────────────────── */
.hwv-emit-stages {
  display: flex; flex-direction: column; gap: 0;
  border: 1px solid var(--hwv-line); border-radius: var(--hwv-radius-sm);
  overflow: hidden; margin: var(--hwv-space-2) 0;
}
.hwv-emit-stage {
  display: flex; align-items: center; gap: var(--hwv-space-3);
  padding: var(--hwv-space-1) var(--hwv-space-3);
  border-bottom: 1px solid var(--hwv-line);
  font-size: var(--hwv-fs-caption);
}
.hwv-emit-stage:last-child { border-bottom: none; }
.hwv-emit-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--hwv-dim); flex: 0 0 auto;
}
.hwv-emit-stage.ok .hwv-emit-dot { background: var(--hwv-ok); }
.hwv-emit-stage.not_ready .hwv-emit-dot { background: var(--hwv-warn); }
.hwv-emit-stage.failed .hwv-emit-dot { background: var(--hwv-err); }
.hwv-emit-name { min-width: 64px; color: var(--hwv-text); }
.hwv-emit-status { color: var(--hwv-dim); }
.hwv-emit-stage.failed .hwv-emit-status { color: var(--hwv-err); }
.hwv-emit-stage.not_ready .hwv-emit-status { color: var(--hwv-warn); }
.hwv-emit-retry { margin-left: auto; }

/* ── P3 治理面板（预检 / 回读 / 成熟度 / ADR）───────────────────────── */
.hwv-gov-mark {
  width: 14px; text-align: center; font-weight: 600;
  color: var(--hwv-dim);
}
.hwv-gov-counts {
  display: flex; flex-wrap: wrap; gap: var(--hwv-space-2);
  margin: var(--hwv-space-2) 0;
}
.hwv-table-tight td, .hwv-table-tight th { padding: 2px var(--hwv-space-2); }
.hwv-gov-dims { display: flex; flex-direction: column; gap: 2px; margin-top: var(--hwv-space-2); }
.hwv-gov-dim {
  display: flex; align-items: baseline; gap: var(--hwv-space-3);
  padding: 2px var(--hwv-space-2);
  border-left: 2px solid var(--hwv-line);
  font-size: var(--hwv-fs-caption);
}
.hwv-gov-dim.pass { border-left-color: var(--hwv-ok); }
.hwv-gov-dim.fail { border-left-color: var(--hwv-err); }
.hwv-gov-dim.unknown, .hwv-gov-dim.missing { border-left-color: var(--hwv-warn); }
.hwv-gov-dim .id { color: var(--hwv-text); min-width: 84px; }
.hwv-gov-dim .st { color: var(--hwv-dim); min-width: 56px; }
.hwv-gov-dim .detail { color: var(--hwv-dim); }
.hwv-gov-dim .need { color: var(--hwv-warn); margin-left: auto; }

.hwv-table { width: 100%; border-collapse: collapse; font-size: var(--hwv-fs-caption); }
.hwv-table th {
  text-align: left; color: var(--hwv-dim); font-weight: 600;
  border-bottom: 1px solid var(--hwv-line); padding: 2px var(--hwv-space-2);
}
.hwv-table td { border-bottom: 1px solid var(--hwv-line); }
.hwv-table tr:last-child td { border-bottom: none; }
`
