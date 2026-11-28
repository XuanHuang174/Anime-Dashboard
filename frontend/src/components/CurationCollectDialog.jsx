import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import api from '../api/api.js'
import useSeriesSession, { seriesSession } from '../hooks/useSeriesSession.js'
import { createCurationController, curationError } from '../utils/curations.js'
import { loadReviewExport } from '../utils/watchReviewExport.js'
import { reviewTitle } from '../utils/watchReview.js'
import CurationDialog from './CurationDialog.jsx'
import './WatchPlans.css'
import './Curations.css'

export default function CurationCollectDialog({ records = [], review = null, onClose, onViewList }) {
  const session = useSeriesSession(), engine = useMemo(()=>createCurationController({client:api,getSession:seriesSession}),[session])
  const state = useSyncExternalStore(engine.subscribe,engine.getSnapshot)
  const [mode,setMode] = useState(review?'new':'existing'), [target,setTarget] = useState(''), [title,setTitle] = useState(review?`${reviewTitle(review.period)}片单`:''), [description,setDescription] = useState('')
  const [choices,setChoices] = useState(review?[]:records), [selected,setSelected] = useState(!review&&records.length===1?[records[0].id]:[])
  const [reading,setReading] = useState(!!review), [readError,setReadError] = useState(''), [retryRead,setRetryRead] = useState(0), [ack,setAck] = useState(null), [confirm,setConfirm] = useState(false)
  const owner = useRef(seriesSession()), baseline = useRef(null)
  if (!baseline.current) baseline.current=JSON.stringify({mode:review?'new':'existing',target:'',title:review?`${reviewTitle(review.period)}片单`:'',description:'',selected:!review&&records.length===1?[records[0].id]:[]})
  const dirty = !ack && JSON.stringify({mode,target,title,description,selected})!==baseline.current
  const busy=state.status==='saving', locked=busy||!!state.pending||state.conflict||state.needsRead||state.status!=='ready'||reading||!!readError
  useEffect(()=>{engine.activate();engine.read({page_size:100,state:'all'});return()=>engine.dispose()},[engine])
  useEffect(()=>{
    if(!review)return
    const controller=new AbortController();setReading(true);setReadError('');setChoices([])
    loadReviewExport(api,review,{works:true},controller.signal,()=>owner.current===seriesSession()).then(data=>{
      if(!controller.signal.aborted&&owner.current===seriesSession())setChoices(data.sections.works.map(row=>({id:row.work.record_id,title:row.work.title})))
    }).catch(error=>{if(!controller.signal.aborted&&owner.current===seriesSession())setReadError(curationError(error))}).finally(()=>{if(!controller.signal.aborted&&owner.current===seriesSession())setReading(false)})
    return()=>controller.abort()
  },[review,retryRead])
  useEffect(()=>{if(!dirty&&!state.pending&&!busy)return;const warn=e=>{e.preventDefault();e.returnValue=''};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn)},[dirty,state.pending,busy])
  const close=()=>{if(busy)return;if(!ack&&(dirty||state.pending)){setConfirm(true);return}onClose()}
  const save=async(retry=false)=>{
    if(!retry&&locked)return
    const body=retry?null:mode==='existing'?{action:'add',list_id:target,version:state.data.version,record_ids:selected}
      :{action:review?'create-from-review':'create-with-members',title:title.trim(),description,version:state.data.version,record_ids:selected,...(review?{review:{...(review.period.mode==='year'?{year:review.period.start.slice(0,4)}:review.period.mode==='month'?{month:review.period.start.slice(0,7)}:{start:review.period.start,end:review.period.end}),snapshot:review.snapshot}}:{})}
    const result=await engine.submit(body,retry)
    if(result)setAck(result.data)
  }
  const max=review?500:100
  return <CurationDialog title={review?'从回顾创建主题片单':'收录到私人片单'} busy={busy} onClose={close} footer={<div className="plan-actions">{ack?<><button type="button" onClick={onClose}>完成</button>{ack.collection&&onViewList&&<button type="button" className="plan-primary" onClick={()=>onViewList(ack.list_id)}>查看片单</button>}</>:<><button type="button" disabled={busy} onClick={close}>取消</button><button type="button" className="plan-primary" disabled={locked||!selected.length||(mode==='existing'?!target:!title.trim())} onClick={()=>save()}>确认收录 {selected.length} 部作品</button></>}</div>}>
    {confirm&&<div className="plan-leave" role="group" aria-label="确认离开片单收录"><p>{state.pending?'操作结果尚未确认，离开后请先核对片单，避免重复创建。':'是否放弃本次收录草稿？'}</p><button type="button" onClick={()=>setConfirm(false)}>继续收录</button><button type="button" onClick={onClose}>放弃并关闭收录</button></div>}
    {ack?<div role="status"><h3>{ack.collection?`已确认收录到「${ack.collection.title}」`:'原操作已确认，该片单目前已被移除。'}</h3><p>已有成员保留原位置和入选理由；私人评分、观看状态、历程和安排分别管理。</p></div>:<>
      <p className="curation-hint">{review?`依据已应用的「${reviewTitle(review.period)}」读取完整确定作品候选，不限当前分区或分页。请明确勾选，创建后可继续编排；回顾随后更新不会自动改动片单。`:'只收录本次明确勾选的私人作品。已有成员保留原位置和理由，不会自动开始观看或创建计划。'}</p>
      {(reading||state.status==='loading')&&<p role="status">正在读取收录依据…</p>}
      {(state.error||readError)&&<div role="alert" className="plan-error">{state.error||readError}{state.pending?<button type="button" disabled={busy} onClick={()=>save(true)}>重试原收录操作并核对</button>:<p>请关闭面板、刷新来源后重新选择，当前草稿仍保留。{readError&&<button type="button" onClick={()=>setRetryRead(n=>n+1)}>重试完整回顾清单</button>}</p>}</div>}
      <fieldset disabled={locked} className="curation-form">
        {!review&&<label>收录方式<select aria-label="收录方式" value={mode} onChange={e=>setMode(e.target.value)}><option value="existing">收录到已有片单</option><option value="new">创建新片单并收录</option></select></label>}
        {mode==='existing'?<label>目标片单<select aria-label="目标片单" value={target} onChange={e=>setTarget(e.target.value)}><option value="">请选择片单</option>{state.data?.results.map(row=><option key={row.id} value={row.id}>{row.title}{row.archived?'（已归档）':''}</option>)}</select>{state.data?.count===0&&<p>还没有片单，可以切换为创建新片单。</p>}</label>:<><label>片单名称<input aria-label="片单名称" maxLength={80} value={title} onChange={e=>setTitle(e.target.value)}/></label><label>专题简介<textarea aria-label="专题简介" maxLength={review?1900:2000} rows="3" value={description} onChange={e=>setDescription(e.target.value)}/></label></>}
        <div className="curation-picker-summary"><span>已选 {selected.length} / {max} 部 · 候选 {choices.length} 部</span><button type="button" disabled={!choices.length||choices.length>max} onClick={()=>setSelected(choices.map(row=>row.id))}>{review?'勾选本次全部候选':'勾选本页全部候选'}</button><button type="button" onClick={()=>setSelected([])}>清空勾选</button></div>
        <ul className="curation-collect-choices">{choices.map(row=><li key={row.id}><label className="curation-check"><input type="checkbox" aria-label={`收录${row.title}`} checked={selected.includes(row.id)} disabled={!selected.includes(row.id)&&selected.length>=max} onChange={()=>setSelected(values=>values.includes(row.id)?values.filter(id=>id!==row.id):[...values,row.id])}/><span>{row.title}</span></label></li>)}</ul>
        {!reading&&!readError&&!choices.length&&<p>本次没有可收录的确定作品，可以调整来源条件后再试。</p>}
      </fieldset>
    </>}
  </CurationDialog>
}
