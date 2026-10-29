import axios from 'axios'
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

let refreshPromise = null

function isPublicAuthRequest(url = '') {
  const normalizedUrl = url.replace(/^\/api\//, '').replace(/^\//, '')
  return normalizedUrl === 'token/'
    || normalizedUrl === 'token/refresh/'
    || normalizedUrl.startsWith('auth/')
}

function redirectToLogin() {
  const isAdminArea = window.location.pathname.startsWith('/admin-')
  const loginPath = isAdminArea ? '/admin-login' : '/login'
  if (window.location.pathname !== loginPath) {
    window.location.replace(`${loginPath}?session=expired`)
  }
}

async function refreshAccessToken() {
  if (refreshPromise) return refreshPromise

  const refreshToken = getRefreshToken()
  if (!refreshToken) throw new Error('No refresh token available')

  refreshPromise = refreshClient
    .post('token/refresh/', { refresh: refreshToken })
    .then((response) => {
      const nextAccessToken = response.data.access
      const nextRefreshToken = response.data.refresh || refreshToken
      saveTokens({ access: nextAccessToken, refresh: nextRefreshToken })
      return nextAccessToken
    })
    .finally(() => {
      refreshPromise = null
    })

  return refreshPromise
}

api.interceptors.request.use(
  (config) => {
    const accessToken = getAccessToken()

    if (config.data instanceof FormData) {
      // Let Axios/the browser generate `multipart/form-data; boundary=...`.
      // A manually fixed Content-Type can omit the boundary and corrupt uploads.
      config.headers.delete?.('Content-Type')
    }

    if (accessToken) {
      config.headers.Authorization = `Bearer ${accessToken}`
    }

    return config
  },
  (error) => Promise.reject(error),
)

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config
    const shouldRefresh = error.response?.status === 401
      && originalRequest
      && !originalRequest._retry
      && !isPublicAuthRequest(originalRequest.url)

    if (!shouldRefresh) return Promise.reject(error)

    originalRequest._retry = true
    try {
      const accessToken = await refreshAccessToken()
      originalRequest.headers = originalRequest.headers || {}
      originalRequest.headers.Authorization = `Bearer ${accessToken}`
      return api(originalRequest)
    } catch (refreshError) {
      clearTokens()
      if (!originalRequest.optionalAuth) redirectToLogin()
      return Promise.reject(refreshError)
    }
  },
)

export default api
