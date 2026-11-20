import { retryAfterMs } from './retryAfter.js'

export function installSessionRefresh(api, refreshClient, {
  getAccessToken, getRefreshToken, saveTokens, clearTokens, onExpired,
  now = () => performance.now(),
}) {
  let flight = null
  let cooldown = null
  let lastSuccess = null
  const publicAuth = (url = '') => /^(token\/(refresh\/)?|auth\/)/.test(url.replace(/^\/?api\//, '').replace(/^\//, ''))
  const changed = () => Object.assign(new Error('登录账号已变化，请重新读取页面。'), { code: 'SESSION_CHANGED' })

  async function refresh() {
    const token = getRefreshToken()
    if (!token) throw Object.assign(new Error('登录状态已失效，请重新登录。'), { code: 'SESSION_MISSING' })
    if (flight?.token === token) return flight.promise
    if (cooldown?.token === token && now() < cooldown.until) throw cooldown.error
    const state = { token, access: getAccessToken() }
    state.promise = refreshClient.post('token/refresh/', { refresh: token })
      .then(response => {
        if (getRefreshToken() !== token) throw changed()
        if (typeof response.data?.access !== 'string' || !response.data.access) {
          throw new Error('登录状态读取异常，请稍后重试。')
        }
        saveTokens({ access: response.data.access, refresh: response.data.refresh || token })
        lastSuccess = { previous: state.access, access: response.data.access, refresh: getRefreshToken() }
        cooldown = null
        return response.data.access
      })
      .catch(error => {
        if (getRefreshToken() !== token) throw changed()
        if (getRefreshToken() === token && error.response?.status !== 401) {
          const wait = error.response?.status === 429 ? retryAfterMs(error, 60000) : 10000
          cooldown = { token, until: now() + Math.max(1000, wait), error }
        }
        throw error
      })
      .finally(() => { if (flight === state) flight = null })
    flight = state
    return state.promise
  }

  api.interceptors.request.use(config => {
    const token = getAccessToken()
    config._sessionAccessToken = token
    if (typeof FormData !== 'undefined' && config.data instanceof FormData) config.headers.delete?.('Content-Type')
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })

  api.interceptors.response.use(response => response, async error => {
    const request = error.config
    if (error.response?.status !== 401 || !request || request._retry || publicAuth(request.url)) throw error
    request._retry = true
    if (request._sessionAccessToken !== getAccessToken()) {
      if (lastSuccess?.previous === request._sessionAccessToken && lastSuccess.access === getAccessToken()
        && lastSuccess.refresh === getRefreshToken()) return api(request)
      throw changed()
    }
    const previousRefresh = getRefreshToken()
    let token
    try {
      token = await refresh()
    } catch (refreshError) {
      // 429, network errors, 5xx and malformed responses never destroy a session.
      // A late response from a previous account cannot sign out the new account.
      if (getRefreshToken() === previousRefresh && (
        refreshError.response?.status === 401 || refreshError.code === 'SESSION_MISSING'
      )) {
        clearTokens()
        if (!request.optionalAuth) onExpired()
      }
      throw refreshError
    }
    // Keep replay outside the refresh catch: a business/API error after a
    // successful refresh is not evidence that the refresh token is invalid.
    request.headers = request.headers || {}
    request.headers.Authorization = `Bearer ${token}`
    return api(request)
  })
}
