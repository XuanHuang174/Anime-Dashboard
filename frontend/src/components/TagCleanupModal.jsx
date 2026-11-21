import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import useSeriesSession, { seriesSession } from '../hooks/useSeriesSession.js'
import { lockDialogBody } from '../utils/dialogBodyLock.js'
import { cleanupCounts, cleanupLabels, cleanupPhases, cleanupProgress, cleanupResult, cleanupScope, defaultCleanupTab } from '../utils/tagCleanupPresentation.js'
import TagCleanupPreview, { CleanupDisclosure } from './TagCleanupPreview.jsx'
import './TagCleanupModal.css'

const errorText = error => {
  if (error?.response?.status === 429) return '操作较频繁，请稍后重新读取任务。已完成的批次会保留。'
  const data = error?.response?.data
  return typeof data?.detail === 'string' ? data.detail : data ? Object.values(data).flat().join('；') : '连接暂时中断。已完成的批次会保留，请重新读取后继续。'
}
const download = (value, filename) => {
  const url = URL.createObjectURL(value instanceof Blob ? value : new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url)
}
const shortDate = value => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })

export default function TagCleanupModal({ filters, filteredCount, onClose, onChanged }) {
  const activeSession = useSeriesSession()
  const [scope, setScope] = useState('filtered'), [history, setHistory] = useState([]), [job, setJob] = useState(null)
  const [items, setItems] = useState([]), [page, setPage] = useState(1), [itemCount, setItemCount] = useState(0), [tab, setTab] = useState('all')
  const [excluded, setExcluded] = useState(new Set()), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [notice, setNotice] = useState(''), [confirmedUndo, setConfirmedUndo] = useState(false), [viewLoading, setViewLoading] = useState(true)
  const [rulesOpen, setRulesOpen] = useState(false), [setupOpen, setSetupOpen] = useState(true)
  const [historyOpen, setHistoryOpen] = useState(() => window.innerWidth >= 768)
  const [downloading, setDownloading] = useState(''), [closing, setClosing] = useState(false), [pauseRequested, setPauseRequested] = useState(false)
  const [activePhase, setActivePhase] = useState('scan')
  const live = useRef(true), session = useRef(seriesSession()), request = useRef(null), reader = useRef(null), historian = useRef(null)
  const pause = useRef(false), running = useRef(false), starting = useRef(false), modal = useRef(null), previews = useRef(null)
  const scanRequest = useRef(null), changed = useRef(onChanged), close = useRef(onClose), downloadGate = useRef(false), closeTimer = useRef(null)
  changed.current = onChanged; close.current = onClose
  const current = () => live.current && session.current === seriesSession()
  const reportError = reason => { if (current() && reason.code !== 'ERR_CANCELED') setError(errorText(reason)) }
  const closeModal = () => {
    if (busy || closing) return
    setClosing(true)
    closeTimer.current = setTimeout(() => { if (current()) close.current() }, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 140)
  }
  useEffect(() => {
    if (activeSession !== session.current) {
      request.current?.abort(); reader.current?.abort(); historian.current?.abort(); close.current()
    }
  }, [activeSession])
  const load = async (id, targetPage = 1, targetTab = null) => {
    reader.current?.abort(); const operation = new AbortController(); reader.current = operation
    setViewLoading(true)
    try {
      const mode = targetTab || 'all'
      const response = await api.get(`tag-cleanup/${id}/`, { params: { page: targetPage, filter: mode }, signal: operation.signal })
      if (!current() || operation.signal.aborted) return
      let data = response.data, resolvedTab = targetTab || defaultCleanupTab(data)
      if (!targetTab && resolvedTab !== mode) {
        const response = await api.get(`tag-cleanup/${id}/`, { params: { page: targetPage, filter: resolvedTab }, signal: operation.signal })
        if (!current() || operation.signal.aborted) return
        data = response.data
      }
      setJob(data); setItems(data.items); setItemCount(data.item_count); setPage(targetPage); setTab(resolvedTab); setError('')
      if (previews.current) previews.current.scrollTop = 0
      return data
    } finally { if (current() && reader.current === operation) setViewLoading(false) }
  }
  const readHistory = async () => {
    historian.current?.abort(); const operation = new AbortController(); historian.current = operation
    const { data } = await api.get('tag-cleanup/', { signal: operation.signal })
    if (!current() || operation.signal.aborted) return null
    setHistory(data)
    return data
  }
  const readLatest = async () => {
    setViewLoading(true)
    try {
      const rows = await readHistory()
      if (!current() || !rows) return
      const selected = rows.find(row => row.id === job?.id) || rows.find(row => ['scanning', 'applying', 'undoing', 'ready'].includes(row.status)) || rows[0]
      if (selected) { setSetupOpen(false); await load(selected.id) }
      else { setJob(null); setItems([]); setItemCount(0); setViewLoading(false); setSetupOpen(true) }
    } catch (reason) { reportError(reason); if (current() && reason.code !== 'ERR_CANCELED') setViewLoading(false) }
  }
  useEffect(() => {
    const release = lockDialogBody(document), previous = document.activeElement
    const query = window.matchMedia('(max-width: 767px)'), resize = () => setHistoryOpen(!query.matches)
    live.current = true; modal.current.showModal(); readLatest(); query.addEventListener('change', resize)
    return () => {
      live.current = false; request.current?.abort(); reader.current?.abort(); historian.current?.abort(); clearTimeout(closeTimer.current)
      query.removeEventListener('change', resize); modal.current?.close(); release()
      const visible = element => element?.isConnected && element.getClientRects().length && !element.closest('[hidden], [inert]')
      const target = visible(previous) && previous !== document.body ? previous : [...document.querySelectorAll('.journal-dropdown-trigger')].find(visible)
      target?.focus({ preventScroll: true })
    }
  }, [])
  const run = async (task, phase) => {
    if (running.current) return
    running.current = true; setBusy(true); setActivePhase(phase); setError(''); setNotice(''); pause.current = false; setPauseRequested(false)
    const operation = new AbortController(); request.current = operation
    let latest = task
    try {
      const expected = { scan: 'scanning', apply: 'applying', undo: 'undoing' }[phase]
      while (current() && latest.status === expected && !pause.current) {
        const { data } = await api.post(`tag-cleanup/${latest.id}/step/`, { phase, cursor: latest[`${phase}_cursor`] }, { signal: operation.signal })
        if (!current()) return
        latest = data; setJob(data)
        await new Promise(resolve => setTimeout(resolve, 550))
      }
      if (!current()) return
      const paused = latest.status === expected
      await load(latest.id, 1, paused ? tab : null); await readHistory()
      setNotice(paused ? '已暂停，进度已保存。关闭或刷新后可从最近任务继续。' : phase === 'scan' ? '扫描完成；未知标签会保留，无需额外处理。' : '')
    } catch (reason) { reportError(reason) }
    finally {
      running.current = false
      if (current()) { setBusy(false); setPauseRequested(false); if (phase !== 'scan') changed.current?.() }
    }
  }
  const scan = async () => {
    if (busy || viewLoading || downloading || running.current || starting.current) return
    starting.current = true; setBusy(true); setActivePhase('scan'); setError(''); setNotice(''); setExcluded(new Set()); setConfirmedUndo(false)
    scanRequest.current ||= crypto.randomUUID()
    try {
      const { data } = await api.post('tag-cleanup/', { request_id: scanRequest.current, scope, filters: scope === 'filtered' ? filters : {} })
      if (!current()) return
      scanRequest.current = null; setJob(data); setItems([]); setTab('all'); setPage(1); setSetupOpen(false)
      await run(data, 'scan')
    } catch (reason) { reportError(reason) }
    finally { starting.current = false; if (current()) setBusy(false) }
  }
  const apply = async () => {
    if (busy || viewLoading || downloading || starting.current || running.current) return
    starting.current = true; setBusy(true); setActivePhase('apply'); setError(''); setNotice('')
    try {
      const { data } = await api.post(`tag-cleanup/${job.id}/apply/`, { excluded_ids: [...excluded] })
      if (current()) await run(data, 'apply')
    } catch (reason) { reportError(reason) }
    finally { starting.current = false; if (current()) setBusy(false) }
  }
  const undo = async () => {
    if (busy || viewLoading || downloading || starting.current || running.current) return
    starting.current = true; setBusy(true); setActivePhase('undo'); setError(''); setNotice(''); setConfirmedUndo(false)
    try {
      const { data } = await api.post(`tag-cleanup/${job.id}/undo/`, {})
      if (current()) await run(data, 'undo')
    } catch (reason) { reportError(reason) }
    finally { starting.current = false; if (current()) setBusy(false) }
  }
  const file = async backup => {
    if (downloadGate.current || busy) return
    downloadGate.current = true; setDownloading(backup ? 'backup' : 'changes'); setNotice('')
    try {
      const { data } = await api.get(backup ? 'export/' : `tag-cleanup/${job.id}/changes/`, backup ? { responseType: 'blob' } : {})
      if (current()) { download(data, backup ? '私人手账备份.json' : '标签整理变更清单.json'); setNotice('文件已交给浏览器下载。') }
    } catch (reason) { reportError(reason) }
    finally { downloadGate.current = false; if (current()) setDownloading('') }
  }
  const selectJob = async row => {
    setExcluded(new Set()); setConfirmedUndo(false); setNotice(''); setSetupOpen(false)
    try { await load(row.id) } catch (reason) { reportError(reason) }
  }
  const toggleRecord = id => setExcluded(old => { const next = new Set(old); next.has(id) ? next.delete(id) : next.add(id); return next })
  const selectPage = include => setExcluded(old => {
    const next = new Set(old)
    items.filter(item => item.status === 'proposed').forEach(item => include ? next.delete(item.record_id) : next.add(item.record_id))
    return next
  })
  const totalPages = Math.max(1, Math.ceil(itemCount / 20)), selected = Math.max(0, (job?.counts.proposed || 0) - excluded.size)
  const resumable = cleanupPhases[job?.status], counts = cleanupCounts(job), progress = cleanupProgress(job)
  const blocked = busy || viewLoading || Boolean(downloading) || closing
  const hasActiveTask = Boolean(resumable && !job?.expired) || history.some(row => cleanupPhases[row.status] && !row.expired)
  const canSelect = job?.status === 'ready' && !job.expired
  const pageProposals = items.filter(item => item.status === 'proposed')
  const stage = job?.expired ? '已过期' : busy ? (pauseRequested ? '正在完成当前批次' : { scan: '扫描中', apply: '应用中', undo: '撤销中' }[activePhase]) : cleanupLabels[job?.status]
  const tabs = [['all', '全部', job?.scan_cursor || 0], ['changes', '有变化', counts.changes], ['review', '未知保留', counts.review], ['protected', '已保护', counts.protected], ...(counts.conflicts ? [['conflicts', '冲突', counts.conflicts]] : [])]
  return <dialog ref={modal} className={`tag-cleanup-backdrop${closing ? ' is-closing' : ''}`} aria-modal="true" aria-labelledby="tagCleanupTitle"
    onKeyDown={event => { if (event.key === 'Escape') event.stopPropagation() }} onCancel={event => { event.preventDefault(); closeModal() }}>
    <section className="tag-cleanup-modal">
      <header className="tag-cleanup-header"><div><span className="tag-cleanup-sticker">TAG WORKSHOP</span><h2 id="tagCleanupTitle">整理作品标签</h2><p>先看变化，再安心整理。个人标记默认保护。</p></div>
        <button type="button" className="tag-cleanup-close" aria-label="关闭标签整理" disabled={busy || closing} onClick={closeModal}>×</button></header>
      <div className="tag-cleanup-workspace">
        <aside className="tag-cleanup-history" aria-label="最近整理任务">
          <button type="button" className="tag-cleanup-history-toggle" aria-expanded={historyOpen} aria-controls="tagCleanupHistory" onClick={() => setHistoryOpen(value => !value)}><strong>最近任务 <small>{history.length}</small></strong><span aria-hidden="true">{historyOpen ? '－' : '＋'}</span></button>
          <div id="tagCleanupHistory" hidden={!historyOpen}><p>最近 5 次完成记录，最长保留 30 天。重新打开预览需核对勾选。</p>
            <div className="tag-cleanup-history-list">{history.map(row => <button type="button" disabled={blocked} aria-pressed={job?.id === row.id} key={row.id} onClick={() => selectJob(row)}>
              <strong>{job?.id === row.id ? '▸ ' : ''}{job?.id === row.id && busy ? stage : cleanupLabels[row.status] || row.status}</strong><span>{shortDate(row.created_at)}</span><small>{row.total} 部 · {row.scope === 'all' ? '全部手账' : '筛选作品'}</small>
            </button>)}{!history.length && <p>扫描后，任务会保存在这里。</p>}</div>
          </div>
        </aside>
        <div className="tag-cleanup-main">
          <div className="tag-cleanup-controls">
            <div className="tag-cleanup-task-scope"><div><small>{job ? '当前任务范围' : '开始前'}</small><p>{job ? cleanupScope(job) : '扫描不会修改作品。先选择范围，生成可核对的整理方案。'}</p></div>
              <button type="button" className="tag-cleanup-subtle" disabled={blocked} aria-expanded={setupOpen} aria-controls="tagCleanupSetup" onClick={() => setSetupOpen(value => !value)}>{setupOpen ? '收起扫描设置' : '新建扫描'}</button>
              <button type="button" className="tag-cleanup-subtle" disabled={busy || Boolean(downloading)} onClick={() => file(true)}>{downloading === 'backup' ? '准备备份中…' : '下载 JSON 备份'}</button></div>
            <div id="tagCleanupSetup" className="tag-cleanup-setup" hidden={!setupOpen}><label>新扫描范围<select aria-label="标签整理范围" disabled={blocked} value={scope} onChange={event => { scanRequest.current = null; setScope(event.target.value) }}><option value="filtered">当前筛选的全部作品（{filteredCount} 部，含其他分页）</option><option value="all">全部私人手账</option></select></label>
              <button type="button" className="tag-cleanup-primary" disabled={blocked || hasActiveTask} onClick={scan}>扫描并生成预览</button>
              {job?.status === 'ready' && <p>新扫描会替换当前尚未应用的预览，请先完成核对或确认重新扫描。</p>}
              {hasActiveTask && <p>请先从最近任务继续进行中的任务；已应用部分也可以撤销。</p>}
            </div>
            <CleanupDisclosure title="整理规则" open={rulesOpen} onToggle={() => setRulesOpen(value => !value)} className="tag-cleanup-rules">
              <p>仅修改本人的私人基础标签。自定义、常用预设、快捷筛选引用、已配色及作品格式标签默认保护；未知标签保留。</p><p>标准题材来自已确认作品的本地资料。公开手账会同步更新，已有精选投稿不变。可先下载 JSON 备份，备份不是执行前提。</p><p>扫描预览有效期 24 小时；最近 5 次完成记录最长保留至应用开始后 30 天。撤销会跳过后来编辑的标签。</p>
            </CleanupDisclosure>
          </div>
          {job && <section className={`tag-cleanup-progress${counts.conflicts ? ' has-conflicts' : ''}${resumable || busy ? ' is-active' : ''}`} aria-label="整理进度">
            <div className="tag-cleanup-stage"><strong role="status">{stage}</strong><span>{progress.name} {progress.done}/{progress.total} 部</span><span>已应用 {job.counts.applied || 0} 部</span></div>
            <div className="tag-cleanup-meter" role="progressbar" aria-label={`${progress.name}进度`} aria-valuenow={progress.done} aria-valuemin={0} aria-valuemax={Math.max(1, progress.total)} aria-valuetext={`${progress.done}/${progress.total} 部`}><span style={{ width: `${progress.percent}%` }} /></div>
            <div className="tag-cleanup-result"><p role="status">{busy ? '按批次保存进度，请勿重复操作。' : cleanupResult(job)}</p>{counts.conflicts > 0 && <button type="button" disabled={blocked} onClick={() => load(job.id, 1, 'conflicts').catch(reportError)}>查看冲突 {counts.conflicts} 部</button>}</div>
            {job.expired ? <p role="alert">此预览或撤销记录已过期，请重新扫描。</p> : ['applied', 'applying', 'undone'].includes(job.status) && <small>本次撤销记录有效至 {shortDate(job.expires_at)}</small>}
          </section>}
          {error && <div className="tag-cleanup-error" role="alert"><p>{error}</p><button type="button" disabled={busy || viewLoading} onClick={readLatest}>重新读取任务</button></div>}
          {notice && <p className="tag-cleanup-notice" role="status">{notice}</p>}
          <div className="tag-cleanup-list-tools"><nav className="tag-cleanup-tabs" aria-label="标签整理预览类别">{tabs.map(([value, label, count]) => <button type="button" key={value} disabled={blocked || !job} aria-pressed={tab === value} onClick={() => load(job.id, 1, value).catch(reportError)}>{label}<span>{count}</span></button>)}</nav>
            <div className="tag-cleanup-selection">{canSelect ? <><span>跨页已选 <strong>{selected}</strong> 部</span><button type="button" disabled={blocked || !pageProposals.length} onClick={() => selectPage(true)}>全选本页</button><button type="button" disabled={blocked || !pageProposals.length} onClick={() => selectPage(false)}>取消本页</button></> : <span>分类可重叠；未知标签默认保留。</span>}</div>
          </div>
          <div ref={previews} className="tag-cleanup-previews" tabIndex={0} aria-label="作品标签变化预览" aria-busy={viewLoading}>
            {viewLoading ? <div className="tag-cleanup-empty" role="status"><span className="tag-cleanup-loader" aria-hidden="true" /><strong>正在读取预览…</strong><p>正在读取任务与标签变化。</p></div> : items.length ? items.map(item => <TagCleanupPreview key={`${job.id}:${item.record_id}`} item={item} checked={!excluded.has(item.record_id)} selectable={canSelect && item.status === 'proposed'} disabled={blocked} onToggle={() => toggleRecord(item.record_id)} />)
              : <div className="tag-cleanup-empty"><span aria-hidden="true">◇</span><strong>{busy ? '正在分批处理…' : job ? '此分类暂无记录' : '从一次扫描开始'}</strong><p>{busy ? '已完成的进度会保存。' : job ? '可切换其他分类查看；未知标签会自动保留。' : '查看变化，再选择需要整理的作品。'}</p></div>}
          </div>
          <div className="tag-cleanup-pagination"><span>{page}/{totalPages} 页 · 本分类 {itemCount} 部</span><button type="button" disabled={blocked || page <= 1} onClick={() => load(job.id, page - 1, tab).catch(reportError)}>上一页</button><button type="button" disabled={blocked || page >= totalPages} onClick={() => load(job.id, page + 1, tab).catch(reportError)}>下一页</button></div>
        </div>
      </div>
      <footer className="tag-cleanup-footer">
        {confirmedUndo && <div className="tag-cleanup-undo-confirm" role="group" aria-label="确认撤销"><strong>撤销本次已整理的 {job?.counts.applied || 0} 部？</strong><p>仅恢复本次标签；后来编辑的记录会跳过，保留新修改。</p></div>}
        <div className="tag-cleanup-footer-row"><small>{busy ? '进度逐批保存' : canSelect ? `已选 ${selected} 部，含其他分页` : job ? '变更清单可供核对与留存' : '扫描只生成方案'}</small><div className="tag-cleanup-actions">
          {job && !job.expired && <button type="button" className="tag-cleanup-subtle" disabled={busy || Boolean(downloading)} onClick={() => file(false)}>{downloading === 'changes' ? '准备清单中…' : '下载变更清单'}</button>}
          {busy ? <button type="button" disabled={pauseRequested} onClick={() => { pause.current = true; setPauseRequested(true); setNotice('正在完成当前批次，随后暂停…') }}>{pauseRequested ? '正在暂停…' : '完成当前批次后暂停'}</button> : <>
            {resumable && !job?.expired && !confirmedUndo && <button type="button" className="tag-cleanup-primary" disabled={blocked} onClick={() => run(job, resumable)}>继续{resumable === 'scan' ? '扫描' : resumable === 'apply' ? '应用' : '撤销'}</button>}
            {canSelect && <button type="button" className="tag-cleanup-primary" disabled={blocked || !selected} onClick={apply}>应用选中的 {selected} 部</button>}
            {['applied', 'applying'].includes(job?.status) && !job.expired && (confirmedUndo ? <><button type="button" className="tag-cleanup-danger" disabled={blocked} onClick={undo}>确认撤销本次整理</button><button type="button" disabled={blocked} onClick={() => setConfirmedUndo(false)}>暂不撤销</button></> : <button type="button" disabled={blocked} onClick={() => setConfirmedUndo(true)}>撤销本次整理</button>)}
            <button type="button" className={['applied', 'undone'].includes(job?.status) && !confirmedUndo ? 'tag-cleanup-primary' : ''} onClick={closeModal} disabled={closing}>{['applied', 'undone'].includes(job?.status) ? '完成并关闭' : '关闭'}</button>
          </>}
        </div></div>
      </footer>
    </section>
  </dialog>
}
