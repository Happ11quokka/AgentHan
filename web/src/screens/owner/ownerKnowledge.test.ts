import { describe, expect, it } from 'vitest'
import {
  type KnowledgeCard,
  type OwnerAction,
  type OwnerState,
  initialOwnerState,
  knowledgeCards,
  ownerReducer,
  unansweredKnowledge,
} from './ownerState.ts'
import { OWNER_TEXT, knowledgeView } from './ownerText.ts'

function run(...actions: OwnerAction[]): OwnerState {
  return actions.reduce(ownerReducer, initialOwnerState)
}

function card(id: string, over: Partial<KnowledgeCard> = {}): KnowledgeCard {
  return {
    id,
    menu_id: 'm03',
    menu_name: '아메리카노',
    menu_name_owner: '아메리카노',
    kind: 'taste',
    subject: '',
    call_id: 'call-1',
    text: '아메리카노 어떤 맛이에요?',
    text_owner: 'Какой вкус у американо?',
    count: 1,
    asked_at: '2026-11-10T16:02:00+09:00',
    last_asked_at: '2026-11-10T16:02:00+09:00',
    status: 'open',
    answer_text: '',
    answer_ko: '',
    saved_at: '',
    ...over,
  }
}

const changed = (c: KnowledgeCard): OwnerAction => ({ type: 'knowledge.update', data: c })
const ids = (cards: KnowledgeCard[]) => cards.map((c) => c.id)

describe('owner screen knowledge cards (ticket 13)', () => {
  it('adds new cards at the end and replaces a card with the same id', () => {
    const s = run(changed(card('k1')), changed(card('k2')), changed(card('k1', { count: 3 })))
    expect(ids(knowledgeCards(s))).toEqual(['k1', 'k2'])
    expect(knowledgeCards(s)[0].count).toBe(3)
  })

  it('takes the cards from the snapshot and reads an older snapshot as none', () => {
    const s = run(changed(card('k9')), {
      type: 'snapshot',
      data: { orders: [], calls: [], knowledge: [card('k1', { status: 'filled' }), card('k2')] },
    })
    expect(ids(knowledgeCards(s))).toEqual(['k1', 'k2'])
    expect(knowledgeCards(run({ type: 'snapshot', data: { orders: [], calls: [] } }))).toEqual([])
  })

  it('counts only the cards still waiting for the owner', () => {
    const s = run(changed(card('k1')), changed(card('k2')), changed(card('k1', { status: 'filled' })))
    expect(ids(unansweredKnowledge(s))).toEqual(['k2'])
  })

  it('shows the question in the owner language with the Korean original and how often it came', () => {
    const view = knowledgeView(card('k1', { count: 3 }), 'ru')
    expect(view).toEqual({
      menu: '아메리카노',
      question: 'Какой вкус у американо?',
      questionKo: '아메리카노 어떤 맛이에요?',
      kind: OWNER_TEXT.ru.knowledgeKind.taste,
      count: 'Спросили 3 раза',
    })
    expect(knowledgeView(card('k1'), 'ru').count).toBe('Спросили 1 раз')
    expect(knowledgeView(card('k1', { count: 5 }), 'ru').count).toBe('Спросили 5 раз')
    expect(knowledgeView(card('k1', { count: 2 }), 'ko').count).toBe('2번 물어봄')
  })

  it('names the subject next to the kind for one allergen or ingredient', () => {
    const nuts = card('k1', { kind: 'allergen', subject: '견과', menu_name_owner: 'Медовик' })
    expect(knowledgeView(nuts, 'ru').kind).toBe(`${OWNER_TEXT.ru.knowledgeKind.allergen}: 견과`)
    expect(knowledgeView(nuts, 'ru').menu).toBe('Медовик')
  })
})
