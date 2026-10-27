import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import gsap from 'gsap'
import defaultClient from '../../api/recommendations.js'
import useSeriesSession, { seriesSession } from '../../hooks/useSeriesSession.js'
import { CREDIT_COVERAGE, creditsAvailable, creditsFailure, creditsNavigationState,
  validateCreditsCapabilities, validateCreditsRecord, validateCreditsSummary } from '../../utils/recommendationCredits.js'
import { CreditsIcon } from './CreditsEntry.jsx'
import { watchRecommendationScope } from '../../utils/recommendationScopeMonitor.js'
import '../../styles/recommendation-credits.css'

export default function CreditsSummary(props) {
  const session = useSeriesSession()
  return <Summary key={`${session}:${props.recordId || props.bangumiId}`} {...props} session={session} />
}
function Summary({ client: base = defaultClient, recordId, bangumiId, session }) {
  const [adult, setAdult] = useState(false), [expanded, setExpanded] = useState(false), [attempt, retry] = useState(0)
  const [state, setState] = useState({ status: 'loading' }), [full, setFull] = useState(null)
  const root = useRef(null), navigate = useNavigate(), location = useLocation()
  const client = useMemo(() => recordId && adult ? base.forScope('include_adult') : base, [base, recordId, adult])
  const scope = client.contentScope || 'safe'
  useEffect(() => {
    const request = new AbortController(), current = () => !request.signal.aborted && seriesSession() === session
    setState({ status: 'loading' }); setFull(null); setExpanded(false)
    if (!client.creditsSummary || !client.capabilities) { setState({ status: 'disabled' }); return () => request.abort() }
    ;(async () => {
      const capability = validateCreditsCapabilities((await client.capabilities(request.signal)).credits)
      if (!creditsAvailable(capability)) { if (current()) setState({ status: 'disabled' }); return }
      let identity = bangumiId
      if (recordId) {
        const ref = validateCreditsRecord(await client.creditsRecord(recordId, request.signal), recordId, scope)
        if (ref.identity_state !== 'linked') { if (current()) setState({ status: 'unlinked', unsafe: ref.identity_state === 'unsafe_or_unknown' }); return }
        identity = ref.bangumi_id
      }
      const data = validateCreditsSummary(await client.creditsSummary(identity, false, request.signal), identity, scope)
      if (current()) setState({ status: 'ready', data })
    })().catch(error => { if (current()) setState({ status: error?.response?.data?.code === 'credits_disabled' ? 'disabled' : 'error', message: creditsFailure(error) }) })
    return () => request.abort()
  }, [client, scope, recordId, bangumiId, session, attempt])
  useEffect(() => {
    if (!expanded || !state.data || full?.data) return
    const request = new AbortController(), identity = state.data.bangumi_id
    setFull({ status: 'loading' })
    client.creditsSummary(identity, true, request.signal).then(raw => {
      const data = validateCreditsSummary(raw, identity, scope)
      if (!request.signal.aborted && seriesSession() === session) {
        if (data.credits_revision !== state.data.credits_revision || data.content_scope_context.scope_revision !== state.data.content_scope_context.scope_revision) {
          setState({ status: 'error', message: '参与资料已更新，请重新读取。' }); setFull(null)
        } else setFull({ status: 'ready', data })
      }
    }).catch(error => {
      if (request.signal.aborted || seriesSession() !== session) return
      if (error?.scopeLost || [401, 403, 404].includes(error?.response?.status) || error?.response?.data?.code === 'credits_disabled') {
        setState({ status: 'error', message: creditsFailure(error) }); setFull(null)
      } else setFull({ status: 'error', message: creditsFailure(error) })
    })
    return () => request.abort()
  }, [expanded, state.data, client, scope, session, full?.data])
  useEffect(() => {
    if (!state.data) return
    const lose = () => { if (seriesSession() === session) { setState({ status: 'error', message: '参与资料或访问范围已到期，请重新读取。' }); setFull(null) } }
    const timer = setTimeout(lose, 900000)
    const monitor = scope === 'include_adult' && client.context ? watchRecommendationScope({
      deadline: state.data.content_scope_context.scope_valid_until === null ? null : Date.parse(state.data.content_scope_context.scope_valid_until),
      probe: signal => client.context(signal), onContext: data => {
        if (data.content_scope_context?.scope_revision !== state.data.content_scope_context.scope_revision) lose()
      }, onLost: lose }) : null
    return () => { clearTimeout(timer); monitor?.dispose() }
  }, [client, scope, session, state.data])
  useLayoutEffect(() => {
    if (state.status !== 'ready' || !root.current || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const context = gsap.context(() => gsap.fromTo(root.current, { y: 10, opacity: .45 }, { y: 0, opacity: 1, duration: .25, clearProps: 'transform,opacity' }), root.current)
    return () => context.revert()
  }, [state.status])
  if (state.status === 'disabled') return null
  const data = state.data, section = (full?.data || data)?.sections.voice_actor
  const go = (kind, person) => navigate(`/recommendations/credits/${kind}/${person}`, { state: creditsNavigationState(location, scope) })
  return <section ref={root} className="credits-summary" aria-label="声优与制作资料">
    <h3><CreditsIcon />声优与制作</h3>
    {state.status === 'loading' ? <p role="status">正在读取参与资料…</p> : state.status === 'error'
      ? <p role="status">{state.message} <button type="button" className="series-link" onClick={() => retry(n => n + 1)}>重新读取</button></p>
      : state.status === 'unlinked' ? <><p>当前范围内尚无已确认的作品身份，不会根据私人填写的公司名建立关联。</p>
        {recordId && state.unsafe && !adult && <button type="button" className="series-link" onClick={() => setAdult(true)}>以包含已审核 NSFW 的范围读取（仅本次）</button>}</>
        : <>{data.sections.studio.status !== 'disabled' && <div className="credits-studios"><strong><CreditsIcon kind="studio" />动画制作</strong>
          {data.sections.studio.results.map(row => <button type="button" className="credits-person-link" key={row.person_id} onClick={() => go('studio', row.person_id)}>{row.name} ↗</button>)}
          {!data.sections.studio.results.length && <span>{CREDIT_COVERAGE[data.sections.studio.status]}，不代表作品没有制作公司。</span>}
          {data.sections.studio.has_more && <span>当前仅展示前 {data.sections.studio.limit} 项。</span>}</div>}
          {section.status !== 'disabled' && <><button type="button" className="credits-cast-toggle" aria-expanded={expanded} onClick={() => setExpanded(v => !v)}>
            <span>声优与角色{section.count > 0 ? ` · ${section.count} 位` : ''}</span><span aria-hidden="true">{expanded ? '−' : '＋'}</span></button>
          {expanded && <div className="credits-cast-list">{full?.status === 'loading' ? <p role="status">正在读取声优清单…</p>
            : full?.status === 'error' ? <p role="status">{full.message} <button className="series-link" onClick={() => { setExpanded(false); setFull(null) }}>收起后重试</button></p>
              : <><p>{CREDIT_COVERAGE[section.status]}。仅展示当前来源确认的原声 CV，不代表完整演员表。</p>
                {section.last_attempt_status !== section.status && ['partial', 'failed'].includes(section.last_attempt_status) && <p>最近更新不完整，保留上次可用资料。</p>}
                <ul>{section.results.map(row => <li key={row.person_id}><button className="credits-person-link" onClick={() => go('voice_actor', row.person_id)}>{row.name} ↗</button>
                  <span>饰演：{row.evidence.characters.map(c => c.name).join('、')}</span></li>)}</ul>
                {section.has_more && <p>当前展示前 {section.limit} 项，清单仍有截断。</p>}
              </>}</div>}</>}
          <small>标准参与资料独立于私人填写内容；没有列出参与关系不代表没有相关人员。</small></>}
    {adult && <button type="button" className="series-link" onClick={() => setAdult(false)}>恢复默认内容范围</button>}
  </section>
}
