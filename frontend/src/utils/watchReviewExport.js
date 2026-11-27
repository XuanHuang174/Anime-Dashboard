import { validReviewEntries, validReviewSummary, sameReviewCohort, reviewTitle, REVIEW_KINDS } from './watchReview.js'
import { formatWatchDate } from './watchHistory.js'

export const EXPORT_LIMIT = 500
export async function loadReviewExport(client, source, selection, signal, current = () => true) {
  const params = source.period.mode === 'year' ? { year: source.period.start.slice(0, 4) }
    : source.period.mode === 'month' ? { month: source.period.start.slice(0, 7) } : { start: source.period.start, end: source.period.end }
  if (!validReviewSummary(source, params)) throw new Error('导出依据不完整，请刷新回顾后重新预览。')
  const result = { source, params, sections: {} }
  let total = 0
  for (const section of ['works', 'completed', 'notes'].filter(key => selection[key])) {
    const rows = []
    let page = 1
    do {
      if (signal.aborted || !current()) throw new Error('导出已取消。')
      const { data } = await client.get('watch-review/entries/', { params: { ...params, section, page, page_size: 100, snapshot: source.snapshot }, signal })
      if (signal.aborted || !current()) throw new Error('导出已取消。')
      if (!validReviewEntries(data, { params, section, page, pageSize: 100, snapshot: source.snapshot, baseline: source })) throw new Error('导出资料不完整，请重新读取回顾。')
      if (total + data.count > EXPORT_LIMIT) throw new Error(`所选内容超过 ${EXPORT_LIMIT} 条，请缩短区间或减少导出分区；没有截断导出。`)
      rows.push(...data.results)
      if (!data.next_page) break
      page = data.next_page
    } while (true)
    total += rows.length
    const ids = rows.map(row => section === 'works' ? row.work.record_id : row.id)
    if (new Set(ids).size !== ids.length) throw new Error('导出分页有重复资料，请刷新后重试。')
    result.sections[section] = rows
  }
  await verifyReviewExport(client, result, signal, current)
  return result
}
export async function verifyReviewExport(client, report, signal, current) {
  if (signal.aborted || !current()) throw new Error('导出已取消。')
  const { data } = await client.get('watch-review/', { params: { ...report.params, snapshot: report.source.snapshot }, signal })
  if (signal.aborted || !current()) throw new Error('导出已取消。')
  if (!validReviewSummary(data, report.params) || !sameReviewCohort(data, report.source)) throw new Error('回顾资料已变化，请刷新后重新预览。')
}
export function reviewExportBlocks(report, selection) {
  const blocks = []
  if (selection.summary) {
    const s = report.source.summary
    blocks.push({ title: report.source.period.mode === 'year' ? '这一年的观看记录' : '这段时间的观看记录', text: `开始 ${s.started_works} 部／${s.started_rounds} 轮；完成 ${s.completed_works} 部／${s.completed_rounds} 轮；重看完成 ${s.rewatch_completed_rounds} 轮；随记与补录 ${s.notes} 条。\n完成轮次评分：${s.completed_round_rating.average ?? '未记录'}，${s.completed_round_rating.count} 轮已评分，${s.completed_round_rating.missing_count} 轮缺失。` })
  }
  if (report.source.period.mode === 'year' && selection.months) {
    const a = report.source.annual, u = a.month_unknown
    blocks.push({ kind: 'months', title: '十二个月的观看记录', months: a.months,
      text: '按明确的实际月份归属。0 表示没有可归属的已记录资料，不代表当月没有观看；同一作品可出现在多个月，年度作品数不按各月相加。' })
    blocks.push({ title: '属于本年，月份待补充', text: `${u.facts} 条记录，涉及 ${u.works} 部作品。已计入年度总览，未分配到具体月份。\n开始 ${u.summary.started_rounds} 轮；完成 ${u.summary.completed_rounds} 轮；重看完成 ${u.summary.rewatch_completed_rounds} 轮；随记与补录 ${u.summary.notes} 条。\n完全没有日期的资料来自本人全部手账，不能视为本年度遗漏。` })
  }
  for (const [section, title] of [['works','本期作品清单'],['completed','完成轮次'],['notes','随记与补录']]) {
    if (!selection[section]) continue
    const rows = report.sections[section] || []
    blocks.push({ title, text: `完整分区 · ${rows.length} ${section === 'works' ? '部作品' : '条记录'}` })
    for (const row of rows) {
      const heading = section === 'works' ? row.work.title : `${row.work.title} · 第 ${row.round_number} 轮${row.is_rewatch ? '重看' : ''}`
      const detail = section === 'works' ? Object.entries(row.matched_kinds).map(([key,n]) => `${REVIEW_KINDS[key]} ${n}`).join(' · ')
        : `${REVIEW_KINDS[row.kind]} · ${formatWatchDate(row.actual_date)}${row.date_precision === 'month' ? '（仅月份）' : row.date_precision === 'year' ? '（仅年份）' : ''}${section === 'completed' ? ` · 本轮评分 ${row.rating ?? '未记录'}` : ''}`
      // Private text is opt-in, including a round's note on completion cards.
      const note = selection.notes ? row.note || '' : ''
      const parts = note ? Array.from(note).reduce((chunks, char, index) => { if (index % 600 === 0) chunks.push(''); chunks[chunks.length - 1] += char; return chunks }, []) : ['']
      parts.forEach((part,index) => blocks.push({ title: `${heading}${index ? '（续）' : ''}`, text: `${index ? '' : detail}${part ? `\n${part}` : ''}`, cover: !index && selection.covers ? row.work.poster_url : '' }))
    }
  }
  return blocks
}
export const exportFilename = report => `${reviewTitle(report.source.period).replace(/[<>:"/\\|?*\u0000-\u001f]/g,'_')}${report.source.period.mode==='custom'?'_观看回顾':''}`

// ZIP STORE: one user download for multi-image reports, with no extra dependency.
export function crc32(bytes) {
  let crc = 0xffffffff
  for (const byte of bytes) { crc ^= byte; for (let n=0;n<8;n++) crc = (crc>>>1) ^ (crc&1 ? 0xedb88320 : 0) }
  return (crc ^ 0xffffffff) >>> 0
}
export async function pngArchive(files) {
  const chunks=[], directory=[]; let offset=0, directorySize=0
  for (const file of files) {
    const name=new TextEncoder().encode(file.name), bytes=new Uint8Array(await file.blob.arrayBuffer()), crc=crc32(bytes)
    const header=new Uint8Array(30+name.length), h=new DataView(header.buffer)
    h.setUint32(0,0x04034b50,true);h.setUint16(4,20,true);h.setUint16(6,0x800,true)
    h.setUint16(12,0x21,true);h.setUint32(14,crc,true);h.setUint32(18,bytes.length,true);h.setUint32(22,bytes.length,true);h.setUint16(26,name.length,true);header.set(name,30)
    const central=new Uint8Array(46+name.length), c=new DataView(central.buffer)
    c.setUint32(0,0x02014b50,true);c.setUint16(4,20,true);c.setUint16(6,20,true);c.setUint16(8,0x800,true)
    c.setUint16(14,0x21,true);c.setUint32(16,crc,true);c.setUint32(20,bytes.length,true);c.setUint32(24,bytes.length,true);c.setUint16(28,name.length,true);c.setUint32(42,offset,true);central.set(name,46)
    chunks.push(header,bytes);directory.push(central);offset+=header.length+bytes.length;directorySize+=central.length
  }
  const end=new Uint8Array(22), e=new DataView(end.buffer)
  e.setUint32(0,0x06054b50,true);e.setUint16(8,files.length,true);e.setUint16(10,files.length,true);e.setUint32(12,directorySize,true);e.setUint32(16,offset,true)
  return new Blob([...chunks,...directory,end],{type:'application/zip'})
}
