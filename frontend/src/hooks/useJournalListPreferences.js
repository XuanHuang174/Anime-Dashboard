import { useEffect, useState } from 'react'
import useSeriesSession from './useSeriesSession.js'
import { journalListPreferencesKey, readJournalListPreferences, saveJournalListPreference } from '../utils/journalListPreferences.js'

export default function useJournalListPreferences(sharedUsername = '') {
  const session = useSeriesSession(), shared = Boolean(sharedUsername)
  const context = shared ? `shared:${sharedUsername}` : session
  const read = () => readJournalListPreferences(shared ? null : session, typeof window !== 'undefined' && window.innerWidth < 768)
  const [selection, setSelection] = useState(() => ({ context, ...read() }))
  const values = selection.context === context ? selection : read()
  useEffect(() => {
    const restore = () => setSelection({ context, ...read() })
    restore()
    const changed = event => { if (!shared && (event.key === null || event.key === journalListPreferencesKey(session))) restore() }
    window.addEventListener('storage', changed)
    return () => window.removeEventListener('storage', changed)
  }, [context])
  const change = (field, value) => {
    if (field === 'view' ? !['list', 'grid'].includes(value) : !['all', '12', '24', '48', '96'].includes(value)) return
    setSelection(current => ({ ...(current.context === context ? current : { context, ...read() }), [field]: value }))
    if (!shared) saveJournalListPreference(session, field, value)
  }
  return [values.view, value => change('view', value), values.pageSize, value => change('pageSize', value)]
}
