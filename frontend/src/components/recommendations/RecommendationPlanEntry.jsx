import { createContext, useContext } from 'react'
export const RecommendationPlanContext = createContext(null)

export default function RecommendationPlanEntry({item, disabled}) {
  const plans = useContext(RecommendationPlanContext)
  return plans ? <button type="button" className="rec-button rec-plan-entry" disabled={disabled} onClick={()=>plans.open(item)} aria-label={`安排${item.title}观看计划`}>⚑ 观看安排</button> : null
}
