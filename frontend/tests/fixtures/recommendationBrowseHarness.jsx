// Full production page AND transport; all HTTP is intercepted by the test runner.
import React, { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import Recommendations from '../../src/pages/Recommendations.jsx'
import { createRecommendationClient } from '../../src/api/recommendationClient.js'

window.browseQA = { calls: [] }
async function send(path, { params, signal, method = 'GET', body } = {}) {
  window.browseQA.calls.push({ path, params, method })
  const response = await fetch(`/__9bqa__/${path}${params ? '?' + new URLSearchParams(params) : ''}`, { signal, method,
    ...(body ? { body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } } : {}) })
  const data = await response.json()
  if (!response.ok) throw { response: { status: response.status, data } }
  return { data }
}
const client = createRecommendationClient({ get: (path, config) => send(path, config),
  post: (path, body, config) => send(path, { ...config, body, method: 'POST' }),
  patch: (path, body, config) => send(path, { ...config, body, method: 'PATCH' }) })
localStorage.setItem('anime_journal_access_token', 'synthetic-9b-owner')
function App() {
  const navigate = useNavigate()
  useEffect(() => { window.browseQA.navigate = navigate }, [navigate])
  return <Routes><Route path="/recommendations" element={<Recommendations client={client} />} />
    <Route path="/dashboard" element={<h1>9B 隔离手账</h1>} /></Routes>
}
createRoot(document.getElementById('root')).render(<StrictMode><MemoryRouter initialEntries={['/recommendations']}><App /></MemoryRouter></StrictMode>)
