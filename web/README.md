# web — 사장님 화면 (React + Vite)

대화창(왼쪽)과 더미 스마트플레이스 화면(오른쪽), 전화 주문 데모 화면을 둔다. 기능을 먼저 만들고 UI는 마지막 이틀에 다듬는다(PRD §8).
방향은 `docs/PRD.md`와 `docs/research/04-web-infra-integration.md`를 본다.

지금은 뼈대만 있다. `create-vite` 9.3.4의 `react-ts` 템플릿(React 19.3, Vite 8.3, TypeScript 7.0, 린터 oxlint)에서 시작했다.

## 실행

Node `^20.19.0 || >=22.12.0`이 필요하다(Vite 8 요구). `node -v`로 확인한다.

```bash
cd web
npm ci            # 처음 한 번, package-lock.json 그대로 설치
npm run dev       # 개발 서버 http://localhost:5173
npm run build     # 타입 검사(tsc -b) + 빌드 → web/dist/
npm run lint      # oxlint
```

- `http://localhost:5173/` 사장님 비서 화면 자리, `http://localhost:5173/phone` 통화 화면 자리.
- 개발 서버는 `/api`로 시작하는 요청(HTTP·SSE·WebSocket)을 FastAPI `http://localhost:8000`으로 넘긴다(`vite.config.ts`). 백엔드를 같이 띄우면 화면 코드에서 `fetch("/api/...")`처럼 상대 경로로 부른다.
- 의존성 버전은 `package-lock.json`으로 고정한다. 11/20까지는 올리지 않는다. 새 패키지를 넣을 때는 카톡에 한 줄 공지.

## 폴더

| 폴더 | 내용 | 주인 |
|---|---|---|
| `src/App.tsx` | 주소 경로로 화면 고르기(라우터 라이브러리 없음) | 프론트 담당 |
| `src/api/` | 서버 호출 공용 코드(SSE 파싱 등) | 프론트 담당 |
| `src/state/` | 화면 상태(`useAgent` 등) | 프론트 담당 |
| `src/screens/place/` | 더미 스마트플레이스 화면(아직 없음) | Part A + 디자인 |
| `src/screens/phone/` | 전화 주문 통화 화면. WS 클라이언트·마이크 워클릿·재생도 여기 | Part C |

CSS 도구는 아직 넣지 않았다. 스타일은 `src/index.css`와 각 화면 옆 CSS 파일로 시작한다.
