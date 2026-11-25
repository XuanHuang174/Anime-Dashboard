import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import api from '../api/api.js'
import useSeriesSession, { seriesSession } from '../hooks/useSeriesSession.js'

const MAX_FILE_SIZE = 5 * 1024 * 1024

function getDownloadFilename(contentDisposition) {
  const match = contentDisposition?.match(/filename="?([^";]+)"?/i)
  return match?.[1] || `anime-journal-${new Date().toISOString().slice(0, 10)}.json`
}

function JournalDataTransfer({ onImported, showToast, theme = 'default', renderActions }) {
  const isMemphis = theme === 'memphis'
  const fileInputRef = useRef(null)
  const [selectedFile, setSelectedFile] = useState(null)
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [isImporting, setIsImporting] = useState(false)
  const [preview, setPreview] = useState(null), [previewError, setPreviewError] = useState(''), [isPreviewing, setIsPreviewing] = useState(false), [acknowledged, setAcknowledged] = useState(false)
  const session = useSeriesSession(), requestRef = useRef(0), importGate = useRef(false), dialogRef = useRef(null)
  useEffect(() => {
    if (!previewError) return
    const alert = dialogRef.current?.querySelector('[role="alert"]')
    alert?.scrollIntoView({ block: 'nearest' }); alert?.focus({ preventScroll: true })
  }, [previewError])
  useEffect(() => { requestRef.current++; setIsConfirmOpen(false); setSelectedFile(null); setPreview(null); setIsImporting(false); setIsPreviewing(false); importGate.current = false }, [session])
  useEffect(() => {
    if (!isImporting) return
    const warn = event => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [isImporting])

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
      if (event.key === 'Tab') {
        const nodes = [...(dialogRef.current?.querySelectorAll('button:not(:disabled),input:not(:disabled),[tabindex="0"]') || [])]
        const first = nodes[0], last = nodes.at(-1)
        if (first && event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (last && !event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }
    }
    const priorFocus = document.activeElement
    dialogRef.current?.focus()
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      document.body.style.paddingRight = previousPaddingRight
      window.removeEventListener('keydown', closeOnEscape)
      if (!isImporting) priorFocus?.focus?.()
    }
  }, [isConfirmOpen, isImporting])

  const clearFileSelection = () => {
    setSelectedFile(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const cancelImport = () => {
    if (isImporting) return
    setIsConfirmOpen(false)
    requestRef.current++
    clearFileSelection()
  }

  const handleExport = async () => {
    if (isExporting) return
    setIsExporting(true)
    const owner = seriesSession()
    try {
      const response = await api.get('export/', { responseType: 'blob' })
      if (owner !== seriesSession()) return
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

  const previewFile = async file => {
    const run = ++requestRef.current, owner = seriesSession()
    setPreview(null); setAcknowledged(false); setPreviewError(''); setIsPreviewing(true)
    const form = new FormData(); form.append('file', file)
    try {
      const { data } = await api.post('import/?preview=true', form)
      if (run === requestRef.current && owner === seriesSession()) {
        if (!data.token || !Number.isInteger(data.incoming_count)) throw new Error('恢复预览返回不完整，请重新预览。')
        setPreview(data)
      }
    } catch (error) {
      if (run === requestRef.current && owner === seriesSession()) setPreviewError(error.response?.data?.detail || error.message || '恢复预览失败，请重试；尚未导入。')
    } finally { if (run === requestRef.current) setIsPreviewing(false) }
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
    previewFile(file)
  }

  const confirmImport = async () => {
    if (!selectedFile || isImporting || importGate.current || !preview || !acknowledged) return
    importGate.current = true
    const owner = seriesSession(), run = requestRef.current
    setIsImporting(true)
    const formData = new FormData()
    formData.append('file', selectedFile)
    formData.append('confirmation_token', preview.token)

    try {
      const response = await api.post('import/?confirm=true', formData)
      if (owner !== seriesSession() || run !== requestRef.current) return
      setIsConfirmOpen(false)
      clearFileSelection()
      showToast?.(
        `导入成功，已恢复 ${response.data.imported_count ?? 0} 条手账记录。`,
        'success',
      )
      onImported?.()
    } catch (error) {
      if (owner !== seriesSession() || run !== requestRef.current) return
      const uncertain = !error.response?.status || error.response.status >= 500
      setPreview(null); setAcknowledged(false)
      const message = uncertain ? '恢复结果尚未确认，可能已经完成。请先检查手账，再重新预览；不会自动重复导入。' : error.response?.data?.detail || '恢复被拒绝，请重新预览并核对文件。'
      setPreviewError(typeof message === 'string' ? message : JSON.stringify(message))
      showToast?.(typeof message === 'string' ? message : '恢复被拒绝，请核对文件。', 'error')
      if (uncertain) onImported?.()
    } finally {
      if (owner === seriesSession() && run === requestRef.current) { setIsImporting(false); importGate.current = false }
    }
  }

  return (
    <>
      {renderActions ? renderActions({ onExport: handleExport, onImport: () => fileInputRef.current?.click(), isExporting, isImporting }) : <div className="flex items-center gap-2">
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
      </div>}
        <input
          ref={fileInputRef}
          type="file"
          accept=".json,application/json"
          onChange={handleFileChange}
          className="hidden"
        />

      {isConfirmOpen && createPortal(
        <div
          className={`fixed inset-0 z-[100] flex items-center justify-center p-4 backdrop-blur-sm animate-[viewFadeIn_0.2s_ease-out_both] ${isMemphis ? 'memphis-modal-backdrop bg-zinc-900/80' : 'bg-slate-950/65'}`}
          style={isMemphis ? { '--memphis-ink': '#111', '--memphis-coral': '#ff6b6b', '--memphis-teal': '#4ecdc4', '--memphis-yellow': '#ffe66d' } : undefined}
          role="dialog"
          aria-modal="true"
          aria-labelledby="importConfirmTitle"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) cancelImport()
          }}
        >
          <section
            ref={dialogRef}
            tabIndex={-1}
            className={isMemphis
              ? 'max-h-[90dvh] w-full max-w-lg overflow-y-auto border-4 border-black bg-[#fff5ee] text-black shadow-[10px_10px_0_#111]'
              : 'max-h-[90dvh] w-full max-w-lg overflow-y-auto rounded-[2rem] border border-white/70 bg-white/95 text-slate-700 shadow-2xl shadow-slate-950/30 ring-1 ring-slate-900/5'}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className={isMemphis ? 'h-3 border-b-4 border-black bg-[#ff6b6b]' : 'h-2 bg-gradient-to-r from-rose-400 via-pink-500 to-indigo-500'} />
            <div className="relative px-7 py-8 text-center">
              {!isMemphis && <div className="pointer-events-none absolute -right-10 -top-10 h-36 w-36 rounded-full bg-rose-100/70 blur-2xl" />}
              <span className={`relative mx-auto flex h-16 w-16 items-center justify-center text-2xl ${isMemphis ? 'border-[3px] border-black bg-[#ffe66d] text-black shadow-[5px_5px_0_#111]' : 'rounded-2xl border border-rose-100 bg-gradient-to-br from-rose-50 to-pink-100 text-rose-500 shadow-inner'}`}>
                <i className="fa-solid fa-triangle-exclamation" />
              </span>
              <h2 id="importConfirmTitle" className={`relative mt-5 text-2xl font-black ${isMemphis ? 'text-black' : 'text-slate-800'}`}>
                确认 JSON 恢复影响
              </h2>
              <p className={`relative mt-3 text-sm leading-7 ${isMemphis ? 'font-bold text-zinc-700' : 'font-medium text-slate-500'}`}>
                本次整体替换手账。文件内的评分展示配置一并恢复，旧文件未包含配置时保留当前设置。请先核对观看历程的处理方式。
              </p>
              {isPreviewing && <p role="status" className="mt-4">正在读取文件并核对当前手账，尚未导入…</p>}
              {previewError && <p role="alert" tabIndex={-1} className="mt-4 break-words text-left text-sm font-bold text-red-700">{previewError}</p>}
              {preview && <div className="mt-4 border-2 border-black bg-white p-3 text-left text-sm leading-7">
                <p>恢复 {preview.incoming_count} 部作品；其中 {preview.with_history} 部包含完整历程（{preview.rounds_in_file} 个轮次、{preview.events_in_file} 条事件）。</p>
                <p>替换已有历程：{preview.replaced_history} 部；旧格式匹配并保留历程：{preview.preserved_history} 部；其余日期未知起点：{preview.new_baselines} 部。</p>
                <p className="font-black text-red-700">文件未包含的 {preview.removed_count} 部作品及其历程将移除，其中有 {preview.removed_history_events} 条事件。</p>
                {!!preview.removed_titles?.length && <details><summary>查看将移除的作品（最多 20 部）</summary><ul>{preview.removed_titles.map((title, index) => <li key={index}>{title}</li>)}</ul></details>}
                <p>完整历程按文件恢复，可能恢复曾清空的随记。旧文件仅在作品准确匹配时保留原历程；不能匹配的作品不猜测合并。不会用导入日期补作观看日期。</p>
              </div>}
              <div className="mt-4 text-left text-sm"><button type="button" disabled={isExporting || isImporting} onClick={handleExport} className="border-2 border-black bg-white px-3 py-2 font-bold">{isExporting ? '正在导出…' : '先导出当前 JSON 备份'}</button><label className="mt-3 flex items-start gap-2"><input type="checkbox" checked={acknowledged} disabled={!preview || isImporting} onChange={event => setAcknowledged(event.target.checked)} /><span>我已核对影响，了解整体替换无法通过历程撤销恢复。</span></label></div>
              {!preview && !isPreviewing && <button type="button" disabled={isImporting} className="mt-3 border-2 border-black bg-white p-2 font-bold" onClick={() => previewFile(selectedFile)}>重新预览恢复影响</button>}
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
                  disabled={isImporting || !preview || !acknowledged || isPreviewing}
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
      , document.body)}
    </>
  )
}

export default JournalDataTransfer
