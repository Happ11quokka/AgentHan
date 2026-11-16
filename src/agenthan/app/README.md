# app · 통합 진입점

FastAPI 앱 하나에 파트별 라우터를 붙인다: `/assistant`(Part A), `/phone`(Part C, WebSocket, 예정). 여기서 진짜 구현과 가짜 구현을 골라 끼운다(의존성 주입). 합칠 때 이 폴더만 고치면 되게 한다.

## `/assistant` (사장님 화면 백엔드, ADR 0002)

| 요청 | 하는 일 |
|---|---|
| `POST /assistant/sessions` | 대화 세션 시작(세션마다 파일럿 매장 복사본에서 시작한 저장소) |
| `POST /assistant/sessions/{id}/messages` | 사장님 말 한 턴 → `reply`·`place`·`pending` |
| `POST /assistant/sessions/{id}/actions` | 버튼의 `action_id`·`args`·`label`을 실행하고 같은 응답에 `next_actions` 추가 |
| `POST /assistant/sessions/{id}/photos` | 메뉴판 사진·음식 사진(multipart `image` + 선택 `text`) → 같은 모양 |
| `GET /assistant/sessions/{id}/photos/{name}` | `draft-<n>`: 기다리는 초안 사진, 메뉴 id: 저장된 메뉴 사진. 다른 세션·없는 사진은 404 |
| `GET /assistant/sessions/{id}/place` | 지금 `place`·`pending` |
| `GET /assistant/sessions/{id}/reviews` | 리뷰 탭: 요약(평균 별점·건수·답글 안 단 수)과 리뷰(최신순, 거친 표현을 거른 `text`, 걸렀을 때만 `original`) |
| `GET /assistant/sessions/{id}/report?period=week\|day` | 손님 통계 탭: 요약 카드 3개 증감, 유입 채널, 시간대·요일 조회, 키워드(새로 뜬 것 표시), 해 볼 일 한 줄(`tip`). LLM 없음 |

- 파일럿 매장(`pilot.py`, `pilot_store.json`): 서버의 새 대화는 이 가게로 시작하고 비서의 '오늘'은 2026-11-09로 고정된다(일반 기능·리뷰 둘 다). 메뉴 13·리뷰 20(미답글 8)·톡톡·주문·예약·전화·쿠폰·정산이 손님 통계와 같은 이야기다. 평가 시드(`tools/dummy_store_seed.json`)는 그대로.
- `place`: 메뉴(`photo_url` 포함)·영업시간·쿠폰 스냅샷.
- `pending`: 승인 대기 변경. 사진 작업은 `kind: "photo"`와 초안 줄(`change`: `new`·`price_change`·`unchanged`·`photo_attach`, 가격을 모르면 null. `menu_name`: 맞는 저장된 메뉴 이름)을 더한다. 실제 저장은 승인 뒤에만.
- 버튼 쓰기는 미리보기만 만들며 `task.approve`에 `task_id`와 현재 `version`을 보내야 저장한다. 리뷰 답글은 `kind: "review"`의 리뷰 ID·초안으로 표시한다. `task.cancel`은 작업 ID로 취소한다. 버튼 목록과 입력은 `agent/actions.py`가 정본이다.
