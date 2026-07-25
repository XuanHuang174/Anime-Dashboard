import useScrollReveal from '../hooks/useScrollReveal.js'
import { getCoverSrc, getSeasonDetails, getTagClass } from '../utils/animeDisplay.js'

function getGridScoreColorClass(score) {
  if (score >= 9.9) {
    return 'text-transparent bg-clip-text bg-gradient-to-r from-pink-400 to-indigo-300 animate-pulse'
  }
  if (score >= 9.5) return 'text-rose-400'
  if (score >= 9.0) return 'text-amber-400'
  return 'text-white'
}

function AnimeGrid({ items, onOpenDetail }) {
  const containerRef = useScrollReveal(items)

  const handleMouseMove = (event) => {
    if (window.matchMedia('(hover: none) and (pointer: coarse)').matches) return

    const card = event.currentTarget
    const rect = card.getBoundingClientRect()
    card.style.setProperty('--mouse-x', `${event.clientX - rect.left}px`)
    card.style.setProperty('--mouse-y', `${event.clientY - rect.top}px`)
  }

  return (
    <div
      id="viewGridContainer"
      ref={containerRef}
      className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4 sm:gap-5 pb-8 transition-opacity duration-300"
    >
      {items.map((item) => {
        const scoreDisplay = item.score !== null ? item.score.toFixed(1) : '待定'
        const scoreColorClass = getGridScoreColorClass(item.score)
        const season = getSeasonDetails(item.period)
        const topTags = item.tags.slice(0, 3)

        return (
          <div
            key={item.id}
            onClick={() => onOpenDetail(item)}
            onMouseMove={handleMouseMove}
            className="animate-item group group/spotlight relative flex flex-col bg-white rounded-2xl overflow-hidden shadow-[0_4px_15px_rgb(0,0,0,0.03)] hover:shadow-2xl hover:shadow-indigo-500/15 hover:-translate-y-1.5 transition-all duration-300 cursor-pointer border border-slate-200/70 border-b-[4px] border-b-slate-300/80"
          >
            <div
              className="pointer-events-none absolute -inset-px opacity-0 transition duration-300 group-hover/spotlight:opacity-100 z-20"
              style={{
                background:
                  'radial-gradient(500px circle at var(--mouse-x, 0) var(--mouse-y, 0), rgba(99, 102, 241, 0.12), transparent 40%)',
              }}
            ></div>

            <div className="relative aspect-[3/4] overflow-hidden bg-slate-100 z-10 group/poster border-b border-slate-200/80">
              <img
                src={getCoverSrc(item)}
                className="w-full h-full object-cover group-hover/spotlight:scale-110 transition-transform duration-700 ease-out"
                alt="Cover"
              />

              <div className="absolute top-0 -left-[150%] w-[150%] h-full bg-gradient-to-r from-transparent via-white/50 to-transparent skew-x-[-25deg] group-hover/spotlight:translate-x-[200%] transition-transform duration-1000 ease-in-out z-10 pointer-events-none"></div>

              <div className="absolute top-2 right-2 px-2 py-1 rounded-lg backdrop-blur-md bg-black/40 text-white shadow-sm flex items-center gap-1 border border-white/10">
                <i className="fa-solid fa-star text-amber-400 text-[10px]"></i>
                <span className={`text-[11px] font-bold tracking-wider ${scoreColorClass}`}>
                  {scoreDisplay}
                </span>
              </div>

              <div className="absolute bottom-0 left-0 w-full pt-10 pb-2 px-3 bg-gradient-to-t from-slate-900 via-slate-900/60 to-transparent flex justify-between items-end opacity-90">
                <span className="text-white text-[11px] font-medium tracking-wider flex items-center gap-1.5">
                  {season.icon && <i className={`${season.icon} ${season.gridIconClass}`}></i>}
                  {item.period}
                </span>
                {item.statusLabel && (
                  <span className="rounded-md border border-white/20 bg-indigo-500/80 px-1.5 py-0.5 text-[10px] font-bold text-white backdrop-blur-sm">
                    {item.statusLabel}
                  </span>
                )}
              </div>
            </div>

            <div className="p-3.5 flex flex-col flex-1 justify-between gap-3 relative z-10">
              <div>
                <h3
                  className="font-bold text-slate-700 text-sm leading-tight line-clamp-2 group-hover:text-indigo-600 transition-colors"
                  title={item.title}
                >
                  {item.title}
                </h3>
                <p
                  className="text-[10px] text-slate-400 font-medium tracking-normal transition-all duration-500 ease-out group-hover:translate-x-2 group-hover:tracking-[0.05em] opacity-90 group-hover:opacity-100 line-clamp-1 mt-1"
                  title={item.japaneseTitle || ''}
                >
                  {item.japaneseTitle || ' '}
                </p>
              </div>
              <div className="flex flex-nowrap overflow-hidden gap-1 mt-auto relative pr-2">
                {topTags.map((tag) => (
                  <span
                    key={tag}
                    className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium ${getTagClass(tag, item.tagColors?.[tag]).replace('shadow-sm', '')} bg-opacity-70`}
                  >
                    {tag}
                  </span>
                ))}
                {item.tags.length > 3 && (
                  <span className="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-400">...</span>
                )}
                <div className="absolute right-0 top-0 bottom-0 w-4 bg-gradient-to-l from-white to-transparent pointer-events-none"></div>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default AnimeGrid
