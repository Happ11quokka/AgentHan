import { describe, expect, it } from 'vitest'
import {
  type CallCard,
  type CaptionLine,
  type OwnerAction,
  type OwnerState,
  captionLines,
  initialOwnerState,
  ownerReducer,
} from './ownerState.ts'
import { captionView } from './ownerText.ts'

function run(...actions: OwnerAction[]): OwnerState {
  return actions.reduce(ownerReducer, initialOwnerState)
}

function call(id: string, status: CallCard['status'] = 'live'): CallCard {
  return {
    id,
    status,
    started_at: '2026-11-08T16:02:00+09:00',
    started_label: '16:02',
    reason: '',
    lines: [],
    total: 0,
    total_label: '0₩',
    heard: [],
    heard_owner: [],
  }
}

const customer = (n: number, text: string, text_owner = ''): CaptionLine => ({
  n,
  who: 'customer',
  text,
  text_owner,
})
const bot = (n: number, text: string): CaptionLine => ({ n, who: 'bot', text, text_owner: '' })
const caption = (call_id: string, ...lines: CaptionLine[]): OwnerAction => ({
  type: 'call.caption',
  data: { call_id, lines },
})

describe('owner screen captions (ticket 10)', () => {
  it('shows the latest captions of each call, overwritten whole by every event', () => {
    const s = run(
      { type: 'snapshot', data: { orders: [], calls: [call('a'), call('b')] } },
      caption('a', customer(1, '메도빅 하나요')),
      caption('b', bot(1, '안녕하세요')),
      caption('a', customer(1, '메도빅 하나요', 'Медовик одну'), bot(2, '담았어요')),
    )
    expect(captionLines(s, 'a')).toEqual([
      customer(1, '메도빅 하나요', 'Медовик одну'),
      bot(2, '담았어요'),
    ])
    expect(captionLines(s, 'b')).toEqual([bot(1, '안녕하세요')])
    expect(captionLines(s, 'nope')).toEqual([])
  })

  it('keeps only the last four lines', () => {
    const lines = [1, 2, 3, 4, 5].map((n) => bot(n, `대사 ${n}`))
    const s = run(caption('a', ...lines))
    expect(captionLines(s, 'a').map((l) => l.n)).toEqual([2, 3, 4, 5])
  })

  it('takes the captions of calls on the line from a snapshot', () => {
    const s = run(caption('old', bot(1, '지난 연결')), {
      type: 'snapshot',
      data: {
        orders: [],
        calls: [call('a')],
        captions: [{ call_id: 'a', lines: [customer(3, '여보세요')] }],
      },
    })
    expect(captionLines(s, 'a')).toEqual([customer(3, '여보세요')])
    expect(captionLines(s, 'old')).toEqual([])
  })

  it.each(['ordered', 'handoff', 'missed'] as const)(
    'drops the captions once the call is %s',
    (status) => {
      const s = run(caption('a', bot(1, '안녕하세요')), { type: 'call.update', data: call('a', status) })
      expect(captionLines(s, 'a')).toEqual([])
    },
  )
})

describe('caption lines in the owner language (ticket 10)', () => {
  it('shows a customer line translated with the Korean original beside', () => {
    expect(captionView(customer(1, '고수 빼 주세요', 'кинза Уберите'), 'ru')).toEqual({
      who: 'customer',
      main: 'кинза Уберите',
      mainKo: false,
      original: '고수 빼 주세요',
    })
  })

  it('shows a customer line in Korean until its translation arrives or when it fails', () => {
    expect(captionView(customer(1, '고수 빼 주세요'), 'ru')).toEqual({
      who: 'customer',
      main: '고수 빼 주세요',
      mainKo: true,
      original: '',
    })
  })

  it('shows a bot line as one Korean line', () => {
    expect(captionView(bot(2, '메도빅 하나 담았어요.'), 'ru')).toEqual({
      who: 'bot',
      main: '메도빅 하나 담았어요.',
      mainKo: true,
      original: '',
    })
  })

  it('shows only Korean on the Korean screen', () => {
    expect(captionView(customer(1, '고수 빼 주세요', 'кинза Уберите'), 'ko')).toEqual({
      who: 'customer',
      main: '고수 빼 주세요',
      mainKo: true,
      original: '',
    })
  })
})
