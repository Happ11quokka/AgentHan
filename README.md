# AgentHan

외국인 사장님의 요청을 네이버 스마트플레이스 기능으로 연결하는 비서입니다.

## 플레이스 기능 매칭

```python
from agenthan.rag.place_matcher import get_feature, match_features

for match in match_features("메뉴 가격을 바꾸고 싶어요"):
    feature = get_feature(match.feature_id)
    print(feature["name_ko"], match.score, match.keyword_only)
```

`src/agenthan/rag/place_features.json`에는 18개 기능의 쉬운 설명과 한국어·영어
동의어를 저장합니다. 메뉴와 리뷰 답글만 `input_supported=true`이며,
`input_fields`의 `id`, `name_ko`, `required`에서 도구 스키마와 필수 입력 질문을 만듭니다.
메뉴 등록·가격 수정과 기존 리뷰 답글은 HCX-005 Function calling으로 로컬 더미 가게에
저장합니다. 실제 네이버 API 연동이나 답글 발송은 수행하지 않습니다.

## 사장님 비서 대화

`CLOVASTUDIO_API_KEY`를 환경변수로 전달한 뒤 실행합니다. `.env`는 읽지 않습니다.
새 의존성 없이 표준 라이브러리 `urllib`로 HCX-005를 호출합니다.

```sh
uv run python -m agenthan.agent.owner_assistant
# 별도 더미 저장소 경로 지정
uv run python -m agenthan.agent.owner_assistant --store /tmp/agenthan-demo/store.json
```

예: `신메뉴 연습 차를 등록하고 싶어요` → 가격 질문 → `5000원`.
대화 이력은 실행 중 턴 사이에 유지되며, `exit` 또는 EOF로 종료합니다.
키가 없거나 API/저장소 오류가 발생하면 민감 정보 없는 메시지와 종료 코드 1을 반환합니다.

기본 저장 위치는 gitignore 대상인 `.agenthan/store.json`입니다. 첫 유효한 저장 시
`src/agenthan/tools/dummy_store_seed.json`의 가상 가게·메뉴·리뷰를 복사하여 사용하며,
시드 자체는 수정하지 않습니다. 쓰기는 같은 폴더의 임시 파일을 원자적으로 교체합니다.
같은 메뉴 이름(유니코드·대소문자·연속 공백 정규화)은 가격을 수정하고 이전 가격을 반환합니다.
이름이 다르면 등록하며 가격은 양의 정수(원)만 허용합니다.
리뷰 id, 리뷰 원문, 또는 한 리뷰에만 해당하는 원문 일부로 기존 리뷰를 찾아
사장님이 제공한 답글을 저장하고 `saved_not_sent`로 표시합니다. 리뷰를 못 찾거나 여러 개면 저장하지 않고
후보 리뷰를 돌려줍니다. 저장 응답에는 파일을 다시 읽은 레코드가 포함됩니다.

필수 값이 없거나 잘못되면 파일을 변경하지 않고 카탈로그의 항목 이름으로 되묻습니다.
턴마다 기능을 매칭합니다. 입력을 받는 중이면 새 발화가 아무 기능에도 맞지 않거나
진행 중인 기능이 후보에 있을 때 그 기능을 유지하고, 다른 기능만 맞을 때 전환합니다.
상위 후보의 도구만 노출하고, 예약 등 미지원 기능은 이름과 설명만 안내합니다.
관련 없는 요청에는 가능한 작업을 안내합니다. 한 턴의 도구 호출은 최대 3회입니다.
메뉴 설명·사진, 답글 초안 생성·말투 변환, 메뉴 삭제·품절, 음성, PDF/웹 검색·메일은
지원하지 않습니다. 스키마에 포함된 선택 항목(`description`, `photo`, `tone`)에 값을
보내면 저장을 거부하며, 외부 발송 도구는 없습니다.

매칭은 키워드 점수 30%와 질의·기능 임베딩 코사인 유사도 70%를 더하고,
점수가 `RELEVANCE_THRESHOLD`(0.48)를 넘는 후보를 최대 `top_k`개 반환합니다.
키워드는 유니코드·대소문자를 정규화하고 한국어 조사와 띄어쓰기를 흡수합니다.
일치한 동의어가 하나면 0.7점, 추가로 일치할 때마다 0.1점을 더해 최대 1점입니다.
빈 요청과 관련 없는 요청은 빈 리스트를 반환합니다.

기능 벡터는 저장된 인덱스를 사용하고 실행 중에는 질의만 임베딩합니다.
키 미설정, API 오류, 잘못되거나 오래된 인덱스에서는 키워드 점수만 사용합니다.
이때 각 결과의 `keyword_only=True`, `embedding_score=None`으로 표시합니다.
`embed` 인자에 함수를 주입하면 API 없이도 하이브리드 점수를 확인할 수 있습니다.

## 인덱스 생성과 검증

`CLOVASTUDIO_API_KEY`는 환경변수로만 전달합니다. `.env`를 읽거나 만들지 않습니다.
카탈로그 변경 후에는 실제 CLOVA API를 사용하는 다음 명령으로 인덱스를 갱신합니다.
모든 벡터 생성이 성공해야 기존 파일을 교체합니다. 실패 시 종료 코드는 1입니다.

```sh
uv run python -m agenthan.rag.place_matcher build-index
```

인덱스에는 카탈로그 파일 바이트의 SHA-256과 기능별 1024차원 벡터가 들어갑니다.
임베딩 입력은 기능의 한국어 이름, 쉬운 설명, 한국어·영어 키워드를 줄바꿈으로 합친
텍스트입니다. [CLOVA 임베딩 v2 문서](https://api.ncloud-docs.com/docs/clovastudio-embeddingv2)의
Bearer 인증·요청 ID·성공 코드 형식을 사용하고, 429와 5xx는 최대 두 번 재시도합니다.
기능 설명은 [스마트플레이스 소개](https://new.smartplace.naver.com/introduction/smartplace)와
[place+ 안내](https://place.naver.co.kr/placeplusbeta)를 참고합니다.
서비스 이용 자격과 제공 조건은 네이버에서 확인해야 합니다.

```sh
# 기본 테스트: 키와 네트워크 없이 실행
uv run --frozen --offline pytest -q
uv run --frozen --offline ruff check .
uv run --frozen --offline ruff format --check .

# 환경변수 키와 네트워크가 필요하며 키가 없으면 skip
uv run --frozen --offline pytest -q -m network
```

`--offline`은 uv의 패키지 다운로드를 막습니다. `network` 테스트의 CLOVA 호출은
네트워크를 사용합니다. 기본 pytest 실행에서는 해당 테스트를 제외합니다.
API 평가는 호출 제한을 고려해 요청 사이에 약 1초를 두며, 키워드 전용 전환도 실패로 봅니다.
비서의 오프라인 테스트는 모의 chat/embed와 `tmp_path` 저장소로 입력 재질문, 가격 수정,
답글 저장, 잘못된 인자 거부, 도구 제한과 오류 정제를 확인합니다. `network` 테스트에는
실제 HCX-005 메뉴 도구 호출과 임시 더미 저장소 저장 검증도 포함됩니다.
평가 세트는 `tests/data/place_request_cases.jsonl`의 새로 작성한 일반 문장 64개입니다.
한국어 27개, 서툰 한국어 15개, 영어 22개이며 무관한 문장 6개를 포함합니다.
실제 매장명·사람 이름·인터뷰 원문은 사용하지 않습니다. 테스트 실패 메시지에는
오분류 문장을 표시합니다. 정확도는 이 고정 세트에 대한 검증이며 사용자 요청 전체의
정확도를 보장하지는 않습니다.

## 18개 기능 도구와 도구 선택 평가

`src/agenthan/tools/feature_tools.py`에 메뉴·리뷰 답글 외 16개 기능의 도구를 정의합니다
(`all_tool_schemas()`로 18개 전체). 모두 로컬 더미 저장소나 고정 더미 자료만 다루며 실제 네이버
연동·발송은 없습니다. 인자는 저장 전에 검증하고, 잘못되면 `needs_input`으로 되묻습니다.
아직 사장님 비서 대화(`owner_assistant`)에는 연결하지 않았습니다.

`tests/data/tool_selection_cases.jsonl`(정보가 다 있는 요청 54개, 정보가 빠진 요청 8개,
관계없는 요청 6개)로 18개 도구를 모두 노출했을 때 HCX-005가 맞는 도구를 고르는지 측정합니다.

```sh
uv run --env-file .env python -m agenthan.eval.tool_selection --out tool_eval.jsonl
# 반복 측정, 일부 사례만
uv run --env-file .env python -m agenthan.eval.tool_selection --repeat 3 --only create_coupon-1,missing-coupon
```

지표: `selection_accuracy`(기대 도구를 첫 호출로 고른 비율), `wrong_tool`, `no_call_when_expected`,
`false_call_rate`(호출하면 안 되는 사례에서 호출한 비율), `args_valid_rate`(맞게 고른 호출 중 인자가
검증을 통과한 비율), 도구별·언어별 정확도와 틀린 사례 목록.
