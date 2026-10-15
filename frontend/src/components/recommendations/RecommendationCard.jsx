import { useEffect, useState } from 'react'
import { getRecommendationCover } from '../../utils/recommendations.js'
import { wantButtonState } from '../../utils/recommendationWant.js'
import RecommendationEvidence from './RecommendationEvidence.jsx'
import { RecommendationPublicScore } from './RecommendationScore.jsx'

export function RecommendationCover({ item, priority = false }) {
  const safeSrc = getRecommendationCover(item.coverUrl)
  const [failedSrc, setFailedSrc] = useState(null)
  useEffect(() => setFailedSrc(null), [safeSrc])
  return (
    <div className="rec-cover">
      {safeSrc && failedSrc !== safeSrc
        ? <img src={safeSrc} alt={`${item.title}封面`} loading={priority ? 'eager' : 'lazy'} decoding="async"
          onError={() => setFailedSrc(safeSrc)} />
        : <div className="rec-cover-placeholder" role="img" aria-label={`${item.title}暂无托管封面`}>
          <span className="rec-cover-symbol" aria-hidden="true">✦</span>
          <span>STORY TO BE FOUND</span><strong>故事还在，<br />心动不缺席。</strong><small>暂无托管封面</small>
        </div>}
    </div>
  )
}

export function RecommendationMeta({ item }) {
  return <p className="rec-meta"><span>{item.year || '年份未知'}</span><span>{item.type || '类型未知'}</span>
    <RecommendationPublicScore score={item.score} /></p>
}

export function RecommendationActions({ item, onDetail, detailLabel = '查看详情', onWant, operation, onIdentity }) {
  const button = wantButtonState(item, operation)
  return <><div className="rec-card-actions">
    <button type="button" className="rec-button rec-button--primary" onClick={() => onDetail(item)}
      aria-label={detailLabel === '查看详情' ? `查看${item.title}详情` : detailLabel}>{detailLabel} <span aria-hidden="true">↗</span></button>
    <button type="button" className="rec-button" disabled={button.disabled || (!onWant && !button.confirm)}
      onClick={() => button.confirm ? onIdentity?.() : onWant?.(item)}
      aria-label={`${item.title}：${button.label}`}>
      {button.label}
    </button>
  </div>{operation?.error && <p className="rec-inline-error rec-want-error" role="alert">{operation.error.message}</p>}
  {operation?.syncWarning && <p className="rec-muted" role="status">{operation.syncWarning}</p>}</>
}

export default function RecommendationCard({ item, hero = false, onDetail, onWant, operation, onIdentity, onReload }) {
  return <article className={`rec-card ${hero ? 'rec-card--hero' : ''}`}>
    <RecommendationCover item={item} priority={hero} />
    <div className="rec-card-body">
      {hero && <span className="rec-eyebrow">YOUR NEXT STORY <span className="rec-sticker">本期主推</span></span>}
      <h3 className="rec-card-title" title={item.title}>{item.title}</h3>
      <RecommendationMeta item={item} />
      <RecommendationEvidence item={item} onReload={onReload} />
      {hero && <p className="rec-hero-synopsis">{item.synopsis}</p>}
      <div className="rec-card-tags" aria-label="作品标签">{item.tags?.slice(0, 2).map((tag) => <span key={tag}>{tag}</span>)}</div>
      <RecommendationActions item={item} onDetail={onDetail} onWant={onWant} operation={operation} onIdentity={onIdentity} />
    </div>
  </article>
}
