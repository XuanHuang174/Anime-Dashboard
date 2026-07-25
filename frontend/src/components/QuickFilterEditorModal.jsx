import { useEffect, useMemo, useState } from 'react'
import api from '../api/api.js'
import { getTagClass } from '../utils/animeDisplay.js'

const emptyForm = { name: '', tags: [], match_mode: 'any' }

function QuickFilterEditorModal({
  isOpen,
  onClose,
  availableTags,
  filters,
  onSaved,
  onDeleted,
  showToast,
}) {
  const [form, setForm] = useState(emptyForm)
  const [editingId, setEditingId] = useState(null)
  const [busy, setBusy] = useState(false)
  const [deletingId, setDeletingId] = useState(null)
  const [error, setError] = useState('')

  const sortedTags = useMemo(
    () => [...availableTags].filter((tag) => tag !== '轩皇力推').sort(),
    [availableTags],
  )

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
      if (event.key === 'Escape' && !busy && !deletingId) onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [busy, deletingId, isOpen, onClose])

  if (!isOpen) return null

  const toggleTag = (tag) => {
    setForm((current) => ({
      ...current,
      tags: current.tags.includes(tag)
        ? current.tags.filter((item) => item !== tag)
        : [...current.tags, tag],
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
        ? await api.patch(`quick-filters/${editingId}/`, payload)
        : await api.post('quick-filters/', payload)
      onSaved(response.data)
      showToast(editingId ? '快捷筛选已更新。' : '快捷筛选已创建。', 'success')
      resetForm()
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
      await api.delete(`quick-filters/${filter.id}/`)
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
      className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/60 p-4 backdrop-blur-sm animate-[viewFadeIn_0.2s_ease-out_both]"
      role="dialog"
      aria-modal="true"
      aria-labelledby="quickFilterEditorTitle"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy && !deletingId) onClose()
      }}
    >
      <section className="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-3xl border border-white/60 bg-white shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
        <div className="h-2 bg-gradient-to-r from-pink-400 via-purple-500 to-indigo-500"></div>
        <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-5">
          <div>
            <h2 id="quickFilterEditorTitle" className="text-xl font-black text-slate-800">
              <i className="fa-solid fa-filter-circle-plus mr-2 text-indigo-500"></i>自定义快速筛选
            </h2>
            <p className="mt-1 text-xs font-medium text-slate-400">组合常用标签，并保存到当前账号。</p>
          </div>
          <button type="button" onClick={onClose} className="flex h-9 w-9 items-center justify-center rounded-full text-slate-400 transition hover:rotate-90 hover:bg-rose-50 hover:text-rose-500" aria-label="关闭自定义筛选编辑器">
            <i className="fa-solid fa-xmark"></i>
          </button>
        </header>

        <div className="grid flex-1 gap-5 overflow-y-auto p-6 md:grid-cols-[1fr_1.25fr] custom-scrollbar">
          <aside>
            <h3 className="mb-3 text-xs font-black text-slate-500">已保存的组合</h3>
            <div className="space-y-2">
              {filters.length === 0 && (
                <div className="rounded-2xl border border-dashed border-slate-200 py-8 text-center text-xs font-medium text-slate-400">暂时没有自定义筛选</div>
              )}
              {filters.map((filter) => (
                <article key={filter.id} className={`rounded-2xl border p-3 transition ${editingId === filter.id ? 'border-indigo-300 bg-indigo-50 shadow-inner' : 'border-slate-100 bg-white shadow-sm'}`}>
                  <div className="flex items-center justify-between gap-2">
                    <strong className="truncate text-sm text-slate-700">{filter.name}</strong>
                    <div className="flex gap-1">
                      <button type="button" onClick={() => beginEdit(filter)} className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-50 text-indigo-500 transition hover:bg-indigo-100" aria-label={`编辑 ${filter.name}`}><i className="fa-solid fa-pen"></i></button>
                      <button type="button" disabled={deletingId === filter.id} onClick={() => handleDelete(filter)} className="flex h-7 w-7 items-center justify-center rounded-lg bg-rose-50 text-rose-500 transition hover:bg-rose-100 disabled:opacity-50" aria-label={`删除 ${filter.name}`}><i className={`fa-solid ${deletingId === filter.id ? 'fa-spinner animate-spin' : 'fa-trash-can'}`}></i></button>
                    </div>
                  </div>
                  <p className="mt-1 text-[10px] font-bold text-slate-400">{filter.match_mode === 'all' ? '同时包含' : '包含任意'} · {[...filter.tags, ...(filter.title_keywords ?? []).map((keyword) => `标题:${keyword}`)].join(' / ')}</p>
                </article>
              ))}
            </div>
          </aside>

          <form onSubmit={handleSubmit}>
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-xs font-black text-slate-500">{editingId ? '修改组合' : '创建新组合'}</h3>
              {editingId && <button type="button" onClick={resetForm} className="text-xs font-bold text-indigo-500 hover:text-indigo-700">取消编辑</button>}
            </div>
            <label className="block text-xs font-bold text-slate-500">筛选名称
              <input value={form.name} maxLength={30} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} className="mt-1 w-full rounded-xl border border-slate-200 border-b-[3px] border-b-slate-300 bg-white px-3 py-2.5 text-sm font-semibold outline-none transition focus:translate-y-[2px] focus:border-indigo-400 focus:border-b focus:ring-2 focus:ring-indigo-100" placeholder="例如：治愈日常" />
            </label>
            <label className="mt-4 block text-xs font-bold text-slate-500">匹配方式
              <select value={form.match_mode} onChange={(event) => setForm((current) => ({ ...current, match_mode: event.target.value }))} className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-semibold text-indigo-600 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100">
                <option value="any">命中任意一个标签</option>
                <option value="all">必须包含全部标签</option>
              </select>
            </label>
            <fieldset className="mt-4">
              <legend className="mb-2 text-xs font-bold text-slate-500">选择标签（最多 8 个）</legend>
              <div className="flex max-h-48 flex-wrap gap-2 overflow-y-auto rounded-2xl border border-slate-100 bg-slate-50/70 p-3 shadow-inner custom-scrollbar">
                {sortedTags.map((tag) => {
                  const selected = form.tags.includes(tag)
                  return <button key={tag} type="button" disabled={!selected && form.tags.length >= 8} onClick={() => toggleTag(tag)} className={`rounded-lg px-2.5 py-1.5 text-xs transition ${selected ? `${getTagClass(tag)} ring-2 ring-indigo-300 shadow-sm` : 'border border-slate-200 bg-white text-slate-500 hover:border-indigo-200 hover:text-indigo-500'} disabled:cursor-not-allowed disabled:opacity-30`}><i className={`fa-solid ${selected ? 'fa-check' : 'fa-plus'} mr-1.5 text-[9px]`}></i>{tag}</button>
                })}
              </div>
            </fieldset>
            {error && <p className="mt-4 rounded-xl bg-rose-50 px-4 py-3 text-xs font-bold text-rose-600" role="alert">{error}</p>}
            <button type="submit" disabled={busy} className="mt-5 w-full rounded-xl bg-gradient-to-r from-pink-500 to-indigo-500 px-5 py-3 text-sm font-black text-white shadow-md transition hover:-translate-y-0.5 hover:shadow-lg disabled:opacity-60">
              <i className={`fa-solid ${busy ? 'fa-spinner animate-spin' : editingId ? 'fa-floppy-disk' : 'fa-plus'} mr-2`}></i>{busy ? '保存中...' : editingId ? '保存修改' : '创建组合筛选'}
            </button>
          </form>
        </div>
      </section>
    </div>
  )
}

export default QuickFilterEditorModal
