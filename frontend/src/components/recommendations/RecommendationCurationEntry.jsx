import { createContext, useContext } from 'react'
export const RecommendationCurationContext = createContext(null)
export default function RecommendationCurationEntry({item,disabled}) {
  const context=useContext(RecommendationCurationContext)
  return context?<button type="button" className="rec-button rec-curation-entry" disabled={disabled} aria-label={`将${item.title}收录到私人片单`} onClick={()=>context.open(item)}>▤ 收录到片单</button>:null
}
