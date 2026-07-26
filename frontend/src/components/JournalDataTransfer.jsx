import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'

const MAX_FILE_SIZE = 5 * 1024 * 1024

function getDownloadFilename(contentDisposition) {
  const match = contentDisposition?.match(/filename="?([^";]+)"?/i)
  return match?.[1] || `anime-journal-${new Date().toISOString().slice(0, 10)}.json`
}

function JournalDataTransfer({ onImported, showToast, theme = 'default' }) {
  const isMemphis = theme === 'memphis'
  const fileInputRef = useRef(null)
  const [selectedFile, setSelectedFile] = useState(null)
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [isImporting, setIsImporting] = useState(false)

  useEffect(() => {
    if (!isConfirmOpen) return undefined

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

    const closeOnEscape = (event) => {
      if (event.key === 'Escape' && !isImporting) cancelImport()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [isConfirmOpen, isImporting])

  const clearFileSelection = () => {
    setSelectedFile(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const cancelImport = () => {
    if (isImporting) return
    setIsConfirmOpen(false)
    clearFileSelection()
  }

  const handleExport = async () => {
    if (isExporting) return
    setIsExporting(true)
    try {
      const response = await api.get('export/', { responseType: 'blob' })
      const blobUrl = window.URL.createObjectURL(response.data)
      const downloadLink = document.createElement('a')
      downloadLink.href = blobUrl
      downloadLink.download = getDownloadFilename(
        response.headers['content-disposition'],
      )
      document.body.appendChild(downloadLink)
      downloadLink.click()
      downloadLink.remove()
      window.URL.revokeObjectURL(blobUrl)
      showToast?.('手账 JSON 数据已导出。', 'success')
    } catch {
      showToast?.('数据导出失败，请稍后重试。', 'error')
    } finally {
      setIsExporting(false)
    }
  }

  const handleFileChange = (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.json')) {
      clearFileSelection()
      showToast?.('请选择扩展名为 .json 的手账文件。', 'error')
      return
    }
    if (file.size > MAX_FILE_SIZE) {
      clearFileSelection()
      showToast?.('JSON 文件不能超过 5MB。', 'error')
      return
    }
    setSelectedFile(file)
    setIsConfirmOpen(true)
  }

  const confirmImport = async () => {
    if (!selectedFile || isImporting) return
    setIsImporting(true)
    const formData = new FormData()
    formData.append('file', selectedFile)

    try {
      const response = await api.post('import/', formData)
      setIsConfirmOpen(false)
      clearFileSelection()
      showToast?.(
        `导入成功，已恢复 ${response.data.imported_count ?? 0} 条手账记录。`,
        'success',
      )
      onImported?.()
    } catch (error) {
      const message = error.response?.data?.detail
        || '数据导入失败，原有手账数据未发生改变。'
      showToast?.(message, 'error')
    } finally {
      setIsImporting(false)
    }
  }

  return (
    <>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleExport}
          disabled={isExporting || isImporting}
          title="导出 JSON 数据"
          className={isMemphis
            ? 'dashboard-arcade-button inline-flex items-center gap-2 bg-[#ffe66d]'
            : 'inline-flex items-center gap-2 rounded-xl border border-white/35 bg-white/15 px-3 py-2.5 text-sm font-black text-white shadow-md backdrop-blur-sm transition-all hover:-translate-y-0.5 hover:bg-white/25 hover:shadow-lg active:translate-y-0 active:scale-95 disabled:cursor-wait disabled:opacity-60'}
        >
          <i className={`fa-solid ${isExporting ? 'fa-spinner animate-spin' : 'fa-file-export'}`} />
          <span className={isMemphis ? '' : 'hidden xl:inline'}>{isExporting ? '导出中' : '导出数据'}</span>
        </button>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={isExporting || isImporting}
          title="导入 JSON 模板"
          className={isMemphis
            ? 'dashboard-arcade-button inline-flex items-center gap-2 bg-[#ff6b6b]'
            : 'inline-flex items-center gap-2 rounded-xl border border-white/35 bg-white/15 px-3 py-2.5 text-sm font-black text-white shadow-md backdrop-blur-sm transition-all hover:-translate-y-0.5 hover:bg-white/25 hover:shadow-lg active:translate-y-0 active:scale-95 disabled:cursor-wait disabled:opacity-60'}
        >
          <i className="fa-solid fa-file-import" />
          <span className={isMemphis ? '' : 'hidden xl:inline'}>导入模板</span>
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".json,application/json"
          onChange={handleFileChange}
          className="hidden"
        />
      </div>

      {isConfirmOpen && (
        <div
          className={`fixed inset-0 z-[100] flex items-center justify-center p-4 backdrop-blur-sm animate-[viewFadeIn_0.2s_ease-out_both] ${isMemphis ? 'memphis-modal-backdrop bg-zinc-900/80' : 'bg-slate-950/65'}`}
          role="dialog"
          aria-modal="true"
          aria-labelledby="importConfirmTitle"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) cancelImport()
          }}
        >
          <section
            className={isMemphis
              ? 'w-full max-w-md overflow-hidden border-4 border-black bg-[#fff5ee] text-black shadow-[10px_10px_0_#111]'
              : 'w-full max-w-md overflow-hidden rounded-[2rem] border border-white/70 bg-white/95 text-slate-700 shadow-2xl shadow-slate-950/30 ring-1 ring-slate-900/5'}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className={isMemphis ? 'h-3 border-b-4 border-black bg-[#ff6b6b]' : 'h-2 bg-gradient-to-r from-rose-400 via-pink-500 to-indigo-500'} />
            <div className="relative px-7 py-8 text-center">
              {!isMemphis && <div className="pointer-events-none absolute -right-10 -top-10 h-36 w-36 rounded-full bg-rose-100/70 blur-2xl" />}
              <span className={`relative mx-auto flex h-16 w-16 items-center justify-center text-2xl ${isMemphis ? 'border-[3px] border-black bg-[#ffe66d] text-black shadow-[5px_5px_0_#111]' : 'rounded-2xl border border-rose-100 bg-gradient-to-br from-rose-50 to-pink-100 text-rose-500 shadow-inner'}`}>
                <i className="fa-solid fa-triangle-exclamation" />
              </span>
              <h2 id="importConfirmTitle" className={`relative mt-5 text-2xl font-black ${isMemphis ? 'text-black' : 'text-slate-800'}`}>
                危险操作确认
              </h2>
              <p className={`relative mt-3 text-sm leading-7 ${isMemphis ? 'font-bold text-zinc-700' : 'font-medium text-slate-500'}`}>
                导入新的 JSON 模板将会彻底覆盖您当前所有的手账数据，此操作无法撤销。是否继续？
              </p>
              <div className={`relative mt-4 flex items-center gap-3 px-4 py-3 text-left ${isMemphis ? 'border-[3px] border-black bg-[#4ecdc4] shadow-[4px_4px_0_#111]' : 'rounded-2xl border border-indigo-100 bg-indigo-50/80 shadow-inner'}`}>
                <span className={`flex h-10 w-10 shrink-0 items-center justify-center ${isMemphis ? 'border-2 border-black bg-[#ff8fab] text-black shadow-[3px_3px_0_#111]' : 'rounded-xl bg-white text-indigo-500 shadow-sm'}`}>
                  <i className="fa-solid fa-file-code" />
                </span>
                <span className="min-w-0">
                  <span className={`inline-block text-[10px] font-black uppercase tracking-wider ${isMemphis ? 'border-2 border-black bg-[#ffe66d] px-2 py-0.5 text-black' : 'text-indigo-400'}`}>即将导入</span>
                  <span className={`mt-1 block truncate text-sm font-black ${isMemphis ? 'text-black' : 'text-slate-700'}`}>{selectedFile?.name}</span>
                </span>
              </div>
              <div className="relative mt-7 grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={cancelImport}
                  disabled={isImporting}
                  className={isMemphis
                    ? 'dashboard-arcade-button bg-white disabled:opacity-50'
                    : 'rounded-xl bg-slate-100 px-4 py-3 text-sm font-black text-slate-600 shadow-inner transition hover:bg-slate-200 active:scale-95 disabled:opacity-50'}
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={confirmImport}
                  disabled={isImporting}
                  className={isMemphis
                    ? 'dashboard-arcade-button bg-[#ff6b6b] disabled:cursor-wait disabled:opacity-70'
                    : 'rounded-xl bg-gradient-to-r from-rose-500 to-pink-500 px-4 py-3 text-sm font-black text-white shadow-lg shadow-rose-200 transition hover:-translate-y-0.5 hover:shadow-xl active:translate-y-0 active:scale-95 disabled:cursor-wait disabled:opacity-70'}
                >
                  <i className={`fa-solid mr-2 ${isImporting ? 'fa-spinner animate-spin' : 'fa-file-import'}`} />
                  {isImporting ? '正在覆盖' : '确认覆盖'}
                </button>
              </div>
            </div>
          </section>
        </div>
      )}
    </>
  )
}

export default JournalDataTransfer
