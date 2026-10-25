// Local synthetic UI only. No network/API adapter, account or external covers.
import React, { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Link } from 'react-router-dom'
import Recommendations from '../../src/pages/Recommendations.jsx'
import api from '../../src/api/api.js'
import { page, detail, tagPage, tagDetail, query, seed, versions, scope, adultScope } from './explorationContract.js'
import contract from './recommendationExplorationContract.json'
import { normalizeExplorationSearch } from '../../src/utils/recommendationExploration.js'
import { preferences, profileData, coldState, versions as profileVersions } from './profileSampling.js'
import { browsePage } from './browseContract.js'
import SeriesNavigation from '../../src/router/SeriesNavigation.jsx'
import SeriesRelations from '../../src/pages/SeriesRelations.jsx'
import { related, annotations } from './seriesExplorationContract.js'
import { seriesPage, seriesDetail } from './seriesContract.js'

const harnessParams = new URLSearchParams(window.location.search)
const seriesEnabled = harnessParams.get('series') === '1' || location.pathname.endsWith('/seriesExplorationHarness.html')
const reducedMotion = harnessParams.get('motion') === 'reduce'
if (reducedMotion) {
  // Isolated preference simulation, never changes browser/OS settings.
  const originalMatchMedia = window.matchMedia.bind(window)
  window.matchMedia = query => query === '(prefers-reduced-motion: reduce)' ? {
    media: query, matches: true, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
  } : originalMatchMedia(query)
  const style = document.createElement('style')
  style.textContent = '* { animation: none !important; transition: none !important; scroll-behavior: auto !important; }'
  document.head.append(style)
}

api.defaults.adapter = async config => ({ config, status: 200, statusText: 'OK', headers: {},
  data: { contract_version: 'showcase.publication.v1', can_publish: false, count: 0, results: [], next_page: null } })
localStorage.setItem('anime_journal_access_token', 'synthetic-11c-only')
const owned = new Set(), hidden = new Map(), calls = []
let expires = Math.floor(Date.now() / 1000) + 900
let feedbackVersion = 0
const snapshots = new Map(); let serial = 0
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
function scopedClient(selectedScope = 'safe', seriesPolicy = false) {
  const scopeContext = selectedScope === 'include_adult' ? adultScope : scope
  return {
    contentScope: selectedScope, seriesExploration: seriesPolicy,
    forSeries: value => scopedClient(selectedScope, value),
    forScope: value => scopedClient(value, seriesPolicy),
    capabilities: async signal => { await wait('capabilities', signal); return { contract_version: 'recommendations.capabilities.v1',
      exploration: { ...contract.capabilities, series_exploration: { enabled: seriesEnabled, policy_version: 'series-explore.direct.v1' } }, algorithm_version: 'content.v1', browse_enabled: true, rollout_revision: 'b'.repeat(64) } },
    seriesAnnotations: async (ids, signal) => { await wait('annotations', signal); return { ...annotations(ids), catalog_revision: versions.catalog_feature_version, content_scope_context: scopeContext } },
    series: async (source, context, signal) => { await wait('series', signal); return { ...mockSeries(source), content_scope_context: scopeContext } },
    seriesDetail: async (source, target, context, signal) => { await wait('seriesDetail', signal); return { ...seriesDetail(mockSeries(source), target), content_scope_context: scopeContext } },
    seriesWant: async (source, target, context, signal) => { await wait('seriesWant', signal); const created = !owned.has(target); owned.add(target)
      return { contract_version: 'recommendations.want.v1', bangumi_id: target, anime_id: 2000 + target, record_id: 1000 + target, created,
        outcome: created ? 'created' : 'existing', watch_status: 'plan', content_rating: 'non_adult', content_scope_context: scopeContext } },
    explorationContract: async signal => { await wait('contract', signal); return { ...contract, content_scope_context: scopeContext } },
    explorationOptions: async signal => { await wait('options', signal); return { contract_version: contract.capabilities.options_contract_version, taxonomy_version: 'genres.v8',
      feature_rule_version: 'journal-genres.v3', catalog_feature_version: versions.catalog_feature_version, count_definition: 'scoped_catalog_without_result_filters',
      catalog_count: 30, count_description: '当前内容范围的目录数量，未叠加结果筛选；不扣除已有记录或隐藏反馈。',
      exclusion_description: '按目录已有标注筛选，不能保证作品绝无相关内容。', content_scope_context: scopeContext,
      tags: contract.available_tags.map((tag, i) => ({ tag, count: i < 4 || tag === '音乐' ? 30 : 0 })) } },
    explorationCatalog: async (raw, signal) => { await wait(`search:${raw}`, signal); const q = normalizeExplorationSearch(raw)
      return { contract_version: contract.search.contract_version, identity_namespace: 'bangumi', taxonomy_version: 'genres.v8', feature_rule_version: 'journal-genres.v3',
        query: q, limit: 15, has_more: false, content_scope_context: scopeContext,
        results: q.kind === 'bangumi_id' ? [catalogRow(Number(q.value))] : [catalogRow(1), catalogRow(3)] } },
    explorationBrowse: async (params, signal, withSeries = false) => {
      await wait(`browse:${JSON.stringify(params)}`, signal)
      const snapshotId = params.cursor ? params.cursor.split(':')[0] : (++serial).toString(16).padStart(32, '0')
      if (!params.cursor) snapshots.set(snapshotId, { query: params, seed: params.intent === 'tags' ? null : catalogRow(params.seed_bangumi_id),
        scope: scopeContext, expires: Math.floor(Date.now() / 1000) + 900, withSeries })
      const snap = snapshots.get(snapshotId), q = snap.query
      const noMatch = q.intent === 'tags' && (q.include_tags.includes('悬疑') || q.include_tags.includes('音乐') && q.match === 'all' && q.include_tags.includes('校园'))
      const ids = noMatch ? [] : Array.from({ length: 30 }, (_, n) => n + 10).filter(id => id !== q.seed_bangumi_id && !hidden.get(id)?.is_active && (!q.hide_recorded || !owned.has(id)))
      const batch = params.cursor?.endsWith('batch-two') ? 2 : 1, part = params.cursor?.endsWith('page-two') ? 2 : 1
      const data = q.intent === 'tags' ? tagPage(q, { batch, part, count: ids.length }) : page({ batch, part, count: ids.length })
      if (snap.withSeries) { data.contract_version = 'recommendations.explore.series.v1'; data.series_context = related() }
      const offset = (batch - 1) * 24 + (part - 1) * 12
      data.query = q; data.selected_seed = snap.seed; data.snapshot_expires_at = snap.expires; data.content_scope_context = snap.scope
      data.snapshot_id = snapshotId; data.snapshot_context = snapshotId
      data.next_page_cursor = data.next_page_cursor ? `${snapshotId}:${data.next_page_cursor}` : null
      data.next_batch_cursor = data.next_batch_cursor ? `${snapshotId}:${data.next_batch_cursor}` : null
      data.batch_hero_bangumi_id = ids[(batch - 1) * 24] ?? null
      data.results = data.results.map((row, i) => ({ ...row, bangumi_id: ids[offset + i], title_zh: `合成故事${ids[offset + i]}`,
        detail_context: `${snapshotId}:${ids[offset + i]}`,
        watch_status: owned.has(ids[offset + i]) ? 'plan' : null, evidence: { ...row.evidence,
          source: snap.seed ? { bangumi_id: snap.seed.bangumi_id, title: snap.seed.title_zh, tags: snap.seed.tags } : null } }))
      return data
    },
    explorationContext: async (context, signal) => { await wait('context', signal); const snap = snapshots.get(context)
      return { contract_version: 'recommendations.explore.context.v1', intent: snap.query.intent, valid: true,
      snapshot_id: context, snapshot_expires_at: snap.expires, query_fingerprint: versions.query_fingerprint,
      evidence_context: { ...versions, ranking_policy_version: snap.query.intent === 'tags' ? 'tag-explore.v1' : 'similarity.v1' },
      selected_seed: snap.seed ? { bangumi_id: snap.seed.bangumi_id } : null, content_scope_context: snap.scope,
      ...(snap.withSeries ? { series_context: related() } : {}) } },
    explorationDetail: async (id, context, signal) => { await wait(`detail:${id}`, signal); const snap = snapshots.get(context.split(':')[0])
      const data = snap.query.intent === 'tags' ? tagDetail(snap.query, id) : detail(id)
      return { ...data, query: snap.query, selected_seed: snap.seed, content_scope_context: snap.scope,
        ...(snap.withSeries ? { series_context: related() } : {}),
        snapshot_expires_at: snap.expires, detail_context: context,
        watch_status: owned.has(id) ? 'plan' : null, evidence_token: owned.has(id) ? 'f'.repeat(64) : data.evidence_token,
        evidence: { ...data.evidence, source: snap.seed ? { bangumi_id: snap.seed.bangumi_id, title: snap.seed.title_zh, tags: snap.seed.tags } : null } } },
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
    eligibility: async signal => { await wait('eligibility', signal); return { contract_version: 'recommendations.eligibility.session.v1', policy_version: 'eligibility.session.v1',
      effective_status: 'not_requested', authorization_source: 'none', manual_authorization_valid: false, eligibility_version: 0, age_assurance_status: 'not_performed',
      can_apply: false, adult_access_allowed: true, serving_integration: true, effective_scope: 'safe', decision_message: '', valid_until: null, eligibility_revision: 'a'.repeat(64) } },
    profile: async () => profileData(), preferences: async () => preferences(),
    context: async () => ({ contract_version: 'recommendations.context.v1', rollout_revision: 'b'.repeat(64), browse_enabled: true,
      context: { ...profileVersions('8.5'), feedback_version: 0 } }),
    browse: async options => ({ ...browsePage({ ...options, context: { ...profileVersions('8.5'), feedback_version: 0 } }), cold_start_state: coldState() }),
  }
}
function mockSeries(source) {
  const data = seriesPage({ source })
  if (source === 100) data.results = Array.from({ length: 30 }, (_, n) => ({ ...data.results[0], bangumi_id: n + 10, title_zh: `合成故事${n + 10}`, title_jp: '',
    watch_status: owned.has(n + 10) ? 'plan' : null, record_state: owned.has(n + 10) ? 'linked' : 'no_confirmed_record', can_add_from_series: !owned.has(n + 10) }))
  return data
}
const client = scopedClient()
const viewport = harnessParams.get('viewport'), height = Number(harnessParams.get('height')) || (viewport === '390' ? 844 : 900)
// Test gesture is visible on the landing page and works inside native dialogs.
window.addEventListener('keydown', event => {
  if (event.altKey && event.shiftKey && event.code === 'KeyU') window.dispatchEvent(new Event('recommendation-data-changed'))
})
const app = <StrictMode><BrowserRouter><SeriesNavigation renderSeries={overlay => <SeriesRelations client={client} overlay={overlay} />}>
  <Route path="/recommendations/*" element={<Recommendations client={client} />} />
  <Route path="/dashboard" element={<main><h1>11C 隔离手账</h1><Link to="/recommendations/similar" state={{ journalRecord: 2 }}>找相似</Link></main>} />
  <Route path="*" element={<main><Link to="/recommendations">开始验收个性化推荐</Link><br /><Link to="/recommendations/similar">开始验收单番相似</Link><br /><Link to="/recommendations/tags">开始验收按标签找</Link><br /><Link to="/recommendations/tags?include=日常,音乐&exclude=战斗">验收标签组合</Link><p>隔离验收快捷键：Alt+Shift+U 模拟后续数据失效。</p>{reducedMotion && <p>正在模拟 prefers-reduced-motion；没有更改浏览器设置。</p>}</main>} />
</SeriesNavigation></BrowserRouter></StrictMode>
// Fixed iframe gives a real CSS viewport for desktop/mobile checks in a narrow side panel.
createRoot(document.getElementById('root')).render(viewport ? <iframe title={`${viewport}px 隔离验收视口`}
  src={`/tests/fixtures/explorationHarness.html?${new URLSearchParams({ ...(seriesEnabled ? { series: '1' } : {}), ...(reducedMotion ? { motion: 'reduce' } : {}) })}`} style={{ border: 0, width: `${Number(viewport)}px`, height: `${height}px`, transformOrigin: 'top left', transform: `scale(${Math.min(1, window.innerWidth / Number(viewport))})` }} /> : app)
