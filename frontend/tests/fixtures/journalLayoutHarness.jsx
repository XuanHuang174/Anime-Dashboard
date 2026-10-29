// Synthetic, read-only UI harness. The adapter never sends an API request.
import React, { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes, Link, useLocation } from 'react-router-dom'
import LoginPage from '../../src/pages/LoginPage.jsx'
import FeaturedColumn from '../../src/pages/FeaturedColumn.jsx'
import PublicUniverse from '../../src/pages/PublicUniverse.jsx'
import JournalHeaderActions from '../../src/components/JournalHeaderActions.jsx'
import DetailModal from '../../src/components/DetailModal.jsx'
import api from '../../src/api/api.js'
import { saveTokens, clearTokens } from '../../src/auth/tokenStorage.js'
import { creditsCapability, creditsCommon, creditsSummary } from './creditsContract.js'
import '../../src/index.css'

const params = new URLSearchParams(location.search)
if (params.get('auth') === 'yes') saveTokens({ access: 'synthetic-layout-only' })
else clearTokens()
api.defaults.adapter = async config => {
  let data = { results: [], count: 0, next: null }
  if (config.url === 'user/profile/') data = { username: 'Synthetic', id: 1 }
  if (config.url === 'recommendations/capabilities/') data = { credits: creditsCapability }
  if (config.url?.includes('credits/record/')) data = { ...creditsCommon('record'), record_id: 9, identity_state: 'linked', bangumi_id: 1 }
  if (config.url?.includes('credits/subjects/')) data = creditsSummary()
  if (config.url?.includes('series/')) throw { response: { status: 503, data: { code: 'series_disabled' } } }
  if (config.method === 'post') throw { response: { status: 400, data: { detail: '这是合成错误信息，用于验证较长的表单提示仍可完整阅读。'.repeat(5) } } }
  return { data, config, status: 200, statusText: 'OK', headers: {} }
}
const item = { id: 9, title: '合成旅程', japaneseTitle: 'Synthetic', tags: ['日常', '音乐'], score: 8,
  period: '2026-1', studio: '星河动画', episodes: '12', status: 'completed', cover: '', posterUrl: '', baikeUrl: '',
  description: '这是一段用于检查剧情简介阅读空间的合成文本。\n'.repeat(35), review: '用于检查个人评价的合成文本。\n'.repeat(25) }
function Journal() {
  const [open, setOpen] = useState(false)
  return <main className="memphis-public min-h-screen bg-[#fff5ee] p-6"><h1>手账布局隔离验收</h1>
    <JournalHeaderActions isPreviewMode onReturnHome={() => {}} onTogglePreview={() => {}} csvDisabled csvCount={0} />
    <button className="border-4 border-black bg-[#ffe66d] p-4 mt-6" onClick={() => setOpen(true)}>打开私人预览详情</button>
    {open && <DetailModal isPrivateJournal selectedItem={item} closeModal={() => setOpen(false)} />}
    <p className="mt-6"><Link to="/login">查看登录布局</Link></p>
  </main>
}
function LayoutApp() {
  const location = useLocation()
  const query = new URLSearchParams(location.search)
  const screen = query.get('screen')
  // Keep the fixture entry useful after a refresh without changing app routes.
  return <Routes><Route path="/dashboard" element={<Journal />} /><Route path="/login" element={<LoginPage />} />
    <Route path="/featured" element={<FeaturedColumn />} /><Route path="/universe" element={<PublicUniverse />} />
    <Route path="*" element={screen === 'featured' ? <FeaturedColumn /> : screen === 'universe' ? <PublicUniverse />
      : query.get('login') === '1' ? <LoginPage /> : <Journal />} /></Routes>
}
const width = Number(params.get('viewport')), height = Number(params.get('height')) || 900
const innerParams = new URLSearchParams(params); innerParams.delete('viewport'); innerParams.delete('height')
createRoot(document.getElementById('root')).render(width ? <iframe title={`${width}px 手账布局验收视口`}
  src={`/tests/fixtures/journalLayoutHarness.html?${innerParams}`}
  style={{ border: 0, width, height, transformOrigin: 'top left', transform: `scale(${Math.min(1, innerWidth / width)})` }} />
  : <StrictMode><BrowserRouter><LayoutApp /></BrowserRouter></StrictMode>)
