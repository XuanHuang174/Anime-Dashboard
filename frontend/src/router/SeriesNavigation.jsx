import { Route, Routes, useLocation } from 'react-router-dom'
import { seriesBackground } from '../utils/recommendationSeries.js'
import ProtectedRoute from '../components/ProtectedRoute.jsx'
import SeriesRoute from './SeriesRoute.jsx'
import { SeriesOverlayContext, SeriesForegroundContext } from '../hooks/useSeriesJournalReturn.js'
import CreditsRoute from './CreditsRoute.jsx'
import { creditsBackground, creditsRoute } from '../utils/recommendationCredits.js'
import useJournalExplorationReturn, { JournalExplorationAwayContext } from '../hooks/useJournalExplorationReturn.js'

const defaultSeries = overlay => <ProtectedRoute><SeriesRoute overlay={overlay} /></ProtectedRoute>
const defaultCredits = overlay => <ProtectedRoute><CreditsRoute overlay={overlay} /></ProtectedRoute>
// Keep the original route mounted while a series page occupies the browser top layer.
export default function SeriesNavigation({ children, renderSeries = defaultSeries, renderCredits = defaultCredits }) {
  const location = useLocation()
  const { origin: journalOrigin, session } = useJournalExplorationReturn(location)
  const seriesOrigin = /^\/recommendations\/series\/[1-9]\d*$/.test(location.pathname) ? seriesBackground(location.state?.seriesBackground) : null
  const creditsLocation = seriesOrigin && creditsRoute(seriesOrigin.pathname) ? seriesOrigin : creditsRoute(location.pathname) ? location : null
  const creditsOrigin = creditsLocation ? creditsBackground(creditsLocation.state?.creditsBackground) : null
  const background = creditsOrigin || seriesOrigin
  const routes = routeLocation => <Routes location={routeLocation}>{children}
    <Route path="/recommendations/series/:bangumiId" element={renderSeries(false)} />
    <Route path="/recommendations/credits/*" element={renderCredits(false)} /></Routes>
  return <JournalExplorationAwayContext.Provider value={Boolean(journalOrigin)}><SeriesForegroundContext.Provider value={Boolean(seriesOrigin)}>
    <SeriesOverlayContext.Provider value={Boolean(background || journalOrigin)}>
      <div key={session} hidden={Boolean(journalOrigin)} inert={Boolean(journalOrigin)}>{routes(journalOrigin || background || location)}</div>
    </SeriesOverlayContext.Provider>
    <SeriesOverlayContext.Provider value={Boolean(background)}>
    {journalOrigin && routes(background || location)}
    {creditsOrigin && <Routes location={creditsLocation}><Route path="/recommendations/credits/*" element={renderCredits(true)} /></Routes>}
    {seriesOrigin && <Routes><Route path="/recommendations/series/:bangumiId" element={renderSeries(true)} /></Routes>}
  </SeriesOverlayContext.Provider></SeriesForegroundContext.Provider></JournalExplorationAwayContext.Provider>
}
