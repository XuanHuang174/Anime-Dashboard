import { createContext, useContext } from 'react'
import { useNavigate } from 'react-router-dom'
import { emptyTagDraft, tagQuery, tagQueryPath } from '../../utils/explorationTagQuery.js'

export const ExplorationTopicsContext = createContext([])
function TagButton({ tag, allowed, beforeNavigate }) {
  const navigate = useNavigate()
  return <button type="button" title={`探索这个题材：${tag}`} aria-label={`探索这个题材：${tag}`}
    onClick={() => { beforeNavigate?.(); navigate(tagQueryPath(tagQuery({ ...emptyTagDraft(), include: [tag] }, allowed))) }}>{tag} ↗</button>
}
export default function ExplorationTagChips({ tags = [], beforeNavigate, className = 'rec-card-tags', standardTags, inline = false }) {
  const context = useContext(ExplorationTopicsContext), allowed = standardTags ?? context
  const Container = inline ? 'span' : 'div'
  return <Container className={className} aria-label="作品标签">{tags.map(tag => allowed.includes(tag)
    ? <TagButton key={tag} tag={tag} allowed={allowed} beforeNavigate={beforeNavigate} />
    : <span key={tag}>{tag}</span>)}</Container>
}
