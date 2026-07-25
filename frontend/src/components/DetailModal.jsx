import { useEffect, useState } from 'react'
import api from '../api/api.js'
import { getCoverSrc, getTagClass } from '../utils/animeDisplay.js'

function DetailModal({
  selectedItem: activeItem,
  closeModal,
  editable = false,
  onRecordUpdated,
  onRecordDeleted,
  showToast,
}) {
  const [selectedItem, setSelectedItem] = useState(activeItem)
  const [isVisible, setIsVisible] = useState(false)
  const [formData, setFormData] = useState({ score: '', status: 'plan', review: '' })
  const [isSaving, setIsSaving] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [actionError, setActionError] = useState('')
  const [activeTextPanel, setActiveTextPanel] = useState('description')

  useEffect(() => {
    let firstFrame
    let secondFrame
    let closeTimer

    if (activeItem) {
      setSelectedItem(activeItem)
      setFormData({
        score: activeItem.score ?? '',
        status: activeItem.status ?? 'plan',
        review: activeItem.review ?? '',
      })
      setActionError('')
      setActiveTextPanel('description')
      setIsVisible(false)

      firstFrame = window.requestAnimationFrame(() => {
        secondFrame = window.requestAnimationFrame(() => {
          setIsVisible(true)
        })
      })
    } else {
      setIsVisible(false)
      closeTimer = window.setTimeout(() => {
        setSelectedItem(null)
      }, 300)
    }

    return () => {
      window.cancelAnimationFrame(firstFrame)
      window.cancelAnimationFrame(secondFrame)
      window.clearTimeout(closeTimer)
    }
  }, [activeItem])

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
      if (event.key === 'Escape') closeModal()
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [closeModal, selectedItem])

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
      onRecordDeleted?.(selectedItem.id)
      showToast?.('追番记录已删除。', 'success')
      closeModal()
    } catch {
      setActionError('删除失败，请稍后重试。')
      showToast?.('删除失败，请稍后重试。', 'error')
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <div
      id="detailModal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="modalTitle"
      className={`fixed inset-0 bg-slate-900/60 backdrop-blur-sm z-50 flex items-center justify-center p-4 transition-all duration-300 ease-out ${isVisible ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeModal()
      }}
    >
      <div
        className={`bg-white rounded-3xl max-w-2xl w-full shadow-2xl overflow-hidden transform transition-transform duration-300 ease-out flex flex-col max-h-[90vh] ${isVisible ? 'scale-100 translate-y-0' : 'scale-95 translate-y-4'}`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="bg-gradient-to-r from-pink-400 to-indigo-500 h-2 w-full"></div>

        <div className="p-6 overflow-y-auto flex-1">
          <div className="flex justify-between items-start mb-4">
            <div>
              <h3 id="modalTitle" className="text-2xl font-bold text-slate-800">
                {selectedItem.title}
              </h3>
              <p id="modalJapaneseTitle" className="text-sm text-slate-400 mt-1">
                {selectedItem.japaneseTitle || ''}
              </p>
            </div>
            <button
              type="button"
              onClick={closeModal}
              aria-label="关闭详情"
              className="text-slate-400 hover:text-rose-500 hover:rotate-90 hover:bg-rose-50 p-2 rounded-full transition-all duration-200"
            >
              <i className="fa-solid fa-xmark text-lg"></i>
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="md:col-span-1 flex flex-col items-center">
              <div className="relative w-full aspect-[3/4] bg-white rounded-2xl overflow-hidden shadow-xl shadow-slate-200/50 border-2 border-slate-50 border-b-[6px] border-b-slate-200 group transition-all hover:-translate-y-1">
                <img
                  id="modalCoverImg"
                  className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
                  src={coverSrc}
                  alt={`${selectedItem.title} Cover`}
                />

                <a
                  id="modalCoverLink"
                  href={coverSrc}
                  target="_blank"
                  rel="noreferrer"
                  className="absolute inset-0 bg-black/50 opacity-0 group-hover:opacity-100 transition-opacity duration-200 flex flex-col items-center justify-center text-white p-2 text-decoration-none z-20"
                >
                  <i className="fa-solid fa-arrow-up-right-from-square text-2xl mb-1 transform translate-y-2 group-hover:translate-y-0 transition-transform duration-200"></i>
                  <span className="text-xs text-center font-medium transform translate-y-2 group-hover:translate-y-0 transition-transform duration-200 delay-75">
                    点击查看大图
                  </span>
                </a>
              </div>

              <div className="w-full mt-3">
                <label htmlFor="coverUrlInput" className="block text-xs font-semibold text-slate-500 mb-1">
                  网络图片URL：
                </label>
                <input
                  type="text"
                  id="coverUrlInput"
                  readOnly
                  value={selectedItem.cover || ''}
                  placeholder="暂无链接..."
                  className="w-full px-2.5 py-1.5 text-xs border border-slate-200 border-b-[2px] rounded-lg focus:outline-none bg-slate-50 text-slate-500 cursor-not-allowed shadow-inner transition-all"
                />
              </div>

              <div className="w-full mt-5 flex flex-col items-center bg-pink-50/50 p-3.5 rounded-xl border border-pink-100 border-b-[3px] border-b-pink-200 shadow-sm">
                <span className="text-xs text-slate-600 font-medium mb-2.5">
                  前往萌娘百科查看完整信息
                </span>
                <a
                  id="modalBaikeBtn"
                  href={hasBaikeUrl ? selectedItem.baikeUrl : undefined}
                  target={hasBaikeUrl ? '_blank' : undefined}
                  rel={hasBaikeUrl ? 'noreferrer' : undefined}
                  aria-disabled={!hasBaikeUrl}
                  className={`w-full flex items-center justify-center gap-1.5 px-4 py-2 bg-gradient-to-r from-pink-400 to-pink-500 hover:from-pink-500 hover:to-pink-600 active:translate-y-0.5 border-b-[3px] border-b-pink-600 active:border-b-0 text-white font-bold text-xs rounded-lg shadow-md transition-all duration-200${hasBaikeUrl ? '' : ' opacity-50 cursor-not-allowed pointer-events-none grayscale'}`}
                >
                  {hasBaikeUrl ? (
                    <>
                      <i className="fa-solid fa-book-open"></i> 点击跳转
                    </>
                  ) : (
                    <>
                      <i className="fa-solid fa-link-slash"></i> 暂未录入百科链接
                    </>
                  )}
                </a>
              </div>
            </div>

            <div className="md:col-span-2 flex flex-col gap-4">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div className="bg-white p-3 rounded-xl border border-slate-100 border-b-[4px] border-b-slate-200 shadow-sm hover:-translate-y-0.5 hover:shadow-md hover:border-indigo-200 hover:border-b-indigo-300 transition-all group/input">
                  <span className="text-slate-400 text-xs font-semibold block mb-0.5">放送季度</span>
                  <input
                    type="text"
                    id="modalPeriod"
                    readOnly
                    value={selectedItem.period}
                    className="bg-transparent font-bold text-slate-700 focus:outline-none border-b border-dashed border-slate-300 w-full cursor-not-allowed group-hover/input:border-indigo-300 transition-colors"
                    placeholder="--"
                  />
                </div>

                <div className="bg-white p-3 rounded-xl border border-slate-100 border-b-[4px] border-b-slate-200 shadow-sm hover:-translate-y-0.5 hover:shadow-md hover:border-pink-200 hover:border-b-pink-300 transition-all group/input">
                  <span className="text-slate-400 text-xs font-semibold block mb-0.5">综合评分</span>
                  <input
                    type={editable ? 'number' : 'text'}
                    id="modalScore"
                    name="score"
                    min={editable ? '0' : undefined}
                    max={editable ? '10' : undefined}
                    step={editable ? '0.1' : undefined}
                    readOnly={!editable}
                    value={editable
                      ? formData.score
                      : selectedItem.score !== null
                        ? `${selectedItem.score.toFixed(1)} / 10`
                        : '期待开播/待定'
                    }
                    onChange={handleFieldChange}
                    className={`bg-transparent font-black text-pink-500 text-base drop-shadow-sm focus:outline-none border-b border-dashed border-slate-300 w-full group-hover/input:border-pink-300 transition-colors${editable ? ' cursor-text focus:border-pink-400' : ' cursor-not-allowed'}`}
                    placeholder="--"
                  />
                </div>

                <div className="bg-white p-3 rounded-xl border border-slate-100 border-b-[4px] border-b-slate-200 shadow-sm hover:-translate-y-0.5 hover:shadow-md hover:border-indigo-200 hover:border-b-indigo-300 transition-all group/input">
                  <span className="text-slate-400 text-xs font-semibold block mb-0.5">制作公司</span>
                  <input
                    type="text"
                    id="modalStudio"
                    readOnly
                    value={selectedItem.studio || ''}
                    className="bg-transparent font-bold text-slate-700 focus:outline-none border-b border-dashed border-slate-300 w-full cursor-not-allowed group-hover/input:border-indigo-300 transition-colors"
                    placeholder="未录入..."
                  />
                </div>

                <div className="bg-white p-3 rounded-xl border border-slate-100 border-b-[4px] border-b-slate-200 shadow-sm hover:-translate-y-0.5 hover:shadow-md hover:border-indigo-200 hover:border-b-indigo-300 transition-all group/input">
                  <span className="text-slate-400 text-xs font-semibold block mb-0.5">话数情况</span>
                  <input
                    type="text"
                    id="modalEpisodes"
                    readOnly
                    value={selectedItem.episodes || ''}
                    className="bg-transparent font-bold text-slate-700 focus:outline-none border-b border-dashed border-slate-300 w-full cursor-not-allowed group-hover/input:border-indigo-300 transition-colors"
                    placeholder="未录入..."
                  />
                </div>
              </div>

              <div>
                <span className="text-slate-400 text-xs block mb-1">标签分类</span>
                <div id="modalTags" className="flex flex-wrap gap-1.5">
                  {selectedItem.tags.map((tag) => (
                    <span key={tag} className={`px-3 py-1 rounded-md text-xs font-medium ${getTagClass(tag, selectedItem.tagColors?.[tag])}`}>
                      {tag}
                    </span>
                  ))}
                </div>
              </div>

              {editable && (
                <div className="rounded-xl border border-indigo-100 bg-indigo-50/60 p-3 shadow-inner">
                  <label htmlFor="modalWatchStatus" className="mb-1.5 block text-xs font-semibold text-slate-500">
                    观看状态
                  </label>
                  <select
                    id="modalWatchStatus"
                    name="status"
                    value={formData.status}
                    onChange={handleFieldChange}
                    className="w-full rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-bold text-indigo-600 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
                  >
                    <option value="plan">想看</option>
                    <option value="watching">在看</option>
                    <option value="completed">看过</option>
                    <option value="on_hold">搁置</option>
                  </select>
                </div>
              )}

              <div className="flex flex-col mt-1 group/desc">
                <div className="mb-2 flex items-center gap-2 rounded-xl border border-slate-200/80 bg-slate-100/80 p-1 shadow-inner">
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
                  id="modalDescription"
                  name={activeTextPanel === 'review' ? 'review' : undefined}
                  readOnly={!editable || activeTextPanel === 'description'}
                  value={activeTextPanel === 'description' ? selectedItem.description || '' : editable ? formData.review : selectedItem.review || ''}
                  onChange={activeTextPanel === 'review' ? handleFieldChange : undefined}
                  className={`w-full h-[180px] p-4 bg-slate-50/80 border-2 border-slate-200/60 rounded-2xl text-xs text-slate-600 font-medium focus:outline-none resize-none transition-all shadow-[inset_0_4px_12px_rgba(0,0,0,0.06)] group-hover/desc:shadow-[inset_0_4px_12px_rgba(0,0,0,0.1)] leading-relaxed custom-scrollbar animate-[viewFadeIn_0.2s_ease-out_both]${editable && activeTextPanel === 'review' ? ' cursor-text focus:border-indigo-300 focus:bg-white' : ' cursor-not-allowed'}`}
                  placeholder={activeTextPanel === 'description' ? '暂无剧情简介...' : editable ? '写下你的观看感受...' : '暂无个人评价...'}
                  aria-label={activeTextPanel === 'description' ? '剧情简介' : '个人评价'}
                ></textarea>
              </div>

              {editable && actionError && (
                <p role="alert" className="rounded-xl bg-rose-50 px-4 py-3 text-xs font-medium text-rose-600">
                  <i className="fa-solid fa-circle-exclamation mr-2"></i>
                  {actionError}
                </p>
              )}
            </div>
          </div>
        </div>

        <div className="bg-slate-50 px-6 py-4 border-t border-slate-100 flex flex-wrap justify-between gap-3">
          {editable ? (
            <button
              type="button"
              onClick={handleDelete}
              disabled={isSaving || isDeleting}
              className="px-5 py-2 bg-white text-rose-600 hover:bg-rose-50 active:scale-95 font-bold text-sm rounded-xl border border-rose-200 transition-all shadow-sm disabled:cursor-not-allowed disabled:opacity-50"
            >
              <i className={`fa-solid ${isDeleting ? 'fa-spinner animate-spin' : 'fa-trash-can'} mr-2`}></i>
              {isDeleting ? '删除中...' : '删除记录'}
            </button>
          ) : <span />}

          <div className="flex gap-3">
          <button
            type="button"
            onClick={closeModal}
            disabled={isSaving || isDeleting}
            className="px-5 py-2 bg-slate-100 text-slate-500 hover:text-slate-700 hover:bg-slate-200 active:scale-95 font-medium text-sm rounded-xl transition-all shadow-sm"
          >
            {editable ? '关闭' : '取消'}
          </button>
          {editable && (
            <button
              type="button"
              onClick={handleSave}
              disabled={isSaving || isDeleting}
              className="px-5 py-2 bg-gradient-to-r from-pink-500 to-indigo-500 text-white hover:shadow-lg active:scale-95 font-bold text-sm rounded-xl transition-all shadow-md disabled:cursor-not-allowed disabled:opacity-60"
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
