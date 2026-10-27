import { lazy, Suspense } from 'react'
import { RecommendationRouteBoundary } from '../components/recommendations/RecommendationLoading.jsx'
const CreditsExplorer = lazy(() => import('../pages/CreditsExplorer.jsx'))
export default function CreditsRoute(props) {
  return <RecommendationRouteBoundary><Suspense fallback={<main className="memphis-rec-container"><div className="rec-page"><h1>人物与制作</h1><p role="status">正在打开人物与制作…</p></div></main>}><CreditsExplorer {...props} /></Suspense></RecommendationRouteBoundary>
}
