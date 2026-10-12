import { useEffect, useState } from 'react'
import { getRecommendationCover } from '../../utils/recommendationPreview.js'

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
    <span>Bangumi <b>{Number.isFinite(item.score) ? item.score.toFixed(1) : '暂无评分'}</b></span></p>
}

export function RecommendationActions({ item, record, onDetail, onWant, detailLabel = '查看详情' }) {
  const labels = { want: '演示：已在想看', watched: '演示：已看过', watching: '演示：在看中', dropped: '演示：已搁置' }
  return <div className="rec-card-actions">
    <button type="button" className="rec-button rec-button--primary" onClick={() => onDetail(item)}
      aria-label={detailLabel === '查看详情' ? `查看${item.title}详情` : detailLabel}>{detailLabel} <span aria-hidden="true">↗</span></button>
    <button type="button" className="rec-button" disabled={Boolean(record)} onClick={() => onWant(item)}
      aria-label={record ? `${item.title}${labels[record.status] || '已有记录'}` : `将${item.title}加入演示想看`}>
      {record ? labels[record.status] || '演示：已有记录' : '+ 加入想看'}
    </button>
  </div>
}

export default function RecommendationCard({ item, hero = false, record, onDetail, onWant }) {
  return <article className={`rec-card ${hero ? 'rec-card--hero' : ''}`}>
    <RecommendationCover item={item} priority={hero} />
    <div className="rec-card-body">
      {hero && <span className="rec-eyebrow">THE EDITOR’S PICK <span className="rec-sticker">本期主推 · 演示</span></span>}
      <h3 className="rec-card-title" title={item.title}>{item.title}</h3>
      <RecommendationMeta item={item} />
      <p className="rec-card-reason" title={item.reason}><span aria-hidden="true">✧ </span>{item.reason}</p>
      {hero && <p className="rec-hero-synopsis">{item.synopsis}</p>}
      <div className="rec-card-tags" aria-label="作品标签">{item.tags?.slice(0, 2).map((tag) => <span key={tag}>{tag}</span>)}</div>
      <RecommendationActions item={item} record={record} onDetail={onDetail} onWant={onWant} />
    </div>
  </article>
}
