import { useEffect, useState } from 'react'
import api from '../api/api.js'
import {
  getCoverSrc,
  getDefaultTagColor,
  getTagClass,
  tagColorOptions,
} from '../utils/animeDisplay.js'

const presetTags = [
  '真百', '轻百', '日常', '治愈', '萌系', '搞笑', '校园', '恋爱',
  '原创', '奇幻', '异世界', '战斗', '冒险', '剧场版', '泡面番',
  '魔法少女', '音乐', '悬疑', '职场', '游戏改',
]

function isHttpUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

function createForm(item) {
  return {
    title: item.title ?? '',
    japaneseTitle: item.japaneseTitle ?? '',
    period: item.period ?? '',
    studio: item.studio ?? '',
    episodes: item.episodes ?? '',
    tags: [...(item.baseTags ?? item.tags ?? [])],
    tagColors: { ...(item.tagColors ?? {}) },
    cover: item.cover ?? '',
    description: item.description ?? '',
    baikeUrl: item.baikeUrl ?? '',
    score: item.score ?? '',
    status: item.status ?? 'plan',
    review: item.review ?? '',
  }
}

function EditableDetailModal({
  selectedItem,
  closeModal,
  onRecordUpdated,
  onRecordDeleted,
  showToast,
}) {
  const [renderedItem, setRenderedItem] = useState(selectedItem)
  const [formData, setFormData] = useState(() => createForm(selectedItem ?? {}))
  const [newTag, setNewTag] = useState('')
  const [isVisible, setIsVisible] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [error, setError] = useState('')
  const [activeTextPanel, setActiveTextPanel] = useState('description')

  useEffect(() => {
    let firstFrame
    let secondFrame
    let closeTimer

    if (selectedItem) {
      setRenderedItem(selectedItem)
      setFormData(createForm(selectedItem))
      setNewTag('')
      setError('')
      setActiveTextPanel('description')
      setIsVisible(false)
      firstFrame = window.requestAnimationFrame(() => {
        secondFrame = window.requestAnimationFrame(() => {
          setIsVisible(true)
        })
      })
    } else {
      setIsVisible(false)
      closeTimer = window.setTimeout(() => setRenderedItem(null), 300)
    }

    return () => {
      window.cancelAnimationFrame(firstFrame)
      window.cancelAnimationFrame(secondFrame)
      window.clearTimeout(closeTimer)
    }
  }, [selectedItem])

  useEffect(() => {
    if (!renderedItem) return undefined

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

    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !isSaving && !isDeleting) closeModal()
    }
    window.addEventListener('keydown', onKeyDown)

    return () => {
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [closeModal, isDeleting, isSaving, renderedItem])

  if (!renderedItem) return null

  const updateField = (event) => {
    const { name, value } = event.target
    setFormData((current) => ({ ...current, [name]: value }))
  }

  const updateTag = (index, value) => {
    setFormData((current) => {
      const previousTag = current.tags[index]
      const previousColor = current.tagColors[previousTag] || getDefaultTagColor(previousTag)
      const tagColors = { ...current.tagColors }
      delete tagColors[previousTag]
      if (value.trim()) tagColors[value] = previousColor

      return {
        ...current,
        tagColors,
        tags: current.tags.map((tag, tagIndex) => tagIndex === index ? value : tag),
      }
    })
  }

  const removeTag = (index) => {
    setFormData((current) => {
      const tagColors = { ...current.tagColors }
      delete tagColors[current.tags[index]]
      return {
        ...current,
        tagColors,
        tags: current.tags.filter((_, tagIndex) => tagIndex !== index),
      }
    })
  }

  const addTagValue = (value) => {
    const tag = value.trim()
    if (!tag) return
    setFormData((current) => ({
      ...current,
      tagColors: {
        ...current.tagColors,
        [tag]: current.tagColors[tag] || getDefaultTagColor(tag),
      },
      tags: current.tags.includes(tag) ? current.tags : [...current.tags, tag],
    }))
  }

  const addTag = () => {
    addTagValue(newTag)
    setNewTag('')
  }

  const updateTagColor = (tag, color) => {
    setFormData((current) => ({
      ...current,
      tagColors: { ...current.tagColors, [tag]: color },
    }))
  }

  const handleSave = async () => {
    const score = formData.score === '' ? null : Number(formData.score)
    if (!formData.title.trim() || !formData.period.trim()) {
      setError('番剧中文名和放送季度不能为空。')
      return
    }
    if (score !== null && (!Number.isFinite(score) || score < 0 || score > 10)) {
      setError('评分必须是 0 到 10 之间的数字。')
      return
    }

    const normalizedTags = [...new Set(formData.tags.map((tag) => tag.trim()).filter(Boolean))]
    const normalizedTagColors = Object.fromEntries(normalizedTags.map((tag) => [
      tag,
      formData.tagColors[tag] || getDefaultTagColor(tag),
    ]))
    setIsSaving(true)
    setError('')

    try {
      const response = await api.patch(`records/${renderedItem.id}/details/`, {
        anime: {
          chinese_title: formData.title.trim(),
          japanese_title: formData.japaneseTitle.trim(),
          airing_period: formData.period.trim(),
          studio: formData.studio.trim(),
          episodes: formData.episodes.trim(),
          base_tags: normalizedTags,
          tag_colors: normalizedTagColors,
          poster_url: formData.cover.trim(),
          description: formData.description.trim(),
          baike_url: formData.baikeUrl.trim(),
        },
        personal_score: score,
        watch_status: formData.status,
        review: formData.review,
      })
      onRecordUpdated(response.data)
      showToast('番剧资料和私人手账已同步保存。', 'success')
    } catch (requestError) {
      const detail = requestError.response?.data
      const message = Object.values(detail ?? {}).flat(Infinity).join(' ')
        || '保存失败，请检查 URL 和填写内容。'
      setError(message)
      showToast('保存失败，请检查填写内容。', 'error')
    } finally {
      setIsSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!window.confirm(`确定删除《${formData.title}》的私人追番记录吗？此操作不可恢复。`)) return

    setIsDeleting(true)
    setError('')
    try {
      await api.delete(`records/${renderedItem.id}/`)
      onRecordDeleted(renderedItem.id)
      showToast('私人追番记录已删除。', 'success')
      closeModal()
    } catch {
      setError('删除失败，请稍后重试。')
      showToast('删除失败，请稍后重试。', 'error')
    } finally {
      setIsDeleting(false)
    }
  }

  const coverPreview = formData.cover.trim()
    || getCoverSrc({ title: formData.title || '番剧', cover: '' })
  const validBaikeUrl = isHttpUrl(formData.baikeUrl.trim())
  const busy = isSaving || isDeleting
  const inputClass = 'w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100'

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="editAnimeTitle"
      className={`fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-sm transition-all duration-300 ease-out ${isVisible ? 'opacity-100' : 'pointer-events-none opacity-0'}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) closeModal()
      }}
    >
      <section
        className={`flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-3xl bg-white shadow-2xl transform transition-transform duration-300 ease-out ${isVisible ? 'translate-y-0 scale-100' : 'translate-y-4 scale-95'}`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="h-2 shrink-0 bg-gradient-to-r from-pink-400 via-purple-500 to-indigo-500"></div>

        <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-4">
          <div className="min-w-0 flex-1 space-y-2">
            <label className="block text-xs font-bold text-slate-400" htmlFor="editAnimeTitle">番剧名称（中文）</label>
            <input id="editAnimeTitle" name="title" value={formData.title} onChange={updateField} className={`${inputClass} text-xl font-black`} />
            <label className="block text-xs font-bold text-slate-400" htmlFor="editAnimeJapaneseTitle">番剧名称（日文）</label>
            <input id="editAnimeJapaneseTitle" name="japaneseTitle" value={formData.japaneseTitle} onChange={updateField} className={inputClass} />
          </div>
          <button type="button" onClick={closeModal} disabled={busy} aria-label="关闭" className="rounded-full p-2 text-slate-400 transition hover:rotate-90 hover:bg-rose-50 hover:text-rose-500 disabled:opacity-50">
            <i className="fa-solid fa-xmark text-xl"></i>
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
          <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
            <aside>
              <div className="aspect-[3/4] overflow-hidden rounded-2xl border-4 border-white bg-slate-100 shadow-xl ring-1 ring-slate-200">
                <img src={coverPreview} alt={`${formData.title} 海报`} className="h-full w-full object-cover" />
              </div>

              <label htmlFor="editCoverUrl" className="mb-1 mt-4 block text-xs font-bold text-slate-500">网络图片 URL</label>
              <input id="editCoverUrl" name="cover" type="url" value={formData.cover} onChange={updateField} placeholder="https://..." className={inputClass} />

              <label htmlFor="editBaikeUrl" className="mb-1 mt-4 block text-xs font-bold text-slate-500">萌娘百科 URL</label>
              <input id="editBaikeUrl" name="baikeUrl" type="url" value={formData.baikeUrl} onChange={updateField} placeholder="https://zh.moegirl.org.cn/..." className={inputClass} />
              <a
                href={validBaikeUrl ? formData.baikeUrl.trim() : undefined}
                target={validBaikeUrl ? '_blank' : undefined}
                rel={validBaikeUrl ? 'noreferrer' : undefined}
                aria-disabled={!validBaikeUrl}
                className={`mt-3 flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-pink-500 to-rose-500 px-4 py-2.5 text-xs font-bold text-white shadow-md transition hover:-translate-y-0.5${validBaikeUrl ? '' : ' pointer-events-none opacity-40 grayscale'}`}
              >
                <i className={`fa-solid ${validBaikeUrl ? 'fa-book-open' : 'fa-link-slash'}`}></i>
                {validBaikeUrl ? '前往萌娘百科' : '请输入有效的 HTTP(S) 地址'}
              </a>
            </aside>

            <div className="space-y-5">
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <label className="block text-xs font-bold text-slate-500">放送季度
                  <input name="period" value={formData.period} onChange={updateField} className={`${inputClass} mt-1`} placeholder="2026-1" />
                </label>
                <label className="block text-xs font-bold text-slate-500">制作公司
                  <input name="studio" value={formData.studio} onChange={updateField} className={`${inputClass} mt-1`} />
                </label>
                <label className="block text-xs font-bold text-slate-500">话数情况
                  <input name="episodes" value={formData.episodes} onChange={updateField} className={`${inputClass} mt-1`} />
                </label>
                <label className="block text-xs font-bold text-slate-500">主观评分
                  <input name="score" type="number" min="0" max="10" step="0.1" value={formData.score} onChange={updateField} className={`${inputClass} mt-1 text-pink-500`} />
                </label>
                <label className="block text-xs font-bold text-slate-500">观看状态
                  <select name="status" value={formData.status} onChange={updateField} className={`${inputClass} mt-1 text-indigo-600`}>
                    <option value="plan">想看</option>
                    <option value="watching">在看</option>
                    <option value="completed">看过</option>
                    <option value="on_hold">搁置</option>
                  </select>
                </label>
              </div>

              <section className="grid gap-4 lg:grid-cols-2">
                <div className="rounded-2xl border border-indigo-100 bg-white p-4 shadow-sm">
                  <div className="mb-3 flex items-center justify-between gap-2">
                    <span className="text-xs font-black text-slate-600">
                      <i className="fa-solid fa-tags mr-2 text-indigo-500"></i>当前标签
                    </span>
                    <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-[10px] font-bold text-indigo-500">
                      {formData.tags.length} 个
                    </span>
                  </div>

                  <div className="grid gap-2 sm:grid-cols-2">
                    {formData.tags.map((tag, index) => {
                      const color = formData.tagColors[tag] || getDefaultTagColor(tag)
                      return (
                        <div key={`${tag}-${index}`} className={`rounded-xl p-2 shadow-sm ${getTagClass(tag, color)}`}>
                          <input
                            value={tag}
                            onChange={(event) => updateTag(index, event.target.value)}
                            className="block w-full rounded-lg border border-white/70 bg-white/80 px-2.5 py-2 text-center text-xs font-black text-current outline-none shadow-inner focus:bg-white"
                            aria-label={`修改标签 ${tag}`}
                          />
                          <div className="mt-1.5 flex items-center gap-1.5">
                            <span className="shrink-0 pl-1 text-[10px] font-bold opacity-70">
                              <i className="fa-solid fa-palette mr-1"></i>配色
                            </span>
                            <select
                              value={color}
                              onChange={(event) => updateTagColor(tag, event.target.value)}
                              className="min-w-0 flex-1 rounded-lg border border-white/70 bg-white/80 px-2 py-1.5 text-[10px] font-bold text-current outline-none shadow-inner"
                              aria-label={`${tag}的颜色`}
                            >
                              {tagColorOptions.map((option) => (
                                <option key={option.value} value={option.value}>{option.label}</option>
                              ))}
                            </select>
                            <button
                              type="button"
                              onClick={() => removeTag(index)}
                              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-white/70 bg-white/80 text-rose-500 shadow-sm transition hover:bg-rose-50"
                              aria-label={`删除标签 ${tag}`}
                            >
                              <i className="fa-solid fa-xmark"></i>
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>

                  <div className="mt-3 flex gap-2 border-t border-slate-100 pt-3">
                    <input
                      value={newTag}
                      onChange={(event) => setNewTag(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          event.preventDefault()
                          addTag()
                        }
                      }}
                      className={`${inputClass} min-w-0`}
                      placeholder="自定义新标签"
                    />
                    <button type="button" onClick={addTag} className="shrink-0 rounded-xl bg-indigo-500 px-4 py-2 text-xs font-bold text-white shadow-sm transition hover:bg-indigo-600">
                      <i className="fa-solid fa-plus mr-1.5"></i>增加
                    </button>
                  </div>
                </div>

                <div className="rounded-2xl border border-pink-100 bg-gradient-to-br from-pink-50/70 to-indigo-50/70 p-4 shadow-inner">
                  <div className="mb-3">
                    <span className="text-xs font-black text-slate-600">
                      <i className="fa-solid fa-wand-magic-sparkles mr-2 text-pink-500"></i>快捷预设
                    </span>
                    <p className="mt-1 text-[10px] font-medium text-slate-400">点击即可加入，再在左侧选择专属颜色。</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {presetTags.map((tag) => {
                      const isSelected = formData.tags.includes(tag)
                      return (
                        <button
                          key={tag}
                          type="button"
                          disabled={isSelected}
                          onClick={() => addTagValue(tag)}
                          className={`rounded-lg px-2.5 py-1.5 text-xs transition hover:-translate-y-0.5 hover:shadow-md disabled:cursor-default disabled:opacity-40 ${getTagClass(tag, getDefaultTagColor(tag))}`}
                        >
                          <i className={`fa-solid ${isSelected ? 'fa-check' : 'fa-plus'} mr-1.5 text-[9px]`}></i>{tag}
                        </button>
                      )
                    })}
                  </div>
                </div>
              </section>

              <section>
                <div className="mb-2 flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-100/80 p-1 shadow-inner">
                  <button
                    type="button"
                    aria-pressed={activeTextPanel === 'description'}
                    onClick={() => setActiveTextPanel('description')}
                    className={`flex-1 rounded-lg px-4 py-2 text-xs font-black transition-all ${activeTextPanel === 'description' ? 'bg-white text-indigo-600 shadow-sm ring-1 ring-indigo-100' : 'text-slate-500 hover:bg-white/60 hover:text-slate-700'}`}
                  >
                    <i className="fa-solid fa-align-left mr-2"></i>剧情简介
                  </button>
                  <button
                    type="button"
                    aria-pressed={activeTextPanel === 'review'}
                    onClick={() => setActiveTextPanel('review')}
                    className={`flex-1 rounded-lg px-4 py-2 text-xs font-black transition-all ${activeTextPanel === 'review' ? 'bg-white text-pink-600 shadow-sm ring-1 ring-pink-100' : 'text-slate-500 hover:bg-white/60 hover:text-slate-700'}`}
                  >
                    <i className="fa-solid fa-pen-nib mr-2"></i>个人评价
                  </button>
                </div>
                <textarea
                  key={activeTextPanel}
                  name={activeTextPanel}
                  value={formData[activeTextPanel]}
                  onChange={updateField}
                  className={`${inputClass} h-40 resize-y leading-6 animate-[viewFadeIn_0.2s_ease-out_both]`}
                  placeholder={activeTextPanel === 'description' ? '填写番剧剧情简介...' : '写下你的观看感受...'}
                  aria-label={activeTextPanel === 'description' ? '剧情简介' : '个人评价'}
                />
              </section>

              {error && (
                <p role="alert" className="rounded-xl bg-rose-50 px-4 py-3 text-sm font-medium text-rose-600">
                  <i className="fa-solid fa-circle-exclamation mr-2"></i>{error}
                </p>
              )}
            </div>
          </div>
        </div>

        <footer className="flex flex-wrap justify-between gap-3 border-t border-slate-100 bg-slate-50 px-6 py-4">
          <button type="button" onClick={handleDelete} disabled={busy} className="rounded-xl border border-rose-200 bg-white px-5 py-2.5 text-sm font-bold text-rose-600 transition hover:bg-rose-50 disabled:opacity-50">
            <i className={`fa-solid ${isDeleting ? 'fa-spinner animate-spin' : 'fa-trash-can'} mr-2`}></i>{isDeleting ? '删除中...' : '删除私人记录'}
          </button>
          <div className="flex gap-3">
            <button type="button" onClick={closeModal} disabled={busy} className="rounded-xl bg-slate-200 px-5 py-2.5 text-sm font-bold text-slate-600 transition hover:bg-slate-300 disabled:opacity-50">关闭</button>
            <button type="button" onClick={handleSave} disabled={busy} className="rounded-xl bg-gradient-to-r from-pink-500 to-indigo-500 px-5 py-2.5 text-sm font-bold text-white shadow-md transition hover:shadow-lg disabled:opacity-60">
              <i className={`fa-solid ${isSaving ? 'fa-spinner animate-spin' : 'fa-floppy-disk'} mr-2`}></i>{isSaving ? '保存中...' : '保存全部修改'}
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}

export default EditableDetailModal
