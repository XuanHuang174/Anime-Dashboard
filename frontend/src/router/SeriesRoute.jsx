import { lazy, Suspense } from 'react'
import { RecommendationRouteBoundary, RecommendationRouteLoading } from '../components/recommendations/RecommendationLoading.jsx'
const SeriesRelations = lazy(() => import('../pages/SeriesRelations.jsx'))
export default function SeriesRoute(props) {
  return <RecommendationRouteBoundary><Suspense fallback={<RecommendationRouteLoading />}><SeriesRelations {...props} /></Suspense></RecommendationRouteBoundary>
}
