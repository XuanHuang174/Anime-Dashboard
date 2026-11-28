import { validCuration, validCurationEntry } from './curations.js'
import { WATCH_LABELS } from './watchHistory.js'

export function validCurationExport(data, id) {
  return Boolean(data?.contract_version === 'journal.curation-export.v1' && Number.isSafeInteger(data.version) && data.version >= 0
    && validCuration(data.collection) && data.collection.id === id && typeof data.snapshot === 'string' && data.snapshot.length > 0 && data.snapshot.length <= 2048
    && typeof data.generated_at === 'string' && Number.isFinite(Date.parse(data.generated_at)) && Array.isArray(data.entries)
    && data.entries.length === data.collection.entry_count && data.entries.every(validCurationEntry)
    && new Set(data.entries.map(row => row.id)).size === data.entries.length
    && new Set(data.entries.filter(row => row.record).map(row => row.record.id)).size === data.entries.filter(row => row.record).length
    && data.entries.every((row,index)=>!index || row.position >= data.entries[index-1].position))
}
function stable(value) {
  if (Array.isArray(value)) return value.map(stable)
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().filter(key=>!['generated_at'].includes(key) && !(key==='cover' && typeof value[key]==='string')).map(key=>[key,stable(value[key])]))
  return value
}
export async function loadCurationExport(client, source, selection, signal, live) {
  if (signal.aborted || !live()) throw new Error('导出已取消。')
  const {data} = await client.get(`curations/${source.id}/export/`,{params:{version:source.version},signal})
  if (signal.aborted || !live()) throw new Error('导出已取消。')
  if (!validCurationExport(data,source.id) || data.version !== source.version) throw new Error('片单导出依据不完整，请刷新后重新预览。')
  return data
}
export async function verifyCurationExport(client, source, signal, live) {
  if (signal.aborted || !live()) throw new Error('导出已取消。')
  const {data} = await client.get(`curations/${source.collection.id}/export/`,{params:{snapshot:source.snapshot},signal})
  if (signal.aborted || !live()) throw new Error('导出已取消。')
  if (!validCurationExport(data,source.collection.id) || JSON.stringify(stable(data)) !== JSON.stringify(stable(source))) throw new Error('片单或作品资料已变化，请重新预览。')
}
const chunks = text => Array.from(text).reduce((parts,char,index)=>{if(index%600===0)parts.push('');parts[parts.length-1]+=char;return parts},[])
export function curationExportBlocks(source, selection) {
  const blocks=[]
  if(selection.intro) chunks(source.collection.description || '暂无专题简介。').forEach((part,index)=>blocks.push({title:`专题简介${index?'（续）':''}`,text:part}))
  if(selection.works) source.entries.forEach((row,index)=>{
    const title=`${index+1}. ${row.record?.title || row.title_snapshot.title || '已移除作品'}`
    const info=[row.unavailable?'作品已从私人手账移除，保留原条目。':'',selection.status&&row.record?`${WATCH_LABELS[row.record.watch_status]} · ${row.record.watched_eps}/${row.record.total_eps||'?'} 集`:'',selection.score&&row.record?`个人评分：${row.record.personal_score??'未评分'}`:''].filter(Boolean).join('\n')
    const parts=selection.reasons&&row.reason?chunks(row.reason):['']
    parts.forEach((part,i)=>blocks.push({title:title+(i?'（续）':''),text:[i?'':info,part?`入选理由：${part}`:''].filter(Boolean).join('\n'),cover:!i&&selection.covers?row.record?.cover||'':''}))
  })
  return blocks
}
export const curationExportFilename = source => `${source.collection.title.replace(/[<>:"/\\|?*\u0000-\u001f]/g,'_').replace(/[. ]+$/,'') || '主题片单'}_私人片单`
