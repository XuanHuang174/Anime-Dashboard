export const tagColorOptions = [
  { value: 'pink', label: '粉色' },
  { value: 'rose', label: '玫红' },
  { value: 'blue', label: '蓝色' },
  { value: 'emerald', label: '翡翠' },
  { value: 'amber', label: '琥珀' },
  { value: 'orange', label: '橙色' },
  { value: 'indigo', label: '靛蓝' },
  { value: 'violet', label: '堇紫' },
  { value: 'fuchsia', label: '洋红' },
  { value: 'yellow', label: '黄色' },
  { value: 'purple', label: '紫色' },
  { value: 'cyan', label: '青色' },
  { value: 'lime', label: '青柠' },
  { value: 'sky', label: '天蓝' },
  { value: 'slate', label: '灰色' },
]

const tagColorClasses = {
  pink: 'bg-pink-100 text-pink-700 font-semibold border border-pink-300',
  rose: 'bg-rose-100 text-rose-600 border border-rose-300',
  blue: 'bg-blue-50 text-blue-600 border border-blue-300',
  emerald: 'bg-emerald-50 text-emerald-600 border border-emerald-300',
  amber: 'bg-amber-50 text-amber-600 border border-amber-300',
  orange: 'bg-orange-50 text-orange-600 border border-orange-300',
  indigo: 'bg-indigo-50 text-indigo-600 border border-indigo-300',
  violet: 'bg-violet-50 text-violet-600 border border-violet-300',
  fuchsia: 'bg-fuchsia-50 text-fuchsia-700 border border-fuchsia-300',
  yellow: 'bg-yellow-100 text-yellow-800 border border-yellow-300',
  purple: 'bg-purple-100 text-purple-700 font-semibold border border-purple-300',
  cyan: 'bg-cyan-50 text-cyan-700 font-medium border border-cyan-300 shadow-sm',
  lime: 'bg-lime-50 text-lime-700 font-medium border border-lime-400 shadow-sm',
  sky: 'bg-sky-50 text-sky-700 border border-sky-300 shadow-sm',
  slate: 'bg-slate-50 text-slate-600 border border-slate-300 shadow-sm',
}

const defaultTagColors = {
  真百: 'pink', 轻百: 'rose', 日常: 'blue', 治愈: 'emerald', 萌系: 'amber',
  搞笑: 'orange', 奇幻: 'indigo', 异世界: 'violet', 恋爱: 'fuchsia',
  泡面番: 'yellow', 入坑之作: 'purple', 轩皇力推: 'purple', 原创: 'cyan',
  爱情: 'fuchsia', 校园: 'lime', 剧场版: 'sky', 魔法少女: 'rose',
}

export function getDefaultTagColor(tag) {
  return defaultTagColors[tag] || 'slate'
}

export function getTagClass(tag, color) {
  return tagColorClasses[color || getDefaultTagColor(tag)] || tagColorClasses.slate
}

export function getCoverSrc(item) {
  if (item.cover) return item.cover
  const initials = item.title.replace(/《|》/g, '').substring(0, 3)
  return `data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="120" height="160" viewBox="0 0 120 160"><rect width="100%" height="100%" fill="%23e2e8f0"/><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" font-family="'Noto Sans SC', sans-serif" font-weight="bold" font-size="12" fill="%2364748b">${initials}</text></svg>`
}

export function getSeasonDetails(period) {
  const details = {
    periodStyle: 'bg-white text-slate-600 border-2 border-slate-400 shadow-[2px_2px_0_0_#94a3b8]',
    icon: '',
    listIconClass: '',
    mobileIconClass: '',
    gridIconClass: '',
  }

  if (!period || !period.includes('-')) return details

  const month = period.split('-')[1]
  if (month === '1') {
    return {
      periodStyle: 'bg-sky-50 text-sky-700 border-2 border-sky-500 shadow-[2px_2px_0_0_#38bdf8]',
      icon: 'fa-solid fa-snowflake',
      listIconClass: 'text-sky-400 mr-1.5 animate-[spin_6s_linear_infinite]',
      mobileIconClass: 'text-sky-400 mr-1 text-[10px] animate-[spin_6s_linear_infinite]',
      gridIconClass: 'text-sky-300 animate-[spin_6s_linear_infinite]',
    }
  }
  if (month === '4') {
    return {
      periodStyle: 'bg-green-50 text-green-700 border-2 border-green-500 shadow-[2px_2px_0_0_#22c55e]',
      icon: 'fa-solid fa-seedling',
      listIconClass: 'text-green-500 mr-1.5 animate-pulse',
      mobileIconClass: 'text-green-500 mr-1 text-[10px] animate-pulse',
      gridIconClass: 'text-green-400 animate-pulse',
    }
  }
  if (month === '7') {
    return {
      periodStyle: 'bg-amber-50 text-amber-700 border-2 border-amber-500 shadow-[2px_2px_0_0_#f59e0b]',
      icon: 'fa-solid fa-sun',
      listIconClass: 'text-amber-500 mr-1.5 animate-[spin_4s_linear_infinite]',
      mobileIconClass: 'text-amber-500 mr-1 text-[10px] animate-[spin_4s_linear_infinite]',
      gridIconClass: 'text-amber-400 animate-[spin_4s_linear_infinite]',
    }
  }
  if (month === '10') {
    return {
      periodStyle: 'bg-orange-50 text-orange-700 border-2 border-orange-500 shadow-[2px_2px_0_0_#f97316]',
      icon: 'fa-solid fa-leaf',
      listIconClass: 'text-orange-500 mr-1.5 animate-sway',
      mobileIconClass: 'text-orange-500 mr-1 text-[10px] animate-sway',
      gridIconClass: 'text-orange-400 animate-sway',
    }
  }

  return details
}

export function comparePeriod(p1, p2) {
  if (p1 === '未定档') return 1
  if (p2 === '未定档') return -1

  const parsePeriod = (period) => {
    const parts = period.replace(/待定|上半|下半/g, '').split('-')
    const year = Number.parseInt(parts[0], 10) || 0
    const month = Number.parseInt(parts[1], 10) || 0
    return year * 100 + month
  }

  return parsePeriod(p1) - parsePeriod(p2)
}
