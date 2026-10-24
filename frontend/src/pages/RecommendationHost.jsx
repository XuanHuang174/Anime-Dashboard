import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import SimilarRecommendations from './SimilarRecommendations.jsx'
import TagRecommendations from './TagRecommendations.jsx'
import useExplorationTopics from '../hooks/useExplorationTopics.js'
import { ExplorationTopicsContext } from '../components/recommendations/ExplorationTagChips.jsx'
import RecommendationModeNavigation from '../components/recommendations/RecommendationModeNavigation.jsx'
import { validateExplorationCapabilities, parseSimilarSeed } from '../utils/recommendationExploration.js'
import '../styles/recommendations-exploration.css'

export default function RecommendationHost({ client: baseClient, sessionScope, Personal }) {
  const location = useLocation()
  const mode = location.pathname === '/recommendations' || location.pathname === '/recommendations/' ? 'personal'
    : location.pathname === '/recommendations/similar' ? 'similar' : 'tags'
  const caches = useRef({ personal: {}, similar: {}, tags: {} })
  const [adult, setAdult] = useState(false)
  const [notice, setNotice] = useState('')
  const [capability, setCapability] = useState({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const topics = useExplorationTopics(baseClient, capability.data?.navigation?.[2]?.available === true)
  const client = useMemo(() => baseClient.forScope ? baseClient.forScope(adult ? 'include_adult' : 'safe', () => {
    setAdult(false); setNotice('访问授权或服务状态已变化，已恢复安全范围。')
  }) : baseClient, [baseClient, adult])
  useEffect(() => {
    const request = new AbortController()
    setCapability({ status: 'loading' })
    if (!baseClient.capabilities) { setCapability({ status: 'unavailable' }); return () => request.abort() }
    baseClient.capabilities(request.signal).then(data => {
      if (!request.signal.aborted) setCapability({ status: 'ready', data: validateExplorationCapabilities(data.exploration) })
    }).catch(() => { if (!request.signal.aborted) setCapability({ status: 'error' }) })
    return () => request.abort()
  }, [baseClient, attempt])
  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(''), 6000)
    return () => window.clearTimeout(timer)
  }, [notice])
  // React-owned cache survives mode navigation, never a refresh/account change.
  useLayoutEffect(() => {
    const frame = requestAnimationFrame(() => window.scrollTo({ top: caches.current[mode].scroll ?? 0, behavior: 'instant' }))
    return () => { cancelAnimationFrame(frame); caches.current[mode].scroll = window.scrollY }
  }, [mode])
  const navigation = <RecommendationModeNavigation current={mode} capabilities={capability.data}
    tagsPath={mode === 'tags' ? `/recommendations/tags${location.search}` : caches.current.tags.path}
    similarSeed={mode === 'similar' ? parseSimilarSeed(location.search).id : caches.current.similar.seedId} />
  const shared = { client, sessionScope, adult, setAdult, navigation, capabilities: capability.data }
  return <ExplorationTopicsContext.Provider value={topics}>
    {mode === 'personal' ? <Personal {...shared} cache={caches.current.personal} />
      : mode === 'similar' && capability.data?.navigation?.[1]?.available
        ? <SimilarRecommendations {...shared} cache={caches.current.similar} />
        : mode === 'tags' && capability.data?.navigation?.[2]?.available
          ? <TagRecommendations {...shared} cache={caches.current.tags} />
        : <main className="memphis-rec-container"><div className="rec-page"><div className="rec-masthead">
          <nav className="rec-nav"><Link className="rec-back" to="/dashboard">← 返回我的手账</Link></nav>
          <header className="rec-page-heading rec-explore-heading"><span className="rec-chapter-label">我的手账</span><h1>番剧探索</h1>{navigation}</header>
          </div><section className="rec-empty" role="status"><h2>{mode === 'tags' ? '按标签找' : '单番相似'}</h2>
          <p>{capability.status === 'loading' ? '正在读取探索服务状态…' : capability.status === 'error' ? '探索服务状态读取失败。' : '此探索方式尚未开放，请稍后再来。'}</p>
          <Link className="rec-button" to="/recommendations">返回个性化推荐</Link>
          <button type="button" className="rec-button" onClick={() => setAttempt(value => value + 1)}>重新读取服务状态</button>
        </section></div></main>}
    {notice && <div className="rec-host-notice" role="status">{notice}</div>}
  </ExplorationTopicsContext.Provider>
}
