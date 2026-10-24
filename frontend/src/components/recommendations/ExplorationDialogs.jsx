import { notifyRecommendationChange } from '../../utils/recommendationReload.js'
import RecommendationDialog from './RecommendationDialog.jsx'
import RecommendationDetailActions from './RecommendationDetailActions.jsx'
import RecommendationResourcePanel from './RecommendationResourcePanel.jsx'
import RecommendationFeedbackMenu from './RecommendationFeedbackMenu.jsx'
import RecommendationHiddenWorks from './RecommendationHiddenWorks.jsx'
import RecommendationIdentityReview from './RecommendationIdentityReview.jsx'
import { RecommendationCover, RecommendationMeta } from './RecommendationCard.jsx'
import ExplorationEvidence from './ExplorationEvidence.jsx'
import ExplorationTagChips from './ExplorationTagChips.jsx'

export default function ExplorationDialogs({ interactions, client, capabilities, onSimilar, onIdentityChanged, editor, editorTitle = '给心动留一点范围' }) {
  const { panel, detail, detailController, resourceTrigger, close, reload, changed, feedback, reloadFeedback, want, openIdentity, toast } = interactions
  return <>
    {panel?.kind === 'identity' && <RecommendationIdentityReview client={client} scope="all" recordId={panel.recordId} onClose={close} onChanged={() => {
      want.resetFailures(); changed(); notifyRecommendationChange({ local: false }); onIdentityChanged?.()
    }} />}
    {panel && panel.kind !== 'identity' && <RecommendationDialog kind={panel.kind} title={panel.kind === 'filters' ? editorTitle : panel.kind === 'hidden' ? '已隐藏作品' : panel.item.title}
      onClose={close} resourceOpen={panel.kind === 'detail' && detail.status === 'ready' && detail.resourceOpen}
      resourceTriggerRef={resourceTrigger} onResourceClose={detailController.closeResources}
      headerActions={panel.kind === 'detail' && detail.status === 'ready' ? <RecommendationFeedbackMenu item={detail.item} onHide={feedback.controller.hide}
        operation={feedback.state.operations[detail.item.bangumiId]} disabled={feedback.state.pending || feedback.state.uncertain || feedback.state.version === null || want.operations[detail.item.bangumiId]?.status === 'adding'} onReload={() => reloadFeedback()} /> : null}
      resourcePanel={panel.kind === 'detail' && detail.status === 'ready' ? <RecommendationResourcePanel item={detail.item} open={detail.resourceOpen}
        sessionKey={`${detail.selection.session}:${detail.resourceSession || 0}:${detail.item.bangumiId}:${detail.item.evidenceToken}`} onClose={detailController.closeResources} /> : null}>
      {panel.kind === 'filters' ? editor
        : panel.kind === 'hidden' ? <>{feedback.state.uncertain && <div role="alert"><p>反馈结果尚未确认。</p>
          <button type="button" className="rec-button" disabled={feedback.state.pending} onClick={feedback.controller.retry}>重试确认原操作</button></div>}
          <RecommendationHiddenWorks state={feedback.hiddenState} feedback={feedback.state} onPage={feedback.hidden.load}
          onUndo={feedback.controller.undo} onReload={() => { feedback.controller.clearErrors(); feedback.hidden.refresh() }} writeBusy={id => want.operations[id]?.status === 'adding'} />{toast}</>
          : ['closed', 'loading', 'refreshing'].includes(detail.status) ? <p role="status">{detail.status === 'refreshing' ? '加入操作已确认，正在重新读取完整详情…' : '正在读取作品详情…'}</p>
            : detail.status === 'error' ? <div role="alert"><p>{detail.message}</p><button type="button" className="rec-button" onClick={detail.snapshotChanged ? reload : detailController.retry}>{detail.snapshotChanged ? '重新探索' : '重试详情'}</button></div>
              : <div className="rec-detail-content"><RecommendationCover item={detail.item} priority /><div><p className="rec-original-title">{detail.item.originalTitle}</p>
                <RecommendationMeta item={detail.item} /><h3>{detail.item.explorationQuery.intent === 'tags' ? '题材匹配依据' : '相似依据'}</h3><ExplorationEvidence item={detail.item} expanded onReload={reload} />
                <ExplorationTagChips tags={detail.item.tags} beforeNavigate={close} /><h3>故事简介</h3><p>{detail.item.synopsis || '暂未收录简介。'}</p><RecommendationDetailActions item={detail.item} resourceOpen={detail.resourceOpen}
                  resourceTriggerRef={resourceTrigger} onResourceOpen={detailController.openResources} onResourceClose={detailController.closeResources}
                  onClose={close} onWant={want.add} operation={want.operations[detail.item.bangumiId]} onIdentity={openIdentity}
                  onSimilar={capabilities?.navigation?.[1]?.available ? onSimilar : undefined}
                  writeBlocked={feedback.state.operations[detail.item.bangumiId]?.status === 'saving' || feedback.state.operations[detail.item.bangumiId]?.uncertain} /></div></div>}
    </RecommendationDialog>}
  </>
}
