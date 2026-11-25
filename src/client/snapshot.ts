/**
 * 「硬件工作台」这个会话视图的数据模型。
 *
 * 视图的内容全部来自宿主 RPC（快照），不来自会话事件流——所以这里是一个
 * **空壳 builder**：它满足 `views.register` 的契约，但不试图把工具调用翻译
 * 成视图节点。工具调用只通过 `hint()`（自增序号）触发一次刷新，这是最便宜
 * 的通知方式，也不会让"视图状态"和"模型状态"变成两份。
 *
 * @module dsh-embagent/client/snapshot
 */

import type { HwConversationViewBuilder } from './contract.js'

export interface HwIdeSnapshot {
  readonly nodes: readonly unknown[]
  readonly updatedAt: number
}

/** 增量/全量都收敛到同一个不可变快照。 */
export class HwIdeSnapshotBuilder implements HwConversationViewBuilder {
  private current: HwIdeSnapshot = { nodes: [], updatedAt: 0 }

  get empty(): HwIdeSnapshot {
    return this.current
  }

  replace(input: { nodes: readonly unknown[]; timeline: unknown }): HwIdeSnapshot {
    this.current = { nodes: input.nodes, updatedAt: Date.now() }
    return this.current
  }

  apply(input: { upserts: readonly unknown[]; timeline: unknown }): HwIdeSnapshot {
    const merged = [...this.current.nodes]
    for (const node of input.upserts) {
      const key = (node as { key?: unknown }).key
      const index = merged.findIndex((item) => (item as { key?: unknown }).key === key)
      if (index >= 0) merged[index] = node
      else merged.push(node)
    }
    this.current = { nodes: merged, updatedAt: Date.now() }
    return this.current
  }
}
