import { readFileSync } from 'node:fs'
import { mapExplorationPage } from '../../src/utils/explorationResults.js'
import { validateCreditsCapabilities, validateCreditsSearch, validateCreditsSummary, validateCreditsWorks,
  validateCreditsDetail } from '../../src/utils/recommendationCredits.js'
const { operation, data, kind, person, source, row, scope = 'safe', previous } = JSON.parse(readFileSync(0, 'utf8'))
if (operation === 'capabilities') validateCreditsCapabilities(data)
else if (operation === 'search') validateCreditsSearch(data, kind, scope)
else if (operation === 'summary') validateCreditsSummary(data, source, scope)
else if (operation === 'entity') {
  if (data.entity?.person_id !== person) throw Error('Unrelated entity header')
  validateCreditsSearch({ ...data, contract_version: 'recommendations.credits.search.v1',
    kind, limit: 15, has_more: false, results: [data.entity] }, kind, scope)
}
else if (operation === 'works') {
  validateCreditsWorks(data, kind, person, data.conditions, scope)
  if (previous && (data.credits_revision !== previous.credits_revision || data.count !== previous.count
    || data.expires_at !== previous.expires_at || data.content_scope_context.scope_revision !== previous.content_scope_context.scope_revision
    || data.results.some(r => previous.results.some(p => p.bangumi_id === r.bangumi_id)))) throw Error('Invalid continuation')
} else if (operation === 'detail') validateCreditsDetail(data, kind, person, row, scope)
else if (operation === 'legacy') mapExplorationPage(data, data.query, scope)
else throw Error('Unsupported credits release contract')
process.stdout.write('validated')
