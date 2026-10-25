import { readFileSync } from 'node:fs'
import { validateSeries, validateSeriesDetail } from '../../src/utils/recommendationSeries.js'
const { page, detail } = JSON.parse(readFileSync(0, 'utf8'))
const scope = page.content_scope_context.content_scope
validateSeries(page, page.selected_seed.bangumi_id, scope)
validateSeriesDetail(detail, page, detail.item.bangumi_id, scope)
process.stdout.write(String(page.results.length))
