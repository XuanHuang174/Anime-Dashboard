export function retryAfterMs(error, fallbackMs = 30000) {
  const headers = error?.response?.headers
  const raw = headers?.get?.('retry-after') ?? headers?.['retry-after']
  const seconds = error?.response?.data?.retry_after ?? raw
  if (seconds !== undefined && seconds !== null && String(seconds).trim() !== '') {
    const numeric = Number(seconds)
    if (Number.isFinite(numeric)) return numeric >= 0 ? Math.ceil(numeric * 1000) : fallbackMs
  }
  const date = Date.parse(raw)
  const serverDate = Date.parse(headers?.get?.('date') ?? headers?.date)
  if (Number.isFinite(date)) return Math.max(0, date - (Number.isFinite(serverDate) ? serverDate : Date.now()))
  return fallbackMs
}
