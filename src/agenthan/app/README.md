# app · 통합 진입점

FastAPI 앱 하나에 파트별 라우터를 붙인다: `/assistant`(Part A), `/phone`(Part C, WebSocket, 예정). 여기서 진짜 구현과 가짜 구현을 골라 끼운다(의존성 주입). 합칠 때 이 폴더만 고치면 되게 한다.

## `/assistant` (사장님 화면 백엔드, ADR 0002)

| 요청 | 하는 일 |
|---|---|
| `POST /assistant/sessions` | 대화 세션 시작(세션마다 파일럿 매장 복사본에서 시작한 저장소) |
| `POST /assistant/sessions/{id}/messages` | 사장님 말 한 턴 → `reply`·`place`·`pending`, planner 모드는 `next_actions` 포함 |
| `POST /assistant/sessions/{id}/actions` | 버튼의 `action_id`·`args`·`label`을 실행하고 같은 응답에 `next_actions` 추가 |
| (선택) 오픈 배경 제거 모델 | `uv sync --extra cutout` + `AGENTHAN_CUTOUT_MODEL=rembg`: 1차 흰 배경·2차 보정의 음식 영역을 GrabCut 대신 U2Net(CPU, 사진당 0.4초, 처음 한 번 모델 176MB 받음)으로 자른다. 코드 기본은 꺼짐. 시연 서버 이미지(`deploy/phone/Dockerfile`)는 모델 파일을 빌드 때 받아(SHA-256 고정) 켠다 |
| `POST /assistant/sessions?menu=empty` | 시연용: 메뉴만 비운 가게로 시작(메뉴판 사진으로 채우는 장면). 화면은 주소에 `?menu=empty`를 붙이고 [새 대화] |
| `POST /assistant/sessions/{id}/photos` | 메뉴판 사진·음식 사진(multipart `image` + 선택 `text`) → 같은 모양. 메뉴판은 승인 없이 메뉴 탭에 바로 올리고(`menu[].ai`·`menu[].filling`), 설명·흰 배경 사진은 백그라운드로 채운다(`agent/menu_board.py`) |
| `PATCH /assistant/sessions/{id}/menus/{menu_id}` | 메뉴 탭 직접 수정 `{name?, price?, description?}` → 세션 조회 응답. 승인 카드 없음, 고친 칸의 `ai` 표시를 지움. 빈 이름·같은 이름·0원 422, 없는 메뉴 404 |
| `DELETE /assistant/sessions/{id}/menus/{menu_id}` | 메뉴 탭에서 잘못 읽은 줄 지우기 → 세션 조회 응답 |
| `POST /assistant/sessions/{id}/menus/{menu_id}/polish` | [사진 다듬기] 2차: 메뉴판에서 자른 원본 조각을 CPU 스튜디오 보정(`tools/image_studio.py`: 밝기·색·가운데 정렬·바닥 그림자, 1초 안팎)으로 다시 만든다. GPU 서버(`AGENTHAN_IMAGE_POLISH_URL`, SEED Omni)가 있으면 그쪽(1~2분). `{message, started, ...세션 조회}`. 사진 없는 메뉴는 `started: false` |
| `GET /assistant/sessions/{id}/photos/{name}` | `draft-<n>`: 기다리는 초안 사진, 메뉴 id: 저장된 메뉴 사진. 다른 세션·없는 사진은 404 |
| `GET /assistant/sessions/{id}/place` | 지금 `place`·`pending`·`shop`(가게 이름·업종. 둘 중 하나가 비면 화면이 온보딩을 띄움) |
| `POST /assistant/sessions/{id}/onboarding` | 처음 쓰는 사장님(#94): `name`·`category`(빈칸 422)와 대표 메뉴 `menus` 0~3개(`name`·`price` 양의 정수)를 넣은 값 그대로 `shop`·`menus`에 저장 → 같은 모양. 대화 턴·승인 카드 아님, 실제 발송 없음 |
| `GET /assistant/sessions/{id}/reviews` | 리뷰 탭: 요약(평균 별점·건수·답글 안 단 수)과 리뷰(최신순, 거친 표현을 거른 `text`, 걸렀을 때만 `original`) |
| `POST /assistant/sessions/{id}/reviews/report` | 리뷰 탭 [지금 받아 보기](시연, #98): 보고 설정과 상관없이 정기 보고 한 회분을 `messages`로 → `place`와 같은 모양. 보고 설정·다음 보고 시각은 그대로 |
| `POST /assistant/sessions/{id}/reviews/{review_id}/reply` | 리뷰 카드 버튼(대화 턴 아님: 대화 기록·작업 원장·승인 카드에 남기지 않음). `op`: `draft`(초안 받기, 쓸 만한 초안이 있으면 그대로) · `post`(선택 `text`: 고친 문장) · `skip` · `redraft`(`hint`: `shorter`·`more_apologetic`·`warmer`). 초안 쓰기(HCX)는 세션 잠금 밖에서 → `review`(+`draft`)·`summary`, 실패면 `error`(`reason`·`message`·`issues`) |
| `GET /assistant/sessions/{id}/report?period=week\|day` | 손님 통계 탭: 요약 카드 3개 증감, 유입 채널, 시간대·요일 조회, 키워드(새로 뜬 것 표시), 해 볼 일 한 줄(`tip`). LLM 없음 |

- 파일럿 매장(`pilot.py`, `pilot_store.json`): 서버의 새 대화는 이 가게로 시작하고 비서의 '오늘'은 2026-11-09로 고정된다(일반 기능·리뷰 둘 다). 메뉴 13·리뷰 20(미답글 8)·톡톡·주문·예약·전화·쿠폰·정산이 손님 통계와 같은 이야기다. 평가 시드(`tools/dummy_store_seed.json`)는 그대로.
- `place`: 메뉴(`photo_url`·`illustration_url` 포함)·영업시간·쿠폰 스냅샷. `illustration_url`은 메뉴 대표 그림 주소(`/illustrations/<id>.svg`, 카탈로그에 없는 그림이면 null)로, 사진이 있어도 보낸다(무엇을 보일지는 화면이 정한다). AI 표시(`ai`)·채우는 중(`filling`) 대상이 아니다.
- 메뉴 대표 그림(`src/agenthan/illustration/README.md`): 메뉴가 들어오거나 이름이 바뀌면 저장소가 잠금 안에서 바로(임베딩 없이) 고른다. 세션을 열 때는 그림이 없는 메뉴(예전 세션·시드 가게)를 바로 채우고, 그 뒤와 메뉴가 바뀐 뒤(대화 턴·버튼·메뉴판 채우기 끝·메뉴 탭 저장·온보딩)마다 `fill_illustrations`가 백그라운드에서 분류 기본 그림인 메뉴를 CLOVA 임베딩으로 다시 찾아본다(저장소 잠금 밖, 응답을 막지 않음, 세션마다 하나씩). `create_app(illustration_embed=...)`로 임베딩을 끼운다(운영 `embed_text`, 기본 None은 임베딩 없이).
- `store.json`의 그림 칸: 메뉴마다 `illustration`(그림 id). 최상위 `illustration_catalog`(마지막으로 맞춘 카탈로그 지문, 바뀌면 기본 그림 메뉴를 다시 고름)와 `illustration_tried`(`{메뉴 id: 이름}` 그 이름으로 임베딩까지 해 본 메뉴. 이름이 바뀌면 다시 해 보고, 실패한 이름은 기록하지 않아 다음에 다시 해 본다).
- `pending`: 승인 대기 변경. 사진 작업은 `kind: "photo"`와 초안 줄(`change`: `new`·`price_change`·`unchanged`·`photo_attach`, 가격을 모르면 null. `menu_name`: 맞는 저장된 메뉴 이름)을 더한다. 실제 저장은 승인 뒤에만.
- 리뷰 정기 보고(#98): 세션 조회 응답(턴·버튼·`/place`)마다 `review_report`(`enabled`·`schedule`·`next_run`·`text`, 예: "매일 09:00 리뷰 요약 받는 중" / "정기 보고 꺼짐")와 `messages`(사장님 말 없이 비서가 보낸 말: `id`·`role`·`text`·`next_actions`)가 붙는다. 켜진 대화는 조회 때 다음 보고 시각이 지났으면 보고 한 회분(`review_routine.py`)을 `messages`에 한 번만 담는다(마지막 보낸 시각 기록, 밀린 회차는 한 번만). 화면은 `/place`를 1분마다 읽는다. 설정은 대화 메모리에만 있어 서버를 다시 켜면 사라진다. `create_app(clock=...)`로 시계를 바꿔 끼운다.
- 루틴 설정(#33): `PUT /assistant/sessions/{id}/routines` `{report?: {enabled?, frequency?, weekday?, time?}, drafts?}`. 사장님이 사이드카 스위치로 직접 바꾼 값이라 승인 카드 없이 저장하고 세션 조회 응답을 돌려준다(주기 없이 켜면 매일 09:00, 잘못된 시각은 400). 세션 조회 응답에 `routines`(`drafts`: 답글 초안 미리 쓰기, `report`: 보고 일정 값)가 붙는다. `drafts` 기본값은 `prepare_on_start`, 끄면 새 리뷰(`/reviews/simulate`)에 초안을 미리 쓰지 않고, 켜는 순간 답글 없는 리뷰 초안을 백그라운드로 쓴다. 설정은 세션 메모리에만 있다.
- AI 리뷰 통계·비서 제안: `GET /assistant/sessions/{id}/reviews/stats?period=day|week|month`는 가게 데이터의 마지막 리뷰 날짜까지 리뷰 특징 구간을 규칙 감정(RuleSentiment)으로 세어 좋아요·아쉬워요 TOP3(네이버 9개 테마로 묶음, 직전 같은 길이 기간 대비 증감)와 테마별 합계를 준다. `GET /assistant/sessions/{id}/suggestions`는 지난주 통계와 리뷰의 해석 근거(`report_insights.build_insights`·`reply_insight`)를 카드(`feature_name`·`evidence`·`action`, 비서에게 보낼 `ask` 또는 열 사이드카 `tab`)로 준다. 둘 다 LLM을 쓰지 않는다.
- planner의 리뷰 보고 설정은 `kind: "settings"`와 `settings.before`·`settings.after`로 받는다. 가게 변경용 `before`·`after`는 null, `changes`는 빈 객체다. 변경할 주기·요일·시각·보고 대상·알림은 코드 응답에도 표시하며 원장 미리보기 승인 뒤에만 저장한다.
- 버튼 쓰기는 미리보기만 만들며 `task.approve`에 `task_id`와 현재 `version`을 보내야 저장한다. 리뷰 답글은 `kind: "review"`의 리뷰 ID·초안으로 표시한다. `task.cancel`은 작업 ID로 취소한다. 버튼 목록과 입력은 `agent/actions.py`가 정본이다.
