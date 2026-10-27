import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import defaultClient from '../api/recommendations.js'
import useSeriesSession, { seriesSession } from '../hooks/useSeriesSession.js'
import useCreditsMotion from '../hooks/useCreditsMotion.js'
import { CREDIT_KINDS, CREDIT_DEFAULTS, creditsAvailable, creditsFailure, creditsRoute, creditItem,
  creditsNavigationState, validateCreditsCapabilities } from '../utils/recommendationCredits.js'
import { createCreditsWorkspace } from '../utils/creditsWorkspace.js'
import { watchRecommendationScope } from '../utils/recommendationScopeMonitor.js'
import RecommendationDialog from '../components/recommendations/RecommendationDialog.jsx'
import RecommendationContentScopeToggle from '../components/recommendations/RecommendationContentScopeToggle.jsx'
import { RecommendationCover, RecommendationMeta } from '../components/recommendations/RecommendationCard.jsx'
import { CreditsIcon } from '../components/recommendations/CreditsEntry.jsx'
import '../styles/recommendations.css'
import '../styles/recommendations-layout.css'
import '../styles/recommendations-theme.css'
import '../styles/recommendation-credits.css'

export default function CreditsExplorer(props) {
  const session = useSeriesSession()
  return <Explorer key={session} {...props} session={session} />
}
function Explorer({ client: base = defaultClient, overlay = false, session }) {
  const location = useLocation(), navigate = useNavigate(), route = creditsRoute(location.pathname)
  const [adult, setAdult] = useState(location.state?.creditsScope === 'include_adult')
  const [capability, setCapability] = useState({ status: 'loading' }), [attempt, retry] = useState(0)
  const [notice, setNotice] = useState(''), [phase, nextPhase] = useState(0), [generation, purge] = useState(0)
  const caches = useRef({}), root = useRef(null), previousCapability = useRef(null)
  const scope = adult ? 'include_adult' : 'safe'
  const lost = () => { caches.current = {}; purge(n => n + 1); setAdult(false); setNotice('访问范围已变化，已停止展示旧资料。请重新读取。') }
  const client = useMemo(() => base.forScope ? base.forScope(scope, lost) : base, [base, scope])
  useEffect(() => {
    const request = new AbortController(); let last = 0
    const read = async (force = false) => {
      if (!force && Date.now() - last < 60000) return
      last = Date.now()
      try {
        const data = validateCreditsCapabilities((await base.capabilities(request.signal)).credits)
        if (!request.signal.aborted && seriesSession() === session) {
          if (previousCapability.current && Object.keys(CREDIT_KINDS).some(k => previousCapability.current[k] !== data[k])) {
            caches.current = {}; purge(n => n + 1)
          }
          previousCapability.current = data; setCapability({ status: 'ready', data })
        }
      } catch (error) {
        if (request.signal.aborted || seriesSession() !== session) return
        if (error?.scopeLost || [401, 403].includes(error?.response?.status)) {
          lost(); setCapability({ status: 'error', message: creditsFailure(error) })
        } else setCapability(old => old.data ? old : { status: 'error', message: creditsFailure(error) })
      }
    }
    read(true)
    const focus = () => { if (document.visibilityState !== 'hidden') read() }
    window.addEventListener('focus', focus); document.addEventListener('visibilitychange', focus)
    return () => { request.abort(); window.removeEventListener('focus', focus); document.removeEventListener('visibilitychange', focus) }
  }, [base, session, attempt])
  useEffect(() => {
    const mark = value => {
      if (value.state) value.state = { ...value.state, stale: true }
      for (const kind of Object.keys(CREDIT_KINDS)) if (value[kind]) mark(value[kind])
    }
    const changed = () => Object.values(caches.current).forEach(mark)
    window.addEventListener('recommendation-data-changed', changed)
    return () => window.removeEventListener('recommendation-data-changed', changed)
  }, [])
  useEffect(() => {
    const previous = document.title; document.title = '人物与制作 · 番剧探索'
    return () => { document.title = previous }
  }, [])
  const key = `${scope}:${route?.kind || 'search'}:${route?.person || ''}`
  const cache = caches.current[key] ||= {}
  useLayoutEffect(() => {
    const node = root.current?.closest('dialog')?.querySelector('.rec-dialog-body')
    const frame = requestAnimationFrame(() => (node || window).scrollTo({ top: cache.scroll || 0, behavior: 'instant' }))
    return () => { cancelAnimationFrame(frame); cache.scroll = node ? node.scrollTop : window.scrollY }
  }, [key, cache, capability.status, overlay])
  const available = capability.status === 'ready' && creditsAvailable(capability.data) && (!route?.kind || capability.data[route.kind])
  useCreditsMotion(root, route?.person ? 'entity' : 'search', capability.status === 'ready', `${key}:${phase}`)
  const hasPrevious = overlay || typeof location.state?.creditsFrom === 'string' && creditsRoute(location.state.creditsFrom)
  const back = () => hasPrevious ? navigate(-1) : navigate('/recommendations')
  const choose = (kind, person) => navigate(`/recommendations/credits/${kind}/${person}`, { state: creditsNavigationState(location, scope) })
  const body = <main ref={root} className="credits-page rec-page">
    <header className="rec-masthead credits-heading"><nav className="rec-nav"><button className="rec-button" onClick={back}>← {hasPrevious ? '返回上一页' : '返回番剧探索'}</button>
      {route?.person && <Link className="rec-button" to="/recommendations/credits" state={creditsNavigationState(location, scope)}>更换人物 / 公司</Link>}</nav>
      <div className="rec-page-heading"><span className="rec-chapter-label">我的手账 / 番剧探索</span><h1>人物与制作</h1></div>
      <p>循着熟悉的声音与制作，找到下一部想了解的作品。</p></header>
    {notice && <p className="credits-notice" role="status">{notice}</p>}
    {!route ? <section className="credits-panel"><h2>人物路径无效</h2><Link className="rec-button" to="/recommendations/credits">返回人物与制作</Link></section>
      : !available ? <section className="credits-panel" role="status"><h2>{capability.status === 'loading' ? '正在读取服务状态…' : capability.status === 'error' ? '服务状态读取失败' : '人物与制作尚未开放'}</h2>
        <p>{capability.message || '原有推荐和手账仍可正常使用。'}</p><button className="rec-button" onClick={() => retry(n => n + 1)}>重新读取服务状态</button></section>
        : <><ScopeControl client={client} adult={adult} onApply={value => { setNotice(''); setAdult(value) }} />
          {route.person ? <EntityWorks key={`${key}:${generation}`} client={client} kind={route.kind} person={route.person}
            scope={scope} session={session} cache={cache} onReady={() => nextPhase(n => n + 1)} />
            : <CreditsSearch key={`${key}:${generation}`} client={client} flags={capability.data} scope={scope} session={session}
              cache={cache} onChoose={choose} onReady={() => nextPhase(n => n + 1)} />}</>}
    <footer className="rec-footer">我的手账 / 番剧探索 / 人物与制作</footer>
  </main>
  return <div className={`memphis-rec-container credits-shell${overlay ? ' credits-shell--overlay' : ''}`}>
    {overlay ? <RecommendationDialog kind="credits" title="人物与制作" onClose={back}>{body}</RecommendationDialog> : body}
  </div>
}
function ScopeControl({ client, adult, onApply }) {
  const [draft, setDraft] = useState(adult)
  useEffect(() => setDraft(adult), [adult])
  return client.eligibility ? <details className="credits-scope"><summary>本次内容范围：{adult ? '包含已审核 NSFW' : '默认安全范围'}</summary>
    <RecommendationContentScopeToggle client={client} includeAdult={draft} onChange={setDraft} />
    <button className="rec-button" disabled={draft === adult} onClick={() => onApply(draft)}>应用内容范围</button>
  </details> : null
}
function useWorkspace({ client, kind, person, scope, session, cache }, onReady) {
  const controller = useMemo(() => createCreditsWorkspace({ client, kind, person, scope, cache,
    isCurrent: () => seriesSession() === session }), [client, kind, person, scope, session, cache])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const notify = useRef(onReady); notify.current = onReady
  useEffect(() => { controller.activate(); return () => controller.dispose() }, [controller])
  useLayoutEffect(() => { if (state.data) notify.current() }, [state.data])
  useEffect(() => {
    const changed = () => controller.invalidate(false, '手账或隐藏记录已变化，重新读取后继续浏览。')
    window.addEventListener('recommendation-data-changed', changed)
    return () => window.removeEventListener('recommendation-data-changed', changed)
  }, [controller])
  useEffect(() => {
    if (!state.data || state.stale) return
    const end = person ? state.data.expires_at * 1000 : state.loadedAt + 900000
    const timer = setTimeout(() => controller.invalidate(), Math.max(0, end - Date.now()))
    const check = () => { if (Date.now() >= end) controller.invalidate() }
    window.addEventListener('focus', check)
    let monitor
    if (scope === 'include_adult' && client.context) monitor = watchRecommendationScope({
      deadline: state.data.content_scope_context.scope_valid_until === null ? null : Date.parse(state.data.content_scope_context.scope_valid_until),
      probe: signal => client.context(signal), onContext: data => {
        if (data.content_scope_context?.scope_revision !== state.data.content_scope_context.scope_revision) controller.invalidate(true, '访问范围已变化，已停止展示旧资料。')
      }, onLost: () => controller.invalidate(true, '访问范围已变化，已停止展示旧资料。') })
    return () => { clearTimeout(timer); window.removeEventListener('focus', check); monitor?.dispose() }
  }, [controller, scope, client, person, state.data, state.stale, state.loadedAt])
  return [controller, state]
}
function CreditsSearch({ client, flags, scope, session, cache, onChoose, onReady }) {
  const [kind, setKind] = useState(cache.kind && flags[cache.kind] ? cache.kind : flags.voice_actor ? 'voice_actor' : 'studio')
  const [term, setTerm] = useState(cache.term || ''), [submitted, setSubmitted] = useState(cache.submitted || ''), [error, setError] = useState('')
  const bucket = cache[kind] ||= {}
  const [controller, state] = useWorkspace({ client, kind, scope, session, cache: bucket }, onReady)
  useLayoutEffect(() => { cache.kind = kind; cache.term = term; cache.submitted = submitted }, [cache, kind, term, submitted])
  useEffect(() => { if (bucket.state?.data) controller.ensure(bucket.state.query) }, [controller])
  const submit = event => {
    event.preventDefault()
    const query = term.trim()
    if (!query || query.length > 120 || /^\d+$/.test(query)) { setError('请输入姓名、已收录别名或完整 Bangumi 人物链接；不接受裸数字。'); return }
    setError(''); setSubmitted(query); controller.load(query)
  }
  return <><section className="credits-panel rec-featured-main">
    <div className="credits-kind-tabs" role="group" aria-label="选择检索类型">{Object.entries(CREDIT_KINDS).map(([value, label]) => <button key={value}
      className={`rec-button${kind === value ? ' rec-button--primary' : ''}`} aria-pressed={kind === value} disabled={!flags[value]}
      onClick={() => { setKind(value); setError('') }}><CreditsIcon kind={value} />找{label}{!flags[value] ? '（尚未开放）' : ''}</button>)}</div>
    <form className="credits-search-form" onSubmit={submit}><label htmlFor="credits-query">{CREDIT_KINDS[kind]}姓名或人物链接</label>
      <div><input id="credits-query" maxLength={120} autoComplete="off" value={term} onChange={event => setTerm(event.target.value)}
        placeholder={kind === 'studio' ? '制作公司名称 / Bangumi person 链接' : '声优姓名 / 别名 / Bangumi person 链接'} />
      <button className="rec-button rec-button--primary" type="submit" disabled={state.pending}>查找 <span aria-hidden="true">↗</span></button></div>
      <p>仅搜索当前范围有确认关联作品的实体。同名结果按人物 ID 区分，不会自动合并。</p>
      {error && <p role="alert">{error}</p>}</form></section>
    <section className={`credits-results${state.data ? ' rec-more-section' : ''}`} aria-label="人物检索结果" aria-busy={state.pending}>
      <h2>{state.query ? `「${state.query}」的检索结果` : '从熟悉的名字开始'}</h2>
      {state.message && <p className="credits-notice" role="status">{state.message} <button className="series-link" onClick={() => controller.load(state.query || submitted)}>重新查找</button></p>}
      {state.pending && <p role="status">正在查找{CREDIT_KINDS[kind]}…</p>}
      {state.data ? <><ul className="credits-entities">{state.data.results.map(row => <li key={row.person_id}><div><h3>{row.name}</h3>
        <p>Bangumi person {row.person_id} · {CREDIT_KINDS[kind]}</p><p>当前范围确认关联 {row.work_count} 部</p></div>
        <button className="rec-button" disabled={state.pending || state.stale} onClick={() => onChoose(kind, row.person_id)}>查看关联作品 ↗</button></li>)}</ul>
        {!state.data.results.length && <p>当前范围未找到匹配实体。可尝试其他已收录别名或完整人物链接；不会自动放宽内容范围。</p>}
        {state.data.has_more && <p>当前展示前 15 项，请输入更完整的名字或人物链接缩小范围。</p>}
        <p className="credits-caption">{state.data.coverage.detail}</p></> : state.status === 'idle' && <p>找到一个声音或一家制作公司，继续了解其参与的故事。</p>}
    </section></>
}
function EntityWorks({ client, kind, person, scope, session, cache, onReady }) {
  const [options, setOptions] = useState(cache.options || { ...CREDIT_DEFAULTS }), [draft, setDraft] = useState(cache.draft || options), [error, setError] = useState('')
  const [controller, state] = useWorkspace({ client, kind, person, scope, session, cache }, onReady)
  useEffect(() => { controller.ensure(options) }, [controller])
  useLayoutEffect(() => { cache.options = options; cache.draft = draft }, [cache, options, draft])
  const field = (key, value) => setDraft(old => ({ ...old, [key]: value }))
  const apply = event => {
    event.preventDefault()
    const next = { ...draft, year_from: draft.year_from === null || draft.year_from === '' ? null : Number(draft.year_from),
      year_to: draft.year_to === null || draft.year_to === '' ? null : Number(draft.year_to) }
    if ([next.year_from, next.year_to].some(v => v !== null && (!Number.isInteger(v) || v < 1000 || v > 9999))
      || next.year_from && next.year_to && next.year_from > next.year_to) { setError('请检查四位年份及起止范围。'); return }
    setError(''); setOptions(next); controller.load(next)
  }
  const data = state.data
  return <><section className="credits-panel rec-featured-main"><div className="credits-entity-title"><CreditsIcon kind={kind} /><div>
    <p>{CREDIT_KINDS[kind]} · Bangumi person {person}</p><h2>{data?.entity.name || `人物 ${person}`}</h2></div></div>
    {data && <p>当前内容范围确认关联 <strong>{data.entity.work_count}</strong> 部 · 本次筛选 <strong>{data.count}</strong> 部</p>}
    <form className="credits-filters" onSubmit={apply} aria-label="参与作品筛选">
      <label>排序<select value={draft.sort} onChange={event => field('sort', event.target.value)}><option value="score_desc">Bangumi 评分优先</option><option value="release_desc">放送时期：由新到旧</option><option value="release_asc">放送时期：由旧到新</option></select></label>
      <label>作品类型<select value={draft.type} onChange={event => field('type', event.target.value)}>{['all', 'TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画'].map(type => <option key={type} value={type}>{type === 'all' ? '不限类型' : type}</option>)}</select></label>
      <label>起始年份<input inputMode="numeric" maxLength={4} placeholder="不限" value={draft.year_from ?? ''} onChange={event => field('year_from', event.target.value)} /></label>
      <label>结束年份<input inputMode="numeric" maxLength={4} placeholder="不限" value={draft.year_to ?? ''} onChange={event => field('year_to', event.target.value)} /></label>
      <label className="credits-score-filter">Bangumi 评分<select value={draft.max_score_exclusive ? 'lt6' : String(draft.min_score)} onChange={event => setDraft(old => ({ ...old,
        min_score: event.target.value === 'lt6' ? 0 : Number(event.target.value), max_score: event.target.value === 'lt6' ? 6 : 10, max_score_exclusive: event.target.value === 'lt6' }))}>
        <option value="0">不限评分</option><option value="lt6">6.0 以下（不含未知评分）</option>{[6, 6.5, 7, 7.5, 8, 8.5, 9].map(score => <option value={score} key={score}>{score.toFixed(1)} 及以上</option>)}</select></label>
      <label className="rec-checkbox credits-record-filter"><input type="checkbox" checked={draft.hide_recorded} onChange={event => field('hide_recorded', event.target.checked)} />隐藏手账中已记录的作品</label>
      <div className="credits-filter-actions"><button type="button" className="rec-button" onClick={() => { setDraft({ ...CREDIT_DEFAULTS }); setError('') }}>恢复默认条件</button>
        <button type="submit" className="rec-button rec-button--primary" disabled={state.pending}>应用筛选</button></div>
      {error && <p role="alert">{error}</p>}</form><p className="credits-caption">按已知放送时期排序，未知时期排在最后；不代表观看顺序。仅筛选本次浏览，不修改你的偏好。</p></section>
    <section className={`credits-results${data ? ' rec-more-section' : ''}`} aria-label="参与作品列表" aria-busy={state.pending || state.morePending}>
      <h2>{kind === 'studio' ? '参与制作的作品' : '参与配音的作品'}</h2>
      {state.message && <p className="credits-notice" role="status">{state.message} <button className="series-link" onClick={() => controller.load(options)}>重新读取</button></p>}
      {state.pending && <p role="status">{data ? '正在按新条件读取，当前保留上次结果…' : '正在读取关联作品…'}</p>}
      {data && <><p className="credits-caption">{data.coverage.detail}</p><div className="credits-work-grid">{data.results.map(row => <article className="rec-card credits-work" key={row.bangumi_id}>
        <RecommendationCover item={creditItem(row)} /><div className="rec-card-body"><h3>{row.title_zh || row.title_jp}</h3><RecommendationMeta item={creditItem(row)} />
          <p className="credits-proof">{kind === 'studio' ? '参与职务：动画制作' : `饰演：${row.evidence.characters.slice(0, 3).map(c => c.name).join('、')}${row.evidence.characters.length > 3 ? ` 等 ${row.evidence.characters.length} 个角色` : ''}`}</p>
          {row.evidence.source_episode_note && <p>来源备注：{row.evidence.source_episode_note}</p>}
          {kind === 'voice_actor' && (row.evidence.characters.length > 3 || row.evidence.characters.some(c => c.source_episode_note)) && <details><summary>完整角色与出演备注</summary>{row.evidence.characters.map(c => <p key={c.character_id}>{c.name}{c.source_episode_note ? `：${c.source_episode_note}` : ''}</p>)}</details>}
          <p className="credits-caption">放送时期：{row.release_period || '未知'} · Bangumi {row.bangumi_id}</p>
        </div></article>)}</div>
        {!data.results.length && <div className="credits-panel"><h3>当前条件没有可展示的作品</h3><p>可调整年份、类型、评分或隐藏已有记录选项；条件不会自动放宽。</p></div>}
        <div className="credits-pagination"><p role="status">已展示 {data.results.length} / {data.count} 部</p>
          {data.next_cursor && <button className="rec-button rec-button--primary" disabled={state.pending || state.morePending || state.stale} onClick={controller.more}>{state.morePending ? '正在加载…' : '加载更多关联作品'}</button>}</div>
      </>}
    </section></>
}
