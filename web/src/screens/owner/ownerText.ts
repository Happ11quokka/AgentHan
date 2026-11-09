// 사장님 화면 고정 문구 사전 (4단계 티켓 03). 런타임 번역을 쓰지 않고 사람이 확인한 문구를 둔다.
// `/owner?lang=ru`(기본 ru). ko는 개발·확인용 한국어 화면이다. 데모 언어는 ru 하나(spec "번역").
// 시각(`18:30`)·금액(`25,000₩`)은 서버가 고정 표기로 넣고 여기서는 그대로 보인다(Intl을 쓰지 않는다).
// 러시아어 문구는 러시아어 화자 확인 전 초안이다(티켓 11 사람 확인).
import type { CallEndReason, OrderCardLine, OwnerAnswer, QuestionCard, RequestBadge } from './ownerState.ts'

export type OwnerLang = 'ru' | 'ko'

type Connection = 'closed' | 'connecting' | 'live' | 'failed'
type Sound = 'locked' | 'on' | 'unavailable'

export interface OwnerText {
  title: string
  connection: Record<Connection, string>
  sound: Record<Sound, string>
  openStore: string
  liveCall: string
  liveEmpty: string
  handoff: string
  missed: string
  endedEmpty: string
  heard: string
  korean: string
  dismiss: string
  noOrders: string
  pickup: string
  accept: string
  accepted: string
  sectionLive: string
  sectionFollowUp: string
  sectionOrders: string
  sectionQuestions: string
  /** 질문 카드 제목 (티켓 05) */
  question: string
  secondsLeft: (seconds: number) => string
  late: string
  answer: Record<OwnerAnswer, string>
  endReason: Record<CallEndReason, string>
  badge: Record<RequestBadge, string>
  papago: string
}

export const OWNER_TEXT: Record<OwnerLang, OwnerText> = {
  ru: {
    title: 'Заказы по телефону',
    connection: {
      closed: 'Откройте магазин, чтобы принимать заказы',
      connecting: 'Подключение…',
      live: 'Принимаем заказы',
      failed: 'Нет подключения. Проверьте ключ в адресе',
    },
    sound: {
      locked: 'Звук выключен · включится после «Открыть магазин»',
      on: 'Звук уведомлений включён',
      unavailable: 'Не удалось включить звук',
    },
    openStore: 'Открыть магазин',
    liveCall: 'Идёт звонок',
    liveEmpty: 'Пока ничего не выбрано',
    handoff: 'Перезвоните клиенту',
    missed: 'Незавершённый звонок',
    endedEmpty: 'Ничего не выбрано',
    heard: 'Последние слова клиента',
    korean: 'Оригинал (кор.)',
    dismiss: 'Закрыть',
    noOrders: 'Заказов пока нет.',
    pickup: 'Забрать в',
    accept: 'Принять',
    accepted: 'Принят',
    sectionLive: 'Идут звонки',
    sectionFollowUp: 'Звонки для проверки',
    sectionOrders: 'Заказы',
    sectionQuestions: 'Вопросы клиентов',
    question: 'Вопрос клиента',
    secondsLeft: (n) => `${n} сек`,
    late: 'Время вышло',
    answer: { yes: 'Можно', no: 'Нельзя', unsure: 'Не знаю' },
    endReason: {
      confirmed: 'Заказ подтверждён',
      handoff: 'Клиент попросил хозяина',
      cancelled: 'Клиент отменил заказ',
      no_input: 'Клиент не отвечал',
      hangup: 'Клиент положил трубку',
      error: 'Ошибка связи',
    },
    badge: { remove: 'УБРАТЬ', allergy: 'АЛЛЕРГИЯ' },
    papago: 'Перевод: Papago',
  },
  ko: {
    title: '사장님 화면',
    connection: {
      closed: '가게 문을 열면 주문을 받아요',
      connecting: '연결 중…',
      live: '주문 받는 중',
      failed: '연결할 수 없어요. 주소의 토큰을 확인해 주세요',
    },
    sound: {
      locked: '소리 꺼짐 · 가게 열기를 누르면 켜져요',
      on: '알림 소리 켜짐',
      unavailable: '알림 소리를 켤 수 없어요',
    },
    openStore: '가게 열기',
    liveCall: '통화 중',
    liveEmpty: '아직 담은 메뉴가 없어요',
    handoff: '손님 연락 필요',
    missed: '미완료 통화',
    endedEmpty: '담은 메뉴 없음',
    heard: '마지막 손님 말',
    korean: '한국어 원문',
    dismiss: '닫기',
    noOrders: '아직 들어온 주문이 없어요.',
    pickup: '픽업',
    accept: '접수',
    accepted: '접수함',
    sectionLive: '통화 중',
    sectionFollowUp: '확인할 통화',
    sectionOrders: '주문',
    sectionQuestions: '손님 질문',
    question: '손님 질문',
    secondsLeft: (n) => `${n}초`,
    late: '시간 지남',
    answer: { yes: '돼요', no: '안 돼요', unsure: '모르겠어요' },
    endReason: {
      confirmed: '주문 확정',
      handoff: '사장님 연결 요청',
      cancelled: '손님이 주문 취소',
      no_input: '손님 응답 없음',
      hangup: '손님이 끊음',
      error: '연결 오류',
    },
    badge: { remove: '빼기', allergy: '알레르기' },
    papago: '번역: Papago',
  },
}

/** Papago 이용 조건: 번역을 쓰는 화면에 브랜드 표기와 링크 */
export const PAPAGO_URL = 'https://papago.naver.com/'

/** `/owner?lang=…`에서 화면 언어를 읽는다. 없거나 사전이 없는 언어면 ru */
export function ownerLang(search: string): OwnerLang {
  return new URLSearchParams(search).get('lang') === 'ko' ? 'ko' : 'ru'
}

export interface LineView {
  /** 크게: 사장님 원어 이름(비면 한국어) */
  name: string
  /** 작게: 한국어 이름. 큰 이름과 같으면 빈 글(두 번 쓰지 않는다) */
  nameKo: string
  options: string[]
  /** 요청 번역. 아직·실패면 빈 글(한국어 원문만 보인다) */
  request: string
  requestKo: string
}

/** 카드 줄을 화면 언어로. ko 화면은 한국어 이름·옵션·요청 원문만 */
export function lineView(line: OrderCardLine, lang: OwnerLang): LineView {
  if (lang === 'ko') {
    return { name: line.name_ko, nameKo: '', options: line.options, request: '', requestKo: line.request }
  }
  const name = line.name_owner || line.name_ko
  return {
    name,
    nameKo: name === line.name_ko ? '' : line.name_ko,
    options: line.options_owner,
    request: line.request_owner,
    requestKo: line.request,
  }
}

export interface QuestionView {
  /** 질문한 메뉴: 사장님 원어 이름(비면 한국어) */
  menu: string
  /** 손님 질문 번역. 아직·실패면 빈 글(한국어 원문만 보인다) */
  question: string
  questionKo: string
}

/** 질문 카드를 화면 언어로. ko 화면은 한국어만 */
export function questionView(
  q: Pick<QuestionCard, 'menu_name' | 'menu_name_owner' | 'text' | 'text_owner'>,
  lang: OwnerLang,
): QuestionView {
  if (lang === 'ko') return { menu: q.menu_name, question: '', questionKo: q.text }
  return { menu: q.menu_name_owner || q.menu_name, question: q.text_owner, questionKo: q.text }
}
