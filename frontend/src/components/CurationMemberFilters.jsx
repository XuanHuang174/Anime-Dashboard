import SearchableSelect from './SearchableSelect.jsx'
import { WATCH_LABELS } from '../utils/watchHistory.js'

export const emptyMemberFilters = { search:'', status:'all', tag:'all', year:'all', availability:'all' }

export default function CurationMemberFilters({draft,onChange,onApply,onReset,options,disabled}) {
  const field=(key,value)=>onChange({...draft,[key]:value})
  return <form className="plan-controls curation-member-filters" aria-label="筛选片单作品" onSubmit={event=>{event.preventDefault();onApply()}}>
    <div className="curation-member-basic"><label>搜索片单内作品<input aria-label="搜索片单内作品" placeholder="中文或日文名称" maxLength={100} value={draft.search} disabled={disabled} onChange={e=>field('search',e.target.value)}/></label>
      <label>观看状态<select aria-label="片单内观看状态" value={draft.status} disabled={disabled} onChange={e=>field('status',e.target.value)}><option value="all">全部状态</option>{Object.entries(WATCH_LABELS).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
      <div className="plan-actions"><button type="submit" disabled={disabled}>筛选作品</button><button type="button" disabled={disabled} onClick={onReset}>重置作品筛选</button></div></div>
    <details className="curation-advanced"><summary>更多筛选 · 标签、年份与失效条目</summary><div className="curation-member-advanced">
      <div className="curation-select-field"><label htmlFor="curation-member-tag">题材标签</label><SearchableSelect id="curation-member-tag" label="片单内题材标签" value={draft.tag} disabled={disabled} onChange={value=>field('tag',value)} options={options?.tags||[]} allOption={{value:'all',label:'全部标签'}} searchPlaceholder="搜索片单内标签"/></div>
      <div className="curation-select-field"><label htmlFor="curation-member-year">放送年份</label><SearchableSelect id="curation-member-year" label="片单内放送年份" value={draft.year} disabled={disabled} onChange={value=>field('year',value)} options={options?.years||[]} allOption={{value:'all',label:'全部年份'}} searchPlaceholder="搜索片单内年份"/></div>
      <label>条目情况<select aria-label="片单条目情况" value={draft.availability} disabled={disabled} onChange={e=>field('availability',e.target.value)}><option value="all">全部条目</option><option value="available">可用作品</option><option value="unavailable">失效条目</option></select></label>
    </div></details>
  </form>
}
