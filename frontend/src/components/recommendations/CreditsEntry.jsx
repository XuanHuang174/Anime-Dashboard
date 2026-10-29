import { Link, useLocation } from 'react-router-dom'
import { creditsAvailable, creditsNavigationState } from '../../utils/recommendationCredits.js'

export function CreditsIcon({ kind = 'voice_actor' }) {
  return <svg aria-hidden="true" focusable="false" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    {kind === 'studio' ? <><path d="M3 21V9l6 3V7l6 4V3h5v18Z" /><path d="M7 17h1m4 0h1m4 0h1" /></>
      : <><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" /></>}
  </svg>
}
export default function CreditsEntry({ capabilities, scope = 'safe', navigation = false, current = false }) {
  const location = useLocation()
  return creditsAvailable(capabilities) ? <Link className={navigation ? `rec-mode-link rec-mode-link--route${current ? ' is-current' : ''}` : 'rec-button credits-entry'} to="/recommendations/credits"
    aria-current={current ? 'page' : undefined} state={creditsNavigationState(location, scope)}><CreditsIcon />人物与制作{current ? ' · 当前' : <span aria-hidden="true">↗</span>}</Link> : null
}
