import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import { seriesSession } from '../hooks/useSeriesSession.js'
import { quickFilterSummary } from '../utils/quickFilter.js'
import { moveQuickFilter, sortedQuickFilters } from '../utils/quickFilterOrder.js'

const message = error => error?.response?.status === 429 ? '操作较频繁，请稍后主动重试。' : error?.response?.data?.detail || error.message || '顺序读取或保存失败，请重新读取。'
const validSnapshot = data => data && /^[a-f0-9]{64}$/.test(data.version) && Array.isArray(data.filters)
  && data.filters.every(row => Number.isSafeInteger(row.id) && row.id > 0 && Number.isInteger(row.sort_order) && row.sort_order >= 0)
  && new Set(data.filters.map(row => row.id)).size === data.filters.length

export default function QuickFilterOrderPanel({ filters, editingId, savedOpen, onToggleOpen, onExpand, onEdit, onDelete, onReordered, onStateChange, disabled, endpoint }) {
  const [active, setActive] = useState(false), [busy, setBusy] = useState(false), [rows, setRows] = useState([]), [ids, setIds] = useState([]), [base, setBase] = useState([]), [version, setVersion] = useState('')
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [dragging, setDragging] = useState(null)
  const alive = useRef(true), owner = useRef(seriesSession()), operation = useRef(null), gate = useRef(false), cards = useRef(new Map()), handles = useRef(new Map()), before = useRef(null), animations = useRef([])
  const dirty = JSON.stringify(ids) !== JSON.stringify(base), current = () => alive.current && owner.current === seriesSession()
  const blocked = disabled || busy
  useEffect(() => { alive.current = true; return () => { alive.current = false; operation.current?.abort(); animations.current.forEach(animation => animation.cancel()) } }, [])
  useEffect(() => { onStateChange({ active, dirty, busy }) }, [active, dirty, busy, onStateChange])
  useLayoutEffect(() => {
    if (!before.current) return
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      for (const [id, node] of cards.current) {
        const old = before.current.get(id), top = node.getBoundingClientRect().top
        if (old !== undefined && Math.abs(old - top) > 1) animations.current.push(node.animate([{ transform: `translateY(${old - top}px)` }, { transform: 'translateY(0)' }], { duration: 180, easing: 'ease-out' }))
      }
    }
    before.current = null
  }, [ids])
  const read = async () => {
    if (gate.current || disabled || !current()) return
    gate.current = true; setBusy(true); setActive(true); setError(''); setNotice(''); onExpand()
    onStateChange({ active: true, dirty, busy: true })
    if (!active) { const initial = sortedQuickFilters(filters); setRows(initial); setIds(initial.map(row => row.id)); setBase(initial.map(row => row.id)); setVersion('') }
    const controller = new AbortController(); operation.current = controller
    try {
      const { data } = await api.get(`${endpoint}/order/`, { signal: controller.signal })
      if (!validSnapshot(data)) throw new Error('顺序读取不完整，请重新读取。')
      if (!current()) return
      setRows(data.filters); setIds(data.filters.map(row => row.id)); setBase(data.filters.map(row => row.id)); setVersion(data.version)
      onReordered?.(data.filters); setNotice('可拖动手柄，或用上下按钮与方向键调整。右侧条件草稿保留。')
    } catch (reason) { if (current() && !controller.signal.aborted) setError(message(reason)) }
    finally { gate.current = false; if (current()) setBusy(false) }
  }
  const cancel = () => { if (blocked) return; setActive(false); setError(''); setNotice(''); setIds(base); onStateChange({ active: false, dirty: false, busy: false }) }
  const move = (id, target) => {
    if (blocked || !version) return
    const next = moveQuickFilter(ids, id, target)
    if (next === ids) return
    animations.current.forEach(animation => animation.cancel()); animations.current = []
    before.current = new Map([...cards.current].map(([key, node]) => [key, node.getBoundingClientRect().top]))
    setIds(next); setError('')
    setNotice(`${rows.find(row => row.id === id)?.name}已移至第 ${next.indexOf(id) + 1} 项，共 ${next.length} 项。排序尚未保存。`)
    requestAnimationFrame(() => handles.current.get(id)?.focus({ preventScroll: true }))
  }
  const save = async () => {
    if (gate.current || blocked || !dirty || !current()) return
    gate.current = true; setBusy(true); setError(''); onStateChange({ active: true, dirty: true, busy: true })
    const controller = new AbortController(); operation.current = controller
    try {
      const { data } = await api.post(`${endpoint}/order/`, { ids, version }, { signal: controller.signal })
      if (!validSnapshot(data)) throw new Error('保存结果读取不完整，请重新读取顺序。')
      if (!current()) return
      onReordered?.(data.filters); setRows(data.filters); setIds(data.filters.map(row => row.id)); setBase(data.filters.map(row => row.id)); setVersion(data.version); setActive(false)
      setNotice('顺序已保存，右侧条件草稿保留。其他展示入口下次读取时同步。')
    } catch (reason) { if (current() && !controller.signal.aborted) setError(message(reason)) }
    finally { gate.current = false; if (current()) setBusy(false) }
  }
  const indexed = new Map(rows.map(row => [row.id, row]))
  return <div className={`quick-order-panel${active ? ' is-ordering' : ''}`}>
    <div className="quick-saved-heading"><button type="button" className="quick-workshop-history-toggle" aria-expanded={active || savedOpen} disabled={busy} onClick={() => { if (!active) onToggleOpen() }}><span><i className="fa-solid fa-bookmark" aria-hidden="true" /> 已保存的组合（{active ? rows.length : filters.length}）</span><i className={`fa-solid ${savedOpen || active ? 'fa-minus' : 'fa-plus'}`} aria-hidden="true" /></button>
      {!active && <button type="button" className="quick-start-order" disabled={blocked} onClick={read}><i className="fa-solid fa-arrow-down-wide-short" aria-hidden="true" /> 调整顺序</button>}</div>
    {active ? <>
      <p className="quick-order-help">仅调整组合位置；条件编辑草稿保留。</p>
      <ol className="quick-workshop-history quick-order-list" aria-label="组合排序">{ids.map((id, index) => {
        const row = indexed.get(id)
        if (!row) return null
        return <li key={id} data-order-id={id} ref={node => node ? cards.current.set(id, node) : cards.current.delete(id)} className={dragging === id ? 'is-dragging' : ''} onDragOver={event => { if (dragging !== null && !blocked) event.preventDefault() }} onDrop={event => { event.preventDefault(); if (dragging !== null) move(dragging, id); setDragging(null) }}>
          <button type="button" className="quick-order-handle" ref={node => node ? handles.current.set(id, node) : handles.current.delete(id)} draggable={!blocked && Boolean(version)} disabled={blocked || !version} aria-label={`拖动排序：${row.name}，上下方向键移动`} title="拖动或使用上下方向键" onDragStart={event => { if (blocked) return event.preventDefault(); setDragging(id); event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', String(id)) }} onDragEnd={() => setDragging(null)} onKeyDown={event => { if (['ArrowUp', 'ArrowDown'].includes(event.key)) { event.preventDefault(); const target = ids[index + (event.key === 'ArrowUp' ? -1 : 1)]; if (target !== undefined) move(id, target) } }}><i className="fa-solid fa-grip-vertical" aria-hidden="true" /></button>
          <div className="quick-order-name"><small>第 {index + 1} 项</small><strong>{row.name}</strong></div>
          <div className="quick-order-arrows"><button type="button" disabled={blocked || !version || index === 0} onClick={() => move(id, ids[index - 1])} aria-label={`上移 ${row.name}`}><i className="fa-solid fa-arrow-up" aria-hidden="true" /></button><button type="button" disabled={blocked || !version || index === ids.length - 1} onClick={() => move(id, ids[index + 1])} aria-label={`下移 ${row.name}`}><i className="fa-solid fa-arrow-down" aria-hidden="true" /></button></div>
        </li>
      })}{!ids.length && <li>暂无可排序组合。</li>}</ol>
      {error && <p className="quick-workshop-error" role="alert">{error}</p>}
      <div className="quick-order-actions"><button type="button" disabled={blocked || !dirty || !version} className="quick-workshop-primary" onClick={save}>保存顺序</button><button type="button" disabled={blocked} onClick={cancel}>取消排序</button><button type="button" disabled={blocked} onClick={read}>{dirty ? '重新读取顺序（替换排序草稿）' : '重新读取顺序'}</button></div>
    </> : <div hidden={!savedOpen} className="quick-workshop-history">{!filters.length && <p>暂时没有自定义筛选。</p>}{sortedQuickFilters(filters).map(filter => <article key={filter.id} className={editingId === filter.id ? 'is-selected' : ''}><div><strong>{filter.name}</strong><button type="button" disabled={blocked} className="quick-edit-filter" aria-label={`编辑 ${filter.name}`} title="编辑组合" onClick={() => onEdit(filter)}><i className="fa-solid fa-pen" aria-hidden="true" /></button><button type="button" disabled={blocked} className="quick-delete-filter" aria-label={`删除 ${filter.name}`} title="删除组合" onClick={() => onDelete(filter)}><i className="fa-solid fa-trash-can" aria-hidden="true" /></button></div><details className="quick-saved-summary"><summary title={quickFilterSummary(filter)}><span>{quickFilterSummary(filter)}</span></summary></details></article>)}</div>}
    <p className="quick-order-notice" role="status" aria-live="polite">{busy ? '正在读取或保存顺序…' : notice}</p>
  </div>
}
