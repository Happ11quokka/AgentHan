// 주문 현황 다시 열기 (전화 메뉴·주문 현황·쿠폰 티켓 06, spec "주문 현황: 통화 중 대기를 없앤다").
// 주문 번호는 주소(`/phone/orders/<주문 번호>`)와 브라우저 저장소에 남는다. 새로 고치거나 새 탭으로 열어도
// 주소만으로 같은 주문 현황이 뜨고, `/phone`을 다시 열면 아직 끝나지 않은 주문(가게 확인 중, 확정됐고 픽업
// 시각 전)으로 간다. [새로 전화하기]는 저장소를 비우고 통화 화면으로 돌아간다. 저장소를 못 쓰는 브라우저(비공개
// 창 등)에서는 주소만으로 돈다. 화면이 어느 모습인지(통화·주문 현황·다시 여는 중)를 여기서 정한다.
import type { OrderStatusAction, OrderStatusJson } from './orderStatus.ts'

export const PHONE_PATH = '/phone'
const ORDER_PREFIX = '/phone/orders/'

/** 주문 현황 주소. 주문 번호는 통화 id라 그대로 써도 되지만 주소에 넣을 때는 늘 감싼다 */
export function orderPath(orderId: string): string {
  return ORDER_PREFIX + encodeURIComponent(orderId)
}

/** 주문 현황 주소면 그 주문 번호, 아니면 null. 끝의 `/`는 무시한다 */
export function orderIdFromPath(pathname: string): string | null {
  const path = pathname.replace(/\/+$/, '')
  if (!path.startsWith(ORDER_PREFIX)) return null
  const raw = path.slice(ORDER_PREFIX.length)
  if (!raw || raw.includes('/')) return null
  try {
    return decodeURIComponent(raw) || null
  } catch {
    return null // 깨진 % 표기
  }
}

/** 손님 전화 화면(`/phone`, `/phone/orders/<주문 번호>`)인가. `main.tsx`가 화면을 고를 때 쓴다 */
export function isPhonePath(pathname: string): boolean {
  const path = pathname.replace(/\/+$/, '')
  return path === PHONE_PATH || orderIdFromPath(path) !== null
}

// ------------------------------------------------------------------ 브라우저 저장소

const ORDER_KEY = 'agenthan.phone.order.v1'

/** 마지막 주문 번호 한 개. 저장소가 막혀도(비공개 창·차단) 던지지 않는다: 읽기는 null, 쓰기는 버린다 */
export interface OrderMemory {
  recall(): string | null
  remember(orderId: string): void
  forget(): void
}

export function browserOrderMemory(storage: () => Storage = () => window.localStorage): OrderMemory {
  return {
    recall() {
      try {
        return storage().getItem(ORDER_KEY) || null
      } catch {
        return null
      }
    },
    remember(orderId) {
      try {
        storage().setItem(ORDER_KEY, orderId)
      } catch {
        // 저장소를 못 쓰는 브라우저: 주소만으로 돈다
      }
    },
    forget() {
      try {
        storage().removeItem(ORDER_KEY)
      } catch {
        // 위와 같다
      }
    },
  }
}

// ------------------------------------------------------------------ 화면 모습

/** call: 통화 화면 · order: 주문 현황 · resuming: `/phone`에서 남은 주문을 확인하는 중(잠깐) */
export type PhoneView = { kind: 'call' } | { kind: 'order'; orderId: string } | { kind: 'resuming'; orderId: string }

/** 아직 끝나지 않은 주문: 가게 확인 중이거나, 확정됐고 픽업 시각 전. 취소된 주문은 끝났다 */
export function isUnfinished(order: OrderStatusJson, now: Date): boolean {
  if (order.stage === 'checking') return true
  if (order.stage !== 'confirmed') return false
  const pickup = Date.parse(order.pickup.time)
  return Number.isFinite(pickup) && pickup > now.getTime()
}

/** 처음 열 때: 주문 현황 주소면 그 주문, `/phone`인데 남겨 둔 주문이 있으면 확인부터, 아니면 통화 화면 */
export function initialPhoneView(pathname: string, remembered: string | null): PhoneView {
  const orderId = orderIdFromPath(pathname)
  if (orderId) return { kind: 'order', orderId }
  return remembered ? { kind: 'resuming', orderId: remembered } : { kind: 'call' }
}

/**
 * 남겨 둔 주문을 읽은 결과 → 다음 모습. 끝나지 않은 주문이면 주문 현황, 아니면(끝남·없음·못 읽음) 통화 화면.
 * `forget`: 저장소의 주문 번호를 지울지. 못 읽었을 때(회선)는 남겨 두고 다음에 다시 본다
 */
export function resumedView(
  orderId: string,
  action: OrderStatusAction,
  now: Date,
): { view: PhoneView; forget: boolean } {
  if (action.type === 'loaded' && action.order.order_id === orderId && isUnfinished(action.order, now)) {
    return { view: { kind: 'order', orderId }, forget: false }
  }
  return { view: { kind: 'call' }, forget: action.type !== 'failed' }
}
