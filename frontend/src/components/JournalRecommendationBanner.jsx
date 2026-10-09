import './JournalToolbar.css'

// A visual teaser only; no recommendation endpoint or navigation is enabled yet.
export default function JournalRecommendationBanner() {
  return (
    <aside className="journal-recommendation-banner" aria-labelledby="journalRecommendationTitle">
      <div className="journal-recommendation-banner__inner">
        <span className="journal-recommendation-banner__icon" aria-hidden="true">✨</span>
        <div className="journal-recommendation-banner__copy">
          <div className="journal-recommendation-banner__eyebrow">
            <span>YOUR NEXT FAVORITE</span><span className="journal-recommendation-banner__badge">即将上线</span>
          </div>
          <h2 id="journalRecommendationTitle">个性化番剧推荐</h2>
          <p>根据你的 8.5+ 高分喜好，为您智能挖掘<span className="journal-recommendation-banner__ending">宝藏作品 →</span></p>
        </div>
      </div>
    </aside>
  )
}
