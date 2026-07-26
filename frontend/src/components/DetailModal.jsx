import { useEffect, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import api from '../api/api.js'
import { getCoverSrc, getTagClass } from '../utils/animeDisplay.js'

gsap.registerPlugin(useGSAP)

const infoCardClasses = [
  'bg-[#ffe66d] shadow-[5px_5px_0_0_#ff6b6b]',
  'bg-[#ff8fab] shadow-[5px_5px_0_0_#4ecdc4]',
  'bg-[#4ecdc4] shadow-[5px_5px_0_0_#ffe66d]',
  'bg-white shadow-[5px_5px_0_0_#ff6b6b]',
]

function DetailModal({
  selectedItem: activeItem,
  closeModal,
  editable = false,
  onRecordUpdated,
  onRecordDeleted,
  showToast,
}) {
  const [selectedItem, setSelectedItem] = useState(activeItem)
  const [formData, setFormData] = useState({ score: '', status: 'plan', review: '' })
  const [isSaving, setIsSaving] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [isClosing, setIsClosing] = useState(false)
  const [actionError, setActionError] = useState('')
  const [activeTextPanel, setActiveTextPanel] = useState('description')
  const modalRootRef = useRef(null)
  const backdropRef = useRef(null)
  const modalPanelRef = useRef(null)
  const closingRef = useRef(false)

  useEffect(() => {
    if (!activeItem) {
      if (!closingRef.current) setSelectedItem(null)
      return
    }

    closingRef.current = false
    setIsClosing(false)
    setSelectedItem(activeItem)
    setFormData({
      score: activeItem.score ?? '',
      status: activeItem.status ?? 'plan',
      review: activeItem.review ?? '',
    })
    setActionError('')
    setActiveTextPanel('description')
  }, [activeItem])

  const { contextSafe } = useGSAP(() => {
    if (!selectedItem || !backdropRef.current || !modalPanelRef.current) return undefined

    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const pieces = gsap.utils.toArray('.memphis-modal-piece')
    gsap.killTweensOf([backdropRef.current, modalPanelRef.current, ...pieces])

    if (reduceMotion) {
      gsap.set(backdropRef.current, { autoAlpha: 1 })
      gsap.set(modalPanelRef.current, {
        autoAlpha: 1,
        clearProps: 'transform',
      })
      gsap.set(pieces, { autoAlpha: 1, clearProps: 'transform' })
      return undefined
    }

    const timeline = gsap.timeline({
      defaults: { force3D: true },
      onComplete: () => {
        gsap.set([modalPanelRef.current, ...pieces], { clearProps: 'willChange' })
      },
    })

    timeline
      .fromTo(
        backdropRef.current,
        { autoAlpha: 0 },
        { autoAlpha: 1, duration: 0.14, ease: 'power1.out' },
      )
      .fromTo(
        modalPanelRef.current,
        {
          autoAlpha: 0,
          scale: 0.5,
          rotation: -5,
          y: 24,
          willChange: 'transform,opacity',
        },
        {
          autoAlpha: 1,
          scale: 1,
          rotation: 0,
          y: 0,
          duration: 0.52,
          ease: 'back.out(1.5)',
        },
        0.02,
      )
      .fromTo(
        pieces,
        {
          autoAlpha: 0,
          y: -24,
          scale: 0.88,
          willChange: 'transform,opacity',
        },
        {
          autoAlpha: 1,
          y: 0,
          scale: 1,
          duration: 0.38,
          stagger: 0.045,
          ease: 'back.out(1.8)',
        },
        0.14,
      )

    return () => timeline.kill()
  }, {
    scope: modalRootRef,
    dependencies: [selectedItem?.id],
    revertOnUpdate: true,
  })

  const handleClose = contextSafe(() => {
    if (!selectedItem || closingRef.current) return

    closingRef.current = true
    setIsClosing(true)
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    const finishClose = () => {
      closeModal()
      setSelectedItem(null)
      setIsClosing(false)
      closingRef.current = false
    }

    if (reduceMotion || !modalPanelRef.current || !backdropRef.current) {
      finishClose()
      return
    }

    gsap.killTweensOf([modalPanelRef.current, backdropRef.current])
    gsap.timeline({ onComplete: finishClose })
      .to(modalPanelRef.current, {
        scale: 0.8,
        rotation: 3,
        y: 50,
        autoAlpha: 0,
        duration: 0.24,
        ease: 'power3.in',
        force3D: true,
      })
      .to(
        backdropRef.current,
        {
          autoAlpha: 0,
          duration: 0.14,
          ease: 'power2.in',
        },
        0.1,
      )
  })

  useEffect(() => {
    if (!selectedItem) return undefined

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
      if (event.key === 'Escape') handleClose()
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [handleClose, selectedItem])

  if (!selectedItem) return null

  const coverSrc = getCoverSrc(selectedItem)
  const hasBaikeUrl = Boolean(selectedItem.baikeUrl)

  const handleFieldChange = (event) => {
    const { name, value } = event.target
    setFormData((current) => ({ ...current, [name]: value }))
  }

  const handleSave = async () => {
    const score = formData.score === '' ? null : Number(formData.score)
    if (score !== null && (!Number.isFinite(score) || score < 0 || score > 10)) {
      setActionError('评分必须是 0 到 10 之间的数字。')
      return
    }

    setIsSaving(true)
    setActionError('')
    try {
      const response = await api.patch(`records/${selectedItem.id}/`, {
        personal_score: score,
        watch_status: formData.status,
        review: formData.review,
      })
      onRecordUpdated?.(response.data)
      showToast?.('手账记录已保存。', 'success')
    } catch (requestError) {
      const detail = requestError.response?.data
      const message = typeof detail === 'string'
        ? detail
        : Object.values(detail ?? {}).flat().join(' ') || '保存失败，请稍后重试。'
      setActionError(message)
      showToast?.('保存失败，请检查填写内容。', 'error')
    } finally {
      setIsSaving(false)
    }
  }

  const handleDelete = async () => {
    const confirmed = window.confirm(`确定要删除《${selectedItem.title}》的追番记录吗？此操作无法撤销。`)
    if (!confirmed) return

    setIsDeleting(true)
    setActionError('')
    try {
      await api.delete(`records/${selectedItem.id}/`)
      showToast?.('追番记录已删除。', 'success')
      handleClose()
      onRecordDeleted?.(selectedItem.id)
    } catch {
      setActionError('删除失败，请稍后重试。')
      showToast?.('删除失败，请稍后重试。', 'error')
    } finally {
      setIsDeleting(false)
    }
  }

  const arcadeButtonClass = 'border-[3px] border-black bg-white px-4 py-1.5 text-xs font-black text-black shadow-[3px_3px_0_0_#111] transition-none hover:bg-[#ffe66d] active:translate-x-[3px] active:translate-y-[3px] active:shadow-none disabled:cursor-not-allowed disabled:opacity-50'

  return (
    <div
      ref={modalRootRef}
      id="detailModal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="modalTitle"
      className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) handleClose()
      }}
    >
      <div
        ref={backdropRef}
        className="memphis-modal-backdrop pointer-events-none absolute inset-0 bg-zinc-900/80 backdrop-blur-sm"
        aria-hidden="true"
      />

      <div
        ref={modalPanelRef}
        className="relative flex h-[calc(100dvh-1rem)] max-h-[860px] w-full max-w-4xl flex-col overflow-hidden border-4 border-black bg-[#fff5ee] shadow-[12px_12px_0_0_rgba(0,0,0,1)] sm:h-[calc(100dvh-2rem)]"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="grid h-3 shrink-0 grid-cols-[1fr_0.7fr_1.2fr] border-b-4 border-black">
          <span className="bg-[#ff6b6b]" />
          <span className="border-x-4 border-black bg-[#ffe66d]" />
          <span className="bg-[#4ecdc4]" />
        </div>

        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-3 custom-scrollbar sm:overflow-hidden sm:p-4">
          <div className="memphis-modal-piece mb-3 flex shrink-0 items-start justify-between gap-4 border-b-[3px] border-black pb-3">
            <div>
              <span className="mb-1.5 inline-flex -rotate-1 border-2 border-black bg-[#4ecdc4] px-2.5 py-0.5 text-[10px] font-black tracking-[0.18em] shadow-[3px_3px_0_#111]">
                ANIME FILE / 番剧档案
              </span>
              <h3 id="modalTitle" className="text-xl font-black leading-tight text-black sm:text-2xl">
                {selectedItem.title}
              </h3>
              <p id="modalJapaneseTitle" className="mt-1 text-xs font-bold text-zinc-500 sm:text-sm">
                {selectedItem.japaneseTitle || ''}
              </p>
            </div>
            <button
              type="button"
              onClick={handleClose}
              disabled={isClosing}
              aria-label="关闭详情"
              className="group flex h-9 w-9 shrink-0 items-center justify-center border-[3px] border-black bg-[#ff6b6b] text-black shadow-[3px_3px_0_#111] transition-none hover:bg-[#ffe66d] active:translate-x-1 active:translate-y-1 active:shadow-none disabled:pointer-events-none"
            >
              <i className="fa-solid fa-xmark text-xl group-hover:rotate-6"></i>
            </button>
          </div>

          <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 sm:grid-cols-[minmax(190px,0.78fr)_minmax(0,2fr)]">
            <div className="flex min-h-0 flex-col">
              <div className="memphis-modal-piece group relative aspect-[3/4] min-h-0 flex-1 overflow-hidden border-4 border-black bg-white shadow-[7px_7px_0_#4ecdc4] sm:aspect-auto">
                <img
                  id="modalCoverImg"
                  className="h-full w-full object-cover transition-[transform,filter,opacity] duration-300 ease-out group-hover:scale-[1.025] group-hover:rotate-[0.4deg] group-hover:opacity-85 group-hover:saturate-[0.85]"
                  src={coverSrc}
                  alt={`${selectedItem.title} Cover`}
                />
                <a
                  id="modalCoverLink"
                  href={coverSrc}
                  target="_blank"
                  rel="noreferrer"
                  className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-zinc-950/50 p-2 text-white opacity-0 transition-opacity duration-200 group-hover:opacity-100"
                >
                  <i className="fa-solid fa-arrow-up-right-from-square mb-2 text-2xl"></i>
                  <span className="border-2 border-white px-3 py-1 text-xs font-black">查看原图</span>
                </a>
              </div>

              <div className="memphis-modal-piece mt-3 shrink-0">
                <label htmlFor="coverUrlInput" className="mb-1 block text-[10px] font-black uppercase tracking-wider text-black">
                  网络图片 URL
                </label>
                <input
                  type="text"
                  id="coverUrlInput"
                  readOnly
                  value={selectedItem.cover || ''}
                  placeholder="暂无链接..."
                  className="w-full cursor-not-allowed border-2 border-black bg-white px-2.5 py-1.5 text-[10px] font-bold text-zinc-500 outline-none shadow-[inset_2px_2px_0_#d4d4d8]"
                />
              </div>

              <div className="memphis-modal-piece mt-3 shrink-0 border-[3px] border-black bg-[#ff8fab] p-2.5 shadow-[5px_5px_0_#111]">
                <span className="mb-2 block text-[11px] font-black text-black">
                  <i className="fa-solid fa-book-open mr-2"></i>
                  前往萌娘百科查看完整信息
                </span>
                <a
                  id="modalBaikeBtn"
                  href={hasBaikeUrl ? selectedItem.baikeUrl : undefined}
                  target={hasBaikeUrl ? '_blank' : undefined}
                  rel={hasBaikeUrl ? 'noreferrer' : undefined}
                  aria-disabled={!hasBaikeUrl}
                  className={`flex w-full items-center justify-center gap-2 border-[3px] border-black bg-[#ffe66d] px-3 py-1.5 text-[11px] font-black text-black shadow-[3px_3px_0_#111] transition-none hover:bg-[#4ecdc4] active:translate-x-[3px] active:translate-y-[3px] active:shadow-none${hasBaikeUrl ? '' : ' pointer-events-none cursor-not-allowed opacity-50 grayscale'}`}
                >
                  {hasBaikeUrl ? (
                    <><i className="fa-solid fa-arrow-up-right-from-square"></i> 点击跳转</>
                  ) : (
                    <><i className="fa-solid fa-link-slash"></i> 暂未录入百科链接</>
                  )}
                </a>
              </div>
            </div>

            <div className="flex min-h-0 min-w-0 flex-col gap-3">
              <div className="grid shrink-0 grid-cols-2 gap-2.5 text-sm">
                {[
                  {
                    label: '放送季度',
                    id: 'modalPeriod',
                    value: selectedItem.period,
                    inputClass: 'text-black',
                  },
                  {
                    label: '综合评分',
                    id: 'modalScore',
                    name: 'score',
                    value: editable
                      ? formData.score
                      : selectedItem.score !== null
                        ? `${selectedItem.score.toFixed(1)} / 10`
                        : '期待开播/待定',
                    inputClass: 'text-rose-700',
                  },
                  {
                    label: '制作公司',
                    id: 'modalStudio',
                    value: selectedItem.studio || '',
                    placeholder: '未录入...',
                    inputClass: 'text-black',
                  },
                  {
                    label: '话数情况',
                    id: 'modalEpisodes',
                    value: selectedItem.episodes || '',
                    placeholder: '未录入...',
                    inputClass: 'text-black',
                  },
                ].map((field, index) => (
                  <div
                    key={field.id}
                    className={`memphis-modal-piece min-w-0 border-2 border-black p-2.5 ${infoCardClasses[index]}`}
                  >
                    <span className="mb-0.5 block text-xs font-black uppercase tracking-wide text-black/70">
                      {field.label}
                    </span>
                    <input
                      type={field.name && editable ? 'number' : 'text'}
                      id={field.id}
                      name={field.name}
                      min={field.name && editable ? '0' : undefined}
                      max={field.name && editable ? '10' : undefined}
                      step={field.name && editable ? '0.1' : undefined}
                      readOnly={!field.name || !editable}
                      value={field.value}
                      onChange={field.name ? handleFieldChange : undefined}
                      className={`border-0 text-base font-black outline-none sm:text-lg ${field.id === 'modalScore' ? 'w-[7rem] max-w-full bg-white px-2 py-0.5 shadow-[2px_2px_0_#111]' : 'w-full bg-transparent'} ${field.inputClass}${field.name && editable ? ' cursor-text' : ' cursor-not-allowed'}`}
                      placeholder={field.placeholder || '--'}
                    />
                  </div>
                ))}
              </div>

              <div className="memphis-modal-piece">
                <span className="mb-1.5 block text-[10px] font-black uppercase tracking-[0.18em] text-black">
                  标签分类 / TAGS
                </span>
                <div id="modalTags" className="flex flex-wrap gap-2">
                  {selectedItem.tags.map((tag, index) => (
                    <span
                      key={tag}
                      className={`memphis-modal-tag ${index % 2 ? 'rotate-1' : '-rotate-1'} rounded-full px-3 py-1 text-xs font-black ${getTagClass(tag, selectedItem.tagColors?.[tag])}`}
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>

              {editable && (
                <div className="memphis-modal-piece border-[3px] border-black bg-[#4ecdc4] p-3 shadow-[5px_5px_0_#111]">
                  <label htmlFor="modalWatchStatus" className="mb-1.5 block text-xs font-black text-black">
                    观看状态
                  </label>
                  <select
                    id="modalWatchStatus"
                    name="status"
                    value={formData.status}
                    onChange={handleFieldChange}
                    className="w-full border-[3px] border-black bg-white px-3 py-2 text-sm font-black text-black outline-none shadow-[3px_3px_0_#111]"
                  >
                    <option value="plan">想看</option>
                    <option value="watching">在看</option>
                    <option value="completed">看过</option>
                    <option value="on_hold">搁置</option>
                  </select>
                </div>
              )}

              <div className="memphis-modal-piece flex min-h-0 flex-1 flex-col">
                <div className="mb-2 grid shrink-0 grid-cols-2 gap-1.5 border border-black bg-[#fff5ee] p-1">
                  <button
                    type="button"
                    aria-pressed={activeTextPanel === 'description'}
                    onClick={() => setActiveTextPanel('description')}
                    className={`border-2 border-black px-3 py-1.5 text-xs font-black transition-none ${activeTextPanel === 'description' ? 'bg-[#ffe66d] text-black shadow-[2px_2px_0_#ff6b6b]' : 'bg-white text-black hover:bg-[#4ecdc4]'}`}
                  >
                    <i className="fa-solid fa-align-left mr-2"></i>剧情简介
                  </button>
                  <button
                    type="button"
                    aria-pressed={activeTextPanel === 'review'}
                    onClick={() => setActiveTextPanel('review')}
                    className={`border-2 border-black px-3 py-1.5 text-xs font-black transition-none ${activeTextPanel === 'review' ? 'bg-[#ff8fab] text-black shadow-[2px_2px_0_#4ecdc4]' : 'bg-white text-black hover:bg-[#4ecdc4]'}`}
                  >
                    <i className="fa-solid fa-pen-nib mr-2"></i>个人评价
                  </button>
                </div>
                <textarea
                  key={activeTextPanel}
                  id="modalDescription"
                  name={activeTextPanel === 'review' ? 'review' : undefined}
                  readOnly={!editable || activeTextPanel === 'description'}
                  value={activeTextPanel === 'description'
                    ? selectedItem.description || ''
                    : editable
                      ? formData.review
                      : selectedItem.review || ''
                  }
                  onChange={activeTextPanel === 'review' ? handleFieldChange : undefined}
                  className={`min-h-[96px] w-full flex-1 resize-none overflow-y-auto border-2 border-black bg-white p-3 text-xs font-bold leading-relaxed text-zinc-700 outline-none shadow-[inset_3px_3px_0_#e4e4e7] custom-scrollbar${editable && activeTextPanel === 'review' ? ' cursor-text focus:bg-[#fffbea]' : ' cursor-not-allowed'}`}
                  placeholder={activeTextPanel === 'description' ? '暂无剧情简介...' : editable ? '写下你的观看感受...' : '暂无个人评价...'}
                  aria-label={activeTextPanel === 'description' ? '剧情简介' : '个人评价'}
                />
              </div>

              {editable && actionError && (
                <p role="alert" className="border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-xs font-black text-black shadow-[4px_4px_0_#111]">
                  <i className="fa-solid fa-circle-exclamation mr-2"></i>
                  {actionError}
                </p>
              )}
            </div>
          </div>
        </div>

        <div className="mx-3 mb-3 flex shrink-0 flex-wrap justify-between gap-2 border-[3px] border-black bg-[#4ecdc4] px-3 py-2 shadow-[4px_4px_0_#111] sm:mx-4">
          {editable ? (
            <button
              type="button"
              onClick={handleDelete}
              disabled={isSaving || isDeleting || isClosing}
              className={`${arcadeButtonClass} bg-[#ff6b6b] hover:bg-[#ff8fab]`}
            >
              <i className={`fa-solid ${isDeleting ? 'fa-spinner animate-spin' : 'fa-trash-can'} mr-2`}></i>
              {isDeleting ? '删除中...' : '删除记录'}
            </button>
          ) : <span />}

          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={handleClose}
              disabled={isSaving || isDeleting || isClosing}
              className={arcadeButtonClass}
            >
              关闭
            </button>
            {editable && (
              <button
                type="button"
                onClick={handleSave}
                disabled={isSaving || isDeleting || isClosing}
                className={`${arcadeButtonClass} bg-[#ffe66d] hover:bg-[#ff8fab]`}
              >
                <i className={`fa-solid ${isSaving ? 'fa-spinner animate-spin' : 'fa-floppy-disk'} mr-2`}></i>
                {isSaving ? '保存中...' : '保存修改'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default DetailModal
