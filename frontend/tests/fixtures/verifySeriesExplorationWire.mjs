import { readFileSync } from 'node:fs'
import { mapExplorationPage, explorationDetailAdapter, requireExplorationTransition } from '../../src/utils/explorationResults.js'
import { validateAnnotations } from '../../src/utils/seriesExploration.js'
const { page, next, detail, annotations } = JSON.parse(readFileSync(0, 'utf8'))
const scope = page.content_scope_context.content_scope
const first = mapExplorationPage(page, page.query, scope, true), selected = first.items[0]
const later = mapExplorationPage(next, page.query, scope, true)
requireExplorationTransition(first, later, 'more', new Set(first.items.map(row => row.bangumiId)))
explorationDetailAdapter.map(detail, { ...selected, ...explorationDetailAdapter.select(selected) }, { context: selected.evidenceContext, token: selected.evidenceToken })
validateAnnotations(annotations, annotations.results.map(row => row.bangumi_id), scope)
process.stdout.write(String(first.items.length))
