# app · 통합 진입점

FastAPI 앱 하나에 파트별 라우터를 붙인다: `/assistant`(Part A), `/phone`(Part C, WebSocket, 예정). 여기서 진짜 구현과 가짜 구현을 골라 끼운다(의존성 주입). 합칠 때 이 폴더만 고치면 되게 한다.

## `/assistant` (사장님 화면 백엔드, ADR 0002)

| 요청 | 하는 일 |
|---|---|
| `POST /assistant/sessions` | 대화 세션 시작(세션마다 시드에서 시작한 저장소) |
| `POST /assistant/sessions/{id}/messages` | 사장님 말 한 턴 → `reply`·`place`·`pending` |
| `POST /assistant/sessions/{id}/photos` | 메뉴판 사진·음식 사진(multipart `image` + 선택 `text`) → 같은 모양 |
| `GET /assistant/sessions/{id}/photos/{name}` | `draft-<n>`: 기다리는 초안 사진, 메뉴 id: 저장된 메뉴 사진. 다른 세션·없는 사진은 404 |
| `GET /assistant/sessions/{id}/place` | 지금 `place`·`pending` |

- `place`: 메뉴(`photo_url` 포함)·영업시간·쿠폰 스냅샷.
- `pending`: 승인 대기 변경. 사진 작업은 `kind: "photo"`와 초안 줄(`change`: `new`·`price_change`·`unchanged`·`photo_attach`, 가격을 모르면 null)을 더한다. 실제 저장은 승인 뒤에만.
