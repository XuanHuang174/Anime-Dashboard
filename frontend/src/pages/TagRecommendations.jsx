import { clearScore } from '../utils/browseScore.js'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import useExploration from '../hooks/useExploration.js'
import useExplorationInteractions from '../hooks/useExplorationInteractions.jsx'
import useRecommendationMotion from '../hooks/useRecommendationMotion.js'
import { validateExplorationOptions } from '../utils/recommendationExploration.js'
import { emptyTagDraft, parseTagQuery, tagDraftFromQuery, tagQuery, tagQueryPath } from '../utils/explorationTagQuery.js'
import { DEFAULT_BROWSE_FILTERS, filterChips } from '../utils/recommendationBrowse.js'
import RecommendationCard from '../components/recommendations/RecommendationCard.jsx'
import ExplorationDialogs from '../components/recommendations/ExplorationDialogs.jsx'
import ExplorationTagEditor from '../components/recommendations/ExplorationTagEditor.jsx'
import { RecommendationBatchActions } from '../components/recommendations/RecommendationBrowseControls.jsx'
import { RecommendationSkeleton } from '../components/recommendations/RecommendationStates.jsx'
import { RecommendationGridSkeleton } from '../components/recommendations/RecommendationLoading.jsx'
import ShowcasePublicationPanel from '../components/ShowcasePublicationPanel.jsx'
import BackToTop from '../components/BackToTop.jsx'
import { SeriesAnnotationsProvider } from '../components/recommendations/SeriesExploration.jsx'

export default function TagRecommendations({ client, sessionScope, cache, adult, setAdult, navigation, capabilities, creditsEntry }) {
  const location = useLocation(), navigate = useNavigate(), scope = adult ? 'include_adult' : 'safe'
  const [options, setOptions] = useState(cache.options?.client === client ? cache.options : { status: 'loading', client })
  const [attempt, setAttempt] = useState(0), [linkError, setLinkError] = useState('')
  const [draft, setDraft] = useState(cache.draft || emptyTagDraft())
  const [request, setRequest] = useState(cache.request || null)
  const pageRef = useRef(null), resultsRef = useRef(null)
  const currentOptions = options.client === client ? options.data : null
  useEffect(() => {
    const operation = new AbortController()
    if (options.client !== client || !options.data) setOptions({ status: 'loading', client })
    client.explorationOptions(operation.signal).then(raw => {
      const data = validateExplorationOptions(raw, scope)
      if (!operation.signal.aborted) setOptions({ status: 'ready', client, data })
    }).catch(() => { if (!operation.signal.aborted) setOptions({ status: 'error', client }) })
    return () => operation.abort()
  }, [client, scope, attempt])
  useEffect(() => {
    if (!currentOptions || request?.search === location.search) return
    const parsed = parseTagQuery(location.search, currentOptions.tags.map(row => row.tag))
    setLinkError(parsed.error || ''); setDraft(parsed.draft); setRequest({ search: location.search, query: parsed.query })
  }, [location.search, currentOptions])
  const query = request?.search === location.search ? request.query : null
  const state = useExploration(client, query, scope, cache)
  const interactions = useExplorationInteractions(client, sessionScope, state, scope)
  const { panel, setPanel, notice, setNotice, publicationRevision, close, reload, feedback, openHidden, cardProps, toast } = interactions
  useLayoutEffect(() => {
    cache.draft = draft; cache.request = request; cache.options = options
    cache.path = `/recommendations/tags${location.search}`
  }, [cache, draft, request, options, location.search])
  useEffect(() => { const previous = document.title; document.title = '按标签找 · 番剧探索手账'; return () => { document.title = previous } }, [])
  useRecommendationMotion(pageRef, { status: state.status, epoch: JSON.stringify([state.key, state.data?.snapshot_id, state.data?.batch]),
    ids: state.items.map(item => item.bangumiId).join(','), blocked: state.snapshotDirty })
  const openEditor = () => { close(); setPanel({ kind: 'filters' }) }
  const apply = (next, nextAdult = adult) => {
    try {
      const nextQuery = tagQuery(next, currentOptions?.tags.map(row => row.tag) || [])
      const path = tagQueryPath(nextQuery), search = path.slice(path.indexOf('?'))
      close(); setDraft(next); setLinkError(''); setRequest({ search, query: nextQuery }); setAdult(nextAdult)
      if (location.search === search && adult === nextAdult) state.retry()
      else navigate(path)
    } catch (error) { setNotice(error.message) }
  }
  const reset = () => { close(); setDraft(emptyTagDraft()); setRequest({ search: '', query: null }); setLinkError(''); navigate('/recommendations/tags') }
  // A failed/pending query never receives an "applied" summary.
  const applied = ['ready', 'empty'].includes(state.status) ? state.data?.query : null
  const appliedDraft = applied ? tagDraftFromQuery(applied) : null
  const chips = appliedDraft ? filterChips({ ...appliedDraft.filters, includeAdult: adult }) : []
  const editWithout = (group, tag) => {
    const next = appliedDraft || draft
    setDraft({ ...next, [group]: next[group].filter(value => value !== tag) }); openEditor()
  }
  const editFilter = id => {
    const next = appliedDraft || draft
    setDraft({ ...next, filters: id === 'minScore' ? clearScore(next.filters) : id === 'years' ? { ...next.filters, yearFrom: '', yearTo: '' }
      : { ...next.filters, [id]: id === 'hideRecorded' || id === 'includeAdult' ? false : DEFAULT_BROWSE_FILTERS[id] } }); openEditor()
  }
  const nextBatch = async () => { close(); if (await state.nextBatch()) { resultsRef.current?.focus({ preventScroll: true }); resultsRef.current?.scrollIntoView({ block: 'start', behavior: 'auto' }) } }
  const editor = <ExplorationTagEditor draft={draft} onChange={setDraft} options={currentOptions}
    status={options.client === client ? options.status : 'loading'} onReload={() => setAttempt(value => value + 1)} onApply={apply} client={client} adult={adult} />
  return <SeriesAnnotationsProvider client={client} items={state.items} enabled={capabilities?.series_exploration?.enabled}
    blocked={!!state.snapshotDirty} catalogRevision={state.data?.evidence_context.catalog_feature_version}><main ref={pageRef} className="memphis-rec-container">
    <a className="rec-skip-link" href="#tag-results">跳到题材探索结果</a><div className="rec-page"><div className="rec-masthead">
      <nav className="rec-nav" aria-label="探索页面导航"><Link className="rec-back" to="/dashboard">← 返回我的手账</Link><div className="rec-nav-tools">{creditsEntry}<button type="button" className="rec-text-button" onClick={openHidden}>已隐藏作品</button></div></nav>
      <header className="rec-page-heading rec-explore-heading"><span className="rec-chapter-label">我的手账</span><h1>番剧探索</h1>{navigation}</header>
      <section className="rec-controls rec-seed-controls rec-tag-controls" aria-label="按标签找条件"><div className="rec-seed-selection"><strong>按标签找</strong><span>{applied ? `${applied.match === 'all' ? '同时满足全部' : '满足任一'} · ${applied.sort === 'score' ? 'Bangumi 评分优先' : '题材关联优先'}` : '选定题材组合，寻找这次想看的故事。'}</span></div>
        <button type="button" className="rec-button rec-button--primary" onClick={openEditor}>选择题材 / 调整条件</button></section>
      <div className="rec-control-summary"><div className="rec-active-filters" aria-label="当前生效条件"><div className="rec-filter-chips">
        {applied ? <>{['include', 'exclude'].map(group => appliedDraft[group].map(tag => <button type="button" key={`${group}:${tag}`} className="rec-filter-chip" onClick={() => editWithout(group, tag)} aria-label={`编辑移除${group === 'include' ? '包含' : '排除'}题材：${tag}`}>{group === 'include' ? '＋' : '−'} {tag} ×</button>))}
          {chips.map(chip => <button type="button" key={chip.id} className="rec-filter-chip" aria-label={`编辑移除条件：${chip.label}`} onClick={() => editFilter(chip.id)}>{chip.label} ×</button>)}</> : <span>尚无生效条件</span>}</div><button type="button" className="rec-text-button" onClick={reset}>重置本次条件</button></div>
        <details className="rec-browse-help"><summary>探索说明</summary><div className="rec-browse-help-body"><p>包含 1–5 个标准题材，排除 0–5 个。ALL 同时满足全部，ANY 满足任一；排除始终生效。不读取个人评分，不修改长期偏好。</p><p>按目录已有标注筛选，不能保证作品绝无相关内容。默认隐藏手账已有记录，未知分级始终排除。</p></div></details></div>
    </div><ShowcasePublicationPanel refreshKey={publicationRevision} />
    <section id="tag-results" ref={resultsRef} tabIndex={-1} className="rec-results" aria-label="题材探索结果">
      {state.snapshotDirty && <section id="rec-context-update" className="rec-feedback-refresh" role="status"><p>探索依据已更新，浏览位置仍保留。重新探索后继续浏览。</p><button type="button" className="rec-button" onClick={reload}>重新探索</button></section>}
      {feedback.state.uncertain && <div className="rec-feedback-refresh" role="alert"><p>反馈结果尚未确认，请先确认原操作。</p><button type="button" className="rec-button" disabled={feedback.state.pending} onClick={feedback.controller.retry}>重试确认原操作</button></div>}
      <div className="rec-featured-layout"><div className="rec-featured-main">{state.status === 'loading' ? <RecommendationSkeleton featuredOnly phase="tags" />
        : state.status === 'ready' ? <RecommendationCard {...cardProps(state.items[0])} hero /> : <section className="rec-empty" role={state.status === 'error' || linkError ? 'alert' : 'status'}>
          <h2>{state.status === 'error' ? '暂时无法探索' : state.status === 'empty' ? '当前条件没有匹配作品' : '从一个题材开始，发现更多故事'}</h2>
          <p>{linkError || state.error || (state.status === 'empty' ? '可以主动调整匹配规则、排除题材或普通筛选。条件不会自动放宽。' : '无需个人评分样本。选择包含与排除题材，再开始探索。')}</p>
          {state.status === 'error' && <button type="button" className="rec-button" onClick={reload}>重试本次查询</button>}
          <button type="button" className="rec-button rec-button--primary" onClick={openEditor}>选择题材 / 调整条件</button>
          {state.status === 'empty' && appliedDraft?.match === 'all' && appliedDraft.include.length > 1 && <button type="button" className="rec-button" onClick={() => apply({ ...appliedDraft, match: 'any' })}>改为满足任一，再探索</button>}
          {state.status === 'empty' && appliedDraft?.exclude.length > 0 && <button type="button" className="rec-button" onClick={() => { setDraft({ ...appliedDraft, exclude: [] }); openEditor() }}>编辑草稿：清除排除题材</button>}
        </section>}</div>
        <aside className="rec-taste-note rec-reference-note" aria-label="探索条件"><h2>探索条件</h2>{appliedDraft ? <>
          <p><strong>包含：</strong>{appliedDraft.include.join('、')}</p><p><strong>排除：</strong>{appliedDraft.exclude.join('、') || '无'}</p>
          <p>{appliedDraft.match === 'all' ? '同时满足全部（ALL）' : '满足任一（ANY）'}<br />{appliedDraft.sort === 'score' ? 'Bangumi 评分优先' : '题材关联优先'}</p>
          <p>{state.snapshotDirty ? '依据已变化，请重新探索。' : `符合条件 ${state.data.eligible_count} 部 · 本轮最多展示 ${state.data.returned_count} 部`}</p>
        </> : <p>{state.status === 'loading' ? '正在执行查询，成功后显示生效条件。' : '选择题材后，成功执行的条件会显示在这里。'}</p>}
          <p>只影响本次浏览，不修改你的口味偏好。</p><button type="button" className="rec-text-button" onClick={openEditor}>编辑探索草稿 →</button>
        </aside></div>
      {state.status === 'loading' && <RecommendationGridSkeleton />}
      {state.status === 'ready' && <>{state.items.length > 1 && <section className="rec-more-section" aria-labelledby="tag-more-title"><div className="rec-more-heading"><h2 id="tag-more-title">还有更多</h2><span className="rec-result-count">本轮 {state.data.returned_count} 部 / 符合条件 {state.data.eligible_count} 部</span></div><div className="rec-grid">{state.items.slice(1).map(item => <RecommendationCard key={item.id} {...cardProps(item)} />)}</div></section>}
        <RecommendationBatchActions state={state} onMore={state.loadMore} onBatch={nextBatch} onReload={reload} onFilter={openEditor} onRestart={reload} /></>}
    </section><footer className="rec-footer">我的手账 / 按标签找</footer></div>
    <div className="rec-toast" role="status" aria-live="polite">{notice && <p>{notice}</p>}</div>{panel?.kind !== 'hidden' && toast}
    <ExplorationDialogs interactions={interactions} client={client} capabilities={capabilities} editor={editor} editorTitle="选择这次想看的题材"
      onSimilar={item => { close(); navigate(`/recommendations/similar?seed=${item.bangumiId}`) }} />
    <BackToTop isModalOpen={Boolean(panel)} theme="memphis" />
  </main></SeriesAnnotationsProvider>
}
