import { useState } from 'react'

export default function CurationArtwork({ source }) {
  const [failed, setFailed] = useState(null)
  return source && failed !== source
    ? <img src={source} alt="" loading="lazy" onError={() => setFailed(source)}/>
    : <span aria-hidden="true">▤</span>
}
