// Actual product components; the browser test intercepts every API request.
import React, { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import JournalHeaderActions from '../../src/components/JournalHeaderActions.jsx'
import TagCleanupModal from '../../src/components/TagCleanupModal.jsx'

function Harness() {
  const [open, setOpen] = useState(false), [preview, setPreview] = useState(false), [changes, setChanges] = useState(0)
  return <main style={{ padding: 24 }}><h1>本机合成手账</h1><output aria-label="手账刷新次数">{changes}</output>
    <JournalHeaderActions isPreviewMode={preview} onTogglePreview={() => setPreview(value => !value)}
      onCleanTags={() => setOpen(true)} publicStatus="private" csvCount={26} />
    {open && <TagCleanupModal filters={{ search: '合成', status: 'plan' }} filteredCount={26}
      onClose={() => setOpen(false)} onChanged={() => setChanges(value => value + 1)} />}
  </main>
}
createRoot(document.getElementById('root')).render(<StrictMode><MemoryRouter><Harness /></MemoryRouter></StrictMode>)
