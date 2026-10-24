import useExplorationInteractions from '../hooks/useExplorationInteractions.jsx'
import ExplorationDialogs from '../components/recommendations/ExplorationDialogs.jsx'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import useExploration from '../hooks/useExploration.js'
import useRecommendationMotion from '../hooks/useRecommendationMotion.js'
import { createExplorationSearchController, parseSimilarSeed } from '../utils/recommendationExploration.js'
import { similarQuery } from '../utils/explorationResults.js'
import { DEFAULT_BROWSE_FILTERS, filterChips } from '../utils/recommendationBrowse.js'
import RecommendationCard from '../components/recommendations/RecommendationCard.jsx'
import { RecommendationFilterPanel, RecommendationBatchActions } from '../components/recommendations/RecommendationBrowseControls.jsx'
import { RecommendationSkeleton } from '../components/recommendations/RecommendationStates.jsx'
import { RecommendationGridSkeleton } from '../components/recommendations/RecommendationLoading.jsx'
import ShowcasePublicationPanel from '../components/ShowcasePublicationPanel.jsx'
import BackToTop from '../components/BackToTop.jsx'

export default function SimilarRecommendations({ client, sessionScope, cache, adult, setAdult, navigation, capabilities }) {
  const location = useLocation(), navigate = useNavigate(), parsed = parseSimilarSeed(location.search)
  const seedId = parsed.id, scope = adult ? 'include_adult' : 'safe'
  const [localFilters, setLocalFilters] = useState(cache.filters || { ...DEFAULT_BROWSE_FILTERS })
  const filters = { ...localFilters, includeAdult: adult }
  const [selected, setSelected] = useState(cache.selected || null)
  const [finding, setFinding] = useState(!seedId)
  const [term, setTerm] = useState('')
  const journalRecord = location.state?.journalRecord
  const referenceRequest = useRef(null), referenceLive = useRef(true)
  useLayoutEffect(() => {
    referenceLive.current = true
    return () => { referenceLive.current = false; referenceRequest.current?.abort() }
  }, [client])
  const pageRef = useRef(null), resultsRef = useRef(null), searchInput = useRef(null)
  const searchController = useMemo(() => createExplorationSearchController(client, scope), [client, scope])
  const search = useSyncExternalStore(searchController.subscribe, searchController.getSnapshot, searchController.getSnapshot)
  useEffect(() => { searchController.activate(); return () => searchController.dispose() }, [searchController])
  useEffect(() => { if (finding) searchInput.current?.focus({ preventScroll: true }) }, [finding])
  const seed = selected?.scope === scope && selected.item.bangumiId === seedId ? selected.item : null
  const query = seed?.canSeed ? similarQuery(seedId, filters) : null
  const state = useExploration(client, query, scope, cache)
  const interactions = useExplorationInteractions(client, sessionScope, state, scope)
  const { panel, setPanel, notice, setNotice, publicationRevision, detailController, close, reload, feedback, openHidden, cardProps, toast } = interactions
  const resolveReference = async () => {
    referenceRequest.current?.abort()
    const request = new AbortController(); referenceRequest.current = request
    try {
      const data = await client.explorationRecord(journalRecord, request.signal)
      if (!referenceLive.current || request.signal.aborted || referenceRequest.current !== request) return
      if (data?.contract_version !== 'recommendations.explore.record.v1' || data.record_id !== journalRecord
        || !['linked', 'unlinked', 'identity_conflict', 'stale_link', 'not_in_catalog', 'unsafe_or_unknown'].includes(data.identity_state)
        || (data.identity_state === 'linked' ? !Number.isInteger(data.bangumi_id) || data.bangumi_id < 1 || data.bangumi_id > 2147483647 : data.bangumi_id !== null)) throw new Error()
      if (data.identity_state === 'linked') { close(); navigate(`/recommendations/similar?seed=${data.bangumi_id}`, { replace: true, state: null }) }
      else if (data.identity_state === 'unsafe_or_unknown') setNotice('此作品不在当前内容范围，请通过筛选检查 NSFW 开关；未知分级作品暂不可探索。')
      else { detailController.close(); setPanel({ kind: 'identity', recordId: journalRecord }) }
    } catch { if (referenceLive.current && !request.signal.aborted && referenceRequest.current === request) setNotice('手账作品身份读取失败，请重新从手账进入，或按 Bangumi ID 搜索。') }
  }
  useEffect(() => {
    if (!Number.isSafeInteger(journalRecord) || journalRecord < 1) return
    resolveReference()
    return () => referenceRequest.current?.abort()
  }, [client, journalRecord])
  useLayoutEffect(() => { cache.filters = localFilters; cache.selected = selected; cache.seedId = seedId }, [cache, localFilters, selected, seedId])
  useEffect(() => {
    if (!seedId || seed) return
    searchController.search(String(seedId))
  }, [seedId, scope, searchController])
  useEffect(() => {
    if (seedId && !seed && search.query?.kind === 'bangumi_id' && search.query.value === String(seedId) && search.status === 'ready') {
      setSelected({ item: search.items[0], scope }); setFinding(false)
    }
  }, [search, seedId, seed, scope])
  useEffect(() => {
    const previous = document.title; document.title = '单番相似 · 番剧探索手账'
    return () => { document.title = previous }
  }, [])
  useRecommendationMotion(pageRef, { status: state.status, epoch: JSON.stringify([state.key, state.data?.snapshot_id, state.data?.batch]),
    ids: state.items.map(item => item.bangumiId).join(','), blocked: state.snapshotDirty })
  const choose = item => {
    if (!item.canSeed) return
    close(); searchController.clear(); setSelected({ item, scope }); setFinding(false)
    navigate(`/recommendations/similar?seed=${item.bangumiId}`)
  }
  const clearSeed = () => {
    close(); searchController.clear(); setSelected(null); setFinding(true); setTerm('')
    navigate('/recommendations/similar'); searchInput.current?.focus()
  }
  const applyFilters = next => { close(); setLocalFilters(next); setAdult(!!next.includeAdult) }
  const openFilters = () => { close(); setPanel({ kind: 'filters' }) }
  const chips = filterChips(filters)
  const remove = id => applyFilters(id === 'years' ? { ...filters, yearFrom: '', yearTo: '' }
    : { ...filters, [id]: id === 'hideRecorded' ? false : DEFAULT_BROWSE_FILTERS[id] })
  const nextBatch = async () => {
    close()
    if (await state.nextBatch()) { resultsRef.current?.focus({ preventScroll: true }); resultsRef.current?.scrollIntoView({ block: 'start', behavior: 'auto' }) }
  }
  const selectedSeed = state.data?.selectedSeed || seed

  return <main ref={pageRef} className="memphis-rec-container">
    <a className="rec-skip-link" href="#similar-results">跳到相似作品</a>
    <div className="rec-page"><div className="rec-masthead">
      <nav className="rec-nav" aria-label="探索页面导航"><Link className="rec-back" to="/dashboard">← 返回我的手账</Link>
        <div className="rec-nav-tools"><button type="button" className="rec-text-button" onClick={openHidden}>已隐藏作品</button></div></nav>
      <header className="rec-page-heading rec-explore-heading"><span className="rec-chapter-label">我的手账</span><h1>番剧探索</h1>{navigation}</header>
      <section className="rec-controls rec-seed-controls" aria-label="选择参照作品">
        <div className="rec-seed-selection"><strong>单番相似</strong>{seed ? <><span>参照：《{seed.title}》</span>
          <button type="button" className="rec-text-button" onClick={() => { setFinding(true); searchInput.current?.focus() }}>更换作品</button>
          <button type="button" className="rec-text-button" onClick={clearSeed}>清除</button></> : <span>先选择一部作品，寻找共有题材的番剧。</span>}</div>
        <button type="button" className="rec-button rec-filter-button" onClick={openFilters}>☷ 筛选 {chips.length > 0 && <span className="rec-filter-count">{chips.length}</span>}</button>
      </section>
      <div className="rec-control-summary"><div className="rec-active-filters"><div className="rec-filter-chips">{chips.map(chip => <button type="button" key={chip.id}
        className="rec-filter-chip" aria-label={`移除条件：${chip.label}`} onClick={() => remove(chip.id)}>{chip.label} ×</button>)}</div>
        <button type="button" className="rec-text-button" onClick={() => applyFilters({ ...DEFAULT_BROWSE_FILTERS })}>恢复默认</button></div>
        <details className="rec-browse-help"><summary>浏览说明</summary><div className="rec-browse-help-body"><p>按参照作品共有题材寻找关联，无需个人评分样本。只用于本次浏览，不修改你的口味偏好。默认隐藏手账已有记录；未知分级始终排除。</p></div></details></div>
      {(finding || !seed) && <section className="rec-seed-picker" aria-label="搜索参照作品">
        <form onSubmit={event => { event.preventDefault(); searchController.search(term) }}>
          <label htmlFor="rec-seed-query">作品名称 / Bangumi ID / subject 链接</label><div className="rec-seed-search-row">
            <input id="rec-seed-query" ref={searchInput} value={term} maxLength={120} placeholder="例如：作品名称或 27364" onChange={event => setTerm(event.target.value)} />
            <button type="submit" className="rec-button rec-button--primary">搜索作品</button></div>
          <small>搜索本站目录，每次最多显示 15 部；不会发起外部抓取。</small>
        </form>
        <div aria-live="polite">{parsed.error && <p role="alert">{parsed.error}</p>}
          {search.status === 'loading' && <p role="status">正在读取作品目录…</p>}
          {['error', 'unavailable'].includes(search.status) && <p role="alert">{search.message}</p>}
          {search.status === 'empty' && <p>当前范围没有找到这部作品。请更换关键词或检查筛选范围。</p>}
          {search.items.length > 0 && <ul className="rec-seed-options">{search.items.map(item => <li key={item.bangumiId}>
            <button type="button" disabled={!item.canSeed} onClick={() => choose(item)}><strong>{item.title}</strong><span>{item.originalTitle}</span>
              <small>{item.year || '年份未知'} · {item.type} · BGM {item.bangumiId}{item.contentRating === 'adult' ? ' · NSFW' : ''}</small>
              <span>{item.canSeed ? `题材：${item.tags.join('、')}` : '缺少有效题材，暂不能作为参照。'}</span></button></li>)}</ul>}
          {search.hasMore && <p>还有更多结果，请补充作品名称或使用 Bangumi ID 精确查找。</p>}
        </div>{seed && <button type="button" className="rec-text-button" onClick={() => { searchController.clear(); setFinding(false) }}>保留当前参照，收起搜索</button>}
      </section>}
    </div>
    <ShowcasePublicationPanel refreshKey={publicationRevision} />
    <section id="similar-results" ref={resultsRef} tabIndex={-1} className="rec-results" aria-label="相似作品">
      {state.snapshotDirty && <section id="rec-context-update" className="rec-feedback-refresh" role="status"><p>探索依据已更新，已保留浏览位置。重新探索后继续浏览。</p><button type="button" className="rec-button" onClick={reload}>重新探索</button></section>}
      {feedback.state.uncertain && <div className="rec-feedback-refresh" role="alert"><p>反馈结果尚未确认，请先确认原操作。</p><button type="button" className="rec-button" disabled={feedback.state.pending} onClick={feedback.controller.retry}>重试确认原操作</button></div>}
      <div className="rec-featured-layout"><div className="rec-featured-main">
        {state.status === 'loading' ? <RecommendationSkeleton featuredOnly phase="explore" /> : state.status === 'ready' ? <RecommendationCard {...cardProps(state.items[0])} hero />
          : <section className="rec-empty" role={state.status === 'error' ? 'alert' : 'status'}>
            <h2>{state.status === 'error' ? '暂时无法探索' : !seed ? '选一部作品，发现相似故事' : !seed.canSeed ? '参照作品缺少有效题材' : '当前条件没有相似作品'}</h2>
            <p>{state.error || (!seed ? '支持按名称、Bangumi ID 或 subject 链接搜索。' : !seed.canSeed ? '请更换参照作品。' : '可尝试放宽年份、类型、评分条件，或更换参照。')}</p>
            {state.status === 'error' && <button type="button" className="rec-button" onClick={seed ? reload : () => searchController.search(String(seedId))}>重试</button>}
            {seed && <button type="button" className="rec-button" onClick={openFilters}>调整筛选</button>}
          </section>}
      </div><aside className="rec-taste-note rec-reference-note" aria-label="本次参照"><h2>本次参照</h2>
        {selectedSeed ? <><strong>{selectedSeed.title}</strong><p className="rec-muted">{selectedSeed.year || '年份未知'} · {selectedSeed.type} · BGM {selectedSeed.bangumiId}</p>
          <div className="rec-evidence-tags">{selectedSeed.tags.map(tag => <span key={tag}>{tag}</span>)}</div>
          <p>根据共有题材找关联，不使用个人评分，不代表喜欢概率。</p><button type="button" className="rec-text-button" onClick={() => { setFinding(true); searchInput.current?.focus(); pageRef.current?.scrollIntoView() }}>更换参照 →</button></>
          : <p>选定作品后，这里会显示参照信息与有效题材。</p>}</aside></div>
      {state.status === 'loading' && <RecommendationGridSkeleton />}
      {state.status === 'ready' && <>{state.items.length > 1 && <section className="rec-more-section" aria-labelledby="similar-more-title">
        <div className="rec-more-heading"><h2 id="similar-more-title">还有更多</h2><span className="rec-result-count">本轮 {state.data.returned_count} 部 / 符合条件 {state.data.eligible_count} 部</span></div>
        <div className="rec-grid">{state.items.slice(1).map(item => <RecommendationCard key={item.id} {...cardProps(item)} />)}</div></section>}
        <RecommendationBatchActions state={state} onMore={state.loadMore} onBatch={nextBatch} onReload={reload} onFilter={openFilters} onRestart={reload} /></>}
    </section><footer className="rec-footer">我的手账 / 单番相似</footer></div>
    <div className="rec-toast" role="status" aria-live="polite">{notice && <p>{notice}</p>}</div>{panel?.kind !== 'hidden' && toast}
    <ExplorationDialogs interactions={interactions} client={client} capabilities={capabilities}
      onSimilar={item => { close(); setSelected(null); navigate(`/recommendations/similar?seed=${item.bangumiId}`) }}
      onIdentityChanged={() => { if (journalRecord) resolveReference() }}
      editor={<RecommendationFilterPanel initialFilters={filters} onApply={applyFilters} client={client} />} />
    <BackToTop isModalOpen={Boolean(panel)} theme="memphis" />
  </main>
}
