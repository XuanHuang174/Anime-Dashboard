import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
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
import WatchHistoryPanel from './WatchHistoryPanel.jsx'
import WatchPlanPanel from './WatchPlanPanel.jsx'
import useTagPresets from '../hooks/useTagPresets.js'
import { bangumiSubjectUrl, moegirlTitleUrl } from '../utils/journalSourceLinks.js'

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
    bangumiUrl: item.bangumiUrl ?? '',
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
  onReviewIdentity,
  initialHistoryOpen = false,
  initialPlanOpen = false,
  initialPlanAction = '',
  onPlanCommitted,
  showToast,
}) {
  const overlayRef = useRef(null)
  const modalRef = useRef(null)
  const closeHandlerRef = useRef(null)
  const historyGuardRef = useRef(null)
  const planGuardRef = useRef(null)
  const [planLocked, setPlanLocked] = useState(false)
  const [planOpen, setPlanOpen] = useState(initialPlanOpen)
  const planEntryRef = useRef(null), returnEntryRef = useRef(null)
  const editorScrollRef = useRef(null)
  const editorPositions = useRef([]), historyEntryRef = useRef(null)
  const [historyOpen, setHistoryOpen] = useState(initialHistoryOpen)
  useLayoutEffect(() => {
    if (!historyOpen && !planOpen && editorPositions.current.length) {
      for (const [element, top, left] of editorPositions.current) { element.scrollTop = top; element.scrollLeft = left }
      (returnEntryRef.current || historyEntryRef.current)?.focus({ preventScroll: true })
    }
  }, [historyOpen, planOpen])
  const posterInputRef = useRef(null)
  const [renderedItem, setRenderedItem] = useState(selectedItem)
  const [formData, setFormData] = useState(() => createForm(selectedItem ?? {}))
  const [posterFile, setPosterFile] = useState(null)
  const [newTag, setNewTag] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [error, setError] = useState('')
  const [activeEditorPanel, setActiveEditorPanel] = useState('tags')
  const personalPresets = useTagPresets(Boolean(selectedItem))
  const [saveAsPreset, setSaveAsPreset] = useState(false), [managePresets, setManagePresets] = useState(false)
  const [presetDraft, setPresetDraft] = useState(''), [suggestedTags, setSuggestedTags] = useState([])
  const [presetMessage, setPresetMessage] = useState('')
  useEffect(() => {
    if (!managePresets) return
    const controller = new AbortController()
    api.get('records/filter-options/', { signal: controller.signal }).then(({ data }) => setSuggestedTags(data.tags ?? [])).catch(() => {})
    return () => controller.abort()
  }, [managePresets])

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
    // Preserve the editor tab and scroll when a history command updates this work.
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
    if (!busy) {
      const close = () => playExit(closeModal)
      if (historyOpen && historyGuardRef.current) historyGuardRef.current(close)
      else if (planOpen && planGuardRef.current) planGuardRef.current(close, '关闭详情')
      else close()
    }
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
      if (event.key === 'Tab') {
        const nodes = [...(modalRef.current?.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]') || [])].filter(node => !node.closest('[inert]') && node.getClientRects().length)
        const first = nodes[0], last = nodes.at(-1)
        if (first && event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (last && !event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [renderedItem])

  if (!renderedItem) return null
  const comparableForm = form => ({ ...form, score: String(form.score), watchedEps: String(form.watchedEps), totalEps: String(form.totalEps) })
  const hasUnsavedChanges = Boolean(posterFile) || JSON.stringify(comparableForm(formData)) !== JSON.stringify(comparableForm(createForm(renderedItem)))
  const openHistory = () => {
    editorPositions.current = [editorScrollRef.current, ...editorScrollRef.current.querySelectorAll('*')].filter(element => element.scrollTop || element.scrollLeft).map(element => [element, element.scrollTop, element.scrollLeft])
    returnEntryRef.current = historyEntryRef.current
    setHistoryOpen(true)
  }
  const openPlan = () => {
    editorPositions.current = [editorScrollRef.current, ...editorScrollRef.current.querySelectorAll('*')].filter(element => element.scrollTop || element.scrollLeft).map(element => [element, element.scrollTop, element.scrollLeft])
    returnEntryRef.current = planEntryRef.current
    setPlanOpen(true)
  }

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

  const addTag = async () => {
    if (saveAsPreset && newTag.trim() && !presetTags.includes(newTag.trim())) {
      if (!await personalPresets.change(newTag.trim())) return
    }
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
      bangumi_url: formData.bangumiUrl.trim(),
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
  const editorPanelOptions = [
    { value: 'tags', label: '标签管理', icon: 'fa-tags', activeClass: 'bg-[#ffe66d]' },
    { value: 'description', label: '剧情简介', icon: 'fa-align-left', activeClass: 'bg-[#4ecdc4]' },
    { value: 'review', label: '个人评价', icon: 'fa-pen-nib', activeClass: 'bg-[#ff8fab]' },
  ]
  const editorPanelSwitcher = (
    <div className="grid grid-cols-3 border-[3px] border-black bg-white shadow-[4px_4px_0_#111]" role="group" aria-label="切换编辑模块">
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
          <i className={`fa-solid ${option.icon} mr-1.5`} aria-hidden="true"></i>
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
      aria-label={historyOpen ? '观看历程管理' : planOpen ? '编辑观看安排' : '编辑私人作品'}
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
            <span className="inline-block border-2 border-black bg-[#ffe66d] px-2 py-1 text-[10px] font-black uppercase tracking-[0.16em] shadow-[3px_3px_0_#111]">{historyOpen ? '观看历程管理 · 仅本人可见' : planOpen ? '观看安排 · 独立保存' : 'Edit Record'}</span>
            {(historyOpen || planOpen) && <h2 className="mt-3 text-lg font-black">{renderedItem.title}</h2>}
            <div inert={planLocked} style={historyOpen || planOpen ? { display: 'none' } : undefined} className="mt-3 grid gap-3 sm:grid-cols-2">
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

        {historyOpen && <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar sm:p-4"><WatchHistoryPanel item={renderedItem} onRecordUpdated={onRecordUpdated} closeGuardRef={historyGuardRef} initiallyOpen management /></div>}
        {planOpen && !historyOpen && <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar sm:p-4"><WatchPlanPanel item={renderedItem} editable initiallyOpen management initialAction={initialPlanAction} disabled={busy || hasUnsavedChanges} closeGuardRef={planGuardRef} onLockChange={setPlanLocked} onCommitted={onPlanCommitted} onHistory={() => { setPlanOpen(false); setHistoryOpen(true) }} /></div>}
        <div ref={editorScrollRef} style={historyOpen || planOpen ? { display: 'none' } : undefined} className="edit-record-body min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar sm:p-4">
          <div className="edit-record-layout grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
            <aside inert={planLocked} className="edit-record-sources edit-modal-piece mx-auto w-full max-w-[240px] lg:mx-0 lg:flex lg:max-w-[220px] lg:flex-col custom-scrollbar">
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

              <div className="mb-1 mt-3 flex shrink-0 flex-wrap items-center justify-between gap-1">
                <label htmlFor="editBaikeUrl" className="text-[11px] font-black">萌娘百科 URL</label>
                <button type="button" className="border-2 border-black bg-[#ff8fab] px-2 py-1 text-[11px] font-black hover:bg-[#ffe66d] disabled:opacity-50" disabled={!formData.title.trim()} onClick={() => setFormData(current => ({ ...current, baikeUrl: moegirlTitleUrl(current.title) }))}><i className="fa-solid fa-book-open mr-1" aria-hidden="true" />生成链接</button>
              </div>
              <input id="editBaikeUrl" name="baikeUrl" type="url" value={formData.baikeUrl} onChange={updateField} placeholder="https://zh.moegirl.org.cn/..." className={fieldClass} />
              <div className="mb-1 mt-3 flex shrink-0 flex-wrap items-center justify-between gap-1">
                <label htmlFor="editBangumiUrl" className="text-[11px] font-black">Bangumi 作品 URL</label>
                <button type="button" className="border-2 border-black bg-[#4ecdc4] px-2 py-1 text-[11px] font-black hover:bg-[#ffe66d] disabled:opacity-50" disabled={!bangumiSubjectUrl(renderedItem.confirmedBangumiId)} onClick={() => setFormData(current => ({ ...current, bangumiUrl: bangumiSubjectUrl(renderedItem.confirmedBangumiId) }))}><i className="fa-solid fa-link mr-1" aria-hidden="true" />填入链接</button>
              </div>
              <input id="editBangumiUrl" name="bangumiUrl" type="url" value={formData.bangumiUrl} onChange={updateField} placeholder="https://bgm.tv/subject/..." className={fieldClass} />
              {onReviewIdentity && <button type="button" disabled={busy || hasUnsavedChanges || planLocked} onClick={() => onReviewIdentity(renderedItem.id)} className="mt-3 border-2 border-black bg-white px-2 py-1 text-[11px] font-black shadow-[3px_3px_0_#4ecdc4] disabled:opacity-50" title={hasUnsavedChanges ? '请先保存作品修改，再管理关联' : '更换、解除或撤销私人标准作品关联'}>管理标准作品关联</button>}
              {!bangumiSubjectUrl(renderedItem.confirmedBangumiId) && <p className="mt-2 text-[11px] font-bold leading-relaxed">尚未确认标准作品，可在“数据管理 → 核对标准作品”中核对身份。</p>}
              <details className="mt-3 shrink-0 text-[11px] font-bold leading-relaxed">
                <summary className="cursor-pointer py-1 underline underline-offset-2">链接填写说明</summary>
                <p className="mt-1">Bangumi 根据已确认身份填写。萌百按标题生成，可能需要修正消歧义页面。修改名称不会自动覆盖链接。</p>
              </details>

            </aside>

            <div className="edit-record-workspace flex min-h-0 min-w-0 flex-col gap-3">
              <div inert={planLocked} className="edit-modal-piece grid shrink-0 gap-2.5 border-[3px] border-black bg-white p-3 shadow-[6px_6px_0_#ffe66d] sm:grid-cols-2 lg:grid-cols-12">
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
                <label className={`${['watching', 'completed', 'on_hold'].includes(formData.status) ? 'lg:col-span-3' : 'lg:col-span-6'} block text-xs font-black`}>
                  主观评分
                  <input name="score" type="number" min="0" max="10" step="0.1" value={formData.score} onChange={updateField} className={`${fieldClass} mt-1 !text-red-600`} placeholder="0.0 - 10.0" />
                </label>
                <div className={`${['watching', 'completed', 'on_hold'].includes(formData.status) ? 'lg:col-span-3' : 'lg:col-span-6'} edit-watch-status-select min-w-0 text-xs font-black`}>
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
                {['watching', 'completed', 'on_hold'].includes(formData.status) && (
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

              <div inert={planLocked} className="edit-record-panel-switcher edit-modal-piece shrink-0 bg-[#fff5ee] pb-1">
                {editorPanelSwitcher}
              </div>
              <section inert={planLocked} className="edit-record-editor edit-modal-piece min-h-0 flex-1">
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
                          disabled={personalPresets.busy}
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
                        <button type="button" disabled={personalPresets.busy} onClick={addTag} className="border-y-2 border-r-2 border-black bg-[#ffe66d] px-3 text-xs font-black hover:bg-[#4ecdc4]">
                          <i className="fa-solid fa-plus mr-1.5"></i>增加
                        </button>
                      </div>
                      <label className="mt-2 flex shrink-0 items-center gap-2 text-[11px] font-bold"><input type="checkbox" checked={saveAsPreset} onChange={event => setSaveAsPreset(event.target.checked)} />同时保存为常用标签</label>
                    </div>

                    <div className="edit-record-presets-panel flex min-h-0 flex-col border-[3px] border-black bg-[#ffe66d] p-3 shadow-[6px_6px_0_#ff8fab]">
                      <div className="mb-2 shrink-0">
                        <div className="flex justify-between gap-2"><span className="text-xs font-black"><i className="fa-solid fa-wand-magic-sparkles mr-2"></i>快捷预设</span>
                          <button type="button" className="text-[11px] font-bold underline" aria-expanded={managePresets} onClick={() => setManagePresets(value => !value)}>{managePresets ? '完成管理' : '管理常用'}</button></div>
                        <p className="mt-1 text-[10px] font-bold">点击即可加入，再在左侧选择专属颜色。</p>
                      </div>
                      <div className="edit-record-presets-scroll min-h-0 flex-1 overflow-y-auto pr-1 custom-scrollbar">
                        {/* Natural-height rows scroll inside the bounded viewport. */}
                        <div className="flex flex-wrap items-start content-start gap-1.5 pb-2">
                        {personalPresets.error && <p role="alert" className="w-full text-xs font-bold text-red-700">{personalPresets.error}</p>}
                        {managePresets && <div className="w-full border-2 border-black bg-white p-2">
                          <div className="flex gap-1"><input list="personal-tag-suggestions" disabled={personalPresets.busy} aria-label="新增常用标签" maxLength={50} value={presetDraft} onChange={event => { setPresetDraft(event.target.value); setPresetMessage('') }} className="min-w-0 flex-1 border-2 border-black px-2 text-xs" placeholder="输入或选择手账已有标签" />
                            <button type="button" disabled={personalPresets.busy || !presetDraft.trim()} className="border-2 border-black bg-[#4ecdc4] px-2 py-1 text-xs font-bold" onClick={async () => {
                              if (presetTags.includes(presetDraft.trim())) { setPresetMessage('该标签已在默认预设中，可直接点击添加。'); return }
                              if (await personalPresets.change(presetDraft.trim())) { setPresetDraft(''); setPresetMessage('已保存到我的常用。') }
                            }}>保存常用</button></div>
                          <datalist id="personal-tag-suggestions">{[...new Set([...suggestedTags, ...formData.tags])].filter(tag => !presetTags.includes(tag) && !personalPresets.tags.includes(tag)).map(tag => <option value={tag} key={tag} />)}</datalist>
                          <p className="mt-1 text-[10px]">按账号保存；移除常用不会删除作品上的标签。默认预设已可直接使用。</p>
                          {presetMessage && <p role="status" className="mt-1 text-[10px] font-bold">{presetMessage}</p>}
                        </div>}
                        <p className="w-full text-[11px] font-black">我的常用</p>
                        {!personalPresets.tags.length && <p className="w-full text-[10px]">暂无常用标签，可在“管理常用”中保存。</p>}
                        {personalPresets.tags.filter(tag => !presetTags.includes(tag)).map(tag => <span key={tag} className="inline-flex items-center gap-1">
                          <button type="button" disabled={formData.tags.includes(tag)} onClick={() => addTagValue(tag)} className={`rounded-full border-2 border-black px-2 py-1 text-[11px] font-black shadow-[2px_2px_0_#111] disabled:opacity-40 ${getTagClass(tag, getDefaultTagColor(tag))}`}>{formData.tags.includes(tag) ? '✓ ' : '+ '}{tag}</button>
                          {managePresets && <button type="button" disabled={personalPresets.busy} aria-label={`移除常用标签 ${tag}`} className="border-2 border-black bg-[#ff8fab] px-1 text-xs" onClick={() => personalPresets.change(tag, true)}>×</button>}
                        </span>)}
                        <p className="mt-2 w-full text-[11px] font-black">默认预设</p>
                        {presetTags.map((tag) => {
                          const isSelected = formData.tags.includes(tag)
                          return (
                            <button key={tag} type="button" disabled={isSelected} onClick={() => addTagValue(tag)} className={`rounded-full border-2 border-black px-2 py-1 text-[11px] font-black leading-4 shadow-[2px_2px_0_#111] transition-none active:translate-x-0.5 active:translate-y-0.5 active:shadow-none disabled:cursor-default disabled:opacity-40 ${getTagClass(tag, getDefaultTagColor(tag))}`}>
                              <i className={`fa-solid ${isSelected ? 'fa-check' : 'fa-plus'} mr-1.5 text-[9px]`}></i>{tag}
                            </button>
                          )
                        })}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className={`flex h-full min-h-0 flex-col border-[3px] border-black p-3 shadow-[6px_6px_0_#111] ${
                    activeEditorPanel === 'description' ? 'bg-[#4ecdc4]' : 'bg-[#ff8fab]'
                  }`}>
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
          {historyOpen ? <><button type="button" className={`${arcadeButton} bg-white`} onClick={() => historyGuardRef.current?.(() => setHistoryOpen(false), '返回作品编辑')}>返回作品编辑</button><button type="button" onClick={handleClose} className={`${arcadeButton} bg-white`}>关闭</button></> : planOpen ? <><button type="button" className={`${arcadeButton} bg-white`} onClick={() => planGuardRef.current?.(() => setPlanOpen(false), '返回作品编辑')}>返回作品编辑</button><button type="button" onClick={handleClose} className={`${arcadeButton} bg-white`}>关闭</button></> : <>
          <div className="flex flex-wrap gap-4">
          <button type="button" onClick={handleDelete} disabled={busy || planLocked} className={`${arcadeButton} bg-red-500`}>
            <i className={`fa-solid ${isDeleting ? 'fa-spinner animate-spin' : 'fa-trash-can'} mr-2`}></i>{isDeleting ? '删除中...' : '删除私人记录'}
          </button>
          <button ref={historyEntryRef} type="button" disabled={busy || hasUnsavedChanges || planLocked} aria-describedby={hasUnsavedChanges ? 'edit-history-hint' : undefined} title={hasUnsavedChanges ? '请先保存当前作品修改后再查看历程' : '查看观看历程与重看记录'} onClick={openHistory} className={`${arcadeButton} bg-white`}>观看历程</button>
          <button ref={planEntryRef} type="button" disabled={busy || hasUnsavedChanges} title={hasUnsavedChanges ? '请先保存当前作品修改' : '管理观看计划与优先级'} onClick={openPlan} className={`${arcadeButton} bg-white`}>观看安排</button>
          </div>
          <div className="flex flex-wrap gap-3">
            <button type="button" onClick={handleSave} disabled={busy || planLocked} className={`${arcadeButton} bg-[#ff4f9a]`}>
              <i className={`fa-solid ${isSaving ? 'fa-spinner animate-spin' : 'fa-floppy-disk'} mr-2`}></i>{isSaving ? '保存中...' : '保存全部修改'}
            </button>
            <button type="button" onClick={handleClose} disabled={busy} className={`${arcadeButton} bg-white`}>关闭</button>
          </div>
          {hasUnsavedChanges && <p id="edit-history-hint" className="w-full text-xs font-bold">请先保存当前作品修改，再打开观看历程或观看安排；两者单独保存。</p>}
          </>}
        </footer>
      </section>
    </div>
  )
}

export default EditableDetailModal
