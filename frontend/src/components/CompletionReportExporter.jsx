import { useRef, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import api from '../api/api.js'
import { getCoverSrc, getScoreAccentColor } from '../utils/animeDisplay.js'
import { formatAiringPeriod } from '../utils/featuredDisplay.js'
import '../styles/completion-report.css'

const REPORT_URL = 'https://xh-anime.com'
const TAG_COLORS = ['#ffe66d', '#ff8fab', '#4ecdc4', '#ffffff']
const PLACEHOLDER_COVER = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI2MDAiIGhlaWdodD0iODAwIiB2aWV3Qm94PSIwIDAgNjAwIDgwMCI+PHJlY3Qgd2lkdGg9IjYwMCIgaGVpZ2h0PSI4MDAiIGZpbGw9IiNmZmU2NmQiLz48cGF0aCBkPSJNMCAwaDYwMHY4MDBIMHoiIGZpbGw9InVybCgjYSkiLz48ZGVmcz48cGF0dGVybiBpZD0iYSIgd2lkdGg9IjQwIiBoZWlnaHQ9IjQwIiBwYXR0ZXJuVW5pdHM9InVzZXJTcGFjZU9uVXNlIj48Y2lyY2xlIGN4PSI0IiBjeT0iNCIgcj0iMyIgZmlsbD0iI2Q3YzA1OCIvPjwvcGF0dGVybj48L2RlZnM+PHJlY3QgeD0iNjAiIHk9IjEwMCIgd2lkdGg9IjQ4MCIgaGVpZ2h0PSI2MDAiIGZpbGw9IiNmZmY1ZWUiIHN0cm9rZT0iIzExMSIgc3Ryb2tlLXdpZHRoPSIxMiIvPjxjaXJjbGUgY3g9IjMwMCIgY3k9IjMyMCIgcj0iMTAwIiBmaWxsPSIjNGVjZGM0IiBzdHJva2U9IiMxMTEiIHN0cm9rZS13aWR0aD0iMTIiLz48cGF0aCBkPSJNMjMwIDMzMGwxNDAgMCAwIDEyMC0xNDAgMHoiIGZpbGw9IiNmZjhmYWIiIHN0cm9rZT0iIzExMSIgc3Ryb2tlLXdpZHRoPSIxMiIvPjx0ZXh0IHg9IjMwMCIgeT0iNTUwIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iQXJpYWwsIHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMzYiIGZvbnQtd2VpZ2h0PSI5MDAiIGZpbGw9IiMxMTEiPk5PIElNQUdFPC90ZXh0Pjx0ZXh0IHg9IjMwMCIgeT0iNjEwIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmb250LWZhbWlseT0iQXJpYWwsIHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMjgiIGZvbnQtd2VpZ2h0PSI5MDAiIGZpbGw9IiMxMTEiPuaguuaXoOWbvueJhzwvdGV4dD48L3N2Zz4='

function canUseCoverDirectly(source) {
  if (!source || /^(data:|blob:)/i.test(source)) return true

  try {
    const parsed = new URL(source, window.location.origin)
    // Subdomains are different origins. Proxy media.xh-anime.com and every
    // external/R2 URL so export never depends on a bucket's CORS response.
    return parsed.origin === window.location.origin
  } catch {
    return false
  }
}

async function resolveReportCover(source) {
  if (canUseCoverDirectly(source)) return source

  try {
    const response = await api.get('utils/image-proxy/', {
      params: { url: source },
      timeout: 8000,
    })
    return response.data?.data_url || PLACEHOLDER_COVER
  } catch (error) {
    console.warn('Report cover proxy failed; using placeholder.', error)
    return PLACEHOLDER_COVER
  }
}

async function loadPosterImage(image, source) {
  if (!image) throw new Error('海报画板尚未准备完成。')

  image.crossOrigin = 'anonymous'
  image.src = source
  if (image.complete && image.naturalWidth > 0) {
    if (typeof image.decode === 'function') await image.decode()
    return
  }

  await new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(
      () => reject(new Error('番剧封面加载超时，请稍后重试。')),
      12000,
    )
    const finish = (callback) => {
      window.clearTimeout(timeoutId)
      image.removeEventListener('load', handleLoad)
      image.removeEventListener('error', handleError)
      callback()
    }
    const handleLoad = () => finish(resolve)
    const handleError = () => finish(() => reject(new Error('番剧封面无法用于海报导出。')))
    image.addEventListener('load', handleLoad, { once: true })
    image.addEventListener('error', handleError, { once: true })
  })
}

function sanitizeFilename(title) {
  return String(title || '番剧')
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
    .replace(/[《》]/g, '')
    .trim() || '番剧'
}

function waitForStableLayout() {
  return new Promise((resolve) => {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(resolve)
    })
  })
}

function getReportTagWidth(tag) {
  const text = String(tag || '')
  const visualUnits = Array.from(text).reduce(
    (total, character) => total + (/^[\u0000-\u00ff]$/.test(character) ? 0.58 : 1),
    0,
  )
  return Math.max(62, Math.ceil(visualUnits * 17 + 28))
}

function CompletionReportExporter({ item, disabled = false, buttonClassName = '', showToast }) {
  const posterRef = useRef(null)
  const coverRef = useRef(null)
  const [isGenerating, setIsGenerating] = useState(false)

  const handleGenerate = async () => {
    const poster = posterRef.current
    if (!poster || isGenerating) return

    setIsGenerating(true)
    const originalCover = getCoverSrc(item)
    try {
      const { default: html2canvas } = await import('html2canvas')
      const exportCover = await resolveReportCover(originalCover)
      if (document.fonts?.ready) await document.fonts.ready
      try {
        await loadPosterImage(coverRef.current, exportCover)
      } catch (imageError) {
        if (exportCover === PLACEHOLDER_COVER || !coverRef.current) throw imageError
        await loadPosterImage(coverRef.current, PLACEHOLDER_COVER)
      }
      await waitForStableLayout()

      const canvas = await html2canvas(poster, {
        backgroundColor: '#fff5ee',
        scale: 2,
        useCORS: true,
        allowTaint: false,
        logging: false,
        width: poster.offsetWidth,
        height: poster.scrollHeight,
        windowWidth: poster.offsetWidth,
        windowHeight: poster.scrollHeight,
      })

      const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png', 1))
      if (!blob) throw new Error('浏览器未能生成图片文件。')

      const downloadUrl = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = downloadUrl
      link.download = `${sanitizeFilename(item.title)}_结案报告.png`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000)
      showToast?.('结案报告已生成并开始下载。', 'success')
    } catch (error) {
      console.error('Completion report generation failed:', error)
      showToast?.(error.message || '结案报告生成失败，请稍后重试。', 'error')
    } finally {
      if (coverRef.current) coverRef.current.src = originalCover
      setIsGenerating(false)
    }
  }

  const tags = Array.isArray(item.tags) ? item.tags : []
  const review = String(item.review || '').trim() || '这部番剧还没有写下个人评价。'
  const score = item.score === null || item.score === undefined
    ? '--'
    : Number(item.score).toFixed(1)
  const scoreAccentColor = getScoreAccentColor(item.score)

  return (
    <>
      <button
        type="button"
        onClick={handleGenerate}
        disabled={disabled || isGenerating}
        className={buttonClassName}
        aria-busy={isGenerating}
      >
        <i className={`fa-solid ${isGenerating ? 'fa-spinner animate-spin' : 'fa-image'} mr-2`} />
        {isGenerating ? '生成中...' : '生成结案报告'}
      </button>

      <div className="completion-report-stage" aria-hidden="true">
        <article ref={posterRef} className="completion-report-poster">
          <span className="completion-report-shape completion-report-shape--circle" />
          <span className="completion-report-shape completion-report-shape--triangle" />
          <span className="completion-report-shape completion-report-shape--zigzag">〰〰〰</span>

          <header className="completion-report-header">
            <div className="completion-report-stamp">ANIME COMPLETED / 追番结案</div>
            <div className="completion-report-number">NO. {String(item.id || '000').padStart(3, '0')}</div>
          </header>

          <section className="completion-report-title-block">
            <p>MY ANIME JOURNAL</p>
            <h1>{item.title}</h1>
            {item.japaneseTitle && <h2>{item.japaneseTitle}</h2>}
          </section>

          <section className="completion-report-main">
            <div className="completion-report-cover-frame">
              <img
                ref={coverRef}
                src={getCoverSrc(item)}
                alt=""
                crossOrigin="anonymous"
                referrerPolicy="no-referrer"
                className="completion-report-cover"
              />
            </div>

            <div className="completion-report-facts">
              <div
                className="completion-report-score-card"
                style={{ backgroundColor: scoreAccentColor }}
              >
                <span>综合评分</span>
                <div className="completion-report-score-value">
                  <strong>
                    <svg
                      viewBox="0 0 176 96"
                      width="176"
                      height="96"
                      aria-label={`${score} 分`}
                    >
                      <text
                        x="88"
                        y="49"
                        textAnchor="middle"
                        dominantBaseline="middle"
                      >
                        {score}
                      </text>
                    </svg>
                  </strong>
                  <small>/ 10</small>
                </div>
              </div>
              <div className="completion-report-fact-card">
                <span>放送季度 / SEASON</span>
                <strong>{formatAiringPeriod(item.period) || '未定档'}</strong>
              </div>
              <div className="completion-report-tag-card">
                <span>标签分类 / TAGS</span>
                <div className="completion-report-tags">
                  {(tags.length > 0 ? tags : ['未分类']).map((tag, index) => {
                    const tagWidth = getReportTagWidth(tag)
                    return (
                      <svg
                        key={`${tag}-${index}`}
                        className="completion-report-tag"
                        width={tagWidth}
                        height="36"
                        viewBox={`0 0 ${tagWidth} 36`}
                        role="img"
                        aria-label={tag}
                      >
                        <rect
                          x="1.5"
                          y="1.5"
                          width={tagWidth - 3}
                          height="33"
                          rx="16.5"
                          fill={TAG_COLORS[index % TAG_COLORS.length]}
                          stroke="#111"
                          strokeWidth="3"
                        />
                        <text
                          x={tagWidth / 2}
                          y="18.5"
                          textAnchor="middle"
                          dominantBaseline="middle"
                        >
                          {tag}
                        </text>
                      </svg>
                    )
                  })}
                </div>
              </div>
            </div>
          </section>

          <section className="completion-report-review">
            <div className="completion-report-review-label">PERSONAL REVIEW / 个人评价</div>
            <p>{review}</p>
          </section>

          <footer className="completion-report-footer">
            <div className="completion-report-slogan">
              <span>KEEP YOUR FAVORITES.</span>
              <strong>把喜欢的动画全部记下来！</strong>
            </div>
            <div className="completion-report-qr">
              <div className="completion-report-qr-frame">
                <QRCodeSVG
                  value={REPORT_URL}
                  size={166}
                  level="H"
                  bgColor="#ffffff"
                  fgColor="#111111"
                />
              </div>
              <strong>xh-anime.com</strong>
            </div>
          </footer>
        </article>
      </div>
    </>
  )
}

export default CompletionReportExporter
