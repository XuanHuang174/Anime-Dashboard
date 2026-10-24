import { useEffect, useState } from 'react'
import { validateExplorationCapabilities, validateExplorationContract } from '../utils/recommendationExploration.js'

// Exact standard names only. Private/custom labels are never promoted by guessing.
export default function useExplorationTopics(client, enabled) {
  const [state, setState] = useState({ client: null, tags: [] })
  useEffect(() => {
    const request = new AbortController()
    if (enabled === false) return () => request.abort()
    async function read() {
      try {
        const available = enabled ?? validateExplorationCapabilities((await client.capabilities(request.signal)).exploration).navigation[2].available
        if (!available || request.signal.aborted) return
        const contract = validateExplorationContract(await client.explorationContract(request.signal))
        if (!request.signal.aborted) setState({ client, tags: contract.available_tags })
      } catch { /* A missing capability keeps ordinary journal labels readable. */ }
    }
    read()
    return () => request.abort()
  }, [client, enabled])
  return enabled !== false && state.client === client ? state.tags : []
}
