import { useMemo } from 'react'
import { countJournalStatuses } from '../utils/journalStats'

function StatsSummary({ db = [], statsData = null, theme = 'default' }) {
  const calculatedStats = useMemo(() => {
    const hasScoreList = db.filter((item) => item.score !== null)
    const completedCount = db.filter((item) => item.status === 'completed').length
    const sumScore = hasScoreList.reduce((acc, curr) => acc + curr.score, 0)

    return {
      completedCount,
      avg: hasScoreList.length > 0 ? (sumScore / hasScoreList.length).toFixed(2) : 'N/A',
      movieCount: db.filter(
        (item) => item.title.includes('剧场版') || item.tags.includes('剧场版'),
      ).length,
      ovaCount: db.filter(
        (item) => item.title.toUpperCase().includes('OVA') || item.tags.includes('OVA'),
      ).length,
      shortCount: db.filter((item) => item.tags.includes('泡面番')).length,
      masterpieceCount: db.filter(
        (item) => item.score !== null && item.score >= 9.5,
      ).length,
      ...countJournalStatuses(db),
    }
  }, [db])

  const stats = statsData
    ? {
        completedCount: statsData.completed_count ?? 0,
        watchingCount: statsData.watching_count ?? calculatedStats.watchingCount,
        avg: statsData.average_score ?? 'N/A',
        movieCount: statsData.movie_count ?? 0,
        ovaCount: statsData.ova_count ?? 0,
        shortCount: statsData.short_count ?? 0,
        masterpieceCount: statsData.masterpiece_count ?? 0,
        pendingCount: statsData.pending_count ?? 0,
      }
    : calculatedStats

  const cards = [
    {
      label: '追完的番剧数量',
      value: stats.completedCount,
      note: `${stats.watchingCount} 部在看 / ${stats.pendingCount} 部待定`,
      icon: 'fa-circle-check',
      color: 'bg-[#ffe66d]',
      valueClass: 'text-black',
      valueBackdrop: true,
      defaultValueClass: 'text-slate-800',
      id: 'statTotal',
    },
    {
      label: '平均评分',
      value: stats.avg,
      note: '仅统计已评分作品',
      icon: 'fa-chart-line',
      color: 'bg-[#ff6b6b]',
      valueClass: 'text-[#ff4f87]',
      valueBackdrop: true,
      defaultValueClass: 'text-pink-500',
      id: 'statAvgScore',
    },
    {
      label: '剧场版',
      value: stats.movieCount,
      note: '独立大银幕作品',
      icon: 'fa-film',
      color: 'bg-[#4ecdc4]',
      valueClass: 'text-black',
      defaultValueClass: 'text-indigo-500',
      id: 'statMovie',
    },
    {
      label: 'OVA',
      value: stats.ovaCount,
      note: '纪念 / 特别篇',
      icon: 'fa-compact-disc',
      color: 'bg-white',
      valueClass: 'text-[#ff6b6b]',
      defaultValueClass: 'text-purple-500',
      id: 'statOva',
    },
    {
      label: '泡面番',
      value: stats.shortCount,
      note: '轻松短篇作品',
      icon: 'fa-bowl-food',
      color: 'bg-[#4ecdc4]',
      valueClass: 'text-black',
      defaultValueClass: 'text-teal-500',
      id: 'statShort',
    },
    {
      label: '极高推荐 (9.5+)',
      value: stats.masterpieceCount,
      note: '殿堂级佳作',
      icon: 'fa-crown',
      color: 'bg-[#ff6b6b]',
      valueClass: 'text-red-600',
      valueBackdrop: true,
      defaultValueClass: 'text-red-600',
      id: 'statMasterpiece',
    },
  ]

  if (theme === 'memphis') {
    return (
      <section className="mb-7 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6" aria-label="番剧统计">
        {cards.map((card, index) => (
          <article
            key={card.id}
            className={`memphis-stat-card relative flex min-h-36 flex-col justify-between border-[3px] border-black p-4 shadow-[6px_6px_0_0_#111] ${card.color} ${index % 2 ? 'rotate-[1deg]' : '-rotate-[1deg]'}`}
          >
            <div className="flex items-start justify-between gap-2">
              <span className="text-[13px] font-black uppercase leading-5 tracking-wide">{card.label}</span>
              <span className="flex h-8 w-8 shrink-0 items-center justify-center border-2 border-black bg-white">
                <i className={`fa-solid ${card.icon} text-sm`}></i>
              </span>
            </div>
            <span
              id={card.id}
              className={`mt-2 w-fit text-[2.75rem] font-black leading-none ${card.valueClass} ${card.valueBackdrop ? 'border-2 border-black bg-white px-2 py-1 shadow-[3px_3px_0_0_#111]' : ''}`}
            >
              {card.value}
            </span>
            <span className="mt-2 border-t-2 border-black pt-2 text-xs font-bold leading-4">
              {card.note}
            </span>
          </article>
        ))}
      </section>
    )
  }

  return (
    <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-6">
      {cards.map((card) => (
        <div key={card.id} className="flex flex-col justify-between rounded-2xl border border-slate-100 bg-white p-4 shadow-sm transition-all duration-300 hover:-translate-y-1 hover:shadow-md">
          <span className="text-xs font-medium text-yellow-700">{card.label}</span>
          <span id={card.id} className={`mt-1 text-2xl font-bold sm:text-3xl ${card.defaultValueClass}`}>
            {card.value}
          </span>
          <span className="mt-1 text-xs text-slate-500">{card.note}</span>
        </div>
      ))}
    </div>
  )
}

export default StatsSummary
