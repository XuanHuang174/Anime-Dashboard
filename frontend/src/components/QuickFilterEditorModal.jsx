import { useEffect, useMemo, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import api from '../api/api.js'
import { getTagClass } from '../utils/animeDisplay.js'

gsap.registerPlugin(useGSAP)

const emptyForm = { name: '', tags: [], match_mode: 'any' }
const fieldClass = 'mt-1 w-full border-[3px] border-black bg-white px-3 py-2.5 text-sm font-black text-black shadow-[4px_4px_0_#111] outline-none transition-none placeholder:text-zinc-400 focus:translate-x-1 focus:translate-y-1 focus:bg-[#fff2a8] focus:shadow-none'

function QuickFilterEditorModal({
  isOpen,
  onClose,
  availableTags,
  filters,
  onSaved,
  onDeleted,
  showToast,
  endpoint = 'quick-filters',
  description = '组合常用标签，并保存到当前账号。',
}) {
  const overlayRef = useRef(null)
  const modalRef = useRef(null)
  const closeHandlerRef = useRef(null)
  const [form, setForm] = useState(emptyForm)
  const [editingId, setEditingId] = useState(null)
  const [busy, setBusy] = useState(false)
  const [deletingId, setDeletingId] = useState(null)
  const [error, setError] = useState('')

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

  const toggleTag = (tag) => {
    setForm((current) => ({
      ...current,
      tags: current.tags.includes(tag)
        ? current.tags.filter((item) => item !== tag)
        : current.tags.length < 8 ? [...current.tags, tag] : current.tags,
    }))
  }

  const resetForm = () => {
    setEditingId(null)
    setForm(emptyForm)
    setError('')
  }

  const beginEdit = (filter) => {
    setEditingId(filter.id)
    setForm({ name: filter.name, tags: [...filter.tags], match_mode: filter.match_mode })
    setError('')
  }

  const handleSubmit = async (event) => {
    event.preventDefault()
    const name = form.name.trim()
    if (!name || form.tags.length === 0) {
      setError('请填写筛选名称，并至少选择一个标签。')
      return
    }

    setBusy(true)
    setError('')
    try {
      const payload = { ...form, name }
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
      const detail = requestError.response?.data
      setError(Object.values(detail ?? {}).flat(Infinity).join(' ') || '保存失败，请稍后重试。')
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
      <section ref={modalRef} className="flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden border-4 border-black bg-[#fff5ee] text-black shadow-[12px_12px_0_#111]" onMouseDown={(event) => event.stopPropagation()}>
        <header className="quick-filter-piece relative flex items-start justify-between gap-4 border-b-4 border-black bg-[#4ecdc4] px-5 py-4 sm:px-6">
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

        <div className="grid flex-1 gap-5 overflow-y-auto p-4 custom-scrollbar sm:p-5 md:grid-cols-[1fr_1.25fr]">
          <aside className="quick-filter-piece border-[3px] border-black bg-white p-4 shadow-[6px_6px_0_#ffe66d]">
            <h3 className="mb-3 inline-block border-2 border-black bg-[#ff8fab] px-3 py-1.5 text-xs font-black shadow-[3px_3px_0_#111]">
              <i className="fa-solid fa-bookmark mr-2"></i>已保存的组合
            </h3>
            <div className="max-h-[52vh] space-y-3 overflow-y-auto pr-1 custom-scrollbar">
              {filters.length === 0 && (
                <div className="border-[3px] border-dashed border-black bg-[#f4f1ea] py-8 text-center text-xs font-black text-zinc-500">暂时没有自定义筛选</div>
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
                  <p className="mt-2 text-[10px] font-bold leading-5 text-zinc-600">{filter.match_mode === 'all' ? '同时包含' : '包含任意'} · {[...filter.tags, ...(filter.title_keywords ?? []).map((keyword) => `标题:${keyword}`)].join(' / ')}</p>
                </article>
              ))}
            </div>
          </aside>

          <form onSubmit={handleSubmit} className="quick-filter-piece border-[3px] border-black bg-[#ffe66d] p-4 shadow-[6px_6px_0_#ff8fab]">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="border-2 border-black bg-white px-3 py-1.5 text-xs font-black shadow-[3px_3px_0_#111]">{editingId ? '修改组合' : '创建新组合'}</h3>
              {editingId && <button type="button" onClick={resetForm} className="border-2 border-black bg-[#f4f1ea] px-2.5 py-1 text-xs font-black shadow-[2px_2px_0_#111] active:translate-x-0.5 active:translate-y-0.5 active:shadow-none">取消编辑</button>}
            </div>
            <label className="block text-xs font-black text-black">筛选名称
              <input value={form.name} maxLength={30} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} className={fieldClass} placeholder="例如：治愈日常" />
            </label>
            <label className="mt-4 block text-xs font-black text-black">匹配方式
              <select value={form.match_mode} onChange={(event) => setForm((current) => ({ ...current, match_mode: event.target.value }))} className={fieldClass}>
                <option value="any">命中任意一个标签</option>
                <option value="all">必须包含全部标签</option>
              </select>
            </label>
            <fieldset className="mt-4">
              <legend className="mb-2 text-xs font-black text-black">选择标签（最多 8 个）</legend>
              <div className="flex max-h-48 flex-wrap gap-2 overflow-y-auto border-[3px] border-black bg-[#fff5ee] p-3 shadow-[4px_4px_0_#111] custom-scrollbar">
                {sortedTags.map((tag) => {
                  const selected = form.tags.includes(tag)
                  return <button key={tag} type="button" disabled={!selected && form.tags.length >= 8} onClick={() => toggleTag(tag)} className={`rounded-full border-2 border-black px-2.5 py-1.5 text-xs font-black shadow-[2px_2px_0_#111] transition-none ${selected ? `${getTagClass(tag)} translate-x-1 translate-y-1 shadow-none` : 'bg-white text-black hover:bg-[#bdf4ef]'} disabled:cursor-not-allowed disabled:opacity-30`}><i className={`fa-solid ${selected ? 'fa-check' : 'fa-plus'} mr-1.5 text-[9px]`}></i>{tag}</button>
                })}
              </div>
            </fieldset>
            {error && <p className="mt-4 border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-xs font-black text-black shadow-[4px_4px_0_#111]" role="alert"><i className="fa-solid fa-circle-exclamation mr-2"></i>{error}</p>}
            <button type="submit" disabled={busy} className="mt-5 w-full border-[3px] border-black bg-[#ff4f9a] px-5 py-3 text-sm font-black text-black shadow-[6px_6px_0_#111] transition-none hover:bg-[#4ecdc4] active:translate-x-[6px] active:translate-y-[6px] active:shadow-none disabled:cursor-wait disabled:opacity-60">
              <i className={`fa-solid ${busy ? 'fa-spinner animate-spin' : editingId ? 'fa-floppy-disk' : 'fa-plus'} mr-2`}></i>{busy ? '保存中...' : editingId ? '保存修改' : '创建组合筛选'}
            </button>
          </form>
        </div>
      </section>
    </div>
  )
}

export default QuickFilterEditorModal
