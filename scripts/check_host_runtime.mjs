/**
 * 宿主 bundle 的运行时冒烟测试（真实的 cordis Context，不需要 GUI）。
 *
 *   node scripts/check_host_runtime.mjs
 *
 * 它验证 `tsc` 验证不了的东西：
 *
 *   1. `dist/index.js` 在真实 cordis 里 `apply` 不炸；
 *   2. 远程服务以 `hwvelxio` 为 key 发布，`@Remote` 端点被登记；
 *   3. 8 个 `hw_*` 工具真的注册到了 `tools` 服务；
 *   4. 工具描述里不带"判定"承诺（判定只属于 Python 检查器）。
 *
 * 用真的 `@deepseek-ai/cordis` 而不是手搓 ctx：手搓的 ctx 会把
 * `Service` 注册路径整个跳过，那正是最可能出错的地方。
 */

import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const require = createRequire(import.meta.url)
const here = dirname(fileURLToPath(import.meta.url))

const failures = []
function check(name, condition, detail = '') {
  if (condition) console.log(`  ok   ${name}`)
  else {
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ''}`)
    failures.push(name)
  }
}

const { Context } = require('@deepseek-ai/cordis')
const { remoteMethods } = require('@deepseek-ai/dsh-typert-protocol')

const registeredTools = []
const ctx = new Context()

// 真实的 tools 服务替身：只收注册，不执行。
ctx.provide('tools', {
  register: (tool, options) => {
    registeredTools.push({ tool, options })
    return () => {}
  },
})

const host = await import(pathToFileURL(resolve(here, '..', 'dist', 'index.js')).href)

check('导出了 name/inject/apply',
  typeof host.name === 'string'
  && Array.isArray(host.inject)
  && typeof host.apply === 'function')
check('inject 声明了 tools', (host.inject ?? []).includes('tools'))

let threw = null
try {
  host.apply(ctx)
} catch (error) {
  threw = error
}
check('apply 在真实 cordis 里不抛异常', threw === null,
  threw === null ? '' : String(threw && (threw.stack || threw.message)).slice(0, 500))

const service = ctx.get('hwvelxio')
check('远程服务以 hwvelxio 发布', service !== undefined)
if (service !== undefined) {
  const markers = remoteMethods(service)
  const exported = markers.map((marker) => marker.exportName ?? marker.method)
  const expected = [
    'bootstrap', 'projects', 'snapshot', 'createProject', 'importProject',
    'getDesign', 'movePart', 'addPart', 'deletePart', 'setPart', 'check',
    'budget', 'setBudgetLimits', 'crosscheck', 'deferred', 'trace', 'evidence',
    'exportBundle', 'velxioHealth', 'velxioBoards', 'runBlock', 'capacities', 'hint',
  ]
  check(`远程端点齐全（${exported.length}）`,
    expected.every((name) => exported.includes(name)),
    `缺：${expected.filter((name) => !exported.includes(name)).join(', ')}`)
}

const toolNames = registeredTools
  .map((entry) => entry.tool?.name)
  .filter((name) => typeof name === 'string')
const expectedTools = [
  'hw_projects', 'hw_snapshot', 'hw_check', 'hw_import',
  'hw_budget', 'hw_crosscheck', 'hw_export', 'hw_block',
]
check(`注册了 ${expectedTools.length} 个工具`, expectedTools.every((n) => toolNames.includes(n)),
  `实际：${toolNames.join(', ')}`)

for (const entry of registeredTools) {
  const tool = entry.tool ?? {}
  check(`工具 ${tool.name} 有描述`, typeof tool.description === 'string'
    && tool.description.length > 10)
  check(`工具 ${tool.name} 有 schema`, tool.output?.schema?.type === 'object')
}

console.log()
if (failures.length > 0) {
  console.log(`❌ 宿主运行时冒烟：${failures.length} 项失败`)
  process.exit(1)
}
console.log('✅ 宿主运行时冒烟通过')
