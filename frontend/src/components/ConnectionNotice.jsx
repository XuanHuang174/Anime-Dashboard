import { useEffect, useRef, useSyncExternalStore } from 'react'
import { connectionRecovery } from '../api/connectionRecovery.js'
import api from '../api/api.js'
import { getAccessToken } from '../auth/tokenStorage.js'

export default function ConnectionNotice() {
  const state = useSyncExternalStore(connectionRecovery.subscribe, connectionRecovery.getSnapshot, connectionRecovery.getSnapshot)
  const pending = useRef(null)
  const reconnect = (reason = 'manual') => {
    if (pending.current || !connectionRecovery.recover(reason)) return
    const request = new AbortController()
    pending.current = request
    // A small read also supports recovery on login/forms with no failed list mounted.
    api.get(getAccessToken() ? 'user/profile/' : 'showcase-profile/', { signal: request.signal, connectionRetry: false })
      .catch(() => {})
      .finally(() => { if (pending.current === request) pending.current = null })
  }
  useEffect(() => {
    const wake = () => { if (document.visibilityState === 'visible') reconnect('network') }
    window.addEventListener('online', wake)
    window.addEventListener('focus', wake)
    document.addEventListener('visibilitychange', wake)
    return () => { window.removeEventListener('online', wake); window.removeEventListener('focus', wake); document.removeEventListener('visibilitychange', wake); pending.current?.abort() }
  }, [])
  if (state.phase === 'idle') return null
  return <aside role="status" aria-live="polite" className="connection-notice fixed top-4 left-4 right-4 z-[200] mx-auto max-h-[40dvh] max-w-3xl overflow-y-auto border-[3px] border-black bg-[#fff2a8] p-3 text-black shadow-[5px_5px_0_#111] sm:p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0 flex-1"><p className="font-black">{state.phase === 'retrying' ? '数据服务连接中断，正在尝试恢复…' : '暂时无法连接数据服务'}</p>
        <p className="mt-1 text-xs leading-relaxed">已读取的内容和当前输入会保留。请确认当前网络可以访问数据服务；切换代理后可重新连接。</p></div>
      <button type="button" onClick={() => reconnect()} className="shrink-0 border-[3px] border-black bg-white px-3 py-2 text-sm font-black shadow-[3px_3px_0_#111] hover:bg-[#4ecdc4]">重新连接</button>
    </div>
    <details className="mt-2 text-xs"><summary className="cursor-pointer">连接信息</summary><p className="mt-1 break-words">数据服务：{state.host || '当前站点'} · 错误：{state.code}。浏览器可能因 DNS、网络、证书或跨域问题阻止连接，具体原因可在开发者工具中查看。</p></details>
  </aside>
}
