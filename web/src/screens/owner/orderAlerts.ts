// 새 주문 알림과 리마인드 (4단계 티켓 08). 화면의 주문 목록이 바뀔 때마다 `sync`에 넘긴다.
// 처음 보는 새 주문(new)이면 바로 한 번 울리고, 접수하지 않은 채 `remindAfterMs`가 지나면 한 번 더 울린다.
// 접수하거나 카드가 사라지면 리마인드를 취소한다. 리마인드는 한 번뿐이다(4초 반복은 발표장에서 시끄럽다, V2 D9).
// 소리·깜빡임은 콜백이 맡는다. 이 모듈은 언제 울릴지만 정한다.
import { ALERT_SETTINGS } from './alertSettings.ts'

export interface AlertOrder {
  id: string
  status: string
}

export interface OrderAlertsOptions {
  /** 처음 보는 새 주문들. 한 번 동기화에 많아야 한 번 불린다(소리 겹침 없이 1회) */
  onArrive: (ids: string[]) => void
  /** 접수 안 한 주문의 리마인드 */
  onRemind: (id: string) => void
  remindAfterMs?: number
}

export class OrderAlerts {
  private readonly options: OrderAlertsOptions
  /** 한 번이라도 본 주문 id. 다시 붙어 snapshot이 와도 또 울리지 않게 */
  private readonly seen = new Set<string>()
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>()

  constructor(options: OrderAlertsOptions) {
    this.options = options
  }

  sync(orders: readonly AlertOrder[]): void {
    const arrived: string[] = []
    const waiting = new Set<string>()
    for (const { id, status } of orders) {
      const isNew = status === 'new'
      if (isNew) waiting.add(id)
      if (this.seen.has(id)) continue
      this.seen.add(id)
      if (!isNew) continue
      arrived.push(id)
      this.timers.set(id, setTimeout(() => this.remind(id), this.remindAfterMs))
    }
    for (const id of [...this.timers.keys()]) {
      if (!waiting.has(id)) this.cancel(id)
    }
    if (arrived.length > 0) this.options.onArrive(arrived)
  }

  /** 화면을 떠날 때. 남은 리마인드를 모두 취소한다 */
  close(): void {
    for (const id of [...this.timers.keys()]) this.cancel(id)
  }

  private get remindAfterMs(): number {
    return this.options.remindAfterMs ?? ALERT_SETTINGS.remindAfterMs
  }

  private remind(id: string): void {
    this.timers.delete(id)
    this.options.onRemind(id)
  }

  private cancel(id: string): void {
    clearTimeout(this.timers.get(id))
    this.timers.delete(id)
  }
}
