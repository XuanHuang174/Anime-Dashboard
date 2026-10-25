import test from 'node:test'
import assert from 'node:assert/strict'
import { validateReminders, createSeriesReminderController, REMINDERS_VERSION } from '../src/utils/seriesReminders.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { validateExplorationCapabilities } from '../src/utils/recommendationExploration.js'
import { reminders } from './fixtures/seriesRemindersContract.js'
import { adultScope } from './fixtures/explorationContract.js'
import contract from './fixtures/recommendationExplorationContract.json' with { type: 'json' }

test('12E strict direction, identity, coverage bounds and presentation contract', () => {
  assert.equal(validateReminders(reminders()).results.length, 2)
  const inverse = reminders(); Object.assign(inverse.results[0].facts[0], { source_id: 2, target_id: 1, kind: 'prequel', relation_label: '前传' })
  assert.equal(validateReminders(inverse).results[0].bangumi_id, 2)
  for (const edit of [data => { data.results[0].facts[0].kind = 'summary' }, data => { data.results[0].facts[0].completed_id = 2 },
    data => { data.results[0].record_state = 'linked' }, data => { data.results.push(data.results[0]) }, data => { data.version = -1 },
    data => { data.context = '' }, data => { data.expires_at = 1 }, data => { data.count = 9 }, data => { data.results[0].facts = [] }]) {
    const data = reminders(); edit(data); assert.throws(() => validateReminders(data))
  }
  assert.throws(() => validateReminders(reminders(), 'include_adult'))
  assert.equal(validateReminders(reminders({ contentScope: adultScope }), 'include_adult').count, 2)
})

test('hide/collapse/restore are serialized and re-read confirmed server state', async () => {
  let version = 0, collapsed = false; const hidden = new Set(), writes = []
  const client = { seriesReminders: async () => reminders({ hidden, collapsed, version }),
    changeSeriesReminders: async body => { writes.push(body); version++
      if (body.action === 'hide') hidden.add(body.bangumi_id)
      if (body.action === 'collapse') collapsed = body.collapsed
      if (body.action === 'restore') hidden.clear()
      return { contract_version: REMINDERS_VERSION, saved: true, action: body.action, version }
    } }
  const controller = createSeriesReminderController(client)
  await controller.refresh(); await controller.change('hide', { bangumi_id: 2 })
  assert.equal(controller.getState().data.count, 1)
  await controller.change('collapse', { collapsed: true })
  assert.equal(controller.getState().data.collapsed, true)
  await controller.change('restore')
  assert.equal(controller.getState().data.count, 2)
  assert.deepEqual(writes.map(row => row.expected_version), [0, 1, 2])
  assert.deepEqual(writes.map(row => row.context), ['synthetic-reminders-0', 'synthetic-reminders-1', 'synthetic-reminders-2'])
})

test('uncertain write never replays; conflict or expired binding discards stale controls', async () => {
  let writes = 0
  const controller = createSeriesReminderController({ seriesReminders: async () => reminders(), changeSeriesReminders: async () => { writes++; throw Error('offline') } })
  await controller.refresh(); await controller.change('hide', { bangumi_id: 2 })
  assert.equal(controller.getState().data, null); assert.match(controller.getState().message, /尚未确认/)
  await controller.refresh(); assert.equal(writes, 1)
  controller.getState().data.expires_at = 1
  await controller.change('collapse', { collapsed: true }); assert.equal(writes, 1)
})

test('late account response and disposed component cannot expose previous own records', async () => {
  let finish, current = true
  const controller = createSeriesReminderController({ seriesReminders: () => new Promise(resolve => { finish = resolve }) }, () => current)
  const task = controller.refresh(); current = false; finish(reminders()); await task
  assert.equal(controller.getState().data, null)
  current = true; const next = controller.refresh(); controller.dispose(); finish(reminders()); await next
  assert.equal(controller.getState().data, null)
})

test('failed refresh after saved PATCH reports confirmation without repeating the write', async () => {
  let calls = 0
  const controller = createSeriesReminderController({ seriesReminders: async () => { if (++calls > 1) throw Error(); return reminders() },
    changeSeriesReminders: async body => ({ contract_version: REMINDERS_VERSION, saved: true, action: body.action, version: 1 }) })
  await controller.refresh(); await controller.change('hide', { bangumi_id: 2 })
  assert.equal(controller.getState().saved, true); assert.match(controller.getState().message, /已保存/)
})

test('client uses scoped own endpoint and typed relation conflict does not reset NSFW', async () => {
  const calls = []; let losses = 0
  const transport = { get: async (path, config) => { calls.push([path, config]); return { data: reminders({ contentScope: adultScope }) } },
    patch: async () => { throw { response: { status: 409, data: { code: 'series_snapshot_changed' } } } } }
  const client = createRecommendationClient(transport, 'include_adult', () => losses++)
  await client.seriesReminders(); await assert.rejects(client.changeSeriesReminders({ action: 'hide' }))
  assert.equal(calls[0][0], 'recommendations/series/reminders/'); assert.equal(calls[0][1].params.content_scope, 'include_adult')
  assert.equal(losses, 0)
  assert.equal(validateExplorationCapabilities({ ...contract.capabilities, series_reminders: { enabled: true, policy_version: 'series-reminders.completed-direct.v1' } }).series_reminders.enabled, true)
  assert.throws(() => validateExplorationCapabilities({ ...contract.capabilities, series_reminders: { enabled: true, policy_version: 'guessed-family' } }))
})
