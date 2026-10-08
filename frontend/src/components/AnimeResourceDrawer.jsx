import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'
import { cleanResourceKeyword, dmhySearchUrl, hasResourceSearchSession, isSafeMagnet } from '../utils/resourceSearch.js'
import './AnimeResourceDrawer.css'

export default function AnimeResourceDrawer({ open, title, onClose, showToast }) {
  const [keyword, setKeyword] = useState('')
  const [searchedKeyword, setSearchedKeyword] = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [needsLogin, setNeedsLogin] = useState(false)
  const [copyFallback, setCopyFallback] = useState('')
  const [copiedMagnet, setCopiedMagnet] = useState('')
  const drawerRef = useRef(null)
  const inputRef = useRef(null)
  const requestRef = useRef(null)
  const sequenceRef = useRef(0)

  async function search(value) {
    requestRef.current?.abort()
    const sequence = ++sequenceRef.current
    const trimmed = value.trim()
    setSearchedKeyword(trimmed)
    setCopyFallback('')
    setCopiedMagnet('')
    setResults([])
    setError('')
    setLoading(false)
    if (!trimmed) {
      setError('请输入搜索关键词。')
      return
    }
    if (!hasResourceSearchSession()) {
      setNeedsLogin(true)
      setError('请先登录手账账号，再使用资源搜索。')
      return
    }
    setNeedsLogin(false)
    setLoading(true)
    const controller = new AbortController()
    requestRef.current = controller
    try {
      const response = await api.get('utils/anime-search/', {
        params: { keyword: trimmed }, signal: controller.signal, timeout: 15000,
      })
      if (sequence !== sequenceRef.current) return
      if (response.data.status !== 'success' || !Array.isArray(response.data.results)) {
        throw new Error('资源搜索返回的数据异常，请稍后再试。')
      }
      setResults(response.data.results)
    } catch (requestError) {
      if (controller.signal.aborted || sequence !== sequenceRef.current) return
      setError(requestError.response?.data?.message
        || (requestError.code === 'ECONNABORTED'
          ? '数据源站点目前开启了防御或正在唤醒，请稍后再试'
          : '资源搜索暂时不可用，请稍后再试或使用网页搜索。'))
    } finally {
      if (sequence === sequenceRef.current) setLoading(false)
    }
  }

  useEffect(() => {
    if (!open) return undefined
    const initialKeyword = cleanResourceKeyword(title)
    setKeyword(initialKeyword)
    search(initialKeyword)
    // Focus after the opening frame, once the formerly hidden drawer is visible.
    const focusFrame = requestAnimationFrame(() => inputRef.current?.focus({ preventScroll: true }))
    return () => {
      cancelAnimationFrame(focusFrame)
      ++sequenceRef.current
      requestRef.current?.abort()
    }
  }, [open, title])

  async function copyMagnet(magnet) {
    if (!isSafeMagnet(magnet)) return
    try {
      await navigator.clipboard.writeText(magnet)
      setCopiedMagnet(magnet)
      showToast?.('Magnet 链接已复制。', 'success')
      setCopyFallback('')
    } catch {
      // Clipboard access may be disabled: offer a selectable manual fallback.
      setCopyFallback(magnet)
    }
  }

  function keepFocusInDrawer(event) {
    if (event.key !== 'Tab') return
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
    <aside ref={drawerRef} id="animeResourceDrawer" className={`anime-resource-drawer${open ? ' is-open' : ''}`}
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
          search(keyword)
        }}>
          <label htmlFor="resourceSearchKeyword">搜索关键词</label>
          <div>
            <input ref={inputRef} id="resourceSearchKeyword" value={keyword} maxLength={200}
              onChange={(event) => setKeyword(event.target.value)} placeholder="试试核心番剧名…" />
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
                      onClick={() => copyMagnet(result.magnet)}>{copiedMagnet === result.magnet ? '已复制 ✓' : '复制链接'}</button>
                    <button type="button" className="resource-button resource-button--pink"
                      onClick={() => { window.location.href = result.magnet }}>🧲 打开 Magnet</button>
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
        <p className="resource-hint">打开 Magnet 需安装 BT 客户端，请遵守版权及当地法规。</p>
      </div>
    </aside>
  )
}
