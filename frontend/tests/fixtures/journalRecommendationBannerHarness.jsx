import React, { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import JournalRecommendationBanner from '../../src/components/JournalRecommendationBanner.jsx'
import '../../src/index.css'

createRoot(document.getElementById('root')).render(<StrictMode><MemoryRouter>
  <div className="memphis-dashboard" style={{ padding: 16 }}>
    <div style={{ maxWidth: 500, margin: '30px auto', boxSizing: 'border-box' }}><JournalRecommendationBanner /></div>
  </div>
</MemoryRouter></StrictMode>)
