import { describe, expect, it } from 'vitest'
import type { OrderCardLine } from './ownerState.ts'
import { OWNER_TEXT, lineView, ownerLang, questionView } from './ownerText.ts'

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
