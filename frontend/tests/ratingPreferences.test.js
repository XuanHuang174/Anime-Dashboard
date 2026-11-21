import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { getScoreAccentColor } from '../src/utils/animeDisplay.js'
import { RATING_COLORS, defaultRatingPreferences, legacyRatingPreferences, restoreClassicAppearance, ratingPreferencesError, normalizeRatingPreferences, ratingForScore, histogramHighlight, highlightRange, ratingPreset, ratingStyle } from '../src/utils/ratingPreferences.js'

test('rating defaults match the backend versioned contract and never share mutable defaults', () => {
  const fixture = JSON.parse(readFileSync(new URL('./fixtures/ratingPreferencesContract.json', import.meta.url), 'utf8'))
  assert.deepEqual(defaultRatingPreferences(), fixture)
  const value = defaultRatingPreferences(); value.tiers[0].color = 'red'
  assert.equal(defaultRatingPreferences().tiers[0].color, 'rainbow')
  assert.equal(ratingPreferencesError(fixture), '')
})

test('classic defaults use neon soft glow while the original palettes remain selectable', () => {
  const config = defaultRatingPreferences()
  for (const [score, number, stars] of [[8, '#1e293b', '#fbbf24'], [9.3, '#f97316', '#fbbf24'], [9.5, '#dc2626', '#ea580c']]) {
    assert.equal(ratingStyle(ratingForScore(score, config)).color, number)
    assert.equal(ratingStyle(ratingForScore(score, config), true).color, stars)
  }
  const glow = { color: '#ff2a85', textShadow: '0 1px 7px #b537f255' }
  assert.deepEqual(ratingStyle(ratingForScore(9.9, config)), glow)
  assert.deepEqual(ratingStyle(ratingForScore(9.9, config), true), glow)
  assert.deepEqual(ratingStyle(ratingForScore(9.9, ratingPreset('numbers'))), glow)
  assert.equal(RATING_COLORS.classic.label, '紫蓝渐变')
  config.tiers[0].effect = 'gradient'
  assert.deepEqual(normalizeRatingPreferences(config), config, 'Explicitly saved rainbow gradients remain selectable')
  const neon = 'linear-gradient(to right, #ff2a85, #ff7b00, #ffd000, #00e5ff, #4d7cff, #b537f2, #ff2a85)'
  assert.equal(ratingStyle(ratingForScore(9.9, config)).backgroundImage, neon)
  assert.equal(ratingStyle(ratingForScore(9.9, config), true).backgroundImage, neon)
  config.tiers[0].color = 'classic'; config.tiers[0].star_color = 'classic'
  assert.equal(ratingStyle(ratingForScore(9.9, config)).backgroundImage, 'linear-gradient(to right, #4f46e5, #9333ea, #d946ef)')
  assert.equal(ratingStyle(ratingForScore(9.9, config), true).backgroundImage, 'linear-gradient(to right, #ec4899, #a855f7, #6366f1)')
  assert.equal(getScoreAccentColor(9.9, config), '#a78bfa')
  config.tiers[0].color = 'teal'
  assert.equal(getScoreAccentColor(9.9, config), '#0f766e')
})

test('legacy default upgrade preserves names, star counts, statistics and never mutates the source', () => {
  const old = legacyRatingPreferences(), snapshot = JSON.stringify(old)
  assert.deepEqual(normalizeRatingPreferences(old), defaultRatingPreferences())
  assert.equal(JSON.stringify(old), snapshot)
  old.statistics = { ...old.statistics, min: 7, label: '我的口径', cards: ['highlight'] }
  old.tiers[0].label = '我的殿堂'; old.tiers[0].stars = 3
  const upgraded = normalizeRatingPreferences(old)
  assert.deepEqual(upgraded.statistics, old.statistics)
  assert.equal(upgraded.tiers[0].label, '我的殿堂'); assert.equal(upgraded.tiers[0].stars, 3)
  old.tiers[0].star_color = 'teal'
  assert.deepEqual(normalizeRatingPreferences(old), { ...old, display: { mobile_stars: true, detail_stars: true, poster_style: 'stars' } }, 'Hand-picked legacy palettes and expanded display must remain unchanged')
  old.tiers[0].min = 9.8
  const classic = restoreClassicAppearance(old)
  assert.equal(classic.tiers[0].min, 9.8); assert.equal(classic.tiers[0].stars, 3)
  assert.deepEqual(classic.statistics, old.statistics)
})

test('compact view defaults upgrade old defaults while explicit purple and view choices stay intact', () => {
  const old = defaultRatingPreferences(); delete old.display
  old.tiers[0].color = 'classic'; old.tiers[0].star_color = 'classic'; old.tiers[0].effect = 'gradient'
  assert.equal(normalizeRatingPreferences(old).tiers[0].color, 'rainbow')
  old.display = { mobile_stars: true, detail_stars: true, poster_style: 'stars' }
  assert.deepEqual(normalizeRatingPreferences(old), old)
  assert.deepEqual(defaultRatingPreferences().display, { mobile_stars: false, detail_stars: false, poster_style: 'badge' })
  for (const display of [{ mobile_stars: 'yes', detail_stars: false, poster_style: 'badge' }, { mobile_stars: false, detail_stars: false, poster_style: 'html' }, { mobile_stars: false, poster_style: 'badge' }]) {
    assert.ok(ratingPreferencesError({ ...defaultRatingPreferences(), display }))
  }
})

test('threshold boundaries assign one tier and preserve original numeric scores including real zero', () => {
  const value = defaultRatingPreferences()
  for (const [score, label, stars] of [[10, '殿堂', 5], [9.9, '殿堂', 5], [9.8, '极高推荐', 5], [9.5, '极高推荐', 5], [9, '强烈推荐', 5], [8.9, '推荐', 4], [8, '推荐', 4], [7, '值得一看', 3], [6, '一般', 2], [.1, '其他评分', 1], [0, '零分', 0]]) {
    const result = ratingForScore(score, value)
    assert.equal(result.label, label); assert.equal(result.stars, stars); assert.equal(result.score, score)
  }
  for (const score of [null, undefined, '', false, NaN, Infinity, -1, 11]) assert.equal(ratingForScore(score, value), null)
})

test('histogram preview counts full-collection inclusive ranges and separates unrated from zero', () => {
  const histogram = [{ score: null, count: 20 }, { score: 0, count: 2 }, { score: 8, count: 3 }, { score: 9, count: 4 }, { score: 10, count: 1 }]
  const config = defaultRatingPreferences(); config.statistics.min = 8; config.statistics.max = 9
  assert.equal(histogramHighlight(histogram, config), 7)
  assert.equal(highlightRange(config), '8–9')
  config.statistics.min = 0; config.statistics.max = 0
  assert.equal(histogramHighlight(histogram, config), 2)
})

test('overlap, uncovered ranges, invalid precision, hidden-all cards and arbitrary styles are rejected', () => {
  for (const change of [c => c.tiers[0].min = 9.5, c => c.tiers.at(-1).min = .1, c => c.tiers[0].stars = 11, c => c.statistics.min = 9.99, c => c.statistics.min = NaN, c => c.statistics.max = 1, c => c.statistics.cards = [], c => c.tiers[0].color = 'url(evil)', c => c.tiers[0].effect = 'html']) {
    const config = defaultRatingPreferences(); change(config)
    assert.ok(ratingPreferencesError(config)); assert.deepEqual(normalizeRatingPreferences(config), defaultRatingPreferences())
  }
})

test('presets change presentation and count criteria only, with numeric-only and ten-star support', () => {
  for (const key of ['default', 'bangumi', 'numbers']) assert.equal(ratingPreferencesError(ratingPreset(key)), '')
  assert.equal(ratingPreset('numbers').show_stars, false)
  assert.equal(ratingForScore(8, ratingPreset('bangumi')).label, '力荐')
  const config = defaultRatingPreferences(); config.star_max = 10; config.tiers[0].stars = 10
  assert.equal(ratingForScore(9.9, config).max, 10)
  assert.equal(ratingForScore(9.9, config).stars, 10)
  assert.ok(ratingStyle(ratingForScore(9.9, config)).textShadow)
})
