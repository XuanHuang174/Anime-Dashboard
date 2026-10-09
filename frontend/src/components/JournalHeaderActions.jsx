import { useEffect, useId, useState } from 'react'
import MemphisDropdown from './MemphisDropdown.jsx'
import JournalDataTransfer from './JournalDataTransfer.jsx'
import PublicJournalShareButton from './PublicJournalShareButton.jsx'

export default function JournalHeaderActions({ isPreviewMode, onReturnHome, onTogglePreview, onImportBangumi,
  onRecordsImported, onFindResources, onExportCsv, csvDisabled, csvCount, publicStatus, onStatusChange, showToast }) {
  const [mobile, setMobile] = useState(() => window.innerWidth < 768)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [activeMenu, setActiveMenu] = useState('')
  const owner = useId()
  useEffect(() => {
    const query = window.matchMedia('(max-width: 767px)')
    const update = () => { setMobile(query.matches); setMobileOpen(false); setActiveMenu('') }
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  useEffect(() => { setMobileOpen(false); setActiveMenu('') }, [isPreviewMode])
  const close = () => { setActiveMenu(''); setMobileOpen(false) }
  const run = (action) => () => { close(); action?.() }
  const csvButton = <button type="button" className="journal-menu-item journal-menu-item--yellow"
    disabled={csvDisabled} onClick={run(onExportCsv)}>📊 导出当前数据 (CSV)<small>当前筛选列表 · {csvCount} 条</small></button>
  return <JournalDataTransfer theme="memphis" onImported={onRecordsImported} showToast={showToast}
    renderActions={({ onExport, onImport, isExporting, isImporting }) => (
      <PublicJournalShareButton publicStatus={publicStatus} onStatusChange={onStatusChange} showToast={showToast}
        renderTrigger={(shareButton) => {
          const actions = <div className="journal-toolbar__items">
            <button type="button" className="dashboard-arcade-button dashboard-return-home-cta bg-white"
              onClick={run(onReturnHome)}>← 返回主界面</button>
            {!isPreviewMode && <MemphisDropdown label="📦 数据管理" className="bg-[#ffe66d]"
              open={activeMenu === 'data'} onOpenChange={(value) => setActiveMenu(value ? 'data' : '')} owner={owner}>
              <button type="button" className="journal-menu-item" disabled={isExporting || isImporting}
                onClick={run(onExport)}>{isExporting ? '正在导出…' : '📤 导出数据 (JSON)'}</button>
              <button type="button" className="journal-menu-item journal-menu-item--pink" disabled={isExporting || isImporting}
                onClick={run(onImport)}>📥 导入模板 (JSON)</button>
              <button type="button" className="journal-menu-item" onClick={run(onImportBangumi)}>☁ Bangumi 导入</button>
              {csvButton}
            </MemphisDropdown>}
            <button type="button" className="dashboard-arcade-button bg-[#4ecdc4]"
              onClick={run(onFindResources)}>🧲 寻找资源</button>
            {isPreviewMode && <MemphisDropdown label="🛠 更多工具" className="bg-[#ff8fab]"
              open={activeMenu === 'tools'} onOpenChange={(value) => setActiveMenu(value ? 'tools' : '')} owner={owner}>
              {csvButton}<div onClick={close}>{shareButton}</div>
            </MemphisDropdown>}
            <button type="button" className={`dashboard-arcade-button ${isPreviewMode ? 'dashboard-edit-mode-cta bg-[#ffe66d]' : 'bg-white'}`}
              onClick={run(onTogglePreview)}>{isPreviewMode ? '📝 编辑模式' : '👁 预览模式'}</button>
            {!isPreviewMode && <div onClick={close}>{shareButton}</div>}
          </div>
          return <div className="journal-toolbar">
            {mobile ? <MemphisDropdown label="☰ 菜单" className="bg-[#ffe66d]" owner={owner}
              open={mobileOpen} onOpenChange={(value) => { setMobileOpen(value); if (!value) setActiveMenu('') }}>{actions}</MemphisDropdown> : actions}
          </div>
        }} />
    )} />
}
