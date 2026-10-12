import './JournalToolbar.css'
import { Link } from 'react-router-dom'

// Protected UI preview only; no personalized results are claimed yet.
export default function JournalRecommendationBanner() {
  return (
    <Link to="/recommendations" className="journal-recommendation-banner journal-recommendation-banner--link" aria-labelledby="journalRecommendationTitle">
      <div className="journal-recommendation-banner__inner">
        <span className="journal-recommendation-banner__icon" aria-hidden="true">✨</span>
        <div className="journal-recommendation-banner__copy">
          <div className="journal-recommendation-banner__eyebrow">
            <span>YOUR NEXT FAVORITE</span><span className="journal-recommendation-banner__badge">界面预览</span>
          </div>
          <h2 id="journalRecommendationTitle">个性化番剧推荐</h2>
          <p>发现下一部心头好，翻开<span className="journal-recommendation-banner__ending">番剧发现手账 →</span></p>
        </div>
      </div>
    </Link>
  )
}
