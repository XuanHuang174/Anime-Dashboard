import api from '../api/api.js'
import PrivateImageExporter from './PrivateImageExporter.jsx'
import { curationExportBlocks, curationExportFilename, loadCurationExport, verifyCurationExport } from '../utils/curationExport.js'

function Sheet({report,blocks,index,count,covers}) {
  return <article className="review-export-sheet"><header><span>MY COLLECTION · 私人主题片单</span><h1>{report.collection.title}</h1><p>完整片单 · {report.entries.length} 项 · 保留手动编排顺序</p></header>
    {blocks.map((block,i)=><section key={i}>{block.cover&&<div className="review-export-cover">{covers[block.cover]?<><img src={covers[block.cover]} alt="" onError={event=>{event.currentTarget.hidden=true;event.currentTarget.nextElementSibling.hidden=false}}/><span hidden>海报未载入</span></>:<span>海报未载入</span>}</div>}<h2>{block.title}</h2>{block.text&&<p>{block.text}</p>}</section>)}
    <footer>本页仅包含本次明确选择的导出内容。作品资料为读取时的私人手账资料。<br/>仅本机下载，未自动发布。{index+1} / {count} 页 · 读取依据时间 {new Date(report.generated_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}</footer>
  </article>
}
export default function CurationExporter({source,onClose}) {
  return <PrivateImageExporter source={source} onClose={onClose} title="导出私人片单" downloadLabel="下载片单长图"
    defaults={{intro:false,works:true,reasons:false,status:false,score:false,covers:false}} privateKeys={['intro','reasons','status','score']} dependentKeys={['reasons','status','score','covers']}
    options={[["works","完整作品清单"],["intro","专题简介"],["reasons","入选理由"],["status","观看状态与进度"],["score","个人评分"],["covers","包含海报（最多24张，失败或超限显示占位）"]]}
    load={(source,selection,signal,live)=>loadCurationExport(api,source,selection,signal,live)} verify={(source,signal,live)=>verifyCurationExport(api,source,signal,live)} blocks={curationExportBlocks} Sheet={Sheet} filename={curationExportFilename}
    hint="读取整张片单，包含其他分页及失效条目。专题简介、入选理由、个人评分和观看进度默认不导出；预览后仅向本机下载。"/>
}
