import test from 'node:test'
import assert from 'node:assert/strict'
import { createResourceSearchSession } from '../src/utils/resourceSearchSession.js'

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
const row = name => ({ title: name, magnet: `magnet:?xt=urn:btih:${name}`, size: '1 GB', publisher: 'QA', published_at: '' })
const ok = name => ({ data: { status: 'success', results: name ? [row(name)] : [] } })
const options = extra => ({ hasSession: () => true, copyText: async () => {}, ...extra })

test('only explicit open starts search; StrictMode replay does not duplicate auto requests', async () => {
  const calls = []
  const session = createResourceSearchSession({ get: async (...args) => { calls.push(args); return ok('A') } }, options())
  assert.equal(calls.length, 0)
  const discarded = session.open('《作品》 第二季'); session.close()
  await session.open('《作品》 第二季'); await discarded
  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], 'utils/anime-search/')
  assert.deepEqual(calls[0][1].params, { keyword: '作品' })
  assert.equal(calls[0][1].timeout, 15000)
  session.close(); session.open('', false)
  assert.equal(session.getSnapshot().results.length, 0)
  assert.equal(calls.length, 1)
})

test('closing/reopening cancels old request; late results/errors/finally cannot enter the new session', async () => {
  for (const rejects of [false, true]) {
    const slow = deferred(), calls = []
    const session = createResourceSearchSession({ get: (path, request) => {
      calls.push(request); return calls.length === 1 ? slow.promise : Promise.resolve(ok('B'))
    } }, options())
    const a = session.open('A'); await Promise.resolve()
    session.close(); await session.open('B')
    const expected = session.getSnapshot()
    if (rejects) slow.reject(new Error('stale failure')); else slow.resolve(ok('A'))
    await a
    assert.ok(calls[0].signal.aborted)
    assert.equal(session.getSnapshot(), expected)
    assert.equal(expected.results[0].title, 'B')
    assert.equal(expected.loading, false); assert.equal(expected.error, '')
  }
})

test('editing/research cancels old requests and clears copy/error/result state, without auto querying input', async () => {
  const slow = deferred(); let calls = 0
  const session = createResourceSearchSession({ get: async () => ++calls === 1 ? slow.promise : ok('B') }, options())
  const pending = session.open('A'); await Promise.resolve()
  session.setKeyword('B')
  assert.equal(calls, 1); assert.equal(session.getSnapshot().loading, false)
  await session.search('B'); slow.resolve(ok('A')); await pending
  assert.equal(session.getSnapshot().results[0].title, 'B')
  assert.equal(session.getSnapshot().searchedKeyword, 'B')
})

test('copy success/failure from a previous session, edited query or superseded copy never leaks prompts/toasts', async () => {
  for (const reject of [false, true]) {
    const copy = deferred(); let toasts = 0
    const session = createResourceSearchSession({ get: async () => ok('A') }, options({ copyText: () => copy.promise, onCopied: () => toasts++ }))
    await session.open('A')
    const pending = session.copyMagnet(row('A').magnet)
    session.close(); session.open('B', false)
    const expected = session.getSnapshot()
    if (reject) copy.reject(new Error('clipboard denied')); else copy.resolve()
    await pending
    assert.equal(session.getSnapshot(), expected); assert.equal(toasts, 0)
  }
  const copies = [deferred(), deferred()]; let index = 0
  const session = createResourceSearchSession({ get: async () => ok('A') }, options({ copyText: () => copies[index++].promise }))
  await session.open('A')
  const old = session.copyMagnet(row('A').magnet), latest = session.copyMagnet(row('A').magnet)
  copies[1].resolve(); await latest; copies[0].reject(new Error('old')); await old
  assert.equal(session.getSnapshot().copiedMagnet, row('A').magnet)
  assert.equal(session.getSnapshot().copyFallback, '')
  const editCopy = deferred()
  const editing = createResourceSearchSession({ get: async () => ok('A') }, options({ copyText: () => editCopy.promise }))
  await editing.open('A'); const pending = editing.copyMagnet(row('A').magnet)
  editing.setKeyword('new'); editCopy.reject(new Error('late')); await pending
  assert.equal(editing.getSnapshot().copyFallback, '')
})

test('only active current safe magnets can be copied/opened; fallback survives only within its own session', async () => {
  let writes = 0
  const session = createResourceSearchSession({ get: async () => ok('A') }, options({ copyText: async () => { writes++; throw new Error('denied') } }))
  await session.open('A')
  for (const magnet of ['javascript:bad', row('B').magnet]) { await session.copyMagnet(magnet); assert.equal(session.canUseMagnet(magnet), false) }
  assert.equal(writes, 0)
  await session.copyMagnet(row('A').magnet)
  assert.equal(session.getSnapshot().copyFallback, row('A').magnet)
  session.close()
  assert.equal(session.canUseMagnet(row('A').magnet), false)
  assert.equal(session.getSnapshot().copyFallback, '')
})

test('resource failure/loading/empty are local; instances and owners never share any result cache', async () => {
  const a = createResourceSearchSession({ get: async () => { throw { code: 'ECONNABORTED' } } }, options())
  const b = createResourceSearchSession({ get: async () => ok('') }, options())
  await a.open('A'); await b.open('A')
  assert.match(a.getSnapshot().error, /正在唤醒/)
  assert.equal(b.getSnapshot().error, ''); assert.deepEqual(b.getSnapshot().results, [])
  const guest = createResourceSearchSession({ get: () => assert.fail('No unauthorized request') }, options({ hasSession: () => false }))
  await guest.open('A'); assert.equal(guest.getSnapshot().needsLogin, true)
})
