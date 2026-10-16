import { RecommendationActions } from './RecommendationCard.jsx'

// Resources render in the parent dialog's sibling slot, not inside the synopsis.
export default function RecommendationDetailActions({ item, resourceOpen, onResourceOpen, onResourceClose,
  onClose, onWant, operation, onIdentity, resourceTriggerRef, writeBlocked }) {
  return <>
    <div className="rec-detail-actions">
      <RecommendationActions item={item} detailLabel="关闭详情" onDetail={onClose}
        onWant={onWant} operation={operation} onIdentity={onIdentity} writeBlocked={writeBlocked} />
      <button ref={resourceTriggerRef} type="button" className="rec-button rec-resource-trigger" aria-expanded={resourceOpen}
        aria-controls="rec-resource-panel" onClick={resourceOpen ? onResourceClose : onResourceOpen}>🧲 寻找资源</button>
    </div>
  </>
}
