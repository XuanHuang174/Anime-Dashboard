import test from 'node:test'
import assert from 'node:assert/strict'
import { buildJournalCsv, csvCell, journalCsvFilename } from '../src/utils/journalCsv.js'

test('CSV 带 BOM、中文列名与 Excel CRLF 换行', () => {
  const csv = buildJournalCsv([{ title: '孤独摇滚', japaneseTitle: 'ぼっち', period: '2022-10', studio: 'CloverWorks',
    score: 9.6, status: 'completed', episodes: '12', tags: ['音乐', '日常'], review: '很喜欢' }])
  assert.ok(csv.startsWith('\uFEFF"番剧名称","译名/原名"'))
  assert.ok(csv.includes('"9.6","看过","12","音乐、日常","很喜欢"'))
  assert.ok(csv.endsWith('\r\n'))
  assert.equal(Buffer.from(csv).subarray(0, 3).toString('hex'), 'efbbbf')
})
test('CSV 正确转义逗号、双引号和完整多行长评', () => {
  assert.equal(csvCell('标题,"特典"\n第二行'), '"标题,""特典""\n第二行"')
  const review = ('长评, "内容"\r\n').repeat(100)
  assert.ok(buildJournalCsv([{ review }]).includes(csvCell(review)))
})
test('CSV 阻止 Excel 公式注入，正常文本不改变', () => {
  for (const text of ['=HYPERLINK("evil")', '+CMD', '-CMD', '@SUM(A1)', ' \t=SUM(A1)']) {
    assert.ok(csvCell(text).startsWith('"\''))
  }
  assert.equal(csvCell('中文 + 日常'), '"中文 + 日常"')
})
test('只导出调用方传入的筛选结果，不追加未选中记录', () => {
  const all = [{ title: '在看番', status: 'watching', totalEps: 12 }, { title: '未选中', status: 'completed' }]
  const csv = buildJournalCsv(all.filter((item) => item.status === 'watching'))
  assert.ok(csv.includes('"在看","12"'))
  assert.ok(!csv.includes('未选中'))
  assert.equal(buildJournalCsv([]).split('\r\n').length, 2)
})
test('下载文件名使用本地日期 YYYYMMDD', () => {
  assert.equal(journalCsvFilename(new Date(2026, 0, 2)), '我的番剧手账_20260102.csv')
})
