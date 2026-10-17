// D2 contract support only: the SERVER selects a recipe. No client V2 switch.
const expected = { contract_version: 'recommendations.execution.v1', algorithm_version: 'content.v2',
  ranking_policy_version: 'balanced-rerank.v3', recall_policy_version: 'source-pattern-recall.v2',
  runtime_version: 'pattern-prefix-runtime.v1', control_policy_version: 'browse-controls.v2',
  candidate_limit: 400, result_limit: 120, batch_size: 24, default_page_size: 12 }
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
export const validDetailContext = value => typeof value === 'string' && value.length > 0 && value.length <= 8192
  && /^[A-Za-z0-9_.:-]+$/.test(value)
export function validExecutionRecipe(recipe) {
  return recipe && Object.keys(recipe).length === Object.keys(expected).length + 3
    && Object.entries(expected).every(([key, value]) => recipe[key] === value)
    && hash(recipe.recipe_revision) && hash(recipe.control_policy_sha256) && typeof recipe.serving_enabled === 'boolean'
}
export function sameExecutionRecipe(left, right) {
  if (left === undefined && right === undefined) return true
  return validExecutionRecipe(left) && validExecutionRecipe(right)
    && Object.keys(left).every(key => left[key] === right[key])
}
export function matchesExecutionContext(context, recipe) {
  return validExecutionRecipe(recipe) && context?.algorithm_version === recipe.algorithm_version
    && ['ranking_policy_version', 'recall_policy_version', 'runtime_version', 'control_policy_version', 'recipe_revision']
      .every(key => context[key] === recipe[key]) && hash(context.browse_context_version)
    && hash(context.profile_version) && hash(context.catalog_feature_version)
    && ['preference_version', 'feedback_version'].every(key => Number.isInteger(context[key]) && context[key] >= 0)
}
export function requireScopedDetail(item, selection) {
  const scope = selection.executionContext
  if (scope && (!validDetailContext(item.detailContext) || !item.evidenceContext
    || ['algorithm_version', 'ranking_policy_version', 'recall_policy_version', 'runtime_version',
      'control_policy_version', 'recipe_revision'].some(key => item.evidenceContext[key] !== scope[key])
    || !hash(item.evidenceContext.browse_context_version))) {
    throw new Error('推荐依据合同不兼容，浏览版本已变化，请重新加载推荐。')
  }
}
