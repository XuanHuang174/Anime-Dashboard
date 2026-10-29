import { CREDIT_DEFAULTS, validateCreditConditions } from './recommendationCredits.js'
import { scoreConditions, scoreDraft } from './browseScore.js'
import { filterDraftError } from './recommendationBrowse.js'

export function creditsFilterDraft(options, adult) {
  return { ...options, ...scoreDraft(options), includeAdult: adult,
    yearFrom: options.year_from === null ? '' : String(options.year_from), yearTo: options.year_to === null ? '' : String(options.year_to) }
}
export function creditsFilterOptions(draft) {
  const error = filterDraftError(draft)
  if (error) throw new Error(error)
  const score = scoreConditions(draft)
  return validateCreditConditions({ ...Object.fromEntries(Object.keys(CREDIT_DEFAULTS).map(key => [key, draft[key]])),
    year_from: draft.yearFrom ? Number(draft.yearFrom) : null, year_to: draft.yearTo ? Number(draft.yearTo) : null,
    min_score: Number(score.min_score), max_score: Number(score.max_score), max_score_exclusive: score.max_score_exclusive })
}
export function creditsChipFilters(options, adult) {
  return { ...creditsFilterDraft(options, adult), hideRecorded: options.hide_recorded }
}
