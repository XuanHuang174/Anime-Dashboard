import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import useSeriesSession, { seriesSession } from '../hooks/useSeriesSession.js'
import { lockDialogBody } from '../utils/dialogBodyLock.js'
import './TagCleanupModal.css'

const labels = { scanning: '扫描中／可继续', ready: '预览待确认', applying: '应用中／可继续', applied: '应用已完成', undoing: '撤销中／可继续', undone: '撤销已完成', expired: '已过期', cancelled: '已取消' }
const itemLabels = { proposed: '拟整理', unchanged: '无需修改', missing: '已跳过', applied: '已应用', conflict: '应用冲突', undone: '已撤销', undo_conflict: '撤销冲突' }
const errorText = error => {
  if (error?.response?.status === 429) return '操作较频繁，请稍后点击“重新读取任务”，已完成的批次会保留。'
  const data = error?.response?.data
  return typeof data?.detail === 'string' ? data.detail : data ? Object.values(data).flat().join('；') : '连接暂时中断。已完成的批次会保留，请重新读取后继续。'
}
const download = (value, filename) => {
  const url = URL.createObjectURL(value instanceof Blob ? value : new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url)
}

export default function TagCleanupModal({ filters, filteredCount, onClose, onChanged }) {
  const activeSession = useSeriesSession()
  const [scope, setScope] = useState('filtered'), [history, setHistory] = useState([]), [job, setJob] = useState(null)
  const [items, setItems] = useState([]), [page, setPage] = useState(1), [itemCount, setItemCount] = useState(0), [tab, setTab] = useState('all')
  const [excluded, setExcluded] = useState(new Set()), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [notice, setNotice] = useState(''), [confirmedUndo, setConfirmedUndo] = useState(false)
  const live = useRef(true), session = useRef(seriesSession()), request = useRef(null), reader = useRef(null), pause = useRef(false), running = useRef(false), starting = useRef(false), modal = useRef(null)
  const scanRequest = useRef(null), changed = useRef(onChanged), close = useRef(onClose)
  changed.current = onChanged; close.current = onClose
  const current = () => live.current && session.current === seriesSession()
  const reportError = reason => { if (current() && reason.code !== 'ERR_CANCELED') setError(errorText(reason)) }
  useEffect(() => {
    if (activeSession !== session.current) {
      request.current?.abort(); reader.current?.abort(); close.current()
    }
  }, [activeSession])
  const load = async (id, targetPage = 1, targetTab = tab) => {
    reader.current?.abort(); const operation = new AbortController(); reader.current = operation
    const { data } = await api.get(`tag-cleanup/${id}/`, { params: { page: targetPage, filter: targetTab }, signal: operation.signal })
    if (!current() || operation.signal.aborted) return
    setJob(data); setItems(data.items); setItemCount(data.item_count); setPage(targetPage); setError('')
  }
  const readHistory = async () => {
    const { data } = await api.get('tag-cleanup/')
    if (current()) setHistory(data)
    return data
  }
  const readLatest = async () => {
    try {
      const rows = await readHistory()
      if (!current()) return
      const selected = rows.find(row => row.id === job?.id) || rows.find(row => ['scanning', 'applying', 'undoing', 'ready'].includes(row.status)) || rows[0]
      if (selected) await load(selected.id)
    } catch (reason) { if (current() && reason.code !== 'ERR_CANCELED') setError(errorText(reason)) }
  }
  useEffect(() => {
    const release = lockDialogBody(document), previous = document.activeElement
    live.current = true
    modal.current.showModal()
    readLatest()
    return () => { live.current = false; request.current?.abort(); reader.current?.abort(); modal.current?.close(); release(); previous?.isConnected && previous.focus({ preventScroll: true }) }
  }, [])
  const run = async (task, phase) => {
    if (running.current) return
    running.current = true; setBusy(true); setError(''); pause.current = false
    const operation = new AbortController(); request.current = operation
    let latest = task
    try {
      const expected = { scan: 'scanning', apply: 'applying', undo: 'undoing' }[phase]
      while (current() && latest.status === expected && !pause.current) {
        const { data } = await api.post(`tag-cleanup/${latest.id}/step/`, { phase, cursor: latest[`${phase}_cursor`] }, { signal: operation.signal })
        if (!current()) return
        latest = data; setJob(data)
        // Bound client pacing as well as server work; a 429 is never retried automatically.
        await new Promise(resolve => setTimeout(resolve, 550))
      }
      if (!current()) return
      await load(latest.id, 1, tab); await readHistory()
      setNotice(pause.current ? '已暂停。关闭或刷新后，可从最近任务继续。' : phase === 'scan' ? '扫描完成，请核对变更后再应用。' : phase === 'apply' ? '应用完成。可下载变更清单或撤销本次整理。' : '撤销完成，请留意保留新修改的冲突记录。')
    } catch (reason) {
      if (current() && reason.code !== 'ERR_CANCELED') setError(errorText(reason))
    } finally {
      running.current = false
      if (current()) { setBusy(false); if (phase !== 'scan') changed.current?.() }
    }
  }
  const scan = async () => {
    if (busy || running.current || starting.current) return
    starting.current = true
    setBusy(true); setError(''); setNotice(''); setExcluded(new Set()); setConfirmedUndo(false)
    scanRequest.current ||= crypto.randomUUID()
    try {
      const { data } = await api.post('tag-cleanup/', { request_id: scanRequest.current, scope, filters: scope === 'filtered' ? filters : {} })
      if (!current()) return
      scanRequest.current = null; setJob(data); setItems([]); setTab('all'); setPage(1)
      await run(data, 'scan')
    } catch (reason) { if (current()) setError(errorText(reason)) }
    finally { starting.current = false; if (current()) setBusy(false) }
  }
  const apply = async () => {
    if (busy || starting.current || running.current) return
    starting.current = true
    setBusy(true); setError('')
    try {
      const { data } = await api.post(`tag-cleanup/${job.id}/apply/`, { excluded_ids: [...excluded] })
      if (current()) await run(data, 'apply')
    } catch (reason) { if (current()) setError(errorText(reason)) }
    finally { starting.current = false; if (current()) setBusy(false) }
  }
  const undo = async () => {
    if (busy || starting.current || running.current) return
    starting.current = true
    setBusy(true); setError(''); setConfirmedUndo(false)
    try {
      const { data } = await api.post(`tag-cleanup/${job.id}/undo/`, {})
      if (current()) await run(data, 'undo')
    } catch (reason) { if (current()) setError(errorText(reason)) }
    finally { starting.current = false; if (current()) setBusy(false) }
  }
  const file = async backup => {
    try {
      const { data } = await api.get(backup ? 'export/' : `tag-cleanup/${job.id}/changes/`, backup ? { responseType: 'blob' } : {})
      if (current()) { download(data, backup ? '私人手账备份.json' : '标签整理变更清单.json'); setNotice('文件已交给浏览器下载。') }
    } catch (reason) { if (current()) setError(errorText(reason)) }
  }
  const selectJob = async row => {
    setExcluded(new Set()); setConfirmedUndo(false); setNotice(''); setTab('all')
    try { await load(row.id, 1, 'all') } catch (reason) { if (current()) setError(errorText(reason)) }
  }
  const totalPages = Math.max(1, Math.ceil(itemCount / 20)), selected = Math.max(0, (job?.counts.proposed || 0) - excluded.size)
  const resumable = { scanning: 'scan', applying: 'apply', undoing: 'undo' }[job?.status]
  return <dialog ref={modal} className="tag-cleanup-backdrop" aria-modal="true" aria-labelledby="tagCleanupTitle"
    onKeyDown={event => { if (event.key === 'Escape') event.stopPropagation() }}
    onCancel={event => { event.preventDefault(); if (!busy) close.current() }}>
    <section className="tag-cleanup-modal">
      <header><div><span className="tag-cleanup-sticker">TAG WORKSHOP</span><h2 id="tagCleanupTitle">整理作品标签</h2><p>先预览再应用；个人标记默认保护，最近 5 次整理保留 30 天撤销记录。</p></div>
        <button type="button" aria-label="关闭标签整理" disabled={busy} onClick={onClose}>×</button></header>
      <div className="tag-cleanup-workspace">
        <aside aria-label="最近整理任务"><h3>最近任务</h3><p>中断后可继续。重新打开预览时，请重新核对勾选。</p>
          {history.map(row => <button type="button" disabled={busy} aria-pressed={job?.id === row.id} key={row.id} onClick={() => selectJob(row)}>
            <strong>{labels[row.status] || row.status}</strong><span>{new Date(row.created_at).toLocaleString('zh-CN')} · {row.total} 部</span></button>)}
          {!history.length && <p>还没有整理任务。</p>}
        </aside>
        <div className="tag-cleanup-main">
          <div className="tag-cleanup-scope"><label>整理范围<select aria-label="标签整理范围" disabled={busy} value={scope} onChange={event => { scanRequest.current = null; setScope(event.target.value) }}><option value="filtered">当前筛选的全部作品（{filteredCount} 部，含其他分页）</option><option value="all">全部私人手账</option></select></label>
            <button type="button" disabled={busy || Boolean(resumable && !job?.expired)} onClick={scan}>扫描并生成预览</button>
            <button type="button" disabled={busy} onClick={() => file(true)}>下载 JSON 备份</button></div>
          <p className="tag-cleanup-note">仅修改本人的私人基础标签。未知标签、个人常用/快捷筛选引用、已配色标签及作品格式标记默认保留。公开手账同步更新，已有精选投稿不变。</p>
          {job && <div className="tag-cleanup-progress" role="status"><strong>{labels[job.status] || job.status}</strong><span>已扫描 {job.scan_cursor}/{job.total} 部</span><span>待应用 {job.counts.proposed || 0} 部</span><span>已应用 {job.counts.applied || 0} 部</span><span>已撤销 {job.counts.undone || 0} 部</span><span>冲突 {(job.counts.conflict || 0) + (job.counts.undo_conflict || 0)} 部</span><span>待确认 {job.needs_review} 部</span><span>已保护 {job.protected_records} 部</span></div>}
          {job?.expired && <p role="alert">此预览或撤销记录已过期，请重新扫描。</p>}
          {error && <div className="tag-cleanup-error" role="alert">{error}<button type="button" disabled={busy} onClick={readLatest}>重新读取任务</button></div>}
          {notice && <p role="status">{notice}</p>}
          <nav className="tag-cleanup-tabs" aria-label="标签整理预览类别">{[['all', '全部'], ['changes', '有变化'], ['review', '待确认'], ['protected', '已保护']].map(([value, label]) => <button type="button" key={value} disabled={busy || !job} aria-pressed={tab === value} onClick={() => { setTab(value); load(job.id, 1, value).catch(reportError) }}>{label}</button>)}</nav>
          <div className="tag-cleanup-previews" tabIndex={0} aria-label="作品标签变化预览">
            {items.map(item => <article key={item.record_id}><div className="tag-cleanup-item-heading"><label>{item.status === 'proposed' && <input type="checkbox" aria-label={`整理 ${item.title}`} disabled={busy || job?.status !== 'ready'} checked={!excluded.has(item.record_id)} onChange={() => setExcluded(old => { const next = new Set(old); next.has(item.record_id) ? next.delete(item.record_id) : next.add(item.record_id); return next })} />}<strong>{item.title || '已无法读取的记录'}</strong></label><span>{itemLabels[item.status] || item.status}</span></div>
              <div className="tag-cleanup-comparison"><div><small>整理前</small><p>{item.before.join('、') || '暂无标签'}</p></div><div><small>整理后</small><p>{item.after.join('、') || '暂无标签'}</p></div></div>
              {item.changes.length > 0 && <ul>{item.changes.map((change, i) => <li key={i}>{change.kind === 'normalize' ? `规范：${change.from} → ${change.to}` : change.kind === 'remove' ? `拟移除：${change.from}（${change.reason}）` : change.kind === 'add' ? `补充：${change.to}（${change.reason}）` : `去重：${change.from}`}</li>)}</ul>}
              {item.protected.length > 0 && <p className="tag-cleanup-protected">已保护：{item.protected.join('、')}</p>}
              {item.uncertain.length > 0 && <p>用途待确认，默认保留：{item.uncertain.join('、')}</p>}
              <small>{item.source_note}</small>{item.message && <p role="status">{item.message}</p>}
            </article>)}
            {!items.length && <p>{busy ? '正在分批处理，已完成的进度会保存…' : job ? '当前类别没有可显示的记录。' : '扫描不会修改作品。生成预览后，逐条核对保留与变化。'}</p>}
          </div>
          <div className="tag-cleanup-pagination"><button type="button" disabled={busy || page <= 1} onClick={() => load(job.id, page - 1).catch(reportError)}>上一页</button><span>{page}/{totalPages} 页 · {itemCount} 部</span><button type="button" disabled={busy || page >= totalPages} onClick={() => load(job.id, page + 1).catch(reportError)}>下一页</button></div>
        </div>
      </div>
      <footer>
        {job && !job.expired && <button type="button" disabled={busy} onClick={() => file(false)}>下载变更清单</button>}
        {busy ? <button type="button" onClick={() => { pause.current = true; setNotice('正在完成当前批次，随后暂停…') }}>完成当前批次后暂停</button> : <>
          {resumable && !job?.expired && <button type="button" onClick={() => run(job, resumable)}>继续{resumable === 'scan' ? '扫描' : resumable === 'apply' ? '应用' : '撤销'}</button>}
          {job?.status === 'ready' && !job.expired && <button type="button" className="tag-cleanup-primary" disabled={!selected} onClick={apply}>应用选中的 {selected} 部</button>}
          {['applied', 'applying'].includes(job?.status) && !job.expired && (confirmedUndo ? <><span>仅恢复本次标签，后来编辑的记录将跳过。</span><button type="button" onClick={undo}>确认撤销本次整理</button><button type="button" onClick={() => setConfirmedUndo(false)}>暂不撤销</button></> : <button type="button" onClick={() => setConfirmedUndo(true)}>撤销本次整理</button>)}
          <button type="button" onClick={onClose}>关闭</button>
        </>}
      </footer>
    </section>
  </dialog>
}
