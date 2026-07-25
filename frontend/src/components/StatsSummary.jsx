import { useMemo } from 'react'

function StatsSummary({ db = [], statsData = null }) {
  const calculatedStats = useMemo(() => {
    const hasScoreList = db.filter((item) => item.score !== null)
    const completedCount = db.filter((item) => item.status === 'completed').length

    const sumScore = hasScoreList.reduce((acc, curr) => acc + curr.score, 0)
    const avg = hasScoreList.length > 0 ? (sumScore / hasScoreList.length).toFixed(2) : 'N/A'

    const movieCount = db.filter(
      (item) => item.title.includes('剧场版') || item.tags.includes('剧场版'),
    ).length
    const ovaCount = db.filter(
      (item) => item.title.toUpperCase().includes('OVA') || item.tags.includes('OVA'),
    ).length
    const shortCount = db.filter((item) => item.tags.includes('泡面番')).length
    const masterpieceCount = db.filter(
      (item) => item.score !== null && item.score >= 9.5,
    ).length
    const pendingCount = db.filter(
      (item) =>
        item.score === null || item.period.includes('待定') || item.period === '未定档',
    ).length

    return {
      completedCount,
      avg,
      movieCount,
      ovaCount,
      shortCount,
      masterpieceCount,
      pendingCount,
    }
  }, [db])

  const stats = statsData
    ? {
        completedCount: statsData.completed_count ?? 0,
        avg: statsData.average_score ?? 'N/A',
        movieCount: statsData.movie_count ?? 0,
        ovaCount: statsData.ova_count ?? 0,
        shortCount: statsData.short_count ?? 0,
        masterpieceCount: statsData.masterpiece_count ?? 0,
        pendingCount: statsData.pending_count ?? 0,
      }
    : calculatedStats

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4 mb-6">
      <div className="bg-white rounded-2xl p-4 shadow-sm border border-slate-100 flex flex-col justify-between hover:-translate-y-1 hover:shadow-md transition-all duration-300">
        <span className="text-yellow-700 text-xs font-medium">追完的番剧数量</span>
        <span className="text-2xl sm:text-3xl font-bold text-slate-800 mt-1" id="statTotal">
          {stats.completedCount}
        </span>
        <span className="text-xs text-green-500 mt-1" id="statPendingCount">
          <i className="fa-solid fa-circle-plus"></i> {stats.pendingCount}部计划/待定
        </span>
      </div>
      <div className="bg-white rounded-2xl p-4 shadow-sm border border-slate-100 flex flex-col justify-between hover:-translate-y-1 hover:shadow-md transition-all duration-300">
        <span className="text-yellow-700 text-xs font-medium">平均评分</span>
        <span className="text-2xl sm:text-3xl font-bold text-pink-500 mt-1" id="statAvgScore">
          {stats.avg}
        </span>
        <span className="text-xs text-slate-500 mt-1">仅统计已评分作品</span>
      </div>
      <div className="bg-white rounded-2xl p-4 shadow-sm border border-slate-100 flex flex-col justify-between hover:-translate-y-1 hover:shadow-md transition-all duration-300">
        <span className="text-yellow-700 text-xs font-medium">剧场版</span>
        <span className="text-2xl sm:text-3xl font-bold text-indigo-500 mt-1" id="statMovie">
          {stats.movieCount}
        </span>
        <span className="text-xs text-slate-500 mt-1">独立大荧幕作品</span>
      </div>
      <div className="bg-white rounded-2xl p-4 shadow-sm border border-slate-100 flex flex-col justify-between hover:-translate-y-1 hover:shadow-md transition-all duration-300">
        <span className="text-yellow-700 text-xs font-medium">OVA</span>
        <span className="text-2xl sm:text-3xl font-bold text-purple-500 mt-1" id="statOva">
          {stats.ovaCount}
        </span>
        <span className="text-xs text-slate-500 mt-1">含纪念/特别篇</span>
      </div>
      <div className="bg-white rounded-2xl p-4 shadow-sm border border-slate-100 flex flex-col justify-between hover:-translate-y-1 hover:shadow-md transition-all duration-300">
        <span className="text-yellow-700 text-xs font-medium">泡面番</span>
        <span className="text-2xl sm:text-3xl font-bold text-teal-500 mt-1" id="statShort">
          {stats.shortCount}
        </span>
        <span className="text-xs text-slate-500 mt-1">轻松且幽默的小短片</span>
      </div>
      <div className="bg-white rounded-2xl p-4 shadow-sm border border-slate-100 flex flex-col justify-between hover:-translate-y-1 hover:shadow-md transition-all duration-300">
        <span className="text-yellow-700 text-xs font-medium">极高推荐(9.5+)</span>
        <span className="text-2xl sm:text-3xl font-bold text-red-600 mt-1" id="statMasterpiece">
          {stats.masterpieceCount}
        </span>
        <span className="text-xs text-red-600 font-medium mt-1">
          <i className="fa-solid fa-crown"></i> 殿堂级佳作
        </span>
      </div>
    </div>
  )
}

export default StatsSummary
