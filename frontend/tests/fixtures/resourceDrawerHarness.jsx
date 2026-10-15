// Local browser fixture only, never imported by the production router.
import React, { StrictMode, useEffect, useRef } from 'react'
import { createRoot } from 'react-dom/client'
import RecommendationDialog from '../../src/components/recommendations/RecommendationDialog.jsx'
import RecommendationDetailActions from '../../src/components/recommendations/RecommendationDetailActions.jsx'
import RecommendationResourcePanel from '../../src/components/recommendations/RecommendationResourcePanel.jsx'
import useRecommendationDetail from '../../src/hooks/useRecommendationDetail.js'
import { mapRecommendationItem } from '../../src/utils/recommendations.js'
import '../../src/styles/recommendations.css'

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const raw = id => ({ bangumi_id: id, title_zh: id === 101 ? '作品A 第二季' : '作品B', title_jp: 'Original',
  season: '2026-04', status_type: 'TV', score: 7.5, tags: ['音乐'], content_rating: 'non_adult', watch_status: null,
  evidence: null, reason: '', evidence_token: 'a'.repeat(64) })
const response = keyword => ({ data: { status: 'success', results: keyword === 'empty' ? [] : [
  { title: `资源-${keyword}`, magnet: 'magnet:?xt=urn:btih:ABC123', size: '1 GB', publisher: '本地测试' },
] } })
const searches = [], pending = new Map(), copies = []
const resources = { get: (path, options) => {
  const keyword = options.params.keyword
  searches.push({ keyword, signal: options.signal, path })
  if (keyword === 'error') return Promise.reject({ response: { data: { message: '本地模拟资源失败' } } })
  if (keyword === 'slow') { const value = deferred(); pending.set(keyword, value); return value.promise }
  return Promise.resolve(response(keyword))
} }
Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: () => {
  const value = deferred(); copies.push(value); return value.promise
} } })
localStorage.setItem('anime_journal_access_token', 'local-mock-token')
const client = { detail: async id => raw(id) }
function Harness() {
  const { detail, controller } = useRecommendationDetail(client), trigger = useRef(null)
  useEffect(() => {
    window.resourceQA = { open: id => controller.open(mapRecommendationItem(raw(id))),
      close: controller.close, searches, copies, resolveSlow: () => pending.get('slow')?.resolve(response('OLD')) }
  })
  return <main className="memphis-rec-container"><button onClick={() => controller.open(mapRecommendationItem(raw(101)))}>打开作品A</button>
    {detail.status !== 'closed' && <RecommendationDialog title={detail.selection?.title || '详情'} onClose={controller.close}
      resourceOpen={detail.status === 'ready' && detail.resourceOpen} onResourceClose={controller.closeResources} resourceTriggerRef={trigger}
      resourcePanel={detail.status === 'ready' ? <RecommendationResourcePanel item={detail.item} open={detail.resourceOpen}
        sessionKey={`${detail.selection.session}:${detail.resourceSession || 0}`} onClose={controller.closeResources} client={resources} /> : null}>
      {detail.status === 'ready' ? <div><h3 data-testid="detail-title">{detail.item.title}</h3><p>{'完整的详情上下文。'.repeat(100)}</p>
        <RecommendationDetailActions item={detail.item} resourceOpen={detail.resourceOpen} resourceTriggerRef={trigger}
          onResourceOpen={controller.openResources} onResourceClose={controller.closeResources} onClose={controller.close} onWant={() => {}} />
      </div> : <p>正在读取详情</p>}
    </RecommendationDialog>}
  </main>
}
createRoot(document.getElementById('root')).render(<StrictMode><Harness /></StrictMode>)
