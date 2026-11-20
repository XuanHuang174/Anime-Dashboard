import { useMemo, useState } from 'react'
import client from '../api/recommendations.js'
import RecommendationIdentityReview from './recommendations/RecommendationIdentityReview.jsx'
import '../styles/recommendations.css'
import '../styles/recommendations-theme.css'

export default function JournalIdentityReview({ recordId, onClose, onChanged }) {
  const [adult, setAdult] = useState(false)
  const scopedClient = useMemo(() => client.forScope(adult ? 'include_adult' : 'safe'), [adult])
  return <div className="memphis-rec-container" style={{ minHeight: 0, height: 0 }}>
    <RecommendationIdentityReview key={adult ? 'adult' : 'safe'} client={scopedClient} scope="all" recordId={recordId}
      onClose={onClose} onChanged={onChanged} title="核对标准作品"
      scopeControl={state => <label className="rec-checkbox"><input type="checkbox" checked={adult} onChange={event => { if (state.mayHaveChanged) onChanged(); setAdult(event.target.checked) }} />本次包含已审核 NSFW 作品，不改变推荐筛选</label>} />
  </div>
}
