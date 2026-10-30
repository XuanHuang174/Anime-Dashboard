// A local display preference only; never grants access or stores a token.
export function journalDisplayModeKey(session) {
  const subject = /^subject:([1-9]\d*)$/.exec(session || '')?.[1]
  return subject ? `anime-dashboard:journal-display-mode:v1:${subject}` : null
}

export function readJournalPreview(session, storage) {
  const key = journalDisplayModeKey(session)
  if (!key) return false
  try { return (storage ?? globalThis.localStorage).getItem(key) === 'preview' }
  catch { return false }
}

export function saveJournalPreview(session, preview, storage) {
  const key = journalDisplayModeKey(session)
  if (!key || typeof preview !== 'boolean') return
  try { (storage ?? globalThis.localStorage).setItem(key, preview ? 'preview' : 'edit') }
  catch { /* Optional preference storage must not block switching modes. */ }
}
