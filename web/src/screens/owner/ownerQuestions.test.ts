import { describe, expect, it } from 'vitest'
import {
  SeenReports,
  type OwnerAction,
  type OwnerState,
  type QuestionCard,
  canAnswer,
  initialOwnerState,
  needsSeen,
  questionClock,
  openQuestions,
  ownerReducer,
  secondsLeft,
  storeNow,
} from './ownerState.ts'

function run(...actions: OwnerAction[]): OwnerState {
  return actions.reduce(ownerReducer, initialOwnerState)
}

function question(id: string, status: QuestionCard['status'] = 'open'): QuestionCard {
  return {
    id,
    call_id: 'call-1',
    number: 1,
    menu_id: 'm01',
    menu_name: '메도빅',
    menu_name_owner: 'Медовик',
    text: '메도빅에 견과류 들어가요?',
    text_owner: '',
    sent_at: '2026-11-08T16:02:00+09:00',
    deadline_at: '2026-11-08T16:02:20+09:00',
    status,
    answer: status === 'answered' ? 'yes' : '',
  }
}

const asked = (card: QuestionCard): OwnerAction => ({ type: 'owner.question', data: card })
const changed = (card: QuestionCard): OwnerAction => ({ type: 'owner.question.update', data: card })
const withQuestions = (...questions: QuestionCard[]): OwnerAction => ({
  type: 'snapshot',
  data: { orders: [], calls: [], questions },
})
const ids = (cards: QuestionCard[]) => cards.map((q) => q.id)

describe('owner screen question cards (ticket 05)', () => {
  it('shows a new question card and keeps the earlier one on top', () => {
    const s = run(withQuestions(), asked(question('call-1-q1')), asked(question('call-2-q1')))
    expect(ids(openQuestions(s))).toEqual(['call-1-q1', 'call-2-q1'])
    expect(canAnswer(openQuestions(s)[0])).toBe(true)
  })

  it('takes the question cards from the snapshot', () => {
    const s = run(withQuestions(question('call-1-q1')))
    expect(ids(openQuestions(s))).toEqual(['call-1-q1'])
  })

  it('treats a snapshot without questions as none', () => {
    const s = run(withQuestions(question('a')), { type: 'snapshot', data: { orders: [], calls: [] } })
    expect(s.questions).toEqual([])
  })

  it('takes an answered card off the buttons', () => {
    const s = run(withQuestions(question('call-1-q1')), changed(question('call-1-q1', 'answered')))
    expect(openQuestions(s)).toEqual([])
    expect(canAnswer(s.questions[0])).toBe(false)
  })

  it('counts down whole seconds to the deadline and stops at zero', () => {
    const card = question('call-1-q1')
    const sent = Date.parse(card.sent_at)
    expect(secondsLeft(card, sent)).toBe(20)
    expect(secondsLeft(card, sent + 7_400)).toBe(13)
    expect(secondsLeft(card, sent + 20_000)).toBe(0)
    expect(secondsLeft(card, sent + 60_000)).toBe(0)
  })
})

describe('owner screen question timing (ticket 06)', () => {
  it('keeps timed-out and call-ended cards answerable on screen', () => {
    const s = run(
      withQuestions(question('call-1-q1'), question('call-2-q1')),
      changed(question('call-1-q1', 'timeout')),
      changed(question('call-2-q1', 'call_ended')),
    )
    expect(ids(openQuestions(s))).toEqual(['call-1-q1', 'call-2-q1'])
    expect(openQuestions(s).every(canAnswer)).toBe(true)
  })

  it('shows the countdown while open, then why the wait is over', () => {
    const card = question('call-1-q1')
    const sent = Date.parse(card.sent_at)
    expect(questionClock(card, sent + 7_400)).toBe(13)
    expect(questionClock(card, sent + 25_000)).toBe('late')
    expect(questionClock(question('call-1-q1', 'timeout'), sent)).toBe('late')
    expect(questionClock(question('call-1-q1', 'call_ended'), sent)).toBe('ended')
  })

  it('reports a card as seen once, only while the call is still waiting', () => {
    expect(needsSeen(question('a'))).toBe(true)
    expect(needsSeen({ ...question('a'), seen_at: '2026-11-08T16:02:03+09:00' })).toBe(false)
    expect(needsSeen(question('a', 'timeout'))).toBe(false)
    expect(needsSeen(question('a', 'answered'))).toBe(false)
  })
})

describe('store clock on the owner screen (review C2)', () => {
  it('counts down on the hub clock, not the device clock', () => {
    // 시연(`PHONE_NOW`)·시계가 틀린 태블릿: snapshot의 허브 시각과 받은 때 기기 시각의 차이로 맞춘다
    const device = Date.parse('2030-01-01T00:00:00Z')
    const s = run({
      type: 'snapshot',
      data: { orders: [], calls: [], questions: [question('call-1-q1')], now: '2026-11-08T16:02:05+09:00' },
      receivedAt: device,
    })
    expect(storeNow(s, device + 1_000)).toBe(Date.parse('2026-11-08T16:02:06+09:00'))
    expect(questionClock(s.questions[0], storeNow(s, device + 1_000))).toBe(14)
  })

  it('uses the device clock when the server sends no time', () => {
    const s = run(withQuestions(question('call-1-q1')))
    expect(storeNow(s, 1234)).toBe(1234)
  })
})

describe('seen signal only when the owner opens the card (review P4)', () => {
  it('reports a waiting card once, when the owner opens it', () => {
    // 화면이 켜져 있는 것만으로는 "사장님이 보고 계세요"를 보내지 않는다. 카드를 눌러 펼칠 때만
    const seen = new SeenReports()
    expect(seen.opened(question('q1'))).toBe(true)
    expect(seen.opened(question('q1'))).toBe(false) // 두 번 펼쳐도 한 번
  })

  it('does not report a card the call is no longer waiting on or one already seen', () => {
    const seen = new SeenReports()
    expect(seen.opened(question('q1', 'timeout'))).toBe(false)
    expect(seen.opened({ ...question('q2'), seen_at: '2026-11-08T16:02:03+09:00' })).toBe(false)
  })
})
