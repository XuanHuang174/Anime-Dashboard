import test from 'node:test'
import assert from 'node:assert/strict'
import { scrollContainer } from '../src/utils/scrollContainer.js'

test('series dialog scroll owner is used instead of its naturally growing visible body', () => {
  const body = {}, dialog = { overflowY: 'auto', parentElement: body }, content = { overflowY: 'visible', parentElement: dialog }
  const prior = { window: globalThis.window, document: globalThis.document, getComputedStyle: globalThis.getComputedStyle }
  try {
    globalThis.window = {}; globalThis.document = { body }; globalThis.getComputedStyle = node => node
    assert.equal(scrollContainer(content), dialog)
    const inner = { overflowY: 'auto', parentElement: dialog }
    assert.equal(scrollContainer(inner), inner) // Credits/content layouts may own the scroll themselves.
    assert.equal(scrollContainer({ overflowY: 'visible', parentElement: body }), globalThis.window)
    assert.equal(scrollContainer(null), globalThis.window)
  } finally { Object.assign(globalThis, prior) }
})
