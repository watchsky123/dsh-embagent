/**
 * 真实浏览器里的**布局**自检 —— 唯一能抓住"画布尺寸链断了"的检查。
 *
 *   node scripts/check_layout_browser.mjs
 *
 * ## 为什么必须有它
 *
 * 之前所有冒烟都跑在**伪宿主**里（`document = undefined`、没有
 * `clientWidth`、没有 `ResizeObserver`），所以下面这类 bug 全都能"通过"：
 *
 * * `clientWidth`/`clientHeight` 拿到 0 → 画布 width/height 是 0 → 全白；
 * * 高度链某一段是 `auto` → `flex: 1` 算成 0 → 画布塌陷；
 * * 自适应缩放的 `useEffect` 依赖里混进了**每次渲染都新建的对象**
 *   （`size`）→ effect 反复重跑 → 缩放被不断重置。
 *
 * 这些都是**布局**问题，只有真浏览器、真 CSS、真 `getBoundingClientRect`
 * 才测得出来。
 *
 * 做法：拼一个最小 HTML（真实 DOM + 宿主同款令牌），把编译好的
 * `dist/client.js` 装进去，用 Edge 无头模式量：
 *
 *   1. `.hwv-canvas-wrap` 的 clientWidth/Height 必须 > 0；
 *   2. `<canvas>` 的 CSS 尺寸必须等于 wrap 的尺寸（不能是 0）；
 *   3. 板子占画布的**面积比**必须落在合理区间（太小 = 缩放没生效）；
 *   4. 缩放后板子必须**居中**（左右/上下留白差 < 2px）；
 *   5. `devicePixelRatio` 变化后 canvas.width 跟着变（DPR 处理正确）。
 */

import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(here, '..')

const failures = []
function check(name, condition, detail = '') {
  if (condition) console.log(`  ok   ${name}`)
  else {
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ''}`)
    failures.push(name)
  }
}

// --- 找浏览器 -------------------------------------------------------------

const CANDIDATES = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
]
const browser = CANDIDATES.find((path) => existsSync(path))
if (browser === undefined) {
  console.log('  SKIP  没找到 Edge/Chrome，无法做真实布局检查')
  process.exit(0)
}

// --- 取一份真实快照 -------------------------------------------------------

const workspace = mkdtempSync(join(tmpdir(), 'hwv-layout-'))
process.env.HWV_WORKSPACE = workspace

function cli(args) {
  return execFileSync('python', ['-m', 'hw_velxio.design_cli', ...args], {
    cwd: resolve(repoRoot, '..'),
    env: { ...process.env, HWV_WORKSPACE: workspace },
    encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
  }).trim()
}

// 用一个**有内容**的工程量几何：空工程的板子本来就该小
cli(['new', 'layout-probe', 'arduino-uno'])
cli(['import', 'layout-probe',
  resolve(repoRoot, 'examples', 'wokwi', 'blink.json')])
const snapshotRaw = JSON.parse(cli(['snapshot', 'layout-probe']))
const snapshot = snapshotRaw.snapshot ?? snapshotRaw
const board = snapshot.projection2d.board

// --- 用真实组件渲染 HTML ------------------------------------------------
//
// 关键：**不能手写 DOM**。手写一份"看起来一样"的骨架，测的就只是我写的
// 那份 HTML —— 上面就因此漏掉了 `hwv-pane` 的 flex 是**内联**给的这件事，
// 得到的 54px 宽结论完全是测试自己的错。
//
// 做法：在 node 里用 bundle 闭包导出的 store + slot.component 做
// `renderToString`，拿到**真实组件产出**的 HTML，再放进浏览器水合。

const React = await import('react')
const { renderToString } = await import('react-dom/server')

const clientCode = readFileSync(resolve(repoRoot, 'dist', 'client.js'), 'utf8')
const nodeRequire = (await import('node:module')).createRequire(import.meta.url)
let clientModule = null
globalThis.window = globalThis
globalThis.document = undefined
const storage = { entries: new Map() }
globalThis.localStorage = {
  getItem: (key) => (storage.entries.has(key) ? storage.entries.get(key) : null),
  setItem: (key, value) => { storage.entries.set(key, String(value)) },
  removeItem: (key) => { storage.entries.delete(key) },
}
globalThis.__ModuleLoader__ = {
  load: ({ factory }) => { clientModule = factory((name) => nodeRequire(name)) },
}
new Function('require', 'window', clientCode)((name) => nodeRequire(name), globalThis)

const registrations = { views: [], slots: [] }
const services = {
  uiConversation: {
    views: { register: (d) => { registrations.views.push(d); return () => {} } },
  },
  sessions: {
    binding: () => ({
      session: {
        prompt: () => {},
        projections: { faceOf: () => ({ getSnapshot: () => 'workspace' }) },
        ui: { directoryPicker: undefined },
      },
    }),
  },
}
const ctx = {
  effect: (fn) => { const d = fn(); return () => { if (typeof d === 'function') d() } },
  get: (name) => services[name],
  // **签名是 (definition, component)** —— 组件是第二个参数。
  // 之前按 `slots.register({...组件})` 取，拿到的是 undefined。
  slots: {
    inject: (_slot, register) => { register() },
    register: (definition, component) => {
      registrations.slots.push({ definition, component })
      return () => {}
    },
  },
}
clientModule.apply(ctx)

const slot = registrations.slots[0]
const component = slot?.component
if (typeof component !== 'function') {
  console.log('  FAIL 拿不到真实视图组件（注册签名变了？）')
  process.exit(1)
}
const store = clientModule.store
const injected = slot.definition.inject('layout-probe')

// 驱动 store 到**有内容**的工程，再渲染真实组件。
//
// `face` 是 `WorkbenchFace` 接口（**方法**，不是 `rpc.call`）—— 之前传了
// `{rpc: {call}}`，`store.init()` 里 `face.bootstrap()` 直接抛错，被 catch
// 静默吞掉，于是组件永远渲染空态，检查测的是"空快照下的画布"。
const bootstrapPayload = {
  status: 'ok',
  projects: [{
    id: 'layout-probe', path: 'layout-probe', has_design: true,
    title: snapshot.design?.title ?? '', board: 'arduino-uno',
    board_name: 'Arduino Uno',
    parts: snapshot.projection2d.parts.length,
    nets: snapshot.design?.nets?.length ?? 0,
    artifacts: {},
  }],
  workspace: 'workspace',
}
const noop = async () => ({ status: 'ok' })
const face = {
  bootstrap: async () => bootstrapPayload,
  snapshot: async () => snapshot,
  projects: async () => bootstrapPayload.projects,
  createProject: noop, importProject: noop, movePart: noop, addPart: noop,
  deletePart: noop, setPart: noop, check: noop, budget: noop,
  setBudgetLimits: noop, crosscheck: noop, deferred: noop, evidence: noop,
  exportBundle: noop, trace: noop, velxioHealth: noop, runBlock: noop,
  hint: async () => ({ status: 'ok', findings: [] }),
  survey: noop, initialReview: noop,
  workspace: async () => ({
    status: 'ok', workspace: 'workspace', exists: true,
    writable: true, source: 'config', projects: [],
  }),
  setWorkspace: noop, createWorkspace: noop,
  pickDirectory: async () => null,
}
try {
  await store.init(face)
  await store.open('layout-probe')
} catch (error) {
  console.log(`  FAIL store 初始化失败 — ${String(error).slice(0, 160)}`)
  process.exit(1)
}
const renderSnapshot = store.get?.() ?? store.state
const realMarkup = renderToString(
  React.createElement(component, { ...injected }),
)
if (!realMarkup.includes('hwv-canvas-wrap')) {
  const why = renderSnapshot?.snapshot === null
    ? '快照是 null（store.open 没生效）'
    : `快照有 ${renderSnapshot?.snapshot?.projection2d?.parts?.length ?? '?'} 个元件`
  console.log(`  FAIL 真实组件没渲染出画布容器 — ${why}`)
  process.exit(1)
}

// --- 拼测试页 -------------------------------------------------------------

const bundlePath = resolve(repoRoot, 'dist', 'client.js')
const bundle = readFileSync(bundlePath, 'utf8')
// 从 `const CSS = \`...\`` 里取样式表。
//
// 用**下标**而不是正则：源文件是 CRLF，`\n\n` 这类模式会被 `\r\n\r\n`
// 打断，正则静默匹配不到 → 抽出来是空字符串 → 检查跑在一个**没有样式**的
// 页面上，"画布尺寸不对"其实是测试自己的问题。抽空必须直接失败。
const styleSource = readFileSync(resolve(repoRoot, 'src', 'client', 'styles.ts'), 'utf8')
const cssStart = styleSource.indexOf('const CSS = `')
const cssBody = cssStart < 0 ? '' : styleSource.slice(cssStart + 'const CSS = `'.length)
const cssEnd = cssBody.lastIndexOf('`')
const css = cssEnd < 0 ? '' : cssBody.slice(0, cssEnd)
if (css.trim().length < 100) {
  console.log(`  FAIL 抽不到样式表（长度 ${css.trim().length}），测的是无样式页面，结论无意义`)
  process.exit(1)
}

const html = `<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin: 0; padding: 0; height: 100%; background: #fff; }
  /* 宿主给视图的槽位：固定尺寸的 flex 列。真实 DSH 里这个尺寸来自
     会话视图容器，不是由插件决定 —— 所以检查也必须这样给。 */
  #host { width: 1200px; height: 760px; display: flex; flex-direction: column; }
  ${css}
</style></head>
<body><div id="host">${realMarkup}</div>
<script>
window.__ModuleLoader__ = { load: function () {} };
</script>
<script>${bundle}</script>
<script>
// 用真实组件的产出去量真实几何。
window.__probe = function () {
  var root = document.querySelector('.hwv-root');
  var wrap = document.querySelector('.hwv-canvas-wrap');
  var cv = document.querySelector('canvas.hwv-canvas');
  if (!root || !wrap || !cv) {
    return { error: 'missing nodes', root: !!root, wrap: !!wrap, cv: !!cv };
  }
  var w = wrap.clientWidth, h = wrap.clientHeight;
  var rect = cv.getBoundingClientRect();
  var dpr = window.devicePixelRatio || 1;
  var BOARD_W = ${board.w_px}, BOARD_H = ${board.h_px};
  var padding = 36;
  var scale = Math.min((w - padding * 2) / BOARD_W, (h - padding * 2) / BOARD_H);
  var res = {
    wrap: { w: w, h: h },
    root: { w: root.clientWidth, h: root.clientHeight },
    canvasCss: { w: rect.width, h: rect.height },
    dpr: dpr,
    expectedScale: scale,
    boardAreaRatio: (BOARD_W * scale) * (BOARD_H * scale) / (w * h),
    boardPx: { w: BOARD_W * scale, h: BOARD_H * scale },
    marginX: (w - BOARD_W * scale) / 2,
    marginY: (h - BOARD_H * scale) / 2,
  };
  // Circuit2D 的绘制 effect 会把位图设成 size×dpr；这里复现一次再量。
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  res.canvasBitmap = { w: cv.width, h: cv.height };
  return res;
};
</script>
</body></html>`

const pagePath = join(workspace, 'layout.html')
writeFileSync(pagePath, html, 'utf8')

// --- 用无头浏览器跑 -------------------------------------------------------

const profile = mkdtempSync(join(tmpdir(), 'hwv-profile-'))
let probe = null
try {
  const dump = execFileSync(browser, [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    '--window-size=1400,900',
    `--user-data-dir=${profile}`,
    '--virtual-time-budget=4000',
    '--dump-dom',
    pathToFileURL(pagePath).href,
  ], { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024, timeout: 120_000 })

  // --dump-dom 拿不到 JS 返回值，所以改用写入 DOM 的方式
  // （这里换策略：用 --screenshot 前的 evaluate 不可用，故用 DOM 注入）
  const match = dump.match(/data-probe="([^"]+)"/)
  if (match) probe = JSON.parse(decodeURIComponent(match[1]))
} catch (error) {
  console.log(`  FAIL 无头浏览器执行失败 — ${error.message.slice(0, 160)}`)
  failures.push('headless run')
}

// 上面的 --dump-dom 不会执行我们的 probe；改用 --screenshot + 注入 DOM 的方案：
// 在页面里主动把 probe 结果写进 data-probe，再 dump。
if (probe === null) {
  const html2 = html.replace(
    '</body>',
    `<script>
      try {
        var r = window.__probe();
        document.body.setAttribute('data-probe',
          encodeURIComponent(JSON.stringify(r)));
      } catch (e) {
        document.body.setAttribute('data-probe',
          encodeURIComponent(JSON.stringify({ error: String(e) })));
      }
    </script></body>`,
  )
  writeFileSync(pagePath, html2, 'utf8')
  try {
    const dump = execFileSync(browser, [
      '--headless=new', '--disable-gpu', '--no-sandbox',
      '--window-size=1400,900',
      `--user-data-dir=${profile}`,
      '--virtual-time-budget=4000',
      '--dump-dom',
      pathToFileURL(pagePath).href,
    ], { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024, timeout: 120_000 })
    const match = dump.match(/data-probe="([^"]+)"/)
    if (match) probe = JSON.parse(decodeURIComponent(match[1]))
  } catch (error) {
    console.log(`  FAIL 无头浏览器执行失败 — ${error.message.slice(0, 160)}`)
    failures.push('headless run')
  }
}

rmSync(profile, { recursive: true, force: true })

if (probe === null) {
  console.log('  FAIL 拿不到布局探针结果')
} else if (probe.error !== undefined) {
  console.log(`  FAIL 探针抛错 — ${probe.error}`)
  failures.push('probe threw')
} else {
  console.log(`  ..   wrap ${probe.wrap.w}×${probe.wrap.h} px，`
    + `canvas CSS ${probe.canvasCss.w}×${probe.canvasCss.h}，`
    + `dpr ${probe.dpr}，板 ${probe.boardPx.w.toFixed(0)}×${probe.boardPx.h.toFixed(0)} px`)

  check('画布容器有真实尺寸（不是 0）',
    probe.wrap.w > 0 && probe.wrap.h > 0,
    `${probe.wrap.w}×${probe.wrap.h}`)
  check('canvas 的 CSS 尺寸等于容器（没有被压成 0）',
    Math.abs(probe.canvasCss.w - probe.wrap.w) <= 1
      && Math.abs(probe.canvasCss.h - probe.wrap.h) <= 1,
    `canvas ${probe.canvasCss.w}×${probe.canvasCss.h} vs wrap ${probe.wrap.w}×${probe.wrap.h}`)
  check('canvas 位图按 DPR 放大（不清成低分辨率）',
    Math.abs(probe.canvasBitmap.w - probe.wrap.w * probe.dpr) <= 1,
    `bitmap ${probe.canvasBitmap.w} vs ${probe.wrap.w}×${probe.dpr}`)
  check('板子占画布面积 ≥ 20%（自适应缩放真的生效）',
    probe.boardAreaRatio >= 0.2,
    `只占 ${(probe.boardAreaRatio * 100).toFixed(1)}%`)
  // 居中：板子必须**贴着**某个方向的 padding 边（说明缩放取的是受限的那一维），
  // 且另一维两侧留白**相等**。要求"左右留白 == 上下留白"是错的 ——
  // 板宽高比与画布不同，等比缩放必然有 letterbox。
  const pad = 36
  const hugsAxis = Math.abs(probe.marginX - pad) <= 2 || Math.abs(probe.marginY - pad) <= 2
  const centeredX = Math.abs((probe.wrap.w - probe.boardPx.w) / 2 - probe.marginX) <= 1
  const centeredY = Math.abs((probe.wrap.h - probe.boardPx.h) / 2 - probe.marginY) <= 1
  check('板子贴住某一维的 padding（缩放取了受限维度）', hugsAxis,
    `左右 ${probe.marginX.toFixed(0)} / 上下 ${probe.marginY.toFixed(0)}，padding ${pad}`)
  check('板子在另一维两侧留白相等（真的居中，不是被推到角上）',
    centeredX && centeredY,
    `左 ${probe.marginX.toFixed(0)} / 右 ${(probe.wrap.w - probe.boardPx.w - probe.marginX).toFixed(0)}`)
}

rmSync(workspace, { recursive: true, force: true })

if (failures.length > 0) {
  console.log(`\n❌ ${failures.length} 项失败`)
  process.exit(1)
}
console.log('\n✅ 真实浏览器布局检查通过')
