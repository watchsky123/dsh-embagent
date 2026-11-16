/**
 * Remote 端点签名回归检查。
 *
 * 起因：宿主报
 *   gateway/signature-invalid: typert gateway: hwvelxio/snapshot: SRC method
 *   "snapshot" must use unique identifier parameters without destructuring,
 *   defaults, or rest
 *
 * 机制（从宿主 app.asar 里读到的校验器源码，逐条复刻）：Typert 网关在**注册期**
 * 对每个 `@Remote` 方法做 `Function.prototype.toString`，把参数表按逗号切开，
 * 每段必须匹配 `/^[$A-Z_a-z][$\w]*$/` 且不重复。于是：
 *   - `pitchMm = 0.5`  → 段里有 `=`，不匹配 → 抛 signature-invalid
 *   - `{ a, b }`       → 逗号把它切成 `{ a` / ` b }`，两段都不匹配 → 抛
 *   - `...rest`        → 段里有 `.`，不匹配 → 抛
 *   - 同名参数两次      → 重复 → 抛
 *
 * 关键点：校验的是**编译产物里的 JS 参数表**，不是 TS 源码。TS 的类型注解会被
 * 擦除，所以 `params: Record<string, unknown> = {}` 在产物里就是 `params = {}`
 * ——默认值照样留着，照样被拒。因此这里读 `dist/service.js` 的真实原型方法，
 * 而不是去 grep 源码：源码干净不代表产物干净（忘记重新 build 就是这种裂缝）。
 *
 * 用 `import()` 装载产物，遍历 `HwVelxioGateway.prototype` 的自有方法（这里
 * 每个方法都是 @Remote 端点），把宿主算法原样跑一遍。
 *
 *   node scripts/check_remote_signature.mjs
 */

import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const pluginRoot = resolve(here, '..')
const bundle = resolve(pluginRoot, 'dist', 'service.js')

const failures = []
const check = (name, ok, detail) => {
  if (ok) console.log(`  ok   ${name}`)
  else { console.log(`  FAIL ${name}${detail ? ' — ' + detail : ''}`); failures.push(name) }
}

// 宿主的参数名解析算法（复刻自 app.asar 的 methodParameterNames）。
// 返回参数名数组；非法形状返回 null —— 与宿主"返回 invalidSignature"一致。
function parseParameters(source) {
  const open = source.indexOf('(')
  const close = source.indexOf(')', open + 1)
  if (open < 0 || close < 0) return null
  const body = source.slice(open + 1, close).trim()
  if (body.length === 0) return []
  const parts = body.split(',').map((part) => part.trim())
  const names = new Set()
  for (const part of parts) {
    if (!/^[$A-Z_a-z][$\w]*$/u.test(part) || names.has(part)) return null
    names.add(part)
  }
  return [...names]
}

// 把一段非法参数翻译成"为什么非法"，让失败信息能直接指到默认值/解构/rest。
function explain(source) {
  const open = source.indexOf('(')
  const close = source.indexOf(')', open + 1)
  if (open < 0 || close < 0) return '（读不到参数表）'
  const body = source.slice(open + 1, close).trim()
  if (body === '') return ''
  const bad = body.split(',').map((p) => p.trim()).filter((p) => !/^[$A-Z_a-z][$\w]*$/u.test(p))
  return bad.length > 0 ? `非法参数段：${bad.join(', ')}` : '参数名重复'
}

const mod = await import(pathToFileURL(bundle).href)
const Gateway = mod.HwVelxioGateway
check('dist/service.js 导出 HwVelxioGateway', typeof Gateway === 'function')

if (typeof Gateway === 'function') {
  const proto = Gateway.prototype
  const methods = Object.getOwnPropertyNames(proto).filter((name) => {
    if (name === 'constructor') return false
    const descriptor = Object.getOwnPropertyDescriptor(proto, name)
    return descriptor !== undefined && typeof descriptor.value === 'function'
  })
  check('原型上有 Remote 方法可检查', methods.length > 0, `${methods.length} 个`)

  // 这些端点曾带默认值（本轮修复的对象）；点名确认它们现在是纯标识符参数表。
  const previouslyBroken = ['snapshot', 'importProject', 'movePart', 'addPart',
    'check', 'deferred', 'trace', 'runBlock']

  let allValid = true
  const broken = []
  for (const name of methods) {
    const source = Function.prototype.toString.call(proto[name])
    const parsed = parseParameters(source)
    if (parsed === null) { allValid = false; broken.push(`${name}: ${explain(source)}`) }
  }
  check('所有 @Remote 端点参数表是纯标识符（无默认值/解构/rest）',
    allValid, broken.join('；'))

  for (const name of previouslyBroken) {
    const descriptor = Object.getOwnPropertyDescriptor(proto, name)
    if (descriptor === undefined || typeof descriptor.value !== 'function') {
      check(`端点 ${name} 仍存在`, false, '产物里找不到该方法')
      continue
    }
    const source = Function.prototype.toString.call(descriptor.value)
    check(`端点 ${name} 已修复`, parseParameters(source) !== null, explain(source))
  }
}

console.log()
if (failures.length > 0) {
  console.log(`❌ Remote 签名检查：${failures.length} 项失败`)
  process.exit(1)
}
console.log('✅ Remote 签名检查通过')
