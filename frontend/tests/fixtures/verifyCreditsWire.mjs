import { validateCreditsSearch, validateCreditsSummary, validateCreditsRecord, validateCreditsWorks } from '../../src/utils/recommendationCredits.js'
let raw = ''
for await (const chunk of process.stdin) raw += chunk
const data = JSON.parse(raw)
validateCreditsSearch(data.search, data.search.kind, 'safe')
validateCreditsSummary(data.summary, data.summary.bangumi_id, 'safe')
validateCreditsSummary(data.full, data.full.bangumi_id, 'safe')
validateCreditsRecord(data.record, data.record.record_id, 'safe')
validateCreditsWorks(data.works, data.works.entity.kind, data.works.entity.person_id, data.works.conditions, 'safe')
// Exercise actual HTTP payloads against both fast and slow browser clocks.
for (const skew of [-86400000, 86400000]) validateCreditsWorks(data.works, data.works.entity.kind,
  data.works.entity.person_id, data.works.conditions, 'safe', Date.now() + skew)
process.stdout.write('5')
