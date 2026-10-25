import { Route, Routes, useLocation } from 'react-router-dom'
import { seriesBackground } from '../utils/recommendationSeries.js'
import ProtectedRoute from '../components/ProtectedRoute.jsx'
import SeriesRoute from './SeriesRoute.jsx'
import { SeriesOverlayContext } from '../hooks/useSeriesJournalReturn.js'

const defaultSeries = overlay => <ProtectedRoute><SeriesRoute overlay={overlay} /></ProtectedRoute>
// Keep the original route mounted while a series page occupies the browser top layer.
export default function SeriesNavigation({ children, renderSeries = defaultSeries }) {
  const location = useLocation()
  const background = /^\/recommendations\/series\/[1-9]\d*$/.test(location.pathname) ? seriesBackground(location.state?.seriesBackground) : null
  return <SeriesOverlayContext.Provider value={Boolean(background)}><Routes location={background || location}>{children}
    <Route path="/recommendations/series/:bangumiId" element={renderSeries(false)} /></Routes>
    {background && <Routes><Route path="/recommendations/series/:bangumiId" element={renderSeries(true)} /></Routes>}</SeriesOverlayContext.Provider>
}
