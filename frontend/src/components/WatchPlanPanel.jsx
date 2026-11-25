import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import useWatchPlan from '../hooks/useWatchPlan.js'
import useSeriesSession from '../hooks/useSeriesSession.js'
import { datePart } from '../utils/watchHistory.js'
import { PLAN_PRIORITIES, PLAN_PURPOSES, PLAN_STATES, buildPlanCommand, dateValue, planDateLabel, planForm, plannedTimePassed } from '../utils/watchPlans.js'
import './WatchPlans.css'

export function PlanDateField({ label, part, onChange }) {
  return <fieldset className="plan-date"><legend>{label}</legend><select aria-label={`${label}精度`} value={part.precision} onChange={event => onChange({ precision: event.target.value, value: '' })}>
    <option value="unknown">未定／未记录</option><option value="year">年份</option><option value="month">年月</option><option value="day">具体日期</option>
  </select>{part.precision !== 'unknown' && <input aria-label={label} type={{ year: 'number', month: 'month', day: 'date' }[part.precision]} value={part.value} min={part.precision === 'year' ? 1000 : undefined} max={part.precision === 'year' ? 9999 : undefined} placeholder={part.precision === 'year' ? '例如 2030' : undefined} onChange={event => onChange({ ...part, value: event.target.value })} />}</fieldset>
}
export function PlanError({ snapshot, engine, onRetry, localError = '' }) {
  if (snapshot.status === 'saving' || !snapshot.error && !snapshot.pending && !localError) return null
  return <div className="plan-error" role="alert" tabIndex={-1}>{localError || snapshot.error || '原操作结果尚待核对。'}
    {snapshot.conflict && <p>填写内容已保留。请重新读取后核对，不能直接覆盖。</p>}
    {snapshot.pending && <><p>操作可能已经保存。重试沿用原编号，不会重复开始或重新应用旧顺序。</p>{onRetry ? <button type="button" disabled={snapshot.status === 'loading'} onClick={onRetry}>重试原操作并核对结果</button> : <p>请进入编辑模式核对原操作。</p>}</>}
    {!snapshot.pending && snapshot.status === 'error' && <button type="button" onClick={() => engine.read()}>重新读取计划</button>}
  </div>
}
export default function WatchPlanPanel(props) {
  const session = useSeriesSession()
  return <PlanPanel key={`${session}:${props.item.id}`} {...props} />
}
function PlanPanel({ item, editable = false, initiallyOpen = false, initialAction = '', disabled = false, closeGuardRef, onLockChange, onCommitted, onManage, onHistory }) {
  const [open, setOpen] = useState(initiallyOpen), [form, setForm] = useState(null), [baseline, setBaseline] = useState(''), [confirm, setConfirm] = useState(null), [localError, setLocalError] = useState(''), [notice, setNotice] = useState('')
  const root = useRef(null), focusOrigin = useRef(null), autoOpened = useRef(false)
  const { engine, snapshot } = useWatchPlan({ recordId: item.id, enabled: open || !editable, onCommitted })
  const data = snapshot.data, plan = data?.plan, busy = snapshot.status === 'saving', loading = snapshot.status === 'loading'
  useEffect(() => {
    const state = engine.getSnapshot(), revision = item.rawRecord?.watch_revision
    if (open && state.data && Number.isSafeInteger(revision) && revision > state.data.record.watch_revision && !['loading', 'saving'].includes(state.status) && !state.pending) engine.read()
  }, [engine, open, item.rawRecord?.watch_revision])
  const dirty = form && JSON.stringify(form) !== baseline, locked = busy || Boolean(snapshot.pending) || Boolean(form)
  const blocked = disabled || busy || loading || Boolean(snapshot.pending) || snapshot.status !== 'ready' || snapshot.conflict
  useEffect(() => { onLockChange?.(locked); return () => onLockChange?.(false) }, [locked, onLockChange])
  useEffect(() => {
    if (!dirty && !snapshot.pending && !busy) return
    const warn = event => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn)
  }, [dirty, snapshot.pending, busy])
  useEffect(() => {
    if (!localError && !snapshot.error) return
    const frame = requestAnimationFrame(() => { const node = root.current?.querySelector('.plan-error'); node?.scrollIntoView({ block: 'nearest' }); node?.focus({ preventScroll: true }) })
    return () => cancelAnimationFrame(frame)
  }, [localError, snapshot.error])
  const guard = (run, label = '离开') => {
    if (busy) { setNotice('正在保存安排，请等待本次操作完成。'); return }
    if (dirty || snapshot.pending) { setOpen(true); setConfirm({ run, label }); requestAnimationFrame(() => root.current?.querySelector('.plan-leave button')?.focus()); return }
    setForm(null); run()
  }
  useLayoutEffect(() => {
    if (closeGuardRef) closeGuardRef.current = guard
    return () => { if (closeGuardRef?.current === guard) closeGuardRef.current = null }
  }, [closeGuardRef, guard])
  const startForm = action => guard(() => {
    const next = action === 'metadata' ? planForm(plan, data.version) : { action, version: data.version, watch_version: data.record.watch_revision, occurred: datePart() }
    focusOrigin.current = document.activeElement
    setForm(next); setBaseline(JSON.stringify(next)); setLocalError(''); setNotice(''); setConfirm(null)
    requestAnimationFrame(() => { const node = root.current?.querySelector('.plan-form'); node?.scrollIntoView({ block: 'nearest' }); node?.querySelector('select,input,textarea,button')?.focus({ preventScroll: true }) })
  }, '切换安排操作')
  const change = (key, value) => { setForm(old => ({ ...old, [key]: value })); setLocalError('') }
  const finish = () => { setForm(null); setBaseline(''); setNotice('观看安排已保存。'); requestAnimationFrame(() => root.current?.querySelector('.plan-summary')?.focus({ preventScroll: true })) }
  const submit = async event => {
    event.preventDefault(); if (!editable || blocked || form.version !== data.version) return
    try {
      const body = ['enroll', 'update'].includes(form.action) ? buildPlanCommand(form) : form.action === 'remove' ? { action: 'remove', version: form.version } : { action: 'start', version: form.version, watch_version: form.watch_version, occurred_on: dateValue(form.occurred) }
      if (await engine.submit(body)) finish()
    } catch (error) { setLocalError(error.message) }
  }
  const retry = async () => { if (await engine.submit(null, true)) finish() }
  const isActive = plan && ['pending', 'ongoing'].includes(plan.state)
  const canStart = plan?.state === 'pending' && (plan.purpose === 'rewatch' ? data.record.watch_status === 'completed' && plan.target_round_number === null : ['plan', 'on_hold'].includes(data.record.watch_status))
  const startLabel = plan?.purpose === 'rewatch' ? '开始重看' : data?.record.watch_status === 'on_hold' ? '恢复观看' : '开始观看'
  useEffect(() => {
    if (initialAction !== 'start' || autoOpened.current || !data || snapshot.status !== 'ready' || !editable) return
    autoOpened.current = true
    if (canStart) startForm('start')
    else setNotice('当前安排或观看状态已变化，请核对后选择下一步操作。')
  }, [initialAction, data, snapshot.status, editable, canStart])
  if (!editable && !plan && snapshot.status !== 'error') return null
  return <section ref={root} className="watch-plans watch-plan-panel" aria-label="观看安排">
    {editable ? <button type="button" className="plan-panel-toggle" aria-expanded={open} onClick={() => open ? guard(() => setOpen(false), '收起安排') : setOpen(true)}><span><i className="fa-solid fa-flag" aria-hidden="true" /> 观看安排</span><span>{open ? '收起' : '展开'}</span></button> : <h3>观看安排 · 仅本人可见</h3>}
    {(open || !editable) && <div className="watch-plan-body">
      {confirm && <div className="plan-leave" role="group" aria-label="确认离开计划草稿"><p>{snapshot.pending ? '操作可能已经保存。离开后请先检查计划，不要重复创建或开始。' : '有未保存的观看安排，是否放弃填写？'}</p><button type="button" onClick={() => setConfirm(null)}>继续编辑安排</button><button type="button" onClick={() => { const task = confirm.run; setForm(null); setBaseline(''); setConfirm(null); task() }}>{snapshot.pending ? '离开并稍后核对' : `放弃安排草稿并${confirm.label}`}</button></div>}
      {disabled && <p className="plan-hint">请先保存当前作品修改，再操作观看安排；两者分别保存。</p>}
      {(loading || busy) && <p role="status">{busy ? '正在保存安排…' : '正在读取安排…'}</p>}
      <PlanError snapshot={snapshot} engine={engine} onRetry={retry} localError={localError} />
      {notice && <p role="status" className="plan-hint">{notice}</p>}
      {data && <>
        <div className="plan-summary" tabIndex={-1}>{plan ? <><span className={`plan-priority priority-${plan.priority}`}><i className="fa-solid fa-flag" aria-hidden="true" /> {PLAN_PRIORITIES[plan.priority]}</span><strong>{PLAN_PURPOSES[plan.purpose]} · {PLAN_STATES[plan.state]}</strong><p>计划开始：{planDateLabel(plan.planned_for)}{plannedTimePassed(plan.planned_for) && <small> · 可重新安排</small>}</p>{plan.note && <p className="plan-note">{plan.note}</p>}</> : <p>还没有观看安排。不设日期，也可以先排好优先级。</p>}</div>
        {editable ? <div className="plan-actions"><button type="button" disabled={blocked} onClick={() => startForm('metadata')}>{isActive ? '调整安排' : '加入观看计划'}</button>
          {canStart && <button type="button" className="plan-primary" disabled={blocked} onClick={() => startForm('start')}>{startLabel}</button>}
          {plan?.state === 'ongoing' && onHistory && <button type="button" disabled={blocked} onClick={() => guard(onHistory, '前往观看历程')}>记录观看进度</button>}
          {isActive && <button type="button" disabled={blocked} onClick={() => startForm('remove')}>移出计划</button>}
          <button type="button" disabled={busy || loading} onClick={() => engine.read()}>重新读取安排</button>
        </div> : onManage && <button type="button" onClick={onManage}>前往编辑安排</button>}
        {editable && plan?.state === 'pending' && !canStart && <p className="plan-hint">当前状态与此安排不一致，请到观看历程核对；不会根据普通状态修改自动开始新一轮。</p>}
        {form && <form className="plan-form" noValidate onSubmit={submit}><h4>{form.action === 'start' ? `确认${startLabel}` : form.action === 'remove' ? '确认移出计划' : '安排下一次观看'}</h4>
          {['enroll', 'update'].includes(form.action) ? <fieldset disabled={blocked}><p className="plan-hint">仅调整意愿，不更改实际观看状态或评分。{form.action === 'enroll' && data.record.watch_status === 'completed' ? '这部作品已看过，将加入“准备重看”安排；不会立即开始或清零进度。' : ''}</p><label>观看优先级<select aria-label="观看优先级" value={form.priority} onChange={event => change('priority', Number(event.target.value))}>{Object.entries(PLAN_PRIORITIES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><PlanDateField label="计划开始时间" part={form.planned} onChange={value => change('planned', value)} /><label>计划备注<textarea aria-label="计划备注" rows={3} maxLength={500} value={form.note} onChange={event => change('note', event.target.value)} placeholder="例如：等当前作品看完再开始" /><small>{form.note.length} / 500</small></label></fieldset>
            : form.action === 'start' ? <fieldset disabled={blocked}><p className="plan-hint">{plan.purpose === 'rewatch' ? '保留上一轮，新一轮进度从 0 集开始。综合评分与长评不变。' : '将实际观看状态改为在看，保留当前进度。'}计划日期不会自动当作实际日期。</p><PlanDateField label="实际开始日期" part={form.occurred} onChange={value => change('occurred', value)} /></fieldset>
            : <p>仅将安排移出当前队列，保留作品、进度、评分和观看历程。之后可重新安排。</p>}
          {form.version !== data.version && <div className="plan-hint">安排或历程已变化，原填写仍保留。{['enroll', 'update'].includes(form.action) && (form.action === 'update' ? isActive : !isActive) ? <button type="button" disabled={blocked} onClick={() => change('version', data.version)}>核对后使用最新计划版本</button> : '请取消本次操作后重新选择，核对最新状态。'}</div>}
          <div className="plan-actions"><button type="submit" className="plan-primary" disabled={blocked || form.version !== data.version}>{form.action === 'start' ? `确认${startLabel}` : form.action === 'remove' ? '确认移出' : '保存观看安排'}</button><button type="button" disabled={busy} onClick={() => guard(() => { setForm(null); setLocalError(''); focusOrigin.current?.focus() }, '取消填写')}>取消安排填写</button></div>
        </form>}
      </>}
    </div>}
  </section>
}
