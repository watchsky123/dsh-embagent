/**
 * 客户端 Job 记录 —— "现在有没有长任务在跑"。
 *
 * 之前 `emitRun` 只有 `busy: '发射…'` 一个字符串：UI 知道在忙，但不知道
 * 什么时候开始、跑的是哪几段、失败在哪。P2 起发射会在**每段结束**写一次
 * `emit.json`（Python `on_progress`），所以时间线可以从快照读；这里只补
 * "客户端这一侧正在跑" 的那一个事实（快照可能还没轮到下一次轮询）。
 *
 * 没有 SSE、没有重试：`running` 由调用方 begin/end，粒度是整次发射。
 *
 * @module dsh-hw-velxio/client/jobs
 */

import { useSyncExternalStore } from 'react'

export interface JobSnapshot {
  readonly running: boolean
  readonly kind: string
  readonly project: string
  /** 人话标签（"发射" / "只重验"）。 */
  readonly label: string
  readonly startedAt: number
}

const IDLE: JobSnapshot = { running: false, kind: '', project: '',
                            label: '', startedAt: 0 }

class JobStore {
  private state: JobSnapshot = IDLE
  private listeners = new Set<() => void>()

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  getSnapshot = (): JobSnapshot => this.state

  begin(kind: string, project: string, label: string): void {
    this.set({ running: true, kind, project, label, startedAt: Date.now() })
  }

  end(): void {
    this.set(IDLE)
  }

  private set(next: JobSnapshot): void {
    this.state = next
    for (const listener of this.listeners) listener()
  }
}

export const jobs = new JobStore()

export function useJob(): JobSnapshot {
  return useSyncExternalStore(jobs.subscribe, jobs.getSnapshot, jobs.getSnapshot)
}