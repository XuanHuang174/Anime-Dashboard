import test from 'node:test'
import assert from 'node:assert/strict'
import { journalDisplayModeKey, readJournalPreview, saveJournalPreview } from '../src/utils/journalDisplayMode.js'
import { recommendationSessionScope } from '../src/utils/recommendationSession.js'
import { getAccessToken, saveTokens, clearTokens } from '../src/auth/tokenStorage.js'

function memoryStorage() {
  const values = new Map()
  return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
}
const token = (id, serial) => `synthetic.${Buffer.from(JSON.stringify({ user_id: id, jti: serial })).toString('base64url')}.fixture`

test('journal mode survives token refresh, logout and logging back into the same account', () => {
  const previous = globalThis.localStorage, storage = memoryStorage()
  globalThis.localStorage = storage
  try {
    saveTokens({ access: token(41, 1), refresh: 'synthetic-refresh' })
    const session = recommendationSessionScope(getAccessToken())
    assert.equal(readJournalPreview(session), false)
    saveJournalPreview(session, true)
    saveTokens({ access: token(41, 2) })
    assert.equal(readJournalPreview(recommendationSessionScope(getAccessToken())), true)
    clearTokens()
    assert.equal(getAccessToken(), null)
    assert.equal(readJournalPreview('anonymous'), false)
    saveTokens({ access: token(41, 3) })
    assert.equal(readJournalPreview(recommendationSessionScope(getAccessToken())), true)
    saveJournalPreview(session, false)
    assert.equal(readJournalPreview(session), false)
  } finally { if (previous === undefined) delete globalThis.localStorage; else globalThis.localStorage = previous }
})

test('different accounts retain independent display choices', () => {
  const storage = memoryStorage()
  saveJournalPreview('subject:41', true, storage)
  assert.equal(readJournalPreview('subject:42', storage), false)
  saveJournalPreview('subject:42', true, storage)
  saveJournalPreview('subject:42', false, storage)
  assert.equal(readJournalPreview('subject:41', storage), true)
  assert.equal(readJournalPreview('subject:42', storage), false)
})

test('anonymous, malformed and opaque sessions cannot persist a token as a preference key', () => {
  const storage = memoryStorage()
  for (const session of ['anonymous', 'opaque:secret-token', 'subject:0', 'subject:41/other', null]) {
    assert.equal(journalDisplayModeKey(session), null)
    saveJournalPreview(session, true, storage)
    assert.equal(readJournalPreview(session, storage), false)
  }
  assert.equal(storage.values.size, 0)
})

test('unavailable storage and invalid values preserve the default without breaking mode changes', () => {
  const blocked = { getItem() { throw Error('blocked') }, setItem() { throw Error('blocked') } }
  assert.equal(readJournalPreview('subject:41', blocked), false)
  assert.doesNotThrow(() => saveJournalPreview('subject:41', true, blocked))
  const storage = memoryStorage(), key = journalDisplayModeKey('subject:41')
  storage.setItem(key, 'true')
  assert.equal(readJournalPreview('subject:41', storage), false)
  saveJournalPreview('subject:41', 'preview', storage)
  assert.equal(storage.getItem(key), 'true')
})
