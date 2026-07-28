import { useEffect, useState } from 'react'
import api from '../api/api.js'

const BUTTON_STATES = {
  private: {
    label: '分享个人手账',
    icon: 'fa-share-nodes',
    className: 'bg-[#4ecdc4] shadow-[4px_4px_0_#111]',
  },
  pending: {
    label: '审核中',
    icon: 'fa-hourglass-half',
    className: 'cursor-not-allowed bg-[#ffe66d] shadow-[4px_4px_0_#111]',
  },
  approved: {
    label: '取消分享',
    icon: 'fa-eye-slash',
    className: 'bg-[#ff6b6b] shadow-[4px_4px_0_#111]',
  },
}

function PublicJournalShareButton({
  publicStatus = 'private',
  onStatusChange,
  showToast,
}) {
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)
  const [isProcessing, setIsProcessing] = useState(false)
  const buttonState = BUTTON_STATES[publicStatus] ?? BUTTON_STATES.private

  useEffect(() => {
    if (!isConfirmOpen) return undefined
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const closeOnEscape = (event) => {
      if (event.key === 'Escape' && !isProcessing) setIsConfirmOpen(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [isConfirmOpen, isProcessing])

  const applyForSharing = async () => {
    setIsProcessing(true)
    try {
      const response = await api.post('public-journal/status/', {})
      onStatusChange?.(response.data)
      setIsConfirmOpen(false)
      showToast?.('分享申请已提交，请等待管理员审核。')
    } catch (error) {
      showToast?.(
        error.response?.data?.detail || '分享申请提交失败，请稍后重试。',
        'error',
      )
    } finally {
      setIsProcessing(false)
    }
  }

  const cancelSharing = async () => {
    setIsProcessing(true)
    try {
      const response = await api.patch('public-journal/status/', {})
      onStatusChange?.(response.data)
      showToast?.('已取消分享，你的手账已立即恢复私密。')
    } catch (error) {
      showToast?.(
        error.response?.data?.detail || '取消分享失败，请稍后重试。',
        'error',
      )
    } finally {
      setIsProcessing(false)
    }
  }

  const handleClick = () => {
    if (isProcessing || publicStatus === 'pending') return
    if (publicStatus === 'approved') {
      cancelSharing()
      return
    }
    setIsConfirmOpen(true)
  }

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        disabled={isProcessing || publicStatus === 'pending'}
        className={`dashboard-arcade-button inline-flex items-center gap-2 rounded-full ${buttonState.className}`}
      >
        <i className={`fa-solid ${isProcessing ? 'fa-spinner animate-spin' : buttonState.icon}`} />
        {isProcessing ? '处理中…' : buttonState.label}
      </button>

      {isConfirmOpen && (
        <div
          className="fixed inset-0 z-[300] flex items-center justify-center bg-zinc-950/80 px-4 backdrop-blur-[2px]"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !isProcessing) {
              setIsConfirmOpen(false)
            }
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="share-journal-title"
            className="relative w-full max-w-lg border-4 border-black bg-[#fff5ee] p-6 text-black shadow-[12px_12px_0_#111]"
          >
            <span className="absolute -right-5 -top-5 flex h-14 w-14 rotate-6 items-center justify-center border-4 border-black bg-[#4ecdc4] text-2xl shadow-[4px_4px_0_#ffe66d]">
              <i className="fa-solid fa-satellite-dish" />
            </span>
            <span className="inline-flex -rotate-1 border-2 border-black bg-[#ffe66d] px-3 py-1 text-xs font-black tracking-[0.15em] shadow-[3px_3px_0_#111]">
              PUBLIC JOURNAL PASS
            </span>
            <h2 id="share-journal-title" className="mt-5 text-3xl font-black">
              申请分享个人手账？
            </h2>
            <p className="mt-4 border-l-[7px] border-black bg-white px-4 py-3 text-sm font-bold leading-7 shadow-[4px_4px_0_#ff8fab]">
              审核通过后，其他访客可以查看你的番剧记录、评分和个人评价；账号邮箱、管理功能与私人操作不会公开。
            </p>
            <div className="mt-7 grid grid-cols-2 gap-4">
              <button
                type="button"
                onClick={() => setIsConfirmOpen(false)}
                disabled={isProcessing}
                className="border-4 border-black bg-white px-4 py-3 font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:opacity-60"
              >
                暂不分享
              </button>
              <button
                type="button"
                onClick={applyForSharing}
                disabled={isProcessing}
                className="border-4 border-black bg-[#4ecdc4] px-4 py-3 font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none disabled:opacity-60"
              >
                {isProcessing ? '正在提交…' : '确认申请'}
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  )
}

export default PublicJournalShareButton
