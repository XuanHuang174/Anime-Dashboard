import axios from 'axios'
import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  saveTokens,
} from '../auth/tokenStorage.js'

const API_BASE_URL = '/api/'

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
})

// A separate client prevents a failed refresh request from recursively entering
// the response interceptor below.
const refreshClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
})

let refreshPromise = null

function isPublicAuthRequest(url = '') {
  const normalizedUrl = url.replace(/^\/api\//, '').replace(/^\//, '')
  return normalizedUrl === 'token/'
    || normalizedUrl === 'token/refresh/'
    || normalizedUrl.startsWith('auth/')
}

function redirectToLogin() {
  if (window.location.pathname !== '/login') {
    window.location.replace('/login?session=expired')
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
      redirectToLogin()
      return Promise.reject(refreshError)
    }
  },
)

export default api
