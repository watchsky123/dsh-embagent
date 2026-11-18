/**
 * 工作台的样式，一次注入。
 *
 * 分布形式学自 Velxio（frontend/src/tokens/* 的纪律，不抄它的值）：
 *
 * 1. **三层颜色**：primitive 层直接坐在宿主 --dsw-alias-* 令牌上（不重
 *    复发明灰阶），semantic 层（--hwv-*）供组件使用，切主题只重映射
 *    semantic 层。宿主 ui-theme 把解析后的主题写到 body[data-ds-dark-theme]
 *    （preference=system 时跟随 prefers-color-scheme），chrome 引用 alias
 *    令牌即自动翻转。
 * 2. **画布调色板按深度索引**（--hwv-p-*，仿 --wb-0…13）：canvas 与
 *    three.js 不跟 CSS，getComputedStyle 对自定义属性返回**声明原文**，
 *    var() 链不会解析成颜色——所以浅/深各一套**字面量**，值与宿主两套
 *    调色板对齐，裸环境（无宿主样式）也能解析。翻转监听在 theme.ts。
 * 3. **刻度令牌**：spacing 4pt 基数、radius 按角色、z-index 显式分级
 *    （禁魔法数）、motion 三时长三缓动 + reduced-motion 归零、焦点环
 *    全局 :focus-visible。组件里新代码不许写裸 px/hex/zIndex。
 *
 * @module dsh-hw-velxio/client/styles
 */

const CSS = `
/* ── 1. 刻度令牌（与主题无关，只声明一次）────────────────────────────── */
.hwv-root {
  --hwv-space-1: 2px;  --hwv-space-2: 4px;  --hwv-space-3: 6px;
  --hwv-space-4: 8px;  --hwv-space-5: 12px; --hwv-space-6: 16px;
  --hwv-space-7: 20px; --hwv-space-8: 24px;

  /* 字阶（Linear 的紧凑阶梯）：只有三档 + 一个标题档。
     没有 10px —— 低于 11px 在 1x DPI 上就要凑近看，而"元信息"不是
     "可以看不清的信息"。 */
  --hwv-fs-caption: 11px;   /* 计数、提示、元信息 */
  --hwv-fs-body: 12px;      /* 默认正文与按钮 */
  --hwv-fs-title: 13px;     /* 面板标题（配 600 字重） */
  --hwv-fs-heading: 15px;   /* 区块标题 */

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

  /* 画布调色板 —— **分两层**：材质层（--hwv-m-*）+ 引用层（--hwv-p-*）。
   *
   * 为什么分层：同一个概念在 2D 与 3D 各有一个名字（board-fill / board3d），
   * 以前它们各自写死一个 hex，改一个忘一个就会出现"2D 浅绿、3D 深绿"同一工程
   * 两个板色。现在材质只有一处取值，2D/3D 都引用它。
   *
   * **为什么不用 color-mix()/oklab() 派生**：three.js 的 Color 只认
   * hex/rgb/hsl，遇到 oklab() / color-mix() 会静默退回黑色——画布不报错，
   * 只是颜色错了。所以"跟随系统"靠下面深色块**整层替换材质值**实现，
   * 而不是靠 CSS 色彩函数。
   *
   * chrome 语义色（标签/错误/警告/线/强调）**引用 --hwv-* 语义令牌**，
   * 那些令牌本身坐在宿主 --dsw-alias-* 上，因此随系统主题自动翻转；
   * 材质色（板/焊盘/铜线/外壳）是"物理颜色"，深浅两套各自定义。 */

  /* ── 材质层：唯一取值处 ── */
  --hwv-m-board: #e9f2ec;
  --hwv-m-board-edge: #7fae8f;
  --hwv-m-board-3d: #cfe9d6;
  --hwv-m-board-3d-edge: #5f8f70;
  --hwv-m-hole-fill: #eef1f4;
  --hwv-m-hole-3d: #43454a;
  --hwv-m-pad: #c8a951;          /* 焊盘（金属）：比元件本体醒目 */
  --hwv-m-pad-stroke: #8a6d1f;
  --hwv-m-pad-3d: #d4af37;
  --hwv-m-wire: #1971c2;         /* 3D 导线 */
  --hwv-m-trace: #c45c26;        /* 铜线（已布线走线） */
  --hwv-m-shell: #f59e0b;        /* 外壳 */
  --hwv-m-edge: #81858c;
  --hwv-m-grid3d-major: #cfd3d6;
  --hwv-m-grid3d-minor: #e1e5ee;
  --hwv-m-fill-datasheet: #3b82f6;   /* 尺寸来源四态 */
  --hwv-m-fill-measured: #22c55e;
  --hwv-m-fill-derived: #7aaaff;
  --hwv-m-fill-estimated: #a78bfa;
  --hwv-m-fill-error: #f25a5a;       /* 判定着色 */
  --hwv-m-fill-warn: #f7ad31;
  --hwv-m-wire-l: 45%;               /* 2D 导线 HSL 亮度：浅底要深才看得清 */

  /* ── 引用层：2D/3D 各自的命名都指到这里 ── */
  --hwv-p-canvas-bg: var(--hwv-panel-2);   /* 床 = 面板底 */
  --hwv-p-scene-bg: var(--hwv-panel-2);    /* 3D 床 */
  --hwv-p-grid: var(--hwv-line);
  --hwv-p-plain-stroke: var(--hwv-line);
  --hwv-p-hole-stroke: var(--hwv-line);
  --hwv-p-label: var(--hwv-text);
  /* 唯一强调色：紫（--hwv-est）。选择环与"估算"标记共用它——
     两者靠几何区分（环 vs 角标），不靠色相区分，所以不算第二个强调色。 */
  --hwv-p-est-mark: var(--hwv-est);
  --hwv-p-sel: var(--hwv-est);
  --hwv-p-err: var(--hwv-err);
  --hwv-p-warn: var(--hwv-warn);
  --hwv-p-board-fill: var(--hwv-m-board);
  --hwv-p-board-stroke: var(--hwv-m-board-edge);
  --hwv-p-board3d: var(--hwv-m-board-3d);
  --hwv-p-board3d-edge: var(--hwv-m-board-3d-edge);
  --hwv-p-hole-fill: var(--hwv-m-hole-fill);
  --hwv-p-hole3d: var(--hwv-m-hole-3d);
  --hwv-p-pad-fill: var(--hwv-m-pad);
  --hwv-p-pad-stroke: var(--hwv-m-pad-stroke);
  --hwv-p-pad3d: var(--hwv-m-pad-3d);
  --hwv-p-wire3d: var(--hwv-m-wire);
  --hwv-p-trace: var(--hwv-m-trace);
  --hwv-p-shell3d: var(--hwv-m-shell);
  --hwv-p-edge: var(--hwv-m-edge);
  --hwv-p-grid3d-major: var(--hwv-m-grid3d-major);
  --hwv-p-grid3d-minor: var(--hwv-m-grid3d-minor);
  --hwv-p-fill-datasheet: var(--hwv-m-fill-datasheet);
  --hwv-p-fill-measured: var(--hwv-m-fill-measured);
  --hwv-p-fill-derived: var(--hwv-m-fill-derived);
  --hwv-p-fill-estimated: var(--hwv-m-fill-estimated);
  --hwv-p-fill-error: var(--hwv-m-fill-error);
  --hwv-p-fill-warn: var(--hwv-m-fill-warn);
  --hwv-p-wire-l: var(--hwv-m-wire-l);

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

/* ── 8. 深色主题：宿主 ui-theme 在 body 写 data-ds-dark-theme。
 *
 * 这里**只覆盖材质层**（--hwv-m-*）：引用层（--hwv-p-*）是 var()，
 * 自动跟着材质走。chrome 语义色本来就坐在 alias 令牌上，不用管。
 *
 * 以前这里逐个重写 28 个 --hwv-p-*，和浅色块是两份平行清单——加一个颜色
 * 要改两处，漏一次就出现"浅色对了、深色没跟上"。现在只有材质这一处。 */
body[data-ds-dark-theme] .hwv-root {
  --hwv-m-board: #1d3a2a;
  --hwv-m-board-edge: #4f8f66;
  --hwv-m-board-3d: #24553a;
  --hwv-m-board-3d-edge: #5a9f74;
  --hwv-m-hole-fill: #0c0e12;
  --hwv-m-hole-3d: #6e7683;
  --hwv-m-pad: #b8963f;
  --hwv-m-pad-stroke: #e0c268;
  --hwv-m-pad-3d: #d4af37;
  --hwv-m-wire: #4c9aff;
  --hwv-m-trace: #e07b4a;
  --hwv-m-shell: #f59e0b;
  --hwv-m-edge: #9aa3af;
  --hwv-m-grid3d-major: #43454a;
  --hwv-m-grid3d-minor: #2c2c2e;
  --hwv-m-fill-datasheet: #2f6fb5;
  --hwv-m-fill-measured: #2f9e5f;
  --hwv-m-fill-derived: #4d6ea6;
  --hwv-m-fill-estimated: #6d5a9e;
  --hwv-m-fill-error: #8f2f2f;
  --hwv-m-fill-warn: #8f6f2f;
  --hwv-m-wire-l: 60%;
}`


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
