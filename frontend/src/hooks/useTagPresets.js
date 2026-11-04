import { useEffect, useRef, useState } from 'react'
import api from '../api/api.js'

export default function useTagPresets(active) {
  const [tags, setTags] = useState([]), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const alive = useRef(false), pending = useRef(false)
  const generation = useRef(0)
  useEffect(() => {
    const order = ++generation.current
    pending.current = false; setBusy(false)
    alive.current = Boolean(active)
    if (!active) { setTags([]); return }
    const controller = new AbortController()
    setError('')
    api.get('tag-presets/', { signal: controller.signal }).then(({ data }) => {
      if (!controller.signal.aborted && order === generation.current) setTags(data.tags)
    }).catch(() => { if (!controller.signal.aborted && order === generation.current) setError('常用标签读取失败，可重新打开窗口重试。') })
    return () => { alive.current = false; generation.current++; controller.abort() }
  }, [active])
  const change = async (tag, remove = false) => {
    if (pending.current) return false
    const order = ++generation.current
    pending.current = true; setBusy(true); setError('')
    try {
      const { data } = remove ? await api.delete('tag-presets/', { data: { tag } }) : await api.post('tag-presets/', { tag })
      if (!alive.current || order !== generation.current) return false
      setTags(data.tags); return true
    } catch (error) {
      if (alive.current && order === generation.current) setError(error.response?.data?.tag?.[0] || '常用标签保存失败，请重试。')
      return false
    } finally { if (order === generation.current) { pending.current = false; if (alive.current) setBusy(false) } }
  }
  return { tags, error, busy, change }
}
