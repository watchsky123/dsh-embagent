/**
 * P3 治理面板：发射前预检 / 回读差异 / 成熟度 / ADR。
 *
 * 四个面板都是**动作驱动**：点按钮 → store 调 RPC → 结果留在 store →
 * 面板只投影（不做第二遍判定）。共同纪律：
 *
 * * 没有结果时不是"通过"，是"还没跑"（`empty` 文案必须说清）
 * * `not_ready` 载荷**照样显示**（预检没过的那一层就是内容）
 * * 表格只显示计数与最前面的几条，完整载荷在 store 里可查
 *
 * @module dsh-hw-velxio/client/panels_governance
 */

import type {
  AdrPayload, DiffPayload, MaturityPayload, PreflightPayload,
} from '../shared/types.js'

export interface GovernanceProps {
  readonly preflight: PreflightPayload | null
  readonly diff: DiffPayload | null
  readonly maturity: MaturityPayload | null
  readonly adr: AdrPayload | null
  readonly busy: string
  readonly onPreflight: () => void
  readonly onDiff: () => void
  readonly onMaturity: () => void
  readonly onAdr: () => void
}

function StatusRow(props: { status: string; reason?: string }): JSX.Element {
  const ok = props.status === 'ok'
  return (
    <div className={`hwv-row ${ok ? 'ok' : 'warn'}`}>
      <span className="hwv-gov-mark" aria-hidden="true">{ok ? '✓' : '!'}</span>
      <span className="msg">
        {props.reason !== undefined && props.reason !== ''
          ? props.reason : `状态：${props.status}`}
      </span>
    </div>
  )
}

function Empty(props: { text: string }): JSX.Element {
  return <div className="hwv-note">{props.text}——空白不代表通过。</div>
}

/** 发射前预检：三层（编号 → 器件 → 引脚名）。 */
export function PreflightPanel(props: GovernanceProps): JSX.Element {
  const data = props.preflight
  return (
    <div className="hwv-body">
      <div className="hwv-inline hwv-block-tight">
        <button className="hwv-btn primary" disabled={props.busy !== ''}
                title="只问系统库：每个 lcsc 能否解析、有无封装、引脚名是否存在。不开工程。"
                onClick={props.onPreflight}>运行预检</button>
        <span className="hwv-dim">三层：编号 → 器件 → 引脚名（硬拦）；封装关联只告警</span>
      </div>
      {data === null
        ? <Empty text="还没跑预检" />
        : (
          <>
            <StatusRow status={data.status} reason={data.reason} />
            <div className="hwv-gov-counts">
              {Object.entries(data.counts ?? {}).map(([key, value]) => (
                <span key={key} className="hwv-chip">{key} {String(value)}</span>
              ))}
            </div>
            {(data.pin_missing ?? []).length > 0 && (
              <div className="hwv-row warn">
                <span className="msg">
                  引脚不存在：{data.pin_missing?.slice(0, 6).map(
                    (row) => `${row.ref}.${row.missing.join(',')}`).join('；')}
                </span>
              </div>
            )}
            {(data.unresolved ?? []).length > 0 && (
              <div className="hwv-row warn">
                <span className="msg">
                  解析不到：{data.unresolved?.slice(0, 6).map(
                    (row) => `${row.ref}(${row.lcsc})`).join('；')}
                </span>
              </div>
            )}
            {(data.no_footprint ?? []).length > 0 && (
              <div className="hwv-row">
                <span className="msg">
                  暂无封装关联（缓存异步，仅告警）：
                  {data.no_footprint?.slice(0, 6).map((row) => row.ref).join('、')}
                </span>
              </div>
            )}
            {(data.unverified_pins ?? []).length > 0 && (
              <div className="hwv-row">
                <span className="msg">
                  引脚表没收录：{data.unverified_pins?.join('、')}
                </span>
              </div>
            )}
            {data.note !== undefined && <div className="hwv-note">{data.note}</div>}
          </>
        )}
    </div>
  )
}

/** 回读差异：设计 vs 立创 PCB 网表（逐引脚）。 */
export function DiffPanel(props: GovernanceProps): JSX.Element {
  const data = props.diff
  const counts = data?.counts ?? {}
  return (
    <div className="hwv-body">
      <div className="hwv-inline hwv-block-tight">
        <button className="hwv-btn primary" disabled={props.busy !== ''}
                title="打开立创工程读回 PCB 网表，逐引脚对比（几十秒）"
                onClick={props.onDiff}>回读对比</button>
        <span className="hwv-dim">会开一个立创会话；回写只做加法/搬迁</span>
      </div>
      {data === null
        ? <Empty text="还没跑过回读对比" />
        : (
          <>
            <StatusRow status={data.status} reason={data.reason} />
            <div className="hwv-gov-counts">
              {Object.entries(counts).map(([key, value]) => (
                <span key={key}
                      className={`hwv-chip${key === 'total' && Number(value) > 0 ? ' warn' : ''}`}>
                  {key} {String(value)}
                </span>
              ))}
            </div>
            {(data.differences ?? []).length > 0 && (
              <table className="hwv-table hwv-table-tight">
                <thead><tr><th>类型</th><th>位号/网</th><th>设计</th><th>EDA</th></tr></thead>
                <tbody>
                  {data.differences?.slice(0, 20).map((row, index) => (
                    <tr key={index}>
                      <td>{String(row['kind'] ?? '')}</td>
                      <td>{String(row['ref'] ?? row['net'] ?? '')}</td>
                      <td>{String(row['pin'] ?? '')} {String(row['design_net'] ?? '')}</td>
                      <td>{String(row['eda_net'] ?? '')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {data.note !== undefined && <div className="hwv-note">{data.note}</div>}
          </>
        )}
    </div>
  )
}

/** 成熟度：六维三态（只读本工程，不与参考工程比较）。 */
export function MaturityPanel(props: GovernanceProps): JSX.Element {
  const data = props.maturity
  return (
    <div className="hwv-body">
      <div className="hwv-inline hwv-block-tight">
        <button className="hwv-btn" disabled={props.busy !== ''}
                onClick={props.onMaturity}>刷新成熟度</button>
        <span className="hwv-dim">
          需求 / 规格 / 块 / 约束 / 证据 / 发射 —— 只读本工程产物
        </span>
      </div>
      {data === null
        ? <Empty text="还没算过成熟度" />
        : (
          <>
            <StatusRow status={data.status}
                       reason={`${data.level ?? 0}/${data.total ?? 6} 维通过`} />
            <div className="hwv-gov-dims">
              {data.dimensions?.map((dim) => (
                <div key={dim.id} className={`hwv-gov-dim ${dim.status}`}>
                  <span className="id">{dim.id}</span>
                  <span className="st">{dim.status}</span>
                  <span className="detail">{dim.detail ?? ''}</span>
                  {dim.need !== undefined && dim.need !== ''
                    ? <span className="need">→ {dim.need}</span> : null}
                </div>
              ))}
            </div>
            {data.note !== undefined && <div className="hwv-note">{data.note}</div>}
          </>
        )}
    </div>
  )
}

/** ADR：列表 + 四节校验。 */
export function AdrPanel(props: GovernanceProps): JSX.Element {
  const data = props.adr
  return (
    <div className="hwv-body">
      <div className="hwv-inline hwv-block-tight">
        <button className="hwv-btn" disabled={props.busy !== ''}
                onClick={props.onAdr}>列表 + 校验</button>
        <span className="hwv-dim">四节（决定/理由/被否方案/证据）缺一不算 ADR</span>
      </div>
      {data === null
        ? <Empty text="还没读过 ADR" />
        : (
          <>
            <StatusRow status={data.status}
                       reason={(data.problems ?? []).join('；')
                         || `共 ${data.entries?.length ?? 0} 条`} />
            {(data.entries ?? []).length > 0 && (
              <table className="hwv-table hwv-table-tight">
                <thead><tr><th>编号</th><th>标题</th><th>日期</th><th>缺节</th></tr></thead>
                <tbody>
                  {data.entries?.map((entry) => {
                    const row = data.rows?.find((item) => item.id === entry.id)
                    return (
                      <tr key={entry.id}>
                        <td>{entry.id}</td>
                        <td>{entry.title}</td>
                        <td>{entry.date ?? ''}</td>
                        <td className={row !== undefined && row.missing.length > 0
                          ? 'hwv-fw-direction' : ''}>
                          {row === undefined ? '—'
                            : (row.missing.length === 0 ? '齐' : row.missing.join('、'))}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </>
        )}
    </div>
  )
}