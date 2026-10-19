import { lazy, Suspense } from 'react'
import { RecommendationRouteBoundary, RecommendationRouteLoading } from '../components/recommendations/RecommendationLoading.jsx'

const Recommendations = lazy(() => import('../pages/Recommendations.jsx'))

export default function RecommendationRoute({ client }) {
  return <RecommendationRouteBoundary><Suspense fallback={<RecommendationRouteLoading />}>
    <Recommendations client={client} />
  </Suspense></RecommendationRouteBoundary>
}
