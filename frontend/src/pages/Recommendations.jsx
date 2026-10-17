import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import RecommendationCard, { RecommendationCover, RecommendationMeta } from '../components/recommendations/RecommendationCard.jsx'
import RecommendationDialog from '../components/recommendations/RecommendationDialog.jsx'
import RecommendationEvidence from '../components/recommendations/RecommendationEvidence.jsx'
import RecommendationCategoryGuide from '../components/recommendations/RecommendationCategoryGuide.jsx'
import RecommendationIdentityReview from '../components/recommendations/RecommendationIdentityReview.jsx'
import { RecommendationTasteNote, RecommendationColdStartGuide, RecommendationRefreshNotice } from '../components/recommendations/RecommendationProfileGuide.jsx'
import { RecommendationEmptyState, RecommendationSkeleton } from '../components/recommendations/RecommendationStates.jsx'
import RecommendationPreferencesPanel from '../components/recommendations/RecommendationPreferencesPanel.jsx'
import recommendationApi from '../api/recommendations.js'
import useRecommendations from '../hooks/useRecommendations.js'
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
import { recommendationAlgorithmLabel } from '../utils/recommendationExplanation.js'
import { identitySummary } from '../utils/recommendations.js'
import { DEFAULT_BROWSE_FILTERS } from '../utils/recommendationBrowse.js'
import { RecommendationBrowseControls, RecommendationFilterPanel, RecommendationBatchActions } from '../components/recommendations/RecommendationBrowseControls.jsx'
import '../styles/recommendations.css'

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
  return <RecommendationWorkspace key={sessionScope} client={client} sessionScope={sessionScope} />
}

function RecommendationWorkspace({ client, sessionScope }) {
  const [category, setCategory] = useState('all')
  const [style, setStyle] = useState('balanced')
  const [filters, setFilters] = useState({ ...DEFAULT_BROWSE_FILTERS })
  const [panel, setPanel] = useState(null)
  const [notice, setNotice] = useState('')
  const resourceTrigger = useRef(null)
  const detailQuery = useRef('')
  const resultsRef = useRef(null)
  const { detail, controller: detailController } = useRecommendationDetail(client)
  const state = useRecommendations(category, filters, client, style)
  const legacy = state.transport === 'legacy'
  const visibleFilters = legacy ? { ...filters, yearFrom: '', yearTo: '' } : filters
  const writeGate = useMemo(() => createRecommendationWriteGate({ isCurrent: () => {
    try { return recommendationSessionScope(getAccessToken()) === sessionScope } catch { return sessionScope === 'anonymous' }
  } }), [client, sessionScope])
  const preferenceEditor = useRecommendationPreferences(client, confirmedPreferences => {
    setPanel(null); setNotice('偏好已保存，正在重新读取画像与推荐。')
    reload({ confirmedPreferences })
    notifyRecommendationChange({ local: false })
  })
  const saving = ['saving', 'reloading'].includes(preferenceEditor.editor.status)
  const preferences = state.preferences || state.confirmedPreferences
  const acknowledgeReload = useRecommendationReload(options => reload(options), Boolean(panel) || state.status === 'loading')
  const discardDetail = () => { detailController.close(); setPanel(current => current?.kind === 'detail' ? null : current) }
  const reload = options => { if (!options?.preserveDetail) discardDetail(); acknowledgeReload(); state.retry(options) }
  const feedback = useRecommendationFeedback(client, result => {
    // Discard the entire old detail/resource context ONLY after server acknowledgement.
    detailController.close()
    setPanel(current => current?.kind === 'detail' ? null : current)
    reload({ confirmedFeedback: { version: feedback.controller.getSnapshot().version } })
    feedback.hidden.refresh()
    notifyFeedbackChange()
  }, writeGate, version => feedbackReload.current?.(version, false))
  const want = useRecommendationWant(client, result => {
    const refreshedDetail = detailController.afterWant(result) // synchronous invalidation BEFORE other reloads
    state.recordChanged(result.bangumi_id, result.watch_status)
    reload({ preserveDetail: true })
    notifyRecommendationChange({ local: false })
    setNotice(result.created ? '已加入想看，评分和长评留空。正在更新推荐依据。' : '手账已有这部作品，已保留原有状态、评分和长评。正在更新推荐依据。')
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
      onChanged: version => feedbackReload.current(version) })
  }, [client, feedback.controller])
  useEffect(() => { if (panel?.kind !== 'hidden') feedback.hidden.close() }, [panel?.kind, feedback.hidden])
  useEffect(() => { want.reconcile(state.items) }, [state.items, want.reconcile])
  useEffect(() => { if (detail.status === 'ready') want.reconcile([detail.item]) }, [detail, want.reconcile])
  useLayoutEffect(() => {
    const query = JSON.stringify([category, style, filters.type, filters.minScore, filters.hideRecorded, filters.yearFrom, filters.yearTo])
    if (detailQuery.current !== query) {
      detailQuery.current = query
      detailController.close()
      setPanel(current => current?.kind === 'detail' ? null : current)
    }
  }, [category, style, filters.type, filters.minScore, filters.hideRecorded, filters.yearFrom, filters.yearTo, detailController])
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
  }, [legacy, style, filters.yearFrom, filters.yearTo, detailController])

  useEffect(() => {
    const previous = document.title
    document.title = '个性化推荐 · 番剧发现手账'
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
    if (category === 'all' && style === 'balanced' && JSON.stringify(filters) === JSON.stringify(DEFAULT_BROWSE_FILTERS)) reload()
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
    if (preferences && preferenceEditor.open(preferences)) setPanel({ kind: 'preferences', profile: state.profile })
  }
  const closePanel = () => {
    if (panel?.kind === 'preferences' && !preferenceEditor.close()) return
    // Cancelling still performs no write; a conflict read may have learned a newer version.
    if (panel?.kind === 'preferences') reload()
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
  const pending = profile?.diagnostics?.pending_identity_count || 0
  const feedbackToast = <RecommendationFeedbackToast toast={feedback.state.toast} busy={feedback.state.pending}
    operation={feedback.state.operations[feedback.state.toast?.item.bangumiId]}
    onUndo={feedback.controller.undo} onManage={openHidden} onDismiss={feedback.controller.dismissToast} onReload={reloadFeedback} />

  return <main className="memphis-rec-container">
    <a className="rec-skip-link" href="#rec-results">跳到推荐内容</a>
    <div className="rec-page">
      <nav className="rec-nav" aria-label="推荐页面导航"><Link className="rec-back" to="/dashboard">← 返回我的手账</Link>
        <div className="rec-nav-tools"><button type="button" className="rec-text-button" onClick={openHidden}>已隐藏作品</button>
          <button type="button" className="rec-button rec-preferences" disabled={!preferences} onClick={openPreferences}>☷ 偏好设置</button></div></nav>
      <header className="rec-intro">
        <div className="rec-intro-copy"><span className="rec-eyebrow">YOUR NEXT FAVORITE <span className="rec-preview-badge">{recommendationAlgorithmLabel(state.data?.algorithm_version)}</span></span>
          <h1>找到你的下一部<br /><span>心头好<span className="rec-title-star" aria-hidden="true">✦</span></span></h1>
          <p>从喜欢的故事出发，把下一次心动写进手账。</p></div>
        <RecommendationTasteNote profile={profile} preferences={preferences} status={state.status} confirmedPreferences={state.confirmedPreferences}
          onPreferences={openPreferences} onRetry={reload} />
      </header>
      <RecommendationRefreshNotice confirmedPreferences={state.confirmedPreferences} status={state.status} onRetry={() => reload()} />
      {(state.confirmedFeedback && state.status === 'error' || feedback.state.syncError) && <section className="rec-feedback-refresh" role="alert">
        <p>反馈已保存，推荐更新失败。</p><button type="button" className="rec-button" onClick={() => { feedback.controller.clearErrors(); reload() }}>重新加载推荐</button></section>}
      {feedback.state.uncertain && <section className="rec-feedback-refresh" role="alert"><p>上次反馈结果尚未确认。请先确认原操作，暂不提交新的反馈。</p>
        <button type="button" className="rec-button" disabled={feedback.state.pending} onClick={feedback.controller.retry}>重试确认原操作</button></section>}
      <RecommendationColdStartGuide profile={profile} status={state.status} preferencesReady={!!preferences}
        onPreferences={openPreferences} onIdentity={() => setPanel({ kind: 'identity' })} />
      {pending > 0 && !profile.cold_start && <section className="rec-identity-notice" aria-label="旧数据关联提示"><p>{identitySummary(profile)}</p>
        <button type="button" className="rec-button rec-button--primary" disabled={profile.identity_service_ready === false} onClick={() => setPanel({ kind: 'identity' })}>核对旧作品 →</button></section>}
      {profile?.identity_service_ready === false && <p className="rec-muted">关联工具尚待完成部署。旧手账记录仍完整保留，当前不会自动修改数据。</p>}
      {profile?.diagnostics?.unavailable_identity_count > 0 && <p className="rec-muted">另有 {profile.diagnostics.unavailable_identity_count} 条高分记录因未收录或分级限制未计入推荐画像。</p>}
      <section className="rec-demo-note" aria-label="推荐依据"><span className="rec-demo-dot" aria-hidden="true" />
        <p>{state.data?.mode === 'preference_based' ? '根据你选择的题材推荐，尚未形成高分作品画像。'
          : state.data?.mode === 'personalized' ? '参考高分作品，并结合你的明确偏好；只展示已有数据支持的推荐理由。'
            : '仅使用安全、已确认身份的作品和明确偏好；数据未加载时不会假装已有画像。'}
          {' '}加入想看不会覆盖已有评分、长评或观看状态。</p></section>
      {legacy && <p className="rec-muted" role="status">增强浏览已关闭，已恢复原有 V1 推荐与分页。你的手账、偏好和反馈均未改变。</p>}
      <RecommendationBrowseControls category={category} style={style} filters={visibleFilters} legacy={legacy} algorithmVersion={state.data?.algorithm_version}
        onCategory={next => { if (next !== category) { discardDetail(); state.cancelBrowse(); setCategory(next) } }}
        onStyle={next => { if (next !== style) { discardDetail(); state.cancelBrowse(); setStyle(next) } }}
        onFilter={openFilters} onRemove={removeFilter} onReset={reset} />
      <RecommendationCategoryGuide category={category} mode={state.data?.mode} />
      <section id="rec-results" ref={resultsRef} className="rec-results" tabIndex={-1} aria-labelledby="rec-results-title">
        <div className="rec-section-heading"><div><span className="rec-eyebrow">PICK A STORY, START A JOURNEY</span>
          <h2 id="rec-results-title">下一次心动，从这里开始</h2></div>
          <span className="rec-result-count">{state.data ? `本轮 ${state.data.count} 部作品` : state.status === 'error' ? '暂未加载结果' : '正在读取数据'}</span></div>
        {state.error && state.status !== 'ready' && <p className="rec-inline-error" role="alert">{state.error}</p>}
        {state.status === 'loading' ? <RecommendationSkeleton /> : state.status === 'cold'
          ? <p className="rec-muted">完成上方口味设置后，再来翻开推荐故事。你的原有手账不会被修改。</p> : state.status !== 'ready'
          ? <RecommendationEmptyState state={state.status} onRetry={reload} onReset={resetFilters} onFilter={openFilters} />
          : <><RecommendationCard {...cardProps(state.items[0])} hero />
            {state.items.length > 1 && <><div className="rec-more-heading"><h3>还有这些故事，值得翻开</h3><span>不止一种心动</span></div>
              <div className="rec-grid">{state.items.slice(1).map((item) => <RecommendationCard key={item.id} {...cardProps(item)} />)}</div></>}
            <RecommendationBatchActions state={state} onMore={state.loadMore} onBatch={nextBatch}
              onReload={() => reload()} onFilter={openFilters} onRestart={() => reload()} /></>}
      </section>
      <footer className="rec-footer"><span>给每一次喜欢，一个值得停留的位置。</span><span>DISCOVERY JOURNAL / {recommendationAlgorithmLabel(state.data?.algorithm_version)}</span></footer>
    </div>
    <div className="rec-toast" role="status" aria-live="polite">{notice && <p>{notice}</p>}</div>
    {panel?.kind !== 'hidden' && feedbackToast}
    {panel?.kind === 'identity' && <RecommendationIdentityReview client={client} scope={panel.scope || 'high-score'} onClose={() => setPanel(null)}
      onChanged={() => { want.resetFailures(); reload(); notifyRecommendationChange({ local: false }) }} />}
    {panel && panel.kind !== 'identity' && <RecommendationDialog kind={panel.kind} busy={saving} title={panel.kind === 'filters' ? '给心动留一点范围' : panel.kind === 'preferences' ? '我的口味' : panel.kind === 'hidden' ? '已隐藏作品' : panel.item.title}
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
        : panel.kind === 'filters' ? <RecommendationFilterPanel initialFilters={visibleFilters} onApply={applyFilters} legacy={legacy} />
        : panel.kind === 'preferences' ? <RecommendationPreferencesPanel editor={preferenceEditor.editor} profile={panel.profile}
        onTag={preferenceEditor.setTag} onClear={preferenceEditor.clearDraft} onSave={preferenceEditor.save}
        onReloadLatest={preferenceEditor.reloadLatest} onResolveConflict={preferenceEditor.resolveConflict} onClose={closePanel} />
        : ['loading', 'refreshing', 'closed'].includes(detail.status) ? <p className="rec-muted" role="status">{detail.status === 'refreshing' ? '加入操作已确认，旧依据已作废，正在重新读取完整详情…' : '正在读取作品详情…'}</p>
        : detail.status === 'error' ? <div role="alert"><p>{detail.message}</p><button type="button" className="rec-button"
          onClick={() => { if (detail.snapshotChanged) { closePanel(); reload() } else detailController.retry() }}>{detail.snapshotChanged ? '重新加载推荐' : '重试详情'}</button></div>
          : <div className="rec-detail-content"><RecommendationCover item={detail.item} priority />
            <div><p className="rec-original-title">{detail.item.originalTitle}</p><RecommendationMeta item={detail.item} />
              <h3>推荐依据</h3><RecommendationEvidence item={detail.item} expanded onReload={() => { closePanel(); reload() }} />
              <h3>故事简介</h3><p>{detail.item.synopsis || '暂未收录简介。'}</p>
              <RecommendationDetailActions item={detail.item} resourceOpen={detail.resourceOpen}
                resourceTriggerRef={resourceTrigger}
                onResourceOpen={detailController.openResources} onResourceClose={detailController.closeResources} onClose={closePanel}
                onWant={addWant} operation={want.operations[detail.item.bangumiId]} onIdentity={openWantIdentity}
                writeBlocked={feedback.state.operations[detail.item.bangumiId]?.status === 'saving' || feedback.state.operations[detail.item.bangumiId]?.uncertain} />
            </div></div>}
    </RecommendationDialog>}
  </main>
}
