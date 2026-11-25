import { useEffect, useState } from 'react'
import { PLAN_PRIORITIES, dateValue } from '../utils/watchPlans.js'
import { PlanDateField } from './WatchPlanPanel.jsx'

export default function WatchPlanBatch({ rows, version, currentVersion, engine, blocked, onDirty, onSaved, onCancel }) {
  const [ids,setIds] = useState([]), [action,setAction] = useState('update'), [priority,setPriority] = useState('')
  const [setDate,toggleDate] = useState(false), [planned,setPlanned] = useState({precision:'unknown',value:''})
  const [confirm,setConfirm] = useState(false), [error,setError] = useState('')
  useEffect(()=>{onDirty(ids.length>0);return ()=>onDirty(false)},[ids,onDirty])
  const changed = version !== currentVersion
  const submit = async event => {
    event.preventDefault(); if(blocked || changed || !ids.length) return
    if(action === 'update' && !priority && !setDate) {setError('请选择要调整的优先级或计划时间。');return}
    try {
      const body={action,version,record_ids:ids,...(action==='update' ? {...(priority ? {priority:Number(priority)} : {}),...(setDate ? {planned_for:dateValue(planned)} : {})} : {})}
      if(!confirm){setConfirm(true);setError('');return}
      if(await engine.submit(body)) {onSaved();engine.read({page:1})}
    }catch(problem){setError(problem.message)}
  }
  return <form className="plan-batch" aria-label="批量调整观看计划" onSubmit={submit}>
    <h3>批量调整当前页计划</h3><p className="plan-hint">明确勾选后统一调整，最多 100 部；其他分页不自动选中。作品、评分、备注和实际观看历程保持原样。</p>
    <fieldset disabled={blocked || confirm || changed}><div className="plan-batch-list">{rows.filter(row=>['pending','ongoing'].includes(row.plan.state)).map(row=><label key={row.record.id}><input type="checkbox" checked={ids.includes(row.record.id)} onChange={()=>setIds(old=>old.includes(row.record.id)?old.filter(id=>id!==row.record.id):[...old,row.record.id])}/><span>{row.record.title}</span></label>)}</div>
      <label>批量操作<select aria-label="批量操作" value={action} onChange={event=>setAction(event.target.value)}><option value="update">调整安排</option><option value="remove">移出当前计划</option></select></label>
      {action==='update' && <div className="plan-batch-fields"><label>统一优先级<select aria-label="批量优先级" value={priority} onChange={event=>setPriority(event.target.value)}><option value="">保持各自优先级</option>{Object.entries(PLAN_PRIORITIES).map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label><div><label className="plan-check"><input type="checkbox" checked={setDate} onChange={event=>toggleDate(event.target.checked)}/>统一计划时间</label>{setDate && <PlanDateField label="批量计划开始时间" part={planned} onChange={setPlanned}/>}</div></div>}
    </fieldset>
    {changed && <p role="alert" className="plan-error">计划队列已变化，请取消批量操作、刷新并重新勾选。</p>}{error && <p role="alert" className="plan-error">{error}</p>}
    {confirm && <div className="plan-leave"><strong>已选 {ids.length} 部，确认{action==='remove'?'移出计划':'调整安排'}？</strong><ul>{rows.filter(row=>ids.includes(row.record.id)).map(row=><li key={row.record.id}>{row.record.title}</li>)}</ul><p>{action==='remove'?'仅移出安排，不删除私人作品。':`${priority ? `优先级：${PLAN_PRIORITIES[priority]}；` : ''}${setDate ? `计划时间：${planned.value || '未定'}` : '计划时间各自保留'}`}</p><button type="button" disabled={blocked} onClick={()=>setConfirm(false)}>返回核对选择</button></div>}
    <div className="plan-actions"><button type="submit" className="plan-primary" disabled={blocked || changed || !ids.length}>{confirm?'确认批量保存':'核对批量调整'}</button><button type="button" disabled={engine.getSnapshot().status==='saving'} onClick={onCancel}>取消批量调整</button></div>
  </form>
}
