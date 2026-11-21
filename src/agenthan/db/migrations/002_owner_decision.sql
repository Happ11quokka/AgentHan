-- 주문 승인(수락·거절): 사장님 결정을 phone_orders에 남긴다. 서버를 다시 켜도 결정이 남는다.
-- 상태·사유는 일반 글이다(enum·CHECK 없음). 새 결정 종류가 생겨도 스키마를 바꾸지 않는다.
--   owner_status      accepted · auto_accepted(사장님 화면 없이 확정) · rejected · expired … ('' = 아직)
--   reject_reason     out_of_stock · busy · closed · free … ('' = 거절 아님)
--   decided_at        사장님이 결정한(자동 확정·자동 취소는 그렇게 된) 시각
--   owner_pickup_time 늦춘 수락의 바뀐 픽업 시각 (ISO 8601, 없으면 '')
-- 기존 행은 빈 값이다.

ALTER TABLE phone_orders
    ADD COLUMN owner_status      text NOT NULL DEFAULT '',
    ADD COLUMN reject_reason     text NOT NULL DEFAULT '',
    ADD COLUMN decided_at        timestamptz,
    ADD COLUMN owner_pickup_time text NOT NULL DEFAULT '';
