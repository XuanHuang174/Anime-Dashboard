// Synthetic service, not imported into app routes or production builds.
import React, { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import Recommendations from '../../src/pages/Recommendations.jsx'

window.feedbackQA = { failList: false, calls: [] }
async function read(path, method = 'GET', body, signal) {
  window.feedbackQA.calls.push({ path, method, body })
  if (path.startsWith('list?') && window.feedbackQA.failList) throw { response: { status: 503 } }
  const response = await fetch(`/__8bqa__/${path}`, { method, signal,
    ...(body ? { body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } } : {}) })
  const data = await response.json()
  if (!response.ok) throw { response: { status: response.status, data } }
  return data
}
const preferences = { contract_version: 'recommendations.3a.v1', version: 0, tags: ['音乐'], available_tags: ['音乐'] }
const profile = { contract_version: 'recommendations.3a.v1', eligible_sample_count: 0, usable_sample_count: 0,
  cold_start: true, sample_policy: { minimum_score: '8.5' }, tag_summary: [] }
const client = {
  profile: async () => profile, preferences: async () => preferences,
  list: (options, signal) => read(`list?page=${options.page || 1}`, 'GET', null, signal),
  detail: (id, signal) => read(`detail?id=${id}`, 'GET', null, signal),
  want: (id, signal) => read('want', 'POST', { id }, signal),
  hiddenFeedback: (params, signal) => read(`hidden?${new URLSearchParams(params)}`, 'GET', null, signal),
  hideFeedback: (body, signal) => read('hide', 'POST', body, signal),
  undoFeedback: (body, signal) => read('undo', 'POST', body, signal),
}
// Only synthetic auth; actual resource responses are intercepted by the runner.
localStorage.setItem('anime_journal_access_token', 'synthetic-feedback-8b')
createRoot(document.getElementById('root')).render(<StrictMode><MemoryRouter><Recommendations client={client} /></MemoryRouter></StrictMode>)
