const STATUS_LABELS = { watching: '在看', completed: '看过', plan: '想看', on_hold: '搁置' }
export const CSV_COLUMNS = ['番剧名称', '译名/原名', '放送季度', '制作公司', '综合评分', '追番状态', '话数', '标签', '个人评价']

export function csvCell(value) {
  let text = String(value ?? '')
  // Quoting alone does not prevent Excel from interpreting a cell as a formula.
  if (/^[\s\u0000-\u001f]*[=+@-]/u.test(text)) text = `'${text}`
  return `"${text.replaceAll('"', '""')}"`
}

export function buildJournalCsv(items) {
  const rows = items.map((item) => [
    item.title, item.japaneseTitle, item.period, item.studio, item.score,
    STATUS_LABELS[item.status] || item.statusLabel || item.status,
    item.episodes || item.totalEps || '', (item.tags || []).join('、'), item.review,
  ])
  return '\uFEFF' + [CSV_COLUMNS, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n'
}

export function journalCsvFilename(date = new Date()) {
  const day = [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('')
  return `我的番剧手账_${day}.csv`
}

export function downloadJournalCsv(items) {
  const url = URL.createObjectURL(new Blob([buildJournalCsv(items)], { type: 'text/csv;charset=utf-8;' }))
  const link = document.createElement('a')
  link.href = url
  link.download = journalCsvFilename()
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Give the browser time to start the download before releasing the Blob.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
