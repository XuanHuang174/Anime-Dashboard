import { Route, Routes, useLocation } from 'react-router-dom'
import { seriesBackground } from '../utils/recommendationSeries.js'
import ProtectedRoute from '../components/ProtectedRoute.jsx'
import SeriesRoute from './SeriesRoute.jsx'
import { SeriesOverlayContext } from '../hooks/useSeriesJournalReturn.js'
import CreditsRoute from './CreditsRoute.jsx'
import { creditsBackground, creditsRoute } from '../utils/recommendationCredits.js'

const defaultSeries = overlay => <ProtectedRoute><SeriesRoute overlay={overlay} /></ProtectedRoute>
const defaultCredits = overlay => <ProtectedRoute><CreditsRoute overlay={overlay} /></ProtectedRoute>
// Keep the original route mounted while a series page occupies the browser top layer.
export default function SeriesNavigation({ children, renderSeries = defaultSeries, renderCredits = defaultCredits }) {
  const location = useLocation()
  const credits = creditsRoute(location.pathname)
  const background = /^\/recommendations\/series\/[1-9]\d*$/.test(location.pathname) ? seriesBackground(location.state?.seriesBackground)
    : credits ? creditsBackground(location.state?.creditsBackground) : null
  return <SeriesOverlayContext.Provider value={Boolean(background)}><Routes location={background || location}>{children}
    <Route path="/recommendations/series/:bangumiId" element={renderSeries(false)} />
    <Route path="/recommendations/credits/*" element={renderCredits(false)} /></Routes>
    {background && <Routes><Route path="/recommendations/series/:bangumiId" element={renderSeries(true)} />
      <Route path="/recommendations/credits/*" element={renderCredits(true)} /></Routes>}</SeriesOverlayContext.Provider>
}
