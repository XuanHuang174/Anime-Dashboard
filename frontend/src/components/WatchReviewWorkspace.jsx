import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import api from '../api/api.js'
import { seriesSession } from '../hooks/useSeriesSession.js'
import { createReviewController, currentReviewMonth, REVIEW_KINDS, REVIEW_SECTIONS, reviewParams, reviewPeriod, reviewTitle, shiftReviewMonth } from '../utils/watchReview.js'
import { formatWatchDate, WATCH_LABELS } from '../utils/watchHistory.js'
import { safeExternalUrl } from '../utils/journalSourceLinks.js'
import PersonalScore from './PersonalScore.jsx'
import './WatchReviewWorkspace.css'
import WatchReviewExporter from './WatchReviewExporter.jsx'

function Cover({ work }) {
  const [broken, setBroken] = useState(false)
  const src = safeExternalUrl(work.poster_url)
  return <div className="review-cover">{src && !broken ? <img src={src} alt="" loading="lazy" onError={() => setBroken(true)} /> : <span aria-hidden="true">▤</span>}</div>
}

function ReviewEntry({ row, section, onOpenRecord }) {
  const work = row.work, fact = row.entry_type === 'fact'
  const needsDate = ['uncertain', 'undated'].includes(section)
  return <article className={`review-entry${fact ? ' review-entry--fact' : ''}`} aria-label={`${work.title}${fact ? ` · ${REVIEW_KINDS[row.kind]}` : ''}`}>
    <Cover key={`${work.record_id}:${work.poster_url}`} work={work} />
    <div className="review-entry-main"><button type="button" className="review-title-button" onClick={() => onOpenRecord(work.record_id, { readOnly: true })}>{work.title}</button>
      {work.original_title && <p className="review-original">{work.original_title}</p>}
      <div className="review-badges">{fact ? <><span>{REVIEW_KINDS[row.kind]}</span><span>第 {row.round_number} 轮{row.is_rewatch ? ' · 重看' : ''}</span>
        <span className={`review-date review-date--${row.attribution}`}>{formatWatchDate(row.actual_date)}{row.date_precision === 'month' ? ' · 仅月份' : row.date_precision === 'year' ? ' · 仅年份' : ''}</span></>
        : <>{Object.entries(row.matched_kinds).map(([kind, count]) => <span key={kind}>{REVIEW_KINDS[kind]} {count}</span>)}<span className="review-date">最近相关：{formatWatchDate(row.latest_actual_date)}</span></>}
      </div>
      {fact && row.kind === 'completed' && <p className="review-round-rating">本轮评分：{row.rating === null ? '未记录' : <PersonalScore score={row.rating} stars={false} suffix />}<small>{row.rating_origin === 'completion_snapshot' ? '完成时记录' : row.rating_origin === 'manual' ? '手动填写／更正' : '未用当前综合评分补齐'}</small></p>}
      {fact && row.note && <div className="review-thought">{row.note.length > 180 ? <><p>{row.note.slice(0, 180)}…</p><details><summary>展开完整文字</summary><p>{row.note}</p></details></> : <p>{row.note}</p>}</div>}
      {needsDate && <p className="review-date-explanation">{section === 'uncertain' ? '日期范围与所选区间部分相交，未计入确定统计。' : '来自本人全部历程，无法确定属于所选区间，未计入本期统计。'}</p>}
      <p className="review-current">当前手账：{WATCH_LABELS[work.current_watch_status]}{work.current_score !== null && <> · 综合评分 <PersonalScore score={work.current_score} stars={false} /></>}</p>
      {fact && <details className="review-source"><summary>查看依据</summary><p>{row.date_source === 'effective_round' ? '实际日期来自当前有效轮次；轮次日期更正后回顾随之更新。' : row.kind === 'memory' ? '按补录中明确填写的实际日期归属，录入时间不作为观看日期。' : '按这条历程中明确填写的实际日期归属。'}</p>
        {row.source_kind && <p>感想附于状态／进度操作，文字本身不增加观看数量。</p>}
        {row.recorded_at && <p>系统录入时间：{new Date(row.recorded_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}（不是观看日期）</p>}
      </details>}
    </div>
    <div className="review-entry-actions"><button type="button" onClick={() => onOpenRecord(work.record_id, { readOnly: true })}>查看详情</button>
      {needsDate && <button type="button" className="review-primary" onClick={() => onOpenRecord(work.record_id, { edit: true, history: true })}>补充／更正日期</button>}
    </div>
  </article>
}

export default function WatchReviewWorkspace({ active, refreshKey, onOpenRecord }) {
  const engine = useMemo(() => createReviewController({ client: api, getSession: seriesSession }), [])
  const state = useSyncExternalStore(engine.subscribe, engine.getSnapshot, engine.getSnapshot)
  const [draft, setDraft] = useState(() => ({ mode: 'month', month: currentReviewMonth(), start: '', end: '' }))
  const [formError, setFormError] = useState(''), [dateInfoOpen, setDateInfoOpen] = useState(false)
  const [exportSource,setExportSource] = useState(null)
  const resultsRef = useRef(null), moveFocus = useRef(false)
  const data = state.data, loading = state.status === 'loading'
  useEffect(() => { engine.activate(); return () => engine.dispose() }, [engine])
  useEffect(() => {
    if (active) engine.refresh()
    return () => engine.cancel()
  }, [active, refreshKey, engine])
  useEffect(() => {
    if (!active || exportSource) return undefined
    let timer
    const refresh = () => { if (document.visibilityState !== 'hidden') { clearTimeout(timer); timer = setTimeout(() => engine.refresh(), 120) } }
    window.addEventListener('focus', refresh); document.addEventListener('visibilitychange', refresh)
    return () => { clearTimeout(timer); window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', refresh) }
  }, [active, engine, exportSource])
  useEffect(() => {
    if (state.status === 'ready' && moveFocus.current && active) {
      moveFocus.current = false; resultsRef.current?.focus({ preventScroll: true })
      resultsRef.current?.scrollIntoView({ block: 'start', behavior: 'instant' })
    }
  }, [state.status, active])
  function apply(next = draft) {
    try { const params = reviewParams(next); setFormError(''); setDraft(next); engine.read({ params, page: 1, fresh: true }) }
    catch (error) { setFormError(error.message) }
  }
  const section = name => { moveFocus.current = true; engine.read({ section: name, page: 1 }) }
  const page = next => { moveFocus.current = true; engine.read({ page: next }) }
  const period = data?.period || reviewPeriod(state.params)
  let periodDraftChanged = true
  try { periodDraftChanged = JSON.stringify(reviewParams(draft)) !== JSON.stringify(state.params) } catch { /* An incomplete date draft stays separate from the applied report. */ }
  const s = data?.summary, c = data?.coverage
  const cards = [
    ['started', '开始观看', s?.started_works, '部作品', s ? `${s.started_rounds} 个轮次开始` : '按实际开始日期'],
    ['completed', '完成作品', s?.completed_works, '部作品', s ? `${s.completed_rounds} 个完成轮次，含重看` : '按作品去重，轮次另计'],
    ['rewatch', '重看完成', s?.rewatch_completed_rounds, '个轮次', s ? `本期另有 ${s.rewatch_started_rounds} 个重看轮次开始` : '不把重看算成新增作品'],
    ['notes', '随记与补录', s?.notes, '条感想', s ? `其中 ${s.memories} 条为补录往事` : '按明确填写的实际日期'],
  ]
  const pagination = where => data && data.count > data.page_size && <nav className="review-pagination" aria-label={`${where}回顾分页`}>
    <button type="button" disabled={loading || data.page <= 1} onClick={() => page(data.page - 1)}>上一页回顾</button>
    <span>{data.page} / {Math.max(1, Math.ceil(data.count / data.page_size))} 页</span>
    <button type="button" disabled={loading || !data.next_page} onClick={() => page(data.next_page)}>下一页回顾</button>
  </nav>
  return <section className="watch-review-workspace" hidden={!active} aria-label="私人观看回顾工作区">
    <header className="review-header"><div><span className="review-sticker">WATCH REVIEW</span><h2><i className="fa-solid fa-clock-rotate-left" aria-hidden="true" /> 观看回顾</h2><p>回到那些与动画相遇的日子 <span><i className="fa-solid fa-lock" aria-hidden="true" /> 仅本人可见 · 只读查看</span></p></div>
      <div className="review-entry-actions"><button type="button" disabled={loading||!data} onClick={()=>setExportSource(data)}>导出回顾长图</button><button type="button" disabled={loading} onClick={() => engine.refresh()}>刷新回顾</button></div></header>
    <div className="review-controls"><div className="review-period-modes" role="group" aria-label="回顾时间类型">
      <button type="button" aria-pressed={draft.mode === 'month'} onClick={() => { setDraft(d => ({ ...d, mode: 'month' })); setFormError('') }}>按月份</button>
      <button type="button" aria-pressed={draft.mode === 'custom'} onClick={() => { setDraft(d => ({ ...d, mode: 'custom' })); setFormError('') }}>自选时间段</button></div>
      <form className="review-period-form" aria-label="选择回顾时间" onSubmit={event => { event.preventDefault(); apply() }}>
        {draft.mode === 'month' ? <><label>回顾月份<input type="month" required min="1000-01" max="9999-12" value={draft.month} onChange={event => setDraft(d => ({ ...d, month: event.target.value }))} /></label>
          <div className="review-month-step"><button type="button" disabled={!draft.month || draft.month === '1000-01'} onClick={() => { try { apply({ ...draft, month: shiftReviewMonth(draft.month, -1) }) } catch (e) { setFormError(e.message) } }}>上个月</button>
            <button type="button" disabled={!draft.month || draft.month === '9999-12'} onClick={() => { try { apply({ ...draft, month: shiftReviewMonth(draft.month, 1) }) } catch (e) { setFormError(e.message) } }}>下个月</button></div></>
          : <><label>开始日期<input type="date" required min="1000-01-01" max="9999-12-31" value={draft.start} onChange={event => setDraft(d => ({ ...d, start: event.target.value }))} /></label>
            <label>结束日期<input type="date" required min="1000-01-01" max="9999-12-31" value={draft.end} onChange={event => setDraft(d => ({ ...d, end: event.target.value }))} /></label></>}
        <button type="submit" className="review-primary">查看回顾</button><button type="button" onClick={() => apply({ ...draft, mode: 'month', month: currentReviewMonth() })}>回到本月</button>
      </form>{formError && <p role="alert" className="review-error">{formError}</p>}
      <p className="review-range-note">自选区间包含起止当天，最多 366 天；只统计本人历程，不受手账筛选影响。</p>
      {periodDraftChanged && <p className="review-draft-hint" role="status">时间选择尚未应用，下方仍为已应用的回顾。点击“查看回顾”更新。</p>}
    </div>
    <div className="review-report-heading"><div><h3>{reviewTitle(period)}</h3><p>{loading ? '正在读取完整回顾…' : '依据当前有效历程实时计算'}</p></div></div>
    <div className="review-summary" aria-label="本期回顾摘要" aria-busy={loading}>{cards.map(([key, title, count, unit, hint]) => <button type="button" key={key} className={`review-stat review-stat--${key}`} disabled={!data || loading} onClick={() => section(key)}>
      <span>{title} <i className="fa-solid fa-arrow-down" aria-hidden="true" /></span><strong>{count ?? '—'}<small>{unit}</small></strong><p>{hint}</p></button>)}</div>
    {s && <p className="review-score-summary">本期完成轮次评分：<strong>{s.completed_round_rating.average ?? '暂无评分'}</strong>{s.completed_round_rating.average !== null && ' / 10'} · {s.completed_round_rating.count} 个已评分轮次，{s.completed_round_rating.missing_count} 个未记录评分。<span>不以当前综合评分补缺。</span></p>}
    <details className="review-date-help" open={dateInfoOpen} onToggle={event => setDateInfoOpen(event.currentTarget.open)}><summary>日期说明与待补充记录{c && <span>可能相关 {c.uncertain.works} 部 · 日期待补充 {c.undated.works} 部</span>}</summary>
      <p>年月／年份的完整可能范围落入所选区间才计入统计。部分相交只列为“可能相关”，不猜测某一天；录入时间和计划日期不作为观看日期。</p>
      <div className="review-date-actions"><button type="button" disabled={!data || loading} onClick={() => section('uncertain')}>查看可能相关{c ? `（${c.uncertain.facts} 条）` : ''}</button>
        <button type="button" disabled={!data || loading} onClick={() => section('undated')}>查看日期待补充{c ? `（${c.undated.facts} 条）` : ''}</button></div>
      <p>“日期待补充”来自本人全部历程，无法判断属于哪个月份；这两类均未计入上方确定数量。可打开作品历程补充或更正。</p>
    </details>
    <div className="review-results-header" ref={resultsRef} tabIndex={-1}><h3>{REVIEW_SECTIONS[state.section]}{data && <small>共 {data.count} {data.count_unit === 'works' ? '部作品' : '条记录'}</small>}</h3>
      <label>每页显示<select aria-label="回顾每页数量" value={state.pageSize} onChange={event => { moveFocus.current = true; engine.read({ pageSize: Number(event.target.value), page: 1 }) }}>{[12, 24, 48].map(n => <option key={n} value={n}>{n} 条</option>)}</select></label></div>
    <div className="review-section-tabs" role="group" aria-label="回顾内容">{['works', 'completed', 'started', 'rewatch', 'activity', 'notes'].map(key => <button type="button" key={key} aria-pressed={state.section === key} onClick={() => section(key)}>{REVIEW_SECTIONS[key]}</button>)}</div>
    {state.section === 'undated' && <p className="review-section-note">本人全部历程中日期未知的记录，无法归入本期。数量不是当月漏记数量。</p>}
    {state.section === 'uncertain' && <p className="review-section-note">可能日期范围与所选区间部分相交；这些记录没有计入本期确定统计。</p>}
    <div aria-live="polite" className="review-live-status">{loading && <p role="status">正在读取回顾，请稍候…</p>}{state.message && <p role="status">{state.message}</p>}</div>
    {state.error && <div className="review-error" role="alert"><p>{state.error}</p><button type="button" onClick={() => engine.refresh()}>重试回顾</button></div>}
    {data && <>{pagination('顶部')}<div className={`review-entries${state.section === 'works' ? ' review-entries--works' : ''}`}>{data.results.map(row => <ReviewEntry key={row.id || row.work.record_id} row={row} section={state.section} onOpenRecord={onOpenRecord} />)}</div>
      {!data.count && <div className="review-empty"><span aria-hidden="true">▤</span><h4>{state.section === 'undated' ? '暂无日期待补充记录' : state.section === 'uncertain' ? '没有需要判断日期范围的记录' : state.section === 'notes' ? '这段时间暂无可归属的感想' : '这段时间暂无可归属的记录'}</h4>
        <p>{['uncertain', 'undated'].includes(state.section) ? '可以查看其他回顾内容。' : '未记录实际日期的旧数据不会被当作本期观看，也不代表这段时间没有观看。可调整区间，或在日期说明中查看待补充记录。'}</p></div>}
      {pagination('底部')}<p className="review-footer-note">作品名称、海报与综合评分显示当前手账资料。回顾随日期更正、撤销、清空和备份恢复更新。</p></>}
    {exportSource&&<WatchReviewExporter source={exportSource} onClose={()=>setExportSource(null)}/>}
  </section>
}
