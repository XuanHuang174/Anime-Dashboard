import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RecommendationCurationContext } from './RecommendationCurationEntry.jsx'
import { validateWantResult, wantFailure } from '../../utils/recommendationWant.js'
import useSeriesSession, { seriesSession } from '../../hooks/useSeriesSession.js'
import { notifyRecommendationChange } from '../../utils/recommendationReload.js'
import CurationDialog from '../CurationDialog.jsx'
import CurationCollectDialog from '../CurationCollectDialog.jsx'
import '../WatchPlans.css'
import '../Curations.css'

export default function RecommendationCurationProvider({client,children}) {
  const session=useSeriesSession(), navigate=useNavigate(), [item,setItem]=useState(null), [record,setRecord]=useState(null), [busy,setBusy]=useState(false), [error,setError]=useState('')
  const gate=useRef(false), job=useRef(null), generation=useRef(0)
  useEffect(()=>{generation.current++;setItem(null);setRecord(null);setBusy(false);gate.current=false;return()=>{generation.current++;job.current?.abort()}},[client,session])
  async function prepare() {
    if(gate.current||!item)return
    gate.current=true;setBusy(true);setError('')
    const controller=new AbortController(), run=generation.current, owner=seriesSession();job.current=controller
    try {
      const data=validateWantResult(await client.want(item.bangumiId,controller.signal),item.bangumiId)
      if(controller.signal.aborted||run!==generation.current||owner!==seriesSession())return
      setRecord({id:data.record_id,title:item.title});notifyRecommendationChange({local:false})
    }catch(error){if(!controller.signal.aborted&&run===generation.current&&owner===seriesSession())setError(wantFailure(error).message)}
    finally{if(job.current===controller){gate.current=false;if(!controller.signal.aborted&&run===generation.current&&owner===seriesSession())setBusy(false)}}
  }
  const close=()=>{if(!gate.current){setItem(null);setRecord(null)}}
  return <RecommendationCurationContext.Provider value={client?.want?{open:item=>{setItem(item);setRecord(null);setError('')}}:null}>{children}
    {item&&!record&&<CurationDialog title="从推荐收录到片单" busy={busy} onClose={close} footer={<div className="plan-actions curation-confirm-actions"><button type="button" className="plan-primary" disabled={busy} onClick={prepare}>{item.watchStatus?'核对私人记录并选片单':'加入想看并选片单'}</button><button type="button" disabled={busy} onClick={close}>取消</button></div>}><h3>{item.title}</h3><p>先明确加入或核对私人手账，再选择目标片单，两个步骤分别保存。已有评分、状态和长评保留；取消后续收录会保留已加入的手账作品。</p><p>此入口不开始观看、不创建计划。作品身份冲突须先核对。</p>{error&&<p className="plan-error" role="alert">{error}</p>}</CurationDialog>}
    {record&&<CurationCollectDialog records={[record]} onClose={close} onViewList={id=>{close();navigate(`/dashboard?curation=${id}`)}}/>}
  </RecommendationCurationContext.Provider>
}
