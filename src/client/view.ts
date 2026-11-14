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
  const face: WorkbenchFace = connection === undefined
    ? unavailableFace('宿主连接不可用：会话视图拿不到 RPC 通道')
    : createFace(connection)

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
      return {
        sessionId,
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
 * 构建一条交给会话的 AI 提示词。
 *
 * 纪律：提示词只**陈述事实与证据**（哪条检查、measured/limit、未验证项），
 * 不要求模型"重新判定"——判定只来自确定性检查器。模型的作用是解释与给
 * 改进建议，不是当第二个判定器。
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
}): string {
  const lines = [
    `请评审硬件项目「${input.project}」${input.board ? `（板卡 ${input.board}）` : ''}。`,
    input.file !== undefined ? `当前文件：${input.file}` : '',
    '已知判定（来自确定性检查器；不要重新判定，只做解释与改进建议）：',
    ...input.verdicts.map((verdict) =>
      `- ${verdict.ok ? '通过' : '未通过'} ${verdict.check}`
      + (verdict.measured !== undefined
        ? `：measured=${JSON.stringify(verdict.measured)} limit=${JSON.stringify(verdict.limit)}`
        : '')
      + (verdict.message !== undefined ? ` — ${verdict.message}` : '')),
    input.notVerified.length > 0
      ? `未验证（不要当成通过）：${input.notVerified.join('；')}`
      : '',
    '请给出：1) 最可能的问题 2) 需要改哪个元件/文件 3) 下一步应运行哪个检查。',
  ].filter((line) => line !== '')
  return lines.join('\n')
}
