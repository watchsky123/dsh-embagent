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
import { bridge, design, designPut } from './bridge.js'
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
      const summary = (payload.summary ?? {}) as Record<string, unknown>
      return {
        project,
        board,
        parts: num(summary.parts),
        path: str(payload.path, ''),
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
        return project === '' ? {} : viewHint(project, '2d')
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
      const plan = (payload.plan ?? {}) as Record<string, unknown>
      const wiring = (payload.wiring ?? {}) as Record<string, unknown>
      const enhance = (payload.role_enhancement ?? {}) as Record<string, unknown>
      return {
        roles: ((payload.roles ?? []) as unknown[]).map(String),
        addedRoles: ((enhance.added ?? []) as unknown[]).map(String),
        parts: num(plan.part_count),
        nets: num(wiring.net_count),
        unmatched: ((plan.unmatched ?? []) as unknown[]).map(String),
        unrouted: ((wiring.unrouted ?? []) as unknown[]).map(String),
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
      action: { type: 'string', description: 'list（默认）/ read / coverage' },
      name: { type: 'string', description: 'read 时必填：日志文件名' },
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

  return disposers
}
