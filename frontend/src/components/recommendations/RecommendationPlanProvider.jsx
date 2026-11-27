import { useEffect, useRef, useState } from 'react'
import { RecommendationPlanContext } from './RecommendationPlanEntry.jsx'
import { useNavigate } from 'react-router-dom'
import RecommendationDialog from './RecommendationDialog.jsx'
import { validateWantResult, wantFailure } from '../../utils/recommendationWant.js'
import { seriesSession } from '../../hooks/useSeriesSession.js'
import { notifyRecommendationChange } from '../../utils/recommendationReload.js'

export default function RecommendationPlanProvider({client, children}) {
  const navigate = useNavigate()
  const [item,setItem] = useState(null)
  const [open,setOpen] = useState(false), [busy,setBusy] = useState(false), [error,setError] = useState('')
  const gate=useRef(false), request=useRef(null), owner=useRef(seriesSession())
  useEffect(()=>{setOpen(false);setBusy(false);gate.current=false;return ()=>request.current?.abort()},[client])
  const arrange = async () => {
    if(gate.current || owner.current !== seriesSession()) return
    gate.current=true;setBusy(true);setError('');const controller=new AbortController();request.current=controller
    try {
      const result=validateWantResult(await client.want(item.bangumiId,controller.signal),item.bangumiId)
      if(controller.signal.aborted || owner.current !== seriesSession()) return
      notifyRecommendationChange({local:false})
      navigate(`/dashboard?arrange=${result.record_id}`)
    }catch(problem){if(!controller.signal.aborted && owner.current === seriesSession())setError(wantFailure(problem).message)}
    finally {if(request.current===controller){gate.current=false;if(!controller.signal.aborted && owner.current===seriesSession())setBusy(false)}}
  }
  return <RecommendationPlanContext.Provider value={client?.want ? {open: chosen=>{setItem(chosen);setError('');setOpen(true)}} : null}>{children}
    {open && <div className="memphis-rec-container rec-plan-dialog-scope"><RecommendationDialog title="加入观看计划" kind="plan" busy={busy} onClose={()=>{if(!gate.current)setOpen(false)}} footer={<>
      <button type="button" className="rec-button" disabled={busy} onClick={()=>setOpen(false)}>取消</button>
      <button type="button" className="rec-button rec-button--primary" disabled={busy} onClick={arrange}>{busy?'正在核对私人记录…':item.watchStatus?'核对手账并安排':'加入想看并安排'}</button>
    </>}>
      <h3 className="rec-plan-title">{item.title}</h3><p>{item.watchStatus ? '先核对对应的私人记录，保留现有观看状态、评分和长评，再进入观看安排。' : '先将作品加入私人手账的“想看”，再选择优先级和计划时间。两步分别保存，取消安排会保留已加入的手账作品。'}</p>
      <p>此入口不会直接开始观看或重看。计划保存完成后才加入队列。</p>
      {error && <p role="alert" className="rec-inline-error">{error}</p>}
    </RecommendationDialog></div>}
  </RecommendationPlanContext.Provider>
}
