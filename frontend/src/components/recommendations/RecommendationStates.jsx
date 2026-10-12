import { Link } from 'react-router-dom'

export function RecommendationSkeleton() {
  return <section className="rec-skeleton" aria-busy="true" aria-label="正在加载推荐">
    <p className="rec-muted" role="status">正在整理下一次心动…</p>
    <div className="rec-skeleton-hero" aria-hidden="true"><span /><div><i /><i /><i /></div></div>
    <div className="rec-grid" aria-hidden="true">{Array.from({ length: 4 }, (_, index) =>
      <div className="rec-skeleton-card" key={index}><span /><i /><i /><i /></div>)}</div>
  </section>
}

export function RecommendationEmptyState({ state, onRetry, onReset, onPreferences, needsTags = false, needsIdentity = false, onIdentity }) {
  const content = {
    cold: ['✧', '让我们先认识你的口味', '请先给几部喜欢的番剧打分，或选择喜好。样本不足时，我们不会编造个性化结果。'],
    empty: ['⌕', '这次筛选，还没遇见心头好', '试着放宽评分或作品类型，给意外的心动留一点空间。'],
    error: ['↻', '推荐暂时没有准备好', '暂时无法加载结果。你的手账不会受影响，可以稍后重试。'],
  }[state]
  return <section className="rec-empty" role="status" aria-live="polite">
    <span className="rec-empty-symbol" aria-hidden="true">{content[0]}</span>
    <span className="rec-eyebrow">A LITTLE PAUSE, NOT THE END</span>
    <h2>{needsIdentity ? '让高分旧作品重新参与推荐' : needsTags ? '给你的高分作品补充一点题材' : content[1]}</h2>
    <p>{needsIdentity ? '你的高分记录仍在，只是部分旧作品尚未关联标准资料。核对身份后即可参与画像，不需要重新评分。'
      : needsTags ? '有效题材不足，请完善作品标签或选择明确喜好；不会凭空生成个性化结果。' : content[2]}</p>
    <div className="rec-empty-actions">
      {state === 'cold' ? <>{needsIdentity ? <button type="button" className="rec-button rec-button--primary" onClick={onIdentity}>核对旧作品</button>
        : <Link className="rec-button rec-button--primary" to="/dashboard">去手账评分</Link>}
        <button type="button" className="rec-button" onClick={onPreferences}>选择喜好</button></>
        : <><button type="button" className="rec-button rec-button--primary" onClick={onRetry}>重新加载</button>
          <button type="button" className="rec-button" onClick={onReset}>恢复筛选</button></>}
    </div>
  </section>
}
