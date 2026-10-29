import { useState } from 'react'
import { CREDIT_DEFAULTS } from '../../utils/recommendationCredits.js'
import { creditsFilterDraft, creditsFilterOptions } from '../../utils/creditsBrowseFilters.js'
import BangumiScoreFilter from './BangumiScoreFilter.jsx'
import RecommendationContentScopeToggle from './RecommendationContentScopeToggle.jsx'

export default function CreditsFilterPanel({ options, adult, client, busy, onApply }) {
  const [draft, setDraft] = useState(() => creditsFilterDraft(options, adult))
  const [error, setError] = useState(''), [scopeAvailable, setScopeAvailable] = useState(false)
  const field = (key, value) => { setDraft(old => ({ ...old, [key]: value })); setError('') }
  return <form className="rec-filter-form" aria-label="参与作品筛选" onSubmit={event => {
    event.preventDefault()
    try { onApply(creditsFilterOptions(draft), client.eligibility ? !!draft.includeAdult && scopeAvailable : adult) }
    catch (error) { setError(error.message) }
  }}><div className="rec-filter-scroll">
    <p className="rec-muted rec-filter-intro">仅筛选本次参与作品；浏览范围与排序保持主界面的选择。</p>
    <label className="rec-field">作品类型<select value={draft.type} onChange={event => field('type', event.target.value)}>
      {['all', 'TV', 'OVA', '剧场版', 'WEB', '短片', '动态漫画'].map(type => <option key={type} value={type}>{type === 'all' ? '不限类型' : type}</option>)}
    </select></label>
    <fieldset className="rec-year-range"><legend>放送年份区间</legend>
      {[['yearFrom', '起始年份'], ['yearTo', '结束年份']].map(([key, label]) => <label className="rec-field" key={key}>{label}
        <input aria-label={label} inputMode="numeric" maxLength={4} placeholder="不限" value={draft[key]} onChange={event => field(key, event.target.value)} />
      </label>)}
    </fieldset>
    <BangumiScoreFilter filters={draft} onChange={next => { setDraft(next); setError('') }} />
    <label className="rec-checkbox"><input type="checkbox" checked={draft.hide_recorded} disabled={draft.view === 'completed'} onChange={event => field('hide_recorded', event.target.checked)} />隐藏手账中已记录的作品</label>
    {draft.view === 'completed' && <p className="rec-muted">“我看过的”包含本人已看完记录，此项保持关闭。</p>}
    {client.eligibility && <RecommendationContentScopeToggle client={client} includeAdult={!!draft.includeAdult} onChange={value => field('includeAdult', value)} onAvailability={setScopeAvailable} />}
  </div><footer className="rec-filter-footer">{error && <p className="rec-inline-error" role="alert">{error}</p>}<div className="rec-dialog-actions">
    <button type="button" className="rec-button" onClick={() => { setDraft(creditsFilterDraft({ ...CREDIT_DEFAULTS, view: options.view, sort: options.sort,
      hide_recorded: options.view !== 'completed' }, false)); setError('') }}>恢复默认</button>
    <button type="submit" className="rec-button rec-button--primary" disabled={busy}>应用筛选</button>
  </div></footer></form>
}
