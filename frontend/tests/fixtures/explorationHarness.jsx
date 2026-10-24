// Local synthetic UI only. No network/API adapter, account or external covers.
import React, { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes, Link } from 'react-router-dom'
import Recommendations from '../../src/pages/Recommendations.jsx'
import api from '../../src/api/api.js'
import { page, detail, query, seed, versions, scope, adultScope } from './explorationContract.js'
import contract from './recommendationExplorationContract.json'
import { normalizeExplorationSearch } from '../../src/utils/recommendationExploration.js'
import { preferences, profileData, coldState, versions as profileVersions } from './profileSampling.js'
import { browsePage } from './browseContract.js'

api.defaults.adapter = async config => ({ config, status: 200, statusText: 'OK', headers: {},
  data: { contract_version: 'showcase.publication.v1', can_publish: false, count: 0, results: [], next_page: null } })
localStorage.setItem('anime_journal_access_token', 'synthetic-11c-only')
const owned = new Set(), hidden = new Map(), calls = []
let expires = Math.floor(Date.now() / 1000) + 900
let feedbackVersion = 0
let activeQuery = query, activeScope = scope, activeSeed = seed
const fail = (status, code) => { throw { response: { status, data: { code } } } }
const wait = async (name, signal) => {
  calls.push(name); await new Promise(resolve => setTimeout(resolve, 100))
  if (signal?.aborted) { const error = new Error('cancelled'); error.name = 'AbortError'; throw error }
}
const catalogRow = id => ({ ...seed, bangumi_id: id, title_zh: id === 1 ? '合成参照' : `合成故事${id}`,
  ...(id === 3 ? { tags: [], can_seed: false, unavailable_reason: 'no_valid_topics' } : {}) })
async function feedbackResult(action, body, signal) {
  await wait(`${action}:${body.bangumi_id}`, signal)
  if (body.expected_feedback_version !== feedbackVersion) fail(409, 'feedback_conflict')
  feedbackVersion++
  const now = new Date().toISOString()
  const row = { bangumi_id: body.bangumi_id, is_active: action === 'hide', feedback_version: feedbackVersion, hidden_at: now, updated_at: now,
    title_zh: `合成故事${body.bangumi_id}`, title_jp: '', season: '2026-01', status_type: 'TV' }
  hidden.set(body.bangumi_id, row)
  return { contract_version: 'recommendations.feedback.v1', action, operation_id: body.operation_id,
    feedback_version: feedbackVersion, operation_feedback_version: feedbackVersion, changed: true, replayed: false, feedback: row }
}
function scopedClient(selectedScope = 'safe') {
  const scopeContext = selectedScope === 'include_adult' ? adultScope : scope
  return {
    forScope: value => scopedClient(value),
    capabilities: async signal => { await wait('capabilities', signal); return { contract_version: 'recommendations.capabilities.v1',
      exploration: contract.capabilities, algorithm_version: 'content.v1', browse_enabled: true, rollout_revision: 'b'.repeat(64) } },
    explorationCatalog: async (raw, signal) => { await wait(`search:${raw}`, signal); const q = normalizeExplorationSearch(raw)
      return { contract_version: contract.search.contract_version, identity_namespace: 'bangumi', taxonomy_version: 'genres.v8', feature_rule_version: 'journal-genres.v3',
        query: q, limit: 15, has_more: false, content_scope_context: scopeContext,
        results: q.kind === 'bangumi_id' ? [catalogRow(Number(q.value))] : [catalogRow(1), catalogRow(3)] } },
    explorationBrowse: async (params, signal) => {
      await wait(`browse:${JSON.stringify(params)}`, signal)
      if (!params.cursor) { activeQuery = params; activeSeed = catalogRow(params.seed_bangumi_id); activeScope = scopeContext; expires = Math.floor(Date.now() / 1000) + 900 }
      const ids = Array.from({ length: 30 }, (_, n) => n + 10).filter(id => id !== activeQuery.seed_bangumi_id && !hidden.get(id)?.is_active && (!activeQuery.hide_recorded || !owned.has(id)))
      const batch = params.cursor === 'batch-two' ? 2 : 1, part = params.cursor === 'page-two' ? 2 : 1
      const data = page({ batch, part, count: ids.length }), offset = (batch - 1) * 24 + (part - 1) * 12
      data.query = activeQuery; data.selected_seed = activeSeed; data.snapshot_expires_at = expires; data.content_scope_context = scopeContext
      data.batch_hero_bangumi_id = ids[(batch - 1) * 24]
      data.results = data.results.map((row, i) => ({ ...row, bangumi_id: ids[offset + i], title_zh: `合成故事${ids[offset + i]}`,
        watch_status: owned.has(ids[offset + i]) ? 'plan' : null, evidence: { ...row.evidence, source: { bangumi_id: activeSeed.bangumi_id, title: activeSeed.title_zh, tags: activeSeed.tags } } }))
      return data
    },
    explorationContext: async (_, signal) => { await wait('context', signal); return { contract_version: 'recommendations.explore.context.v1', valid: true,
      snapshot_id: '1'.repeat(32), snapshot_expires_at: expires, query_fingerprint: versions.query_fingerprint,
      evidence_context: versions, selected_seed: { bangumi_id: activeQuery.seed_bangumi_id }, content_scope_context: activeScope } },
    explorationDetail: async (id, _, signal) => { await wait(`detail:${id}`, signal); const data = detail(id)
      return { ...data, query: activeQuery, selected_seed: activeSeed, content_scope_context: scopeContext,
        snapshot_expires_at: expires,
        watch_status: owned.has(id) ? 'plan' : null, evidence_token: owned.has(id) ? 'f'.repeat(64) : data.evidence_token,
        evidence: { ...data.evidence, source: { bangumi_id: activeSeed.bangumi_id, title: activeSeed.title_zh, tags: activeSeed.tags } } } },
    explorationRecord: async (id, signal) => { await wait(`record:${id}`, signal); return { contract_version: 'recommendations.explore.record.v1',
      record_id: id, bangumi_id: 1, identity_state: 'linked', content_scope_context: scopeContext } },
    want: async (id, signal) => { await wait(`want:${id}`, signal); const created = !owned.has(id); owned.add(id)
      return { contract_version: 'recommendations.want.v1', content_rating: 'non_adult', bangumi_id: id, record_id: id + 1000,
        anime_id: id + 2000, created, outcome: created ? 'created' : 'existing', watch_status: 'plan' } },
    hiddenFeedback: async (params, signal) => { await wait('hidden', signal); const rows = [...hidden.values()].filter(row => row.is_active)
      return { contract_version: 'recommendations.feedback.v1', feedback_version: feedbackVersion, count: rows.length,
        page: 1, page_size: params?.page_size || 12, next_page: null, results: rows.slice(0, params?.page_size || 12) } },
    hideFeedback: (body, signal) => feedbackResult('hide', body, signal),
    undoFeedback: (body, signal) => feedbackResult('undo', body, signal),
    eligibility: async signal => { await wait('eligibility', signal); return { available: false } },
    profile: async () => profileData(), preferences: async () => preferences(),
    context: async () => ({ contract_version: 'recommendations.context.v1', rollout_revision: 'b'.repeat(64), browse_enabled: true,
      context: { ...profileVersions('8.5'), feedback_version: 0 } }),
    browse: async options => ({ ...browsePage({ ...options, context: { ...profileVersions('8.5'), feedback_version: 0 } }), cold_start_state: coldState() }),
  }
}
const client = scopedClient()
const viewport = new URLSearchParams(window.location.search).get('viewport')
const app = <StrictMode><BrowserRouter><Routes>
  <Route path="/recommendations/*" element={<Recommendations client={client} />} />
  <Route path="/dashboard" element={<main><h1>11C 隔离手账</h1><Link to="/recommendations/similar" state={{ journalRecord: 2 }}>找相似</Link></main>} />
  <Route path="*" element={<Link to="/recommendations/similar">开始验收单番相似</Link>} />
</Routes></BrowserRouter></StrictMode>
// Fixed iframe gives a real CSS viewport for desktop/mobile checks in a narrow side panel.
createRoot(document.getElementById('root')).render(viewport ? <iframe title={`${viewport}px 隔离验收视口`}
  src="/tests/fixtures/explorationHarness.html" style={{ border: 0, width: `${Number(viewport)}px`, height: '2400px', transformOrigin: 'top left', transform: `scale(${Math.min(1, window.innerWidth / Number(viewport))})` }} /> : app)
