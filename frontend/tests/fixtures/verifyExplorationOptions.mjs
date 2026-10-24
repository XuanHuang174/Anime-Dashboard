// Python supplies only synthetic public scope/count data over stdin.
import { validateExplorationOptions } from '../../src/utils/recommendationExploration.js'
let input = ''
for await (const chunk of process.stdin) input += chunk
const raw = JSON.parse(input)
const data = validateExplorationOptions(raw, raw.content_scope_context.content_scope)
process.stdout.write(String(data.tags.length))
