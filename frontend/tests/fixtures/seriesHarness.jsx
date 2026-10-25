// Local synthetic component verification. No real API, user, covers or production writes.
import React, { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, Route } from 'react-router-dom'
import SeriesNavigation from '../../src/router/SeriesNavigation.jsx'
import SeriesRelations from '../../src/pages/SeriesRelations.jsx'
import SeriesSummary from '../../src/components/recommendations/SeriesSummary.jsx'
import RecommendationDialog from '../../src/components/recommendations/RecommendationDialog.jsx'
import DetailModal from '../../src/components/DetailModal.jsx'
import useSeriesJournalReturn from '../../src/hooks/useSeriesJournalReturn.js'
import api from '../../src/api/api.js'
import { seriesPage, seriesDetail, scope } from './seriesContract.js'
import '../../src/index.css'

localStorage.setItem('anime_journal_access_token', 'synthetic-12c-only')
let revision = 1, unavailable = false
const owned = new Set(), calls = []
const wait = async signal => { await new Promise(resolve => setTimeout(resolve, 80)); if (signal?.aborted) throw new DOMException('Aborted', 'AbortError') }
const currentPage = () => {
  const page = seriesPage()
  page.series_revision = revision
  page.context = `synthetic-series-${revision}`
  page.results = page.results.map(row => owned.has(row.bangumi_id) ? { ...row, watch_status: 'plan', record_state: 'linked', can_add_from_series: false } : row)
  return page
}
function check(context) {
  if (unavailable) throw { response: { status: 503 } }
  if (context && context !== `synthetic-series-${revision}`) throw { response: { status: 409, data: { code: 'series_snapshot_changed' } } }
}
const client = { contentScope: 'safe', forScope: () => client,
  series: async (id, context, signal) => { calls.push(`read:${id}`); await wait(signal); check(context); return currentPage() },
  seriesRecord: async (id, signal) => { await wait(signal); return { contract_version: 'recommendations.series.record.v1', record_id: id, identity_state: 'linked', bangumi_id: 1, content_scope_context: scope } },
  seriesDetail: async (source, target, context, signal) => { await wait(signal); check(context); return seriesDetail(currentPage(), target) },
  seriesWant: async (source, target, context, signal) => { calls.push(`want:${target}`); await wait(signal); check(context); const created = !owned.has(target); owned.add(target)
    return { contract_version: 'recommendations.want.v1', bangumi_id: target, anime_id: 100 + target, record_id: 200 + target,
      watch_status: 'plan', created, outcome: created ? 'created' : 'existing', content_rating: 'non_adult', content_scope_context: scope } },
}
// Only the preview summary uses the real default client. Intercept locally with the same synthetic contracts.
api.defaults.adapter = async config => {
  let data
  if (config.url.startsWith('recommendations/series/record/')) data = await client.seriesRecord(9, config.signal)
  else if (config.url.startsWith('recommendations/series/')) data = await client.series(1, config.params?.context, config.signal)
  else data = { results: [], count: 0 }
  return { config, status: 200, statusText: 'OK', headers: {}, data }
}
window.addEventListener('keydown', event => {
  if (event.altKey && event.shiftKey && event.code === 'KeyU') { revision++; window.dispatchEvent(new Event('focus')) }
  if (event.altKey && event.shiftKey && event.code === 'KeyO') { unavailable = !unavailable; window.dispatchEvent(new Event('focus')) }
})
function Journal() {
  const [detail, openDetail] = useState(false), [preview, setPreview] = useState(false), [filter, setFilter] = useState('已看过'), [refreshed, refresh] = useState(0)
  useSeriesJournalReturn(() => refresh(value => value + 1))
  return <main className="memphis-rec-container" style={{ padding: 24, minHeight: 1400 }}><h1>隔离私人手账 / 预览</h1>
    <p>仅合成资料，当前筛选和预览应在返回后保留。刷新次数：{refreshed}</p>
    <select aria-label="原页筛选" value={filter} onChange={event => setFilter(event.target.value)}><option>已看过</option><option>想看</option></select>
    <button className="rec-button" onClick={() => openDetail(true)}>打开合成作品详情</button><Link className="rec-button" to="/recommendations">模拟推荐详情</Link>
    <button className="rec-button" onClick={() => setPreview(true)}>打开实际私人预览</button>
    {preview && <PrivatePreview close={() => setPreview(false)} />}
    {detail && <RecommendationDialog title="合成故事：起点" onClose={() => openDetail(false)}><p>原页详情位置应保留。</p><SeriesSummary client={client} bangumiId={1} /></RecommendationDialog>}
    <p>快捷键：Alt+Shift+U 使旧系列版本失效，Alt+Shift+O 切换服务失败。</p>
    <p>加入想看请求：{calls.filter(call => call.startsWith('want:')).length} 次（仅本页内存）。</p>
    <div style={{ marginTop: 700 }}><SeriesSummary client={client} recordId={9} /></div>
  </main>
}
function Preview() {
  const [open, setOpen] = useState(true)
  return <main><Link to="/dashboard">回隔离手账</Link>{open && <PrivatePreview close={() => setOpen(false)} />}</main>
}
const previewItem = { id: 9, title: '合成故事：起点', japaneseTitle: 'Synthetic Story', tags: ['日常', '音乐'], tagColors: {}, score: 8,
  period: '2026-1', studio: '合成制作', episodes: '12', status: 'completed', description: '这是一段合成简介。', review: '合成个人评价', baikeUrl: '', cover: '', posterUrl: '' }
function PrivatePreview({ close }) { return <DetailModal isPrivateJournal selectedItem={previewItem} closeModal={close} /> }
const app = <StrictMode><BrowserRouter><SeriesNavigation renderSeries={overlay => <SeriesRelations client={client} overlay={overlay} />}>
  <Route path="/dashboard" element={<Journal />} /><Route path="/recommendations" element={<Journal />} />
  <Route path="/preview-fixture" element={<Preview />} />
  <Route path="*" element={<main style={{ padding: 30 }}><h1>12C 合成资料验收</h1><Link to="/dashboard">开始系列页验收</Link><br /><Link to="/recommendations/series/1">直接打开系列页</Link><br /><Link to="/preview-fixture">检查私人预览详情</Link></main>} />
</SeriesNavigation></BrowserRouter></StrictMode>
const viewport = new URLSearchParams(location.search).get('viewport')
createRoot(document.getElementById('root')).render(viewport ? <iframe title={`${viewport}px 隔离验收视口`} src="/tests/fixtures/seriesHarness.html"
  style={{ border: 0, width: `${Number(viewport)}px`, height: viewport === '390' ? 844 : 1000, transformOrigin: 'top left', transform: `scale(${Math.min(1, innerWidth / Number(viewport))})` }} /> : app)
