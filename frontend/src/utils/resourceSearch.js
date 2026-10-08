import { getAccessToken, getRefreshToken } from '../auth/tokenStorage.js'

export function hasResourceSearchSession() {
  return Boolean(getAccessToken() || getRefreshToken())
}

export function cleanResourceKeyword(title = '') {
  const normalized = String(title).normalize('NFKC').trim()
  // Keep a core franchise title, not subtitles separated with waves/colon.
  const core = normalized.replace(/第\s*[0-9一二三四五六七八九十百]+\s*[季期部]/g, '')
    .replace(/\b(?:season\s*\d+|s\d+|\d+(?:st|nd|rd|th)\s+season)\b/gi, '')
    .replace(/(?:最终季|最終季|完结篇|完結編|续篇|続編|第二部分|part\s*\d+)\s*$/gi, '')
    .replace(/\s*[-—–]\s*[^-—–]+$/, '')
    .split(/[~〜～]/)[0]
    .replace(/[《》「」『』【】\[\]!?！？。、,，.。·・:：—–\-]/g, ' ')
    .replace(/\s+/g, ' ').trim()
  return (core || normalized).slice(0, 200)
}

export function isSafeMagnet(value) {
  return typeof value === 'string'
    && /^magnet:\?xt=urn:bt(?:ih|mh):[a-z0-9]+(?:&|$)/i.test(value)
}

export function dmhySearchUrl(keyword) {
  return `https://share.dmhy.org/topics/list?keyword=${encodeURIComponent(keyword.trim())}`
}
