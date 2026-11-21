// 비서 화면의 전화 주문: 사장님 화면과 같은 SSE(`/api/owner/stream`)를 열어 주문 카드를 받는다.
// 사장님 화면 토큰(key)은 주소나 이 브라우저가 기억한 값을 쓴다. 없으면 연결하지 않는다(서버가 401).
// 비서 화면은 소리를 내지 않는다(주방 태블릿의 사장님 화면이 울린다). 대신 말풍선(`orderNudges`)으로 알린다.
// 이 화면이 연결돼 있으면 서버는 "사장님 화면 있음"으로 보고 자동 확정 대신 확인을 기다린다.
import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import {
  type OrderCard,
  type OwnerAction,
  type OwnerState,
  canAccept,
  initialOwnerState,
  ownerReducer,
  storeNow,
} from '../screens/owner/ownerState.ts'
import {
  type AcceptDelay,
  acceptOrder,
  connectOwnerStream,
  ownerKey,
  rejectOrder,
  undoSoldOut,
} from '../screens/owner/ownerStream.ts'
import type { RejectBody } from '../screens/owner/rejectSheet.ts'
import { type Nudge, OrderNudges } from './orderNudges.ts'

export interface OrdersApi {
  connect: (key: string, dispatch: (a: OwnerAction) => void) => () => void
  accept: (key: string, orderId: string, delayMin: AcceptDelay) => Promise<boolean>
  reject: (key: string, orderId: string, body: RejectBody) => Promise<boolean>
  undoSoldOut: (key: string, menuId: string) => Promise<boolean>
}

export const httpOrdersApi: OrdersApi = {
  // 테스트(jsdom)처럼 EventSource가 없는 곳에서는 붙지 않는다
  connect: (key, dispatch) => (typeof EventSource === 'undefined' ? () => {} : connectOwnerStream(key, dispatch)),
  accept: acceptOrder,
  reject: rejectOrder,
  undoSoldOut,
}

/** 확인 대기 카드가 있으면 카운트다운을 초마다, 없으면 "N분 전"만 이 간격으로 다시 그린다 */
const TICK_WAITING_MS = 1_000
const TICK_IDLE_MS = 15_000

export interface Orders {
  /** 사장님 화면 토큰이 없어 연결하지 않았다 */
  noKey: boolean
  key: string
  state: OwnerState
  /** 허브 시계로 본 지금(ms) */
  nowMs: number
  /** 확인 대기(사장님이 확인·거절할 것) */
  waiting: OrderCard[]
  /** 확인함(늦춘 확인·자동 확정 포함) */
  confirmed: OrderCard[]
  /** 버튼을 누르고 서버 답을 기다리는 주문 */
  busy: ReadonlySet<string>
  /** 마지막으로 실패한 주문 id (그 카드에 "다시 눌러 주세요") */
  failed: string | null
  accept: (orderId: string, delayMin: AcceptDelay) => void
  reject: (orderId: string, body: RejectBody) => void
  undoSoldOut: (menuId: string) => void
}

export function useOrders(api: OrdersApi, onNudge: (nudge: Nudge) => void): Orders {
  const [key] = useState(() => ownerKey(window.location.search))
  const [state, dispatch] = useReducer(ownerReducer, initialOwnerState)
  const [deviceNow, setDeviceNow] = useState(() => Date.now())
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set())
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    if (!key) return
    dispatch({ type: 'open' })
    // 이벤트가 오면 시계도 다시 읽는다(다음 틱까지 "N분 전"·카운트다운이 멈춰 있지 않게)
    return api.connect(key, (action) => {
      dispatch(action)
      setDeviceNow(Date.now())
    })
  }, [api, key])

  const waiting = state.orders.filter(canAccept)
  const hasWaiting = waiting.length > 0
  useEffect(() => {
    const timer = setInterval(() => setDeviceNow(Date.now()), hasWaiting ? TICK_WAITING_MS : TICK_IDLE_MS)
    return () => clearInterval(timer)
  }, [hasWaiting])

  const nowMs = storeNow(state, deviceNow)
  const nudges = useRef(new OrderNudges())
  const latestNudge = useRef(onNudge)
  useEffect(() => {
    latestNudge.current = onNudge
  })
  useEffect(() => {
    if (state.connection !== 'live') return
    for (const nudge of nudges.current.sync(state.orders, nowMs)) latestNudge.current(nudge)
  }, [state.connection, state.orders, nowMs])

  const run = useCallback(
    (orderId: string, send: () => Promise<boolean>) => {
      setBusy((b) => new Set(b).add(orderId))
      setFailed(null)
      send()
        .catch(() => false)
        .then((ok) => {
          if (!ok) setFailed(orderId)
          setBusy((b) => {
            const next = new Set(b)
            next.delete(orderId)
            return next
          })
        })
    },
    [],
  )

  return {
    noKey: !key,
    key,
    state,
    nowMs,
    waiting,
    confirmed: state.orders.filter((o) => o.status === 'accepted'),
    busy,
    failed,
    accept: (orderId, delayMin) => run(orderId, () => api.accept(key, orderId, delayMin)),
    reject: (orderId, body) => run(orderId, () => api.reject(key, orderId, body)),
    undoSoldOut: (menuId) => void api.undoSoldOut(key, menuId).catch(() => false),
  }
}
