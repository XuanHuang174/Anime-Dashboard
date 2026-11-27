// Layout-only fixture for all production batch states; no network or account writes.
import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { RecommendationBatchActions } from '../../src/components/recommendations/RecommendationBrowseControls.jsx'
import '../../src/index.css'
import '../../src/styles/recommendations.css'
import '../../src/styles/recommendations-layout.css'
import '../../src/styles/recommendations-theme.css'
function App() {
  const [mode, setMode] = useState('ready')
  const state = { items: Array(12), data: { batch: 1, batch_count: 24, next_page_cursor: 'page', next_batch_cursor: 'batch', exhausted: mode === 'exhausted' },
    moreLoading: mode === 'loading', moreError: mode === 'error' ? '合成加载失败，请重试。' : '', batchError: mode === 'error' ? '合成换批失败。' : '', snapshotExpired: mode === 'expired' }
  return <main className="memphis-rec-container"><div className="rec-page"><label>验收状态<select aria-label="验收状态" value={mode} onChange={e => setMode(e.target.value)}>{['ready', 'loading', 'error', 'exhausted', 'expired'].map(value => <option key={value}>{value}</option>)}</select></label>
    <RecommendationBatchActions state={state} onMore={() => {}} onBatch={() => {}} onReload={() => {}} onFilter={() => {}} onRestart={() => {}} />
  </div></main>
}
createRoot(document.getElementById('root')).render(<App />)
