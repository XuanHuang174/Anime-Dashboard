import { useState } from 'react'
import { emptyTagDraft, tagDraftError } from '../../utils/explorationTagQuery.js'
import RecommendationContentScopeToggle from './RecommendationContentScopeToggle.jsx'

export default function ExplorationTagEditor({ draft, onChange, options, status, onReload, onApply, client, adult }) {
  const [term, setTerm] = useState(''), [order, setOrder] = useState('count'), [group, setGroup] = useState('include')
  const [error, setError] = useState(''), [scopeAvailable, setScopeAvailable] = useState(false)
  const includeAdult = draft.filters.includeAdult ?? adult
  const field = (key, value) => { setError(''); onChange({ ...draft, [key]: value }) }
  const filter = (key, value) => field('filters', { ...draft.filters, [key]: value })
  const tags = options?.tags.map(row => row.tag) || []
  const toggle = tag => {
    const other = group === 'include' ? 'exclude' : 'include'
    if (draft[group].includes(tag)) field(group, draft[group].filter(value => value !== tag))
    else if (draft[other].includes(tag)) setError('这个题材已在另一组，请先移除后再选择。')
    else if (draft[group].length >= 5) setError('每组最多选择 5 个题材。')
    else field(group, [...draft[group], tag])
  }
  const choices = (options?.tags || []).filter(row => row.tag.includes(term.trim())).toSorted((a, b) => order === 'count'
    ? b.count - a.count || a.tag.localeCompare(b.tag, 'zh-CN') : a.tag.localeCompare(b.tag, 'zh-CN'))
  return <form className="rec-filter-form rec-tag-editor" onSubmit={event => {
    event.preventDefault(); const message = tagDraftError(draft, tags); setError(message)
    if (!message && status === 'ready') onApply(draft, includeAdult && scopeAvailable)
  }}><div className="rec-filter-scroll">
    <p className="rec-muted">草稿仅用于本次探索，点击“开始探索”后生效，不修改口味偏好。</p>
    <div className="rec-tag-selections">{['include', 'exclude'].map(key => <section key={key} aria-label={key === 'include' ? '包含题材草稿' : '排除题材草稿'}>
      <strong>{key === 'include' ? '包含题材' : '排除题材'} {draft[key].length}/5</strong><div className="rec-filter-chips">{draft[key].length ? draft[key].map(tag =>
        <button type="button" key={tag} className="rec-filter-chip" aria-label={`从${key === 'include' ? '包含' : '排除'}草稿移除${tag}`} onClick={() => field(key, draft[key].filter(value => value !== tag))}>{tag} ×</button>) : <span className="rec-muted">尚未选择</span>}</div>
    </section>)}</div>
    <fieldset className="rec-tag-rule"><legend>匹配规则</legend>{[['all', '同时满足全部（ALL）'], ['any', '满足任一（ANY）']].map(([value, label]) =>
      <label key={value}><input type="radio" name="tag-match" checked={draft.match === value} onChange={() => field('match', value)} />{label}</label>)}</fieldset>
    <label className="rec-field">结果排序<select value={draft.sort} onChange={event => field('sort', event.target.value)}><option value="relevance">题材关联优先</option><option value="score">Bangumi 评分优先</option></select></label>
    <div className="rec-tag-picker-tools"><div role="group" aria-label="题材选择目标">{['include', 'exclude'].map(key => <button type="button" key={key}
      className={`rec-button ${group === key ? 'rec-button--primary' : ''}`} aria-pressed={group === key} onClick={() => { setGroup(key); setError('') }}>{key === 'include' ? '＋ 包含' : '− 排除'}</button>)}</div>
      <label>查找题材<input aria-label="查找题材" value={term} maxLength={50} onChange={event => setTerm(event.target.value)} /></label>
      <label>题材顺序<select value={order} onChange={event => setOrder(event.target.value)}><option value="count">数量优先</option><option value="name">名称顺序</option></select></label></div>
    <p className="rec-muted">{options?.count_description || '数量为当前内容范围的目录数量，未叠加结果筛选，不扣除已有记录或隐藏作品。'} 数量范围：{adult ? '已应用的 NSFW 浏览范围' : '已应用的普通浏览范围'}；切换 NSFW 草稿后，应用时更新数量。</p>
    {status === 'loading' ? <p role="status">正在读取标准题材与目录数量…</p> : status !== 'ready' ? <div role="alert"><p>标准题材读取失败，请重新读取。</p><button type="button" className="rec-button" onClick={onReload}>重读题材</button></div>
      : <div className="rec-tag-options" aria-label="标准题材">{choices.map(({ tag, count }) => <button type="button" key={tag} onClick={() => toggle(tag)} aria-pressed={draft[group].includes(tag)}
        className={draft.include.includes(tag) ? 'is-included' : draft.exclude.includes(tag) ? 'is-excluded' : ''}>{draft.include.includes(tag) ? '＋ ' : draft.exclude.includes(tag) ? '− ' : ''}{tag}<small>{count} 部</small></button>)}{!choices.length && <p>没有匹配的标准题材。</p>}</div>}
    <p className="rec-muted">{options?.exclusion_description || '按目录已有标注筛选，不能保证作品绝无相关内容。'}</p>
    <details className="rec-tag-common"><summary>类型、年份与评分范围</summary>
      <label className="rec-field">作品类型<select value={draft.filters.type} onChange={event => filter('type', event.target.value)}><option value="all">全部类型</option>{['TV', '剧场版', 'OVA', 'WEB', '短片', '动态漫画'].map(type => <option key={type}>{type}</option>)}</select></label>
      <fieldset className="rec-year-range"><legend>放送年份</legend>{[['yearFrom', '起始年份'], ['yearTo', '结束年份']].map(([key, label]) => <label className="rec-field" key={key}>{label}<input aria-label={label} inputMode="numeric" maxLength={4} placeholder="不限" value={draft.filters[key]} onChange={event => filter(key, event.target.value)} /></label>)}</fieldset>
      <label className="rec-field">Bangumi 评分<select value={draft.filters.minScore} onChange={event => filter('minScore', event.target.value)}><option value="0">不限评分</option><option value="lt6">6.0 以下（不含未知评分）</option>{['6', '7', '8', '9'].map(value => <option key={value} value={value}>{value}.0 及以上</option>)}</select></label>
    </details>
    <label className="rec-checkbox"><input type="checkbox" checked={draft.filters.hideRecorded} onChange={event => filter('hideRecorded', event.target.checked)} />隐藏手账中已记录的作品</label>
    {client?.eligibility && <RecommendationContentScopeToggle client={client} includeAdult={includeAdult} onChange={value => filter('includeAdult', value)} onAvailability={setScopeAvailable} />}
  </div><footer className="rec-filter-footer">{error && <p role="alert" className="rec-inline-error">{error}</p>}<div className="rec-dialog-actions">
    <button type="button" className="rec-button" onClick={() => { const next = emptyTagDraft(); next.filters.includeAdult = false; onChange(next); setError('') }}>重置草稿</button>
    <button type="submit" disabled={status !== 'ready'} className="rec-button rec-button--primary">开始探索</button>
  </div></footer></form>
}
