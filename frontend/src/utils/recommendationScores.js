import { RECOMMENDATION_COPY as COPY } from './recommendationCopy.js'

// Public import normalization conflates raw zero, missing and invalid values.
// Without per-row provenance/vote counts, zero MUST remain ambiguous.
export function publicScorePresentation(value) {
  if (value === 0) return { state: 'zero_ambiguous', text: COPY.publicScoreUnknown, help: COPY.publicZeroHelp }
  if (typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= 10) {
    return { state: 'public_rating', text: `${value.toFixed(1)} / 10`, help: COPY.publicScoreHelp }
  }
  return { state: 'unavailable', text: COPY.publicScoreMissing, help: COPY.publicMissingHelp }
}

// Personal scores have a DIFFERENT meaning: explicit 0 is valid, not missing.
// This formatter does not decide eligibility as a >=8.5 trusted source.
export function personalScoreValue(value) {
  if (!['string', 'number'].includes(typeof value) || !/^\d{1,2}(?:\.\d)?$/.test(String(value))) return null
  const score = Number(value)
  return Number.isFinite(score) && score >= 0 && score <= 10 ? score : null
}

export function personalScoreText(value) {
  const score = personalScoreValue(value)
  return score === null ? COPY.personalScoreMissing : `${score.toFixed(1)} 分`
}
