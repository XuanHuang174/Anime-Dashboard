import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import { accessApprovalPayload, accessAdminFailure, isSelfApproval } from '../utils/recommendationAccessAdmin.js'

const STATUS_LABELS = { pending: '待审批', active: '已批准', rejected: '已拒绝', revoked: '已撤销', expired: '已到期', legacy_unavailable: '历史授权不可用', account_inactive: '账号停用', not_requested: '未申请' }

export function SelfApprovalConfirmation({ selected, action, checked, onChange, disabled }) {
  if (!isSelfApproval(selected, action)) return null
  return <label className="my-3 flex items-start gap-2 border-2 border-black bg-white p-3">
    <input type="checkbox" checked={checked} disabled={disabled} onChange={e => onChange(e.target.checked)} />
    我明确为本人申请授予人工访问授权，确认记录为超级管理员自审；这不是年龄核验，也不会自动开启 NSFW。
  </label>
}

// No journal data, no inferred age and no automatic approval. Server rechecks authority.
export default function AdminRecommendationAccessPanel({ client = api }) {
  const [control, setControl] = useState(null), [queue, setQueue] = useState(null)
  const [status, setStatus] = useState('pending'), [page, setPage] = useState(1)
  const [selected, setSelected] = useState(null), [reason, setReason] = useState('')
  const [message, setMessage] = useState(''), [days, setDays] = useState(30)
  const [confirmed, setConfirmed] = useState(false), [pending, setPending] = useState(false)
  const [selfApprovalConfirmed, setSelfApprovalConfirmed] = useState(false)
  const [error, setError] = useState(''), [revision, setRevision] = useState(0)
  const [intent, setIntent] = useState(null)
  const gate = useRef(false), operation = useRef(null), generation = useRef(0), selection = useRef(null)
  useEffect(() => {
    const epoch = ++generation.current, abort = new AbortController()
    setError(''); setQueue(null); setSelected(null); operation.current = null; setConfirmed(false); setIntent(null)
    setSelfApprovalConfirmed(false)
    Promise.all([client.get('recommendations/admin/control/', { signal: abort.signal }),
      client.get('recommendations/admin/applications/', { params: { status, page }, signal: abort.signal })])
      .then(([a, b]) => { if (generation.current === epoch && !abort.signal.aborted) { setControl(a.data); setQueue(b.data) } })
      .catch(() => { if (!abort.signal.aborted && generation.current === epoch) setError('管理数据读取失败，请重新读取。仅超级管理员可访问。') })
    return () => { abort.abort(); selection.current?.abort(); generation.current++ }
  }, [client, status, page, revision])
  const select = async row => {
    if (gate.current) return
    selection.current?.abort(); const abort = new AbortController(); selection.current = abort
    setSelected(null); setConfirmed(false); setReason(''); setMessage(''); setError(''); operation.current = null; setIntent(null)
    setSelfApprovalConfirmed(false)
    try {
      const response = await client.get(`recommendations/admin/applications/${row.user_id}/`, { signal: abort.signal })
      if (!abort.signal.aborted && selection.current === abort) setSelected({ ...response.data, user_id: row.user_id })
    } catch { if (!abort.signal.aborted) setError('申请详情读取失败，请重试。') }
  }
  const submit = async action => {
    if (gate.current || !intent || !confirmed || !reason.trim()) return
    const epoch = generation.current
    gate.current = true; setPending(true); setError('')
    try {
      if (!operation.current) operation.current = action === 'control'
        ? { path: 'recommendations/admin/control/', body: { enabled: !control.enabled, expected_version: control.version,
          operation_id: crypto.randomUUID(), reason, confirmed: true } }
        : { path: `recommendations/admin/applications/${selected.user_id}/`, body: accessApprovalPayload(selected, action,
          { reason, days, message, confirmed, selfApprovalConfirmed }) }
      await client.post(operation.current.path, operation.current.body)
      if (generation.current !== epoch) return
      operation.current = null; setConfirmed(false); setReason(''); setRevision(v => v + 1)
    } catch (failure) {
      if (generation.current !== epoch) return
      if (!failure.response && !operation.current) { setError(failure.message); return }
      if (failure.response?.status >= 400 && failure.response?.status < 500) {
        operation.current = null; setConfirmed(false)
        setSelfApprovalConfirmed(false); setError(accessAdminFailure(failure))
      } else setError('提交结果尚未确认。请点击“重试确认原操作”，不会重复写入新的决定。')
    } finally { gate.current = false; setPending(false) }
  }
  const frozen = pending || !!operation.current
  const sessionMode = control?.access_mode === 'session_general_adult'
  const prepare = action => { if (!frozen) { setIntent(action); setConfirmed(false); setSelfApprovalConfirmed(false); setReason(''); setError('') } }
  const intentLabel = intent === 'control' ? `${control?.enabled ? '关闭' : '开启'} NSFW 推荐总开关`
    : `${({ approve: '批准申请', reject: '拒绝申请', revoke: '撤销授权' })[intent] || ''}：${selected?.username || ''}`
  const button = 'border-2 border-black bg-white px-4 py-2 font-bold shadow-[3px_3px_0_#111] disabled:opacity-50'
  return <section className="mx-auto mt-8 max-w-[1250px] border-4 border-black bg-[#fff5ee] p-5 text-black shadow-[8px_8px_0_#4ecdc4] sm:p-8" aria-busy={pending}>
    <h2 className="text-3xl font-black">受限内容管理 · 模块 3</h2>
    <p className="my-3">{sessionMode ? '本次浏览开关不是年龄核验。作品安全校验、推荐总开关和个人筛选仍分别生效；未知分级始终排除。'
      : '人工访问授权，不是年龄核验。用户批准、作品审核、服务总开关与个人筛选彼此独立；未知分级始终排除。'}</p>
    {sessionMode && <p className="my-3 border-2 border-black bg-white p-3" role="status">
      当前为一般成人内容的本次浏览开关模式，不再依赖账号申请或批准。下方保留历史审批记录；撤销旧账号授权不等于关闭此模式。作品安全校验与推荐总开关仍生效。
    </p>}
    {error && <p role="alert" className="my-3 border-2 border-black bg-[#fff0b2] p-3">{error}</p>}
    <button type="button" className={button} disabled={pending} onClick={() => { operation.current = null; setRevision(v => v + 1) }}>重新读取</button>
    {control && <div className="my-5 border-2 border-black bg-[#e5f5ef] p-4">
      <h3 className="text-xl font-black">推荐总开关：{control.effective_enabled ? '已开放' : '已关闭'}</h3>
      <p>部署总闸：{control.runtime_enabled ? '允许' : '关闭'} · 后台开关：{control.enabled ? '开启' : '关闭'} · 版本 {control.version}</p>
      <p>作品仍须在 Django 后台逐条审核并保留依据；本页不会自动放行作品。</p>
      <button type="button" className={button + ' mt-3'} disabled={frozen || !control.enabled && !control.runtime_enabled}
        onClick={() => prepare('control')}>{control.enabled ? '准备关闭 NSFW 推荐' : '准备开启 NSFW 推荐'}</button>
    </div>}
    <div className="my-4 flex flex-wrap items-center gap-3"><label>申请状态 <select aria-label="申请状态" className="border-2 border-black p-2" value={status} disabled={frozen}
      onChange={e => { setStatus(e.target.value); setPage(1) }}>{['pending', 'active', 'rejected', 'revoked', 'all'].map((v, i) => <option key={v} value={v}>{['待审批', '已批准', '已拒绝', '已撤销', '全部'][i]}</option>)}</select></label>
      <span>第 {page} 页 · {queue?.count ?? '…'} 项</span></div>
    {!queue ? <p role="status">正在读取申请…</p> : <div className="grid gap-3 md:grid-cols-2">
      {queue.results.map(row => <article className="border-2 border-black bg-white p-3" key={row.user_id}>
        <h3 className="break-all font-bold">{row.username}</h3><p>{STATUS_LABELS[row.status] || '不可用'} · 版本 {row.version}</p>
        <p>申请：{row.requested_at ? new Date(row.requested_at).toLocaleString('zh-CN') : '未申请'}</p>
        <button type="button" className={button + ' mt-2'} disabled={frozen} onClick={() => select(row)}>读取审批详情</button>
      </article>)}{!queue.results.length && <p>当前没有此状态的申请。</p>}</div>}
    <div className="my-4 flex gap-3"><button className={button} disabled={frozen || page === 1} onClick={() => setPage(p => p - 1)}>上一页</button>
      <button className={button} disabled={frozen || !queue?.next_page} onClick={() => setPage(queue.next_page)}>下一页</button></div>
    {selected && <section className="my-4 border-2 border-black bg-white p-4"><h3 className="text-xl font-bold">审批：{selected.username}</h3>
      <p>当前状态：{STATUS_LABELS[selected.eligibility.effective_status] || '不可用'} · {selected.is_self
        ? '本人申请：批准需额外确认，并记录自审；不自动开启服务。' : '仅处理当前账号；操作将记录审计。'}</p>
      <label className="my-2 block">有效天数（1–30） <input type="number" min="1" max="30" value={days} disabled={frozen} onChange={e => setDays(e.target.value)} className="border-2 border-black p-2" /></label>
      <label className="block">给用户的说明（选填）<input maxLength={200} value={message} disabled={frozen} onChange={e => setMessage(e.target.value)} className="w-full border-2 border-black p-2" /></label>
      <div className="mt-3 flex flex-wrap gap-3">{['approve', 'reject', 'revoke'].map((action, i) => <button type="button" className={button} key={action}
        disabled={frozen || sessionMode && action === 'approve' || action !== 'revoke' && selected.eligibility.effective_status !== 'pending'} onClick={() => prepare(action)}>{['批准申请', '拒绝申请', '撤销授权'][i]}</button>)}</div>
    </section>}
    {intent && <div className="border-2 border-black bg-[#fff0b2] p-4"><h3 className="mb-3 break-all text-xl font-bold">待确认：{intentLabel}</h3>
      <label className="block font-bold">本次操作依据／原因（必填，最多 200 字）<input maxLength={200} value={reason} disabled={frozen}
      onChange={e => setReason(e.target.value)} className="my-2 w-full border-2 border-black bg-white p-2" /></label>
      <label className="my-3 flex items-center gap-2"><input type="checkbox" checked={confirmed} disabled={frozen} onChange={e => setConfirmed(e.target.checked)} />我已核对，确认本次操作；批准不代表年龄核验。</label>
      <SelfApprovalConfirmation selected={selected} action={intent} checked={selfApprovalConfirmed} onChange={setSelfApprovalConfirmed} disabled={frozen} />
      <button type="button" className={button} disabled={frozen || !confirmed || !reason.trim()
        || isSelfApproval(selected, intent) && (!selfApprovalConfirmed || selected.self_approval_allowed !== true)} onClick={() => submit(intent)}>确认提交：{intentLabel}</button>
      <button type="button" className={button + ' ml-3'} disabled={frozen} onClick={() => { setIntent(null); setConfirmed(false); setSelfApprovalConfirmed(false) }}>取消本次操作</button>
      {operation.current && !pending && <button type="button" className={button + ' mt-3'} onClick={() => submit('retry')}>重试确认原操作</button>}
    </div>}
  </section>
}
