function AddMark() {
  return (
    <span className="add-anime-placeholder-mark" aria-hidden="true">
      <i className="fa-solid fa-plus" />
    </span>
  )
}

export function AddAnimeGridPlaceholder({ onAddAnime }) {
  return (
    <button
      type="button"
      onClick={onAddAnime}
      className="add-anime-placeholder add-anime-placeholder-grid animate-item group"
      aria-label="添加一部新番剧"
    >
      <span className="add-anime-placeholder-dots" aria-hidden="true" />
      <AddMark />
      <strong>添加番剧</strong>
      <span className="add-anime-placeholder-caption">EXPAND YOUR JOURNAL</span>
      <span className="add-anime-placeholder-sticker">ADD +1</span>
    </button>
  )
}

export function AddAnimeListPlaceholder({ onAddAnime }) {
  return (
    <tr className="add-anime-placeholder-row animate-item">
      <td colSpan="7" className="p-3 sm:p-4">
        <button
          type="button"
          onClick={onAddAnime}
          className="add-anime-placeholder add-anime-placeholder-list group"
        >
          <AddMark />
          <span className="min-w-0 text-left">
            <strong className="block text-base font-black sm:text-lg">继续扩充你的番剧手账</strong>
            <span className="mt-0.5 block text-[10px] font-black tracking-[0.14em] sm:text-xs">
              ADD A NEW ANIME RECORD
            </span>
          </span>
          <span className="add-anime-placeholder-list-cta">
            添加番剧 <i className="fa-solid fa-arrow-right" />
          </span>
        </button>
      </td>
    </tr>
  )
}
