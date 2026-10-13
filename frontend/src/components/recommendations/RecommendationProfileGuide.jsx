import { Link } from 'react-router-dom'

export function RecommendationRefreshNotice({ confirmedPreferences, status, onRetry }) {
  if (!confirmedPreferences) return null
  return <section className="rec-save-refresh" role={status === 'error' ? 'alert' : 'status'}>
    <p>{status === 'error' ? '偏好已保存，推荐更新失败。已保存的设置不会丢失，也无需重复保存。'
      : '偏好已保存，正在重新读取画像与推荐…'}</p>
    {status === 'error' && <button type="button" className="rec-button" onClick={onRetry}>重试更新推荐</button>}
  </section>
}

export function RecommendationTasteNote({ profile, preferences, status, onPreferences, onRetry, confirmedPreferences }) {
  const unavailable = !profile || status === 'loading' || status === 'error'
  return <aside className="rec-taste-note" aria-labelledby="rec-taste-title" aria-busy={status === 'loading'}>
    <span className="rec-eyebrow">A NOTE ABOUT YOUR TASTE</span>
    <h2 id="rec-taste-title">我的口味 <span aria-hidden="true">↗</span></h2>
    {unavailable ? <p className="rec-muted" role="status">{status === 'error'
      ? '口味摘要暂未加载，不能据此判断你的手账或喜好。' : '正在读取你的真实喜好…'}</p>
      : <><dl className="rec-taste-metrics">
        <div><dt>可分析样本</dt><dd>{profile.usable_sample_count ?? '—'} 部</dd></div>
        <div><dt>自动识别题材</dt><dd>{profile.tag_summary.length} 个</dd></div>
        <div><dt>手动偏好</dt><dd>{preferences?.tags.length ?? '—'} 个</dd></div>
      </dl>
      <div className="rec-taste-genres"><p className="rec-muted">来自高分作品</p>
        <div className="rec-card-tags">{profile.tag_summary.slice(0, 3).map(({ tag }) => <span key={tag}>{tag}</span>)}</div>
        {!profile.tag_summary.length && <small>尚无可用题材，不代表没有喜好</small>}
        <p className="rec-muted">你的明确选择</p>
        <div className="rec-card-tags">{preferences?.tags.slice(0, 3).map(tag => <span key={tag}>{tag}</span>)}</div>
        {!preferences?.tags.length && <small>尚未选择；不会自动替你勾选</small>}
      </div></>}
    {unavailable && confirmedPreferences && <p className="rec-muted">手动偏好已保存 {confirmedPreferences.tags.length} 个；自动画像等待重新读取。</p>}
    {status === 'error' ? <button type="button" className="rec-text-button" onClick={onRetry}>重试读取 →</button>
      : <button type="button" className="rec-text-button" disabled={!preferences} onClick={onPreferences}>调整明确喜好 →</button>}
  </aside>
}

function guideCopy(state, score) {
  switch (state.code) {
    case 'empty_journal': return ['从几种喜欢的题材开始', state.explicit_preference_count
      ? '手账暂时为空，已保留你的明确选择；记录和评分后可以逐步形成画像。'
      : '手账暂时为空，也没有明确偏好。可以先选择 3–5 个喜欢的题材，或记下看过的作品。']
    case 'all_unrated': return ['给喜欢的作品留下评分', `手账中的 ${state.unrated_record_count} 部作品都尚未评分。未评分不等于 0 分，常见题材也不等于喜欢。`]
    case 'no_qualifying_high_scores': return ['还没有符合条件的高分样本', `目前没有“看过”且个人评分 ≥ ${score} 的作品。请按真实感受评分，不需要为了推荐提高分数。`]
    case 'pending_identity': return ['让高分旧作品参与画像', `${state.pending_identity_count} 条高分记录尚待核对作品身份，暂未计入画像。原有评分和长评仍完整保留，无需重新评分。`]
    case 'missing_genres': return ['给高分作品补充有效题材', `${state.missing_genre_count} 部已关联高分作品缺少可识别的题材。可以在手账中确认题材，或单独选择明确偏好；不会用目录标签覆盖你主动清空的标签。`]
    case 'insufficient_samples': return ['再认识几部你的心头好', `目前有 ${state.usable_sample_count} 部可分析高分作品，至少需要 ${state.minimum_samples} 部才能形成画像。不会仅凭少量样本假装了解你。`]
    default: return ['部分高分作品暂不能参与画像', `${state.unavailable_sample_count} 条高分记录因未收录或分级限制未计入画像。不会绕过安全规则，也不会要求你重新评分。`]
  }
}

export function RecommendationColdStartGuide({ profile, status, onPreferences, onIdentity, preferencesReady }) {
  // A failed request/empty filtered list is not evidence of an empty journal.
  if (!profile || status === 'loading' || status === 'error' || !profile.cold_start) return null
  const state = profile.cold_start_state
  if (!state) return <section className="rec-cold-guide" role="status"><h2>画像引导等待更新</h2>
    <p>当前接口尚未提供细分原因，请刷新页面或等待服务更新；不会将缺失数据当作空手账。</p></section>
  const [title, text] = guideCopy(state, profile.sample_policy.minimum_score)
  return <section className="rec-cold-guide" aria-labelledby="rec-cold-title" role="status">
    <div><span className="rec-eyebrow">BUILD YOUR TASTE, AT YOUR PACE</span><h2 id="rec-cold-title">{title}</h2>
      <p>{text}</p>
      <p className="rec-muted">可分析样本 {state.usable_sample_count} / {state.minimum_samples} 部；仅使用已看过的安全作品与个人评分。</p>
      {state.reasons.includes('pending_identity') && state.code !== 'pending_identity' && <p>另有 {state.pending_identity_count} 条高分记录待核对身份。</p>}
      {state.reasons.includes('missing_genres') && state.code !== 'missing_genres' && <p>另有 {state.missing_genre_count} 部已关联高分作品缺少有效题材。</p>}
      {state.recommendation_basis === 'explicit_preferences'
        ? <p className="rec-basis-label"><strong>根据你选择的题材推荐</strong> · 尚未形成完整的高分作品画像。</p>
        : <p className="rec-muted">画像尚未准备好，也没有明确偏好，暂不生成个性化结果。</p>}
    </div>
    <div className="rec-cold-actions">
      {state.reasons.includes('pending_identity') && <button type="button" className="rec-button"
        disabled={profile.identity_service_ready === false} onClick={onIdentity}>核对旧作品 →</button>}
      <button type="button" className="rec-button rec-button--primary" disabled={!preferencesReady} onClick={onPreferences}>选择 3–5 个喜欢的题材</button>
      <Link className="rec-button" to="/dashboard">{state.code === 'missing_genres' ? '去手账确认题材' : '去手账记录与评分'} →</Link>
    </div>
  </section>
}
