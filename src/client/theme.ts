/**
 * 画布 / WebGL 的主题取色。
 *
 * 宿主的主题信号在 DOM 上：`body[data-ds-dark-theme]` + `--dsw-alias-*` 令牌，
 * 且 preference=system 时由 `ui-theme` 跟随系统 `prefers-color-scheme` 翻转。
 * CSS 面板会自动跟着变量走；**canvas 与 three.js 不会**——它们是命令式绘制，
 * 必须在主题翻转时重读一次颜色。这就是本模块存在的原因。
 *
 * 颜色的"值"全部在 `styles.ts` 的 `--hwv-*` 变量里定义（浅色一套 + 深色覆盖
 * 一套），这里只做读取与回退：拿不到宿主样式表时用深色兜底集，行为与旧版
 * 硬编码一致，插件在裸环境里也不至于不可读。
 *
 * @module dsh-hw-velxio/client/theme
 */

import { useEffect, useState } from 'react'

/** 语义键 → CSS 自定义属性名。值必须是可解析颜色（见 styles.ts 顶注）。 */
const PALETTE_VARS = {
  canvasBg: '--hwv-p-canvas-bg',
  grid: '--hwv-p-grid',
  boardFill: '--hwv-p-board-fill',
  boardStroke: '--hwv-p-board-stroke',
  holeFill: '--hwv-p-hole-fill',
  holeStroke: '--hwv-p-hole-stroke',
  label: '--hwv-p-label',
  estMark: '--hwv-p-est-mark',
  plainStroke: '--hwv-p-plain-stroke',
  selStroke: '--hwv-p-sel',
  errStroke: '--hwv-p-err',
  warnStroke: '--hwv-p-warn',
  fillDatasheet: '--hwv-p-fill-datasheet',
  fillMeasured: '--hwv-p-fill-measured',
  fillDerived: '--hwv-p-fill-derived',
  fillEstimated: '--hwv-p-fill-estimated',
  fillError: '--hwv-p-fill-error',
  fillWarn: '--hwv-p-fill-warn',
  board3d: '--hwv-p-board3d',
  board3dEdge: '--hwv-p-board3d-edge',
  hole3d: '--hwv-p-hole3d',
  edge3d: '--hwv-p-edge',
  grid3dMajor: '--hwv-p-grid3d-major',
  grid3dMinor: '--hwv-p-grid3d-minor',
  shell3d: '--hwv-p-shell3d',
  wire3d: '--hwv-p-wire3d',
  sceneBg: '--hwv-p-scene-bg',
  padFill: '--hwv-p-pad-fill',
  padStroke: '--hwv-p-pad-stroke',
  pad3d: '--hwv-p-pad3d',
  trace: '--hwv-p-trace',
} as const

export type PaletteKey = keyof typeof PALETTE_VARS
export type Palette = Record<PaletteKey, string> & {
  /** 2D 导线的 HSL 亮度（hsl(hue 70% X)）：浅色主题要深一些才看得清。 */
  wireLightness: string
}

/** 回退 = **浅色**一套（与 styles.ts 的 `.hwv-root` 声明一致）。 */
const FALLBACK: Palette = {
  canvasBg: '#f7f8fa',
  grid: 'rgba(15, 17, 21, 0.06)',
  boardFill: '#e9f2ec',
  boardStroke: '#7fae8f',
  holeFill: '#eef1f4',
  holeStroke: 'rgba(15, 17, 21, 0.35)',
  label: '#1b1b1c',
  estMark: '#7c3aed',
  plainStroke: 'rgba(15, 17, 21, 0.30)',
  selStroke: '#1971c2',
  errStroke: '#ec1313',
  warnStroke: '#dd8629',
  fillDatasheet: '#3b82f6',
  fillMeasured: '#22c55e',
  fillDerived: '#7aaaff',
  fillEstimated: '#a78bfa',
  fillError: '#f25a5a',
  fillWarn: '#f7ad31',
  board3d: '#cfe9d6',
  board3dEdge: '#5f8f70',
  hole3d: '#43454a',
  edge3d: '#81858c',
  grid3dMajor: '#cfd3d6',
  grid3dMinor: '#e1e5ee',
  shell3d: '#f59e0b',
  wire3d: '#1971c2',
  sceneBg: '#eef1f4',
  padFill: '#c8a951',
  padStroke: '#8a6d1f',
  pad3d: '#d4af37',
  trace: '#c45c26',
  wireLightness: '45%',
}

/**
 * 从元素读当前调色板。元素未挂载/宿主样式缺失时逐项回退。
 * `el` 传画布的容器（`hwv-pane` 之内即可，变量定义在祖先 `.hwv-root`）。
 */
export function readPalette(el: Element | null): Palette {
  const out: Record<string, string> = {}
  if (el !== null && typeof getComputedStyle === 'function') {
    const computed = getComputedStyle(el)
    for (const [key, variable] of Object.entries(PALETTE_VARS)) {
      const raw = computed.getPropertyValue(variable).trim()
      out[key] = raw === '' ? FALLBACK[key as PaletteKey] : raw
    }
    const wireL = computed.getPropertyValue('--hwv-p-wire-l').trim()
    out.wireLightness = wireL === '' ? FALLBACK.wireLightness : wireL
    return out as Palette
  }
  return { ...FALLBACK }
}

/** 元件填充色按尺寸来源取。 */
export function partFill(palette: Palette, source: string): string {
  switch (source) {
    case 'datasheet': return palette.fillDatasheet
    case 'measured': return palette.fillMeasured
    case 'derived': return palette.fillDerived
    default: return palette.fillEstimated
  }
}

/** 订阅宿主主题翻转（`body[data-ds-dark-theme]` 增删即变）。返回退订。 */
export function subscribeTheme(cb: () => void): () => void {
  if (typeof MutationObserver === 'undefined' || typeof document === 'undefined') {
    return () => {}
  }
  const observer = new MutationObserver(cb)
  observer.observe(document.body, { attributes: true, attributeFilter: ['data-ds-dark-theme'] })
  return () => { observer.disconnect() }
}

/** 组件用：主题每翻一次，返回的序号 +1 —— 把它放进绘制 effect 的依赖里。 */
export function useThemeRevision(): number {
  const [revision, setRevision] = useState(0)
  useEffect(() => subscribeTheme(() => { setRevision((value) => value + 1) }), [])
  return revision
}
