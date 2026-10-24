import { useEffect, useMemo, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import api from '../api/api.js'
import SearchableSelect from './SearchableSelect.jsx'
import {
  getCoverSrc,
  getDefaultTagColor,
  getTagClass,
  getTagTextClass,
  tagColorOptions,
} from '../utils/animeDisplay.js'
import { getImageFileError, IMAGE_FILE_ACCEPT } from '../utils/imageUpload.js'
import { mapRecordToAnimeItem } from '../utils/recordMapping.js'
import './EditableDetailModal.css'

gsap.registerPlugin(useGSAP)

const presetTags = [
  '真百', '轻百', '日常', '治愈', '萌系', '搞笑', '校园', '恋爱',
  '原创', '奇幻', '异世界', '战斗', '冒险', '剧场版', '泡面番',
  '魔法少女', '音乐', '悬疑', '职场', '游戏改',
  '爱情', 'OVA', '小说改', '漫画改', 'Gal改', '喜剧', '催泪', '偶像',
  '后宫', '运动', '热血', '龙傲天', '凤傲天', '黑暗', '致郁',
]

const fieldClass = 'w-full border-2 border-black bg-white px-3 py-2 text-sm font-bold text-black shadow-[3px_3px_0_#111] outline-none transition-none placeholder:text-zinc-400 focus:translate-x-1 focus:translate-y-1 focus:bg-[#fff2a8] focus:shadow-none'
const arcadeButton = 'border-[3px] border-black px-4 py-2.5 text-sm font-black text-black shadow-[5px_5px_0_#111] transition-none active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:cursor-not-allowed disabled:opacity-50'

function isHttpUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

function createForm(item) {
  const editableTags = [
    ...(Array.isArray(item.baseTags) ? item.baseTags : []),
    ...(Array.isArray(item.customTags) ? item.customTags : []),
  ]
  return {
    title: item.title ?? '',
    japaneseTitle: item.japaneseTitle ?? '',
    period: item.period ?? '',
    studio: item.studio ?? '',
    episodes: item.episodes ?? '',
    tags: editableTags.length > 0
      ? [...new Set(editableTags)]
      : [...(item.tags ?? [])],
    tagColors: { ...(item.tagColors ?? {}) },
    // The persisted ImageField URL (R2 in production) is authoritative. The
    // original Bangumi proxy URL remains only as a fallback/display source.
    cover: item.posterImage || item.posterUrl || item.cover || '',
    description: item.description ?? '',
    baikeUrl: item.baikeUrl ?? '',
    score: item.score ?? '',
    status: item.status ?? 'plan',
    watchedEps: item.watchedEps ?? 0,
    totalEps: item.totalEps ?? 0,
    review: item.review ?? '',
  }
}

function EditableDetailModal({
  selectedItem,
  closeModal,
  onRecordUpdated,
  onRecordDeleted,
  showToast,
  onSimilar,
}) {
  const overlayRef = useRef(null)
  const modalRef = useRef(null)
  const closeHandlerRef = useRef(null)
  const posterInputRef = useRef(null)
  const [renderedItem, setRenderedItem] = useState(selectedItem)
  const [formData, setFormData] = useState(() => createForm(selectedItem ?? {}))
  const [posterFile, setPosterFile] = useState(null)
  const [newTag, setNewTag] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [error, setError] = useState('')
  const [activeEditorPanel, setActiveEditorPanel] = useState('tags')

  const localPosterPreview = useMemo(
    () => (posterFile ? URL.createObjectURL(posterFile) : ''),
    [posterFile],
  )

  useEffect(() => () => {
    if (localPosterPreview) URL.revokeObjectURL(localPosterPreview)
  }, [localPosterPreview])

  useEffect(() => {
    if (!selectedItem) return
    setRenderedItem(selectedItem)
    setFormData(createForm(selectedItem))
    setPosterFile(null)
    setNewTag('')
    setIsSaving(false)
    setIsDeleting(false)
    setError('')
    setActiveEditorPanel('tags')
    if (posterInputRef.current) posterInputRef.current.value = ''
  }, [selectedItem])

  const { contextSafe } = useGSAP(() => {
    if (!renderedItem || !overlayRef.current || !modalRef.current) return undefined
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      gsap.set([overlayRef.current, modalRef.current, '.edit-modal-piece'], { clearProps: 'all' })
      return undefined
    }

    const timeline = gsap.timeline()
    timeline
      .fromTo(overlayRef.current, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.14 })
      .fromTo(
        modalRef.current,
        { autoAlpha: 0, scale: 0.7, y: -50, rotation: -2 },
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
        '.edit-modal-piece',
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
    dependencies: [renderedItem?.id],
    revertOnUpdate: true,
  })

  const playExit = contextSafe((afterExit = closeModal) => {
    if (!modalRef.current || !overlayRef.current || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      afterExit()
      setRenderedItem(null)
      return
    }

    gsap.killTweensOf([modalRef.current, overlayRef.current])
    gsap.timeline({
      onComplete: () => {
        afterExit()
        setRenderedItem(null)
      },
    })
      .to(modalRef.current, {
        y: 50,
        scale: 0.82,
        rotation: 2,
        autoAlpha: 0,
        duration: 0.24,
        ease: 'power3.in',
      })
      .to(overlayRef.current, { autoAlpha: 0, duration: 0.12 }, '-=0.08')
  })

  const busy = isSaving || isDeleting
  const handleClose = () => {
    if (!busy) playExit(closeModal)
  }
  closeHandlerRef.current = handleClose

  useEffect(() => {
    if (!renderedItem) return undefined
    const previousOverflow = document.body.style.overflow
    const previousPaddingRight = document.body.style.paddingRight
    const scrollbarWidth = window.innerWidth - document.documentElement.clientWidth
    const currentPaddingRight = Number.parseFloat(window.getComputedStyle(document.body).paddingRight) || 0
    if (scrollbarWidth > 0) {
      document.body.style.paddingRight = `${currentPaddingRight + scrollbarWidth}px`
    }
    document.body.style.overflow = 'hidden'

    const onKeyDown = (event) => {
      if (event.key === 'Escape') closeHandlerRef.current?.()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [renderedItem])

  if (!renderedItem) return null
  const hasUnsavedChanges = Boolean(posterFile) || JSON.stringify(formData) !== JSON.stringify(createForm(renderedItem))

  const updateField = (event) => {
    const { name, value } = event.target
    if (name === 'cover' && value.trim()) {
      setPosterFile(null)
      if (posterInputRef.current) posterInputRef.current.value = ''
    }
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

  const handlePosterChange = (event) => {
    const file = event.target.files?.[0] ?? null
    setError('')
    const fileError = getImageFileError(file, '海报')
    if (fileError) {
      setPosterFile(null)
      event.target.value = ''
      setError(fileError)
      return
    }
    setPosterFile(file)
    if (file) {
      setFormData((current) => ({ ...current, cover: '' }))
    }
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
    const watchedEps = Number(formData.watchedEps)
    const totalEps = Number(formData.totalEps)
    if (!Number.isInteger(watchedEps) || watchedEps < 0 || !Number.isInteger(totalEps) || totalEps < 0) {
      setError('已观看集数和总集数必须是非负整数。')
      return
    }
    if (totalEps > 0 && watchedEps > totalEps) {
      setError('已观看集数不能超过总集数。')
      return
    }

    const normalizedTags = [...new Set(formData.tags.map((tag) => tag.trim()).filter(Boolean))]
    const normalizedTagColors = Object.fromEntries(normalizedTags.map((tag) => [
      tag,
      formData.tagColors[tag] || getDefaultTagColor(tag),
    ]))
    setIsSaving(true)
    setError('')

    const animePayload = {
      chinese_title: formData.title.trim(),
      japanese_title: formData.japaneseTitle.trim(),
      airing_period: formData.period.trim(),
      studio: formData.studio.trim(),
      episodes: formData.episodes.trim(),
      base_tags: normalizedTags,
      tag_colors: normalizedTagColors,
      description: formData.description.trim(),
      baike_url: formData.baikeUrl.trim(),
    }
    const persistedCover = (
      renderedItem.posterImage
      || renderedItem.posterUrl
      || renderedItem.cover
      || ''
    ).trim()
    const nextCover = formData.cover.trim()
    if (!posterFile && nextCover !== persistedCover) {
      animePayload.poster_url = nextCover
    }

    const payload = new FormData()
    payload.append('anime', JSON.stringify(animePayload))
    payload.append('record', JSON.stringify({
      personal_score: score,
      watch_status: formData.status,
      watched_eps: watchedEps,
      total_eps: totalEps,
      review: formData.review,
    }))
    if (posterFile) payload.append('poster_image', posterFile)

    try {
      const response = await api.patch(`records/${renderedItem.id}/details/`, payload)
      const latestItem = mapRecordToAnimeItem(response.data)
      setRenderedItem(latestItem)
      setFormData(createForm(latestItem))
      onRecordUpdated(response.data)
      setPosterFile(null)
      if (posterInputRef.current) posterInputRef.current.value = ''
      showToast('番剧资料和私人手账已同步保存。', 'success')
    } catch (requestError) {
      const detail = requestError.response?.data
      const rawMessage = Object.values(detail ?? {}).flat(Infinity).join(' ')
      const message = /chinese_title.*japanese_title.*airing_period|唯一集合|unique set/i.test(rawMessage)
        ? '同一放送季度下已存在相同名称的番剧，请核对中文名、日文名或放送季度。'
        : rawMessage || '保存失败，请检查 URL 和填写内容。'
      setError(message)
      showToast(message, 'error')
    } finally {
      setIsSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!window.confirm(
      `确定删除${formData.title}的私人追番记录吗？\n\n如果该作品已提交或已入选精选专栏，对应的精选专栏内容也会同步删除。\n\n此操作不可恢复。`,
    )) return
    setIsDeleting(true)
    setError('')
    try {
      await api.delete(`records/${renderedItem.id}/`)
      showToast('私人追番记录及其精选专栏内容已同步删除。', 'success')
      playExit(() => {
        setIsDeleting(false)
        onRecordDeleted(renderedItem.id)
      })
    } catch {
      setError('删除失败，请稍后重试。')
      showToast('删除失败，请稍后重试。', 'error')
      setIsDeleting(false)
    }
  }

  const coverPreview = localPosterPreview
    || formData.cover.trim()
    || renderedItem.posterImage
    || getCoverSrc({ title: formData.title || '番剧', cover: '' })
  const validBaikeUrl = isHttpUrl(formData.baikeUrl.trim())
  const editorPanelOptions = [
    { value: 'tags', label: '标签管理', icon: 'fa-tags', activeClass: 'bg-[#ffe66d]' },
    { value: 'description', label: '剧情简介', icon: 'fa-align-left', activeClass: 'bg-[#4ecdc4]' },
    { value: 'review', label: '个人评价', icon: 'fa-pen-nib', activeClass: 'bg-[#ff8fab]' },
  ]
  const editorPanelSwitcher = (
    <div className="grid grid-cols-3 border-[3px] border-black bg-white shadow-[4px_4px_0_#111]">
      {editorPanelOptions.map((option, index) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={activeEditorPanel === option.value}
          onClick={() => setActiveEditorPanel(option.value)}
          className={`min-w-0 px-2 py-2 text-[10px] font-black transition-none sm:text-xs ${
            index < editorPanelOptions.length - 1 ? 'border-r-[3px] border-black' : ''
          } ${
            activeEditorPanel === option.value
              ? `${option.activeClass} translate-x-0.5 translate-y-0.5`
              : 'bg-[#f4f1ea] hover:bg-white'
          }`}
        >
          <i className={`fa-solid ${option.icon} mr-1.5`}></i>
          {option.label}
        </button>
      ))}
    </div>
  )

  return (
    <div
      ref={overlayRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="editAnimeTitle"
      className="fixed inset-0 z-[110] flex items-center justify-center overflow-y-auto bg-zinc-950/80 p-2 backdrop-blur-sm sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) handleClose()
      }}
    >
      <section
        ref={modalRef}
        className="edit-record-modal relative my-auto flex max-h-[calc(100dvh-2rem)] w-full max-w-6xl flex-col overflow-hidden border-4 border-black bg-[#fff5ee] text-black shadow-[12px_12px_0_#111]"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="edit-modal-piece flex shrink-0 items-start justify-between gap-4 border-b-4 border-black bg-[#4ecdc4] px-4 py-3 sm:px-6">
          <div className="min-w-0 flex-1">
            <span className="inline-block border-2 border-black bg-[#ffe66d] px-2 py-1 text-[10px] font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">Edit Record</span>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="block text-xs font-black" htmlFor="editAnimeTitle">
                番剧名称（中文）
                <input id="editAnimeTitle" name="title" value={formData.title} onChange={updateField} className={`${fieldClass} mt-1 text-lg font-black`} />
              </label>
              <label className="block text-xs font-black" htmlFor="editAnimeJapaneseTitle">
                番剧名称（日文）
                <input id="editAnimeJapaneseTitle" name="japaneseTitle" value={formData.japaneseTitle} onChange={updateField} className={`${fieldClass} mt-1`} />
              </label>
            </div>
          </div>
          <button type="button" onClick={handleClose} disabled={busy} aria-label="关闭" className="flex h-10 w-10 shrink-0 items-center justify-center border-[3px] border-black bg-white text-lg shadow-[4px_4px_0_#111] transition-none hover:bg-[#ff6b6b] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:opacity-50">
            <i className="fa-solid fa-xmark"></i>
          </button>
        </header>

        <div className="edit-record-body min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar sm:p-4">
          <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
            <aside className="edit-modal-piece mx-auto w-full max-w-[240px] lg:mx-0 lg:flex lg:max-w-[220px] lg:flex-col">
              <div className="aspect-[3/4] shrink-0 overflow-hidden border-4 border-black bg-[#ff8fab] shadow-[7px_7px_0_#4ecdc4]">
                <img src={coverPreview} alt={`${formData.title} 海报`} className="h-full w-full object-cover" />
              </div>

              <label className="mt-3 block shrink-0 cursor-pointer border-[3px] border-black bg-[#ffe66d] px-3 py-2 text-center text-[11px] font-black shadow-[4px_4px_0_#111] transition-none hover:bg-[#4ecdc4] active:translate-x-1 active:translate-y-1 active:shadow-none">
                <i className="fa-solid fa-upload mr-2"></i>
                {posterFile ? '已选择：更换文件' : '从本地上传海报'}
                <input ref={posterInputRef} type="file" accept={IMAGE_FILE_ACCEPT} onChange={handlePosterChange} className="sr-only" />
              </label>
              {posterFile && <p className="mt-2 break-all text-[10px] font-black">{posterFile.name}</p>}

              <label htmlFor="editCoverUrl" className="mb-1 mt-3 block shrink-0 text-[11px] font-black">网络图片 URL</label>
              <input id="editCoverUrl" name="cover" type="url" value={formData.cover} onChange={updateField} placeholder="https://..." className={fieldClass} />

              <label htmlFor="editBaikeUrl" className="mb-1 mt-3 block shrink-0 text-[11px] font-black">萌娘百科 URL</label>
              <input id="editBaikeUrl" name="baikeUrl" type="url" value={formData.baikeUrl} onChange={updateField} placeholder="https://zh.moegirl.org.cn/..." className={fieldClass} />
              <a
                href={validBaikeUrl ? formData.baikeUrl.trim() : undefined}
                target={validBaikeUrl ? '_blank' : undefined}
                rel={validBaikeUrl ? 'noreferrer' : undefined}
                aria-disabled={!validBaikeUrl}
                className={`mt-3 flex w-full shrink-0 items-center justify-center gap-2 border-[3px] border-black bg-[#ff8fab] px-3 py-2 text-[11px] font-black shadow-[4px_4px_0_#111] transition-none active:translate-x-1 active:translate-y-1 active:shadow-none${validBaikeUrl ? '' : ' pointer-events-none bg-zinc-300 text-zinc-500'}`}
              >
                <i className={`fa-solid ${validBaikeUrl ? 'fa-book-open' : 'fa-link-slash'}`}></i>
                {validBaikeUrl ? '前往萌娘百科' : '请输入有效的 HTTP(S) 地址'}
              </a>
            </aside>

            <div className="flex min-h-0 flex-col gap-3">
              <div className="edit-modal-piece grid shrink-0 gap-2.5 border-[3px] border-black bg-white p-3 shadow-[6px_6px_0_#ffe66d] sm:grid-cols-2 lg:grid-cols-12">
                {[
                  ['period', '放送季度', 'text', '2026-1', 'lg:col-span-4'],
                  ['studio', '制作公司', 'text', '', 'lg:col-span-4'],
                  ['episodes', '话数情况', 'text', '', 'lg:col-span-4'],
                ].map(([name, label, type, placeholder, gridClass]) => (
                  <label key={name} className={`block text-xs font-black ${gridClass}`}>
                    {label}
                    <input name={name} type={type} value={formData[name]} onChange={updateField} className={`${fieldClass} mt-1`} placeholder={placeholder} />
                  </label>
                ))}
                <label className={`${formData.status === 'watching' ? 'lg:col-span-3' : 'lg:col-span-6'} block text-xs font-black`}>
                  主观评分
                  <input name="score" type="number" min="0" max="10" step="0.1" value={formData.score} onChange={updateField} className={`${fieldClass} mt-1 !text-red-600`} placeholder="0.0 - 10.0" />
                </label>
                <div className={`${formData.status === 'watching' ? 'lg:col-span-3' : 'lg:col-span-6'} edit-watch-status-select min-w-0 text-xs font-black`}>
                  <span className="block">观看状态</span>
                  <SearchableSelect
                    id="editWatchStatus"
                    value={formData.status}
                    onChange={(value) => setFormData((current) => ({ ...current, status: value }))}
                    options={[
                      { value: 'watching', label: '在看' },
                      { value: 'completed', label: '看过' },
                      { value: 'on_hold', label: '搁置' },
                    ]}
                    allOption={{ value: 'plan', label: '想看' }}
                    searchable={false}
                  />
                </div>
                {formData.status === 'watching' && (
                  <>
                    <label className="block text-xs font-black lg:col-span-3">
                      已观看集数
                      <input name="watchedEps" type="number" min="0" step="1" value={formData.watchedEps} onChange={updateField} className={`${fieldClass} mt-1`} placeholder="0" />
                    </label>
                    <label className="block text-xs font-black lg:col-span-3">
                      总集数（未知填 0）
                      <input name="totalEps" type="number" min="0" step="1" value={formData.totalEps} onChange={updateField} className={`${fieldClass} mt-1`} placeholder="0" />
                    </label>
                  </>
                )}
              </div>

              <section className="edit-record-editor edit-modal-piece min-h-0 flex-1">
                {activeEditorPanel === 'tags' ? (
                  <div className="grid h-full min-h-0 gap-3 lg:grid-cols-2">
                    <div className="edit-record-tags-panel flex min-h-0 flex-col border-[3px] border-black bg-white p-2.5 shadow-[6px_6px_0_#4ecdc4]">
                      <div className="mb-2 flex shrink-0 items-center justify-between gap-2">
                        <span className="text-xs font-black"><i className="fa-solid fa-tags mr-2"></i>当前标签</span>
                        <span className="rounded-full border-2 border-black bg-[#ffe66d] px-2.5 py-1 text-[10px] font-black">{formData.tags.length} 个</span>
                      </div>

                      <div className="edit-record-tags-scroll grid min-h-0 flex-1 auto-rows-max content-start gap-1.5 overflow-y-auto pr-1 custom-scrollbar sm:grid-cols-2">
                        {formData.tags.map((tag, index) => {
                          const color = formData.tagColors[tag] || getDefaultTagColor(tag)
                          return (
                            <div key={`${tag}-${index}`} className={`rounded-[22px] border-[3px] border-black p-1.5 shadow-[3px_3px_0_#111] ${getTagClass(tag, color)}`}>
                              <div className="flex items-center gap-1.5">
                                <input value={tag} onChange={(event) => updateTag(index, event.target.value)} className={`min-w-0 flex-1 rounded-full border-2 border-black bg-white px-2 py-1 text-center text-[11px] font-black outline-none focus:bg-[#fff2a8] ${getTagTextClass(tag, color)}`} aria-label={`修改标签 ${tag}`} />
                                <button type="button" onClick={() => removeTag(index)} className="flex h-6 w-6 shrink-0 items-center justify-center border-2 border-black bg-[#ff6b6b] text-[11px] text-black shadow-[2px_2px_0_#111] active:translate-x-0.5 active:translate-y-0.5 active:shadow-none" aria-label={`删除标签 ${tag}`}>
                                  <i className="fa-solid fa-xmark"></i>
                                </button>
                              </div>
                              <select value={color} onChange={(event) => updateTagColor(tag, event.target.value)} className="mt-1 w-full rounded-full border-2 border-black bg-white px-2 py-0.5 text-[10px] font-black text-black outline-none focus:bg-[#fff2a8]" aria-label={`${tag}的颜色`}>
                                {tagColorOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                              </select>
                            </div>
                          )
                        })}
                      </div>

                      <div className="edit-record-new-tag mt-2 flex shrink-0 border-[3px] border-black bg-[#ff8fab] p-1.5 shadow-[4px_4px_0_#111]">
                        <input
                          value={newTag}
                          onChange={(event) => setNewTag(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') {
                              event.preventDefault()
                              addTag()
                            }
                          }}
                          className="min-w-0 flex-1 border-2 border-black bg-white px-3 py-1.5 text-xs font-black outline-none focus:bg-[#fff2a8]"
                          placeholder="自定义新标签"
                        />
                        <button type="button" onClick={addTag} className="border-y-2 border-r-2 border-black bg-[#ffe66d] px-3 text-xs font-black hover:bg-[#4ecdc4]">
                          <i className="fa-solid fa-plus mr-1.5"></i>增加
                        </button>
                      </div>
                    </div>

                    <div className="edit-record-presets-panel flex min-h-0 flex-col border-[3px] border-black bg-[#ffe66d] p-3 shadow-[6px_6px_0_#ff8fab]">
                      <div className="mb-2 shrink-0">
                        <span className="text-xs font-black"><i className="fa-solid fa-wand-magic-sparkles mr-2"></i>快捷预设</span>
                        <p className="mt-1 text-[10px] font-bold">点击即可加入，再在左侧选择专属颜色。</p>
                      </div>
                      <div className="edit-record-presets-scroll flex min-h-0 flex-1 content-start flex-wrap gap-1.5 overflow-y-auto pr-1 custom-scrollbar">
                        {presetTags.map((tag) => {
                          const isSelected = formData.tags.includes(tag)
                          return (
                            <button key={tag} type="button" disabled={isSelected} onClick={() => addTagValue(tag)} className={`rounded-full border-2 border-black px-2 py-1 text-[11px] font-black leading-4 shadow-[2px_2px_0_#111] transition-none active:translate-x-0.5 active:translate-y-0.5 active:shadow-none disabled:cursor-default disabled:opacity-40 ${getTagClass(tag, getDefaultTagColor(tag))}`}>
                              <i className={`fa-solid ${isSelected ? 'fa-check' : 'fa-plus'} mr-1.5 text-[9px]`}></i>{tag}
                            </button>
                          )
                        })}
                      </div>
                      <div className="edit-record-panel-switcher mt-3 shrink-0 border-t-[3px] border-black pt-3">
                        <p className="mb-2 text-[10px] font-black uppercase tracking-[0.12em]">切换编辑模块</p>
                        {editorPanelSwitcher}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className={`flex h-full min-h-0 flex-col border-[3px] border-black p-3 shadow-[6px_6px_0_#111] ${
                    activeEditorPanel === 'description' ? 'bg-[#4ecdc4]' : 'bg-[#ff8fab]'
                  }`}>
                    <div className="mb-3 shrink-0">
                      {editorPanelSwitcher}
                    </div>
                    <textarea
                      key={activeEditorPanel}
                      name={activeEditorPanel}
                      value={formData[activeEditorPanel]}
                      onChange={updateField}
                      className={`${fieldClass} min-h-0 flex-1 resize-none overflow-y-auto p-4 text-sm leading-7 custom-scrollbar`}
                      placeholder={activeEditorPanel === 'description' ? '填写番剧剧情简介...' : '写下你的观看感受...'}
                      aria-label={activeEditorPanel === 'description' ? '剧情简介' : '个人评价'}
                    />
                  </div>
                )}
              </section>

              {error && <p role="alert" className="edit-modal-piece border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-sm font-black shadow-[4px_4px_0_#111]"><i className="fa-solid fa-circle-exclamation mr-2"></i>{error}</p>}
            </div>
          </div>
        </div>

        <footer className="edit-modal-piece flex shrink-0 flex-wrap justify-between gap-3 border-t-4 border-black bg-[#ffe66d] px-4 py-3 sm:px-6">
          <button type="button" onClick={handleDelete} disabled={busy} className={`${arcadeButton} bg-red-500`}>
            <i className={`fa-solid ${isDeleting ? 'fa-spinner animate-spin' : 'fa-trash-can'} mr-2`}></i>{isDeleting ? '删除中...' : '删除私人记录'}
          </button>
          <div className="flex gap-3">
            {onSimilar && <button type="button" disabled={busy || hasUnsavedChanges} className={`${arcadeButton} bg-white`}
              title={hasUnsavedChanges ? '请先保存当前修改，再进入单番相似' : '以这部作品为参照找相似'}
              onClick={() => onSimilar(renderedItem)}>{hasUnsavedChanges ? '保存后找相似' : '找相似'}</button>}
            <button type="button" onClick={handleSave} disabled={busy} className={`${arcadeButton} bg-[#ff4f9a]`}>
              <i className={`fa-solid ${isSaving ? 'fa-spinner animate-spin' : 'fa-floppy-disk'} mr-2`}></i>{isSaving ? '保存中...' : '保存全部修改'}
            </button>
            <button type="button" onClick={handleClose} disabled={busy} className={`${arcadeButton} bg-white`}>关闭</button>
          </div>
        </footer>
      </section>
    </div>
  )
}

export default EditableDetailModal
