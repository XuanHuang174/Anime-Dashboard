import { Link } from 'react-router-dom'
import { EXPLORATION_NAVIGATION, explorationNavigationLabel } from '../../utils/recommendationExploration.js'

export default function RecommendationModeNavigation({ current, capabilities, similarSeed }) {
  return <nav className="rec-mode-nav" aria-label="番剧探索方式">
    {EXPLORATION_NAVIGATION.map(row => {
      const available = row.id === 'personal' || capabilities?.navigation?.find(item => item.id === row.id)?.available === true
      const label = explorationNavigationLabel(row.id, current)
      return available ? <Link key={row.id} className={`rec-mode-link${current === row.id ? ' is-current' : ''}`}
        aria-current={current === row.id ? 'page' : undefined}
        to={row.path + (row.id === 'similar' && similarSeed ? `?seed=${similarSeed}` : '')}>{label}</Link>
        : <button key={row.id} type="button" className="rec-mode-link" disabled
          title={`${row.label}尚未开放`} aria-label={`${row.label}，尚未开放`}>{label}<small>尚未开放</small></button>
    })}
  </nav>
}
