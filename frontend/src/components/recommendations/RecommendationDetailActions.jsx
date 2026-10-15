import { useRef } from 'react'
import { RecommendationActions } from './RecommendationCard.jsx'
import AnimeResourceDrawer from '../AnimeResourceDrawer.jsx'
import RecommendationResourceBoundary from './RecommendationResourceBoundary.jsx'

// 7A uses an inline section INSIDE the native dialog top layer. 7B can enhance
// its layout without introducing a second search service or body-level portal.
export default function RecommendationDetailActions({ item, resourceOpen, onResourceOpen, onResourceClose,
  onClose, onWant, operation, onIdentity }) {
  const trigger = useRef(null)
  const closeResources = () => { onResourceClose(); trigger.current?.focus({ preventScroll: true }) }
  return <>
    <div className="rec-detail-actions">
      <RecommendationActions item={item} detailLabel="关闭详情" onDetail={onClose}
        onWant={onWant} operation={operation} onIdentity={onIdentity} />
      <button ref={trigger} type="button" className="rec-button rec-resource-trigger" aria-expanded={resourceOpen}
        aria-controls="rec-resource-panel" onClick={resourceOpen ? closeResources : onResourceOpen}>🧲 寻找资源</button>
    </div>
    {resourceOpen && <section id="rec-resource-panel" className="rec-resource-panel" aria-label="当前作品资源检索"
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeResources() } }}>
      <h3>{item.title}</h3><p className="rec-muted">放送季度：{item.season || '未知'}</p>
      <p className="rec-muted">关键词仅用于资源检索，不代表已匹配正确季度或版本；请核对原始资源标题。</p>
      <RecommendationResourceBoundary key={`${item.bangumiId}:${item.evidenceToken || ''}`}>
        <AnimeResourceDrawer id="recResourceDrawer" open title={item.title} onClose={closeResources} />
      </RecommendationResourceBoundary>
    </section>}
  </>
}
