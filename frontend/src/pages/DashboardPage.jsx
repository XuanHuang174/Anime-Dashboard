import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import api from '../api/api.js'
import { clearTokens } from '../auth/tokenStorage.js'
import CustomCursor from '../components/CustomCursor.jsx'

function DashboardPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [records, setRecords] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()

    async function loadRecords() {
      try {
        const response = await api.get('records/', {
          signal: controller.signal,
        })
        const data = response.data
        setRecords(Array.isArray(data) ? data : data.results ?? [])
      } catch (requestError) {
        if (requestError.code === 'ERR_CANCELED') return

        if (requestError.response?.status === 401) {
          clearTokens()
          navigate('/login', { replace: true, state: { from: location } })
          return
        }

        setError('追番记录加载失败，请稍后重试。')
      } finally {
        if (!controller.signal.aborted) setIsLoading(false)
      }
    }

    loadRecords()
    return () => controller.abort()
  }, [location, navigate])

  const handleLogout = () => {
    clearTokens()
    navigate('/login', { replace: true })
  }

  return (
    <main className="min-h-screen bg-slate-100 text-slate-800">
      <CustomCursor />
      <header className="border-b border-white/30 bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-600 text-white shadow-lg">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-5 sm:px-6 lg:px-8">
          <Link to="/" className="flex items-center gap-3 font-black tracking-tight">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/20 backdrop-blur-sm">
              <i className="fa-solid fa-star text-yellow-300"></i>
            </span>
            <span>我的番剧手账房</span>
          </Link>
          <button
            type="button"
            onClick={handleLogout}
            className="rounded-xl border border-white/30 bg-white/15 px-4 py-2 text-sm font-bold backdrop-blur-sm transition hover:bg-white/25"
          >
            <i className="fa-solid fa-arrow-right-from-bracket mr-2"></i>
            退出登录
          </button>
        </div>
      </header>

      <section className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
        <div className="rounded-3xl border border-white bg-white p-7 shadow-xl shadow-slate-200/60 sm:p-10">
          <span className="text-sm font-bold uppercase tracking-[0.2em] text-indigo-500">Private dashboard</span>
          <h1 className="mt-3 text-3xl font-black text-slate-900 sm:text-4xl">欢迎来到你的专属追番空间</h1>
          <p className="mt-3 max-w-2xl leading-7 text-slate-500">
            这里展示通过 JWT 身份验证后，从 Django 获取的私人追番记录。
          </p>

          {isLoading && (
            <div className="mt-10 flex items-center justify-center gap-3 rounded-2xl bg-indigo-50 py-12 font-bold text-indigo-500">
              <i className="fa-solid fa-spinner animate-spin text-xl"></i>
              正在读取你的追番手账...
            </div>
          )}

          {!isLoading && error && (
            <div role="alert" className="mt-10 rounded-2xl bg-rose-50 px-5 py-4 font-medium text-rose-600">
              <i className="fa-solid fa-circle-exclamation mr-2"></i>
              {error}
            </div>
          )}

          {!isLoading && !error && records.length === 0 && (
            <div className="mt-10 rounded-2xl border border-dashed border-slate-300 bg-slate-50 py-12 text-center">
              <i className="fa-solid fa-book-open text-4xl text-slate-300"></i>
              <h2 className="mt-4 font-black text-slate-700">手账还是空的</h2>
              <p className="mt-1 text-sm text-slate-500">去 Django 后台或后续的新增页面录入第一部番剧吧。</p>
            </div>
          )}

          {!isLoading && !error && records.length > 0 && (
            <div className="mt-9 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {records.map((record) => (
                <article
                  key={record.id}
                  className="group overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-lg shadow-slate-200/60 transition duration-300 hover:-translate-y-1 hover:shadow-xl"
                >
                  <div className="aspect-[16/9] overflow-hidden bg-gradient-to-br from-pink-100 to-indigo-100">
                    {record.anime?.poster_url ? (
                      <img
                        src={record.anime.poster_url}
                        alt={`${record.anime.chinese_title} 海报`}
                        className="h-full w-full object-cover transition duration-500 group-hover:scale-105"
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center text-4xl text-indigo-300">
                        <i className="fa-solid fa-image"></i>
                      </div>
                    )}
                  </div>

                  <div className="p-5">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h2 className="font-black text-slate-900">
                          {record.anime?.chinese_title || '未命名番剧'}
                        </h2>
                        {record.anime?.japanese_title && (
                          <p className="mt-1 line-clamp-1 text-xs text-slate-400">
                            {record.anime.japanese_title}
                          </p>
                        )}
                      </div>
                      {record.personal_score !== null && (
                        <span className="shrink-0 rounded-lg bg-amber-50 px-2.5 py-1 text-sm font-black text-amber-600">
                          <i className="fa-solid fa-star mr-1"></i>
                          {record.personal_score}
                        </span>
                      )}
                    </div>

                    <div className="mt-4 flex flex-wrap gap-2 text-xs font-bold">
                      <span className="rounded-full bg-indigo-50 px-3 py-1 text-indigo-600">
                        {record.watch_status_display || record.watch_status}
                      </span>
                      {record.anime?.airing_period && (
                        <span className="rounded-full bg-slate-100 px-3 py-1 text-slate-500">
                          {record.anime.airing_period}
                        </span>
                      )}
                    </div>

                    {record.review && (
                      <p className="mt-4 line-clamp-3 text-sm leading-6 text-slate-500">
                        {record.review}
                      </p>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>
    </main>
  )
}

export default DashboardPage
