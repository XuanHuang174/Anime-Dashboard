import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { createEligibilityController, eligibilityCopy } from '../../utils/recommendationEligibility.js'

export function RecommendationAccessApplicationView({ state, onConfirm, onSubmit, onReload, includeAdult = false, onIncludeAdult }) {
  const busy = ['idle', 'loading', 'writing'].includes(state.status)
  return <section className="rec-access-application" aria-labelledby="rec-access-title" aria-busy={busy}>
    <div className="rec-access-summary"><h3 id="rec-access-title">NSFW 访问</h3><span className="rec-muted">人工审批 · 非年龄核验</span></div>
    <p className="rec-access-note rec-muted">默认安全范围；授权通过且服务开放后，才可手动开启。</p>
    {['idle', 'loading'].includes(state.status) ? <p role="status">正在读取申请状态…</p>
      : <>{state.data && <p role="status" className={state.data.effective_status === 'not_requested' ? 'rec-sr-only' : undefined}>{eligibilityCopy(state.data)}</p>}
        {state.data?.manual_authorization_valid && state.data.valid_until && <p className="rec-muted">授权有效期至：<time dateTime={state.data.valid_until}>{new Date(state.data.valid_until).toLocaleString('zh-CN')}</time></p>}
        {state.error && <p role="alert" className="rec-inline-error">{state.error}</p>}
        {state.status === 'ready' && state.data?.can_apply && <label className="rec-checkbox">
          <input type="checkbox" checked={state.confirmed} onChange={event => onConfirm(event.target.checked)} />
          我已阅读提示，确认申请（批准后仍需手动开启）。</label>}
        {state.status === 'ready' && state.data?.adult_access_allowed && <label className="rec-checkbox"><input type="checkbox" checked={includeAdult} onChange={e => onIncludeAdult?.(e.target.checked)} />包含 NSFW 标记作品（本次浏览）</label>}
        {state.status === 'writing' && <p role="status">正在提交申请，请勿重复操作…</p>}
        <div className="rec-access-actions">
          {state.data?.can_apply && ['ready', 'writing', 'uncertain'].includes(state.status) && <button type="button"
            className="rec-button" disabled={busy || !state.confirmed} onClick={onSubmit}>
            {state.status === 'writing' ? '正在提交…' : state.status === 'uncertain' ? '重试确认原申请' : '申请开启'}</button>}
          <button type="button" className="rec-text-button" disabled={busy} onClick={onReload}>
            {['error', 'conflict'].includes(state.status) ? '重新读取申请状态' : '刷新审批状态'}</button>
        </div></>}
  </section>
}

export default function RecommendationAccessApplication({ client, onChanged, includeAdult, onIncludeAdult }) {
  const latest = useRef(onChanged); latest.current = onChanged
  const controller = useMemo(() => createEligibilityController(client, data => latest.current?.(data)), [client])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  useEffect(() => { controller.activate(); controller.load(); return () => controller.dispose() }, [controller])
  return <RecommendationAccessApplicationView state={state} onConfirm={controller.confirm} onSubmit={controller.submit} onReload={controller.load} includeAdult={includeAdult} onIncludeAdult={onIncludeAdult} />
}
