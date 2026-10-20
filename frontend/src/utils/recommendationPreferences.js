import { validateRecommendationPreferences, validateColdStartState } from './recommendations.js'
import { profileScore, samplingPreferences, validateProfilePreview } from './recommendationSampling.js'

export const MAX_TASTE_TAGS = 20

export function mergePreferenceDraft(base, draft, latest) {
  const removed = new Set(base.filter(tag => !draft.includes(tag)))
  const added = draft.filter(tag => !base.includes(tag))
  return [...new Set([...latest.filter(tag => !removed.has(tag)), ...added])]
}

// Private, page-local state. Nothing here persists drafts or inferred preferences.
export function createPreferenceEditor(client, onSaved = () => {}, timing = {}) {
  let state = { status: 'closed', draftTags: [] }, pending = null, disposed = false, epoch = 0
  let previewRequest = null, previewTimer = null, previewEpoch = 0
  const schedule = timing.setTimeout || globalThis.setTimeout, unschedule = timing.clearTimeout || globalThis.clearTimeout
  const cancelPreview = () => {
    previewEpoch++; if (previewTimer !== null) unschedule(previewTimer)
    previewTimer = null; previewRequest?.controller.abort(); previewRequest = null
  }
  const listeners = new Set()
  const update = changes => { state = { ...state, ...changes }; listeners.forEach(listener => listener()) }
  const editing = () => !disposed && ['editing', 'error'].includes(state.status)
  const start = () => {
    const request = { controller: new AbortController(), epoch }
    pending = request
    request.current = () => !disposed && request.epoch === epoch && pending === request
    return request
  }
  const stop = () => { epoch += 1; pending?.controller.abort(); pending = null }
  const idlePreview = () => ({ status: 'idle', data: null, error: '' })
  const preview = () => {
    if (!editing() || !samplingPreferences(state.preferences)?.control.enabled) return Promise.resolve(null)
    cancelPreview()
    const captured = state.preferences, score = state.draftMinScore, controller = new AbortController()
    const request = { controller, epoch: previewEpoch, editorEpoch: epoch }
    previewRequest = request
    const current = () => !disposed && previewRequest === request && request.epoch === previewEpoch
      && request.editorEpoch === epoch && state.draftMinScore === score && state.preferences === captured && editing()
    update({ preview: { status: 'loading', data: null, error: '' } })
    request.promise = Promise.resolve().then(() => current() ? client.previewProfile(score, controller.signal) : null).then(raw => {
      if (!current()) return null
      const data = validateProfilePreview(raw, score, captured)
      validateColdStartState(data.cold_start_state)
      update({ preview: { status: 'ready', data, error: '' } })
      return data
    }).catch(() => {
      if (current()) update({ preview: { status: 'error', data: null,
        error: '预览暂不可用或依据已变化，原设置未改变。可重试预览，或关闭后重新读取设置。' } })
      return null
    }).finally(() => { if (previewRequest === request) previewRequest = null })
    return request.promise
  }
  const queuePreview = () => {
    cancelPreview()
    if (state.draftMinScore === samplingPreferences(state.preferences)?.score) {
      update({ preview: idlePreview() }); return
    }
    update({ preview: { status: 'debouncing', data: null, error: '' } })
    const generation = previewEpoch
    previewTimer = schedule(() => { previewTimer = null; if (generation === previewEpoch) void preview() }, timing.delay ?? 350)
  }
  const setMinScore = value => {
    const control = state.preferences && samplingPreferences(state.preferences)
    const score = profileScore(value)
    if (!editing() || !control?.control.enabled || !score || state.draftMinScore === score) return
    update({ status: 'editing', draftMinScore: score, error: '' }); queuePreview()
  }
  return {
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) },
    getSnapshot: () => state,
    activate() { disposed = false },
    open(preferences) {
      if (disposed || pending) return false
      const value = validateRecommendationPreferences(preferences)
      cancelPreview(); epoch++
      update({ status: 'editing', preferences: value, draftTags: [...value.tags], draftMinScore: samplingPreferences(value)?.score ?? null,
        preview: idlePreview(), scoreConflict: false, error: '', message: '', remotePreferences: null })
      return true
    },
    setTag(tag, checked) {
      if (!editing() || !state.preferences.available_tags.includes(tag)) return
      const next = checked ? [...new Set([...state.draftTags, tag])] : state.draftTags.filter(value => value !== tag)
      if (next.length > MAX_TASTE_TAGS) { update({ error: '最多选择 20 个题材，请先移除一个。' }); return }
      update({ status: 'editing', draftTags: next, error: '' })
    },
    clearDraft() { if (editing()) update({ status: 'editing', draftTags: [], error: '' }) },
    setMinScore,
    resetMinScore() {
      const control = state.preferences && samplingPreferences(state.preferences)
      if (control?.control.enabled) setMinScore(control.control.default)
    },
    retryPreview: preview,
    close() {
      if (pending) return false // Do not claim cancellation of an in-flight server write.
      cancelPreview(); stop(); update({ status: 'closed', draftTags: [], draftMinScore: null, preview: idlePreview(), preferences: null, remotePreferences: null, error: '' })
      return true
    },
    save() {
      if (pending) return pending.promise
      if (!editing()) return Promise.resolve(null)
      cancelPreview(); update({ preview: idlePreview() })
      const request = start(), tags = [...state.draftTags], version = state.preferences.version
      const sampling = samplingPreferences(state.preferences), score = state.draftMinScore
      const scoreChanged = Boolean(sampling?.control.enabled && score !== sampling.score)
      const changed = tags.length !== state.preferences.tags.length
        || !tags.every(tag => state.preferences.tags.includes(tag)) || scoreChanged
      const changes = { tags, expected_version: version, ...(scoreChanged ? { profile_min_score: score } : {}) }
      let acknowledged = false
      request.promise = Promise.resolve().then(() => request.current()
        ? client.savePreferences(changes, request.controller.signal) : null).then(async raw => {
        if (!request.current()) return null
        acknowledged = true
        const confirmed = validateRecommendationPreferences(raw)
        if (confirmed.version < version || (changed && confirmed.version === version) || confirmed.tags.length !== tags.length
          || !confirmed.tags.every(tag => tags.includes(tag))
          || sampling && (!samplingPreferences(confirmed) || samplingPreferences(confirmed).score !== (scoreChanged ? score : sampling.score))) throw new Error('Invalid save acknowledgement')
        update({ status: 'closed', preferences: confirmed, draftTags: [...confirmed.tags], draftMinScore: samplingPreferences(confirmed)?.score ?? null, error: '' })
        // Keep a local refresh-initiation failure outside the save/validation state.
        try { await onSaved(confirmed) } catch {
          if (request.current()) update({ status: 'saved_refresh_failed', error: '偏好已保存，推荐更新失败。关闭面板重新读取即可，无需重复保存。' })
        }
        return confirmed
      }).catch(error => {
        if (request.current()) update({ status: acknowledged ? 'uncertain' : error?.response?.status === 409 ? 'conflict' : 'error',
          error: acknowledged ? '服务已响应，但保存确认数据不兼容。请读取最新设置确认结果，不会重复提交。'
            : error?.response?.status === 409 ? '偏好已在其他页面更新，本次没有覆盖新设置。你的草稿仍保留，请读取最新设置。'
              : '保存请求未确认，草稿仍保留，可以重试；不会覆盖其他页面的新设置。' })
        return null
      }).finally(() => { if (pending === request) pending = null })
      update({ status: 'saving', error: '' })
      return request.promise
    },
    reloadLatest() {
      if (pending) return pending.promise
      if (disposed || !['conflict', 'uncertain'].includes(state.status)) return Promise.resolve(null)
      cancelPreview(); update({ preview: idlePreview() })
      const request = start()
      request.promise = Promise.resolve().then(() => request.current() ? client.preferences(request.controller.signal) : null).then(raw => {
        if (!request.current()) return null
        const remote = validateRecommendationPreferences(raw)
        const base = samplingPreferences(state.preferences), latest = samplingPreferences(remote)
        const scoreConflict = Boolean(base && latest && state.draftMinScore !== base.score
          && latest.score !== base.score && state.draftMinScore !== latest.score)
        update({ status: 'conflict_review', remotePreferences: remote, scoreConflict, error: '' })
        return remote
      }).catch(() => {
        if (request.current()) update({ status: 'conflict', error: '重新读取失败，原草稿仍保留，请稍后重试。' })
        return null
      }).finally(() => { if (pending === request) pending = null })
      update({ status: 'reloading', error: '' })
      return request.promise
    },
    resolveConflict(merge, scoreChoice) {
      if (disposed || state.status !== 'conflict_review') return
      const remote = state.remotePreferences
      const draft = merge ? mergePreferenceDraft(state.preferences.tags, state.draftTags, remote.tags) : [...remote.tags]
      const base = samplingPreferences(state.preferences), latest = samplingPreferences(remote)
      const scoreEdited = Boolean(base && state.draftMinScore !== base.score)
      if (merge && scoreEdited && (!latest || !latest.control.enabled)) {
        update({ error: '最新服务暂未开放门槛编辑，请采用最新设置后重试；原草稿仍保留。' }); return
      }
      if (merge && state.scoreConflict && !['local', 'remote'].includes(scoreChoice)) {
        update({ error: '两处都修改了画像门槛，请明确选择采用哪一个值；不会自动覆盖。' }); return
      }
      const score = merge && scoreEdited && scoreChoice !== 'remote' ? state.draftMinScore : latest?.score ?? null
      if (draft.length > MAX_TASTE_TAGS || !draft.every(tag => remote.available_tags.includes(tag))) {
        update({ error: '合并后题材超出上限或词表已变化。请采用最新设置后重新选择；原草稿仍保留。' }); return
      }
      cancelPreview()
      update({ status: 'editing', preferences: remote, draftTags: draft, draftMinScore: score, remotePreferences: null, scoreConflict: false,
        error: '', message: merge ? '已合并到草稿，尚未保存。请检查后再点击保存喜好。' : '已读取最新设置，本地旧草稿已放弃。' })
      if (latest?.control.enabled && score !== latest.score) queuePreview()
    },
    dispose() { disposed = true; cancelPreview(); stop(); state = { status: 'closed', draftTags: [] } },
  }
}
