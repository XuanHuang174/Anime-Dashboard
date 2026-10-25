import { readFileSync } from 'node:fs'
import { validateSeries } from '../../src/utils/recommendationSeries.js'
import { validateAnnotations } from '../../src/utils/seriesExploration.js'
import { validateReminders } from '../../src/utils/seriesReminders.js'
import { mapExplorationPage, requireExplorationTransition } from '../../src/utils/explorationResults.js'
const { kind, data, source, ids, scope = 'safe', previous } = JSON.parse(readFileSync(0, 'utf8'))
if (kind === 'workspace') validateSeries(data, source, scope)
else if (kind === 'annotations') validateAnnotations(data, ids, scope)
else if (kind === 'reminders') validateReminders(data, scope)
else if (kind === 'partition' || kind === 'legacy') {
  const mapped = mapExplorationPage(data, data.query, scope, kind === 'partition')
  if (previous) {
    const first = mapExplorationPage(previous, previous.query, scope, true)
    requireExplorationTransition(first, mapped, 'more', new Set(first.items.map(row => row.bangumiId)))
  }
} else throw Error('Unsupported series release contract')
process.stdout.write('validated')
