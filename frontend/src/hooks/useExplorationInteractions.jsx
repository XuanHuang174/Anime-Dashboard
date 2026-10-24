import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import useRecommendationDetail from './useRecommendationDetail.js'
import useRecommendationWant from './useRecommendationWant.js'
import useRecommendationFeedback from './useRecommendationFeedback.js'
import { explorationDetailAdapter } from '../utils/explorationResults.js'
import { shouldCloseExplorationDetail } from '../utils/explorationSession.js'
import { createRecommendationWriteGate, validateHiddenFeedback } from '../utils/recommendationFeedback.js'
import { notifyFeedbackChange, subscribeFeedbackChanges } from '../utils/recommendationFeedbackSync.js'
import { notifyRecommendationChange } from '../utils/recommendationReload.js'
import { getAccessToken } from '../auth/tokenStorage.js'
import { recommendationSessionScope } from '../utils/recommendationSession.js'
import ExplorationEvidence from '../components/recommendations/ExplorationEvidence.jsx'
import RecommendationFeedbackToast from '../components/recommendations/RecommendationFeedbackToast.jsx'

// Both exploration intents share the same owner-bound writes and complete-detail refresh.
export default function useExplorationInteractions(client, sessionScope, state, scope) {
  const [panel, setPanel] = useState(null), [notice, setNotice] = useState('')
  const [publicationRevision, setPublicationRevision] = useState(0)
  const resourceTrigger = useRef(null)
  const { detail, controller: detailController } = useRecommendationDetail(client, explorationDetailAdapter)
  const close = () => { detailController.close(); setPanel(null) }
  const reload = () => { close(); state.retry() }
  const changed = () => { close(); state.controller.contextChanged() }
  useLayoutEffect(() => { close() }, [scope, state.key, detailController])
  useEffect(() => {
    if (shouldCloseExplorationDetail(state, detail)) close()
  }, [state.snapshotDirty, state.invalidationVersion])
  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(''), 6000); return () => window.clearTimeout(timer) }, [notice])
  const writeGate = useMemo(() => createRecommendationWriteGate({ isCurrent: () => {
    try { return recommendationSessionScope(getAccessToken()) === sessionScope } catch { return sessionScope === 'anonymous' }
  } }), [client, sessionScope])
  const feedback = useRecommendationFeedback(client, () => {
    detailController.close(); setPanel(current => current?.kind === 'detail' ? null : current)
    state.retry(); feedback.hidden.refresh(); notifyFeedbackChange(); notifyRecommendationChange({ local: false })
  }, writeGate, changed)
  const reloadFeedback = async (signal) => {
    try {
      const data = validateHiddenFeedback(await client.hiddenFeedback({ page: 1, page_size: 1 }, signal))
      if (signal?.aborted) return
      feedback.controller.observeVersion(data.feedback_version); feedback.controller.clearErrors()
      return data
    } catch { if (!signal?.aborted) setNotice('隐藏反馈状态读取失败，可重新读取后再操作。') }
  }
  useEffect(() => {
    const request = new AbortController(); reloadFeedback(request.signal)
    const unsubscribe = subscribeFeedbackChanges({ client, getVersion: () => feedback.controller.getSnapshot().version,
      onChanged: version => { feedback.controller.observeVersion(version); changed() } })
    return () => { request.abort(); unsubscribe() }
  }, [client, feedback.controller])
  useEffect(() => { if (panel?.kind !== 'hidden') feedback.hidden.close() }, [panel?.kind, feedback.hidden])
  const want = useRecommendationWant(client, result => {
    setPublicationRevision(value => value + 1)
    const refreshed = detailController.afterWant(result)
    state.controller.recordChanged(result.bangumi_id, result.watch_status)
    notifyRecommendationChange({ local: false })
    setNotice(result.showcase_publication?.status === 'review_required' ? '已加入手账，公开到公共库仍需逐部确认。'
      : result.created ? '已加入想看。旧列表已保留，重新探索可更新结果。' : '手账已有此作品，已保留原有评分、长评及观看状态。')
    return refreshed
  }, writeGate)
  useEffect(() => { want.reconcile(state.items) }, [state.items, want.reconcile])
  useEffect(() => { if (detail.status === 'ready') want.reconcile([detail.item]) }, [detail, want.reconcile])
  const openHidden = () => { close(); setPanel({ kind: 'hidden' }); feedback.hidden.open() }
  const openIdentity = () => { close(); setPanel({ kind: 'identity' }) }
  const cardProps = item => ({ item, EvidenceComponent: ExplorationEvidence, onWant: want.add, operation: want.operations[item.bangumiId], onIdentity: openIdentity,
    onDetail: chosen => { detailController.open(chosen); setPanel({ kind: 'detail', item: chosen }) }, onReload: reload,
    browseUnavailable: !!state.snapshotDirty, onHide: feedback.controller.hide, feedbackOperation: feedback.state.operations[item.bangumiId],
    feedbackDisabled: feedback.state.pending || feedback.state.uncertain || feedback.state.version === null, onFeedbackReload: () => reloadFeedback() })
  const toast = <RecommendationFeedbackToast toast={feedback.state.toast} busy={feedback.state.pending}
    operation={feedback.state.operations[feedback.state.toast?.item.bangumiId]} onUndo={feedback.controller.undo}
    onManage={openHidden} onDismiss={feedback.controller.dismissToast} onReload={() => reloadFeedback()} />
  return { panel, setPanel, notice, setNotice, publicationRevision, resourceTrigger, detail, detailController,
    close, reload, changed, feedback, reloadFeedback, want, openHidden, openIdentity, cardProps, toast }
}
