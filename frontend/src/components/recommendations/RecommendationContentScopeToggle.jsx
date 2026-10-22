import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { createScopeControlReader, scopeControlCopy } from '../../utils/recommendationScopeControl.js'

export function RecommendationContentScopeToggleView({ state, includeAdult = false, onChange, onReload }) {
  const available = state.status === 'ready' && state.data?.available === true
  const busy = ['idle', 'loading'].includes(state.status)
  return <section className="rec-access-application" aria-label="内容范围" aria-busy={busy}>
    <div className="rec-access-summary">
      <label className="rec-checkbox"><input type="checkbox" role="switch" aria-label="包含 NSFW 作品"
        aria-describedby="rec-scope-control-note" disabled={!available} checked={available && includeAdult}
        onChange={event => { if (available) onChange?.(event.target.checked) }} />包含 NSFW 作品</label>
      <button type="button" className="rec-text-button" disabled={busy} onClick={onReload}>
        {state.status === 'error' ? '重试' : '刷新状态'}</button>
    </div>
    <p id="rec-scope-control-note" role="status" className="rec-muted">{scopeControlCopy(state)}</p>
  </section>
}

export default function RecommendationContentScopeToggle({ client, includeAdult, onChange, onAvailability }) {
  const change = useRef(onChange); change.current = onChange
  const availability = useRef(onAvailability); availability.current = onAvailability
  const controller = useMemo(() => createScopeControlReader(client), [client])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  useEffect(() => { controller.activate(); controller.load(); return () => controller.dispose() }, [controller])
  // Clear only the local filter draft; never applies it or writes an account setting.
  useEffect(() => {
    availability.current?.(state.status === 'ready' && state.data?.available === true)
    if (state.status === 'error' || state.status === 'ready' && !state.data.available) change.current?.(false)
  }, [state])
  return <RecommendationContentScopeToggleView state={state} includeAdult={includeAdult}
    onChange={onChange} onReload={controller.load} />
}
