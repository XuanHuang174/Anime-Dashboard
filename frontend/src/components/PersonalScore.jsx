import { createContext, useContext } from 'react'
import { normalizeRatingPreferences, ratingForScore, ratingStyle } from '../utils/ratingPreferences.js'
import './PersonalScore.css'

export const RatingPreferencesContext = createContext(null)
export const useRatingPreferences = () => normalizeRatingPreferences(useContext(RatingPreferencesContext))

export default function PersonalScore({ score, preferences, stars = true, suffix = false, className = '' }) {
  const inherited = useContext(RatingPreferencesContext)
  const tier = ratingForScore(score, preferences ?? inherited)
  const description = tier ? `${tier.score.toFixed(1)} 分，${tier.label}${stars && tier.show_stars ? `，${tier.stars}/${tier.max} 星` : ''}` : '尚未评分'
  return <span className={`personal-score ${className}`} title={description} aria-label={description}>
    <span className="personal-score-number" style={ratingStyle(tier)} aria-hidden="true">{tier ? tier.score.toFixed(1) : '待定'}{suffix && tier && <small> / 10</small>}</span>
    {tier && stars && tier.show_stars && <span className="personal-score-stars" aria-hidden="true">{Array.from({ length: tier.max }, (_, index) => <span key={index} style={index < tier.stars ? ratingStyle(tier, true) : undefined} className={index < tier.stars ? 'is-lit' : ''}>{index < tier.stars ? '★' : '☆'}</span>)}</span>}
  </span>
}

export function RatingLegend() {
  const preferences = useRatingPreferences()
  return <details className="rating-legend"><summary>作者评分口径 · {preferences.show_stars ? `${preferences.star_max} 星制` : '数字显示'}</summary>
    <p>评分仍为 0–10 分；以下是作者的展示档位，未评分不计入评分统计。</p>
    {preferences.tiers.map((tier, index) => <div key={index}><strong>{tier.label}</strong><span>{tier.min} ≤ 分数 {index === 0 ? '≤ 10' : `< ${preferences.tiers[index-1].min}`}</span>{preferences.show_stars && <span>{tier.stars}/{preferences.star_max} 星</span>}</div>)}
  </details>
}
