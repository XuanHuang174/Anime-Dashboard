import { useId } from 'react'
import { formatWatchDate } from '../utils/watchHistory.js'

const METRICS = { completed_rounds: '完成轮次', started_rounds: '开始轮次', rewatch_completed_rounds: '重看完成轮次' }

export default function AnnualReviewDistribution({ annual, metric, onMetric, onMonth, onUnknown, detailsOpen, onDetailsOpen }) {
  const titleId = useId(), maximum = Math.max(1, ...annual.months.map(row => row.summary[metric]))
  const unknown = annual.month_unknown
  return <section className="review-annual" aria-labelledby={titleId}>
    <div className="review-annual-heading"><div><h3 id={titleId}>全年月份分布</h3><p>按实际日期归属 · 单位为轮次</p></div>
      <div className="review-annual-metrics" role="group" aria-label="月份分布指标">{Object.entries(METRICS).map(([key, label]) =>
        <button type="button" key={key} aria-pressed={metric === key} onClick={() => onMetric(key)}>{label}</button>)}</div>
    </div>
    <div className="review-month-chart" role="group" aria-label={`各月${METRICS[metric]}`}>{annual.months.map(row =>
      <button type="button" key={row.month} data-review-month={row.month} className="review-month-column"
        aria-label={`查看${formatWatchDate(row.month)}回顾：${METRICS[metric]} ${row.summary[metric]} 轮`} onClick={() => onMonth(row.month)}>
        <span className="review-month-value">{row.summary[metric]}<small>轮</small></span>
        <span className="review-month-track" aria-hidden="true"><span className="review-month-bar" style={{ '--review-month-size': `${row.summary[metric] / maximum * 100}%` }} /></span>
        <span className="review-month-label">{Number(row.month.slice(5))}月</span>
      </button>)}</div>
    <p className="review-annual-note">点击月份查看月度回顾。0 表示没有可归属的已记录资料，不代表当月没有观看；同一作品可出现在多个月，年度作品数不按各月相加。</p>
    <div className="review-month-unknown"><div><strong>已确定属于本年，月份待补充</strong><p>{unknown.facts} 条记录 · 涉及 {unknown.works} 部作品 · 已计入年度总览，未分配到具体月份。</p></div>
      <button type="button" onClick={onUnknown}>查看本年月份待补充</button></div>
    <details className="review-month-details" open={detailsOpen} onToggle={event => onDetailsOpen(event.currentTarget.open)}><summary>月份明细</summary><ul>{annual.months.map(row => <li key={row.month}>
      <button type="button" data-review-month-detail={row.month} onClick={() => onMonth(row.month, 'details')} aria-label={`月份明细：查看${formatWatchDate(row.month)}回顾`}>{Number(row.month.slice(5))}月 →</button>
      <dl><div><dt>开始观看</dt><dd>{row.summary.started_works} 部 / {row.summary.started_rounds} 轮</dd></div>
        <div><dt>完成作品</dt><dd>{row.summary.completed_works} 部 / {row.summary.completed_rounds} 轮</dd></div>
        <div><dt>重看完成</dt><dd>{row.summary.rewatch_completed_rounds} 轮</dd></div>
        <div><dt>随记与补录</dt><dd>{row.summary.notes} 条</dd></div></dl>
    </li>)}</ul></details>
  </section>
}
