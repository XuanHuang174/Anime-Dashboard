import { useContext, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import useSeriesSession, { seriesSession } from '../../hooks/useSeriesSession.js'
import { seriesBackground } from '../../utils/recommendationSeries.js'
import { createSeriesReminderController } from '../../utils/seriesReminders.js'
import { watchRecommendationScope } from '../../utils/recommendationScopeMonitor.js'
import { SeriesOverlayContext } from '../../hooks/useSeriesJournalReturn.js'
import '../../styles/series-reminders.css'

export default function SeriesReminders({ client, enabled }) {
  return enabled && client.seriesReminders && client.changeSeriesReminders ? <ReminderPanel key={client.contentScope || 'safe'} client={client} /> : null
}
function ReminderPanel({ client }) {
  const session = useSeriesSession(), overlay = useContext(SeriesOverlayContext)
  const controllerRef = useRef(null)
  const [loaded, setLoaded] = useState(null)
  const state = loaded?.client === client && loaded.session === session ? loaded.state : { status: 'loading', data: null }
  const navigate = useNavigate(), location = useLocation()
  useLayoutEffect(() => {
    const controller = createSeriesReminderController(client, () => seriesSession() === session)
    controllerRef.current = controller
    const unsubscribe = controller.subscribe(state => setLoaded({ client, session, state }))
    return () => { unsubscribe(); controller.dispose() }
  }, [client, session])
  useEffect(() => {
    if (overlay) return
    const refresh = () => controllerRef.current?.refresh()
    refresh()
    window.addEventListener('recommendation-data-changed', refresh)
    const storage = event => { if (event.key === 'anime-dashboard:series-reminders-changed:v1' || event.key === 'anime-dashboard:recommendation-invalidate:v1') refresh() }
    window.addEventListener('storage', storage)
    return () => { window.removeEventListener('recommendation-data-changed', refresh); window.removeEventListener('storage', storage) }
  }, [client, session, overlay])
  useEffect(() => {
    if (overlay || !state.data || state.status !== 'ready') return
    const refresh = () => controllerRef.current?.refresh()
    const expiry = state.data?.content_scope_context?.scope_valid_until
    const monitor = watchRecommendationScope({ deadline: Math.min(state.data?.expires_at * 1000 || Infinity, expiry ? Date.parse(expiry) : Infinity),
      probe: refresh, onLost: refresh })
    return () => monitor.dispose()
  }, [client, session, overlay, state.status, state.data?.expires_at, state.data?.content_scope_context?.scope_revision])
  const data = state.data, busy = state.status !== 'ready'
  if (data && !data.count && !data.hidden_count && !data.collapsed) return null
  const change = async (action, fields) => {
    await controllerRef.current?.change(action, fields)
    // Opaque invalidation only; the other tab must authenticate and re-read its own state.
    try { localStorage.setItem('anime-dashboard:series-reminders-changed:v1', `${Date.now()}-${Math.random()}`) } catch { /* optional cross-tab sync */ }
  }
  return <section className="series-reminders" aria-label="你看过的故事，还有后续" aria-busy={busy}>
    <header><div><h2>你看过的故事，还有后续</h2><p>依据本人已看完记录与已确认的直接续作关系</p></div>
      {data && <button type="button" className="rec-button" disabled={busy} aria-expanded={!data.collapsed} aria-controls="series-reminder-items"
        onClick={() => change('collapse', { collapsed: !data.collapsed })}>{data.collapsed ? '展开后续提醒' : '收起后续提醒'}</button>}</header>
    {state.status === 'error' ? <div role="status"><p>{state.message}</p><button type="button" className="rec-button" onClick={() => controllerRef.current?.refresh()}>重新读取后续提醒</button></div>
      : !data ? <p role="status">正在读取后续提醒…</p> : <>
        {data.collapsed ? <p id="series-reminder-items">后续提醒已收起。当前有 {data.count} 部符合条件的作品。</p> : <div id="series-reminder-items">
          <p className="rec-muted">{data.record_note} 只展示已收录且近期核对的直接关系，资料可能不完整；不推断观看顺序。</p>
          {!data.count ? <p>当前没有可展示的后续提醒。</p> : <ul>{data.results.map(row => <li key={row.bangumi_id}>
            <div><h3>{row.title_zh || row.title_jp}</h3><p>{row.facts.map((fact, i) => <span key={i}>根据你已记录看完的《{fact.completed_id === fact.source_id ? fact.source_title : fact.target_title}》{i < row.facts.length - 1 ? '；' : ''}</span>)}</p>
              <small>已确认续作 · {row.release_date || '发行日期未知'} · {row.release_format || '发行形式未知'} · 无已确认记录</small></div>
            <div className="series-reminder-actions"><button type="button" className="rec-button" disabled={busy} onClick={() => navigate(`/recommendations/series/${row.facts[0].source_id}`,
              { state: { seriesBackground: seriesBackground(location), seriesScope: client.contentScope || 'safe', seriesTarget: row.bangumi_id } })}>查看后续与关系 ↗</button>
              <button type="button" className="rec-text-button" disabled={busy} aria-label={`隐藏后续提醒：${row.title_zh || row.title_jp}`} onClick={() => change('hide', { bangumi_id: row.bangumi_id })}>隐藏这条提醒</button></div>
          </li>)}</ul>}
          {data.truncated && <p>符合条件 {data.count} 部，本区展示前 6 部；可从关系详情查看关联。</p>}
        </div>}
        <footer><small>收起与隐藏仅影响这个区块，不修改手账或长期偏好。</small>{data.hidden_count > 0 && <button type="button" className="rec-text-button" disabled={busy}
          onClick={() => change('restore')}>恢复隐藏的后续提醒（当前范围 {data.hidden_count} 部）</button>}</footer>
      </>}
  </section>
}
