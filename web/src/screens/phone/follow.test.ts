import { describe, expect, it } from 'vitest'
import { FOLLOW_SLACK_PX, nearBottom } from './follow.ts'

describe('nearBottom', () => {
  it('맨 아래(또는 거의 맨 아래)면 새 글을 따라간다', () => {
    expect(nearBottom(1000, 600, 400)).toBe(true)
    expect(nearBottom(1000, 600 - FOLLOW_SLACK_PX, 400)).toBe(true)
  })
  it('위로 올려 예전 글을 보는 중이면 따라가지 않는다', () => {
    expect(nearBottom(1000, 300, 400)).toBe(false)
  })
  it('글이 화면보다 짧으면 늘 따라간다', () => {
    expect(nearBottom(300, 0, 400)).toBe(true)
  })
})
