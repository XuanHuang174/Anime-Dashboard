import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import useSeriesSession, { seriesSession } from '../hooks/useSeriesSession.js'
import { lockDialogBody } from '../utils/dialogBodyLock.js'
import { defaultRatingPreferences, normalizeRatingPreferences, restoreClassicAppearance, ratingPreferencesError, ratingPreset, histogramHighlight, highlightRange, ratingStyle, RATING_COLORS, STAT_CARDS } from '../utils/ratingPreferences.js'
import PersonalScore, { RatingPreferencesContext } from './PersonalScore.jsx'
import StatsSummary from './StatsSummary.jsx'
import './RatingPreferencesModal.css'

const errorText = error => error?.response?.status === 429 ? '操作较频繁，请稍后重试。不会自动重复保存。' : error?.response?.data?.detail || Object.values(error?.response?.data || {}).flat().join('；') || error.message || '连接暂时中断，请重试。'
const copy = value => JSON.parse(JSON.stringify(value))
const presets = [['default', '经典默认', '五颗星 · 经典幻彩与金星'], ['bangumi', 'Bangumi 整数习惯', '按整数分档 · 8 分起统计'], ['numbers', '仅数字', '保留经典数字 · 隐藏星级']]
const numeric = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 10 && Math.abs(value * 10 - Math.round(value * 10)) < 1e-8
const validName = value => value.trim() && [...value].length <= 16 && !/[\u0000-\u001f]/.test(value)
const rangeLabel = (rows, index) => `${rows[index].min === '' ? '…' : rows[index].min} ≤ 分数 ${index ? `< ${rows[index - 1].min || 0}` : '≤ 10'}`
function fieldIssues(config) {
  const issues = []
  config.tiers.forEach((row, i) => {
    if (!validName(row.label)) issues.push({ field: `tier-${i}-label`, index: i, tab: 'tiers', text: '请输入 1–16 字的档位名称。' })
    if (!numeric(row.min) || (i && row.min >= config.tiers[i - 1].min) || (i === config.tiers.length - 1 && row.min !== 0)) issues.push({ field: `tier-${i}-min`, index: i, tab: 'tiers', text: '起点需为 0–10 的一位小数，按从高到低排列且不重复；最低档固定为 0。' })
  })
  if (!validName(config.statistics.label)) issues.push({ field: 'statistics-label', tab: 'statistics', text: '请输入 1–16 字的统计名称。' })
  for (const key of ['min', 'max']) if (!numeric(config.statistics[key]) || config.statistics.min > config.statistics.max) issues.push({ field: `statistics-${key}`, tab: 'statistics', text: '请输入 0–10 的一位小数，下限不能高于上限。' })
  if (!config.statistics.cards.length) issues.push({ field: 'statistics-cards', tab: 'statistics', text: '至少保留一张统计卡片。' })
  return issues
}
function ColorPicker({ tier, property, index, onChange }) {
  const text = property === 'color' ? '数字配色' : '星级配色'
  return <div className="rating-color-picker"><label>{text}<select aria-label={`第 ${index + 1} 档${text}`} value={tier[property]} onChange={event => onChange(event.target.value)}>{Object.entries(RATING_COLORS).map(([key, palette]) => <option value={key} key={key}>{palette.label}</option>)}</select></label>
    <div className="rating-swatches" role="group" aria-label={`第 ${index + 1} 档${text}色板`}>{Object.entries(RATING_COLORS).map(([key, palette]) => <button type="button" key={key} aria-label={`${text}：${palette.label}`} aria-pressed={tier[property] === key} title={palette.label} style={{ '--swatch': palette.gradient ? `linear-gradient(to right, ${(property === 'star_color' ? palette.starGradient : palette.gradient).join(',')})` : palette.color }} onClick={() => onChange(key)}><span aria-hidden="true">{tier[property] === key ? '✓' : ''}</span></button>)}</div>
  </div>
}
function ContextPreview({ config, score, context, caption }) {
  return <div className={`rating-context-card rating-context-card--${context}`}><span className="rating-preview-caption">{caption}</span><div className="rating-context-example"><div className="rating-demo-poster" aria-hidden="true"><i className="fa-solid fa-film" /></div><div className="rating-demo-title"><strong>作品标题</strong><span>私人手账 · 评分展示</span></div><PersonalScore score={score} preferences={config} suffix={context !== 'grid'} variant={context} /></div></div>
}

export default function RatingPreferencesModal({ onClose, onSaved }) {
  const activeSession = useSeriesSession(), session = useRef(seriesSession()), live = useRef(true), gate = useRef(false), operation = useRef(null), dialog = useRef(null), exitTimer = useRef(null)
  const [draft, setDraft] = useState(defaultRatingPreferences), [original, setOriginal] = useState(null), [revision, setRevision] = useState(null)
  const [histogram, setHistogram] = useState([]), [journalStats, setJournalStats] = useState(null), [loading, setLoading] = useState(true), [saving, setSaving] = useState(false)
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [tab, setTab] = useState('tiers'), [discard, setDiscard] = useState(false), [closing, setClosing] = useState(false)
  const [activeTier, setActiveTier] = useState(0), [previewContext, setPreviewContext] = useState('list')
  const [previewScore, setPreviewScore] = useState(9.5), [previewOpen, setPreviewOpen] = useState(() => window.innerWidth >= 768)
  const lastValid = useRef(defaultRatingPreferences()), close = useRef(onClose), saved = useRef(onSaved)
  close.current = onClose; saved.current = onSaved
  const current = () => live.current && session.current === seriesSession()
  const dirty = original !== null && JSON.stringify(draft) !== JSON.stringify(original)
  const validation = ratingPreferencesError(draft), issues = fieldIssues(draft)
  if (!validation) lastValid.current = draft
  const preview = lastValid.current
  const finishClose = () => {
    if (exitTimer.current) return
    setClosing(true)
    exitTimer.current = setTimeout(() => close.current(), window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 140)
  }
  const requestClose = () => { if (!saving) { if (dirty) setDiscard(true); else finishClose() } }
  const load = async () => {
    operation.current?.abort(); const controller = new AbortController(); operation.current = controller
    setLoading(true); setError(''); setNotice('')
    try {
      const [settings, statistics] = await Promise.allSettled([api.get('rating-preferences/', { signal: controller.signal }), api.get('records/stats/', { signal: controller.signal })])
      if (!current() || controller.signal.aborted) return
      if (settings.status === 'rejected') throw settings.reason
      const { data } = settings.value
      if (ratingPreferencesError(data.preferences) || !Number.isSafeInteger(data.revision) || !Array.isArray(data.histogram)) throw new Error('配置读取不完整，请重新读取。')
      const preferences = normalizeRatingPreferences(data.preferences)
      setDraft(copy(preferences)); setOriginal(copy(preferences)); setRevision(data.revision); setHistogram(data.histogram); setDiscard(false); setActiveTier(0)
      setJournalStats(statistics.status === 'fulfilled' ? statistics.value.data : null)
    } catch (reason) { if (current() && reason.code !== 'ERR_CANCELED') setError(errorText(reason)) }
    finally { if (current() && !controller.signal.aborted) setLoading(false) }
  }
  useEffect(() => {
    live.current = true
    const release = lockDialogBody(document), previous = document.activeElement
    const query = window.matchMedia('(max-width: 767px)'), resize = () => setPreviewOpen(!query.matches)
    dialog.current.showModal(); load(); query.addEventListener('change', resize)
    return () => {
      live.current = false; clearTimeout(exitTimer.current); operation.current?.abort(); dialog.current?.close(); release(); query.removeEventListener('change', resize)
      const visible = node => node?.isConnected && node.getClientRects().length && !node.closest('[hidden], [inert]')
      const target = previous !== document.body && visible(previous) ? previous : [...document.querySelectorAll('.journal-dropdown-trigger')].find(visible)
      target?.focus({ preventScroll: true })
    }
  }, [])
  useEffect(() => { if (activeSession !== session.current) { operation.current?.abort(); close.current() } }, [activeSession])
  const changeTier = (index, key, value) => setDraft(old => ({ ...old, tiers: old.tiers.map((tier, i) => i === index ? { ...tier, [key]: value } : tier) }))
  const changeStats = (key, value) => setDraft(old => ({ ...old, statistics: { ...old.statistics, [key]: value } }))
  const selectTier = index => { setActiveTier(activeTier === index ? -1 : index); if (numeric(draft.tiers[index].min)) setPreviewScore(draft.tiers[index].min) }
  const focusIssue = () => {
    const issue = issues[0]; if (!issue) return
    setTab(issue.tab); if (issue.index !== undefined) setActiveTier(issue.index)
    requestAnimationFrame(() => requestAnimationFrame(() => document.getElementById(`rating-${issue.field}`)?.focus()))
  }
  const fieldError = id => issues.find(issue => issue.field === id)
  const errorProps = id => ({ id: `rating-${id}`, 'aria-invalid': Boolean(fieldError(id)), 'aria-describedby': fieldError(id) ? `rating-error-${id}` : undefined })
  const fieldMessage = id => fieldError(id) && <small className="rating-field-error" id={`rating-error-${id}`}>{fieldError(id).text}</small>
  const addTier = () => {
    const rows = [...draft.tiers].sort((a, b) => b.min - a.min)
    const intervals = rows.map((row, i) => ({ low: row.min, high: i === 0 ? 10 : rows[i - 1].min })).sort((a, b) => (b.high - b.low) - (a.high - a.low))
    const gap = intervals.find(row => row.high - row.low >= .2)
    if (!gap || rows.length >= 10) return
    const min = Math.round((gap.high + gap.low) * 5) / 10
    const tiers = [...rows, { min, label: '新档位', stars: Math.min(draft.star_max, Math.round(min / 10 * draft.star_max)), color: 'blue', star_color: 'blue', effect: 'plain' }].sort((a, b) => b.min - a.min)
    setDraft(old => ({ ...old, tiers })); setActiveTier(tiers.findIndex(row => row.min === min)); setPreviewScore(min)
  }
  const save = async () => {
    if (gate.current || loading || !original || validation || !dirty || closing) return
    gate.current = true; setSaving(true); setError(''); setNotice('')
    const controller = new AbortController(); operation.current = controller
    try {
      const { data } = await api.patch('rating-preferences/', { preferences: draft, revision }, { signal: controller.signal })
      if (!current()) return
      const preferences = normalizeRatingPreferences(data.preferences)
      setDraft(copy(preferences)); setOriginal(copy(preferences)); setRevision(data.revision); setHistogram(data.histogram)
      setNotice('已保存并应用。公开手账使用当前配置，新投稿会保存本次配置快照。')
      saved.current?.(data)
    } catch (reason) { if (current() && reason.code !== 'ERR_CANCELED') setError(errorText(reason)) }
    finally { gate.current = false; if (current()) setSaving(false) }
  }
  const disabled = loading || saving || closing
  const selectedPreset = presets.find(([key]) => JSON.stringify(ratingPreset(key)) === JSON.stringify(draft))?.[0]
  const ratedCount = histogram.reduce((sum, row) => sum + (row.score === null ? 0 : row.count), 0)
  const statPreview = journalStats ? { ...journalStats, masterpiece_count: histogramHighlight(histogram, preview), average_score: ratedCount ? (histogram.reduce((sum, row) => sum + (row.score === null ? 0 : Number(row.score) * row.count), 0) / ratedCount).toFixed(2) : 'N/A' } : null
  return <dialog ref={dialog} className={`rating-preferences-backdrop ${closing ? 'is-closing' : ''}`} aria-modal="true" aria-labelledby="ratingPreferencesTitle" onCancel={event => { event.preventDefault(); requestClose() }} onKeyDown={event => { if (event.key === 'Escape') event.stopPropagation() }}>
    <section className="rating-preferences-modal" aria-busy={loading || saving}>
      <header><div><span className="rating-workshop-sticker">MY RATING STYLE</span><h2 id="ratingPreferencesTitle">评分样式与统计</h2><p>你的分数，你的表达。只调整展示，保留原始 0–10 分评分。</p></div><button type="button" aria-label="关闭评分设置" disabled={saving} onClick={requestClose}>×</button></header>
      <div className="rating-preferences-content">
        <div className="rating-preferences-editor">
          {loading ? <div className="rating-loading" role="status"><i className="fa-solid fa-circle-notch fa-spin" aria-hidden="true" />正在读取账号配置与全手账评分分布…</div> : <>
            <div className="rating-section-heading"><h3>从预设开始</h3><span>{selectedPreset ? '预设样式' : '自定义样式'}</span></div>
            <div className="rating-presets" role="group" aria-label="评分预设">{presets.map(([key, text, note]) => <button type="button" aria-label={text} aria-pressed={selectedPreset === key} disabled={disabled || !original} key={key} onClick={() => { setDraft(ratingPreset(key)); setActiveTier(0); setNotice('预设已载入草稿，保存后才会应用。'); setDiscard(false) }}><strong>{text}</strong><PersonalScore score={9.9} preferences={ratingPreset(key)} /><small>{note}</small></button>)}</div>
            <nav className="rating-preferences-tabs" aria-label="评分设置分类">{[['tiers', '档位与星级'], ['statistics', '统计显示']].map(([key, text]) => <button type="button" key={key} aria-pressed={tab === key} onClick={() => setTab(key)}>{text}</button>)}</nav>
            {tab === 'tiers' ? <>
              <div className="rating-scale"><label><input type="checkbox" checked={draft.show_stars} disabled={disabled} onChange={event => setDraft(old => ({ ...old, show_stars: event.target.checked }))} />展示星级</label><label>星级上限<select aria-label="星级上限" value={draft.star_max} disabled={disabled || !original} onChange={event => { const max = Number(event.target.value); setDraft(old => ({ ...old, star_max: max, tiers: old.tiers.map(tier => ({ ...tier, stars: Math.min(tier.stars, max) })) })) }}>{Array.from({ length: 10 }, (_, i) => <option key={i} value={i + 1}>{i + 1} 星</option>)}</select></label></div>
              <details className="rating-rules"><summary>档位如何划分？</summary><p>起点包含，上限随上一档起点联动且不包含；最高档包含 10 分，最低档从 0 分开始。调小星级上限会收拢超出的星数。</p></details>
              <div className="rating-tier-tools"><button type="button" disabled={disabled || !original || draft.tiers.length >= 10 || Boolean(validation)} onClick={addTier}>＋ 添加档位</button><button type="button" disabled={disabled || !original} onClick={() => { setDraft(old => ({ ...old, tiers: [...old.tiers].sort((a, b) => b.min - a.min) })); setActiveTier(0) }}>按起点排序</button><span>{draft.tiers.length}/10 档</span><button type="button" disabled={disabled || !original} onClick={() => { setDraft(old => restoreClassicAppearance(old)); setNotice('经典外观已载入草稿，名称、分数区间、星数和统计选择均已保留。') }}>恢复经典外观</button></div>
              <fieldset disabled={disabled || !original} className="rating-tier-list"><legend className="rating-sr">评分档位</legend>{draft.tiers.map((tier, index) => <article key={index} className={`rating-tier ${activeTier === index ? 'is-open' : ''} ${issues.some(issue => issue.index === index) ? 'has-error' : ''}`}>
                <button type="button" className="rating-tier-heading" aria-expanded={activeTier === index} aria-controls={`rating-tier-panel-${index}`} aria-label={`编辑第 ${index + 1} 档：${tier.label}`} onClick={() => selectTier(index)}><span className="rating-tier-index">{String(index + 1).padStart(2, '0')}</span><span className="rating-tier-title"><strong>{tier.label || '未命名档位'}</strong><small>{rangeLabel(draft.tiers, index)}</small></span><PersonalScore score={numeric(tier.min) ? tier.min : 0} preferences={preview} /><span className="rating-chevron" aria-hidden="true">⌄</span></button>
                <div className="rating-tier-panel" id={`rating-tier-panel-${index}`} inert={activeTier !== index ? true : undefined}><div><div className="rating-tier-inner">
                  <div className="rating-tier-fields"><label>档位名称<input {...errorProps(`tier-${index}-label`)} aria-label={`第 ${index + 1} 档名称`} value={tier.label} maxLength={16} onChange={event => changeTier(index, 'label', event.target.value)} />{fieldMessage(`tier-${index}-label`)}</label><label>起点（含）<input {...errorProps(`tier-${index}-min`)} aria-label={`第 ${index + 1} 档起点`} type="number" min="0" max="10" step="0.1" disabled={index === draft.tiers.length - 1} value={tier.min} onChange={event => changeTier(index, 'min', event.target.value === '' ? '' : Number(event.target.value))} />{fieldMessage(`tier-${index}-min`)}</label><label>点亮星数<select aria-label={`第 ${index + 1} 档星数`} value={tier.stars} disabled={!draft.show_stars} onChange={event => changeTier(index, 'stars', Number(event.target.value))}>{Array.from({ length: draft.star_max + 1 }, (_, i) => <option key={i} value={i}>{i} 星</option>)}</select></label></div>
                  <details className="rating-appearance" open={activeTier === index ? true : undefined}><summary>配色与效果 <span>{RATING_COLORS[tier.color].label} · {RATING_COLORS[tier.star_color].label}</span></summary><div className="rating-appearance-fields">{['color', 'star_color'].map(property => <ColorPicker key={property} tier={tier} property={property} index={index} onChange={value => changeTier(index, property, value)} />)}<label>效果<select aria-label={`第 ${index + 1} 档效果`} value={tier.effect} onChange={event => changeTier(index, 'effect', event.target.value)}><option value="plain">纯色</option><option value="gradient">渐变</option><option value="glow">柔和高亮</option></select></label><div className="rating-effect-example"><span style={ratingStyle(tier)}>Aa 9.9</span><small>数字与星级各用所选配色</small></div></div></details>
                  <div className="rating-tier-bottom"><small>{index === draft.tiers.length - 1 ? '基础档位保留 0 分，不能删除。' : '删除后，该分数区间并入下一档。'}</small><button type="button" disabled={index === draft.tiers.length - 1 || disabled} aria-label={`删除第 ${index + 1} 档`} onClick={() => { setDraft(old => ({ ...old, tiers: old.tiers.filter((_, i) => i !== index) })); setActiveTier(Math.max(0, index - 1)) }}>移除</button></div>
                </div></div></div>
              </article>)}</fieldset>
            </> : <fieldset disabled={disabled || !original} className="rating-statistics"><legend>自定义推荐统计</legend><p>按全手账评分统计，和当前筛选、分页无关。未评分排除，真实 0 分按范围计入。</p><label>统计名称<input {...errorProps('statistics-label')} aria-label="推荐统计名称" maxLength={16} value={draft.statistics.label} onChange={event => changeStats('label', event.target.value)} />{fieldMessage('statistics-label')}</label><div className="rating-stat-range">{[['min', '最低分（含）'], ['max', '最高分（含）']].map(([key, text]) => <label key={key}>{text}<input {...errorProps(`statistics-${key}`)} type="number" step="0.1" min="0" max="10" aria-label={text} value={draft.statistics[key]} onChange={event => changeStats(key, event.target.value === '' ? '' : Number(event.target.value))} />{fieldMessage(`statistics-${key}`)}</label>)}</div>
              <h3>显示哪些统计卡片</h3><div {...errorProps('statistics-cards')} tabIndex={-1} className="rating-card-options">{Object.entries(STAT_CARDS).map(([key, text]) => <label key={key}><input type="checkbox" checked={draft.statistics.cards.includes(key)} onChange={() => changeStats('cards', draft.statistics.cards.includes(key) ? draft.statistics.cards.filter(value => value !== key) : [...draft.statistics.cards, key])} />{text}</label>)}</div>{fieldMessage('statistics-cards')}<p className="rating-help">卡片顺序与手账一致；公开手账和共创大厅也按作者选择展示。</p>
            </fieldset>}
          </>}
          {error && <div className="rating-preferences-error" role="alert"><p>{error}</p><button type="button" disabled={disabled} onClick={load}>{dirty ? '重新读取（替换草稿）' : '重新读取'}</button></div>}
          {notice && <p className="rating-preferences-notice" role="status">{notice}</p>}
        </div>
        <aside className="rating-preview"><button type="button" className="rating-preview-toggle" aria-expanded={previewOpen} aria-controls="ratingLivePreview" onClick={() => setPreviewOpen(value => !value)}><strong>实时预览 <small>{dirty ? '草稿效果' : '当前效果'}</small></strong><span>{previewOpen ? '－' : '＋'}</span></button><p className="rating-preview-count">{loading ? '正在读取评分分布…' : <>{preview.statistics.label} ({highlightRange(preview)}) · <strong>{histogramHighlight(histogram, preview)} 部</strong></>}</p>
          <div id="ratingLivePreview" hidden={!previewOpen}>
            <nav className="rating-preview-contexts" aria-label="预览展示场景">{[['list', '列表'], ['grid', '海报墙'], ['detail', '详情']].map(([key, text]) => <button type="button" key={key} aria-pressed={previewContext === key} onClick={() => setPreviewContext(key)}>{text}</button>)}</nav>
            <label className="rating-preview-input">试一个分数<input aria-label="预览分数" type="number" min="0" max="10" step="0.1" value={previewScore ?? ''} placeholder="未评分" onChange={event => { const value = event.target.value; setPreviewScore(value === '' ? '' : Math.min(10, Math.max(0, Number(value)))) }} /></label>
            <div className="rating-preview-comparison"><ContextPreview config={original || defaultRatingPreferences()} score={previewScore} context={previewContext} caption="当前已保存" /><div className="rating-preview-feature"><ContextPreview config={preview} score={previewScore} context={previewContext} caption={dirty ? '修改后 · 待保存' : '草稿预览'} /></div></div>
            {validation && <p className="rating-help rating-preview-warning">草稿有待修正项，预览保留最近一次有效配置。</p>}
            <div className="rating-preview-samples" role="group" aria-label="边界与未评分示例">{[10, 9.9, 9.5, 9, 8, 6, 0, null].map((score, index) => <button type="button" key={index} aria-pressed={previewScore === score} onClick={() => setPreviewScore(score)}><span>{score === null ? '未评分' : `${score.toFixed(1)} 分`}</span><PersonalScore score={score} preferences={preview} variant="grid" /></button>)}</div>
            <h3>统计卡片预览</h3><div className="rating-preview-statistics">{statPreview ? <RatingPreferencesContext.Provider value={preview}><StatsSummary statsData={statPreview} theme="memphis" idPrefix="rating-preview-" /></RatingPreferencesContext.Provider> : <p className="rating-help">{loading ? '正在读取统计…' : '其他统计暂未读取，可重新打开设置重试；推荐数量以上方评分分布为准。'}</p>}</div><p className="rating-help">预览基于打开时的全手账数据。保存后对外生效；已有精选投稿保留原快照。</p>
          </div>
        </aside>
      </div>
      <footer>{discard && <div className="rating-discard" role="group" aria-label="放弃评分草稿"><p>尚有未保存修改。关闭后会丢弃本页草稿。</p><button type="button" onClick={() => setDiscard(false)}>继续编辑</button><button type="button" onClick={finishClose}>放弃修改并关闭</button></div>}<div className="rating-preferences-actions"><span role="status">{validation ? `有 ${issues.length || 1} 项待修正` : dirty ? '草稿未保存' : original ? '已与账号配置同步' : '配置尚未读取'}</span>{validation && <button type="button" className="rating-locate-error" onClick={focusIssue}>定位问题</button>}<button type="button" disabled={saving} onClick={requestClose}>关闭</button><button type="button" className="rating-save" disabled={disabled || !original || Boolean(validation) || !dirty || discard} onClick={save}>{saving ? '正在保存…' : '保存并应用'}</button></div>{validation && <p className="rating-validation-summary" role="alert">{validation}</p>}</footer>
    </section>
  </dialog>
}
