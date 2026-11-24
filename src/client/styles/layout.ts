/**
 * 样式分片：工具栏 / 主区 / 检查器 / 底部面板 / 横幅空态。
 *
 * 从原 `styles.ts` 按注释段原样切出；拼接顺序见 `styles.ts`。
 */

export const LAYOUT = `
/* ── 3. 工具栏：两段 + 视图组 + 徽章，窄宽收成图标不整行 wrap ───────── */
.hwv-toolbar {
  display: flex;
  align-items: center;
  gap: var(--hwv-space-3);
  padding: var(--hwv-space-2) var(--hwv-space-4);
  border-bottom: 1px solid var(--hwv-line);
  background: var(--hwv-panel);
  flex: 0 0 auto;
  flex-wrap: wrap;
  row-gap: var(--hwv-space-2);
}
.hwv-tb-left {
  display: flex; align-items: center; gap: var(--hwv-space-3);
  flex: 0 0 auto; min-width: 0;
}
.hwv-tb-actions {
  display: flex; align-items: center; gap: var(--hwv-space-2);
  flex: 0 1 auto; min-width: 0; flex-wrap: wrap; row-gap: var(--hwv-space-2);
}
.hwv-tb-right {
  display: flex; align-items: center; gap: var(--hwv-space-2);
  flex: 0 0 auto;
}
.hwv-title { font-weight: 600; letter-spacing: .3px; }
.hwv-spacer { flex: 1 1 auto; min-width: var(--hwv-space-2); }

/* 状态灯：一眼回答"能不能交付"（绿=可交付无错，黄=有警告/未决，红=有错误） */
.hwv-dot {
  width: 9px; height: 9px; border-radius: var(--hwv-radius-pill);
  flex: 0 0 9px; background: var(--hwv-dim);
  box-shadow: inset 0 0 0 1px var(--hwv-line);
}
.hwv-dot.ok { background: var(--hwv-ok); }
.hwv-dot.warn { background: var(--hwv-warn); }
.hwv-dot.err { background: var(--hwv-err); }

.hwv-chip {
  padding: var(--hwv-space-1) 7px; border-radius: var(--hwv-radius-pill);
  font-size: 11px;
  border: 1px solid var(--hwv-line); color: var(--hwv-dim);
  white-space: nowrap;
}
.hwv-chip.ok { color: var(--hwv-ok); border-color: color-mix(in srgb, var(--hwv-ok) 45%, transparent); }
.hwv-chip.warn { color: var(--hwv-warn); border-color: color-mix(in srgb, var(--hwv-warn) 45%, transparent); }
.hwv-chip.err { color: var(--hwv-err); border-color: color-mix(in srgb, var(--hwv-err) 45%, transparent); }
.hwv-chip.est { color: var(--hwv-est); border-color: color-mix(in srgb, var(--hwv-est) 45%, transparent); }

/* 阶段徽章：empty 用警告色而不是成功色——"空集通过"是假绿灯，不能画成绿。 */
.hwv-chip.phase-empty { color: var(--hwv-warn); border-color: color-mix(in srgb, var(--hwv-warn) 50%, transparent); }
.hwv-chip.phase-partial { color: var(--hwv-est); border-color: color-mix(in srgb, var(--hwv-est) 45%, transparent); }
.hwv-chip.phase-complete { color: var(--hwv-ok); border-color: color-mix(in srgb, var(--hwv-ok) 45%, transparent); }
.hwv-chip.phase-broken { color: var(--hwv-err); border-color: color-mix(in srgb, var(--hwv-err) 50%, transparent); }

/* ── 4. 主区：面板 + 可拖把手 ────────────────────────────────────────── */
.hwv-main { display: flex; flex: 1 1 auto; min-height: 0; }
.hwv-panes { display: flex; flex: 1 1 auto; min-width: 0; min-height: 0; gap: 1px; background: var(--hwv-line); }
.hwv-pane { position: relative; min-width: 0; min-height: 0; background: var(--hwv-panel-2); display: flex; flex-direction: column; }
.hwv-pane-head {
  display: flex; align-items: center; gap: var(--hwv-space-3);
  padding: var(--hwv-space-2) var(--hwv-space-4);
  border-bottom: 1px solid var(--hwv-line); color: var(--hwv-dim);
  font-size: 11px; flex: 0 0 auto;
}
/* 画布容器。
   overflow:hidden —— 画面不溢出到相邻面板（以前没有，缩放后元件会画到
   检查器上）。
   overscroll-behavior:contain —— 滚轮缩放到边界时**不链式**传给祖先滚动
   容器。这是 wheel 修复的第二层防御：第一层是非 passive 监听里的
   preventDefault，两层都要有——监听负责「缩放时不许滚」，contain 负责
   「万一还有残余 delta 也不许滚」。 */
.hwv-canvas-wrap {
  position: relative; flex: 1 1 auto; min-height: 0;
  z-index: var(--hwv-z-canvas);
  overflow: hidden;
  overscroll-behavior: contain;
  outline: none;
}
.hwv-canvas-wrap:focus-visible {
  box-shadow: inset var(--hwv-focus);
}
/* 缩放胶囊：右下角，glass 风格但不加渐变（Linear 纪律：靠 surface + hairline）。 */
.hwv-zoom {
  position: absolute; right: var(--hwv-space-3); bottom: var(--hwv-space-3);
  z-index: var(--hwv-z-actionbar);
  display: inline-flex; align-items: center;
  border: 1px solid var(--hwv-line);
  border-radius: var(--hwv-radius-md);
  background: var(--hwv-panel);
  overflow: hidden;
}
.hwv-zoom-btn, .hwv-zoom-pct {
  border: 0; background: transparent; color: var(--hwv-text);
  font: inherit; cursor: pointer;
  height: 26px; padding: 0 var(--hwv-space-2);
  min-width: 26px;
}
.hwv-zoom-pct {
  min-width: 52px; font-size: 11px; color: var(--hwv-dim);
  font-variant-numeric: tabular-nums;
  border-left: 1px solid var(--hwv-line);
  border-right: 1px solid var(--hwv-line);
}
.hwv-zoom-btn:hover, .hwv-zoom-pct:hover {
  background: var(--hwv-hover); color: var(--hwv-text);
}
.hwv-zoom-btn:focus-visible, .hwv-zoom-pct:focus-visible {
  outline: none; box-shadow: var(--hwv-focus);
}
/* 画布绝对定位铺满父级：不依赖父级 height 是"确定值"，
   否则 flex 列里的百分比高度在部分浏览器算成 0（空白画布）。 */
.hwv-canvas {
  position: absolute; inset: 0;
  display: block; width: 100%; height: 100%;
  touch-action: none;
}
/* 空态：板子在、但上面没东西时**明说**，不要让人以为是渲染挂了 */
.hwv-canvas-empty {
  position: absolute; inset: 0;
  z-index: var(--hwv-z-actionbar);     /* 必须压过 3D 的 canvas（它最后挂进 DOM）*/
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: var(--hwv-space-2); pointer-events: none;
  color: var(--hwv-dim); font-size: 12px; text-align: center;
}
.hwv-canvas-empty strong { color: var(--hwv-text); font-size: 13px; font-weight: 600; }
.hwv-canvas-empty span { opacity: 0.8; }

/* 把手：4px 命中区 + hover 变粗的视觉线（Velxio 用 5px 行把手，同族做法） */
.hwv-handle {
  flex: 0 0 5px; position: relative; z-index: var(--hwv-z-sticky);
  background: var(--hwv-line);
  transition: background var(--hwv-dur-fast) var(--hwv-ease-out);
}
.hwv-handle:hover { background: var(--hwv-accent); }
.hwv-handle.vertical { cursor: col-resize; }
.hwv-handle.horizontal { cursor: row-resize; border-top: 1px solid var(--hwv-line); border-bottom: 1px solid var(--hwv-line); }

/* 3D pane 右上角视图工具（爆炸/剖面/网格）：浮层归 actionbar 级 */
/** 剖面滑杆：和按钮同一条工具栏，只在剖面开启时出现。 */
.hwv-section-slider {
  display: inline-flex;
  align-items: center;
  gap: var(--hwv-space-2);
  padding: 0 var(--hwv-space-2);
  font-size: var(--hwv-fs-xs);
  color: var(--hwv-m-fg-muted);
}
.hwv-section-slider input[type='range'] {
  width: 96px;
  accent-color: var(--hwv-m-accent);
}
.hwv-pane-tools {
  position: absolute; right: var(--hwv-space-3); top: var(--hwv-space-3);
  display: flex; gap: var(--hwv-space-2); z-index: var(--hwv-z-actionbar);
}

/* ── 5. 检查器速览列（宽度由把手拖动 + localStorage；内联 style 给宽）─ */
.hwv-inspector {
  flex: 0 0 auto;
  background: var(--hwv-panel); overflow: auto; padding: var(--hwv-space-4);
}
.hwv-inspector h4 { margin: var(--hwv-space-6) 0 var(--hwv-space-4); font-size: 12px; color: var(--hwv-dim); font-weight: 600; }
.hwv-inspector > h4:first-child { margin-top: var(--hwv-space-1); }
/* 检查器里的表单小件 */
.hwv-form { display: flex; flex-direction: column; gap: var(--hwv-space-3); }
.hwv-field-label { font-size: var(--hwv-fs-caption); color: var(--hwv-dim); }
.hwv-row-3 { display: flex; gap: var(--hwv-space-2); }
.hwv-row-3 > * { flex: 1 1 0; min-width: 0; }
.hwv-btn-row { display: flex; flex-wrap: wrap; gap: var(--hwv-space-3); margin-top: var(--hwv-space-4); }
.hwv-note { line-height: 1.8; }
.hwv-kv { display: grid; grid-template-columns: 78px 1fr; gap: 3px var(--hwv-space-4); font-size: 12px; }
.hwv-kv dt { color: var(--hwv-dim); }
/* 数据完整性提示：说明这份数据**缺什么**（丢了几块板、哪些是排布不是布局）。
   用一句一行、带左侧色条，和普通段落区分开 —— 它是限制，不是说明。 */
.hwv-provenance {
  list-style: none; margin: 0; padding: 0;
  display: flex; flex-direction: column; gap: var(--hwv-space-3);
  font-size: 11px; line-height: 1.7; color: var(--hwv-dim);
}
.hwv-provenance li {
  border-left: 2px solid var(--hwv-warn);
  padding: var(--hwv-space-2) var(--hwv-space-2) var(--hwv-space-2) var(--hwv-space-4);
  background: var(--hwv-panel-2);
}
.hwv-kv dd { margin: 0; word-break: break-word; }
.hwv-src { font-size: 11px; }
.hwv-src.datasheet, .hwv-src.measured { color: var(--hwv-ok); }
.hwv-src.derived { color: var(--hwv-accent); }
.hwv-src.estimated { color: var(--hwv-est); }

.hwv-inline { display: flex; gap: var(--hwv-space-3); align-items: center; }
.hwv-inline-stretch { flex: 1 1 0; min-width: 0; }
/* 面板里的通用位：行首的摘要条、可折叠块、行尾的定位 chip */
.hwv-summary { display: flex; gap: var(--hwv-space-2); align-items: center; flex-wrap: wrap; color: var(--hwv-dim); margin-bottom: var(--hwv-space-3); }
.hwv-block { margin-top: var(--hwv-space-5); }
.hwv-block-tight { margin-top: var(--hwv-space-4); }
.hwv-refs { flex: 0 0 auto; }

.hwv-input {
  width: 100%; box-sizing: border-box; background: var(--hwv-panel-2);
  color: var(--hwv-text); border: 1px solid var(--hwv-line);
  border-radius: var(--hwv-radius-xs);
  padding: 3px var(--hwv-space-3); font-size: 12px;
  transition: var(--hwv-transition-color);
}
.hwv-input:focus { border-color: var(--hwv-accent); }

/* ── 6. 底部面板（高度由把手拖动 + localStorage）────────────────────── */
.hwv-bottom {
  flex: 0 0 auto; border-top: 1px solid var(--hwv-line);
  background: var(--hwv-panel);
  display: flex; flex-direction: column; min-height: 0;
}
.hwv-tabs { display: flex; gap: var(--hwv-space-1); padding: var(--hwv-space-2) var(--hwv-space-4) 0; flex-wrap: wrap; row-gap: var(--hwv-space-1); }
.hwv-tab {
  padding: var(--hwv-space-2) var(--hwv-space-4);
  border: 1px solid transparent; border-bottom: none;
  border-radius: var(--hwv-radius-sm) var(--hwv-radius-sm) 0 0;
  background: transparent; color: var(--hwv-dim);
  cursor: pointer; font-size: 12px;
  transition: var(--hwv-transition-color);
}
.hwv-tab:hover { color: var(--hwv-text); }
.hwv-tab.active { background: var(--hwv-panel-2); color: var(--hwv-text); border-color: var(--hwv-line); }
.hwv-tab .count { margin-left: 5px; font-size: var(--hwv-fs-caption); opacity: .8; }
.hwv-tab .count.err { color: var(--hwv-err); }
.hwv-tab .count.warn { color: var(--hwv-warn); }

/* 分组页签上的**点**（不是数字）：一个组里有 5 个分区，"3" 究竟是 3 个错误
   还是 3 个未决？聚合数字必然含混，所以分组只报"这组有问题"，数字留给分区。 */
.hwv-tab-dot {
  display: inline-block; width: 5px; height: 5px; margin-left: 6px;
  border-radius: 50%; vertical-align: middle;
}
.hwv-tab-dot.err { background: var(--hwv-err); }
.hwv-tab-dot.warn { background: var(--hwv-warn); }

/* 组内分区（segmented）。比页签更轻：它不改"我在哪个组"，只切"看哪一段"。 */
.hwv-subtabs {
  display: flex; gap: var(--hwv-space-1); flex-wrap: wrap;
  padding: var(--hwv-space-1) var(--hwv-space-4);
  border-bottom: 1px solid var(--hwv-line);
  flex: 0 0 auto;
}
.hwv-subtab {
  padding: 3px var(--hwv-space-3);
  border: 1px solid transparent; border-radius: var(--hwv-radius-sm);
  background: transparent; color: var(--hwv-dim);
  cursor: pointer; font-size: 11px;
  transition: var(--hwv-transition-color);
}
.hwv-subtab:hover { color: var(--hwv-text); background: var(--hwv-hover); }
.hwv-subtab.active {
  color: var(--hwv-text); background: var(--hwv-panel-2);
  border-color: var(--hwv-line);
}
.hwv-subtab .count { margin-left: 4px; font-size: var(--hwv-fs-caption); opacity: .85; }
.hwv-subtab .count.err { color: var(--hwv-err); }
.hwv-subtab .count.warn { color: var(--hwv-warn); }

/* AI 抽屉：从右侧滑入的浮层，不是压在工具栏里的一个按钮。
   动效走 --hwv-ease-out；prefers-reduced-motion 时由根令牌把时长降到 1ms
   （见文件顶部）。 */
/* AI 入口按钮：和普通按钮同高，但**语义不同**（打开抽屉而非执行动作），
   用虚线边框 + 强调色文字提示它不是主操作之一。 */
.hwv-ai-toggle { border-style: dashed; color: var(--hwv-est); }
.hwv-ai-toggle.on { border-style: solid; background: var(--hwv-panel-2); }
.hwv-drawer-scrim {
  position: fixed; inset: 0; z-index: var(--hwv-z-scrim, 40);
  background: rgba(0, 0, 0, 0.28);
}
.hwv-drawer {
  position: fixed; top: 0; right: 0; bottom: 0;
  width: min(420px, 92vw);
  z-index: var(--hwv-z-drawer, 41);
  display: flex; flex-direction: column; gap: var(--hwv-space-3);
  padding: var(--hwv-space-5);
  background: var(--hwv-panel);
  border-left: 1px solid var(--hwv-line);
  overflow: auto;
  animation: hwv-drawer-in var(--hwv-dur-base) var(--hwv-ease-out);
}
@keyframes hwv-drawer-in {
  from { transform: translateX(12px); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}

.hwv-body { overflow: auto; padding: var(--hwv-space-4) var(--hwv-space-5); flex: 1 1 auto; min-height: 0; }
.hwv-row {
  display: flex; gap: var(--hwv-gap-row); padding: 3px 0;
  border-bottom: 1px dashed color-mix(in srgb, var(--hwv-line) 60%, transparent);
  align-items: baseline;
}
.hwv-row:last-child { border-bottom: none; }
.hwv-row .mark { width: 14px; flex: 0 0 14px; text-align: center; }
.hwv-row.err .mark { color: var(--hwv-err); }
.hwv-row.warn .mark { color: var(--hwv-warn); }
.hwv-row.ok .mark { color: var(--hwv-ok); }
.hwv-row.info .mark { color: var(--hwv-dim); }
.hwv-row .msg { flex: 1 1 auto; }
/* refs 是行内的可点引用标签。它不能带 .hwv-row：那会让它自己变成
   flex 容器并继承虚线下边框（此前 class 写成 "hwv-row refs" 是个真缺陷）。 */
.hwv-refs {
  color: var(--hwv-accent); cursor: pointer; white-space: nowrap;
  flex: 0 0 auto; font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11px;
}
.hwv-refs:hover { text-decoration: underline; }
.hwv-dim { color: var(--hwv-dim); }
.hwv-mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 11px; }

.hwv-tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.hwv-tbl th { text-align: left; color: var(--hwv-dim); font-weight: 500; }
.hwv-tbl td { border-top: 1px dashed color-mix(in srgb, var(--hwv-line) 60%, transparent); padding: 3px var(--hwv-space-3) 3px 0; }

/* ── 7. 横幅 / 空态 / 图例 / 进度 ───────────────────────────────────── */
.hwv-banner {
  padding: var(--hwv-space-3) var(--hwv-space-4); border-bottom: 1px solid var(--hwv-line);
  background: color-mix(in srgb, var(--hwv-err) 14%, var(--hwv-panel));
  color: var(--hwv-text); display: flex; gap: var(--hwv-gap-row); align-items: center;
}
.hwv-banner.info { background: color-mix(in srgb, var(--hwv-accent) 12%, var(--hwv-panel)); }


/* 空态与设置块共用的滚动容器（原来写在内联 style 里，内联样式逃过
   check_dead_css 的扫描，也逃过主题的重映射——改为类） */
.hwv-main-scroll { display: block; overflow: auto; padding: var(--hwv-space-6); }

/* 空态抬头：标题 + 一句引导。工作区路径**不在这里重复**——工具栏右上角
   那条可点路径已经说明了"工程会建在哪"，两处都写就是这次要修的那种重复。 */
.hwv-empty-head {
  display: flex; align-items: baseline; gap: var(--hwv-space-5);
  flex-wrap: wrap; max-width: 720px; margin: 0 auto var(--hwv-space-6);
}
.hwv-empty-head strong { display: block; font-size: 15px; color: var(--hwv-text); }
.hwv-empty-head > div { color: var(--hwv-dim); line-height: 1.8; }

/* 开始卡：一张卡两个页签（新建预设 / 从文件导入）。
   两个动作互斥，所以用页签而不是两张并列卡——并列会诱导"都要填"。 */
.hwv-start {
  max-width: 720px; margin: 0 auto;
  border: 1px solid var(--hwv-line); border-radius: var(--hwv-radius-md);
  background: var(--hwv-panel); overflow: hidden;
}
.hwv-start-tabs {
  display: flex; gap: var(--hwv-space-1);
  border-bottom: 1px solid var(--hwv-line);
  background: var(--hwv-panel-2); padding: var(--hwv-space-2) var(--hwv-space-3) 0;
}
.hwv-start-tab {
  background: transparent; border: 1px solid transparent; border-bottom: none;
  border-radius: var(--hwv-radius-sm) var(--hwv-radius-sm) 0 0;
  color: var(--hwv-dim); cursor: pointer; font-family: inherit;
  padding: var(--hwv-space-2) var(--hwv-space-5); font-size: 12px;
  transition: var(--hwv-transition-color);
}
.hwv-start-tab:hover { color: var(--hwv-text); }
.hwv-start-tab.on {
  background: var(--hwv-panel); color: var(--hwv-text);
  border-color: var(--hwv-line); margin-bottom: -1px;
}
.hwv-start-body { padding: var(--hwv-space-5); display: flex; flex-direction: column; gap: var(--hwv-space-4); }

/* 表单网格：字段带标签在上，而不是把提示塞进 placeholder ——
   输入框里的灰字一敲键盘就没了，标签不会 */
.hwv-form-grid { display: flex; gap: var(--hwv-space-4); flex-wrap: wrap; }
.hwv-field { display: flex; flex-direction: column; gap: var(--hwv-space-2); flex: 1 1 160px; min-width: 0; }
.hwv-field-label { font-size: var(--hwv-fs-caption); color: var(--hwv-dim); }
.hwv-select-wide { max-width: none; width: 100%; }
.hwv-start-foot { display: flex; align-items: center; gap: var(--hwv-space-4); flex-wrap: wrap; }
.hwv-hint { font-size: 11px; color: var(--hwv-dim); line-height: 1.7; }

/* ── 板级汇总（P1）────────────────────────────────────────────────────── */
.hwv-boardstrip {
  display: flex; align-items: center; gap: var(--hwv-space-2);
  padding: var(--hwv-space-2) var(--hwv-space-4);
  border-bottom: 1px solid var(--hwv-line);
  overflow-x: auto; white-space: nowrap;
}
.hwv-boardstrip-label {
  font-size: var(--hwv-fs-caption); color: var(--hwv-dim);
  letter-spacing: .04em;
}
.hwv-board-chip {
  display: inline-flex; align-items: center; gap: var(--hwv-space-2);
  padding: 2px 10px; border: 1px solid var(--hwv-line);
  border-radius: var(--hwv-radius-pill);
  background: var(--hwv-surface); color: var(--hwv-text);
  font-size: var(--hwv-fs-caption); cursor: pointer;
}
.hwv-board-chip:hover { border-color: var(--hwv-accent); }
.hwv-board-chip.err { border-color: color-mix(in srgb, var(--hwv-err) 55%, transparent); }
.hwv-board-chip.warn { border-color: color-mix(in srgb, var(--hwv-warn) 50%, transparent); }
.hwv-board-parts { color: var(--hwv-dim); }
.hwv-board-chip .n.err { color: var(--hwv-err); font-weight: 600; }
.hwv-board-chip .n.warn { color: var(--hwv-warn); font-weight: 600; }
.hwv-boardstrip-counts {
  margin-left: auto; display: inline-flex; gap: var(--hwv-space-5);
  font-size: var(--hwv-fs-caption); color: var(--hwv-dim);
}
.hwv-boardstrip-counts b { color: var(--hwv-text); font-weight: 600; }
.hwv-boardstrip-counts .n.err b, .hwv-boardstrip-counts .n.err { color: var(--hwv-err); }
.hwv-boardstrip-counts .n.warn b, .hwv-boardstrip-counts .n.warn { color: var(--hwv-warn); }
`
