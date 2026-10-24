// Django test sends ONLY synthetic public responses to this cross-runtime check.
import { mapExplorationPage, explorationDetailAdapter, requireExplorationTransition } from '../../src/utils/explorationResults.js'
let input = ''
for await (const chunk of process.stdin) input += chunk
const data = JSON.parse(input), scope = data.page.content_scope_context.content_scope
const mapped = mapExplorationPage(data.page, data.page.query, scope)
if (data.detail) explorationDetailAdapter.map(data.detail, { explorationQuery: mapped.query, explorationScope: scope },
  { token: mapped.items[0].evidenceToken, context: mapped.evidence_context })
if (data.next) requireExplorationTransition(mapped, mapExplorationPage(data.next, mapped.query, scope), 'more', new Set(mapped.items.map(item => item.bangumiId)))
process.stdout.write(String(mapped.items.length))
