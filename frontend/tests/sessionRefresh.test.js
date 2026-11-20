import test from 'node:test'
import assert from 'node:assert/strict'
import axios, { AxiosError } from 'axios'
import { installSessionRefresh } from '../src/api/sessionRefresh.js'
import { retryAfterMs } from '../src/api/retryAfter.js'

function rejected(config, status, data = {}, headers = {}) {
  return new AxiosError('synthetic failure', 'ERR_BAD_RESPONSE', config, null,
    { config, status, data, headers })
}
const ok = (config, data) => ({ config, status: 200, data, headers: {} })
function setup({ refresh, resource } = {}) {
  const state = { access: 'old-access', refresh: 'old-refresh', calls: 0, expired: 0, clock: 0, resources: 0 }
  const api = axios.create({ adapter: async config => {
    state.resources++
    if (resource) return resource(config, state)
    if (config.headers.Authorization === 'Bearer old-access') throw rejected(config, 401)
    return ok(config, { ok: true })
  } })
  const refreshClient = axios.create({ adapter: async config => {
    state.calls++
    return refresh ? refresh(config, state) : ok(config, { access: 'new-access', refresh: 'new-refresh' })
  } })
  installSessionRefresh(api, refreshClient, {
    getAccessToken: () => state.access, getRefreshToken: () => state.refresh,
    saveTokens: tokens => Object.assign(state, tokens),
    clearTokens: () => { state.access = null; state.refresh = null },
    onExpired: () => state.expired++, now: () => state.clock,
  })
  return { api, state }
}

test('429 preserves tokens and respects Retry-After without automatic request replay', async () => {
  const { api, state } = setup({ refresh: (config, state) => {
    if (state.calls === 1) throw rejected(config, 429, { retry_after: 120 })
    return ok(config, { access: 'new-access', refresh: 'new-refresh' })
  } })
  await assert.rejects(api.post('records/', { title: 'synthetic' }), error => error.response.status === 429)
  assert.equal(state.resources, 1)
  assert.equal(state.access, 'old-access'); assert.equal(state.refresh, 'old-refresh')
  await assert.rejects(api.get('records/'))
  assert.equal(state.calls, 1); assert.equal(state.expired, 0)
  state.clock = 120001
  assert.equal((await api.get('records/')).status, 200)
  assert.equal(state.calls, 2); assert.equal(state.access, 'new-access')
})

test('network, server and malformed refresh failures preserve the session and back off', async () => {
  for (const status of [null, 500, 503, 400, 'malformed']) {
    const { api, state } = setup({ refresh: config => {
      if (status === 'malformed') return ok(config, {})
      if (status === null) throw new AxiosError('offline', 'ERR_NETWORK', config)
      throw rejected(config, status)
    } })
    await assert.rejects(api.get('records/'))
    await assert.rejects(api.get('records/'))
    assert.equal(state.calls, 1)
    assert.equal(state.access, 'old-access'); assert.equal(state.refresh, 'old-refresh')
    assert.equal(state.expired, 0)
  }
})

test('confirmed invalid refresh token signs out once and optional reads do not redirect', async () => {
  for (const optionalAuth of [false, true]) {
    const { api, state } = setup({ refresh: config => { throw rejected(config, 401, { code: 'token_not_valid' }) } })
    await assert.rejects(api.get('records/', { optionalAuth }))
    assert.equal(state.access, null); assert.equal(state.refresh, null)
    assert.equal(state.expired, optionalAuth ? 0 : 1)
  }
})

test('concurrent expired requests share one refresh and each replay once', async () => {
  let release
  const gate = new Promise(resolve => { release = resolve })
  const { api, state } = setup({ refresh: async config => {
    await gate
    return ok(config, { access: 'new-access', refresh: 'new-refresh' })
  } })
  const requests = [api.get('records/'), api.get('user/profile/'), api.get('notifications/poll/')]
  await new Promise(setImmediate)
  assert.equal(state.calls, 1)
  release()
  assert.deepEqual((await Promise.all(requests)).map(r => r.status), [200, 200, 200])
  assert.equal(state.calls, 1); assert.equal(state.resources, 6)
})

test('a late 401 after token rotation can reuse the same-account recovery', async () => {
  let release
  const gate = new Promise(resolve => { release = resolve })
  const { api, state } = setup({ resource: async config => {
    if (config.url === 'slow/' && !config._retry) { await gate; throw rejected(config, 401) }
    if (config.headers.Authorization === 'Bearer old-access') throw rejected(config, 401)
    return ok(config, {})
  } })
  const slow = api.get('slow/')
  await api.get('fast/')
  release()
  assert.equal((await slow).status, 200)
  assert.equal(state.calls, 1); assert.equal(state.expired, 0)
})

test('late refresh success or failure cannot overwrite or sign out another account', async () => {
  for (const failure of [false, true]) {
    let release
    const gate = new Promise(resolve => { release = resolve })
    const { api, state } = setup({ refresh: async config => {
      await gate
      if (failure) throw rejected(config, 401)
      return ok(config, { access: 'stale-access', refresh: 'stale-refresh' })
    } })
    const request = api.get('records/')
    await new Promise(setImmediate)
    Object.assign(state, { access: 'other-account', refresh: 'other-refresh' })
    release()
    await assert.rejects(request)
    assert.equal(state.access, 'other-account'); assert.equal(state.refresh, 'other-refresh')
    assert.equal(state.expired, 0)
  }
})

test('API failure after successful refresh does not clear the refreshed session', async () => {
  const { api, state } = setup({ resource: config => {
    throw rejected(config, config._retry ? 500 : 401)
  } })
  await assert.rejects(api.get('records/'), error => error.response.status === 500)
  assert.equal(state.access, 'new-access'); assert.equal(state.expired, 0)
})

test('login failures never invoke the refresh endpoint', async () => {
  const { api, state } = setup()
  await assert.rejects(api.post('token/', {}))
  assert.equal(state.calls, 0); assert.equal(state.access, 'old-access')
})

test('missing local refresh token requires login, while canceled refresh preserves credentials', async () => {
  const missing = setup()
  missing.state.refresh = null
  await assert.rejects(missing.api.get('records/'))
  assert.equal(missing.state.expired, 1)
  const canceled = setup({ refresh: () => { throw new AxiosError('canceled', 'ERR_CANCELED') } })
  await assert.rejects(canceled.api.get('records/'))
  assert.equal(canceled.state.expired, 0)
  assert.equal(canceled.state.access, 'old-access')
})

test('Retry-After supports seconds, structured JSON and HTTP dates relative to server clock', () => {
  assert.equal(retryAfterMs({ response: { headers: { 'retry-after': '95' } } }), 95000)
  assert.equal(retryAfterMs({ response: { data: { retry_after: 130 }, headers: { 'retry-after': '1' } } }), 130000)
  assert.equal(retryAfterMs({ response: { headers: {
    date: 'Thu, 01 Jan 1970 00:00:00 GMT', 'retry-after': 'Thu, 01 Jan 1970 00:02:00 GMT',
  } } }), 120000)
  assert.equal(retryAfterMs({}), 30000)
})
