/**
 * 模型可见的工具 —— 让"这个插件在工作"有可检验的证据。
 *
 * 工具集（一个动作一个工具，不提供"计划"工具：计划会让模型以为自己组合了
 * 什么，实际只是把一个数组递了过去）：
 *
 *   hw_projects    工程列表与各自跑到哪。只读，最轻，用来确认插件可用。
 *   hw_snapshot    一个工程的判定/预算/闸门摘要。只读。
 *   hw_check       跑物理检查。**写** checks.json。
 *   hw_import      导入 KiCad/Wokwi/BOM 成工程。**写**。
 *   hw_budget      评估五轨预算。**写** budget.json。
 *   hw_crosscheck  交叉核对 + 2D↔3D 一致性。**写** crosscheck.json。
 *   hw_export      制造包导出（受未决决策闸门约束）。**写**。
 *   hw_block       跑一个 hwforge 积木（跨层判定）。**写**。
 *
 * AI 协作八件套（P1/P2 —— 让"看懂工作区并改它"有受校验的路径）：
 *
 *   hw_survey      勘察工作区 + 确定性阶段推导 + 生成初审.md。半写。
 *   hw_new         新建空工程（板卡预设）。**写**。
 *   hw_put         提交完整 design.json，**走 DesignModel 校验**。**写**。
 *   hw_setpart     改单个元件字段。**写**。
 *   hw_deferred    读/闭合不可逆决策账本（lock 不可撤销）。半写。
 *   hw_workspace   读/切换工作区。半写。
 *   hw_log         工程式日志：列文件 / 读一份 / 看覆盖度。只读。
 *
 * 从需求到设计到仿真（P1–P4）：
 *
 *   hw_synth       按需求从 0 组装（元件+网表+板框），引脚经模板保证存在。
 *   hw_scene       design.json → scene.json，打通 hwforge 的仿真/电气判定链。
 *
 * 为什么 hw_put 是必须的：没有它，模型改设计只能直接写 design.json 文件，
 * 那条路**绕过 validate()**——不合法（尺寸缺失、足迹出界、ref 重复）的设计
 * 会被原样留在磁盘上，而面板下一次读到的就是它。
 *
 * 判定永远来自确定性检查器；工具只负责**触发与呈现**，从不自造结论。
 *
 * @module dsh-hw-velxio/tools
 */

import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { bridge, design, designLong, designPut } from './bridge.js'
import { noteHint } from './service.js'
import * as sim from './velxio/sim.js'
import { loadCompiledHex } from './velxio/artifact.js'

/** DSH 工具服务的结构声明（只用一个方法，避免把整个包拉成类型依赖）。 */
interface ToolsService {
  register(tool: unknown, options?: { when?: (scope: unknown) => boolean }): () => void
}

type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }

function text(...lines: string[]): Array<{ type: 'text'; text: string }> {
  return [{ type: 'text', text: lines.join('\n') }]
}

/** 让面板知道该看哪个工程/面板。命名空间 `hwvelxio` 属于本插件。 */
function viewHint(project: string, view?: string): JsonValue {
  const inner: Record<string, JsonValue> = { project }
  if (view !== undefined) inner.view = view
  return { hwvelxio: inner }
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function num(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function findings(payload: Record<string, unknown>): string[] {
  const raw = payload.findings
  return Array.isArray(raw) ? raw.map((x) => String(x)) : []
}

function ok(payload: Record<string, unknown>): boolean {
  return payload.status === 'ok'
}

/** 把无类型的字段读成对象数组；读不到就是空数组（不会抛）。 */
function rowsOf(payload: Record<string, unknown>,
                key: string): Array<Record<string, unknown>> {
  const raw = payload[key]
  if (!Array.isArray(raw)) return []
  return raw.filter((row): row is Record<string, unknown> =>
    row !== null && typeof row === 'object')
}

/** 无工程 id 时的统一拒绝：**不猜一个默认工程**（会悄悄改错设计）。 */
function requireProject(args: unknown): string {
  const project = str((args as { project?: unknown }).project)
  if (project.trim() === '') {
    throw new Error('需要 project 参数（工程目录名）。先调 hw_projects 看有哪些。')
  }
  return project
}

/**
 * 注册全部工具。
 * @param ctx - 宿主 context（`tools` 服务必须已就绪，见 index.ts 的 `inject`）。
 */
export function registerTools(ctx: Context): Array<() => void> {
  const tools = ctx.get('tools') as ToolsService | undefined
  if (tools === undefined) {
    console.warn('[dsh-hw-velxio] tools 服务不可用，工具未注册')
    return []
  }
  const disposers: Array<() => void> = []

  // ---- 1. hw_projects ---------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_projects',
    description:
      '列出硬件工作台的工程及各自状态（板卡、元件数、是否已判定/可导出）。只读，' +
      '这是确认插件可用的最轻方法。',
    parameters: {},
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          projects: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                id: { type: 'string' },
                title: { type: 'string' },
                board: { type: 'string' },
                parts: { type: 'number' },
                nets: { type: 'number' },
                artifacts: { type: 'array', items: { type: 'string' } },
              },
            },
          },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          projects: Array<{ id: string; title: string; board: string; parts: number; nets: number; artifacts: string[] }>
          summary: string
        }
        if (v.projects.length === 0) {
          return text('还没有工程。用 hw_import 导入一个 KiCad/Wokwi 文件，或在面板里新建。')
        }
        return text(
          `共 ${v.projects.length} 个工程（${v.summary}）：`,
          ...v.projects.map((p) =>
            `· ${p.id}  ${p.title || '(未命名)'}  [${p.board}]  ` +
            `${p.parts} 元件 / ${p.nets} 网表` +
            (p.artifacts.length > 0 ? `  已生成：${p.artifacts.join(', ')}` : '  尚未判定')),
        )
      },
    },
    execute: async () => {
      const payload = await design('list')
      const projects = ((payload.projects ?? []) as Array<Record<string, unknown>>).map((p) => {
        const artifacts = (p.artifacts ?? {}) as Record<string, boolean>
        return {
          id: str(p.id),
          title: str(p.title),
          board: str(p.board_name) || str(p.board),
          parts: num(p.parts),
          nets: num(p.nets),
          artifacts: Object.keys(artifacts).filter((k) => artifacts[k] === true),
        }
      })
      const withDesign = projects.filter((p) => p.parts > 0).length
      return { projects, summary: `${withDesign} 个有元件` }
    },
  })))

  // ---- 2. hw_snapshot ---------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_snapshot',
    description:
      '读一个工程的判定摘要：失败的物理检查、预算违规、未闭合的不可逆决策、' +
      '核对差异。只读；不会重新运行检查（用 hw_check）。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          board: { type: 'string' },
          parts: { type: 'number' },
          failedChecks: { type: 'array', items: { type: 'string' } },
          notVerified: { type: 'array', items: { type: 'string' } },
          deliverable: { type: 'boolean' },
          openDecisions: { type: 'array', items: { type: 'string' } },
          lines: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; board: string; parts: number
          failedChecks: string[]; notVerified: string[]
          deliverable: boolean; openDecisions: string[]; lines: string[]
        }
        return text(
          `# ${v.project} — ${v.board}，${v.parts} 个元件`,
          v.deliverable ? '交付闸门：已闭合，可导出' : `交付闸门：未闭合（${v.openDecisions.join(', ')}）`,
          '',
          ...(v.failedChecks.length > 0
            ? ['未通过的检查：', ...v.failedChecks.map((line) => `✗ ${line}`)]
            : ['未通过的检查：无']),
          '',
          ...(v.notVerified.length > 0
            ? ['未验证（不是通过）：', ...v.notVerified.map((line) => `? ${line}`)]
            : []),
          ...v.lines,
        )
      },
      presentationMeta: (args) => viewHint(requireProject(args), 'verdict'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const payload = await design('snapshot', project)
      if (!ok(payload)) {
        throw new Error(`读不到工程 ${project}：${findings(payload).join('；') || '未知原因'}`)
      }
      const checks = (payload.checks ?? {}) as Record<string, unknown>
      const deferred = (payload.deferred ?? {}) as Record<string, unknown>
      const budget = (payload.budget ?? {}) as Record<string, unknown>
      const designDoc = (payload.design ?? {}) as Record<string, unknown>
      const boardDoc = (designDoc.board ?? {}) as Record<string, unknown>
      const allFindings = (checks.findings ?? []) as Array<Record<string, unknown>>
      const failed = allFindings.filter((f) => f.ok !== true)
      const summary = (checks.summary ?? {}) as Record<string, unknown>

      const lines: string[] = []
      if (budget.confidence === 'none') {
        lines.push('预算：元件没有声明功耗/成本，无法评估（不是"通过"）')
      } else if (budget.confidence !== undefined) {
        const byCurrency = (budget.by_currency ?? {}) as Record<string, Record<string, unknown>>
        for (const [currency, row] of Object.entries(byCurrency)) {
          if (row.over === true) {
            lines.push(`预算超限 ${currency}: ${num(row.used)} / ${num(row.limit)}`)
          }
        }
      }
      const cross = (payload.crosscheck ?? {}) as Record<string, unknown>
      const crossSummary = (cross.summary ?? {}) as Record<string, unknown>
      if (num(crossSummary.mismatches) > 0) {
        lines.push(`交叉核对：${num(crossSummary.mismatches)} 处差异`)
      }
      if (num(crossSummary.unverified) > 0) {
        lines.push(`交叉核对：${num(crossSummary.unverified)} 项未验证`)
      }

      // **结论层**：一屏回答"这版行不行、下一步做什么"。它来自已有产物，
      // 因此可能 `stale`（设计改过但阶梯没重跑）——那句话必须原样带出来。
      const verdict = (payload.verdict ?? {}) as Record<string, unknown>
      const verdictNext = ((verdict.next ?? []) as Array<Record<string, unknown>>)
        .map((row) => {
          const step = (row.step ?? {}) as Record<string, unknown>
          const tail = typeof step.command === 'string' && step.command !== ''
            ? `（命令：${step.command}）`
            : typeof step.field === 'string' && step.field !== ''
              ? `（改 ${step.field}）`
              : ''
          return `${str(row.category)}：${str(row.title)}${tail}`
        })

      return {
        project,
        verdict: str(verdict.summary),
        verdictRollup: str(verdict.rollup),
        verdictStale: verdict.stale === true,
        next: verdictNext,
        board: str(boardDoc.name) || str(boardDoc.kind),
        parts: ((designDoc.parts ?? []) as unknown[]).length,
        failedChecks: failed.map((f) => `${str(f.check)} — ${str(f.message)}`),
        notVerified: ((checks.not_verified ?? []) as unknown[]).map(String),
        deliverable: deferred.deliverable === true,
        openDecisions: ((deferred.open_irreversible ?? []) as unknown[]).map(String),
        lines: [
          ...lines,
          `检查：${num(summary.failed)} 项未通过 / ${num(summary.total)} 项（${
            num(summary.warnings)} 个警告）`,
          ...(verdict.stale === true
            ? ['**设计改过了**：下面是上次判定的结论，重跑 `hw_ladder` 前别拿它做决定']
            : []),
          ...(verdictNext.length > 0
            ? ['下一步（已按紧急程度排序，前 5 条）：',
              ...verdictNext.map((line, index) => `  ${index + 1}. ${line}`)]
            : []),
        ],
      }
    },
  })))

  // ---- 3. hw_check ------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_check',
    description:
      '对工程跑物理/机械检查（实体重叠、板轮廓、高度、网表跨度、连接器靠边、' +
      '安装孔、尺寸来源、2D↔3D 一致性），写入 checks.json。写操作。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      pitchMm: { type: 'number', description: '体素间距 mm，默认 0.5' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          verdict: { type: 'string' },
          verdictRollup: { type: 'string' },
          verdictStale: { type: 'boolean' },
          next: { type: 'array', items: { type: 'string' } },
          board: { type: 'string' },
          parts: { type: 'number' },
          failedChecks: { type: 'array', items: { type: 'string' } },
          notVerified: { type: 'array', items: { type: 'string' } },
          deliverable: { type: 'boolean' },
          openDecisions: { type: 'array', items: { type: 'string' } },
          lines: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; ok: boolean; errors: number; warnings: number; lines: string[]
        }
        return text(
          `# ${v.project} 检查：${v.ok ? '通过' : `${v.errors} 个错误`}`
          + (v.warnings > 0 ? `，${v.warnings} 个警告` : ''),
          ...v.lines,
        )
      },
      presentationMeta: (args) => viewHint(requireProject(args), 'checks'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const pitch = num((args as { pitchMm?: unknown }).pitchMm, 0.5)
      const payload = await design('check', project, '--pitch', String(pitch))
      if (payload.status === 'not_ready') {
        throw new Error(findings(payload).join('；') || '检查前置缺失')
      }
      if (!ok(payload)) {
        throw new Error(findings(payload).join('；') || '检查失败')
      }
      noteHint(project, 'checks')
      const summary = (payload.summary ?? {}) as Record<string, unknown>
      const failed = rowsOf(payload as Record<string, unknown>, 'findings')
        .filter((f) => f.ok !== true)
      return {
        project,
        ok: payload.ok === true,
        errors: num(summary.errors),
        warnings: num(summary.warnings),
        lines: failed.map((f) =>
          `${f.severity === 'error' ? '✗' : '!'} ${str(f.check)}: ${str(f.message)}`),
      }
    },
  })))

  // ---- 3b. hw_ladder ----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_ladder',
    description:
      '按 V0→V6 阶梯跑验证：V0 符号（引用完整性）、V1 约束（绝对最大值/极性）、'
      + 'V2 几何、V3 电气 DC、V4 时序、V5 数字仿真、V6 SPICE。'
      + 'V0/V1 硬失败会**截断**下游（下游记 not_run，不是 pass）。'
      + '不传 project 时只返回阶梯说明。写 ladder.json。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回阶梯说明）' },
      levels: {
        type: 'string',
        description: '逗号分隔的层，如 "V0,V1,V2"；省略则跑全部 7 层',
      },
      stopOnHard: {
        type: 'boolean',
        description: 'V0/V1 失败是否截断下游，默认 true',
      },
      pitchMm: { type: 'number', description: 'V2 体素间距 mm，默认 0.5' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          ok: { type: 'boolean' },
          stoppedAt: { type: 'string' },
          downstream: { type: 'string' },
          levels: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                level: { type: 'string' },
                title: { type: 'string' },
                status: { type: 'string' },
                checked: { type: 'number' },
                failed: { type: 'number' },
                unknown: { type: 'array', items: { type: 'string' } },
                findings: { type: 'array', items: { type: 'string' } },
              },
            },
          },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; ok: boolean; stoppedAt: string; downstream: string
          levels: Array<{
            level: string; title: string; status: string; checked: number
            failed: number; unknown: string[]; findings: string[]
          }>
          summary: string
        }
        const head = v.stoppedAt === ''
          ? `# ${v.project} 阶梯：${v.ok ? '全部通过' : '未全部通过'}`
          : `# ${v.project} 阶梯：在 ${v.stoppedAt} 硬失败，下游已截断`
        const body: string[] = []
        for (const lv of v.levels) {
          const mark = lv.status === 'pass' ? '✓'
            : lv.status === 'fail' ? '✗'
            : lv.status === 'not_run' ? '·' : '?'
          body.push(`${mark} ${lv.level} ${lv.title} — ${lv.status}`
            + `（检查 ${lv.checked} 个对象`
            + (lv.failed > 0 ? `，${lv.failed} 项失败` : '') + '）')
          for (const f of lv.findings) body.push(`    ✗ ${f}`)
          for (const u of lv.unknown) body.push(`    ? ${u}`)
        }
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        if (project === '') return {}
        // 跑完阶梯跳到**结论**页：那一页的九层横条就是刚跑出来的东西。
        // （P0 之前这里指 `checks`，因为当时面板上没有阶梯的视图；
        // 现在有了结论层，落在 `checks` 反而看不到 V1/V3/V4 的结论。）
        return viewHint(project, 'verdict')
      },
    },
    execute: async (args) => {
      const a = args as {
        project?: unknown; levels?: unknown; stopOnHard?: unknown; pitchMm?: unknown
      }
      const project = str(a.project)
      const cliArgs: string[] = project === '' ? [] : [project]
      const levels = str(a.levels)
      if (levels !== '') cliArgs.push('--levels', levels)
      if (a.stopOnHard === false) cliArgs.push('--no-truncate')
      if (a.pitchMm !== undefined) cliArgs.push('--pitch', String(num(a.pitchMm, 0.5)))

      const payload = await design('ladder', ...cliArgs)
      if (payload.status === 'failed' || payload.status === 'not_ready') {
        throw new Error(findings(payload).join('；') || '阶梯无法运行')
      }
      // 不带 project：只是阶梯说明，没有 levels 列表
      if (project === '') {
        const table = rowsOf(payload as Record<string, unknown>, 'levels')
        return {
          project: '',
          ok: true,
          stoppedAt: '',
          downstream: 'run',
          levels: table.map((row) => ({
            level: str(row.level),
            title: str(row.title),
            status: str(row.implemented) === 'true' ? 'built' : 'unsupported',
            checked: 0,
            failed: 0,
            unknown: [] as string[],
            findings: [] as string[],
          })),
          summary: `阶梯共 ${table.length} 层；V5/V6 未实现（如实报 not_run）`,
        }
      }

      noteHint(project, 'checks')
      return {
        project,
        ok: payload.ok === true,
        stoppedAt: str(payload.stopped_at),
        downstream: str(payload.downstream),
        levels: rowsOf(payload as Record<string, unknown>, 'levels').map((lv) => ({
          level: str(lv.level),
          title: str(lv.title),
          status: str(lv.status),
          checked: num(lv.checked),
          failed: num(lv.failed),
          unknown: (Array.isArray(lv.unknown) ? lv.unknown : []).map((x) => str(x)),
          findings: (Array.isArray(lv.findings) ? lv.findings : [])
            .filter((f) => (f as Record<string, unknown>).ok !== true)
            .map((f) => {
              const row = f as Record<string, unknown>
              return `${str(row.check)}: ${str(row.message)}`
            }),
        })),
        summary: (() => {
          const s = (payload.summary ?? {}) as Record<string, unknown>
          return `通过 ${num(s.pass)} / 失败 ${num(s.fail)}`
            + ` / 未知 ${num(s.unknown)} / 未跑 ${num(s.not_run)}`
            + `，检查 ${num(s.checked)} 个对象，${num(s.ms).toFixed(1)}ms`
        })(),
      }
    },
  })))

  // ---- 3c. hw_claims ----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_claims',
    description:
      '推导并求值断言层：每个元件为什么在板上，变成一条可执行断言。'
      + '断言由**规则生成**（不让 AI 写，否则会写出永远为真的断言）。'
      + '三态 pass/fail/unknown，缺数据是 unknown 不是 pass。'
      + '同时报出**语义覆盖率**——全绿可能只是因为断言全是结构性 connected。'
      + '写 claims.json。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
      priority: {
        type: 'string',
        description: '逗号分隔的优先级过滤，如 "P0,P1"',
      },
      evaluateOnly: {
        type: 'boolean',
        description: '只求值不写 claims.json（默认写）',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          ok: { type: 'boolean' },
          count: { type: 'number' },
          pass: { type: 'number' },
          fail: { type: 'number' },
          unknown: { type: 'number' },
          semanticRatio: { type: 'number' },
          partsWithSemantic: { type: 'number' },
          partsCovered: { type: 'number' },
          byType: { type: 'string' },
          failures: { type: 'array', items: { type: 'string' } },
          unknowns: { type: 'array', items: { type: 'string' } },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; ok: boolean; count: number; pass: number
          fail: number; unknown: number; semanticRatio: number
          partsWithSemantic: number; partsCovered: number; byType: string
          failures: string[]; unknowns: string[]; summary: string
        }
        const head = `# ${v.project} 断言：${v.count} 条，`
          + `${v.ok ? '全部通过' : '未全部通过'}`
        const cov = `语义覆盖 ${v.partsWithSemantic}/${v.partsCovered}`
          + `（${(v.semanticRatio * 100).toFixed(0)}%）· ${v.byType}`
        const body: string[] = [cov]
        for (const f of v.failures) body.push(`✗ ${f}`)
        for (const u of v.unknowns) body.push(`? ${u}`)
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as {
        project?: unknown; priority?: unknown; evaluateOnly?: unknown
      }
      const project = str(a.project)
      const cliArgs: string[] = project === '' ? [] : [project]
      const priority = str(a.priority)
      if (priority !== '') cliArgs.push('--priority', priority)
      if (a.evaluateOnly === true) cliArgs.push('--evaluate')

      const payload = await design('claims', ...cliArgs)
      if (payload.status === 'failed' || payload.status === 'not_ready') {
        throw new Error(findings(payload).join('；') || '断言层无法运行')
      }
      if (project === '') {
        return {
          project: '', ok: true, count: 0, pass: 0, fail: 0, unknown: 0,
          semanticRatio: 0, partsWithSemantic: 0, partsCovered: 0,
          byType: '', failures: [] as string[], unknowns: [] as string[],
          summary: '断言层：requires 缺失即 fail —— 这是"删除即失败"的支点',
        }
      }

      const cov = (payload.coverage ?? {}) as Record<string, unknown>
      const rows = rowsOf(payload as Record<string, unknown>, 'claims')
      const byType = (cov.by_type ?? {}) as Record<string, unknown>
      noteHint(project, 'checks')
      return {
        project,
        ok: payload.ok === true,
        count: num(payload.count),
        pass: num(payload.pass),
        fail: num(payload.fail),
        unknown: num(payload.unknown),
        semanticRatio: num(cov.semantic_ratio),
        partsWithSemantic: num(cov.parts_with_semantic),
        partsCovered: num(cov.parts_covered),
        byType: Object.entries(byType)
          .map(([k, n]) => `${k} ${num(n)}`).join(' / '),
        failures: rows
          .filter((r) => str(r.status) === 'fail')
          .map((r) => `${str(r.id)} — ${str(r.basis)}`),
        unknowns: rows
          .filter((r) => str(r.status) === 'unknown')
          .map((r) => `${str(r.id)} — ${str(r.basis)}`),
        summary: num(cov.semantic === 0)
          ? '注意：没有任何语义断言。全绿只说明元件接进了网表，'
            + '不说明功能正确'
          : `语义断言 ${num(cov.semantic)} 条 / 结构断言 ${num(cov.structural)} 条`,
      }
    },
  })))

  // ---- 3d. hw_assert ----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_assert',
    description:
      '**删除即失败**的验证：把某个元件从内存副本里剔除后重新求值断言，'
      + '报出哪些断言因此从 pass 变红。原设计一个字节都不改（不写盘）。'
      + 'reddened 为空说明断言机制没覆盖到那个元件。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      remove: { type: 'string', description: '要剔除的元件 ref，如 R12' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          removed: { type: 'string' },
          count: { type: 'number' },
          reddened: { type: 'array', items: { type: 'string' } },
          unchangedPass: { type: 'number' },
          wasUnknown: { type: 'number' },
          note: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; removed: string; count: number
          reddened: string[]; unchangedPass: number; wasUnknown: number
          note: string
        }
        const head = v.count > 0
          ? `# 移除 ${v.removed} → ${v.count} 条断言变红`
          : `# 移除 ${v.removed} → 没有断言变红（机制未覆盖它）`
        const body = v.reddened.map((id) => `✗ ${id}`)
        return text(head, ...body,
          `未受影响 ${v.unchangedPass} 条 · 原本 unknown ${v.wasUnknown} 条`,
          v.note)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; remove?: unknown }
      const project = str(a.project)
      const remove = str(a.remove)
      if (project === '' || remove === '') {
        throw new Error('需要 project 和 remove')
      }
      const payload = await design('assert', project, '--remove', remove)
      if (payload.status === 'failed' || payload.status === 'not_ready') {
        throw new Error(findings(payload).join('；') || '无法求值')
      }
      const reddened = (Array.isArray(payload.reddened) ? payload.reddened : [])
        .map((x) => str(x))
      return {
        project,
        removed: str(payload.removed),
        count: num(payload.count),
        reddened,
        unchangedPass: num(payload.unchanged_pass),
        wasUnknown: num(payload.was_unknown),
        note: str(payload.note),
      }
    },
  })))

  // ---- 3e. hw_justify ---------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_justify',
    description:
      '归因反推：从网表拓扑反推每个元件**为什么在板上**，给出'
      + 'why + effect_if_removed + confidence。判不出时返回 unknown 并说清'
      + '为什么判不出（哈希 kind / 没有网表），**不编理由**。'
      + '同时报两个比例：全部元件口径 与 有网表元件口径——'
      + '两者差距大说明是缺数据，不是算法不行。'
      + '写 justify.json（`writeBack=true` 时还写回 design.json）。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
      ref: { type: 'string', description: '只看某个元件，如 R12' },
      writeBack: {
        type: 'boolean',
        description: '把结论写进 design.json 的 part.justification（默认不写）',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          written: { type: 'boolean' },
          ok: { type: 'boolean' },
          total: { type: 'number' },
          known: { type: 'number' },
          unknown: { type: 'number' },
          ratio: { type: 'number' },
          judgeable: { type: 'number' },
          ratioJudgeable: { type: 'number' },
          noNetlist: { type: 'number' },
          groups: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                label: { type: 'string' },
                count: { type: 'number' },
                why: { type: 'string' },
              },
            },
          },
          detail: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                ref: { type: 'string' },
                category: { type: 'string' },
                kind: { type: 'string' },
                confidence: { type: 'string' },
                why: { type: 'string' },
                removed: { type: 'string' },
              },
            },
          },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; ok: boolean; total: number; known: number
          unknown: number; ratio: number; judgeable: number
          ratioJudgeable: number; noNetlist: number
          groups: Array<{ label: string; count: number; why: string }>
          detail: Array<{ ref: string; category: string; kind: string
            confidence: string; why: string; removed: string }>
          summary: string
        }
        const head = `# ${v.project} 归因：${v.known}/${v.total} 判出理由`
          + `（${(v.ratio * 100).toFixed(1)}%）`
        const body: string[] = []
        if (v.noNetlist > 0) {
          body.push(`! ${v.noNetlist} 个元件没有网表：有网表的 ${v.judgeable} 个`
            + `里判出 ${(v.ratioJudgeable * 100).toFixed(1)}% —— `
            + `差距是缺数据，不是算法判不出`)
        }
        if (v.detail.length > 0) {
          body.push('', '## 明细（人工核对用）')
          for (const row of v.detail) {
            body.push(`* ${row.ref} [${row.category}] ${row.confidence}`)
            body.push(`    ${row.why}`)
            body.push(`    删掉：${row.removed}`)
          }
        } else if (v.groups.length > 0) {
          body.push('', '## 分组（无源件读这一层）')
          for (const g of v.groups) body.push(`* ${g.count} × ${g.label} — ${g.why}`)
        }
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as {
        project?: unknown; ref?: unknown; writeBack?: unknown
      }
      const project = str(a.project)
      const cliArgs: string[] = project === '' ? [] : [project]
      const ref = str(a.ref)
      if (ref !== '') cliArgs.push('--ref', ref)
      if (a.writeBack === true) cliArgs.push('--write')

      const payload = await design('justify', ...cliArgs)
      if (payload.status === 'failed' || payload.status === 'not_ready') {
        throw new Error(findings(payload).join('；') || '归因失败')
      }
      const groups = (Array.isArray(payload.groups) ? payload.groups : [])
        .slice(0, 40)
        .map((g) => {
          const row = (g ?? {}) as Record<string, unknown>
          return {
            label: str(row.label),
            count: num(row.count),
            why: str(row.why),
          }
        })
      const wanted = Array.isArray(payload.parts) ? payload.parts : []
      const detail = wanted.slice(0, 30).map((raw) => {
        const row = (raw ?? {}) as Record<string, unknown>
        const j = (row.justification ?? {}) as Record<string, unknown>
        const g = (row.group ?? {}) as Record<string, unknown>
        return {
          ref: str(row.ref),
          category: str(row.category),
          kind: str(g.kind),
          confidence: str(j.confidence),
          why: str(j.why),
          removed: str(j.effect_if_removed),
        }
      })
      const missing = (payload.missing_data ?? {}) as Record<string, unknown>
      return {
        project,
        written: payload.written === true,
        ok: payload.ok === true,
        total: num(payload.total),
        known: num(payload.known),
        unknown: num(payload.unknown),
        ratio: num(payload.ratio),
        judgeable: num(payload.judgeable),
        ratioJudgeable: num(payload.ratio_judgeable),
        noNetlist: num(missing.no_netlist_count),
        groups,
        detail,
        summary: str(payload.summary),
      }
    },
  })))

  // ---- 3f. hw_handoff ---------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_handoff',
    description:
      '交接层：把散在阶梯/断言/预算/归因里的「未验证」聚成五类'
      + '（阻断 / 缺数据 / 默认值 / 不会验 / 未接线），'
      + '每类都写「你要做什么」和风险。输出 handoff.json + handoff.md。'
      + '**不重新跑判定**——它读已有产物，并用指纹标明对着哪一版。'
      + '空表会显式写「无」，缺产物会标 missing（不是「没问题」）。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          deliverable: { type: 'boolean' },
          blocking: { type: 'number' },
          missingData: { type: 'number' },
          assumed: { type: 'number' },
          unverifiable: { type: 'number' },
          orphan: { type: 'number' },
          items: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                category: { type: 'string' },
                title: { type: 'string' },
                action: { type: 'string' },
                risk: { type: 'string' },
              },
            },
          },
          missingArtifacts: { type: 'array', items: { type: 'string' } },
          summary: { type: 'string' },
          markdownPath: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; deliverable: boolean
          blocking: number; missingData: number; assumed: number
          unverifiable: number; orphan: number
          items: Array<{ category: string; title: string
            action: string; risk: string }>
          missingArtifacts: string[]
          summary: string
          markdownPath: string
        }
        const head = v.deliverable
          ? `# ${v.project} 交接：可打板（无阻断项）`
          : `# ${v.project} 交接：不可打板（${v.blocking} 项阻断）`
        const label: Record<string, string> = {
          blocking: '必须修好才能打板',
          missing_data: '缺数据（机器不知道）',
          assumed: '机器用了默认值',
          unverifiable: '这套系统不会验',
          orphan: '生成了但没接线',
        }
        const body: string[] = []
        for (const cat of Object.keys(label)) {
          const rows = v.items.filter((i) => i.category === cat)
          body.push('', `## ${label[cat]}（${rows.length}）`)
          if (rows.length === 0) {
            body.push('  无。')
            continue
          }
          for (const row of rows) {
            body.push(`* ${row.title}`)
            body.push(`    要做：${row.action}`)
            body.push(`    风险：${row.risk}`)
          }
        }
        if (v.missingArtifacts.length > 0) {
          body.push('', `! 缺失的产物：${v.missingArtifacts.join(', ')} —— `
            + '那几类没有被检查过，不是「检查了没问题」')
        }
        return text(head, ...body, v.summary, `完整表格：${v.markdownPath}`)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'verdict')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown }
      const project = str(a.project)
      if (project === '') throw new Error('需要 project')
      const payload = await design('handoff', project)
      if (payload.status === 'failed' || payload.status === 'not_ready') {
        throw new Error(findings(payload).join('；') || '无法构建交接单')
      }
      const counts = (payload.counts ?? {}) as Record<string, unknown>
      const sources = (payload.sources ?? {}) as Record<string, unknown>
      const missingArtifacts = Object.entries(sources)
        .filter(([, v]) => str(v) === 'missing')
        .map(([k]) => k)
      const items = (Array.isArray(payload.items) ? payload.items : [])
        .slice(0, 60)
        .map((raw) => {
          const row = (raw ?? {}) as Record<string, unknown>
          return {
            category: str(row.category),
            title: str(row.title),
            action: str(row.action),
            risk: str(row.risk),
          }
        })
      return {
        project,
        deliverable: payload.deliverable === true,
        blocking: num(counts.blocking),
        missingData: num(counts.missing_data),
        assumed: num(counts.assumed),
        unverifiable: num(counts.unverifiable),
        orphan: num(counts.orphan),
        items,
        missingArtifacts,
        summary: str(payload.summary),
        markdownPath: 'handoff.md',
      }
    },
  })))

  // ---- 3g. hw_coverify --------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_coverify',
    description:
      '协同验证：需求里写的数值（指向精度/跟踪速率/探测距离/链路带宽/延迟/'
      + '电源电流）与硬件能力是否对得上（8 条 CV 检查）。'
      + '**未申报的项会用默认值计算，并明确列为 not_verified** ——'
      + '那是「机器替你做主了」，不是「没问题」。写 coverify.json。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          ok: { type: 'boolean' },
          passed: { type: 'number' },
          failed: { type: 'number' },
          notVerified: { type: 'number' },
          items: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                check: { type: 'string' },
                status: { type: 'string' },
                message: { type: 'string' },
              },
            },
          },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; ok: boolean; passed: number; failed: number
          notVerified: number
          items: Array<{ check: string; status: string; message: string }>
          summary: string
        }
        const head = `# ${v.project} 协同验证：`
          + (v.ok ? '通过' : '未通过')
          + `（通过 ${v.passed} / 失败 ${v.failed} / 未验证 ${v.notVerified}）`
        const body: string[] = []
        for (const row of v.items) {
          body.push(`* [${row.status}] ${row.check} — ${row.message}`)
        }
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown }
      const project = str(a.project)
      if (project === '') throw new Error('需要 project')
      const payload = await design('coverify', project)
      // 真实字段是 `check_id` / `passed`（布尔），计数在 `summary` 里。
      // 第一版按 `id` / `status` / `counts` 写，全读成空——工具会报
      // "通过 0 / 失败 0" 而 `ok=false`，自相矛盾。
      const checks = Array.isArray(payload.checks) ? payload.checks : []
      const items = checks.slice(0, 40).map((raw) => {
        const row = (raw ?? {}) as Record<string, unknown>
        const passed = row.passed === true
        return {
          check: str(row.check_id) || str(row.check),
          status: passed ? 'pass' : 'fail',
          message: str(row.message) || str(row.title),
        }
      })
      const summary = (payload.summary ?? {}) as Record<string, unknown>
      const total = num(summary.total)
      const failed = num(summary.failed)
      return {
        project,
        ok: payload.ok === true,
        passed: Math.max(0, total - failed),
        failed,
        notVerified: (Array.isArray(payload.not_verified)
          ? payload.not_verified.length : 0),
        items,
        summary: str(payload.summary),
      }
    },
  })))

  // ---- 3h. hw_units -----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_units',
    description:
      '单元层：把设计按 `Placement.board_id` 切成功能单元（**纯结构，不用 AI**），'
      + '并判每块板的角色。角色只看板上的**具名网络**——自动网名'
      + '（`$1N394` / `w1`）不算。没有具名网的板如实记 unknown，'
      + '并说明是「缺数据」而不是「判不出」。'
      + '同时报两个比例：全部单元口径 与 有具名网单元口径。写 units.json。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
      apply: {
        type: 'boolean',
        description: '把结果写进 design.json 的 units_list（默认只读推导）',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          multiBoard: { type: 'boolean' },
          total: { type: 'number' },
          known: { type: 'number' },
          ratio: { type: 'number' },
          ratioJudgeable: { type: 'number' },
          noNetlist: { type: 'number' },
          units: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                id: { type: 'string' },
                role: { type: 'string' },
                partCount: { type: 'number' },
                confidence: { type: 'string' },
                why: { type: 'string' },
              },
            },
          },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; multiBoard: boolean; total: number; known: number
          ratio: number; ratioJudgeable: number; noNetlist: number
          units: Array<{ id: string; role: string; partCount: number
            confidence: string; why: string }>
          summary: string
        }
        const head = `# ${v.project} 单元层：${v.total} 个单元，`
          + `判出角色 ${v.known}（${(v.ratio * 100).toFixed(1)}%）`
        const body: string[] = []
        for (const u of v.units) {
          body.push(`* ${u.id} [${u.role}] ${u.partCount} 件 · ${u.confidence}`)
          body.push(`    ${u.why}`)
        }
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; apply?: unknown }
      const project = str(a.project)
      const cliArgs: string[] = project === '' ? [] : [project]
      if (a.apply === true) cliArgs.push('--apply')
      const payload = await design('units', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '单元层失败')
      }
      const unitRows = (Array.isArray(payload.units) ? payload.units : [])
        .slice(0, 60).map((raw) => {
          const row = (raw ?? {}) as Record<string, unknown>
          return {
            id: str(row.id),
            role: str(row.role),
            partCount: num(row.part_count),
            confidence: str(row.confidence),
            why: str(row.why),
          }
        })
      const missing = (payload.missing_data ?? {}) as Record<string, unknown>
      return {
        project,
        multiBoard: payload.multi_board === true,
        total: num(payload.total),
        known: num(payload.known),
        ratio: num(payload.ratio),
        ratioJudgeable: num(payload.ratio_judgeable),
        noNetlist: num(missing.no_netlist_count),
        units: unitRows,
        summary: str(payload.summary),
      }
    },
  })))

  // ---- 3i. hw_links -----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_links',
    description:
      '板间链路四条检查：协议族一致 / 电平兼容 / 波特率可达 / '
      + '**孤儿外设**（声明了外设但没说接谁——隐性假设）。'
      + '`source=inferred` 的链路**只提示不判 fail**（推断会错）。'
      + '链路表为空时状态是 not_ready，不是通过。写 links.json。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
      apply: {
        type: 'boolean',
        description: '把推断出的候选链路写进 design.json 的 links_list',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          ok: { type: 'boolean' },
          status: { type: 'string' },
          declared: { type: 'number' },
          inferred: { type: 'number' },
          declaredUnlinked: { type: 'number' },
          observedUnlinked: { type: 'number' },
          unitsWithoutNets: { type: 'number' },
          findings: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                check: { type: 'string' },
                message: { type: 'string' },
              },
            },
          },
          reason: { type: 'string' },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; ok: boolean; status: string
          declared: number; inferred: number
          declaredUnlinked: number; observedUnlinked: number
          unitsWithoutNets: number
          findings: Array<{ check: string; message: string }>
          reason: string; summary: string
        }
        const head = `# ${v.project} 板间链路：${v.status}`
          + `（声明 ${v.declared} / 推断 ${v.inferred}）`
        const body: string[] = []
        if (v.reason) body.push(`! ${v.reason}`)
        for (const row of v.findings) body.push(`* [${row.check}] ${row.message}`)
        body.push('', `孤儿：声明未接 ${v.declaredUnlinked} · `
          + `仅提示 ${v.observedUnlinked} · `
          + `无网表单元 ${v.unitsWithoutNets}`)
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; apply?: unknown }
      const project = str(a.project)
      const cliArgs: string[] = project === '' ? [] : [project]
      if (a.apply === true) cliArgs.push('--apply')
      const payload = await design('links', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '链路检查失败')
      }
      const checked = (payload.checked ?? {}) as Record<string, unknown>
      const counts = (payload.orphans ?? {}) as Record<string, unknown>
      const orphanCounts = (counts.counts ?? {}) as Record<string, unknown>
      const rows = (Array.isArray(payload.findings) ? payload.findings : [])
        .slice(0, 30).map((raw) => {
          const row = (raw ?? {}) as Record<string, unknown>
          return { check: str(row.check), message: str(row.message) }
        })
      return {
        project,
        ok: payload.ok === true,
        status: str(payload.status),
        declared: num(checked.declared_links),
        inferred: num(checked.inferred_links),
        declaredUnlinked: num(orphanCounts.declared_unlinked),
        observedUnlinked: num(orphanCounts.observed_unlinked),
        unitsWithoutNets: num(orphanCounts.units_without_nets),
        findings: rows,
        reason: str(payload.reason),
        summary: str(payload.summary),
      }
    },
  })))

  // ---- 3j. hw_capability -------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_capability',
    description:
      '能力窄表查询（字典 + 纯过滤，不是知识图谱）。'
      + '`source` 不是 datasheet/measured 的型号**直接不参与选择**'
      + '（猜出来的能力值 = 假通过）。`fills` 回答「它是什么」，'
      + '`provides` 回答「它能干什么」，两者不可互相替代。'
      + '空结果会解释成「表没收录」还是「约束砍光了」。',
    parameters: {
      needs: { type: 'string', description: '需要的能力，逗号分隔，如 uart,spi' },
      role: { type: 'string', description: '单元角色，如 mcu / radio / sensor' },
      areaMm2: { type: 'number', description: '面积上限 mm²' },
      exclude: { type: 'string', description: '排除的型号，逗号分隔' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          ok: { type: 'boolean' },
          candidates: { type: 'array', items: { type: 'string' } },
          count: { type: 'number' },
          reason: { type: 'string' },
          hint: { type: 'string' },
          rejected: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          ok: boolean; candidates: string[]; count: number
          reason: string; hint: string; rejected: string[]
        }
        if (v.ok) {
          return text(`# 能力表：${v.count} 个候选`,
            ...v.candidates.map((c) => `* ${c}`))
        }
        return text('# 能力表：没有候选',
          `! ${v.reason}`, `建议：${v.hint}`,
          ...v.rejected.slice(0, 12).map((r) => `* ${r}`))
      },
    },
    execute: async (args) => {
      const a = args as {
        needs?: unknown; role?: unknown; areaMm2?: unknown; exclude?: unknown
      }
      const cliArgs: string[] = []
      const needs = str(a.needs)
      if (needs !== '') cliArgs.push('--needs', needs)
      const role = str(a.role)
      if (role !== '') cliArgs.push('--role', role)
      if (typeof a.areaMm2 === 'number') {
        cliArgs.push('--area', String(a.areaMm2))
      }
      const exclude = str(a.exclude)
      if (exclude !== '') cliArgs.push('--exclude', exclude)
      const payload = await design('capability', ...cliArgs)
      const rejected = Object.entries(
        (payload as Record<string, unknown>).rejected ?? {} as Record<string, unknown>)
        .slice(0, 20)
        .map(([k, v]) => `${k}: ${str(v)}`)
      return {
        ok: payload.status === 'ok',
        candidates: (Array.isArray(payload.candidates) ? payload.candidates : [])
          .map((x) => str(x)),
        count: num(payload.count),
        reason: str(payload.detail),
        hint: str(payload.hint),
        rejected,
      }
    },
  })))

  // ---- 3k. hw_generate ---------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_generate',
    description:
      '按需求**有限枚举**生成候选设计（四段：需求→单元→元件组合→链路→断言）。'
      + '**没有收敛循环**：枚举有硬上限，天然终止，不会震荡。'
      + '按 (硬失败, 软失败, unknown, 面积) 排序输出多个候选，'
      + '工程师从中选。不保证全局最优——可行解比最优解重要。'
      + '生成器只对**新工程**有效（不为导入的哈希 kind 补映射）。',
    parameters: {
      requirement: { type: 'string', description: '需求文件路径（.yaml/.yml/.json）' },
      maxCandidates: { type: 'number', description: '枚举上限，默认 50' },
      fast: {
        type: 'boolean',
        description: '跳过验证阶梯，只出候选清单（更快）',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          ok: { type: 'boolean' },
          status: { type: 'string' },
          enumerated: { type: 'number' },
          unique: { type: 'number' },
          deliverableCount: { type: 'number' },
          reason: { type: 'string' },
          candidates: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                id: { type: 'string' },
                hardFailures: { type: 'number' },
                softFailures: { type: 'number' },
                cost: { type: 'number' },
                parts: { type: 'string' },
                why: { type: 'string' },
              },
            },
          },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          ok: boolean; status: string; enumerated: number; unique: number
          deliverableCount: number; reason: string
          candidates: Array<{ id: string; hardFailures: number
            softFailures: number; cost: number; parts: string; why: string }>
          summary: string
        }
        const head = `# 生成：${v.status}（枚举 ${v.enumerated} / 唯一 ${v.unique}`
          + ` / 可交付 ${v.deliverableCount}）`
        const body: string[] = []
        if (v.reason) body.push(`! ${v.reason}`)
        for (const c of v.candidates.slice(0, 8)) {
          body.push(`* ${c.id} 硬失败=${c.hardFailures} 软=${c.softFailures}`)
          body.push(`    ${c.parts}`)
        }
        if (v.candidates.length > 8) {
          body.push(`（其余 ${v.candidates.length - 8} 个候选见 JSON）`)
        }
        return text(head, ...body, v.summary)
      },
    },
    execute: async (args) => {
      const a = args as {
        requirement?: unknown; maxCandidates?: unknown; fast?: unknown
      }
      const requirement = str(a.requirement)
      if (requirement === '') throw new Error('需要 requirement 文件路径')
      const cliArgs: string[] = ['--requirement', requirement]
      if (typeof a.maxCandidates === 'number') {
        cliArgs.push('--max-candidates', String(Math.trunc(a.maxCandidates)))
      }
      if (a.fast === true) cliArgs.push('--fast')
      const payload = await design('generate', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '生成失败')
      }
      const rows = (Array.isArray(payload.candidates) ? payload.candidates : [])
        .slice(0, 20).map((raw) => {
          const row = (raw ?? {}) as Record<string, unknown>
          const parts = Array.isArray(row.parts) ? row.parts : []
          // 名字不能叫 text —— 那会遮蔽模块级的 text() 辅助函数，
          // 虽然当前作用域不用它，但下一次有人在这里加一行 text(...) 就会撞。
          const partsText = parts.map((p) => {
            const r = (p ?? {}) as Record<string, unknown>
            const owners = Array.isArray(r.units) ? r.units.map((x) => str(x))
              : [str(r.unit)]
            return `${owners.join('+')}=${str(r.model)}`
          }).join(' ')
          return {
            id: str(row.id),
            hardFailures: num(row.hard_failures),
            softFailures: num(row.soft_failures),
            cost: num(row.cost),
            parts: partsText,
            why: str(row.why),
          }
        })
      return {
        ok: payload.ok === true,
        status: str(payload.status),
        enumerated: num(payload.enumerated),
        unique: num(payload.unique),
        deliverableCount: num(payload.deliverable_count),
        reason: str(payload.reason),
        candidates: rows,
        summary: str(payload.summary),
      }
    },
  })))

  // ---- 3l. hw_ioc_links -------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_ioc_links',
    description:
      '从 STM32CubeMX 的 `.ioc` 抽板间链路：解析 `PA2.Signal=USART2_TX`，'
      + '按三档可信度配对（显式规则 > 同名外设+TX↔RX 交叉 > label 语义）。'
      + '**全部标 source=inferred，默认不写进 links_list**'
      + '（推断会错，不能判人 fail）。配不上的端点进 unpaired 并说明原因，不猜。',
    parameters: {
      root: { type: 'string', description: '去哪找 .ioc（目录）' },
      apply: { type: 'string', description: '工程名：把 declared 链路写进该工程' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          ok: { type: 'boolean' },
          status: { type: 'string' },
          boards: { type: 'number' },
          endpoints: { type: 'number' },
          links: { type: 'number' },
          tier2: { type: 'number' },
          tier3: { type: 'number' },
          unpaired: { type: 'number' },
          applied: { type: 'number' },
          detail: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                id: { type: 'string' },
                protocol: { type: 'string' },
                evidence: { type: 'string' },
              },
            },
          },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          ok: boolean; status: string; boards: number; endpoints: number
          links: number; tier2: number; tier3: number; unpaired: number
          applied: number
          detail: Array<{ id: string; protocol: string; evidence: string }>
          summary: string
        }
        const head = `# .ioc 抽取：${v.boards} 板 / ${v.endpoints} 端点`
          + ` -> ${v.links} 条链路（同名外设 ${v.tier2} / label ${v.tier3}）`
        const body: string[] = v.detail.slice(0, 15).map(
          (l) => `* ${l.id} [${l.protocol}]`)
        if (v.unpaired > 0) body.push(`未配上 ${v.unpaired} 个端点（见 JSON）`)
        if (v.applied > 0) body.push(`已写入 ${v.applied} 条 declared 链路`)
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const apply = str((args as { apply?: unknown }).apply)
        return apply === '' ? {} : viewHint(apply, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as { root?: unknown; apply?: unknown }
      const root = str(a.root)
      if (root === '') throw new Error('需要 root：去哪找 .ioc')
      const cliArgs: string[] = ['--root', root]
      const apply = str(a.apply)
      if (apply !== '') cliArgs.push('--apply', apply)
      const payload = await design('ioc-links', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '抽取失败')
      }
      const counts = (payload.counts ?? {}) as Record<string, unknown>
      const links = (payload.draft ?? {}) as Record<string, unknown>
      const rows = (Array.isArray(links.links) ? links.links : [])
        .slice(0, 30).map((raw) => {
          const row = (raw ?? {}) as Record<string, unknown>
          return {
            id: str(row.id),
            protocol: str(row.protocol),
            evidence: str(row.evidence),
          }
        })
      return {
        ok: payload.ok === true,
        status: str(payload.status),
        boards: num(counts.boards),
        endpoints: num(counts.endpoints),
        links: num(counts.links),
        tier2: num(counts.tier2),
        tier3: num(counts.tier3),
        unpaired: num(counts.unpaired),
        applied: num(payload.applied),
        detail: rows,
        summary: str(payload.summary),
      }
    },
  })))

  // ---- 3m. hw_renode ----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_renode',
    description:
      'Renode 接入：探测 Renode/ARM 工具链，生成 `.resc` 联调脚本 + run.cmd。'
      + '加载地址从 `.bin` 的 Cortex-M 向量表读出（不默认 0x08000000）。'
      + '多板用 `connector Connect`（Renode 原生命令），**inferred 链路不接线**。'
      + '**本机实测无法在 DSH 内加载 CPU 平台**（Renode 要独占打开锁文件，'
      + '沙箱拒绝），所以 `runnable_here` 会如实报 false，'
      + '产物需在 DSH 外执行。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略=只探测）' },
      firmware: {
        type: 'string',
        description: 'JSON：{board_id: "路径/x.bin"}',
      },
      boardMcu: {
        type: 'string',
        description: 'JSON：{board_id: "STM32F103C8T6"} 显式映射（哈希 kind 时必需）',
      },
      iocRoot: { type: 'string', description: '去哪找 .ioc（列出可选 MCU 型号）' },
      checkCpu: { type: 'boolean', description: '真的起一次 Renode 试加载 CPU 平台' },
      write: { type: 'boolean', description: '把 .resc / run.cmd 写到工程目录' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          ok: { type: 'boolean' },
          status: { type: 'string' },
          runnableHere: { type: 'boolean' },
          machines: { type: 'number' },
          connectors: { type: 'array', items: { type: 'string' } },
          written: { type: 'array', items: { type: 'string' } },
          warnings: { type: 'array', items: { type: 'string' } },
          mcuOptions: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                board: { type: 'string' },
                mcu: { type: 'string' },
                platform: { type: 'string' },
              },
            },
          },
          notes: { type: 'array', items: { type: 'string' } },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; ok: boolean; status: string
          runnableHere: boolean; machines: number
          connectors: string[]
          written: string[]
          warnings: string[]
          mcuOptions: Array<{ board: string; mcu: string; platform: string }>
          notes: string[]
          summary: string
        }
        const head = `# Renode：${v.status} —— ${v.machines} 个机台，`
          + `${v.connectors.length} 条 connector`
        const body: string[] = []
        if (!v.runnableHere) {
          body.push('! 本机不能跑（沙箱拦截 Renode 的独占文件锁）。'
            + '生成的 .resc 需在 DSH 外执行。')
        }
        for (const note of v.notes) body.push(`- ${note}`)
        for (const c of v.connectors) body.push(`* ${c}`)
        if (v.warnings.length > 0) {
          body.push('', `需要注意 ${v.warnings.length} 条：`)
          for (const w of v.warnings.slice(0, 6)) body.push(`  - ${w}`)
        }
        if (v.mcuOptions.length > 0) {
          body.push('', '.ioc 提供的型号（用 boardMcu 显式映射）：')
          for (const o of v.mcuOptions) {
            body.push(`  - ${o.board} = ${o.mcu} -> ${o.platform}`)
          }
        }
        if (v.written.length > 0) {
          body.push('', `已写：${v.written.join(', ')}`)
        }
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as {
        project?: unknown; firmware?: unknown; boardMcu?: unknown
        iocRoot?: unknown; checkCpu?: unknown; write?: unknown
      }
      const project = str(a.project)
      if (project === '') {
        const probe = await design('renode', 'status')
        return {
          project: '',
          ok: probe.status === 'ok',
          status: str(probe.status),
          runnableHere: probe.runnable_here === true,
          machines: 0,
          connectors: [],
          written: [],
          warnings: [],
          mcuOptions: [],
          notes: [
            `Renode: ${str((probe.renode as Record<string, unknown>)?.version)}`,
            `CPU 平台: ${(probe.cpu_platform as Record<string, unknown>)?.available === true ? '可用' : '不可用'}`,
            `ARM GCC: ${(probe.arm_gcc as Record<string, unknown>)?.available === true ? '已装' : '未装'}`,
          ],
          summary: '只探测，没给工程名',
        }
      }
      const cliArgs: string[] = [project]
      const firmware = str(a.firmware)
      if (firmware !== '') cliArgs.push('--firmware', firmware)
      const boardMcu = str(a.boardMcu)
      if (boardMcu !== '') cliArgs.push('--board-mcu', boardMcu)
      const iocRoot = str(a.iocRoot)
      if (iocRoot !== '') cliArgs.push('--ioc-root', iocRoot)
      if (a.checkCpu === true) cliArgs.push('--check-cpu')
      if (a.write === true) cliArgs.push('--write')
      const payload = await design('renode', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || 'Renode 接入失败')
      }
      const resc = (payload.resc ?? {}) as Record<string, unknown>
      const options = (Array.isArray(payload.mcu_options) ? payload.mcu_options : [])
        .slice(0, 12).map((raw) => {
          const row = (raw ?? {}) as Record<string, unknown>
          return {
            board: str(row.board),
            mcu: str(row.mcu),
            platform: str(row.platform),
          }
        })
      return {
        project,
        ok: payload.ok === true,
        status: str(payload.status),
        runnableHere: payload.runnable_here === true,
        machines: Object.keys((resc.machines ?? {}) as object).length,
        connectors: (Array.isArray(resc.connectors) ? resc.connectors : [])
          .map((x) => str(x)),
        written: (Array.isArray(payload.written) ? payload.written : [])
          .map((x) => str(x)),
        warnings: (Array.isArray(resc.warnings) ? resc.warnings : [])
          .slice(0, 12).map((x) => str(x)),
        mcuOptions: options,
        notes: (Array.isArray(payload.notes) ? payload.notes : [])
          .map((x) => str(x)),
        summary: str(payload.summary),
      }
    },
  })))

  // ---- 3o. hw_joints ----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_joints',
    description:
      '机械关系表（`Joint`）：两个零件之间靠什么连（stud/pin/axle/gear/'
      + 'chain/tread/rack/hinge/slide/stack/port/contact）。'
      + '可从几何与网表推断 stack/port，**推断标 inferred 不参与硬处理**，'
      + '要参与 P16 仿真须先 promote。没有声明关系时状态是 not_ready，'
      + '不是通过。需要参数的 kind（gear/axle/hinge…）缺参数报 error，'
      + '**不替你编默认值**。写 joints.json。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
      infer: { type: 'boolean', description: '同时跑一次推断（stack/port）' },
      promote: { type: 'boolean', description: '把推断的提升为 declared（人确认）' },
      apply: { type: 'boolean', description: '把 declared 的写进 design.json' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          status: { type: 'string' },
          ok: { type: 'boolean' },
          declared: { type: 'number' },
          inferred: { type: 'number' },
          skipped: { type: 'number' },
          applied: { type: 'number' },
          promoted: { type: 'number' },
          byKind: { type: 'string' },
          findings: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: { check: { type: 'string' }, message: { type: 'string' } },
            },
          },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; status: string; ok: boolean
          declared: number; inferred: number; skipped: number
          applied: number; promoted: number; byKind: string
          findings: Array<{ check: string; message: string }>
          summary: string
        }
        const head = `# ${v.project} 机械关系：${v.status}`
          + `（声明 ${v.declared} / 推断 ${v.inferred} / 跳过 ${v.skipped}）`
        const body: string[] = []
        if (v.byKind) body.push(`按 kind：${v.byKind}`)
        if (v.promoted > 0) body.push(`已确认 ${v.promoted} 条（inferred → declared）`)
        if (v.applied > 0) body.push(`已写入 design.json ${v.applied} 条`)
        for (const row of v.findings.slice(0, 10)) {
          body.push(`* [${row.check}] ${row.message}`)
        }
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as {
        project?: unknown; infer?: unknown; promote?: unknown; apply?: unknown
      }
      const project = str(a.project)
      const cliArgs: string[] = project === '' ? [] : [project]
      if (a.infer === true) cliArgs.push('--infer')
      if (a.promote === true) cliArgs.push('--promote')
      if (a.apply === true) cliArgs.push('--apply')
      const payload = await design('joints', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '机械关系失败')
      }
      const inf = (payload.inference ?? {}) as Record<string, unknown>
      const byKind = (payload.by_kind ?? {}) as Record<string, unknown>
      return {
        project,
        status: str(payload.status),
        ok: payload.ok === true,
        declared: (Array.isArray(payload.declared) ? payload.declared : []).length,
        inferred: (Array.isArray(inf.joints) ? inf.joints : []).length,
        skipped: (Array.isArray(inf.skipped) ? inf.skipped : []).length,
        applied: num(payload.applied),
        promoted: num(payload.promoted),
        byKind: Object.entries(byKind)
          .map(([k, n]) => `${k} ${num(n)}`).join('、'),
        findings: (Array.isArray(payload.findings) ? payload.findings : [])
          .slice(0, 20).map((raw) => {
            const row = (raw ?? {}) as Record<string, unknown>
            return { check: str(row.check), message: str(row.message) }
          }),
        summary: str(payload.summary),
      }
    },
  })))

  // ---- 3c. hw_dims ------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_dims',
    description:
      '每颗元件的 L/W/H **从哪来**：datasheet/measured/catalog 可信，'
      + 'estimated/proposed 不可信，缺就是 unknown。不重新推导尺寸（推导只有'
      + '`easyeda._size_from_attrs` 一份），只分类 + 报告；`apply` 用立创 3D 模型'
      + '回写并标 catalog，**不冒充 datasheet**。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
      apply: { type: 'boolean', description: '把能补的尺寸写回 design.json（标 catalog）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          status: { type: 'string' },
          total: { type: 'number' },
          trusted: { type: 'number' },
          counts: { type: 'string' },
          fixable: { type: 'number' },
          unknown: { type: 'number' },
          applied: { type: 'number' },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; status: string; total: number; trusted: number
          counts: string; fixable: number; unknown: number; applied: number
          summary: string
        }
        const head = `# ${v.project} 尺寸来源：${v.status}`
          + `（可信 ${v.trusted}/${v.total}）`
        const body: string[] = []
        if (v.counts !== '') body.push(`按来源：${v.counts}`)
        if (v.fixable > 0) body.push(`能用立创 3D 模型补：${v.fixable} 颗（dims --apply）`)
        if (v.unknown > 0) body.push(`完全没有尺寸：${v.unknown} 颗`)
        if (v.applied > 0) body.push(`已回写 ${v.applied} 颗`)
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; apply?: unknown }
      const project = str(a.project)
      const cliArgs: string[] = project === '' ? [] : [project]
      if (a.apply === true) cliArgs.push('--apply')
      const payload = await design('dims', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '尺寸来源检查失败')
      }
      const counts = (payload.counts ?? {}) as Record<string, unknown>
      return {
        project,
        status: str(payload.status),
        total: num(payload.total),
        trusted: num(payload.trusted),
        counts: Object.entries(counts).map(([k, n]) => `${k} ${num(n)}`).join('、'),
        fixable: (Array.isArray(payload.fixable_by_3d) ? payload.fixable_by_3d : []).length,
        unknown: (Array.isArray(payload.unknown_refs) ? payload.unknown_refs : []).length,
        applied: (Array.isArray(payload.applied) ? payload.applied : []).length,
        summary: str(payload.note),
      }
    },
  })))

  // ---- 3f. hw_dfm -------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_dfm',
    description:
      '**制造可行性**：把 DRC 与 DFM 合成一条结论。DRC 里 `clearance/netlist/'
      + 'physical` 可信、`connection` **不可信**（不认 API 过孔，只提示）；'
      + 'DFM 只在私有化构建可用，且**结果不完整时是抛 `INCOMPLETE_RESULT` 而不是'
      + '返回失败列表** —— 那是 `unknown`，绝不能当通过。默认重判 `emit.json`，'
      + '`live=true` 才去立创里真跑。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
      live: { type: 'boolean', description: '去立创里真跑 DRC+DFM（要 CLI 在位）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          status: { type: 'string' },
          trustworthy: { type: 'number' },
          connection: { type: 'number' },
          dfmStatus: { type: 'string' },
          dfmReason: { type: 'string' },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; status: string; trustworthy: number
          connection: number; dfmStatus: string; dfmReason: string; summary: string
        }
        const head = `# ${v.project} 制造可行性：${v.status}`
        const body = [
          `可信违规 ${v.trustworthy}（clearance/netlist/physical + 几何）`,
          `Connection ${v.connection}（只提示，不进结论）`,
          `DFM ${v.dfmStatus}${v.dfmReason !== '' ? `：${v.dfmReason}` : ''}`,
        ]
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; live?: unknown }
      const project = str(a.project)
      const cliArgs: string[] = project === '' ? [] : [project]
      if (a.live === true) cliArgs.push('--live')
      const payload = await design('dfm', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '制造可行性判定失败')
      }
      const dfm = (payload.dfm ?? {}) as Record<string, unknown>
      return {
        project,
        status: str(payload.status),
        trustworthy: num(payload.trustworthy_violations),
        connection: num(payload.connection_violations),
        dfmStatus: str(dfm.status),
        dfmReason: str(dfm.reason).slice(0, 120),
        summary: str(payload.note),
      }
    },
  })))

  // ---- 3e. hw_mech ------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_mech',
    description:
      '四条**机械判据**：实体干涉（有 joint 的对不算）/ 重心是否落在支撑内 / '
      + '舵机扭矩够不够 / 关节行程够不够。三态 `pass`/`fail`/`unknown`，'
      + '缺质量或缺标称扭矩是 `unknown` —— **不是通过**。写 `mech.json`，'
      + '`hw_handoff` 会把 fail 归入阻断、unknown 归入缺数据。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          status: { type: 'string' },
          pass: { type: 'number' },
          fail: { type: 'number' },
          unknown: { type: 'number' },
          rows: { type: 'string' },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; status: string; pass: number; fail: number
          unknown: number; rows: string; summary: string
        }
        const head = `# ${v.project} 机械判据：${v.status}`
          + `（pass ${v.pass} / fail ${v.fail} / unknown ${v.unknown}）`
        const body = v.rows.split('\n').filter((x) => x !== '')
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const project = str((args as { project?: unknown }).project)
      const payload = await design('mech', ...(project === '' ? [] : [project]))
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '机械判据失败')
      }
      const counts = (payload.counts ?? {}) as Record<string, unknown>
      const rows = Array.isArray(payload.checks) ? payload.checks : []
      return {
        project,
        status: str(payload.status),
        pass: num(counts.pass),
        fail: num(counts.fail),
        unknown: num(counts.unknown),
        rows: rows
          .map((raw) => {
            const row = (raw ?? {}) as Record<string, unknown>
            return `* ${str(row.check)} → ${str(row.status)}：${str(row.message)}`
          })
          .join('\n'),
        summary: str(payload.note),
      }
    },
  })))

  // ---- 3d. hw_scaffold --------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_scaffold',
    description:
      '**编排器**：一句需求 → 一份半成品（design.json → 引脚表 → 关节提案 → '
      + '固件骨架 → MJCF）。三条纪律：route 必须显式给（synth/generate/roles）；'
      + '缺 `.ioc` 就走网表、缺关节参数就走提案，**不中止**；缺的东西进 '
      + '`open_items.md`（人读清单，不是闸门）。`to="pcb"` 才会去碰立创，'
      + '而且发射失败**不拖累**其它段。',
    parameters: {
      requirement: { type: 'string', description: '需求文件路径（req.json / .yaml）；route=synth 可省' },
      project: { type: 'string', description: '工程目录名（不存在则新建）' },
      route: { type: 'string', description: 'synth | generate | roles（必填，不猜）' },
      template: { type: 'string', description: 'route=synth 的模板（blink/button/i2c-sensor…）' },
      target: { type: 'string', description: '固件 target（arduino/platformio/esp-idf/stm32/avr）' },
      to: { type: 'string', description: 'design | fw | mech | pcb | all（默认 design+fw+mech）' },
      mcu: { type: 'string', description: '引脚表认不出 MCU 时的型号提示' },
      ioc: { type: 'string', description: 'CubeMX .ioc（有就用它，没有走网表）' },
      title: { type: 'string', description: '工程标题' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          status: { type: 'string' },
          route: { type: 'string' },
          stages: { type: 'string' },
          openItems: { type: 'number' },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; status: string; route: string
          stages: string; openItems: number; summary: string
        }
        const head = `# ${v.project} 半成品：${v.status}（route=${v.route}）`
        const body = v.stages.split('\n').filter((x) => x !== '')
        return text(head, ...body, `待办 ${v.openItems} 条`, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'emit')
      },
    },
    execute: async (args) => {
      const a = args as {
        requirement?: unknown; project?: unknown; route?: unknown
        template?: unknown; target?: unknown; to?: unknown
        mcu?: unknown; ioc?: unknown; title?: unknown
      }
      const project = str(a.project)
      const cliArgs: string[] = []
      const requirement = str(a.requirement)
      if (requirement !== '') cliArgs.push(requirement)
      cliArgs.push(`--project=${project}`)
      cliArgs.push(`--route=${str(a.route)}`)
      for (const [key, value] of [['template', a.template], ['target', a.target],
                                  ['to', a.to], ['mcu', a.mcu], ['ioc', a.ioc],
                                  ['title', a.title]] as const) {
        const text = str(value)
        if (text !== '') cliArgs.push(`--${key}=${text}`)
      }
      const payload = await design('scaffold', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '编排失败')
      }
      const stages = Array.isArray(payload.stages) ? payload.stages : []
      return {
        project,
        status: str(payload.status),
        route: str(payload.route),
        stages: stages
          .map((raw) => {
            const row = (raw ?? {}) as Record<string, unknown>
            const counts = (row.counts ?? {}) as Record<string, unknown>
            const bits = Object.entries(counts).map(([k, n]) => `${k}=${str(n)}`).join(',')
            return `* ${str(row.stage)} → ${str(row.status)}`
              + (bits !== '' ? `（${bits}）` : '')
              + (str(row.note) !== '' ? `：${str(row.note).slice(0, 80)}` : '')
          })
          .join('\n'),
        openItems: (Array.isArray(payload.open_items) ? payload.open_items : []).length,
        summary: str(payload.note),
      }
    },
  })))

  // ---- 3a2. hw_joints_propose -------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_joints_propose',
    description:
      '机械关系的**提案**：几何（stack/axle/slide）能算的算出来，'
      + '铰链行程取 `mechanical.limits.hinge_deg`；齿轮一律不提案（齿数不在模型里）。'
      + '推不出的进 `unresolved` 并写 `need`，**不编参数**。'
      + '默认只写 `joints.proposed.json`；`apply` 才进 design.json（source=proposed，'
      + '能进物理模型但会在报告里被数出来）。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
      apply: { type: 'boolean', description: '把提案写进 design.json 的 joints_list' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          status: { type: 'string' },
          proposals: { type: 'number' },
          unresolved: { type: 'number' },
          counts: { type: 'string' },
          applied: { type: 'number' },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; status: string; proposals: number
          unresolved: number; counts: string; applied: number; summary: string
        }
        const head = `# ${v.project} 关节提案：${v.status}`
          + `（${v.proposals} 条提案 / ${v.unresolved} 条推不出）`
        const body: string[] = []
        if (v.counts !== '') body.push(`按 kind：${v.counts}`)
        if (v.applied > 0) body.push(`已写入 design.json ${v.applied} 条（source=proposed）`)
        return text(head, ...body, v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; apply?: unknown }
      const project = str(a.project)
      const cliArgs: string[] = project === '' ? [] : [project]
      if (a.apply === true) cliArgs.push('--apply')
      const payload = await design('joints-propose', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '关节提案失败')
      }
      const counts = (payload.counts ?? {}) as Record<string, unknown>
      return {
        project,
        status: str(payload.status),
        proposals: (Array.isArray(payload.proposals) ? payload.proposals : []).length,
        unresolved: (Array.isArray(payload.unresolved) ? payload.unresolved : []).length,
        counts: Object.entries(counts).map(([k, n]) => `${k} ${num(n)}`).join('、'),
        applied: (Array.isArray(payload.applied) ? payload.applied : []).length,
        summary: str(payload.note),
      }
    },
  })))

  // ---- 3b. hw_mjcf ------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_mjcf',
    description:
      '机械模型（MJCF）：把 design.json + joints 编成 MuJoCo 模型，并**真的'
      + '加载一次**。判据落在"MuJoCo 编译通过"上，不是"我拼出了字符串"。'
      + '没装 MuJoCo 时是 not_ready（没验证过 ≠ 通过）。`--build` 落 model.xml。'
      + '关节范围要 `[lo, hi]`（或 min_deg/max_deg）：标量有歧义（±180 还是 0..180），'
      + '**不猜**，直接跳过那个关节。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
      build: { type: 'boolean', description: '落盘 model.xml（不给我就只检查不写）' },
      free: { type: 'boolean', description: '零件自由运动（默认焊死在世界里）' },
      weld: { type: 'boolean', description: '每块板作为一个刚体（PCB 的正确力学模型）' },
      noFloor: { type: 'boolean', description: '不加地板（默认加）' },
      out: { type: 'string', description: '自定义输出路径（配合 build）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          status: { type: 'string' },
          ok: { type: 'boolean' },
          partsTotal: { type: 'number' },
          partsSimulated: { type: 'number' },
          partsSkipped: { type: 'number' },
          jointsSkipped: { type: 'number' },
          loadStatus: { type: 'string' },
          njnt: { type: 'number' },
          nbody: { type: 'number' },
          xmlChars: { type: 'number' },
          modelPath: { type: 'string' },
          notes: { type: 'array', items: { type: 'string' } },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; status: string; ok: boolean
          partsTotal: number; partsSimulated: number
          partsSkipped: number; jointsSkipped: number
          loadStatus: string; njnt: number; nbody: number
          xmlChars: number; modelPath: string
          notes: string[]; summary: string
        }
        const head = `# ${v.project} 机械模型：${v.status}`
          + `（零件 ${v.partsSimulated}/${v.partsTotal}，关节 ${v.njnt}）`
        const body: string[] = []
        body.push(`MuJoCo 加载：${v.loadStatus}（nbody ${v.nbody}，XML ${v.xmlChars} 字）`)
        if (v.partsSkipped > 0) body.push(`跳过零件：${v.partsSkipped}`)
        if (v.jointsSkipped > 0) body.push(`跳过关节：${v.jointsSkipped}`)
        if (v.modelPath !== '') body.push(`model.xml：${v.modelPath}`)
        for (const note of v.notes.slice(0, 6)) body.push(`* ${note}`)
        return text(head, ...body, v.summary)
      },
      // 产物落在工程目录里，界面暂时没有机械分区（P46 才做）——
      // 指到有内容的「判定」页，而不是编一个不存在的分区（§3.13 那个坑）。
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as {
        project?: unknown; build?: unknown; free?: unknown
        weld?: unknown; noFloor?: unknown; out?: unknown
      }
      const project = str(a.project)
      const cliArgs: string[] = project === '' ? [] : [project]
      if (a.build === true) cliArgs.push('--build')
      if (a.free === true) cliArgs.push('--free')
      if (a.weld === true) cliArgs.push('--weld')
      if (a.noFloor === true) cliArgs.push('--no-floor')
      const out = str(a.out)
      if (out !== '') cliArgs.push(`--out=${out}`)
      const payload = await design('mjcf', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || 'MJCF 构建失败')
      }
      const stats = (payload.stats ?? {}) as Record<string, unknown>
      const load = (payload.load ?? {}) as Record<string, unknown>
      const xml = (payload.xml ?? {}) as Record<string, unknown>
      const notes = Array.isArray(payload.notes)
        ? (payload.notes as unknown[]).map((n) => str(n)) : []
      const skipped = Array.isArray(stats.skipped_reasons)
        ? (stats.skipped_reasons as unknown[]).length : 0
      return {
        project,
        status: str(payload.status),
        ok: payload.status === 'ok',
        partsTotal: num(stats.parts_total),
        partsSimulated: num(stats.parts_simulated),
        partsSkipped: num(stats.parts_skipped),
        jointsSkipped: num(stats.joints_skipped),
        loadStatus: str(load.status),
        njnt: num(load.njnt),
        nbody: num(load.nbody),
        xmlChars: num(xml.chars),
        modelPath: str(xml.path),
        notes,
        summary: skipped > 0
          ? `有 ${skipped} 项被跳过：模型不完整，逐条原因在 stats.skipped_reasons`
          : (str(payload.status) === 'ok'
            ? '模型完整且 MuJoCo 已编译通过'
            : '见 notes：状态不是 ok 时不要当成验过'),
      }
    },
  })))

  // ---- 4. hw_import -----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_import',
    description:
      '把一个 KiCad PCB / Wokwi diagram / Velxio 工程导入成工作台工程（可附 BOM）。' +
      '写操作。导入后 2D 网表与 3D 实体同时成立。',
    parameters: {
      project: { type: 'string', description: '目标工程 id（不存在则新建）' },
      path: { type: 'string', description: '.kicad_pcb / .json 的绝对或相对路径' },
      bomPath: { type: 'string', description: '可选：BOM CSV 路径' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          parts: { type: 'number' },
          nets: { type: 'number' },
          warnings: { type: 'array', items: { type: 'string' } },
          bomUnmatched: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; parts: number; nets: number
          warnings: string[]; bomUnmatched: string[]
        }
        return text(
          `导入成功：${v.project} — ${v.parts} 个元件 / ${v.nets} 条网表`,
          ...(v.bomUnmatched.length > 0
            ? [`BOM 未匹配：${v.bomUnmatched.join(', ')}`] : []),
          ...(v.warnings.length > 0 ? ['', ...v.warnings.map((w) => `! ${w}`)] : []),
        )
      },
      presentationMeta: (args) => viewHint(requireProject(args), '2d'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const path = str((args as { path?: unknown }).path)
      if (path.trim() === '') throw new Error('需要 path（要导入的文件）')
      const bomPath = str((args as { bomPath?: unknown }).bomPath)
      const argv = ['import', project, path]
      if (bomPath !== '') argv.push('--bom', bomPath)
      const payload = await design(...argv)
      if (!ok(payload)) {
        throw new Error(findings(payload).join('；') || '导入失败')
      }
      noteHint(project, '2d')
      const summary = (payload.summary ?? {}) as Record<string, unknown>
      return {
        project,
        parts: num(summary.parts),
        nets: num(summary.nets),
        warnings: ((payload.warnings ?? []) as unknown[]).map(String),
        bomUnmatched: ((payload.bom_unmatched ?? []) as unknown[]).map(String),
      }
    },
  })))

  // ---- 5. hw_budget -----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_budget',
    description:
      '评估五轨预算（功率/热/EMC/时序/成本），写入 budget.json。' +
      '没有 claim 的元件不算通过，会明确报"无法评估"。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      limitsJson: { type: 'string', description: '可选：JSON 上限，如 {"power_w":3.3}' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          confidence: { type: 'string' },
          violations: { type: 'number' },
          lines: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; confidence: string; violations: number; lines: string[]
        }
        const label = v.confidence === 'none' ? '无法评估（没有 claim）'
          : v.confidence === 'partial' ? '部分覆盖' : '全覆盖'
        return text(`# ${v.project} 预算：${label}，${v.violations} 处超限`,
          ...(v.lines.length > 0 ? v.lines : ['无超限项']))
      },
      presentationMeta: (args) => viewHint(requireProject(args), 'budget'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const limits = str((args as { limitsJson?: unknown }).limitsJson)
      const argv = ['budget', project]
      if (limits !== '') argv.push('--limits', limits)
      const payload = await design(...argv)
      // `not_ready` 在这里**不是错误**：预算在"没有任何元件声明功耗/成本"时
      // 就是无法评估（`budgets.evaluate` 明确返回 status=not_ready +
      // confidence=none）。把它 throw 掉会让面板在干净工程和 141 件全未
      // claim 的真实工程上同样打不开——正是本仓库反对的"把缺失当失败"。
      // 正确行为：把"无法评估 + 覆盖率"如实呈现出来。
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '预算评估失败')
      }
      noteHint(project, 'budget')
      const byCurrency = (payload.by_currency ?? {}) as Record<string, Record<string, unknown>>
      const coverage = (payload.coverage ?? {}) as Record<string, unknown>
      const claimed = num(coverage.claimed_parts)
      const total = num(coverage.total_parts)
      const confidence = str(payload.confidence, 'none')
      const lines: string[] = Object.entries(byCurrency)
        .filter(([, row]) => row.over === true)
        .map(([currency, row]) =>
          `✗ ${currency}: ${num(row.used)} / ${num(row.limit)}`)
      if (confidence === 'none') {
        lines.unshift(
          `没有可评估的数据：${claimed} / ${total} 个元件声明了功耗/成本。`
          + '这不是"通过"，是"没数据"。',
          '补 claim：hw_setpart 传 {"electrical":{"power_w":…,"cost_usd":…}}',
        )
      } else if (total > claimed) {
        lines.unshift(`覆盖率 ${claimed} / ${total} 个元件（其余按未知处理，结论是下界）`)
      }
      return {
        project,
        confidence,
        violations: ((payload.violations ?? []) as unknown[]).length,
        lines,
      }
    },
  })))

  // ---- 6. hw_crosscheck -------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_crosscheck',
    description:
      '交叉核对：画图几何 vs 物理事实，以及 2D 投影↔3D 实体一致性。写入 ' +
      'crosscheck.json。"没有数据"进未验证，不算矛盾。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          mismatches: { type: 'number' },
          unverified: { type: 'number' },
          lines: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; mismatches: number; unverified: number; lines: string[]
        }
        return text(
          `# ${v.project} 核对：${v.mismatches} 处差异，${v.unverified} 项未验证`,
          ...(v.lines.length > 0 ? v.lines : ['没有差异']),
        )
      },
      presentationMeta: (args) => viewHint(requireProject(args), 'crosscheck'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const payload = await design('crosscheck', project)
      if (!ok(payload)) throw new Error(findings(payload).join('；') || '核对失败')
      noteHint(project, 'crosscheck')
      const summary = (payload.summary ?? {}) as Record<string, unknown>
      const rows = (payload.mismatches ?? []) as Array<Record<string, unknown>>
      return {
        project,
        mismatches: num(summary.mismatches),
        unverified: num(summary.unverified),
        lines: rows.map((row) =>
          `${row.severity === 'error' ? '✗' : '!'} ${str(row.message)}`),
      }
    },
  })))

  // ---- 7. hw_export -----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_export',
    description:
      '导出制造包（BOM/贴片坐标/板轮廓 DXF/规范模型/README）。' +
      '不可逆决策未闭合时**拒绝导出**并说明原因。不伪造 Gerber。写操作。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          blocked: { type: 'boolean' },
          path: { type: 'string' },
          written: { type: 'array', items: { type: 'string' } },
          missing: { type: 'array', items: { type: 'string' } },
          reason: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; blocked: boolean; path: string
          written: string[]; missing: string[]; reason: string
        }
        if (v.blocked) {
          return text(`导出被拒绝：${v.reason}`,
            '先把不可逆决策闭合（面板「未决」页，或 deferred lock），这不是可以绕过的提示。')
        }
        return text(`制造包已写出：${v.path}`,
          ...v.written.map((name) => `· ${name}`),
          '',
          '不含（如实声明）：',
          ...v.missing.map((name) => `- ${name}`))
      },
      presentationMeta: (args) => viewHint(requireProject(args), 'export'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const payload = await design('export', project)
      noteHint(project, 'export')
      if (payload.status === 'not_ready') {
        return {
          project,
          blocked: true,
          path: '',
          written: [],
          missing: [],
          reason: findings(payload).join('；') || '不可逆决策未闭合',
        }
      }
      if (!ok(payload)) throw new Error(findings(payload).join('；') || '导出失败')
      return {
        project,
        blocked: false,
        path: str(payload.path),
        written: ((payload.written ?? []) as unknown[]).map(String),
        missing: ((payload.missing ?? []) as unknown[]).map(String),
        reason: '',
      }
    },
  })))

  // ---- 8. hw_block ------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_block',
    description:
      '跑一个 hwforge 积木（assemble/import/modify/coverify/simulate/generate/' +
      'build/electrical/internals/review/fw_check/interfaces/…）做跨层判定。写操作。' +
      '**hwforge 是可选增强**：没 clone 时返回 not_ready 并说明原因；' +
      'hw_velxio 自己的判定（hw_check/hw_budget/hw_crosscheck）不依赖它。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      block: { type: 'string', description: '积木名' },
      paramsJson: { type: 'string', description: '可选：积木参数 JSON' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          block: { type: 'string' },
          status: { type: 'string' },
          findings: { type: 'array', items: { type: 'string' } },
          wrote: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; block: string; status: string
          findings: string[]; wrote: string[]
        }
        return text(`# ${v.project} · ${v.block} → ${v.status}`,
          ...(v.findings.length > 0 ? v.findings : ['（无发现）']),
          ...(v.wrote.length > 0 ? ['', `写出：${v.wrote.join(', ')}`] : []))
      },
      presentationMeta: (args) => viewHint(requireProject(args), 'blocks'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const block = str((args as { block?: unknown }).block)
      if (block.trim() === '') throw new Error('需要 block（积木名）')
      const paramsJson = str((args as { paramsJson?: unknown }).paramsJson, '{}')
      try {
        JSON.parse(paramsJson)
      } catch {
        throw new Error(`paramsJson 不是合法 JSON：${paramsJson.slice(0, 120)}`)
      }
      const payload = await bridge('run', project, block, paramsJson)
      noteHint(project, 'blocks')
      return {
        project,
        block,
        status: str(payload.status, 'failed'),
        findings: ((payload.findings ?? []) as unknown[]).map(String),
        wrote: ((payload.wrote ?? []) as unknown[]).map(String),
      }
    },
  })))

  // ---- 9. hw_survey -----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_survey',
    description:
      '勘察工作区：列出所有工程及其**确定性推导**的阶段（empty/partial/complete/'
      + 'broken）、每个工程差什么、以及工程之外的相关文件。write=true 时同时'
      + '刷新工作区里的「初审.md」（机器事实段重写，AI 判断段保留）。'
      + '写初审.md 是写操作；不传 write 则纯只读。',
    parameters: {
      write: { type: 'boolean', description: '可选：同时写/刷新初审.md' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          workspace: { type: 'string' },
          fingerprint: { type: 'string' },
          projects: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                id: { type: 'string' },
                phase: { type: 'string' },
                parts: { type: 'number' },
                veto: { type: 'array', items: { type: 'string' } },
              },
            },
          },
          counts: { type: 'string' },
          initialReview: { type: 'string' },
          written: { type: 'boolean' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          workspace: string; fingerprint: string
          projects: Array<{ id: string; phase: string; parts: number; veto: string[] }>
          counts: string; initialReview: string; written: boolean
        }
        return text(
          `# 工作区勘察 — ${v.workspace}`,
          `指纹：${v.fingerprint}　${v.counts}`,
          '',
          ...(v.projects.length === 0
            ? ['还没有工程。']
            : v.projects.map((p) => `· ${p.id} [${p.phase}] ${p.parts} 元件`
              + (p.veto.length > 0 ? `　差：${p.veto.join('；')}` : ''))),
          '',
          v.written
            ? `初审.md 已刷新：${v.initialReview}`
            : `初审.md 位置（未写）：${v.initialReview}`,
          '',
          '阶段是**确定性推导**，不是判断。',
          '下一步：把「事实」段作为依据，在初审.md 的「判断」段里写你的结论；',
          '要适配某个工程，用 hw_put 提交设计（model 会校验），而不是手写 JSON 文件。',
        )
      },
    },
    execute: async (args) => {
      const write = (args as { write?: unknown }).write === true
      const payload = await design('survey', ...(write ? ['--write'] : []))
      if (!ok(payload)) {
        throw new Error(`勘察失败：${findings(payload).join('；') || '未知原因'}`)
      }
      const projects = rowsOf(payload, 'projects').map((row) => ({
        id: str(row.id),
        phase: str(row.phase),
        parts: num(row.parts),
        veto: ((row.veto ?? []) as unknown[]).map(String),
      }))
      const counts = (payload.counts ?? {}) as Record<string, unknown>
      return {
        workspace: str(payload.workspace),
        fingerprint: str(payload.fingerprint),
        projects,
        counts: `${num(counts.projects)} 工程（空 ${num(counts.empty)} / 半成品 `
          + `${num(counts.partial)} / 成品 ${num(counts.complete)}）· `
          + `${num(counts.loose_files)} 个工程外相关文件`,
        initialReview: str(payload.initial_review),
        written: payload.written === true,
      }
    },
  })))

  // ---- 10. hw_new -------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_new',
    description:
      '在工作区里新建一个空工程（用板卡预设给板轮廓/安装孔）。写操作。'
      + '已存在同名工程会失败而不是覆盖。',
    parameters: {
      project: { type: 'string', description: '工程目录名（=工程 id）' },
      board: { type: 'string', description: '可选：板卡预设 kind，如 arduino-uno' },
      title: { type: 'string', description: '可选：标题' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          board: { type: 'string' },
          parts: { type: 'number' },
          path: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as { project: string; board: string; parts: number; path: string }
        return text(`已新建工程 ${v.project}（板卡 ${v.board}，${v.parts} 元件）`,
          `目录：${v.path}`,
          '',
          '它现在是 empty 阶段。用 hw_import 导入文件，或 hw_put 提交完整设计。')
      },
      presentationMeta: (args) => viewHint(requireProject(args), '2d'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const board = str((args as { board?: unknown }).board, 'arduino-uno')
      const title = str((args as { title?: unknown }).title, project)
      const payload = await design('new', project, board, title)
      if (!ok(payload)) {
        throw new Error(findings(payload).join('；') || '新建失败')
      }
      noteHint(project, 'design')
      // `design_cli new` 的载荷形状是 {status, project:{id,path,parts,…},
      // workspace, design, warnings} —— **不是** {path, summary}。
      // 以前读 payload.path / payload.summary 恒为空，面板于是显示
      // "目录：（空）""0 元件"，看起来像新建失败了但其实成功了。
      const created = (payload.project ?? {}) as Record<string, unknown>
      return {
        project,
        board: str(created.board_name, board),
        parts: num(created.parts),
        path: str(created.path, ''),
      }
    },
  })))

  // ---- 11. hw_put -------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_put',
    description:
      '提交一份完整 design.json（走 DesignModel 校验后落盘）。**这是唯一受校验的'
      + '设计写入路径**——不要用通用文件工具直接写 design.json，那条路绕过 '
      + 'validate()，会把不合法的设计留在磁盘上。返回 problems 列表（可以为空）。',
    parameters: {
      project: { type: 'string', description: '工程目录名（不存在则新建）' },
      designJson: { type: 'string', description: '完整 design.json 内容（字符串）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          parts: { type: 'number' },
          nets: { type: 'number' },
          problems: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; parts: number; nets: number; problems: string[]
        }
        return text(
          `已写入 ${v.project}：${v.parts} 元件 / ${v.nets} 网表`,
          '',
          ...(v.problems.length === 0
            ? ['模型校验：没有问题。']
            : ['模型校验发现的问题（已落盘，必须逐条修）：',
               ...v.problems.map((line) => `! ${line}`)]),
        )
      },
      presentationMeta: (args) => viewHint(requireProject(args), '2d'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const raw = str((args as { designJson?: unknown }).designJson)
      if (raw.trim() === '') throw new Error('需要 designJson（完整设计 JSON 字符串）')
      let parsed: unknown
      try {
        parsed = JSON.parse(raw)
      } catch (error) {
        throw new Error(`designJson 不是合法 JSON：`
          + (error instanceof Error ? error.message : String(error)))
      }
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('designJson 必须是一个 JSON 对象')
      }
      const payload = await designPut(project, raw)
      if (!ok(payload)) {
        throw new Error(findings(payload).join('；') || '写入被模型拒绝')
      }
      noteHint(project, 'design')
      const summary = (payload.summary ?? {}) as Record<string, unknown>
      return {
        project,
        parts: num(summary.parts),
        nets: num(summary.nets),
        problems: ((payload.problems ?? []) as unknown[]).map(String),
      }
    },
  })))

  // ---- 12. hw_setpart ---------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_setpart',
    description:
      '改一个元件的字段（label/role/size_mm/physical_source/electrical 等）。'
      + '写操作。尺寸未知就**留空并标 estimated**，不要编造 datasheet 数值。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      ref: { type: 'string', description: '元件参考号，如 R1 / U3' },
      patchJson: { type: 'string', description: '要改的字段（JSON 对象字符串）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          ref: { type: 'string' },
          problems: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as { project: string; ref: string; problems: string[] }
        return text(`已修改 ${v.project} 的 ${v.ref}`,
          ...(v.problems.length > 0
            ? ['', '模型校验发现的问题：', ...v.problems.map((line) => `! ${line}`)]
            : []))
      },
      presentationMeta: (args) => viewHint(requireProject(args), '2d'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const ref = str((args as { ref?: unknown }).ref)
      if (ref.trim() === '') throw new Error('需要 ref（元件参考号）')
      const patchJson = str((args as { patchJson?: unknown }).patchJson)
      if (patchJson.trim() === '') throw new Error('需要 patchJson')
      let parsed: unknown
      try {
        parsed = JSON.parse(patchJson)
      } catch (error) {
        throw new Error(`patchJson 不是合法 JSON：`
          + (error instanceof Error ? error.message : String(error)))
      }
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('patchJson 必须是一个 JSON 对象')
      }
      const payload = await design('setpart', project, ref, patchJson)
      if (!ok(payload)) {
        throw new Error(findings(payload).join('；') || '修改被拒')
      }
      noteHint(project, '2d')
      return {
        project,
        ref,
        problems: ((payload.problems ?? []) as unknown[]).map(String),
      }
    },
  })))

  // ---- 13. hw_deferred --------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_deferred',
    description:
      '读或闭合不可逆决策账本。action=show 只读；action=lock 是**写**操作且'
      + '**不可撤销**（lock 只追加，没有 unlock）。闭合的决策决定能不能导出。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      action: { type: 'string', description: 'show（默认）或 lock' },
      name: { type: 'string', description: 'lock 时必填：决策名，如 package_family' },
      value: { type: 'string', description: 'lock 时必填：选定值' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          openDecisions: { type: 'array', items: { type: 'string' } },
          locked: { type: 'array', items: { type: 'string' } },
          deliverable: { type: 'boolean' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; openDecisions: string[]; locked: string[]; deliverable: boolean
        }
        return text(
          `# ${v.project} 的决策账本`,
          v.deliverable
            ? '交付闸门：已闭合，可导出'
            : `交付闸门：未闭合（${v.openDecisions.join(', ')}）`,
          '',
          ...(v.locked.length > 0
            ? ['已锁定：', ...v.locked.map((line) => `· ${line}`)]
            : ['已锁定：无']),
          ...(v.openDecisions.length > 0
            ? ['', '仍待决定（不闭合不能导出）：',
               ...v.openDecisions.map((line) => `· ${line}`)]
            : []),
        )
      },
      presentationMeta: (args) => viewHint(requireProject(args), 'deferred'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const action = str((args as { action?: unknown }).action, 'show').trim() || 'show'
      const name = str((args as { name?: unknown }).name)
      const value = str((args as { value?: unknown }).value)
      const argv = ['deferred', project, action]
      if (action === 'lock') {
        if (name.trim() === '') throw new Error('lock 需要 name')
        if (value.trim() === '') throw new Error('lock 需要 value')
        argv.push(name.trim(), value.trim())
      }
      const payload = await design(...argv)
      if (!ok(payload)) {
        throw new Error(findings(payload).join('；') || '决策账本操作失败')
      }
      noteHint(project, 'deferred')
      const open = ((payload.open_irreversible ?? []) as unknown[]).map(String)
      const locked = rowsOf(payload, 'locked')
        .map((row) => `${str(row.name)} = ${str(row.value)}`)
      return {
        project,
        openDecisions: open,
        locked,
        deliverable: payload.deliverable === true,
      }
    },
  })))

  // ---- 14. hw_workspace -------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_workspace',
    description:
      '读当前工作区（工程住的目录），或切换到一个已存在的文件夹。'
      + '切换是**写**操作：之后的相对路径都按新工作区解析。',
    parameters: {
      path: { type: 'string', description: '可选：要切换到的工作区绝对路径' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          workspace: { type: 'string' },
          source: { type: 'string' },
          writable: { type: 'boolean' },
          changed: { type: 'boolean' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          workspace: string; source: string; writable: boolean; changed: boolean
        }
        return text(
          `${v.changed ? '已切换工作区' : '当前工作区'}：${v.workspace}`,
          `来源：${v.source}　可写：${v.writable ? '是' : '否'}`,
          ...(v.writable ? [] : ['', '这个目录不可写：保存会失败。换一个文件夹或修好权限。']),
        )
      },
    },
    execute: async (args) => {
      const path = str((args as { path?: unknown }).path)
      const argv = path.trim() === '' ? ['workspace', 'show'] : ['workspace', 'set', path.trim()]
      const payload = await design(...argv)
      if (!ok(payload)) {
        throw new Error(findings(payload).join('；') || '工作区操作失败')
      }
      if (path.trim() !== '') noteHint('', 'workspace')
      return {
        workspace: str(payload.workspace),
        source: str(payload.source, 'unknown'),
        writable: payload.writable !== false,
        changed: path.trim() !== '',
      }
    },
  })))

  // ---- 15. hw_synth -----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_synth',
    description:
      '按需求**从 0 组装**一个设计（元件 + 网表 + 板框），走模板而不是让模型'
      + '自由写 JSON：模板保证每条网表的引脚名都真实存在，组装完还会自检。'
      + '写操作。模板：blink / button / led-bar / i2c-sensor / ultrasonic / '
      + 'servo / buzzer。覆盖范围有限（模拟电路、电源树、多板互联没有路径）。',
    parameters: {
      project: { type: 'string', description: '工程目录名（已存在会失败）' },
      template: { type: 'string', description: '模板名，如 blink' },
      specJson: { type: 'string', description: '可选：参数 JSON，如 {"mcu":"arduino-nano","pin":"D13"}' },
      title: { type: 'string', description: '可选：标题' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          template: { type: 'string' },
          parts: { type: 'number' },
          nets: { type: 'number' },
          board: { type: 'string' },
          problems: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; template: string; parts: number; nets: number
          board: string; problems: string[]
        }
        return text(
          `已组装 ${v.project}（模板 ${v.template}）：${v.parts} 元件 / ${v.nets} 网表`,
          `板框：${v.board}`,
          '',
          ...(v.problems.length === 0
            ? ['模型校验：没有问题。']
            : ['模型校验发现的问题：', ...v.problems.map((line) => `! ${line}`)]),
          '',
          '下一步：hw_scene 转成仿真场景，或 hw_check 跑物理检查。',
        )
      },
      presentationMeta: (args) => viewHint(requireProject(args), '2d'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const template = str((args as { template?: unknown }).template)
      if (template.trim() === '') throw new Error('需要 template（如 blink）')
      const specJson = str((args as { specJson?: unknown }).specJson, '')
      const title = str((args as { title?: unknown }).title)
      const argv = ['synth', project, template.trim()]
      if (specJson.trim() !== '') {
        try {
          JSON.parse(specJson)
        } catch {
          throw new Error(`specJson 不是合法 JSON：${specJson.slice(0, 120)}`)
        }
        argv.push('--spec', specJson)
      }
      if (title !== '') argv.push('--title', title)
      const payload = await design(...argv)
      if (!ok(payload)) {
        throw new Error(findings(payload).join('；') || '组装失败')
      }
      noteHint(project, 'design')
      const summary = (payload.summary ?? {}) as Record<string, unknown>
      const board = (payload.board ?? {}) as Record<string, unknown>
      const size = (board.size_mm ?? {}) as Record<string, unknown>
      return {
        project,
        template: str(payload.template),
        parts: num(summary.parts),
        nets: num(summary.nets),
        board: `${str(board.name)} ${num(size.w)}×${num(size.h)}mm`,
        problems: ((payload.problems ?? []) as unknown[]).map(String),
      }
    },
  })))

  // ---- 15.5 hw_roles ----------------------------------------------------
  //
  // R0：需求入口。**没有这个工具，模型永远不知道这条路存在** ——
  // 在此之前"需求 → 装配"只能靠人去跑 CLI。
  disposers.push(tools.register(defineTool({
    name: 'hw_roles',
    description:
      '按**需求**推导装配方案：需求 → 角色（camera/gimbal_pan/mcu…）→ '
      + '装配计划（模板 + 尺寸）→ 可选直接装成设计。'
      + '默认只**出计划不写盘**；assemble=true 才落盘。'
      + '角色→模板的映射表会对照 hwforge 真实模板校验，映射不到的角色如实报出，'
      + '不猜最像的。写操作（仅当 assemble=true）。',
    parameters: {
      goal: {
        type: 'string',
        description: '自然语言需求，如「做一个壁挂云台相机，手机看实时视频」。'
          + '与 roles 二选一或同给（同给时 roles 优先）',
      },
      roles: {
        type: 'string',
        description: '直接给角色（逗号分隔），跳过需求分析，如 camera,gimbal_pan,mcu',
      },
      project: { type: 'string', description: '可选：给了就落到这个工程' },
      assemble: {
        type: 'boolean',
        description: 'true = 装配成设计并落盘（需要 project）；默认 false 只出计划',
      },
      title: { type: 'string', description: '可选：工程标题' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          roles: { type: 'array', items: { type: 'string' } },
          addedRoles: { type: 'array', items: { type: 'string' } },
          parts: { type: 'number' },
          nets: { type: 'number' },
          unmatched: { type: 'array', items: { type: 'string' } },
          unrouted: { type: 'array', items: { type: 'string' } },
          assembled: { type: 'boolean' },
          project: { type: 'string' },
          unresolved: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          roles: string[]; addedRoles: string[]; parts: number; nets: number
          unmatched: string[]; unrouted: string[]; assembled: boolean
          project: string; unresolved: string[]
        }
        const lines = [
          `角色：${v.roles.join('、') || '（无）'}`,
          v.addedRoles.length > 0
            ? `自动补的角色：${v.addedRoles.join('、')}`
            : '',
          `装配计划：${v.parts} 个元件` + (v.assembled ? ` / ${v.nets} 条网表（已落盘 ${v.project}）` : '（未落盘）'),
          v.unmatched.length > 0
            ? `没有映射的角色：${v.unmatched.join('、')} —— 这些需求会被丢掉`
            : '',
          v.unrouted.length > 0
            ? `接不上的引脚 ${v.unrouted.length} 个：`
            : '',
          ...v.unrouted.slice(0, 6).map((line) => `  - ${line}`),
          v.unresolved.length > 0
            ? `需求未决 ${v.unresolved.length} 项：${v.unresolved.slice(0, 5).join('、')}`
            : '',
        ].filter((line) => line !== '')
        return text(...lines)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project).trim()
        // 没有 project 时不给视图提示（只出计划，没有画布可看）。
        // `viewHint` 返回 JsonValue，所以这里用空对象而不是 undefined。
        return project === '' ? {} : viewHint(project, 'blocks')
      },
    },
    execute: async (args) => {
      const goal = str((args as { goal?: unknown }).goal).trim()
      const roles = str((args as { roles?: unknown }).roles).trim()
      const project = str((args as { project?: unknown }).project).trim()
      const title = str((args as { title?: unknown }).title).trim()
      const doAssemble = (args as { assemble?: unknown }).assemble === true
      if (goal === '' && roles === '') {
        throw new Error('需要 goal（自然语言需求）或 roles（逗号分隔的角色）之一')
      }
      if (doAssemble && project === '') {
        throw new Error('assemble=true 需要 project（装配要落到一个工程里）')
      }
      const argv = ['roles']
      if (roles !== '') argv.push('--roles', roles)
      if (goal !== '') argv.push('--goal', goal)
      if (doAssemble) {
        argv.push('--assemble', project)
        if (title !== '') argv.push('--title', title)
      }
      const payload = await design(...argv)
      // 映射表校验失败（角色没映射/模板缺失）时 `status` 是 not_ready，
      // 但那**不等于执行失败** —— 计划本身可能是可用的，只是有缺口。
      // 所以这里不抛错，把缺口原样交给模型。
      if (doAssemble && !ok(payload)) {
        throw new Error(findings(payload).join('；') || '装配失败')
      }
      if (doAssemble) noteHint(project, 'design')
      // `design_cli roles` 的载荷只有 {roles, plan:{part_count,parts,
      // unmatched,…}, role_check, role_enhancement, unresolved, summary}。
      // **没有 `wiring`** —— 以前读 payload.wiring 恒得到空对象，于是这个
      // 工具永远报"0 条网表"，而实际上装配成功后网表就在 summary 里。
      const plan = (payload.plan ?? {}) as Record<string, unknown>
      const enhance = (payload.role_enhancement ?? {}) as Record<string, unknown>
      const summary = (payload.summary ?? {}) as Record<string, unknown>
      const assembled = (payload.assembled ?? {}) as Record<string, unknown>
      // 网表数有三个可能来源，按可靠性排序；都没有就如实留 0 并说明未落盘。
      const nets = doAssemble
        ? num(assembled.nets) || num(summary.nets)
        : 0
      return {
        roles: ((payload.roles ?? []) as unknown[]).map(String),
        addedRoles: ((enhance.added ?? []) as unknown[]).map(String),
        parts: num(plan.part_count),
        nets,
        unmatched: ((plan.unmatched ?? []) as unknown[]).map(String),
        unrouted: ((assembled.unrouted ?? payload.unrouted ?? []) as unknown[]).map(String),
        assembled: doAssemble,
        project: doAssemble ? project : '',
        unresolved: ((payload.unresolved ?? []) as unknown[]).map(String),
      }
    },
  })))

  // ---- 16. hw_scene -----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_scene',
    description:
      '把工程转成 hwforge 能读的 scene.json（体素场景），打通仿真/电气判定链。'
      + '写操作（在工程目录里生成 scene.json）。转换后可用 hw_block 跑 '
      + 'simulate / electrical / coverify。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      pitchMm: { type: 'string', description: '可选：体素间距 mm（默认 0.5）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          placements: { type: 'number' },
          runs: { type: 'number' },
          dims: { type: 'string' },
          unverified: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; placements: number; runs: number
          dims: string; unverified: string[]
        }
        return text(
          `已生成 scene.json：${v.placements} 个元件 / ${v.runs} 条连线`,
          `体素尺寸：${v.dims}`,
          '',
          ...(v.unverified.length === 0
            ? ['没有未验证项。']
            : ['未验证（不是通过）：', ...v.unverified.map((line) => `? ${line}`)]),
          '',
          '现在可以 hw_block(simulate) 跑仿真。',
        )
      },
      presentationMeta: (args) => viewHint(requireProject(args), 'checks'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const pitch = str((args as { pitchMm?: unknown }).pitchMm, '')
      const payload = await design('scene', project,
        ...(pitch !== '' ? ['--pitch', pitch] : []))
      if (!ok(payload)) throw new Error(findings(payload).join('；') || '转换失败')
      noteHint(project, 'checks')
      const summary = (payload.summary ?? {}) as Record<string, unknown>
      const dims = (summary.dims_voxels ?? []) as unknown[]
      return {
        project,
        placements: num(summary.placements),
        runs: num(summary.runs),
        dims: `${dims.map((n) => num(n)).join('×')} 体素`,
        unverified: ((payload.unverified ?? []) as unknown[]).map(String),
      }
    },
  })))

  // ---- 17. hw_log -------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_log',
    description:
      '读工程式日志（写在 `<工作区>/logs/`，**只追加**）。action=list 列文件、'
      + 'read 读某一份、coverage 看"哪些文件读了、哪些没读"。'
      + 'write 是**写**操作：追加一条活动记录。',
    parameters: {
      action: {
        type: 'string',
        description: 'list（默认）/ read / coverage / append（追加一条活动记录，写操作）',
      },
      name: { type: 'string', description: 'read 时必填：日志文件名' },
      step: { type: 'string', description: 'append 时必填：步骤名' },
      outcome: { type: 'string', description: 'append 时必填：结果（ok/failed/跳过…）' },
      detail: { type: 'string', description: 'append 时可选：详情' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          action: { type: 'string' },
          dir: { type: 'string' },
          files: { type: 'array', items: { type: 'string' } },
          coverage: { type: 'string' },
          text: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          action: string; dir: string; files: string[]
          coverage: string; text: string
        }
        if (v.action === 'coverage') return text(v.coverage)
        if (v.action === 'read') return text(v.text)
        if (v.action === 'append') return text(v.text)
        return text(
          `# 日志目录：${v.dir}`,
          '',
          ...(v.files.length === 0 ? ['还没有日志。'] : v.files),
          '',
          '用 action=coverage 看读取覆盖度（哪些文件读了、哪些没读）。',
        )
      },
    },
    execute: async (args) => {
      const action = str((args as { action?: unknown }).action, 'list').trim() || 'list'
      // append 分支：描述里承诺了"write 是写操作：追加一条活动记录"，但此前
      // 参数表与 execute 都没有这个分支 —— 工具描述在撒谎。现在接上
      // `design_cli log append <step> <outcome> <detail>`。
      if (action === 'append' || action === 'write') {
        const step = str((args as { step?: unknown }).step).trim()
        const outcome = str((args as { outcome?: unknown }).outcome).trim()
        if (step === '') throw new Error('append 需要 step（步骤名）')
        if (outcome === '') throw new Error('append 需要 outcome（结果）')
        const detail = str((args as { detail?: unknown }).detail)
        const argv = ['log', 'append', step, outcome]
        if (detail !== '') argv.push(detail)
        const payload = await design(...argv)
        if (!ok(payload)) throw new Error(findings(payload).join('；') || '写日志失败')
        return {
          action: 'append',
          dir: '',
          files: [],
          coverage: '',
          text: `已追加到 ${str(payload.path)}：${step} → ${outcome}`
            + (detail !== '' ? `（${detail}）` : ''),
        }
      }
      if (action === 'coverage') {
        const payload = await design('log', 'coverage')
        if (!ok(payload)) throw new Error(findings(payload).join('；'))
        const total = num(payload.total_files)
        const read = num(payload.read_files)
        const unread = num(payload.unread_files)
        const byExt = (payload.unread_by_ext ?? {}) as Record<string, number>
        return {
          action,
          dir: '',
          files: [],
          coverage: [
            `读取覆盖度：${read} / ${total} 个文件（${(num(payload.read_ratio) * 100).toFixed(1)}%）`,
            `未读：${unread}`,
            String(payload.note ?? ''),
            '',
            '未读按扩展名：',
            ...Object.entries(byExt).slice(0, 12).map(([ext, count]) => `  ${ext} ×${count}`),
          ].join('\n'),
          text: '',
        }
      }
      if (action === 'read') {
        const name = str((args as { name?: unknown }).name)
        if (name.trim() === '') throw new Error('read 需要 name（日志文件名）')
        const payload = await design('log', 'read', name.trim())
        if (!ok(payload)) throw new Error(findings(payload).join('；') || '读不到日志')
        return { action, dir: '', files: [], coverage: '', text: str(payload.text) }
      }
      const payload = await design('log', 'list')
      if (!ok(payload)) throw new Error(findings(payload).join('；'))
      return {
        action: 'list',
        dir: str(payload.dir),
        files: rowsOf(payload, 'logs').map((row) =>
          `${str(row.kind)}  ${str(row.name)}  ${num(row.size)} B`),
        coverage: '',
        text: '',
      }
    },
  })))

  // ---- 17. hw_velxio ----------------------------------------------------
  //
  // 后端生命周期。**没有这个工具，模型永远不知道后端有没有起** —— 在此之前
  // 只有 `hw_block(velxio_compile)` 会失败，而失败信息说"后端不可达"，
  // 却不提供"把它起来"的动作。这个工具就是那个动作。
  //
  // 纪律：`not_ready`（没起）与 `failed`（起了但坏）必须分开，因为处理方式
  // 完全不同——前者点一下 start，后者要看日志。
  disposers.push(tools.register(defineTool({
    name: 'hw_velxio',
    description:
      'Velxio 后端（真实编译 / 电路 / 库）的生命周期：action=status 看状态，'
      + 'start 启动，stop 停止，logs 读日志。'
      + '**纯 DSH 内跑，不需要浏览器、不需要 Docker**：'
      + '后端是本机 venv 里的 uvicorn，工具链在 ~/.dsh/hw_velxio/toolchain。'
      + '没装 arduino-cli 时后端仍能起，但编译会报 setup 未就绪。',
    parameters: {
      action: { type: 'string', description: 'status（默认）/ start / stop / logs' },
      tail: { type: 'number', description: 'logs 时可选：读多少行（默认 60）' },
      port: { type: 'number', description: 'start 时可选：端口（默认 3080）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          action: { type: 'string' },
          status: { type: 'string' },
          url: { type: 'string' },
          port: { type: 'number' },
          pid: { type: 'number' },
          arduinoReady: { type: 'boolean' },
          lines: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          action: string; status: string; url: string; port: number
          pid: number; arduinoReady: boolean; lines: string[]
        }
        const label = v.status === 'ok' ? '运行中'
          : v.status === 'failed' ? '异常' : '未启动'
        return text(`# Velxio 后端：${label}`, ...v.lines)
      },
    },
    execute: async (args) => {
      const action = str((args as { action?: unknown }).action, 'status').trim() || 'status'
      const argv = ['serve', action]
      if (action === 'logs') {
        argv.push('--tail', String(num((args as { tail?: unknown }).tail, 60)))
      }
      if (action === 'start' && (args as { port?: unknown }).port !== undefined) {
        argv.push('--port', String(num((args as { port?: unknown }).port, 3080)))
      }
      // `start` 是**会故意等待**的动作（等 /health 起来，最长 150s），
      // 必须给它比默认 120s 更长的超时，否则会在拿到结果前被砍断，
      // 面板只看到一句 "Command failed"——那会误导成"后端启动失败"。
      const payload = action === 'start'
        ? await designLong(240_000, ...argv)
        : await design(...argv)
      const status = str(payload.status, 'failed')
      const cli = (payload.toolchain ?? {}) as Record<string, unknown>
      const lines: string[] = []
      if (action === 'logs') {
        lines.push(str(payload.text, '（空）'))
      } else {
        lines.push(`URL：${str(payload.url)}　端口：${num(payload.port)}`)
        if (payload.pid !== undefined && payload.pid !== null) {
          lines.push(`PID：${num(payload.pid)}（来源 ${str(payload.pid_source, '未知')}）`)
        }
        lines.push(`arduino-cli：${cli.arduino_cli_ready === true ? '就绪' : '**未安装**（编译会失败）'}`)
        lines.push(`QEMU：${cli.qemu_ready === true ? '就绪（ESP32 可仿真）' : '未安装（ESP32 仿真不可用）'}`)
        if (status !== 'ok') {
          lines.push('', '原因：', ...findings(payload).map((f) => `! ${f}`))
          if (status === 'not_ready') {
            lines.push('', '下一步：hw_velxio action=start')
          } else {
            lines.push('', '下一步：hw_velxio action=logs 看日志尾部')
          }
        }
      }
      return {
        action,
        status,
        url: str(payload.url),
        port: num(payload.port),
        pid: num(payload.pid),
        arduinoReady: cli.arduino_cli_ready === true,
        lines,
      }
    },
  })))

  // ---- 18. hw_compile ---------------------------------------------------
  //
  // 真实编译。**没有这个工具，模型只能靠 hw_block("velxio_compile", …)
  // 摸黑传 JSON** —— 参数名、板卡 fqbn、files 的形状全靠猜，猜错拿到的
  // 是一句"参数形状不对"。
  //
  // 这个工具把三件事固定下来：文件从工程的 sources/ 读（或内联给）、
  // 板卡是 fqbn、结果是**等编译完成后的 hex**而不是 job_id。
  disposers.push(tools.register(defineTool({
    name: 'hw_compile',
    description:
      '用 Velxio 后端**真实编译** Arduino/ESP-IDF 源码，返回 hex/bin。'
      + '需要后端在跑（hw_velxio action=start）。'
      + 'boardFqbn 形如 arduino:avr:uno / arduino:avr:nano / esp32:esp32:esp32。'
      + 'files 是 [{name, content}]；只想编工程里已有的 sketch 时给 project 即可。'
      + '**编译是真的**：hex 来自 arduino-cli，不是占位符。',
    parameters: {
      project: { type: 'string', description: '工程目录名（结果落盘到 <project>/velxio_compile.json）' },
      boardFqbn: { type: 'string', description: '板卡 FQBN，如 arduino:avr:uno' },
      filesJson: {
        type: 'string',
        description: '可选：源码文件 JSON 数组，如 [{"name":"b.ino","content":"…"}]。'
          + '不给则尝试读工程 sources/ 下的 .ino',
      },
      waitSeconds: { type: 'number', description: '可选：最多等多少秒（默认 300）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          boardFqbn: { type: 'string' },
          success: { type: 'boolean' },
          hexBytes: { type: 'number' },
          binaryBytes: { type: 'number' },
          jobId: { type: 'string' },
          lines: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; boardFqbn: string; success: boolean
          hexBytes: number; binaryBytes: number; jobId: string; lines: string[]
        }
        return text(
          `# ${v.project} 编译（${v.boardFqbn}）：${v.success ? '成功' : '失败'}`,
          ...v.lines,
        )
      },
      presentationMeta: (args) => viewHint(requireProject(args), 'velxio'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const boardFqbn = str((args as { boardFqbn?: unknown }).boardFqbn, 'arduino:avr:uno')
      const waitSeconds = num((args as { waitSeconds?: unknown }).waitSeconds, 300)
      const filesJson = str((args as { filesJson?: unknown }).filesJson)
      const params: Record<string, unknown> = {
        board: boardFqbn,
        wait_s: waitSeconds,
      }
      if (filesJson.trim() !== '') {
        let parsed: unknown
        try {
          parsed = JSON.parse(filesJson)
        } catch (error) {
          throw new Error(`filesJson 不是合法 JSON：`
            + (error instanceof Error ? error.message : String(error)))
        }
        if (!Array.isArray(parsed) || parsed.length === 0) {
          throw new Error('filesJson 必须是非空数组，如 [{"name":"b.ino","content":"…"}]')
        }
        params.files = parsed
      } else {
        // 不给 files 时从工程的 sources/ 读。
        //
        // 这里**用 hw_survey 拿到的工程路径**去读盘，而不是调 `design
        // firmware --list`：后者是"读固件做静态检查"，不是"列源码"，
        // 参数形状完全不同（它要 --scope，不要 --list）。用一个错的命令
        // 去实现文件列举，只会得到一个和意图无关的报错。
        const survey = await design('survey')
        const rows = rowsOf(survey as Record<string, unknown>, 'projects')
        const row = rows.find((entry) => str(entry.id) === project)
        if (row === undefined) {
          throw new Error(`工程 ${project} 不在当前工作区的勘察结果里。`
            + '用 hw_projects 确认工程名，或先 hw_workspace 切到它的工作区。')
        }
        const projectPath = str(row.path)
        if (projectPath === '') {
          throw new Error(`工程 ${project} 没有可用路径（勘察结果缺 path 字段）。`)
        }
        const sourcesDir = `${projectPath}\\sources`
        const listing = await design('log', 'list')
        // 没有专用的"列目录"工具，直接用 Node 读——这是本地文件，不涉及判定。
        const { readdirSync, readFileSync } = await import('node:fs')
        let names: string[] = []
        try {
          names = readdirSync(sourcesDir)
        } catch {
          throw new Error(
            `没有可编译的源码：${sourcesDir} 不存在。`
            + '给 filesJson 内联源码，或把 .ino 放进工程 sources/ 目录。')
        }
        const sketchFiles = names
          .filter((name) => /\.(ino|cpp|c|h)$/i.test(name))
          .map((name) => ({
            name,
            content: readFileSync(`${sourcesDir}\\${name}`, 'utf8'),
          }))
        if (sketchFiles.length === 0) {
          throw new Error(
            `没有可编译的源码：${sourcesDir} 里没有 .ino/.cpp/.c/.h。`)
        }
        params.files = sketchFiles
        void listing
      }
      const payload = await bridge('run', project, 'velxio_compile', JSON.stringify(params))
      const data = (payload.data ?? {}) as Record<string, unknown>
      const success = data.success === true
      const hex = str(data.hex_content)
      const bin = str(data.binary_content)
      const lines: string[] = []
      lines.push(`状态：${str(data.state, 'unknown')}`)
      if (hex !== '') {
        lines.push(`hex：${hex.length} 字符（${Math.round(hex.length * 0.75)} 字节二进制）`)
      }
      if (bin !== '') lines.push(`bin：${bin.length} 字符（base64）`)
      if (str(data.job_id) !== '') lines.push(`作业：${str(data.job_id)}`)
      const err = str(data.error)
      if (err !== '') lines.push('', `错误：${err.slice(0, 600)}`)
      const stderr = str(data.stderr)
      if (stderr !== '') lines.push('', `stderr：${stderr.slice(-800)}`)
      if (!success && err === '' && stderr === '') {
        lines.push('', '编译没有成功但后端没给原因——用 hw_velxio action=logs 看后端日志。')
      }
      if (!success && /not found in PATH|not_ready/i.test(str(payload.findings))) {
        lines.push('', '提示：后端可能没起来或缺 arduino-cli，用 hw_velxio action=status 查。')
      }
      noteHint(project, 'velxio')
      return {
        project,
        boardFqbn,
        success,
        hexBytes: hex === '' ? 0 : Math.round(hex.length * 0.75),
        binaryBytes: bin === '' ? 0 : Math.round(bin.length * 0.75),
        jobId: str(data.job_id),
        lines,
      }
    },
  })))

  // ---- 19. hw_circuit ---------------------------------------------------
  //
  // 电路 JSON ↔ Wokwi diagram.json。**纯计算，不需要后端在线** ——
  // 这一点很重要：它是全插件唯一在后端没起时仍然能用的 Velxio 能力。
  disposers.push(tools.register(defineTool({
    name: 'hw_circuit',
    description:
      'Wokwi 电路图互转（复用 Velxio 的 wokwi 纯计算）：'
      + 'action=import 把 diagram.json 解析成电路，export 反向生成 diagram，'
      + 'sketch 生成 Arduino 草图。**不需要后端在线**，只需要 Velxio 源码在盘上。',
    parameters: {
      action: { type: 'string', description: 'import / export / sketch' },
      diagramJson: { type: 'string', description: 'diagram JSON 对象字符串' },
      project: { type: 'string', description: '可选：结果落盘到该工程' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          action: { type: 'string' },
          status: { type: 'string' },
          lines: { type: 'array', items: { type: 'string' } },
          text: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as { action: string; status: string; lines: string[]; text: string }
        return text(`# 电路 ${v.action} → ${v.status}`, ...v.lines,
          ...(v.text !== '' ? ['', v.text] : []))
      },
    },
    execute: async (args) => {
      const action = str((args as { action?: unknown }).action, 'export').trim() || 'export'
      const raw = str((args as { diagramJson?: unknown }).diagramJson)
      if (raw.trim() === '') throw new Error('需要 diagramJson（Wokwi diagram JSON 对象）')
      let diagram: unknown
      try {
        diagram = JSON.parse(raw)
      } catch (error) {
        throw new Error(`diagramJson 不是合法 JSON：`
          + (error instanceof Error ? error.message : String(error)))
      }
      if (diagram === null || typeof diagram !== 'object' || Array.isArray(diagram)) {
        throw new Error('diagramJson 必须是一个 JSON 对象')
      }
      const project = str((args as { project?: unknown }).project)
      const params: Record<string, unknown> = { action, diagram }
      if (project !== '') params.project = project
      const payload = await bridge('run', project === '' ? '_shared' : project,
        'velxio_circuit', JSON.stringify(params))
      const status = str(payload.status, 'failed')
      const data = (payload.data ?? {}) as Record<string, unknown>
      const lines: string[] = []
      if (status === 'ok') {
        const circuit = (data.circuit ?? {}) as Record<string, unknown>
        const components = (circuit.components ?? []) as unknown[]
        lines.push(`元件 ${components.length} 个，板卡 ${str(circuit.board_fqbn, '未识别')}`)
      } else {
        lines.push(...findings(payload).map((f) => `! ${f}`))
      }
      const textOut = typeof data.wokwi === 'string' ? data.wokwi
        : typeof data.sketch === 'string' ? data.sketch : ''
      return { action, status, lines, text: textOut.slice(0, 2000) }
    },
  })))

  // ---- 20. hw_libs ------------------------------------------------------
  //
  // 库搜索/安装。**Arduino 与 MicroPython 双通道** —— 后者在
  // `velxio_client` 里写了两个函数但从未被调用（死代码），这里接上。
  disposers.push(tools.register(defineTool({
    name: 'hw_libs',
    description:
      '搜索/安装 Arduino 或 MicroPython 库（Velxio 后端）。'
      + 'kind=arduino（默认）走 /api/libraries，kind=micropython 走 '
      + '/api/micropython-libs。给 query 搜索，给 name 安装，都不给则列出已装'
      + '（MicroPython 没有 list 接口，会如实说明）。',
    parameters: {
      query: { type: 'string', description: '搜索关键词' },
      name: { type: 'string', description: '要安装的库名' },
      version: { type: 'string', description: '可选：版本' },
      kind: { type: 'string', description: 'arduino（默认）/ micropython' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          kind: { type: 'string' },
          status: { type: 'string' },
          count: { type: 'number' },
          lines: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as { kind: string; status: string; count: number; lines: string[] }
        return text(`# 库（${v.kind}）→ ${v.status}，${v.count} 条`, ...v.lines)
      },
    },
    execute: async (args) => {
      const kind = str((args as { kind?: unknown }).kind, 'arduino').trim() || 'arduino'
      const params: Record<string, unknown> = { kind }
      const query = str((args as { query?: unknown }).query)
      const name = str((args as { name?: unknown }).name)
      const version = str((args as { version?: unknown }).version)
      if (query !== '') params.query = query
      if (name !== '') params.name = name
      if (version !== '') params.version = version
      const payload = await bridge('run', '_shared', 'velxio_libs', JSON.stringify(params))
      const status = str(payload.status, 'failed')
      const data = (payload.data ?? {}) as Record<string, unknown>
      const libraries = (data.libraries ?? []) as Array<Record<string, unknown>>
      const packages = (data.packages ?? []) as Array<Record<string, unknown>>
      const rows = libraries.length > 0 ? libraries : packages
      const lines = rows.slice(0, 15).map((row) =>
        `· ${str(row.name)}${str(row.version) !== '' ? ` @${str(row.version)}` : ''}`
        + `${str(row.sentence) !== '' ? ` — ${str(row.sentence).slice(0, 80)}` : ''}`
        + `${str(row.description) !== '' ? ` — ${str(row.description).slice(0, 80)}` : ''}`)
      if (status !== 'ok') lines.push(...findings(payload).map((f) => `! ${f}`))
      return { kind, status, count: rows.length, lines }
    },
  })))

  // ---- 21. hw_sim -------------------------------------------------------
  //
  // **真跑固件**。在此之前"编译出 hex"是终点——hex 躺在文件里，没人执行它。
  // 这个工具把 avr8js 跑在宿主 Node 里，于是 `Serial.print` 真的会产字节。
  //
  // 关键设计：只做"执行 + 串口/引脚"，**不做可视化**。150+ 元件动画是
  // Velxio 前端的职责，与"不依赖浏览器"直接冲突；能拿到字节与电平，
  // 才是无浏览器环境下有意义的那部分。
  disposers.push(tools.register(defineTool({
    name: 'hw_sim',
    description:
      '跑真实固件（AVR）。action=start 起一个会话（需要 hex）、run 推进仿真并取串口、'
      + 'stop 停止、status 看状态。'
      + '**在宿主 Node 进程里用 avr8js 执行真指令**，不需要浏览器、不需要 Velxio 后端。'
      + '支持 ATmega328p 家族（Uno/Nano/Mini/Pro Mini）；Mega/ESP32 不支持并会如实说明。'
      + '不提供元件动画——那需要浏览器。',
    parameters: {
      action: { type: 'string', description: 'start / run / stop / status' },
      project: { type: 'string', description: '工程目录名（会话按它索引）' },
      board: { type: 'string', description: 'start 时可选：板卡 FQBN（默认 arduino:avr:uno）' },
      hexJson: {
        type: 'string',
        description: 'start 时可选：直接给 hex 文本；不给则读 <工程>/velxio_compile.json 里的 hex_content',
      },
      maxInstructions: { type: 'number', description: 'run 时可选：最多跑多少条指令（默认 4000 万 ≈ 2.5s 仿真）' },
      maxBytes: { type: 'number', description: 'run 时可选：攒够多少串口字节就停（默认 4096）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          action: { type: 'string' },
          status: { type: 'string' },
          project: { type: 'string' },
          board: { type: 'string' },
          running: { type: 'boolean' },
          instructions: { type: 'number' },
          simMs: { type: 'number' },
          serialText: { type: 'string' },
          serialTotal: { type: 'number' },
          droppedBytes: { type: 'number' },
          lines: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          action: string; status: string; project: string; board: string
          running: boolean; instructions: number; simMs: number
          serialText: string; serialTotal: number; droppedBytes: number
          lines: string[]
        }
        return text(`# 仿真 · ${v.project || '（无工程）'} → ${v.status}`,
          ...(v.lines.length > 0 ? v.lines : []),
          ...(v.serialText !== '' ? ['', '串口输出：', v.serialText] : []))
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project).trim()
        return project === '' ? {} : viewHint(project, 'velxio')
      },
    },
    execute: async (args) => {
      const action = str((args as { action?: unknown }).action, 'status').trim() || 'status'
      const project = str((args as { project?: unknown }).project).trim()

      if (action === 'start') {
        if (project === '') throw new Error('start 需要 project（会话按工程名索引）')
        const board = str((args as { board?: unknown }).board, 'arduino:avr:uno')
        let hex = str((args as { hexJson?: unknown }).hexJson)
        if (hex.trim() === '') {
          // 从编译产物里取。读盘逻辑与宿主端点 `simStart` **共用一份**
          // （`velxio/artifact.ts`）：两处各写一遍会在"异步/同步编译落盘
          // 形状不同"这个细节上分叉，后果是"工具能起、面板不能"。
          const survey = await design('survey')
          const rows = rowsOf(survey as Record<string, unknown>, 'projects')
          const row = rows.find((entry) => str(entry.id) === project)
          const projectPath = row === undefined ? '' : str(row.path)
          const loaded = loadCompiledHex(projectPath)
          if (loaded.status !== 'ok') {
            throw new Error((loaded.findings ?? ['读不到编译产物']).join('；'))
          }
          hex = loaded.hex ?? ''
        }
        const out = sim.start({ project, board, hex })
        const status = str(out.status, 'failed')
        const lines: string[] = []
        if (status === 'ok') {
          lines.push(`板卡：${board}`)
          lines.push(`固件：${num(out.hexBytes)} 字节`)
          lines.push(`会话已起（${str(out.note, '')}）`)
          if (out.replaced === true) lines.push('（替换了同名的旧会话）')
          lines.push('', '下一步：hw_sim action=run 推进仿真；hw_serial 读串口。')
          noteHint(project, 'velxio')
        } else {
          lines.push(...findings(out as Record<string, unknown>).map((f) => `! ${f}`))
        }
        return {
          action, status, project, board,
          running: status === 'ok',
          instructions: 0, simMs: 0, serialText: '', serialTotal: 0, droppedBytes: 0,
          lines,
        }
      }

      if (action === 'run') {
        if (project === '') throw new Error('run 需要 project')
        const out = sim.run(project, {
          maxInstructions: num((args as { maxInstructions?: unknown }).maxInstructions, 40_000_000),
          maxBytes: num((args as { maxBytes?: unknown }).maxBytes, 4096),
        })
        const status = str(out.status, 'failed')
        const lines: string[] = []
        if (status === 'ok') {
          lines.push(`执行 ${num(out.instructions)} 条指令（${num(out.simMs)} ms 仿真时间）`)
          lines.push(`停止原因：${str(out.stopReason)}`)
          lines.push(`串口累计 ${num(out.serialTotal)} 字节，本次新增 ${num(out.serialBytes)}`)
          if (num(out.droppedBytes) > 0) {
            lines.push(`⚠ 缓冲容量不够，丢了 ${num(out.droppedBytes)} 字节（数据不完整）`)
          }
          const pins = (out.pinEvents ?? []) as Array<Record<string, unknown>>
          if (pins.length > 0) {
            lines.push(`引脚变化 ${pins.length} 次（最近）：`
              + pins.slice(-6).map((p) => `P${str(p.port)}=${num(p.value)}`).join(' '))
          }
          noteHint(project, 'velxio')
        } else {
          lines.push(...findings(out as Record<string, unknown>).map((f) => `! ${f}`))
        }
        return {
          action, status, project,
          board: '', running: true,
          instructions: num(out.instructions), simMs: num(out.simMs),
          serialText: str(out.serialText), serialTotal: num(out.serialTotal),
          droppedBytes: num(out.droppedBytes), lines,
        }
      }

      if (action === 'stop') {
        const out = sim.stop(project)
        return {
          action, status: str(out.status, 'ok'), project, board: '',
          running: false, instructions: num(out.instructions), simMs: 0,
          serialText: '', serialTotal: num(out.serialTotal), droppedBytes: 0,
          lines: [out.stopped === true
            ? `已停止（共执行 ${num(out.instructions)} 条指令，串口 ${num(out.serialTotal)} 字节）`
            : str(out.note, '没有正在跑的会话')],
        }
      }

      const out = sim.status(project)
      const lines: string[] = []
      if (project !== '') {
        lines.push(`运行中：${out.running === true ? '是' : '否'}`)
        if (out.running === true) {
          lines.push(`板卡：${str(out.board)}`)
          lines.push(`指令 ${num(out.instructions)}（${num(out.simMs)} ms）`)
          lines.push(`串口累计 ${num(out.serialTotal)}，待读 ${num(out.serialPending)}`)
          lines.push(`起于 ${str(out.startedAt)}`)
        } else {
          lines.push(...findings(out as Record<string, unknown>).map((f) => `! ${f}`))
        }
      } else {
        lines.push(`活跃会话：${num(out.running)} 个`)
        for (const row of (out.sessions ?? []) as Array<Record<string, unknown>>) {
          lines.push(`· ${str(row.project)}（${str(row.board)}，${num(row.instructions)} 指令）`)
        }
      }
      return {
        action: 'status', status: str(out.status, 'ok'), project,
        board: str(out.board), running: out.running === true,
        instructions: num(out.instructions), simMs: num(out.simMs),
        serialText: '', serialTotal: num(out.serialTotal), droppedBytes: 0, lines,
      }
    },
  })))

  // ---- 22. hw_serial ----------------------------------------------------
  //
  // 串口读取单独一个工具，因为它是**每次交互都要用的**动作；塞进 hw_sim 的
  // action 里会让"只读一下有没有输出"变成一次完整的 run（多跑 4000 万条
  // 指令）。读与跑分开，代价才可控。
  disposers.push(tools.register(defineTool({
    name: 'hw_serial',
    description:
      '读仿真会话的串口输出（**只读，不推进 CPU**）。'
      + '返回自上次读取以来新增的字节。会话必须先由 hw_sim action=start 起。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          status: { type: 'string' },
          text: { type: 'string' },
          bytes: { type: 'number' },
          totalBytes: { type: 'number' },
          droppedBytes: { type: 'number' },
          lines: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; status: string; text: string
          bytes: number; totalBytes: number; droppedBytes: number; lines: string[]
        }
        if (v.status !== 'ok') return text(...v.lines)
        return text(
          `# ${v.project} 串口（新增 ${v.bytes} 字节 / 累计 ${v.totalBytes}）`,
          ...(v.droppedBytes > 0
            ? [`⚠ 缓冲丢弃 ${v.droppedBytes} 字节：输出比读取快，数据不完整`] : []),
          '',
          ...(v.text === '' ? ['（本次没有新输出）'] : [v.text]),
        )
      },
    },
    execute: async (args) => {
      const project = requireProject(args)
      const out = sim.serialRead(project)
      const status = str(out.status, 'failed')
      const lines = status === 'ok'
        ? []
        : findings(out as Record<string, unknown>).map((f) => `! ${f}`)
      return {
        project,
        status,
        text: str(out.text),
        bytes: num(out.bytes),
        totalBytes: num(out.totalBytes),
        droppedBytes: num(out.droppedBytes),
        lines,
      }
    },
  })))

  // ---- 3j. hw_fw_build（骨架 + 真编译成 .elf/.bin）-----------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_fw_build',
    description:
      '**可编译骨架 + 真编译**：从 design.json + interface.json 生成 startup.c / link.ld /'
      + ' main.c / Makefile，用仓库内 arm-none-eabi-gcc 编成 `.elf` + `.bin`。'
      + '四条硬判据：编译过 / Machine=ARM / Entry 在 FLASH / `text+data ≤ flash` 且'
      + ' `data+bss ≤ ram`。生成的 main.c 是**骨架**，只证明能编能链，不证明功能对。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      out: { type: 'string', description: '输出目录（默认 fw/build）' },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          project: { type: 'string' }, status: { type: 'string' },
          rows: { type: 'string' }, summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as { project: string; status: string; rows: string; summary: string }
        return text(`# ${v.project} 固件构建：${v.status}`,
          ...v.rows.split('\n').filter((x) => x !== ''), v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'firmware')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; out?: unknown }
      const project = str(a.project)
      const cliArgs: string[] = [project]
      const out = str(a.out)
      if (out !== '') cliArgs.push(`--out=${out}`)
      const payload = await design('fw-build', ...cliArgs)
      const b = (payload.build ?? {}) as Record<string, unknown>
      const checks = (b.checks ?? {}) as Record<string, unknown>
      const rows: string[] = []
      if (payload.mcu !== undefined) {
        const memory = (payload.memory ?? {}) as Record<string, unknown>
        rows.push(`MCU ${str(payload.mcu)}（${str(payload.core)}）`
          + ` ${num(memory.flash_kb)}K flash / ${num(memory.ram_kb)}K ram`)
      }
      if (checks.machine !== undefined) {
        rows.push(`Machine=${str(checks.machine)}  Entry=${str(checks.entry)}`
          + `  text=${num(checks.text_b)}B data=${num(checks.data_b)}B bss=${num(checks.bss_b)}B`)
      }
      for (const raw of (payload.findings as unknown[] | undefined) ?? []) {
        rows.push(`* ${str(raw)}`)
      }
      return {
        project,
        status: str(payload.status),
        rows: rows.join('\n'),
        summary: str(b.reason) || str(payload.reason) || str(b.note) || '',
      }
    },
  })))

  // ---- 3i. hw_interface / hw_fw_algo（软件：契约 + 可跑的纯 C）-----------
  disposers.push(tools.register(defineTool({
    name: 'hw_interface',
    description:
      '**软硬接口契约**：从 `design.json`（引脚+网表）与 `.ioc` 合成 `interface.json`/`.md`。'
      + '固件与检查器共用同一份事实。**唯一硬判据是引脚↔网络逐条一致**；'
      + '没有 `.ioc` 时外设分组标 `inferred`；时钟/中断/AF/时序一律 `unknown`。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      ioc: { type: 'string', description: '可选：.ioc 路径（有就按它定外设）' },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          project: { type: 'string' }, status: { type: 'string' },
          rows: { type: 'string' }, summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as { project: string; status: string; rows: string; summary: string }
        return text(`# ${v.project} 接口契约：${v.status}`,
          ...v.rows.split('\n').filter((x) => x !== ''), v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'firmware')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; ioc?: unknown }
      const project = str(a.project)
      const cliArgs: string[] = [project]
      const ioc = str(a.ioc)
      if (ioc !== '') cliArgs.push(`--ioc=${ioc}`)
      const payload = await design('interface', ...cliArgs)
      const counts = (payload.counts ?? {}) as Record<string, unknown>
      const peripherals = Array.isArray(payload.peripherals) ? payload.peripherals : []
      const unknown = Array.isArray(payload.unknown) ? payload.unknown : []
      const rows: string[] = [
        `引脚 ${num(counts.pins)}（连通 ${num(counts.connected)} / 未接 ${num(counts.unconnected)}）`,
        `外设 ${peripherals.map((raw) => {
          const g = (raw ?? {}) as Record<string, unknown>
          return `${str(g.family)}(${str(g.source)})`
        }).join(' ') || '无'}`,
      ]
      for (const raw of unknown.slice(0, 4)) {
        const u = (raw ?? {}) as Record<string, unknown>
        rows.push(`* unknown：${str(u.what)} —— 需要 ${str(u.need)}`)
      }
      return {
        project,
        status: str(payload.status),
        rows: rows.join('\n'),
        summary: str(payload.note),
      }
    },
  })))
  disposers.push(tools.register(defineTool({
    name: 'hw_fw_algo',
    description:
      '**纯 C 算法 + PC 侧单元测试**：把不依赖硬件的那部分（状态机/滤波/限幅）'
      + '写成 C，用本机 gcc **真编译真跑**。`ok`=全过、`failed`=有用例失败、'
      + '`not_ready`=没 gcc/编译不过。Renode 跑不了时，这是唯一能验软件的路。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      algo: { type: 'string', description: 'failsafe / lpf（不给则列算法）' },
      out: { type: 'string', description: '输出目录（默认 fw/<algo>）' },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          project: { type: 'string' }, status: { type: 'string' },
          rows: { type: 'string' }, summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as { project: string; status: string; rows: string; summary: string }
        return text(`# ${v.project} 算法测试：${v.status}`,
          ...v.rows.split('\n').filter((x) => x !== ''), v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'firmware')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; algo?: unknown; out?: unknown }
      const project = str(a.project)
      const algo = str(a.algo)
      const cliArgs: string[] = [project]
      if (algo === '') cliArgs.push('list')
      else cliArgs.push(`--algo=${algo}`)
      const out = str(a.out)
      if (out !== '') cliArgs.push(`--out=${out}`)
      const payload = await design('fw-algo', ...cliArgs)
      const cases = (payload.cases ?? {}) as Record<string, unknown>
      const failed = Array.isArray(cases.failed) ? (cases.failed as string[]) : []
      const lists = Array.isArray(payload.algorithms) ? payload.algorithms : []
      const rows = lists.length > 0
        ? lists.map((raw) => {
          const x = (raw ?? {}) as Record<string, unknown>
          return `* ${str(x.name)}：${str(x.proves)}`
        })
        : [
          `用例 ${num(cases.passed)}/${num(cases.total)} 通过`,
          ...failed.slice(0, 6).map((x) => `* ${x}`),
        ]
      return {
        project,
        status: str(payload.status),
        rows: rows.join('\n'),
        summary: str(payload.reason) || str(payload.note) || str(payload.dir),
      }
    },
  })))

  // ---- 3h. hw_plan ------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_plan',
    description:
      '**项目计划图**：需求 → 能力（每条带可测判据）→ 模块（design.json 的**真实坐标**）'
      + '→ 四层 3D 信息图（任务 z=-60 / 能力 z=+60 / 模块 / 板）→ 任务树。'
      + '每次给同一项目**加需求**就多一份 `plan/vN.json`（同一份需求不重复建版）；'
      + '缺证据的能力是 `unknown`，**不是通过**。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      req: { type: 'string', description: '新需求 JSON（会建新版本 vN+1）' },
      text: { type: 'string', description: '需求原文（记进版本）' },
      mode: { type: 'string', description: 'list / graph / show（默认建/刷新）' },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          project: { type: 'string' }, version: { type: 'string' },
          status: { type: 'string' }, rows: { type: 'string' },
          summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; version: string; status: string
          rows: string; summary: string
        }
        return text(`# ${v.project} 计划：${v.version}（${v.status}）`,
          ...v.rows.split('\n').filter((x) => x !== ''), v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'verdict')
      },
    },
    execute: async (args) => {
      const a = args as {
        project?: unknown; req?: unknown; text?: unknown; mode?: unknown
      }
      const project = str(a.project)
      const mode = str(a.mode)
      const cliArgs: string[] = [project]
      if (mode === 'list') cliArgs.push('--list')
      if (mode === 'graph') cliArgs.push('--graph')
      const req = str(a.req)
      if (req !== '') cliArgs.push(`--req=${req}`)
      const plain = str(a.text)
      if (plain !== '') cliArgs.push(`--text=${plain}`)
      const payload = await design('plan', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '计划生成失败')
      }
      const versions = Array.isArray(payload.versions) ? payload.versions : []
      if (mode === 'list') {
        return {
          project, version: '', status: str(payload.status),
          rows: versions.map((raw) => {
            const v = (raw ?? {}) as Record<string, unknown>
            return `* ${str(v.version)}（parent ${str(v.parent) || '—'}）${str(v.created)}`
          }).join('\n'),
          summary: str(payload.note),
        }
      }
      const caps = Array.isArray(payload.capabilities) ? payload.capabilities : []
      const tasks = Array.isArray(payload.tasks) ? payload.tasks : []
      const graph = (payload.graph ?? {}) as Record<string, unknown>
      const counts = (graph.counts ?? {}) as Record<string, unknown>
      const lines: string[] = []
      if (caps.length > 0) {
        lines.push(`能力 ${caps.length}：` + caps.map((raw) => {
          const c = (raw ?? {}) as Record<string, unknown>
          return `${str(c.id)}=${str(c.status)}`
        }).join(' '))
      }
      if (tasks.length > 0) {
        lines.push(`任务 ${tasks.length}（每条都有验收方式）：`)
        for (const raw of tasks.slice(0, 12)) {
          const t = (raw ?? {}) as Record<string, unknown>
          lines.push(`* ${str(t.id)} ${str(t.kind)}：${str(t.title).slice(0, 40)}`)
        }
      }
      if (counts.nodes !== undefined) {
        lines.push(`3D 图：${num(counts.nodes)} 节点 / ${num(counts.edges)} 边`
          + `（模块 ${num(counts.modules)}，能力 ${num(counts.capabilities)}，任务 ${num(counts.tasks)}）`)
      }
      return {
        project,
        version: str(payload.version),
        status: str(payload.status),
        rows: lines.join('\n'),
        summary: str(payload.reason) || str(payload.note),
      }
    },
  })))

  // ---- 3g. hw_audit / hw_doctor / hw_examples / hw_fixloop（P47）--------
  disposers.push(tools.register(defineTool({
    name: 'hw_audit',
    description:
      '**六段审计**：需求 → 单元与角色 → 选料 → 原理图/网表一致 → PCB（布线/铺铜/'
      + '连通 + 制造结论）→ 固件 → 机械 → 交接。每段给 `status`/`checked`/`basis`，'
      + '**缺产物是 `not_ready`，不是 ok**。写 `audit.json` + `audit.md`。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
      stages: { type: 'string', description: '只跑几段（逗号分隔，如 pcb,mechanical）' },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          project: { type: 'string' }, status: { type: 'string' },
          rows: { type: 'string' }, summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as { project: string; status: string; rows: string; summary: string }
        return text(`# ${v.project} 审计：${v.status}`,
          ...v.rows.split('\n').filter((x) => x !== ''), v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'verdict')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; stages?: unknown }
      const project = str(a.project)
      const cliArgs: string[] = project === '' ? [] : [project]
      const stages = str(a.stages)
      if (stages !== '') cliArgs.push(`--stages=${stages}`)
      const payload = await design('audit', ...cliArgs)
      const rows = Array.isArray(payload.stages) ? payload.stages : []
      return {
        project,
        status: str(payload.status),
        rows: rows.map((raw) => {
          const row = (raw ?? {}) as Record<string, unknown>
          return `* ${str(row.stage)} → ${str(row.status)}：${str(row.note)}`
        }).join('\n'),
        summary: str(payload.note),
      }
    },
  })))
  disposers.push(tools.register(defineTool({
    name: 'hw_doctor',
    description:
      '**环境探测**：立创可执行文件、Bridge 通道、LCSC 缓存、MuJoCo、'
      + 'arduino-cli / gcc / ffmpeg。**只探测，不安装、不修复、不联网**；'
      + '必需项（workspace/lceda）缺才是 `failed`，可选项缺是 `not_ready` 并写影响。',
    parameters: {},
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          status: { type: 'string' }, rows: { type: 'string' }, summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as { status: string; rows: string; summary: string }
        return text(`# 环境：${v.status}`,
          ...v.rows.split('\n').filter((x) => x !== ''), v.summary)
      },
    },
    execute: async () => {
      const payload = await design('doctor')
      const checks = Array.isArray(payload.checks) ? payload.checks : []
      return {
        status: str(payload.status),
        rows: checks.map((raw) => {
          const row = (raw ?? {}) as Record<string, unknown>
          return `* ${str(row.name)} → ${str(row.status)}：${str(row.detail).slice(0, 100)}`
        }).join('\n'),
        summary: str(payload.note),
      }
    },
  })))
  disposers.push(tools.register(defineTool({
    name: 'hw_examples',
    description:
      '三个**可抄的半成品样例**：blink（最小闭环）/ button（输入方向）/ '
      + 'gimbal（铰链 + 舵机 + 机械域）。`action=copy` 时把样例写成一个新工程'
      + '（**不覆盖**已存在的工程）。',
    parameters: {
      action: { type: 'string', description: 'list（默认）或 copy' },
      name: { type: 'string', description: 'copy 时的样例名' },
      to: { type: 'string', description: 'copy 时的目标工程 id' },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: { status: { type: 'string' }, summary: { type: 'string' } },
      },
      render: (_args, value) => text((value as { summary: string }).summary),
    },
    execute: async (args) => {
      const a = args as { action?: unknown; name?: unknown; to?: unknown }
      const action = str(a.action)
      const cliArgs: string[] = action === 'copy'
        ? ['copy', str(a.name), `--to=${str(a.to)}`]
        : ['list']
      const payload = await design('examples', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '样例操作失败')
      }
      const list = Array.isArray(payload.examples) ? payload.examples : []
      return {
        status: str(payload.status),
        summary: action === 'copy'
          ? str(payload.note)
          : list.map((raw) => `* ${str((raw as Record<string, unknown>).name)}`
            + `：${str((raw as Record<string, unknown>).note)}`).join('\n'),
      }
    },
  })))
  disposers.push(tools.register(defineTool({
    name: 'hw_fixloop',
    description:
      '**有界自修循环**：拿 audit + handoff 的可执行项，最多 N 轮（默认 3），'
      + '连续 2 轮分数没降就停。**不改设计**：本工具没有编辑器，'
      + '所以总是 `stop=needs_editor` 并给出计划（改设计是人/模型的决定）。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      rounds: { type: 'number', description: '最多几轮（默认 3）' },
      stages: { type: 'string', description: '只审这几段（逗号分隔）' },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          project: { type: 'string' }, status: { type: 'string' },
          stop: { type: 'string' }, rows: { type: 'string' }, summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; status: string; stop: string; rows: string; summary: string
        }
        return text(`# ${v.project} 自修循环：${v.status}（stop=${v.stop}）`,
          ...v.rows.split('\n').filter((x) => x !== ''), v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'verdict')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; rounds?: unknown; stages?: unknown }
      const project = str(a.project)
      const cliArgs: string[] = [project]
      if (typeof a.rounds === 'number') cliArgs.push(`--rounds=${a.rounds}`)
      const stages = str(a.stages)
      if (stages !== '') cliArgs.push(`--stages=${stages}`)
      const payload = await design('fixloop', ...cliArgs)
      if (payload.status === 'failed') {
        throw new Error(findings(payload).join('；') || '自修循环失败')
      }
      const rounds = Array.isArray(payload.rounds) ? payload.rounds : []
      return {
        project,
        status: str(payload.status),
        stop: str(payload.stop),
        rows: rounds.map((raw) => {
          const row = (raw ?? {}) as Record<string, unknown>
          return `* 第 ${str(row.round)} 轮：score=${str(row.score)}，`
            + `可执行项 ${str(row.items)}，${str(row.did)}`
        }).join('\n'),
        summary: str(payload.note),
      }
    },
  })))

  // ---- 3p. hw_emit -------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_emit',
    description:
      '把 design.json 发射成立创EDA专业版工程（原理图 + PCB + 板框 + 布线 + 铺铜）。'
      + '六段可选：project / schematic / pcb / route / pour / outputs（默认全跑）。'
      + '器件按 lcsc 精确匹配（模糊命中会被 Supplier Part 回核剔除），'
      + '搜不到的进 unresolved 且**其余继续**。'
      + '回读立创 PCB 网表与 design.json 逐引脚比对（parity）。'
      + 'route=native 用立创引擎（实测 2/2 布通），route=ours 用自研栅格 A* 兜底；'
      + '两档的**连通性判定都量几何**（并查集），不读引擎自述。'
      + 'pour 铺双面 GND 铜并放缝合过孔，只认 getCopperRegion() 有铜。'
      + 'outputs 把 Gerber/BOM/坐标/`.epro2` 落盘（默认 <工程>/artifacts）。写 emit.json。',
    parameters: {
      project: { type: 'string', description: '工程目录名（省略则只返回说明）' },
      jobs: {
        type: 'string',
        description: '要跑哪几段：project,layout,schematic,pcb,route,pour,verify,outputs',
      },
      route: { type: 'string', description: 'native（默认）/ ours / none' },
      reuse: { type: 'boolean', description: '接管磁盘上已有的同名立创工程' },
      autolayout: {
        type: 'boolean',
        description: '按封装占位重排一次（只保证不短路，不做电气分区/散热/装配顺序）',
      },
      channel: {
        type: 'string',
        description: 'cli（默认，长任务）/ auto（有 Bridge 就用）/ bridge（用户窗口，30s 上限）',
      },
      saveDir: { type: 'string', description: '产物落盘目录（默认 <工程>/artifacts）' },
      maxParts: { type: 'number', description: '最多装多少个器件（默认 260）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          status: { type: 'string' },
          version: { type: 'string' },
          projectName: { type: 'string' },
          projectPath: { type: 'string' },
          jobs: { type: 'string' },
          placed: { type: 'number' },
          unresolved: { type: 'number' },
          parity: { type: 'string' },
          drc: { type: 'string' },
          routeNote: { type: 'string' },
          notes: {
            type: 'array', items: { type: 'string' },
          },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; status: string; version: string
          projectName: string; projectPath: string; jobs: string
          placed: number; unresolved: number
          parity: string; drc: string; routeNote: string
          notes: string[]
        }
        const head = `# ${v.project} 立创发射：${v.status}`
          + (v.version ? `（${v.version}）` : '')
        const body: string[] = []
        if (v.projectName) body.push(`工程：${v.projectName}`)
        if (v.projectPath) body.push(`路径：${v.projectPath}`)
        body.push(v.jobs)
        if (v.placed > 0) body.push(`已装器件：${v.placed} 个（未解析 ${v.unresolved}）`)
        if (v.parity) body.push(v.parity)
        if (v.drc) body.push(v.drc)
        if (v.routeNote) body.push(`布线：${v.routeNote}`)
        for (const note of v.notes.slice(0, 6)) body.push(`* ${note}`)
        return text(head, ...body)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'emit')
      },
    },
    execute: async (args) => {
      const a = args as {
        project?: unknown; jobs?: unknown; route?: unknown
        reuse?: unknown; saveDir?: unknown; maxParts?: unknown
      }
      const project = str(a.project)
      if (project === '') {
        const payload = await design('emit')
        return {
          project: '', status: str(payload.status, 'ok'), version: '',
          projectName: '', projectPath: '',
          jobs: (Array.isArray(payload.jobs) ? payload.jobs : []).join(', '),
          placed: 0, unresolved: 0, parity: '', drc: '', routeNote: '',
          notes: [str(payload.coordinate_caveat)],
        }
      }
      const cliArgs: string[] = [project]
      const jobs = str(a.jobs)
      if (jobs) cliArgs.push(`--jobs=${jobs}`)
      const route = str(a.route)
      if (route) cliArgs.push(`--route=${route}`)
      if (a.reuse === true) cliArgs.push('--reuse')
      const saveDir = str(a.saveDir)
      if (saveDir) cliArgs.push(`--save-dir=${saveDir}`)
      if (typeof a.maxParts === 'number') cliArgs.push(`--max-parts=${a.maxParts}`)
      const payload = await design('emit', ...cliArgs)
      const rows = (payload.jobs ?? {}) as Record<string, Record<string, unknown>>
      const jobLine = Object.entries(rows)
        .map(([name, row]) => `${name}:${str(row.status)}`).join(' · ')
      const sch = (rows.schematic?.counts ?? {}) as Record<string, unknown>
      const parity = (payload.parity ?? {}) as Record<string, unknown>
      const drc = (payload.drc ?? {}) as Record<string, unknown>
      const routeRow = rows.route ?? {}
      const proj = (payload.project ?? {}) as Record<string, unknown>
      const pcounts = (parity.counts ?? {}) as Record<string, unknown>
      return {
        project,
        status: str(payload.status),
        version: str(payload.version),
        projectName: str(proj.name),
        projectPath: str(proj.path),
        jobs: jobLine,
        placed: num(sch.placed),
        unresolved: (Array.isArray(payload.unresolved) ? payload.unresolved : []).length,
        parity: parity.status
          ? `parity：${str(parity.status)}（比对 ${num(parity.checked)} 引脚`
            + `，缺 ${num(pcounts.missing)}，错 ${num(pcounts.wrong)}）`
          : '',
        drc: drc.status
          ? `DRC：${str(drc.note)}（pass=${String(drc.pass)}）`
          : '',
        routeNote: str(routeRow.note),
        notes: (Array.isArray(payload.notes) ? payload.notes : []).map((n) => str(n)),
      }
    },
  })))

  // ---- 3q. hw_parts ------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_parts',
    description:
      'LCSC 元件目录（免 key）。action=search 关键词搜索；enrich 查一个编号的'
      + '型号/封装/尺寸/引脚/库存/分级/手册页，可选回填进工程；promote 把目录'
      + '条目升级成 datasheet 级（**必须带 --datasheet 手册页 URL 留痕**）。'
      + '目录数据是 catalog 级：可以写进设计让 2D/3D 有东西看，但**不进能力表'
      + '硬选型**（只在 explain_miss.rejected 出现）。封装查不到就留空，不猜尺寸。',
    parameters: {
      action: { type: 'string', description: 'search（默认）/ enrich / promote / info' },
      query: { type: 'string', description: 'search 的关键词（如 "stm32f103 lqfp48"）' },
      lcsc: { type: 'string', description: 'enrich/promote 的 LCSC 编号（如 C1523）' },
      project: { type: 'string', description: 'enrich 时回填到该工程' },
      refs: { type: 'string', description: '只回填这些位号（逗号分隔，默认按 lcsc 匹配）' },
      saveCatalog: { type: 'boolean', description: 'enrich 时写进能力表目录层（未确认）' },
      datasheet: { type: 'string', description: 'promote 必填：手册页 URL' },
      role: { type: 'string', description: 'promote 必填：它是什么（mcu/sensor/radio…）' },
      needs: { type: 'string', description: 'promote：它能干什么（逗号分隔，如 i2c,spi）' },
      limit: { type: 'number', description: 'search 返回条数（默认 10）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          action: { type: 'string' },
          status: { type: 'string' },
          query: { type: 'string' },
          total: { type: 'number' },
          items: { type: 'array', items: { type: 'string' } },
          lcsc: { type: 'string' },
          mpn: { type: 'string' },
          package: { type: 'string' },
          sizeMm: { type: 'string' },
          stock: { type: 'number' },
          lifecycle: { type: 'string' },
          libraryType: { type: 'string' },
          datasheet: { type: 'string' },
          cache: { type: 'string' },
          designFields: { type: 'string' },
          updated: { type: 'array', items: { type: 'string' } },
          key: { type: 'string' },
          trusted: { type: 'boolean' },
          notes: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          action: string; status: string; query: string; total: number
          items: string[]; lcsc: string; mpn: string; package: string
          sizeMm: string; stock: number; lifecycle: string; libraryType: string
          datasheet: string; cache: string; designFields: string
          updated: string[]; key: string; trusted: boolean; notes: string[]
        }
        const head = `# hw_parts ${v.action}：${v.status}`
        const body: string[] = []
        if (v.query) body.push(`关键词：${v.query}（共 ${v.total} 条）`)
        for (const row of v.items.slice(0, 12)) body.push(`* ${row}`)
        if (v.lcsc) {
          body.push(`${v.lcsc} → ${v.mpn}｜${v.package}｜${v.sizeMm}`
            + (v.stock >= 0 ? `｜库存 ${v.stock}` : '')
            + (v.lifecycle && v.lifecycle !== 'unknown' ? `｜${v.lifecycle}` : '')
            + (v.libraryType ? `｜${v.libraryType}` : ''))
        }
        if (v.cache) body.push(`数据来源：${v.cache}`)
        if (v.designFields) body.push(`可写回设计：${v.designFields}`)
        if (v.updated.length) body.push(`已回填位号：${v.updated.join('、')}`)
        if (v.key) body.push(`能力表键：${v.key}（信任级${v.trusted ? '已确认' : '目录级'}）`)
        if (v.datasheet) body.push(`手册页：${v.datasheet}`)
        for (const note of v.notes.slice(0, 5)) body.push(`! ${note}`)
        return text(head, ...body)
      },
    },
    execute: async (args) => {
      const a = args as {
        action?: unknown; query?: unknown; lcsc?: unknown; project?: unknown
        refs?: unknown; saveCatalog?: unknown; datasheet?: unknown
        role?: unknown; needs?: unknown; limit?: unknown
      }
      const action = str(a.action) || (str(a.lcsc) ? 'enrich' : 'search')
      const empty = {
        action, status: 'ok', query: '', total: 0, items: [] as string[],
        lcsc: '', mpn: '', package: '', sizeMm: '', stock: -1, lifecycle: '',
        libraryType: '', datasheet: '', cache: '', designFields: '',
        updated: [] as string[], key: '', trusted: false, notes: [] as string[],
      }
      if (action === 'info') {
        const payload = await design('parts-search')
        const ep = (payload.endpoints ?? {}) as Record<string, unknown>
        const cacheInfo = (payload.cache ?? {}) as Record<string, unknown>
        return { ...empty, action: 'info', status: str(payload.status, 'ok'),
          notes: [`端点：${Object.values(ep).join(' / ')}`,
                  str(cacheInfo.policy)] }
      }

      if (action === 'search') {
        const query = str(a.query)
        const cliArgs = [query]
        if (typeof a.limit === 'number') cliArgs.push(`--limit=${a.limit}`)
        const payload = await design('parts-search', ...cliArgs)
        const items = (Array.isArray(payload.items) ? payload.items : [])
          .map((raw) => {
            const row = (raw ?? {}) as Record<string, unknown>
            const stock = typeof row.stock === 'number' ? `库存 ${row.stock}` : '库存 ?'
            return `${str(row.lcsc)} ${str(row.mpn)}｜${str(row.package)}｜`
              + `${stock}｜${str(row.library_type)}`
          })
        return {
          ...empty,
          action: 'search',
          status: str(payload.status),
          query,
          total: num(payload.total),
          items,
          notes: findings(payload),
        }
      }

      if (action === 'enrich') {
        const lcsc = str(a.lcsc)
        const cliArgs = [lcsc]
        const project = str(a.project)
        if (project) cliArgs.push(`--project=${project}`)
        const refs = str(a.refs)
        if (refs) cliArgs.push(`--refs=${refs}`)
        if (a.saveCatalog === true) cliArgs.push('--save-catalog')
        const payload = await design('parts-enrich', ...cliArgs)
        const part = (payload.part ?? {}) as Record<string, unknown>
        const size = (part.size_mm ?? {}) as Record<string, unknown>
        const fields = (payload.design_fields ?? {}) as Record<string, unknown>
        return {
          ...empty,
          action: 'enrich',
          status: str(payload.status),
          lcsc: str(part.lcsc),
          mpn: str(part.mpn),
          package: str(part.package),
          sizeMm: size.w ? `${num(size.w)}×${num(size.h)}×${num(size.d)}mm` : '',
          stock: typeof part.stock === 'number' ? part.stock : -1,
          lifecycle: str(part.lifecycle),
          libraryType: str(part.library_type),
          datasheet: str(part.datasheet_url),
          cache: str(payload.cache),
          designFields: Object.keys(fields).length
            ? JSON.stringify(fields) : '',
          updated: (Array.isArray(payload.updated) ? payload.updated : []).map((x) => str(x)),
          key: str(payload.catalog_key),
          trusted: false,
          notes: [...findings(payload),
                  ...(Array.isArray(payload.findings) ? payload.findings : []).map((x) => str(x))],
        }
      }

      // promote
      const target = str(a.lcsc) || str(a.query)
      const cliArgs = [target]
      const datasheet = str(a.datasheet)
      if (datasheet) cliArgs.push(`--datasheet=${datasheet}`)
      const role = str(a.role)
      if (role) cliArgs.push(`--role=${role}`)
      const needs = str(a.needs)
      if (needs) cliArgs.push(`--needs=${needs}`)
      const payload = await design('parts-promote', ...cliArgs)
      return {
        ...empty,
        action: 'promote',
        status: str(payload.status),
        key: str(payload.key),
        datasheet: str(payload.datasheet),
        trusted: payload.trusted === true,
        notes: [
          ...(str(payload.status) === 'ok' ? [] : findings(payload)),
          ...(Array.isArray(payload.findings) ? payload.findings : []).map((x) => str(x)),
          str(payload.note),
        ].filter((x) => x !== ''),
      }
    },
  })))

  // ---- 3s. hw_req_draft / hw_req_apply (P5) ------------------------------
  //
  // 这两个是**会话里**用的：模型把一句话变成结构化需求草稿 → 校验 →
  // 人在面板上确认后才写 req.json。
  //
  // 刻意**不做**"模型直接出 design.json"：那会跳过 validate 与 generate，
  // 等于把"模型说行"当成"可以开工"。
  disposers.push(tools.register(defineTool({
    name: 'hw_req_draft',
    description:
      '写/校验需求草稿（**不是需求本身**）。action=draft 时把 requirement 落成 '
      + 'req_draft.json 并立刻校验；action=validate 只校验当前草稿；'
      + 'action=show（默认）报状态。字段与 generate.Requirement 一致：'
      + 'id / title / roles[] / interfaces[] / constraints{} / numbers{} / notes。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      action: {
        type: 'string',
        description: 'show（默认）/ draft / validate / list（列工作区草稿）',
      },
      requirement: {
        type: 'object',
        additionalProperties: true,
        description: '草稿内容：{id,title,roles,interfaces,constraints,numbers,notes}',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          ok: { type: 'boolean' },
          roles: { type: 'array', items: { type: 'string' } },
          applied: { type: 'boolean' },
          problems: { type: 'array', items: { type: 'string' } },
          warnings: { type: 'array', items: { type: 'string' } },
          next: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; ok: boolean; roles: string[]; applied: boolean
          problems: string[]; warnings: string[]; next: string
        }
        return text(
          `# ${v.project} 的需求草稿`,
          `角色（${v.roles.length}）：${v.roles.join(', ') || '（空）'}`,
          `校验：${v.ok ? '通过' : `${v.problems.length} 个问题`}`
            + `｜应用：${v.applied ? '已应用' : '未应用'}`,
          ...(v.problems.length > 0
            ? ['', '问题（拦 apply）：', ...v.problems.map((line) => `✗ ${line}`)]
            : []),
          ...(v.warnings.length > 0
            ? ['', '提示（不拦）：', ...v.warnings.map((line) => `! ${line}`)]
            : []),
          '',
          `下一步：${v.next}`,
        )
      },
      presentationMeta: (args) => viewHint(requireProject(args), 'req'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const action = str((args as { action?: unknown }).action, 'show').trim() || 'show'
      const requirement = (args as { requirement?: unknown }).requirement
      if (action === 'list') {
        // 工作区草稿（工程还不存在时的那份）。第 1 个位置参数是占位 id，
        // `--list` 不需要工程存在。
        const listed = await design('req-draft', '--list')
        const rows = ((listed.drafts ?? []) as Array<Record<string, unknown>>)
        return {
          project,
          ok: rows.every((row) => row.ok === true),
          roles: rows.map((row) => str(row.id)),
          applied: false,
          problems: rows.flatMap((row) =>
            ((row.problems ?? []) as unknown[]).map(String)),
          warnings: rows.map((row) =>
            `${str(row.id)}（${(row.roles as unknown[] | undefined)?.length ?? 0} 角色）`),
          next: rows.length === 0
            ? '工作区里没有草稿'
            : 'apply 时会顺手建工程（工程还不存在）',
        }
      }
      const argv = ['req-draft', project]
      if (action === 'draft') {
        if (requirement === null || typeof requirement !== 'object') {
          throw new Error('draft 需要 requirement（对象）')
        }
        argv.push(`--set=${JSON.stringify(requirement)}`)
      } else if (action === 'validate') {
        argv.push('--validate')
      }
      const payload = await design(...argv)
      noteHint(project, 'req')
      const validation = (payload.validation ?? {}) as Record<string, unknown>
      const problems = ((validation.problems ?? []) as Array<Record<string, unknown>>)
        .map((row) => str(row.message))
      const warnings = ((validation.warnings ?? []) as Array<Record<string, unknown>>)
        .map((row) => str(row.message))
      const draft = (payload.draft ?? {}) as Record<string, unknown>
      return {
        project,
        ok: validation.ok === true,
        roles: ((draft.roles ?? []) as unknown[]).map(String),
        applied: payload.applied === true,
        problems,
        warnings,
        next: str(payload.next)
          || (validation.ok === true
            ? '草稿合法，等人在面板上确认后 apply'
            : '按 problems 改草稿再 validate'),
      }
    },
  })))

  disposers.push(tools.register(defineTool({
    name: 'hw_req_apply',
    description:
      '把需求草稿变成真需求（写 req.json）。**这是人的动作**：调用前必须已经'
      + '有人在面板上点了「确认并应用」——不要替人调用。草稿没通过校验时会拒绝。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      confirm: { type: 'boolean', description: '必须是 true（人的确认）' },
      board: {
        type: 'string',
        description: '工程还不存在时建它用哪种板卡预设（默认 custom → 50×30 通用板）',
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          project: { type: 'string' },
          ok: { type: 'boolean' },
          file: { type: 'string' },
          created: { type: 'boolean' },
          lines: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          project: string; ok: boolean; file: string; created: boolean
          lines: string[]
        }
        return text(
          `# ${v.project} 的需求`,
          v.ok ? `已写入 ${v.file}` : '未写入（见下）',
          ...v.lines,
        )
      },
      presentationMeta: (args) => viewHint(requireProject(args), 'req'),
    },
    execute: async (args) => {
      const project = requireProject(args)
      const confirm = (args as { confirm?: unknown }).confirm === true
      const board = str((args as { board?: unknown }).board).trim()
      const argv = ['req-draft', project, '--apply']
      if (confirm) argv.push('--confirm')
      if (board !== '') argv.push(`--board=${board}`)
      const payload = await design(...argv)
      noteHint(project, 'req')
      const lines = findings(payload)
      const warnings = ((payload.warnings ?? []) as unknown[]).map(String)
      return {
        project,
        ok: ok(payload),
        file: str(payload.file) || str(payload.reason),
        created: payload.created_project === true,
        lines: [
          ...(confirm ? [] : ['**没有 confirm** —— 草稿合法也不会写 req.json']),
          ...(payload.created_project === true
            ? [`工程原本不存在：已顺手建一个（板卡 ${str(payload.board)}）`]
            : []),
          ...lines,
          ...warnings.map((line) => `提示：${line}`),
        ],
      }
    },
  })))

  // ---- 3r. hw_fwgen ------------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_fwgen',
    description:
      '固件生成：action=pins 从 .ioc 生成 pins.h（含 HAL 的 HWV_PIN_x = GPIO_PIN_n）；'
      + 'check 把固件里实际用到的引脚和硬件配置对撞（幽灵引脚/闲置/方向冲突）；'
      + 'scaffold 出工程骨架（arduino/platformio/esp-idf/stm32/avr 五个 target）；'
      + 'driver 生成某个器件的 init/read C 代码（7 个器件，每个带 datasheet 章节）。'
      + '幽灵引脚与方向冲突是 **error**：运行时必炸或静默失效。'
      + '非 STM32 固件（Arduino/AVR/ESP-IDF）报 not_ready，不猜别的 SDK 写法。',
    parameters: {
      action: { type: 'string', description: 'pins（默认）/ check / scaffold / driver / info' },
      project: { type: 'string', description: '工程目录名（driver 之外都要）' },
      ioc: { type: 'string', description: '.ioc 路径（相对路径按工程目录解析）' },
      src: { type: 'string', description: 'check 的固件源码目录（相对工程目录）' },
      target: { type: 'string', description: 'scaffold 的 target' },
      part: { type: 'string', description: 'driver 的器件名（如 SHT31）' },
      out: { type: 'string', description: '输出目录（相对工程目录）' },
      ref: { type: 'string', description: 'MCU 位号（默认 U1）' },
      drivers: { type: 'string', description: 'scaffold 要一并生成的驱动（逗号分隔）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          action: { type: 'string' },
          status: { type: 'string' },
          ok: { type: 'boolean' },
          mcu: { type: 'string' },
          header: { type: 'string' },
          assignments: { type: 'number' },
          macros: { type: 'string' },
          counts: { type: 'string' },
          ghost: { type: 'array', items: { type: 'string' } },
          idle: { type: 'array', items: { type: 'string' } },
          direction: { type: 'array', items: { type: 'string' } },
          target: { type: 'string' },
          files: { type: 'number' },
          out: { type: 'string' },
          notes: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          action: string; status: string; ok: boolean; mcu: string
          header: string; assignments: number; macros: string; counts: string
          ghost: string[]; idle: string[]; direction: string[]
          target: string; files: number; out: string; notes: string[]
        }
        const head = `# hw_fwgen ${v.action}：${v.status}`
          + (v.mcu ? `（${v.mcu}）` : '')
        const body: string[] = []
        if (v.header) body.push(`pins.h：${v.header}（${v.assignments} 个引脚）`)
        if (v.macros) body.push(`宏：${v.macros}`)
        if (v.counts) body.push(`核对：${v.counts}`)
        for (const row of v.direction.slice(0, 6)) body.push(`! 方向冲突 ${row}`)
        for (const row of v.ghost.slice(0, 8)) body.push(`! 幽灵引脚 ${row}`)
        for (const row of v.idle.slice(0, 8)) body.push(`· 闲置 ${row}`)
        if (v.target) body.push(`骨架：${v.target} → ${v.out}（${v.files} 个文件）`)
        for (const note of v.notes.slice(0, 5)) body.push(`* ${note}`)
        return text(head, ...body)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as {
        action?: unknown; project?: unknown; ioc?: unknown; src?: unknown
        target?: unknown; part?: unknown; out?: unknown; ref?: unknown
        drivers?: unknown
      }
      const project = str(a.project)
      const action = str(a.action) || (str(a.part) && project === '' ? 'driver' : 'pins')
      const base = {
        action, status: 'ok', ok: true, mcu: '', header: '',
        assignments: 0, macros: '', counts: '',
        ghost: [] as string[], idle: [] as string[], direction: [] as string[],
        target: '', files: 0, out: '', notes: [] as string[],
      }
      if (action === 'info' || project === '' && action !== 'driver') {
        const payload = await design('fw-scaffold')
        return { ...base, action: 'info', status: str(payload.status, 'ok'),
          notes: [`targets：${(Array.isArray(payload.targets) ? payload.targets : []).join(', ')}`] }
      }
      const opt: string[] = []
      const ioc = str(a.ioc)
      if (ioc) opt.push(`--ioc=${ioc}`)
      const ref = str(a.ref)
      if (ref) opt.push(`--ref=${ref}`)
      const out = str(a.out)
      if (out) opt.push(`--out=${out}`)

      if (action === 'driver') {
        const part = str(a.part)
        if (!part) throw new Error('driver 需要 part')
        const payload = await design('fw-driver', part, ...opt)
        return { ...base, action: 'driver', status: str(payload.status),
          files: (Array.isArray(payload.files) ? payload.files : []).length,
          out: out, notes: (Array.isArray(payload.findings) ? payload.findings : []).map((x) => str(x)) }
      }

      if (action === 'check') {
        const src = str(a.src)
        const cliArgs = [project, ...opt]
        if (src) cliArgs.push(`--src=${src}`)
        const payload = await design('pinmap-check', ...cliArgs)
        const counts = (payload.counts ?? {}) as Record<string, unknown>
        return {
          ...base,
          action: 'check',
          status: str(payload.status),
          ok: str(payload.status) === 'ok',
          mcu: str(payload.mcu),
          counts: `幽灵 ${num(counts.ghost)} / 闲置 ${num(counts.idle)} / 方向冲突 ${num(counts.direction)}`
            + `（核对了 ${num(payload.checked)} 个配置脚）`,
          ghost: (Array.isArray(payload.ghost) ? payload.ghost : [])
            .map((raw) => {
              const row = (raw ?? {}) as Record<string, unknown>
              return `${str(row.pin)} ← ${str(row.firmware)}（${str(row.why)}）`
            }),
          direction: (Array.isArray(payload.direction) ? payload.direction : [])
            .map((raw) => {
              const row = (raw ?? {}) as Record<string, unknown>
              return `${str(row.pin)} ← ${str(row.firmware)}（${str(row.why)}）`
            }),
          idle: (Array.isArray(payload.idle) ? payload.idle : [])
            .map((raw) => {
              const row = (raw ?? {}) as Record<string, unknown>
              return `${str(row.pin)} ${str(row.label)}`
            }),
          notes: (Array.isArray(payload.findings) ? payload.findings : []).map((x) => str(x)),
        }
      }

      if (action === 'scaffold') {
        const cliArgs = [project, ...opt]
        const target = str(a.target)
        if (target) cliArgs.push(`--target=${target}`)
        const driversArg = str(a.drivers)
        if (driversArg) cliArgs.push(`--drivers=${driversArg}`)
        const payload = await design('fw-scaffold', ...cliArgs)
        return {
          ...base,
          action: 'scaffold',
          status: str(payload.status),
          ok: str(payload.status) === 'ok',
          mcu: str(payload.mcu),
          target: str(payload.target),
          out: str(payload.out),
          files: num(payload.count),
          notes: (Array.isArray(payload.findings) ? payload.findings : []).map((x) => str(x)),
        }
      }

      const payload = await design('pinmap-gen', project, ...opt)
      return {
        ...base,
        action: 'pins',
        status: str(payload.status),
        ok: str(payload.status) === 'ok',
        mcu: str(payload.mcu),
        header: str(payload.header),
        assignments: num(payload.assignments),
        macros: (Array.isArray(payload.macros) ? payload.macros : []).slice(0, 10).join(' '),
        notes: (Array.isArray(payload.findings) ? payload.findings : []).map((x) => str(x)),
      }
    },
  })))

  // ---- 3t. hw_params -----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_params',
    description:
      '**飞控参数 → 能力映射**：解析 `name=value` 的 `.param` 与 `MF_PARAM(...)` 参数表，'
      + '按参数族算每条能力"参数齐不齐"。**参数只提供证据**：它说明增益有值，'
      + '不说明环稳定——能力状态仍由 `hw_plan` 的判据定（仍是 unknown）。',
    parameters: {
      project: { type: 'string', description: '工程目录名' },
      roots: { type: 'string', description: '扫描根（分号分隔）；默认当前工作区' },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          project: { type: 'string' }, status: { type: 'string' },
          rows: { type: 'string' }, summary: { type: 'string' },
        },
      },
      render: (_args, value) => {
        const v = value as { project: string; status: string; rows: string; summary: string }
        return text(`# ${v.project} 参数映射：${v.status}`,
          ...v.rows.split('\n').filter((x) => x !== ''), v.summary)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'firmware')
      },
    },
    execute: async (args) => {
      const a = args as { project?: unknown; roots?: unknown }
      const project = str(a.project)
      const cliArgs: string[] = [project]
      const roots = str(a.roots)
      if (roots !== '') cliArgs.push(`--roots=${roots}`)
      const payload = await design('params', ...cliArgs)
      const counts = (payload.counts ?? {}) as Record<string, unknown>
      const readiness = Array.isArray(payload.readiness) ? payload.readiness : []
      const absent = Array.isArray(payload.known_absent) ? payload.known_absent : []
      const rows: string[] = [
        `参数 ${num(counts.params)}（映射 ${num(counts.mapped)} / 未映射 ${num(counts.unmapped)}），`
        + `族 ${num(counts.families)}，涉及能力 ${num(counts.capabilities)}`,
        ...readiness.map((raw) => {
          const r = (raw ?? {}) as Record<string, unknown>
          const missing = Array.isArray(r.missing) ? (r.missing as string[]) : []
          return `* ${str(r.capability)} ${str(r.status)} ${num(r.have)}/${num(r.need)}`
            + (missing.length > 0 ? ` 缺 ${missing.join('、')}` : '')
        }),
        ...absent.map((raw) => {
          const x = (raw ?? {}) as Record<string, unknown>
          return `* unknown：${str(x.capability)} —— ${str(x.why)}`
        }),
      ]
      return {
        project,
        status: str(payload.status),
        rows: rows.join('\n'),
        summary: str(payload.note),
      }
    },
  })))

  // ---- 3s. hw_physical ----------------------------------------------------
  disposers.push(tools.register(defineTool({
    name: 'hw_physical',
    description:
      '实物测试 + 参数校准。action=list 看记录与统计（reproduced / not_reproduced / untried '
      + '三类分开数）；record 记一条（fail **必须**写 observed；implicated 的位号必须在设计里；'
      + 'sim_reproduced 是三态，none=没试过 ≠ false）；calibrate 用一条实测失败校准仿真参数，'
      + '**采纳标准是「旧设计在新参数下复现录像里的失败」，不是「新设计能过」**（每次 ≤2 个参数）；'
      + 'sheet 把视频拼成缩略图（要 ffmpeg）。'
      + '实测失败进 handoff 的 external 类，**不进阻断**。',
    parameters: {
      action: { type: 'string', description: 'list（默认）/ record / calibrate / sheet / info' },
      project: { type: 'string', description: '工程目录名' },
      test: { type: 'string', description: 'record：写了什么测试；calibrate：对应哪条记录 id' },
      result: { type: 'string', description: 'record：pass / fail / unknown' },
      observed: { type: 'string', description: 'record：看到什么现象（fail 必填）' },
      implicated: { type: 'string', description: 'record：牵涉的位号（逗号分隔，如 ref:W2,ref:M2）' },
      video: { type: 'string', description: 'record：视频路径' },
      simParams: { type: 'string', description: 'record：用哪套参数仿的（如 initial）' },
      reproduced: { type: 'string', description: 'record：true / false / none（没试过）' },
      candidates: { type: 'string', description: 'calibrate：JSON 数组，每个元素是一组参数' },
      old: { type: 'string', description: 'calibrate：旧设计工程名（不给则与当前相同）' },
      archive: { type: 'boolean', description: 'calibrate：落盘成对存档 sim_params/initial+learned' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          action: { type: 'string' },
          status: { type: 'string' },
          ok: { type: 'boolean' },
          total: { type: 'number' },
          byResult: { type: 'string' },
          reproduced: { type: 'number' },
          notReproduced: { type: 'number' },
          untried: { type: 'number' },
          failedUnreproduced: { type: 'string' },
          accepted: { type: 'boolean' },
          adopted: { type: 'string' },
          attempts: { type: 'array', items: { type: 'string' } },
          archiveFiles: { type: 'string' },
          notes: { type: 'array', items: { type: 'string' } },
        },
      },
      render: (_args, value) => {
        const v = value as {
          action: string; status: string; ok: boolean; total: number
          byResult: string; reproduced: number; notReproduced: number
          untried: number; failedUnreproduced: string; accepted: boolean
          adopted: string; attempts: string[]; archiveFiles: string
          notes: string[]
        }
        const head = `# hw_physical ${v.action}：${v.status}`
        const body: string[] = []
        if (v.total > 0) {
          body.push(`记录 ${v.total} 条：${v.byResult}`)
          body.push(`仿真复现 ${v.reproduced} / 没复现 ${v.notReproduced} / 没试过 ${v.untried}`)
          if (v.failedUnreproduced) body.push(`! 失败且仿真没复现：${v.failedUnreproduced}`)
        }
        if (v.adopted) body.push(`采纳：${v.adopted}（验收=${v.accepted ? '通过' : '未通过'}）`)
        for (const row of v.attempts.slice(0, 8)) body.push(`* ${row}`)
        if (v.archiveFiles) body.push(`存档：${v.archiveFiles}`)
        for (const note of v.notes.slice(0, 5)) body.push(`· ${note}`)
        return text(head, ...body)
      },
      presentationMeta: (args) => {
        const project = str((args as { project?: unknown }).project)
        return project === '' ? {} : viewHint(project, 'checks')
      },
    },
    execute: async (args) => {
      const a = args as {
        action?: unknown; project?: unknown; test?: unknown; result?: unknown
        observed?: unknown; implicated?: unknown; video?: unknown
        simParams?: unknown; reproduced?: unknown; candidates?: unknown
        old?: unknown; archive?: unknown
      }
      const project = str(a.project)
      const action = str(a.action) || 'list'
      const base = {
        action, status: 'ok', ok: true, total: 0, byResult: '',
        reproduced: 0, notReproduced: 0, untried: 0, failedUnreproduced: '',
        accepted: false, adopted: '', attempts: [] as string[],
        archiveFiles: '', notes: [] as string[],
      }
      if (action === 'info') {
        const payload = await design('physical')
        return { ...base, action: 'info', status: str(payload.status, 'ok'),
          notes: [str(payload.sim_reproduced), str(payload.reference_rule)] }
      }
      if (project === '') throw new Error('需要 project')

      if (action === 'sheet') {
        const video = str(a.video)
        if (!video) throw new Error('sheet 需要 video')
        const payload = await design('contact-sheet', video)
        return { ...base, action: 'sheet', status: str(payload.status),
          notes: [str(payload.reason), str(payload.command)].filter((x) => x !== '') }
      }

      if (action === 'record') {
        const cliArgs = [project, '--record', `--test=${str(a.test)}`,
          `--result=${str(a.result)}`]
        const observed = str(a.observed)
        if (observed) cliArgs.push(`--observed=${observed}`)
        const implicated = str(a.implicated)
        if (implicated) cliArgs.push(`--implicated=${implicated}`)
        const video = str(a.video)
        if (video) cliArgs.push(`--video=${video}`)
        const simParams = str(a.simParams)
        if (simParams) cliArgs.push(`--sim-params=${simParams}`)
        const reproduced = str(a.reproduced)
        if (reproduced) cliArgs.push(`--reproduced=${reproduced}`)
        const payload = await design('physical', ...cliArgs)
        const summary = (payload.summary ?? {}) as Record<string, unknown>
        const counts = (summary.by_result ?? {}) as Record<string, unknown>
        return {
          ...base,
          action: 'record',
          status: str(payload.status),
          ok: str(payload.status) === 'ok',
          total: num(summary.total),
          byResult: `fail ${num(counts.fail)} / pass ${num(counts.pass)} / unknown ${num(counts.unknown)}`,
          reproduced: num(summary.reproduced),
          notReproduced: num(summary.not_reproduced),
          untried: num(summary.untried),
          notes: (Array.isArray(payload.findings) ? payload.findings : []).map((x) => str(x)),
        }
      }

      if (action === 'calibrate') {
        const candidates = str(a.candidates)
        if (!candidates) throw new Error('calibrate 需要 candidates（JSON 数组）')
        const cliArgs = [project, `--candidates=${candidates}`]
        const test = str(a.test)
        if (test) cliArgs.push(`--test=${test}`)
        const old = str(a.old)
        if (old) cliArgs.push(`--old=${old}`)
        if (a.archive === true) cliArgs.push('--archive')
        const payload = await design('calibrate', ...cliArgs)
        const adopted = (payload.adopted ?? {}) as Record<string, unknown>
        const archive = (payload.archive ?? {}) as Record<string, unknown>
        return {
          ...base,
          action: 'calibrate',
          status: str(payload.status),
          ok: str(payload.status) === 'ok',
          accepted: payload.accepted === true,
          adopted: Object.entries(adopted).map(([k, v]) => `${k}=${String(v)}`).join(' '),
          attempts: (Array.isArray(payload.attempts) ? payload.attempts : [])
            .map((raw) => {
              const row = (raw ?? {}) as Record<string, unknown>
              const keys = Object.keys((row.params ?? {}) as Record<string, unknown>)
              return `${row.adopted === true ? '采纳' : '拒绝'} ${keys.join(',')}`
                + `（旧=${str(row.old_status)} 新=${str(row.new_status)}`
                + `${row.reason ? ' ' + str(row.reason) : ''}）`
            }),
          archiveFiles: archive.learned ? `${str(archive.initial)} + ${str(archive.learned)}` : '',
          notes: (Array.isArray(payload.notes) ? payload.notes : []).map((x) => str(x)),
        }
      }

      const payload = await design('physical', project)
      const tests = (payload.tests ?? {}) as Record<string, unknown>
      const counts = (tests.by_result ?? {}) as Record<string, unknown>
      const unrepro = (tests.failed_unreproduced ?? []) as unknown[]
      return {
        ...base,
        action: 'list',
        status: str(payload.status),
        total: num(tests.total),
        byResult: `fail ${num(counts.fail)} / pass ${num(counts.pass)} / unknown ${num(counts.unknown)}`,
        reproduced: num(tests.reproduced),
        notReproduced: num(tests.not_reproduced),
        untried: num(tests.untried),
        failedUnreproduced: unrepro.map((x) => str(x)).join(', '),
        notes: [str(tests.note)],
      }
    },
  })))

  return disposers
}