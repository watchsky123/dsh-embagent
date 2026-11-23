/**
 * 分区注册表 —— **UI 的唯一事实源**。
 *
 * 在此之前，一个分区要在三处各写一份：`SECTIONS`（顺序）、`SECTION_LABEL`
 * （文案）、`PANELS`（分组入口），加一个面板就漏一处、对不齐。
 * 现在只有这张表；`store.ts` 从它派生，`HwIdeView` 只读派生结果。
 *
 * @module dsh-hw-velxio/client/features
 */

/** 底部面板的**分组**（3 个），不是平级的 8 个页签。 */
export type PanelId = 'health' | 'delivery' | 'velxio'

/** 分组内的分区。 */
export type SectionId =
  | 'verdict'
  | 'params'
  | 'firmware'
  | 'req'
  | 'checks'
  | 'budget'
  | 'deferred'
  | 'crosscheck'
  | 'blocks'
  | 'emit'
  | 'brief'
  | 'export'
  | 'evidence'
  | 'velxio'
  | 'sim'

/**
 * 徽章口径。**空 = 该分区不显示数字**。
 *
 * 只声明"数字从哪来"，具体计算留在 `HwIdeView.sectionCount`（它已经懂
 * `blocking`/`unknown` 的区别）；这里放一个枚举是为了让新增分区时必须
 * 想清楚"这个分区有没有值得上徽章的信号"。
 */
export type BadgeKind =
  | 'verdict'
  | 'errors'
  | 'budget'
  | 'deferred'
  | 'crosscheck'
  | 'none'

export interface FeatureDef {
  readonly id: SectionId
  readonly label: string
  readonly panel: PanelId
  /** 组内顺序（分段控件的顺序 = 人做事的顺序）。 */
  readonly order: number
  readonly badge: BadgeKind
  /** 没有数据时说什么。**不允许空字符串**：空表必须说话（AGENTS §0.2）。 */
  readonly empty: string
}

export const GROUP_LABEL: Readonly<Record<PanelId, string>> = {
  health: '体检',
  delivery: '交付',
  velxio: 'Velxio',
}

/**
 * 全部分区。**顺序 = `order`**；`panel` 相同的连续条目就是一组。
 */
export const FEATURES: readonly FeatureDef[] = [
  // -- 体检：这东西有没有毛病 -------------------------------------------
  { id: 'verdict', label: '结论', panel: 'health', order: 10, badge: 'verdict',
    empty: '还没跑过判定（点「检查」）' },
  { id: 'params', label: '参数', panel: 'health', order: 20, badge: 'none',
    empty: '没有参数表（固件里没写 .param，或没跑 fw-matrix）' },
  { id: 'checks', label: '判定', panel: 'health', order: 30, badge: 'errors',
    empty: '还没跑过检查' },
  { id: 'budget', label: '预算', panel: 'health', order: 40, badge: 'budget',
    empty: '还没有预算（点「预算」）' },
  { id: 'deferred', label: '未决', panel: 'health', order: 50, badge: 'deferred',
    empty: '没有未闭合的不可逆决策' },
  { id: 'crosscheck', label: '核对', panel: 'health', order: 60, badge: 'crosscheck',
    empty: '还没跑过核对' },
  { id: 'blocks', label: '积木', panel: 'health', order: 70, badge: 'none',
    empty: '还没按块装配（assemble）' },
  // -- 交付：先出板 → 看半成品 → 再打包 ---------------------------------
  { id: 'emit', label: '发射', panel: 'delivery', order: 10, badge: 'none',
    empty: '还没发射过（emit.json 不存在）' },
  { id: 'brief', label: '半成品', panel: 'delivery', order: 20, badge: 'none',
    empty: '还没跑过 scaffold（open_items.md 不存在）' },
  { id: 'export', label: '制造', panel: 'delivery', order: 30, badge: 'none',
    empty: '还没导出制造包' },
  { id: 'firmware', label: '固件', panel: 'delivery', order: 40, badge: 'none',
    empty: '还没扫到固件源文件' },
  { id: 'req', label: '需求', panel: 'delivery', order: 50, badge: 'none',
    empty: '还没有需求草稿（req-draft）' },
  { id: 'evidence', label: '证据/轨迹', panel: 'delivery', order: 60, badge: 'none',
    empty: '还没有事件轨迹' },
  // -- Velxio 后端 / 仿真 ------------------------------------------------
  { id: 'velxio', label: '后端', panel: 'velxio', order: 10, badge: 'none',
    empty: '还没探测 Velxio 后端' },
  { id: 'sim', label: '仿真', panel: 'velxio', order: 20, badge: 'none',
    empty: '还没启动仿真会话' },
]

const ORDERED = [...FEATURES].sort((a, b) => a.panel.localeCompare(b.panel) || a.order - b.order)

/** 每个分组里有哪几个分区（顺序即分段控件顺序）。 */
export const SECTIONS: Readonly<Record<PanelId, readonly SectionId[]>> = {
  health: ORDERED.filter((f) => f.panel === 'health').map((f) => f.id),
  delivery: ORDERED.filter((f) => f.panel === 'delivery').map((f) => f.id),
  velxio: ORDERED.filter((f) => f.panel === 'velxio').map((f) => f.id),
}

export const SECTION_LABEL: Readonly<Record<SectionId, string>> =
  Object.fromEntries(FEATURES.map((f) => [f.id, f.label])) as Record<SectionId, string>

/** 每个分组的默认分区 —— 打开就该看到最常用的那个。 */
export const DEFAULT_SECTION: Readonly<Record<PanelId, SectionId>> = {
  health: 'verdict', delivery: 'export', velxio: 'velxio',
}

export function featureOf(id: SectionId): FeatureDef | undefined {
  return FEATURES.find((f) => f.id === id)
}

/** 是不是一个已知分区。**别猜**：host 的 `view` 是自由字符串。 */
export function isSectionId(value: string): boolean {
  return FEATURES.some((f) => f.id === value)
}

/** 分区属于哪个分组。由注册表反推，避免两处各写一份对不齐。 */
export function groupOf(section: SectionId): PanelId {
  return featureOf(section)?.panel ?? 'health'
}