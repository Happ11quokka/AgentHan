# naver-review-clf — 네이버 플레이스 리뷰 [특징] 분류 복제기

네이버 방문자 리뷰 탭의 **[특징] 필터 16개 클래스**와 **굵게 표시 구간**을 비슷하게 재현하는 분류기입니다.
목적은 네이버 분류기를 개선하는 것이 아니라, 우리가 접근할 수 없는 네이버 분류 결과를 대신 만들어서
**그 위에 붙는 다른 도구(리뷰 분석·요약, 보고 루틴 등)를 검증**하는 것입니다.

클래스: 맛, 만족도, 서비스, 음식량, 가격, 분위기, 메뉴, 대기시간, 청결도, 목적, 위치, 예약, 전망, 배달, 방역, 주차

## 빠른 시작

```bash
uv sync                                   # src/의 패키지까지 설치 (스크립트·테스트는 sys.path를 고치지 않음)
uv run python scripts/classify.py "웨이팅 30분 했는데 음식은 맛있었어요. 직원분이 불친절했어요."
uv run pytest -q
```

아래 예시의 `python scripts/...`는 `uv run python scripts/...`로 실행하거나, `source .venv/bin/activate`로 가상환경을 켠 뒤 실행합니다. pip를 쓴다면 `pip install -e .`로 패키지까지 설치해야 합니다.

```python
import sys

sys.path.insert(0, "src")
from naver_review_clf import get_backend

clf = get_backend("baseline")  # 로컬 모델, 키 불필요
# clf = get_backend("hcx")      # HyperCLOVA X, CLOVASTUDIO_API_KEY 필요

r = clf.classify("음식도 빨리 나오고 직원분들도 친절하세요")
r.labels  # ['서비스', '대기시간']
r.to_dict()  # {"text":..., "features":[{"feature":"서비스","highlights":[{"start":..,"end":..,"text":"직원분들도 친절하세요"}],"score":..}]}
```

**다른 도구는 `classify()`와 `ClassifyResult` 형식에만 의존하세요.** 백엔드를 baseline에서 hcx로 바꿔도 도구 코드는 그대로 동작합니다.

## 구조

```
src/naver_review_clf/
  schema.py        16개 클래스, 실제 범위 설명(FEATURE_GUIDE), 출력 형식
  segment.py       절 단위 분절기 (네이버 하이라이트 경계 재현)
  dataset.py       조사 엑셀 → data/reviews.jsonl (양성/확실한 음성 라벨)
  evaluate.py      백엔드 공통 평가 (네이버와의 일치도)
  backends/
    baseline.py    TF-IDF 문자 n-gram + 특징별 로지스틱 회귀
    hcx.py         HyperCLOVA X (CLOVA Studio OpenAI 호환 API)
scripts/
  train_baseline.py   학습·튜닝·평가·저장
  evaluate.py         --backend baseline|hcx [--limit N]
  classify.py         CLI 분류
  make_fewshot.py     HCX용 few-shot 예시 생성
  check_alignment.py  분절기 경계 재현율 측정
  make_snapshot.py    다른 도구 검증용 고정 입력 세트 생성
data/  naver_reviews_raw.xlsx(조사 원본), reviews.jsonl(가공), fewshot.json, snapshots/(도구 검증용 고정 입력)
models/ baseline.joblib, baseline_metrics.json
```

## 데이터와 라벨

명동 60개 매장, 특징별 최신 20건 → 고유 리뷰 9,237건 (학습 50개 매장 / 테스트 10개 매장, 매장 단위로 분리).

- **양성**: 특징 f 필터에서 나온 리뷰 → f 확정, 굵게 표시 구간까지 확보
- **확실한 음성**: 표본이 최신순 20건이므로, f 표본의 가장 오래된 날짜보다 최근인데 f 표본에 없는 리뷰는 f가 아님
  (또는 그 매장의 f 리뷰 총수가 표본 수 이하인 경우). 같은 날짜는 판단하지 않음
- **모름**: 나머지 리뷰/특징 쌍은 학습·평가에서 제외
- 16개 특징이 모두 확인된 리뷰 1,091건 기준, 네이버는 리뷰당 평균 **1.89개** 특징을 붙임

## 네이버 분류 방식에 대해 확인한 것 (재현 설계 근거)

1. **절 단위 멀티라벨.** 하이라이트는 어절 경계에서 시작·끝나고, 끝은 문장 종결(~요. ~니다) 또는
   연결어미(~하고 ~해서 ~는데 ~지만). 우리 분절기는 하이라이트의 **83.1%를 경계까지 정확히** 재현 (끝 95.2%, 시작 86.7%).
2. **긍정·부정 구분 없음.** '친절해요'와 '불친절해요'가 모두 서비스.
3. **이름과 실제 범위가 다른 클래스**가 있음 → `FEATURE_GUIDE`에 실제 범위로 정의
   (대기시간 = 웨이팅 + 음식 나오는 속도, 예약 = 예약 + 주문·결제, 방역 = 테이블 간격·환기, 만족도 = 재방문·전반적 호감).
4. **모델 특유의 오분류**: "잘 왔어요" → 배달, "리뷰도 좋고" → 전망. 정답 데이터에 그대로 남아 있으므로
   복제기는 이것까지 따라 배우게 됨 (목적이 '네이버와 비슷하게'이므로 의도된 동작).

## 베이스라인 성능 (테스트 10개 매장, 네이버 대비 일치도)

| 특징 | 정답 리뷰 수 | 리뷰 P | 리뷰 R | 리뷰 F1 | 구간 F1 |
|---|---|---|---|---|---|
| 맛 | 197 | 89.6% | 96.4% | 92.9% | 88.8% |
| 만족도 | 194 | 92.6% | 89.7% | 91.1% | 79.5% |
| 서비스 | 200 | 93.1% | 87.5% | 90.2% | 87.8% |
| 음식량 | 130 | 87.9% | 89.2% | 88.5% | 87.5% |
| 가격 | 163 | 61.7% | 89.0% | 72.9% | 67.9% |
| 분위기 | 172 | 80.0% | 72.1% | 75.8% | 74.3% |
| 메뉴 | 144 | 76.2% | 79.9% | 78.0% | 75.8% |
| 대기시간 | 155 | 73.8% | 81.9% | 77.7% | 75.4% |
| 청결도 | 125 | 85.7% | 81.6% | 83.6% | 82.6% |
| 목적 | 147 | 70.2% | 91.2% | 79.3% | 74.3% |
| 위치 | 114 | 75.2% | 77.2% | 76.2% | 75.3% |
| 예약 | 69 | 94.0% | 91.3% | 92.6% | 90.3% |
| 전망 | 4 | 14.3% | 75.0% | 24.0% | 22.2% |
| 배달 | 27 | 41.7% | 55.6% | 47.6% | 45.5% |
| 방역 | 22 | 57.7% | 68.2% | 62.5% | 60.0% |
| 주차 | 7 | 50.0% | 85.7% | 63.2% | 63.6% |
| **전체(micro)** | 1,870 | **78.7%** | **85.1%** | **81.8%** | **78.5%** |

- 리뷰 단위: "이 리뷰에 특징 f가 있는가" / 구간 단위: 굵게 표시 위치까지 맞췄는가 (겹치면 정답)
- 리뷰당 예측 특징 수 2.59개로, 네이버(약 1.89개)보다 **과잉 예측 경향**이 있음
- 약점: 표본이 적은 전망·배달·방역·주차, 그리고 가격(가격+양을 함께 말하는 문장에서 혼동)

## HyperCLOVA X 백엔드

- 리뷰를 로컬 분절기로 자른 뒤 `[번호] 절` 목록으로 보내고, LLM은 **절 번호 → 특징**만 답함
  → 구간 오프셋이 항상 정확함 (LLM이 문장을 베끼다 틀리는 문제 없음)
- `response_format=json_schema`를 사용하지만 CLOVA Studio는 `strict`를 무시하므로 pydantic 검증 + 1회 재시도
- 시스템 프롬프트는 `FEATURE_GUIDE`(실제 범위 정의), few-shot은 `data/fewshot.json`(16개 특징이 모두 확인된 학습 리뷰 14건)
- **실제 API로는 아직 검증 전** (가짜 응답으로 파싱·재시도·구간 병합만 테스트됨)

```bash
export CLOVASTUDIO_API_KEY=...        # CLOVA Studio [API 키] 메뉴의 테스트 키
export HCX_MODEL=HCX-005              # GET /v1/openai/models 로 사용 가능 모델 확인
python scripts/evaluate.py --backend hcx --limit 200 --out models/hcx_metrics.json
python scripts/evaluate.py --backend baseline --limit 200   # 같은 표본으로 비교
```

## 도구 검증용 스냅샷

`data/snapshots/test_baseline.jsonl` — 테스트 10개 매장 리뷰 1,575건 + 베이스라인 분류 결과.
리뷰 분석·요약, 보고 루틴은 이 파일을 입력으로 개발하면 분류기를 다시 학습해도 테스트 입력이 바뀌지 않습니다.
필드 설명·매장별 건수·기간은 같은 폴더의 `.manifest.json`에 있습니다.

- 기간 필터는 `visit_date`(방문일) 기준. 수집 데이터에 작성일이 없음
- `rating`은 약 46%가 null (네이버가 별점을 표시하지 않음), `has_owner_reply`는 수집하지 않아 null
- `features`는 긍정·부정을 구분하지 않으므로 불만/칭찬 분석은 도구 쪽에서 따로 판단해야 함
- 디저트 매장 예시가 필요하면 홍만당·코인(카페,디저트)

```bash
python scripts/make_snapshot.py              # 다시 만들기 (HCX 평가 후 --backend hcx 로 비교본도 가능)
```

## 리뷰 도구: analyze_reviews (src/review_tools)

도구 명세(`src/review_tools/specs.py`)를 구현한 첫 도구입니다. 스냅샷을 리뷰 데이터로 씁니다.

```bash
python scripts/try_analyze.py --store 홍만당                                 # 최근 리뷰 어때? (7일 0건 → 마지막 리뷰 안내)
python scripts/try_analyze.py --store 홍만당 --period all --focus complaint   # 불만 분석
python scripts/try_analyze.py --store 홍만당 --period all --topic 딸기모찌     # 메뉴별
python scripts/try_analyze.py --store 보배반점 --period 30d --format report     # 보고서 형식
python tests/test_analyze.py
```

- `repo.py` 리뷰 저장소 인터페이스. `SnapshotRepo`를 팀 DB 구현으로 바꾸면 나머지는 그대로
- `topic.py` topic → 메뉴 + 네이버 16개 특징. 남는 단어가 있으면 `topic_unresolved` + 후보를 돌려줘 되묻게 함.
  메뉴·별칭은 `data/menus_sample.json`(홍만당 예시)이며 실제로는 사장님 RAG에서 받음
- `sentiment.py` 특징 구간별 긍정·부정. 지금은 오프라인 규칙(`RuleSentiment`)
- `analyze.py` 기간·topic·focus 필터 → 집계 → 예시·요약. 0건이면 `last_review`, `next_actions`

오프라인 감정 규칙의 한계 (별점 대비, 별점은 판단에 쓰지 않고 텍스트만): 별점 2 이하 리뷰의 60%를 부정·혼합으로 잡고,
별점 4.5 이상 리뷰는 3%만 잘못 부정으로 봅니다. '바퀴벌레 기어다녀요'처럼 부정 단어 없이 불만을 말하는 리뷰를 놓치므로
HCX 감정 판단(`span_sentiment`를 같은 인터페이스로 구현)과 HCX 요약(`insights_source: hcx`)이 다음 작업입니다.

## 대화 데모 (E2E): 사장님 한마디 → 도구 → 답변

`src/review_tools/agent.py` + `scripts/chat_demo.py`. HCX 호출이 필요하므로 CLOVA Studio에 접속할 수 있는 환경에서 실행합니다.

```bash
export CLOVASTUDIO_API_KEY=...   # uv sync 후 .venv를 켜거나 아래 명령 앞에 uv run
python scripts/chat_demo.py --store 홍만당 --analysis hcx --ask "요즘 손님들 불만 뭐야?"
python scripts/chat_demo.py --store 홍만당 --analysis hcx --lang en --ask "What do customers say about strawberry mochi?"
# 답글 흐름 (같은 대화로 이어서)
python scripts/chat_demo.py --store 홍만당 --analysis hcx --reset-replies --ask "답글 안 단 리뷰 있어?" \
  --ask "안 좋은 리뷰에 답글 써줘" --ask "사과를 더 정중하게 해줘" --ask "이대로 올려줘"
python scripts/chat_demo.py --store 홍만당 --analysis hcx           # 대화형, 빈 줄이면 종료
```

모델: 도구 선택·답변·답글 작성 **HCX-005**, 감정 판단 **HCX-DASH-002**, 리뷰 특징 분류 **로컬 베이스라인** (2026-11-07 비교 결과).

도구 4개: `analyze_reviews`, `get_unanswered_reviews`, `draft_review_reply`, `post_review_reply`(더미). 명세는 `src/review_tools/specs.py`.

시스템이 LLM 대신 맡는 것 (실제로 HCX가 틀린 경우들):
- 가게·오늘 날짜·방금 보여준 리뷰·직전 초안은 LLM이 아니라 시스템이 넣음
- 사장님 문장에 분명한 기간 표현이 없으면 LLM이 넣은 기간을 무시하고 7일→30일→90일→전체로 자동 확장
- topic을 빠뜨리거나 망가뜨리면 사장님 원문에서 메뉴를 다시 찾음
- 리뷰 인용·답글 초안 원문은 시스템이 답변 아래 붙이고, 번역은 전용 호출 + 메뉴 용어집
- 리뷰 질문에 도구 없이 답하면 재요청 → 그래도 안 되면 시스템이 직접 실행
- 답변 본문이 사장님 언어가 아니면 다시 쓰게 함
- 답글은 정책 검사(개인정보·마스킹 이름·비방·리뷰 수정 요구) 통과 + 사장님 확정 표현이 있어야 등록

답글 품질·안전 (참고: 브런치 '46-smart_cs_automation' 실습):
- **고위험 리뷰**: 이물질·머리카락·벌레·식중독·환불·보건소 등 위생·안전 단어 + 감정이 부정·혼합일 때만 '고위험'.
  미답글 목록 맨 앞(🚨), 답글은 짧은 사과 + 사실 확인 중 + 매장으로 연락 요청 경로, '사장님 확인 필요' 표시, 즉시 알림에도 표시.
  단어만 보면 '고소한 맛', '이상한 맛'까지 걸려서(스냅샷에서 확인) 단어 경계를 좁히고 감정으로 한 번 더 거름
- **검수**: 개인정보(전화·이메일·주민번호·카드), 마스킹 이름, 손님 탓 표현, 보상·환불 약속, 채우지 않은 자리표시, 비방, 리뷰 수정·재평가 요구 → 등록 불가 /
  길이(네이버 300자의 1.3배), 최근 답글과 같은 첫 문장 → 경고
- **작성 프롬프트**: 업체명·업종·대표 메뉴, 네이버 300자, 경로별 작성 방법, 최근 답글 3개(반복 방지)

```bash
python scripts/chat_demo.py --store 원조남산왕돈까스 --analysis hcx --reset-replies --seed-days 200 \
  --ask "답글 안 단 리뷰 중에 급한 거 있어?" --ask "안 좋은 리뷰에 답글 써줘" --ask "이대로 올려줘"
```

답글 상태는 `models/replies_mock.json`(더미). 스냅샷에 답글 여부가 없어서 **방문일이 14일보다 오래된 리뷰는 답글이 있다고 가정**합니다.

## 정기 보고 루틴 (src/review_tools/report.py)

- 보고 주기 제안: 최근 4주 리뷰 수로 daily / weekly / biweekly / monthly (홍만당 → 격주, 보배반점 → 매일)
- 설정은 대화로: "보고 얼마나 자주 받는 게 좋아?" → 제안 → "응 그렇게 해줘", "매주 월요일 아침에 보내줘", "나쁜 리뷰 올라오면 바로 알려줘",
  "보고할 때 불만만", "보고 시간 8시로", "보고 그만", "지금 보고서 보여줘". 답글 흐름처럼 **규칙으로 해석** (`report_parse.py`, 한·영·러)
- 설정 저장 `models/report_settings.json`, 실행기 `scripts/run_report.py` (스케줄러가 짧은 간격으로 실행: 보고 시각 도래 확인 + 부정 리뷰 알림)

```bash
python scripts/chat_demo.py --store 홍만당 --analysis hcx --reset-replies --ask "보고 얼마나 자주 받는 게 좋아?" \
  --ask "응 그렇게 해줘" --ask "나쁜 리뷰 올라오면 바로 알려줘" --ask "지금 리뷰 보고서 보여줘"
python scripts/run_report.py --store 홍만당 --check --at "2026-11-21 09:00" --alerts-since 2026-10-24
```

## CLOVA Studio 실측 제약 (2026-11-07)

| 항목 | 내용 | 대응 |
|---|---|---|
| response_format | HCX-005·DASH는 400, HCX-007만 지원 | 프롬프트로 JSON 요청 + 검증 (`request_options`) |
| HCX-007 | max_tokens 불가 | max_completion_tokens + reasoning_effort none |
| tool_choice 도구 지정 | 400 Unsupported function (문서와 다름) | 쓰지 않음 |
| system 메시지 | 요청당 1개 (2개면 400 Invalid messages) | 추가 안내는 user 메시지로 |
| JSON 형식 | 가끔 `{i:8}`, 한국어 라벨 | 보정 + 재시도 + 실패 시 규칙 대체 |
| 호출 한도 | 연속 호출 시 429 | 재시도 8회, 간격 1초(`HCX_MIN_INTERVAL`), 응답 캐시 |
| 언어 | 한국어·영어 안정, 러시아어 문장 품질 낮음 | 데모는 한국어·영어 |

## 다음 단계

1. ~~HCX 백엔드 평가~~ → 분류는 베이스라인 유지 (HCX-DASH-002 리뷰 F1 55.3% vs 80.2%)
2. ~~도구 검증용 고정 입력 세트~~ → `data/snapshots/`
3. ~~HCX 감정 판단·요약 연결~~ → 정확도 88%, `scripts/eval_sentiment.py`
   - 리뷰 10개씩 묶어 판단(기본값, `HCX_BATCH`): 정확도 88% 유지, 부정 구절 재현율 79%→100%, 호출 약 200→73회.
     구절 번호는 묶음 전체에 이어서 매김(리뷰마다 0부터 세면 HCX가 번호를 이어서 매겨 절반이 어긋남), 빠진 리뷰는 작은 묶음 → 단건 순으로 다시 판단
4. ~~set_review_report~~ → 정기 보고 루틴 완료. create_review_request 남음
5. 실제 대상 가게 데이터·답글 여부·다국어 메뉴 별칭 연결 (팀 DB/RAG)
6. 데이터 보강이 필요하면 전망·배달·방역·주차가 많은 매장 추가 수집
