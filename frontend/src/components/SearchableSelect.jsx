import { useEffect, useMemo, useRef, useState } from 'react'

function SearchableSelect({
  id,
  value,
  onChange,
  options,
  allOption,
  searchPlaceholder,
  emptyText = '没有匹配选项',
}) {
  const rootRef = useRef(null)
  const searchRef = useRef(null)
  const [isOpen, setIsOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)

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
    window.requestAnimationFrame(() => searchRef.current?.focus())
  }, [isOpen])

  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  const chooseOption = (option) => {
    onChange(option.value)
    setIsOpen(false)
  }

  const handleSearchKeyDown = (event) => {
    if (event.key === 'Escape') {
      setIsOpen(false)
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
    <div ref={rootRef} className="searchable-select">
      <button
        id={id}
        type="button"
        className="searchable-select__trigger"
        aria-haspopup="listbox"
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
        <div className="searchable-select__panel">
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
