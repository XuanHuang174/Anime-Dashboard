import { useEffect, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { useNavigate } from 'react-router-dom'
import api from '../api/api.js'
import usePublicReturn from '../hooks/usePublicReturn.js'
import BackToTop from '../components/BackToTop.jsx'
import P5PopArtwork from '../components/P5PopArtwork.jsx'

gsap.registerPlugin(useGSAP)

const UNIVERSE_CHARACTER_URL = '/koisinu.png'

const CORE_STATS = [
  {
    key: 'completed_count',
    label: '追完',
    icon: 'fa-circle-check',
    color: 'bg-[#ffe66d]',
  },
  {
    key: 'average_score',
    label: '均分',
    icon: 'fa-chart-line',
    color: 'bg-[#ff8fab]',
  },
  {
    key: 'masterpiece_count',
    label: '9.5+',
    icon: 'fa-crown',
    color: 'bg-[#4ecdc4]',
  },
]

const SECONDARY_STATS = [
  {
    key: 'movie_count',
    label: '剧场版',
    icon: 'fa-film',
  },
  {
    key: 'ova_count',
    label: 'OVA',
    icon: 'fa-compact-disc',
  },
  {
    key: 'short_count',
    label: '泡面番',
    icon: 'fa-bowl-food',
  },
]

const POLAROID_STYLES = [
  {
    rotation: '-rotate-6',
    shadow: 'shadow-[9px_9px_0_#ff2f92]',
    zIndex: 'z-[3]',
    offset: 'mt-8',
  },
  {
    rotation: 'rotate-3',
    shadow: 'shadow-[9px_9px_0_#4ecdc4]',
    zIndex: 'z-[2]',
    offset: 'mb-8',
  },
  {
    rotation: '-rotate-2',
    shadow: 'shadow-[9px_9px_0_#ffe66d]',
    zIndex: 'z-[1]',
    offset: 'mt-12',
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
  const returnTarget = usePublicReturn()
  const navigate = useNavigate()
  const pageRef = useRef(null)
  const cardsRef = useRef(null)
  const hasAnimatedCards = useRef(false)
  const [isPageIntroComplete, setIsPageIntroComplete] = useState(false)
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
    const reduceMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches
    const leftPanel = '.universe-crash-left'
    const rightPanel = '.universe-crash-right'
    const artwork = '.universe-pop-art'
    const headerItems = '.universe-header-reveal'
    const signalStage = '.universe-signal-stage'

    if (reduceMotion) {
      gsap.set(
        [leftPanel, rightPanel, artwork, headerItems, signalStage],
        { clearProps: 'all' },
      )
      setIsPageIntroComplete(true)
      return undefined
    }

    gsap.set([leftPanel, rightPanel, artwork, signalStage], {
      willChange: 'transform, opacity',
    })

    const timeline = gsap.timeline({
      defaults: { force3D: true },
      onComplete: () => {
        gsap.set(
          [leftPanel, rightPanel, artwork, headerItems, signalStage],
          {
            clearProps: 'transform,opacity,visibility,willChange',
          },
        )
        setIsPageIntroComplete(true)
      },
    })

    timeline
      .fromTo(
        leftPanel,
        { xPercent: -100 },
        { xPercent: 0, duration: 0.48, ease: 'power4.out' },
        0,
      )
      .fromTo(
        rightPanel,
        { xPercent: 100 },
        { xPercent: 0, duration: 0.48, ease: 'power4.out' },
        0,
      )
      .fromTo(
        artwork,
        { autoAlpha: 0, scale: 0.2, rotation: 30, x: 90, y: 24 },
        {
          autoAlpha: 1,
          scale: 1,
          rotation: 0,
          x: 0,
          y: 0,
          duration: 0.64,
          ease: 'back.out(2)',
        },
        0.34,
      )
      .fromTo(
        headerItems,
        { autoAlpha: 0, y: 46, rotation: 1.5 },
        {
          autoAlpha: 1,
          y: 0,
          rotation: 0,
          duration: 0.34,
          stagger: 0.045,
          ease: 'back.out(1.7)',
        },
        0.43,
      )
      .fromTo(
        signalStage,
        { autoAlpha: 0, y: 150 },
        {
          autoAlpha: 1,
          y: 0,
          duration: 0.46,
          ease: 'back.out(1.45)',
        },
        0.72,
      )

    return () => timeline.kill()
  }, { scope: pageRef })

  useGSAP(() => {
    if (
      isLoading
      || !isPageIntroComplete
      || hasAnimatedCards.current
      || profiles.length === 0
    ) {
      return undefined
    }
    hasAnimatedCards.current = true

    const cards = gsap.utils.toArray('.public-universe-card')
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      gsap.set(cards, { clearProps: 'all' })
      return undefined
    }

    gsap.set(cards, { willChange: 'transform, opacity' })
    const animation = gsap.fromTo(
      cards,
      { y: 150, scale: 0.88, rotation: -2, autoAlpha: 0 },
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
    dependencies: [isLoading, profiles.length, isPageIntroComplete],
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
      <div className="pointer-events-none absolute -left-12 top-[45rem] h-36 w-36 rotate-12 border-4 border-black bg-[#ff8fab] shadow-[8px_8px_0_#111]" aria-hidden="true" />
      <div className="pointer-events-none absolute -right-14 top-[57rem] h-40 w-40 rounded-full border-[18px] border-black bg-[#4ecdc4] shadow-[8px_8px_0_#ffe66d]" aria-hidden="true" />

      <header className="relative z-10 overflow-visible border-b-4 border-black">
        <div className="universe-crash-left absolute inset-0 bg-[#ffe66d]" aria-hidden="true" />
        <div className="universe-crash-right absolute inset-y-0 right-0 w-[46%] -skew-x-12 border-l-4 border-black bg-[#ff2f92]" aria-hidden="true" />
        <div className="universe-crash-right absolute right-[31%] top-9 h-16 w-16 rotate-12 border-4 border-black bg-white shadow-[5px_5px_0_#111]" aria-hidden="true" />
        <P5PopArtwork
          src={UNIVERSE_CHARACTER_URL}
          alt="番剧共创宇宙波普人物立绘"
          eyebrow="X：@yng_hoti"
          className="universe-pop-art -bottom-7 right-[1.5%] h-[108%] w-[34%]"
        />

        <div className="relative mx-auto max-w-[1500px] px-4 py-8 sm:px-6 lg:px-8 lg:py-12">
          <button
            type="button"
            onClick={() => navigate(returnTarget.path)}
            className="universe-header-reveal mb-7 inline-flex items-center gap-2 rounded-full border-4 border-black bg-white px-5 py-2.5 text-sm font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
          >
            <i className="fa-solid fa-arrow-left" />
            {returnTarget.label}
          </button>

          <div className="max-w-5xl lg:max-w-[65%]">
            <span className="universe-header-reveal inline-flex -rotate-2 border-[3px] border-black bg-[#4ecdc4] px-4 py-1.5 text-xs font-black uppercase tracking-[0.2em] shadow-[4px_4px_0_#111]">
              PUBLIC JOURNAL / LIVE UNIVERSE
            </span>
            <h1 className="universe-header-reveal mt-5 flex flex-wrap items-end gap-3 text-5xl font-black tracking-[-0.065em] sm:text-7xl lg:text-8xl">
              <span className="border-4 border-black bg-black px-4 py-2 text-white">番剧共创</span>
              <span className="-rotate-1 border-4 border-black bg-white px-4 py-2 shadow-[9px_9px_0_#111]">宇宙</span>
            </h1>
            <p className="universe-header-reveal mt-6 max-w-3xl border-l-[10px] border-black bg-[#ff8fab] px-5 py-3 text-sm font-black leading-7 shadow-[6px_6px_0_#111] sm:text-base">
              穿过各位同好们的观看轨道，发现真实同步、持续生长的私人番剧宇宙。
            </p>

            <label className="universe-header-reveal mt-8 block max-w-3xl">
              <span className="mb-2 inline-flex border-2 border-black bg-black px-3 py-1 text-xs font-black tracking-[0.13em] text-white">
                <i className="fa-solid fa-satellite-dish mr-2" />
                搜索昵称或账号
              </span>
              <span className="flex border-4 border-black bg-white shadow-[9px_9px_0_#111] focus-within:translate-x-[5px] focus-within:translate-y-[5px] focus-within:shadow-none">
                <span className="flex w-16 shrink-0 items-center justify-center border-r-4 border-black bg-[#4ecdc4] text-xl">
                  <i className="fa-solid fa-magnifying-glass" />
                </span>
                <input
                  type="text"
                  inputMode="search"
                  role="searchbox"
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  placeholder="搜索用户昵称，例如：兔子"
                  aria-label="搜索公开用户昵称或账号"
                  className="min-w-0 flex-1 bg-white px-5 py-4 text-base font-black outline-none placeholder:text-zinc-500 focus:bg-[#fff3ad] sm:text-lg"
                />
                {searchInput && (
                  <button
                    type="button"
                    onClick={() => setSearchInput('')}
                    aria-label="清空用户搜索"
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
        <div className="universe-signal-stage">
          <div className="mb-7 flex flex-wrap items-end justify-between gap-4 border-b-4 border-black pb-4">
            <div>
              <span className="inline-flex border-2 border-black bg-[#4ecdc4] px-3 py-1 text-xs font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">
                LIVE SIGNALS
              </span>
              <h2 className="mt-3 text-3xl font-black sm:text-4xl">公开手账信号</h2>
            </div>
            <span className="border-4 border-black bg-[#ffe66d] px-4 py-2 text-sm font-black shadow-[4px_4px_0_#111]">
              <i className="fa-solid fa-users-viewfinder mr-2" />
              {isLoading ? '正在扫描…' : `${profiles.length} 位同好`}
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
                {searchQuery ? '没有捕捉到匹配的用户' : '共创宇宙正在等待首位同好'}
              </h3>
              <p className="mx-auto mt-3 max-w-xl border-2 border-black bg-[#fff5ee] px-4 py-2 text-sm font-bold text-zinc-600 shadow-[3px_3px_0_#4ecdc4]">
                {searchQuery
                  ? '可以输入昵称或账号中的关键词重新搜索。'
                  : '通过审核并主动公开后，用户的手账卡片会出现在这里。'}
              </p>
            </div>
          )}
        </div>

        <div ref={cardsRef} className="grid grid-cols-1 gap-9 lg:grid-cols-2">
          {isPageIntroComplete
            && !isLoading
            && profiles.map((profile, profileIndex) => {
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
                    <div className="mt-4 flex flex-wrap items-center gap-2">
                      {CORE_STATS.map((stat) => (
                        <span
                          key={stat.key}
                          className={`${stat.color} inline-flex items-center gap-2 rounded-full border-2 border-black px-3 py-1 text-[11px] font-black shadow-[3px_3px_0_#111]`}
                        >
                          <i className={`fa-solid ${stat.icon}`} />
                          <span>{stat.label}</span>
                          <strong className="border-l-2 border-black pl-2 text-sm leading-none">
                            {formatStatValue(profile, stat.key)}
                          </strong>
                        </span>
                      ))}
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-1.5">
                      {SECONDARY_STATS.map((stat, statIndex) => (
                        <span
                          key={stat.key}
                          className={`${statIndex === 1 ? 'bg-zinc-200 text-black' : 'bg-black text-white'} inline-flex items-center gap-1.5 border-2 border-black px-2 py-1 text-[10px] font-black tracking-wide`}
                        >
                          <i className={`fa-solid ${stat.icon}`} />
                          {stat.label}
                          <strong>{formatStatValue(profile, stat.key)}</strong>
                        </span>
                      ))}
                    </div>
                  </div>
                  <span className="hidden h-12 w-12 shrink-0 rotate-3 items-center justify-center border-4 border-black bg-[#ff8fab] text-xl shadow-[4px_4px_0_#111] sm:flex">
                    <i className="fa-solid fa-arrow-up-right-from-square" />
                  </span>
                </div>

                <div className="mt-5 pt-1">
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <span className="border-2 border-black bg-black px-3 py-1 text-xs font-black tracking-[0.14em] text-white">
                      TOP 3 / 高分轨道
                    </span>
                    <span className="rotate-1 border-2 border-black bg-[#ffe66d] px-2 py-1 text-[10px] font-black shadow-[3px_3px_0_#ff2f92]">
                      <i className="fa-solid fa-hand-pointer mr-1.5" />
                      悬停抽取照片
                    </span>
                  </div>
                  {posters.length > 0 ? (
                    <div className="relative flex min-h-[25rem] items-center justify-center overflow-visible px-1 pb-5 pt-4 sm:min-h-[30rem] sm:px-4">
                      {posters.map((poster, posterIndex) => (
                        <figure
                          key={`${poster}-${posterIndex}`}
                          className={`${POLAROID_STYLES[posterIndex]?.rotation ?? '-rotate-2'} ${POLAROID_STYLES[posterIndex]?.shadow ?? 'shadow-[9px_9px_0_#ff2f92]'} ${POLAROID_STYLES[posterIndex]?.zIndex ?? 'z-[1]'} ${POLAROID_STYLES[posterIndex]?.offset ?? ''} ${posterIndex > 0 ? '-ml-8 sm:-ml-12' : ''} relative w-[42%] max-w-[220px] shrink-0 border-4 border-black bg-white p-2 transition-[transform,box-shadow] duration-150 ease-out hover:z-50 hover:-translate-y-5 hover:rotate-0 hover:shadow-[12px_12px_0_#111] sm:p-2.5`}
                        >
                          <img
                            src={poster}
                            alt={`${displayName}的高分番剧海报 ${posterIndex + 1}`}
                            className="aspect-[2/3] w-full border-2 border-black bg-[#ffe66d] object-cover"
                            loading="lazy"
                          />
                          <figcaption className="flex min-h-12 items-center justify-between gap-2 px-1 pt-2 text-[11px] font-black sm:min-h-14 sm:text-sm">
                            <span className="border-2 border-black bg-[#fff5ee] px-2 py-1">
                              NO.{posterIndex + 1}
                            </span>
                            <span className="truncate tracking-[0.08em] text-zinc-500">
                              TOP PICK
                            </span>
                          </figcaption>
                        </figure>
                      ))}
                    </div>
                  ) : (
                    <div className="flex min-h-[25rem] items-center justify-center border-4 border-black bg-[#fff5ee] text-center font-black text-zinc-500 shadow-[7px_7px_0_#4ecdc4] sm:min-h-[30rem]">
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
