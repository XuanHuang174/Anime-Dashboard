import AnimeResourceDrawer from '../AnimeResourceDrawer.jsx'
import RecommendationResourceBoundary from './RecommendationResourceBoundary.jsx'

// A slot INSIDE the native dialog; never a body portal or second modal.
export default function RecommendationResourcePanel({ item, open, sessionKey, onClose, client }) {
  return <section id="rec-resource-panel" className="rec-resource-panel" aria-label="当前作品资源检索">
    <header className="rec-resource-context">
      <button type="button" className="rec-button" onClick={onClose}>← 返回详情</button>
      <h3>{item.title}</h3><p>放送季度：{item.season || '未知'}</p>
      <p>关键词仅用于资源检索，不代表已匹配正确季度或版本；请核对原始资源标题。</p>
    </header>
    <RecommendationResourceBoundary key={sessionKey}>
      <AnimeResourceDrawer id="recResourceDrawer" open={open} title={item.title} sessionKey={sessionKey}
        trapFocus={false} onClose={onClose} client={client} />
    </RecommendationResourceBoundary>
  </section>
}
