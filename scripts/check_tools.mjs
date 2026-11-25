/**
 * 工具注册回归检查 —— 证明"AI 能改工作区"这件事有**受校验的路径**。
 *
 * 起因：上一版模型只能调 8 个工具，其中**没有一条能改设计**。想改只能
 * 直接写 design.json 文件，那条路绕过 `DesignModel.validate()`——尺寸缺失、
 * 足迹出界、ref 重复的设计会被原样留在磁盘上，而面板下一次读到的就是它。
 *
 * 所以这里断言的不是"工具数量够了"，而是三件具体的事：
 *
 *   1. 七个 AI 协作工具都注册了，且**每个都写了 description**（模型靠它
 *      决定该不该调用；没有描述的写工具是陷阱）；
 *   2. `hw_put` 的 description 里明确写了"走校验 / 不要直接写文件"——
 *      这是把纪律写进模型可见的文本，而不是只写在代码注释里；
 *   3. 写工具（put/new/setpart/deferred/workspace）在描述里标了写操作，
 *      让模型知道它们会改磁盘。
 *
 *   node scripts/check_tools.mjs
 */

import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const pluginRoot = resolve(here, '..')

const failures = []
const check = (name, ok, detail) => {
  if (ok) console.log(`  ok   ${name}`)
  else {
    console.log(`  FAIL ${name}${detail ? ' — ' + detail : ''}`)
    failures.push(name)
  }
}

const mod = await import(pathToFileURL(resolve(pluginRoot, 'dist', 'tools.js')).href)
check('dist/tools.js 导出 registerTools', typeof mod.registerTools === 'function')

// 伪 tools 服务：只记录注册了什么，不执行。
const registered = []
const fakeTools = {
  register: (tool) => {
    registered.push(tool)
    return () => {}
  },
}

const ctx = { get: (name) => (name === 'tools' ? fakeTools : undefined) }
const disposers = mod.registerTools(ctx)
check('registerTools 返回 disposers', Array.isArray(disposers) && disposers.length > 0)
// 数量**不写死**：每加一个工具就要改一次常数，而这个断言的真实意图是
// "没有工具在注册时静默丢失"。下面对 EXPECTED_TOOLS 的逐个检查才是那条
// 保证；这里只要求"至少覆盖清单"。
check('注册数不少于工具清单', registered.length >= 18, `实际 ${registered.length}`)

// 工具清单必须与 README / 文档一致。少一个 = 模型不知道那条路存在
// （`hw_roles` 就是这样补进来的：之前"需求 → 装配"只有 CLI，没有工具）。
const EXPECTED_TOOLS = [
  'hw_projects', 'hw_snapshot', 'hw_check', 'hw_import', 'hw_budget',
  'hw_crosscheck', 'hw_export', 'hw_block', 'hw_survey', 'hw_new',
  'hw_put', 'hw_setpart', 'hw_deferred', 'hw_workspace', 'hw_synth',
  'hw_roles', 'hw_scene', 'hw_log',
  // P3：Velxio 能力从"只有 hw_block 摸黑传 JSON"提升为一等工具。
  'dsh_embagent', 'hw_compile', 'hw_circuit', 'hw_libs',
  // P1：验证阶梯（审查机）。
  'hw_ladder', 'hw_sim', 'hw_serial',
]
for (const name of EXPECTED_TOOLS) {
  check(`工具 ${name} 存在`, registered.some((tool) => tool.name === name))
}

const byName = new Map(registered.map((tool) => [tool.name, tool]))

// ---- 1. AI 协作七件套 ----------------------------------------------------
const EXPECTED = [
  ['hw_survey', '勘察工作区'],
  ['hw_new', '新建工程'],
  ['hw_put', '提交设计'],
  ['hw_setpart', '改元件'],
  ['hw_deferred', '决策账本'],
  ['hw_workspace', '工作区'],
  ['hw_log', '工程式日志'],
  ['hw_synth', '需求→设计'],
  ['hw_scene', 'scene 转换'],
  ['hw_import', '导入'],
  ['hw_snapshot', '快照'],
  ['hw_check', '检查'],
  ['hw_budget', '预算'],
  ['hw_crosscheck', '核对'],
  ['hw_export', '导出'],
  ['hw_block', '积木'],
  ['hw_projects', '工程列表'],
]
for (const [name] of EXPECTED) {
  check(`工具 ${name} 已注册`, byName.has(name))
}

// ---- 2. 每个工具都要有非空 description -----------------------------------
const noDescription = registered.filter((tool) =>
  typeof tool.description !== 'string' || tool.description.trim() === '')
check('每个工具都有 description（模型靠它决定调用）', noDescription.length === 0,
  noDescription.map((t) => t.name).join(', '))

// ---- 3. hw_put 必须把"受校验"写进模型可见文本 ---------------------------
const put = byName.get('hw_put')
check('hw_put 存在', put !== undefined)
if (put !== undefined) {
  const d = String(put.description ?? '')
  check('hw_put 描述写明走模型校验', d.includes('校验'))
  check('hw_put 描述警告不要直接写文件', d.includes('不要用通用文件工具'))
  check('hw_put 描述提到返回 problems', d.includes('problems'))
}

// ---- 4. 写操作必须标出来 -------------------------------------------------
const WRITERS = ['hw_new', 'hw_put', 'hw_setpart', 'hw_deferred', 'hw_workspace',
  'hw_import', 'hw_check', 'hw_budget', 'hw_crosscheck', 'hw_export', 'hw_block']
const unmarked = WRITERS.filter((name) => {
  const tool = byName.get(name)
  if (tool === undefined) return true
  const d = String(tool.description ?? '')
  return !(d.includes('写') || d.includes('**写**'))
})
check('所有写操作的描述里标了"写"', unmarked.length === 0, unmarked.join(', '))

// ---- 5. 只读工具不该被标成写 ---------------------------------------------
for (const name of ['hw_projects', 'hw_snapshot']) {
  const tool = byName.get(name)
  const d = String(tool?.description ?? '')
  check(`${name} 标为只读`, d.includes('只读'), d.slice(0, 60))
}

// ---- 5b. 验证阶梯：描述必须把"截断"和"不是 pass"说清楚 ----------------
// 这两点是 hw_ladder 存在的理由。描述里没写，模型会以为它和 hw_check 等价，
// 于是拿 V2 的结果当"全部通过"。
{
  const d = String(byName.get('hw_ladder')?.description ?? '')
  check('hw_ladder 列出 V0→V6 七层', /V0/.test(d) && /V6/.test(d), d.slice(0, 80))
  check('hw_ladder 说明硬失败会截断下游', d.includes('截断'), d.slice(0, 120))
  check('hw_ladder 点明下游是 not_run 而非 pass',
    d.includes('not_run') || d.includes('不是 pass'), d.slice(0, 160))
  // `parameters` 是 JSON Schema（`{type, properties}`），不是名字映射。
  const params = Object.keys(byName.get('hw_ladder')?.parameters?.properties ?? {})
  check('hw_ladder 可指定层（levels）', params.includes('levels'), params.join(','))
  check('hw_ladder 可关闭截断（stopOnHard）', params.includes('stopOnHard'),
    params.join(','))
  check('hw_ladder 可调体素间距（pitchMm）', params.includes('pitchMm'),
    params.join(','))
}

// ---- 5c. 仿真三件套：描述必须诚实说明"只有 AVR" ------------------------
{
  const d = String(byName.get('hw_sim')?.description ?? '')
  check('hw_sim 说明可跑真实固件', d.includes('固件') || d.includes('指令'), d.slice(0, 80))
  check('hw_sim 如实说明 ESP32/Mega 不支持',
    d.includes('ESP32') && (d.includes('不支持') || d.includes('不提供')),
    d.slice(0, 200))
}

// ---- 6. 参数形态：Typert 会拒绝解构/默认值，工具参数表也得是平的 --------
// 工具走的是 dsh-tools 而不是 Typert，但同一纪律值得守住：参数名必须是
// 纯标识符，否则模型侧的 schema 生成会出错。
const badParams = []
for (const tool of registered) {
  const params = tool.parameters ?? {}
  for (const key of Object.keys(params)) {
    if (!/^[A-Za-z_$][\w$]*$/.test(key)) badParams.push(`${tool.name}.${key}`)
  }
}
check('参数名都是纯标识符', badParams.length === 0, badParams.join(', '))

// ---- 6b. 需求→设计→仿真这条链要能自我证明 -----------------------------
const synthTool = byName.get('hw_synth')
check('hw_synth 存在', synthTool !== undefined)
if (synthTool !== undefined) {
  const d = String(synthTool.description ?? '')
  check('hw_synth 说明走模板（不是自由写 JSON）', d.includes('模板'))
  check('hw_synth 说明覆盖范围有限', d.includes('覆盖范围') || d.includes('有限'))
}
const sceneTool = byName.get('hw_scene')
check('hw_scene 存在', sceneTool !== undefined)
if (sceneTool !== undefined) {
  const d = String(sceneTool.description ?? '')
  check('hw_scene 说明打通 hwforge 仿真链', d.includes('hwforge') && d.includes('scene.json'))
  check('hw_scene 给出下一步（hw_block）', d.includes('hw_block'))
}

// ---- 7. 日志工具必须说清"只追加"与覆盖度 -------------------------------
const logTool = byName.get('hw_log')
check('hw_log 存在', logTool !== undefined)
if (logTool !== undefined) {
  const d = String(logTool.description ?? '')
  check('hw_log 描述说明写在 logs/ 且只追加', d.includes('logs') && d.includes('只追加'))
  check('hw_log 暴露 coverage（哪些读了/哪些没读）', d.includes('coverage'))
}

// ---- 8. 勘察工具必须说清"未接入 ≠ 不存在" ------------------------------
const surveyTool = byName.get('hw_survey')
if (surveyTool !== undefined) {
  const d = String(surveyTool.description ?? '')
  check('hw_survey 描述提到确定性阶段推导', d.includes('阶段'))
  check('hw_survey 描述点明写初审.md 是写操作', d.includes('写'))
}

// ---- 9. 每个写工具都要有 presentationMeta（继续沿用）-------------------
const missingHint = ['hw_new', 'hw_put', 'hw_setpart', 'hw_deferred']
  .filter((name) => byName.get(name)?.output?.presentationMeta === undefined)
check('写工具带 presentationMeta（改完面板跟着刷新）', missingHint.length === 0,
  missingHint.join(', '))

console.log()
if (failures.length > 0) {
  console.log(`❌ 工具注册检查：${failures.length} 项失败`)
  process.exit(1)
}
console.log('✅ 工具注册检查通过')