import { Link } from 'react-router-dom'
import { EXPLORATION_NAVIGATION, explorationNavigationLabel } from '../../utils/recommendationExploration.js'
import CreditsEntry from './CreditsEntry.jsx'

function ModeIcon({ mode }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {mode === 'personal' ? <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2-5.5-2.9L6.5 20l1-6.2L3 9.6l6.2-.9Z" />
      : mode === 'similar' ? <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /><path d="M14 6h4v4M10 18H6v-4M18 6l-6 6M6 18l6-6" /></>
        : <><path d="M3 3h8l10 10-8 8L3 11Z" /><circle cx="7.5" cy="7.5" r="1" /></>}
  </svg>
}

export default function RecommendationModeNavigation({ current, capabilities, similarSeed, tagsPath, credits, scope }) {
  return <nav className="rec-mode-nav" aria-label="番剧探索方式">
    {EXPLORATION_NAVIGATION.map(row => {
      const available = row.id === 'personal' || capabilities?.navigation?.find(item => item.id === row.id)?.available === true
      const label = explorationNavigationLabel(row.id, current)
      return available ? <Link key={row.id} className={`rec-mode-link${current === row.id ? ' is-current' : ''}`}
        aria-current={current === row.id ? 'page' : undefined}
        to={row.id === 'tags' && tagsPath ? tagsPath : row.path + (row.id === 'similar' && similarSeed ? `?seed=${similarSeed}` : '')}><ModeIcon mode={row.id} />{label}</Link>
        : <button key={row.id} type="button" className="rec-mode-link" disabled
          title={`${row.label}尚未开放`} aria-label={`${row.label}，尚未开放`}><ModeIcon mode={row.id} />{label}<small>尚未开放</small></button>
    })}
    <CreditsEntry capabilities={credits} scope={scope} navigation current={current === 'credits'} />
  </nav>
}
