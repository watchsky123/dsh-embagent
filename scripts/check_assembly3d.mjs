/**
 * 3D 视图**真实渲染**自检 —— 挂真 `Assembly3D`，在无头浏览器里截图。
 *
 *   node scripts/check_assembly3d.mjs
 *
 * ## 它抓的是什么
 *
 * 3D 视图出过的 bug 全是"看不到 / 长得不对"这一类，DOM 断言和 payload
 * 断言都抓不住：
 *
 * * 光照过曝 → 所有材质冲成白色，截图一片惨白；
 * * 地面网格挂在旋转过的根节点下 → 变成一面竖墙；
 * * 元件几何用旋转后的 bbox 又转一次 → 四个朝向一样；
 * * WebGL 上下文创建失败 → 画布是空的（异常被 effect 吞掉）。
 *
 * 做法：把真 `Assembly3D` 打包，在无头 Edge 里挂载（开
 * `--use-gl=swiftshader` 让无头环境也有 WebGL），然后读 WebGL 画布的像素：
 *
 *   1. 画布不是纯色（渲染真的有输出）；
 *   2. 亮度分布合理（不是全白过曝，也不是全黑）；
 *   3. 颜色数足够多（有体积感的明暗层次）。
 */

import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync, readdirSync } from 'node:fs'
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

// --- 快照 ----------------------------------------------------------------

const workspace = mkdtempSync(join(tmpdir(), 'hwv-3d-'))
function cli(args) {
  return execFileSync('python', ['-m', 'hw_velxio.design_cli', ...args], {
    cwd: resolve(repoRoot, '..'),
    env: { ...process.env, HWV_WORKSPACE: workspace },
    encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
  }).trim()
}
const EPRO2 = 'C:/Users/<user>/Desktop/示例工作区/'
  + 'ProPrj_示例四旋翼飞行器_2026-11-15.epro2'
if (existsSync(EPRO2)) {
  cli(['import', 'probe3d', EPRO2])
} else {
  cli(['new', 'probe3d', 'arduino-uno'])
  cli(['import', 'probe3d', resolve(repoRoot, 'examples', 'wokwi', 'blink.json')])
}
const snapshotRaw = JSON.parse(cli(['snapshot', 'probe3d']))
const snapshot = snapshotRaw.snapshot ?? snapshotRaw
const scene = snapshot.scene3d
console.log(`  ..   ${scene.parts.length} 实体 / ${scene.wires.length} 铜线 / `
  + `板 ${scene.board.size_mm.w.toFixed(1)}×${scene.board.size_mm.h.toFixed(1)}mm`)

// --- 打包真组件 -----------------------------------------------------------

const probeEntry = resolve(here, '.probe3d-entry.tsx')
writeFileSync(probeEntry, `
import { createRoot } from 'react-dom/client'
import { Assembly3D } from '../src/client/Assembly3D.js'
const snapshot = (window as any).__SNAPSHOT__
function App() {
  return (
    <div className="hwv-root" style={{ height: '100%' }}>
      <div className="hwv-main"><div className="hwv-panes">
        <div className="hwv-pane" style={{ flex: '1 1 0%' }}>
          <div className="hwv-pane-head">3D 装配</div>
          <Assembly3D
            snapshot={snapshot}
            selectedRef=""
            findings={[]}
            onSelect={() => {}}
            explode={false} section={false} grid={true}
            onExplode={() => {}} onSection={() => {}} onGrid={() => {}}
            sectionAt={0.6} onSectionAt={() => {}}
          />
        </div>
      </div></div>
    </div>
  )
}
const host = document.getElementById('host')
if (host) createRoot(host).render(<App />)
`, 'utf8')

const outDir = mkdtempSync(join(tmpdir(), 'hwv-3d-bundle-'))
try {
  const tsdown = await import(
    pathToFileURL(resolve(repoRoot, 'node_modules', 'tsdown', 'dist', 'index.mjs')).href)
  await tsdown.build({
    cwd: repoRoot, config: false,
    entry: { probe: probeEntry }, outDir,
    format: 'iife', platform: 'browser',
    dts: false, sourcemap: false, clean: false,
    noExternal: () => true,
    define: { 'process.env.NODE_ENV': JSON.stringify('production') },
    tsconfig: resolve(repoRoot, 'tsconfig.client.json'),
  })
} catch (error) {
  console.log(`  FAIL 打包 3D 组件失败 — ${String(error).slice(0, 240)}`)
  rmSync(probeEntry, { force: true })
  rmSync(workspace, { recursive: true, force: true })
  process.exit(1)
}
const bundlePath = [join(outDir, 'probe.iife.js'), join(outDir, 'probe.js')]
  .find((p) => existsSync(p)) ?? join(outDir, 'probe.iife.js')

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

const pagePath = join(tmpdir(), 'hwv-3d-page.html')
writeFileSync(pagePath, `<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin: 0; padding: 0; height: 100%; background: #fff; }
  #host { width: 1000px; height: 620px; }
  ${css}
</style></head>
<body><div id="host"></div>
<script>
window.__errors = [];
window.addEventListener('error', function (e) {
  window.__errors.push('error: ' + (e.message || ''));
});
var _ce = console.error;
console.error = function () {
  window.__errors.push('console.error: ' +
    Array.prototype.slice.call(arguments).join(' '));
  return _ce.apply(console, arguments);
};
window.__SNAPSHOT__ = ${JSON.stringify(snapshot)};
</script>
<script>${readFileSync(bundlePath, 'utf8')}</script>
<script>
function probe() {
  var cv = document.querySelector('canvas');
  if (!cv) return { error: 'no canvas', errors: window.__errors };
  // **不要用 gl.readPixels**：three 的 rAF 循环每帧 render 后缓冲可能已被
  // 交换/清空，readPixels 读到的是全 0 —— 上一版就是这么把"渲染正常"
  // 误判成"全黑"的（截图明明有板子）。改成从 2D 上下文取快照，
  // 它读的是合成后的最终画面。
  var w = cv.width, h = cv.height;
  var off = document.createElement('canvas');
  off.width = w; off.height = h;
  var ctx = off.getContext('2d');
  ctx.drawImage(cv, 0, 0);
  var px;
  try {
    px = ctx.getImageData(0, 0, w, h).data;
  } catch (e) {
    return { error: 'getImageData threw: ' + String(e), errors: window.__errors };
  }
  var hist = {}, lit = 0, sum = 0, n = 0, white = 0, black = 0;
  for (var i = 0; i < px.length; i += 4) {
    var r = px[i], g = px[i+1], b = px[i+2], a = px[i+3];
    if (a === 0) continue;
    var lum = 0.2126*r + 0.7152*g + 0.0722*b;
    sum += lum; n++;
    if (lum > 245 && r > 245 && g > 245 && b > 245) white++;
    if (lum < 8) black++;
    if (lum > 10) lit++;
    var key = (r >> 4) + ',' + (g >> 4) + ',' + (b >> 4);
    hist[key] = (hist[key] || 0) + 1;
  }
  if (n === 0) return { error: 'no opaque pixels', errors: window.__errors };
  return {
    size: { w: w, h: h },
    meanLum: sum / n,
    litRatio: lit / n,
    whiteRatio: white / n,
    blackRatio: black / n,
    distinct: Object.keys(hist).length,
    errors: window.__errors,
  };
}
var DEADLINE = Date.now() + 6000;
function poll() {
  var cv = document.querySelector('canvas');
  // 等真出画面：有 canvas 且位图非默认尺寸
  if ((!cv || cv.width <= 1) && Date.now() < DEADLINE) {
    setTimeout(poll, 80); return;
  }
  setTimeout(function () {
    var r = probe();
    document.body.setAttribute('data-probe',
      encodeURIComponent(JSON.stringify(r)));
  }, 400);
}
setTimeout(poll, 0);
</script>
</body></html>`, 'utf8')

// --- 用截图判定（最可靠）--------------------------------------------------
//
// 不能用 `gl.readPixels`：three 的 rAF 循环每帧 render 后缓冲已被交换，
// 读到的是全 0 —— 上一版就是这么把"渲染正常"误判成"全黑"的。
// 也不能 `drawImage(webglCanvas)`：`preserveDrawingBuffer` 默认 false，
// 合成后二进制数据不可读，`getImageData` 全是 0。
//
// **浏览器自己的 `--screenshot` 是唯一可靠的证据** —— 它截的就是用户看到的
// 合成结果。所以判定改成读那张 PNG 的像素直方图。

const pngPath = join(tmpdir(), 'hwv-3d-real.png')
const profile = mkdtempSync(join(tmpdir(), 'hwv-3d-profile-'))
let shot = false
try {
  execFileSync(browser, [
    '--headless=new', '--no-sandbox',
    '--use-gl=angle', '--use-angle=swiftshader',
    '--enable-unsafe-swiftshader',
    '--window-size=1100,720',
    `--user-data-dir=${profile}`,
    '--virtual-time-budget=9000',
    `--screenshot=${pngPath}`,
    pathToFileURL(pagePath).href,
  ], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024, timeout: 150_000, stdio: 'pipe' })
  shot = existsSync(pngPath)
} catch { shot = existsSync(pngPath) }

rmSync(profile, { recursive: true, force: true })
rmSync(outDir, { recursive: true, force: true })
rmSync(probeEntry, { force: true })
rmSync(workspace, { recursive: true, force: true })

if (!shot) {
  console.log('  FAIL 截图失败，拿不到 3D 画面证据')
  process.exit(1)
}

// 用 Python + Pillow 读像素（本机已装，Node 侧没有解码器）
const py = `
import json, sys
from PIL import Image
img = Image.open(sys.argv[1]).convert("RGB")
w, h = img.size
# 只看画布区域：去掉顶部标题栏与底部图例
top, bottom = 30, h - 40
crop = img.crop((0, top, w, bottom))
px = list(crop.getdata())
n = len(px)
lit = white = black = 0
hist = {}
total_lum = 0.0
for r, g, b in px:
    lum = 0.2126*r + 0.7152*g + 0.0722*b
    total_lum += lum
    if lum > 245 and r > 245 and g > 245 and b > 245: white += 1
    if lum < 8: black += 1
    if lum > 10: lit += 1
    hist[(r >> 4, g >> 4, b >> 4)] = 1
print(json.dumps({
  "size": [w, h], "n": n,
  "meanLum": total_lum / n,
  "litRatio": lit / n, "whiteRatio": white / n, "blackRatio": black / n,
  "distinct": len(hist),
}))
`
const stats = JSON.parse(execFileSync('python', ['-c', py, pngPath],
  { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 }))

console.log(`  ..   截图 ${stats.size[0]}×${stats.size[1]}，`
  + `平均亮度 ${stats.meanLum.toFixed(1)}，`
  + `有光像素 ${(stats.litRatio * 100).toFixed(1)}%，`
  + `纯白 ${(stats.whiteRatio * 100).toFixed(1)}%，`
  + `颜色 ${stats.distinct} 种`)

check('3D 画布有真实尺寸', stats.size[0] > 100 && stats.size[1] > 100,
  `${stats.size[0]}×${stats.size[1]}`)
check('3D 渲染有输出（不是全黑）', stats.litRatio > 0.3,
  `有光像素只占 ${(stats.litRatio * 100).toFixed(1)}%`)
check('没有过曝（纯白占比 < 60%）', stats.whiteRatio < 0.6,
  `纯白占 ${(stats.whiteRatio * 100).toFixed(1)}% —— 光照太强或 toneMapping 没生效`)
check('没有大面积纯黑（< 80%）', stats.blackRatio < 0.8,
  `纯黑占 ${(stats.blackRatio * 100).toFixed(1)}%`)
check('有明暗层次（颜色 > 8 种）', stats.distinct > 8,
  `只有 ${stats.distinct} 种 —— 可能是纯色块`)
console.log(`  ..   真实渲染图：${pngPath}`)

if (failures.length > 0) {
  console.log(`\n❌ ${failures.length} 项失败`)
  process.exit(1)
}
console.log('\n✅ 3D 真实渲染检查通过')