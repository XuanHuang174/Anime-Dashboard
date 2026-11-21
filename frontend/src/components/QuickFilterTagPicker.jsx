export default function QuickFilterTagPicker({ field, form, availableTags, query, onSearch, onToggle, onClear, disabled, onTitleChange }) {
  const include = field === 'tags', label = include ? '包含' : '排除'
  const opposite = form[include ? 'exclude_tags' : 'tags'], selected = form[field]
  const candidates = [...new Set(availableTags)].filter(tag => tag !== '轩皇力推' && !selected.includes(tag) && tag.toLowerCase().includes(query.trim().toLowerCase())).sort()
  return <section className={`quick-workshop-tag-panel quick-workshop-tag-panel--${field}`} aria-label={`${label}条件`}>
    <h4><i className={`fa-solid ${include ? 'fa-circle-plus' : 'fa-ban'}`} aria-hidden="true" /> {label}条件</h4>
    <fieldset><legend>{label}标签（最多 8 个）</legend>
      <div className="quick-selected-heading"><strong>已{label} {selected.length}/8</strong><button type="button" disabled={disabled || !selected.length} onClick={onClear} aria-label={`清空${label}标签`}>清空</button></div>
      <div className="quick-selected-tags" aria-label={`已${label}标签`}>{selected.map(tag => <button type="button" key={tag} aria-pressed="true" aria-label={`取消${label}：${tag}`} title={`点击取消${label}`} disabled={disabled} onClick={() => onToggle(field, tag)}><i className={`fa-solid ${include ? 'fa-check' : 'fa-ban'}`} aria-hidden="true" /> {tag}</button>)}{!selected.length && <small>尚未选择{label}标签</small>}</div>
      <input type="search" aria-label={`搜索${label}标签`} placeholder="搜索标签…" value={query} onChange={event => onSearch(event.target.value)} />
      <div className="quick-workshop-tags">{candidates.map(tag => {
        const move = opposite.includes(tag)
        return <button key={tag} type="button" aria-pressed="false" aria-label={move ? `移到${label}：${tag}` : `+ ${tag}`} disabled={disabled || selected.length >= 8} title={selected.length >= 8 ? '已达到 8 个标签上限，请先取消一个标签。' : move ? `从另一侧移到${label}` : `添加到${label}`} onClick={() => onToggle(field, tag)}><i className={`fa-solid ${move ? 'fa-right-left' : 'fa-plus'}`} aria-hidden="true" /> {tag}{move && <small>改为{label}</small>}</button>
      })}{!candidates.length && <p>{query ? '没有匹配标签，已选项保留在上方。' : '没有更多可选标签。'}</p>}</div>
    </fieldset><label>{label}标题词（最多 4 个）<input value={form[include ? 'title_keywords' : 'exclude_title_keywords']} onChange={event => onTitleChange(include ? 'title_keywords' : 'exclude_title_keywords', event.target.value)} placeholder="逗号分隔；匹配中日文标题" /></label>
  </section>
}
