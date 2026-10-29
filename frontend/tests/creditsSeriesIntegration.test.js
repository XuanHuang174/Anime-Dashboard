import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter, Route, useLocation } from 'react-router-dom'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { CREDIT_DEFAULTS } from '../src/utils/recommendationCredits.js'
import { seriesBackground } from '../src/utils/recommendationSeries.js'

const server = await createServer({ configFile: false, plugins: [react()], server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { default: Navigation } = await server.ssrLoadModule('/src/router/SeriesNavigation.jsx')
const { default: Filter } = await server.ssrLoadModule('/src/components/recommendations/CreditsFilterPanel.jsx')
const { default: Help } = await server.ssrLoadModule('/src/components/recommendations/BrowseExplanation.jsx')
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props))
function Probe({ kind, overlay }) {
  return React.createElement('div', { 'data-layer': kind, 'data-overlay': String(overlay), 'data-path': useLocation().pathname })
}
const root = { pathname: '/dashboard', search: '', state: null, key: 'journal' }
const credits = overlay => ({ pathname: '/recommendations/credits/studio/201', search: '', key: 'credits',
  state: { creditsBackground: overlay ? root : null, creditsScope: 'include_adult' } })
function layers(location) {
  return renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: [location] }, React.createElement(Navigation, {
    renderSeries: overlay => React.createElement(Probe, { kind: 'series', overlay }),
    renderCredits: overlay => React.createElement(Probe, { kind: 'credits', overlay }),
  }, React.createElement(Route, { path: '/dashboard', element: React.createElement(Probe, { kind: 'journal', overlay: false }) }))))
}
test('series over credits retains both credits route and original journal layer for either entry path', () => {
  for (const overlay of [true, false]) {
    const origin = credits(overlay)
    const html = layers({ pathname: '/recommendations/series/100', state: { seriesBackground: origin, seriesScope: 'include_adult' } })
    assert.equal((html.match(/data-layer="credits"/g) || []).length, 1)
    assert.equal((html.match(/data-layer="series"/g) || []).length, 1)
    assert.equal((html.match(/data-layer="journal"/g) || []).length, overlay ? 1 : 0)
    assert.match(html, new RegExp(`data-layer="credits" data-overlay="${overlay}" data-path="/recommendations/credits/studio/201"`))
    assert.match(html, /data-layer="series" data-overlay="true"/)
    assert.equal(layers(origin).includes('data-layer="series"'), false)
    assert.equal(seriesBackground(origin), origin)
  }
  for (const pathname of ['/recommendations/credits/voice_actor/0', '/recommendations/credits/studio/2147483648', '/recommendations/credits/guess/1']) {
    assert.equal(seriesBackground({ pathname }), null)
  }
})

test('credits filter contains only the drawer controls and preserves completed-view ownership rule', () => {
  const html = render(Filter, { options: { ...CREDIT_DEFAULTS, view: 'completed', hide_recorded: false, min_score: 6.3, max_score: 7.5 }, adult: false, client: {} })
  assert.match(html, /rec-filter-scroll/); assert.match(html, /rec-filter-footer/)
  assert.match(html, /评分下限/); assert.match(html, /value="6.3"/); assert.match(html, /value="7.5"/)
  assert.match(html, /type="checkbox" disabled=""/)
  assert.doesNotMatch(html, /<option value="score_desc"|<option value="completed"/)
})

test('explanation toggle keeps the same summary row when its separate body opens', () => {
  const props = { summary: '当前条件', onToggle() {}, children: '详细说明' }
  const closed = render(Help, { ...props, open: false }), open = render(Help, { ...props, open: true })
  assert.match(closed, /aria-expanded="false"/); assert.doesNotMatch(closed, /详细说明/)
  assert.match(open, /aria-expanded="true"/); assert.match(open, /browse-explanation-body/)
  assert.ok(open.indexOf('browse-explanation-toggle') < open.indexOf('browse-explanation-body'))
  assert.match(open, /当前条件/)
})
