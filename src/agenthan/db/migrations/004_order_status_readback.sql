-- 주문 현황 다시 열기(전화 메뉴·주문 현황·쿠폰 티켓 06): 서버가 다시 떠 사장님 허브(메모리)에 주문
-- 카드가 없을 때 손님 주문 현황을 phone_orders 한 줄에서 다시 만든다. 결정 열(002)만으로 모자라는
-- 거절 내용을 더 남긴다. 사장님 결정 기록과 같이 쓴다(같은 주문이 다시 오면 덮어쓴다).
--   reject_menu_ids  재료 소진 거절의 떨어진 메뉴 id들 (jsonb 글 배열, 없으면 [])
--   reject_text      직접 쓰기 거절의 사장님 글 원문 ('' = 없음)
--   reject_text_ko   그 글의 한국어 번역. 번역이 늦게 오면 결정을 한 번 더 쓴다 ('' = 아직·실패)
-- 확인 요청을 연 시각은 따로 두지 않는다: 통화 뒤 확인 요청은 손님이 복창에 "네"라고 한 턴에
-- 주문서와 같이 열리므로 `created_at`(주문서를 만든 가게 시각)이 그 시각이다. 결정 전 행
-- (owner_status '')은 이 시각 + 자동 취소 마감이 지나면 자동 취소로 읽는다.
-- 기존 행은 빈 값이다.

ALTER TABLE phone_orders
    ADD COLUMN reject_menu_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN reject_text     text  NOT NULL DEFAULT '',
    ADD COLUMN reject_text_ko  text  NOT NULL DEFAULT '';
