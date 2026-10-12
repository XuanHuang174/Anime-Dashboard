import { useEffect, useRef, useState } from 'react'
import RecommendationDialog from './RecommendationDialog.jsx'
import { createLatestRequestGate, recommendationError, validateIdentityReview } from '../../utils/recommendations.js'

export default function RecommendationIdentityReview({ client, onClose, onChanged, scope = 'high-score' }) {
  const [page, setPage] = useState(1)
  const [revision, setRevision] = useState(0)
  const [state, setState] = useState({ status: 'loading' })
  const [selection, setSelection] = useState({})
  const [confirmed, setConfirmed] = useState({})
  const [keywords, setKeywords] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const readGate = useRef(createLatestRequestGate())
  const writeGate = useRef(createLatestRequestGate())
  const busyRef = useRef(false)
  useEffect(() => {
    const request = readGate.current.begin()
    setState({ status: 'loading' }); setSelection({}); setConfirmed({})
    client.identities({ page, page_size: 5, scope }, request.signal).then((raw) => {
      const data = validateIdentityReview(raw)
      if (request.isCurrent()) {
        if (!data.results.length && page > 1) setPage(page - 1)
        else setState({ status: 'ready', data })
      }
    }).catch((failure) => { if (request.isCurrent()) setState({ status: 'error', message: recommendationError(failure) }) })
    return () => readGate.current.cancel()
  }, [page, revision, client, scope])
  useEffect(() => () => writeGate.current.cancel(), [])

  const search = async (item) => {
    if (busyRef.current) return
    busyRef.current = true; setBusy(true); setError('')
    const request = writeGate.current.begin()
    try {
      const data = validateIdentityReview(await client.identities({ scope, record_id: item.record_id, keyword: keywords[item.record_id] || item.title.slice(0,100), page: 1 }, request.signal))
      if (request.isCurrent()) {
        const updated = data.results[0]
        setState((current) => ({ ...current, data: { ...current.data,
          results: current.data.results.map((row) => row.record_id === item.record_id ? updated : row) } }))
        setSelection((value) => ({ ...value, [item.record_id]: null })); setConfirmed((value) => ({ ...value, [item.record_id]: false }))
      }
    } catch (failure) { if (request.isCurrent()) setError(recommendationError(failure)) }
    finally { if (request.isCurrent()) { busyRef.current = false; setBusy(false) } }
  }
  const save = async (item) => {
    const candidate = item.candidates.find((entry) => entry.bangumi_id === Number(selection[item.record_id]))
    if (busyRef.current || !candidate || !confirmed[item.record_id]) return
    busyRef.current = true; setBusy(true); setError('')
    const request = writeGate.current.begin()
    try {
      await client.confirmIdentity({ record_id: item.record_id, bangumi_id: candidate.bangumi_id, ticket: candidate.ticket,
        expected_version: item.identity_version, confirmed: true }, request.signal)
      if (request.isCurrent()) { onChanged(); setRevision((value) => value + 1) }
    } catch (failure) { if (request.isCurrent()) setError(failure?.response?.status === 409
      ? '记录或候选已变化，请刷新候选后重新确认。没有覆盖已有修改。' : recommendationError(failure)) }
    finally { if (request.isCurrent()) { busyRef.current = false; setBusy(false) } }
  }
  return <RecommendationDialog kind="identity" title="核对旧作品身份" busy={busy} onClose={onClose}>
    <p className="rec-muted">仅关联你自己的{scope === 'all' ? '旧记录（全部观看状态）' : '高分旧记录'}。请核对原名、季度及作品类型，尤其注意不同季、OVA 和剧场版。评分、长评和观看状态不会修改。</p>
    {error && <p className="rec-inline-error" role="alert">{error}</p>}
    {state.status === 'loading' ? <p role="status">正在读取待关联作品…</p> : state.status === 'error'
      ? <div role="alert"><p>{state.message}</p><button type="button" className="rec-button" onClick={() => setRevision((n) => n + 1)}>重试读取</button></div>
      : <><p className="rec-muted">{state.data.count} 条{scope === 'all' ? '旧' : '高分'}记录待核对</p>
        {!state.data.results.length && <p role="status">目前没有可核对的{scope === 'all' ? '' : '高分'}旧记录。</p>}
        {state.data.results.map((item) => <section className="rec-identity-row" key={item.record_id}>
          <h3>{item.title}</h3><p className="rec-muted">{item.original_title || '原名未填写'} · {item.season || '季度未知'}</p>
          {item.status === 'needs_admin_review' ? <p>已有权威身份发生冲突，请联系管理员核对。</p> : <>
            <form className="rec-identity-search" onSubmit={(event) => { event.preventDefault(); search(item) }}>
              <label className="rec-field">查找标准作品<input maxLength={100} minLength={2} required disabled={busy}
                value={keywords[item.record_id] ?? item.title.slice(0,100)} onChange={(event) => setKeywords({ ...keywords, [item.record_id]: event.target.value })} /></label>
              <button type="submit" className="rec-button" disabled={busy}>搜索候选</button></form>
            {!item.candidates.length && <p className="rec-muted">暂无安全候选，可尝试原名或缩短搜索词；不会自动匹配。</p>}
            <fieldset className="rec-identity-candidates" disabled={busy}><legend>选择同一部作品</legend>
              {item.candidates.map((candidate) => <label key={candidate.bangumi_id} className="rec-identity-candidate">
                <input type="radio" name={`record-${item.record_id}`} value={candidate.bangumi_id}
                  checked={Number(selection[item.record_id]) === candidate.bangumi_id}
                  onChange={(event) => { setSelection({ ...selection, [item.record_id]: event.target.value }); setConfirmed({ ...confirmed, [item.record_id]: false }) }} />
                <span><strong>{candidate.title_zh || candidate.title_jp}</strong><small>{candidate.title_jp} · {candidate.season || '日期未知'} · {candidate.status_type || '类型未知'} · BGM {candidate.bangumi_id}</small></span>
              </label>)}</fieldset>
            <label className="rec-checkbox"><input type="checkbox" disabled={busy || !selection[item.record_id]} checked={Boolean(confirmed[item.record_id])}
              onChange={(event) => setConfirmed({ ...confirmed, [item.record_id]: event.target.checked })} />我已核对，这是同一部作品（不是其他季或版本）</label>
            <button type="button" className="rec-button rec-button--primary" disabled={busy || !confirmed[item.record_id]} onClick={() => save(item)}>确认关联</button>
          </>}
        </section>)}
        <div className="rec-dialog-actions"><button type="button" className="rec-button" disabled={busy} onClick={() => setRevision((n) => n + 1)}>刷新候选</button>
          <button type="button" className="rec-button" disabled={busy || page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
          <button type="button" className="rec-button" disabled={busy || !state.data.next_page} onClick={() => setPage(state.data.next_page)}>下一页</button></div>
      </>}
  </RecommendationDialog>
}
