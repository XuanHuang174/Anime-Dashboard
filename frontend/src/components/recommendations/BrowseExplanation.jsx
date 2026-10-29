import { useId } from 'react'
import '../../styles/browse-explanation.css'

export default function BrowseExplanation({ summary, open, onToggle, children }) {
  const id = useId()
  return <div className="browse-explanation">
    <div className="browse-explanation-row"><div className="browse-explanation-summary">{summary}</div>
      <button type="button" className="browse-explanation-toggle" aria-expanded={open} aria-controls={id} onClick={() => onToggle(!open)}>
        <span aria-hidden="true">{open ? '▾' : '▸'}</span> 浏览说明
      </button>
    </div>
    {open && <div id={id} className="browse-explanation-body">{children}</div>}
  </div>
}
