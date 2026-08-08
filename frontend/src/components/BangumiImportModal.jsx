import { useEffect, useMemo, useRef, useState } from 'react'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import api from '../api/api.js'

gsap.registerPlugin(useGSAP)

// Keep each backend request short enough to report visible progress before the
// global Axios timeout. Requests remain strictly serial with a pause between.
// One entry per request makes the UI advance after every anime and keeps a
// slow metadata/image upstream from holding an entire multi-item batch open.
const BATCH_SIZE = 1
const BATCH_TIMEOUT_MS = 120000

function sleep(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

function getErrorMessage(error, fallback) {
  const detail = error.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  return fallback
}

function BangumiImportModal({ isOpen, onClose, onCompleted, showToast }) {
  const overlayRef = useRef(null)
  const modalRef = useRef(null)
  const [uid, setUid] = useState('')
  const [status, setStatus] = useState('idle')
  const [total, setTotal] = useState(0)
  const [processed, setProcessed] = useState(0)
  const [created, setCreated] = useState(0)
  const [updated, setUpdated] = useState(0)
  const [failed, setFailed] = useState(0)
  const [activeItem, setActiveItem] = useState(0)
  const [error, setError] = useState('')

  const isRunning = status === 'checking' || status === 'importing'
  const progress = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0
  const progressLabel = useMemo(
    () => `已处理 ${Math.min(processed, total)}/${total} 部`,
    [processed, total],
  )

  const { contextSafe } = useGSAP(() => {
    if (!isOpen || !overlayRef.current || !modalRef.current) return undefined

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      gsap.set([overlayRef.current, modalRef.current], { clearProps: 'all' })
      return undefined
    }

    const timeline = gsap.timeline()
    timeline
      .fromTo(overlayRef.current, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.16 })
      .fromTo(
        modalRef.current,
        { autoAlpha: 0, y: -42, scale: 0.76, rotation: -2 },
        { autoAlpha: 1, y: 0, scale: 1, rotation: 0, duration: 0.52, ease: 'back.out(1.7)' },
        0.04,
      )

    return () => timeline.kill()
  }, { scope: overlayRef, dependencies: [isOpen], revertOnUpdate: true })

  const playExit = contextSafe((afterExit) => {
    if (!overlayRef.current || !modalRef.current || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      afterExit()
      return
    }

    gsap.killTweensOf([overlayRef.current, modalRef.current])
    gsap.timeline({ onComplete: afterExit })
      .to(modalRef.current, {
        autoAlpha: 0,
        y: 44,
        scale: 0.88,
        rotation: 1.5,
        duration: 0.22,
        ease: 'power3.in',
      })
      .to(overlayRef.current, { autoAlpha: 0, duration: 0.12 }, '<0.08')
  })

  useEffect(() => {
    if (!isOpen) return undefined
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) {
      setUid('')
      setStatus('idle')
      setTotal(0)
      setProcessed(0)
      setCreated(0)
      setUpdated(0)
      setFailed(0)
      setActiveItem(0)
      setError('')
    }
  }, [isOpen])

  if (!isOpen) return null

  const closeSafely = () => {
    if (!isRunning) playExit(onClose)
  }

  const startImport = async (event) => {
    event.preventDefault()
    const normalizedUid = uid.trim()
    if (!normalizedUid) {
      setError('请输入 Bangumi UID。')
      return
    }

    setStatus('checking')
    setError('')
    setTotal(0)
    setProcessed(0)
    setCreated(0)
    setUpdated(0)
    setFailed(0)
    setActiveItem(0)

    try {
      const summaryResponse = await api.get('external/bangumi-import/summary/', {
        params: { uid: normalizedUid },
      })
      const collectionTotal = Number(summaryResponse.data?.total) || 0
      setTotal(collectionTotal)

      if (collectionTotal === 0) {
        setStatus('complete')
        showToast?.('该 Bangumi 用户暂时没有公开的动画收藏。', 'success')
        return
      }

      setStatus('importing')
      let offset = 0
      let createdCount = 0
      let updatedCount = 0
      let failedCount = 0

      // Deliberately serial: never issue the next upstream request before the
      // previous batch has fully completed, then leave an 800–1000 ms gap.
      while (offset < collectionTotal) {
        setActiveItem(Math.min(collectionTotal, offset + 1))
        const batchResponse = await api.post(
          'external/bangumi-import/batch/',
          {
            uid: normalizedUid,
            offset,
            limit: BATCH_SIZE,
          },
          { timeout: BATCH_TIMEOUT_MS },
        )
        const batch = batchResponse.data ?? {}
        const fetched = Number(batch.fetched) || 0
        if (fetched <= 0) {
          throw new Error('Bangumi 返回了空批次，导入已安全停止。')
        }

        createdCount += Number(batch.created) || 0
        updatedCount += Number(batch.updated) || 0
        failedCount += Number(batch.failed) || 0
        offset = Math.min(
          collectionTotal,
          Number(batch.next_offset) || (offset + fetched),
        )
        setCreated(createdCount)
        setUpdated(updatedCount)
        setFailed(failedCount)
        setProcessed(offset)

        if (offset < collectionTotal) {
          await sleep(800 + Math.floor(Math.random() * 201))
        }
      }

      setStatus('complete')
      setActiveItem(0)
      showToast?.(
        `Bangumi 收藏导入完成：新增 ${createdCount} 部，更新 ${updatedCount} 部${failedCount ? `，失败 ${failedCount} 部` : ''}。`,
        failedCount ? 'error' : 'success',
      )
      try {
        await onCompleted?.()
      } catch (refreshError) {
        // Import already succeeded. A follow-up dashboard refresh must not
        // turn the completed operation into a misleading import failure.
        showToast?.('导入已完成，但列表刷新失败，请手动刷新页面。', 'error')
      }
    } catch (requestError) {
      setStatus('error')
      setActiveItem(0)
      setError(getErrorMessage(
        requestError,
        requestError.message || '批量导入中断，已完成的批次不会重复写入，请稍后重试。',
      ))
    }
  }

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-[180] flex items-center justify-center bg-zinc-950/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="bangumiImportTitle"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeSafely()
      }}
    >
      <section ref={modalRef} className="relative w-full max-w-xl border-4 border-black bg-[#fff5ee] text-black shadow-[14px_14px_0_#4ecdc4]">
        <div className="h-4 border-b-4 border-black bg-[repeating-linear-gradient(135deg,#ffe66d_0,#ffe66d_18px,#ff6b6b_18px,#ff6b6b_36px)]" />
        <button
          type="button"
          onClick={closeSafely}
          disabled={isRunning}
          className="absolute right-5 top-8 flex h-11 w-11 items-center justify-center border-[3px] border-black bg-white text-lg shadow-[4px_4px_0_#111] disabled:cursor-not-allowed disabled:opacity-40 active:translate-x-1 active:translate-y-1 active:shadow-none"
          aria-label="关闭 Bangumi 导入弹窗"
        >
          <i className="fa-solid fa-xmark" />
        </button>

        <form onSubmit={startImport} className="px-7 pb-8 pt-7">
          <span className="inline-flex -rotate-1 items-center border-2 border-black bg-[#ffe66d] px-3 py-1 text-[10px] font-black tracking-[0.18em] shadow-[3px_3px_0_#111]">
            BANGUMI COLLECTION SYNC
          </span>
          <h2 id="bangumiImportTitle" className="mt-4 pr-14 text-3xl font-black">
            从 Bangumi 一键导入
          </h2>
          <p className="mt-2 max-w-lg text-sm font-bold leading-6 text-zinc-700">
            输入你的 Bangumi UID。系统每批处理 20 部，并在批次间主动休息，避免触发接口限流（该功能需要使用代理）。
          </p>
          <p className="mt-2 max-w-lg text-xs font-bold leading-5 text-zinc-600">
            注意：第二次导入会更新已有的番剧信息，先前修改的数据会被替换。推荐首次大批量导入后，在私人空间进行个性化调整，后续想新增内容，可在智能录入模块补充
          </p>

          <label className="mt-6 block text-sm font-black" htmlFor="bangumiUid">
            Bangumi UID
          </label>
          <div className="mt-2 flex border-[3px] border-black bg-white shadow-[5px_5px_0_#ff8fab] focus-within:bg-[#fff2a8]">
            <span className="flex w-14 shrink-0 items-center justify-center border-r-[3px] border-black bg-[#4ecdc4] text-xl">
              <i className="fa-solid fa-id-card" />
            </span>
            <input
              id="bangumiUid"
              value={uid}
              onChange={(event) => setUid(event.target.value)}
              disabled={isRunning}
              maxLength={64}
              autoComplete="off"
              placeholder="例如：你的 Bangumi 数字 UID"
              className="min-w-0 flex-1 bg-transparent px-4 py-3 font-black outline-none disabled:cursor-wait"
            />
          </div>

          {(status !== 'idle' || error) && (
            <div className="mt-6 border-[3px] border-black bg-white p-4 shadow-[5px_5px_0_#111]">
              <div className="flex items-center justify-between gap-3 text-sm font-black">
                <span>
                  {status === 'checking'
                    ? '正在读取收藏总数…'
                    : status === 'importing' && activeItem > processed
                      ? `${progressLabel} · 正在处理第 ${activeItem}/${total} 部`
                      : progressLabel}
                </span>
                <span className="border-2 border-black bg-[#ffe66d] px-2 py-1">{progress}%</span>
              </div>
              <div className="mt-3 h-7 overflow-hidden border-[3px] border-black bg-[#fff5ee]">
                <div
                  className="h-full bg-[#ff4f9a] transition-[width] duration-300 ease-out"
                  style={{ width: `${progress}%` }}
                />
              </div>
              {total > 0 && (
                <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs font-black">
                  <span className="border-2 border-black bg-[#4ecdc4] px-2 py-2">新增 {created}</span>
                  <span className="border-2 border-black bg-[#ffe66d] px-2 py-2">更新 {updated}</span>
                  <span className="border-2 border-black bg-[#ff8fab] px-2 py-2">失败 {failed}</span>
                </div>
              )}
            </div>
          )}

          {error && (
            <p role="alert" className="mt-5 border-[3px] border-black bg-[#ff6b6b] px-4 py-3 text-sm font-black shadow-[4px_4px_0_#111]">
              <i className="fa-solid fa-circle-exclamation mr-2" />
              {error}
            </p>
          )}

          <div className="mt-7 grid grid-cols-2 gap-4 border-t-4 border-black pt-6">
            <button
              type="button"
              onClick={closeSafely}
              disabled={isRunning}
              className="border-[3px] border-black bg-white px-4 py-3 font-black shadow-[5px_5px_0_#111] disabled:cursor-not-allowed disabled:opacity-40 active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
            >
              {status === 'complete' ? '完成并关闭' : '取消'}
            </button>
            <button
              type="submit"
              disabled={isRunning || status === 'complete'}
              className="border-[3px] border-black bg-[#ff4f9a] px-4 py-3 font-black shadow-[5px_5px_0_#111] disabled:cursor-not-allowed disabled:bg-zinc-300 disabled:opacity-60 active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
            >
              <i className={`fa-solid ${isRunning ? 'fa-spinner animate-spin' : 'fa-bolt'} mr-2`} />
              {status === 'checking' ? '正在检查' : status === 'importing' ? '正在排队导入' : '开始导入'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}

export default BangumiImportModal
