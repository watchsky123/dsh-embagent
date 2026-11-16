/**
 * 工作台的样式，一次注入。
 *
 * 分布形式学自 Velxio（`frontend/src/tokens/*` 的纪律，不抄它的值）：
 *
 * 1. **三层颜色**：primitive 层直接坐在宿主 `--dsw-alias-*` 令牌上（不重
 *    复发明灰阶），semantic 层（`--hwv-*`）供组件使用，切主题只重映射
 *    semantic 层。宿主 `ui-theme` 把解析后的主题写到 `body[data-ds-dark-theme]`
 *    （preference=system 时跟随 `prefers-color-scheme`），chrome 引用 alias
 *    令牌即自动翻转。
 * 2. **画布调色板按深度索引**（`--hwv-p-*`，仿 `--wb-0…13`）：canvas 与
 *    three.js 不跟 CSS，`getComputedStyle` 对自定义属性返回**声明原文**，
 *    `var()` 链不会解析成颜色——所以浅/深各一套**字面量**，值与宿主两套
 *    调色板对齐，裸环境（无宿主样式）也能解析。翻转监听在 `theme.ts`。
 * 3. **刻度令牌**：spacing 4pt 基数、radius 按角色、z-index 显式分级
 *    （禁魔法数）、motion 三时长三缓动 + reduced-motion 归零、焦点环
 *    全局 `:focus-visible`。组件里新代码不许写裸 px/hex/zIndex。
 *
 * @module dsh-hw-velxio/client/styles
 */

const CSS = `
/* ── 1. 刻度令牌（与主题无关，只声明一次）────────────────────────────── */
.hwv-root {
  --hwv-space-1: 2px;  --hwv-space-2: 4px;  --hwv-space-3: 6px;
  --hwv-space-4: 8px;  --hwv-space-5: 12px; --hwv-space-6: 16px;
  --hwv-space-7: 20px; --hwv-space-8: 24px;

  --hwv-radius-xs: 4px; --hwv-radius-sm: 6px; --hwv-radius-md: 10px;
  --hwv-radius-pill: 9999px;

  /* z 分级：画布内容 < 就地操作条 < 浮层 < 模态。禁魔法数。 */
  --hwv-z-canvas: 0;
  --hwv-z-actionbar: 20;
  --hwv-z-sticky: 30;
  --hwv-z-popover: 50;
  --hwv-z-modal: 60;

  --hwv-dur-fast: 120ms; --hwv-dur-base: 200ms; --hwv-dur-slow: 320ms;
  --hwv-ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --hwv-ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
  --hwv-transition-color:
    color var(--hwv-dur-base) var(--hwv-ease-out),
    background-color var(--hwv-dur-base) var(--hwv-ease-out),
    border-color var(--hwv-dur-base) var(--hwv-ease-out);

  --hwv-focus: 0 0 0 2px var(--hwv-accent);

  --hwv-gap-row: var(--hwv-space-4);
}

@media (prefers-reduced-motion: reduce) {
  .hwv-root { --hwv-dur-fast: 1ms; --hwv-dur-base: 1ms; --hwv-dur-slow: 1ms; }
}

/* ── 2. 语义层 + 画布调色板（浅色一套；深色覆盖在文件末尾）──────────── */
.hwv-root {
  /* chrome：引用宿主 alias 令牌，自带兜底（裸环境不白屏）。 */
  --hwv-bg: var(--dsw-alias-bg-base, #ffffff);
  --hwv-panel: var(--dsw-alias-bg-layer-1, #f7f8fa);
  --hwv-panel-2: var(--dsw-alias-bg-document-preview, #eef1f4);
  --hwv-line: var(--dsw-alias-border-l2, #0000001a);
  --hwv-text: var(--dsw-alias-label-primary, #0f1115);
  --hwv-dim: var(--dsw-alias-label-secondary, #61666b);
  --hwv-accent: var(--dsw-alias-link, #4176e6);
  --hwv-ok: var(--dsw-alias-state-success-primary, #22c55e);
  --hwv-warn: var(--dsw-alias-state-warn-primary, #f59e0b);
  --hwv-err: var(--dsw-alias-state-error-primary, #ef4444);
  --hwv-est: var(--dsw-static-violet-600, #7c3aed);
  --hwv-hover: var(--dsw-alias-interactive-bg-hover, #2631480f);

  /* 画布调色板按深度索引：地面 → 板 → 面板 → 描边 → 文字 → 语义色。
     three.js 专用（3d 后缀）必须是**无 alpha 纯色**（Color 不解析 alpha）。 */
  --hwv-p-canvas-bg: #f7f8fa;        /* 床 */
  --hwv-p-grid: rgba(15, 17, 21, 0.06);
  --hwv-p-board-fill: #e9f2ec;       /* 板上 */
  --hwv-p-board-stroke: #7fae8f;
  --hwv-p-hole-fill: #eef1f4;
  --hwv-p-hole-stroke: rgba(15, 17, 21, 0.35);
  --hwv-p-plain-stroke: rgba(15, 17, 21, 0.30);
  --hwv-p-label: #1b1b1c;            /* 前景文字 */
  --hwv-p-est-mark: #7c3aed;
  --hwv-p-sel: #1971c2;              /* 选择/强调 */
  --hwv-p-err: #ec1313;
  --hwv-p-warn: #dd8629;
  --hwv-p-fill-datasheet: #3b82f6;   /* 尺寸来源四态 */
  --hwv-p-fill-measured: #22c55e;
  --hwv-p-fill-derived: #7aaaff;
  --hwv-p-fill-estimated: #a78bfa;
  --hwv-p-fill-error: #f25a5a;       /* 判定着色 */
  --hwv-p-fill-warn: #f7ad31;
  --hwv-p-scene-bg: #eef1f4;         /* 3D：床/板/线/壳 */
  --hwv-p-board3d: #cfe9d6;
  --hwv-p-board3d-edge: #5f8f70;
  --hwv-p-hole3d: #43454a;
  --hwv-p-edge: #81858c;
  --hwv-p-wire3d: #1971c2;
  --hwv-p-shell3d: #f59e0b;
  --hwv-p-pad-fill: #c8a951;       /* 焊盘（金属）：比元件本体醒目 */
  --hwv-p-pad-stroke: #8a6d1f;
  --hwv-p-pad3d: #d4af37;
  --hwv-p-trace: #c45c26;          /* 铜线（已布线走线） */
  --hwv-p-grid3d-major: #cfd3d6;
  --hwv-p-grid3d-minor: #e1e5ee;
  --hwv-p-wire-l: 45%;               /* 2D 导线 HSL 亮度：浅底要深才看得清 */

  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--hwv-bg);
  color: var(--hwv-text);
  font-size: 12px;
  overflow: hidden;
}

/* 全局焦点环（仿 tokens/index.css：所有交互元素，组件可覆盖） */
.hwv-root button:focus-visible,
.hwv-root select:focus-visible,
.hwv-root input:focus-visible,
.hwv-root [tabindex]:focus-visible {
  box-shadow: var(--hwv-focus);
  outline: none;
}

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
.hwv-canvas-wrap { position: relative; flex: 1 1 auto; min-height: 0; z-index: var(--hwv-z-canvas); }
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
.hwv-field-label { font-size: 10px; color: var(--hwv-dim); }
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
.hwv-tab .count { margin-left: 5px; font-size: 10px; opacity: .8; }
.hwv-tab .count.err { color: var(--hwv-err); }
.hwv-tab .count.warn { color: var(--hwv-warn); }

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
.hwv-field-label { font-size: 10px; color: var(--hwv-dim); }
.hwv-select-wide { max-width: none; width: 100%; }
.hwv-start-foot { display: flex; align-items: center; gap: var(--hwv-space-4); flex-wrap: wrap; }
.hwv-hint { font-size: 11px; color: var(--hwv-dim); line-height: 1.7; }

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

/* ── 8. 深色主题：宿主 ui-theme 在 body 写 data-ds-dark-theme。chrome 不动
      （alias 令牌已随主题翻），只翻命令式调色板（按深度索引值逐一对应）。 */
body[data-ds-dark-theme] .hwv-root {
  --hwv-p-canvas-bg: #101216;
  --hwv-p-grid: rgba(255, 255, 255, 0.05);
  --hwv-p-board-fill: #1d3a2a;
  --hwv-p-board-stroke: #4f8f66;
  --hwv-p-hole-fill: #0c0e12;
  --hwv-p-hole-stroke: rgba(255, 255, 255, 0.4);
  --hwv-p-plain-stroke: rgba(255, 255, 255, 0.3);
  --hwv-p-label: #e6edf3;
  --hwv-p-est-mark: #c8a6ff;
  --hwv-p-sel: #74c0fc;
  --hwv-p-err: #f85149;
  --hwv-p-warn: #d29922;
  --hwv-p-fill-datasheet: #2f6fb5;
  --hwv-p-fill-measured: #2f9e5f;
  --hwv-p-fill-derived: #4d6ea6;
  --hwv-p-fill-estimated: #6d5a9e;
  --hwv-p-fill-error: #8f2f2f;
  --hwv-p-fill-warn: #8f6f2f;
  --hwv-p-scene-bg: #15171c;
  --hwv-p-board3d: #24553a;
  --hwv-p-board3d-edge: #5a9f74;
  --hwv-p-hole3d: #6e7683;
  --hwv-p-edge: #9aa3af;
  --hwv-p-wire3d: #4c9aff;
  --hwv-p-shell3d: #f59e0b;
  --hwv-p-pad-fill: #b8963f;
  --hwv-p-pad-stroke: #e0c268;
  --hwv-p-pad3d: #d4af37;
  --hwv-p-trace: #e07b4a;
  --hwv-p-grid3d-major: #43454a;
  --hwv-p-grid3d-minor: #2c2c2e;
  --hwv-p-wire-l: 60%;
}
`

let injected = false

/** 注入一次样式表（多次调用无副作用）。 */
export function ensureStyles(): void {
  if (injected) return
  injected = true
  const doc = globalThis.document
  if (doc === undefined) return
  const style = doc.createElement('style')
  style.setAttribute('data-hw-velxio', '')
  style.textContent = CSS
  doc.head.appendChild(style)
}
