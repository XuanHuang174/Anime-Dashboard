import api from '../api/api.js'
import PrivateImageExporter from './PrivateImageExporter.jsx'
import { loadReviewExport, verifyReviewExport, reviewExportBlocks, exportFilename } from '../utils/watchReviewExport.js'
import { reviewTitle, currentReviewMonth } from '../utils/watchReview.js'

function ExportMonths({ months }) {
  const maximum = Math.max(1, ...months.map(row => row.summary.completed_rounds))
  return <div className="review-export-months"><h3>每月完成轮次 · 单位：轮</h3>
    <div className="review-export-month-chart">{months.map(row => <div className="review-export-month" key={row.month}>
      <strong>{row.summary.completed_rounds}</strong><div className="review-export-month-track"><span style={{ height: `${row.summary.completed_rounds / maximum * 100}%` }} /></div><span>{Number(row.month.slice(5))}月</span>
    </div>)}</div>
    <table className="review-export-month-table"><caption>十二个月明细</caption><thead><tr><th>月份</th><th>开始观看</th><th>完成作品</th><th>重看完成</th><th>随记与补录</th></tr></thead>
      <tbody>{months.map(row => <tr key={row.month}><th scope="row">{Number(row.month.slice(5))}月</th><td>{row.summary.started_works} 部 / {row.summary.started_rounds} 轮</td><td>{row.summary.completed_works} 部 / {row.summary.completed_rounds} 轮</td><td>{row.summary.rewatch_completed_rounds} 轮</td><td>{row.summary.notes} 条</td></tr>)}</tbody>
    </table>
  </div>
}
function unavailableCover(img) {
  img.hidden = true
  if (img.nextElementSibling) img.nextElementSibling.hidden = false
}
function Sheet({ report, blocks, index, count, covers }) {
  const c = report.source.coverage, annual = report.source.annual
  const ongoing = annual?.year === currentReviewMonth(new Date(report.source.generated_at)).slice(0, 4)
  return <article className="review-export-sheet"><header><span>WATCH REVIEW · 私人观看回顾</span><h1>{reviewTitle(report.source.period)}</h1>{ongoing && <strong className="review-export-ongoing">年度进行中 · 依据已记录历程</strong>}<p>依据当前有效历程 · {report.source.period.start} 至 {report.source.period.end}（含首尾）</p></header>
    {blocks.map((block,i)=><section key={i}>{block.cover && <div className="review-export-cover">{covers[block.cover] ? <><img src={covers[block.cover]} alt="" onError={event => unavailableCover(event.currentTarget)}/><span hidden>海报未载入</span></> : <span>海报未载入</span>}</div>}<h2>{block.title}</h2><p>{block.text}</p>{block.kind === 'months' && <ExportMonths months={block.months}/>}</section>)}
    <footer>{annual && <>属于本年但月份待补充 {annual.month_unknown.facts} 条，已计入年度统计，未分配到具体月份。<br/></>}部分相交 {c.uncertain.facts} 条、日期未知 {c.undated.facts} 条均未计入确定统计。未知日期来自本人全部历程。<br/>名称与海报为当前手账资料；轮次评分不以当前综合评分补缺。<br/>本页为本次所选内容 · {index+1} / {count} 页 · 读取依据时间 {new Date(report.source.generated_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}</footer></article>
}
export default function WatchReviewExporter({ source, onClose }) {
  const annual = source.period.mode === 'year'
  return <PrivateImageExporter source={source} onClose={onClose} title="导出观看回顾" downloadLabel="下载回顾长图"
    defaults={{summary:true,months:annual,works:true,completed:false,notes:false,covers:false}}
    options={[["summary","统计摘要"],...(annual ? [["months","十二个月分布／月份待补充说明"]] : []),["works","完整作品清单"],["completed","完成轮次"],["notes","随记与补录／轮次备注"],["covers","包含海报（最多24张，失败或超限显示占位）"]]}
    load={(source,selection,signal,live)=>loadReviewExport(api,source,selection,signal,live)} verify={(report,signal,live)=>verifyReviewExport(api,report,signal,live)} blocks={reviewExportBlocks} Sheet={Sheet} filename={exportFilename}
    hint={`导出当前已应用${annual?'年份':'区间'}的完整所选内容，不限当前页。图片不会自动发布；随记与轮次备注默认不导出。`}/>
}
