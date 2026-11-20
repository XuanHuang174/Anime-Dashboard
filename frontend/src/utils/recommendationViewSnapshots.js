// Owned by one mounted/account-keyed host. No storage, global state or API data copies.
export function createViewSnapshots(limit = 12) {
  const entries = new Map(), last = {}
  return {
    entry(mode, path, scope) {
      const key = JSON.stringify([mode, path, scope])
      const cache = entries.get(key) || {}
      entries.delete(key); entries.set(key, cache)
      last[mode] = { path, cache }
      while (entries.size > limit) {
        const oldest = entries.keys().next().value, removed = entries.get(oldest)
        entries.delete(oldest); removed.controller?.dispose()
        for (const mode of Object.keys(last)) if (last[mode].cache === removed) delete last[mode]
      }
      return cache
    },
    last: mode => last[mode]?.path,
  }
}
