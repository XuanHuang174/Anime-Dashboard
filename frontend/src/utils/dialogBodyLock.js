const locks = new WeakMap()
// Native series/details can unmount together on browser Back, in either cleanup order.
export function lockDialogBody(document) {
  const body = document.body
  let entry = locks.get(body)
  if (!entry) { entry = { count: 0, overflow: body.style.overflow }; locks.set(body, entry) }
  entry.count++; body.style.overflow = 'hidden'
  let released = false
  return () => {
    if (released) return
    released = true
    if (--entry.count === 0) { body.style.overflow = entry.overflow; locks.delete(body) }
  }
}
