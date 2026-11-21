# app · 통합 진입점

FastAPI 앱 하나에 파트별 라우터를 붙인다: `/assistant`(Part A), `/phone`(Part C, WebSocket, 예정). 여기서 진짜 구현과 가짜 구현을 골라 끼운다(의존성 주입). 합칠 때 이 폴더만 고치면 되게 한다.

## `/assistant` (사장님 화면 백엔드, ADR 0002)

| 요청 | 하는 일 |
|---|---|
| `POST /assistant/sessions` | 대화 세션 시작(세션마다 파일럿 매장 복사본에서 시작한 저장소) |
| `POST /assistant/sessions/{id}/messages` | 사장님 말 한 턴 → `reply`·`place`·`pending`, planner 모드는 `next_actions` 포함 |
| `POST /assistant/sessions/{id}/actions` | 버튼의 `action_id`·`args`·`label`을 실행하고 같은 응답에 `next_actions` 추가 |
| `POST /assistant/sessions/{id}/photos` | 메뉴판 사진·음식 사진(multipart `image` + 선택 `text`) → 같은 모양 |
| `GET /assistant/sessions/{id}/photos/{name}` | `draft-<n>`: 기다리는 초안 사진, 메뉴 id: 저장된 메뉴 사진. 다른 세션·없는 사진은 404 |
| `GET /assistant/sessions/{id}/place` | 지금 `place`·`pending`·`shop`(가게 이름·업종. 둘 중 하나가 비면 화면이 온보딩을 띄움) |
| `POST /assistant/sessions/{id}/onboarding` | 처음 쓰는 사장님(#94): `name`·`category`(빈칸 422)와 대표 메뉴 `menus` 0~3개(`name`·`price` 양의 정수)를 넣은 값 그대로 `shop`·`menus`에 저장 → 같은 모양. 대화 턴·승인 카드 아님, 실제 발송 없음 |
| `GET /assistant/sessions/{id}/reviews` | 리뷰 탭: 요약(평균 별점·건수·답글 안 단 수)과 리뷰(최신순, 거친 표현을 거른 `text`, 걸렀을 때만 `original`) |
| `POST /assistant/sessions/{id}/reviews/report` | 리뷰 탭 [지금 받아 보기](시연, #98): 보고 설정과 상관없이 정기 보고 한 회분을 `messages`로 → `place`와 같은 모양. 보고 설정·다음 보고 시각은 그대로 |
| `POST /assistant/sessions/{id}/reviews/{review_id}/reply` | 리뷰 카드 버튼(대화 턴 아님: 대화 기록·작업 원장·승인 카드에 남기지 않음). `op`: `draft`(초안 받기, 쓸 만한 초안이 있으면 그대로) · `post`(선택 `text`: 고친 문장) · `skip` · `redraft`(`hint`: `shorter`·`more_apologetic`·`warmer`). 초안 쓰기(HCX)는 세션 잠금 밖에서 → `review`(+`draft`)·`summary`, 실패면 `error`(`reason`·`message`·`issues`) |
| `GET /assistant/sessions/{id}/report?period=week\|day` | 손님 통계 탭: 요약 카드 3개 증감, 유입 채널, 시간대·요일 조회, 키워드(새로 뜬 것 표시), 해 볼 일 한 줄(`tip`). LLM 없음 |

- 파일럿 매장(`pilot.py`, `pilot_store.json`): 서버의 새 대화는 이 가게로 시작하고 비서의 '오늘'은 2026-11-09로 고정된다(일반 기능·리뷰 둘 다). 메뉴 13·리뷰 20(미답글 8)·톡톡·주문·예약·전화·쿠폰·정산이 손님 통계와 같은 이야기다. 평가 시드(`tools/dummy_store_seed.json`)는 그대로.
- `place`: 메뉴(`photo_url` 포함)·영업시간·쿠폰 스냅샷.
- `pending`: 승인 대기 변경. 사진 작업은 `kind: "photo"`와 초안 줄(`change`: `new`·`price_change`·`unchanged`·`photo_attach`, 가격을 모르면 null. `menu_name`: 맞는 저장된 메뉴 이름)을 더한다. 실제 저장은 승인 뒤에만.
- 리뷰 정기 보고(#98): 세션 조회 응답(턴·버튼·`/place`)마다 `review_report`(`enabled`·`schedule`·`next_run`·`text`, 예: "매일 09:00 리뷰 요약 받는 중" / "정기 보고 꺼짐")와 `messages`(사장님 말 없이 비서가 보낸 말: `id`·`role`·`text`·`next_actions`)가 붙는다. 켜진 대화는 조회 때 다음 보고 시각이 지났으면 보고 한 회분(`review_routine.py`)을 `messages`에 한 번만 담는다(마지막 보낸 시각 기록, 밀린 회차는 한 번만). 화면은 `/place`를 1분마다 읽는다. 설정은 대화 메모리에만 있어 서버를 다시 켜면 사라진다. `create_app(clock=...)`로 시계를 바꿔 끼운다.
- planner의 리뷰 보고 설정은 `kind: "settings"`와 `settings.before`·`settings.after`로 받는다. 가게 변경용 `before`·`after`는 null, `changes`는 빈 객체다. 변경할 주기·요일·시각·보고 대상·알림은 코드 응답에도 표시하며 원장 미리보기 승인 뒤에만 저장한다.
- 버튼 쓰기는 미리보기만 만들며 `task.approve`에 `task_id`와 현재 `version`을 보내야 저장한다. 리뷰 답글은 `kind: "review"`의 리뷰 ID·초안으로 표시한다. `task.cancel`은 작업 ID로 취소한다. 버튼 목록과 입력은 `agent/actions.py`가 정본이다.
