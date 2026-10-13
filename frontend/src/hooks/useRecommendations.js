import { useEffect, useRef, useState } from 'react'
import recommendationApi from '../api/recommendations.js'
import { createLatestRequestGate, mapRecommendationPage, mergeRecommendationPages, recommendationError, recommendationResultState } from '../utils/recommendations.js'
import { loadRecommendationSnapshot } from '../utils/recommendationSnapshot.js'

export default function useRecommendations(category, filters, client = recommendationApi) {
  const root = useRef(createLatestRequestGate())
  const more = useRef(createLatestRequestGate())
  const moreBusy = useRef(false)
  const confirmed = useRef(null)
  const [revision, setRevision] = useState(0)
  const [state, setState] = useState({ status: 'loading', items: [] })
  const key = JSON.stringify([category, filters.type, filters.minScore, filters.hideRecorded, revision])
  useEffect(() => {
    const request = root.current.begin()
    more.current.cancel()
    moreBusy.current = false
    const confirmedPreferences = confirmed.current
    setState({ key, status: 'loading', items: [], confirmedPreferences })
    loadRecommendationSnapshot(client, { category, filters, signal: request.signal, confirmedPreferences }).then(snapshot => {
      if (request.isCurrent()) {
        confirmed.current = null
        setState({ key, status: recommendationResultState(snapshot.data), ...snapshot, moreLoading: false, moreError: '' })
      }
    }).catch((error) => {
      if (request.isCurrent()) {
        root.current.cancel()
        setState({ key, status: 'error', items: [], confirmedPreferences, error: recommendationError(error) })
      }
    })
    return () => { root.current.cancel(); more.current.cancel() }
    // key captures all query primitives. No fetch is triggered by dialog draft changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, client])

  const retry = ({ confirmedPreferences } = {}) => {
    if (confirmedPreferences) confirmed.current = confirmedPreferences
    root.current.cancel(); more.current.cancel(); moreBusy.current = false
    setRevision((value) => value + 1)
  }
  const recordChanged = (identity, watchStatus) => {
    more.current.cancel(); moreBusy.current = false
    setState((current) => ({ ...current, snapshotDirty: true, moreLoading: false, moreError: '',
      items: current.items.map((item) => item.bangumiId === identity ? { ...item, watchStatus } : item) }))
  }
  const loadMore = async () => {
    if (state.key !== key || !state.data?.next_page || moreBusy.current) return
    if (state.snapshotDirty) { retry(); return }
    moreBusy.current = true
    const request = more.current.begin()
    const captured = state.data.state_token
    setState((current) => ({ ...current, moreLoading: true, moreError: '' }))
    try {
      const data = mapRecommendationPage(await client.list({ category, filters, page: state.data.next_page, stateToken: captured }, request.signal))
      if (data.state_token !== captured) { if (request.isCurrent()) retry(); return }
      if (request.isCurrent()) setState((current) => current.key !== key ? current : {
        ...current, data, items: mergeRecommendationPages(current.items, data.items), moreLoading: false })
    } catch (error) {
      if (request.isCurrent()) {
        if (error?.response?.status === 409) retry()
        else setState((current) => current.key !== key ? current : { ...current, moreLoading: false, moreError: recommendationError(error) })
      }
    } finally { if (request.isCurrent()) moreBusy.current = false }
  }
  return { ...(state.key === key ? state : { status: 'loading', items: [], confirmedPreferences: confirmed.current }), retry, loadMore, recordChanged }
}
