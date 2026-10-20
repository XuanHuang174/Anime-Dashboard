import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import RecommendationDialog from './RecommendationDialog.jsx'
import { createIdentityReviewController, MAX_IDENTITY_BATCH, identitySeasonDiffers } from '../../utils/recommendationIdentityBatch.js'
import '../../styles/recommendations-identity.css'

export default function RecommendationIdentityReview({ client, onClose, onChanged, scope = 'high-score' }) {
  const controller = useMemo(() => createIdentityReviewController(client, scope), [client, scope])
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const [discard, setDiscard] = useState(false)
  const closed = useRef(false)
  useEffect(() => { controller.activate(); controller.loadPage(); return () => controller.dispose() }, [controller])
  const drafts = Object.values(state.drafts), checked = drafts.filter(row => row.confirmed && !row.error).length
  const sending = state.batch?.status === 'sending'
  const unresolved = ['uncertain', 'retryable'].includes(state.batch?.status)
  const locked = sending || unresolved
  const searching = Object.values(state.searches).some(row => row.busy)
  const finish = () => { if (closed.current) return; closed.current = true; if (state.mayHaveChanged) onChanged(); onClose() }
  const close = () => { if (!sending) { if (drafts.length || unresolved) setDiscard(true); else finish() } }
  return <RecommendationDialog kind="identity" title="核对旧作品身份" busy={sending} onClose={close}>
    <div className="rec-identity-workbench">
      <p className="rec-identity-intro">核对你自己的{scope === 'all' ? '旧记录（全部观看状态）' : '评分达到当前取样门槛的旧记录'}，逐条选择并勾选后统一提交。请留意不同季、OVA 和剧场版；评分、长评、状态和私人标签不修改。</p>
      <div className="rec-identity-content">
        <div className="rec-identity-pagination"><span>{state.focusedId ? '正在核对清单中的单条记录' : state.data ? `${state.data.count} 条记录待核对 · 第 ${state.page} 页` : '待核对列表'}</span>
          {state.focusedId && <button type="button" className="rec-button" disabled={locked} onClick={() => controller.loadPage(1)}>返回待核对列表</button>}
          <button type="button" className="rec-text-button" disabled={locked || searching || state.status === 'loading'} onClick={() => controller.loadPage(state.page, state.focusedId)}>刷新候选</button>
          <button type="button" className="rec-button" disabled={locked || state.focusedId || state.status === 'loading' || state.page <= 1} onClick={() => controller.loadPage(state.page - 1)}>上一页</button>
          <button type="button" className="rec-button" disabled={locked || state.focusedId || state.status === 'loading' || !state.data?.next_page} onClick={() => controller.loadPage(state.data.next_page)}>下一页</button></div>
        {state.status === 'loading' ? <p role="status">正在读取待关联作品，跨页选择仍保留…</p> : state.status === 'error'
          ? <div role="alert"><p>{state.error}</p><button type="button" className="rec-button" disabled={locked} onClick={() => controller.loadPage(state.page, state.focusedId)}>重试读取</button></div>
          : <>{!state.data?.results.length && <p role="status">目前没有可核对的{scope === 'all' ? '' : '评分达标的'}旧记录。</p>}
            {state.data?.results.map(item => {
              const id = item.record_id, draft = state.drafts[id], local = state.searches[id]
              return <section className="rec-identity-row" key={id} aria-label={`核对记录 ${id}`}>
                <div className="rec-identity-heading"><div><h3>{item.title}</h3><p className="rec-muted">原名：{item.original_title || '未填写'} · 原记录季度：{item.season || '未知'}</p></div>
                  {draft && <span className="rec-identity-badge">{draft.confirmed && !draft.error ? '已核对，待提交' : '待确认'}</span>}</div>
                {item.status === 'needs_admin_review' ? <p>已有权威身份发生冲突，请联系管理员核对。</p> : <>
                  <form className="rec-identity-search" onSubmit={event => { event.preventDefault(); controller.search(item) }}>
                    <label className="rec-field">查找标准作品<input maxLength={100} minLength={2} required disabled={locked || local?.busy}
                      value={state.keywords[id] ?? item.title.slice(0, 100)} onChange={event => controller.setKeyword(id, event.target.value)} /></label>
                    <button type="submit" className="rec-button" disabled={locked || local?.busy}>{local?.busy ? '搜索中…' : '搜索候选'}</button></form>
                  {!item.candidates.length && <p className="rec-muted">暂无安全候选，可尝试原名或缩短搜索词；不会自动匹配。</p>}
                  <fieldset className="rec-identity-candidates" disabled={locked || local?.busy}><legend>选择同一部作品</legend>
                    {item.candidates.map(candidate => <label key={candidate.bangumi_id} className="rec-identity-candidate">
                      <input type="radio" name={`record-${id}`} value={candidate.bangumi_id} checked={draft?.candidate.bangumi_id === candidate.bangumi_id}
                        disabled={!draft && drafts.length >= MAX_IDENTITY_BATCH} onChange={() => controller.choose(item, candidate)} />
                      <span><strong>{candidate.title_zh || candidate.title_jp}</strong><small>{candidate.title_jp} · 候选季度：{candidate.season || '未知'} · {candidate.status_type || '类型未知'} · BGM {candidate.bangumi_id}</small></span></label>)}</fieldset>
                  {draft && identitySeasonDiffers(item.season, draft.candidate.season) && <p className="rec-identity-difference">季度或日期不同：{item.season || '未知'} → {draft.candidate.season || '未知'}。请人工核对，系统不会据此判断为同一作品。</p>}
                  <label className="rec-checkbox"><input type="checkbox" disabled={locked || local?.busy || !draft || !!draft.error}
                    checked={!!draft?.confirmed} onChange={event => controller.confirm(id, event.target.checked)} />我已核对，这是同一部作品（不是其他季或版本）</label>
                  {(draft?.error || local?.error) && <p className="rec-inline-error" role="alert">{draft?.error || local.error}</p>}
                </>}
              </section>
            })}</>}
      </div>
      <footer className="rec-identity-footer">
        {drafts.length > 0 && <details className="rec-identity-drafts"><summary>已暂存 {drafts.length} 部选择（跨页保留）</summary><ul>{drafts.map(({ item, candidate, confirmed, error }) => <li key={item.record_id}>
          <span>{item.title} → {candidate.title_zh || candidate.title_jp}<small>{error || (confirmed ? '已核对，待提交' : '尚未勾选核对')}</small></span>
          <div>{error && <button type="button" className="rec-text-button" disabled={locked} onClick={() => controller.loadPage(1, item.record_id)}>定位核对</button>}
          <button type="button" className="rec-text-button" disabled={locked} onClick={() => controller.remove(item.record_id)}>移除</button></div></li>)}</ul></details>}
        {state.message && <p role="status">{state.message}</p>}
        {state.batch?.data && <p role="status">已处理 {state.batch.data.processed_count} 部：成功 {state.batch.data.success_count} 部，{state.batch.data.failed_count} 部待处理。推荐将在关闭面板后统一更新。</p>}
        {unresolved && <p role="alert">{state.batch.error || '部分条目暂未确认，请重试原批次；已成功项不会重复关联。'}</p>}
        {discard ? <div className="rec-identity-discard" role="alert"><p>放弃未提交的选择并关闭？已完成的关联不会撤销；未确认的提交将在关闭后重新读取。</p>
          <button type="button" className="rec-button" onClick={() => setDiscard(false)}>继续核对</button>
          <button type="button" className="rec-button" onClick={finish}>放弃草稿并关闭</button></div>
          : <div className="rec-identity-savebar"><span>已核对 <strong>{checked}</strong> 部 · 最多暂存 {MAX_IDENTITY_BATCH} 部</span>
            <button type="button" className="rec-text-button" disabled={locked || !drafts.length} onClick={controller.clear}>清空选择</button>
            <button type="button" className="rec-button" disabled={sending} onClick={close}>完成核对并返回</button>
            <button type="button" className="rec-button rec-button--primary" disabled={sending || searching || !unresolved && !checked} onClick={controller.submit}>
              {sending ? `正在关联 ${state.batch.payload.items.length} 部…` : unresolved ? '重试原批次' : `关联已核对的 ${checked} 部`}</button></div>}
      </footer>
    </div>
  </RecommendationDialog>
}
