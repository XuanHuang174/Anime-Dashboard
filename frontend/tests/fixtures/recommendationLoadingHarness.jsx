// Actual eager route, ProtectedRoute and lazy production page. No static page import.
import React, { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Routes, Route, useNavigate } from 'react-router-dom'
import RecommendationRoute from '../../src/router/RecommendationRoute.jsx'
import ProtectedRoute from '../../src/components/ProtectedRoute.jsx'
import { createRecommendationClient } from '../../src/api/recommendationClient.js'

window.loadingQA = { calls: [] }
const guest = new URLSearchParams(location.search).has('guest')
if (guest) localStorage.removeItem('anime_journal_access_token')
else localStorage.setItem('anime_journal_access_token', 'synthetic-loading-owner')
async function read(path, { params, signal } = {}) {
  window.loadingQA.calls.push(path)
  const response = await fetch(`/__loadingqa__/${path}${params ? '?' + new URLSearchParams(params) : ''}`, { signal })
  const data = await response.json()
  if (!response.ok) throw { response: { status: response.status, data } }
  return { data }
}
const client = createRecommendationClient({ get: read, post() { throw new Error('unexpected write') }, patch() { throw new Error('unexpected write') } })
function App() {
  const navigate = useNavigate()
  useEffect(() => { window.loadingQA.navigate = navigate }, [navigate])
  return <Routes><Route path="/recommendations" element={<ProtectedRoute><RecommendationRoute client={client} /></ProtectedRoute>} />
    <Route path="/dashboard" element={<h1>隔离手账</h1>} /><Route path="/login" element={<h1>请先登录</h1>} /></Routes>
}
createRoot(document.getElementById('root')).render(<StrictMode><MemoryRouter initialEntries={['/recommendations']}><App /></MemoryRouter></StrictMode>)
