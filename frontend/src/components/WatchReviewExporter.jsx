import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import api from '../api/api.js'
import { seriesSession } from '../hooks/useSeriesSession.js'
import RecommendationDialog from './recommendations/RecommendationDialog.jsx'
import { loadReviewExport, verifyReviewExport, reviewExportBlocks, exportFilename, pngArchive } from '../utils/watchReviewExport.js'
import { reviewTitle } from '../utils/watchReview.js'
import { safeExternalUrl } from '../utils/journalSourceLinks.js'
import './WatchReviewExporter.css'

const frames = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
function Sheet({ report, blocks, index, count, covers }) {
  const c=report.source.coverage
  return <article className="review-export-sheet"><header><span>WATCH REVIEW · 私人观看回顾</span><h1>{reviewTitle(report.source.period)}</h1><p>依据当前有效历程 · {report.source.period.start} 至 {report.source.period.end}（含首尾）</p></header>
    {blocks.map((block,i)=><section key={i}>{block.cover && <div className="review-export-cover">{covers[block.cover] ? <img src={covers[block.cover]} alt=""/> : <span>海报未载入</span>}</div>}<h2>{block.title}</h2><p>{block.text}</p></section>)}
    <footer>部分相交 {c.uncertain.facts} 条、日期未知 {c.undated.facts} 条均未计入确定统计。未知日期来自本人全部历程。<br/>名称与海报为当前手账资料；轮次评分不以当前综合评分补缺。<br/>本页为本次所选内容 · {index+1} / {count} 页 · 读取依据时间 {new Date(report.source.generated_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}</footer></article>
}
export default function WatchReviewExporter({ source, onClose }) {
  const [selection,setSelection]=useState({summary:true,works:true,completed:false,notes:false,covers:false})
  const [report,setReport]=useState(null),[pages,setPages]=useState([]),[covers,setCovers]=useState({}),[page,setPage]=useState(0)
  const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('')
  const stage=useRef(null), job=useRef(null), serial=useRef(0), owner=useRef(seriesSession()), mounted=useRef(true), gate=useRef(false)
  const current=()=>mounted.current && owner.current===seriesSession()
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;job.current?.abort();serial.current++}},[])
  const close=()=>{job.current?.abort();serial.current++;onClose()}
  function change(key,value){setSelection(s=>({...s,[key]:value}));setReport(null);setPages([]);setError('');setMessage('')}
  async function prepare(){
    if(gate.current || !current())return
    gate.current=true;setBusy(true);setError('');setMessage('正在读取所选分区的全部分页…')
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
          if(live()&&response.data?.data_url?.length<=1500000&&/^data:image\/(png|jpeg|webp|gif);base64,/i.test(response.data?.data_url||''))images[url]=response.data.data_url
        }catch{/* A failed cover has a visible placeholder, never a missing work. */}if(!live())return}}))
      }
      if(!live())return
      const missing=selection.covers?new Set(blocks.map(b=>b.cover).filter(Boolean)).size-Object.keys(images).length:0
      setCovers(images);setReport(data);setPages(grouped.length?grouped:[[]]);setPage(0);setMessage(`已读取完整内容，请核对预览。长内容会分成多张长图，并打包为一个 ZIP 下载。${missing?` ${missing} 张海报使用占位，作品文字完整保留。`:''}`)
    }catch(e){if(live())setError(e.response?.status===409?'回顾资料已变化，请关闭面板、刷新回顾后重新预览。':e.message||'预览未完成，请重试。')}
    finally{gate.current=false;if(live())setBusy(false)}
  }
  async function download(){
    if(gate.current||!report||!current())return
    gate.current=true;setBusy(true);setError('');setMessage('正在生成长图，取消或关闭面板可停止下载。')
    const controller=new AbortController(), run=++serial.current;job.current=controller
    const live=()=>current()&&run===serial.current&&!controller.signal.aborted
    try{
      await verifyReviewExport(api,report,controller.signal,live)
      await frames()
      // Split by measured layout; never crop or silently shorten an oversized sheet.
      let groups=pages
      for(let attempt=0;attempt<12;attempt++){
        const sheets=[...stage.current.querySelectorAll('.review-export-sheet')]
        if(sheets.every(s=>s.scrollHeight<=5000))break
        const split=[]
        sheets.forEach((sheet,index)=>{const group=groups[index];if(sheet.scrollHeight>5000){if(group.length<=1)throw new Error('单段内容超出图片尺寸，请减少导出内容。');const middle=Math.ceil(group.length/2);split.push(group.slice(0,middle),group.slice(middle))}else split.push(group)})
        groups=split;setPages(groups);await frames()
      }
      if(groups.length>100)throw new Error('图片超过 100 页，请缩短区间或减少导出内容。')
      const {default:html2canvas}=await import('html2canvas')
      const files=[]
      for(const [index,sheet]of [...stage.current.querySelectorAll('.review-export-sheet')].entries()){
        if(!live())return
        if(sheet.scrollHeight>5000)throw new Error('图片尺寸过大，未生成截断报告。')
        await Promise.all([...sheet.querySelectorAll('img')].map(img=>img.decode().catch(()=>{img.style.display='none'})))
        const canvas=await html2canvas(sheet,{backgroundColor:'#fff5ee',scale:1.5,allowTaint:false,useCORS:false,logging:false,width:900,height:sheet.scrollHeight,windowWidth:1100})
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
  return <div className="memphis-rec-container"><RecommendationDialog title="导出观看回顾" onClose={close} kind="review-export">
    <div className="review-export-options"><p>导出当前已应用区间的完整所选内容，不限当前页。图片不会自动发布；随记与轮次备注默认不导出。</p>
      <fieldset disabled={busy}><legend>选择导出内容</legend>{[['summary','统计摘要'],['works','完整作品清单'],['completed','完成轮次'],['notes','随记与补录／轮次备注'],['covers','包含海报（最多24张，失败或超限显示占位）']].map(([key,label])=><label key={key}><input type="checkbox" checked={selection[key]} onChange={e=>change(key,e.target.checked)}/>{label}</label>)}</fieldset>
      {selection.notes&&<p className="review-export-private">所选文件将包含私人文字，下载后请自行决定是否分享。</p>}
      <div className="review-export-actions"><button className="rec-button" disabled={busy||!['summary','works','completed','notes'].some(k=>selection[k])} onClick={prepare}>读取并预览完整内容</button>{report&&<button className="rec-button rec-button--primary" disabled={busy} onClick={download}>下载回顾长图</button>}{busy&&<button className="rec-button" onClick={close}>取消并关闭</button>}</div>
      {message&&<p role="status">{message}</p>}{error&&<p role="alert">{error}</p>}
      {report&&<><div className="review-export-pagination"><button className="rec-button" disabled={busy||page===0} onClick={()=>setPage(n=>n-1)}>上一张预览</button><span>{page+1} / {pages.length}</span><button className="rec-button" disabled={busy||page>=pages.length-1} onClick={()=>setPage(n=>n+1)}>下一张预览</button></div><div className="review-export-preview"><Sheet report={report} blocks={pages[Math.min(page,pages.length-1)]} index={page} count={pages.length} covers={covers}/></div></>}
    </div>
    {report&&createPortal(<div className="review-export-stage" aria-hidden="true" ref={stage}>{pages.map((blocks,index)=><Sheet key={index} report={report} blocks={blocks} index={index} count={pages.length} covers={covers}/>)}</div>,document.body)}
  </RecommendationDialog></div>
}
