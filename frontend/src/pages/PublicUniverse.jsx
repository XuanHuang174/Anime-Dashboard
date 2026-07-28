import { useEffect, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { useNavigate } from 'react-router-dom'
import api from '../api/api.js'
import BackToTop from '../components/BackToTop.jsx'

gsap.registerPlugin(useGSAP)

let hasPlayedUniverseEntrance = false

const STAT_BLOCKS = [
  {
    key: 'completed_count',
    label: '追完数量',
    icon: 'fa-circle-check',
    color: 'bg-[#ffe66d]',
    className: 'sm:col-span-2',
  },
  {
    key: 'average_score',
    label: '平均评分',
    icon: 'fa-chart-line',
    color: 'bg-[#ff8fab]',
    className: '',
  },
  {
    key: 'masterpiece_count',
    label: '极高推荐',
    icon: 'fa-crown',
    color: 'bg-[#4ecdc4]',
    className: '',
  },
  {
    key: 'movie_count',
    label: '剧场版',
    icon: 'fa-film',
    color: 'bg-white',
    className: '',
  },
  {
    key: 'ova_count',
    label: 'OVA',
    icon: 'fa-compact-disc',
    color: 'bg-[#ff6b6b]',
    className: '',
  },
  {
    key: 'short_count',
    label: '泡面番',
    icon: 'fa-bowl-food',
    color: 'bg-[#b8f2e6]',
    className: 'sm:col-span-2',
  },
]

function formatStatValue(profile, key) {
  const value = profile[key]
  if (key === 'average_score') {
    return value === null || value === undefined ? 'N/A' : Number(value).toFixed(2)
  }
  return value ?? 0
}

function PublicUniverse() {
  const navigate = useNavigate()
  const pageRef = useRef(null)
  const cardsRef = useRef(null)
  const hasAnimatedCards = useRef(hasPlayedUniverseEntrance)
  const [searchInput, setSearchInput] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [profiles, setProfiles] = useState([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    const timeoutId = window.setTimeout(
      () => setSearchQuery(searchInput.trim()),
      350,
    )
    return () => window.clearTimeout(timeoutId)
  }, [searchInput])

  useEffect(() => {
    const controller = new AbortController()

    const loadProfiles = async () => {
      setIsLoading(true)
      setLoadError('')
      try {
        const response = await api.get('public-journal/users/', {
          params: {
            page_size: 100,
            ...(searchQuery ? { search: searchQuery } : {}),
          },
          signal: controller.signal,
        })
        const payload = response.data
        setProfiles(Array.isArray(payload) ? payload : payload.results ?? [])
      } catch (error) {
        if (error.code !== 'ERR_CANCELED') {
          setLoadError('共创宇宙暂时无法连接，请稍后重新展开。')
        }
      } finally {
        if (!controller.signal.aborted) setIsLoading(false)
      }
    }

    loadProfiles()
    return () => controller.abort()
  }, [searchQuery])

  useGSAP(() => {
    if (
      isLoading
      || hasAnimatedCards.current
      || profiles.length === 0
    ) {
      return undefined
    }
    hasAnimatedCards.current = true
    hasPlayedUniverseEntrance = true

    const cards = gsap.utils.toArray('.public-universe-card')
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      gsap.set(cards, { clearProps: 'all' })
      return undefined
    }

    gsap.set(cards, { willChange: 'transform, opacity' })
    const animation = gsap.fromTo(
      cards,
      { y: 72, scale: 0.88, rotation: -2, autoAlpha: 0 },
      {
        y: 0,
        scale: 1,
        rotation: 0,
        autoAlpha: 1,
        duration: 0.58,
        stagger: 0.1,
        ease: 'back.out(1.5)',
        onComplete: () => {
          gsap.set(cards, {
            clearProps: 'transform,opacity,visibility,willChange',
          })
        },
      },
    )
    return () => animation.kill()
  }, {
    scope: cardsRef,
    dependencies: [isLoading, profiles.length],
  })

  const openJournal = (username) => {
    navigate(`/shared/${encodeURIComponent(username)}`)
  }

  return (
    <main
      ref={pageRef}
      className="memphis-public public-universe relative min-h-screen overflow-hidden bg-[#fff5ee] pb-12 text-black"
    >
      <div className="memphis-grid-texture pointer-events-none absolute inset-0" aria-hidden="true" />
      <div className="pointer-events-none absolute -left-12 top-[34rem] h-36 w-36 rotate-12 border-4 border-black bg-[#ff8fab] shadow-[8px_8px_0_#111]" aria-hidden="true" />
      <div className="pointer-events-none absolute -right-14 top-[57rem] h-40 w-40 rounded-full border-[18px] border-black bg-[#4ecdc4] shadow-[8px_8px_0_#ffe66d]" aria-hidden="true" />

      <header className="relative z-10 overflow-hidden border-b-4 border-black bg-[#ffe66d]">
        <div className="absolute inset-y-0 right-0 w-[46%] -skew-x-12 border-l-4 border-black bg-[#ff2f92]" aria-hidden="true" />
        <div className="absolute -right-10 -top-14 h-48 w-48 rounded-full border-[16px] border-black bg-[#4ecdc4] shadow-[8px_8px_0_#111]" aria-hidden="true">
          <i className="fa-solid fa-earth-asia absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rotate-12 text-7xl" />
        </div>
        <div className="absolute bottom-5 right-[31%] h-16 w-16 rotate-12 border-4 border-black bg-white shadow-[5px_5px_0_#111]" aria-hidden="true" />

        <div className="relative mx-auto max-w-[1500px] px-4 py-8 sm:px-6 lg:px-8 lg:py-12">
          <button
            type="button"
            onClick={() => navigate('/')}
            className="mb-7 inline-flex items-center gap-2 rounded-full border-4 border-black bg-white px-5 py-2.5 text-sm font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
          >
            <i className="fa-solid fa-arrow-left" />
            返回展示主界面
          </button>

          <div className="max-w-5xl">
            <span className="inline-flex -rotate-2 border-[3px] border-black bg-[#4ecdc4] px-4 py-1.5 text-xs font-black uppercase tracking-[0.2em] shadow-[4px_4px_0_#111]">
              PUBLIC JOURNAL / LIVE UNIVERSE
            </span>
            <h1 className="mt-5 flex flex-wrap items-end gap-3 text-5xl font-black tracking-[-0.065em] sm:text-7xl lg:text-8xl">
              <span className="border-4 border-black bg-black px-4 py-2 text-white">番剧共创</span>
              <span className="-rotate-1 border-4 border-black bg-white px-4 py-2 shadow-[9px_9px_0_#111]">宇宙</span>
            </h1>
            <p className="mt-6 max-w-3xl border-l-[10px] border-black bg-[#ff8fab] px-5 py-3 text-sm font-black leading-7 shadow-[6px_6px_0_#111] sm:text-base">
              穿过不同主理人的观看轨道，发现真实同步、持续生长的私人番剧宇宙。
            </p>

            <label className="mt-8 block max-w-3xl">
              <span className="mb-2 inline-flex border-2 border-black bg-black px-3 py-1 text-xs font-black tracking-[0.13em] text-white">
                <i className="fa-solid fa-satellite-dish mr-2" />
                精确定位用户名
              </span>
              <span className="flex border-4 border-black bg-white shadow-[9px_9px_0_#111] focus-within:translate-x-[5px] focus-within:translate-y-[5px] focus-within:shadow-none">
                <span className="flex w-16 shrink-0 items-center justify-center border-r-4 border-black bg-[#4ecdc4] text-xl">
                  <i className="fa-solid fa-magnifying-glass" />
                </span>
                <input
                  type="search"
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  placeholder="输入完整用户名，例如 XHcode"
                  aria-label="精确搜索公开用户名"
                  className="min-w-0 flex-1 bg-white px-5 py-4 text-base font-black outline-none placeholder:text-zinc-500 focus:bg-[#fff3ad] sm:text-lg"
                />
                {searchInput && (
                  <button
                    type="button"
                    onClick={() => setSearchInput('')}
                    aria-label="清空用户名搜索"
                    className="w-14 shrink-0 border-l-4 border-black bg-[#ff6b6b] text-lg font-black active:bg-white"
                  >
                    <i className="fa-solid fa-xmark" />
                  </button>
                )}
              </span>
            </label>
          </div>
        </div>
      </header>

      <section className="relative z-10 mx-auto max-w-[1500px] px-4 py-9 sm:px-6 lg:px-8">
        <div className="mb-7 flex flex-wrap items-end justify-between gap-4 border-b-4 border-black pb-4">
          <div>
            <span className="inline-flex border-2 border-black bg-[#4ecdc4] px-3 py-1 text-xs font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">
              LIVE SIGNALS
            </span>
            <h2 className="mt-3 text-3xl font-black sm:text-4xl">公开手账信号</h2>
          </div>
          <span className="border-4 border-black bg-[#ffe66d] px-4 py-2 text-sm font-black shadow-[4px_4px_0_#111]">
            <i className="fa-solid fa-users-viewfinder mr-2" />
            {isLoading ? '正在扫描…' : `${profiles.length} 位主理人`}
          </span>
        </div>

        {loadError && (
          <div role="alert" className="border-4 border-black bg-[#ff6b6b] px-6 py-8 text-center text-lg font-black shadow-[8px_8px_0_#111]">
            <i className="fa-solid fa-triangle-exclamation mr-2" />
            {loadError}
          </div>
        )}

        {!loadError && isLoading && (
          <div className="border-4 border-black bg-white px-6 py-12 text-center font-black shadow-[8px_8px_0_#4ecdc4]">
            <i className="fa-solid fa-satellite-dish mr-3 animate-pulse text-2xl text-[#ff2f92]" />
            正在接收公开手账信号…
          </div>
        )}

        {!loadError && !isLoading && profiles.length === 0 && (
          <div className="relative overflow-hidden border-4 border-black bg-white px-6 py-14 text-center shadow-[9px_9px_0_#ff8fab]">
            <span className="mx-auto flex h-20 w-20 -rotate-3 items-center justify-center border-4 border-black bg-[#ffe66d] text-3xl shadow-[6px_6px_0_#111]">
              <i className="fa-solid fa-user-astronaut" />
            </span>
            <h3 className="mt-6 text-2xl font-black">
              {searchQuery ? '没有捕捉到这个用户名' : '共创宇宙正在等待首位主理人'}
            </h3>
            <p className="mx-auto mt-3 max-w-xl border-2 border-black bg-[#fff5ee] px-4 py-2 text-sm font-bold text-zinc-600 shadow-[3px_3px_0_#4ecdc4]">
              {searchQuery
                ? '请输入完整用户名重新搜索；这里只匹配账号，不匹配昵称。'
                : '通过审核并主动公开后，用户的手账卡片会出现在这里。'}
            </p>
          </div>
        )}

        <div ref={cardsRef} className="grid grid-cols-1 gap-9 lg:grid-cols-2">
          {!isLoading && profiles.map((profile, profileIndex) => {
            const displayName = profile.nickname?.trim() || profile.username
            const posters = profile.top_anime_poster_urls ?? []
            return (
              <article
                key={profile.username}
                role="link"
                tabIndex={0}
                aria-label={`查看 ${displayName} 的公开手账`}
                onClick={() => openJournal(profile.username)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    openJournal(profile.username)
                  }
                }}
                className="public-universe-card group cursor-pointer border-4 border-black bg-white p-5 shadow-[12px_12px_0_#111] outline-none hover:translate-x-[6px] hover:translate-y-[6px] hover:shadow-[6px_6px_0_#111] focus-visible:ring-4 focus-visible:ring-[#ff2f92] focus-visible:ring-offset-4 active:translate-x-[12px] active:translate-y-[12px] active:shadow-none sm:p-6"
              >
                <div className="flex items-start gap-4 border-b-4 border-black pb-5">
                  <span
                    className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden border-4 border-black bg-[#ffe66d] text-3xl font-black sm:h-24 sm:w-24"
                    style={{
                      boxShadow: `5px 5px 0 ${profileIndex % 2 ? '#ff8fab' : '#4ecdc4'}`,
                    }}
                  >
                    {profile.avatar ? (
                      <img
                        src={profile.avatar}
                        alt={`${displayName}的头像`}
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <i className="fa-solid fa-star text-[#ff2f92]" />
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <span className="inline-flex -rotate-1 border-2 border-black bg-[#4ecdc4] px-2 py-1 text-[10px] font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">
                      JOURNAL OWNER
                    </span>
                    <h3 className="mt-3 break-all text-2xl font-black leading-tight sm:text-3xl">
                      {displayName}
                    </h3>
                    <p className="mt-2 border-l-[6px] border-black bg-[#fff5ee] px-3 py-2 text-sm font-bold leading-6 text-zinc-700">
                      {profile.showcase_subtitle?.trim() || '这位主理人还没有留下宇宙宣言。'}
                    </p>
                  </div>
                  <span className="hidden h-12 w-12 shrink-0 rotate-3 items-center justify-center border-4 border-black bg-[#ff8fab] text-xl shadow-[4px_4px_0_#111] sm:flex">
                    <i className="fa-solid fa-arrow-up-right-from-square" />
                  </span>
                </div>

                <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {STAT_BLOCKS.map((stat, statIndex) => (
                    <div
                      key={stat.key}
                      className={`${stat.className} ${stat.color} min-h-24 border-[3px] border-black p-3 shadow-[4px_4px_0_#111] ${statIndex % 3 === 1 ? 'sm:-rotate-1' : ''}`}
                    >
                      <div className="flex items-center justify-between gap-2 text-[11px] font-black">
                        <span>{stat.label}</span>
                        <i className={`fa-solid ${stat.icon}`} />
                      </div>
                      <strong className="mt-3 block text-3xl font-black leading-none">
                        {formatStatValue(profile, stat.key)}
                      </strong>
                    </div>
                  ))}
                </div>

                <div className="mt-6 border-t-4 border-black pt-5">
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <span className="border-2 border-black bg-black px-3 py-1 text-xs font-black tracking-[0.14em] text-white">
                      TOP 3 / 高分轨道
                    </span>
                    <span className="text-xs font-black text-zinc-600">
                      点击卡片进入手账
                    </span>
                  </div>
                  {posters.length > 0 ? (
                    <div className="flex min-h-44 items-end justify-center gap-3 overflow-hidden px-2 pb-3 pt-2 sm:min-h-52 sm:gap-5">
                      {posters.map((poster, posterIndex) => (
                        <figure
                          key={`${poster}-${posterIndex}`}
                          className={`${posterIndex % 2 === 0 ? '-rotate-2' : 'rotate-2'} w-[29%] max-w-32 border-4 border-black bg-white p-1.5 shadow-[5px_5px_0_#111] group-hover:rotate-0`}
                        >
                          <img
                            src={poster}
                            alt={`${displayName}的高分番剧海报 ${posterIndex + 1}`}
                            className="aspect-[2/3] w-full bg-[#ffe66d] object-cover"
                            loading="lazy"
                          />
                          <figcaption className="pt-1.5 text-center text-[10px] font-black">
                            NO.{posterIndex + 1}
                          </figcaption>
                        </figure>
                      ))}
                    </div>
                  ) : (
                    <div className="flex min-h-44 items-center justify-center border-4 border-black bg-[#fff5ee] text-center font-black text-zinc-500 shadow-[5px_5px_0_#4ecdc4]">
                      <span>
                        <i className="fa-solid fa-images mr-2" />
                        暂无高分海报信号
                      </span>
                    </div>
                  )}
                </div>
              </article>
            )
          })}
        </div>
      </section>

      <BackToTop theme="memphis" />
    </main>
  )
}

export default PublicUniverse
