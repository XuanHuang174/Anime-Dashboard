import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import useSeriesSession, { seriesSession } from '../hooks/useSeriesSession.js'
import { lockDialogBody } from '../utils/dialogBodyLock.js'
import { emptyQuickFilter, quickFilterPayload, quickFilterDraftError, quickFilterSummary, WATCH_STATES } from '../utils/quickFilter.js'
import './QuickFilterEditorModal.css'

const errorText = error => error?.response?.status === 429 ? '操作较频繁，请稍后重试，不会自动重复保存。' : (typeof error?.response?.data === 'object' && error.response.data ? Object.values(error.response.data).flat().join('；') : '') || error.message || '连接暂时中断，请重试。'
const editForm = filter => ({ ...emptyQuickFilter(), ...filter, title_keywords: (filter.title_keywords || []).join('，'), exclude_title_keywords: (filter.exclude_title_keywords || []).join('，'), year_from: filter.year_from ?? '', year_to: filter.year_to ?? '', score_min: filter.score_min ?? '', score_max: filter.score_max ?? '' })
// Only editable fields go to the API, including when opening an older saved combination.
const fieldsOnly = form => Object.fromEntries(Object.keys(emptyQuickFilter()).map(key => [key, form[key]]))

export default function QuickFilterEditorModal({ isOpen, onClose, availableTags, filters, onSaved, onDeleted, showToast, endpoint = 'quick-filters', description = '组合评分、状态、年份与标签条件，并保存到当前账号。' }) {
  const session = useSeriesSession(), owner = useRef(seriesSession()), dialog = useRef(null), live = useRef(false), gate = useRef(false), operation = useRef(null), exitTimer = useRef(null)
  const [form, setForm] = useState(emptyQuickFilter), [baseline, setBaseline] = useState(() => JSON.stringify(emptyQuickFilter())), [editingId, setEditingId] = useState(null)
  const [tab, setTab] = useState('basic'), [savedOpen, setSavedOpen] = useState(() => window.innerWidth >= 1024), [search, setSearch] = useState({ tags: '', exclude_tags: '' })
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [pending, setPending] = useState(null), [deleteTarget, setDeleteTarget] = useState(null), [closing, setClosing] = useState(false)
  const [preview, setPreview] = useState({ state: 'idle' }), [retry, setRetry] = useState(0)
  const dirty = JSON.stringify(form) !== baseline
  const validDraft = quickFilterDraftError(form, false)
  const payload = quickFilterPayload(form), payloadKey = JSON.stringify({ ...payload, name: payload.name || '未命名预览' })
  const current = () => live.current && owner.current === seriesSession()
  const disabled = busy || closing
  const reset = filter => {
    const next = filter ? fieldsOnly(editForm(filter)) : emptyQuickFilter()
    setForm(next); setBaseline(JSON.stringify(next)); setEditingId(filter?.id ?? null); setError(''); setPending(null); setDeleteTarget(null); setTab('basic'); setSearch({ tags: '', exclude_tags: '' })
  }
  const finishClose = () => {
    if (exitTimer.current) return
    setClosing(true)
    exitTimer.current = setTimeout(onClose, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 140)
  }
  const transition = action => { if (gate.current || closing) return; if (dirty) setPending({ run: action }); else action() }
  const requestClose = () => transition(finishClose)
  const closeRef = useRef(requestClose); closeRef.current = requestClose
  useEffect(() => {
    if (!isOpen) return
    owner.current = seriesSession(); live.current = true; gate.current = false; exitTimer.current = null
    setBusy(false); setClosing(false); reset(); dialog.current.showModal()
    const query = window.matchMedia('(max-width: 1023px)'), resize = () => setSavedOpen(!query.matches)
    resize(); query.addEventListener('change', resize)
    const previous = document.activeElement, release = lockDialogBody(document)
    return () => {
      live.current = false; operation.current?.abort(); clearTimeout(exitTimer.current); dialog.current?.close(); release()
      query.removeEventListener('change', resize)
      const visible = el => el?.isConnected && el.getClientRects().length && !el.closest('[hidden],[inert]')
      const target = visible(previous) && previous !== document.body ? previous : [...document.querySelectorAll('.journal-dropdown-trigger')].find(visible)
      target?.focus({ preventScroll: true })
    }
  }, [isOpen])
  useEffect(() => { if (isOpen && session !== owner.current) { operation.current?.abort(); onClose() } }, [session, isOpen, onClose])
  useEffect(() => {
    if (!isOpen || !current()) return
    let active = true
    const controller = new AbortController()
    if (validDraft) { setPreview({ state: 'idle' }); return () => { active = false } }
    setPreview({ state: 'loading' })
    const timer = setTimeout(async () => {
      try {
        const { data } = await api.post(`${endpoint}/preview/`, JSON.parse(payloadKey), { signal: controller.signal })
        if (!Number.isSafeInteger(data.count) || data.count < 0 || typeof data.scope !== 'string') throw new Error('预览数量读取不完整。')
        if (active && current()) setPreview({ state: 'ready', ...data })
      } catch (reason) { if (active && current() && !controller.signal.aborted) setPreview({ state: 'error', message: errorText(reason) }) }
    }, 350)
    return () => { active = false; clearTimeout(timer); controller.abort() }
  }, [isOpen, payloadKey, validDraft, endpoint, retry, session])

  const change = (key, value) => { setForm(old => ({ ...old, [key]: value })); setError(''); setPending(null) }
  const scoreBound = (key, value) => { setError(''); setPending(null); setForm(old => ({ ...old, [key]: value, score_state: value !== '' ? 'rated' : old.score_state })) }
  const scoreState = value => { setError(''); setPending(null); setForm(old => ({ ...old, score_state: value, ...(value === 'unrated' || value === 'any' ? { score_min: '', score_max: '' } : {}) })) }
  const toggle = (key, value, limit) => { setError(''); setPending(null); setForm(old => ({ ...old, [key]: old[key].includes(value) ? old[key].filter(item => item !== value) : old[key].length < limit ? [...old[key], value] : old[key] })) }
  const submit = async (event, apply = false) => {
    event.preventDefault()
    if (gate.current || closing || pending || deleteTarget) return
    const badNumber = [...dialog.current.querySelectorAll('input')].some(input => input.validity.badInput)
    const problem = badNumber ? '请填写有效的年份或评分数字。' : quickFilterDraftError(form)
    if (problem) { setError(problem); return }
    gate.current = true; setBusy(true); setError('')
    const controller = new AbortController(); operation.current = controller
    try {
      const { data } = editingId ? await api.patch(`${endpoint}/${editingId}/`, payload, { signal: controller.signal }) : await api.post(`${endpoint}/`, payload, { signal: controller.signal })
      if (!current()) return
      onSaved(data, { apply }); showToast(editingId ? '快捷筛选已更新。' : '快捷筛选已创建。', 'success'); finishClose()
    } catch (reason) { if (current() && !controller.signal.aborted) setError(errorText(reason)) }
    finally { gate.current = false; if (current()) setBusy(false) }
  }
  const remove = async () => {
    if (gate.current || !deleteTarget) return
    const target = deleteTarget
    gate.current = true; setBusy(true); setError('')
    const controller = new AbortController(); operation.current = controller
    try {
      await api.delete(`${endpoint}/${target.id}/`, { signal: controller.signal })
      if (!current()) return
      onDeleted(target.id); setDeleteTarget(null); if (editingId === target.id) reset()
      showToast('快捷筛选已删除。', 'success')
    } catch (reason) { if (current() && !controller.signal.aborted) setError(errorText(reason)) }
    finally { gate.current = false; if (current()) setBusy(false) }
  }
  const tagPicker = (key, title, caption) => <section className={`quick-workshop-tag-panel quick-workshop-tag-panel--${key}`} aria-label={title}>
    <h4>{title}</h4><fieldset><legend>{caption}（最多 8 个）</legend>
      <input type="search" aria-label={`搜索${caption}`} placeholder="搜索标签…" value={search[key]} onChange={event => setSearch(old => ({ ...old, [key]: event.target.value }))} />
      <div className="quick-workshop-tags">{[...new Set([...form[key], ...availableTags.filter(tag => tag !== '轩皇力推').sort()])].filter(tag => form[key].includes(tag) || tag.toLowerCase().includes(search[key].trim().toLowerCase())).map(tag => <button key={tag} type="button" aria-pressed={form[key].includes(tag)} disabled={disabled || (!form[key].includes(tag) && form[key].length >= 8)} onClick={() => toggle(key, tag, 8)}>{form[key].includes(tag) ? '✓ ' : '+ '}{tag}</button>)}</div>
    </fieldset><label>{title === '包含条件' ? '包含' : '排除'}标题词（最多 4 个）<input value={form[key === 'tags' ? 'title_keywords' : 'exclude_title_keywords']} onChange={event => change(key === 'tags' ? 'title_keywords' : 'exclude_title_keywords', event.target.value)} placeholder="逗号分隔；匹配中日文标题" /></label>
  </section>
  if (!isOpen) return null
  return <dialog ref={dialog} className={`quick-workshop-backdrop${closing ? ' is-closing' : ''}`} aria-labelledby="quickFilterEditorTitle" aria-modal="true" onCancel={event => { event.preventDefault(); closeRef.current() }}>
    <section className="quick-workshop"><header><div><span>FILTER WORKSHOP</span><h2 id="quickFilterEditorTitle">自定义快速筛选</h2><p>{description}</p></div><button type="button" aria-label="关闭自定义筛选编辑器" disabled={disabled} onClick={requestClose}>×</button></header>
      <div className="quick-workshop-body"><aside><button type="button" className="quick-workshop-history-toggle" aria-expanded={savedOpen} onClick={() => setSavedOpen(value => !value)}>已保存的组合（{filters.length}） <span>{savedOpen ? '－' : '＋'}</span></button><div hidden={!savedOpen} className="quick-workshop-history">
        {!filters.length && <p>暂时没有自定义筛选。</p>}{filters.map(filter => <article key={filter.id} className={editingId === filter.id ? 'is-selected' : ''}><div><strong>{filter.name}</strong><button type="button" disabled={disabled} aria-label={`编辑 ${filter.name}`} onClick={() => transition(() => reset(filter))}>✎</button><button type="button" disabled={disabled} aria-label={`删除 ${filter.name}`} onClick={() => setDeleteTarget(filter)}>×</button></div><p>{quickFilterSummary(filter)}</p></article>)}
      </div></aside>
      <form noValidate onSubmit={submit}><div className="quick-workshop-editor"><div className="quick-workshop-heading"><h3>{editingId ? '修改组合' : '创建新组合'}</h3><button type="button" disabled={disabled} onClick={() => transition(() => reset())}>创建新组合</button></div>
        <fieldset disabled={disabled}><label>筛选名称<input aria-label="筛选名称" maxLength={30} value={form.name} onChange={event => change('name', event.target.value)} placeholder="例如：我的高分收藏" /></label></fieldset>
        <div className="quick-workshop-summary"><strong>当前条件</strong><p>{quickFilterSummary(payload)}</p><small>条件组之间同时满足；包含条件中的标签和标题词按所选匹配方式处理。</small></div>
        <nav aria-label="快速筛选条件分类">{[['basic', '常用条件'], ['tags', '标签与标题']].map(([key, label]) => <button key={key} type="button" disabled={disabled} aria-pressed={tab === key} onClick={() => setTab(key)}>{label}</button>)}</nav>
        <fieldset disabled={disabled} hidden={tab !== 'basic'} className="quick-workshop-basic"><legend className="quick-workshop-sr">常用条件</legend>
          <fieldset><legend>放送年份范围（含起止年份）</legend><div className="quick-workshop-pair">{[['year_from', '起始年份'], ['year_to', '结束年份']].map(([key,label]) => <label key={key}>{label}<input aria-label={`组合${label}`} type="number" min="1000" max="9999" step="1" placeholder="不限" value={form[key]} onChange={event => change(key, event.target.value)} /></label>)}</div><p>设置范围后不匹配未知年份。</p></fieldset>
          <fieldset><legend>个人评分（原始 0–10 分）</legend><label>评分状态<select aria-label="组合评分状态" value={form.score_state} onChange={event => scoreState(event.target.value)}><option value="any">不限</option><option value="rated">仅已评分</option><option value="unrated">仅未评分</option></select></label>
            {form.score_state !== 'unrated' && <div className="quick-workshop-pair">{[['score_min', '最低分'], ['score_max', '最高分']].map(([key,label]) => <label key={key}>{label}（含）<input aria-label={`组合${label}`} type="number" min="0" max="10" step="0.1" placeholder="不限" value={form[key]} onChange={event => scoreBound(key, event.target.value)} /></label>)}</div>}
            <p>{form.score_state === 'unrated' ? '仅匹配没有个人评分的作品；评分区间已清空。' : '可只填单侧边界；设置区间会切换为仅已评分。0 分属于已评分，星级和配色不影响筛选。'}</p>
          </fieldset>
          <fieldset><legend>观看状态（多选，命中其中任意一种）</legend><div className="quick-workshop-statuses">{Object.entries(WATCH_STATES).map(([key,label]) => <label key={key}><input type="checkbox" checked={form.watch_statuses.includes(key)} onChange={() => toggle('watch_statuses', key, 4)} />{label}</label>)}</div><p>不选择时不限观看状态。</p></fieldset>
          <label>长评状态<select aria-label="组合长评状态" value={form.review_state} onChange={event => change('review_state', event.target.value)}><option value="any">不限</option><option value="with">有长评</option><option value="without">无长评</option></select><small>仅含空格或换行视为无长评。</small></label>
        </fieldset>
        <fieldset disabled={disabled} hidden={tab !== 'tags'} className="quick-workshop-tag-fields"><legend className="quick-workshop-sr">标签与标题</legend><label>包含条件匹配方式<select value={form.match_mode} onChange={event => change('match_mode', event.target.value)}><option value="any">包含条件命中任意一项</option><option value="all">包含条件必须全部满足</option></select></label><div className="quick-workshop-pair">{tagPicker('tags', '包含条件', '包含标签')}{tagPicker('exclude_tags', '排除条件', '排除标签')}</div><p>排除项命中任意一项即移除；不改变年份、评分或状态条件。OVA 等标题词不等于完整的作品类型识别。</p></fieldset>
      </div><footer>
        <div className="quick-workshop-preview" role="status" aria-live="polite">{validDraft ? validDraft : preview.state === 'ready' ? <><strong>匹配 {preview.count} 部</strong><span>范围：{preview.scope}，包含其他分页；不叠加页面外层筛选。</span></> : preview.state === 'loading' ? '正在统计完整范围…' : preview.state === 'error' ? <><span>{preview.message}</span><button type="button" disabled={disabled} onClick={() => setRetry(value => value + 1)}>重试预览</button></> : '设置条件后预览'}</div>
        <p className="quick-workshop-note">应用后仍与页面搜索、年份和状态条件叠加；如无结果，可清除页面外层条件。预览不会保存草稿。</p>
        {error && <p className="quick-workshop-error" role="alert">{error}</p>}
        {pending && <div className="quick-workshop-confirm" role="group" aria-label="放弃筛选草稿"><p>有未保存修改，是否放弃本页草稿？</p><button type="button" disabled={disabled} onClick={() => setPending(null)}>继续编辑</button><button type="button" disabled={disabled} onClick={() => pending.run()}>放弃修改</button></div>}
        {deleteTarget && <div className="quick-workshop-confirm" role="group" aria-label="删除筛选确认"><p>删除“{deleteTarget.name}”？不会删除作品。</p><button type="button" disabled={disabled} onClick={() => setDeleteTarget(null)}>取消删除</button><button type="button" disabled={disabled} onClick={remove}>确认删除</button></div>}
        <div className="quick-workshop-actions"><span>{dirty ? '草稿未保存' : '条件组合'}</span><button type="submit" disabled={disabled || Boolean(pending) || Boolean(deleteTarget)}>{busy ? '保存中…' : '保存组合'}</button><button type="button" className="quick-workshop-primary" disabled={disabled || Boolean(pending) || Boolean(deleteTarget)} onClick={event => submit(event, true)}>保存并应用</button><button type="button" disabled={disabled} onClick={requestClose}>关闭</button></div>
      </footer></form></div>
    </section>
  </dialog>
}
