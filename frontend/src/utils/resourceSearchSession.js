import { cleanResourceKeyword, isSafeMagnet } from './resourceSearch.js'

const empty = () => ({ keyword: '', searchedKeyword: '', results: [], loading: false, error: '',
  needsLogin: false, copyFallback: '', copiedMagnet: '' })

// Extracted from AnimeResourceDrawer: the SAME proxy and behavior, one instance
// per mounted drawer. No result cache, user/profile access, or direct upstream URL.
export function createResourceSearchSession(client, { hasSession, copyText, onCopied = () => {} }) {
  let state = empty(), active = false, epoch = 0, copySequence = 0, request
  const listeners = new Set()
  const emit = changes => { state = { ...state, ...changes }; for (const listener of listeners) listener() }
  const invalidate = () => { epoch++; copySequence++; request?.abort(); request = null }
  const canUseMagnet = magnet => active && isSafeMagnet(magnet) && state.results.some(row => row.magnet === magnet)
  async function search(value) {
    if (!active) return
    invalidate()
    const currentEpoch = epoch, trimmed = typeof value === 'string' ? value.trim() : ''
    emit({ searchedKeyword: trimmed, results: [], loading: false, error: '', needsLogin: false, copyFallback: '', copiedMagnet: '' })
    if (!trimmed || trimmed.length > 200) { emit({ error: '请输入 1 到 200 字的搜索关键词。' }); return }
    let loggedIn = false
    try { loggedIn = hasSession() } catch { /* storage may be unavailable */ }
    if (!loggedIn) { emit({ needsLogin: true, error: '请先登录手账账号，再使用资源搜索。' }); return }
    const controller = new AbortController()
    request = controller
    const current = () => active && currentEpoch === epoch && !controller.signal.aborted
    emit({ loading: true })
    try {
      const response = await client.get('utils/anime-search/', {
        params: { keyword: trimmed }, signal: controller.signal, timeout: 15000,
      })
      if (!current()) return
      const rows = response.data?.results
      if (response.data?.status !== 'success' || !Array.isArray(rows) || rows.length > 100
        || !rows.every(row => row && typeof row.title === 'string' && row.title.trim()
          && ['magnet', 'size', 'published_at', 'publisher'].every(key => row[key] == null || typeof row[key] === 'string'))) {
        throw new Error('资源搜索返回的数据异常，请稍后再试。')
      }
      emit({ results: rows })
    } catch (error) {
      if (!current()) return
      emit({ error: typeof error.response?.data?.message === 'string' ? error.response.data.message
        : error.code === 'ECONNABORTED' ? '数据源站点目前开启了防御或正在唤醒，请稍后再试'
          : '资源搜索暂时不可用，请稍后再试或使用网页搜索。' })
    } finally { if (current()) emit({ loading: false }) }
  }
  return {
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) },
    getSnapshot: () => state,
    open(title, autoSearch = true) {
      invalidate(); active = true
      state = empty(); emit({ keyword: cleanResourceKeyword(title) })
      const openedEpoch = epoch, keyword = state.keyword
      // StrictMode setup/cleanup/setup must not dispatch two automatic requests.
      if (autoSearch && keyword) return Promise.resolve().then(() => {
        if (active && epoch === openedEpoch) return search(keyword)
      })
    },
    setKeyword(keyword) {
      if (!active) return
      invalidate()
      state = empty(); emit({ keyword }) // manual editing never starts a request
    },
    search,
    async copyMagnet(magnet) {
      if (!canUseMagnet(magnet)) return
      const currentEpoch = epoch, sequence = ++copySequence
      const current = () => active && epoch === currentEpoch && sequence === copySequence
      emit({ copyFallback: '', copiedMagnet: '' })
      try {
        await copyText(magnet)
        if (!current()) return
        emit({ copiedMagnet: magnet })
        try { onCopied() } catch { /* a toast failure cannot undo a completed copy */ }
      } catch {
        if (current()) emit({ copyFallback: magnet })
      }
    },
    canUseMagnet,
    close() { invalidate(); active = false; state = empty(); emit({}) },
  }
}
