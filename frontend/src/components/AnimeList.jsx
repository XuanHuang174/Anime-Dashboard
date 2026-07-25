import useScrollReveal from '../hooks/useScrollReveal.js'
import { getCoverSrc, getSeasonDetails, getTagClass } from '../utils/animeDisplay.js'

function getScoreColorClass(score) {
  if (score >= 9.8) {
    return 'bg-gradient-to-r from-indigo-600 via-purple-600 to-fuchsia-500 bg-clip-text text-transparent'
  }
  if (score >= 9.5) return 'text-orange-700'
  if (score >= 9.0) return 'text-orange-500'
  return 'text-slate-800'
}

function getStarCount(score) {
  if (score === null) return 0
  if (score >= 9.0) return 5
  if (score >= 8.0) return 4
  if (score >= 7.0) return 3
  if (score >= 6.0) return 2
  if (score > 0) return 1
  return 0
}

function getStarColorClass(score, isLit) {
  if (score >= 9.9 && isLit) {
    return 'text-transparent bg-clip-text bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500'
  }
  if (score >= 9.5 && isLit) return 'text-orange-600'
  if (isLit) return 'text-amber-400'
  return 'text-slate-200'
}

function AnimeList({ items, sortOrder, setSortOrder, onOpenDetail, startIndex = 0, theme = 'default' }) {
  const isMemphis = theme === 'memphis'
  const containerRef = useScrollReveal(items)

  const toggleSort = (type) => {
    if (type === 'date') {
      setSortOrder(sortOrder !== 'date-asc' ? 'date-asc' : 'date-desc')
      return
    }

    if (sortOrder !== 'score-desc' && sortOrder !== 'score-asc') {
      setSortOrder('score-desc')
    } else if (sortOrder === 'score-desc') {
      setSortOrder('score-asc')
    } else {
      setSortOrder('date-desc')
    }
  }

  const activeSortColor = isMemphis ? 'text-[#ff6b6b]' : 'text-indigo-600'
  const dateSortIcon = sortOrder === 'date-desc'
    ? `fa-solid fa-sort-down ${activeSortColor}`
    : sortOrder === 'date-asc'
      ? `fa-solid fa-sort-up ${activeSortColor}`
      : 'fa-solid fa-sort text-slate-300 group-hover:text-pink-500 transition-colors'
  const scoreSortIcon = sortOrder === 'score-desc'
    ? `fa-solid fa-sort-down ${activeSortColor}`
    : sortOrder === 'score-asc'
      ? `fa-solid fa-sort-up ${activeSortColor}`
      : 'fa-solid fa-sort text-slate-300 group-hover:text-pink-500 transition-colors'

  return (
    <div id="viewListContainer" className={`${isMemphis ? 'memphis-anime-list' : ''} pb-4`} ref={containerRef}>
      <div className="group/tablebox relative overflow-hidden rounded-[24px] border border-slate-200/80 bg-white shadow-[0_8px_30px_rgb(0,0,0,0.04)] ring-1 ring-slate-900/5">
        <div className="absolute top-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-white to-transparent opacity-80 z-10"></div>

        <div className="overflow-x-auto overflow-y-hidden custom-scrollbar">
          <table className="w-full text-left border-collapse">
            <thead className="hidden md:table-header-group">
              <tr className="bg-gradient-to-r from-slate-50 via-slate-100/50 to-slate-50 border-b-2 border-slate-200/80 text-slate-500 text-xs font-bold uppercase tracking-widest">
                <th className="py-5 px-6 text-center w-24">序号</th>
                <th className="py-5 px-6 w-28">海报画风</th>
                <th className="py-5 px-6">番剧名称（中/日）</th>
                <th
                  className="py-5 px-6 w-36 text-center cursor-pointer group hover:bg-slate-200/50 hover:text-pink-500 transition-colors select-none"
                  onClick={() => toggleSort('date')}
                  title="点击切换开播时间排序"
                >
                  <div className="flex items-center justify-center gap-1.5">
                    放送季度 <i id="sortIconDate" className={dateSortIcon}></i>
                  </div>
                </th>
                <th className="py-5 px-6 w-56">类型标签</th>
                <th
                  className="py-5 px-6 w-32 text-center cursor-pointer group hover:bg-slate-200/50 hover:text-pink-500 transition-colors select-none"
                  onClick={() => toggleSort('score')}
                  title="点击切换评分排序"
                >
                  <div className="flex items-center justify-center gap-1.5">
                    综合评分 <i id="sortIconScore" className={scoreSortIcon}></i>
                  </div>
                </th>
                <th className="py-5 px-6 w-36 text-center">操作</th>
              </tr>
            </thead>
            <tbody id="animeTableBody" className="divide-y divide-slate-100/80 text-sm">
              {items.map((item, index) => {
                const scoreDisplay = item.score !== null ? item.score.toFixed(1) : '待定'
                const scoreColorClass = getScoreColorClass(item.score)
                const stars = getStarCount(item.score)
                const season = getSeasonDetails(item.period)
                const listTopTags = item.tags.slice(0, 3)

                return (
                  <tr
                    key={item.id}
                    onClick={() => onOpenDetail(item)}
                    className={`${isMemphis ? 'memphis-entry' : ''} animate-item group relative flex cursor-pointer flex-wrap gap-x-4 border-b border-slate-100 bg-white p-4 transition-all duration-300 last:border-0 hover:!bg-indigo-50/80 md:table-row md:flex-none md:p-0 md:odd:bg-white md:even:bg-slate-50`}
                  >
                    <td className="hidden md:table-cell py-5 px-6 relative w-24">
                      <div className="absolute left-0 top-1/4 bottom-1/4 w-1 bg-indigo-500 rounded-r-full scale-y-0 group-hover:scale-y-100 opacity-0 group-hover:opacity-100 transition-all duration-300 origin-center"></div>
                      <div className="w-full flex items-center justify-center relative min-h-[40px]">
                        <span className={`${isMemphis ? 'text-black' : 'absolute text-slate-300 transition-all duration-500 ease-out group-hover:scale-75 group-hover:opacity-0'} pointer-events-none select-none text-2xl font-black`}>
                          {startIndex + index + 1}
                        </span>
                        {!isMemphis && (
                          <span className="pointer-events-none absolute scale-75 select-none bg-gradient-to-br from-indigo-500 to-purple-600 bg-clip-text text-3xl font-black text-transparent opacity-0 transition-all duration-500 ease-out group-hover:scale-100 group-hover:opacity-100">
                            {startIndex + index + 1}
                          </span>
                        )}
                      </div>
                    </td>

                    <td className="w-20 shrink-0 md:w-auto md:py-5 md:px-6">
                      <div className={`${isMemphis ? 'memphis-list-poster' : ''} group/poster relative aspect-[3/4] w-full cursor-pointer overflow-hidden rounded-lg bg-slate-100 shadow-sm transition-all duration-300 ring-indigo-300 ring-offset-1 hover:scale-105 hover:shadow-md hover:ring-2 md:h-20 md:w-16`}>
                        <img
                          src={getCoverSrc(item)}
                          className="w-full h-full object-cover group-hover/poster:scale-110 transition duration-500"
                          alt="Promo"
                        />
                        {!isMemphis && (
                          <div className="pointer-events-none absolute -left-[150%] top-0 z-10 h-full w-[150%] skew-x-[-25deg] bg-gradient-to-r from-transparent via-white/60 to-transparent transition-transform duration-700 ease-in-out group-hover/poster:translate-x-[180%]"></div>
                        )}
                      </div>
                    </td>

                    <td className="flex-1 min-w-[50%] md:w-auto md:py-5 md:px-6 cursor-pointer">
                      <div className="flex flex-col justify-center h-full md:block pr-16 md:pr-0">
                        <span className="font-extrabold text-slate-700 text-base md:text-lg group-hover:text-indigo-600 transition-all duration-300 ease-out group-hover:translate-x-1 line-clamp-2 md:line-clamp-1">
                          {item.title}
                        </span>
                        <span className="text-[10px] md:text-xs text-slate-400 mt-1 font-medium tracking-normal transition-all duration-500 ease-out group-hover:translate-x-3.5 group-hover:tracking-[0.05em] opacity-90 group-hover:opacity-100 line-clamp-1">
                          {item.japaneseTitle || ' '}
                        </span>

                        <div className="flex md:hidden flex-wrap gap-1.5 mt-2.5">
                          {item.statusLabel && (
                            <span className={`${isMemphis ? `memphis-status memphis-status-${item.status}` : 'border border-indigo-200 bg-indigo-50 text-indigo-600'} inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-bold`}>
                              {item.statusLabel}
                            </span>
                          )}
                          <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold border ${season.periodStyle}`}>
                            {season.icon && <i className={`${season.icon} ${season.mobileIconClass}`}></i>}
                            {item.period}
                          </span>
                          {listTopTags.map((tag, tagIndex) => (
                            <span
                              key={tag}
                              className={`${isMemphis ? `memphis-sticker ${tagIndex % 2 ? 'rotate-2' : '-rotate-2'}` : ''} px-1.5 py-0.5 rounded text-[10px] font-medium ${getTagClass(tag, item.tagColors?.[tag]).replace('shadow-sm', '')}`}
                            >
                              {tag}
                            </span>
                          ))}
                          {item.tags.length > 3 && (
                            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-400">...</span>
                          )}
                        </div>
                      </div>
                    </td>

                    <td className="hidden md:table-cell py-5 px-6 text-center">
                      <span className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold border shadow-sm transition-all duration-300 hover:shadow-md ${season.periodStyle}`}>
                        {season.icon && <i className={`${season.icon} ${season.listIconClass}`}></i>}
                        {item.period}
                      </span>
                    </td>

                    <td className="hidden md:table-cell py-5 px-6">
                      <div className="flex flex-wrap gap-1.5 max-w-[220px]">
                        {item.statusLabel && (
                          <span className={`${isMemphis ? `memphis-status memphis-status-${item.status}` : 'border border-indigo-200 bg-indigo-50 text-indigo-600'} rounded-md px-2.5 py-1 text-xs font-bold`}>
                            {item.statusLabel}
                          </span>
                        )}
                        {item.tags.map((tag, tagIndex) => (
                          <span key={tag} className={`${isMemphis ? `memphis-sticker ${tagIndex % 2 ? 'rotate-2' : '-rotate-2'}` : ''} px-2.5 py-1 rounded-md text-xs font-medium ${getTagClass(tag, item.tagColors?.[tag])}`}>
                            {tag}
                          </span>
                        ))}
                      </div>
                    </td>

                    <td className="absolute top-4 right-4 md:relative md:top-auto md:right-auto md:py-5 md:px-6 text-center">
                      <div className="flex flex-col items-end md:items-center group-hover:scale-105 transition-transform duration-300">
                        <div className="flex items-baseline gap-0.5">
                          <span className={`font-black ${scoreColorClass} text-lg md:text-xl drop-shadow-sm`}>
                            {scoreDisplay}
                          </span>
                          {item.score !== null && (
                            <span className="hidden md:inline text-[10px] text-slate-400 font-semibold">/10</span>
                          )}
                        </div>
                        <div className="hidden md:flex gap-0.5 mt-1">
                          {item.score === null ? (
                            <span className="text-xs text-slate-400">期待开播</span>
                          ) : (
                            Array.from({ length: 5 }, (_, starIndex) => {
                              const isLit = starIndex + 1 <= stars
                              return (
                                <i
                                  key={starIndex}
                                  className={`${isLit ? 'fa-solid' : 'fa-regular'} fa-star ${getStarColorClass(item.score, isLit)} text-xs`}
                                ></i>
                              )
                            })
                          )}
                        </div>
                      </div>
                    </td>

                    <td className="hidden md:table-cell py-5 px-6 text-center">
                      <button
                        type="button"
                        className="inline-flex items-center gap-1.5 bg-white hover:bg-indigo-500 text-indigo-500 hover:text-white font-bold px-4 py-2 rounded-xl text-xs border border-indigo-100 shadow-sm hover:shadow-md hover:shadow-indigo-200/50 hover:-translate-y-0.5 active:translate-y-0 active:shadow-sm transition-all duration-300"
                      >
                        <i className="fa-solid fa-layer-group"></i> 详情
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

export default AnimeList
