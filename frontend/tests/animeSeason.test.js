import test from 'node:test'
import assert from 'node:assert/strict'
import { getSeasonDetails, comparePeriod } from '../src/utils/animeDisplay.js'

test('journal list, mobile and poster-wall share padded/unpadded seasonal styles', () => {
  for (const [month, icon] of [['1', 'snowflake'], ['4', 'seedling'], ['7', 'sun'], ['10', 'leaf']]) {
    const old = getSeasonDetails(`2026-${month}`)
    const imported = getSeasonDetails(`2026-${month.padStart(2, '0')}`)
    assert.deepEqual(imported, old)
    assert.ok(imported.icon.includes(icon))
    assert.ok(imported.listIconClass && imported.mobileIconClass && imported.gridIconClass)
    assert.equal(comparePeriod(`2026-${month}`, `2026-${month.padStart(2, '0')}`), 0)
  }
})

test('unknown periods retain neutral badges; no mutation of source metadata', () => {
  const neutral = getSeasonDetails('未定档')
  for (const input of [null, undefined, 2026, '', '2026', '2026-00', '2026-13', '2026-004', '2026-04待定']) {
    assert.deepEqual(getSeasonDetails(input), neutral)
  }
  const record = Object.freeze({ period: '2025-04' })
  assert.ok(getSeasonDetails(record.period).icon.includes('seedling'))
  assert.equal(record.period, '2025-04')
})
