import { MAX_TASTE_TAGS } from '../../utils/recommendationPreferences.js'
import { samplingPreferences } from '../../utils/recommendationSampling.js'

export default function RecommendationPreferencesPanel({ editor, profile, onTag, onClear, onSave,
  onReloadLatest, onResolveConflict, onClose, onMinScore, onResetMinScore, onRetryPreview }) {
  const busy = ['saving', 'reloading'].includes(editor.status)
  const blocked = busy || ['conflict', 'conflict_review', 'uncertain', 'saved_refresh_failed'].includes(editor.status)
  const trusted = profile?.extraction?.trusted_profile
  const common = profile?.extraction?.common_topics
  const available = editor.preferences?.available_tags || []
  const observed = (common?.requires_confirmation === true && common?.used_for_recommendation === false
    ? common.tag_summary : [])?.filter(item => available.includes(item.tag)).slice(0, 8) || []
  const personalized = profile && !profile.cold_start
  const sampling = editor.preferences && samplingPreferences(editor.preferences)
  const preview = editor.preview || { status: 'idle' }
  const previewChanged = sampling && editor.draftMinScore && editor.draftMinScore !== sampling.score

  return <div className="rec-preference-panel">
    <p className="rec-preference-basis"><strong>{personalized ? '参考符合取样条件的作品，并结合你的明确偏好'
      : editor.preferences?.tags.length ? '根据你选择的题材推荐' : '先选择明确偏好，再开始题材推荐'}</strong>
      <span>明确偏好是排序加权信号，不是硬性过滤；取消题材不代表完全隐藏。草稿需保存后生效。</span></p>
    <div className="rec-preference-columns">
    <section className="rec-preference-section rec-preference-section--explicit" aria-labelledby="rec-explicit-title">
      <h3 id="rec-explicit-title">1. 我的明确偏好 <small>可修改</small></h3>
      <div className="rec-preference-scroll" role="region" aria-label="我的明确偏好内容" tabIndex={0}>
      <p>建议选择 3–5 个，最多 {MAX_TASTE_TAGS} 个。不修改手账评分、标签或长评。</p>
      {editor.preferences?.option_order_status === 'ready' && <p className="rec-muted">按安全推荐库中题材覆盖的作品数排序。</p>}
      {editor.preferences?.option_order_status === 'unavailable' && <p className="rec-muted">题材数量暂不可用，当前按固定顺序展示，不影响选择和保存。</p>}
      <p className="rec-muted">已保存：{editor.preferences?.tags.join('、') || '尚未选择'} · 当前为本地草稿</p>
      <fieldset className="rec-taste-options" disabled={blocked}><legend>明确喜好标签（{editor.draftTags.length}/{MAX_TASTE_TAGS}）</legend>
        {available.map(tag => <label className="rec-taste-option" key={tag}>
          <input type="checkbox" checked={editor.draftTags.includes(tag)}
            disabled={!editor.draftTags.includes(tag) && editor.draftTags.length >= MAX_TASTE_TAGS}
            onChange={event => onTag(tag, event.target.checked)} />{tag}</label>)}
      </fieldset>
      <button type="button" className="rec-text-button" disabled={blocked || !editor.draftTags.length} onClick={onClear}>清空草稿中的明确偏好</button>
      <p className="rec-muted">清空后仍需保存；只清空手动选择，不删除自动画像或手账资料。</p>
      </div>
    </section>
    <section className="rec-preference-section rec-preference-section--trusted" aria-labelledby="rec-trusted-title">
      <h3 id="rec-trusted-title">2. 我的作品画像 <small>取样设置与解释</small></h3>
      <div className="rec-preference-scroll" role="region" aria-label="我的作品画像内容" tabIndex={0}>
      {sampling ? <section className="rec-sampling-control" aria-label="画像取样门槛">
        <label htmlFor="rec-profile-min-score">画像取样门槛：个人评分 ≥</label>
        <div className="rec-sampling-inputs"><select id="rec-profile-min-score" value={editor.draftMinScore ?? sampling.score}
          disabled={blocked || !sampling.control.enabled} onChange={event => onMinScore?.(event.target.value)} aria-describedby="rec-sampling-help">
          {sampling.control.choices.map(score => <option key={score} value={score}>{score} 分</option>)}
        </select><button type="button" className="rec-text-button" onClick={onResetMinScore}
          disabled={blocked || !sampling.control.enabled || (editor.draftMinScore ?? sampling.score) === sampling.control.default}>恢复默认 {sampling.control.default}</button></div>
        <p id="rec-sampling-help">这是个人评分取样门槛，不是 Bangumi 评分筛选。降低门槛会扩大画像范围，不保证推荐更准确；至少需要 3 部有效样本。</p>
        <p className="rec-muted">已保存 ≥ {sampling.score} 分 · 当前生效 ≥ {sampling.effective} 分。修改仅为草稿，保存后才更新推荐。</p>
        {!sampling.control.enabled && <p className="rec-muted" role="status">画像门槛调整暂未开放，已保存值仍保留，当前继续使用默认门槛。</p>}
        {sampling.control.enabled && previewChanged && <div className="rec-sampling-preview" aria-live="polite" aria-busy={['loading', 'debouncing'].includes(preview.status)}>
          <strong>未保存预览 · 个人评分 ≥ {editor.draftMinScore} 分</strong>
          {['loading', 'debouncing'].includes(preview.status) ? <p role="status">正在预览可分析样本…</p>
            : preview.status === 'ready' ? <><p>可分析样本 {preview.data.usable_sample_count} 部 · {preview.data.cold_start ? '尚不足 3 部' : '达到 3 部画像条件'}。</p>
              {preview.data.diagnostics.pending_identity_count > 0 && <p>{preview.data.diagnostics.pending_identity_count} 条达标记录待关联身份。</p>}</>
              : <><p role={preview.status === 'error' ? 'alert' : 'status'}>{preview.error || '预览尚未读取，不影响当前已保存设置。'}</p>
                <button type="button" className="rec-text-button" disabled={blocked} onClick={onRetryPreview}>重试预览</button></>}
          <p className="rec-muted">预览不保存，也不替换下方已生效画像。</p>
        </div>}
      </section> : <p className="rec-muted">当前服务尚未提供门槛设置，沿用现有取样规则。</p>}
      {trusted ? <><p><strong>已生效画像：</strong>可分析样本 {trusted.usable_work_count} 部。来源是“看过”、个人评分 ≥ {profile.sample_policy.minimum_score}、
        已确认身份且安全分级的作品；同一 Bangumi 作品只计一次。不会读取长评推断喜好。</p>
        <ul className="rec-topic-list">{trusted.tag_summary.map(item => <li key={item.tag}>
          <strong>{item.tag}</strong><span>{item.sample_count} 部画像样本包含此题材</span></li>)}</ul>
        {!trusted.tag_summary.length && <p className="rec-muted">尚无可解释的题材特征，不代表你没有喜好。</p>}</>
        : <p className="rec-muted">画像摘要暂不可用，重新读取后展示，不以 0 代替缺失数据。</p>}
      </div>
    </section>
    <section className="rec-preference-section rec-preference-section--observed" aria-labelledby="rec-observed-title">
      <h3 id="rec-observed-title">3. 手账常见题材 <small>待你确认</small></h3>
      <div className="rec-preference-scroll" role="region" aria-label="手账常见题材内容" tabIndex={0}>
      <p>观察未评分、低分或其他观看状态的安全已关联作品。这只是常见题材，不代表你喜欢；未经确认不会用于推荐。</p>
      {observed.length ? <ul className="rec-topic-list">{observed.map(item => {
        const chosen = editor.draftTags.includes(item.tag)
        return <li key={item.tag}><span>你的手账中有 {item.sample_count} 部作品包含“<strong>{item.tag}</strong>”</span>
          <button type="button" className="rec-button" disabled={blocked || chosen || editor.draftTags.length >= MAX_TASTE_TAGS}
            onClick={() => onTag(item.tag, true)}>{chosen ? '已在草稿中' : '加入我的偏好'}</button></li>
      })}</ul> : <p className="rec-muted">{common ? '暂无可确认的常见题材；不会猜测未知标签的含义。' : '观察摘要暂不可用，请重新读取。'}</p>}
      <p className="rec-muted">最多展示 8 项，点击只加入草稿，不自动保存。</p>
      </div>
    </section>
    </div>
    <footer className="rec-preference-footer">
    <div className="rec-preference-feedback">
    {editor.error && <p className="rec-inline-error" role="alert">{editor.error}</p>}
    {editor.message && <p className="rec-muted" role="status">{editor.message}</p>}
    {['conflict', 'uncertain'].includes(editor.status) && <button type="button" className="rec-button" onClick={onReloadLatest}>读取最新设置，保留草稿</button>}
    {editor.status === 'saved_refresh_failed' && <button type="button" className="rec-button" onClick={onClose}>关闭并重试更新推荐</button>}
    {editor.status === 'reloading' && <p role="status">正在读取最新设置，原草稿仍保留…</p>}
    {editor.status === 'conflict_review' && <section className="rec-preference-conflict" aria-label="检查偏好冲突">
      <p>最新已保存：{editor.remotePreferences.tags.join('、') || '未选择'}。你的草稿仍保留，请选择处理方式。</p>
      <p>合并仅应用本次新增/移除的题材，保留其他页面新增的题材；不会自动保存，请检查后再提交。</p>
      {editor.remotePreferences.profile_min_score !== undefined && <p>最新门槛 ≥ {editor.remotePreferences.profile_min_score} 分；本页草稿 ≥ {editor.draftMinScore ?? '—'} 分。</p>}
      <div className="rec-cold-actions"><button type="button" className="rec-button" onClick={() => onResolveConflict(false)}>采用最新设置，放弃旧草稿</button>
        {editor.scoreConflict ? <><button type="button" className="rec-button" onClick={() => onResolveConflict(true, 'local')}>保留本页门槛并合并题材</button>
          <button type="button" className="rec-button" onClick={() => onResolveConflict(true, 'remote')}>采用最新门槛并合并题材</button></>
          : <button type="button" className="rec-button" onClick={() => onResolveConflict(true)}>合并本次修改到草稿</button>}</div>
    </section>}
    </div>
    <div className="rec-preference-savebar"><p className="rec-preference-draft" role="status" aria-live="polite">草稿已选 <strong>{editor.draftTags.length}</strong> / {MAX_TASTE_TAGS}{sampling?.control.enabled && <> · 门槛 ≥ {editor.draftMinScore} 分</>} · 保存后生效</p>
    <div className="rec-dialog-actions"><button type="button" className="rec-button" disabled={busy} onClick={onClose}>取消，不保存</button>
      <button type="button" className="rec-button rec-button--primary" disabled={blocked} onClick={onSave}>{editor.status === 'saving' ? '正在保存…' : editor.status === 'error' ? '重试保存喜好' : '保存喜好'}</button></div>
    </div>
    </footer>
  </div>
}
