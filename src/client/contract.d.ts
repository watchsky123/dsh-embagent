/**
 * 本插件用到的宿主接口的**结构声明**。
 *
 * 为什么不用 `import`：这些服务由宿主应用（`app.asar` 里的 `dsh-client-*`
 * 包）提供，而不是本插件的依赖。声明成结构类型，缺一个服务时插件仍然能加载
 * （只是功能降级），而硬 `import` 会让整个插件在缺包时加载失败。
 *
 * 形状来自两处证据，不是猜的：
 *
 * 1. `@deepseek-ai/dsh-client-ui-trajectory` 的 `apply`（随安装包分发）：
 *    ``ctx.slots.inject('conversation.view', () => ctx.slots.register({...}))``
 *    ``ctx.uiConversation.views.register({ target, create })``
 * 2. `@deepseek-ai/dsh-client-ui-chat` 的 `conversation.view` 注册：`id` /
 *    `order` / `label` / `children` / `inject(sessionId)`。
 *
 * 风险明确写在这里：这是**内部契约**，DSH 升级可能改形状。`scripts/check_view.py`
 * 会对着安装包重读一次，把"类型对、运行时不对"的情况拦下来。
 *
 * @module dsh-hw-velxio/client/contract
 */

import type {} from '@deepseek-ai/cordis'

/** `ctx.slots`：注册渲染座位与它的注入面。 */
export interface HwSlotsService {
  /** 等某个所属座位就绪后再注册（所属尚未出现时挂起）。 */
  inject(slot: string, register: () => unknown): void
  register(definition: HwSlotDefinition, component: (props: never) => unknown): unknown
}

export interface HwSlotDefinition {
  name: string
  id: string
  order?: number
  label?: () => string
  locale?: string
  children?: Record<string, { kind: string; scope: string }>
  /** 每个会话渲染时调用，返回值并进组件 props。 */
  inject?: (sessionId: string) => Record<string, unknown>
  store?: unknown
}

/** 一个会话视图标签的数据模型工厂。 */
export interface HwConversationViewDefinition {
  target: string
  create: () => HwConversationViewBuilder
  toolCallFocus?: (callId: string) => unknown
}

export interface HwConversationViewBuilder {
  readonly empty: unknown
  replace(input: { nodes: readonly unknown[]; timeline: unknown }): unknown
  apply(input: { upserts: readonly unknown[]; timeline: unknown }): unknown
}

export interface HwConversationViews {
  register(definition: HwConversationViewDefinition): () => void
}

export interface HwConversationTarget {
  getSnapshot(): unknown
  subscribe(listener: () => void): () => void
}

export interface HwUiConversation {
  views: HwConversationViews
  binding(sessionId: string): { target(id: string): HwConversationTarget }
}

export interface HwSessionPromptPart {
  type: 'text'
  text: string
}

export interface HwSessionBinding {
  session: {
    prompt(parts: readonly HwSessionPromptPart[], mode: 'queue' | 'interrupt'): Promise<unknown> | unknown
    /**
     * 会话投影：`faceOf('workingDirectory')` 返回 DSH 当前工作目录的快照。
     * 形状取自 `@deepseek-ai/dsh-client-ui-chat` 的使用处（`client.js:184`）。
     */
    projections?: {
      faceOf(name: string): { getSnapshot(): unknown } | undefined
    }
  }
}

export interface HwSessionsService {
  binding(sessionId: string): HwSessionBinding | undefined
}

/**
 * 宿主侧的目录选择 seam（`ctx.directoryPicker`）。
 *
 * **这是宿主（node）上下文的服务，客户端上下文里 `ctx.get` 取不到**——
 * 客户端要走 `uiWorkspace.pickDirectory()` 或桌面壳的
 * `__DSH_DIRECTORY_PICKER__`（见 directoryPicker.ts）。这里保留类型只为
 * 读代码时不再把两层接错。
 *
 * `capability()` 返回判别联合；`native` 是 **kind 字段**，不是服务上的属性
 * ——hwforge 曾把 `picker.native?.pick` 当真，结果永远是 undefined。
 */
export interface HwDirectoryPicker {
  capability(): {
    readonly kind: 'native' | 'browse' | (string & {})
    readonly pick?: (signal: AbortSignal) => Promise<string | null>
    readonly list?: (path?: string, signal?: AbortSignal) => Promise<unknown>
    readonly createDirectory?: (path: string, name: string) => Promise<string>
  }
}

/**
 * 宿主 ui-workspace 的**客户端**服务（`@deepseek-ai/dsh-client-ui-workspace`
 * 里 `super(ctx, "uiWorkspace")` 注册的就是它）。它的 `pickDirectory()`
 * 内部 RPC 到宿主 directoryPicker 并把失败信封抛成 Error——这正是我们在
 * 浏览器上下文里能拿到的原生选择器入口。
 */
export interface HwUiWorkspace {
  pickDirectory(): Promise<unknown>
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    slots: HwSlotsService
    uiConversation: HwUiConversation
    sessions: HwSessionsService
    uiWorkspace: HwUiWorkspace
    directoryPicker?: HwDirectoryPicker
  }
}
