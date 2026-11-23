/**
 * 布局与外壳的运行时冒烟（F2/F3 验收）。
 *
 *   node scripts/check_layout_runtime.mjs
 *
 * `check_client_runtime.mjs` 证明"能注册"；这里证明"壳的行为符合方案"：
 *
 *   1. **把手存在且可访问**：split 布局下 3 根 role=separator（2D|3D、
 *      检查器、底部），单视图下 2 根（分栏把手随布局消失，不该留一根
 *      拖不动的空缝）；
 *   2. **隐藏即停**：layout='2d' 时 3D pane **不在 DOM 里**（组件卸载，
 *      rAF 随 effect cleanup 停止）——这是把"display:none 还跑循环"
 *      的教训变成断言；
 *   3. **一个状态两个视图**：layout 变化只切换挂载，不重置选中；
 *   4. **视图组是 segmented + aria-pressed**（三态恰好一个按下）；
 *   5. **状态灯**：role=status 的文案随 errors/openDecisions 变化；
 *   6. **令牌进入 bundle**：spacing/z-index/focus-visible/reduced-motion。
 *
 * store 是模块级单例；SSR 下 useEffect 不跑，尺寸读 localStorage 失败并
 * 静默回退到默认值（这正是"存储被禁不崩"的那条纪律，顺带被验证）。
 */

import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

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

// --- 伪宿主（与 check_client_runtime 同一协议）---------------------------

globalThis.window = globalThis
globalThis.document = undefined
let clientModule = null
/**
 * 假 localStorage。store 模块**顶层**读一次（INITIAL），所以它必须在 bundle
 * 执行前就位；`entries` 可预置以验证读侧，`throwOnAccess` 验证"存储被禁
 * 就静默用默认"那条纪律（Velxio EditorPage.tsx:207 的同款处理）。
 */
const storage = { entries: new Map(), throwOnAccess: false }
globalThis.localStorage = {
  getItem(key) {
    if (storage.throwOnAccess) throw new Error('SecurityError: storage blocked')
    return storage.entries.has(key) ? storage.entries.get(key) : null
  },
  setItem(key, value) {
    if (storage.throwOnAccess) throw new Error('SecurityError: storage blocked')
    storage.entries.set(key, String(value))
  },
  removeItem(key) { storage.entries.delete(key) },
}
const clientCode = readFileSync(bundlePath, 'utf8')
globalThis.__ModuleLoader__ = {
  load: ({ factory }) => { clientModule = factory((name) => require(name)) },
}
new Function('require', 'window', clientCode)(
  (name) => require(name), globalThis)

const registrations = { views: [], slots: [] }
/** 原生选择器：记录被调用次数与"用户选了什么"，用来证明接线真的通了。 */
const pickerCalls = []
const services = {
  connection: { rpc: { call: async () => ({ ok: true, value: {} }) } },
  sessions: {
    binding: () => ({
      session: {
        prompt: () => {},
        // DSH 当前工作目录投影（chat 包同款路径）
        projections: { faceOf: () => ({ getSnapshot: () => 'workspace' }) },
      },
    }),
  },
  // 目录选择的**客户端**入口。真实浏览器上下文里没有 `directoryPicker`
  // 服务（那是宿主 node 侧的）——这里故意**不挂**它，只挂 ui-workspace
  // 客户端提供的 `uiWorkspace.pickDirectory`。上一版接错层时，这个脚本
  // 因为挂了宿主服务而全绿、真环境却报错："绿着测空气"的又一例。
  uiWorkspace: {
    pickDirectory: async () => {
      pickerCalls.push({ via: 'uiWorkspace' })
      return 'workspace/hw-created-ws'
    },
  },
  uiConversation: { views: { register: (d) => { registrations.views.push(d); return () => {} } } },
}
const ctx = {
  effect: (fn) => { const d = fn(); return () => { if (typeof d === 'function') d() } },
  get: (name) => services[name],
  slots: {
    inject: (_slot, register) => { register() },
    register: (definition, component) => { registrations.slots.push({ definition, component }); return () => {} },
  },
}
clientModule.apply(ctx)
const slot = registrations.slots[0]
const injected = slot.definition.inject('session-1')

// --- 假数据：一个够渲染的快照 --------------------------------------------

function fakeSnapshot(projectId, { errors = 0, openDecisions = 0 } = {}) {
  // findings 由参数**生成**：1 条 warning（估算件）+ errors 条 error。
  // 计数与参数不一致会让"状态灯"的断言测到假东西。
  const findings = [
    { check: 'space.overlap', ok: errors === 0, severity: 'error',
      refs: errors > 0 ? ['led1', 'r1'] : [], measured: 0, limit: 0,
      message: errors > 0 ? 'led1 与 r1 实体重叠' : '没有实体重叠', fix: {} },
    { check: 'consistency.provenance', ok: false, severity: 'warning', refs: ['r1'],
      measured: 1, limit: 0, message: 'r1 尺寸是估算值', fix: {} },
  ]
  for (let index = 1; index < errors; index += 1) {
    findings.push({ check: `space.bounds#${index}`, ok: false, severity: 'error',
      refs: ['r1'], measured: 1, limit: 0, message: `r1 足迹超出板轮廓 #${index}`, fix: {} })
  }
  const parts = [
    { ref: 'led1', kind: 'wokwi-led', label: 'LED', role: 'component',
      board_id: 'arduino-uno', x_px: 52.98 * 3.7751, y_px: 20, w_px: 18.88, h_px: 18.88, rotation_z: 0,
      physical_source: 'datasheet', size_mm: { w: 5, h: 5, d: 8.6 }, z_mm: 0 },
    { ref: 'r1', kind: 'wokwi-resistor', label: 'R', role: 'component',
      board_id: 'arduino-uno', x_px: 39.8, y_px: 40, w_px: 23.8, h_px: 8.7, rotation_z: 0,
      physical_source: 'estimated', size_mm: { w: 6.3, h: 2.3, d: 2.3 }, z_mm: 0 },
  ]
  return {
    status: 'ok',
    project: { id: projectId, path: '/tmp/' + projectId, has_design: true,
      artifacts: { 'design.json': true }, title: '冒烟', board: 'arduino-uno',
      board_name: 'Arduino Uno', parts: 2, nets: 1 },
    design: {
      schema: 'hw_velxio.design/1', units: 'mm', id: projectId, title: '冒烟',
      board: { kind: 'arduino-uno', name: 'Arduino Uno',
        outline: [[0, 0], [72.58, 0], [72.58, 53.34], [0, 53.34]],
        size_mm: { w: 72.58, h: 53.34 }, thickness_mm: 1.6,
        mount_holes: [{ x_mm: 13.97, y_mm: 2.54, diameter_mm: 3.2, source: 'datasheet' }],
        px_per_mm: 3.7751 },
      parts: [
        { ref: 'led1', kind: 'wokwi-led', label: 'LED', role: 'component',
          size_mm: { w: 5, h: 5, d: 8.6 }, physical_source: 'datasheet',
          placement: { x_mm: 52.98, y_mm: 32.44, z_mm: 0, rotation_z: 0, mount: 'board' },
          electrical: { legacy_px: { x: 200, y: 60 } } },
        { ref: 'r1', kind: 'wokwi-resistor', label: '220', role: 'component',
          size_mm: { w: 6.3, h: 2.3, d: 2.3 }, physical_source: 'estimated',
          placement: { x_mm: 10.5, y_mm: 20, z_mm: 0, rotation_z: 90, mount: 'board' },
          electrical: { legacy_px: { x: 150, y: 120 }, power_w: 0.125 } },
      ],
      nets: [{ net: 'D13', pins: [{ ref: 'board', pin: '13' }, { ref: 'r1', pin: '1' }] }],
      wires: [], meta: {}, warnings: [],
    },
    summary: { id: projectId, title: '冒烟',
      board: { kind: 'arduino-uno', name: 'Arduino Uno', size_mm: { w: 72.58, h: 53.34 }, thickness_mm: 1.6 },
      parts: 2, parts_by_source: { datasheet: 1, estimated: 1 }, nets: 1, wires: 0,
      enclosure: false, estimated_parts: ['r1'] },
    problems: [],
    projection2d: {
      units: 'mm', px_per_mm: 3.7751,
      // 拼板（单板时 = 那一块板）。字段与 `projection.to_2d()` 一致；
      // 少了它组件会在读 `panel.board_count` 时抛错 —— 这个冒烟检查
      // 抓到的正是"载荷结构变了但测试夹具没跟上"。
      panel: { size_mm: { w: 72.58, h: 53.34 }, gap_mm: 8, board_count: 1 },
      board: { id: 'arduino-uno', kind: 'arduino-uno', name: 'Arduino Uno',
        w_px: 274, h_px: 202,
        thickness_mm: 1.6, px_per_mm: 3.7751, size_mm: { w: 72.58, h: 53.34 },
        outline_px: [[0, 0], [274, 0], [274, 202], [0, 202]],
        x_px: 0, y_px: 0, parts: parts.length },
      boards: [{ id: 'arduino-uno', kind: 'arduino-uno', name: 'Arduino Uno',
        x_mm: 0, y_mm: 0, w_mm: 72.58, h_mm: 53.34,
        w_px: 274, h_px: 202, x_px: 0, y_px: 0,
        thickness_mm: 1.6, size_mm: { w: 72.58, h: 53.34 },
        outline_px: [[0, 0], [274, 0], [274, 202], [0, 202]],
        parts: parts.length }],
      parts, wires: [{ net: 'D13', layer: 1, width_px: 0.94, routed: true,
        path_px: [[10, 10], [20, 10], [20, 40]] }],
      ratlines: [],
      cross_board_ratlines_skipped: 0,
      mount_holes: [{ x_px: 52.7, y_px: 192.4, d_px: 12.1, source: 'datasheet' }],
      estimated_parts: ['r1'], warnings: [],
    },
    scene3d: {
      units: 'mm',
      panel: { size_mm: { w: 72.58, h: 53.34 }, gap_mm: 8, board_count: 1 },
      board: { id: 'arduino-uno', kind: 'arduino-uno', name: 'Arduino Uno',
        outline: [[0, 0], [72.58, 0], [72.58, 53.34], [0, 53.34]],
        size_mm: { w: 72.58, h: 53.34 }, thickness_mm: 1.6 },
      boards: [{ id: 'arduino-uno', kind: 'arduino-uno', name: 'Arduino Uno',
        x_mm: 0, y_mm: 0,
        outline: [[0, 0], [72.58, 0], [72.58, 53.34], [0, 53.34]],
        size_mm: { w: 72.58, h: 53.34 }, thickness_mm: 1.6,
        parts: 2 }],
      parts: [
        { ref: 'led1', label: 'LED', kind: 'wokwi-led', role: 'component',
          board_id: 'arduino-uno',
          bbox_mm: [52.98, 32.44, 0, 57.98, 37.44, 8.6], rotation_z: 0,
          size_mm: { w: 5, h: 5, d: 8.6 }, side: 'top', pads: [],
          physical_source: 'datasheet', mount: 'board' },
        { ref: 'r1', label: '220', kind: 'wokwi-resistor', role: 'component',
          board_id: 'arduino-uno',
          bbox_mm: [10.5, 20, 0, 16.8, 22.3, 2.3], rotation_z: 90,
          size_mm: { w: 6.3, h: 2.3, d: 2.3 }, side: 'top', pads: [],
          physical_source: 'estimated', mount: 'board' },
      ],
      wires: [], ratlines: [],
      mount_holes: [{ x_mm: 13.97, y_mm: 2.54, diameter_mm: 3.2, source: 'datasheet' }],
      enclosure: null,
    },
    checks: {
      ok: errors === 0, pitch_mm: 0.5,
      space: { dims_mm: [82.58, 63.34, 12], dims_voxels: [166, 127, 24],
        occupied_voxels: 1234, placements: 2 },
      findings,
      not_verified: ['statics：非地面轮式安装，未运行'],
      unsized: [], estimated: ['r1'],
      summary: { total: findings.length, failed: findings.filter((f) => !f.ok).length,
        errors, warnings: 1, refs_with_issues: ['r1'] },
    },
    budget: {
      ok: true, status: 'ok', confidence: 'partial', currencies: ['power_w'],
      resolution_order: ['safety', 'rating', 'target', 'cost', 'area'],
      limits: { power_w: 5, thermal_c: 100, emc_dbuv: 40, timing_ns: 1000, cost_usd: 30 },
      by_currency: { power_w: { used: 0.125, limit: 5, ratio: 0.025, over: false, default_limit: true } },
      violations: [], claims: [{ module: 'passives', currency: 'power_w', amount: 0.125, ref: 'r1', ratio: 0.025 }],
      heatmap: { r1: { power_w: 0.125 } },
      coverage: { claimed_parts: 1, total_parts: 2, uncovered: ['led1'], note: '没有 claim 的元件不参与等式' },
      nodes: {},
    },
    deferred: {
      deferred: [], locked: [{ name: 'mcu_family', value: 'Uno', at: 'L1' }],
      open_irreversible: openDecisions > 0
        ? ['package_family', 'connector_family'].slice(0, openDecisions) : [],
      deliverable: openDecisions === 0, reasons: {}, message: 'ok',
    },
    crosscheck: { ok: true, px_per_mm: 3.7751, mismatches: [],
      not_verified: ['r1: 来源是 estimated'], summary: { mismatches: 0, errors: 0, warnings: 0, unverified: 1, refs: [] },
      note: '未验证≠矛盾' },
    evidence: { design_hash: '0123456789abcdef',
      trace: { events: 3, chain_ok: true, content_ok: true, tampered_seqs: [], head: 'abc', last: null },
      gate: { deliverable: openDecisions === 0, open_irreversible: [], reasons: {}, message: 'ok' },
      checked_at: '2026-11-14T00:00:00+00:00' },
  }
}

const fakeBootstrap = (projectId) => ({
  status: 'ok',
  boards: [{ kind: 'arduino-uno', name: 'Arduino Uno',
    size_mm: { w: 72.58, h: 53.34, d: 1.6 }, px: { w: 274, h: 202 },
    px_per_mm: 3.7751, thickness_mm: 1.6, source: 'datasheet', mount_holes: 4 }],
  catalog: [], formats: [{ ext: '.kicad_pcb', kind: 'kicad-pcb', reads: [], missing: [] }],
  projects: [{ id: projectId, path: '/tmp', has_design: true, artifacts: { 'design.json': true },
    title: '冒烟', board: 'arduino-uno', board_name: 'Arduino Uno', parts: 2, nets: 1 }],
  hwforge: { root: 'D:/x', available: true, capabilities: 14 },
  velxio: { url: 'http://127.0.0.1:3080', reachable: false },
  python: { executable: 'python', root: 'workspace' },
})

/**
 * 一个**可变**的 fake face：`snapshot()` 返回"当前"数据。渲染前调
 * `store.open(id)` —— 与用户在工程下拉里选同一个工程、面板刷新走的是
 * **同一条真实路径**。不给 store 加测试专用 API（那等于往产品里放钩子）。
 */
const impl = { current: fakeSnapshot('001-blink'), projects: null }
impl.projects = [impl.current.project]
const face = {
  bootstrap: async () => ({
    ...fakeBootstrap(impl.current.project.id),
    projects: impl.projects,
  }),
  snapshot: async () => impl.current,
  projects: async () => impl.projects,
  hint: async () => ({ status: 'ok', seq: 0, at: 0 }),
  // 工作区三件套：真宿主由 design_cli 提供，这里给固定的可信答案。
  workspace: async () => ({
    status: 'ok', workspace: 'workspace/hw-ws', exists: true,
    writable: true, source: 'config', default: 'C:/Users/x/.dsh/hw_velxio/workspace',
    config_path: 'C:/Users/x/.dsh/hw_velxio.json',
  }),
  setWorkspace: async (path) => ({
    status: 'ok', workspace: path, source: 'config', writable: true,
  }),
  createWorkspace: async (parent, name) => ({
    status: 'ok', workspace: `${parent}/${name}`, source: 'config',
    writable: true, created: true,
  }),
}

// --- 渲染 -----------------------------------------------------------------

const React = require('react')
const { renderToString } = require('react-dom/server')

/**
 * store 从 **bundle 闭包的导出**里取（`factory(require)` 的返回值）。
 * 不用 `import(pathToFileURL(dist/client.js))`：那条路会让闭包再执行一次，
 * 得到**第二个** store 单例，而组件用的是第一个——断言会全绿但测的是空气。
 */
const store = clientModule.store
check('bundle 导出 store（冒烟入口）', typeof store?.setLayout === 'function')

async function renderOnce(snapshot, layout) {
  if (store === undefined) return null
  impl.current = snapshot
  await store.init(face)                       // 幂等：face 已就位即返回
  await store.open(snapshot.project.id)        // 真实刷新路径
  if (layout !== undefined) store.setLayout(layout)
  return renderToString(React.createElement(slot.component, { ...injected, sessionId: 's1' }))
}
const htmlSplit = await renderOnce(fakeSnapshot('001-blink'), 'split')
if (htmlSplit === null) {
  console.log('  FAIL 无法从 bundle 取得 store（导形变化？）')
  failures.push('store-access')
} else {
  console.log('[A] split 布局')
  const separators = (htmlSplit.match(/role="separator"/g) ?? []).length
  check('3 根把手（分栏/检查器/底部）', separators === 3, `实际 ${separators}`)
  check('2D pane 在', htmlSplit.includes('2D 电路'))
  check('3D pane 在', htmlSplit.includes('3D 装配'))
  const pressed = (htmlSplit.match(/class="hwv-seg-btn" aria-pressed="true"/g) ?? []).length
  check('视图组恰好一个按下', pressed === 1, `实际 ${pressed}`)
  check('segmented 三态齐备', (htmlSplit.match(/hwv-seg-btn/g) ?? []).length >= 3)
  check('状态灯存在且带语义', htmlSplit.includes('role="status"'))
  // 工作区入口：有工程时它也在工具栏（不是只在空态出现），且是**一个**
  // 按钮——设置块与入口分离，入口全局只有这一个。
  // P1 IA 减法：底部从 8 个平级页签收敛成 3 个分组，组内再分段。
  const groupTabs = (htmlSplit.match(/class="hwv-tab(?: |")/g) ?? []).length
  check('底部是 3 个分组页签（不是 8 个平级页签）', groupTabs === 3, `实际 ${groupTabs}`)
  check('分组页签用 aria-pressed（不是 tab 语义）',
    /class="hwv-tab(?: active)?" aria-pressed="/.test(htmlSplit))
  const subTabs = (htmlSplit.match(/class="hwv-subtab/g) ?? []).length
  check('组内有分区分段控件（≥2 个）', subTabs >= 2, `实际 ${subTabs}`)

  check('工具栏是工作区的唯一入口',
    (htmlSplit.match(/hwv-ws-toggle/g) ?? []).length === 1
    && /aria-expanded="false"/.test(htmlSplit))

  check('图例走类不走内联样式',
    htmlSplit.includes('class="src-datasheet"')
    && !/hwv-legend[^>]*style=/.test(htmlSplit))

  console.log('[B] 隐藏即停：layout=2d')
  const html2d = await renderOnce(fakeSnapshot('001-blink'), '2d')
  check('3D pane 不在 DOM（组件已卸载）', !html2d.includes('3D 装配'))
  check('2D pane 仍在', html2d.includes('2D 电路'))
  const sep2d = (html2d.match(/role="separator"/g) ?? []).length
  check('分栏把手随布局退场（2 根）', sep2d === 2, `实际 ${sep2d}`)

  console.log('[C] layout=3d')
  const html3d = await renderOnce(fakeSnapshot('001-blink'), '3d')
  check('2D pane 不在 DOM', !html3d.includes('2D 电路'))
  check('3D pane 在', html3d.includes('3D 装配'))

  console.log('[D] 状态灯随严重度')
  const htmlErr = await renderOnce(
    fakeSnapshot('001-blink', { errors: 2, openDecisions: 2 }), 'split')
  check('灯语含错误数', htmlErr.includes('2 个错误'))
  // 导出按钮：openDecisions>0 时必须 disabled，且 title 说明被闸门拦的原因
  const exportBtn = /<button[^>]*disabled[^>]*title="([^"]*)"[^>]*>导出/.exec(htmlErr)
    ?? /<button[^>]*title="([^"]*)"[^>]*disabled[^>]*>导出/.exec(htmlErr)
  check('导出按钮在闸门未闭合时禁用并说明原因',
    exportBtn !== null && exportBtn[1].includes('闸门'),
    exportBtn === null ? '没匹配到带 title 的禁用导出按钮' : exportBtn[0])
  // 同一份 HTML 里，检查器"加元件"说明仍在（确认主体没被横幅挤掉）
  check('严重度下主体仍完整', htmlErr.includes('加一个元件') || htmlErr.includes('3D 装配'))

  console.log('[F] 持久化（localStorage 读写两侧 + 存储被禁）')
  // 此刻只有 layout 被写过（[A]-[D] 切过视图）；尺寸要显式走 setter 才有落库。
  store.setSplitPct(60)
  check('写侧：layout 与尺寸都落存储',
    storage.entries.get('dsh-hwv.layout') === 'split'
    && storage.entries.get('dsh-hwv.splitPct') === '60',
    JSON.stringify([...storage.entries.entries()]))
  store.setSplitPct(999)
  store.setInspectorWidth(-50)
  check('setter 自己 clamp，不把越界值写进存储',
    storage.entries.get('dsh-hwv.splitPct') === '80'
    && storage.entries.get('dsh-hwv.inspectorWidth') === '200',
    `${storage.entries.get('dsh-hwv.splitPct')} / ${storage.entries.get('dsh-hwv.inspectorWidth')}`)
  store.setBottomHeight(321)
  check('底部高度往返', store.getSnapshot().bottomHeight === 321)

  console.log('[H] 空态结构（开始卡 + 工作区设置块）与工作区能力')
  {
    // ① face 上必须真的挂着 pickDirectory，且它走的是宿主 seam。
    const picked = await injected.face.pickDirectory()
    check('pickDirectory 走客户端入口（uiWorkspace，不是宿主服务）',
      pickerCalls.length === 1 && pickerCalls[0]?.via === 'uiWorkspace',
      JSON.stringify(pickerCalls))
    check('pickDirectory 返回选中的绝对路径',
      picked === 'workspace/hw-created-ws', String(picked))

    // ①b 桌面壳优先：Electron 窗口里 preload 的 __DSH_DIRECTORY_PICKER__
    //     直达主进程原生对话框，必须盖过 RPC 路径（宿主自己的 native 客户端
    //     就是这个顺序）。入口是**点击时才解析**的，所以这里改全局即刻生效，
    //     不需要重建闭包。
    globalThis.__DSH_DIRECTORY_PICKER__ = {
      pick: async () => { pickerCalls.push({ via: 'desktop' }); return 'D:/desktop-picked' },
    }
    const viaDesktop = await injected.face.pickDirectory()
    check('桌面壳 seam 优先于 uiWorkspace',
      pickerCalls.length === 2 && pickerCalls[1]?.via === 'desktop'
      && viaDesktop === 'D:/desktop-picked', JSON.stringify(pickerCalls))
    delete globalThis.__DSH_DIRECTORY_PICKER__

    // ①c 两个入口都没有：报"查过的是哪两个名字"，不再含糊地说
    //     "宿主没有提供能力"（那句话正是上一版误导人的地方）。
    const savedPicker = services.uiWorkspace
    services.uiWorkspace = undefined
    let seamError = ''
    try { await injected.face.pickDirectory() } catch (e) { seamError = String(e.message ?? e) }
    services.uiWorkspace = savedPicker
    check('两个入口都缺时错误里点名两个 seam',
      seamError.includes('__DSH_DIRECTORY_PICKER__') && seamError.includes('uiWorkspace'),
      seamError)

    // ② cwd 注入：会话投影里的 workspace 应到达 store。
    check('DSH 当前工作目录注入到 store', store.getSnapshot().dshCwd === 'workspace',
      String(store.getSnapshot().dshCwd))

    // ③ 空态渲染：**真的没有工程**（工程列表为空）。
    //    以前这里用"快照读失败"来冒充空态——那正是产品里那个自相矛盾的
    //    来源（下拉有工程、正文说没有）。两条路现在分开测。
    const broken = { ...fakeSnapshot('001-blink'), status: 'failed',
      findings: ['快照读取失败：签名不匹配'], project: fakeSnapshot('001-blink').project }
    const savedProjects = impl.projects
    impl.projects = []
    await store.reloadProjects()          // 真实路径：列表为空
    const emptyHtml = await renderOnce(broken, 'split')
    // 合并后的结构：一张「开始」卡（两页签）+ 一个收起的工作区设置块。
    // 断言的是**去重后的形状**，不是文案——文案会改，形状是这次修改的目的。
    check('空态只有一个"开始"区', (emptyHtml.match(/aria-label="开始"/g) ?? []).length === 1)
    check('开始区是三页签（新建预设 / 从文件导入 / 说需求给 AI）',
      /role="tablist"/.test(emptyHtml)
      && emptyHtml.includes('新建预设板卡') && emptyHtml.includes('从文件导入')
      && emptyHtml.includes('说需求给 AI'))
    check('同一时刻只有一个 tab 被选中',
      (emptyHtml.match(/aria-selected="true"/g) ?? []).length === 1)
    // 旧的四张并列卡必须消失：它们是"看起来像四个功能"的直接来源。
    check('旧的"选择文件夹作为工作区"卡片已并入设置块',
      !emptyHtml.includes('选择文件夹作为工作区'))
    check('旧的"在当前工作区新建工程"卡片已并入开始卡',
      !emptyHtml.includes('在当前工作区新建工程'))
    // 工作区默认收起：设置不该在空态抢主操作的权重。
    check('工作区设置默认收起（不渲染）', !emptyHtml.includes('aria-label="工作区设置"'))
    check('收起的设置仍留一个入口', emptyHtml.includes('aria-expanded="false"'))
    // 路径只出现一次：空态抬头不再复述"工程将建在 …"。两处都写正是这次
    // 要修的重复。比对**可见文本**（剥掉 title 属性——那是同一个元素的无障碍
    // 补充说明，不是第二处的重复显示）。
    const visible = emptyHtml.replace(/title="[^"]*"/g, '')
    check('工作区路径只出现一次（空态不复述）',
      (visible.match(/D:\/think-play\/hw-ws/g) ?? []).length === 1,
      `出现 ${(visible.match(/D:\/think-play\/hw-ws/g) ?? []).length} 次`)

    // ④ 展开后：设置块出现，且入口只有一个（工具栏那个按钮）。
    store.setWorkspaceOpen(true)
    const openHtml = await renderOnce(broken, 'split')
    check('展开后渲染工作区设置块', openHtml.includes('aria-label="工作区设置"'))
    check('设置块里两个入口都在（浏览 / 新建）',
      openHtml.includes('浏览文件夹…') && openHtml.includes('新建'))
    check('设置块只有一份（不会同时出现在空态里）',
      (openHtml.match(/aria-label="工作区设置"/g) ?? []).length === 1)
    // 收起动作只有一个入口：工具栏那个按钮（展开后它变成 aria-expanded=true）。
    // 块内再来一个「收起」就是同一动作的第二入口——上一版四张卡的老毛病。
    check('收起只有一个入口（块内不重复）',
      (openHtml.match(/aria-expanded="true"/g) ?? []).length === 1
      && !openHtml.includes('收起</button>'),
      `aria-expanded=true 出现 ${(openHtml.match(/aria-expanded="true"/g) ?? []).length} 次`)
    store.setWorkspaceOpen(false)

    // ⑤ 两个页签的**分支体**都要在产物里。renderToString 点不了按钮，所以
    //    这里退一步查产物：两个分支的独有文案都在 bundle 里，说明"从文件导入"
    //    不是只有页签没有内容（页签在、正文没了的退化，类型检查看不出来）。
    const bundleText = readFileSync(bundlePath, 'utf8')
    check('新建分支有正文（板卡预设 / 新建工程）',
      bundleText.includes('板卡预设') && bundleText.includes('用板卡预设建一个空工程'))
    check('导入分支有正文（源文件 / BOM / 相对路径提示）',
      bundleText.includes('源文件') && bundleText.includes('BOM（可选）')
      && bundleText.includes('相对路径相对工作区'))

    // ③b 有工程但读不到：**不能**画成"还没有工程"。这是真实截图里的
    //     自相矛盾——下拉选中 示例-无人机，正文说还没有工程。
    impl.projects = savedProjects
    await store.reloadProjects()
    const failedWithProject = await renderOnce(broken, 'split')
    check('读失败时不说"还没有工程"',
      !failedWithProject.includes('还没有工程'),
      failedWithProject.includes('还没有工程') ? '仍渲染了空态' : '')
    check('读失败时点名是哪个工程读不到',
      // SSR 会在文本插值两侧插 `<!-- -->`，所以按"两段都在"判定而不是整句匹配。
      failedWithProject.includes('读不到工程') && failedWithProject.includes('001-blink'))
    check('读失败时给重试入口', failedWithProject.includes('重试'))
    // 同一句话不该在屏幕上出现两遍：主区已经显示 error，顶部横幅让位。
    check('读失败原因只出现一次（横幅让位给主区）',
      (failedWithProject.match(/读不到</g) ?? []).length
      + (failedWithProject.match(/读不到(?!工程)/g) ?? []).length <= 1,
      `出现 ${(failedWithProject.match(/读不到/g) ?? []).length} 次`)

    // 恢复一个能用的快照，后续段落照常跑。
    await renderOnce(fakeSnapshot('001-blink'), 'split')
  }

  console.log('[I] AI 协作：阶段 / 空集通过 / 对话框')
  {
    // 阶段与 vacuous 都来自**确定性推导**，这里验证它们真的到达了界面。
    const snapWithPhase = {
      ...fakeSnapshot('001-blink'),
      phase: { phase: 'empty', reason: '没有任何元件：所有物理检查都会因空集而通过（假绿灯）',
        veto: ['没有一个元件（parts 为空）'],
        counts: { parts: 0, nets: 0, unsized: 0, estimated: 0, zero_height: 0 } },
      checks: {
        ...fakeSnapshot('001-blink').checks,
        vacuous: ['space.overlap', 'space.height', 'mech.mount_holes'],
      },
    }
    const phaseHtml = await renderOnce(snapWithPhase, 'split')
    check('阶段徽章显示人话（空工程）', phaseHtml.includes('空工程'))
    check('阶段徽章走 phase-empty 类而不是绿',
      phaseHtml.includes('phase-empty'))
    check('阶段徽章 title 说明为什么（不是只给形容词）',
      /title="[^"]*假绿灯/.test(phaseHtml))

    // ── AI 面板：展开后三段动作都在 ──────────────────────────────────
    // 组件内部 state 用 renderToString 点不到，所以直接断言产物里的文案：
    // 三个动作与输入框的 placeholder 都在 bundle 里 = 面板真的有内容，
    // 不是"只有按钮没有动作"的空壳。
    const bundleForAi = readFileSync(bundlePath, 'utf8')
    check('AI 面板有「初审工作区」',
      bundleForAi.includes('初审工作区'))
    check('AI 面板有「适配当前工程」',
      bundleForAi.includes('适配当前工程'))
    check('AI 面板有「评审当前工程」',
      bundleForAi.includes('评审当前工程'))
    check('AI 面板有需求输入框', bundleForAi.includes('描述你想改什么'))
    check('初审动作要求工具生成事实段（不是让 AI 凭空写）',
      bundleForAi.includes('hw_survey') && bundleForAi.includes('不要改它'))
    check('适配动作禁止编造 datasheet 数值',
      bundleForAi.includes('不要编造 datasheet'))
    check('适配动作要求走 hw_put',
      bundleForAi.includes('必须走 hw_put'))
    // SSR 会在文本插值两侧插 `<!-- -->`，所以断言"两段都在"而不是整句匹配。
    check('空集通过单独计数',
      /3(\s|<!--.*?-->)+项空集通过/s.test(phaseHtml),
      (phaseHtml.match(/[\d]+(\s|<!--.*?-->)*项空集通过/s) ?? ['(没匹配到)'])[0])
    check('空集通过的检查列在 title 里（可悬停看到具体是哪几条）',
      /title="[^"]*space\.overlap/.test(phaseHtml))

    // AI 从工具栏按钮改成**右侧抽屉**：入口是一个小 `AI` 按钮（带 aria-expanded），
    // 面板打开时是 role=dialog 的抽屉，不是一个内联的行。
    check('工具栏有「AI」抽屉入口', phaseHtml.includes('hwv-ai-toggle'))
    check('AI 抽屉默认收起（不渲染 dialog）',
      !phaseHtml.includes('role="dialog"'))
    check('抽屉入口带 aria-expanded=false',
      /aria-expanded="false"/.test(phaseHtml))

    // 恢复一个能用的快照，后续段落照常跑。
    await renderOnce(fakeSnapshot('001-blink'), 'split')
  }


  const cssOnly = htmlSplit.includes('hwv-')
  const client = readFileSync(bundlePath, 'utf8')
  check('spacing 令牌', client.includes('--hwv-space-1'))
  check('z-index 分级令牌', client.includes('--hwv-z-actionbar'))
  check('焦点环规则', client.includes(':focus-visible'))
  check('reduced-motion', client.includes('prefers-reduced-motion'))
  check('把手样式', client.includes('.hwv-handle.vertical'))
  // bundle 里 CSS 是 JS 字符串：双引号可能被转义成 \"。两种都认。
  check('segmented 按下态',
    client.includes('.hwv-seg-btn[aria-pressed="true"]')
    || client.includes('.hwv-seg-btn[aria-pressed=\\"true\\"]'))
  check('深色命令式调色板仍在', client.includes('body[data-ds-dark-theme] .hwv-root'))
  // P2/P3 静态断言：字阶只有 11/12/13/15 四档，且不再出现 10px。
  check('字号阶梯已令牌化（--hwv-fs-*）', client.includes('--hwv-fs-caption')
    && client.includes('--hwv-fs-body') && client.includes('--hwv-fs-title'))
  check('没有 10px 字号（低于 11px 在 1x DPI 上不可读）',
    !/font-size: 10px/.test(client))
  // P2：唯一强调色是紫（--hwv-est），选择环与估算共用它。
  check('选择色引用唯一强调色 --hwv-est',
    /--hwv-p-sel: var\(--hwv-est\)/.test(client))
  check('估算色引用唯一强调色 --hwv-est',
    /--hwv-p-est-mark: var\(--hwv-est\)/.test(client))
  check('渲染含 hwv- 类名', cssOnly)
  // P1：板级汇总 + 三态计数（缺数据要看得见，不能只显示"通过/失败"）。
  check('板级汇总条在 DOM', htmlSplit.includes('hwv-boardstrip')
    && htmlSplit.includes('hwv-board-chip'))
  check('三态计数含「未验证」', htmlSplit.includes('未验证'))
  check('板 chip 带件数', htmlSplit.includes('hwv-board-parts'))
}

// store.init 起了 2.5s 轮询定时器（浏览器里它负责随工具调用刷新面板）。
// 脚本用完必须释放，否则事件循环永不退出——这与组件卸载的路径一致。
store?.dispose()

console.log('[G] 读侧与降级（重跑闭包 = 下一次页面加载）')
{
  // 上面 [F] 已把值写进 storage.entries；重建闭包，INITIAL 必须读回来。
  // 副作用：每次重建都会 import 一份 three，控制台出现
  // "Multiple instances of Three.js" —— 那是**本脚本**的产物（真宿主只装载
  // 一次），不是插件缺陷；这里需要的是同一个 bundle 的第二份干净 store。
  storage.entries.set('dsh-hwv.layout', 'bogus')   // 坏枚举：手改存储/旧版本残留
  const reloaded = []
  const loader = { load: ({ factory }) => { reloaded.push(factory((name) => require(name))) } }
  new Function('require', 'window', clientCode)((name) => require(name), { ...globalThis, __ModuleLoader__: loader })
  const store2 = reloaded[0]?.store
  if (store2 !== undefined) {
    check('尺寸从存储读回（刷新后保持）',
      store2.getSnapshot().splitPct === 80
      && store2.getSnapshot().inspectorWidth === 200
      && store2.getSnapshot().bottomHeight === 321,
      JSON.stringify(store2.getSnapshot()))
    check('坏 layout 退回默认而不是污染布局', store2.getSnapshot().layout === 'split')
    store2.dispose()
  } else {
    check('第二次闭包能构造', false)
  }
}
{
  // 存储被禁：读与写都静默降级，绝不抛（Velxio EditorPage.tsx:207 同纪律）。
  storage.throwOnAccess = true
  storage.entries.clear()
  const blocked = []
  const loader = { load: ({ factory }) => { blocked.push(factory((name) => require(name))) } }
  new Function('require', 'window', clientCode)((name) => require(name), { ...globalThis, __ModuleLoader__: loader })
  const store3 = blocked[0]?.store
  check('存储被禁仍能构造，值为默认',
    store3 !== undefined
    && store3.getSnapshot().splitPct === 50
    && store3.getSnapshot().inspectorWidth === 268)
  let threw = null
  try { store3?.setBottomHeight(123) } catch (error) { threw = error }
  check('存储被禁时 setter 不抛', threw === null, String(threw))
  storage.throwOnAccess = false
  store3?.dispose()
}

console.log()
if (failures.length > 0) {
  console.log(`❌ 布局运行时冒烟：${failures.length} 项失败`)
  process.exit(1)
}
console.log('✅ 布局运行时冒烟通过')
process.exit(0)
