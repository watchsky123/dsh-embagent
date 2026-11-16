/**
 * 会话视图注册 —— 「硬件工作台」。
 *
 * 机制来自安装包里的两个同侪（`@deepseek-ai/dsh-client-ui-chat` 与
 * `-trajectory`），不是猜的：
 *
 *   1. `ctx.uiConversation.views.register({ target, create })` —— 视图的数据模型；
 *   2. `ctx.slots.inject('conversation.view', () => ctx.slots.register({...}, View))`
 *      —— 标签 + 组件，`id` 必须等于 `target`，`order` 决定它在
 *      Chat(0) / Trajectory(10) 之后的位置；
 *   3. `ctx.sessions.binding(sessionId).session.prompt(parts, 'queue')` —— AI 接入。
 *
 * 与 v1 的关键差别：这里占据**中栏主区域**（整个会话宽度），才放得下
 * 「文件/2D/3D/检查器」四栏；v1 挤在右侧边栏里，控件位置和名称都不对。
 *
 * @module dsh-hw-velxio/client/view
 */

import type { Context } from '@deepseek-ai/cordis'
import type { ConnectionHandle } from '@deepseek-ai/dsh-client-connection/client'
import { HwIdeView } from './HwIdeView.js'
import { createFace, RpcError } from './rpc.js'
import type { WorkbenchFace } from './rpc.js'
import { HwIdeSnapshotBuilder } from './snapshot.js'
import type { HwSessionsService, HwUiConversation } from './contract.js'
import type { DirectoryPickerSeams } from './directoryPicker.js'

/** 视图 target，同时是 slot 注册的 `id`。 */
export const HW_IDE_TARGET = 'hw-ide'
/** 标签文字。不接 locale 字典：一个标签不值得引入一套翻译键。 */
export const HW_IDE_LABEL = '硬件工作台'
/** Chat=0, Trajectory=10, 本视图=20。 */
export const HW_IDE_ORDER = 20

/** 拿不到宿主连接时的占位 face：每个方法都明确失败，而不是返回空数据。 */
function unavailableFace(reason: string): WorkbenchFace {
  return new Proxy({} as WorkbenchFace, {
    get: () => async () => { throw new RpcError(reason) },
  })
}

/**
 * 注册「硬件工作台」会话视图。
 *
 * `uiConversation` 用 `ctx.get` 取而不是属性访问：它不在 `inject` 列表里时
 * 属性访问会抛 `cannot get property without inject`，而 `get` 只是返回
 * `undefined`——那样我们能给出一句可读的降级日志，而不是整插件加载失败。
 */
export function registerHwIdeView(ctx: Context): void {
  const uiConversation = ctx.get('uiConversation') as HwUiConversation | undefined
  if (uiConversation === undefined || uiConversation.views === undefined) {
    console.warn('[dsh-hw-velxio] 宿主没有 uiConversation.views，会话视图未注册'
      + '（工具仍可用）')
    return
  }

  const connection = ctx.get('connection') as ConnectionHandle | undefined
  // 目录选择的**客户端**入口，每次点击时才解析（装载顺序无关）：
  //   1. 桌面壳 preload 暴露的 `__DSH_DIRECTORY_PICKER__`（Electron 窗口里
  //      直达主进程的原生选择框）；
  //   2. 宿主 ui-workspace 客户端服务的 `pickDirectory()`（RPC 到宿主的
  //      directoryPicker，native/browse 由宿主启动时探测决定）。
  // 注意：**不能**在这里 `ctx.get('directoryPicker')`——那是宿主（node）
  // 上下文的服务，浏览器上下文里永远取不到。上一版正是栽在这一行。
  const seams = (): DirectoryPickerSeams => ({
    desktop: (globalThis as Record<string, unknown>).__DSH_DIRECTORY_PICKER__ as
      { pick(): Promise<unknown> } | undefined,
    uiWorkspace: ctx.get('uiWorkspace') as
      { pickDirectory(): Promise<unknown> } | undefined,
  })
  const face: WorkbenchFace = connection === undefined
    ? unavailableFace('宿主连接不可用：会话视图拿不到 RPC 通道')
    : createFace(connection, seams)

  ctx.effect(
    () => uiConversation.views.register({
      target: HW_IDE_TARGET,
      create: () => new HwIdeSnapshotBuilder(),
    }),
    'dsh-hw-velxio: conversation view',
  )

  ctx.slots.inject('conversation.view', () => ctx.slots.register({
    name: 'conversation.view',
    id: HW_IDE_TARGET,
    order: HW_IDE_ORDER,
    label: () => HW_IDE_LABEL,
    children: {},
    inject: (sessionId: string) => {
      // `ctx.sessions` 已被宿主运行时声明为它的 `SessionStore`；我们只用到
      // `binding()`，用 `get` 取再按结构收窄，避免与宿主声明冲突。
      const sessions = ctx.get('sessions') as HwSessionsService | undefined
      const binding = sessions?.binding(sessionId)
      // DSH 当前工作目录：会话的 `workingDirectory` 投影（ui-chat 用的就是
      // 这条路径 —— `session.projections.faceOf('workingDirectory').getSnapshot()`）。
      // 投影缺失时给空串，UI 会显示"（未知）"而不是猜一个目录。
      let cwd = ''
      try {
        const projection = binding?.session?.projections?.faceOf('workingDirectory')
        const value = projection?.getSnapshot()
        cwd = typeof value === 'string' ? value : ''
      } catch {
        cwd = ''
      }
      return {
        sessionId,
        cwd,
        face,
        ai: {
          /** 把上下文动作发进当前会话，走 DSH 正常提交路径。 */
          send: (text: string): void => {
            if (binding === undefined) {
              console.warn('[dsh-hw-velxio] 会话不可用，AI 提示未发送')
              return
            }
            void binding.session.prompt([{ type: 'text', text }], 'queue')
          },
        },
      }
    },
  }, HwIdeView as never))
}

/**
 * 构建「AI 评审」提示词。
 *
 * 纪律：提示词只**陈述事实与证据**（哪条检查、measured/limit、未验证项），
 * 不要求模型"重新判定"——判定只来自确定性检查器。模型的作用是解释与给
 * 改进建议，不是当第二个判定器。
 *
 * 上一版只送 `failed` 项，于是"9 条全部通过"的工程会送出空清单，模型完全
 * 不知道板子上有 0 个元件。现在补齐三件事：
 *
 *   1. **全部** finding 都送（标注通过/未通过），并单列 `vacuous`——
 *      空集通过是假绿灯，必须让模型看见；
 *   2. `phase`（确定性阶段）与 `veto`（差什么）；
 *   3. `tools` 提示模型有哪些受校验的写路径（不要用通用文件工具改设计）。
 */
export function composeReviewPrompt(input: {
  project: string
  board?: string
  file?: string
  verdicts: ReadonlyArray<{
    check: string
    ok: boolean
    measured?: unknown
    limit?: unknown
    message?: string
  }>
  notVerified: readonly string[]
  /** 假绿灯：因空集而"通过"的检查。 */
  vacuous?: readonly string[]
  /** 确定性阶段（empty/partial/complete）。 */
  phase?: { phase: string; reason: string; veto: readonly string[] }
  /** 用户在对话框里输入的具体要求（P3）。 */
  request?: string
  /**
   * 工作区勘察摘要。**这是评审能不能看到真实内容的关键**：上一版 prompt
   * 关于"这是什么"只有 `project.id` + `board.name` 两项，于是 2085 个文件
   * 的工作区在模型眼里就是「一个空的 Arduino Uno」。
   */
  workspace?: {
    readonly path: string
    readonly entries: number
    readonly notAdopted: number
    readonly kinds: readonly string[]
    readonly readFiles: number
    readonly totalFiles: number
    readonly mcus: readonly string[]
  }
}): string {
  const lines = [
    `请评审硬件项目「${input.project}」${input.board ? `（板卡 ${input.board}）` : ''}。`,
    input.file !== undefined ? `当前文件：${input.file}` : '',
    input.workspace !== undefined
      ? [
        `工作区：「${input.workspace.path}」`,
        `  条目（真实项目）：${input.workspace.entries} 个，其中 `
        + `${input.workspace.notAdopted} 个未接入工作台`,
        `  类型：${input.workspace.kinds.join(' · ') || '（无）'}`,
        input.workspace.mcus.length > 0
          ? `  检测到的 MCU：${input.workspace.mcus.join(', ')}`
          : '',
        `  文件读取：${input.workspace.readFiles} / `
        + `${input.workspace.totalFiles}（其余未读——不是「没有问题」，是「没看」）`,
      ].filter((line) => line !== '').join('\n')
      : '',
    input.phase !== undefined
      ? `项目阶段（确定性推导，不是判断）：${input.phase.phase}`
        + ` — ${input.phase.reason}`
        + (input.phase.veto.length > 0
          ? `\n差什么：\n${input.phase.veto.map((v) => `  · ${v}`).join('\n')}`
          : '')
      : '',
    '已知判定（来自确定性检查器；不要重新判定，只做解释与改进建议）：',
    ...input.verdicts.map((verdict) =>
      `- ${verdict.ok ? '通过' : '未通过'} ${verdict.check}`
      + (verdict.measured !== undefined
        ? `：measured=${JSON.stringify(verdict.measured)} limit=${JSON.stringify(verdict.limit)}`
        : '')
      + (verdict.message !== undefined ? ` — ${verdict.message}` : '')),
    input.vacuous !== undefined && input.vacuous.length > 0
      ? `⚠ 以下"通过"是**空集通过**（没有可检查的对象），不是真的核对过：`
        + `${input.vacuous.join(', ')}。不要把整份"全部通过"当成设计没问题。`
      : '',
    input.notVerified.length > 0
      ? `未验证（不要当成通过）：${input.notVerified.join('；')}`
      : '',
    input.request !== undefined && input.request.trim() !== ''
      ? `用户的具体要求：${input.request.trim()}`
      : '',
    '请给出：1) 最可能的问题 2) 需要改哪个元件/文件 3) 下一步应运行哪个检查。',
    '',
    '可用工具（判定与写入都走它们，不要用通用文件工具直接改 design.json）：',
    'hw_survey / hw_snapshot / hw_check / hw_budget / hw_crosscheck / hw_import /',
    'hw_new / hw_put / hw_setpart / hw_deferred / hw_export / hw_block / hw_workspace',
  ].filter((line) => line !== '')
  return lines.join('\n')
}
