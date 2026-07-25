import { useEffect, useRef } from 'react'

function CustomCursor() {
  const cursorRef = useRef(null)

  useEffect(() => {
    const crosshair = cursorRef.current
    const pointerQuery = window.matchMedia('(hover: hover) and (pointer: fine)')
    if (!crosshair || !pointerQuery.matches) return undefined

    let mouseX = window.innerWidth / 2
    let mouseY = window.innerHeight / 2
    let isCursorHidden = false

    const getClosest = (target, selector) =>
      target instanceof Element ? target.closest(selector) : null

    const handleMouseMove = (event) => {
      mouseX = event.clientX
      mouseY = event.clientY
      const scale = crosshair.style.transform.includes('scale(1.5)') ? 'scale(1.5)' : 'scale(1)'
      crosshair.style.transform = `translate(${mouseX}px, ${mouseY}px) translate(-50%, -50%) ${scale}`

      if (isCursorHidden && !getClosest(event.target, 'input, select, textarea')) {
        crosshair.style.opacity = '1'
        isCursorHidden = false
      }
    }

    const handleMouseOver = (event) => {
      const isNativeUi = getClosest(event.target, 'input, select, textarea')
      const isInteractive = getClosest(
        event.target,
        'button, a, tr.animate-item, .cat-btn, th.cursor-pointer',
      )

      if (isNativeUi) {
        crosshair.style.opacity = '0'
        isCursorHidden = true
      } else if (isInteractive) {
        crosshair.style.transform = `translate(${mouseX}px, ${mouseY}px) translate(-50%, -50%) scale(1.5)`
        crosshair.style.backgroundColor = '#f43f5e'
      }
    }

    const handleMouseOut = (event) => {
      const isNativeUi = getClosest(event.target, 'input, select, textarea')
      const isInteractive = getClosest(
        event.target,
        'button, a, tr.animate-item, .cat-btn, th.cursor-pointer',
      )

      if (isNativeUi) {
        crosshair.style.opacity = '1'
        isCursorHidden = false
      } else if (isInteractive) {
        crosshair.style.transform = `translate(${mouseX}px, ${mouseY}px) translate(-50%, -50%) scale(1)`
        crosshair.style.backgroundColor = '#6366f1'
      }
    }

    const hideCursor = () => {
      crosshair.style.opacity = '0'
      isCursorHidden = true
    }

    window.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseover', handleMouseOver)
    document.addEventListener('mouseout', handleMouseOut)
    window.addEventListener('contextmenu', hideCursor)
    document.addEventListener('mouseleave', hideCursor)

    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseover', handleMouseOver)
      document.removeEventListener('mouseout', handleMouseOut)
      window.removeEventListener('contextmenu', hideCursor)
      document.removeEventListener('mouseleave', hideCursor)
    }
  }, [])

  return <div id="customCrosshair" ref={cursorRef}></div>
}

export default CustomCursor
