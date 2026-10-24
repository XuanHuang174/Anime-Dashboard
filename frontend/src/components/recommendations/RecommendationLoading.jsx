import { Component, useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
// Eagerly imported by the route, not dependent on the lazy page's CSS/GSAP.
import '../../styles/recommendations-loading.css'

export const RECOMMENDATION_SLOW_LOAD_MS = 8000

export function RecommendationLoadingNotice({ phase = 'data' }) {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), RECOMMENDATION_SLOW_LOAD_MS)
    return () => window.clearTimeout(timer)
  }, [])
  return <div className="rec-load-notice" role="status" aria-live="polite" aria-atomic="true">
    <p>{phase === 'route' ? '正在翻开番剧探索手账…' : phase === 'tags' ? '正在寻找符合题材条件的作品…' : phase === 'explore' ? '正在读取参照与相似作品…' : '正在读取你的口味与推荐…'}</p>
    {slow && <small>这次加载较慢，请稍候。预计还需 5s~15s。</small>}
  </div>
}

export function RecommendationHeroSkeleton({ phase = 'data' }) {
  return <div className="rec-skeleton-hero rec-load-hero">
    <div className="rec-load-poster" aria-hidden="true"><div className="rec-load-book"><span /><span /><i /></div></div>
    <div className="rec-load-body">
      <RecommendationLoadingNotice phase={phase} />
      <div className="rec-load-copy" aria-hidden="true">
        <span className="rec-load-block rec-load-title" /><span className="rec-load-block rec-load-meta" />
        <div className="rec-load-reason"><span className="rec-load-block" /><span className="rec-load-block" /></div>
        <div className="rec-load-tags"><span className="rec-load-block" /><span className="rec-load-block" /></div>
      </div>
      <div className="rec-load-actions" aria-hidden="true"><span /><span /></div>
    </div>
  </div>
}

export function RecommendationTasteSkeleton() {
  return <div className="rec-load-taste-lines" aria-hidden="true">{[0, 1, 2].map(index =>
    <div key={index}><span className="rec-load-block" /><span className="rec-load-block" /></div>)}
    <div className="rec-load-tags"><span className="rec-load-block" /><span className="rec-load-block" /></div>
  </div>
}

export function RecommendationGridSkeleton() {
  return <div className="rec-load-grid-shell" aria-hidden="true"><span className="rec-load-block rec-load-section-title" />
    <div className="rec-grid">{[0, 1, 2, 3].map(index => <div className="rec-load-grid-card" key={index}>
      <div className="rec-load-grid-cover" /><div className="rec-load-grid-copy"><span className="rec-load-block" />
        <span className="rec-load-block" /><span className="rec-load-block" /></div>
    </div>)}</div>
  </div>
}

export function RecommendationRouteLoading({ failed = false }) {
  const similar = useLocation().pathname.startsWith('/recommendations/similar')
  const tags = useLocation().pathname.startsWith('/recommendations/tags')
  return <main className="rec-loading-route" aria-label={failed ? '推荐页面暂不可用' : '正在打开推荐页面'}>
    <div className="rec-load-page">
      <div className="rec-load-masthead"><nav aria-label="推荐加载导航"><Link to="/dashboard">← 返回我的手账</Link>
        <span className="rec-load-nav-placeholder" aria-hidden="true"><i /><i /></span></nav>
        <header><span>我的手账</span><h1>番剧探索</h1></header>
        <div className="rec-load-controls" aria-hidden="true"><span /><span /><span /><i /></div>
      </div>
      {failed ? <section className="rec-load-route-error" role="alert"><span aria-hidden="true">↻</span>
        <h2>推荐页面暂时无法打开</h2><p>可以重新加载页面，或返回我的手账。你的资料不会被修改。</p>
        <button type="button" onClick={() => window.location.reload()}>重新加载页面</button>
      </section> : <>
        <div className="rec-load-featured" aria-busy="true"><RecommendationHeroSkeleton phase="route" />
          <aside className="rec-load-taste"><h2>{similar ? '本次参照' : tags ? '探索条件' : '我的口味'}</h2><RecommendationTasteSkeleton /></aside></div>
        <RecommendationGridSkeleton />
      </>}
    </div>
  </main>
}

export class RecommendationRouteBoundary extends Component {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() { return this.state.failed ? <RecommendationRouteLoading failed /> : this.props.children }
}
