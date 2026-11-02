import test from 'node:test'
import assert from 'node:assert/strict'
import { journalListPreferencesKey, readJournalListPreferences, saveJournalListPreference } from '../src/utils/journalListPreferences.js'
import { clearTokens, saveTokens } from '../src/auth/tokenStorage.js'

const storage = () => { const data = new Map(); return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) } }
test('view and page size survive logout with independent account values', () => {
  const previous = globalThis.localStorage, store = storage(); globalThis.localStorage = store
  try {
    saveTokens({ access: 'synthetic-only' })
    saveJournalListPreference('subject:41', 'view', 'grid')
    saveJournalListPreference('subject:41', 'pageSize', '24')
    clearTokens(); saveTokens({ access: 'new-synthetic-only' })
    assert.deepEqual(readJournalListPreferences('subject:41'), { view: 'grid', pageSize: '24' })
    assert.deepEqual(readJournalListPreferences('subject:42'), { view: 'list', pageSize: 'all' })
    saveJournalListPreference('subject:42', 'view', 'list')
    saveJournalListPreference('subject:42', 'pageSize', '12')
    assert.equal(readJournalListPreferences('subject:41').pageSize, '24')
  } finally { if (previous === undefined) delete globalThis.localStorage; else globalThis.localStorage = previous }
})
test('explicit mobile list choice wins over grid default; pagination alone does not freeze the view', () => {
  const store = storage()
  saveJournalListPreference('subject:41', 'pageSize', '48', store)
  assert.deepEqual(readJournalListPreferences('subject:41', true, store), { view: 'grid', pageSize: '48' })
  saveJournalListPreference('subject:41', 'view', 'list', store)
  assert.deepEqual(readJournalListPreferences('subject:41', true, store), { view: 'list', pageSize: '48' })
  saveJournalListPreference('subject:41', 'pageSize', 'all', store)
  assert.equal(readJournalListPreferences('subject:41', true, store).view, 'list')
})
test('bad data, blocked storage and non-account sessions cannot inject invalid preferences', () => {
  const store = storage(), key = journalListPreferencesKey('subject:41')
  store.setItem(key, '{broken')
  saveJournalListPreference('subject:41', 'pageSize', '96', store)
  assert.equal(readJournalListPreferences('subject:41', false, store).pageSize, '96')
  store.setItem(key, JSON.stringify({ view: 'bad', pageSize: '999999' }))
  assert.deepEqual(readJournalListPreferences('subject:41', false, store), { view: 'list', pageSize: 'all' })
  for (const session of ['anonymous', 'opaque:secret', null]) {
    assert.equal(journalListPreferencesKey(session), null)
    saveJournalListPreference(session, 'view', 'grid', store)
    assert.equal(readJournalListPreferences(session, false, store).view, 'list')
  }
  const blocked = { getItem() { throw Error() }, setItem() { throw Error() } }
  assert.doesNotThrow(() => saveJournalListPreference('subject:41', 'view', 'grid', blocked))
  assert.equal(readJournalListPreferences('subject:41', false, blocked).view, 'list')
})
