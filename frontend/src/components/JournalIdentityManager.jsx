import { useEffect, useRef, useState } from 'react'
import RecommendationDialog from './recommendations/RecommendationDialog.jsx'
import { identitySeasonDiffers } from '../utils/recommendationIdentityBatch.js'
import { seriesSession } from '../hooks/useSeriesSession.js'

const errorText = error => typeof error.response?.data?.detail === 'string' ? error.response.data.detail : '暂时无法确认结果，请稍后重试。'

export default function JournalIdentityManager({ client, recordId, onClose, onPending, onChanged, scopeControl }) {
  const [query, setQuery] = useState({ page: 1, ...(recordId ? { record_id: recordId } : {}) })
  const [keyword, setKeyword] = useState(''), [data, setData] = useState(null), [busy, setBusy] = useState(false)
  const [choice, setChoice] = useState(null), [confirmed, setConfirmed] = useState(false), [error, setError] = useState('')
  const [request, setRequest] = useState(null), [saving, setSaving] = useState(false), [discard, setDiscard] = useState(false)
  const [refresh, setRefresh] = useState(0), [message, setMessage] = useState('')
  const alive = useRef(true), sequence = useRef(0), writeLock = useRef(false)
  const owner = useRef(seriesSession())
  const current = () => alive.current && owner.current === seriesSession()
  useEffect(() => { alive.current = true; return () => { alive.current = false; sequence.current++ } }, [])
  useEffect(() => {
    const controller = new AbortController(), token = ++sequence.current
    setBusy(true); setError('')
    client.identityManagement(query, controller.signal).then(result => {
      if (current() && token === sequence.current) setData(result)
    }).catch(e => { if (!controller.signal.aborted && alive.current && token === sequence.current) setError(errorText(e)) })
      .finally(() => { if (alive.current && token === sequence.current) setBusy(false) })
    return () => controller.abort()
  }, [client, query, refresh])
  const locked = saving || !!request, dirty = !!choice || !!request
  const item = query.record_id ? data?.results?.[0] : null
  function navigate(next) { setChoice(null); setConfirmed(false); setKeyword(''); setData(null); setQuery(next); setMessage('') }
  async function submit() {
    if (!current() || writeLock.current || !item || (!request && (!choice || !confirmed))) return
    const payload = request || { record_id: item.record_id, action: choice.action, version: item.version,
      operation_id: crypto.randomUUID(), ticket: choice.candidate?.ticket || item.ticket,
      ...(choice.candidate ? { bangumi_id: choice.candidate.bangumi_id } : {}) }
    writeLock.current = true; setRequest(payload); setSaving(true); setError('')
    try {
      const result = await client.correctIdentity(payload)
      if (!current()) return
      if (result.record_id !== payload.record_id || !Number.isInteger(result.version) || result.version <= payload.version) throw new Error('无效回执')
      setRequest(null); setChoice(null); setConfirmed(false); setMessage('关联已更新。'); onChanged(); setRefresh(n => n + 1)
    } catch (e) {
      if (!current()) return
      if (e.response?.status >= 400 && e.response.status < 500) {
        setRequest(null); setChoice(null); setConfirmed(false)
        setError(`${errorText(e)} 请刷新后重新核对。`)
      } else setError('提交结果尚未确认。请重试原操作，避免重复更正。')
    } finally { writeLock.current = false; if (alive.current) setSaving(false) }
  }
  const finish = () => { if (request) onChanged(); onClose() }
  const close = () => { if (!saving) { if (dirty) setDiscard(true); else finish() } }
  return <RecommendationDialog kind="identity" title="管理标准作品关联" onClose={close} busy={saving}>
    <div className="rec-identity-workbench">
      <p className="rec-identity-intro">更换或解除私人标准作品关联。系列、人物与声优、标准链接及推荐将随之更新；名称、评分、长评、海报、观看历程和观看计划保留。原始录入来源保留，可撤销最近一次更正。</p>
      <fieldset className="rec-identity-scope" disabled={dirty || busy || saving}>{scopeControl}</fieldset>
      <div className="rec-identity-content">
      <div className="rec-identity-pagination">
        <button className="rec-button" disabled={dirty || busy} onClick={onPending}>待核对记录</button>
        {query.record_id && <button className="rec-button" disabled={dirty || busy} onClick={() => navigate({ page: 1 })}>已关联与已解除记录</button>}
        <button className="rec-text-button" disabled={locked || busy || !!choice} onClick={() => setRefresh(n => n + 1)}>刷新关联</button>
      </div>
      <form className="rec-identity-search" onSubmit={e => { e.preventDefault(); setQuery(q => ({ ...q, page: 1, keyword: keyword.trim() })); setChoice(null); setConfirmed(false) }}>
        <label className="rec-field">{query.record_id ? '查找标准作品（名称、Bangumi 编号或条目 URL）' : '搜索自己的记录'}
          <input value={keyword} maxLength={100} disabled={locked || !!choice} onChange={e => setKeyword(e.target.value)} /></label>
        <button className="rec-button" disabled={locked || busy || !!choice}>搜索</button>
      </form>
      {error && <p role="alert" className="rec-inline-error">{error}</p>}
      {message && <p role="status">{message}</p>}
      {busy ? <p role="status">正在读取关联…</p> : !query.record_id ? <>
        <p>{data?.count ?? 0} 条记录 · 第 {query.page} 页</p>
        {data?.results.map(row => <section className="rec-identity-row" key={row.record_id}><h3>{row.title}</h3>
          <p>{row.season || '季度未知'} · {row.current ? `${row.current.title_zh || row.current.title_jp} · BGM ${row.current.bangumi_id}` : row.state === 'user_unlinked' ? '已解除私人关联' : '关联待重新核对'}</p>
          <button className="rec-button" onClick={() => navigate({ page: 1, record_id: row.record_id })}>管理关联</button></section>)}
        <div className="rec-identity-pagination"><button className="rec-button" disabled={query.page <= 1} onClick={() => navigate({ ...query, page: query.page - 1 })}>上一页</button>
          <button className="rec-button" disabled={!data?.next_page} onClick={() => navigate({ ...query, page: data.next_page })}>下一页</button></div>
      </> : item && <section className="rec-identity-row" aria-label="管理当前作品关联">
        <h3>{item.title}</h3><p className="rec-muted">原名：{item.original_title || '未填写'} · 原记录季度：{item.season || '未知'}</p>
        <p>当前标准作品：<strong>{item.current ? `${item.current.title_zh || item.current.title_jp} · BGM ${item.current.bangumi_id} · ${item.current.season}` : item.state === 'user_unlinked' ? '已解除私人关联' : '尚无有效关联'}</strong></p>
        <fieldset className="rec-identity-candidates" disabled={locked}><legend>选择正确的作品</legend>
          {item.candidates.map(candidate => <label className="rec-identity-candidate" key={candidate.bangumi_id}>
            <input type="radio" name="identity-correction" checked={choice?.candidate?.bangumi_id === candidate.bangumi_id} onChange={() => { setChoice({ action: 'replace', candidate }); setConfirmed(false) }} />
            <span><strong>{candidate.title_zh || candidate.title_jp}</strong><small>{candidate.title_jp} · {candidate.season || '季度未知'} · {candidate.status_type} · BGM {candidate.bangumi_id}</small></span>
          </label>)}{!item.candidates.length && <p>暂无可用候选，请按名称、编号或条目链接重新搜索。</p>}</fieldset>
        {choice?.candidate && identitySeasonDiffers(item.season, choice.candidate.season) && <p className="rec-identity-difference">季度或日期不同：{item.season || '未知'} → {choice.candidate.season || '未知'}。请特别核对季数、OVA 和剧场版。</p>}
        <div className="rec-identity-pagination"><button className="rec-button" disabled={locked || item.state === 'user_unlinked'} onClick={() => { setChoice({ action: 'unlink' }); setConfirmed(false) }}>解除私人关联</button>
          <button className="rec-button" disabled={locked || !item.can_undo} onClick={() => { setChoice({ action: 'undo' }); setConfirmed(false) }}>撤销最近一次更正</button></div>
        {choice && <div className="rec-identity-discard"><p>{choice.action === 'unlink' ? '解除后暂停此记录的标准资料关联，不会自动恢复原来的匹配。' : choice.action === 'undo' ? '将恢复最近一次更正前的私人关联。' : `将关联到 BGM ${choice.candidate.bangumi_id}，请确认不是其他季或版本。`}</p>
          <label className="rec-checkbox"><input type="checkbox" checked={confirmed} disabled={locked} onChange={e => setConfirmed(e.target.checked)} />我已核对并确认此操作</label>
          <button className="rec-button" disabled={locked} onClick={() => { setChoice(null); setConfirmed(false) }}>取消选择</button>
          <button className="rec-button rec-button--primary" disabled={saving || !request && !confirmed} onClick={submit}>{saving ? '正在提交…' : request ? '重试原操作' : '确认更正'}</button></div>}
      </section>}
      {discard && <div role="alert" className="rec-identity-discard"><p>放弃当前选择并关闭？结果未确认的操作可能已保存，重新进入时会读取最新关联。</p>
        <button className="rec-button" onClick={() => setDiscard(false)}>继续核对</button><button className="rec-button" onClick={finish}>关闭并稍后核对</button></div>}
      </div>
    </div>
  </RecommendationDialog>
}
