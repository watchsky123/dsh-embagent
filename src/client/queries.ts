/**
 * 按域查询缓存 —— 让"任何动作后全量刷"变成"只失效相关的域"。
 *
 * 之前 store 每个动作都 `open(project)`（一次 snapshot 往返 = 2D/3D/判定/
 * 预算/闸门全算一遍）+ `reloadProjects`。43 件的板一次刷新就是几秒，
 * 而用户只是点了一个元件的画像。
 *
 * 这里只做三件事：**短期缓存、并发去重、按前缀失效**。没有后台刷新、
 * 没有重试策略——那些会改变"数据什么时候算新的"的语义，先不做。
 *
 * @module dsh-embagent/client/queries
 */

interface Entry<T> {
  readonly value?: T
  readonly at: number
  readonly promise?: Promise<T>
}

export class QueryCache {
  private readonly map = new Map<string, Entry<unknown>>()

  /**
   * 取缓存或执行 `fn`。`ttlMs` 内命中直接返回；并发调用共用同一个 promise
   * （避免同一帧内点两次元件发两次 RPC）。
   */
  async fetch<T>(key: string, fn: () => Promise<T>, ttlMs = 15_000): Promise<T> {
    const hit = this.map.get(key) as Entry<T> | undefined
    const now = Date.now()
    if (hit !== undefined) {
      if (hit.value !== undefined && now - hit.at <= ttlMs) return hit.value
      if (hit.promise !== undefined) return hit.promise
    }
    const promise = fn()
      .then((value) => {
        this.map.set(key, { value, at: Date.now() })
        return value
      })
      .catch((error: unknown) => {
        this.map.delete(key)
        throw error
      })
    this.map.set(key, { at: now, promise })
    return promise
  }

  /** 只看不取（渲染层可用来做"有旧值就先画"）。 */
  peek<T>(key: string): T | undefined {
    return (this.map.get(key) as Entry<T> | undefined)?.value
  }

  /** 按前缀失效；返回删掉的条数（便于测试断言）。 */
  invalidate(prefix = ''): number {
    let removed = 0
    for (const key of [...this.map.keys()]) {
      if (prefix === '' || key.startsWith(prefix)) {
        this.map.delete(key)
        removed += 1
      }
    }
    return removed
  }

  clear(): void {
    this.map.clear()
  }
}