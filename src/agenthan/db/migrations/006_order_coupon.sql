-- 전화 주문에 쓴 쿠폰 (전화 메뉴·주문 현황·쿠폰 티켓 09). 주문서(`Order`)의 쿠폰 칸과 같다.
-- total은 낼 금액(할인 뒤)이다. 할인 전 금액 = total + discount. 쿠폰 전 주문은 ''·''·0으로 읽힌다.
ALTER TABLE phone_orders
    ADD COLUMN coupon_id   text    NOT NULL DEFAULT '',
    ADD COLUMN coupon_name text    NOT NULL DEFAULT '',
    ADD COLUMN discount    integer NOT NULL DEFAULT 0;
