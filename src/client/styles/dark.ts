/**
 * 样式分片：深色主题覆盖（`body[data-ds-dark-theme]`）。
 *
 * 从原 `styles.ts` 按注释段原样切出；拼接顺序见 `styles.ts`。
 */

export const DARK = `
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
`
