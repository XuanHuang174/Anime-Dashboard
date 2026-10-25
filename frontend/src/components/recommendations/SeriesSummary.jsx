import { useEffect, useLayoutEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import defaultClient from '../../api/recommendations.js'
import useSeriesSession, { seriesSession } from '../../hooks/useSeriesSession.js'
import { COVERAGE, validateSeries, seriesBackground, seriesFailure } from '../../utils/recommendationSeries.js'
import { watchRecommendationScope } from '../../utils/recommendationScopeMonitor.js'
import '../../styles/recommendation-series.css'

export default function SeriesSummary(props) {
  const session = useSeriesSession()
  return <Summary key={`${session}:${props.bangumiId || props.recordId}`} {...props} session={session} />
}
function Summary({ client: baseClient = defaultClient, bangumiId, recordId, session, compact = false }) {
  const [state, setState] = useState({ status: 'loading' }), [attempt, retry] = useState(0)
  const [adult, setAdult] = useState(false)
  const client = useMemo(() => recordId && adult ? baseClient.forScope('include_adult') : baseClient, [baseClient, recordId, adult])
  const navigate = useNavigate(), location = useLocation(), scope = client.contentScope || 'safe'
  useLayoutEffect(() => {
    const controller = new AbortController()
    const current = () => !controller.signal.aborted && seriesSession() === session
    setState({ status: 'loading' })
    if (!client.series) { setState({ status: 'disabled' }); return () => controller.abort() }
    ;(async () => {
      let source = bangumiId
      if (recordId) {
        const ref = await client.seriesRecord(recordId, controller.signal)
        if (ref?.contract_version !== 'recommendations.series.record.v1' || ref.record_id !== recordId) throw new Error('手账身份合同不兼容。')
        if (ref.identity_state !== 'linked' || !Number.isInteger(ref.bangumi_id)) {
          if (current()) setState({ status: 'unlinked' })
          return
        }
        source = ref.bangumi_id
      }
      const data = validateSeries(await client.series(source, null, controller.signal), source, scope)
      if (current()) setState({ status: 'ready', data })
    })().catch(error => { if (current()) setState({ status: error?.response?.status === 503 ? 'disabled' : 'error', message: seriesFailure(error) }) })
    return () => controller.abort()
  }, [client, bangumiId, recordId, attempt, session, scope])
  const data = state.data
  useEffect(() => {
    if (state.status !== 'ready') return
    const expiry = data.content_scope_context.scope_valid_until
    const deadline = Math.min(Date.now() + 900000, expiry === null || expiry === undefined ? Infinity : Date.parse(expiry))
    const monitor = watchRecommendationScope({ deadline,
      probe: signal => client.series(data.selected_seed.bangumi_id, data.context, signal),
      onContext: raw => {
        if (seriesSession() !== session) return
        try { setState({ status: 'ready', data: validateSeries(raw, data.selected_seed.bangumi_id, scope) }) }
        catch (error) { setState({ status: 'error', message: seriesFailure(error) }) }
      },
      onLost: () => setState({ status: 'error', message: '系列资料或访问范围已变化，请重新读取。' }) })
    return () => monitor.dispose()
  }, [client, scope, session, state.status, data?.context])
  if (state.status === 'disabled') return null
  return <section className={`series-summary${compact ? ' series-summary--compact' : ''}`} aria-label="系列与关联">
    <h3><span aria-hidden="true">⌘</span> 系列与关联</h3>
    {state.status === 'loading' ? <p role="status">正在读取关联资料…</p> : state.status === 'unlinked'
      ? <><p>当前范围内没有已确认的作品身份，暂不能读取关联。旧资料可在个性化推荐的旧记录核对中确认身份。</p>
        {!adult && recordId && <button type="button" className="series-link" onClick={() => setAdult(true)}>以包含已审核 NSFW 的范围重新读取（仅本次）</button>}</>
      : state.status === 'error' ? <p role="status">{state.message} <button type="button" className="series-link" onClick={() => retry(n => n + 1)}>重试</button></p>
        : <><p>{COVERAGE[state.data.coverage]}{state.data.last_attempt_status === 'failed' && state.data.coverage !== 'failed' ? ' · 最近更新失败，保留上次资料' : ''}</p>
          {state.data.results.length > 0 && (compact ? <p>{[...new Set(state.data.results.map(row => row.relation_label))].slice(0, 3).join(' · ')}</p>
            : <ul>{state.data.results.slice(0, 3).map(row => <li key={`${row.bangumi_id}:${row.kind}`}><span>{row.relation_label}</span> {row.title_zh || row.title_jp}</li>)}</ul>)}
          <p className="series-caption">关系相对于本作；没有列出关联不代表独立作品。</p>
          <button type="button" className="rec-button" onClick={() => navigate(`/recommendations/series/${state.data.selected_seed.bangumi_id}`, {
            state: { seriesBackground: seriesBackground(location), seriesScope: scope },
          })}>查看全部关联 <span aria-hidden="true">↗</span></button></>}
    {adult && <p>本次包含已审核 NSFW 关联。 <button type="button" className="series-link" onClick={() => setAdult(false)}>恢复默认范围</button></p>}
  </section>
}
