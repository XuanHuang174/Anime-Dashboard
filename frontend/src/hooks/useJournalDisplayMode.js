import { useEffect, useState } from 'react'
import useSeriesSession from './useSeriesSession.js'
import { journalDisplayModeKey, readJournalPreview, saveJournalPreview } from '../utils/journalDisplayMode.js'

export default function useJournalDisplayMode(readOnly = false) {
  const session = useSeriesSession()
  const [selection, setSelection] = useState(() => ({ session, preview: readJournalPreview(session) }))
  // Do not render the previous account's choice while its storage effect catches up.
  const preview = selection.session === session ? selection.preview : readJournalPreview(session)
  useEffect(() => {
    const read = () => setSelection({ session, preview: readJournalPreview(session) })
    read()
    const changed = event => { if (event.key === null || event.key === journalDisplayModeKey(session)) read() }
    window.addEventListener('storage', changed)
    return () => window.removeEventListener('storage', changed)
  }, [session])
  return [readOnly || preview, next => {
    if (readOnly || typeof next !== 'boolean') return
    setSelection({ session, preview: next })
    saveJournalPreview(session, next)
  }]
}
