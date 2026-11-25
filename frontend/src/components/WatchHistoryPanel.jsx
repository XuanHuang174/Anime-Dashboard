import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import api from '../api/api.js'
import useSeriesSession, { seriesSession } from '../hooks/useSeriesSession.js'
import { ACTION_LABELS, SOURCE_LABELS, WATCH_LABELS, buildWatchCommand, createWatchHistoryController, eventWatchDate, formatWatchDate, initialWatchForm, watchEventSummary, watchProgress } from '../utils/watchHistory.js'
import './WatchHistoryPanel.css'

function DatePrecisionField({ label, part, onChange }) {
  return <fieldset className="watch-date"><legend>{label}</legend><select aria-label={`${label}精度`} value={part.precision} onChange={e => onChange({ precision: e.target.value, value: '' })}>
    <option value="unknown">未记录</option><option value="year">只记得年份</option><option value="month">记得年月</option><option value="day">具体日期</option>
  </select>{part.precision !== 'unknown' && <input aria-label={label} type={{ year: 'number', month: 'month', day: 'date' }[part.precision]} min={part.precision === 'year' ? '1000' : undefined} max={part.precision === 'year' ? '9999' : undefined} placeholder={part.precision === 'year' ? '例如 2020' : undefined} value={part.value} onChange={e => onChange({ ...part, value: e.target.value })} />}</fieldset>
}
const TITLES = { transition: '记录状态与进度', 'start-rewatch': '开启新一轮观看', 'update-round': '更正轮次资料', note: '写一条随记', memory: '补录往事', undo: '核对最近操作撤销' }

export default function WatchHistoryPanel({ item, onRecordUpdated, closeGuardRef, initiallyOpen = false }) {
  const callbacks = useRef(onRecordUpdated); callbacks.current = onRecordUpdated
  const owner = useRef(seriesSession()), session = useSeriesSession(), panel = useRef(null), trigger = useRef(null)
  const engine = useMemo(() => createWatchHistoryController({ client: api, recordId: item.id, getSession: seriesSession, onCommitted: record => callbacks.current?.(record) }), [item.id])
  const snapshot = useSyncExternalStore(engine.subscribe, engine.getSnapshot)
  const [open, setOpen] = useState(initiallyOpen), [form, setForm] = useState(null), [baseline, setBaseline] = useState(''), [localError, setLocalError] = useState(''), [confirm, setConfirm] = useState(null), [notice, setNotice] = useState(''), [selectedRound, setSelectedRound] = useState(null)
  const data = snapshot.data, busy = snapshot.status === 'saving', loading = snapshot.status === 'loading', sameOwner = session === owner.current
  const dirty = form && JSON.stringify(form) !== baseline, blocked = busy || loading || Boolean(snapshot.pending)
  const current = data?.rounds.find(row => row.is_current && !row.voided)
  const selected = data?.rounds.find(row => row.number === selectedRound) || current
  useEffect(() => { engine.activate(); return () => engine.dispose() }, [engine])
  useEffect(() => { if (open && sameOwner && engine.getSnapshot().status === 'idle') engine.read() }, [engine, open, sameOwner])
  useEffect(() => {
    if (session !== owner.current) { engine.dispose(); setForm(null); setConfirm(null) }
  }, [session, engine])
  useEffect(() => {
    const revision = item.rawRecord?.watch_revision, state = engine.getSnapshot()
    if (open && sameOwner && Number.isInteger(revision) && revision > (state.data?.version ?? -1) && !['saving', 'loading'].includes(state.status) && !state.pending) engine.read()
  }, [item.rawRecord?.watch_revision, open, sameOwner, engine])
  useEffect(() => {
    if (!dirty && !snapshot.pending && !busy) return
    const warn = event => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn)
  }, [dirty, snapshot.pending, busy])
  useEffect(() => {
    if (!localError && !snapshot.error) return
    const frame = requestAnimationFrame(() => { const node = panel.current?.querySelector('.watch-error'); node?.scrollIntoView({ block: 'nearest' }); node?.focus({ preventScroll: true }) })
    return () => cancelAnimationFrame(frame)
  }, [localError, snapshot.error])
  const focusCurrent = () => requestAnimationFrame(() => { const node = panel.current?.querySelector('.watch-current'); node?.scrollIntoView({ block: 'nearest' }); node?.focus({ preventScroll: true }) })
  const focusConfirm = () => requestAnimationFrame(() => { const node = panel.current?.querySelector('.watch-confirm'); node?.scrollIntoView({ block: 'nearest' }); node?.querySelector('button')?.focus({ preventScroll: true }) })
  const guard = (run, label = '离开详情') => {
    if (!sameOwner) return run()
    if (busy) { setNotice('正在提交，请等待本次操作完成。'); return }
    if (dirty || snapshot.pending) { setOpen(true); setConfirm({ run, label }); focusConfirm(); return }
    run()
  }
  useLayoutEffect(() => {
    if (closeGuardRef) closeGuardRef.current = guard
    return () => { if (closeGuardRef?.current === guard) closeGuardRef.current = null }
  }, [closeGuardRef, guard])
  const change = (key, value) => { setForm(old => ({ ...old, [key]: value })); setLocalError('') }
  const openForm = (action, status, round = current) => guard(() => {
    const latest = data.events[0]
    const next = action === 'undo' ? { action, version: data.version, target_revision: data.version, undoLabel: ACTION_LABELS[latest.kind], undoSummary: watchEventSummary(latest), undoRewatch: latest.kind === 'start-rewatch' } : initialWatchForm(action, round, data.version, status)
    trigger.current = document.activeElement; setForm(next); setBaseline(JSON.stringify(next)); setLocalError(''); setNotice(''); setConfirm(null)
    requestAnimationFrame(() => { const node = panel.current?.querySelector('.watch-form'); node?.scrollIntoView({ block: 'nearest' }); node?.querySelector('select,input,textarea,button')?.focus({ preventScroll: true }) })
  }, '切换操作')
  const cancelForm = () => guard(() => { setForm(null); setLocalError(''); trigger.current?.focus() }, '取消本次填写')
  const submit = async event => {
    event.preventDefault(); if (blocked || snapshot.conflict || form.version !== data.version) return
    try {
      const body = form.action === 'undo' ? { action: 'undo', version: form.version, target_revision: form.target_revision } : buildWatchCommand(form)
      if (form.action === 'start-rewatch' && current.status !== 'completed') throw new Error('当前轮次尚未完成，请选择其他操作。')
      if (await engine.submit(body)) { setForm(null); setBaseline(''); setNotice('历程已保存。'); setSelectedRound(null); focusCurrent() }
    } catch (error) { setLocalError(error.message) }
  }
  const retry = async () => { if (await engine.submit(null, true)) { setForm(null); setBaseline(''); setNotice('原操作结果已确认，不会重复记录。'); setSelectedRound(null); focusCurrent() } }
  const latest = data?.events[0], canUndo = latest && latest.revision === data.version && latest.kind !== 'undo'
  if (!sameOwner) return <section className="watch-workshop"><p>账号已切换，请重新打开作品详情。</p></section>
  return <section className="watch-workshop" aria-label="私人观看历程" ref={panel}>
    <button type="button" className="watch-toggle" aria-expanded={open} aria-controls={`watchHistory-${item.id}`} onClick={() => open ? guard(() => setOpen(false), '收起历程') : setOpen(true)}><span><strong><i className="fa-solid fa-clock-rotate-left" aria-hidden="true" /> 观看历程</strong><small>仅本人可见 · 起止日期、随记与重看</small></span><i className={`fa-solid ${open ? 'fa-chevron-up' : 'fa-chevron-down'}`} aria-hidden="true" /></button>
    {open && <div className="watch-body" id={`watchHistory-${item.id}`}>
      {confirm && <div className="watch-confirm" role="group" aria-label="确认离开历程草稿"><p>{snapshot.pending ? '本次操作可能已经保存。可继续核对；若离开，请在重新打开后先检查历程，不要重复创建。' : '有未保存的填写内容，是否放弃草稿？'}</p><button type="button" onClick={() => setConfirm(null)}>继续编辑</button><button type="button" onClick={() => { const task = confirm.run; setForm(null); setBaseline(''); setConfirm(null); task() }}>{snapshot.pending ? '离开并稍后核对' : `放弃草稿并${confirm.label}`}</button></div>}
      <div className="watch-read-tools"><span>{data ? `已记录完成 ${data.completed_rounds} 轮 · 重看记录 ${data.rewatch_count} 次` : '读取这部作品的观看记录'}</span><button type="button" disabled={busy || loading} onClick={() => engine.read()}>重新读取历程</button></div>
      {(busy || loading) && <p role="status" className="watch-status">{busy ? '正在保存本次操作…' : '正在读取历程…'}</p>}
      {(snapshot.error || localError || snapshot.pending) && !busy && <div className="watch-error" role="alert" tabIndex={-1}>{localError || snapshot.error || '原操作结果尚待核对。'}{snapshot.conflict && <p>草稿已保留。请重新读取，再核对后继续。</p>}{snapshot.pending && <><p>结果暂未确认。重试会使用同一操作编号，不会重复创建。</p><button type="button" disabled={loading} onClick={retry}>重试原操作并核对结果</button></>}</div>}
      {notice && <p role="status" className="watch-status">{notice}</p>}
      {data && <>
        <div className="watch-current" tabIndex={-1}><span className="watch-badge">当前 · 第 {current.number} 轮</span><strong>{WATCH_LABELS[current.status]} · {watchProgress(current)}</strong><span>开始：{formatWatchDate(current.started_on)}<br />完成：{formatWatchDate(current.completed_on)}</span></div>
        <div className="watch-quick-actions" role="group" aria-label="观看操作">
          {current.status === 'plan' && <button type="button" disabled={blocked} onClick={() => openForm('transition', 'watching')}>开始观看</button>}
          {current.status === 'on_hold' && <button type="button" disabled={blocked} onClick={() => openForm('transition', 'watching')}>继续观看</button>}
          {current.status === 'watching' && <><button type="button" disabled={blocked} onClick={() => openForm('transition')}>记录进度</button><button type="button" disabled={blocked} onClick={() => openForm('transition', 'completed')}>标记完成</button><button type="button" disabled={blocked} onClick={() => openForm('transition', 'on_hold')}>暂时搁置</button></>}
          {current.status === 'completed' && <button type="button" className="watch-primary" disabled={blocked} onClick={() => openForm('start-rewatch')}>开始重看</button>}
          <button type="button" disabled={blocked} onClick={() => openForm('note')}>写随记</button><button type="button" disabled={blocked} onClick={() => openForm('memory')}>补录往事</button>
        </div>
        {form && <form className="watch-form" noValidate onSubmit={submit}><h4>{TITLES[form.action]}{form.action === 'update-round' ? ` · 第 ${form.round_number} 轮` : ''}</h4>
          <p className="watch-help">{form.action === 'start-rewatch' ? '保留之前的完成记录；新一轮从 0 集开始。综合评分与长评保持不变。' : form.action === 'memory' ? '只补充这段回忆，不改变当前状态与进度。' : form.action === 'update-round' ? '更正这一轮的日期与感受，不修改作品综合评分或当前观看进度。' : form.action === 'undo' ? '仅恢复最近操作涉及的观看信息，保留撤销轨迹，不覆盖综合评分和长评。' : '日期可留空，系统记录时间不会当作实际观看日期。'}</p>
          {form.action === 'undo' ? <p>将撤销：{form.undoLabel}。{form.undoRewatch ? '新轮次将标为已撤销，并恢复上一轮为当前轮次。' : form.undoSummary}</p> : <fieldset disabled={blocked}>
            {form.action === 'transition' && <><label>观看状态<select aria-label="历程观看状态" value={form.watch_status} onChange={e => change('watch_status', e.target.value)}>{Object.entries(WATCH_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><div className="watch-pair">{[['watched_eps', '本轮已看集数'], ['total_eps', '本轮总集数（未知填 0）']].map(([key, label]) => <label key={key}>{label}<input aria-label={label} type="number" min="0" step="1" value={form[key]} onChange={e => change(key, e.target.value)} /></label>)}</div></>}
            {form.action === 'update-round' ? <><div className="watch-pair"><DatePrecisionField label="开始日期" part={form.started_on} onChange={value => change('started_on', value)} /><DatePrecisionField label="完成日期" part={form.completed_on} onChange={value => change('completed_on', value)} /></div><label>轮次评分（不修改综合评分）<input aria-label="轮次评分" type="number" min="0" max="10" step="0.1" value={form.rating} onChange={e => change('rating', e.target.value)} placeholder="可不评分" /></label></> : <DatePrecisionField label={form.action === 'start-rewatch' ? '重看开始日期' : form.action === 'memory' ? '回忆发生日期' : '观看日期'} part={form.action === 'start-rewatch' ? form.started_on : form.occurred_on} onChange={value => change(form.action === 'start-rewatch' ? 'started_on' : 'occurred_on', value)} />}
            <label>{form.action === 'update-round' ? '轮次备注' : '随记内容'}<textarea aria-label={form.action === 'update-round' ? '轮次备注' : '随记内容'} maxLength={2000} rows={4} value={form.note} onChange={e => change('note', e.target.value)} placeholder={['note', 'memory'].includes(form.action) ? '记下这一次的感受…' : '可选，写一句当时的感受'} /><small>{form.note.length} / 2000</small></label>
          </fieldset>}
          {form.version !== data.version && <div className="watch-warning">{form.action === 'undo' ? '最近操作已变化，请取消填写后重新选择需要撤销的操作。' : <>已读到更新的记录，原草稿仍保留。核对当前状态后再继续。<button type="button" disabled={blocked || snapshot.status !== 'ready'} onClick={() => { change('version', data.version); setNotice('已采用最新版本，请再次核对填写内容。') }}>核对后使用最新版本</button></>}</div>}
          <div className="watch-form-actions"><button type="submit" className="watch-primary" disabled={blocked || snapshot.status !== 'ready' || snapshot.conflict || form.version !== data.version}>{form.action === 'start-rewatch' ? '确认开始重看' : form.action === 'undo' ? '确认撤销最近操作' : '保存这条记录'}</button><button type="button" disabled={busy} onClick={cancelForm}>取消填写</button></div>
        </form>}
        <div className="watch-round-view"><label>查看轮次<select aria-label="查看观看轮次" value={selected.number} disabled={busy} onChange={e => setSelectedRound(Number(e.target.value))}>{data.rounds.map(row => <option key={row.number} value={row.number}>第 {row.number} 轮{row.voided ? ' · 已撤销' : row.is_current ? ' · 当前' : ` · ${WATCH_LABELS[row.status]}`}</option>)}</select></label>
          <p>开始：{formatWatchDate(selected.started_on)}<br />完成：{formatWatchDate(selected.completed_on)}</p><p>轮次评分：{selected.rating ?? '未记录'}{selected.rating_origin === 'completion_snapshot' ? '（完成时的评分）' : selected.rating_origin === 'manual' ? '（手动记录）' : ''}</p>{selected.note && <p className="watch-note">{selected.note}</p>}
          {!selected.voided && <button type="button" disabled={blocked} onClick={() => openForm('update-round', undefined, selected)}>更正第 {selected.number} 轮</button>}
        </div>
        <div className="watch-timeline-heading"><h4>观看时间线</h4>{canUndo && <button type="button" disabled={blocked} onClick={() => openForm('undo')}>撤销最近一次操作</button>}</div>
        <p className="watch-help">按记录顺序显示，最新在前。实际日期与系统记录时间分别展示；旧资料不推定未记录的重看次数。</p>
        {!data.count && <p className="watch-empty">还没有历程事件。已有状态保留为日期未知的初始记录，可先补充日期，或写下第一条随记。</p>}
        <ol className="watch-timeline">{data.events.map(event => <li key={event.revision} className={event.kind === 'undo' ? 'is-undo' : ''}><strong>{ACTION_LABELS[event.kind]}</strong><span className="watch-date-label">{eventWatchDate(event) ? `实际日期：${formatWatchDate(eventWatchDate(event))}` : ['change', 'transition', 'start-rewatch'].includes(event.kind) ? '实际观看日期未记录' : '未另填发生日期'}</span><p>{watchEventSummary(event)}</p>{event.note && <p className="watch-note">{event.note}</p>}<small>{SOURCE_LABELS[event.source] || '历程操作'} · 系统记录于 {new Date(event.recorded_at).toLocaleString('zh-CN', { hour12: false })}</small></li>)}</ol>
        {data.count > data.page_size && <div className="watch-pagination"><button type="button" disabled={busy || loading || data.page === 1} onClick={() => engine.read(data.page - 1)}>上一页历程</button><span>{data.page} / {Math.ceil(data.count / data.page_size)} 页 · {data.count} 条</span><button type="button" disabled={busy || loading || data.page * data.page_size >= data.count} onClick={() => engine.read(data.page + 1)}>下一页历程</button></div>}
      </>}
    </div>}
  </section>
}
