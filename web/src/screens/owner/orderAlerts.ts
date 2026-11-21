// 새 주문 알림과 반복 알림 (4단계 티켓 08 → 주문 승인 티켓 08). 화면의 주문 목록이 바뀔 때마다 `sync`에 넘긴다.
// 처음 보는 새 주문(new)이면 바로 한 번 울린다. 확인 대기 카드(new)가 하나라도 남아 있으면
// `repeatEveryMs`마다 다시 울리고, 모두 처리되면(수락·거절·자동 취소·손님이 거둠) 멈춘다.
// 예전 "1분 뒤 한 번 더" 리마인드는 이 반복으로 바뀌었다(멘토링: 조리 중에도 놓치지 않게).
// 소리 끄기(`soundOn`이 false)면 울리지 않는다. 반복 때 깜빡임(`onRepeat`)은 소리와 따로 계속한다.
// 이 모듈은 언제 울릴지만 정한다. 소리·깜빡임은 콜백이 맡는다.
// 주문 승인(티켓 02): 확인 요청 없이 확정된 자동 확정 카드(accepted + auto_accepted)도 처음 볼 때 한 번 울린다
// (승인을 꺼도 새 주문 소리는 난다). 반복은 하지 않는다(기다리는 결정이 없다).
import { ALERT_SETTINGS } from './alertSettings.ts'

export interface AlertOrder {
  id: string
  status: string
  auto_accepted?: boolean
  /** 손님이 고치는 중·취소한 확인 요청: 결정을 받지 않으니 기다리는 주문이 아니다 */
  withdrawn?: string
  /** 확인 요청 번호. 손님이 고쳐 같은 카드에 새 요청이 오면 번호가 바뀐다(새로 온 것으로 울린다) */
  request?: number
}

export interface OrderAlertsOptions {
  /** 알림음 한 번. 한 번 동기화·한 번 반복에 많아야 한 번 불린다(소리 겹침 없이) */
  ring: () => void
  /** 기존 소리 설정. false면 `ring`을 부르지 않는다. 생략하면 켜짐 */
  soundOn?: () => boolean
  /** 반복 때마다 아직 확인 대기인 주문 id들(깜빡임). 소리 설정과 상관없이 불린다 */
  onRepeat?: (ids: string[]) => void
  repeatEveryMs?: number
}

export class OrderAlerts {
  private readonly options: OrderAlertsOptions
  /**
   * 한 번이라도 본 (주문 id, 요청 번호). 다시 붙어 snapshot이 와도 또 울리지 않게. 같은 카드라도 새 요청
   * 번호면 새로 온 것이다(손님이 고쳐 다시 요청함, 주문 승인 리뷰 6번)
   */
  private readonly seen = new Set<string>()
  /** 지금 확인 대기인 주문 id (sync 순서) */
  private waiting: string[] = []
  private timer: ReturnType<typeof setInterval> | null = null

  constructor(options: OrderAlertsOptions) {
    this.options = options
  }

  sync(orders: readonly AlertOrder[]): void {
    let arrived = false
    const waiting: string[] = []
    for (const { id, status, auto_accepted, withdrawn, request } of orders) {
      const isNew = status === 'new' && !withdrawn
      if (isNew) waiting.push(id)
      const key = `${id}#${request ?? 0}`
      if (this.seen.has(key)) continue
      this.seen.add(key)
      if (isNew || auto_accepted) arrived = true
    }
    this.waiting = waiting
    if (waiting.length === 0) this.stop()
    else this.timer ??= setInterval(() => this.repeat(), this.repeatEveryMs)
    if (arrived) this.ring()
  }

  /** 화면을 떠날 때. 반복을 멈춘다 */
  close(): void {
    this.stop()
  }

  private get repeatEveryMs(): number {
    return this.options.repeatEveryMs ?? ALERT_SETTINGS.repeatEveryMs
  }

  private repeat(): void {
    this.ring()
    this.options.onRepeat?.([...this.waiting])
  }

  private ring(): void {
    if (this.options.soundOn?.() ?? true) this.options.ring()
  }

  private stop(): void {
    if (this.timer !== null) clearInterval(this.timer)
    this.timer = null
  }
}
