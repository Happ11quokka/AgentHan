// 손님에게 보낸 문자 미리보기 (4단계 티켓 07, 서버 `voice/owner/hub.py`·`sms.py`). 실제 문자는 보내지
// 않는다. 사장님 화면(카드 안 "손님에게 보낸 문자")과 손님 통화 화면("받은 문자")이 같은 모양을 쓴다
// (리뷰 C10: 두 화면에 같은 타입이 따로 있던 것을 합쳤다).

/**
 * 문자 하나. order: 주문 확정 안내. late_answer: 통화에서 못 전한 사장님 버튼 답(시간 초과·통화 끝
 * 뒤, 또는 손님이 말하는 중이라 말하지 못한 제시간 답). free_answer: 사장님이 글로 남긴 답(번역)
 */
export interface CustomerText {
  id: string
  call_id: string
  kind: 'order' | 'late_answer' | 'free_answer'
  /** 손님이 받는 한국어 문장 */
  text: string
  sent_at: string
  /** 서버 고정 표기 `16:05` */
  sent_label: string
  /** 사장님 답 문자면 그 질문 id, 아니면 빈 글 */
  question_id: string
  /** 사장님 글을 번역하지 못해 원문을 넣었다 */
  translate_failed: boolean
}
