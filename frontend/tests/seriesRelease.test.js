import test from 'node:test'
import assert from 'node:assert/strict'
import { isSeriesDisabled } from '../src/utils/recommendationSeries.js'
import { createExplorationBrowseController } from '../src/utils/explorationSession.js'
import { createRecommendationClient } from '../src/api/recommendationClient.js'
import { seriesExplorePage } from './fixtures/seriesExplorationContract.js'
import { query } from './fixtures/explorationContract.js'

const withdrawn = { response: { status: 503, data: { code: 'series_disabled' } } }
test('12F withdrawal invalidates partition contexts; transient outages retain a valid snapshot', async () => {
  let failure = { response: { status: 503, data: {} } }
  const client = { seriesExploration: true, explorationBrowse: async () => seriesExplorePage(), explorationContext: async () => { throw failure } }
  const controller = createExplorationBrowseController(client)
  await controller.start({ key: 'series', query }); await controller.verify()
  assert.equal(controller.getSnapshot().snapshotDirty, undefined)
  failure = withdrawn; await controller.verify()
  assert.equal(controller.getSnapshot().snapshotDirty, true)
  assert.equal(controller.getSnapshot().items[0].evidence, null)
  assert.equal(await controller.loadMore(), false)
})
test('withdrawal during continuation does not silently mix old and new algorithms', async () => {
  let count = 0
  const client = { seriesExploration: true, explorationBrowse: async () => { if (++count > 1) throw withdrawn; return seriesExplorePage() } }
  const controller = createExplorationBrowseController(client)
  await controller.start({ key: 'series', query }); await controller.loadMore()
  assert.equal(controller.getSnapshot().snapshotDirty, true)
  assert.equal(count, 2)
})
test('rollback 503 cannot reset adult authorization; only typed withdrawal hides optional series data', async () => {
  let lost = 0
  const client = createRecommendationClient({ get: async () => { throw withdrawn } }, 'include_adult', () => lost++)
  await assert.rejects(client.seriesAnnotations([1])); assert.equal(lost, 0)
  assert.equal(isSeriesDisabled(withdrawn), true)
  assert.equal(isSeriesDisabled({ response: { status: 503 } }), false)
  assert.equal(isSeriesDisabled({ response: { status: 409, data: { code: 'series_snapshot_changed' } } }), false)
})
