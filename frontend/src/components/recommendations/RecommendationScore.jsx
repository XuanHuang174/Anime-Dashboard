import { RECOMMENDATION_COPY as COPY, sourcePersonalScoreLabel } from '../../utils/recommendationCopy.js'
import { publicScorePresentation, personalScoreText } from '../../utils/recommendationScores.js'

export function RecommendationPublicScore({ score }) {
  const display = publicScorePresentation(score)
  return <span className="rec-public-score" data-score-state={display.state} title={display.help}>
    <strong>{COPY.publicScore}</strong> <b>{display.text}</b>
    <span className="rec-sr-only">。{display.help}</span>
  </span>
}

export function RecommendationPersonalScore({ title, score }) {
  return <p className="rec-source-score">{sourcePersonalScoreLabel(title)}<strong>{personalScoreText(score)}</strong></p>
}
