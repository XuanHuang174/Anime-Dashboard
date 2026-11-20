import test from 'node:test'
import assert from 'node:assert/strict'
import axios, { AxiosError } from 'axios'
import { createConnectionRecovery, installConnectionRecovery, isConnectionFailure, isRetryableRead } from '../src/api/connectionRecovery.js'
import { installSessionRefresh } from '../src/api/sessionRefresh.js'

const network = config => new AxiosError('synthetic DNS/transport failure', 'ERR_NETWORK', config)
const response = config => ({ config, status: 200, data: {}, headers: {} })
const http = (config, status) => new AxiosError('synthetic HTTP failure', 'ERR_BAD_RESPONSE', config, null, { config, status, data: {}, headers: { 'retry-after': '120' } })
function setup(adapter, extra = {}) {
  let calls = 0
  const store = createConnectionRecovery()
  const api = axios.create({ adapter: config => { calls++; return adapter(config, calls) } })
  installConnectionRecovery(api, { store, pause: async () => {}, random: () => 0, ...extra })
  return { api, store, calls: () => calls }
}

test('transport failure retries a safe read twice then exposes a single unavailable state', async () => {
  const { api, store, calls } = setup(async config => { throw network(config) })
  await assert.rejects(api.get('records/'), isConnectionFailure)
  assert.equal(calls(), 3)
  assert.equal(store.getSnapshot().phase, 'unavailable')
  assert.equal(store.getSnapshot().code, 'ERR_NETWORK')
})

test('a retry can recover without reloading or discarding a valid session', async () => {
  const { api, store, calls } = setup(async (config, n) => { if (n < 3) throw network(config); return response(config) })
  assert.equal((await api.get('user/profile/')).status, 200)
  assert.equal(calls(), 3)
  assert.equal(store.getSnapshot().phase, 'idle')
})

test('writes, refresh, downloads and consumptive polling are never automatically replayed', async () => {
  for (const [method, url] of [['post', 'records/'], ['patch', 'user/profile/'], ['delete', 'records/1/'], ['post', 'token/refresh/'], ['get', 'notifications/poll/'], ['get', 'records/export/']]) {
    const { api, calls } = setup(async config => { throw network(config) })
    await assert.rejects(api.request({ method, url }))
    assert.equal(calls(), 1, method + url)
    assert.equal(isRetryableRead({ method, url }), false)
  }
})

test('HTTP errors and Retry-After are not turned into network retries', async () => {
  for (const status of [400, 401, 403, 404, 429, 500, 503]) {
    const { api, calls, store } = setup(async config => { throw http(config, status) })
    await assert.rejects(api.get('records/'), error => error.response.status === status)
    assert.equal(calls(), 1)
    assert.equal(store.getSnapshot().phase, 'idle')
  }
  const { api, calls } = setup(async (config, n) => { throw n === 1 ? network(config) : http(config, 429) })
  await assert.rejects(api.get('records/'), error => error.response.status === 429)
  assert.equal(calls(), 2)
})

test('cancellation and account switching during backoff cannot replay an old read', async () => {
  for (const changed of [false, true]) {
    let release, owner = 'account-one'
    const signal = new AbortController()
    const { api, calls } = setup(async config => { throw network(config) }, { getSessionKey: () => owner, pause: () => new Promise(resolve => { release = resolve }) })
    const pending = api.get('records/', { signal: signal.signal })
    await new Promise(setImmediate)
    if (changed) owner = 'account-two'
    else signal.abort()
    release()
    await assert.rejects(pending, error => error.code === (changed ? 'SESSION_CHANGED' : 'ERR_CANCELED'))
    assert.equal(calls(), 1)
  }
})

test('late success from another account is not delivered to the old screen', async () => {
  let release, owner = 'one'
  const { api } = setup(config => new Promise(resolve => { release = () => resolve(response(config)) }), { getSessionKey: () => owner })
  const pending = api.get('records/')
  await new Promise(setImmediate)
  owner = 'two'; release()
  await assert.rejects(pending, error => error.code === 'SESSION_CHANGED')
})

test('concurrent retry requests share backoff and never exceed two active retries', async () => {
  let active = 0, peak = 0, waits = 0
  const { api } = setup(async config => {
    if (!config._connectionReplay) throw network(config)
    active++; peak = Math.max(peak, active)
    await new Promise(resolve => setTimeout(resolve, 5))
    active--; return response(config)
  }, { pause: async () => { waits++; await new Promise(setImmediate) } })
  await Promise.all(['records/', 'user/profile/', 'quick-filters/', 'records/stats/'].map(url => api.get(url)))
  assert.equal(waits, 1)
  assert.equal(peak, 2)
})

test('recovery events coalesce and ordinary successful focus does not cause refreshes', () => {
  let time = 0
  const store = createConnectionRecovery({ now: () => time })
  assert.equal(store.recover('network'), false)
  store.fail(network({ baseURL: 'https://api.example.com/api/?private=never-shown' }), true)
  assert.equal(store.getSnapshot().host, 'api.example.com')
  assert.equal(store.recover('network'), true)
  assert.equal(store.recover('network'), false)
  assert.equal(store.getSnapshot().version, 1)
  time = 11000; store.success()
  assert.equal(store.getSnapshot().phase, 'idle')
  assert.equal(store.recover('network'), false)
  assert.equal(isConnectionFailure({ code: 'ERR_CANCELED' }), false)
})

test('an expired access token and offline refresh show connection failure without losing or replaying refresh', async () => {
  let refreshCalls = 0, cleared = 0
  const api = axios.create({ adapter: async config => { throw http(config, 401) } })
  const refreshClient = axios.create({ adapter: async config => { refreshCalls++; throw network(config) } })
  const store = createConnectionRecovery()
  installSessionRefresh(api, refreshClient, { getAccessToken: () => 'old', getRefreshToken: () => 'refresh', saveTokens: () => {}, clearTokens: () => cleared++, onExpired: () => cleared++ })
  installConnectionRecovery(api, { store, getSessionKey: () => 'same-account', pause: async () => {} })
  await assert.rejects(api.get('records/'), error => error.sessionRefreshFailure && error.config.url === 'records/')
  assert.equal(store.getSnapshot().phase, 'unavailable')
  assert.equal(refreshCalls, 1)
  assert.equal(cleared, 0)
  await assert.rejects(api.get('user/profile/'))
  assert.equal(refreshCalls, 1, 'existing refresh cooldown is respected')
})

test('normal token rotation and a transport retry preserve the current account', async () => {
  let token = 'old', refreshCalls = 0, networkFailures = 0
  const api = axios.create({ adapter: async config => {
    if (config.headers.Authorization === 'Bearer old') throw http(config, 401)
    if (networkFailures++ === 0) throw network(config)
    return response(config)
  } })
  const refreshClient = axios.create({ adapter: async config => { refreshCalls++; return { ...response(config), data: { access: 'new' } } } })
  installSessionRefresh(api, refreshClient, { getAccessToken: () => token, getRefreshToken: () => 'refresh', saveTokens: data => { token = data.access }, clearTokens: () => assert.fail('valid session cleared'), onExpired: () => assert.fail('valid session expired') })
  installConnectionRecovery(api, { store: createConnectionRecovery(), getSessionKey: () => 'same-account', pause: async () => {} })
  assert.equal((await api.get('records/')).status, 200)
  assert.equal(refreshCalls, 1)
  assert.equal(token, 'new')
})
