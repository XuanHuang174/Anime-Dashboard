import { ANIME_TYPES, normalizeTypeSelection } from '../../utils/animeTypeSelection.js'
import './AnimeTypeFilter.css'

export default function AnimeTypeFilter({ value, onChange }) {
  const selected = value === 'all' ? [] : value.split(',')
  const toggle = type => {
    const next = selected.includes(type) ? selected.filter(item => item !== type) : [...selected, type]
    onChange(next.length ? normalizeTypeSelection(next.join(',')) : 'all')
  }
  return <fieldset className="rec-type-filter"><legend>作品类型</legend>
    <div className="rec-type-options">
      <button type="button" role="checkbox" aria-checked={value === 'all'} onClick={() => onChange('all')}>全部类型</button>
      {ANIME_TYPES.map(type => <button key={type} type="button" role="checkbox" aria-checked={selected.includes(type)} onClick={() => toggle(type)}>{selected.includes(type) && <span aria-hidden="true">✓ </span>}{type}</button>)}
    </div><p>可组合选择；全部类型包含尚未标明类型的作品。</p>
  </fieldset>
}
