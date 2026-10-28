// Synthetic fixture only: no real accounts, API, media, or writes.
import React, { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, Route } from 'react-router-dom'
import SeriesNavigation from '../../src/router/SeriesNavigation.jsx'
import CreditsExplorer from '../../src/pages/CreditsExplorer.jsx'
import CreditsSummary from '../../src/components/recommendations/CreditsSummary.jsx'
import CreditsEntry from '../../src/components/recommendations/CreditsEntry.jsx'
import DetailModal from '../../src/components/DetailModal.jsx'
import RecommendationDialog from '../../src/components/recommendations/RecommendationDialog.jsx'
import { createRecommendationClient } from '../../src/api/recommendationClient.js'
import api from '../../src/api/api.js'
import { creditsCapability, creditsCommon, creditsSummary, creditsSearch, creditsPage, creditsDetail } from './creditsContract.js'
import { CREDIT_DEFAULTS } from '../../src/utils/recommendationCredits.js'
import '../../src/index.css'
localStorage.setItem('anime_journal_access_token', 'synthetic-13d-only')
let revision = 1, closed = false, failNext = false
const calls = [], queries = new Map()
const owned = new Map([[101, 'completed'], [102, 'watching'], [103, 'on_hold'], [104, 'plan']]), actions = new Map()
const wait = signal => new Promise((resolve, reject) => { const timer = setTimeout(resolve, 130)
  signal?.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')) }, { once: true }) })
const transport = { get: async (path, config = {}) => {
  calls.push(path); await wait(config.signal)
  if (closed && path.includes('/credits/')) throw { response: { status: 503, data: { code: 'credits_disabled' } } }
  if (failNext && path.includes('/credits/')) { failNext = false; throw { response: { status: 409, data: { code: 'credits_snapshot_changed' } } } }
  const params = config.params || {}, scope = params.content_scope || 'safe'
  let data
  if (path === 'recommendations/capabilities/') data = { credits: { ...creditsCapability, voice_actor: !closed, studio: !closed } }
  else if (path === 'recommendations/eligibility/') data = { contract_version: 'recommendations.eligibility.session.v1',
    policy_version: 'eligibility.session.v1', effective_status: 'not_requested', authorization_source: 'none',
    manual_authorization_valid: false, eligibility_version: 0, age_assurance_status: 'not_performed', can_apply: false,
    adult_access_allowed: true, serving_integration: true, effective_scope: 'safe', decision_message: '', valid_until: null, eligibility_revision: 'a'.repeat(64) }
  else if (path === 'recommendations/context/') data = { ...creditsCommon('record', scope) }
  else if (path === 'recommendations/credits/search/') {
    data = creditsSearch(params.kind, scope); if (params.q === '不存在') data.results = []
  } else if (/credits\/record\//.test(path)) data = { ...creditsCommon('record', scope), record_id: 9, identity_state: 'linked', bangumi_id: 1 }
  else if (/credits\/subjects\//.test(path)) data = creditsSummary(Number(path.split('/').at(-2)), params.expanded === 'true', scope)
  else if (/credits\/entities\/.*\/items\//.test(path)) {
    const row = actions.get(params.context)
    if (!row) throw { response: { status: 409, data: { code: 'credits_snapshot_changed' } } }
    const [, kind, person] = path.match(/entities\/(voice_actor|studio)\/(\d+)/)
    data = creditsDetail({ ...row, watch_status: owned.get(row.bangumi_id) || null }, kind, Number(person), scope)
  } else if (/credits\/entities\//.test(path)) {
    const [kind, identity] = path.match(/entities\/(voice_actor|studio)\/(\d+)/).slice(1)
    const previous = params.cursor ? queries.get(params.cursor) : null
    const options = previous ? previous.options : { ...CREDIT_DEFAULTS, ...params }
    delete options.content_scope
    if (!options) throw Error('Invalid fixture cursor')
    const offset = params.cursor ? Number(params.cursor.split(':')[1]) : 0
    const expires = previous?.expires || Math.floor(Date.now() / 1000) + 900
    const total = scope === 'safe' ? 25 : 26
    const all = [0, 12, 24].flatMap(start => creditsPage({ kind, person: Number(identity), offset: start, options, contentScope: scope, count: total, expires }).results)
      .map(row => ({ ...row, watch_status: owned.get(row.bangumi_id) || null }))
      .filter(row => !(options.hide_recorded && row.watch_status) && (options.view !== 'completed' || row.watch_status === 'completed') && options.min_score < 9)
    data = creditsPage({ kind, person: Number(identity), offset, options, contentScope: scope, count: all.length, expires })
    data.results = all.slice(offset, offset + 12)
    data.results.forEach(row => actions.set(row.action_context, row))
    if (data.next_cursor) queries.set(data.next_cursor, { options, expires })
  } else data = { results: [], count: 0 }
  data.credits_revision = revision
  return { data }
}, post: async (path, body, config = {}) => {
  calls.push(path); await wait(config.signal)
  if (closed) throw { response: { status: 503, data: { code: 'credits_disabled' } } }
  const row = actions.get(config.params?.context)
  if (!row) throw { response: { status: 409, data: { code: 'credits_snapshot_changed' } } }
  const existing = owned.has(row.bangumi_id)
  if (!existing) owned.set(row.bangumi_id, 'plan')
  return { data: { ...creditsCommon('want', config.params?.content_scope), contract_version: 'recommendations.want.v1',
    bangumi_id: row.bangumi_id, anime_id: row.bangumi_id + 1000, record_id: row.bangumi_id + 2000,
    content_rating: row.content_rating, created: !existing, outcome: existing ? 'existing' : 'created', watch_status: owned.get(row.bangumi_id) } }
} }
const client = createRecommendationClient(transport)
api.defaults.adapter = async config => ({ ...(await transport.get(config.url, config)), config, status: 200, statusText: 'OK', headers: {} })
window.addEventListener('keydown', event => {
  if (event.altKey && event.shiftKey && event.code === 'KeyU') { revision++; failNext = true }
  if (event.altKey && event.shiftKey && event.code === 'KeyO') { closed = !closed; window.dispatchEvent(new Event('focus')) }
})
const previewItem = { id: 9, title: '风与星的旅程', japaneseTitle: 'Synthetic Story', tags: ['日常', '音乐'], tagColors: {}, score: 8,
  period: '2026-1', studio: '私人填写的公司名', episodes: '12', status: 'completed', description: '本页仅有合成资料。', review: '', baikeUrl: '', cover: '', posterUrl: '' }
function Origin() {
  const [open, setOpen] = useState(false), [preview, setPreview] = useState(false), [filter, setFilter] = useState('看过')
  return <main className="memphis-rec-container" style={{ padding: 24, minHeight: 1800 }}><div className="rec-page"><h1>合成原页 / 番剧探索</h1>
    <p>返回时保留筛选、详情与滚动。仅本机合成资料。</p><select aria-label="原页筛选" value={filter} onChange={e => setFilter(e.target.value)}><option>看过</option><option>想看</option></select>
    <CreditsEntry capabilities={creditsCapability} /><button className="rec-button" onClick={() => setOpen(true)}>打开推荐详情</button>
    <button className="rec-button" onClick={() => setPreview(true)}>打开私人预览</button>
    {open && <RecommendationDialog title="风与星的旅程" onClose={() => setOpen(false)}><p>合成推荐详情</p><CreditsSummary client={client} bangumiId={1} /></RecommendationDialog>}
    {preview && <DetailModal selectedItem={previewItem} isPrivateJournal closeModal={() => setPreview(false)} />}
    <p style={{ marginTop: 900 }}><button className="rec-button" onClick={() => setOpen(true)}>从页面下方打开详情</button></p>
  </div></main>
}
const app = <StrictMode><BrowserRouter><SeriesNavigation renderCredits={overlay => <CreditsExplorer client={client} overlay={overlay} />}>
  <Route path="/dashboard" element={<Origin />} /><Route path="/recommendations" element={<Origin />} />
  <Route path="/recommendations/similar" element={<Origin />} /><Route path="/recommendations/tags" element={<Origin />} />
  <Route path="*" element={<main style={{ padding: 24 }}><h1>13E 本机隔离验收</h1><Link to="/recommendations">从推荐页开始</Link><br /><Link to="/recommendations/credits">直接打开人物与制作</Link></main>} />
</SeriesNavigation></BrowserRouter></StrictMode>
const viewport = new URLSearchParams(location.search).get('viewport')
createRoot(document.getElementById('root')).render(viewport ? <iframe title={`${viewport}px 隔离视口`} src="/tests/fixtures/creditsHarness.html"
  style={{ border: 0, width: `${Number(viewport)}px`, height: viewport === '390' ? 844 : 1000,
    transformOrigin: 'top left', transform: `scale(${Math.min(1, innerWidth / Number(viewport))})` }} /> : app)
