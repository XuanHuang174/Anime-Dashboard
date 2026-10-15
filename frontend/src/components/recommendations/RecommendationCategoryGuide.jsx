import { REC_CATEGORIES } from '../../utils/recommendations.js'

export default function RecommendationCategoryGuide({ category, mode }) {
  const current = REC_CATEGORIES.find(option => option.id === category)
  return <div id="rec-category-description" className="rec-category-guide" role="status" aria-live="polite" aria-atomic="true">
    <p><strong>{current?.label || '推荐类别'}</strong><span>{current?.description || '只展示有真实题材依据的推荐。'}</span></p>
    {mode === 'preference_based' && <p className="rec-category-basis">根据你选择的题材推荐，当前不是基于完整高分画像的推荐。</p>}
  </div>
}
