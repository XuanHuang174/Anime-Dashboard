import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { createEligibilityController, eligibilityCopy } from '../../utils/recommendationEligibility.js'

export function RecommendationAccessApplicationView({ state, onConfirm, onSubmit, onReload }) {
  const busy = ['idle', 'loading', 'writing'].includes(state.status)
  return <section className="rec-access-application" aria-labelledby="rec-access-title" aria-busy={busy}>
    <h3 id="rec-access-title">受限内容访问</h3>
    <p className="rec-muted">人工访问授权，不是年龄核验。NSFW 推荐目前仍未开放；普通推荐不受影响。</p>
    {['idle', 'loading'].includes(state.status) ? <p role="status">正在读取申请状态…</p>
      : <>{state.data && <p role="status">{eligibilityCopy(state.data)}</p>}
        {state.data?.manual_authorization_valid && state.data.valid_until && <p className="rec-muted">授权有效期至：<time dateTime={state.data.valid_until}>{new Date(state.data.valid_until).toLocaleString('zh-CN')}</time></p>}
        {state.error && <p role="alert" className="rec-inline-error">{state.error}</p>}
        {state.status === 'ready' && state.data?.can_apply && <label className="rec-checkbox">
          <input type="checkbox" checked={state.confirmed} onChange={event => onConfirm(event.target.checked)} />
          我已阅读提示，确认提交访问申请。审批通过不会自动开启 NSFW。</label>}
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

export default function RecommendationAccessApplication({ client, onChanged }) {
  const latest = useRef(onChanged); latest.current = onChanged
  const controller = useMemo(() => createEligibilityController(client, data => latest.current?.(data)), [client])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  useEffect(() => { controller.activate(); controller.load(); return () => controller.dispose() }, [controller])
  return <RecommendationAccessApplicationView state={state} onConfirm={controller.confirm} onSubmit={controller.submit} onReload={controller.load} />
}
