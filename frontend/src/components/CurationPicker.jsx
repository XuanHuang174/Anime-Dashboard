import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import { seriesSession } from '../hooks/useSeriesSession.js'
import { curationError } from '../utils/curations.js'
import { WATCH_LABELS } from '../utils/watchHistory.js'
import SearchableSelect from './SearchableSelect.jsx'
import CurationArtwork from './CurationArtwork.jsx'

const initial = { search: '', status: 'all', tag: 'all', year: 'all', quick_filter: '' }
export default function CurationPicker({ members, selected, onSelection, blocked, relink, quickFilters }) {
  const [draft, setDraft] = useState(initial), [query, setQuery] = useState(initial), [page, setPage] = useState(1)
  const [data, setData] = useState(null), [options, setOptions] = useState({ tags: [], years: [] }), [error, setError] = useState(''), [loading, setLoading] = useState(true), [retry, setRetry] = useState(0)
  const owner = useRef(seriesSession())
  useEffect(() => {
    const controller = new AbortController()
    api.get('records/filter-options/', { signal: controller.signal }).then(({ data }) => {
      if (!controller.signal.aborted && owner.current === seriesSession() && Array.isArray(data.tags) && Array.isArray(data.years)) setOptions(data)
    }).catch(() => {})
    return () => controller.abort()
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    api.get('records/', { params: { ...query, quick_filter: query.quick_filter || undefined, page, page_size: 12 }, signal: controller.signal }).then(({ data }) => {
      if (controller.signal.aborted || owner.current !== seriesSession()) return
      if (!Number.isSafeInteger(data.count) || data.count < 0 || !Array.isArray(data.results) || data.results.length !== Math.min(12, Math.max(0, data.count - (page-1)*12))
        || data.results.some(row => !Number.isSafeInteger(row.id) || row.id < 1 || typeof row.anime?.chinese_title !== 'string' || !Object.hasOwn(WATCH_LABELS, row.watch_status))
        || new Set(data.results.map(row => row.id)).size !== data.results.length) throw new Error('选片资料不完整，请重试。')
      setData(data)
    }).catch(problem => { if (!controller.signal.aborted && owner.current === seriesSession()) { setData(null); setError(curationError(problem)) } })
      .finally(() => { if (!controller.signal.aborted && owner.current === seriesSession()) setLoading(false) })
    return () => controller.abort()
  }, [query, page, retry])
  const toggle = row => onSelection(selected.some(item => item.id === row.id) ? selected.filter(item => item.id !== row.id) : relink ? [{ id: row.id, title: row.anime.chinese_title }] : [...selected, { id: row.id, title: row.anime.chinese_title }])
  const candidates = data?.results.filter(row => !members.has(row.id) && !selected.some(item => item.id === row.id)) || []
  return <>
    <p className="curation-hint">从本人私人手账明确选片。筛选后保留已勾选项，保存后才收录；观看状态和计划分别管理。</p>
    <form className="curation-picker-filters" onSubmit={event => { event.preventDefault(); setPage(1); setQuery({ ...draft, search: draft.search.trim() }); setRetry(n => n+1) }}>
      <label className="curation-wide">作品名称<input aria-label="搜索片单候选作品" maxLength={100} value={draft.search} onChange={event => setDraft(d => ({ ...d, search: event.target.value }))} disabled={blocked} placeholder="中文或日文名称" /></label>
      <label>观看状态<select aria-label="观看状态" value={draft.status} disabled={blocked} onChange={event => setDraft(d => ({ ...d, status: event.target.value }))}><option value="all">全部状态</option>{Object.entries(WATCH_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <div className="curation-select-field"><label htmlFor="curation-picker-tag">题材标签</label><SearchableSelect id="curation-picker-tag" label="题材标签" value={draft.tag} disabled={blocked} onChange={tag=>setDraft(d=>({...d,tag}))} options={options.tags} allOption={{value:'all',label:'全部标签'}} searchPlaceholder="搜索题材标签" contained/></div>
      <div className="curation-select-field"><label htmlFor="curation-picker-year">放送年份</label><SearchableSelect id="curation-picker-year" label="放送年份" value={draft.year} disabled={blocked} onChange={year=>setDraft(d=>({...d,year}))} options={options.years} allOption={{value:'all',label:'全部年份'}} searchPlaceholder="搜索放送年份" contained/></div>
      <div className="curation-select-field"><label htmlFor="curation-picker-quick">我的快捷筛选</label><SearchableSelect id="curation-picker-quick" label="我的快捷筛选" value={draft.quick_filter} disabled={blocked} onChange={quick_filter=>setDraft(d=>({...d,quick_filter}))} options={quickFilters.map(row=>({value:row.id,label:row.name}))} allOption={{value:'',label:'不使用组合'}} searchPlaceholder="搜索快捷筛选" contained/></div>
      <div className="plan-actions curation-wide"><button type="submit" disabled={blocked}>筛选作品</button><button type="button" disabled={blocked} onClick={() => { setDraft(initial); setQuery(initial); setPage(1); setRetry(n => n+1) }}>清空筛选</button></div>
    </form>
    <div className="curation-picker-summary"><p aria-live="polite">当前匹配 {loading ? '…' : data?.count ?? '—'} 部 · 已选 {selected.length}{relink ? ' / 1' : ' / 100'} 部</p>{!relink && <button type="button" disabled={blocked || loading || !candidates.length || selected.length + candidates.length > 100} onClick={() => onSelection([...selected, ...candidates.map(row => ({ id: row.id, title: row.anime.chinese_title }))])}>勾选本页可收录作品</button>}</div>
    {selected.length > 0 && <details className="curation-selected"><summary>核对已选作品（{selected.length} 部，含其他分页）</summary><button type="button" disabled={blocked} onClick={()=>onSelection([])}>清空已选</button><ul>{selected.map(row => <li key={row.id}><span>{row.title}</span><button type="button" aria-label={`取消选择${row.title}`} disabled={blocked} onClick={() => onSelection(selected.filter(item => item.id !== row.id))}>取消选择</button></li>)}</ul></details>}
    {loading && <p role="status">正在读取私人作品…</p>}{error && <div role="alert" className="plan-error">{error}<button type="button" onClick={() => setRetry(n => n+1)}>重试候选列表</button></div>}
    {!loading && !error && <ul className="curation-picker-results">{data?.results.map(row => {
      const included = members.has(row.id), checked = selected.some(item => item.id === row.id), cover = row.anime.poster_image || row.anime.poster_url
      return <li key={row.id}><label><input type={relink ? 'radio' : 'checkbox'} name="curation-candidate" checked={checked} disabled={blocked || included || !checked && !relink && selected.length >= 100} onChange={() => toggle(row)} aria-label={`选择${row.anime.chinese_title}`} /><span className="curation-picker-cover"><CurationArtwork source={cover}/></span><span className="curation-picker-copy"><strong>{row.anime.chinese_title}</strong>{row.anime.japanese_title&&<small className="curation-original-title">{row.anime.japanese_title}</small>}<small>{WATCH_LABELS[row.watch_status]} · {row.watched_eps}/{row.total_eps||'?'} 集{row.anime.airing_period&&` · ${row.anime.airing_period}`}{included ? ' · 已收录，不可重复勾选' : ''}</small></span></label></li>
    })}{data?.count === 0 && <li>没有匹配作品。可以调整筛选，或先在手账中添加作品。</li>}</ul>}
    <div className="plan-pagination"><button type="button" disabled={blocked || loading || page <= 1} onClick={() => setPage(n => n-1)}>上一页</button><span>{page} / {Math.max(1, Math.ceil((data?.count || 0)/12))}</span><button type="button" disabled={blocked || loading || !data || page*12 >= data.count} onClick={() => setPage(n => n+1)}>下一页</button></div>
  </>
}
