import { describe, expect, it } from 'vitest'
import type { OrderCard, OrderCardLine } from '../screens/owner/ownerState.ts'
import { OrderNudges, linesSummary } from './orderNudges.ts'

const REQUESTED = '2026-11-08T16:02:00+09:00'
const DEADLINE = Date.parse('2026-11-08T16:07:00+09:00')

function line(name: string, quantity: number): OrderCardLine {
  return { menu_id: name, name_ko: name, name_owner: name, quantity, options: [], options_owner: [], request: '', request_owner: '', badges: [], subtotal: 0 }
}

function card(over: Partial<OrderCard> = {}): OrderCard {
  return {
    id: 'call-1',
    status: 'new',
    received_at: REQUESTED,
    pickup_time: '2026-11-08T18:30:00+09:00',
    pickup_label: '18:30',
    lines: [line('떡볶이', 2), line('순대', 1)],
    total: 23000,
    total_label: '23,000₩',
    request: 1,
    stage: 'after_call',
    requested_at: REQUESTED,
    deadline_at: '2026-11-08T16:07:00+09:00',
    ...over,
  }
}

const early = DEADLINE - 4 * 60_000

describe('새 주문 말풍선', () => {
  it('화면을 연 뒤 처음 보는 확인 대기 주문을 한 번 알린다', () => {
    const nudges = new OrderNudges()
    expect(nudges.sync([], early)).toEqual([])
    expect(nudges.sync([card()], early)).toEqual([
      { kind: 'arrived', text: '전화 주문이 들어왔어요. 18:30 픽업, 떡볶이 2, 순대 1', orderIds: ['call-1'] },
    ])
    expect(nudges.sync([card()], early + 1000)).toEqual([])
  })

  it('화면을 열 때 이미 있던 주문은 새 주문으로 알리지 않는다', () => {
    const nudges = new OrderNudges()
    expect(nudges.sync([card()], early)).toEqual([])
  })

  it('같은 때 여러 건이 오면 한 말풍선으로 묶는다', () => {
    const nudges = new OrderNudges()
    nudges.sync([], early)
    const [nudge] = nudges.sync([card(), card({ id: 'call-2' })], early)
    expect(nudge.text).toBe('전화 주문 2건이 새로 들어왔어요.')
    expect(nudge.orderIds).toEqual(['call-1', 'call-2'])
  })

  it('손님이 고쳐 다시 요청하면(요청 번호가 바뀜) 새 주문으로 알린다', () => {
    const nudges = new OrderNudges()
    nudges.sync([], early)
    nudges.sync([card()], early)
    expect(nudges.sync([card({ request: 2 })], early).map((n) => n.kind)).toEqual(['arrived'])
  })

  it('자동 확정 주문은 확인할 게 없어 알리지 않는다', () => {
    const nudges = new OrderNudges()
    nudges.sync([], early)
    expect(nudges.sync([card({ status: 'accepted', auto_accepted: true, request: 0 })], early)).toEqual([])
  })
})

describe('마감 임박 말풍선', () => {
  it('마감 1분 전에 한 번, 확인 안 한 주문 수와 남은 시간을 알린다', () => {
    const nudges = new OrderNudges()
    nudges.sync([card({ id: 'call-0', deadline_at: '2026-11-08T16:10:00+09:00' })], early)
    expect(nudges.sync([card({ id: 'call-0', deadline_at: '2026-11-08T16:10:00+09:00' }), card()], DEADLINE - 61_000)).toEqual([
      expect.objectContaining({ kind: 'arrived' }),
    ])
    expect(nudges.sync([card({ id: 'call-0', deadline_at: '2026-11-08T16:10:00+09:00' }), card()], DEADLINE - 45_000)).toEqual([
      { kind: 'due', text: '확인 안 한 주문이 2건 있어요. 45초 안에 확인하지 않으면 자동으로 취소돼요.', orderIds: ['call-1'] },
    ])
    expect(nudges.sync([card({ id: 'call-0', deadline_at: '2026-11-08T16:10:00+09:00' }), card()], DEADLINE - 30_000)).toEqual([])
  })

  it('화면을 열었을 때 이미 마감이 가까우면 바로 알린다', () => {
    const nudges = new OrderNudges()
    expect(nudges.sync([card()], DEADLINE - 30_000).map((n) => n.kind)).toEqual(['due'])
  })

  it('손님이 전화로 기다리는 확인(통화 중)은 마감 임박을 따로 띄우지 않는다', () => {
    const nudges = new OrderNudges()
    nudges.sync([], early)
    const inCall = card({ stage: 'in_call', deadline_at: new Date(early + 20_000).toISOString() })
    expect(nudges.sync([inCall], early).map((n) => n.kind)).toEqual(['arrived'])
    expect(nudges.sync([inCall], early + 10_000)).toEqual([])
  })

  it('확인한 주문은 알리지 않는다', () => {
    const nudges = new OrderNudges()
    expect(nudges.sync([card({ status: 'accepted' })], DEADLINE - 30_000)).toEqual([])
  })
})

describe('마감 지남 말풍선', () => {
  it('대기 중이던 주문이 자동 취소되면 한 번 알린다', () => {
    const nudges = new OrderNudges()
    nudges.sync([card()], DEADLINE - 30_000)
    expect(nudges.sync([card({ status: 'expired' })], DEADLINE + 1000)).toEqual([
      { kind: 'late', text: '18:30 픽업 주문을 시간 안에 확인하지 못해 자동으로 취소됐어요.', orderIds: ['call-1'] },
    ])
    expect(nudges.sync([card({ status: 'expired' })], DEADLINE + 2000)).toEqual([])
  })

  it('마감이 지났는데 아직 대기로 남아 있어도 한 번 알린다', () => {
    const nudges = new OrderNudges()
    nudges.sync([card()], DEADLINE - 30_000)
    expect(nudges.sync([card()], DEADLINE + 1000)).toEqual([
      { kind: 'late', text: '18:30 픽업 주문의 확인 시간이 지났어요.', orderIds: ['call-1'] },
    ])
    expect(nudges.sync([card({ status: 'expired' })], DEADLINE + 2000)).toEqual([])
  })

  it('화면을 열 때 이미 자동 취소된 주문은 알리지 않는다', () => {
    const nudges = new OrderNudges()
    expect(nudges.sync([card({ status: 'expired' })], DEADLINE + 1000)).toEqual([])
  })
})

describe('줄 요약', () => {
  it('세 줄이 넘으면 나머지는 개수로', () => {
    expect(linesSummary({ lines: [line('a', 1), line('b', 2), line('c', 1), line('d', 1), line('e', 3)] })).toBe('a 1, b 2, c 1 외 2개')
  })
})
