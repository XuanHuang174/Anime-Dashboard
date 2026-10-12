import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import RecommendationCard, { RecommendationActions, RecommendationCover, RecommendationMeta } from '../components/recommendations/RecommendationCard.jsx'
import RecommendationDialog from '../components/recommendations/RecommendationDialog.jsx'
import RecommendationIdentityReview from '../components/recommendations/RecommendationIdentityReview.jsx'
import { RecommendationEmptyState, RecommendationSkeleton } from '../components/recommendations/RecommendationStates.jsx'
import recommendationApi from '../api/recommendations.js'
import useRecommendations from '../hooks/useRecommendations.js'
import { createLatestRequestGate, DEFAULT_REC_FILTERS, mapRecommendationItem, recommendationError, REC_CATEGORIES, identitySummary } from '../utils/recommendations.js'
import '../styles/recommendations.css'

export default function Recommendations({ client = recommendationApi }) {
  const [category, setCategory] = useState('all')
  const [filters, setFilters] = useState({ ...DEFAULT_REC_FILTERS })
  const [draftFilters, setDraftFilters] = useState({ ...DEFAULT_REC_FILTERS })
  const [panel, setPanel] = useState(null)
  const [draftTastes, setDraftTastes] = useState([])
  const [preferenceVersion, setPreferenceVersion] = useState(0)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [notice, setNotice] = useState('')
  const [detail, setDetail] = useState({ status: 'loading' })
  const [detailRevision, setDetailRevision] = useState(0)
  const detailGate = useRef(createLatestRequestGate())
  const saveGate = useRef(createLatestRequestGate())
  const saveBusy = useRef(false)
  const state = useRecommendations(category, filters, client)
  const activeFilters = Number(filters.type !== 'all') + Number(filters.minScore !== '0') + Number(filters.hideRecorded)
  const detailId = panel?.kind === 'detail' ? panel.item.bangumiId : null

  useEffect(() => {
    const previous = document.title
    document.title = '个性化推荐 · 番剧发现手账'
    return () => { document.title = previous; saveGate.current.cancel() }
  }, [])
  useEffect(() => {
    if (!detailId) { detailGate.current.cancel(); return undefined }
    const request = detailGate.current.begin()
    setDetail({ status: 'loading' })
    client.detail(detailId, request.signal).then((raw) => {
      const item = mapRecommendationItem(raw)
      if (request.isCurrent()) setDetail({ status: 'ready', item })
    }).catch((error) => { if (request.isCurrent()) setDetail({ status: 'error', message: recommendationError(error) }) })
    return () => detailGate.current.cancel()
  }, [detailId, detailRevision, client])
  useEffect(() => {
    if (!notice) return undefined
    const timer = window.setTimeout(() => setNotice(''), 5000)
    return () => window.clearTimeout(timer)
  }, [notice])

  const reset = () => { setFilters({ ...DEFAULT_REC_FILTERS }); setCategory('all'); state.retry() }
  const openPreferences = () => {
    if (!state.preferences) return
    setDraftTastes([...state.preferences.tags]); setPreferenceVersion(state.preferences.version)
    setSaveError(''); setPanel({ kind: 'preferences' })
  }
  const savePreferences = async () => {
    if (saveBusy.current) return
    saveBusy.current = true
    setSaving(true); setSaveError('')
    const request = saveGate.current.begin()
    try {
      await client.savePreferences({ tags: draftTastes, expected_version: preferenceVersion }, request.signal)
      if (request.isCurrent()) { setPanel(null); setNotice('喜好已保存，正在重新计算推荐。'); state.retry() }
    } catch (error) {
      if (request.isCurrent()) setSaveError(error?.response?.status === 409
        ? '偏好已在其他页面更新。请关闭面板并重新加载，再重新选择；本次没有覆盖新偏好。'
        : '保存失败，请稍后重试；最多选择 20 个题材。')
    } finally {
      if (request.isCurrent()) { setSaving(false); saveBusy.current = false }
    }
  }
  const cardProps = (item) => ({ item, onDetail: (chosen) => { setDetail({ status: 'loading' }); setPanel({ kind: 'detail', item: chosen }) } })
  const profile = state.profile
  const pending = profile?.diagnostics?.pending_identity_count || 0

  return <main className="memphis-rec-container">
    <a className="rec-skip-link" href="#rec-results">跳到推荐内容</a>
    <div className="rec-page">
      <nav className="rec-nav" aria-label="推荐页面导航"><Link className="rec-back" to="/dashboard">← 返回我的手账</Link>
        <button type="button" className="rec-button rec-preferences" disabled={!state.preferences} onClick={openPreferences}>☷ 偏好设置</button></nav>
      <header className="rec-intro">
        <div className="rec-intro-copy"><span className="rec-eyebrow">YOUR NEXT FAVORITE <span className="rec-preview-badge">推荐 V1</span></span>
          <h1>找到你的下一部<br /><span>心头好<span className="rec-title-star" aria-hidden="true">✦</span></span></h1>
          <p>从喜欢的故事出发，把下一次心动写进手账。</p></div>
        <aside className="rec-taste-note" aria-labelledby="rec-taste-title"><span className="rec-eyebrow">A NOTE ABOUT YOUR TASTE</span>
          <h2 id="rec-taste-title">我的口味 <span aria-hidden="true">↗</span></h2>
          <p className="rec-taste-summary">{pending && !profile?.eligible_sample_count ? '高分作品待关联，画像标签待生成'
            : <><b>{profile?.tag_summary.length ?? '—'}</b> 个画像标签 · <b>{profile?.eligible_sample_count ?? '—'}</b> 部已关联样本</>}</p>
          <p className="rec-muted">手动偏好 {state.preferences?.tags.length ?? '—'} 个{pending ? ` · 待关联 ${pending} 条` : ''}</p>
          <div className="rec-card-tags">{profile?.tag_summary.slice(0, 3).map(({ tag }) => <span key={tag}>{tag}</span>)}</div>
          <button type="button" className="rec-text-button" disabled={!state.preferences} onClick={openPreferences}>调整明确喜好 →</button>
        </aside>
      </header>
      {pending > 0 && <section className="rec-identity-notice" aria-label="旧数据关联提示"><p>{identitySummary(profile)}</p>
        <button type="button" className="rec-button rec-button--primary" disabled={profile.identity_service_ready === false} onClick={() => setPanel({ kind: 'identity' })}>核对旧作品 →</button></section>}
      {profile?.identity_service_ready === false && <p className="rec-muted">关联工具尚待完成部署。旧手账记录仍完整保留，当前不会自动修改数据。</p>}
      {profile?.diagnostics?.unavailable_identity_count > 0 && <p className="rec-muted">另有 {profile.diagnostics.unavailable_identity_count} 条高分记录因未收录或分级限制未计入推荐画像。</p>}
      <section className="rec-demo-note" aria-label="推荐依据"><span className="rec-demo-dot" aria-hidden="true" />
        <p>{state.data?.mode === 'preference_based' ? '当前依据你明确选择的题材推荐，尚未形成高分作品画像。'
          : `参考已看过且个人评分 ≥ ${profile?.sample_policy.minimum_score ?? '8.5'} 的作品；只展示已有数据支持的推荐理由。`}
          {' '}手账加入功能将在下一阶段开放。</p></section>
      <section className="rec-controls" aria-label="推荐类别与筛选">
        <div className="rec-categories" role="group" aria-label="推荐类别">{REC_CATEGORIES.map((option) =>
          <button type="button" key={option.id} aria-pressed={category === option.id} className={`rec-category ${category === option.id ? 'is-selected' : ''}`}
            onClick={() => setCategory(option.id)}>{option.label}</button>)}</div>
        <button type="button" className="rec-button rec-filter-button" onClick={() => { setDraftFilters({ ...filters }); setPanel({ kind: 'filters' }) }}>☷ 筛选 {activeFilters > 0 && <span className="rec-filter-count">{activeFilters}</span>}</button>
      </section>
      {activeFilters > 0 && <div className="rec-active-filters"><span>筛选：{filters.type !== 'all' ? `${filters.type} · ` : ''}
        {filters.minScore !== '0' ? `评分 ≥ ${filters.minScore} · ` : ''}{filters.hideRecorded ? '隐藏手账已有记录' : ''}</span>
        <button type="button" className="rec-text-button" onClick={reset}>恢复筛选</button></div>}
      <section id="rec-results" className="rec-results" tabIndex={-1} aria-labelledby="rec-results-title">
        <div className="rec-section-heading"><div><span className="rec-eyebrow">PICK A STORY, START A JOURNEY</span>
          <h2 id="rec-results-title">下一次心动，从这里开始</h2></div>
          <span className="rec-result-count">{state.data ? `${state.data.count} 部可浏览作品` : state.status === 'error' ? '暂未加载结果' : '正在读取数据'}</span></div>
        {state.error && <p className="rec-inline-error" role="alert">{state.error}</p>}
        {state.status === 'loading' ? <RecommendationSkeleton /> : state.status !== 'ready'
          ? <RecommendationEmptyState state={state.status} needsTags={state.status === 'cold' && profile?.readiness === 'needs_tags'}
            needsIdentity={state.status === 'cold' && pending > 0} onIdentity={() => setPanel({ kind: 'identity' })}
            onRetry={state.retry} onReset={reset} onPreferences={openPreferences} />
          : <><RecommendationCard {...cardProps(state.items[0])} hero />
            {state.items.length > 1 && <><div className="rec-more-heading"><h3>还有这些故事，值得翻开</h3><span>不止一种心动</span></div>
              <div className="rec-grid">{state.items.slice(1).map((item) => <RecommendationCard key={item.id} {...cardProps(item)} />)}</div></>}
            {state.moreError && <p className="rec-inline-error" role="alert">{state.moreError}</p>}
            {state.data.next_page && <div className="rec-pagination"><button type="button" className="rec-button rec-button--primary"
              disabled={state.moreLoading} onClick={state.loadMore}>{state.moreLoading ? '正在加载…' : state.moreError ? '重试加载下一页' : '再翻一页 →'}</button>
              <span>已展示 {state.items.length} / {state.data.count}</span></div>}</>}
      </section>
      <footer className="rec-footer"><span>给每一次喜欢，一个值得停留的位置。</span><span>DISCOVERY JOURNAL / CONTENT V1</span></footer>
    </div>
    <div className="rec-toast" role="status" aria-live="polite">{notice && <p>{notice}</p>}</div>
    {panel?.kind === 'identity' && <RecommendationIdentityReview client={client} onClose={() => setPanel(null)} onChanged={state.retry} />}
    {panel && panel.kind !== 'identity' && <RecommendationDialog kind={panel.kind} busy={saving} title={panel.kind === 'filters' ? '给心动留一点范围' : panel.kind === 'preferences' ? '我的口味' : panel.item.title}
      onClose={() => setPanel(null)}>
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
      </form> : panel.kind === 'preferences' ? <div>
        <p className="rec-muted">最多选择 20 个题材，保存后用于推荐计算。不会修改手账评分或评价。</p>
        <fieldset className="rec-taste-options" disabled={saving}><legend>明确喜好标签（{draftTastes.length}/20）</legend>{state.preferences.available_tags.map((tag) => <label className="rec-taste-option" key={tag}>
          <input type="checkbox" checked={draftTastes.includes(tag)} disabled={!draftTastes.includes(tag) && draftTastes.length >= 20}
            onChange={(event) => setDraftTastes(event.target.checked ? [...draftTastes, tag] : draftTastes.filter((value) => value !== tag))} />{tag}</label>)}</fieldset>
        {saveError && <p className="rec-inline-error" role="alert">{saveError}</p>}
        <div className="rec-dialog-actions"><button type="button" disabled={saving} className="rec-button rec-button--primary" onClick={savePreferences}>{saving ? '正在保存…' : '保存喜好'}</button></div>
      </div> : detail.status === 'loading' ? <p className="rec-muted" role="status">正在读取作品详情…</p>
        : detail.status === 'error' ? <div role="alert"><p>{detail.message}</p><button type="button" className="rec-button" onClick={() => setDetailRevision((n) => n + 1)}>重试详情</button></div>
          : <div className="rec-detail-content"><RecommendationCover item={detail.item} priority />
            <div><p className="rec-original-title">{detail.item.originalTitle}</p><RecommendationMeta item={detail.item} />
              <h3>推荐依据</h3><p>{detail.item.reason || '当前没有足够的共有题材证据。'}</p>
              <h3>故事简介</h3><p>{detail.item.synopsis || '暂未收录简介。'}</p>
              <RecommendationActions item={detail.item} detailLabel="关闭详情" onDetail={() => setPanel(null)} />
            </div></div>}
    </RecommendationDialog>}
  </main>
}
