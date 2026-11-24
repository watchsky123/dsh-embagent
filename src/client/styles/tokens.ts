/**
 * 样式分片：刻度令牌 + 语义层（浅色）/ 画布调色板。
 *
 * 从原 `styles.ts` 按注释段原样切出；拼接顺序见 `styles.ts`。
 */

export const TOKENS = `
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
  --hwv-m-body-metal: #9aa3af;   /* 电机外转子/机臂金属 */
  --hwv-m-body-prop: #3a4150;    /* 桨（碳纤深色，和金属区分） */
  --hwv-m-body-shell: #fde8c8;   /* 外壳填充（低不透明度画） */
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
  --hwv-p-body-metal: var(--hwv-m-body-metal);
  --hwv-p-body-prop: var(--hwv-m-body-prop);
  --hwv-p-body-shell: var(--hwv-m-body-shell);
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
`
