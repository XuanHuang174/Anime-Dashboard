// Real production page/hooks, synthetic HTTP contracts only. Not in app routes.
import React, { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import Recommendations from '../../src/pages/Recommendations.jsx'
import { browsePage } from './browseContract.js'

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
let epoch = 0
const owned = new Set(), calls = [], pendingWants = [], pendingDetails = [], pendingLists = []
const config = { detail: 'normal', list: 'normal' }
const context = () => ({ algorithm_version: 'content.v1', profile_version: (epoch ? 'b' : 'a').repeat(64), preference_version: 0 })
const row = id => ({ bangumi_id: id, content_rating: 'non_adult', title_zh: `作品${id}`, title_jp: `Original ${id}`,
  season: epoch ? '2026-07' : '2026-04', status_type: 'TV', score: 7.5, tags: ['音乐'],
  synopsis: `完整详情 ctx-${epoch}。${'可滚动的详情正文。'.repeat(100)}`, evidence: null, reason: '',
  evidence_token: (epoch ? 'b' : 'a').repeat(64), evidence_context: context(), watch_status: owned.has(id) ? 'plan' : null })
const preferences = { contract_version: 'recommendations.3a.v1', version: 0, tags: [], available_tags: ['音乐'] }
const profile = { contract_version: 'recommendations.3a.v1', eligible_sample_count: 3, usable_sample_count: 3,
  cold_start: false, sample_policy: { minimum_score: '8.5' }, tag_summary: [{ tag: '音乐', sample_count: 3 }] }
function list(options) {
  const filler = base => Array.from({ length: 10 }, (_, n) => base + n)
  const ids = (options.category === 'explore' ? [301, 302, ...filler(601), 303]
    : options.category === 'familiar' ? [401, 402, ...filler(701), 403] : [101, 202, ...filler(501), 203])
    .filter(id => !options.filters?.hideRecorded || !owned.has(id))
  return browsePage({ ids, row, pageSize: 12, offset: ((options.page || 1) - 1) * 12,
    category: options.category || 'all', style: options.style || 'balanced', filters: options.filters,
    context: context(), snapshot: (epoch ? 'b' : 'a').repeat(32),
    makeCursor: offset => `loop-${options.category || 'all'}-${options.style || 'balanced'}-${offset}` })
}
const client = {
  profile: async signal => { calls.push({ kind: 'profile', signal }); return profile },
  preferences: async signal => { calls.push({ kind: 'preferences', signal }); return preferences },
  list: async (options, signal) => {
    calls.push({ kind: 'list', options, signal })
    if (config.list === 'error') throw { response: { status: 503 } }
    const snapshot = list(options)
    if (config.list === 'hold') { const pending = deferred(); pendingLists.push({ ...pending, snapshot }); return pending.promise }
    return snapshot
  },
  detail: async (id, signal, token) => {
    calls.push({ kind: 'detail', id, signal, token })
    if (config.detail === 'stale') throw { response: { status: 409 } }
    if (config.detail === 'error') throw new Error('offline')
    const snapshot = row(id)
    if (config.detail === 'hold') { const pending = deferred(); pendingDetails.push({ ...pending, snapshot }); return pending.promise }
    return snapshot
  },
  want: (id, signal) => { calls.push({ kind: 'want', id, signal }); const pending = deferred(); pendingWants.push({ ...pending, id }); return pending.promise },
}
client.browse = (options, signal) => {
  if (options.cursor) {
    const [, category, style, offset] = options.cursor.split('-')
    return client.list({ category, style, page: Number(offset) / 12 + 1 }, signal)
  }
  return client.list(options, signal)
}
function App() {
  const navigate = useNavigate()
  useEffect(() => { window.loopQA = { config, calls, owned,
    navigate,
    confirmWant() {
      const pending = pendingWants.shift(); owned.add(pending.id); epoch++
      pending.resolve({ contract_version: 'recommendations.want.v1', bangumi_id: pending.id, anime_id: 9,
        record_id: 7, content_rating: 'non_adult', created: true, outcome: 'created', watch_status: 'plan' })
    },
    resolveDetails() { for (const pending of pendingDetails.splice(0)) pending.resolve(pending.snapshot) },
    resolveLists() { for (const pending of pendingLists.splice(0)) pending.resolve(pending.snapshot) },
  } }, [navigate])
  return <Routes><Route path="/recommendations" element={<Recommendations client={client} />} />
    <Route path="/dashboard" element={<h1>隔离手账返回页</h1>} /></Routes>
}
localStorage.setItem('anime_journal_access_token', 'local-synthetic-token')
createRoot(document.getElementById('root')).render(<StrictMode><MemoryRouter initialEntries={['/recommendations']}><App /></MemoryRouter></StrictMode>)
