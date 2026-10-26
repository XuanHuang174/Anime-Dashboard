import { useContext, useEffect, useLayoutEffect, useMemo, useSyncExternalStore } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import useSeriesSession, { seriesSession } from '../../hooks/useSeriesSession.js'
import { seriesBackground } from '../../utils/recommendationSeries.js'
import { createSeriesReminderController } from '../../utils/seriesReminders.js'
import { connectSeriesReminderUpdates } from '../../utils/seriesReminderUpdates.js'
import { SeriesOverlayContext } from '../../hooks/useSeriesJournalReturn.js'
import '../../styles/series-reminders.css'

export default function SeriesReminders({ client, enabled, controller }) {
  return enabled && client.seriesReminders && client.changeSeriesReminders
    ? <ReminderPanel key={client.contentScope || 'safe'} client={client} provided={controller} /> : null
}
function ReminderPanel({ client, provided }) {
  const session = useSeriesSession(), overlay = useContext(SeriesOverlayContext)
  const controller = useMemo(() => provided || createSeriesReminderController(client, () => seriesSession() === session), [provided, client, session])
  const state = useSyncExternalStore(controller.subscribe, controller.getState)
  const navigate = useNavigate(), location = useLocation()
  useLayoutEffect(() => {
    if (provided) return
    controller.activate()
    const disconnect = connectSeriesReminderUpdates(controller)
    return () => { disconnect(); controller.dispose() }
  }, [controller, provided])
  useEffect(() => {
    controller.setVisible(!overlay)
    if (!overlay) controller.initialize()
    return () => controller.setVisible(false)
  }, [controller, overlay])
  const data = state.data, expanded = !state.collapsed
  const busy = state.status === 'saving' || state.refreshing || state.preferenceSaving || state.dirty
  if (data && !state.dirty && !data.count && !data.hidden_count) return null
  const notify = () => {
    try { localStorage.setItem('anime-dashboard:series-reminders-changed:v1', `${Date.now()}-${Math.random()}`) } catch { /* optional opaque sync */ }
  }
  const change = async (action, fields) => { if (await controller.change(action, fields)) notify() }
  const toggle = async () => { if (await controller.setCollapsed(!state.collapsed)) notify() }
  return <section className={`series-reminders${expanded ? '' : ' is-collapsed'}`} aria-label="你看过的故事，还有后续" aria-busy={state.refreshing}>
    <header><div><h2>你看过的故事，还有后续</h2>
      {expanded && <p>依据本人已看完记录与已确认的直接续作关系</p>}</div>
      <div className="series-reminder-heading-actions">
        {data && !state.dirty && <span>符合条件 {data.count} 部</span>}
        <button type="button" className="rec-button" disabled={state.preferenceSaving || state.status === 'saving'}
          aria-expanded={expanded} aria-controls="series-reminder-items" onClick={toggle}>
          <span aria-hidden="true">{expanded ? '−' : '+'}</span> {expanded ? '收起后续提醒' : '展开后续提醒'}
        </button>
      </div></header>
    {(state.message || state.preferenceMessage) && <p role="status">{state.message || state.preferenceMessage} <button type="button" className="rec-text-button"
      disabled={state.refreshing || state.preferenceSaving} onClick={async () => { await controller.checkPresentation(true); if (expanded) await controller.refresh() }}>重新读取</button></p>}
    {expanded && <div id="series-reminder-items">
      {!data ? <p role="status">{state.status === 'error' ? '暂时没有可用的提醒资料。' : '正在读取后续提醒…'}</p> : <>
        <div className="series-reminder-update"><small role="status">{state.refreshing ? '正在更新后续提醒…' : state.dirty ? '提醒依据已更新，等待重新读取。' : '提醒已更新'}</small>
          <button type="button" className="rec-text-button" disabled={state.refreshing || state.status === 'saving'} onClick={() => controller.refresh()}>刷新提醒</button></div>
        <p className="rec-muted">{data.record_note} 只展示已收录且近期核对的直接关系，资料可能不完整；不推断观看顺序。</p>
        {!data.count ? <p>当前没有可展示的后续提醒。</p> : <ul>{data.results.map(row => <li key={row.bangumi_id}>
          <div><h3>{row.title_zh || row.title_jp}</h3><p>{row.facts.map((fact, i) => <span key={i}>根据你已记录看完的《{fact.completed_id === fact.source_id ? fact.source_title : fact.target_title}》{i < row.facts.length - 1 ? '；' : ''}</span>)}</p>
            <small>已确认续作 · {row.release_date || '发行日期未知'} · {row.release_format || '发行形式未知'} · 无已确认记录</small></div>
          <div className="series-reminder-actions"><button type="button" className="rec-button" disabled={busy} onClick={() => navigate(`/recommendations/series/${row.facts[0].source_id}`,
            { state: { seriesBackground: seriesBackground(location), seriesScope: client.contentScope || 'safe', seriesTarget: row.bangumi_id } })}>查看后续与关系 ↗</button>
            <button type="button" className="rec-text-button" disabled={busy} aria-label={`隐藏后续提醒：${row.title_zh || row.title_jp}`} onClick={() => change('hide', { bangumi_id: row.bangumi_id })}>隐藏这条提醒</button></div>
        </li>)}</ul>}
        {data.truncated && <p>符合条件 {data.count} 部，本区展示前 6 部；可从关系详情查看关联。</p>}
        <footer><small>收起与隐藏仅影响这个区块，不修改手账或长期偏好。</small>{data.hidden_count > 0 && <button type="button" className="rec-text-button" disabled={busy}
          onClick={() => change('restore')}>恢复隐藏的后续提醒（当前范围 {data.hidden_count} 部）</button>}</footer>
      </>}
    </div>}
  </section>
}
