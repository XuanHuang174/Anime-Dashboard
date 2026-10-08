import { test } from 'node:test'
import assert from 'node:assert/strict'
import { cleanResourceKeyword, dmhySearchUrl, hasResourceSearchSession, isSafeMagnet } from '../src/utils/resourceSearch.js'

test('游客不请求接口，保留刷新令牌的用户仍是已登录状态', () => {
  const originalStorage = globalThis.localStorage
  try {
    globalThis.localStorage = { getItem: () => null }
    assert.equal(hasResourceSearchSession(), false)
    globalThis.localStorage = { getItem: (key) => key.includes('refresh') ? 'fixture-refresh' : null }
    assert.equal(hasResourceSearchSession(), true)
    globalThis.localStorage = { getItem: (key) => key.includes('access') ? 'fixture-access' : null }
    assert.equal(hasResourceSearchSession(), true)
  } finally {
    if (originalStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = originalStorage
  }
})

test('清洗中文季数、全角标点和副标题', () => {
  assert.equal(cleanResourceKeyword('《孤独摇滚！》 第二季'), '孤独摇滚')
  assert.equal(cleanResourceKeyword('无职转生 ～到了异世界就拿出真本事～ 第三季'), '无职转生')
  assert.equal(cleanResourceKeyword('我推的孩子 Season 3'), '我推的孩子')
  assert.equal(cleanResourceKeyword('孤独摇滚 - 特别篇'), '孤独摇滚')
  assert.equal(cleanResourceKeyword('刀剑神域 最终季'), '刀剑神域')
  assert.equal(cleanResourceKeyword('Re:从零开始的异世界生活 第三季'), 'Re 从零开始的异世界生活')
  assert.equal(cleanResourceKeyword(''), '')
  assert.equal(cleanResourceKeyword('x'.repeat(250)).length, 200)
})

test('只允许安全的 BT Magnet 协议', () => {
  assert.equal(isSafeMagnet('magnet:?xt=urn:btih:ABC123&dn=demo'), true)
  assert.equal(isSafeMagnet('magnet:?xt=urn:btmh:1220abcd'), true)
  for (const input of ['javascript:alert(1)', 'https://evil.test', '', null, 'magnet:javascript:bad']) {
    assert.equal(isSafeMagnet(input), false)
  }
})

test('网页搜索关键词安全编码', () => {
  assert.equal(dmhySearchUrl(' 孤独摇滚 &a=1 '),
    'https://share.dmhy.org/topics/list?keyword=%E5%AD%A4%E7%8B%AC%E6%91%87%E6%BB%9A%20%26a%3D1')
})
