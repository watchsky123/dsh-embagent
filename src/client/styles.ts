/**
 * 工作台的样式，一次注入。
 *
 * 分片：`styles/tokens`（令牌/主题色）、`layout`（骨架）、`panels`（分区
 * 局部样式）、`dark`（深色覆盖）。改样式请改对应分片，别在这里加规则。
 *
 * @module dsh-hw-velxio/client/styles
 */

import { TOKENS } from './styles/tokens.js'
import { LAYOUT } from './styles/layout.js'
import { PANELS } from './styles/panels.js'
import { DARK } from './styles/dark.js'

const CSS = TOKENS + LAYOUT + PANELS + DARK

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
