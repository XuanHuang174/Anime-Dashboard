import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import api from '../api/api.js'
import { getImageFileError, IMAGE_FILE_ACCEPT } from '../utils/imageUpload.js'

gsap.registerPlugin(useGSAP)

const emptyManualForm = {
  chineseTitle: '',
  japaneseTitle: '',
  airingPeriod: '',
  studio: '',
  episodes: '',
  tags: '',
  description: '',
  posterUrl: '',
  baikeUrl: '',
  watchStatus: 'plan',
  personalScore: '',
  review: '',
}

const inputClass = 'w-full border-2 border-black bg-white px-3 py-2.5 text-sm font-bold text-black shadow-[3px_3px_0_#111] outline-none transition-none placeholder:text-zinc-400 focus:translate-x-1 focus:translate-y-1 focus:bg-[#fff2a8] focus:shadow-none'
const labelClass = 'mb-1.5 block text-xs font-black text-black'
const arcadeButton = 'border-[3px] border-black px-4 py-2.5 text-sm font-black text-black shadow-[5px_5px_0_#111] transition-none active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:cursor-not-allowed disabled:opacity-50'

function normalizeResults(data) {
  return Array.isArray(data) ? data : data.results ?? []
}

function normalizeAnimeIdentityPart(value) {
  return String(value ?? '')
    .normalize('NFKC')
    .trim()
    .toLocaleLowerCase()
    .replace(/[《》〈〉「」『』【】[\]()（）\s]/g, '')
}

function getAnimeIdentityKey(anime) {
  const chineseTitle = anime?.chinese_title ?? anime?.title
  const japaneseTitle = anime?.japanese_title ?? anime?.japaneseTitle
  const airingPeriod = anime?.airing_period ?? anime?.period

  return [
    normalizeAnimeIdentityPart(chineseTitle),
    normalizeAnimeIdentityPart(japaneseTitle),
    normalizeAnimeIdentityPart(airingPeriod),
  ].join('|')
}

function normalizeShowcaseResults(data) {
  const uniqueAnime = new Map()

  normalizeResults(data).forEach((record) => {
    const anime = record?.anime ?? record
    if (!anime?.id) return

    const identityKey = getAnimeIdentityKey(anime) || `id:${anime.id}`
    if (!uniqueAnime.has(identityKey)) uniqueAnime.set(identityKey, anime)
  })

  return [...uniqueAnime.values()]
}

function formatBangumiChineseTitle(value) {
  const title = String(value ?? '').trim()
  if (!title) return ''

  const unwrappedTitle = title
    .replace(/^《+/, '')
    .replace(/》+$/, '')
    .trim()

  return unwrappedTitle ? `《${unwrappedTitle}》` : ''
}

function AddAnimeModal({
  isOpen,
  onClose,
  existingAnimeIds = new Set(),
  existingAnimeItems = [],
  onRecordAdded,
  showToast,
}) {
  const overlayRef = useRef(null)
  const modalRef = useRef(null)
  const closeHandlerRef = useRef(null)
  const posterInputRef = useRef(null)
  const magicSearchRef = useRef(null)
  const magicDropdownRef = useRef(null)
  const magicSearchTimerRef = useRef(null)
  const magicSearchRequestRef = useRef(null)
  const autofillRequestRef = useRef(null)
  const skipNextMagicSearchRef = useRef('')
  const [activeTab, setActiveTab] = useState('search')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [isSearching, setIsSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [addingAnimeIds, setAddingAnimeIds] = useState(() => new Set())
  const [manualForm, setManualForm] = useState(emptyManualForm)
  const [posterFile, setPosterFile] = useState(null)
  const [manualError, setManualError] = useState('')
  const [isCreating, setIsCreating] = useState(false)
  const [magicSearchResults, setMagicSearchResults] = useState([])
  const [isMagicSearching, setIsMagicSearching] = useState(false)
  const [isAutofilling, setIsAutofilling] = useState(false)
  const [magicSearchError, setMagicSearchError] = useState('')
  const [isMagicDropdownOpen, setIsMagicDropdownOpen] = useState(false)
  const existingAnimeKeys = useMemo(
    () => new Set(existingAnimeItems.map(getAnimeIdentityKey).filter(Boolean)),
    [existingAnimeItems],
  )

  const posterPreview = useMemo(() => {
    if (posterFile) return URL.createObjectURL(posterFile)
    return manualForm.posterUrl.trim()
  }, [manualForm.posterUrl, posterFile])

  useEffect(() => () => {
    if (posterPreview?.startsWith('blob:')) URL.revokeObjectURL(posterPreview)
  }, [posterPreview])

  const { contextSafe } = useGSAP(() => {
    if (!isOpen || !overlayRef.current || !modalRef.current) return undefined
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      gsap.set([overlayRef.current, modalRef.current, '.add-modal-piece'], { clearProps: 'all' })
      return undefined
    }

    const timeline = gsap.timeline()
    timeline
      .fromTo(overlayRef.current, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.14 })
      .fromTo(
        modalRef.current,
        { autoAlpha: 0, scale: 0.7, y: -50, rotation: 2 },
        {
          autoAlpha: 1,
          scale: 1,
          y: 0,
          rotation: 0,
          duration: 0.62,
          ease: 'back.out(1.5)',
          clearProps: 'transform,opacity,visibility',
        },
        0,
      )
      .fromTo(
        '.add-modal-piece',
        { autoAlpha: 0, y: -16, scale: 0.96 },
        {
          autoAlpha: 1,
          y: 0,
          scale: 1,
          duration: 0.3,
          stagger: 0.04,
          ease: 'back.out(1.5)',
          clearProps: 'transform,opacity,visibility',
        },
        0.18,
      )

    return () => timeline.kill()
  }, {
    scope: overlayRef,
    dependencies: [isOpen],
    revertOnUpdate: true,
  })

  const playExit = contextSafe((afterExit = onClose) => {
    if (!modalRef.current || !overlayRef.current || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      afterExit()
      return
    }
    gsap.killTweensOf([modalRef.current, overlayRef.current])
    gsap.timeline({ onComplete: afterExit })
      .to(modalRef.current, {
        y: 50,
        scale: 0.82,
        rotation: -2,
        autoAlpha: 0,
        duration: 0.24,
        ease: 'power3.in',
      })
      .to(overlayRef.current, { autoAlpha: 0, duration: 0.12 }, '-=0.08')
  })

  useGSAP(() => {
    if (
      !isMagicDropdownOpen
      || !magicDropdownRef.current
      || window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
      return undefined
    }

    const dropdownTween = gsap.fromTo(
      magicDropdownRef.current,
      {
        autoAlpha: 0,
        scaleY: 0,
        transformOrigin: 'top center',
      },
      {
        autoAlpha: 1,
        scaleY: 1,
        duration: 0.38,
        ease: 'back.out(1.5)',
        clearProps: 'transform,transformOrigin,opacity,visibility',
      },
    )

    return () => dropdownTween.kill()
  }, {
    scope: magicSearchRef,
    dependencies: [isMagicDropdownOpen],
  })

  const playAutofillShockwave = contextSafe((fieldNames) => {
    if (
      !modalRef.current
      || fieldNames.length === 0
      || window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ) {
      return
    }

    const filledFields = Array.from(
      modalRef.current.querySelectorAll('[data-autofill-field]'),
    ).filter((field) => fieldNames.includes(field.dataset.autofillField))

    if (filledFields.length === 0) return

    gsap.killTweensOf(filledFields)
    gsap.fromTo(
      filledFields,
      {
        backgroundColor: '#ffffff',
        x: 0,
        y: 0,
      },
      {
        backgroundColor: '#ffe66d',
        x: 2,
        y: 2,
        duration: 0.16,
        repeat: 1,
        yoyo: true,
        stagger: 0.035,
        ease: 'power2.inOut',
        clearProps: 'transform,backgroundColor',
      },
    )
  })

  const busy = isCreating || addingAnimeIds.size > 0
  const handleClose = () => {
    if (!busy) playExit(onClose)
  }
  closeHandlerRef.current = handleClose

  useEffect(() => {
    if (!isOpen) {
      setActiveTab('search')
      setQuery('')
      setResults([])
      setSearchError('')
      setAddingAnimeIds(new Set())
      setManualForm(emptyManualForm)
      setPosterFile(null)
      setManualError('')
      setIsCreating(false)
      setMagicSearchResults([])
      setIsMagicSearching(false)
      setIsAutofilling(false)
      setMagicSearchError('')
      setIsMagicDropdownOpen(false)
      skipNextMagicSearchRef.current = ''
      window.clearTimeout(magicSearchTimerRef.current)
      magicSearchTimerRef.current = null
      magicSearchRequestRef.current?.abort()
      autofillRequestRef.current?.abort()
      if (posterInputRef.current) posterInputRef.current.value = ''
      return undefined
    }

    const previousOverflow = document.body.style.overflow
    const previousPaddingRight = document.body.style.paddingRight
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    if (scrollbarWidth > 0) document.body.style.paddingRight = `${scrollbarWidth}px`
    document.body.style.overflow = 'hidden'

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') closeHandlerRef.current?.()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen])

  useEffect(() => {
    if (!isOpen || activeTab !== 'search') return undefined
    const keyword = query.trim()
    if (!keyword) {
      setResults([])
      setSearchError('')
      setIsSearching(false)
      return undefined
    }

    const controller = new AbortController()
    const timer = window.setTimeout(async () => {
      setIsSearching(true)
      setSearchError('')
      try {
        const response = await api.get('showcase/', {
          params: { search: keyword, page_size: 'all', _fresh: Date.now() },
          signal: controller.signal,
        })
        setResults(normalizeShowcaseResults(response.data))
      } catch (requestError) {
        if (requestError.code !== 'ERR_CANCELED') setSearchError('搜索失败，请稍后重试。')
      } finally {
        if (!controller.signal.aborted) setIsSearching(false)
      }
    }, 350)

    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [activeTab, isOpen, query])

  const performMagicSearch = useCallback(async (rawKeyword) => {
    const keyword = rawKeyword.trim()
    if (!keyword) {
      setMagicSearchResults([])
      setMagicSearchError('请先输入需要查找的番剧名称。')
      setIsMagicDropdownOpen(true)
      return
    }

    magicSearchRequestRef.current?.abort()
    const controller = new AbortController()
    magicSearchRequestRef.current = controller
    setIsMagicSearching(true)
    setMagicSearchError('')
    setIsMagicDropdownOpen(true)

    try {
      const response = await api.get('external/search/', {
        params: { q: keyword },
        signal: controller.signal,
      })
      setMagicSearchResults(normalizeResults(response.data))
    } catch (requestError) {
      if (requestError.code !== 'ERR_CANCELED') {
        setMagicSearchResults([])
        setMagicSearchError(
          requestError.response?.data?.detail
          || '智能搜索暂时不可用，请稍后重试。',
        )
      }
    } finally {
      if (magicSearchRequestRef.current === controller) {
        setIsMagicSearching(false)
        magicSearchRequestRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    if (!isOpen || activeTab !== 'manual') return undefined
    const keyword = manualForm.chineseTitle.trim()
    if (skipNextMagicSearchRef.current === keyword) {
      skipNextMagicSearchRef.current = ''
      return undefined
    }
    skipNextMagicSearchRef.current = ''

    if (!keyword) {
      magicSearchRequestRef.current?.abort()
      setMagicSearchResults([])
      setMagicSearchError('')
      setIsMagicSearching(false)
      setIsMagicDropdownOpen(false)
      return undefined
    }

    magicSearchTimerRef.current = window.setTimeout(() => {
      performMagicSearch(keyword)
    }, 500)
    return () => {
      window.clearTimeout(magicSearchTimerRef.current)
      magicSearchTimerRef.current = null
      magicSearchRequestRef.current?.abort()
    }
  }, [
    activeTab,
    isOpen,
    manualForm.chineseTitle,
    performMagicSearch,
  ])

  useEffect(() => {
    if (!isMagicDropdownOpen) return undefined
    const closeDropdown = (event) => {
      if (!magicSearchRef.current?.contains(event.target)) {
        setIsMagicDropdownOpen(false)
      }
    }
    document.addEventListener('pointerdown', closeDropdown)
    return () => document.removeEventListener('pointerdown', closeDropdown)
  }, [isMagicDropdownOpen])

  useEffect(() => () => {
    window.clearTimeout(magicSearchTimerRef.current)
    magicSearchRequestRef.current?.abort()
    autofillRequestRef.current?.abort()
  }, [])

  if (!isOpen) return null

  const handleAddExisting = async (anime) => {
    setAddingAnimeIds((current) => new Set(current).add(anime.id))
    try {
      const response = await api.post('records/add/', {
        anime_id: anime.id,
        watch_status: 'plan',
      })
      setResults((current) => current.map((item) => (
        item.id === anime.id ? { ...item, is_added: true } : item
      )))
      onRecordAdded(response.data)
      showToast(`${anime.chinese_title}已加入手账。`, 'success')
    } catch (requestError) {
      const detail = requestError.response?.data
      const message = Object.values(detail ?? {}).flat(Infinity).join(' ') || '加入失败，请稍后重试。'
      showToast(message, 'error')
    } finally {
      setAddingAnimeIds((current) => {
        const next = new Set(current)
        next.delete(anime.id)
        return next
      })
    }
  }

  const updateManualField = (event) => {
    const { name, value } = event.target
    if (name === 'posterUrl' && value.trim()) {
      setPosterFile(null)
      if (posterInputRef.current) posterInputRef.current.value = ''
    }
    setManualForm((current) => ({ ...current, [name]: value }))
  }

  const handleSelectAnime = async (subjectId, name) => {
    magicSearchRequestRef.current?.abort()
    autofillRequestRef.current?.abort()
    const controller = new AbortController()
    autofillRequestRef.current = controller
    setIsAutofilling(true)
    setMagicSearchError('')

    try {
      const response = await api.get('external/autofill/', {
        params: {
          id: subjectId,
          name,
        },
        signal: controller.signal,
      })
      const scraped = response.data ?? {}
      const keepExistingWhenBlank = (nextValue, currentValue) => (
        typeof nextValue === 'string' && nextValue.trim()
          ? nextValue
          : currentValue
      )
      const formattedChineseTitle = formatBangumiChineseTitle(
        scraped.chineseTitle || name,
      )
      const autofillFieldMap = {
        chineseTitle: formattedChineseTitle,
        japaneseTitle: scraped.japaneseTitle,
        airingPeriod: scraped.airingPeriod,
        studio: scraped.studio,
        episodes: scraped.episodes,
        tags: scraped.tags,
        description: scraped.description,
        posterUrl: scraped.posterUrl,
        baikeUrl: scraped.baikeUrl,
      }
      const filledFieldNames = Object.entries(autofillFieldMap)
        .filter(([, value]) => typeof value === 'string' && value.trim())
        .map(([fieldName]) => fieldName)

      skipNextMagicSearchRef.current = formattedChineseTitle
      setManualForm((current) => ({
        ...current,
        chineseTitle: keepExistingWhenBlank(
          formattedChineseTitle,
          current.chineseTitle,
        ),
        japaneseTitle: keepExistingWhenBlank(
          scraped.japaneseTitle,
          current.japaneseTitle,
        ),
        airingPeriod: keepExistingWhenBlank(
          scraped.airingPeriod,
          current.airingPeriod,
        ),
        studio: keepExistingWhenBlank(scraped.studio, current.studio),
        episodes: keepExistingWhenBlank(scraped.episodes, current.episodes),
        tags: keepExistingWhenBlank(scraped.tags, current.tags),
        description: keepExistingWhenBlank(
          scraped.description,
          current.description,
        ),
        posterUrl: keepExistingWhenBlank(
          scraped.posterUrl,
          current.posterUrl,
        ),
        baikeUrl: keepExistingWhenBlank(
          scraped.baikeUrl,
          current.baikeUrl,
        ),
      }))
      if (scraped.posterUrl) {
        setPosterFile(null)
        if (posterInputRef.current) posterInputRef.current.value = ''
      }
      setMagicSearchResults([])
      setIsMagicDropdownOpen(false)
      setManualError('')
      window.requestAnimationFrame(() => {
        playAutofillShockwave(filledFieldNames)
      })
      showToast('番剧资料已自动填充，请检查后再创建。', 'success')
    } catch (requestError) {
      if (requestError.code !== 'ERR_CANCELED') {
        setMagicSearchError(
          requestError.response?.data?.detail
          || '自动填充失败，请稍后重试。',
        )
        setIsMagicDropdownOpen(true)
      }
    } finally {
      if (autofillRequestRef.current === controller) {
        setIsAutofilling(false)
        autofillRequestRef.current = null
      }
    }
  }

  const handlePosterChange = (event) => {
    const file = event.target.files?.[0] ?? null
    setManualError('')
    const fileError = getImageFileError(file, '海报')
    if (fileError) {
      setPosterFile(null)
      event.target.value = ''
      setManualError(fileError)
      return
    }
    setPosterFile(file)
    if (file) {
      setManualForm((current) => ({ ...current, posterUrl: '' }))
    }
  }

  const handleCreateNew = async (event) => {
    event.preventDefault()
    setManualError('')
    if (!manualForm.chineseTitle.trim() || !manualForm.airingPeriod.trim()) {
      setManualError('番剧中文名和放送季度为必填项。')
      return
    }

    const score = manualForm.personalScore === '' ? null : Number(manualForm.personalScore)
    if (score !== null && (!Number.isFinite(score) || score < 0 || score > 10)) {
      setManualError('评分必须是 0 到 10 之间的数字。')
      return
    }

    const animeData = {
      chinese_title: manualForm.chineseTitle.trim(),
      japanese_title: manualForm.japaneseTitle.trim(),
      airing_period: manualForm.airingPeriod.trim(),
      studio: manualForm.studio.trim(),
      episodes: manualForm.episodes.trim(),
      description: manualForm.description.trim(),
      poster_url: manualForm.posterUrl.trim(),
      baike_url: manualForm.baikeUrl.trim(),
      base_tags: [...new Set(manualForm.tags.split(/[,，\n]/).map((tag) => tag.trim()).filter(Boolean))],
    }

    const formData = new FormData()
    formData.append('anime_data', JSON.stringify(animeData))
    formData.append('watch_status', manualForm.watchStatus)
    formData.append('review', manualForm.review)
    if (score !== null) formData.append('personal_score', String(score))
    if (posterFile) formData.append('poster_image', posterFile)

    setIsCreating(true)
    try {
      const response = await api.post('records/add/', formData)
      onRecordAdded(response.data)
      showToast(`${animeData.chinese_title}已加入手账。`, 'success')
      playExit(onClose)
    } catch (requestError) {
      const detail = requestError.response?.data
      const message = Object.values(detail ?? {}).flat(Infinity).join(' ') || '创建失败，请检查填写内容。'
      setManualError(message)
      showToast(message, 'error')
    } finally {
      setIsCreating(false)
    }
  }

  return (
    <div
      ref={overlayRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="addAnimeModalTitle"
      className="fixed inset-0 z-[110] flex items-center justify-center overflow-y-auto bg-zinc-950/80 p-2 backdrop-blur-sm sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) handleClose()
      }}
    >
      <section ref={modalRef} className="relative my-auto flex max-h-[96vh] w-full max-w-5xl flex-col overflow-hidden border-4 border-black bg-[#fff5ee] text-black shadow-[12px_12px_0_#111]" onMouseDown={(event) => event.stopPropagation()}>
        <header className="add-modal-piece flex shrink-0 items-start justify-between gap-4 border-b-4 border-black bg-[#ff8fab] px-5 py-4 sm:px-6">
          <div>
            <span className="inline-block border-2 border-black bg-[#ffe66d] px-2 py-1 text-[10px] font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">Add Anime</span>
            <h2 id="addAnimeModalTitle" className="mt-3 text-2xl font-black">添加番剧</h2>
            <p className="mt-1 text-sm font-bold">关联公共资料，或亲手创建一部全新番剧。</p>
          </div>
          <button type="button" onClick={handleClose} disabled={busy} aria-label="关闭添加番剧弹窗" className="flex h-10 w-10 items-center justify-center border-[3px] border-black bg-white text-lg shadow-[4px_4px_0_#111] transition-none hover:bg-[#ff6b6b] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50">
            <i className="fa-solid fa-xmark"></i>
          </button>
        </header>

        <div className="add-modal-piece mx-5 mt-4 grid shrink-0 grid-cols-2 border-4 border-black border-b-0 bg-zinc-300 shadow-[6px_6px_0_#111] sm:mx-6" role="tablist" aria-label="添加方式">
          {[
            ['search', 'fa-magnifying-glass', '搜索轩皇的番剧库'],
            ['manual', 'fa-wand-magic-sparkles', '智能录入新番'],
          ].map(([value, icon, label], index) => (
            <button key={value} type="button" role="tab" aria-selected={activeTab === value} onClick={() => setActiveTab(value)} className={`flex items-center justify-center gap-2 px-3 py-3 text-sm font-black transition-none ${index === 1 ? 'border-l-4 border-black' : ''} ${activeTab === value ? 'relative translate-y-1 bg-[#ffe66d]' : 'bg-zinc-300 text-zinc-600 hover:bg-white hover:text-black'}`}>
              <i className={`fa-solid ${icon}`}></i>{label}
            </button>
          ))}
        </div>

        <div className="mx-5 mb-5 flex-1 overflow-y-auto border-4 border-black bg-white custom-scrollbar shadow-[6px_6px_0_#4ecdc4] sm:mx-6">
          {activeTab === 'search' && (
            <div className="p-4 sm:p-5">
              <div className="add-modal-piece sticky top-0 z-10 border-[3px] border-black bg-[#4ecdc4] p-3 shadow-[5px_5px_0_#111]">
                <label htmlFor="animeLibrarySearch" className={labelClass}>搜索中文名、日文名、制作公司或放送季度</label>
                <div className="relative">
                  <i className="fa-solid fa-magnifying-glass absolute left-4 top-1/2 -translate-y-1/2 text-black"></i>
                  <input id="animeLibrarySearch" autoFocus value={query} onChange={(event) => setQuery(event.target.value)} className={`${inputClass} py-3 pl-12 pr-12`} placeholder="例如：轻音少女、京都动画、2025-10..." />
                  {isSearching && <i className="fa-solid fa-spinner absolute right-4 top-1/2 -translate-y-1/2 animate-spin text-black"></i>}
                </div>
              </div>

              {!query.trim() && (
                <div className="add-modal-piece relative my-5 overflow-hidden border-4 border-black bg-[#fff5ee] px-5 py-9 text-center shadow-[7px_7px_0_#ff8fab]">
                  <span className="absolute -left-7 -top-7 h-20 w-20 rotate-12 border-4 border-black bg-[#4ecdc4]" aria-hidden="true"></span>
                  <span className="absolute -bottom-8 -right-5 h-20 w-20 rounded-full border-4 border-black bg-[#ffe66d]" aria-hidden="true"></span>
                  <span className="relative mx-auto flex h-20 w-20 -rotate-3 items-center justify-center border-4 border-black bg-[#ff8fab] text-3xl text-black shadow-[6px_6px_0_#111]">
                    <i className="fa-solid fa-book-atlas"></i>
                  </span>
                  <span className="relative mt-6 inline-flex rotate-1 border-2 border-black bg-[#4ecdc4] px-3 py-1 text-[10px] font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">
                    LIBRARY SEARCH
                  </span>
                  <p className="relative mt-4 text-xl font-black">输入关键字开始搜索公共番剧库</p>
                  <p className="relative mx-auto mt-3 max-w-lg border-l-[6px] border-black bg-white px-4 py-2 text-xs font-bold leading-6 text-zinc-600 shadow-[3px_3px_0_#4ecdc4]">
                    支持中文名、日文名、制作公司，以及 2025-10 格式的放送季度。
                  </p>
                </div>
              )}
              {searchError && <p role="alert" className="mt-5 border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-sm font-black shadow-[4px_4px_0_#111]">{searchError}</p>}
              {query.trim() && !isSearching && !searchError && results.length === 0 && (
                <div className="add-modal-piece relative my-5 overflow-hidden border-4 border-black bg-[#fff5ee] px-5 py-10 text-center shadow-[7px_7px_0_#4ecdc4]">
                  <span className="absolute -left-8 -top-8 h-20 w-20 rotate-12 border-4 border-black bg-[#ff8fab]" aria-hidden="true"></span>
                  <span className="absolute -bottom-9 -right-6 h-24 w-24 rounded-full border-4 border-black bg-[#ffe66d]" aria-hidden="true"></span>
                  <span className="relative mx-auto flex h-20 w-20 -rotate-3 items-center justify-center border-4 border-black bg-white text-3xl text-black shadow-[6px_6px_0_#ff6b6b]">
                    <i className="fa-solid fa-magnifying-glass-minus"></i>
                  </span>
                  <span className="relative mt-6 inline-flex rotate-1 border-2 border-black bg-[#ff8fab] px-3 py-1 text-[10px] font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">
                    NO MATCH
                  </span>
                  <p className="relative mt-4 text-xl font-black text-black">轩皇的番剧库中没有匹配结果</p>
                  <p className="relative mx-auto mt-3 max-w-lg border-2 border-black bg-white px-4 py-2 text-xs font-bold leading-6 text-zinc-600 shadow-[3px_3px_0_#ffe66d]">
                    换一个番名、制作公司或季度试试，也可以切换到“智能录入新番”创建自己的记录。
                  </p>
                </div>
              )}

              <div className="mt-5 grid gap-3 sm:grid-cols-2">
                {results.map((anime, index) => {
                  const isAdded = anime.is_added
                    || existingAnimeIds.has(anime.id)
                    || existingAnimeKeys.has(getAnimeIdentityKey(anime))
                  const isAdding = addingAnimeIds.has(anime.id)
                  const poster = anime.poster_image || anime.poster_url
                  return (
                    <article key={anime.id} className={`add-modal-piece flex items-center gap-3 border-[3px] border-black p-3 shadow-[5px_5px_0_#111] ${index % 2 ? 'bg-[#fff2a8]' : 'bg-[#ffb0c8]'}`}>
                      <div className="h-24 w-[72px] shrink-0 overflow-hidden border-[3px] border-black bg-white">
                        {poster ? <img src={poster} alt={`${anime.chinese_title} 海报`} className="h-full w-full object-cover" /> : <div className="flex h-full items-center justify-center"><i className="fa-solid fa-image text-2xl"></i></div>}
                      </div>
                      <div className="min-w-0 flex-1">
                        <h3 className="truncate font-black">{anime.chinese_title}</h3>
                        <p className="mt-1 truncate text-xs font-bold text-zinc-600">{anime.japanese_title || '暂无日文名'}</p>
                        <p className="mt-2 text-[11px] font-black">{anime.airing_period}</p>
                      </div>
                      <button type="button" disabled={isAdded || isAdding} onClick={() => handleAddExisting(anime)} className="shrink-0 border-[3px] border-black bg-[#4ecdc4] px-3 py-2 text-xs font-black shadow-[4px_4px_0_#111] transition-none active:translate-x-1 active:translate-y-1 active:shadow-none disabled:bg-zinc-300 disabled:shadow-none">
                        <i className={`fa-solid ${isAdding ? 'fa-spinner animate-spin' : isAdded ? 'fa-circle-check' : 'fa-plus'} mr-1.5`}></i>{isAdding ? '加入中' : isAdded ? '已加入' : '加入手账'}
                      </button>
                    </article>
                  )
                })}
              </div>
            </div>
          )}

          {activeTab === 'manual' && (
            <form onSubmit={handleCreateNew} className="p-4 sm:p-5">
              <div className="grid gap-5 lg:grid-cols-[220px_1fr]">
                <aside className="add-modal-piece mx-auto w-full max-w-[220px] lg:mx-0">
                  <label htmlFor="newPosterFile" className="group flex aspect-[3/4] cursor-pointer flex-col items-center justify-center overflow-hidden border-4 border-black bg-[#4ecdc4] text-center shadow-[7px_7px_0_#ff8fab]">
                    {posterPreview
                      ? <img src={posterPreview} alt="海报预览" className="h-full w-full object-cover" />
                      : <><i className="fa-solid fa-file-arrow-up text-5xl"></i><span className="mt-3 border-2 border-black bg-[#ffe66d] px-3 py-1 text-xs font-black">选择本地海报</span><span className="mt-2 text-[10px] font-black">JPG / PNG / WebP · 最大 5MB</span></>}
                    <input ref={posterInputRef} id="newPosterFile" type="file" accept={IMAGE_FILE_ACCEPT} onChange={handlePosterChange} className="sr-only" />
                  </label>
                  {posterFile && <p className="mt-3 break-all border-2 border-black bg-[#ffe66d] p-2 text-[10px] font-black">{posterFile.name}</p>}
                  <label htmlFor="newPosterUrl" className={`${labelClass} mt-4`}>或填写网络图片 URL</label>
                  <input id="newPosterUrl" name="posterUrl" type="url" value={manualForm.posterUrl} onChange={updateManualField} className={inputClass} placeholder="https://..." data-autofill-field="posterUrl" />
                </aside>

                <div className="add-modal-piece grid content-start gap-4 sm:grid-cols-2">
                  <div ref={magicSearchRef} className="relative z-50">
                    <label htmlFor="newAnimeChineseTitle" className={labelClass}>
                      番剧中文名 *
                    </label>
                    <div className="flex items-stretch gap-2">
                      <input
                        id="newAnimeChineseTitle"
                        required
                        name="chineseTitle"
                        value={manualForm.chineseTitle}
                        onChange={updateManualField}
                        onFocus={() => {
                          if (
                            magicSearchResults.length
                            || isMagicSearching
                            || magicSearchError
                          ) {
                            setIsMagicDropdownOpen(true)
                          }
                        }}
                        className={`${inputClass} min-w-0 flex-1`}
                        placeholder="输入名称，停顿后智能搜索"
                        autoComplete="off"
                        data-autofill-field="chineseTitle"
                      />
                      <button
                        type="button"
                        onClick={() => {
                          window.clearTimeout(magicSearchTimerRef.current)
                          magicSearchTimerRef.current = null
                          performMagicSearch(manualForm.chineseTitle)
                        }}
                        disabled={isMagicSearching || isAutofilling}
                        aria-label="从 Bangumi 自动搜索番剧资料"
                        title="自动搜索并填充"
                        className="flex w-12 shrink-0 items-center justify-center border-[3px] border-black bg-[#ffe66d] text-lg text-black shadow-[4px_4px_0_#ff2f92] transition-none hover:bg-[#ff8fab] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:cursor-wait disabled:bg-zinc-300 disabled:shadow-none"
                      >
                        <i className={`fa-solid ${isMagicSearching ? 'fa-spinner animate-spin' : 'fa-magnifying-glass'}`} />
                      </button>
                    </div>

                    {isMagicDropdownOpen && (
                      <div ref={magicDropdownRef} className="absolute left-0 right-0 top-[calc(100%+0.55rem)] z-[70] max-h-80 origin-top overflow-y-auto border-4 border-black bg-white p-2 shadow-[8px_8px_0_#111] custom-scrollbar">
                        <div className="mb-2 flex items-center justify-between border-b-2 border-black bg-[#ffe66d] px-3 py-2 text-[10px] font-black tracking-[0.12em]">
                          <span>
                            <i className="fa-solid fa-satellite-dish mr-2" />
                            MAGIC METADATA SEARCH
                          </span>
                          <button
                            type="button"
                            onClick={() => setIsMagicDropdownOpen(false)}
                            aria-label="关闭智能搜索结果"
                            className="flex h-6 w-6 items-center justify-center border-2 border-black bg-white active:translate-x-0.5 active:translate-y-0.5"
                          >
                            <i className="fa-solid fa-xmark" />
                          </button>
                        </div>

                        {isMagicSearching && (
                          <div className="flex items-center justify-center gap-3 bg-[#fff5ee] px-4 py-8 text-sm font-black">
                            <i className="fa-solid fa-spinner animate-spin text-xl text-[#ff2f92]" />
                            正在搜索 Bangumi…
                          </div>
                        )}

                        {!isMagicSearching && isAutofilling && (
                          <div className="flex items-center justify-center gap-3 bg-[#4ecdc4] px-4 py-8 text-sm font-black">
                            <i className="fa-solid fa-wand-magic-sparkles animate-pulse text-xl" />
                            正在读取完整资料…
                          </div>
                        )}

                        {!isMagicSearching && !isAutofilling && magicSearchError && (
                          <p role="alert" className="border-2 border-black bg-[#ff8fab] px-3 py-3 text-xs font-black leading-5">
                            <i className="fa-solid fa-triangle-exclamation mr-2" />
                            {magicSearchError}
                          </p>
                        )}

                        {!isMagicSearching
                          && !isAutofilling
                          && !magicSearchError
                          && magicSearchResults.length === 0 && (
                          <p className="bg-[#fff5ee] px-3 py-7 text-center text-xs font-black text-zinc-600">
                            没有找到匹配条目，换一个名称试试。
                          </p>
                        )}

                        {!isMagicSearching
                          && !isAutofilling
                          && magicSearchResults.map((anime) => (
                          <button
                            key={anime.id}
                            type="button"
                            onClick={() => handleSelectAnime(
                              anime.id,
                              anime.chinese_title,
                            )}
                            className="group flex w-full items-center gap-3 border-b-2 border-black px-2 py-2 text-left last:border-b-0 hover:bg-[#b8f2e6] active:bg-[#ffb0c8]"
                          >
                            <span className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden border-[3px] border-black bg-[#ffe66d] shadow-[3px_3px_0_#ff2f92]">
                              {anime.thumbnail ? (
                                <img
                                  src={anime.thumbnail}
                                  alt={`${anime.chinese_title} 缩略图`}
                                  className="h-full w-full object-cover"
                                  loading="lazy"
                                />
                              ) : (
                                <i className="fa-solid fa-image text-lg" />
                              )}
                            </span>
                            <span className="min-w-0 flex-1">
                              <strong className="block truncate text-sm font-black">
                                {anime.chinese_title || '未命名番剧'}
                              </strong>
                              <span className="mt-1 block truncate text-[11px] font-bold text-zinc-600">
                                {anime.japanese_title || '暂无日文名'}
                              </span>
                            </span>
                            <i className="fa-solid fa-arrow-right shrink-0 group-hover:translate-x-1" />
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  <label className="block"><span className={labelClass}>番剧日文名</span><input name="japaneseTitle" value={manualForm.japaneseTitle} onChange={updateManualField} className={inputClass} data-autofill-field="japaneseTitle" /></label>
                  <label className="block"><span className={labelClass}>放送季度 *</span><input required name="airingPeriod" value={manualForm.airingPeriod} onChange={updateManualField} className={inputClass} placeholder="例如 2026-4" data-autofill-field="airingPeriod" /></label>
                  <label className="block"><span className={labelClass}>制作公司</span><input name="studio" value={manualForm.studio} onChange={updateManualField} className={inputClass} data-autofill-field="studio" /></label>
                  <label className="block"><span className={labelClass}>话数情况</span><input name="episodes" value={manualForm.episodes} onChange={updateManualField} className={inputClass} placeholder="例如 12、12+1" data-autofill-field="episodes" /></label>
                  <label className="block"><span className={labelClass}>萌娘百科 URL</span><input name="baikeUrl" type="url" value={manualForm.baikeUrl} onChange={updateManualField} className={inputClass} placeholder="https://zh.moegirl.org.cn/..." data-autofill-field="baikeUrl" /></label>
                  <label className="block sm:col-span-2"><span className={labelClass}>公共标签</span><input name="tags" value={manualForm.tags} onChange={updateManualField} className={inputClass} placeholder="使用逗号分隔，例如：日常，治愈，原创" data-autofill-field="tags" /></label>
                  <label className="flex min-h-[220px] flex-col"><span className={labelClass}>剧情简介</span><textarea name="description" value={manualForm.description} onChange={updateManualField} className={`${inputClass} min-h-[190px] flex-1 resize-y leading-6`} data-autofill-field="description" /></label>

                  <div className="border-[3px] border-black bg-[#ffe66d] p-4 shadow-[6px_6px_0_#ff8fab]">
                    <h3 className="mb-3 text-sm font-black"><i className="fa-solid fa-bookmark mr-2"></i>初始私人手账信息</h3>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <label className="block"><span className={labelClass}>观看状态</span><select name="watchStatus" value={manualForm.watchStatus} onChange={updateManualField} className={inputClass}><option value="plan">想看</option><option value="watching">在看</option><option value="completed">看过</option><option value="on_hold">搁置</option></select></label>
                      <label className="block"><span className={labelClass}>主观评分</span><input name="personalScore" type="number" min="0" max="10" step="0.1" value={manualForm.personalScore} onChange={updateManualField} className={inputClass} placeholder="0.0 - 10.0" /></label>
                      <label className="block sm:col-span-2"><span className={labelClass}>个人评价</span><textarea name="review" value={manualForm.review} onChange={updateManualField} className={`${inputClass} min-h-[112px] resize-y leading-6`} /></label>
                    </div>
                  </div>
                </div>
              </div>

              {manualError && <p role="alert" className="mt-5 border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-sm font-black shadow-[4px_4px_0_#111]"><i className="fa-solid fa-circle-exclamation mr-2"></i>{manualError}</p>}
              <div className="mt-6 flex justify-end gap-3 border-t-4 border-black pt-4">
                <button type="button" onClick={handleClose} disabled={isCreating} className={`${arcadeButton} bg-white`}>取消</button>
                <button type="submit" disabled={isCreating} className={`${arcadeButton} bg-[#ff4f9a]`}>
                  <i className={`fa-solid ${isCreating ? 'fa-spinner animate-spin' : 'fa-wand-magic-sparkles'} mr-2`}></i>{isCreating ? '创建中...' : '创建并加入手账'}
                </button>
              </div>
            </form>
          )}
        </div>
      </section>
    </div>
  )
}

export default AddAnimeModal
