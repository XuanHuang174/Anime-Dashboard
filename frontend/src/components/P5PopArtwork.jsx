function P5PopArtwork({
  src,
  alt,
  eyebrow = 'LIVE CUT-IN!',
  className = '',
}) {
  return (
    <div
      className={`p5-pop-art group pointer-events-none absolute z-[15] hidden select-none lg:block ${className}`}
      aria-hidden="true"
    >
      <div className="p5-pop-art__burst" />
      <div className="p5-pop-art__halftone" />
      <span className="p5-pop-art__sticker">{eyebrow}</span>
      <img
        src={src}
        alt={alt}
        draggable="false"
        className="p5-pop-art__image pointer-events-auto"
      />
    </div>
  )
}

export default P5PopArtwork
