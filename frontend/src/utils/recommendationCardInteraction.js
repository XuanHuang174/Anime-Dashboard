// Interactive children retain their own action; selecting text never opens a dialog.
export function openCardDetail(event, item, open, disabled = false) {
  if (disabled || !open || event.defaultPrevented || event.button !== 0
    || event.target.closest('button, a, input, select, textarea, summary, details, label, [role="button"], [role="checkbox"], [contenteditable="true"]')
    || window.getSelection()?.toString()) return
  open(item)
}
