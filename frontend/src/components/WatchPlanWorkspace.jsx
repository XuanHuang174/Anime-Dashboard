import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import useWatchPlan from '../hooks/useWatchPlan.js'
import { seriesSession } from '../hooks/useSeriesSession.js'
import { PLAN_PRIORITIES, PLAN_PURPOSES, PLAN_STATES, loadCompletePlanGroup, movePlan, planDateLabel, planError, plannedTimePassed } from '../utils/watchPlans.js'
import { PlanError } from './WatchPlanPanel.jsx'
import { WATCH_LABELS } from '../utils/watchHistory.js'
import './WatchPlans.css'

function PlanOrder({ initialPriority, blocked, engine, onDirty, onClose, onSaved }) {
  const [priority, setPriority] = useState(initialPriority), [group, setGroup] = useState(null), [ids, setIds] = useState([]), [loading, setLoading] = useState(true), [error, setError] = useState(''), [reload, setReload] = useState(0)
  const drag = useRef(null), owner = useRef(seriesSession()), serial = useRef(0)
  const dirty = group && JSON.stringify(ids) !== JSON.stringify(group.rows.map(row => row.record.id))
  useEffect(() => { onDirty(Boolean(dirty)); return () => onDirty(false) }, [dirty, onDirty])
  useEffect(() => {
    const controller = new AbortController(), run = ++serial.current
    setLoading(true); setError(''); setGroup(null); setIds([])
    loadCompletePlanGroup(api, priority, controller.signal).then(data => {
      if (run === serial.current && owner.current === seriesSession()) { setGroup(data); setIds(data.rows.map(row => row.record.id)) }
    }).catch(problem => { if (!controller.signal.aborted && run === serial.current) setError(planError(problem)) }).finally(() => { if (!controller.signal.aborted && run === serial.current) setLoading(false) })
    return () => { controller.abort(); serial.current++ }
  }, [priority, reload])
  const shift = (id, direction) => { const i = ids.indexOf(id); if (i + direction >= 0 && i + direction < ids.length) setIds(movePlan(ids, id, ids[i + direction])) }
  const save = async () => {
    if (!group || blocked || !dirty) return
    if (await engine.submit({ version: group.version, priority, record_ids: ids })) { onSaved(); engine.read({ page: 1 }) }
  }
  return <section className="plan-order" aria-label="调整计划顺序"><h3>调整同档完整队列</h3><p className="plan-hint">仅移动当前优先级内的全部活跃计划。可拖动把手，或使用上移／下移。保存后才生效。</p>
    <label>排序优先级<select aria-label="排序优先级" value={priority} disabled={blocked || loading || dirty} onChange={event => setPriority(Number(event.target.value))}>{Object.entries(PLAN_PRIORITIES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    {loading && <p role="status">正在读取完整队列…</p>}
    {error && <p role="alert" className="plan-error">{error}<button type="button" onClick={() => setReload(n => n + 1)}>重新读取完整队列</button></p>}
    {group && !ids.length && <p>此档暂无活跃计划，可选择其他优先级。</p>}
    <ol className="plan-order-list">{ids.map((id, index) => {
      const row = group.rows.find(item => item.record.id === id)
      return <li key={id} onDragOver={event => { if (!blocked) event.preventDefault() }} onDrop={event => { event.preventDefault(); if (!blocked && drag.current !== null) setIds(movePlan(ids, drag.current, id)); drag.current = null }}>
        <button type="button" draggable={!blocked} disabled={blocked} className="plan-grip" aria-label={`拖动${row.record.title}`} onDragStart={event => { drag.current = id; event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', String(id)) }} onDragEnd={() => { drag.current = null }}>⠿</button><span>{index + 1}. {row.record.title}</span><div><button type="button" aria-label={`将${row.record.title}上移`} disabled={blocked || index === 0} onClick={() => shift(id, -1)}>↑ 上移</button><button type="button" aria-label={`将${row.record.title}下移`} disabled={blocked || index === ids.length - 1} onClick={() => shift(id, 1)}>↓ 下移</button></div>
      </li>
    })}</ol>
    <div className="plan-actions"><button type="button" className="plan-primary" disabled={blocked || loading || !dirty} onClick={save}>保存计划顺序</button><button type="button" disabled={blocked && engine.getSnapshot().status === 'saving'} onClick={onClose}>取消排序</button></div>
  </section>
}

function PlanPicker({ onOpenRecord }) {
  const [search, setSearch] = useState(''), [query, setQuery] = useState(''), [page, setPage] = useState(1), [data, setData] = useState(null), [error, setError] = useState(''), [loading, setLoading] = useState(false), [retry, setRetry] = useState(0)
  const owner = useRef(seriesSession())
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    api.get('records/', { params: { search: query || undefined, page, page_size: 12 }, signal: controller.signal }).then(({ data: result }) => {
      if (!controller.signal.aborted && owner.current === seriesSession()) {
        if (!Array.isArray(result.results) || !Number.isSafeInteger(result.count)) throw new Error('作品列表返回不完整。')
        setData(result)
      }
    }).catch(problem => { if (!controller.signal.aborted) setError(planError(problem)) }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [query, page, retry])
  return <section className="plan-picker" aria-label="选择手账作品"><h3>从私人手账选择作品</h3><form className="plan-search" onSubmit={event => { event.preventDefault(); setPage(1); setQuery(search.trim()); setRetry(n => n + 1) }}><input aria-label="搜索可安排作品" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索中文或日文名称" /><button type="submit">搜索作品</button></form>
    {loading && <p role="status">正在读取作品…</p>}{error && <p role="alert" className="plan-error">{error}<button type="button" onClick={() => setRetry(n => n + 1)}>重试作品列表</button></p>}
    <ul>{!loading && !error && data?.results.map(record => <li key={record.id}><span>{record.anime.chinese_title}<small>{WATCH_LABELS[record.watch_status]}</small></span><button type="button" onClick={() => onOpenRecord(record.id, { edit: true, plan: true })}>安排此作品</button></li>)}</ul>
    {!loading && !error && !data?.count && <p>没有匹配作品。请先在手账中添加作品，或调整搜索词。</p>}
    {data?.count > 12 && <div className="plan-pagination"><button type="button" disabled={loading || page <= 1} onClick={() => setPage(n => n - 1)}>上一页作品</button><span>{page} / {Math.ceil(data.count / 12)}</span><button type="button" disabled={loading || page * 12 >= data.count} onClick={() => setPage(n => n + 1)}>下一页作品</button></div>}
  </section>
}

export default function WatchPlanWorkspace({ active, editable, refreshKey, onOpenRecord, onEnterEdit, closeGuardRef, onChanged }) {
  const { engine, snapshot } = useWatchPlan({ enabled: active, onCommitted: onChanged })
  const [state, setState] = useState('active'), [priority, setPriority] = useState('all'), [sort, setSort] = useState('manual'), [search, setSearch] = useState(''), [appliedSearch, setAppliedSearch] = useState(''), [picker, setPicker] = useState(false), [order, setOrder] = useState(null), [orderDirty, setOrderDirty] = useState(false), [confirm, setConfirm] = useState(null)
  const root = useRef(null), lastRefresh = useRef(refreshKey), wasActive = useRef(active)
  const data = snapshot.data, busy = snapshot.status === 'saving', loading = snapshot.status === 'loading'
  const blocked = busy || loading || Boolean(snapshot.pending) || snapshot.conflict || snapshot.needsRead || snapshot.status !== 'ready'
  useEffect(() => {
    if (active && (lastRefresh.current !== refreshKey || !wasActive.current)) engine.read()
    lastRefresh.current = refreshKey; wasActive.current = active
  }, [active, engine, refreshKey])
  useEffect(() => {
    if (!snapshot.error) return
    const node = root.current?.querySelector('.plan-error'); node?.scrollIntoView({ block: 'nearest' }); node?.focus({ preventScroll: true })
  }, [snapshot.error])
  useEffect(() => {
    if (!orderDirty && !snapshot.pending && !busy) return
    const warn = event => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn)
  }, [orderDirty, snapshot.pending, busy])
  const guard = (run, label = '离开计划') => {
    if (busy) return
    if (orderDirty || snapshot.pending) { setConfirm({ run, label }); requestAnimationFrame(() => root.current?.querySelector('.plan-leave button')?.focus()); return }
    setOrder(null); run()
  }
  useLayoutEffect(() => { if (closeGuardRef) closeGuardRef.current = guard; return () => { if (closeGuardRef?.current === guard) closeGuardRef.current = null } }, [guard, closeGuardRef])
  const changeView = (options = {}) => { engine.read({ page: 1, state, priority: priority === 'all' ? undefined : Number(priority), search: appliedSearch || undefined, sort, ...options }) }
  const canOrder = editable && state === 'active' && sort === 'manual' && !search.trim() && !appliedSearch && !blocked
  const retry = async () => { if (await engine.submit(null, true)) { setOrder(null); engine.read({ page: 1 }) } }
  return <section ref={root} className="watch-plans plan-workspace" aria-label="观看计划工作区" hidden={!active}>
    <header className="plan-workspace-header"><div><span className="plan-sticker">WATCH NEXT</span><h2>下一部，按自己的节奏</h2><p>这是私人安排。想看收藏不会自动成为任务，计划时间也不是实际观看日期。</p></div>{editable ? <button type="button" disabled={busy || Boolean(snapshot.pending) || Boolean(order)} onClick={() => setPicker(value => !value)}>{picker ? '收起作品选择' : '选择手账作品'}</button> : <button type="button" onClick={() => guard(onEnterEdit, '进入编辑模式')}>前往编辑管理计划</button>}</header>
    {!editable && <p className="plan-hint">预览模式仅查看；修改安排、开始观看和移动顺序需进入编辑模式。</p>}
    {confirm && <div className="plan-leave" role="group" aria-label="确认离开计划排序"><p>{snapshot.pending ? '操作可能已保存，请先核对结果。离开后不要重复创建新操作。' : '排序尚未保存，是否放弃本次调整？'}</p><button type="button" onClick={() => setConfirm(null)}>继续调整计划</button><button type="button" onClick={() => { const task = confirm.run; setOrder(null); setOrderDirty(false); setConfirm(null); task() }}>{snapshot.pending ? '离开并稍后核对' : `放弃排序并${confirm.label}`}</button></div>}
    <PlanError snapshot={snapshot} engine={engine} onRetry={editable ? retry : undefined} />
    <div className="plan-tabs" role="group" aria-label="计划阶段">{[['active', '当前计划'], ['pending', '待开始'], ['ongoing', '进行中'], ['completed', '已完成'], ['archived', '已移出']].map(([value, label]) => <button type="button" key={value} aria-pressed={state === value} disabled={busy || Boolean(order)} onClick={() => { setState(value); changeView({ state: value }) }}>{label} <span>{data ? value === 'active' ? data.counts.pending + data.counts.ongoing : data.counts[value] : '—'}</span></button>)}</div>
    <form className="plan-toolbar" onSubmit={event => { event.preventDefault(); setAppliedSearch(search.trim()); changeView({ search: search.trim() || undefined }) }}><label>搜索计划<input aria-label="搜索观看计划" maxLength={100} value={search} disabled={Boolean(order) || busy} onChange={event => setSearch(event.target.value)} placeholder="中文或日文名称" /></label><label>优先级<select aria-label="计划优先级筛选" value={priority} disabled={Boolean(order) || busy} onChange={event => { setPriority(event.target.value); changeView({ priority: event.target.value === 'all' ? undefined : Number(event.target.value) }) }}><option value="all">全部优先级</option>{Object.entries(PLAN_PRIORITIES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>查看顺序<select aria-label="计划查看顺序" value={sort} disabled={Boolean(order) || busy} onChange={event => { setSort(event.target.value); changeView({ sort: event.target.value }) }}><option value="manual">优先级与手动顺序</option><option value="planned">计划时间优先</option><option value="added">最近加入</option></select></label><button type="submit" disabled={Boolean(order) || busy}>搜索计划</button><button type="button" disabled={busy || Boolean(order)} onClick={() => { setState('active'); setPriority('all'); setSort('manual'); setSearch(''); setAppliedSearch(''); engine.read({ page: 1, state: 'active', priority: undefined, sort: 'manual', search: undefined }) }}>清空计划筛选</button></form>
    <div className="plan-workspace-tools"><p>范围：本人全部观看计划，与手账页外层筛选无关。{data && ` 当前匹配 ${data.count} 部。`}</p><div className="plan-actions"><button type="button" disabled={busy || loading} onClick={() => engine.read()}>刷新计划</button>{editable && <button type="button" disabled={!canOrder || Boolean(order)} onClick={() => { setPicker(false); setOrder(priority === 'all' ? data?.results[0]?.plan.priority || 1 : Number(priority)) }}>调整顺序</button>}</div></div>
    {editable && !canOrder && !order && <p className="plan-hint">调整顺序请返回“当前计划”、手动顺序并清空搜索；同档完整队列将单独读取。</p>}
    {editable && picker && !order && <PlanPicker onOpenRecord={onOpenRecord} />}
    {editable && order !== null && <PlanOrder initialPriority={order} engine={engine} blocked={blocked} onDirty={setOrderDirty} onSaved={() => { setOrder(null); setOrderDirty(false) }} onClose={() => guard(() => { setOrder(null); setOrderDirty(false) }, '取消排序')} />}
    {order !== null && snapshot.conflict && <p className="plan-hint">完整队列已变化。请取消排序、刷新，再重新调整，不会直接覆盖他处的新顺序。</p>}
    {(busy || loading) && <p role="status">{busy ? '正在保存计划顺序…' : '正在读取计划队列…'}</p>}
    {!order && data && <><div className="plan-results">{data.results.map((row, index) => <div key={row.record.id}>
      {sort === 'manual' && (index === 0 || data.results[index - 1].plan.priority !== row.plan.priority) && <h3 className={`plan-group-heading priority-${row.plan.priority}`}><i className="fa-solid fa-flag" aria-hidden="true" /> {PLAN_PRIORITIES[row.plan.priority]}</h3>}
      <article className="plan-card"><button type="button" className="plan-cover" onClick={() => onOpenRecord(row.record.id)} aria-label={`查看${row.record.title}`}>{row.record.cover ? <img src={row.record.cover} alt="" loading="lazy" /> : <span aria-hidden="true">▤</span>}</button><div className="plan-card-title"><button type="button" onClick={() => onOpenRecord(row.record.id)}>{row.record.title}</button><small>{row.record.japanese_title}</small><div className="plan-card-badges"><span className={`plan-priority priority-${row.plan.priority}`}>{PLAN_PRIORITIES[row.plan.priority]}</span><span>{PLAN_PURPOSES[row.plan.purpose]} · {PLAN_STATES[row.plan.state]}</span></div>{row.plan.note && <p className="plan-note">{row.plan.note}</p>}</div><div className="plan-card-time"><strong>{planDateLabel(row.plan.planned_for)}</strong>{plannedTimePassed(row.plan.planned_for) && <small>可重新安排</small>}<span>{WATCH_LABELS[row.record.watch_status]} · {row.record.watched_eps}/{row.record.total_eps || '?'} 集</span></div><div className="plan-card-actions">{editable ? <><button type="button" onClick={() => onOpenRecord(row.record.id, { edit: true, plan: true })}>管理安排</button>{row.plan.state === 'pending' && <button type="button" className="plan-primary" onClick={() => onOpenRecord(row.record.id, { edit: true, plan: true, start: true })}>{row.plan.purpose === 'rewatch' ? '开始重看' : row.record.watch_status === 'on_hold' ? '恢复观看' : '开始观看'}</button>}{row.plan.state === 'ongoing' && <button type="button" className="plan-primary" onClick={() => onOpenRecord(row.record.id, { edit: true, history: true })}>记录进度</button>}</> : <button type="button" onClick={() => onOpenRecord(row.record.id)}>查看详情</button>}</div></article>
    </div>)}</div>{!data.count && !loading && <div className="plan-empty"><h3>给下一部留一个位置</h3><p>{appliedSearch || priority !== 'all' || state !== 'active' ? '当前条件下没有计划，可以调整或清空筛选。' : '从想看、搁置或看过的作品中主动选择；无需填写日期，也不自动添加任务。'}</p></div>}
      {data.count > data.page_size && <div className="plan-pagination"><button type="button" disabled={blocked || data.page === 1} onClick={() => engine.read({ page: data.page - 1 })}>上一页计划</button><span>{data.page} / {Math.ceil(data.count / data.page_size)} 页</span><button type="button" disabled={blocked || data.page * data.page_size >= data.count} onClick={() => engine.read({ page: data.page + 1 })}>下一页计划</button></div>}
    </>}
  </section>
}
