import { useId, useState } from 'react'
import { cleanupItemLabels, cleanupTagTone } from '../utils/tagCleanupPresentation.js'

export function CleanupDisclosure({ title, children, open, onToggle, className = '' }) {
  const id = useId()
  return <div className={`tag-cleanup-disclosure ${className}`} data-open={open}>
    <button type="button" className="tag-cleanup-disclosure-trigger" aria-expanded={open} aria-controls={id} onClick={onToggle}>
      <span>{title}</span><span aria-hidden="true" className="tag-cleanup-chevron">⌄</span>
    </button>
    <div id={id} className="tag-cleanup-disclosure-panel" aria-hidden={!open} inert={!open}>
      <div className="tag-cleanup-disclosure-inner">{children}</div>
    </div>
  </div>
}

const signs = { protected: '▣', add: '＋', remove: '－', normalize: '↔', keep: '' }
const toneLabels = { protected: '已保护', add: '新增', remove: '移除', normalize: '规范名称', keep: '保留' }
function Tag({ name, tone = 'keep' }) {
  return <span className={`tag-cleanup-chip is-${tone}`}><span className="tag-cleanup-sr">{toneLabels[tone]}：</span><span aria-hidden="true">{signs[tone]}</span>{name}</span>
}

export default function TagCleanupPreview({ item, checked, selectable, disabled, onToggle, rebuilding = false, canReview = false, onDecision, onReviewIdentity }) {
  const [expanded, setExpanded] = useState(false)
  const conflict = ['conflict', 'undo_conflict'].includes(item.status)
  const compact = !item.changes.length
  const details = item.details || {}
  return <article className={`tag-cleanup-card${selectable && checked ? ' is-selected' : ''}${conflict ? ' is-conflict' : ''}${compact ? ' is-compact' : ''}`}>
    <div className="tag-cleanup-item-heading">
      <label>{selectable && <input type="checkbox" aria-label={`整理 ${item.title}`} disabled={disabled} checked={checked} onChange={onToggle} />}<strong>{item.title || '已无法读取的记录'}</strong></label>
      <span className={`tag-cleanup-item-status is-${item.status}`}>{cleanupItemLabels[item.status] || item.status}</span>
    </div>
    {compact ? <div className="tag-cleanup-chips tag-cleanup-kept"><small>原标签保留</small>{item.after.slice(0, 6).map((tag, i) => <Tag key={i} name={tag} tone={cleanupTagTone(item, tag, 'after')} />)}{item.after.length > 6 && <small>另 {item.after.length - 6} 个</small>}</div>
      : <div className="tag-cleanup-changes" aria-label="主要标签变化">{item.changes.slice(0, 6).map((change, i) => <span key={i} title={change.reason || ''}>
        {change.kind === 'normalize' ? <Tag name={`${change.from} → ${change.to}`} tone="normalize" />
          : change.kind === 'remove' ? <Tag name={change.from} tone="remove" />
          : change.kind === 'add' ? <Tag name={change.to} tone="add" />
          : change.kind === 'move' ? <Tag name={`个人标记：${change.from}`} tone="protected" />
          : <Tag name={`去重：${change.from}`} />}
      </span>)}{item.changes.length > 6 && <button type="button" className="tag-cleanup-more-changes" onClick={() => setExpanded(true)}>另 {item.changes.length - 6} 项变化，展开查看</button>}</div>}
    <div className="tag-cleanup-card-meta">
      {item.protected.length > 0 && <span className="tag-cleanup-protected">▣ 已保护 {item.protected.length} 个标签</span>}
      {item.uncertain.length > 0 && <span>{rebuilding ? `待取舍 ${item.uncertain.length} 个` : `未知保留 ${item.uncertain.length} 个 · 无需额外处理`}</span>}
      {item.personal_removed > 0 && <span className="tag-cleanup-personal-removal">拟移除个人标记 {item.personal_removed} 个</span>}
    </div>
    {item.status === 'no_source' && <p className="tag-cleanup-source-warning">{item.source_note}，原标签不变。{onReviewIdentity && <button type="button" disabled={disabled} onClick={() => onReviewIdentity(item.record_id)}>核对此作品</button>}</p>}
    {item.message && <p className="tag-cleanup-item-message" role="status">{item.message}</p>}
    <CleanupDisclosure title={expanded ? '收起对比与保护说明' : '查看完整对比与保护说明'} open={expanded} onToggle={() => setExpanded(value => !value)}>
      <div className="tag-cleanup-comparison">{[['before', '整理前'], ['after', '整理后方案']].map(([side, label]) => <div key={side}>
        <h4>{label}</h4><div className="tag-cleanup-chips">{item[side].map((tag, i) => <Tag key={i} name={tag} tone={cleanupTagTone(item, tag, side)} />)}{!item[side].length && <span>暂无标签</span>}</div>
        {details[`${side}_custom`]?.length > 0 && <><h4 className="tag-cleanup-personal-title">个人标记</h4><div className="tag-cleanup-chips">{details[`${side}_custom`].map((tag, i) => <Tag key={i} name={tag} tone={side === 'before' && !details.after_custom?.includes(tag) ? 'remove' : 'protected'} />)}</div></>}
      </div>)}</div>
      {item.changes.length > 0 && <ul className="tag-cleanup-reasons">{item.changes.map((change, i) => <li key={i}>{change.kind === 'normalize' ? `规范名称：${change.from} → ${change.to}` : change.kind === 'remove' ? `移除：${change.from}（${change.reason}）` : change.kind === 'add' ? `补充：${change.to}（${change.reason}）` : change.kind === 'move' ? `保留为个人标记：${change.from}` : `重复项合并：${change.from}`}</li>)}</ul>}
      {item.protected.length > 0 && <div className="tag-cleanup-protection-info"><p><strong>以下标签已保护</strong>：{item.protected.join('、')}</p><p>{rebuilding ? '以上为本方案保留的分类与标记；下方列出具体依据。个人预设及快捷筛选定义不会被修改。' : '个人标记、常用预设、已保存快捷筛选引用、已有配色及作品格式等均默认保护。本次不会改写这些名称。'}</p></div>}
      {!rebuilding && item.uncertain.length > 0 && <p>用途未知，默认保留：{item.uncertain.join('、')}。无需确认才能继续。</p>}
      {rebuilding && details.genres?.length > 0 && <p className="tag-cleanup-standard-genres"><strong>标准资料提供：</strong>{details.genres.join('、')}<span>保留的细分别名会参与题材识别，展示时避免重复。</span></p>}
      {details.candidates?.length > 0 && <div className="tag-cleanup-candidates" aria-label={`${item.title}的标签保留依据`}><h4>{rebuilding ? '旧标签取舍' : '逐标签保留依据'}</h4>{details.candidates.map(row => <div key={row.name} className={`tag-cleanup-candidate is-${row.action}`}><div><strong>{row.name}</strong><small>{row.reasons.join(' · ')}{rebuilding && !row.fixed ? ` · ${row.reason}` : ''}</small></div>{rebuilding && !row.fixed ? <select aria-label={`${item.title}：${row.name}的处理方式`} value={row.action} disabled={disabled || !canReview} onChange={event => onDecision?.(item, row.name, event.target.value)}><option value="keep">保留原位</option><option value="personal">保留为个人标记</option><option value="remove">移除</option></select> : <span>{rebuilding ? '固定保留' : '保留'}</span>}</div>)}</div>}
      <p className="tag-cleanup-source">{item.source_note}。此处展示本次整理方案快照，后续编辑请以手账为准。</p>
    </CleanupDisclosure>
  </article>
}
