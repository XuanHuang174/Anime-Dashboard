import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import api from '../api/api.js'
import AnimeGrid from '../components/AnimeGrid.jsx'
import BackToTop from '../components/BackToTop.jsx'
import DetailModal from '../components/DetailModal.jsx'
import { mapRecordToAnimeItem } from '../utils/recordMapping.js'

function SharedJournalPage() {
  const navigate = useNavigate()
  const { username = '' } = useParams()
  const [items, setItems] = useState([])
  const [selectedItem, setSelectedItem] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    const controller = new AbortController()

    const loadJournal = async () => {
      setIsLoading(true)
      setLoadError('')
      try {
        const records = []
        let page = 1
        let hasNext = true
        while (hasNext) {
          const response = await api.get(
            `public-journal/users/${encodeURIComponent(username)}/records/`,
            {
              params: { page, page_size: 100 },
              signal: controller.signal,
            },
          )
          const payload = response.data
          if (Array.isArray(payload)) {
            records.push(...payload)
            hasNext = false
          } else {
            records.push(...(payload.results ?? []))
            hasNext = Boolean(payload.next)
            page += 1
          }
        }
        setItems(records.map(mapRecordToAnimeItem))
      } catch (error) {
        if (error.code !== 'ERR_CANCELED') {
          setLoadError(
            error.response?.status === 404
              ? '该手账尚未公开，或用户不存在。'
              : '公开手账暂时无法加载，请稍后再试。',
          )
        }
      } finally {
        if (!controller.signal.aborted) setIsLoading(false)
      }
    }

    loadJournal()
    return () => controller.abort()
  }, [username])

  return (
    <main className="memphis-public relative min-h-screen overflow-hidden bg-[#fff5ee] pb-12 text-black">
      <div className="memphis-grid-texture pointer-events-none absolute inset-0" aria-hidden="true" />
      <header className="relative z-10 border-b-4 border-black bg-[#4ecdc4]">
        <div className="mx-auto max-w-7xl px-4 py-7 sm:px-6 lg:px-8">
          <button
            type="button"
            onClick={() => navigate('/universe')}
            className="inline-flex items-center gap-2 rounded-full border-4 border-black bg-white px-5 py-2.5 text-sm font-black shadow-[5px_5px_0_#111] active:translate-x-[5px] active:translate-y-[5px] active:shadow-none"
          >
            <i className="fa-solid fa-arrow-left" />
            返回番剧共创宇宙
          </button>
          <h1 className="mt-6 break-all text-4xl font-black sm:text-6xl">
            <span className="border-4 border-black bg-black px-4 py-2 text-white">
              {username}
            </span>
            <span className="ml-3 inline-block -rotate-1 border-4 border-black bg-[#ffe66d] px-4 py-2 shadow-[7px_7px_0_#111]">
              的公开手账
            </span>
          </h1>
        </div>
      </header>

      <section className="relative z-10 mx-auto max-w-7xl px-4 py-9 sm:px-6 lg:px-8">
        {isLoading && (
          <div className="border-4 border-black bg-white py-12 text-center font-black shadow-[8px_8px_0_#ff8fab]">
            <i className="fa-solid fa-spinner mr-3 animate-spin" />
            正在同步实时手账…
          </div>
        )}
        {!isLoading && loadError && (
          <div role="alert" className="border-4 border-black bg-[#ff6b6b] px-6 py-12 text-center text-xl font-black shadow-[8px_8px_0_#111]">
            <i className="fa-solid fa-lock mr-3" />
            {loadError}
          </div>
        )}
        {!isLoading && !loadError && items.length === 0 && (
          <div className="border-4 border-black bg-white px-6 py-12 text-center text-xl font-black shadow-[8px_8px_0_#4ecdc4]">
            这本公开手账目前还没有番剧记录。
          </div>
        )}
        {!isLoading && !loadError && items.length > 0 && (
          <AnimeGrid
            items={items}
            onOpenDetail={setSelectedItem}
            theme="memphis"
          />
        )}
      </section>

      <DetailModal
        selectedItem={selectedItem}
        closeModal={() => setSelectedItem(null)}
      />
      <BackToTop theme="memphis" />
    </main>
  )
}

export default SharedJournalPage
