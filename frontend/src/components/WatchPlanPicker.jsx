import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import { seriesSession } from '../hooks/useSeriesSession.js'
import { lockDialogBody } from '../utils/dialogBodyLock.js'
import { planError } from '../utils/watchPlans.js'
import { WATCH_LABELS } from '../utils/watchHistory.js'

export default function WatchPlanPicker({ onOpenRecord, onClose }) {
  const [search, setSearch] = useState(''), [query, setQuery] = useState(''), [page, setPage] = useState(1)
  const [data, setData] = useState(null), [error, setError] = useState(''), [loading, setLoading] = useState(false), [retry, setRetry] = useState(0)
  const owner = useRef(seriesSession()), dialog = useRef(null)
  useEffect(() => {
    const previous = document.activeElement, release = lockDialogBody(document), node = dialog.current
    node.showModal(); node.querySelector('input')?.focus()
    return () => { node.close(); release(); previous?.isConnected && previous.focus({preventScroll:true}) }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    api.get('records/', {params:{search:query || undefined,page,page_size:12},signal:controller.signal}).then(({data:result}) => {
      if (!controller.signal.aborted && owner.current === seriesSession()) {
        if (!Array.isArray(result.results) || !Number.isSafeInteger(result.count)) throw new Error('作品列表返回不完整。')
        setData(result)
      }
    }).catch(problem => { if (!controller.signal.aborted) setError(planError(problem)) }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [query,page,retry])
  return <dialog ref={dialog} className="watch-plans plan-picker-dialog" aria-labelledby="planPickerTitle" onCancel={event => {event.preventDefault();event.stopPropagation();onClose()}}
    onPointerDown={event => { const box=event.currentTarget.getBoundingClientRect(); dialog.current.dataset.backdropDown=String(event.target===event.currentTarget && (event.clientX<box.left || event.clientX>box.right || event.clientY<box.top || event.clientY>box.bottom)) }}
    onClick={event => { const box=event.currentTarget.getBoundingClientRect(); if(dialog.current.dataset.backdropDown==='true' && event.target===event.currentTarget && (event.clientX<box.left || event.clientX>box.right || event.clientY<box.top || event.clientY>box.bottom)) onClose(); delete dialog.current.dataset.backdropDown }}>
    <header><div><span className="plan-sticker">WATCH NEXT</span><h2 id="planPickerTitle">从私人手账添加计划</h2></div><button type="button" aria-label="关闭作品选择" onClick={onClose}>×</button></header>
    <form className="plan-search" onSubmit={event => {event.preventDefault();setPage(1);setQuery(search.trim());setRetry(n=>n+1)}}><input aria-label="搜索可安排作品" maxLength={100} value={search} onChange={event=>setSearch(event.target.value)} placeholder="搜索中文或日文名称"/><button type="submit">搜索作品</button></form>
    <div className="plan-picker-content">{loading && <p role="status">正在读取作品…</p>}{error && <p role="alert" className="plan-error">{error}<button type="button" onClick={()=>setRetry(n=>n+1)}>重试作品列表</button></p>}
      <ul>{!loading && !error && data?.results.map(record => {
        const active = ['pending','ongoing'].includes(record.watch_plan_summary?.state)
        return <li key={record.id}>{(record.anime.poster_image || record.anime.poster_url) && <img src={record.anime.poster_image || record.anime.poster_url} alt=""/>}<span><strong>{record.anime.chinese_title}</strong><small>{WATCH_LABELS[record.watch_status]}{active ? ' · 已在计划中' : record.watch_status === 'completed' ? ' · 可安排重看' : ''}</small></span><button type="button" onClick={()=>{onClose();onOpenRecord(record.id,{edit:true,plan:true})}}>{active ? '调整安排' : '安排此作品'}</button></li>
      })}</ul>{!loading && !error && !data?.count && <p>没有匹配作品。请先在手账中添加，或调整搜索词。</p>}
    </div><footer><span>{data ? `共 ${data.count} 部` : '仅本人私人手账'}</span>{data?.count > 12 && <div className="plan-pagination"><button type="button" disabled={loading || page<=1} onClick={()=>setPage(n=>n-1)}>上一页作品</button><span>{page} / {Math.ceil(data.count/12)}</span><button type="button" disabled={loading || page*12>=data.count} onClick={()=>setPage(n=>n+1)}>下一页作品</button></div>}</footer>
  </dialog>
}
