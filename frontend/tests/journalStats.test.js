import assert from 'node:assert/strict'
import test from 'node:test'
import { countJournalStatuses } from '../src/utils/journalStats.js'

test('watching and planned-or-undated counts use distinct criteria', () => {
  const records = [
    { status: 'watching', period: '2026-7', score: null },
    { status: 'watching', period: '未定档', score: 9 },
    { status: 'plan', period: '2026-10', score: 8 },
    { status: 'plan', period: '2027-待定', score: null },
    { status: 'completed', period: '2026-4', score: null },
    { status: 'on_hold', period: '2026-4', score: null },
    { status: 'completed', period: '未定档', score: 7 },
  ]
  assert.deepEqual(countJournalStatuses(records), { watchingCount: 2, pendingCount: 4 })
})

test('empty or absent periods do not throw or imply pending', () => {
  assert.deepEqual(countJournalStatuses([]), { watchingCount: 0, pendingCount: 0 })
  assert.deepEqual(countJournalStatuses([{ status: 'watching' }, { status: 'plan', period: null }]),
    { watchingCount: 1, pendingCount: 1 })
})
