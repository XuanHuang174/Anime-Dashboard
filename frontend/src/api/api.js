import axios from 'axios'
import { installSessionRefresh } from './sessionRefresh.js'
import { connectionRecovery, installConnectionRecovery } from './connectionRecovery.js'
import { recommendationSessionScope } from '../utils/recommendationSession.js'
import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  saveTokens,
} from '../auth/tokenStorage.js'

const BACKEND_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '')
  .trim()
  .replace(/\/+$/, '')
const API_BASE_URL = BACKEND_BASE_URL
  ? `${BACKEND_BASE_URL}/api/`
  : '/api/'
const configuredTimeout = Number(import.meta.env.VITE_API_TIMEOUT_MS)
const API_TIMEOUT_MS = Number.isFinite(configuredTimeout) && configuredTimeout > 0
  ? configuredTimeout
  : import.meta.env.DEV
    ? 60000
    : 30000

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT_MS,
})

// A separate client prevents a failed refresh request from recursively entering
// the response interceptor below.
const refreshClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT_MS,
})

function redirectToLogin() {
  const isAdminArea = window.location.pathname.startsWith('/admin-')
  const loginPath = isAdminArea ? '/admin-login' : '/login'
  if (window.location.pathname !== loginPath) {
    window.location.replace(`${loginPath}?session=expired`)
  }
}

installSessionRefresh(api, refreshClient, {
  getAccessToken, getRefreshToken, saveTokens, clearTokens, onExpired: redirectToLogin,
})

installConnectionRecovery(api, { getSessionKey: () => recommendationSessionScope(getAccessToken()) })
// Logout/account changes cancel pending retries without storing any private data.
if (typeof window !== 'undefined') window.addEventListener('storage', event => {
  if (event.key === null || event.key === 'anime_journal_access_token') connectionRecovery.reset()
})

export default api
