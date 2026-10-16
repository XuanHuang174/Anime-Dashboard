export default function RecommendationHiddenWorks({ state, feedback, onPage, onUndo, onReload, writeBusy }) {
  return <section className="rec-hidden-works" aria-label="已隐藏作品清单">
    <p className="rec-hidden-note">仅隐藏指定作品，不影响手账或题材偏好。撤销后恢复参与推荐的资格，不保证原位置。</p>
    {state.status === 'loading' ? <p role="status">正在读取已隐藏作品…</p> : state.status === 'error'
      ? <div role="alert"><p>{state.message}</p><button type="button" className="rec-button" onClick={onReload}>重新加载第一页</button></div>
      : !state.data?.results.length ? <p className="rec-muted">还没有已隐藏作品。</p>
        : <><ul>{state.data.results.map(row => {
          const item = { bangumiId: row.bangumi_id, title: row.title_zh || row.title_jp }
          const op = feedback.operations[item.bangumiId]
          return <li key={item.bangumiId}>
            <div><h3>{item.title}</h3><p>{row.season || '季度未知'} · {row.status_type || '类型未知'}</p>
              <p>隐藏于 <time dateTime={row.hidden_at}>{new Date(row.hidden_at).toLocaleString('zh-CN')}</time></p></div>
            <button type="button" className="rec-button" disabled={feedback.pending || feedback.uncertain || writeBusy?.(item.bangumiId)}
              onClick={() => onUndo(item, row.feedback_version)}>{op?.status === 'saving' ? '正在处理…' : '撤销隐藏'}</button>
            {op?.message && op.status !== 'saving' && <div className="rec-hidden-error" role="alert"><p>{op.message}</p>
              {op.status === 'conflict' && <button type="button" className="rec-text-button" onClick={onReload}>重新读取反馈状态</button>}</div>}
          </li>
        })}</ul>
          <nav className="rec-hidden-pagination" aria-label="隐藏清单分页">
            <button type="button" className="rec-button" disabled={state.data.page <= 1} onClick={() => onPage(state.data.page - 1)}>上一页</button>
            <span>第 {state.data.page} 页 · {state.data.count} 部</span>
            <button type="button" className="rec-button" disabled={!state.data.next_page} onClick={() => onPage(state.data.next_page)}>下一页</button>
          </nav></>}
  </section>
}
