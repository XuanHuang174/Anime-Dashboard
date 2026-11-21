import React,{useState} from 'react'
import {createRoot} from 'react-dom/client'
import {BrowserRouter} from 'react-router-dom'
import DetailModal from '../../src/components/DetailModal.jsx'
import AddAnimeModal from '../../src/components/AddAnimeModal.jsx'
import QuickFilterEditorModal from '../../src/components/QuickFilterEditorModal.jsx'
import {RecommendationFilterPanel} from '../../src/components/recommendations/RecommendationBrowseControls.jsx'
import CreditsFilterPanel from '../../src/components/recommendations/CreditsFilterPanel.jsx'
import ExplorationTagEditor from '../../src/components/recommendations/ExplorationTagEditor.jsx'
import {emptyTagDraft} from '../../src/utils/explorationTagQuery.js'
import {DEFAULT_BROWSE_FILTERS} from '../../src/utils/recommendationBrowse.js'
import {CREDIT_DEFAULTS} from '../../src/utils/recommendationCredits.js'
import api from '../../src/api/api.js'
import '../../src/index.css'
import '../../src/styles/recommendations.css'
import '../../src/styles/recommendations-theme.css'
api.defaults.adapter=async config=>{
 let data={}
 if(config.url==='external/search/')data=[{id:123,chinese_title:'合成番剧',japanese_title:'Synthetic',date:'2020-01-01'}]
 if(config.url==='external/autofill/')data={chineseTitle:'合成番剧',japaneseTitle:'Synthetic',airingPeriod:'2020-1',tags:'日常,治愈,喜剧',bangumiUrl:'https://bgm.tv/subject/123',autofillTicket:'synthetic-proof',baikeUrl:''}
 if(config.url==='recommendations/capabilities/')data={}
 if(config.url==='quick-filters/preview/')data={count:0,scope:'合成全范围'}
 else if(config.method==='post'||config.method==='patch'){data=typeof config.data==='string'?JSON.parse(config.data):{};window.lastSaved=data}
 return {config,data,status:200,headers:{}}
}
function App(){
 const mode=new URLSearchParams(location.search).get('mode')||'detail'
 const [saved,setSaved]=useState(null),[tagDraft,setTagDraft]=useState(()=>({...emptyTagDraft(),include:['日常']}))
 const item={id:1,title:'《合成番剧的较长标题》',japaneseTitle:'Synthetic long title',period:'2026-10',score:9.5,status:'watching',watchedEps:8,totalEps:12,studio:'Synthetic',episodes:'12',tags:['日常','治愈'],baseTags:['日常'],tagColors:{},description:'仅本机验证。'.repeat(200),baikeUrl:'https://zh.moegirl.org.cn/Synthetic',bangumiUrl:'https://bgm.tv/subject/123',confirmedBangumiId:123,cover:'data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="400"><rect width="300" height="400" fill="#4ecdc4"/><text x="10" y="180" font-size="32">SYNTHETIC POSTER</text></svg>')}
 const tags=Array.from({length:80},(_,i)=>'自定义标签'+i).concat(['日常','治愈','旧标签'])
 if(mode==='detail')return <DetailModal selectedItem={item} isPrivateJournal closeModal={()=>{}}/>
 if(mode==='add')return <AddAnimeModal isOpen onClose={()=>{}} onRecordAdded={()=>{}} showToast={()=>{}}/>
 if(mode==='quick')return <><QuickFilterEditorModal isOpen onClose={()=>{}} availableTags={tags} filters={[{id:1,name:'已有年份',tags:['旧标签'],exclude_tags:[],title_keywords:[],exclude_title_keywords:[],match_mode:'any',year_from:2018,year_to:2021}]} onSaved={setSaved} onDeleted={()=>{}} showToast={()=>{}}/>{saved&&<pre id="saved">{JSON.stringify(saved)}</pre>}</>
 return <div className="memphis-rec-container" style={{minHeight:0,padding:20,maxWidth:640}}>{mode==='credits'?<CreditsFilterPanel options={CREDIT_DEFAULTS} onApply={setSaved} client={{capabilities:async()=>({})}}/>:mode==='tags'?<ExplorationTagEditor draft={tagDraft} onChange={setTagDraft} options={{tags:[{tag:'日常',count:3}]}} status="ready" onApply={setSaved} client={{capabilities:async()=>({})}}/>:<RecommendationFilterPanel initialFilters={DEFAULT_BROWSE_FILTERS} onApply={setSaved} client={{capabilities:async()=>({})}}/>}<pre id="saved">{JSON.stringify(saved)}</pre></div>
}
createRoot(document.getElementById('root')).render(<BrowserRouter><App/></BrowserRouter>)
