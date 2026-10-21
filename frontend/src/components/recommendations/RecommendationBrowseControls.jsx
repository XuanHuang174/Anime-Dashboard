import { useState } from 'react'
import { REC_CATEGORIES } from '../../utils/recommendations.js'
import { DEFAULT_BROWSE_FILTERS, REC_STYLES, filterChips, filterDraftError } from '../../utils/recommendationBrowse.js'
import RecommendationCategoryGuide from './RecommendationCategoryGuide.jsx'
import { recommendationAlgorithmLabel } from '../../utils/recommendationExplanation.js'
import RecommendationAccessApplication from './RecommendationAccessApplication.jsx'

export function RecommendationBrowseControls({ category, style, filters, onCategory, onStyle, onFilter, onRemove, onReset, legacy = false, algorithmVersion, mode }) {
  const chips = filterChips(filters)
  return <>
    <section className="rec-controls" aria-label="推荐类别与筛选">
      <div className="rec-categories" role="group" aria-label="推荐类别" aria-describedby="rec-category-description">
        {REC_CATEGORIES.map(option => <button type="button" key={option.id} aria-pressed={category === option.id}
          className={`rec-category ${category === option.id ? 'is-selected' : ''}`} onClick={() => onCategory(option.id)}>{option.label}</button>)}
      </div>
      <div className="rec-browse-tools">{!legacy && <label className="rec-style-label">风格：<select aria-label="推荐风格" value={style}
        onChange={event => onStyle(event.target.value)}>{REC_STYLES.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}</select></label>}
        <button type="button" className="rec-button rec-filter-button" onClick={onFilter}>☷ 筛选 {chips.length > 0 && <span className="rec-filter-count">{chips.length}</span>}</button></div>
    </section>
    <div className="rec-control-summary"><div className="rec-active-filters" aria-label="当前生效条件"><div className="rec-filter-chips">
      {chips.map(chip => <button type="button" key={chip.id} className="rec-filter-chip" aria-label={`移除条件：${chip.label}`}
        onClick={() => onRemove(chip.id)}>{chip.label}<span aria-hidden="true"> ×</span></button>)}
      {!chips.length && <span>当前不限筛选条件</span>}</div>
      <button type="button" className="rec-text-button" onClick={onReset}>恢复默认</button></div>
      <details className="rec-browse-help"><summary>浏览说明</summary><div className="rec-browse-help-body">
        <RecommendationCategoryGuide category={category} mode={mode} />
        {!legacy && <p>{REC_STYLES.find(option => option.id === style).description.replace('V1', algorithmVersion === 'content.v2' ? 'V2' : 'V1')}</p>}
        <p>推荐理由只使用已有数据支持的题材关联，不代表喜欢概率。</p>
        <p>加入想看不会覆盖已有评分、长评或观看状态。</p>
        <small>{recommendationAlgorithmLabel(algorithmVersion)}</small>
      </div></details>
    </div>
  </>
}

export function RecommendationFilterPanel({ initialFilters, onApply, legacy = false, client, onAccessChanged }) {
  const [draft, setDraft] = useState(() => ({ ...initialFilters }))
  const [error, setError] = useState('')
  const field = (key, value) => { setDraft(current => ({ ...current, [key]: value })); setError('') }
  return <form className="rec-filter-form" onSubmit={event => { event.preventDefault(); const message = filterDraftError(draft); setError(message); if (!message) onApply(draft) }}>
    <div className="rec-filter-scroll">
    <p className="rec-muted rec-filter-intro">仅筛选本次浏览，不修改偏好；默认排除成人作品，未知分级始终排除。</p>
    <label className="rec-field">作品类型<select aria-label="作品类型" value={draft.type} onChange={event => field('type', event.target.value)}>
      <option value="all">全部类型</option>{['TV', '剧场版', 'OVA', 'WEB', '短片', '动态漫画'].map(type => <option key={type}>{type}</option>)}</select></label>
    {!legacy && <fieldset className="rec-year-range"><legend>放送年份区间</legend>
      <label className="rec-field">起始年份<input aria-label="起始年份" inputMode="numeric" maxLength={4} placeholder="不限" value={draft.yearFrom}
        onChange={event => field('yearFrom', event.target.value)} /></label>
      <label className="rec-field">结束年份<input aria-label="结束年份" inputMode="numeric" maxLength={4} placeholder="不限" value={draft.yearTo}
        onChange={event => field('yearTo', event.target.value)} /></label>
    </fieldset>}
    <label className="rec-field">Bangumi 评分<select aria-label="Bangumi 评分筛选" value={draft.minScore} onChange={event => field('minScore', event.target.value)}>
      <option value="0">不限评分</option><option value="lt6">6.0 以下</option>{['6', '7', '8', '9'].map(score => <option key={score} value={score}>{score}.0 及以上</option>)}</select></label>
    {draft.minScore === 'lt6' && <p className="rec-muted">仅包含有效正数公共评分 &lt;6.0 的作品（不含 6.0）；来源不明的 0 值不归入低分作品。</p>}
    <label className="rec-checkbox"><input type="checkbox" checked={draft.hideRecorded} onChange={event => field('hideRecorded', event.target.checked)} />隐藏手账中已记录的作品</label>
    {client?.eligibility && <RecommendationAccessApplication client={client} onChanged={onAccessChanged} includeAdult={!!draft.includeAdult} onIncludeAdult={value => field('includeAdult', value)} />}
    </div>
    <footer className="rec-filter-footer">{error && <p role="alert" className="rec-inline-error">{error}</p>}
    <div className="rec-dialog-actions"><button type="button" className="rec-button" onClick={() => { setDraft({ ...DEFAULT_BROWSE_FILTERS }); setError('') }}>恢复默认</button>
      <button type="submit" className="rec-button rec-button--primary">应用筛选</button></div></footer>
  </form>
}

export function RecommendationBatchActions({ state, onMore, onBatch, onReload, onFilter, onRestart }) {
  const busy = state.moreLoading || state.batchLoading, expired = state.snapshotExpired || state.snapshotDirty
  return <div className="rec-batch-actions">
    {state.contextUpdated ? <p className="rec-muted" role="status">{state.transport === 'legacy' ? '当前列表仍保留' : `第 ${state.data.batch} 批浏览位置仍保留`} · <a href="#rec-context-update">前往更新提示 ↑</a></p>
      : expired ? <div className="rec-inline-error" role="alert"><p>浏览依据已更新，请重新加载推荐。</p>
      <button type="button" className="rec-button" onClick={onReload}>重新加载推荐</button></div> : <>
      {state.moreError && <p className="rec-inline-error" role="alert">{state.transport === 'legacy' ? '加载下一页失败。' : '加载本批更多失败。'}{state.moreError}</p>}
      {state.batchError && <p className="rec-inline-error" role="alert">下一批加载失败，当前结果仍保留。{state.batchError}</p>}
      <div className="rec-pagination">
        <span className="rec-batch-progress" role="status">{state.transport === 'legacy' ? `已展示 ${state.items.length} / ${state.data.count}` : `第 ${state.data.batch} 批 · 本批已展示 ${state.items.length} / ${state.data.batch_count}`}</span>
        <div className="rec-pagination-buttons">
        {state.transport === 'legacy' && state.data.next_page && <button type="button" className="rec-button rec-button--primary" disabled={busy} onClick={onMore}>
          {state.moreLoading ? '正在加载…' : state.moreError ? '重试加载下一页' : '再翻一页 →'}</button>}
        {state.data.next_page_cursor && <button type="button" className="rec-button rec-button--primary" disabled={busy} onClick={onMore}>
          {state.moreLoading ? '正在加载…' : state.moreError ? '重试加载本批更多' : '加载本批更多'}</button>}
        {state.data.next_batch_cursor && <button type="button" className="rec-button" disabled={busy} onClick={onBatch}>
          {state.batchLoading ? '正在换批…' : state.batchError ? '重试原目标批次 →' : '换一批，看看其他作品 →'}</button>}
        </div>
      </div>
      {state.data.exhausted && <div className="rec-browse-end" role="status"><p>本轮符合条件的作品已展示完。可以调整筛选，或重新开始浏览。</p>
        <button type="button" className="rec-text-button" onClick={onFilter}>调整筛选</button>
        <button type="button" className="rec-text-button" onClick={onRestart}>重新开始浏览</button></div>}
    </>}
  </div>
}
