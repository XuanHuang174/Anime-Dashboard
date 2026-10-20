import { recommendationEvidence, recommendationReason } from '../../utils/recommendationExplanation.js'
import { RECOMMENDATION_COPY as COPY } from '../../utils/recommendationCopy.js'
import { RecommendationPersonalScore } from './RecommendationScore.jsx'

// Native disclosure: keyboard/touch accessible; only explicit recovery reloads data.
export default function RecommendationEvidence({ item, expanded = false, onReload }) {
  const evidence = recommendationEvidence(item)
  const reason = recommendationReason(item)
  return <div className="rec-explanation">
    <p className="rec-card-reason" title={reason}><span aria-hidden="true">✧ </span>{reason}</p>
    <details key={JSON.stringify([item.bangumiId, evidence])} className="rec-evidence" open={expanded || undefined}>
      <summary aria-label={`为什么推荐《${item.title}》？`}>为什么推荐？<span aria-hidden="true">＋</span></summary>
      <div className="rec-evidence-body">
        {!evidence ? <div className="rec-evidence-unavailable"><p>{COPY.evidenceRecovery}</p>
          {onReload ? <button type="button" className="rec-text-button" onClick={onReload}>{COPY.reloadEvidence}</button>
            : <a className="rec-text-button" href="/recommendations">{COPY.reloadEvidence}</a>}</div>
          : <>
            <span className="rec-evidence-label">{evidence.kind === 'rated_work' ? COPY.ratedEvidence : COPY.explicitEvidence}</span>
            {evidence.kind === 'rated_work' ? <>
              <RecommendationPersonalScore title={evidence.source.title} score={evidence.source.score} />
              <p className="rec-evidence-policy">取样依据：已看过且个人评分 ≥ {evidence.minimumScore} 分的有效作品。</p>
              <dl className="rec-evidence-facts">
                <div><dt>来源作品</dt><dd>《{evidence.source.title}》</dd></div>
                <div><dt>实际共有题材</dt><dd className="rec-evidence-tags">{evidence.tags.map(tag => <span key={tag}>{tag}</span>)}</dd></div>
              </dl></> : <>
              <p>根据你选择的题材推荐。</p>
              <div className="rec-evidence-tags" aria-label="明确选择且与本作共有的题材">{evidence.tags.map(tag => <span key={tag}>{tag}</span>)}</div>
              <p>这条依据来自你明确选择的题材，而非作品画像；不会据此宣称已形成完整画像。</p>
            </>}
            <p className="rec-evidence-footnote">{COPY.evidenceBoundary}</p>
          </>}
      </div>
    </details>
  </div>
}
