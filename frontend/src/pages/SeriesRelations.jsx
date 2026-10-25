import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import defaultClient from '../api/recommendations.js'
import useSeriesSession, { seriesSession } from '../hooks/useSeriesSession.js'
import { createSeriesWorkspace } from '../utils/seriesWorkspace.js'
import { COVERAGE, SERIES_SECTIONS, seriesStatus } from '../utils/recommendationSeries.js'
import { notifyRecommendationChange } from '../utils/recommendationReload.js'
import { wantButtonState } from '../utils/recommendationWant.js'
import { watchRecommendationScope } from '../utils/recommendationScopeMonitor.js'
import RecommendationDialog from '../components/recommendations/RecommendationDialog.jsx'
import RecommendationResourcePanel from '../components/recommendations/RecommendationResourcePanel.jsx'
import { RecommendationCover } from '../components/recommendations/RecommendationCard.jsx'
import '../styles/recommendations.css'
import '../styles/recommendations-layout.css'
import '../styles/recommendations-theme.css'
import '../styles/recommendation-series.css'

export default function SeriesRelations({ client = defaultClient, overlay = false }) {
  const { bangumiId } = useParams(), session = useSeriesSession(), location = useLocation()
  const scope = location.state?.seriesScope === 'include_adult' ? 'include_adult' : 'safe'
  return <SeriesWorkspace key={`${session}:${bangumiId}:${scope}`} baseClient={client} source={Number(bangumiId)} scope={scope} session={session} overlay={overlay} target={location.state?.seriesTarget} />
}
function SeriesWorkspace({ baseClient, source, scope, session, overlay, target }) {
  const navigate = useNavigate(), resourceTrigger = useRef(null), [resources, setResources] = useState(false)
  const initialTarget = useRef(null)
  const client = useMemo(() => baseClient.forScope(scope), [baseClient, scope])
  const controller = useMemo(() => createSeriesWorkspace({ client, source, scope,
    isCurrent: () => seriesSession() === session, onConfirmed: () => notifyRecommendationChange() }), [client, source, scope, session])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot)
  const back = () => overlay ? navigate(-1) : navigate('/recommendations')
  useEffect(() => {
    controller.activate(); controller.load()
    return () => controller.dispose()
  }, [controller])
  useEffect(() => setResources(false), [state.detail?.target, state.detail?.status])
  const data = state.data
  useEffect(() => {
    if (!data || initialTarget.current === controller || !Number.isInteger(target) || target < 1) return
    initialTarget.current = controller
    if (target === source || data.results.some(row => row.bangumi_id === target)) controller.select(target)
  }, [controller, data, source, target])
  useEffect(() => {
    if (!data) return
    const expiry = data.content_scope_context.scope_valid_until
    const deadline = Math.min(Date.now() + 900000, expiry === null || expiry === undefined ? Infinity : Date.parse(expiry))
    const monitor = watchRecommendationScope({ deadline, probe: () => controller.load(true),
      onLost: () => controller.invalidate({ scopeLost: true }) })
    return () => monitor.dispose()
  }, [controller, data?.context])
  const body = <main className="series-page" aria-label="系列与关联详情">
    <header className="series-heading"><button type="button" className="rec-button" onClick={back}>← {overlay ? '返回原页' : '返回番剧探索'}</button><span className="series-kicker">SERIES / 作品关联</span>
      <h1>{data ? data.selected_seed.title_zh || data.selected_seed.title_jp : '系列与关联'}</h1>
      <p>从这部作品出发，了解故事前后与不同版本。</p></header>
    {state.status === 'loading' ? <p className="series-notice" role="status">正在读取系列资料与本人记录…</p>
      : state.status === 'error' ? <section className="series-notice" role="alert"><p>{state.message}</p><button type="button" className="rec-button" onClick={() => controller.load()}>重新读取</button></section>
        : <><section className="series-notice"><strong>{COVERAGE[data.coverage]}</strong>
          {['failed', 'partial'].includes(data.last_attempt_status) && data.coverage !== data.last_attempt_status && <p>最近更新{data.last_attempt_status === 'failed' ? '失败' : '不完整'}，保留上次可用资料。</p>}
          <p>下列关系均相对于「{data.selected_seed.title_zh || data.selected_seed.title_jp}」。按发行日期整理，不代表建议观看顺序或必看前置。</p>
          <p>{data.scope_note}</p><p>{data.record_note}</p>
          <p>本人本作状态：<strong>{seriesStatus(data.selected_seed)}</strong></p>
          <button type="button" className="rec-button" onClick={() => controller.select(source)}>查看本作详情 ↗</button>
          {scope === 'include_adult' && <p>包含已审核 NSFW 关联，仅对本次浏览生效。</p>}
          <button type="button" className="series-link" onClick={() => controller.load()}>重新读取资料与状态</button>
          {data.snapshot_version && <small className="series-source">来源快照：{data.snapshot_version} · 系列版本 {data.series_revision}</small>}</section>
          {!data.results.length && <section className="series-notice"><h2>当前没有可展示的关联</h2><p>可能尚未获取关系、来源没有列出关联，或关联不在当前可展示范围内。这不代表本作是独立作品。</p></section>}
          {Object.entries(SERIES_SECTIONS).map(([section, title]) => {
            const rows = data.results.filter(row => row.section === section)
            return rows.length ? <section className="series-group" key={section}><h2>{title}</h2>
              {section === 'related' && <p>世界观、角色与其他关联，不等同于同一连续剧情。</p>}
              <ul>{rows.map(row => <li className="series-row" key={`${row.bangumi_id}:${row.kind}`}><span className="series-relation">{row.relation_label}</span>
                <div className="series-row-main"><h3>{row.title_zh || row.title_jp}</h3><p>{row.title_jp && row.title_jp !== row.title_zh ? `${row.title_jp} · ` : ''}Bangumi {row.bangumi_id}</p>
                  <p>{row.release_date || '发行日期未知'} · {row.release_format || '发行形式未知'}{row.origin === 'manual' ? ' · 人工核验' : ''}</p></div>
                <div className="series-row-actions"><span className="series-own-status">{seriesStatus(row)}</span><button type="button" className="rec-button" onClick={() => controller.select(row.bangumi_id)}>查看详情 ↗</button>
                  <SeriesWant row={row} operation={state.operations[row.bangumi_id]} onWant={() => controller.want(row.bangumi_id)} /></div></li>)}</ul></section> : null
          })}
          {data.truncated && <p className="series-notice">可见关联较多，本页展示前 200 条；这不是完整系列或观看清单。</p>}
          <footer className="series-footer">我的手账 / 番剧探索 / 系列与关联</footer></>}
  </main>
  return <div className={`memphis-rec-container series-shell${overlay ? ' series-shell--overlay' : ''}`}>{overlay ? <RecommendationDialog kind="series" title="系列与关联" onClose={back}>{body}</RecommendationDialog> : body}
    {state.detail && <RecommendationDialog title={state.detail.item?.title || '关联作品详情'} onClose={controller.closeDetail}
      resourceOpen={resources && state.detail.status === 'ready'} onResourceClose={() => setResources(false)} resourceTriggerRef={resourceTrigger}
      resourcePanel={state.detail.status === 'ready' ? <RecommendationResourcePanel item={state.detail.item} open={resources}
        sessionKey={`${session}:${source}:${state.detail.target}`} onClose={() => setResources(false)} /> : null}>
      {state.detail.status === 'loading' ? <p role="status">正在读取作品详情…</p> : state.detail.status === 'error'
        ? <div role="alert"><p>{state.detail.message}</p><button type="button" className="rec-button" onClick={() => controller.select(state.detail.target)}>重试详情</button></div>
        : <div className="rec-detail-content"><RecommendationCover item={state.detail.item} priority /><div><p>{state.detail.item.originalTitle}</p>
          <p>{state.detail.item.season || '季度未知'} · {state.detail.item.type || '发行形式未知'}</p><p>本人状态：{seriesStatus({ watch_status: state.detail.item.watchStatus, record_state: state.detail.item.recordState })}</p>
          <h3>故事简介</h3><p>{state.detail.item.synopsis || '暂未收录简介。'}</p><p>{state.detail.item.tags.join(' · ')}</p>
          <div className="series-detail-actions"><SeriesWant row={{ bangumi_id: state.detail.target, watch_status: state.detail.item.watchStatus, record_state: state.detail.item.recordState }}
            operation={state.operations[state.detail.target]} onWant={() => controller.want(state.detail.target)} />
            <button type="button" ref={resourceTrigger} className="rec-button" aria-expanded={resources} onClick={() => setResources(value => !value)}>🧲 寻找资源</button></div></div></div>}
    </RecommendationDialog>}
  </div>
}
function SeriesWant({ row, operation, onWant }) {
  const button = wantButtonState({ watchStatus: row.watch_status }, operation)
  return <><button type="button" className="rec-button" disabled={button.disabled || row.record_state === 'needs_review' || button.confirm} onClick={onWant}>
    {row.record_state === 'needs_review' || button.confirm ? '请先核对旧记录' : button.label}</button>
    {operation?.error && <p className="rec-inline-error" role="alert">{operation.error.message} {button.confirm && '可返回个性化推荐的旧记录核对入口处理。'}</p>}</>
}
