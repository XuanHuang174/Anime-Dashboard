import { useRef, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { getCoverSrc } from '../utils/animeDisplay.js'
import { formatAiringPeriod } from '../utils/featuredDisplay.js'
import '../styles/completion-report.css'

const REPORT_URL = 'https://xh-anime.com'

function waitForPosterImages(root) {
  const images = Array.from(root.querySelectorAll('img'))
  return Promise.all(images.map(async (image) => {
    if (image.complete && image.naturalWidth > 0) return

    if (typeof image.decode === 'function') {
      try {
        await image.decode()
        return
      } catch {
        // Fall through to the load/error listeners for browsers with partial decode support.
      }
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
  }))
}

function sanitizeFilename(title) {
  return String(title || '番剧')
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
    .replace(/[《》]/g, '')
    .trim() || '番剧'
}

function CompletionReportExporter({ item, disabled = false, buttonClassName = '', showToast }) {
  const posterRef = useRef(null)
  const [isGenerating, setIsGenerating] = useState(false)

  const handleGenerate = async () => {
    const poster = posterRef.current
    if (!poster || isGenerating) return

    setIsGenerating(true)
    try {
      const { default: html2canvas } = await import('html2canvas')
      if (document.fonts?.ready) await document.fonts.ready
      await waitForPosterImages(poster)

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
      setIsGenerating(false)
    }
  }

  const tags = Array.isArray(item.tags) ? item.tags : []
  const review = String(item.review || '').trim() || '这部番剧还没有写下个人评价。'
  const score = item.score === null || item.score === undefined
    ? '--'
    : Number(item.score).toFixed(1)

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
                src={getCoverSrc(item)}
                alt=""
                crossOrigin="anonymous"
                referrerPolicy="no-referrer"
                className="completion-report-cover"
              />
            </div>

            <div className="completion-report-facts">
              <div className="completion-report-score-card">
                <span>综合评分</span>
                <strong>{score}</strong>
                <small>/ 10</small>
              </div>
              <div className="completion-report-fact-card">
                <span>放送季度 / SEASON</span>
                <strong>{formatAiringPeriod(item.period) || '未定档'}</strong>
              </div>
              <div className="completion-report-tag-card">
                <span>标签分类 / TAGS</span>
                <div className="completion-report-tags">
                  {tags.length > 0 ? tags.map((tag, index) => (
                    <b key={`${tag}-${index}`} className={`completion-report-tag completion-report-tag--${index % 4}`}>
                      {tag}
                    </b>
                  )) : <b className="completion-report-tag completion-report-tag--0">未分类</b>}
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
