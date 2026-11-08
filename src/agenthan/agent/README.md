# Part A · 사장님 비서 (`agent/` + `tools/`)

- 담당: 노종현, 노근형
- 하는 일: 사장님 한마디 → 의도 파악 → 플레이스 기능 매칭 → tool 호출 → 결과 정리. 대표 기능 3개(메뉴 사진 등록, 리뷰 요약, 통계 리포트)는 실제 입력까지 한다.
- `agent/`: LangGraph 그래프, 프롬프트, 서브에이전트. `tools/`: tool 함수와 더미 플레이스 API.
- 쓸 수 있는 것: `agenthan.core.*`. Part B·C 내부(`agenthan.rag.*`, `agenthan.voice.*`)는 import하지 않는다.
- 가게 지식은 `StoreKnowledge`로 받는다. 진짜 구현 전에는 `core.fakes.FakeStoreKnowledge.from_fixture()`.
- 자세한 요구사항: `docs/PRD.md` §6.1 · 리서치: `docs/research/01-agent-hcx-langgraph.md` · CLOVA 호출·LangGraph 예제: `docs/sdk.md` 3장, 부록 B-3·B-6

## 지금 구현된 것 (PRD v0.2 기준 위치)

```
사장님 발화
 → OwnerAssistant (agent/owner_assistant.py)
     ① 기능 후보: agent/place_matcher.py (키워드 30% + CLOVA 임베딩 70%, 18개 기능)
     ② 기능 하나 고르기: 지금은 매칭 1위 (PRD는 HCX-DASH-002 select_feature — 아직 안 함)
     ③ 그 기능 도구만 붙여 HCX-005 실행
        ├ menu                    → upsert_menu (tools/owner_tools.py, 더미 플레이스 API에 저장)
        ├ 메뉴판 사진 (A-1)         → ask_photo(): agent/menu_photo.py 초안 → 사장님 승인 → 저장
        ├ review_reply, ai_review → 리뷰 서브에이전트 (agent/review_delegate.py → agent/review/)
        └ 그 밖의 기능              → 이름·설명 안내 (도구는 tools/feature_tools.py에 정의만)
```

| 위치 | 하는 일 |
|---|---|
| `agent/owner_assistant.py` | 오케스트레이터 대화 루프 (CLI: `python -m agenthan.agent.owner_assistant`) |
| `agent/menu_photo.py` | A-1 메뉴 사진 → 초안: HCX-005 사진 읽기 → 가게 지식(`StoreKnowledge`) 대조 → 한국어 이름·설명 |
| `agent/place_matcher.py`, `place_features.json` | 플레이스 기능 카탈로그(=PRD의 `features.json`)와 요청→기능 매칭 |
| `agent/review/` | 리뷰 서브에이전트: 분석·요약, 미답글, 답글 초안·등록(더미), 정기 보고 루틴(A-2·A-4) |
| `agent/review_delegate.py` | 오케스트레이터 → 리뷰 서브에이전트 위임 |
| `tools/dummy_store.py` | 더미 플레이스 API (로컬 `.agenthan/store.json`, 실제 네이버 연동 없음) |
| `tools/owner_tools.py`, `feature_tools.py` | 메뉴 도구와 15개 기능 도구(미연결) |
| `tools/review_clf/` | 리뷰 [특징] 분류기 (`models/baseline.joblib`은 실제 리뷰로 학습해 레포에 없음 · 카톡으로 받기) |
| `tools/knowledge_adapters.py` | `StoreKnowledge`(Part B 계약) → 리뷰 서브에이전트의 리뷰·메뉴 입력 |
| `tools/review_adapters.py` | 더미 플레이스 API → 리뷰 서브에이전트 입력 |
| `tools/image_cutout.py` | M1a 음식 사진 배경 제거 → 흰 배경 정사각형 바이트 (M1b 연결 전) |
| `eval/tool_selection.py` | 16개 도구를 다 붙였을 때 HCX-005 도구 선택 평가 |

공통 HCX 클라이언트는 `core/llm.py`, 임베딩은 `core/embedding.py`를 쓴다.

### PRD 방향과 아직 다른 점

- HCX 호출이 openai SDK(OpenAI 호환 API)다. PRD는 sdk.md 네이티브 REST v3. `core/llm.py`만 바꾸면 되게 모아 뒀다.
- 오케스트레이터가 LangGraph `StateGraph`가 아니라 직접 짠 루프다. 승인(HITL)도 `interrupt()`가 아니라 대화 턴 확인이다.
- 기능 고르기(HCX-DASH-002 `select_feature`)가 없다. 매칭 1위를 그대로 쓴다.
- 통계 리포트(A-3)는 아직 없다.
- A-1 승인은 `interrupt()`가 아니라 다음 턴의 "네/취소/고칠 곳"이다. 웹 폼은 `menu_form()` 값으로 채운다.

더미 플레이스 API 시드(`tools/dummy_store_seed.json`)는 `fixtures/sample_store.json`의
샘플 러시안 베이커리와 같은 메뉴 4개·리뷰 3개를 쓴다. 메뉴 ID·이름·가격·별칭·원어명·설명과
리뷰 ID·본문·평점·방문일의 일치는 `tests/test_seed_alignment.py`로 확인한다.
시드의 미답글 리뷰는 2건(`r02`, `r03`)이며, 주소·전화번호·영업시간·통계는 가상 값이다.

## 실행

```bash
uv run --env-file .env python -m agenthan.agent.owner_assistant
uv run --env-file .env python -m agenthan.agent.owner_assistant --store /tmp/agenthan-demo/store.json
uv run python -m agenthan.agent.place_matcher build-index   # 카탈로그를 고친 뒤 기능 벡터 다시 만들기
uv run --env-file .env python -m agenthan.eval.tool_selection --out tool_eval.jsonl
uv run pytest -q -m network                                 # 실제 CLOVA 호출 테스트 (키 필요)
```

예: `신메뉴 시르니키를 등록하고 싶어요` → 가격 질문 → `8500원`.
메뉴판 사진: `/photo ~/menu.jpg 이거 올려 줘` → 초안 확인 → `3번 가격 9500원` → `네`.

### R3 검색 노출 진단

`tools/naver_search.py`의 공식 NAVER API HUB 클라이언트와 `tools/search_visibility.py`의
한국어 템플릿으로 검색어별 지역 검색 순위·같이 나온 가게, 블로그 전체 글 수·상위 글의 가게명
언급 수를 보여 준다. 지역 검색은 상위 5개만 확인하므로 미노출은 **5위 밖 또는 없음**이다.
개인화 없는 검색 기준이며 실제 화면과 다를 수 있다. 실제 방문·유입 수를 측정하지는 않는다.
오케스트레이터·Agent N 연결은 아직 없다.

```bash
uv run --env-file .env python -m agenthan.tools.search_visibility \
  --name "가상 연습 베이커리" --area "연습동" --category "베이커리" \
  --menu 꿀케이크 --menu 겹케이크
uv run --frozen --offline pytest -q -m network tests/test_naver_search.py
```

인증은 환경 변수 `NAVER_API_HUB_CLIENT_ID`, `NAVER_API_HUB_CLIENT_SECRET`만 사용하며,
없거나 호출에 실패하면 정제한 메시지와 종료 코드 1을 반환한다. `--native-menu`는 원어 메뉴,
`--road-address`는 도로명 주소 보조 판별에 쓴다. `--save data/search/<날짜>.json`으로 저장할 수
있으며 `data/` 밖 경로나 심볼릭 링크 순환 등으로 확인할 수 없는 경로는 거부한다.
실제 검색 결과는 커밋하지 않는다.
가게명은 정규화한 이름의 완전 일치를 우선하고, 도로명 주소는 건물 번호의 하이픈을 유지해
비교한다. 지점명만 다른 가게를 이름의 부분 일치로 판별하지 않는다.
오프라인 테스트는 `FixtureSearch`와 가상 데이터 `tools/search_fixture.json`만 사용한다.
실제 API 테스트는 키가 없으면 건너뛰고 결과를 파일로 남기지 않는다.

### A-1 메뉴 사진 등록

입출력은 한국어다. 사진 속 메뉴 이름만 원어 그대로 읽어 `name_native`로 남긴다.

1. 사진 읽기(HCX-005, 네이티브 REST `core.llm.chat_v3` + `dataUri`): 이름·가격만. 도구 없이 따로 부른다.
2. 가게 지식 대조: 이름·원어 이름·별칭 일치 → 글자 유사도 0.8 이상 → `search_menu`. 맞으면 기존
   메뉴의 이름·설명을 쓴다.
3. 새 메뉴는 HCX-005로 메뉴 하나씩 한글 이름·한 문장 설명(여러 개를 한 번에 보내면 순서가 섞였다).
4. 초안을 한국어로 보여 주고, `네`면 이름·가격이 다 있는 것만 더미 플레이스에 저장한다.
   그 밖의 말은 `edit_menu_draft` 도구로 초안만 고친다(번호·이름·가격·설명·빼기).

실측(2026-11-08, 합성 메뉴판): 베트남어·영어는 이름·가격 모두 정확, 러시아어(키릴)는 가격만 맞고
이름은 다른 단어로 읽었다(Наполеон → Народный). 그래서 가게 지식과 안 맞은 키릴 이름에는
"사진 글자 확인 필요" 표시를 붙인다. 가게 메뉴 목록을 힌트로 주면 없는 메뉴까지 목록 이름으로
바꿔 써서 쓰지 않았다. 사진 1장(메뉴 3~5개)에 6~8초.
리뷰 예: `답글 안 단 리뷰 있어?` → `안 좋은 리뷰에 답글 써줘` → `더 정중하게 고쳐줘` → `이대로 올려줘`.
답글은 `owner_reply.status = "saved_not_sent"`로 저장만 하고 보내지 않는다.

샘플 러시안 베이커리로 데모·캡처하기(처음 쓰는 저장 경로 지정):

```bash
uv run --env-file .env python -m agenthan.agent.owner_assistant \
  --store /tmp/agenthan-bakery-demo/store.json
```

메뉴 사진 등록과 위의 리뷰 답글 대화를 같은 저장소에서 시연한다. 기존 저장 파일은 자동으로
새 시드로 바뀌지 않으므로 새 데모에는 아직 파일이 없는 경로를 쓴다.
리뷰 서브에이전트만 별도 합성 fixture로 돌리는 방법은 `docs/integrate-review-tools.md`에 있다.

분류기·스냅샷·평가 스크립트는 `docs/review-clf.md`, 리뷰 통합 설계는 `docs/integrate-review-tools.md`.

### M1a 음식 사진 배경 제거

`agenthan.tools.image_cutout.cutout_on_white(data, box=None, size=1024, fmt="JPEG")`는
OpenCV GrabCut을 5회 사용한다. 학습 모델·외부 API를 쓰지 않는다. EXIF 회전 후 긴 변을
1024px 이하로 줄여 분리하고, 가장 큰 영역의 구멍을 채우고 경계를 부드럽게 한다. 원본 크기로
합성한 뒤 음식 주변 8% 여백을 더해 흰 배경 정사각형으로 만든다. 원본을 수정하거나 저장하지
않고 `CutoutResult(image, mime, ok, reason, fg_ratio)`를 반환한다. JPEG(JPG)·PNG 출력 지원.

`box`는 **EXIF 회전 반영 후** 0~1 좌표 `(x0, y0, x1, y1)`이며, 생략하면 가장자리 8%를
제외한 중앙을 쓴다. 화면 구석의 음식에는 위치 힌트를 준다. 배경과 음식 색이 비슷하면
실패할 수 있고, 분리된 여러 음식 중 가장 큰 덩어리만 남는다. 전경이 전체의 3% 미만 또는
90% 초과이거나 GrabCut이 실패하면 `ok=False`와 한국어 `reason`을 주고 원본에 흰 여백만
더한 정사각형 사진을 반환한다. 빈 파일·잘못된 이미지·20MB 초과·한 변이라도 64px 미만이면
`ImageCutoutError`다. 메뉴 초안·저장·비서 연결은 M1b에서 한다.

```bash
uv run python -m agenthan.tools.image_cutout in.jpg out.jpg --box 0.1,0.1,0.9,0.9
```

확인용 CLI만 출력 파일을 저장한다. `.png` 출력 경로는 PNG, 그 밖에는 JPEG를 쓰며 입력과
같은 출력 경로는 거부한다. `ok=False`인 경우에도 대체 사진을 저장하고 실패 이유를 출력한다.
