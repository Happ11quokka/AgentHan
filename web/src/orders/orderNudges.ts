// 비서 화면의 전화 주문 말풍선: 언제 무엇을 알릴지만 정한다(띄우기·채팅 공지는 부른 쪽 몫).
// - arrived: 처음 보는 확인 대기 주문(같은 카드라도 새 요청 번호면 새 주문). 잠깐 떴다 사라진다.
// - due: 통화 뒤 확인 대기가 마감 `DUE_BEFORE_MS` 안으로 들어왔다. 주문마다 한 번, 같은 때 걸린 건 하나로 묶는다.
// - late: 확인하지 못한 채 마감이 지났다(서버가 자동 취소했거나 아직 대기로 남음). 주문마다 한 번.
// 화면을 열었을 때 이미 있던 주문은 arrived·late로 알리지 않는다(첫 sync는 기억만 한다). due는 첫 sync에도 알린다.
// 통화 중 확인(in_call)은 손님이 전화로 기다리는 몇십 초라 due를 따로 띄우지 않는다(arrived로 충분하다).
import { type OrderCard, approvalClock, canAccept } from '../screens/owner/ownerState.ts'

/** 마감이 이만큼 남으면 "곧 마감" 말풍선 */
export const DUE_BEFORE_MS = 60_000

export type NudgeKind = 'arrived' | 'due' | 'late'

export interface Nudge {
  kind: NudgeKind
  text: string
  orderIds: string[]
}

const requestKey = (card: Pick<OrderCard, 'id' | 'request'>) => `${card.id}#${card.request ?? 0}`

/** 줄 요약 "떡볶이 2, 순대 1" (세 줄이 넘으면 "외 N개") */
export function linesSummary(card: Pick<OrderCard, 'lines'>): string {
  const parts = card.lines.slice(0, 3).map((l) => `${l.name_ko} ${l.quantity}`)
  const rest = card.lines.length - parts.length
  return rest > 0 ? `${parts.join(', ')} 외 ${rest}개` : parts.join(', ')
}

function arrivedText(cards: OrderCard[]): string {
  if (cards.length > 1) return `전화 주문 ${cards.length}건이 새로 들어왔어요.`
  const [card] = cards
  const pickup = card.pickup_label ? `${card.pickup_label} 픽업, ` : ''
  return `전화 주문이 들어왔어요. ${pickup}${linesSummary(card)}`
}

function dueText(waiting: number, soonest: number): string {
  const left = soonest < 60 ? `${soonest}초` : `${Math.ceil(soonest / 60)}분`
  return `확인 안 한 주문이 ${waiting}건 있어요. ${left} 안에 확인하지 않으면 자동으로 취소돼요.`
}

function lateText(cards: OrderCard[]): string {
  if (cards.length > 1) return `확인 시간이 지난 주문이 ${cards.length}건 있어요.`
  const [card] = cards
  const pickup = card.pickup_label ? `${card.pickup_label} 픽업 ` : ''
  return card.status === 'expired'
    ? `${pickup}주문을 시간 안에 확인하지 못해 자동으로 취소됐어요.`
    : `${pickup}주문의 확인 시간이 지났어요.`
}

export class OrderNudges {
  private primed = false
  private readonly arrived = new Set<string>()
  private readonly due = new Set<string>()
  private readonly late = new Set<string>()
  /** 지난 sync에서 확인 대기였던 요청. 대기에서 자동 취소로 바뀐 것을 late로 알린다 */
  private waitingBefore = new Set<string>()

  /** 주문 목록이 바뀔 때와 시계가 갈 때마다 부른다. 새로 알릴 것만 돌려준다 */
  sync(orders: readonly OrderCard[], nowMs: number): Nudge[] {
    const first = !this.primed
    this.primed = true
    const fresh: OrderCard[] = []
    const dueNow: OrderCard[] = []
    const lateNow: OrderCard[] = []
    const waiting = orders.filter(canAccept)
    let soonest = Infinity

    for (const card of orders) {
      const key = requestKey(card)
      const isWaiting = canAccept(card)
      if (isWaiting && !this.arrived.has(key)) {
        this.arrived.add(key)
        if (!first) fresh.push(card)
      }
      if (this.late.has(key)) continue
      const clock = isWaiting && card.deadline_at ? approvalClock(card, nowMs) : null
      const expiredNow = card.status === 'expired' && this.waitingBefore.has(key)
      if (clock === 'late' || expiredNow) {
        this.late.add(key)
        this.due.add(key)
        if (!first) lateNow.push(card)
        continue
      }
      if (typeof clock === 'number' && card.stage !== 'in_call') {
        soonest = Math.min(soonest, clock)
        if (clock * 1000 <= DUE_BEFORE_MS && !this.due.has(key)) {
          this.due.add(key)
          dueNow.push(card)
        }
      }
    }
    this.waitingBefore = new Set(waiting.map(requestKey))

    const nudges: Nudge[] = []
    if (fresh.length) nudges.push({ kind: 'arrived', text: arrivedText(fresh), orderIds: fresh.map((c) => c.id) })
    if (dueNow.length) nudges.push({ kind: 'due', text: dueText(waiting.length, soonest), orderIds: dueNow.map((c) => c.id) })
    if (lateNow.length) nudges.push({ kind: 'late', text: lateText(lateNow), orderIds: lateNow.map((c) => c.id) })
    return nudges
  }
}
