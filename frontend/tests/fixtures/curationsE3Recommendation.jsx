import React from 'react'
import {createRoot} from 'react-dom/client'
import {BrowserRouter} from 'react-router-dom'
import RecommendationCurationProvider from '../../src/components/recommendations/RecommendationCurationProvider.jsx'
import {RecommendationActions} from '../../src/components/recommendations/RecommendationCard.jsx'
import client from '../../src/api/recommendations.js'
import '../../src/index.css'
import '../../src/styles/recommendations.css'
import '../../src/styles/recommendations-theme.css'
const item={bangumiId:940001,title:'B3 推荐安排合成作品',watchStatus:null,contentRating:'non_adult'}
createRoot(document.getElementById('root')).render(<BrowserRouter><RecommendationCurationProvider client={client}><main className="memphis-rec-container"><RecommendationActions item={item} onDetail={()=>{}} onWant={()=>{}}/></main></RecommendationCurationProvider></BrowserRouter>)
