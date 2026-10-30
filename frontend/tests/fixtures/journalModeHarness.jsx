// Actual dashboard, synthetic accounts only; every API request is intercepted.
import React, { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, Route, Routes, useNavigate } from 'react-router-dom'
import Dashboard from '../../src/pages/Dashboard.jsx'
import ProtectedRoute from '../../src/components/ProtectedRoute.jsx'
import api from '../../src/api/api.js'
import { getAccessToken, saveTokens, clearTokens } from '../../src/auth/tokenStorage.js'
import { recommendationSessionScope } from '../../src/utils/recommendationSession.js'
import '../../src/index.css'

const identity = () => recommendationSessionScope(getAccessToken()) === 'subject:41002' ? 41002 : 41001
api.defaults.adapter = async config => {
  let data = { results: [], count: 0, next: null }
  if (config.url === 'user/profile/') data = { id: identity(), username: `合成账号 ${identity()}`, journal_title: '模式保存验收', journal_subtitle: '仅使用本机合成资料。' }
  else if (config.url?.endsWith('/stats/')) data = {}
  else if (config.url?.endsWith('/filter-options/')) data = { tags: [], years: [], quick_filters: [] }
  else if (config.url === 'public-journal/users/') data = { results: [{ username: 'Synthetic', journal_title: '合成公开手账' }] }
  if (config.method && config.method !== 'get') throw Error('This fixture rejects writes')
  return { data, config, status: 200, statusText: 'OK', headers: {} }
}
function Controls() {
  const navigate = useNavigate()
  const login = id => {
    saveTokens({ access: `synthetic.${btoa(JSON.stringify({ user_id: id }))}.fixture` })
    window.dispatchEvent(new Event('focus'))
    navigate('/dashboard')
  }
  return <nav style={{ padding: 12, display: 'flex', flexWrap: 'wrap', gap: 16, background: '#ffe66d' }} aria-label="合成账号验收">
    <button onClick={() => login(41001)}>登录合成账号 A</button><button onClick={() => login(41002)}>登录合成账号 B</button>
    <button onClick={() => { clearTokens(); window.dispatchEvent(new Event('focus')); navigate('/login') }}>退出合成账号</button>
    <Link to="/dashboard">返回私人手账</Link><Link to="/recommendations">前往合成推荐页</Link><Link to="/journal/Synthetic">查看公开手账</Link>
  </nav>
}
createRoot(document.getElementById('root')).render(<StrictMode><BrowserRouter><Controls /><Routes>
  <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
  <Route path="/journal/:username" element={<Dashboard />} />
  <Route path="*" element={<main style={{ padding: 24 }}><h1>手账模式隔离验收</h1><p>从上方登录合成账号；可验证切页、刷新、退出登录与账号隔离。</p></main>} />
</Routes></BrowserRouter></StrictMode>)
