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
 * 判定永远来自确定性检查器；工具只负责**触发与呈现**，从不自造结论。
 *
 * @module dsh-hw-velxio/tools
 */

import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { bridge, design } from './bridge.js'
import { noteHint } from './service.js'

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
      presentationMeta: (args) => viewHint(requireProject(args), 'overview'),
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

      return {
        project,
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
          ok: { type: 'boolean' },
          errors: { type: 'number' },
          warnings: { type: 'number' },
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
      if (payload.status === 'not_ready') {
        throw new Error(findings(payload).join('；') || '预算前置缺失')
      }
      if (!ok(payload)) throw new Error(findings(payload).join('；') || '预算失败')
      noteHint(project, 'budget')
      const byCurrency = (payload.by_currency ?? {}) as Record<string, Record<string, unknown>>
      return {
        project,
        confidence: str(payload.confidence, 'none'),
        violations: ((payload.violations ?? []) as unknown[]).length,
        lines: Object.entries(byCurrency)
          .filter(([, row]) => row.over === true)
          .map(([currency, row]) =>
            `✗ ${currency}: ${num(row.used)} / ${num(row.limit)}`),
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
      'build/electrical/internals/review/fw_check/interfaces/…）做跨层判定。写操作。',
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

  return disposers
}
