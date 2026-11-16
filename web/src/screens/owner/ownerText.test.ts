import type { CustomerText } from '../customerText.ts'
import { describe, expect, it } from 'vitest'
import { type OrderCardLine, WRITTEN_ANSWER_MAX, writtenAnswer } from './ownerState.ts'
import { OWNER_TEXT, answerLabel, lineView, orderMeta, ownerLang, questionView, sentTextView } from './ownerText.ts'

function line(over: Partial<OrderCardLine> = {}): OrderCardLine {
  return {
    menu_id: 'm01',
    name_ko: '메도빅',
    name_owner: 'Медовик',
    quantity: 2,
    options: ['홀케이크 변경'],
    options_owner: ['Целый торт'],
    request: '고수는 빼 주세요',
    request_owner: 'кинза Уберите, пожалуйста.',
    badges: ['remove'],
    subtotal: 91000,
    ...over,
  }
}

describe('owner screen language (ticket 03)', () => {
  it('starts in Russian unless the address asks for Korean', () => {
    expect(ownerLang('')).toBe('ru')
    expect(ownerLang('?key=k')).toBe('ru')
    expect(ownerLang('?key=k&lang=ru')).toBe('ru')
    expect(ownerLang('?lang=ko&key=k')).toBe('ko')
    expect(ownerLang('?lang=mn')).toBe('ru') // 사전이 없는 언어는 기본(ru)
  })

  it('keeps every fixed label in both dictionaries', () => {
    expect(Object.keys(OWNER_TEXT.ru).sort()).toEqual(Object.keys(OWNER_TEXT.ko).sort())
    expect(OWNER_TEXT.ru.accept).toBe('Принять')
    expect(OWNER_TEXT.ru.badge.remove).toBe('УБРАТЬ')
    expect(OWNER_TEXT.ru.badge.allergy).toBe('АЛЛЕРГИЯ')
  })

  it('shows the owner menu name big and the Korean one small', () => {
    expect(lineView(line(), 'ru')).toEqual({
      name: 'Медовик',
      nameKo: '메도빅',
      options: ['Целый торт'],
      request: 'кинза Уберите, пожалуйста.',
      requestKo: '고수는 빼 주세요',
    })
  })

  it('shows the Korean name once when the menu has no owner name', () => {
    const v = lineView(line({ name_owner: '아메리카노', name_ko: '아메리카노' }), 'ru')
    expect([v.name, v.nameKo]).toEqual(['아메리카노', ''])
  })

  it('shows only the Korean request until a translation arrives', () => {
    const v = lineView(line({ request_owner: '' }), 'ru')
    expect([v.request, v.requestKo]).toEqual(['', '고수는 빼 주세요'])
  })

  it('shows a question card with the owner menu name and the translation over the Korean', () => {
    const q = {
      menu_name: '메도빅',
      menu_name_owner: 'Медовик',
      text: '메도빅에 견과류 들어가요?',
      text_owner: 'В Медовик идут орехи?',
    }
    expect(questionView(q, 'ru')).toEqual({
      menu: 'Медовик',
      question: 'В Медовик идут орехи?',
      questionKo: '메도빅에 견과류 들어가요?',
    })
    expect(questionView({ ...q, text_owner: '' }, 'ru').question).toBe('')
    expect(questionView(q, 'ko')).toEqual({ menu: '메도빅', question: '', questionKo: '메도빅에 견과류 들어가요?' })
  })

  it('shows Korean names, options and the request alone on the Korean screen', () => {
    expect(lineView(line(), 'ko')).toEqual({
      name: '메도빅',
      nameKo: '',
      options: ['홀케이크 변경'],
      request: '',
      requestKo: '고수는 빼 주세요',
    })
  })
})

describe('texts sent to the customer on owner cards (ticket 07)', () => {
  function sent(over: Partial<CustomerText> = {}): CustomerText {
    return {
      id: 'call-1-s2',
      call_id: 'call-1',
      kind: 'free_answer',
      text: '메도빅 문의 답변이에요. 사장님 답변(번역): «네, 호두가 들어 있어요.»',
      sent_at: '2026-11-08T16:07:00+09:00',
      sent_label: '16:07',
      question_id: 'call-1-q1',
      translate_failed: false,
      ...over,
    }
  }

  it('says in one line what kind of text went out and when, with the Korean text the customer got', () => {
    expect(sentTextView(sent(), 'ru')).toEqual({
      label: 'SMS клиенту · 16:07 · Ваш ответ текстом',
      text: '메도빅 문의 답변이에요. 사장님 답변(번역): «네, 호두가 들어 있어요.»',
      failed: '',
    })
    expect(sentTextView(sent({ kind: 'order' }), 'ko').label).toBe('손님에게 보낸 문자 · 16:07 · 주문 확정 안내')
    expect(sentTextView(sent({ kind: 'late_answer' }), 'ko').label).toBe(
      '손님에게 보낸 문자 · 16:07 · 늦은 답(통화에서 못 전함)',
    )
  })

  it('warns when the written answer went out untranslated', () => {
    expect(sentTextView(sent({ translate_failed: true }), 'ru').failed).toBe(
      'Перевод не удался: клиенту ушёл ваш текст без перевода',
    )
  })
})

describe('written answer on a question card (ticket 07)', () => {
  it('sends only some text, not too long', () => {
    expect(writtenAnswer('  Да, есть орехи ')).toBe('Да, есть орехи')
    expect(writtenAnswer('   ')).toBeNull()
    expect(writtenAnswer('я'.repeat(WRITTEN_ANSWER_MAX))).toHaveLength(WRITTEN_ANSWER_MAX)
    expect(writtenAnswer('я'.repeat(WRITTEN_ANSWER_MAX + 1))).toBeNull()
  })

  it('tells the owner the written answer goes to the customer only by text', () => {
    expect(OWNER_TEXT.ru.writtenHint).toBe('Клиенту уйдёт только SMS (на корейском, с переводом)')
    expect(OWNER_TEXT.ko.writtenHint).toBe('손님에게는 문자로만 가요(한국어로 번역)')
  })
})

describe('answer buttons on a contains question (review P2)', () => {
  it('labels the buttons "contains / does not contain" when the customer asked whether it goes in', () => {
    // "견과류 들어가요?"에 "Нельзя(안 돼요)"를 누르면 안 들어간다는 뜻으로 저장된다. 버튼 뜻을 맞춘다
    expect([answerLabel(true, 'yes', 'ru'), answerLabel(true, 'no', 'ru')]).toEqual(['Есть', 'Нет'])
    expect([answerLabel(true, 'yes', 'ko'), answerLabel(true, 'no', 'ko')]).toEqual(['들어가요', '안 들어가요'])
    expect(answerLabel(true, 'unsure', 'ru')).toBe(OWNER_TEXT.ru.answer.unsure)
  })

  it('keeps can / cannot for other questions', () => {
    expect([answerLabel(false, 'yes', 'ru'), answerLabel(false, 'no', 'ru')]).toEqual(['Можно', 'Нельзя'])
    expect(answerLabel(undefined, 'yes', 'ko')).toBe('돼요')
  })
})

describe('order time and customer name on the order card (review P1, story 25)', () => {
  it('shows when the order came in, and the customer name when there is one', () => {
    expect(orderMeta({ received_label: '16:05', customer_name: '' }, 'ru')).toBe('Заказ в 16:05')
    expect(orderMeta({ received_label: '16:05', customer_name: '김' }, 'ru')).toBe('Заказ в 16:05 · 김')
    expect(orderMeta({ received_label: '16:05', customer_name: '김' }, 'ko')).toBe('주문 16:05 · 김')
  })

  it('shows nothing for an older server that sends neither', () => {
    expect(orderMeta({}, 'ru')).toBe('')
  })
})
