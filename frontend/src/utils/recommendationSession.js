// Cache-lifetime hint ONLY, never authorization. Server still checks request.user.
// Normal refreshes keep the same subject; a different account discards all state.
export function recommendationSessionScope(token) {
  if (!token) return 'anonymous'
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    if (/^[1-9]\d*$/.test(String(payload.user_id))) return `subject:${payload.user_id}`
  } catch { /* Opaque synthetic/test session: compare token only in memory. */ }
  return `opaque:${token}`
}
