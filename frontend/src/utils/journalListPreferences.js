// Account-scoped UI choices only, independent of journal contents and auth.
export function journalListPreferencesKey(session) {
  const subject = /^subject:([1-9]\d*)$/.exec(session || '')?.[1]
  return subject ? `anime-dashboard:journal-list-preferences:v1:${subject}` : null
}
const valid = (field, value) => field === 'view' ? ['list', 'grid'].includes(value)
  : field === 'pageSize' && ['all', '12', '24', '48', '96'].includes(value)
export function readJournalListPreferences(session, mobile = false, storage) {
  const defaults = { view: mobile ? 'grid' : 'list', pageSize: 'all' }, key = journalListPreferencesKey(session)
  if (!key) return defaults
  try {
    const saved = JSON.parse((storage ?? globalThis.localStorage).getItem(key))
    for (const field of Object.keys(defaults)) if (valid(field, saved?.[field])) defaults[field] = saved[field]
  } catch { /* Missing, corrupt or blocked storage keeps device defaults. */ }
  return defaults
}
export function saveJournalListPreference(session, field, value, storage) {
  const key = journalListPreferencesKey(session)
  if (!key || !valid(field, value)) return
  try {
    const saved = readJournalListPreferences(session, false, storage)
    // Only persist choices the user made; do not freeze a device default.
    const raw = JSON.parse((storage ?? globalThis.localStorage).getItem(key))
    const next = {}
    for (const name of ['view', 'pageSize']) if (valid(name, raw?.[name])) next[name] = saved[name]
    next[field] = value
    ;(storage ?? globalThis.localStorage).setItem(key, JSON.stringify(next))
  } catch {
    try { (storage ?? globalThis.localStorage).setItem(key, JSON.stringify({ [field]: value })) } catch { /* Optional storage. */ }
  }
}
