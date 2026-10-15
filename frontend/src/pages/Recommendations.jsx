import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import RecommendationCard, { RecommendationActions, RecommendationCover, RecommendationMeta } from '../components/recommendations/RecommendationCard.jsx'
import RecommendationDialog from '../components/recommendations/RecommendationDialog.jsx'
import RecommendationEvidence from '../components/recommendations/RecommendationEvidence.jsx'
import RecommendationCategoryGuide from '../components/recommendations/RecommendationCategoryGuide.jsx'
import RecommendationIdentityReview from '../components/recommendations/RecommendationIdentityReview.jsx'
import { RecommendationTasteNote, RecommendationColdStartGuide, RecommendationRefreshNotice } from '../components/recommendations/RecommendationProfileGuide.jsx'
import { RecommendationEmptyState, RecommendationSkeleton } from '../components/recommendations/RecommendationStates.jsx'
import RecommendationPreferencesPanel from '../components/recommendations/RecommendationPreferencesPanel.jsx'
import recommendationApi from '../api/recommendations.js'
import useRecommendations from '../hooks/useRecommendations.js'
import useRecommendationWant from '../hooks/useRecommendationWant.js'
import useRecommendationPreferences from '../hooks/useRecommendationPreferences.js'
import useRecommendationReload from '../hooks/useRecommendationReload.js'
import { notifyRecommendationChange } from '../utils/recommendationReload.js'
import { recommendationAlgorithmLabel, validateDetailEvidence } from '../utils/recommendationExplanation.js'
import { createLatestRequestGate, DEFAULT_REC_FILTERS, mapRecommendationItem, recommendationError, REC_CATEGORIES, identitySummary } from '../utils/recommendations.js'
import '../styles/recommendations.css'

export default function Recommendations({ client = recommendationApi }) {
  const [category, setCategory] = useState('all')
  const [filters, setFilters] = useState({ ...DEFAULT_REC_FILTERS })
  const [draftFilters, setDraftFilters] = useState({ ...DEFAULT_REC_FILTERS })
  const [panel, setPanel] = useState(null)
  const [notice, setNotice] = useState('')
  const [detail, setDetail] = useState({ status: 'loading' })
  const [detailRevision, setDetailRevision] = useState(0)
  const detailGate = useRef(createLatestRequestGate())
  const state = useRecommendations(category, filters, client)
  const preferenceEditor = useRecommendationPreferences(client, confirmedPreferences => {
    setPanel(null); setNotice('偏好已保存，正在重新读取画像与推荐。')
    reload({ confirmedPreferences })
    notifyRecommendationChange({ local: false })
  })
  const saving = ['saving', 'reloading'].includes(preferenceEditor.editor.status)
  const preferences = state.preferences || state.confirmedPreferences
  const acknowledgeReload = useRecommendationReload(state.retry, Boolean(panel) || state.status === 'loading')
  const reload = options => { acknowledgeReload(); state.retry(options) }
  const want = useRecommendationWant(client)
  const activeFilters = Number(filters.type !== 'all') + Number(filters.minScore !== '0') + Number(filters.hideRecorded)
  const detailId = panel?.kind === 'detail' ? panel.item.bangumiId : null
  const detailEvidenceToken = panel?.kind === 'detail' ? panel.item.evidenceToken : undefined

  useEffect(() => { want.reconcile(state.items) }, [state.items, want.reconcile])
  useEffect(() => { if (detail.status === 'ready') want.reconcile([detail.item]) }, [detail, want.reconcile])

  useEffect(() => {
    const previous = document.title
    document.title = '个性化推荐 · 番剧发现手账'
    return () => { document.title = previous }
  }, [])
  useEffect(() => {
    if (!detailId) { detailGate.current.cancel(); return undefined }
    const request = detailGate.current.begin()
    setDetail({ status: 'loading' })
    client.detail(detailId, request.signal, detailEvidenceToken).then((raw) => {
      const item = validateDetailEvidence(mapRecommendationItem(raw), detailEvidenceToken)
      if (request.isCurrent()) setDetail({ status: 'ready', item })
    }).catch((error) => { if (request.isCurrent()) setDetail({ status: 'error',
      snapshotChanged: error?.response?.status === 409 || /^推荐依据合同不兼容/.test(error?.message || ''),
      message: recommendationError(error) }) })
    return () => detailGate.current.cancel()
  }, [detailId, detailEvidenceToken, detailRevision, client])
  useEffect(() => {
    if (!notice) return undefined
    const timer = window.setTimeout(() => setNotice(''), 5000)
    return () => window.clearTimeout(timer)
  }, [notice])

  const reset = () => { setFilters({ ...DEFAULT_REC_FILTERS }); setCategory('all'); reload() }
  const openPreferences = () => {
    if (preferences && preferenceEditor.open(preferences)) setPanel({ kind: 'preferences', profile: state.profile })
  }
  const closePanel = () => {
    if (panel?.kind === 'preferences' && !preferenceEditor.close()) return
    // Cancelling still performs no write; a conflict read may have learned a newer version.
    if (panel?.kind === 'preferences') reload()
    setPanel(null)
  }
  const addWant = async (item) => {
    const result = await want.add(item)
    if (!result) return
    state.recordChanged(result.bangumi_id, result.watch_status)
    reload() // Refresh common-topic observations and the authoritative recorded filter too.
    notifyRecommendationChange({ local: false })
    setNotice(result.created ? '已加入想看，评分和长评留空。' : '手账已有这部作品，已保留原有状态、评分和长评。')
  }
  const openWantIdentity = () => setPanel({ kind: 'identity', scope: 'all' })
  const cardProps = (item) => ({ item, onWant: addWant, operation: want.operations[item.bangumiId], onIdentity: openWantIdentity,
    onReload: () => reload(),
    onDetail: (chosen) => { setDetail({ status: 'loading' }); setPanel({ kind: 'detail', item: chosen }) } })
  const profile = state.profile
  const pending = profile?.diagnostics?.pending_identity_count || 0

  return <main className="memphis-rec-container">
    <a className="rec-skip-link" href="#rec-results">跳到推荐内容</a>
    <div className="rec-page">
      <nav className="rec-nav" aria-label="推荐页面导航"><Link className="rec-back" to="/dashboard">← 返回我的手账</Link>
        <button type="button" className="rec-button rec-preferences" disabled={!preferences} onClick={openPreferences}>☷ 偏好设置</button></nav>
      <header className="rec-intro">
        <div className="rec-intro-copy"><span className="rec-eyebrow">YOUR NEXT FAVORITE <span className="rec-preview-badge">{recommendationAlgorithmLabel(state.data?.algorithm_version)}</span></span>
          <h1>找到你的下一部<br /><span>心头好<span className="rec-title-star" aria-hidden="true">✦</span></span></h1>
          <p>从喜欢的故事出发，把下一次心动写进手账。</p></div>
        <RecommendationTasteNote profile={profile} preferences={preferences} status={state.status} confirmedPreferences={state.confirmedPreferences}
          onPreferences={openPreferences} onRetry={reload} />
      </header>
      <RecommendationRefreshNotice confirmedPreferences={state.confirmedPreferences} status={state.status} onRetry={() => reload()} />
      <RecommendationColdStartGuide profile={profile} status={state.status} preferencesReady={!!preferences}
        onPreferences={openPreferences} onIdentity={() => setPanel({ kind: 'identity' })} />
      {pending > 0 && !profile.cold_start && <section className="rec-identity-notice" aria-label="旧数据关联提示"><p>{identitySummary(profile)}</p>
        <button type="button" className="rec-button rec-button--primary" disabled={profile.identity_service_ready === false} onClick={() => setPanel({ kind: 'identity' })}>核对旧作品 →</button></section>}
      {profile?.identity_service_ready === false && <p className="rec-muted">关联工具尚待完成部署。旧手账记录仍完整保留，当前不会自动修改数据。</p>}
      {profile?.diagnostics?.unavailable_identity_count > 0 && <p className="rec-muted">另有 {profile.diagnostics.unavailable_identity_count} 条高分记录因未收录或分级限制未计入推荐画像。</p>}
      <section className="rec-demo-note" aria-label="推荐依据"><span className="rec-demo-dot" aria-hidden="true" />
        <p>{state.data?.mode === 'preference_based' ? '根据你选择的题材推荐，尚未形成高分作品画像。'
          : state.data?.mode === 'personalized' ? '参考高分作品，并结合你的明确偏好；只展示已有数据支持的推荐理由。'
            : '仅使用安全、已确认身份的作品和明确偏好；数据未加载时不会假装已有画像。'}
          {' '}加入想看不会覆盖已有评分、长评或观看状态。</p></section>
      <section className="rec-controls" aria-label="推荐类别与筛选">
        <div className="rec-categories" role="group" aria-label="推荐类别" aria-describedby="rec-category-description">{REC_CATEGORIES.map((option) =>
          <button type="button" key={option.id} aria-pressed={category === option.id} className={`rec-category ${category === option.id ? 'is-selected' : ''}`}
            onClick={() => setCategory(option.id)}>{option.label}</button>)}</div>
        <button type="button" className="rec-button rec-filter-button" onClick={() => { setDraftFilters({ ...filters }); setPanel({ kind: 'filters' }) }}>☷ 筛选 {activeFilters > 0 && <span className="rec-filter-count">{activeFilters}</span>}</button>
      </section>
      <RecommendationCategoryGuide category={category} mode={state.data?.mode} />
      {activeFilters > 0 && <div className="rec-active-filters"><span>筛选：{filters.type !== 'all' ? `${filters.type} · ` : ''}
        {filters.minScore !== '0' ? `评分 ≥ ${filters.minScore} · ` : ''}{filters.hideRecorded ? '隐藏手账已有记录' : ''}</span>
        <button type="button" className="rec-text-button" onClick={reset}>恢复筛选</button></div>}
      <section id="rec-results" className="rec-results" tabIndex={-1} aria-labelledby="rec-results-title">
        <div className="rec-section-heading"><div><span className="rec-eyebrow">PICK A STORY, START A JOURNEY</span>
          <h2 id="rec-results-title">下一次心动，从这里开始</h2></div>
          <span className="rec-result-count">{state.data ? `${state.data.count} 部可浏览作品` : state.status === 'error' ? '暂未加载结果' : '正在读取数据'}</span></div>
        {state.error && <p className="rec-inline-error" role="alert">{state.error}</p>}
        {state.status === 'loading' ? <RecommendationSkeleton /> : state.status === 'cold'
          ? <p className="rec-muted">完成上方口味设置后，再来翻开推荐故事。你的原有手账不会被修改。</p> : state.status !== 'ready'
          ? <RecommendationEmptyState state={state.status} onRetry={reload} onReset={reset} />
          : <><RecommendationCard {...cardProps(state.items[0])} hero />
            {state.items.length > 1 && <><div className="rec-more-heading"><h3>还有这些故事，值得翻开</h3><span>不止一种心动</span></div>
              <div className="rec-grid">{state.items.slice(1).map((item) => <RecommendationCard key={item.id} {...cardProps(item)} />)}</div></>}
            {state.moreError && <p className="rec-inline-error" role="alert">{state.moreError}</p>}
            {state.data.next_page && <div className="rec-pagination"><button type="button" className="rec-button rec-button--primary"
              disabled={state.moreLoading} onClick={state.loadMore}>{state.moreLoading ? '正在加载…' : state.snapshotDirty ? '更新推荐结果 →' : state.moreError ? '重试加载下一页' : '再翻一页 →'}</button>
              <span>已展示 {state.items.length} / {state.data.count}</span></div>}</>}
      </section>
      <footer className="rec-footer"><span>给每一次喜欢，一个值得停留的位置。</span><span>DISCOVERY JOURNAL / {recommendationAlgorithmLabel(state.data?.algorithm_version)}</span></footer>
    </div>
    <div className="rec-toast" role="status" aria-live="polite">{notice && <p>{notice}</p>}</div>
    {panel?.kind === 'identity' && <RecommendationIdentityReview client={client} scope={panel.scope || 'high-score'} onClose={() => setPanel(null)}
      onChanged={() => { want.resetFailures(); reload(); notifyRecommendationChange({ local: false }) }} />}
    {panel && panel.kind !== 'identity' && <RecommendationDialog kind={panel.kind} busy={saving} title={panel.kind === 'filters' ? '给心动留一点范围' : panel.kind === 'preferences' ? '我的口味' : panel.item.title}
      onClose={closePanel}>
      {panel.kind === 'filters' ? <form onSubmit={(event) => { event.preventDefault(); setFilters({ ...draftFilters }); setPanel(null) }}>
        <p className="rec-muted">成人及分级未知作品已由后端过滤，不会混入推荐流。</p>
        <label className="rec-field">作品类型<select value={draftFilters.type} onChange={(event) => setDraftFilters({ ...draftFilters, type: event.target.value })}>
          <option value="all">全部类型</option>{['TV', '剧场版', 'OVA', 'WEB', '短片', '动态漫画'].map((type) => <option key={type}>{type}</option>)}</select></label>
        <label className="rec-field">Bangumi 评分<select value={draftFilters.minScore} onChange={(event) => setDraftFilters({ ...draftFilters, minScore: event.target.value })}>
          <option value="0">不限评分</option><option value="8">8.0 及以上</option><option value="9">9.0 及以上</option></select></label>
        <label className="rec-checkbox"><input type="checkbox" checked={draftFilters.hideRecorded}
          onChange={(event) => setDraftFilters({ ...draftFilters, hideRecorded: event.target.checked })} />隐藏手账中已记录的作品</label>
        <div className="rec-dialog-actions"><button type="button" className="rec-button" onClick={() => setDraftFilters({ ...DEFAULT_REC_FILTERS })}>恢复默认</button>
          <button type="submit" className="rec-button rec-button--primary">应用筛选</button></div>
      </form> : panel.kind === 'preferences' ? <RecommendationPreferencesPanel editor={preferenceEditor.editor} profile={panel.profile}
        onTag={preferenceEditor.setTag} onClear={preferenceEditor.clearDraft} onSave={preferenceEditor.save}
        onReloadLatest={preferenceEditor.reloadLatest} onResolveConflict={preferenceEditor.resolveConflict} onClose={closePanel} />
        : detail.status === 'loading' ? <p className="rec-muted" role="status">正在读取作品详情…</p>
        : detail.status === 'error' ? <div role="alert"><p>{detail.message}</p><button type="button" className="rec-button"
          onClick={() => { if (detail.snapshotChanged) { setPanel(null); reload() } else setDetailRevision((n) => n + 1) }}>{detail.snapshotChanged ? '重新加载推荐' : '重试详情'}</button></div>
          : <div className="rec-detail-content"><RecommendationCover item={detail.item} priority />
            <div><p className="rec-original-title">{detail.item.originalTitle}</p><RecommendationMeta item={detail.item} />
              <h3>推荐依据</h3><RecommendationEvidence item={detail.item} expanded onReload={() => { setPanel(null); reload() }} />
              <h3>故事简介</h3><p>{detail.item.synopsis || '暂未收录简介。'}</p>
              <RecommendationActions item={detail.item} detailLabel="关闭详情" onDetail={() => setPanel(null)}
                onWant={addWant} operation={want.operations[detail.item.bangumiId]} onIdentity={openWantIdentity} />
            </div></div>}
    </RecommendationDialog>}
  </main>
}
