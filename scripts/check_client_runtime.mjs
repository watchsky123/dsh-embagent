/**
 * 客户端 bundle 的运行时冒烟测试（不需要浏览器）。
 *
 * 它回答的是 `tsc` 回答不了的问题：**这个 bundle 真的能注册到会话视图上吗**。
 *
 *   node scripts/check_client_runtime.mjs
 *
 * 做法：伪造宿主的三样东西——`window.__ModuleLoader__`（bundle 的加载协议）、
 * `require`（loader 的模块表，直接用 node_modules）、一个最小的 cordis
 * `ctx`——然后调用 `apply`，断言：
 *
 *   1. 注册了 `views.register({ target: 'hw-ide' })`；
 *   2. 注册了 `slots` 座位 `{ name: 'conversation.view', id: 'hw-ide', order: 20 }`；
 *   3. 组件是函数（不是 undefined）；
 *   4. `inject(sessionId)` 返回 `{ face, ai.send }` 且 `ai.send` 真的把文本
 *      交给了 `session.prompt`。
 *
 * 这四件事一起，才是"视图能用"的最小证据。
 */

import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const here = dirname(fileURLToPath(import.meta.url))
const bundlePath = resolve(here, '..', 'dist', 'client.js')

const failures = []
function check(name, condition, detail = '') {
  if (condition) console.log(`  ok   ${name}`)
  else {
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ''}`)
    failures.push(name)
  }
}

// --- 伪造宿主 ---------------------------------------------------------------

globalThis.window = globalThis
globalThis.document = undefined // styles.ts 会据此跳过注入
let clientModule = null
globalThis.__ModuleLoader__ = {
  load: ({ id, factory }) => {
    if (id !== 'dsh-hw-velxio') throw new Error(`unexpected bundle id: ${id}`)
    // 模块表：bundler 把这些留作 external，由宿主 require 提供
    clientModule = factory((name) => require(name))
  },
}

const code = readFileSync(bundlePath, 'utf8')
// eslint-disable-next-line no-new-func
new Function('require', 'window', code)((name) => require(name), globalThis)
check('bundle 执行并回调 __ModuleLoader__.load', clientModule !== null)
if (clientModule === null) process.exit(1)

// --- 伪造 cordis ctx --------------------------------------------------------

const registrations = { views: [], slots: [] }
let prompted = null

const services = {
  connection: { rpc: { call: async () => ({ ok: true, value: {} }) } },
  sessions: {
    binding: () => ({ session: { prompt: (parts) => { prompted = parts[0]?.text ?? '' } } }),
  },
  uiConversation: {
    views: {
      register: (definition) => {
        registrations.views.push(definition)
        return () => {}
      },
    },
  },
}

const ctx = {
  effect: (fn) => {
    const dispose = fn()
    return () => { if (typeof dispose === 'function') dispose() }
  },
  get: (name) => services[name],
  slots: {
    inject: (slotName, register) => {
      check(`slots.inject 等待的座位是 conversation.view`, slotName === 'conversation.view',
        slotName)
      register()
    },
    register: (definition, component) => {
      registrations.slots.push({ definition, component })
      return () => {}
    },
  },
  sessions: {
    binding: () => ({ session: { prompt: (parts) => { prompted = parts[0]?.text ?? '' } } }),
  },
}

// --- 调用 apply -------------------------------------------------------------

check('bundle 导出 apply', typeof clientModule.apply === 'function')
if (typeof clientModule.apply === 'function') clientModule.apply(ctx)

check('注册了 1 个视图模型', registrations.views.length === 1)
check('视图 target 是 hw-ide',
  registrations.views[0]?.target === 'hw-ide', JSON.stringify(registrations.views[0]?.target))
check('视图工厂返回 builder',
  typeof registrations.views[0]?.create === 'function'
  && typeof registrations.views[0].create().replace === 'function')

check('注册了 1 个 slot 座位', registrations.slots.length === 1)
const slot = registrations.slots[0]
check('slot 名是 conversation.view', slot?.definition.name === 'conversation.view')
check('slot id 与视图 target 一致', slot?.definition.id === 'hw-ide')
check('slot order 在 Trajectory(10) 之后', slot?.definition.order === 20)
check('组件是函数', typeof slot?.component === 'function')
check('label 返回字符串', typeof slot?.definition.label?.() === 'string')

const injected = slot?.definition.inject?.('session-1')
check('inject 返回 sessionId/face/ai', injected !== undefined
  && injected.sessionId === 'session-1'
  && typeof injected.face?.bootstrap === 'function'
  && typeof injected.ai?.send === 'function')
if (injected !== undefined) {
  injected.ai.send('评审这个设计')
  check('ai.send 把文本交给 session.prompt', prompted === '评审这个设计', String(prompted))
}

// --- 渲染一次（不需要浏览器） -------------------------------------------------

if (injected !== undefined) {
  const React = require('react')
  const { renderToString } = require('react-dom/server')
  try {
    const html = renderToString(
      React.createElement(slot.component, { ...injected, sessionId: 'session-1' }),
    )
    check('组件能渲染出 HTML', html.length > 0)
    check('渲染结果含工作台标题', html.includes('硬件工作台'))
    check('无工程时显示空状态', html.includes('还没有工程'))
    check('空状态给出导入入口', html.includes('导入'))
  } catch (error) {
    check('组件渲染不抛异常', false, String(error && error.message))
  }
}
console.log()
if (failures.length > 0) {
  console.log(`❌ 客户端运行时冒烟：${failures.length} 项失败`)
  process.exit(1)
}
console.log('✅ 客户端运行时冒烟通过')
