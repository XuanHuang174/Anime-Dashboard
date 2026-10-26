import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import RecommendationHost from './RecommendationHost.jsx'
import RecommendationCard, { RecommendationCover, RecommendationMeta } from '../components/recommendations/RecommendationCard.jsx'
import ExplorationTagChips from '../components/recommendations/ExplorationTagChips.jsx'
import { RecommendationGridSkeleton } from '../components/recommendations/RecommendationLoading.jsx'
import RecommendationDialog from '../components/recommendations/RecommendationDialog.jsx'
import RecommendationEvidence from '../components/recommendations/RecommendationEvidence.jsx'
import RecommendationIdentityReview from '../components/recommendations/RecommendationIdentityReview.jsx'
import { RecommendationTasteNote, RecommendationColdStartGuide, RecommendationRefreshNotice } from '../components/recommendations/RecommendationProfileGuide.jsx'
import { RecommendationEmptyState, RecommendationSkeleton } from '../components/recommendations/RecommendationStates.jsx'
import RecommendationPreferencesPanel from '../components/recommendations/RecommendationPreferencesPanel.jsx'
import recommendationApi from '../api/recommendations.js'
import useRecommendations from '../hooks/useRecommendations.js'
import useRecommendationMotion from '../hooks/useRecommendationMotion.js'
import useRecommendationWant from '../hooks/useRecommendationWant.js'
import useRecommendationDetail from '../hooks/useRecommendationDetail.js'
import RecommendationDetailActions from '../components/recommendations/RecommendationDetailActions.jsx'
import RecommendationResourcePanel from '../components/recommendations/RecommendationResourcePanel.jsx'
import useRecommendationPreferences from '../hooks/useRecommendationPreferences.js'
import useRecommendationReload from '../hooks/useRecommendationReload.js'
import { notifyRecommendationChange } from '../utils/recommendationReload.js'
import useRecommendationFeedback from '../hooks/useRecommendationFeedback.js'
import { createRecommendationWriteGate, validateHiddenFeedback } from '../utils/recommendationFeedback.js'
import { notifyFeedbackChange, subscribeFeedbackChanges } from '../utils/recommendationFeedbackSync.js'
import RecommendationFeedbackMenu from '../components/recommendations/RecommendationFeedbackMenu.jsx'
import RecommendationFeedbackToast from '../components/recommendations/RecommendationFeedbackToast.jsx'
import RecommendationHiddenWorks from '../components/recommendations/RecommendationHiddenWorks.jsx'
import { getAccessToken } from '../auth/tokenStorage.js'
import { recommendationSessionScope } from '../utils/recommendationSession.js'
import { watchRecommendationScope } from '../utils/recommendationScopeMonitor.js'
import { controlledScope } from '../utils/recommendationContentScope.js'
import { DEFAULT_BROWSE_FILTERS } from '../utils/recommendationBrowse.js'
import { RecommendationBrowseControls, RecommendationFilterPanel, RecommendationBatchActions } from '../components/recommendations/RecommendationBrowseControls.jsx'
import '../styles/recommendations.css'
import '../styles/recommendations-layout.css'
import '../styles/recommendations-theme.css'
import '../styles/recommendations-motion.css'
import '../styles/recommendations-preferences.css'
import ShowcasePublicationPanel from '../components/ShowcasePublicationPanel.jsx'
import BackToTop from '../components/BackToTop.jsx'
import SeriesSummary from '../components/recommendations/SeriesSummary.jsx'
import { SeriesAnnotationsProvider } from '../components/recommendations/SeriesExploration.jsx'
import SeriesReminders from '../components/recommendations/SeriesReminders.jsx'

export default function Recommendations({ client = recommendationApi }) {
  const readScope = () => { try { return recommendationSessionScope(getAccessToken()) } catch { return 'anonymous' } }
  const [sessionScope, setSessionScope] = useState(readScope)
  useEffect(() => {
    const check = () => setSessionScope(readScope())
    window.addEventListener('storage', check)
    window.addEventListener('focus', check)
    document.addEventListener('visibilitychange', check)
    return () => { window.removeEventListener('storage', check); window.removeEventListener('focus', check); document.removeEventListener('visibilitychange', check) }
  }, [])
  return <RecommendationHost key={sessionScope} client={client} sessionScope={sessionScope} Personal={RecommendationWorkspace} />
}

function RecommendationWorkspace({ client, sessionScope, cache, adult, setAdult, navigation, capabilities, reminders }) {
  const navigate = useNavigate()
  const [category, setCategory] = useState(cache.controls?.category || 'all')
  const [style, setStyle] = useState(cache.controls?.style || 'balanced')
  const [localFilters, setLocalFilters] = useState(cache.controls?.filters || { ...DEFAULT_BROWSE_FILTERS })
  const filters = { ...localFilters, includeAdult: adult }
  const setFilters = update => {
    const next = typeof update === 'function' ? update(filters) : update
    setLocalFilters(next); setAdult(!!next.includeAdult)
  }
  useLayoutEffect(() => { cache.controls = { category, style, filters } }, [cache, category, style, localFilters, adult])
  const [panel, setPanel] = useState(null)
  const [notice, setNotice] = useState('')
  const scopeLost = useRef(() => {})
  const [publicationRevision, setPublicationRevision] = useState(0)
  const resourceTrigger = useRef(null)
  const preferenceTrigger = useRef(null)
  const preferenceReturnFocus = useRef(null)
  const preferenceFocusPending = useRef(false)
  const detailQuery = useRef('')
  const resultsRef = useRef(null)
  const pageRef = useRef(null)
  const { detail, controller: detailController } = useRecommendationDetail(client)
  const state = useRecommendations(category, filters, client, style, cache)
  useRecommendationMotion(pageRef, { status: state.status,
    epoch: JSON.stringify([state.key, state.data?.snapshot_id, state.data?.state_token, state.data?.batch]),
    ids: state.items.map(item => item.bangumiId).join(','), blocked: state.snapshotExpired || state.snapshotDirty })
  const legacy = state.transport === 'legacy'
  const visibleFilters = legacy ? { ...filters, yearFrom: '', yearTo: '' } : filters
  const writeGate = useMemo(() => createRecommendationWriteGate({ isCurrent: () => {
    try { return recommendationSessionScope(getAccessToken()) === sessionScope } catch { return sessionScope === 'anonymous' }
  } }), [client, sessionScope])
  const preferenceEditor = useRecommendationPreferences(client, confirmedPreferences => {
    preferenceFocusPending.current = true
    setPanel(null); setNotice('偏好已保存，正在重新读取画像与推荐。')
    reload({ confirmedPreferences })
    notifyRecommendationChange({ local: false, reminders: false })
  })
  const saving = ['saving', 'reloading'].includes(preferenceEditor.editor.status)
  const preferences = state.preferences || state.confirmedPreferences
  useLayoutEffect(() => {
    if (!preferenceFocusPending.current || panel || state.status === 'loading') return
    preferenceFocusPending.current = false
    // Closing rereads preferences, briefly disabling the original trigger. Restore
    // after that read, but never steal focus from a subsequent user interaction.
    const previous = preferenceReturnFocus.current
    if (document.activeElement !== document.body && document.activeElement !== previous) return
    const target = previous?.isConnected && !previous.disabled ? previous : preferenceTrigger.current
    if (target && !target.disabled) target.focus({ preventScroll: true })
  }, [panel, state.status, preferences])
  const discardDetail = () => { detailController.close(); setPanel(current => current?.kind === 'detail' ? null : current) }
  scopeLost.current = () => {
    if (!filters.includeAdult) return
    discardDetail(); state.cancelBrowse(); setPanel(null)
    setFilters(current => ({ ...current, includeAdult: false }))
    setNotice('访问授权或服务状态已变化，已恢复安全范围。')
  }
  const appliedContentScope = state.data?.evidence_context
  useEffect(() => {
    if (!filters.includeAdult) return undefined
    if (!controlledScope(appliedContentScope)) return undefined
    const deadline = appliedContentScope.scope_valid_until === null ? null : Date.parse(appliedContentScope.scope_valid_until)
    const monitor = watchRecommendationScope({ deadline, probe: signal => client.context(signal), onLost: () => scopeLost.current() })
    return () => monitor.dispose()
  }, [client, !!filters.includeAdult, appliedContentScope?.scope_revision, appliedContentScope?.scope_valid_until])
  const externalChange = reason => { discardDetail(); state.contextChanged(reason) }
  const acknowledgeReload = useRecommendationReload(externalChange,
    panel?.kind === 'preferences' || saving || state.status === 'loading', client,
    state.data && { context: state.data.evidence_context, rollout_revision: state.rolloutRevision,
      browse_enabled: !legacy, expires_at: state.data.snapshot_expires_at })
  const reload = options => { if (!options?.preserveDetail) discardDetail(); acknowledgeReload(); state.retry(options) }
  const feedback = useRecommendationFeedback(client, result => {
    // Discard the entire old detail/resource context ONLY after server acknowledgement.
    detailController.close()
    setPanel(current => current?.kind === 'detail' ? null : current)
    reload({ confirmedFeedback: { version: feedback.controller.getSnapshot().version } })
    feedback.hidden.refresh()
    notifyFeedbackChange()
  }, writeGate, () => externalChange())
  const want = useRecommendationWant(client, result => {
    setPublicationRevision(current => current + 1)
    const refreshedDetail = detailController.afterWant(result) // synchronous invalidation BEFORE other reloads
    state.recordChanged(result.bangumi_id, result.watch_status)
    reload({ preserveDetail: true })
    notifyRecommendationChange({ local: false })
    setNotice(result.showcase_publication?.status === 'review_required'
      ? '已加入手账，但尚未公开到公共番剧库。请在“公共库待公开”中逐部确认。'
      : result.created ? '已加入想看，评分和长评留空。正在更新推荐依据。' : '手账已有这部作品，已保留原有状态、评分和长评。正在更新推荐依据。')
    return refreshedDetail
  }, writeGate)
  const feedbackReload = useRef(null)
  feedbackReload.current = (version, refreshHidden = true) => {
    const previousToast = feedback.controller.getSnapshot().toast
    if (previousToast) feedback.controller.dismissToast(previousToast.key)
    feedback.controller.observeVersion(version)
    detailController.close()
    setPanel(current => current?.kind === 'detail' ? null : current)
    reload({ confirmedFeedback: { version } })
    if (refreshHidden) feedback.hidden.refresh()
  }
  useEffect(() => { feedback.controller.observeVersion(state.data?.evidence_context?.feedback_version) }, [state.data, feedback.controller])
  useEffect(() => {
    if (!client.hiddenFeedback) return undefined
    return subscribeFeedbackChanges({ client, getVersion: () => feedback.controller.getSnapshot().version,
      checkForeground: !client.context,
      onChanged: version => {
        feedback.controller.observeVersion(version)
        const toast = feedback.controller.getSnapshot().toast
        if (toast) feedback.controller.dismissToast(toast.key)
        externalChange()
      } })
  }, [client, feedback.controller])
  useEffect(() => { if (panel?.kind !== 'hidden') feedback.hidden.close() }, [panel?.kind, feedback.hidden])
  useEffect(() => { want.reconcile(state.items) }, [state.items, want.reconcile])
  useEffect(() => { if (detail.status === 'ready') want.reconcile([detail.item]) }, [detail, want.reconcile])
  useLayoutEffect(() => {
    const query = JSON.stringify([category, style, filters.type, filters.minScore, filters.hideRecorded, filters.yearFrom, filters.yearTo, !!filters.includeAdult])
    if (detailQuery.current !== query) {
      detailQuery.current = query
      detailController.close()
      setPanel(current => current?.kind === 'detail' ? null : current)
    }
  }, [category, style, filters.type, filters.minScore, filters.hideRecorded, filters.yearFrom, filters.yearTo, !!filters.includeAdult, detailController])
  useEffect(() => { if (state.snapshotExpired) discardDetail() }, [state.snapshotExpired, detailController])
  const executionEpoch = state.data ? `${state.data.algorithm_version}:${state.data.execution_recipe?.recipe_revision || ''}:${state.transport}` : ''
  const lastExecutionEpoch = useRef('')
  useLayoutEffect(() => {
    if (executionEpoch && lastExecutionEpoch.current && executionEpoch !== lastExecutionEpoch.current) discardDetail()
    if (executionEpoch) lastExecutionEpoch.current = executionEpoch
  }, [executionEpoch, detailController])
  useLayoutEffect(() => {
    if (legacy) {
      discardDetail()
      if (style !== 'balanced') setStyle('balanced')
      if (filters.yearFrom || filters.yearTo) setFilters(current => ({ ...current, yearFrom: '', yearTo: '' }))
    }
  }, [legacy, style, filters.yearFrom, filters.yearTo, !!filters.includeAdult, detailController])

  useEffect(() => {
    const previous = document.title
    document.title = '个性化推荐 · 番剧探索手账'
    return () => { document.title = previous }
  }, [])
  useEffect(() => {
    if (!notice) return undefined
    const timer = window.setTimeout(() => setNotice(''), 5000)
    return () => window.clearTimeout(timer)
  }, [notice])

  const applyFilters = next => {
    setPanel(null)
    if (JSON.stringify(next) === JSON.stringify(filters)) return
    discardDetail(); state.cancelBrowse(); setFilters({ ...next })
  }
  const resetFilters = () => applyFilters({ ...DEFAULT_BROWSE_FILTERS })
  const reset = () => {
    discardDetail(); state.cancelBrowse()
    if (category === 'all' && style === 'balanced' && JSON.stringify(filters) === JSON.stringify({ ...DEFAULT_BROWSE_FILTERS, includeAdult: false })) reload()
    else { setCategory('all'); setStyle('balanced'); setFilters({ ...DEFAULT_BROWSE_FILTERS }) }
  }
  const openFilters = () => { discardDetail(); setPanel({ kind: 'filters' }) }
  const removeFilter = key => applyFilters(key === 'years' ? { ...filters, yearFrom: '', yearTo: '' }
    : { ...filters, [key]: key === 'hideRecorded' ? false : DEFAULT_BROWSE_FILTERS[key] })
  const nextBatch = async () => {
    discardDetail()
    if (await state.nextBatch()) {
      const node = resultsRef.current
      node?.focus({ preventScroll: true })
      node?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
    }
  }
  const openPreferences = () => {
    if (preferences && preferenceEditor.open(preferences)) {
      preferenceReturnFocus.current = document.activeElement
      preferenceFocusPending.current = false
      setPanel({ kind: 'preferences', profile: state.profile })
    }
  }
  const closePanel = () => {
    if (panel?.kind === 'preferences' && !preferenceEditor.close()) return
    // Cancelling still performs no write; a conflict read may have learned a newer version.
    if (panel?.kind === 'preferences') { preferenceFocusPending.current = true; reload() }
    detailController.close()
    setPanel(null)
  }
  const openHidden = () => {
    if (saving) return
    detailController.close()
    setPanel({ kind: 'hidden' })
    feedback.hidden.open()
  }
  const reloadFeedback = async () => {
    try {
      const data = validateHiddenFeedback(await client.hiddenFeedback({ page: 1, page_size: 1 }))
      feedback.controller.clearErrors()
      feedbackReload.current(data.feedback_version)
    } catch { setNotice('反馈状态读取失败，请稍后重试；原有状态未被覆盖。') }
  }
  const addWant = item => want.add(item)
  const openWantIdentity = () => { detailController.close(); setPanel({ kind: 'identity', scope: 'all' }) }
  const cardProps = (item) => ({ item, onWant: addWant, operation: want.operations[item.bangumiId], onIdentity: openWantIdentity,
    browseUnavailable: Boolean(state.snapshotExpired || state.snapshotDirty),
    onHide: feedback.controller.hide, feedbackOperation: feedback.state.operations[item.bangumiId],
    feedbackDisabled: feedback.state.pending || feedback.state.uncertain || feedback.state.version === null,
    onFeedbackReload: reloadFeedback,
    onReload: () => reload(),
    onDetail: (chosen) => { detailController.open(chosen); setPanel({ kind: 'detail', item: chosen }) } })
  const profile = state.profile
  const feedbackToast = <RecommendationFeedbackToast toast={feedback.state.toast} busy={feedback.state.pending}
    operation={feedback.state.operations[feedback.state.toast?.item.bangumiId]}
    onUndo={feedback.controller.undo} onManage={openHidden} onDismiss={feedback.controller.dismissToast} onReload={reloadFeedback} />

  return <SeriesAnnotationsProvider client={client} items={state.items} enabled={capabilities?.series_exploration?.enabled}
    blocked={!!state.snapshotDirty || !!state.snapshotExpired}><main ref={pageRef} className="memphis-rec-container">
    <a className="rec-skip-link" href="#rec-results">跳到推荐内容</a>
    <div className="rec-page">
      <div className="rec-masthead">
      <nav className="rec-nav" aria-label="推荐页面导航"><Link className="rec-back" to="/dashboard">← 返回我的手账</Link>
        <div className="rec-nav-tools"><button type="button" className="rec-text-button" onClick={openHidden}>已隐藏作品</button>
          <button type="button" className="rec-button rec-preferences" ref={preferenceTrigger} disabled={!preferences} onClick={openPreferences}>☷ 偏好设置</button></div></nav>
      <header className="rec-page-heading rec-explore-heading">
        <span className="rec-chapter-label">我的手账</span>
        <h1>番剧探索</h1>
        {navigation}
      </header>
      <RecommendationRefreshNotice confirmedPreferences={state.confirmedPreferences} status={state.status} onRetry={() => reload()} />
      {(state.confirmedFeedback && state.status === 'error' || feedback.state.syncError) && <section className="rec-feedback-refresh" role="alert">
        <p>反馈已保存，推荐更新失败。</p><button type="button" className="rec-button" onClick={() => { feedback.controller.clearErrors(); reload() }}>重新加载推荐</button></section>}
      {feedback.state.uncertain && <section className="rec-feedback-refresh" role="alert"><p>上次反馈结果尚未确认。请先确认原操作，暂不提交新的反馈。</p>
        <button type="button" className="rec-button" disabled={feedback.state.pending} onClick={feedback.controller.retry}>重试确认原操作</button></section>}
      {legacy && <p className="rec-muted" role="status">增强浏览已关闭，已恢复原有 V1 推荐与分页。你的手账、偏好和反馈均未改变。</p>}
      <RecommendationBrowseControls category={category} style={style} filters={visibleFilters} legacy={legacy} algorithmVersion={state.data?.algorithm_version} mode={state.data?.mode}
        onCategory={next => { if (next !== category) { discardDetail(); state.cancelBrowse(); setCategory(next) } }}
        onStyle={next => { if (next !== style) { discardDetail(); state.cancelBrowse(); setStyle(next) } }}
        onFilter={openFilters} onRemove={removeFilter} onReset={reset} />
      </div>
      <ShowcasePublicationPanel refreshKey={publicationRevision} />
      <SeriesReminders client={client} enabled={capabilities?.series_reminders?.enabled} controller={reminders} />
      <section id="rec-results" ref={resultsRef} className="rec-results" tabIndex={-1} aria-labelledby="rec-results-title">
        <h2 id="rec-results-title" className="rec-sr-only">推荐作品</h2>
        {state.contextUpdated && <section id="rec-context-update" className="rec-feedback-refresh rec-context-update" role="status">
          <p>{state.contextExpired ? '浏览快照已到期，已保留当前浏览位置。重新加载后继续浏览。'
            : '推荐依据已更新，已保留当前浏览位置。重新加载后查看最新推荐。'}</p>
          <button type="button" className="rec-button" onClick={() => reload()}>重新加载推荐</button></section>}
        {state.error && state.status !== 'ready' && <p className="rec-inline-error" role="alert">{state.error}</p>}
        <div className="rec-featured-layout">
          <div className="rec-featured-main">
            {state.status === 'loading' ? <RecommendationSkeleton key={state.key || 'initial'} featuredOnly /> : state.status === 'cold'
              ? profile?.cold_start ? <RecommendationColdStartGuide profile={profile} status={state.status} preferencesReady={!!preferences}
                  onPreferences={openPreferences} onIdentity={() => setPanel({ kind: 'identity' })} />
                : <RecommendationEmptyState state="cold" onPreferences={openPreferences} />
              : state.status !== 'ready'
                ? <RecommendationEmptyState state={state.status} onRetry={reload} onReset={resetFilters} onFilter={openFilters} />
                : <><span className="rec-sr-only">本批首推</span>
                  {state.data?.mode === 'preference_based' && <p className="rec-explicit-basis">根据你选择的题材推荐</p>}
                  <RecommendationCard {...cardProps(state.items[0])} hero /></>}
          </div>
          <RecommendationTasteNote profile={profile} preferences={preferences} status={state.status} confirmedPreferences={state.confirmedPreferences}
            onPreferences={openPreferences} onRetry={reload} onIdentity={() => setPanel({ kind: 'identity' })} />
        </div>
        {state.status === 'loading' && <RecommendationGridSkeleton />}
        {profile?.cold_start && ['ready', 'empty'].includes(state.status) && <details className="rec-profile-guide">
          <summary>完善作品画像</summary>
          <RecommendationColdStartGuide profile={profile} status={state.status} preferencesReady={!!preferences}
            onPreferences={openPreferences} onIdentity={() => setPanel({ kind: 'identity' })} />
        </details>}
        {state.status === 'ready' && <>
          {state.items.length > 1 && <section className="rec-more-section" aria-labelledby="rec-more-title"><div className="rec-more-heading"><h2 id="rec-more-title">还有更多</h2>
            <span className="rec-result-count">{state.data ? `本轮 ${state.data.count} 部作品` : ''}</span></div>
            <div className="rec-grid">{state.items.slice(1).map((item) => <RecommendationCard key={item.id} {...cardProps(item)} />)}</div></section>}
          <RecommendationBatchActions state={state} onMore={state.loadMore} onBatch={nextBatch}
            onReload={() => reload()} onFilter={openFilters} onRestart={() => reload()} /></>}
      </section>
      <footer className="rec-footer"><span>我的手账 / 个性化推荐</span></footer>
    </div>
    <div className="rec-toast" role="status" aria-live="polite">{notice && <p>{notice}</p>}</div>
    {panel?.kind !== 'hidden' && feedbackToast}
    {panel?.kind === 'identity' && <RecommendationIdentityReview client={client} scope={panel.scope || 'high-score'} onClose={() => setPanel(null)}
      onChanged={() => { want.resetFailures(); reload(); notifyRecommendationChange({ local: false }) }} />}
    {panel && panel.kind !== 'identity' && <RecommendationDialog kind={panel.kind} busy={saving} fallbackFocusRef={panel.kind === 'preferences' ? preferenceTrigger : undefined} title={panel.kind === 'filters' ? '给心动留一点范围' : panel.kind === 'preferences' ? '我的口味' : panel.kind === 'hidden' ? '已隐藏作品' : panel.item.title}
      headerActions={panel.kind === 'detail' && detail.status === 'ready' ? <RecommendationFeedbackMenu
        item={detail.item} onHide={feedback.controller.hide} operation={feedback.state.operations[detail.item.bangumiId]}
        disabled={feedback.state.pending || feedback.state.uncertain || feedback.state.version === null || want.operations[detail.item.bangumiId]?.status === 'adding'}
        onReload={reloadFeedback} /> : null}
      onClose={closePanel} resourceOpen={panel.kind === 'detail' && detail.status === 'ready' && detail.resourceOpen}
      onResourceClose={detailController.closeResources} resourceTriggerRef={resourceTrigger}
      resourcePanel={panel.kind === 'detail' && detail.status === 'ready' ? <RecommendationResourcePanel
        item={detail.item} open={detail.resourceOpen}
        sessionKey={`${detail.selection.session}:${detail.resourceSession || 0}:${detail.item.bangumiId}:${detail.item.evidenceToken || ''}`}
        onClose={detailController.closeResources} /> : null}>
      {panel.kind === 'hidden' ? <>{state.confirmedFeedback && state.status === 'error' && <div className="rec-feedback-refresh" role="alert">
          <p>反馈已保存，推荐更新失败。</p><button type="button" className="rec-button" onClick={() => reload()}>重新加载推荐</button></div>}
        {feedback.state.uncertain && <div role="alert"><p>反馈结果尚未确认。</p><button type="button" className="rec-button"
          disabled={feedback.state.pending} onClick={feedback.controller.retry}>重试确认原操作</button></div>}
        <RecommendationHiddenWorks state={feedback.hiddenState} feedback={feedback.state}
        onPage={feedback.hidden.load} onUndo={feedback.controller.undo} onReload={() => { feedback.controller.clearErrors(); feedback.hidden.refresh() }}
        writeBusy={id => want.operations[id]?.status === 'adding'} />{feedbackToast}</>
        : panel.kind === 'filters' ? <RecommendationFilterPanel initialFilters={visibleFilters} onApply={applyFilters} legacy={legacy}
          client={client} onAccessChanged={() => { externalChange(); notifyRecommendationChange({ local: false }) }} />
        : panel.kind === 'preferences' ? <RecommendationPreferencesPanel editor={preferenceEditor.editor} profile={panel.profile}
        onTag={preferenceEditor.setTag} onClear={preferenceEditor.clearDraft} onSave={preferenceEditor.save}
        onMinScore={preferenceEditor.setMinScore} onResetMinScore={preferenceEditor.resetMinScore} onRetryPreview={preferenceEditor.retryPreview}
        onReloadLatest={preferenceEditor.reloadLatest} onResolveConflict={preferenceEditor.resolveConflict} onClose={closePanel} />
        : ['loading', 'refreshing', 'closed'].includes(detail.status) ? <p className="rec-muted" role="status">{detail.status === 'refreshing' ? '加入操作已确认，旧依据已作废，正在重新读取完整详情…' : '正在读取作品详情…'}</p>
        : detail.status === 'error' ? <div role="alert"><p>{detail.message}</p><button type="button" className="rec-button"
          onClick={() => { if (detail.snapshotChanged) { closePanel(); reload() } else detailController.retry() }}>{detail.snapshotChanged ? '重新加载推荐' : '重试详情'}</button></div>
          : <div className="rec-detail-content"><RecommendationCover item={detail.item} priority />
            <div><p className="rec-original-title">{detail.item.originalTitle}</p><RecommendationMeta item={detail.item} />
              <h3>推荐依据</h3><RecommendationEvidence item={detail.item} expanded onReload={() => { closePanel(); reload() }} />
              <ExplorationTagChips tags={detail.item.tags} beforeNavigate={closePanel} /><h3>故事简介</h3><p>{detail.item.synopsis || '暂未收录简介。'}</p>
              <SeriesSummary client={client} bangumiId={detail.item.bangumiId} />
              <RecommendationDetailActions item={detail.item} resourceOpen={detail.resourceOpen}
                onSimilar={capabilities?.navigation?.[1]?.available ? item => { closePanel(); navigate(`/recommendations/similar?seed=${item.bangumiId}`) } : undefined}
                resourceTriggerRef={resourceTrigger}
                onResourceOpen={detailController.openResources} onResourceClose={detailController.closeResources} onClose={closePanel}
                onWant={addWant} operation={want.operations[detail.item.bangumiId]} onIdentity={openWantIdentity}
                writeBlocked={feedback.state.operations[detail.item.bangumiId]?.status === 'saving' || feedback.state.operations[detail.item.bangumiId]?.uncertain} />
            </div></div>}
    </RecommendationDialog>}
    <BackToTop isModalOpen={Boolean(panel)} theme="memphis" />
  </main></SeriesAnnotationsProvider>
}
