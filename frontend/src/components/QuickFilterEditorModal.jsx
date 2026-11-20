import { useEffect, useMemo, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import api from '../api/api.js'
import { getTagClass } from '../utils/animeDisplay.js'
import { quickFilterSummary } from '../utils/quickFilter.js'

gsap.registerPlugin(useGSAP)

const emptyForm = { name: '', tags: [], title_keywords: '', exclude_tags: [], exclude_title_keywords: '', match_mode: 'any', year_from: '', year_to: '' }
const keywords = value => [...new Set(value.split(/[，,、\n]/).map(word => word.trim()).filter(Boolean))]
const fieldClass = 'mt-1 w-full border-[3px] border-black bg-white px-3 py-2.5 text-sm font-black text-black shadow-[4px_4px_0_#111] outline-none transition-none placeholder:text-zinc-400 focus:translate-x-1 focus:translate-y-1 focus:bg-[#fff2a8] focus:shadow-none'

function getRequestErrorMessage(requestError, fallback) {
  const detail = requestError.response?.data
  if (!detail || typeof detail === 'string') return fallback
  const message = Object.values(detail).flat(Infinity).filter(Boolean).join(' ')
  return message || fallback
}

function QuickFilterEditorModal({
  isOpen,
  onClose,
  availableTags,
  filters,
  onSaved,
  onDeleted,
  showToast,
  endpoint = 'quick-filters',
  description = '组合标签、标题词与年份条件，并保存到当前账号。',
}) {
  const overlayRef = useRef(null)
  const modalRef = useRef(null)
  const closeHandlerRef = useRef(null)
  const [form, setForm] = useState(emptyForm)
  const [savedOpen, setSavedOpen] = useState(() => typeof window !== 'undefined' && window.matchMedia('(min-width: 1024px)').matches)
  const [editingId, setEditingId] = useState(null)
  const [busy, setBusy] = useState(false)
  const [deletingId, setDeletingId] = useState(null)
  const [error, setError] = useState('')
  const [tagSearch, setTagSearch] = useState({ tags: '', exclude_tags: '' })

  const sortedTags = useMemo(
    () => [...availableTags].filter((tag) => tag !== '轩皇力推').sort(),
    [availableTags],
  )

  const { contextSafe } = useGSAP(() => {
    if (!isOpen || !overlayRef.current || !modalRef.current) return undefined

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      gsap.set([overlayRef.current, modalRef.current, '.quick-filter-piece'], {
        clearProps: 'all',
      })
      return undefined
    }

    const timeline = gsap.timeline()
    timeline
      .fromTo(
        overlayRef.current,
        { autoAlpha: 0 },
        { autoAlpha: 1, duration: 0.14, ease: 'power1.out' },
      )
      .fromTo(
        modalRef.current,
        { autoAlpha: 0, scale: 0.7, y: -40, rotation: -1.5 },
        {
          autoAlpha: 1,
          scale: 1,
          y: 0,
          rotation: 0,
          duration: 0.7,
          ease: 'elastic.out(1, 0.6)',
          clearProps: 'transform,opacity,visibility',
        },
        0,
      )
      .fromTo(
        '.quick-filter-piece',
        { autoAlpha: 0, y: -18, scale: 0.96 },
        {
          autoAlpha: 1,
          y: 0,
          scale: 1,
          duration: 0.34,
          stagger: 0.1,
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
    if (!overlayRef.current || !modalRef.current
      || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      afterExit()
      return
    }

    gsap.killTweensOf([modalRef.current, overlayRef.current])
    gsap.timeline({ onComplete: afterExit })
      .to(modalRef.current, {
        y: 50,
        scale: 0.9,
        autoAlpha: 0,
        duration: 0.22,
        ease: 'power3.in',
      })
      .to(overlayRef.current, {
        autoAlpha: 0,
        duration: 0.12,
        ease: 'power2.in',
      }, '-=0.07')
  })

  const handleClose = () => {
    if (!busy && !deletingId) playExit(onClose)
  }
  closeHandlerRef.current = handleClose

  useEffect(() => {
    if (!isOpen) return
    setForm(emptyForm)
    setTagSearch({ tags: '', exclude_tags: '' })
    setEditingId(null)
    setError('')
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) return undefined
    const previousOverflow = document.body.style.overflow
    const previousPaddingRight = document.body.style.paddingRight
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    const currentPaddingRight = Number.parseFloat(
      window.getComputedStyle(document.body).paddingRight,
    ) || 0
    if (scrollbarWidth > 0) {
      document.body.style.paddingRight = `${currentPaddingRight + scrollbarWidth}px`
    }
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

  if (!isOpen) return null

  const toggleTag = (tag, field = 'tags') => {
    setForm((current) => ({
      ...current,
      [field]: current[field].includes(tag)
        ? current[field].filter((item) => item !== tag)
        : current[field].length < 8 ? [...current[field], tag] : current[field],
    }))
  }

  const resetForm = () => {
    setEditingId(null)
    setForm(emptyForm)
    setTagSearch({ tags: '', exclude_tags: '' })
    setError('')
  }

  const beginEdit = (filter) => {
    setEditingId(filter.id)
    setForm({ name: filter.name, tags: [...filter.tags], match_mode: filter.match_mode,
      title_keywords: (filter.title_keywords ?? []).join('，'), exclude_tags: [...(filter.exclude_tags ?? [])],
      exclude_title_keywords: (filter.exclude_title_keywords ?? []).join('，'), year_from: filter.year_from ?? '', year_to: filter.year_to ?? '' })
    setTagSearch({ tags: '', exclude_tags: '' })
    setError('')
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    const name = form.name.trim()
    if (!name || !(form.tags.length || form.exclude_tags.length || keywords(form.title_keywords).length || keywords(form.exclude_title_keywords).length || form.year_from !== '' || form.year_to !== '')) {
      setError('请填写筛选名称，并至少设置一项包含、排除或年份条件。')
      return
    }
    if ([form.year_from, form.year_to].some(value => value !== '' && !/^[1-9]\d{3}$/.test(String(value)))) { setError('年份必须是 1000—9999 的四位整数。'); return }
    if (form.year_from !== '' && form.year_to !== '' && Number(form.year_from) > Number(form.year_to)) { setError('年份起点不能大于终点。'); return }
    if (form.tags.some(tag => form.exclude_tags.includes(tag))) { setError('标签不能同时包含与排除。'); return }

    setBusy(true)
    setError('')
    try {
      const payload = { ...form, name, title_keywords: keywords(form.title_keywords), exclude_title_keywords: keywords(form.exclude_title_keywords), year_from: form.year_from === '' ? null : Number(form.year_from), year_to: form.year_to === '' ? null : Number(form.year_to) }
      const response = editingId
        ? await api.patch(`${endpoint}/${editingId}/`, payload)
        : await api.post(`${endpoint}/`, payload)
      onSaved(response.data)
      showToast(editingId ? '快捷筛选已更新。' : '快捷筛选已创建。', 'success')
      playExit(() => {
        resetForm()
        onClose()
      })
    } catch (requestError) {
      setError(getRequestErrorMessage(requestError, '保存失败，请稍后重试。'))
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (filter) => {
    if (!window.confirm(`确定删除快捷筛选“${filter.name}”吗？`)) return
    setDeletingId(filter.id)
    try {
      await api.delete(`${endpoint}/${filter.id}/`)
      onDeleted(filter.id)
      if (editingId === filter.id) resetForm()
      showToast('快捷筛选已删除。', 'success')
    } catch {
      setError('删除失败，请稍后重试。')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div
      ref={overlayRef}
      className="memphis-modal-backdrop fixed inset-0 z-[130] flex items-center justify-center bg-zinc-950/80 p-3 backdrop-blur-sm sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="quickFilterEditorTitle"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) handleClose()
      }}
    >
      <section ref={modalRef} className="flex max-h-[94dvh] w-full max-w-6xl flex-col overflow-hidden border-4 border-black bg-[#fff5ee] text-black shadow-[12px_12px_0_#111]" onMouseDown={(event) => event.stopPropagation()}>
        <header className="quick-filter-piece relative flex shrink-0 items-start justify-between gap-4 border-b-4 border-black bg-[#4ecdc4] px-5 py-4 sm:px-6">
          <div className="pointer-events-none absolute right-20 top-3 grid grid-cols-4 gap-2 opacity-40" aria-hidden="true">
            {Array.from({ length: 8 }, (_, index) => <span key={index} className="h-2 w-2 rounded-full bg-black" />)}
          </div>
          <div>
            <span className="inline-block border-2 border-black bg-[#ffe66d] px-2 py-1 text-[10px] font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">
              Filter Workshop
            </span>
            <h2 id="quickFilterEditorTitle" className="mt-3 text-xl font-black sm:text-2xl">
              <i className="fa-solid fa-filter-circle-plus mr-2"></i>自定义快速筛选
            </h2>
            <p className="mt-1 text-xs font-bold text-zinc-700">{description}</p>
          </div>
          <button type="button" onClick={handleClose} disabled={busy || Boolean(deletingId)} className="relative flex h-10 w-10 shrink-0 items-center justify-center border-[3px] border-black bg-white text-lg shadow-[4px_4px_0_#111] transition-none hover:bg-[#ff6b6b] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50" aria-label="关闭自定义筛选编辑器">
            <i className="fa-solid fa-xmark"></i>
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col lg:grid lg:grid-cols-[minmax(200px,0.8fr)_minmax(0,2fr)] lg:grid-rows-[minmax(0,1fr)] lg:gap-4 lg:p-4">
          <aside className="quick-filter-piece mx-4 mt-3 shrink-0 border-[3px] border-black bg-white p-3 shadow-[4px_4px_0_#ffe66d] sm:mx-5 lg:m-0 lg:min-h-0 lg:max-h-full lg:self-start lg:overflow-y-auto">
            <h3 className="mb-2 inline-block border-2 border-black bg-[#ff8fab] px-3 py-1.5 text-xs font-black shadow-[3px_3px_0_#111]">
              <i className="fa-solid fa-bookmark mr-2"></i>已保存的组合
            </h3>
            <button type="button" aria-expanded={savedOpen} className="ml-2 text-xs font-bold underline" onClick={() => setSavedOpen(value => !value)}>{savedOpen ? '收起' : '展开'}（{filters.length}）</button>
            {savedOpen && <div className="grid max-h-28 gap-2 overflow-y-auto pb-1 pr-1 custom-scrollbar sm:grid-cols-2 lg:max-h-[52dvh] lg:grid-cols-1">
              {filters.length === 0 && (
                <div className="border-[3px] border-dashed border-black bg-[#f4f1ea] py-3 text-center text-xs font-black text-zinc-500">暂时没有自定义筛选</div>
              )}
              {filters.map((filter) => (
                <article key={filter.id} className={`border-2 border-black p-3 shadow-[4px_4px_0_#111] transition-none hover:bg-[#fff2a8] ${editingId === filter.id ? 'translate-x-1 translate-y-1 bg-[#bdf4ef] shadow-none' : 'bg-white'}`}>
                  <div className="flex items-center justify-between gap-2">
                    <strong className="truncate text-sm font-black text-black">{filter.name}</strong>
                    <div className="flex gap-2">
                      <button type="button" onClick={() => beginEdit(filter)} className="flex h-8 w-8 items-center justify-center border-2 border-black bg-[#4ecdc4] text-black shadow-[2px_2px_0_#111] transition-none active:translate-x-0.5 active:translate-y-0.5 active:shadow-none" aria-label={`编辑 ${filter.name}`}><i className="fa-solid fa-pen"></i></button>
                      <button type="button" disabled={deletingId === filter.id} onClick={() => handleDelete(filter)} className="flex h-8 w-8 items-center justify-center border-2 border-black bg-[#ff6b6b] text-black shadow-[2px_2px_0_#111] transition-none active:translate-x-0.5 active:translate-y-0.5 active:shadow-none disabled:opacity-50" aria-label={`删除 ${filter.name}`}><i className={`fa-solid ${deletingId === filter.id ? 'fa-spinner animate-spin' : 'fa-trash-can'}`}></i></button>
                    </div>
                  </div>
                  <p className="mt-2 text-[10px] font-bold leading-5 text-zinc-600">{quickFilterSummary(filter)}</p>
                </article>
              ))}
            </div>}
          </aside>

          <form onSubmit={handleSubmit} className="quick-filter-piece flex min-h-0 flex-1 flex-col">
            <div className="shrink-0 px-4 pt-3 sm:px-5"><div className="mb-3 flex items-center justify-between">
              <h3 className="border-2 border-black bg-white px-3 py-1.5 text-xs font-black shadow-[3px_3px_0_#111]">{editingId ? '修改组合' : '创建新组合'}</h3>
              {editingId && <button type="button" onClick={resetForm} className="border-2 border-black bg-[#f4f1ea] px-2.5 py-1 text-xs font-black shadow-[2px_2px_0_#111] active:translate-x-0.5 active:translate-y-0.5 active:shadow-none">取消编辑</button>}
            </div>
            <div className="grid gap-3 sm:grid-cols-2"><label className="block text-xs font-black text-black">筛选名称
              <input value={form.name} maxLength={30} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} className={fieldClass} placeholder="例如：治愈日常" />
            </label>
            <label className="block text-xs font-black text-black">匹配方式
              <select value={form.match_mode} onChange={(event) => setForm((current) => ({ ...current, match_mode: event.target.value }))} className={fieldClass}>
                <option value="any">包含条件命中任意一项</option>
                <option value="all">包含条件必须全部满足</option>
              </select>
            </label>
            </div></div>
            <div className="grid min-h-0 flex-1 gap-4 overflow-y-auto p-4 custom-scrollbar sm:px-5 md:grid-cols-2">
            <fieldset className="min-w-0 border-[3px] border-black bg-white p-3 md:col-span-2">
              <legend className="px-1 text-xs font-black">放送年份范围（含起止年份）</legend>
              <div className="grid grid-cols-2 gap-3">
                <label className="min-w-0 text-xs font-bold">起始年份<input aria-label="组合起始年份" type="number" min="1000" max="9999" step="1" placeholder="不限" value={form.year_from} onChange={event => setForm(current => ({ ...current, year_from: event.target.value }))} className={fieldClass} /></label>
                <label className="min-w-0 text-xs font-bold">结束年份<input aria-label="组合结束年份" type="number" min="1000" max="9999" step="1" placeholder="不限" value={form.year_to} onChange={event => setForm(current => ({ ...current, year_to: event.target.value }))} className={fieldClass} /></label>
              </div><p className="mt-2 text-[11px] font-bold">与包含、排除条件同时生效；设置范围后不匹配未知年份。</p>
            </fieldset>
            <section className="min-w-0 self-start border-[3px] border-black bg-[#ffe66d] p-3 shadow-[4px_4px_0_#ff8fab]" aria-label="包含条件">
            <h4 className="mb-3 text-sm font-black">包含条件</h4>
            <fieldset>
              <legend className="mb-2 text-xs font-black text-black">包含标签（最多 8 个）</legend>
              <input type="search" aria-label="搜索包含标签" placeholder="搜索标签…" value={tagSearch.tags} onChange={event => setTagSearch(current => ({ ...current, tags: event.target.value }))} className="mb-2 w-full min-w-0 border-2 border-black bg-white px-2 py-1.5 text-xs font-bold" />
              <div className="flex max-h-40 flex-wrap gap-2 overflow-y-auto border-[3px] border-black bg-[#fff5ee] p-3 shadow-[4px_4px_0_#111] custom-scrollbar">
                {[...new Set([...form.tags, ...sortedTags])].filter(tag => form.tags.includes(tag) || tag.toLocaleLowerCase().includes(tagSearch.tags.trim().toLocaleLowerCase())).map((tag) => {
                  const selected = form.tags.includes(tag)
                  return <button key={tag} type="button" disabled={!selected && form.tags.length >= 8} onClick={() => toggleTag(tag)} className={`rounded-full border-2 border-black px-2.5 py-1.5 text-xs font-black shadow-[2px_2px_0_#111] transition-none ${selected ? `${getTagClass(tag)} translate-x-1 translate-y-1 shadow-none` : 'bg-white text-black hover:bg-[#bdf4ef]'} disabled:cursor-not-allowed disabled:opacity-30`}><i className={`fa-solid ${selected ? 'fa-check' : 'fa-plus'} mr-1.5 text-[9px]`}></i>{tag}</button>
                })}
              </div>
            </fieldset>
            <label className="mt-4 block text-xs font-black">包含标题词（最多 4 个）
              <input value={form.title_keywords} onChange={event => setForm(current => ({ ...current, title_keywords: event.target.value }))} className={fieldClass} placeholder="逗号分隔；匹配中日文标题" />
            </label>
            </section><section className="min-w-0 self-start border-[3px] border-black bg-[#bdf4ef] p-3 shadow-[4px_4px_0_#ffe66d]" aria-label="排除条件">
            <h4 className="mb-3 text-sm font-black">排除条件</h4>
            <fieldset><legend className="mb-2 text-xs font-black">排除标签（最多 8 个）</legend>
              <input type="search" aria-label="搜索排除标签" placeholder="搜索标签…" value={tagSearch.exclude_tags} onChange={event => setTagSearch(current => ({ ...current, exclude_tags: event.target.value }))} className="mb-2 w-full min-w-0 border-2 border-black bg-white px-2 py-1.5 text-xs font-bold" />
              <div className="flex max-h-40 flex-wrap gap-2 overflow-y-auto border-[3px] border-black bg-[#fff5ee] p-3 custom-scrollbar">
                {[...new Set([...form.exclude_tags, ...sortedTags])].filter(tag => form.exclude_tags.includes(tag) || tag.toLocaleLowerCase().includes(tagSearch.exclude_tags.trim().toLocaleLowerCase())).map(tag => <button key={tag} type="button" disabled={!form.exclude_tags.includes(tag) && form.exclude_tags.length >= 8} onClick={() => toggleTag(tag, 'exclude_tags')}
                  className={`rounded-full border-2 border-black px-2.5 py-1.5 text-xs font-black ${form.exclude_tags.includes(tag) ? 'bg-[#ff8fab]' : 'bg-white'}`}>
                  {form.exclude_tags.includes(tag) ? '− ' : '+ '}{tag}</button>)}
              </div>
            </fieldset>
            <label className="mt-4 block text-xs font-black">排除标题词（最多 4 个）
              <input value={form.exclude_title_keywords} onChange={event => setForm(current => ({ ...current, exclude_title_keywords: event.target.value }))} className={fieldClass} placeholder="例如：总集篇，回顾" />
            </label>
            <p className="mt-3 text-xs font-bold leading-5">排除项命中任意一项即移除。可以只设置排除条件；OVA／剧场版标签和标题词不代表完整的作品类型识别。</p>
            </section></div><footer className="shrink-0 border-t-4 border-black bg-[#fff5ee] px-4 pb-4 pt-3 sm:px-5">
            {error && <p className="mb-3 border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-xs font-black text-black shadow-[4px_4px_0_#111]" role="alert"><i className="fa-solid fa-circle-exclamation mr-2"></i>{error}</p>}
            <button type="submit" disabled={busy} className="ml-auto block w-full border-[3px] border-black bg-[#ff4f9a] px-5 py-3 text-sm font-black text-black shadow-[6px_6px_0_#111] transition-none hover:bg-[#4ecdc4] active:translate-x-[6px] active:translate-y-[6px] active:shadow-none disabled:cursor-wait disabled:opacity-60">
              <i className={`fa-solid ${busy ? 'fa-spinner animate-spin' : editingId ? 'fa-floppy-disk' : 'fa-plus'} mr-2`}></i>{busy ? '保存中...' : editingId ? '保存修改' : '创建组合筛选'}
            </button>
            </footer>
          </form>
        </div>
      </section>
    </div>
  )
}

export default QuickFilterEditorModal
