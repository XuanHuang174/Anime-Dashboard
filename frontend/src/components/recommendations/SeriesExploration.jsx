import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import useSeriesSession, { seriesSession } from '../../hooks/useSeriesSession.js'
import { COVERAGE, seriesBackground, seriesStatus } from '../../utils/recommendationSeries.js'
import { validateAnnotations, relationText } from '../../utils/seriesExploration.js'
import { watchRecommendationScope } from '../../utils/recommendationScopeMonitor.js'
import '../../styles/series-exploration.css'

const BadgeContext = createContext(null)
export function SeriesAnnotationsProvider({ client, items, enabled, blocked = false, catalogRevision, seriesRevision, onSeriesChanged, children }) {
  const session = useSeriesSession(), ids = [...new Set(items.map(item => item.bangumiId))].slice(0, 24)
  const key = JSON.stringify([ids, catalogRevision, seriesRevision]), scope = client.contentScope || 'safe'
  const changed = useRef(onSeriesChanged); changed.current = onSeriesChanged
  const [state, setState] = useState(null)
  useLayoutEffect(() => {
    setState(null)
    if (!enabled || blocked || !ids.length || !client.seriesAnnotations) return
    const request = new AbortController()
    const accept = raw => {
      if (request.signal.aborted || seriesSession() !== session) return
      const map = validateAnnotations(raw, ids, scope, seriesRevision)
      if (catalogRevision && raw.catalog_revision !== catalogRevision) throw new Error('目录版本已变化')
      setState({ key, client, map, scopeContext: raw.content_scope_context })
      return raw
    }
    client.seriesAnnotations(ids, request.signal).then(accept).catch(error => {
      if (!request.signal.aborted && seriesSession() === session) {
        if (error?.seriesChanged || error?.response?.data?.code === 'series_snapshot_changed') changed.current?.()
        setState({ key, client, error: true })
      }
    })
    return () => request.abort()
  }, [client, key, enabled, blocked, session, scope])
  useEffect(() => {
    if (!state?.scopeContext || state.key !== key || state.client !== client) return
    const expiry = state.scopeContext.scope_valid_until
    const monitor = watchRecommendationScope({ deadline: expiry ? Date.parse(expiry) : null,
      probe: signal => client.seriesAnnotations(ids, signal),
      onContext: raw => {
        if (seriesSession() !== session) return
        try {
          const map = validateAnnotations(raw, ids, scope, seriesRevision)
          if (catalogRevision && raw.catalog_revision !== catalogRevision) throw Error()
          setState({ key, client, map, scopeContext: raw.content_scope_context })
        } catch (error) { if (error.seriesChanged) changed.current?.(); setState({ key, client, error: true }) }
      }, onLost: () => { changed.current?.(); setState({ key, client, error: true }) } })
    return () => monitor.dispose()
  }, [client, key, session, state?.scopeContext?.scope_revision])
  const value = enabled && !blocked && state?.client === client && state.key === key && state.map ? { map: state.map, scope } : null
  return <BadgeContext.Provider value={value}>{children}</BadgeContext.Provider>
}

function useSeriesLink(scope) {
  const location = useLocation(), navigate = useNavigate()
  return source => navigate(`/recommendations/series/${source}`, { state: { seriesBackground: seriesBackground(location), seriesScope: scope } })
}
export function SeriesBadges({ item }) {
  const annotations = useContext(BadgeContext)
  const row = annotations?.map.get(item.bangumiId)
  if (!row?.facts.length) return null
  return <BadgeLinks item={item} row={row} scope={annotations.scope} />
}
function BadgeLinks({ item, row, scope }) {
  const open = useSeriesLink(scope)
  return <div className="series-badges" aria-label="已收录的作品关系">{row.facts.map(fact => <button type="button" className="series-badge"
    key={`${fact.source_id}:${fact.target_id}:${fact.kind}`} title={relationText(fact, item.bangumiId)} onClick={() => open(fact.source_id)}>
    {fact.target_id === item.bangumiId ? `${fact.relation_label} · 相对《${fact.source_title}》` : `关联${fact.relation_label}《${fact.target_title}》`} ↗
  </button>)}{row.truncated && <small>另有已收录关联</small>}</div>
}

export function RelatedWorks({ data, scope, blocked }) {
  const open = useSeriesLink(scope)
  if (!data) return null
  return <section className="series-related" aria-label="本作已确认的直接关联">
    <header><h2>本作已确认的直接关联</h2><span>与题材相似结果分开</span></header>
    {blocked ? <p role="status">关系依据已变化，请重新探索后查看。</p> : <>
      <p className="series-related-note">{COVERAGE[data.coverage]}。只依据已收录的直接关系，不推断整个系列或建议观看顺序；未列出不代表独立作品。</p>
      {['partial', 'failed'].includes(data.last_attempt_status) && data.coverage !== data.last_attempt_status && <p>最近获取{data.last_attempt_status === 'failed' ? '失败' : '不完整'}，本区使用上次可用资料。</p>}
      {!data.items.length ? <p>当前筛选下没有可展示的已确认直接关联。年份、类型、评分、隐藏及已有记录条件均保持生效。</p>
        : <ul>{data.items.map(row => <li key={row.bangumi_id}><div><h3>{row.title_zh || row.title_jp}</h3>
          <p>{relationText(row.facts[0], row.bangumi_id)}</p><small>{row.release_date || '发行日期未知'} · {row.release_format || '发行形式未知'} · {seriesStatus({ watch_status: row.watch_status, record_state: row.watch_status ? 'linked' : 'no_confirmed_record' })}</small></div>
          <button type="button" className="rec-button" onClick={() => open(row.facts[0].source_id)}>查看关系详情 ↗</button></li>)}</ul>}
      {data.truncated && <p>符合当前条件 {data.filtered_count} 部，本区展示前 12 部；相似区已排除全部已确认直接关联。</p>}
    </>}
  </section>
}
