import { useEffect, useMemo, useRef, useState } from 'react'

function SearchableSelect({
  id,
  value,
  onChange,
  options,
  allOption,
  searchPlaceholder,
  emptyText = '没有匹配选项',
  searchable = true,
  disabled = false,
  label,
  contained = false,
}) {
  const rootRef = useRef(null)
  const searchRef = useRef(null)
  const [isOpen, setIsOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const [placement, setPlacement] = useState({ up: false, height: 280 })
  const triggerRef = useRef(null)

  const normalizedOptions = useMemo(() => [
    allOption,
    ...options.map((option) => (
      typeof option === 'object'
        ? option
        : { value: String(option), label: String(option) }
    )),
  ], [allOption, options])

  const filteredOptions = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase()
    if (!keyword) return normalizedOptions
    return normalizedOptions.filter((option) => (
      option.label.toLocaleLowerCase().includes(keyword)
    ))
  }, [normalizedOptions, query])

  const selectedOption = normalizedOptions.find((option) => (
    String(option.value) === String(value)
  )) ?? allOption

  useEffect(() => {
    if (!isOpen) return undefined

    const handleOutsidePress = (event) => {
      if (!rootRef.current?.contains(event.target)) setIsOpen(false)
    }

    document.addEventListener('pointerdown', handleOutsidePress)
    return () => document.removeEventListener('pointerdown', handleOutsidePress)
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) return
    setQuery('')
    setActiveIndex(0)
    if (searchable) {
      window.requestAnimationFrame(() => searchRef.current?.focus())
    }
  }, [isOpen, searchable])

  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  useEffect(() => {
    if (!isOpen || !contained) return
    const measure = () => {
      const box = triggerRef.current.getBoundingClientRect()
      const boundary = rootRef.current.closest('.curation-dialog-body')?.getBoundingClientRect()
      const below = Math.min(window.innerHeight, boundary?.bottom ?? window.innerHeight) - box.bottom - 12
      const above = box.top - Math.max(0, boundary?.top ?? 0) - 12
      const up = below < 220 && above > below
      setPlacement({ up, height: Math.max(80, Math.min(280, up ? above : below)) })
    }
    measure(); window.addEventListener('resize', measure); window.addEventListener('scroll', measure, true)
    return () => { window.removeEventListener('resize', measure); window.removeEventListener('scroll', measure, true) }
  }, [isOpen, contained])
  useEffect(() => {
    if (isOpen) rootRef.current?.querySelector(`[id="${id}-option-${activeIndex}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, isOpen, id])
  useEffect(() => { if (disabled) setIsOpen(false) }, [disabled])

  const chooseOption = (option) => {
    onChange(option.value)
    setIsOpen(false)
    triggerRef.current?.focus({ preventScroll: true })
  }

  const handleSearchKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation()
      setIsOpen(false)
      triggerRef.current?.focus({ preventScroll: true })
      return
    }
    if (!filteredOptions.length) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((current) => (current + 1) % filteredOptions.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((current) => (current - 1 + filteredOptions.length) % filteredOptions.length)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      chooseOption(filteredOptions[activeIndex] ?? filteredOptions[0])
    }
  }

  return (
    <div ref={rootRef} className="searchable-select" onKeyDown={event=>{if(isOpen&&event.key==='Escape'){event.preventDefault();event.stopPropagation();setIsOpen(false);triggerRef.current?.focus({preventScroll:true})}}}>
      <button
        id={id}
        ref={triggerRef}
        type="button"
        disabled={disabled}
        aria-label={label}
        className="searchable-select__trigger"
        aria-haspopup="listbox"
        aria-controls={isOpen ? `${id}-options` : undefined}
        aria-expanded={isOpen}
        onClick={() => setIsOpen((open) => !open)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' && !isOpen) {
            event.preventDefault()
            setIsOpen(true)
          }
        }}
      >
        <span className="truncate">{selectedOption.label}</span>
        <i className={`fa-solid fa-chevron-down searchable-select__chevron${isOpen ? ' is-open' : ''}`} aria-hidden="true" />
      </button>

      {isOpen && (
        <div className="searchable-select__panel" style={contained ? { top: placement.up ? 'auto' : 'calc(100% + 7px)', bottom: placement.up ? 'calc(100% + 7px)' : 'auto', maxHeight: placement.height } : undefined}>
          {searchable && (
            <div className="searchable-select__search-wrap">
              <i className="fa-solid fa-magnifying-glass" aria-hidden="true" />
              <input
                ref={searchRef}
                type="search"
                className="searchable-select__search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={handleSearchKeyDown}
                placeholder={searchPlaceholder}
                aria-label={searchPlaceholder}
                aria-controls={`${id}-options`}
                aria-activedescendant={filteredOptions[activeIndex] ? `${id}-option-${activeIndex}` : undefined}
                autoComplete="off"
              />
            </div>
          )}
          <div id={`${id}-options`} className="searchable-select__options" role="listbox">
            {filteredOptions.length ? filteredOptions.map((option, index) => {
              const isSelected = String(option.value) === String(value)
              return (
                <button
                  id={`${id}-option-${index}`}
                  key={option.value}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  className={`searchable-select__option${isSelected ? ' is-selected' : ''}${activeIndex === index ? ' is-active' : ''}`}
                  onPointerMove={() => setActiveIndex(index)}
                  onClick={() => chooseOption(option)}
                >
                  <span>{option.label}</span>
                  {isSelected && <i className="fa-solid fa-check" aria-hidden="true" />}
                </button>
              )
            }) : (
              <p className="searchable-select__empty">{emptyText}</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default SearchableSelect
