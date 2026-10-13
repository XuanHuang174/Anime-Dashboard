import { validateRecommendationPreferences } from './recommendations.js'

export const MAX_TASTE_TAGS = 20

export function mergePreferenceDraft(base, draft, latest) {
  const removed = new Set(base.filter(tag => !draft.includes(tag)))
  const added = draft.filter(tag => !base.includes(tag))
  return [...new Set([...latest.filter(tag => !removed.has(tag)), ...added])]
}

// Private, page-local state. Nothing here persists drafts or inferred preferences.
export function createPreferenceEditor(client, onSaved = () => {}) {
  let state = { status: 'closed', draftTags: [] }, pending = null, disposed = false, epoch = 0
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
  return {
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) },
    getSnapshot: () => state,
    activate() { disposed = false },
    open(preferences) {
      if (disposed || pending) return false
      const value = validateRecommendationPreferences(preferences)
      update({ status: 'editing', preferences: value, draftTags: [...value.tags], error: '', message: '', remotePreferences: null })
      return true
    },
    setTag(tag, checked) {
      if (!editing() || !state.preferences.available_tags.includes(tag)) return
      const next = checked ? [...new Set([...state.draftTags, tag])] : state.draftTags.filter(value => value !== tag)
      if (next.length > MAX_TASTE_TAGS) { update({ error: '最多选择 20 个题材，请先移除一个。' }); return }
      update({ status: 'editing', draftTags: next, error: '' })
    },
    clearDraft() { if (editing()) update({ status: 'editing', draftTags: [], error: '' }) },
    close() {
      if (pending) return false // Do not claim cancellation of an in-flight server write.
      stop(); update({ status: 'closed', draftTags: [], preferences: null, remotePreferences: null, error: '' })
      return true
    },
    save() {
      if (pending) return pending.promise
      if (!editing()) return Promise.resolve(null)
      const request = start(), tags = [...state.draftTags], version = state.preferences.version
      const changed = tags.length !== state.preferences.tags.length
        || !tags.every(tag => state.preferences.tags.includes(tag))
      let acknowledged = false
      request.promise = Promise.resolve().then(() => request.current()
        ? client.savePreferences({ tags, expected_version: version }, request.controller.signal) : null).then(raw => {
        if (!request.current()) return null
        acknowledged = true
        const confirmed = validateRecommendationPreferences(raw)
        if (confirmed.version < version || (changed && confirmed.version === version) || confirmed.tags.length !== tags.length
          || !confirmed.tags.every(tag => tags.includes(tag))) throw new Error('Invalid save acknowledgement')
        update({ status: 'closed', preferences: confirmed, draftTags: [...confirmed.tags], error: '' })
        // Keep a local refresh-initiation failure outside the save/validation state.
        try { onSaved(confirmed) } catch {
          update({ status: 'saved_refresh_failed', error: '偏好已保存，推荐更新失败。关闭面板重新读取即可，无需重复保存。' })
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
      const request = start()
      request.promise = Promise.resolve().then(() => request.current() ? client.preferences(request.controller.signal) : null).then(raw => {
        if (!request.current()) return null
        const remote = validateRecommendationPreferences(raw)
        update({ status: 'conflict_review', remotePreferences: remote, error: '' })
        return remote
      }).catch(() => {
        if (request.current()) update({ status: 'conflict', error: '重新读取失败，原草稿仍保留，请稍后重试。' })
        return null
      }).finally(() => { if (pending === request) pending = null })
      update({ status: 'reloading', error: '' })
      return request.promise
    },
    resolveConflict(merge) {
      if (disposed || state.status !== 'conflict_review') return
      const remote = state.remotePreferences
      const draft = merge ? mergePreferenceDraft(state.preferences.tags, state.draftTags, remote.tags) : [...remote.tags]
      if (draft.length > MAX_TASTE_TAGS || !draft.every(tag => remote.available_tags.includes(tag))) {
        update({ error: '合并后题材超出上限或词表已变化。请采用最新设置后重新选择；原草稿仍保留。' }); return
      }
      update({ status: 'editing', preferences: remote, draftTags: draft, remotePreferences: null,
        error: '', message: merge ? '已合并到草稿，尚未保存。请检查后再点击保存喜好。' : '已读取最新设置，本地旧草稿已放弃。' })
    },
    dispose() { disposed = true; stop(); state = { status: 'closed', draftTags: [] } },
  }
}
