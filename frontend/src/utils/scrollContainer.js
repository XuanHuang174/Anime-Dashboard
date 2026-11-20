// Match the CSS scroll owner, even before async content makes it overflow.
export function scrollContainer(root) {
  for (let node = root; node && node !== document.body; node = node.parentElement) {
    if (/^(auto|scroll)$/.test(getComputedStyle(node).overflowY)) return node
  }
  return window
}
