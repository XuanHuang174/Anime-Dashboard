import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import api from '../api/api.js'
import useSeriesSession, { seriesSession } from '../hooks/useSeriesSession.js'
import { changedCurationFields, createCurationController, curationError, loadCompleteCuration, moveCuration } from '../utils/curations.js'
import { WATCH_LABELS } from '../utils/watchHistory.js'
import CurationDialog from './CurationDialog.jsx'
import CurationPicker from './CurationPicker.jsx'
import CurationArtwork from './CurationArtwork.jsx'
import './Curations.css'

const emptyForm = { title: '', description: '', pinned: false, archived: false }
const titleOf = entry => entry.record?.title || entry.title_snapshot.title || '已移除作品'
function OrderRow({ entry, index, count, blocked, move, drag }) {
  const [position, setPosition] = useState(String(index+1)), title = titleOf(entry)
  useEffect(() => setPosition(String(index+1)), [index])
  return <li onDragOver={event => { if (!blocked) event.preventDefault() }} onDrop={event => { event.preventDefault(); if (!blocked && drag.current) move(drag.current, index); drag.current = null }}>
    <button type="button" className="plan-grip" aria-label={`拖动${title}`} draggable={!blocked} disabled={blocked} onDragStart={event => { drag.current = entry.id; event.dataTransfer.setData('text/plain', entry.id); event.dataTransfer.effectAllowed = 'move' }} onDragEnd={() => { drag.current = null }}>⠿</button>
    <span>{index+1}. {title}{entry.unavailable && <small>作品已移除 · 保留原条目</small>}</span><div className="plan-actions">
      <button type="button" aria-label={`将${title}上移`} disabled={blocked || index === 0} onClick={() => move(entry.id, index-1)}>↑ 上移</button><button type="button" aria-label={`将${title}下移`} disabled={blocked || index === count-1} onClick={() => move(entry.id, index+1)}>↓ 下移</button>
      <form className="curation-position" onSubmit={event => { event.preventDefault(); move(entry.id, Number(position)-1) }}><input aria-label={`将${title}移至位置`} type="number" min="1" max={count} value={position} disabled={blocked} onChange={event => setPosition(event.target.value)} /><button type="submit" disabled={blocked || !Number.isInteger(Number(position)) || Number(position) < 1 || Number(position) > count}>移动</button></form>
    </div>
  </li>
}

export default function CurationWorkspace({ active, editable, refreshKey, paused, onEnterEdit, onOpenRecord, closeGuardRef, quickFilters = [] }) {
  const session = useSeriesSession(), engine = useMemo(() => createCurationController({ client: api, getSession: seriesSession }), [session])
  const state = useSyncExternalStore(engine.subscribe, engine.getSnapshot), data = state.data
  const [listId, setListId] = useState(null), [home, setHome] = useState({ search: '', state: 'active', pinned: 'any', page: 1 }), [search, setSearch] = useState('')
  const [dialog, setDialog] = useState(null), [form, setForm] = useState(emptyForm), [selected, setSelected] = useState([])
  const [order, setOrder] = useState(null), [ids, setIds] = useState([]), [confirm, setConfirm] = useState(null)
  const [preparing, setPreparing] = useState(false), [prepareError, setPrepareError] = useState(''), [message, setMessage] = useState('')
  const root = useRef(null), mounted = useRef(true), prepareJob = useRef(null), preparingGate = useRef(false), drag = useRef(null)
  const wasActive = useRef(false), wasPaused = useRef(false), lastRefresh = useRef(refreshKey), returnHome = useRef(null), moveFocus = useRef(null), confirmOrigin = useRef(null)
  const collection = data?.collection?.id === listId ? data.collection : null
  const busy = state.status === 'saving', loading = state.status === 'loading', blocked = busy || loading || preparing || !!state.pending || state.conflict || state.needsRead || state.status !== 'ready'
  const formDirty = dialog?.base && Object.keys(dialog.base).some(key => form[key] !== dialog.base[key])
  const orderDirty = order && JSON.stringify(ids) !== JSON.stringify(order.rows.map(row => row.id))
  const dirty = !!(formDirty || orderDirty || selected.length)
  useEffect(() => { mounted.current = true; engine.activate(); return () => { mounted.current = false; prepareJob.current?.abort(); engine.dispose() } }, [engine])
  const homeRead = (next = home) => engine.read({ ...next, listId: null, page_size: 12, version: undefined })
  const detailRead = (id = listId, page = data?.page || 1) => engine.read({ listId: id, page, page_size: 12, version: undefined, search: undefined, state: undefined, pinned: undefined })
  const refresh = () => listId ? detailRead() : homeRead()
  useEffect(() => {
    if (active && !paused && (!wasActive.current || wasPaused.current || lastRefresh.current !== refreshKey || engine.getSnapshot().status === 'idle')) {
      if (!dirty && !dialog && !order && !engine.getSnapshot().pending) refresh()
      else setMessage('手账资料已有更新；当前调整仍保留，保存时会核对片单版本。')
    }
    wasActive.current = active; wasPaused.current = paused; lastRefresh.current = refreshKey
  }, [active, paused, refreshKey, engine])
  useEffect(() => {
    if (!active || paused || dialog || order || dirty || state.pending || preparing) return
    const read = () => { if (document.visibilityState === 'visible' && engine.getSnapshot().status === 'ready') refresh() }
    window.addEventListener('focus', read); document.addEventListener('visibilitychange', read)
    return () => { window.removeEventListener('focus', read); document.removeEventListener('visibilitychange', read) }
  }, [active, paused, dialog, order, dirty, state.pending, preparing, engine, listId, home, data?.page])
  useEffect(() => {
    if (!dirty && !state.pending && !busy) return
    const warn = event => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn)
  }, [dirty, state.pending, busy])
  const clearDraft = () => { setDialog(null); setOrder(null); setSelected([]); setConfirm(null); setPrepareError('') }
  const guard = (run, label = '关闭面板') => {
    if (busy || preparingGate.current) return
    if (dirty || state.pending) { confirmOrigin.current = document.activeElement; setConfirm({ run, label }); return }
    clearDraft(); run()
  }
  useLayoutEffect(() => { if (closeGuardRef) closeGuardRef.current = guard; return () => { if (closeGuardRef?.current === guard) closeGuardRef.current = null } }, [guard, closeGuardRef])
  useEffect(() => { if (confirm) root.current?.querySelector('.curation-leave button')?.focus() }, [confirm])
  useLayoutEffect(() => {
    if (!active || loading || state.status !== 'ready' || !moveFocus.current) return
    const target = moveFocus.current; moveFocus.current = null
    const node = (target.id ? root.current?.querySelector(`[data-curation-id="${target.id}"] button`) : target.entry ? root.current?.querySelector(`[data-entry-id="${target.entry}"] button`) : root.current?.querySelector('.curation-section-title')) || root.current?.querySelector('.plan-workspace-header h2')
    node?.focus({ preventScroll: true })
    if (target.scrollY !== undefined && target.width === window.innerWidth) window.scrollTo({ top: target.scrollY, behavior: 'instant' })
    else node?.scrollIntoView({ block: target.id ? 'center' : 'start', behavior: 'instant' })
  }, [active, loading, state.status, data])
  const openCollection = id => {
    returnHome.current = { scrollY: window.scrollY, width: window.innerWidth, id, page: data.page }
    setHome(value => ({ ...value, page: data.page })); setListId(id); moveFocus.current = {}; detailRead(id, 1)
  }
  const back = () => guard(() => { setListId(null); moveFocus.current = returnHome.current || {}; homeRead() }, '返回片单首页')
  async function prepare(kind, entry = null) {
    if (blocked || preparingGate.current) return
    preparingGate.current = true; setPreparing(true); setPrepareError('')
    const controller = new AbortController(), owner = seriesSession(), trigger = document.activeElement; prepareJob.current = controller
    try {
      const complete = await loadCompleteCuration(api, listId, data.version, controller.signal, () => mounted.current && owner === seriesSession())
      if (!mounted.current || controller.signal.aborted || owner !== seriesSession()) return
      if (kind === 'order') { setOrder(complete); setIds(complete.rows.map(row => row.id)); return }
      const base = kind === 'metadata' ? { title: complete.collection.title, description: complete.collection.description, pinned: complete.collection.pinned, archived: complete.collection.archived, cover_entry_id: complete.collection.cover_entry_id || '' } : null
      setSelected([]); setDialog({ kind, complete, entry, version: complete.version, base, trigger }); if (base) setForm(base)
    } catch (error) { if (mounted.current && !controller.signal.aborted && owner === seriesSession()) setPrepareError(curationError(error)) }
    finally { if (prepareJob.current === controller) { preparingGate.current = false; if (mounted.current && owner === seriesSession()) setPreparing(false) } }
  }
  async function finish(result) {
    if (!result) return
    const { data: ack, body } = result
    clearDraft(); setMessage(ack.replayed ? '已核对原操作结果，未重复提交新操作。' : '片单已保存。')
    if (body.action === 'delete' || !ack.collection) { setListId(null); moveFocus.current = returnHome.current || {}; await homeRead(); return }
    setListId(ack.list_id)
    moveFocus.current = body.action === 'update-entry' ? { entry: body.entry_id, scrollY: window.scrollY, width: window.innerWidth } : {}
    await detailRead(ack.list_id, body.action === 'update-entry' ? data?.page || 1 : 1)
  }
  const submit = async body => { if (editable) await finish(await engine.submit(body)) }
  const retry = async () => { if (editable) await finish(await engine.submit(null, true)) }
  function saveForm(event) {
    event.preventDefault()
    if (blocked || !dialog) return
    const common = { version: dialog.version, ...(dialog.kind !== 'create' ? { list_id: listId } : {}) }
    if (dialog.kind === 'create') submit({ ...common, action: 'create', ...form, title: form.title.trim() })
    if (dialog.kind === 'metadata') submit({ ...common, action: 'update', ...changedCurationFields(dialog.base, form) })
    if (dialog.kind === 'reason') submit({ ...common, action: 'update-entry', entry_id: dialog.entry.id, reason: form.reason })
  }
  const leave = confirm && <div className="plan-leave curation-leave" role="group" aria-label="确认离开片单调整"><p>{state.pending ? '保存结果尚未确认。可先重试原操作核对；离开后仍需核对，避免重复创建。' : '还有尚未保存的片单调整，是否放弃本次草稿？'}</p><div className="plan-actions"><button type="button" onClick={() => { setConfirm(null); confirmOrigin.current?.isConnected && confirmOrigin.current.focus() }}>继续编辑片单</button><button type="button" onClick={() => { const run = confirm.run; clearDraft(); run() }}>{state.pending ? `离开并稍后核对` : `放弃草稿并${confirm.label}`}</button></div></div>
  const problem = (state.error || prepareError) && <div role="alert" tabIndex={-1} className="plan-error">{state.error || prepareError}{state.pending && <p>操作可能已保存，请使用原操作编号核对结果。</p>}<div className="plan-actions">{state.pending ? editable ? <button type="button" disabled={busy} onClick={retry}>重试原片单操作并核对结果</button> : <button type="button" onClick={onEnterEdit}>进入编辑核对结果</button> : <button type="button" disabled={busy || preparing} onClick={() => guard(() => { setPrepareError(''); refresh() }, '重新读取片单')}>重新读取片单</button>}</div></div>
  const close = () => guard(() => {}, '关闭面板')
  return <section ref={root} className="watch-plans plan-workspace curation-workspace" hidden={!active} aria-label="私人片单工作区">
    <header className="plan-workspace-header"><div><span className="plan-sticker">MY COLLECTION</span><h2 tabIndex={-1}>主题片单</h2><p>把喜欢的作品，编成自己的主题 <span className="plan-private">🔒 仅本人可见{!editable ? ' · 只读预览' : ''}</span></p></div>{editable ? <button type="button" className="plan-primary" disabled={blocked || !!dialog || !!order} onClick={() => { setForm(emptyForm); setDialog({ kind: 'create', base: emptyForm, version: data.version }); setPrepareError('') }}>＋ 新建片单</button> : <button type="button" onClick={() => guard(onEnterEdit, '进入编辑管理')}>进入编辑管理</button>}</header>
    {!dialog && <>{leave}{problem}</>}{(message || state.notice) && <p className="curation-message" role="status">{state.notice || message}</p>}
    {preparing && <p role="status">正在核对完整片单… <button type="button" onClick={() => { const job = prepareJob.current; prepareJob.current = null; job?.abort(); preparingGate.current = false; setPreparing(false) }}>取消读取</button></p>}
    {(loading || busy) && <p role="status">{busy ? '正在保存片单…' : '正在读取片单…'}</p>}
    {!listId && <>
      <form className="plan-controls curation-toolbar" onSubmit={event => { event.preventDefault(); const next = { ...home, page: 1, search: search.trim() }; setHome(next); homeRead(next) }}>
        <label>搜索片单<input value={search} aria-label="搜索主题片单" maxLength={80} disabled={blocked && state.status !== 'error'} onChange={event => setSearch(event.target.value)} placeholder="按片单名称搜索"/></label>
        <label>片单范围<select aria-label="片单范围" value={home.state} disabled={busy || preparing || !!state.pending} onChange={event => { const next = { ...home, state: event.target.value, page: 1 }; setHome(next); homeRead(next) }}><option value="active">当前片单</option><option value="archived">已归档</option><option value="all">全部片单</option></select></label>
        <label>置顶筛选<select aria-label="置顶筛选" value={home.pinned} disabled={busy || preparing || !!state.pending} onChange={event => { const next = { ...home, pinned: event.target.value, page: 1 }; setHome(next); homeRead(next) }}><option value="any">全部</option><option value="true">仅置顶</option><option value="false">未置顶</option></select></label>
        <button type="submit" disabled={busy || preparing || !!state.pending}>搜索片单</button><button type="button" disabled={busy || preparing || !!state.pending} onClick={() => homeRead()}>刷新片单</button>
      </form>
      {data && !data.collection && <><p className="curation-hint">当前匹配 {data.count} 张 · 置顶优先，其余按创建顺序展示。</p><div className="curation-grid" aria-busy={loading}>{data.results.map(row => <article className="curation-card" key={row.id} data-curation-id={row.id}><button type="button" className="curation-card-cover" disabled={blocked} onClick={() => openCollection(row.id)} aria-label={`打开片单${row.title}`}><CurationArtwork source={row.cover?.cover}/></button><div><h3><button type="button" disabled={blocked} onClick={() => openCollection(row.id)}>{row.title}</button></h3><div className="plan-actions"><small>{row.entry_count} 部作品</small>{row.pinned && <span className="curation-badge">置顶</span>}{row.archived && <span className="curation-badge">已归档</span>}</div><p>{row.description || '还没有专题简介，留一段话记录这张片单的主题。'}</p>{row.unavailable_count > 0 && <small>{row.unavailable_count} 项作品已移除，原理由仍保留</small>}</div></article>)}</div>{!data.count && !loading && <div className="plan-empty"><h3>{home.search || home.pinned !== 'any' || home.state !== 'active' ? '没有匹配片单' : '给喜欢的作品一个主题'}</h3><p>{editable ? '可以新建片单，挑选作品并写下入选理由。' : '这里会展示自己整理的私人片单。'}</p></div>}<div className="plan-pagination"><button type="button" disabled={blocked || data.page <= 1} onClick={() => { const next = { ...home, page: data.page-1 }; setHome(next); homeRead(next) }}>上一页片单</button><span>{data.page} / {Math.max(1, Math.ceil(data.count/12))}</span><button type="button" disabled={blocked || !data.next_page} onClick={() => { const next = { ...home, page: data.next_page }; setHome(next); homeRead(next) }}>下一页片单</button></div></>}
    </>}
    {listId && <>
      <div className="plan-actions curation-detail-tools"><button type="button" disabled={busy || preparing} onClick={back}>← 返回片单首页</button><button type="button" disabled={busy || preparing || !!state.pending} onClick={() => guard(refresh, '刷新片单')}>刷新当前片单</button></div>
      {collection && <><div className="plan-controls curation-intro"><div><h3 className="curation-section-title" tabIndex={-1}>{collection.title}</h3><div className="plan-actions"><small>{collection.entry_count} 部作品</small>{collection.pinned && <span className="curation-badge">置顶</span>}{collection.archived && <span className="curation-badge">已归档</span>}</div><p>{collection.description || '暂无专题简介。'}</p></div>{editable && !order && <div className="plan-actions"><button type="button" disabled={blocked} className="plan-primary" onClick={() => prepare('picker')}>＋ 收录作品</button><button type="button" disabled={blocked} onClick={() => prepare('metadata')}>编辑片单</button><button type="button" disabled={blocked || !collection.entry_count} onClick={() => prepare('order')}>调整片单顺序</button><button type="button" disabled={blocked} className="curation-danger" onClick={() => setDialog({ kind: 'delete', version: data.version })}>删除片单</button></div>}</div>
      {order ? <section className="plan-order" aria-label="调整片单顺序"><h3>编排完整片单</h3><p className="curation-hint">共 {ids.length} 项，包含其他分页及失效条目。支持上移、下移、指定位置及桌面拖动；保存后才生效。</p><ol className="plan-order-list curation-order">{ids.map((id, index) => <OrderRow key={id} entry={order.rows.find(row => row.id === id)} index={index} count={ids.length} blocked={blocked} drag={drag} move={(id, position) => setIds(values => moveCuration(values, id, position))}/>)}</ol><div className="plan-actions"><button type="button" className="plan-primary" disabled={blocked || !orderDirty} onClick={() => submit({ action: 'reorder', list_id: listId, version: order.version, entry_ids: ids })}>保存片单顺序</button><button type="button" disabled={busy} onClick={() => guard(() => {}, '取消排序')}>取消排序</button></div></section>
      : <><ol className="curation-entry-list" aria-busy={loading}>{data.results.map((entry, index) => <li key={entry.id} data-entry-id={entry.id}><article className={`curation-entry${entry.unavailable ? ' is-unavailable' : ''}`}><span className="curation-number">{(data.page-1)*12+index+1}</span><div className="curation-entry-cover"><CurationArtwork source={entry.record?.cover}/></div><div className="curation-entry-content"><h4>{titleOf(entry)}</h4>{entry.record ? <><small>{entry.record.japanese_title}</small><p className="curation-hint">{WATCH_LABELS[entry.record.watch_status]} · {entry.record.watched_eps}/{entry.record.total_eps || '?'} 集{entry.record.personal_score !== null && ` · 个人评分 ${entry.record.personal_score}`}</p></> : <p className="curation-hint">作品已从手账移除，原位置和入选理由仍保留。</p>}<p className="curation-reason">{entry.reason || '尚未填写入选理由。'}</p></div><div className="plan-actions curation-entry-actions">{entry.record && <button type="button" disabled={blocked} onClick={() => onOpenRecord(entry.record.id, { readOnly: true })}>查看作品详情</button>}{editable && <><button type="button" disabled={blocked} onClick={() => { const base = { reason: entry.reason }; setForm(base); setDialog({ kind: 'reason', base, entry, version: data.version }) }}>编辑入选理由</button>{entry.unavailable && <button type="button" disabled={blocked} onClick={() => prepare('relink', entry)}>重新关联作品</button>}<button type="button" disabled={blocked} onClick={() => setDialog({ kind: 'remove', entry, version: data.version })}>移出片单</button></>}</div></article></li>)}</ol>{!data.count && !loading && <div className="plan-empty"><h3>这张片单还没有作品</h3><p>{editable ? '收录几部作品，开始自己的主题编排。' : '可以进入编辑管理收录作品。'}</p></div>}<div className="plan-pagination"><button type="button" disabled={blocked || data.page <= 1} onClick={() => { moveFocus.current = {}; detailRead(listId, data.page-1) }}>上一页成员</button><span>{data.page} / {Math.max(1, Math.ceil(data.count/12))}</span><button type="button" disabled={blocked || !data.next_page} onClick={() => { moveFocus.current = {}; detailRead(listId, data.next_page) }}>下一页成员</button></div></>}
      </>}
    </>}
    {editable && dialog && <CurationDialog title={{ create: '新建主题片单', metadata: '编辑片单资料', reason: '编辑入选理由', picker: '从私人手账收录作品', relink: '重新关联片单作品', delete: '删除这张片单', remove: '移出片单作品' }[dialog.kind]} onClose={close} busy={busy} returnFocus={dialog.trigger} drawer={['picker', 'relink'].includes(dialog.kind)} footer={<div className="plan-actions">
      <button type="button" disabled={busy} onClick={close}>取消</button>
      {['create','metadata','reason'].includes(dialog.kind) && <button type="submit" form="curation-editor-form" className="plan-primary" disabled={blocked || !formDirty || ('title' in form && !form.title.trim())}>保存片单修改</button>}
      {['picker','relink'].includes(dialog.kind) && <button type="button" className="plan-primary" disabled={blocked || !selected.length} onClick={() => submit(dialog.kind === 'relink' ? { action: 'relink', list_id: listId, entry_id: dialog.entry.id, record_id: selected[0].id, version: dialog.version } : { action: 'add', list_id: listId, record_ids: selected.map(row => row.id), version: dialog.version })}>{dialog.kind === 'relink' ? '确认重新关联' : `收录所选 ${selected.length} 部作品`}</button>}
      {['delete','remove'].includes(dialog.kind) && <button type="button" className="curation-danger" disabled={blocked} onClick={() => submit({ action: dialog.kind === 'delete' ? 'delete' : 'remove-entry', list_id: listId, version: dialog.version, ...(dialog.entry ? { entry_id: dialog.entry.id } : {}) })}>{dialog.kind === 'delete' ? '确认删除片单' : '确认移出作品'}</button>}
    </div>}>
      {leave}{problem}
      {['create','metadata','reason'].includes(dialog.kind) && <form id="curation-editor-form" className="curation-form" onSubmit={saveForm}><fieldset disabled={blocked}>
        {dialog.kind === 'reason' ? <><p>{titleOf(dialog.entry)}</p><label>入选理由<textarea aria-label="入选理由" rows="7" maxLength={2000} value={form.reason} onChange={event => setForm({ reason: event.target.value })}/><small>{form.reason.length} / 2000</small></label><p className="curation-hint">只保存这张片单中的理由，作品长评保持独立。</p></> : <>
          <label>片单名称<input maxLength={80} value={form.title} onChange={event => setForm(values => ({ ...values, title: event.target.value }))} required/></label>
          <label>专题简介<textarea aria-label="专题简介" rows="5" maxLength={2000} value={form.description} onChange={event => setForm(values => ({ ...values, description: event.target.value }))}/><small>{form.description.length} / 2000</small></label>
          <label className="curation-check"><input type="checkbox" checked={form.pinned} onChange={event => setForm(values => ({ ...values, pinned: event.target.checked }))}/>置顶这张片单</label><label className="curation-check"><input type="checkbox" checked={form.archived} onChange={event => setForm(values => ({ ...values, archived: event.target.checked }))}/>归档这张片单</label>
          {dialog.kind === 'metadata' && <label>片单封面<select aria-label="片单封面" value={form.cover_entry_id} onChange={event => setForm(values => ({ ...values, cover_entry_id: event.target.value }))}><option value="">自动采用首个可用作品</option>{form.cover_entry_id && !dialog.complete.rows.some(row => row.id === form.cover_entry_id && row.record) && <option value={form.cover_entry_id}>原封面作品已移除（自动回退）</option>}{dialog.complete.rows.filter(row => row.record).map(row => <option key={row.id} value={row.id}>{row.record.title}</option>)}</select></label>}
        </>}
      </fieldset></form>}
      {['picker','relink'].includes(dialog.kind) && <>{dialog.kind === 'relink' && <p className="curation-hint">原条目：{titleOf(dialog.entry)}。确认后保留原顺序和入选理由，明确关联到本次选择的私人作品。</p>}<CurationPicker members={new Set(dialog.complete.rows.filter(row => row.record).map(row => row.record.id))} selected={selected} onSelection={setSelected} blocked={blocked} relink={dialog.kind === 'relink'} quickFilters={quickFilters}/></>}
      {dialog.kind === 'delete' && <p>确认删除「{collection?.title}」及其中 {collection?.entry_count} 项编排和入选理由？私人作品、评分、观看历程和计划继续保留。</p>}
      {dialog.kind === 'remove' && <p>确认从本片单移出「{titleOf(dialog.entry)}」及其入选理由？私人作品及其他片单继续保留。</p>}
    </CurationDialog>}
  </section>
}
