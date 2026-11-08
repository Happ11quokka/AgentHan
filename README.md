# AgentHan

한국어가 서툰 외국인 사장님이 모국어로 말하면, 네이버 스마트플레이스 기능을 대신 찾아 입력까지 해 주는 AI 비서. 한국인 손님 전화 주문은 AI가 한국어로 받는다. NAVER Cloud AI 챌린지 출품작.

- 기획(PRD): [`docs/PRD.md`](docs/PRD.md). 사람과 코딩 에이전트가 먼저 읽는 문서
- 시각화 PRD: [`docs/prd.html`](docs/prd.html) (브라우저로 열기)
- 도식 원본: [`docs/diagrams/`](docs/diagrams/) (draw.io)
- 기술 리서치: [`docs/research/`](docs/research/)
- 팀 SDK: `docs/sdk.md`. gitignore라 레포에 없으니 카톡으로 받아 그 자리에 둔다.

## 3파트

| 파트 | 담당 | 폴더 | 하는 일 |
|---|---|---|---|
| A 사장님 비서 | 노종현, 노근형 | `src/agenthan/agent/`, `tools/` | 요청 → 플레이스 기능 매칭 → tool 호출(메뉴 사진 등록, 리뷰 요약, 통계 리포트), 루틴 먼저 제안 |
| B 가게 지식 | 정민서 | `src/agenthan/rag/` | 메뉴·리뷰 정제 → 사장님 RAG(`StoreKnowledge`) |
| C 전화 주문 | 임동현 | `src/agenthan/voice/` | 브라우저 통화 → STT → 주문 대화 → TTS → 사장님 화면 주문서 |
| 공통 | 모두 | `src/agenthan/core/` | 파트 사이 계약(`contracts.py`)과 가짜 구현(`fakes.py`) |
| 통합 | 모두 | `src/agenthan/app/`, `web/` | FastAPI 진입점, React 화면 |

파트끼리는 `core/contracts.py`만 보고 개발한다. 진짜 구현이 나오기 전엔 `core/fakes.py`로 돌린다. 자세한 규칙은 PRD §7.

## 실행

```bash
uv sync                                # 처음 한 번
git config core.hooksPath .githooks    # 처음 한 번: 실제 데이터 커밋 방지 훅
uv run pytest      # 테스트 (계약 테스트 포함)
uv run ruff check .
```

비밀 키는 `.env.example`을 복사해 `.env`에 채운다. `.env`는 커밋하지 않는다.

## 음식 사진 등록

`/photo ~/cake.jpg 이거 메뉴로 올려 줘`로 음식 사진을 보내면 AI가 이름·설명과 다른 후보를
제안한다. 추측은 틀릴 수 있으므로 내용을 고치고 가격을 알려 준 뒤 `네`로 승인한다. 기존 메뉴의
사진을 바꾸려면 메뉴 이름과 사진 변경을 명시해 확인한다. 사진은 흰 배경 정사각형으로 처리하며,
분리에 실패하면 흰 여백을 더한 원본을 사용한다. 결과는 로컬 더미 플레이스에만 저장되며 사진
여러 장의 동시 등록과 실제 네이버 업로드는 지원하지 않는다. 자세한 내용은
[사장님 비서 안내](src/agenthan/agent/README.md#음식-사진-등록)를 참고한다.

## 협업

- main에 바로 푸시해도 된다. 푸시 전 `git pull --rebase`.
- 자기 파트 폴더만 고치고, `core/`를 고칠 때는 카톡에 한 줄 공지.
- **실제 가게 데이터와 관련 코드는 `data/`·`private/`에만 두고 절대 커밋·푸시하지 않는다.** 레포와 발표 자료는 `fixtures/` 더미 데이터로 통일한다.
- `CLAUDE.md`, `AGENTS.md`, `.claude/` 같은 개인 에이전트 설정은 gitignore. 공통 규칙은 PRD에만 적는다.
