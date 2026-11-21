import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import useSeriesSession, { seriesSession } from '../hooks/useSeriesSession.js'
import { lockDialogBody } from '../utils/dialogBodyLock.js'
import { defaultRatingPreferences, ratingPreferencesError, ratingPreset, histogramHighlight, highlightRange, RATING_COLORS, STAT_CARDS } from '../utils/ratingPreferences.js'
import PersonalScore from './PersonalScore.jsx'
import './RatingPreferencesModal.css'

const errorText = error => error?.response?.status === 429 ? '操作较频繁，请稍后重试。不会自动重复保存。' : error?.response?.data?.detail || Object.values(error?.response?.data || {}).flat().join('；') || error.message || '连接暂时中断，请重试。'
const copy = value => JSON.parse(JSON.stringify(value))
const presets = [['default', '项目默认'], ['bangumi', 'Bangumi 整数习惯'], ['numbers', '仅数字']]

export default function RatingPreferencesModal({ onClose, onSaved }) {
  const activeSession = useSeriesSession(), session = useRef(seriesSession()), live = useRef(true), gate = useRef(false), operation = useRef(null), dialog = useRef(null)
  const [draft, setDraft] = useState(defaultRatingPreferences), [original, setOriginal] = useState(null), [revision, setRevision] = useState(null)
  const [histogram, setHistogram] = useState([]), [loading, setLoading] = useState(true), [saving, setSaving] = useState(false)
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [tab, setTab] = useState('tiers'), [discard, setDiscard] = useState(false)
  const [previewScore, setPreviewScore] = useState(9.5), [previewOpen, setPreviewOpen] = useState(() => window.innerWidth >= 768)
  const lastValid = useRef(defaultRatingPreferences()), close = useRef(onClose), saved = useRef(onSaved)
  close.current = onClose; saved.current = onSaved
  const current = () => live.current && session.current === seriesSession()
  const dirty = original !== null && JSON.stringify(draft) !== JSON.stringify(original)
  const validation = ratingPreferencesError(draft)
  if (!validation) lastValid.current = draft
  const preview = lastValid.current
  const requestClose = () => { if (!saving) { if (dirty) setDiscard(true); else close.current() } }
  const load = async () => {
    operation.current?.abort(); const controller = new AbortController(); operation.current = controller
    setLoading(true); setError(''); setNotice('')
    try {
      const { data } = await api.get('rating-preferences/', { signal: controller.signal })
      if (!current() || controller.signal.aborted) return
      if (ratingPreferencesError(data.preferences) || !Number.isSafeInteger(data.revision) || !Array.isArray(data.histogram)) throw new Error('配置读取不完整，请重新读取。')
      setDraft(copy(data.preferences)); setOriginal(copy(data.preferences)); setRevision(data.revision); setHistogram(data.histogram); setDiscard(false)
    } catch (reason) { if (current() && reason.code !== 'ERR_CANCELED') setError(errorText(reason)) }
    finally { if (current() && !controller.signal.aborted) setLoading(false) }
  }
  useEffect(() => {
    live.current = true
    const release = lockDialogBody(document), previous = document.activeElement
    const query = window.matchMedia('(max-width: 767px)'), resize = () => setPreviewOpen(!query.matches)
    dialog.current.showModal(); load(); query.addEventListener('change', resize)
    return () => {
      live.current = false; operation.current?.abort(); dialog.current?.close(); release(); query.removeEventListener('change', resize)
      const visible = node => node?.isConnected && node.getClientRects().length && !node.closest('[hidden], [inert]')
      const target = previous !== document.body && visible(previous) ? previous : [...document.querySelectorAll('.journal-dropdown-trigger')].find(visible)
      target?.focus({ preventScroll: true })
    }
  }, [])
  useEffect(() => { if (activeSession !== session.current) { operation.current?.abort(); close.current() } }, [activeSession])
  const changeTier = (index, key, value) => setDraft(old => ({ ...old, tiers: old.tiers.map((tier, i) => i === index ? { ...tier, [key]: value } : tier) }))
  const changeStats = (key, value) => setDraft(old => ({ ...old, statistics: { ...old.statistics, [key]: value } }))
  const addTier = () => setDraft(old => {
    const rows = [...old.tiers].sort((a, b) => b.min - a.min)
    const intervals = rows.map((row, i) => ({ low: row.min, high: i === 0 ? 10 : rows[i-1].min })).sort((a, b) => (b.high-b.low) - (a.high-a.low))
    const gap = intervals.find(row => row.high-row.low >= .2)
    if (!gap || rows.length >= 10) return old
    const min = Math.round((gap.high+gap.low)*5)/10
    return { ...old, tiers: [...rows, { min, label: '新档位', stars: Math.min(old.star_max, Math.round(min/10*old.star_max)), color: 'blue', star_color: 'blue', effect: 'plain' }].sort((a, b) => b.min-a.min) }
  })
  const save = async () => {
    if (gate.current || loading || !original || validation || !dirty) return
    gate.current = true; setSaving(true); setError(''); setNotice('')
    const controller = new AbortController(); operation.current = controller
    try {
      const { data } = await api.patch('rating-preferences/', { preferences: draft, revision }, { signal: controller.signal })
      if (!current()) return
      setDraft(copy(data.preferences)); setOriginal(copy(data.preferences)); setRevision(data.revision); setHistogram(data.histogram)
      setNotice('已保存并应用。公开手账使用当前配置，新投稿会保存本次配置快照。')
      saved.current?.(data)
    } catch (reason) { if (current() && reason.code !== 'ERR_CANCELED') setError(errorText(reason)) }
    finally { gate.current = false; if (current()) setSaving(false) }
  }
  const disabled = loading || saving
  return <dialog ref={dialog} className="rating-preferences-backdrop" aria-modal="true" aria-labelledby="ratingPreferencesTitle" onCancel={event => { event.preventDefault(); requestClose() }} onKeyDown={event => { if (event.key === 'Escape') event.stopPropagation() }}>
    <section className="rating-preferences-modal">
      <header><div><span className="rating-workshop-sticker">MY RATING STYLE</span><h2 id="ratingPreferencesTitle">评分样式与统计</h2><p>自定义展示口径，保留原始 0–10 分评分。</p></div><button type="button" aria-label="关闭评分设置" disabled={saving} onClick={requestClose}>×</button></header>
      <div className="rating-preferences-content">
        <div className="rating-preferences-editor">
          {loading ? <p role="status">正在读取账号配置与全手账评分分布…</p> : <>
            <div className="rating-presets" role="group" aria-label="评分预设"><span>从预设开始</span>{presets.map(([key, text]) => <button type="button" disabled={disabled || !original} key={key} onClick={() => { setDraft(ratingPreset(key)); setNotice('预设已载入草稿，保存后才会应用。'); setDiscard(false) }}>{text}</button>)}</div>
            <nav className="rating-preferences-tabs" aria-label="评分设置分类">{[['tiers', '档位与星级'], ['statistics', '统计显示']].map(([key, text]) => <button type="button" key={key} aria-pressed={tab === key} onClick={() => setTab(key)}>{text}</button>)}</nav>
            {tab === 'tiers' ? <>
              <div className="rating-scale"><label><input type="checkbox" checked={draft.show_stars} disabled={disabled} onChange={event => setDraft(old => ({ ...old, show_stars: event.target.checked }))} />展示星级</label><label>星级上限<select aria-label="星级上限" value={draft.star_max} disabled={disabled} onChange={event => { const max = Number(event.target.value); setDraft(old => ({ ...old, star_max: max, tiers: old.tiers.map(tier => ({ ...tier, stars: Math.min(tier.stars, max) })) })) }}>{Array.from({ length: 10 }, (_, i) => <option key={i} value={i+1}>{i+1} 星</option>)}</select></label></div>
              <p className="rating-help">档位从高到低；起点包含，上限随上一档起点联动且不包含（最高档包含 10 分），避免重叠。调小星级上限会同步收拢超出的星数。</p>
              <div className="rating-tier-tools"><button type="button" disabled={disabled || draft.tiers.length >= 10 || Boolean(validation)} onClick={addTier}>＋ 添加档位</button><button type="button" disabled={disabled} onClick={() => setDraft(old => ({ ...old, tiers: [...old.tiers].sort((a, b) => b.min-a.min) }))}>按起点排序</button><span>{draft.tiers.length}/10 档</span></div>
              <fieldset disabled={disabled || !original} className="rating-tier-list"><legend className="rating-sr">评分档位</legend>{draft.tiers.map((tier, index) => <article key={index} className="rating-tier">
                <div className="rating-tier-heading"><strong>第 {index+1} 档</strong><span>{tier.min === '' ? '…' : tier.min} ≤ 分数 {index === 0 ? '≤ 10' : `< ${draft.tiers[index-1].min}`}</span><button type="button" disabled={index === draft.tiers.length-1 || disabled} aria-label={`删除第 ${index+1} 档`} onClick={() => setDraft(old => ({ ...old, tiers: old.tiers.filter((_, i) => i !== index) }))}>移除</button></div>
                <div className="rating-tier-fields"><label>档位名称<input aria-label={`第 ${index+1} 档名称`} value={tier.label} maxLength={16} onChange={event => changeTier(index, 'label', event.target.value)} /></label><label>起点（含）<input aria-label={`第 ${index+1} 档起点`} type="number" min="0" max="10" step="0.1" disabled={index === draft.tiers.length-1} value={tier.min} onChange={event => changeTier(index, 'min', event.target.value === '' ? '' : Number(event.target.value))} /></label><label>点亮星数<select aria-label={`第 ${index+1} 档星数`} value={tier.stars} onChange={event => changeTier(index, 'stars', Number(event.target.value))}>{Array.from({ length: draft.star_max+1 }, (_, i) => <option key={i} value={i}>{i} 星</option>)}</select></label>
                  {[['color', '数字配色'], ['star_color', '星级配色']].map(([key, text]) => <label key={key}>{text}<select aria-label={`第 ${index+1} 档${text}`} value={tier[key]} onChange={event => changeTier(index, key, event.target.value)}>{Object.entries(RATING_COLORS).map(([color, palette]) => <option value={color} key={color}>{palette.label}</option>)}</select></label>)}
                  <label>效果<select aria-label={`第 ${index+1} 档效果`} value={tier.effect} onChange={event => changeTier(index, 'effect', event.target.value)}><option value="plain">纯色</option><option value="gradient">渐变</option><option value="glow">柔和高亮</option></select></label></div>
              </article>)}</fieldset>
            </> : <fieldset disabled={disabled || !original} className="rating-statistics"><legend>自定义推荐统计</legend><p>按全手账评分统计，和当前筛选、分页无关。未评分排除，真实 0 分按范围计入。</p><label>统计名称<input aria-label="推荐统计名称" maxLength={16} value={draft.statistics.label} onChange={event => changeStats('label', event.target.value)} /></label><div className="rating-stat-range">{[['min', '最低分（含）'], ['max', '最高分（含）']].map(([key, text]) => <label key={key}>{text}<input type="number" step="0.1" min="0" max="10" aria-label={text} value={draft.statistics[key]} onChange={event => changeStats(key, event.target.value === '' ? '' : Number(event.target.value))} /></label>)}</div>
              <h3>显示哪些统计卡片</h3><div className="rating-card-options">{Object.entries(STAT_CARDS).map(([key, text]) => <label key={key}><input type="checkbox" checked={draft.statistics.cards.includes(key)} onChange={() => changeStats('cards', draft.statistics.cards.includes(key) ? draft.statistics.cards.filter(value => value !== key) : [...draft.statistics.cards, key])} />{text}</label>)}</div><p className="rating-help">至少保留一张。公开手账和共创大厅也按作者的选择展示。</p>
            </fieldset>}
          </>}
          {validation && <p className="rating-preferences-error" role="alert">{validation} 预览暂时保留最近一次有效草稿。</p>}
          {error && <div className="rating-preferences-error" role="alert"><p>{error}</p><button type="button" disabled={disabled} onClick={load}>{dirty ? '重新读取（替换草稿）' : '重新读取'}</button></div>}
          {notice && <p className="rating-preferences-notice" role="status">{notice}</p>}
        </div>
        <aside className="rating-preview"><button type="button" aria-expanded={previewOpen} aria-controls="ratingLivePreview" onClick={() => setPreviewOpen(value => !value)}><strong>实时预览</strong><span>{previewOpen ? '－' : '＋'}</span></button><p className="rating-preview-count">{preview.statistics.label} ({highlightRange(preview)}) · <strong>{histogramHighlight(histogram, preview)} 部</strong></p>
          <div id="ratingLivePreview" hidden={!previewOpen}><label className="rating-preview-input">试一个分数<input aria-label="预览分数" type="number" min="0" max="10" step="0.1" value={previewScore} onChange={event => setPreviewScore(event.target.value === '' ? '' : Number(event.target.value))} /></label><div className="rating-preview-feature"><PersonalScore score={previewScore} preferences={preview} suffix /></div>
            <div className="rating-preview-samples">{[10, 9.9, 9.5, 9, 8, 6, 0, null].map((score, index) => <div key={index}><span>{score === null ? '未评分' : `${score.toFixed(1)} 分示例`}</span><PersonalScore score={score} preferences={preview} /></div>)}</div>
            <h3>选中的统计卡片</h3><div className="rating-preview-cards">{preview.statistics.cards.map(key => <span key={key}>{key === 'highlight' ? `${preview.statistics.label}：${histogramHighlight(histogram, preview)} 部` : STAT_CARDS[key]}</span>)}</div><p className="rating-help">统计预览基于打开设置时的全手账评分；保存后重新统计。预设和编辑只影响草稿，点击保存后才对外生效。</p>
          </div>
        </aside>
      </div>
      <footer>{discard && <div className="rating-discard" role="group" aria-label="放弃评分草稿"><p>尚有未保存修改。关闭后会丢弃本页草稿。</p><button type="button" onClick={() => setDiscard(false)}>继续编辑</button><button type="button" onClick={() => close.current()}>放弃修改并关闭</button></div>}<div className="rating-preferences-actions"><span>{dirty ? '草稿未保存' : original ? '已与账号配置同步' : '配置尚未读取'}</span><button type="button" disabled={saving} onClick={requestClose}>关闭</button><button type="button" className="rating-save" disabled={disabled || !original || Boolean(validation) || !dirty || discard} onClick={save}>{saving ? '正在保存…' : '保存并应用'}</button></div></footer>
    </section>
  </dialog>
}
