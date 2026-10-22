import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import api from '../api/api.js'
import { getAccessToken } from '../auth/tokenStorage.js'
import { recommendationSessionScope } from '../utils/recommendationSession.js'
import { createPublicationClient, createPublicationController } from '../utils/showcasePublication.js'
import './ShowcasePublicationPanel.css'

const defaultClient = createPublicationClient(api)
const readScope = () => recommendationSessionScope(getAccessToken())

export default function ShowcasePublicationPanel(props) {
  const [scope, setScope] = useState(readScope)
  useEffect(() => {
    const check = () => setScope(readScope())
    window.addEventListener('focus', check); window.addEventListener('storage', check)
    return () => { window.removeEventListener('focus', check); window.removeEventListener('storage', check) }
  }, [])
  return <PublicationWorkspace key={scope} {...props} scope={scope} />
}

function PublicationWorkspace({ refreshKey, scope, client = defaultClient }) {
  const controller = useMemo(() => createPublicationController(client, () => readScope() === scope), [client, scope])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const [selected, setSelected] = useState({})
  const [open, setOpen] = useState(false)
  const summary = useRef(null)
  useEffect(() => { controller.activate(); return () => controller.dispose() }, [controller])
  useEffect(() => { setSelected({}); controller.read(1) }, [controller, refreshKey])
  useEffect(() => { setSelected({}) }, [state.items])
  if (state.canPublish === false || (!state.count && !state.saved && !state.error)) return null
  if (state.saved && !state.count && !state.error) return <section className="showcase-publication" role="status">作品已公开，可在公共番剧库中搜索。</section>
  if (state.canPublish === null) return <section className="showcase-publication" role="status">
    公共库状态尚未读取。<button type="button" disabled={state.loading} onClick={() => controller.read(1)}>重试读取</button></section>
  const busy = state.busy !== null || state.loading
  return <section className="showcase-publication" aria-label="公共番剧库公开确认">
    <details open={open} onToggle={event => setOpen(event.currentTarget.open)}>
      <summary ref={summary}>公共库待公开：{state.count} 部 <span>已在手账中，但尚未进入“搜索轩皇的番剧库”</span></summary>
      <p>历史隐藏原因不明的作品不会自动公开。请逐部核对；管理员下架作品不可在这里恢复。</p>
      <p className="showcase-publication__privacy">确认后，本账号这部作品的资料、评分、长评、观看状态及标签会随公共展示被其他用户看到。不修改任何人的手账内容。</p>
      {state.error && <p role="alert">{state.error}</p>}
      {state.saved && <p role="status">公开已确认，其他用户现在可以搜索该作品。</p>}
      {state.loading && <p role="status">正在读取公共库状态…</p>}
      <button type="button" disabled={busy} onClick={() => { setSelected({}); controller.read(1) }}>重新读取列表</button>
      <ul>{state.items.map(item => <li key={item.record_id}>
        <strong>{item.title}</strong><p>{item.original} · {item.season} {item.bangumi_id ? `· BGM ${item.bangumi_id}` : ''}</p>
        {item.content_rating === 'adult' && item.requires_confirmation && <p>NSFW · 一般成人审核有效，确认后直接进入公共展示。</p>}
        <p>{item.detail}</p>
        {item.requires_confirmation && <label><input type="checkbox" disabled={busy || Boolean(state.error)}
          checked={Boolean(selected[item.record_id])} onChange={event => setSelected(current => ({ ...current, [item.record_id]: event.target.checked }))} />
          我确认将本账号这部作品的手账信息公开展示</label>}
        <button type="button" disabled={busy || Boolean(state.error) || !selected[item.record_id] || !item.requires_confirmation}
          onClick={() => controller.publish(item)}>{state.busy === item.record_id ? '正在确认…' : item.requires_confirmation ? '确认公开到番剧库' : '暂不能公开'}</button>
      </li>)}</ul>
      <div className="showcase-publication__pages">
        {state.page > 1 && <button type="button" disabled={busy} onClick={() => controller.read(state.page - 1)}>上一页</button>}
        {state.nextPage && <button type="button" disabled={busy} onClick={() => controller.read(state.nextPage)}>下一页</button>}
        <button type="button" disabled={Boolean(state.busy)} onClick={() => { setOpen(false); summary.current?.focus() }}>收起</button>
      </div>
    </details>
  </section>
}
