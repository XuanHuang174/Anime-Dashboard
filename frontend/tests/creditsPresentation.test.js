import test, { after } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { readFile } from 'node:fs/promises'
import { creditsCapability } from './fixtures/creditsContract.js'
const server = await createServer({ configFile: false, plugins: [react()], server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
after(() => server.close())
const { default: Entry } = await server.ssrLoadModule('/src/components/recommendations/CreditsEntry.jsx')
const { default: Navigation } = await server.ssrLoadModule('/src/components/recommendations/RecommendationModeNavigation.jsx')
test('credits is a secondary tool with an accessible icon, independently hidden when closed', () => {
  const render = flags => renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: ['/recommendations'] }, React.createElement(Entry, { capabilities: flags })))
  assert.equal(render({ ...creditsCapability, voice_actor: false, studio: false }), '')
  assert.match(render({ ...creditsCapability, voice_actor: false }), /人物与制作/)
  assert.match(render(creditsCapability), /aria-hidden="true"/)
  const nav = renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(Navigation, { current: 'personal' })))
  assert.doesNotMatch(nav, /人物与制作/)
  assert.match(nav, /个性化推荐/); assert.match(nav, /单番相似/); assert.match(nav, /按标签找/)
})
test('private preview alone owns credits summary; editor and public detail do not acquire exploration actions', async () => {
  const preview = await readFile('src/components/DetailModal.jsx', 'utf8'), editor = await readFile('src/components/EditableDetailModal.jsx', 'utf8')
  assert.match(preview, /isPrivateJournal && !editable && <div className="memphis-modal-piece shrink-0"><CreditsSummary/)
  assert.doesNotMatch(editor, /CreditsSummary|creditsSummary|人物与制作/)
})
