# web — 사장님 화면 (React + Vite)

사장님이 비서와 대화하며 가게 정보를 보는 화면이다. 테스트용이면서 발표용 최종 화면이다.

- 세 칸: 왼쪽 새 대화·자주 쓰는 일·최근 대화, 가운데 비서와 대화(사진 첨부·승인 카드), 오른쪽 사이드카(비서 프로필 + 내 가게·리뷰·손님 통계 탭).
- 서버는 `/assistant` API만 쓴다(`src/agenthan/app/README.md`). 비서 코드는 바꾸지 않는다.
- 도구 이름·세션 id·영문 코드는 화면에 보이지 않는다. 버튼은 액션 API를 호출하고 응답의 다음 액션을 대화에 표시한다. 일반·리뷰 승인 카드는 작업 ID와 현재 버전으로 승인한다. 사진 초안은 서버가 버전과 액션 승인을 지원하지 않아 기존 대화 방식으로 처리한다.
- 리뷰·손님 통계 탭은 그 탭이 열려 있을 때만 읽기 API(`reviews`·`report`)로 불러오고, 비서 답이 오면 다시 읽는다.

## 화면 경로

| 주소 | 화면 | 백엔드 |
|---|---|---|
| `/` | 사장님 비서 (이 문서의 나머지) | `/assistant` |
| `/phone` | 손님 전화 주문 통화 화면 (Part C) | `/api/phone/ws` |
| `/owner?key=…` | 사장님 전화 주문 카드 (Part C, 언어 기본 ru) | `/api/owner/*` SSE |

`src/main.tsx`가 주소를 보고 둘 중 한 쪽만 불러온다. 전역 CSS가 달라서(`styles.css` / `index.css`) 섞지 않는다.
Part C 화면은 `src/screens/`(`PhoneApp.tsx`·`phone/`·`owner/`)에 있고, 왼쪽 메뉴 "전화 주문"에서 새 탭으로 연다.

## 실행

```bash
# 레포 루트, :8000. 전화 서버가 /assistant도 같이 낸다(발표 서버와 같은 모양)
uv run --env-file .env python -m agenthan.voice.server --port 8000
# 비서만 볼 때는 이것만 띄워도 된다
uv run --env-file .env uvicorn agenthan.app.server:app
cd web && npm install && npm run dev   # :5173, /assistant·/api는 :8000으로 프록시
```

Node `^20.19.0 || >=22.12.0`(Vite 8), TypeScript 7.

## 테스트

```bash
cd web && npm test        # Vitest: App을 가짜 API에 붙여 사장님 흐름 확인
npm run typecheck
npm run lint      # oxlint
```

## 구조

| 파일 | 하는 일 |
|---|---|
| `src/api.ts` | 서버와 주고받는 유일한 길(`AssistantApi`). 테스트에서는 가짜로 바꿔 끼운다 |
| `src/useConversation.ts` | 대화 상태 한곳: 메시지·가게 화면·승인 대기·오늘 한 일 |
| `src/labels.ts` | 서버 코드·숫자 → 사장님 문구, 승인 카드 규칙. 화면 문구를 고칠 때 여기부터 본다 |
| `src/storage.ts` | 이 브라우저에만 남기는 최근 대화·대화 기록 |
| `src/photo.ts` | 고른 사진의 임시 미리보기 주소 만들기·풀기 |
| `src/types.ts` | API 응답과 화면 상태의 타입 |
| `src/useLoad.ts` | 탭이 열릴 때 읽기 전용 데이터 불러오기·다시 불러오기 |
| `src/components/` | 왼쪽 목록, 대화, 입력창, 승인 카드, 사이드카, 내 가게·리뷰·손님 통계 탭 |
| `src/styles.css` | 디자인 토큰(네이버 그린 `#03C75A`, Pretendard, 라이트 모드)과 화면 |
