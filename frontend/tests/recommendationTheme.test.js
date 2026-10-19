import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import postcss from 'postcss'

const theme = await readFile(new URL('../src/styles/recommendations-theme.css', import.meta.url), 'utf8')
const ast = postcss.parse(theme)

test('R2 theme uses only recommendation namespace including responsive and pseudo element selectors', () => {
  let rules = 0
  ast.walkRules(rule => {
    for (const selector of rule.selectors) assert.ok(selector.startsWith('.memphis-rec-container'), selector)
    rules++
  })
  assert.ok(rules > 50)
  assert.doesNotMatch(theme, /@import|url\(|@font-face|@keyframes|animation\s*:/)
})

test('R2 shares journal brand tokens without remote assets or a new motion engine', () => {
  const tokens = {}
  ast.walkRules('.memphis-rec-container', rule => rule.walkDecls(/^--rec-/, decl => { tokens[decl.prop] = decl.value }))
  assert.deepEqual(tokens, { '--rec-cream': '#fff5ee', '--rec-yellow': '#ffe66d', '--rec-teal': '#4ecdc4',
    '--rec-coral': '#ff6b6b', '--rec-ink': '#111', '--rec-muted': '#57534e' })
  const declarations = []
  ast.walkDecls('box-shadow', decl => declarations.push(decl.value))
  assert.ok(declarations.length > 5)
  for (const value of declarations) assert.match(value, /^(?:none|[234]px [234]px 0 var\(--rec-(?:ink|yellow)\))$/)
})

test('R2 preserves evidence visibility and does not clip dropdowns on cards or masthead', () => {
  ast.walkRules(rule => {
    if (rule.selectors.some(selector => /(?:\.rec-card|\.rec-masthead)$/.test(selector))) {
      rule.walkDecls('overflow', decl => assert.notEqual(decl.value, 'hidden'))
    }
  })
  assert.match(theme, /\.rec-card-reason\s*\{[^}]*-webkit-line-clamp: 2/s)
  assert.match(theme, /:focus-visible\s*\{[^}]*outline: 3px solid/s)
  assert.match(theme, /\.rec-feedback-trigger\s*\{[^}]*min-width: 44px; min-height: 44px/s)
})

test('R2 key text/background combinations retain at least 4.5:1 contrast', () => {
  const luminance = hex => {
    const rgb = hex.match(/[a-f\d]{2}/gi).map(part => parseInt(part, 16) / 255)
      .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4)
    return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722
  }
  for (const [text, background] of [['111111', 'ffe66d'], ['111111', '4ecdc4'], ['ffffff', '111111'],
    ['57534e', 'fff5ee'], ['33463e', 'edf8f4']]) {
    const values = [luminance(text), luminance(background)].sort((a, b) => b - a)
    assert.ok((values[0] + .05) / (values[1] + .05) >= 4.5, `${text}/${background}`)
  }
})
