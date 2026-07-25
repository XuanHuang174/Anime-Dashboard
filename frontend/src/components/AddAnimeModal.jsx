import { useEffect, useMemo, useState } from 'react'
import api from '../api/api.js'

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

function normalizeResults(data) {
  return Array.isArray(data) ? data : data.results ?? []
}

function AddAnimeModal({
  isOpen,
  onClose,
  existingAnimeIds,
  onRecordAdded,
  showToast,
}) {
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

  const posterPreview = useMemo(() => {
    if (posterFile) return URL.createObjectURL(posterFile)
    return manualForm.posterUrl.trim()
  }, [manualForm.posterUrl, posterFile])

  useEffect(() => () => {
    if (posterPreview?.startsWith('blob:')) URL.revokeObjectURL(posterPreview)
  }, [posterPreview])

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
      return undefined
    }

    const previousOverflow = document.body.style.overflow
    const previousPaddingRight = document.body.style.paddingRight
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    if (scrollbarWidth > 0) document.body.style.paddingRight = `${scrollbarWidth}px`
    document.body.style.overflow = 'hidden'

    const handleKeyDown = (event) => {
      if (event.key === 'Escape' && !isCreating) onClose()
    }
    window.addEventListener('keydown', handleKeyDown)

    return () => {
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isCreating, isOpen, onClose])

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
        const response = await api.get('animes/', {
          // Always consult the live library after an Admin or journal deletion.
          params: { search: keyword, _fresh: Date.now() },
          signal: controller.signal,
        })
        setResults(normalizeResults(response.data))
      } catch (requestError) {
        if (requestError.code !== 'ERR_CANCELED') {
          setSearchError('搜索失败，请稍后重试。')
        }
      } finally {
        if (!controller.signal.aborted) setIsSearching(false)
      }
    }, 350)

    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [activeTab, isOpen, query])

  if (!isOpen) return null

  const handleAddExisting = async (anime) => {
    setAddingAnimeIds((current) => new Set(current).add(anime.id))

    try {
      const response = await api.post('records/add/', {
        anime_id: anime.id,
        watch_status: 'plan',
      })
      onRecordAdded(response.data)
      showToast(`《${anime.chinese_title}》已加入手账。`, 'success')
    } catch (requestError) {
      const detail = requestError.response?.data
      const message = Object.values(detail ?? {}).flat(Infinity).join(' ')
        || '加入失败，请稍后重试。'
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
    setManualForm((current) => ({ ...current, [name]: value }))
  }

  const handlePosterChange = (event) => {
    const file = event.target.files?.[0] ?? null
    setManualError('')

    if (file && !file.type.startsWith('image/')) {
      setPosterFile(null)
      setManualError('请选择有效的图片文件。')
      return
    }
    if (file && file.size > 5 * 1024 * 1024) {
      setPosterFile(null)
      setManualError('海报图片不能超过 5MB。')
      return
    }

    setPosterFile(file)
  }

  const handleCreateNew = async (event) => {
    event.preventDefault()
    setManualError('')

    if (!manualForm.chineseTitle.trim() || !manualForm.airingPeriod.trim()) {
      setManualError('番剧中文名和放送季度为必填项。')
      return
    }

    const score = manualForm.personalScore === ''
      ? null
      : Number(manualForm.personalScore)
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
      base_tags: [...new Set(
        manualForm.tags
          .split(/[,，\n]/)
          .map((tag) => tag.trim())
          .filter(Boolean),
      )],
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
      showToast(`《${animeData.chinese_title}》已创建并加入手账。`, 'success')
      onClose()
    } catch (requestError) {
      const detail = requestError.response?.data
      const message = Object.values(detail ?? {}).flat(Infinity).join(' ')
        || '创建失败，请检查填写内容。'
      setManualError(message)
      showToast('创建失败，请检查填写内容。', 'error')
    } finally {
      setIsCreating(false)
    }
  }

  const inputClass = 'w-full rounded-xl border border-slate-200 border-b-[3px] border-b-slate-300 bg-white px-3 py-2.5 text-sm text-slate-700 shadow-sm outline-none transition focus:translate-y-[2px] focus:border-indigo-400 focus:border-b focus:ring-2 focus:ring-indigo-100 focus:shadow-inner'
  const labelClass = 'mb-1.5 block text-xs font-bold text-slate-500'

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="addAnimeModalTitle"
      className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/60 p-4 backdrop-blur-sm animate-[viewFadeIn_0.25s_ease-out_both]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !isCreating) onClose()
      }}
    >
      <section
        className="flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-3xl border border-white/50 bg-white shadow-2xl shadow-indigo-950/30"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="h-2 shrink-0 bg-gradient-to-r from-pink-400 via-purple-500 to-indigo-500"></div>

        <header className="flex items-start justify-between gap-4 px-6 py-5">
          <div>
            <h2 id="addAnimeModalTitle" className="text-2xl font-black text-slate-900">添加番剧</h2>
            <p className="mt-1 text-sm text-slate-500">关联公共资料，或亲手创建一部全新番剧。</p>
          </div>
          <button type="button" onClick={onClose} disabled={isCreating} aria-label="关闭添加番剧弹窗" className="rounded-full p-2 text-slate-400 transition hover:rotate-90 hover:bg-rose-50 hover:text-rose-500 disabled:opacity-50">
            <i className="fa-solid fa-xmark text-lg"></i>
          </button>
        </header>

        <nav className="mx-6 flex rounded-2xl border border-slate-200 bg-slate-100/80 p-1.5 shadow-inner" aria-label="添加方式">
          {[
            ['search', 'fa-magnifying-glass', '搜索轩皇的番剧库'],
            ['manual', 'fa-pen-to-square', '手动录入新番'],
          ].map(([value, icon, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setActiveTab(value)}
              className={`flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-bold transition-all ${activeTab === value ? 'bg-white text-indigo-600 shadow-md ring-1 ring-slate-200' : 'text-slate-500 hover:bg-white/60 hover:text-slate-700'}`}
            >
              <i className={`fa-solid ${icon}`}></i>{label}
            </button>
          ))}
        </nav>

        <div className="mt-4 flex-1 overflow-y-auto custom-scrollbar">
          {activeTab === 'search' && (
            <div className="px-6 pb-6">
              <div className="sticky top-0 z-10 -mx-6 border-y border-slate-100 bg-slate-50/95 px-6 py-4 backdrop-blur-md">
                <label htmlFor="animeLibrarySearch" className={labelClass}>搜索中文名、日文名、制作公司或放送季度</label>
                <div className="relative">
                  <i className="fa-solid fa-magnifying-glass absolute left-4 top-1/2 -translate-y-1/2 text-indigo-400"></i>
                  <input id="animeLibrarySearch" autoFocus value={query} onChange={(event) => setQuery(event.target.value)} className={`${inputClass} py-3.5 pl-12 pr-12`} placeholder="例如：轻音少女、京都动画、2025-10..." />
                  {isSearching && <i className="fa-solid fa-spinner absolute right-4 top-1/2 -translate-y-1/2 animate-spin text-indigo-500"></i>}
                </div>
              </div>

              {!query.trim() && (
                <div className="py-16 text-center text-slate-400">
                  <i className="fa-solid fa-book-atlas text-5xl text-indigo-200"></i>
                  <p className="mt-4 text-sm font-medium">输入关键字开始搜索公共番剧库</p>
                </div>
              )}
              {searchError && <p role="alert" className="mt-5 rounded-xl bg-rose-50 px-4 py-3 text-sm font-medium text-rose-600">{searchError}</p>}
              {query.trim() && !isSearching && !searchError && results.length === 0 && (
                <div className="py-16 text-center text-slate-400"><i className="fa-solid fa-face-frown-open text-4xl text-slate-300"></i><p className="mt-3 text-sm font-medium">没有找到匹配的番剧</p></div>
              )}

              <div className="mt-5 grid gap-3 sm:grid-cols-2">
                {results.map((anime) => {
                  const isAdded = existingAnimeIds.has(anime.id)
                  const isAdding = addingAnimeIds.has(anime.id)
                  const poster = anime.poster_image || anime.poster_url
                  return (
                    <article key={anime.id} className="flex items-center gap-3 rounded-2xl border border-slate-100 border-b-[4px] border-b-slate-200 bg-white p-3 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:border-b-indigo-300 hover:shadow-md">
                      <div className="h-24 w-[72px] shrink-0 overflow-hidden rounded-xl bg-gradient-to-br from-pink-100 to-indigo-100 shadow-inner">
                        {poster ? <img src={poster} alt={`${anime.chinese_title} 海报`} className="h-full w-full object-cover" /> : <div className="flex h-full items-center justify-center text-indigo-300"><i className="fa-solid fa-image text-2xl"></i></div>}
                      </div>
                      <div className="min-w-0 flex-1">
                        <h3 className="truncate font-black text-slate-800">{anime.chinese_title}</h3>
                        <p className="mt-1 truncate text-xs text-slate-400">{anime.japanese_title || '暂无日文名'}</p>
                        <p className="mt-2 text-[11px] font-bold text-indigo-500">{anime.airing_period}</p>
                      </div>
                      <button type="button" disabled={isAdded || isAdding} onClick={() => handleAddExisting(anime)} className="shrink-0 rounded-xl bg-gradient-to-r from-pink-500 to-indigo-500 px-3 py-2.5 text-xs font-bold text-white shadow-md transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-400 disabled:shadow-none">
                        <i className={`fa-solid ${isAdding ? 'fa-spinner animate-spin' : isAdded ? 'fa-circle-check' : 'fa-plus'} mr-1.5`}></i>{isAdding ? '加入中' : isAdded ? '已加入' : '加入手账'}
                      </button>
                    </article>
                  )
                })}
              </div>
            </div>
          )}

          {activeTab === 'manual' && (
            <form onSubmit={handleCreateNew} className="px-6 pb-6">
              <div className="grid gap-6 lg:grid-cols-[220px_1fr]">
                <aside>
                  <div className="aspect-[3/4] overflow-hidden rounded-2xl border-4 border-white bg-gradient-to-br from-pink-100 to-indigo-100 shadow-xl ring-1 ring-slate-200">
                    {posterPreview ? <img src={posterPreview} alt="海报预览" className="h-full w-full object-cover" /> : <div className="flex h-full flex-col items-center justify-center text-indigo-300"><i className="fa-solid fa-cloud-arrow-up text-4xl"></i><span className="mt-2 text-xs font-bold">海报预览</span></div>}
                  </div>
                  <label htmlFor="newPosterFile" className={`${labelClass} mt-4`}>上传海报（最大 5MB）</label>
                  <input id="newPosterFile" type="file" accept="image/*" onChange={handlePosterChange} className="block w-full text-xs text-slate-500 file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-3 file:py-2 file:font-bold file:text-indigo-600 hover:file:bg-indigo-100" />
                  <label htmlFor="newPosterUrl" className={`${labelClass} mt-4`}>或填写网络图片 URL</label>
                  <input id="newPosterUrl" name="posterUrl" type="url" value={manualForm.posterUrl} onChange={updateManualField} className={inputClass} placeholder="https://..." />
                </aside>

                <div className="grid content-start gap-4 sm:grid-cols-2">
                  <label className="block"><span className={labelClass}>番剧中文名 *</span><input required name="chineseTitle" value={manualForm.chineseTitle} onChange={updateManualField} className={inputClass} /></label>
                  <label className="block"><span className={labelClass}>番剧日文名</span><input name="japaneseTitle" value={manualForm.japaneseTitle} onChange={updateManualField} className={inputClass} /></label>
                  <label className="block"><span className={labelClass}>放送季度 *</span><input required name="airingPeriod" value={manualForm.airingPeriod} onChange={updateManualField} className={inputClass} placeholder="例如 2026-4" /></label>
                  <label className="block"><span className={labelClass}>制作公司</span><input name="studio" value={manualForm.studio} onChange={updateManualField} className={inputClass} /></label>
                  <label className="block"><span className={labelClass}>话数情况</span><input name="episodes" value={manualForm.episodes} onChange={updateManualField} className={inputClass} placeholder="例如 12、12+1" /></label>
                  <label className="block"><span className={labelClass}>萌娘百科 URL</span><input name="baikeUrl" type="url" value={manualForm.baikeUrl} onChange={updateManualField} className={inputClass} placeholder="https://zh.moegirl.org.cn/..." /></label>
                  <label className="block sm:col-span-2"><span className={labelClass}>公共标签</span><input name="tags" value={manualForm.tags} onChange={updateManualField} className={inputClass} placeholder="使用逗号分隔，例如：日常，治愈，原创" /></label>
                  <label className="block sm:col-span-2"><span className={labelClass}>剧情简介</span><textarea name="description" value={manualForm.description} onChange={updateManualField} className={`${inputClass} h-28 resize-y leading-6`} /></label>

                  <div className="sm:col-span-2 rounded-2xl border border-indigo-100 bg-indigo-50/60 p-4 shadow-inner">
                    <h3 className="mb-3 text-sm font-black text-indigo-700"><i className="fa-solid fa-bookmark mr-2"></i>初始私人手账信息</h3>
                    <div className="grid gap-4 sm:grid-cols-2">
                      <label className="block"><span className={labelClass}>观看状态</span><select name="watchStatus" value={manualForm.watchStatus} onChange={updateManualField} className={inputClass}><option value="plan">想看</option><option value="watching">在看</option><option value="completed">看过</option><option value="on_hold">搁置</option></select></label>
                      <label className="block"><span className={labelClass}>主观评分</span><input name="personalScore" type="number" min="0" max="10" step="0.1" value={manualForm.personalScore} onChange={updateManualField} className={inputClass} placeholder="0.0 - 10.0" /></label>
                      <label className="block sm:col-span-2"><span className={labelClass}>个人评价</span><textarea name="review" value={manualForm.review} onChange={updateManualField} className={`${inputClass} h-24 resize-y`} /></label>
                    </div>
                  </div>
                </div>
              </div>

              {manualError && <p role="alert" className="mt-5 rounded-xl bg-rose-50 px-4 py-3 text-sm font-medium text-rose-600"><i className="fa-solid fa-circle-exclamation mr-2"></i>{manualError}</p>}
              <div className="mt-6 flex justify-end gap-3 border-t border-slate-100 pt-5">
                <button type="button" onClick={onClose} disabled={isCreating} className="rounded-xl bg-slate-100 px-5 py-2.5 text-sm font-bold text-slate-600 transition hover:bg-slate-200 disabled:opacity-50">取消</button>
                <button type="submit" disabled={isCreating} className="rounded-xl bg-gradient-to-r from-pink-500 via-purple-500 to-indigo-500 px-6 py-2.5 text-sm font-bold text-white shadow-md transition hover:-translate-y-0.5 hover:shadow-lg disabled:cursor-not-allowed disabled:opacity-60">
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
