import { describe, expect, it } from 'vitest'
import { type CallAction, type CallState, type ServerEvent, callReducer, initialCallState } from './callState.ts'

// 쿠폰 티켓 09: 통화 화면의 합계는 서버가 보낸 낼 금액이다. 쿠폰이 붙으면 할인 전 금액·쿠폰 이름이 같이 온다
function run(...actions: CallAction[]): CallState {
  return actions.reduce(callReducer, initialCallState)
}

const dial: CallAction = { type: 'dial' }
const ready: CallAction = { type: 'server', event: { type: 'call.ready', turn: 0, call: 'c1', sample_rate: 16000 } }
const cart = [
  { menu_id: 'm01', name: '메도빅', qty: 1, options: [], request: '', subtotal: 7500 },
  { menu_id: 'm03', name: '아메리카노', qty: 1, options: [], request: '', subtotal: 4500 },
]
const COUPON = '전화 주문 1,000원 할인'
const LABEL = '전화 주문 1,000원 할인 쿠폰'

function state(extra: Partial<Extract<ServerEvent, { type: 'state' }>>): CallAction {
  return {
    type: 'server',
    event: { type: 'state', turn: 3, phase: 'speaking', interruptible: false, dialog: 'confirm', cart, total: 12000, ...extra },
  }
}

describe('coupon amounts on the call screen (coupon ticket 09)', () => {
  it('shows the payable total and the discount from the state event', () => {
    const s = run(dial, ready, state({ total: 11000, subtotal: 12000, discount: 1000, coupon_name: COUPON, coupon_label: LABEL }))
    expect(s.total).toBe(11000)
    expect(s.discount).toEqual({ subtotal: 12000, discount: 1000, coupon_label: LABEL })
  })

  it('drops the discount when a later turn has no coupon (the order was changed)', () => {
    const s = run(dial, ready, state({ total: 11000, subtotal: 12000, discount: 1000, coupon_name: COUPON }), state({ total: 7500 }))
    expect(s.total).toBe(7500)
    expect(s.discount).toBeNull()
  })

  it('keeps the discount of the placed order', () => {
    const order: ServerEvent = {
      type: 'order',
      turn: 4,
      order: { id: 'o1', status: 'confirmed', lines: [], pickup_time: '2026-11-08T18:30:00+09:00', customer_name: null, customer_phone: null, transcript: [] },
      cart,
      total: 11000,
      subtotal: 12000,
      discount: 1000,
      coupon_name: COUPON,
      coupon_label: LABEL,
    }
    const s = run(dial, ready, { type: 'server', event: order }, { type: 'socket.closed' })
    expect(s.order?.total).toBe(11000)
    expect(s.order?.discount).toEqual({ subtotal: 12000, discount: 1000, coupon_label: LABEL })
  })
})
