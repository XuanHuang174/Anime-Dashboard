import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import api from '../api/api.js'
import { dmhySearchUrl, hasResourceSearchSession, isSafeMagnet } from '../utils/resourceSearch.js'
import { createResourceSearchSession } from '../utils/resourceSearchSession.js'
import './AnimeResourceDrawer.css'

export default function AnimeResourceDrawer({ open, title = '', autoSearch = true, id = 'animeResourceDrawer', onClose, showToast,
  sessionKey, trapFocus = true, client = api }) {
  const latestToast = useRef(showToast)
  latestToast.current = showToast
  const session = useMemo(() => createResourceSearchSession(client, {
    hasSession: hasResourceSearchSession,
    copyText: value => navigator.clipboard.writeText(value),
    onCopied: () => latestToast.current?.('Magnet 链接已复制。', 'success'),
  }), [client])
  const { keyword, searchedKeyword, results, loading, error, needsLogin, copyFallback, copiedMagnet } =
    useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot)
  const drawerRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    session.open(title, autoSearch)
    // Focus after the opening frame, once the formerly hidden drawer is visible.
    const focusFrame = requestAnimationFrame(() => inputRef.current?.focus({ preventScroll: true }))
    return () => {
      cancelAnimationFrame(focusFrame)
      session.close()
    }
  }, [open, title, autoSearch, sessionKey, session])

  function keepFocusInDrawer(event) {
    if (!trapFocus || event.key !== 'Tab') return
    const focusable = [...drawerRef.current.querySelectorAll('button:not(:disabled), input, a[href]')]
    const first = focusable[0]
    const last = focusable.at(-1)
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last?.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first?.focus()
    }
  }

  const fallbackKeyword = keyword.trim() || searchedKeyword
  return (
    <aside ref={drawerRef} id={id} className={`anime-resource-drawer${open ? ' is-open' : ''}`}
      aria-label="番剧资源搜索" aria-hidden={!open} inert={!open}
      onKeyDown={keepFocusInDrawer}>
      <div className="anime-resource-drawer__header">
        <div className="anime-resource-drawer__heading">
          <div><span>RESOURCE HUNT</span><h4>🧲 寻找资源</h4></div>
          <button type="button" className="resource-button resource-button--pink"
            aria-label="关闭资源抽屉" onClick={onClose}>✕</button>
        </div>
        <form className="anime-resource-drawer__search" onSubmit={(event) => {
          event.preventDefault()
          session.search(keyword)
        }}>
          <label htmlFor={`${id}-keyword`}>搜索关键词</label>
          <div>
            <input ref={inputRef} id={`${id}-keyword`} value={keyword} maxLength={200}
              onChange={(event) => session.setKeyword(event.target.value)} placeholder="输入番剧名称…快速寻找 Magnet 资源" />
            <button type="submit" className="resource-button">搜索</button>
          </div>
        </form>
      </div>
      <div className="anime-resource-drawer__results custom-scrollbar" aria-busy={loading}>
        {loading ? (
          <div role="status" aria-label="正在搜索资源">
            <p className="resource-hint">正在搜寻，首次唤醒可能稍慢…</p>
            {[0, 1, 2].map((index) => <div key={index} className="resource-skeleton" aria-hidden="true">
              <span /><span /><span />
            </div>)}
          </div>
        ) : !searchedKeyword && !error ? (
          <div className="resource-empty" role="status"><span className="resource-empty__icon">🧲</span>
            <p>输入番剧名称，快速寻找 Magnet 资源。按回车或点击搜索开始。</p></div>
        ) : error || results.length === 0 ? (
          <div className="resource-empty" role="status">
            <span className="resource-empty__icon">{error ? '!' : '?'}</span>
            <p>{error || '未找到相关资源，请尝试缩短或更换搜索关键词'}</p>
            {needsLogin && <a className="resource-button" href="/login">登录手账账号</a>}
            <a className="resource-button resource-button--teal" href={dmhySearchUrl(fallbackKeyword)}
              target="_blank" rel="noopener noreferrer">🌐 在动漫花园网页端搜索</a>
          </div>
        ) : (
          <>
            <p className="resource-hint" role="status">找到 {results.length} 条资源 · {searchedKeyword}</p>
            {results.map((result, index) => (
              <article className="resource-card" key={`${result.magnet}-${index}`}>
                <span className="resource-card__number">#{String(index + 1).padStart(2, '0')}</span>
                <h5>{result.title}</h5>
                <dl>
                  <div><dt>发布时间</dt><dd>{result.published_at || '未知'}</dd></div>
                  <div><dt>文件大小</dt><dd>{result.size || '未知'}</dd></div>
                  <div><dt>发布者</dt><dd>{result.publisher || '未知'}</dd></div>
                </dl>
                <div className="resource-card__actions">
                  {isSafeMagnet(result.magnet) ? <>
                    <button type="button" className="resource-button resource-button--small"
                      onClick={() => session.copyMagnet(result.magnet)}>{copiedMagnet === result.magnet ? '已复制 ✓' : '复制链接'}</button>
                    <button type="button" className="resource-button resource-button--pink"
                      onClick={() => { if (session.canUseMagnet(result.magnet)) window.location.href = result.magnet }}>🧲 打开 Magnet</button>
                  </> : <span className="resource-hint">该条目暂无有效 Magnet 链接</span>}
                </div>
              </article>
            ))}
            <a className="resource-button resource-button--teal" href={dmhySearchUrl(fallbackKeyword)}
              target="_blank" rel="noopener noreferrer">🌐 在动漫花园网页端搜索</a>
          </>
        )}
        {copyFallback && <label className="resource-copy-fallback">无法自动复制，请选中链接手动复制：
          <input aria-label="手动复制 Magnet 链接" readOnly value={copyFallback}
            onFocus={(event) => event.target.select()} />
        </label>}
        <p className="resource-hint">打开 Magnet 需自行安装 BT 客户端。本站仅为数据索引与信息交流平台，所有资源均非本站上传或存储。请用户严格遵守当地法律法规与版权条例，下载内容仅限学习交流使用，请于下载后 24 小时内删除。一切资源真实性与版权归属概与本站无关，使用者须自行承担相关法律责任。</p>
      </div>
    </aside>
  )
}
