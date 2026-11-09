// 사장님 화면 고정 문구 사전 (4단계 티켓 03). 런타임 번역을 쓰지 않고 사람이 확인한 문구를 둔다.
// `/owner?lang=ru`(기본 ru). ko는 개발·확인용 한국어 화면이다. 데모 언어는 ru 하나(spec "번역").
// 시각(`18:30`)·금액(`25,000₩`)은 서버가 고정 표기로 넣고 여기서는 그대로 보인다(Intl을 쓰지 않는다).
// 러시아어 문구는 러시아어 화자 확인 전 초안이다(티켓 11 사람 확인).
import type {
  CallEndReason,
  CaptionLine,
  KnowledgeCard,
  KnowledgeKind,
  OrderCardLine,
  OwnerAnswer,
  QuestionCard,
  RequestBadge,
  SentText,
} from './ownerState.ts'

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
  /** 통화 카드 아래 자막 목록 이름과 말한 쪽 표시 (티켓 10) */
  captions: string
  speaker: Record<CaptionLine['who'], string>
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
  /** 답 전에 통화가 끝난 질문 카드 (티켓 06) */
  callEnded: string
  /** 답을 못 받은 손님 질문이 있던 확정 주문 (티켓 06) */
  ownerCheck: string
  answer: Record<OwnerAnswer, string>
  /** 질문 카드 자유 입력 (티켓 07): 접힌 칸 제목, 안내, 보내기 버튼 */
  written: string
  writtenHint: string
  writtenSend: string
  /** 카드 안 "손님에게 보낸 문자" 한 줄 (티켓 07) */
  sms: string
  smsKind: Record<SentText['kind'], string>
  translateFailed: string
  /**
   * "손님이 궁금해한 것" (티켓 13): 접힌 목록 제목, 질문 종류, 횟수, 안내, 번역 보기·저장 버튼,
   * 미리보기 이름, 번역 실패, 채운 카드 표시
   */
  knowledge: string
  knowledgeKind: Record<KnowledgeKind, string>
  knowledgeCount: (count: number) => string
  knowledgeHint: string
  knowledgeTranslate: string
  knowledgePreview: string
  knowledgeNoTranslation: string
  knowledgeSave: string
  knowledgeSaved: string
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
    captions: 'Субтитры звонка',
    speaker: { customer: 'Клиент', bot: 'ИИ' },
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
    callEnded: 'Звонок завершён',
    ownerCheck: 'Нужно ответить на вопрос клиента',
    answer: { yes: 'Можно', no: 'Нельзя', unsure: 'Не знаю' },
    written: 'Ответить текстом',
    writtenHint: 'Клиенту уйдёт только SMS (на корейском, с переводом)',
    writtenSend: 'Отправить',
    sms: 'SMS клиенту',
    smsKind: {
      order: 'Подтверждение заказа',
      late_answer: 'Ваш ответ после звонка',
      free_answer: 'Ваш ответ текстом',
    },
    translateFailed: 'Перевод не удался: клиенту ушёл ваш текст без перевода',
    knowledge: 'Что спрашивали клиенты',
    knowledgeKind: {
      allergen: 'Аллерген',
      allergy: 'Аллергены',
      price: 'Цена',
      option: 'Опции',
      ingredient: 'Состав',
      taste: 'Вкус и описание',
      other: 'Другое',
    },
    knowledgeCount: (n) => `Спросили ${n} ${ruTimes(n)}`,
    knowledgeHint: 'Ответьте на своём языке. ИИ будет отвечать так следующим клиентам — после «Сохранить»',
    knowledgeTranslate: 'Показать перевод',
    knowledgePreview: 'ИИ скажет клиенту (по-корейски, можно исправить)',
    knowledgeNoTranslation: 'Перевод не удался. Напишите ответ по-корейски',
    knowledgeSave: 'Сохранить',
    knowledgeSaved: 'Сохранено',
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
    captions: '통화 자막',
    speaker: { customer: '손님', bot: 'AI' },
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
    callEnded: '통화 끝',
    ownerCheck: '사장님 확인 필요 · 손님 질문',
    answer: { yes: '돼요', no: '안 돼요', unsure: '모르겠어요' },
    written: '글로 답하기',
    writtenHint: '손님에게는 문자로만 가요(한국어로 번역)',
    writtenSend: '보내기',
    sms: '손님에게 보낸 문자',
    smsKind: {
      order: '주문 확정 안내',
      late_answer: '늦은 답(통화에서 못 전함)',
      free_answer: '글로 남긴 답',
    },
    translateFailed: '번역 실패: 사장님 글 원문이 갔어요',
    knowledge: '손님이 궁금해한 것',
    knowledgeKind: {
      allergen: '알레르기 성분',
      allergy: '알레르기',
      price: '가격',
      option: '옵션',
      ingredient: '재료',
      taste: '맛·설명',
      other: '그 밖',
    },
    knowledgeCount: (n) => `${n}번 물어봄`,
    knowledgeHint: '답을 적고 저장하면 다음 손님부터 AI가 이 답을 전해요',
    knowledgeTranslate: '번역 보기',
    knowledgePreview: 'AI가 손님에게 할 말(한국어, 고칠 수 있어요)',
    knowledgeNoTranslation: '번역이 안 됐어요. 한국어로 적어 주세요',
    knowledgeSave: '저장',
    knowledgeSaved: '채움',
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

export interface CaptionView {
  who: CaptionLine['who']
  /** 크게 보일 글: 손님 말 번역, 번역이 없으면(아직·실패·AI 말·ko 화면) 한국어 원문 */
  main: string
  /** `main`이 한국어 원문이다(`lang="ko"`, AI 말은 회색 한 줄) */
  mainKo: boolean
  /** 번역 아래 작게 붙일 한국어 원문. `main`이 원문이면 빈 글(두 번 쓰지 않는다) */
  original: string
}

/** 자막 한 줄을 화면 언어로 (spec "Papago 실측": 손님 말은 번역 + 원문, AI 말은 한국어 원문) */
export function captionView(line: CaptionLine, lang: OwnerLang): CaptionView {
  const translated = lang === 'ko' || line.who === 'bot' ? '' : line.text_owner
  return translated
    ? { who: line.who, main: translated, mainKo: false, original: line.text }
    : { who: line.who, main: line.text, mainKo: true, original: '' }
}

export interface SentTextView {
  /** 한 줄 머리: 문자 · 보낸 시각 · 종류 */
  label: string
  /** 손님이 받은 한국어 문장 그대로 */
  text: string
  /** 자유 입력 글이 번역 없이 갔으면 경고, 아니면 빈 글 */
  failed: string
}

/** 카드 안 "손님에게 보낸 문자" 한 줄 (티켓 07). 문자 내용은 손님이 받은 한국어 그대로 둔다 */
export function sentTextView(t: SentText, lang: OwnerLang): SentTextView {
  const text = OWNER_TEXT[lang]
  return {
    label: `${text.sms} · ${t.sent_label} · ${text.smsKind[t.kind]}`,
    text: t.text,
    failed: t.translate_failed ? text.translateFailed : '',
  }
}

/** 러시아어 "раз" 꼴: 1·21… раз, 2~4·22~24… раза, 나머지(11~14 포함) раз */
function ruTimes(n: number): string {
  const tens = n % 100
  const ones = n % 10
  return ones >= 2 && ones <= 4 && (tens < 12 || tens > 14) ? 'раза' : 'раз'
}

export interface KnowledgeView extends QuestionView {
  /** 질문 종류. 알레르기 성분 하나·이름 붙은 재료면 뒤에 그 낱말(한국어) */
  kind: string
  /** 같은 질문을 받은 횟수 */
  count: string
}

/** "손님이 궁금해한 것" 카드를 화면 언어로 (티켓 13). 질문은 질문 카드와 같이 번역 + 한국어 원문 */
export function knowledgeView(card: KnowledgeCard, lang: OwnerLang): KnowledgeView {
  const text = OWNER_TEXT[lang]
  const named = card.subject && (card.kind === 'allergen' || card.kind === 'ingredient')
  return {
    ...questionView(card, lang),
    kind: named ? `${text.knowledgeKind[card.kind]}: ${card.subject}` : text.knowledgeKind[card.kind],
    count: text.knowledgeCount(card.count),
  }
}
