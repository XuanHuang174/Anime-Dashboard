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

export default function TagCleanupPreview({ item, checked, selectable, disabled, onToggle }) {
  const [expanded, setExpanded] = useState(false)
  const conflict = ['conflict', 'undo_conflict'].includes(item.status)
  const compact = !item.changes.length
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
          : <Tag name={`去重：${change.from}`} />}
      </span>)}{item.changes.length > 6 && <button type="button" className="tag-cleanup-more-changes" onClick={() => setExpanded(true)}>另 {item.changes.length - 6} 项变化，展开查看</button>}</div>}
    <div className="tag-cleanup-card-meta">
      {item.protected.length > 0 && <span className="tag-cleanup-protected">▣ 已保护 {item.protected.length} 个标签</span>}
      {item.uncertain.length > 0 && <span>未知保留 {item.uncertain.length} 个 · 无需额外处理</span>}
    </div>
    {item.message && <p className="tag-cleanup-item-message" role="status">{item.message}</p>}
    <CleanupDisclosure title={expanded ? '收起对比与保护说明' : '查看完整对比与保护说明'} open={expanded} onToggle={() => setExpanded(value => !value)}>
      <div className="tag-cleanup-comparison">{[['before', '整理前'], ['after', '整理后方案']].map(([side, label]) => <div key={side}>
        <h4>{label}</h4><div className="tag-cleanup-chips">{item[side].map((tag, i) => <Tag key={i} name={tag} tone={cleanupTagTone(item, tag, side)} />)}{!item[side].length && <span>暂无标签</span>}</div>
      </div>)}</div>
      {item.changes.length > 0 && <ul className="tag-cleanup-reasons">{item.changes.map((change, i) => <li key={i}>{change.kind === 'normalize' ? `规范名称：${change.from} → ${change.to}` : change.kind === 'remove' ? `移除：${change.from}（${change.reason}）` : change.kind === 'add' ? `补充：${change.to}（${change.reason}）` : `重复项合并：${change.from}`}</li>)}</ul>}
      {item.protected.length > 0 && <div className="tag-cleanup-protection-info"><p><strong>以下标签已保护</strong>：{item.protected.join('、')}</p><p>个人标记、常用预设、已保存快捷筛选引用、已有配色及作品格式等均默认保护。本次不会改写这些名称。</p></div>}
      {item.uncertain.length > 0 && <p>用途未知，默认保留：{item.uncertain.join('、')}。无需确认才能继续。</p>}
      <p className="tag-cleanup-source">{item.source_note}。此处展示本次整理方案快照，后续编辑请以手账为准。</p>
    </CleanupDisclosure>
  </article>
}
