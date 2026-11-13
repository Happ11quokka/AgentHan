# 도구 스펙 (2026-11-12 확정)

기능 하나에 도구 하나다. 도구 이름은 그대로 두고, 한 도구 안의 여러 작업은 `action` 같은 선택 인자로 나눈다.
정본은 `src/agenthan/tools/feature_tools.py`의 `_TOOL_LIST`이고, 이 문서는 그 요약이다.
쓰기 도구는 미리보기 → 사장님 "네" 뒤에만 저장된다. 모든 값은 사장님이 이 대화에서 말한 값만 쓴다
(`agent/value_source.py`가 확인).

## 결정

| | 결정 |
|---|---|
| A | 기능당 도구 하나 유지. 쓰기 작업 종류는 선택 인자(`action` 등)로 나눈다 |
| B | 기간은 한 가지 표현: `today, yesterday, this_week, last_week, this_month, last_month` |
| C | 사진은 도구 인자로 받지 않는다(메뉴 사진은 `/photo` 흐름). 예약 받을 시간·인원 설정은 범위 밖 |
| D | 대표키워드는 `add`/`remove`로 고친다. 전부 바꾸라고 할 때만 `keywords`(전체 목록) |

## 쓰기 도구

| 기능 | 도구 | 인자 (필수 **굵게**) | 할 수 있는 일 |
|---|---|---|---|
| 메뉴 | `upsert_menu` | **name**, action(save·delete·sold_out·on_sale), price, new_name, description | 등록(새 메뉴는 price 필수), 가격·이름·설명 수정, 삭제, 품절·품절 해제. 별칭으로도 찾는다 |
| 업체 정보 | `update_business_info` | name, address, phone, introduction (하나 이상) | 상호·주소·전화번호·소개글을 한 번에 여러 개 |
| 영업시간 | `update_business_hours` | **entries**: 항목마다 **days**(요일·날짜·weekdays·weekend·everyday), closed, open_time, close_time, break_start, break_end | 같은 시간을 받는 요일은 항목 하나, 요일마다 시간이 다르면 항목을 나눠 한 호출에(#53). 임시 휴무(날짜 나열), 브레이크 타임. 시각을 빼면 지금 시각을 유지. 매일·평일 묶음은 쉬는 날을 그대로 둔다 |
| 톡톡 | `save_talktalk_reply` | **inquiry**, **reply_text** | 문의 하나에 답장 저장(발송 안 함) |
| 주문 | `update_order_status` | **order_id**, **status**, reason | 접수·거절·준비 완료·전달 완료, 거절 사유 |
| 예약 | `update_reservation_status` | **reservation_id**, **status**, reason | 확정·취소·노쇼, 취소 사유 |
| 대표키워드 | `set_representative_keywords` | add, remove, keywords (하나 이상) | 더하기·빼기·전체 교체. 최대 5개, 넘치면 `too_many_keywords`, 없는 단어를 빼면 `keyword_not_found` |
| 쿠폰 | `create_coupon` | **title**, **discount_type**, **discount_value**, **start_date**, **end_date** | 쿠폰 만들기(게시 안 함) |
| 새 오픈 | `register_new_opening` | **opening_date** | 오픈 날짜 등록 |
| 매장결제 | `set_onsite_payment` | **enabled** | 켜기·끄기 |

주문·예약·톡톡은 코드가 사장님 말로 대상 후보를 좁혀 id 인자를 그 후보 enum으로 연다. 목록을 묻는
질문은 도구 없이 후보로 답한다.

## 조회 도구

| 기능 | 도구 | 인자 | 비고 |
|---|---|---|---|
| 전화 | `list_call_history` | missed_only | |
| 네이버페이 | `get_naverpay_signup_guide` | **business_type** | 더미 안내 |
| 정산 | `get_settlement_summary` | **period**(공통 기간) | 더미 자료는 this_week·this_month·last_month만 있음 |
| POS | `check_pos_compatibility` | **pos_name** | |
| 도움말 | `search_help_articles` | **query** | 맞는 글이 없으면 `found: false`와 고객센터 안내 |
| 검색 노출 | `diagnose_search_visibility` | 없음 | 진단 문장은 코드가 만든다 |
| 통계 | `get_statistics` | **period**(7d·30d) | 실제 흐름은 `report_explainer`(일간·주간)라 이 도구는 거의 안 쓰임 |

## 아직 안 맞춘 것

- 통계 `get_statistics`의 기간(7d·30d)은 공통 기간으로 바꾸지 않았다. 통계 질문은 `report_explainer`가 처리한다.
- 리뷰 서브에이전트 도구(`analyze_reviews` 등)의 기간(1d·7d·30d·90d·all)은 그대로다. 근형님과 맞출 때 같이 바꾼다.
- 쿠폰 최소 주문 금액·대상 메뉴는 넣었다가 뺐다. 선택 입력으로 열어 두면 HCX-005가 프롬프트·재요청에도 매번 그 값을 되물어 쿠폰 만들기가 끝나지 않았다(S1 `missing-coupon`).
- 전화 기록 기간 필터, 쿠폰 목록·중지, 새 오픈 신청 조건 검사는 없다.
