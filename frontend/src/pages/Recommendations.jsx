import { useEffect, useId, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import RecommendationCard, { RecommendationActions, RecommendationCover, RecommendationMeta } from '../components/recommendations/RecommendationCard.jsx'
import RecommendationDialog from '../components/recommendations/RecommendationDialog.jsx'
import { RecommendationEmptyState, RecommendationSkeleton } from '../components/recommendations/RecommendationStates.jsx'
import { PREVIEW_EXISTING_RECORDS, RECOMMENDATION_PREVIEW_ITEMS } from '../data/recommendationPreview.js'
import { addPreviewWant, DEFAULT_REC_FILTERS, filterRecommendationPreview, getRecommendationViewState, REC_CATEGORIES, REC_SCENARIOS } from '../utils/recommendationPreview.js'
import '../styles/recommendations.css'

const TASTE_OPTIONS = ['日常', '治愈', '科幻', '音乐', '悬疑', '奇幻']

export default function Recommendations() {
  const [scenario, setScenario] = useState('ready')
  const [category, setCategory] = useState('all')
  const [filters, setFilters] = useState({ ...DEFAULT_REC_FILTERS })
  const [draftFilters, setDraftFilters] = useState({ ...DEFAULT_REC_FILTERS })
  const [panel, setPanel] = useState(null)
  const [records, setRecords] = useState(() => ({ ...PREVIEW_EXISTING_RECORDS }))
  const [tastes, setTastes] = useState(['日常', '治愈', '科幻'])
  const [draftTastes, setDraftTastes] = useState([])
  const [notice, setNotice] = useState('')
  const scenarioId = useId()
  const visible = useMemo(() => filterRecommendationPreview(RECOMMENDATION_PREVIEW_ITEMS, category, filters, records), [category, filters, records])
  const state = getRecommendationViewState(scenario, visible.length)
  const activeFilters = Number(filters.type !== 'all') + Number(filters.minScore !== '0') + Number(filters.hideRecorded)
  const cold = scenario === 'cold'

  useEffect(() => {
    const previousTitle = document.title
    document.title = '番剧发现手账 · 界面预览'
    return () => { document.title = previousTitle }
  }, [])
  useEffect(() => {
    if (scenario !== 'retrying') return undefined
    const timer = window.setTimeout(() => setScenario('ready'), 650)
    return () => window.clearTimeout(timer)
  }, [scenario])
  useEffect(() => {
    if (!notice) return undefined
    const timer = window.setTimeout(() => setNotice(''), 4500)
    return () => window.clearTimeout(timer)
  }, [notice])

  const reset = () => { setFilters({ ...DEFAULT_REC_FILTERS }); setCategory('all'); setScenario('ready') }
  const openFilters = () => { setDraftFilters({ ...filters }); setPanel({ kind: 'filters' }) }
  const openPreferences = () => { setDraftTastes([...tastes]); setPanel({ kind: 'preferences' }) }
  const addWant = (item) => {
    setRecords((current) => addPreviewWant(current, item))
    setNotice('已加入本页的演示想看。没有写入或更改你的正式手账。')
  }
  const cardProps = (item) => ({ item, record: records[item.id], onDetail: (chosen) => setPanel({ kind: 'detail', item: chosen }), onWant: addWant })

  return <main className="memphis-rec-container">
    <a className="rec-skip-link" href="#rec-results">跳到推荐内容</a>
    <div className="rec-page">
      <nav className="rec-nav" aria-label="推荐页面导航"><Link className="rec-back" to="/dashboard">← 返回我的手账</Link>
        <button type="button" className="rec-button rec-preferences" onClick={openPreferences}>☷ 偏好设置</button></nav>
      <header className="rec-intro">
        <div className="rec-intro-copy"><span className="rec-eyebrow">YOUR NEXT FAVORITE <span className="rec-preview-badge">设计预览</span></span>
          <h1>找到你的下一部<br /><span>心头好<span className="rec-title-star" aria-hidden="true">✦</span></span></h1>
          <p>从喜欢的故事出发，把下一次心动写进手账。</p></div>
        <aside className="rec-taste-note" aria-labelledby="rec-taste-title"><span className="rec-eyebrow">A NOTE ABOUT YOUR TASTE</span>
          <h2 id="rec-taste-title">我的口味 <span aria-hidden="true">↗</span></h2>
          <p className="rec-taste-summary"><b>{cold ? 0 : tastes.length}</b> 个演示标签 · <b>{cold ? 0 : 8}</b> 部演示样本</p>
          <div className="rec-card-tags">{!cold && tastes.slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div>
          <button type="button" className="rec-text-button" onClick={openPreferences}>调整演示喜好 →</button>
        </aside>
      </header>
      <section className="rec-demo-note" aria-label="静态预览说明"><span className="rec-demo-dot" aria-hidden="true" />
        <p><strong>这是一份界面样刊。</strong>静态样本不是你的推荐结果，尚未分析手账；所有加入操作仅在本页演示。</p></section>
      <section className="rec-controls" aria-label="推荐类别与筛选">
        <div className="rec-categories" role="group" aria-label="推荐类别">{REC_CATEGORIES.map((option) =>
          <button type="button" key={option.id} aria-pressed={category === option.id} className={`rec-category ${category === option.id ? 'is-selected' : ''}`}
            onClick={() => setCategory(option.id)}>{option.label}</button>)}</div>
        <button type="button" className="rec-button rec-filter-button" onClick={openFilters}>☷ 筛选 {activeFilters > 0 && <span className="rec-filter-count">{activeFilters}</span>}</button>
      </section>
      {activeFilters > 0 && <div className="rec-active-filters"><span>筛选：{filters.type !== 'all' ? `${filters.type} · ` : ''}
        {filters.minScore !== '0' ? `评分 ≥ ${filters.minScore} · ` : ''}{filters.hideRecorded ? '隐藏演示已有记录' : ''}</span>
        <button type="button" className="rec-text-button" onClick={reset}>清除筛选</button></div>}
      <section id="rec-results" className="rec-results" tabIndex={-1} aria-labelledby="rec-results-title">
        <div className="rec-section-heading"><div><span className="rec-eyebrow">PICK A STORY, START A JOURNEY</span>
          <h2 id="rec-results-title">下一次心动，从这里开始</h2></div>
          <span className="rec-result-count">{state === 'ready' ? `${visible.length} 部静态示例` : '界面状态演示'}</span></div>
        {state === 'loading' ? <RecommendationSkeleton /> : state !== 'ready'
          ? <RecommendationEmptyState state={state} onRetry={() => setScenario('retrying')} onReset={reset} onPreferences={openPreferences} />
          : <><RecommendationCard {...cardProps(visible[0])} hero />
            {visible.length > 1 && <><div className="rec-more-heading"><h3>还有这些故事，值得翻开</h3><span>不止一种心动</span></div>
              <div className="rec-grid">{visible.slice(1).map((item) => <RecommendationCard key={item.id} {...cardProps(item)} />)}</div></>}</>}
      </section>
      <section className="rec-qa-panel" aria-label="界面验收工具"><div><strong>样刊验收台</strong><p>切换静态状态，检查页面兜底；不调用推荐 API。</p></div>
        <label htmlFor={scenarioId}>验收场景<select id={scenarioId} value={scenario === 'retrying' ? 'loading' : scenario}
          onChange={(event) => { setScenario(event.target.value); setNotice(''); setPanel(null) }}>
          {REC_SCENARIOS.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>
        <button type="button" className="rec-text-button" onClick={() => { setRecords({ ...PREVIEW_EXISTING_RECORDS }); setTastes(['日常', '治愈', '科幻']); reset(); setNotice('演示数据已重置，正式手账没有变化。') }}>重置演示</button>
      </section>
      <footer className="rec-footer"><span>给每一次喜欢，一个值得停留的位置。</span><span>DISCOVERY JOURNAL / UI PREVIEW</span></footer>
    </div>
    <div className="rec-toast" role="status" aria-live="polite">{notice && <p>{notice}</p>}</div>
    {panel && <RecommendationDialog kind={panel.kind} title={panel.kind === 'filters' ? '给心动留一点范围' : panel.kind === 'preferences' ? '我的口味 · 演示设置' : panel.item.title}
      onClose={() => setPanel(null)}>
      {panel.kind === 'filters' ? <form onSubmit={(event) => { event.preventDefault(); setFilters({ ...draftFilters }); setPanel(null) }}>
        <p className="rec-muted">仅筛选当前静态样本。成人及分级未知条目始终不展示。</p>
        <label className="rec-field">作品类型<select value={draftFilters.type} onChange={(event) => setDraftFilters({ ...draftFilters, type: event.target.value })}>
          <option value="all">全部类型</option><option value="TV">TV</option><option value="剧场版">剧场版</option><option value="OVA">OVA</option></select></label>
        <label className="rec-field">Bangumi 评分<select value={draftFilters.minScore} onChange={(event) => setDraftFilters({ ...draftFilters, minScore: event.target.value })}>
          <option value="0">不限评分</option><option value="8">8.0 及以上</option><option value="9">9.0 及以上</option></select></label>
        <label className="rec-checkbox"><input type="checkbox" checked={draftFilters.hideRecorded}
          onChange={(event) => setDraftFilters({ ...draftFilters, hideRecorded: event.target.checked })} />隐藏演示中已记录的作品</label>
        <div className="rec-dialog-actions"><button type="button" className="rec-button" onClick={() => setDraftFilters({ ...DEFAULT_REC_FILTERS })}>恢复默认</button>
          <button type="submit" className="rec-button rec-button--primary">应用筛选</button></div>
      </form> : panel.kind === 'preferences' ? <div>
        <p className="rec-muted">标签仅调整本页便签展示，尚未保存偏好或参与推荐。冷启动场景仍不会生成结果。</p>
        <fieldset className="rec-taste-options"><legend>演示喜好标签</legend>{TASTE_OPTIONS.map((tag) => <label className="rec-taste-option" key={tag}>
          <input type="checkbox" checked={draftTastes.includes(tag)} onChange={(event) => setDraftTastes(event.target.checked ? [...draftTastes, tag] : draftTastes.filter((value) => value !== tag))} />{tag}</label>)}</fieldset>
        <div className="rec-dialog-actions"><button type="button" className="rec-button rec-button--primary" onClick={() => { setTastes([...draftTastes]); setPanel(null); setNotice('演示标签已调整。尚未生成个性化推荐或保存到服务器。') }}>应用到演示便签</button></div>
      </div> : <div className="rec-detail-content"><RecommendationCover item={panel.item} priority />
        <div><span className="rec-preview-badge">静态详情示例</span><p className="rec-original-title">{panel.item.originalTitle}</p><RecommendationMeta item={panel.item} />
          <h3>推荐理由示例</h3><p>{panel.item.reason}</p><h3>故事简介示例</h3><p>{panel.item.synopsis}</p>
          <p className="rec-muted">公开作品资料为静态快照，评分不是实时值。未接入真实详情或手账写入接口。</p>
          {records[panel.item.id] && <p className="rec-record-note">演示中已有记录，原状态、评分和评价均保留。</p>}
          <RecommendationActions {...cardProps(panel.item)} detailLabel="关闭详情" onDetail={() => setPanel(null)} />
        </div></div>}
    </RecommendationDialog>}
  </main>
}
