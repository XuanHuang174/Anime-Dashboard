// Browser adapters intentionally cannot distinguish DNS, TLS and CORS failures.
// Never present an ERR_NETWORK as proof of either throttling or expired login.
export function isConnectionFailure(error) {
  return !error?.response && ['ERR_NETWORK', 'ECONNABORTED', 'ETIMEDOUT'].includes(error?.code)
}

export function isRetryableRead(config = {}) {
  if (config.connectionRetry === false || !['get', 'head'].includes((config.method || 'get').toLowerCase())) return false
  const path = (config.url || '').replace(/^\/?api\//, '').replace(/^\//, '').split('?')[0]
  // Explicitly exclude consumptive notification polling, downloads, auth and writes.
  return /^(user\/profile\/|records\/(?:stats\/|filter-options\/|showcase-publication\/)?|quick-filters\/|featured-submissions\/state\/|recommendations\/(?:capabilities|exploration\/contract)\/|showcase(?:\/|\-filters\/)|public-journal\/users\/)/.test(path)
    && !/(?:export|download|poll)\//.test(path)
}

export function createConnectionRecovery({ now = () => performance.now() } = {}) {
  let state = { phase: 'idle', version: 0, code: '', host: '' }, lastRecovery = -Infinity
  const listeners = new Set()
  const update = changes => { state = { ...state, ...changes }; listeners.forEach(fn => fn()) }
  function recover(reason = 'manual') {
    if (state.phase === 'idle' || now() - lastRecovery < (reason === 'manual' ? 2000 : 10000)) return false
    lastRecovery = now()
    update({ phase: 'retrying', version: state.version + 1 })
    return true
  }
  return {
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    getSnapshot: () => state,
    fail(error, exhausted = false) {
      if (!isConnectionFailure(error)) return
      let host = ''
      try { host = new URL(error.config?.baseURL || '/', globalThis.location?.origin || 'http://localhost').host } catch { /* No raw URL or query in diagnostics. */ }
      update({ phase: exhausted ? 'unavailable' : 'retrying', code: error.code, host })
    },
    success() {
      if (state.phase === 'idle') return
      // One successful API response proves connectivity, not success of every resource.
      const shouldRecover = state.phase === 'unavailable' && now() - lastRecovery >= 10000
      if (shouldRecover) lastRecovery = now()
      update({ phase: 'idle', code: '', host: '', version: state.version + (shouldRecover ? 1 : 0) })
    },
    recover,
    reset() { lastRecovery = -Infinity; update({ phase: 'idle', code: '', host: '', version: state.version + 1 }) },
  }
}

export const connectionRecovery = createConnectionRecovery()

function abortable(promise, signal) {
  if (!signal) return promise
  const canceled = () => Object.assign(new Error('Request canceled'), { code: 'ERR_CANCELED' })
  if (signal.aborted) return Promise.reject(canceled())
  return new Promise((resolve, reject) => {
    const stop = () => { signal.removeEventListener('abort', stop); reject(canceled()) }
    signal.addEventListener('abort', stop, { once: true })
    promise.then(value => { signal.removeEventListener('abort', stop); resolve(value) }, error => { signal.removeEventListener('abort', stop); reject(error) })
  })
}

export function installConnectionRecovery(api, {
  store = connectionRecovery, getSessionKey = () => '',
  pause = ms => new Promise(resolve => setTimeout(resolve, ms)), random = Math.random,
} = {}) {
  // Concurrent failures share two backoff timers and at most two retry requests.
  const waits = new Map(), queue = []
  let running = 0
  function run(task) {
    return new Promise((resolve, reject) => { queue.push({ task, resolve, reject }); drain() })
  }
  function drain() {
    while (running < 2 && queue.length) {
      const job = queue.shift(); running++
      Promise.resolve().then(job.task).then(job.resolve, job.reject).finally(() => { running--; drain() })
    }
  }
  function wait(attempt, signal) {
    if (!waits.has(attempt)) {
      const promise = pause([1000, 3000][attempt] + Math.floor(random() * 250)).finally(() => waits.delete(attempt))
      waits.set(attempt, promise)
    }
    return abortable(waits.get(attempt), signal)
  }
  const requestId = api.interceptors.request.use(config => {
    if (!Object.hasOwn(config, '_connectionSession')) config._connectionSession = getSessionKey()
    return config
  })
  const responseId = api.interceptors.response.use(response => {
    if (response.config?._connectionSession !== getSessionKey()) throw Object.assign(new Error('登录账号已变化，请重新读取。'), { code: 'SESSION_CHANGED' })
    store.success(); return response
  }, async error => {
    if (!isConnectionFailure(error)) { if (error?.response) store.success(); throw error }
    const config = error.config
    if (config?._connectionReplay || config?.signal?.aborted || config?._connectionSession !== getSessionKey()) throw error
    const retry = config && !error.sessionRefreshFailure && isRetryableRead(config)
    store.fail(error, !retry)
    if (!retry) throw error
    let failure = error
    for (let attempt = 0; attempt < 2; attempt++) {
      await wait(attempt, config.signal)
      try {
        return await run(() => {
          if (config.signal?.aborted) throw Object.assign(new Error('Request canceled'), { code: 'ERR_CANCELED' })
          if (config._connectionSession !== getSessionKey()) throw Object.assign(new Error('登录账号已变化，请重新读取。'), { code: 'SESSION_CHANGED' })
          return api({ ...config, _connectionReplay: true })
        })
      } catch (next) {
        if (!isConnectionFailure(next)) throw next
        failure = next
      }
    }
    store.fail(failure, true)
    throw failure
  })
  return () => { api.interceptors.request.eject(requestId); api.interceptors.response.eject(responseId) }
}
