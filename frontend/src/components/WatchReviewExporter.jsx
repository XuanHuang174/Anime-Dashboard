import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import api from '../api/api.js'
import { seriesSession } from '../hooks/useSeriesSession.js'
import RecommendationDialog from './recommendations/RecommendationDialog.jsx'
import { loadReviewExport, verifyReviewExport, reviewExportBlocks, exportFilename, pngArchive } from '../utils/watchReviewExport.js'
import { reviewTitle, currentReviewMonth } from '../utils/watchReview.js'
import { safeExternalUrl } from '../utils/journalSourceLinks.js'
import './WatchReviewExporter.css'

const frames = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
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
// Preview scales only its own sheet. The independent export stage stays 900px.
function ReviewPreview(props) {
  const viewport = useRef(null), paper = useRef(null)
  const [original, setOriginal] = useState(false), [size, setSize] = useState({ width: 0, height: 0 })
  useLayoutEffect(() => {
    const measure = () => setSize(old => {
      const next = { width: viewport.current.clientWidth, height: paper.current.scrollHeight }
      return old.width === next.width && old.height === next.height ? old : next
    })
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(viewport.current); observer.observe(paper.current)
    return () => observer.disconnect()
  }, [])
  const scale = original ? 1 : Math.min(1, size.width / 900 || 1)
  return <><div className="review-export-zoom" role="group" aria-label="预览缩放">
    <button type="button" className="rec-button" aria-pressed={!original} onClick={() => setOriginal(false)}>适应宽度</button>
    <button type="button" className="rec-button" aria-pressed={original} onClick={() => setOriginal(true)}>原尺寸查看</button>
    <small>预览缩放不影响导出清晰度。</small>
  </div><div ref={viewport} className={`review-export-preview${original ? ' is-original' : ''}`}>
    <div className="review-export-preview-page" style={{ width: original ? 900 : '100%', height: size.height * scale }}>
      <div ref={paper} className="review-export-preview-paper" style={{ transform: `scale(${scale})` }}><Sheet {...props}/></div>
    </div>
  </div></>
}
export default function WatchReviewExporter({ source, onClose }) {
  const [selection,setSelection]=useState({summary:true,months:source.period.mode === 'year',works:true,completed:false,notes:false,covers:false})
  const [report,setReport]=useState(null),[pages,setPages]=useState([]),[covers,setCovers]=useState({}),[page,setPage]=useState(0)
  const [prepared,setPrepared] = useState(false)
  const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('')
  const stage=useRef(null), job=useRef(null), serial=useRef(0), owner=useRef(seriesSession()), mounted=useRef(true), gate=useRef(false)
  const current=()=>mounted.current && owner.current===seriesSession()
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;job.current?.abort();serial.current++}},[])
  const close=()=>{job.current?.abort();serial.current++;onClose()}
  function change(key,value){setSelection(s=>({...s,[key]:value}));setPrepared(false);setReport(null);setPages([]);setError('');setMessage('')}
  async function measurePages(groups, live) {
    for (let attempt = 0; attempt < 12; attempt++) {
      if (!live()) throw new Error('导出已取消。')
      if (groups.length > 100) throw new Error('图片超过 100 页，请缩短区间或减少导出内容。')
      const sheets = [...stage.current.querySelectorAll('.review-export-sheet')]
      if (sheets.length !== groups.length) throw new Error('预览布局未完成，请重新读取。')
      if (sheets.every(sheet => sheet.scrollHeight <= 5000)) return groups
      const split = []
      sheets.forEach((sheet, index) => {
        const group = groups[index]
        if (sheet.scrollHeight > 5000) {
          if (group.length <= 1) throw new Error('单段内容超出图片尺寸，请减少导出内容。')
          const middle = Math.ceil(group.length / 2)
          split.push(group.slice(0, middle), group.slice(middle))
        } else split.push(group)
      })
      groups = split; setPages(groups); await frames()
    }
    throw new Error('预览布局超出图片尺寸，请减少导出内容。')
  }
  async function prepare(){
    if(gate.current || !current())return
    gate.current=true;setBusy(true);setPrepared(false);setReport(null);setPages([]);setError('');setMessage('正在读取所选分区的全部分页…')
    const controller=new AbortController(), run=++serial.current;job.current=controller
    const live=()=>current()&&run===serial.current&&!controller.signal.aborted
    try{
      const data=await loadReviewExport(api,source,selection,controller.signal,live)
      const blocks=reviewExportBlocks(data,selection), grouped=[]
      for(let i=0;i<blocks.length;i+=8)grouped.push(blocks.slice(i,i+8))
      const images={}
      if(selection.covers){
        const sources=[...new Set(blocks.map(b=>b.cover).filter(safeExternalUrl))].slice(0,24)
        let next=0
        await Promise.all([0,1].map(async()=>{while(next<sources.length){const url=sources[next++];try{
          const response=await api.get('utils/image-proxy/',{params:{url},signal:controller.signal,timeout:15000})
          if(live()&&response.data?.data_url?.length<=1500000&&/^data:image\/(png|jpeg|webp|gif);base64,/i.test(response.data?.data_url||'')) {
            const probe = new Image(); probe.src = response.data.data_url
            await probe.decode()
            if(live())images[url]=response.data.data_url
          }
        }catch{/* A failed cover has a visible placeholder, never a missing work. */}if(!live())return}}))
      }
      if(!live())return
      const missing=selection.covers?new Set(blocks.map(b=>b.cover).filter(Boolean)).size-Object.keys(images).length:0
      const initial = grouped.length ? grouped : [[]]
      setCovers(images);setReport(data);setPages(initial);setPage(0);setMessage('正在核对长图尺寸与分页…')
      await frames()
      await measurePages(initial, live)
      if (!live()) return
      await verifyReviewExport(api, data, controller.signal, live)
      if (!live()) return
      setPrepared(true);setMessage(`已读取完整内容，请核对预览。长内容会分成多张长图，并打包为一个 ZIP 下载。${missing?` ${missing} 张海报使用占位，作品文字完整保留。`:''}`)
    }catch(e){if(live()){setReport(null);setPages([]);setPrepared(false);setError(e.response?.status===409?'回顾资料已变化，请关闭面板、刷新回顾后重新预览。':e.message||'预览未完成，请重试。')}}
    finally{gate.current=false;if(live())setBusy(false)}
  }
  async function download(){
    if(gate.current||!report||!prepared||!current())return
    gate.current=true;setBusy(true);setError('');setMessage('正在生成长图，取消或关闭面板可停止下载。')
    const controller=new AbortController(), run=++serial.current;job.current=controller
    const live=()=>current()&&run===serial.current&&!controller.signal.aborted
    try{
      await verifyReviewExport(api,report,controller.signal,live)
      await frames()
      // The preview is already paginated. Never silently change its reviewed pages.
      if (pages.length > 100 || [...stage.current.querySelectorAll('.review-export-sheet')].some(sheet => sheet.scrollHeight > 5000)) throw new Error('图片布局已变化，请重新读取并预览。')
      const {default:html2canvas}=await import('html2canvas')
      const files=[]
      for(const [index,sheet]of [...stage.current.querySelectorAll('.review-export-sheet')].entries()){
        if(!live())return
        if(sheet.scrollHeight>5000)throw new Error('图片尺寸过大，未生成截断报告。')
        await Promise.all([...sheet.querySelectorAll('img')].map(img=>img.decode().catch(()=>unavailableCover(img))))
        const canvas=await html2canvas(sheet,{backgroundColor:'#fff5ee',scale:1.5,allowTaint:false,useCORS:false,logging:false,width:900,height:sheet.scrollHeight,windowWidth:1100})
        if(!live()){canvas.width=1;canvas.height=1;return}
        const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'))
        canvas.width=1;canvas.height=1
        if(!blob)throw new Error('浏览器未能生成图片，请减少内容后重试。')
        files.push({name:`${exportFilename(report)}_${String(index+1).padStart(2,'0')}.png`,blob})
      }
      await verifyReviewExport(api,report,controller.signal,live)
      const blob=files.length===1?files[0].blob:await pngArchive(files)
      if(!live())return
      const url=URL.createObjectURL(blob),link=document.createElement('a')
      link.href=url;link.download=files.length===1?files[0].name:`${exportFilename(report)}.zip`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
      setMessage(`已生成 ${files.length} 张完整长图并开始下载。仅发起本机下载，不会自动公开。`)
    }catch(e){if(live())setError(e.response?.status===409?'回顾资料已变化，未下载旧报告。请关闭面板并刷新后重新预览。':e.message||'导出失败，请重试。')}
    finally{gate.current=false;if(live())setBusy(false)}
  }
  return <div className="memphis-rec-container"><RecommendationDialog title="导出观看回顾" onClose={close} kind="review-export" footer={<div className="review-export-actions"><button className="rec-button" disabled={busy||!['summary','months','works','completed','notes'].some(k=>selection[k])} onClick={prepare}>读取并预览完整内容</button>{report&&prepared&&<button className="rec-button rec-button--primary" disabled={busy} onClick={download}>下载回顾长图</button>}{busy&&<button className="rec-button" onClick={close}>取消并关闭</button>}</div>}>
    <div className="review-export-options"><p>导出当前已应用{source.period.mode === 'year' ? '年份' : '区间'}的完整所选内容，不限当前页。图片不会自动发布；随记与轮次备注默认不导出。</p>
      <fieldset disabled={busy}><legend>选择导出内容</legend>{[['summary','统计摘要'],...(source.period.mode === 'year' ? [['months','十二个月分布／月份待补充说明']] : []),['works','完整作品清单'],['completed','完成轮次'],['notes','随记与补录／轮次备注'],['covers','包含海报（最多24张，失败或超限显示占位）']].map(([key,label])=><label key={key}><input type="checkbox" checked={selection[key]} onChange={e=>change(key,e.target.checked)}/>{label}</label>)}</fieldset>
      {selection.notes&&<p className="review-export-private">所选文件将包含私人文字，下载后请自行决定是否分享。</p>}

      {message&&<p role="status">{message}</p>}{error&&<p role="alert">{error}</p>}
      {report&&prepared&&<><div className="review-export-pagination"><button className="rec-button" disabled={busy||page===0} onClick={()=>setPage(n=>n-1)}>上一张预览</button><span>{page+1} / {pages.length}</span><button className="rec-button" disabled={busy||page>=pages.length-1} onClick={()=>setPage(n=>n+1)}>下一张预览</button></div><ReviewPreview report={report} blocks={pages[Math.min(page,pages.length-1)]} index={page} count={pages.length} covers={covers}/></>}
    </div>
    {report&&createPortal(<div className="review-export-stage" aria-hidden="true" ref={stage}>{pages.map((blocks,index)=><Sheet key={index} report={report} blocks={blocks} index={index} count={pages.length} covers={covers}/>)}</div>,document.body)}
  </RecommendationDialog></div>
}
