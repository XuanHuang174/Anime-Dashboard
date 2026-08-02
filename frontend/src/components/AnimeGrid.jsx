import useScrollReveal from '../hooks/useScrollReveal.js'
import { getCoverSrc, getSeasonDetails, getTagClass } from '../utils/animeDisplay.js'
import { AddAnimeGridPlaceholder } from './AddAnimePlaceholder.jsx'

function getGridScoreColorClass(score, isMemphis = false) {
  if (score >= 9.9) {
    return 'bg-gradient-to-r from-indigo-600 via-purple-600 to-fuchsia-500 bg-clip-text text-transparent'
  }
  if (score >= 9.5) return 'text-red-600'
  if (score >= 9.0) return 'text-orange-500'
  return isMemphis ? 'text-slate-800' : 'text-white'
}

function AnimeGrid({
  items,
  onOpenDetail,
  theme = 'default',
  editable = false,
  showAddPlaceholder = false,
  onAddAnime,
}) {
  const isMemphis = theme === 'memphis'
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
      className={`${isMemphis ? 'memphis-anime-grid' : ''} grid grid-cols-2 gap-4 pb-8 sm:grid-cols-3 sm:gap-5 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6`}
    >
      {items.map((item) => {
        const scoreDisplay = item.score !== null ? item.score.toFixed(1) : '待定'
        const scoreColorClass = getGridScoreColorClass(item.score, isMemphis)
        const season = getSeasonDetails(item.period)
        const topTags = item.tags.slice(0, 3)

        return (
          <div
            key={item.id}
            onClick={() => onOpenDetail(item)}
            onMouseMove={handleMouseMove}
            className={`${isMemphis ? 'memphis-entry' : ''} animate-item group group/spotlight relative flex cursor-pointer flex-col overflow-hidden rounded-2xl border border-slate-200/70 border-b-[4px] border-b-slate-300/80 bg-white shadow-[0_4px_15px_rgb(0,0,0,0.03)] transition-all duration-300 hover:-translate-y-1.5 hover:shadow-2xl hover:shadow-indigo-500/15`}
          >
            {isMemphis && editable && (
              <span
                className="memphis-edit-action pointer-events-none absolute left-2 top-2 z-30 flex h-8 w-8 items-center justify-center"
                aria-hidden="true"
              >
                <i className="fa-solid fa-pen-to-square text-xs"></i>
              </span>
            )}

            {!isMemphis && (
              <div
                className="pointer-events-none absolute -inset-px z-20 opacity-0 transition duration-300 group-hover/spotlight:opacity-100"
                style={{
                  background:
                    'radial-gradient(500px circle at var(--mouse-x, 0) var(--mouse-y, 0), rgba(99, 102, 241, 0.12), transparent 40%)',
                }}
              ></div>
            )}

            <div className="relative aspect-[3/4] overflow-hidden bg-slate-100 z-10 group/poster border-b border-slate-200/80">
              <img
                src={getCoverSrc(item)}
                loading="lazy"
                decoding="async"
                className={`${isMemphis ? '' : 'transition-transform duration-700 ease-out group-hover/spotlight:scale-110'} h-full w-full object-cover`}
                alt="Cover"
              />

              {!isMemphis && (
                <div className="pointer-events-none absolute -left-[150%] top-0 z-10 h-full w-[150%] skew-x-[-25deg] bg-gradient-to-r from-transparent via-white/50 to-transparent transition-transform duration-1000 ease-in-out group-hover/spotlight:translate-x-[200%]"></div>
              )}

              <div className={`${isMemphis ? 'memphis-grid-score' : 'border-white/10 bg-black/40 text-white shadow-sm backdrop-blur-md'} absolute right-2 top-2 flex items-center gap-1 rounded-lg border px-2 py-1`}>
                <i className="fa-solid fa-star text-amber-400 text-[10px]"></i>
                <span className={`text-[11px] font-bold tracking-wider ${scoreColorClass}`}>
                  {scoreDisplay}
                </span>
              </div>

              {!isMemphis && (
                <div className="absolute bottom-0 left-0 flex w-full items-end justify-between bg-gradient-to-t from-slate-900 via-slate-900/60 to-transparent px-3 pb-2 pt-10 opacity-90">
                  <span className="flex items-center gap-1.5 text-[11px] font-medium tracking-wider text-white">
                    {season.icon && <i className={`${season.icon} ${season.gridIconClass}`}></i>}
                    {item.period}
                  </span>
                  {item.statusLabel && (
                    <span className="rounded-md border border-white/20 bg-indigo-500/80 px-1.5 py-0.5 text-[10px] font-bold text-white backdrop-blur-sm">
                      {item.statusLabel}
                    </span>
                  )}
                </div>
              )}
            </div>

            {isMemphis && (
              <div className="memphis-grid-meta flex items-center justify-between gap-1.5 border-b-[3px] border-black bg-[#fff5ee] px-2.5 py-1">
                <span className="flex items-center gap-1 text-[9px] font-black">
                  {season.icon && <i className={`${season.icon} ${season.gridIconClass}`}></i>}
                  {item.period}
                </span>
                {item.statusLabel && (
                  <span className={`memphis-status memphis-status-${item.status} px-1.5 py-0.5 text-[9px] font-black`}>
                    {item.statusLabel}
                  </span>
                )}
              </div>
            )}

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
              <div className="relative mt-auto flex min-w-0 items-center gap-1">
                <div className="flex min-w-0 flex-1 flex-nowrap items-center gap-1 overflow-hidden">
                  {topTags.map((tag, tagIndex) => (
                    <span
                      key={tag}
                      title={tag}
                      className={`${isMemphis ? `memphis-sticker ${tagIndex % 2 ? 'rotate-2' : '-rotate-2'}` : ''} min-w-0 max-w-[5.25rem] flex-[0_1_auto] truncate px-1.5 py-0.5 rounded text-[10px] font-medium ${getTagClass(tag, item.tagColors?.[tag]).replace('shadow-sm', '')} bg-opacity-70`}
                    >
                      {tag}
                    </span>
                  ))}
                </div>
                {item.tags.length > topTags.length && (
                  <span className="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-400">...</span>
                )}
                {!isMemphis && (
                  <div className="pointer-events-none absolute bottom-0 right-0 top-0 w-4 bg-gradient-to-l from-white to-transparent"></div>
                )}
              </div>
            </div>
          </div>
        )
      })}
      {showAddPlaceholder && <AddAnimeGridPlaceholder onAddAnime={onAddAnime} />}
    </div>
  )
}

export default AnimeGrid
