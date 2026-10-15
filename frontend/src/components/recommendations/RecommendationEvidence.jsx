import { recommendationEvidence, recommendationReason } from '../../utils/recommendationExplanation.js'

// Native disclosure: keyboard/touch accessible, independent per card, no requests/writes.
export default function RecommendationEvidence({ item, expanded = false }) {
  const evidence = recommendationEvidence(item)
  const reason = recommendationReason(item)
  return <div className="rec-explanation">
    <p className="rec-card-reason" title={reason}><span aria-hidden="true">✧ </span>{reason}</p>
    <details key={JSON.stringify([item.bangumiId, evidence])} className="rec-evidence" open={expanded || undefined}>
      <summary aria-label={`为什么推荐《${item.title}》？`}>为什么推荐？<span aria-hidden="true">＋</span></summary>
      <div className="rec-evidence-body">
        {!evidence ? <p className="rec-evidence-unavailable">当前没有可展示的完整证据。请刷新推荐后重试，不影响查看作品或手账中的原记录。</p>
          : <>
            <span className="rec-evidence-label">{evidence.kind === 'rated_work' ? '来自高分作品' : '来自明确偏好'}</span>
            {evidence.kind === 'rated_work' ? <dl className="rec-evidence-facts">
              <div><dt>来源作品</dt><dd>《{evidence.source.title}》</dd></div>
              <div><dt>你的个人评分</dt><dd>{evidence.source.score.toFixed(1)} / 10</dd></div>
              <div><dt>实际共有题材</dt><dd className="rec-evidence-tags">{evidence.tags.map(tag => <span key={tag}>{tag}</span>)}</dd></div>
            </dl> : <>
              <p>根据你选择的题材推荐。</p>
              <div className="rec-evidence-tags" aria-label="明确选择且与本作共有的题材">{evidence.tags.map(tag => <span key={tag}>{tag}</span>)}</div>
              <p>这条依据来自你明确选择的题材，而非高分作品画像；不会据此宣称已形成完整画像。</p>
            </>}
            <p className="rec-evidence-footnote">共同题材用于解释关联，不是喜欢概率。Bangumi 评分是公共评价，不是你的个人评分。</p>
          </>}
      </div>
    </details>
  </div>
}
