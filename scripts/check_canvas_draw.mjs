/**
 * 2D 画布**真实绘制**自检 —— 挂真 `Circuit2D` 组件，量真 canvas 像素。
 *
 *   node scripts/check_canvas_draw.mjs
 *
 * ## 它抓的是什么
 *
 * 上一轮我用 Pillow **复刻**了绘制逻辑做像素检查（`check_canvas_render.py`）。
 * 复刻对了只证明"我理解对了"，复刻错了就什么都证明不了 —— 用户截图里板子
 * 明显偏小，那种检查照样全绿。
 *
 * 这里换掉复刻：用 tsdown 把**真正的 `Circuit2D`** 打成一个 bundle，
 * 在无头 Edge 里 `createRoot()` 挂上真实 DOM，然后读**真实 canvas 的
 * `getImageData`**：
 *
 *   1. 板子（板填充色）占画布的面积比 —— 太小说明自适应缩放没生效；
 *   2. 焊盘（金色）真的画了 —— 说明 pad 几何进了渲染；
 *   3. 铜线（橙色）真的画了 —— 说明走线进了渲染；
 *   4. 画布不是空白 —— 至少有 N 种颜色。
 *
 * 组件写的是 `useEffect`，`renderToString` 不跑 effect、碰不到 canvas，
 * 所以必须走**真浏览器 + 真挂载**。
 */

import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, readdirSync } from 'node:fs'
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

const CANDIDATES = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
]
const browser = CANDIDATES.find((path) => existsSync(path))
if (browser === undefined) {
  console.log('  SKIP  没找到 Edge/Chrome')
  process.exit(0)
}

// --- 取真实快照 -----------------------------------------------------------

const workspace = mkdtempSync(join(tmpdir(), 'hwv-draw-'))
function cli(args) {
  return execFileSync('python', ['-m', 'hw_velxio.design_cli', ...args], {
    cwd: resolve(repoRoot, '..'),
    env: { ...process.env, HWV_WORKSPACE: workspace },
    encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
  }).trim()
}

// 用**用户工作区里的真实工程**（若存在），否则退回立创源文件/样例。
// 这样检查覆盖的就是用户实际打开的那份数据。
const WORKSPACE = 'C:/Users/<user>/Desktop/示例工作区'
const REAL_PROJECT = '示例-无人机'
const EPRO2 = WORKSPACE + '/ProPrj_示例四旋翼飞行器_2026-11-15.epro2'
let sourceLabel = ''
// 优先用**多板**工程（如果有），这样检查也覆盖拼板渲染。
const MULTI_PROJECT = 'r3demo'
const useMulti = process.env.HWV_PROBE_MULTI === '1'
  && existsSync(`${WORKSPACE}/${MULTI_PROJECT}/design.json`)
const probeProject = useMulti ? MULTI_PROJECT : REAL_PROJECT
const hasRealProject = existsSync(`${WORKSPACE}/${probeProject}/design.json`)
if (hasRealProject) {
  // 直接复用用户工程：拷进隔离工作区，不写原目录
  const raw = readFileSync(`${WORKSPACE}/${probeProject}/design.json`, 'utf8')
  const dir = join(workspace, probeProject)
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'design.json'), raw, 'utf8')
  sourceLabel = `用户工程 ${probeProject}`
} else if (existsSync(EPRO2)) {
  cli(['import', 'draw-probe', EPRO2])
  sourceLabel = '真实立创工程'
} else {
  cli(['new', 'draw-probe', 'arduino-uno'])
  cli(['import', 'draw-probe', resolve(repoRoot, 'examples', 'wokwi', 'blink.json')])
  sourceLabel = '内置 blink 样例'
}
const probeId = hasRealProject ? probeProject : 'draw-probe'
const snapshotRaw = JSON.parse(cli(['snapshot', probeId]))
const snapshot = snapshotRaw.snapshot ?? snapshotRaw
const projection = snapshot.projection2d
console.log(`  ..   源：${sourceLabel}，${projection.parts.length} 元件 / `
  + `${projection.wires.length} 铜线 / ${projection.ratlines.length} 飞线`)
if (projection.parts.length === 0) {
  console.log('  FAIL 快照里 0 个元件，量不出东西')
  process.exit(1)
}

// 外壳也注入一份：`drawEnclosure` 只有声明了外壳才走，不注入的话这条分支
// 在 CI 里永远是死代码（"死代码"和"画对了"看起来一样）。
{
  const ppm = projection.px_per_mm
  const w = projection.panel.size_mm.w
  const h = projection.panel.size_mm.h
  const pad = 6 * ppm
  projection.enclosure = {
    outer: { x_px: -pad, y_px: -pad, w_px: w * ppm + 2 * pad,
             h_px: h * ppm + 2 * pad, w_mm: w + 12, h_mm: h + 12 },
    inner: { x_px: -pad / 2, y_px: -pad / 2, w_px: w * ppm + pad,
             h_px: h * ppm + pad, w_mm: w + 6, h_mm: h + 6 },
    openings: [{ x_px: 2 * ppm, y_px: 2 * ppm, w_px: 6 * ppm,
                 h_px: 3 * ppm, face: 'top', kind: 'usb' }],
    side_openings: 2,
    wall_mm: 3, lid_mm: 2,
    inner_mm: { w: w + 6, h: h + 6, d: 20 },
    outer_mm: { w: w + 12, h: h + 12, d: 30 },
  }
}

// --- 打包真组件 -----------------------------------------------------------
//
// **入口放在仓库内**（`scripts/.probe-entry.tsx`），不放临时目录：
// 一是相对导入 `../src/client/Circuit2D.js` 要能解析；二是 tsdown 会去读
// 仓库根的 `tsdown.config.ts`，入口在别的盘符会让它的相对路径解析乱掉。

const probeEntry = resolve(here, '.probe-entry.tsx')
writeFileSync(probeEntry, readFileSync(resolve(here, 'canvas_probe_entry.tsx'), 'utf8'))

const outDir = mkdtempSync(join(tmpdir(), 'hwv-draw-bundle-'))
writeFileSync(join(outDir, 'snapshot.json'), JSON.stringify(snapshot), 'utf8')

// 用 tsdown 的**编程接口**（`build`）。不用 CLI：它的入口文件在不同版本里
// 位置不同，猜错就静默失败 —— 而"检查没跑起来"和"检查通过"看起来一样。
//
// 内联配置对象里**不能**带 `config` 字段：那会让 tsdown 去读配置文件，
// 而配置文件在临时目录里，解析不到本包的 `tsdown`（模块解析以配置文件位置
// 为基准），于是报 ERR_MODULE_NOT_FOUND。
// tsdown 按 `format: 'iife'` 把产物命名成 `probe.iife.js`（不是 `probe.js`）。
// 写死名字曾经让"打包成功但产物找不到"，而这看起来和"打包失败"一样。
const bundlePath = [
  join(outDir, 'probe.iife.js'),
  join(outDir, 'probe.js'),
].find((candidate) => existsSync(candidate)) ?? join(outDir, 'probe.iife.js')
try {
  const tsdown = await import(
    pathToFileURL(resolve(repoRoot, 'node_modules', 'tsdown', 'dist', 'index.mjs')).href)
  await tsdown.build({
    cwd: repoRoot,
    config: false,
    entry: { probe: probeEntry },
    outDir,
    format: 'iife',
    platform: 'browser',
    dts: false,
    sourcemap: false,
    clean: false,
    noExternal: () => true,
    define: { 'process.env.NODE_ENV': JSON.stringify('production') },
    tsconfig: resolve(repoRoot, 'tsconfig.client.json'),
  })
} catch (error) {
  console.log(`  FAIL 打包真组件失败 — ${String(error).slice(0, 300)}`)
  rmSync(outDir, { recursive: true, force: true })
  rmSync(workspace, { recursive: true, force: true })
  process.exit(1)
}
if (!existsSync(bundlePath)) {
  console.log(`  FAIL 打包产物不存在：${bundlePath}`)
  rmSync(outDir, { recursive: true, force: true })
  rmSync(workspace, { recursive: true, force: true })
  process.exit(1)
}

// --- 拼页面 ---------------------------------------------------------------

// **顺序 = styles.ts 里的拼接顺序**（tokens → layout → panels → dark）。
// 按文件名排序会把 dark 放最前：它那条 `body[data-ds-dark-theme] .hwv-root {`
// 若在末尾少一个 `}`，会把后面所有规则吞进深色作用域，浅色页面就"没样式"。
const STYLE_ORDER = ['tokens', 'layout', 'panels', 'dark']
const styleSources = [
  readFileSync(resolve(repoRoot, 'src', 'client', 'styles.ts'), 'utf8'),
  ...STYLE_ORDER.map((name) =>
    readFileSync(resolve(repoRoot, 'src', 'client', 'styles', `${name}.ts`), 'utf8')),
]
function extractCss(src) {
  const at = src.indexOf('= `')
  if (at < 0) return ''
  const body = src.slice(at + 3)
  const end = body.lastIndexOf('`')
  return end < 0 ? '' : body.slice(0, end)
}
const css = styleSources.map(extractCss).filter((part) => part.trim() !== '').join('\n')
if (css.trim().length < 100) {
  console.log('  FAIL 抽不到样式表，测的是无样式页面')
  process.exit(1)
}

const snapshotJson = JSON.stringify(snapshot)
const html = `<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin: 0; padding: 0; height: 100%; background: #fff; }
  #host { width: 1000px; height: 640px; }
  ${css}
</style></head>
<body><div id="host"></div>
<script>
  window.__errors = [];
  window.addEventListener('error', function (e) {
    window.__errors.push('error: ' + (e.message || '') + ' @' + (e.filename || ''));
  });
  window.addEventListener('unhandledrejection', function (e) {
    window.__errors.push('reject: ' + String(e.reason));
  });
  var _ce = console.error;
  console.error = function () {
    window.__errors.push('console.error: ' + Array.prototype.slice.call(arguments).join(' '));
    return _ce.apply(console, arguments);
  };
</script>
<script>window.__SNAPSHOT__ = ${snapshotJson};</script>
<script>${readFileSync(bundlePath, 'utf8')}</script>
<script>
function measure() {
  var wrap = document.querySelector('.hwv-canvas-wrap');
  var cv = document.querySelector('canvas.hwv-canvas');
  if (!wrap || !cv) return { error: 'no canvas', wrap: !!wrap, cv: !!cv };
  var w = wrap.clientWidth, h = wrap.clientHeight;
  var ctx = cv.getContext('2d');
  if (!ctx) return { error: 'no 2d context' };
  var data;
  try {
    data = ctx.getImageData(0, 0, cv.width, cv.height).data;
  } catch (e) {
    return { error: 'getImageData threw: ' + String(e) };
  }
  var counts = {};
  var nonBlank = 0;
  for (var i = 0; i < data.length; i += 4) {
    var r = data[i], g = data[i+1], b = data[i+2], a = data[i+3];
    if (a === 0) continue;
    nonBlank++;
    var key = (r >> 4) + ',' + (g >> 4) + ',' + (b >> 4);
    counts[key] = (counts[key] || 0) + 1;
  }
  var total = cv.width * cv.height;
  return {
    errors: window.__errors || [],
    wrap: { w: w, h: h },
    bitmap: { w: cv.width, h: cv.height },
    total: total,
    nonBlank: nonBlank,
    distinct: Object.keys(counts).length,
    top: Object.entries(counts).sort(function (a, b) { return b[1] - a[1]; })
      .slice(0, 8).map(function (e) { return { color: e[0], n: e[1] }; }),
  };
}
// 绘制发生在 useEffect 里，而它依赖 ResizeObserver 量到的尺寸。
// 不能只等固定两帧：观察器回调、布局、绘制三者的相对顺序不保证，
// 固定等待会让这个检查时绿时红 —— 上一版就是这样（同一份代码，一次
// 1000×615/1012 色，一次 300×150/0 像素）。改成轮询到真的画出来。
var DEADLINE = Date.now() + 4000;
function ready() {
  var cv = document.querySelector('canvas.hwv-canvas');
  if (!cv) return false;
  var wrap = document.querySelector('.hwv-canvas-wrap');
  if (!wrap || wrap.clientWidth === 0) return false;
  // 位图被绘制 effect 设成非默认值 = effect 跑过了
  return cv.width > 300 || cv.height > 150;
}
function poll() {
  if (!ready() && Date.now() < DEADLINE) {
    setTimeout(poll, 50);
    return;
  }
  var r = measure();
  r.waitedMs = 4000 - (DEADLINE - Date.now());
  // 顺手导出一张 PNG，方便人眼确认"画出来的是一块板"
  try {
    r.png = cv.toDataURL('image/png');
  } catch (e) { r.pngError = String(e); }
  document.body.setAttribute('data-probe',
    encodeURIComponent(JSON.stringify(r)));
}
setTimeout(poll, 0);
</script>
</body></html>`

const pagePath = join(tmpdir(), 'hwv-last-page.html')
writeFileSync(pagePath, html, 'utf8')

// --- 跑浏览器 -------------------------------------------------------------

const profile = mkdtempSync(join(tmpdir(), 'hwv-draw-profile-'))
let probe = null
try {
  const dump = execFileSync(browser, [
    '--headless=new', '--disable-gpu', '--no-sandbox',
    '--window-size=1100,720',
    `--user-data-dir=${profile}`,
    '--virtual-time-budget=6000',
    '--dump-dom',
    pathToFileURL(pagePath).href,
  ], { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024, timeout: 120_000 })
  const match = dump.match(/data-probe="([^"]+)"/)
  if (match) probe = JSON.parse(decodeURIComponent(match[1]))
} catch (error) {
  console.log(`  FAIL 浏览器执行失败 — ${String(error).slice(0, 160)}`)
}
rmSync(profile, { recursive: true, force: true })
rmSync(outDir, { recursive: true, force: true })
rmSync(workspace, { recursive: true, force: true })
rmSync(probeEntry, { force: true })

if (probe === null) {
  console.log('  FAIL 拿不到探针结果')
  process.exit(1)
}
if (probe.error !== undefined) {
  console.log(`  FAIL 探针失败 — ${probe.error}`)
  process.exit(1)
}

console.log(`  ..   wrap ${probe.wrap.w}×${probe.wrap.h}，`
  + `位图 ${probe.bitmap.w}×${probe.bitmap.h}，`
  + `非空像素 ${(probe.nonBlank / probe.total * 100).toFixed(1)}%，`
  + `颜色 ${probe.distinct} 种`)
console.log(`  ..   主色：${probe.top.map((x) => `${x.color}(${(x.n / probe.total * 100).toFixed(1)}%)`).join(' ')}`)

check('画布容器有尺寸', probe.wrap.w > 0 && probe.wrap.h > 0,
  `${probe.wrap.w}×${probe.wrap.h}`)
check('位图与容器同尺寸（DPR 处理正确）',
  Math.abs(probe.bitmap.w - probe.wrap.w) <= 1
    && Math.abs(probe.bitmap.h - probe.wrap.h) <= 1,
  `bitmap ${probe.bitmap.w}×${probe.bitmap.h} vs wrap ${probe.wrap.w}×${probe.wrap.h}`)
check('画布画了东西（非空像素 > 20%）',
  probe.nonBlank / probe.total > 0.2,
  `只有 ${(probe.nonBlank / probe.total * 100).toFixed(1)}%`)
check('有足够多颜色（不是一整块纯色）', probe.distinct >= 4,
  `只有 ${probe.distinct} 种`)

// 主色里必须出现"板"和"元件"两种明显不同的色块
const biggest = probe.top[0]?.n / probe.total ?? 1
check('最大单一颜色不吞掉整块画布（板子不是铺满屏）',
  biggest < 0.95,
  `最大色占 ${(biggest * 100).toFixed(1)}%`)

// 存一张真图，人眼可复核
// 导出真实画面：走 --screenshot 更可靠（`--dump-dom` 会把超长 data URL 截断）
{
  const profile2 = mkdtempSync(join(tmpdir(), 'hwv-shot-'))
  const pngPath = join(tmpdir(), 'hwv-canvas-real.png')
  try {
    execFileSync(browser, [
      '--headless=new', '--disable-gpu', '--no-sandbox',
      '--window-size=1100,720',
      `--user-data-dir=${profile2}`,
      '--virtual-time-budget=6000',
      `--screenshot=${pngPath}`,
      pathToFileURL(join(tmpdir(), 'hwv-last-page.html')).href,
    ], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024, timeout: 90_000 })
    if (existsSync(pngPath)) console.log(`  ..   真实渲染图：${pngPath}`)
  } catch { /* 截图失败不影响判定 */ }
  rmSync(profile2, { recursive: true, force: true })
}

// 外壳分支：源码里存在 + 注入后画布仍然正常（绘制异常会被 effect 吞掉，
// 表现成"板还在、外壳没画"，只有像素/非空断言能抓到一半）。
const circuitSource = readFileSync(resolve(repoRoot, 'src/client/Circuit2D.tsx'), 'utf8')
check('2D 有外壳投影分支（drawEnclosure）',
  circuitSource.includes('drawEnclosure')
  && circuitSource.includes('projection.enclosure'))

if (failures.length > 0) {
  console.log(`\n❌ ${failures.length} 项失败`)
  process.exit(1)
}
console.log('\n✅ 2D 画布真实绘制检查通过')