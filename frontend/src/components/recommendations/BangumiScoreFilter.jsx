import { SCORE_PRESETS } from '../../utils/browseScore.js'

export default function BangumiScoreFilter({ filters, onChange }) {
  return <div className="rec-score-filter"><label className="rec-field">Bangumi 评分
    <select aria-label="Bangumi 评分筛选" value={filters.minScore} onChange={event => onChange({ ...filters, minScore: event.target.value })}>
      {SCORE_PRESETS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select></label>
    {filters.minScore === 'custom' && <fieldset className="rec-year-range"><legend>自定义评分区间（包含上下限，不含未知评分）</legend>
      {[['scoreFrom', '评分下限'], ['scoreTo', '评分上限']].map(([key, label]) => <label className="rec-field" key={key}>{label}
        <input aria-label={label} type="number" inputMode="decimal" min="0.1" max="10" step="0.1" required placeholder={key === 'scoreFrom' ? '6.3' : '7.5'}
          value={filters[key] ?? ''} onChange={event => onChange({ ...filters, [key]: event.target.value })} />
      </label>)}
    </fieldset>}
  </div>
}
