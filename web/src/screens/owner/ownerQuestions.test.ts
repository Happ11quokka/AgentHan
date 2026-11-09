import { describe, expect, it } from 'vitest'
import {
  type OwnerAction,
  type OwnerState,
  type QuestionCard,
  canAnswer,
  initialOwnerState,
  openQuestions,
  ownerReducer,
  secondsLeft,
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
