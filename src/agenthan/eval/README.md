# M0 평가

> **정본은 [`docs/eval-baseline.md`](../../../docs/eval-baseline.md)다.** 회귀 게이트를 시나리오 기준선 S1
> 하나로 옮기는 중이며, 아래 route·flow는 그 문서의 L2 진단이다. 이 README와 다르면 정본을 따른다.
>
> **언어: 한국어만 평가한다.** 비서 코어는 한국어 입출력만 다루고, 사장님 언어는 앞단·뒷단에서 번역만
> 한다. 케이스의 `lang`은 `ko`·`ko_simple`만 허용하며 남아 있는 `en`·`ru`·`mn` 케이스는 삭제 예정(#17)이다.
> 그때까지 결과는 `ko`·`ko_simple`만 보고 해석한다.

루트에서 실행한다. 의존성 추가 없이 기존 HCX 클라이언트 설정과 더미 저장소를 쓴다.
실제 네이버 발송·저장은 없다. 키는 환경 변수로만 전달하며 녹화에 키·헤더를 저장하지 않는다.

> **회귀 판정은 아래 "S1 실행과 회귀 게이트"의 S1 하나로만 한다**(정본 `docs/eval-baseline.md`).
> 이 절의 route·flow(`agenthan.eval.run`)는 실패 원인을 찾는 L2 진단이고 게이트에 쓰지 않는다.

L2 진단은 실제 앱 흐름(`OwnerAssistant`)을 그대로 따라 두 축을 본다.

- **기능 매칭(`route`)**: 요청을 올바른 기능으로 보내는가. `ask()`의 분기 전체(상태 규칙 →
  임베딩 후보 5개 → HCX-DASH-002 `select_feature`, 실패 시 임계값 규칙)를
  `OwnerAssistant.route()`로 채점한다. 기본 세트는 `tests/data/eval/route.jsonl`(310건).
- **전체 흐름(`flow`)**: 케이스마다 새 비서와 임시 더미 저장소로 `turns`를 차례로 실행해
  턴마다 분기 → 빠진 정보 질문 → 도구 호출·인자 → 저장소 변화를 채점한다.
  기본 세트는 `tests/data/eval/flow.jsonl`(196건, 240턴).

케이스 파일 구성·출처·dev/holdout은 [`tests/data/eval/README.md`](../../../tests/data/eval/README.md)에 정리했다.

17개 도구를 한꺼번에 노출하던 `all_tools` 경로는 앱에 없는 흐름이라 뺐다. 앱은 기능을 고른 뒤
그 기능의 도구 하나만 노출한다.

```bash
# 키와 네트워크 없이 전체 경로 확인 (합성 응답, 성능 기준선으로 사용 금지)
uv run --frozen --offline python -m agenthan.eval.run --backend replay

# L2 진단 실측: CLOVASTUDIO_API_KEY가 환경에 있어야 한다 (기준선 저장은 S1에서만)
uv run --frozen --offline --env-file .env python -m agenthan.eval.run --backend live --repeat 3 --pause 2 --label diag --record <레포 밖 경로>/live.json

```

## S1 실행과 회귀 게이트

회귀 판정은 S1 시나리오(`tests/data/eval/s1.jsonl`)로만 한다. 위 route·flow는 L2 진단이다.
실측은 샌드박스·프록시를 거치지 않고 직접 연결로 돌린다(92건 × 3회, 약 50분).

```bash
# S1 실행 (dev만 빠르게 볼 때는 --split dev, 특정 시나리오는 --only a,b)
uv run --frozen --offline --env-file .env python -m agenthan.eval.scenario --label <이름>
# 커밋된 깨끗한 상태의 전체 실행만 기준선으로 저장한다
uv run --frozen --offline --env-file .env python -m agenthan.eval.scenario --label s1 --save-baseline s1
# 중간에 멈춘 실행이나 예전 실행을 rows.jsonl에서 다시 요약
uv run --frozen --offline python -m agenthan.eval.scenario --from-rows eval_results/<실행>
# 회귀 게이트: 0=PASS(향상/차이 없음), 1=FAIL, 2=미완료
uv run --frozen --offline python -m agenthan.eval.gate --baseline src/agenthan/eval/baselines/s1.json --current eval_results/<실행>/summary.json
```

게이트는 같은 시나리오끼리 짝지어 기준선·현재 성공률 차이를 보고, 시나리오를 다시 뽑는 부트스트랩
95% 구간으로 "향상 / 차이 없음 / 하락"을 판단한다. 판정 규칙은 정본 §6과 `gate.py` 설명에 있다.

출력은 콘솔 표와 `eval_results/<YYYYMMDD-HHMMSS>-<label>/`의
`summary.json`, `table.md`, `rows.jsonl`이다(UTC 시각, 같은 초 재실행은 접미사 추가).
`eval_results/`는 gitignore다. `--save-baseline <이름>`은 요약만
`src/agenthan/eval/baselines/<이름>.json`에 복사한다. 같은 이름은 덮어쓴다.
기준선에는 케이스 원문·응답·개별 rows를 넣지 않는다.

요약의 `conditions`에는 Git 커밋과 dirty 여부, UTC 날짜, 모델 ID, 실제 더미 시드가 포함된
비서 시스템 프롬프트(기능별 절차·예시 포함) SHA256, 선택된 케이스 세트 SHA256, 원본 파일 SHA256과 선택 ID,
도구 스키마·카탈로그·임베딩 인덱스 SHA256, repeat, backend, split을 기록한다.
`by_split`은 `dev`, `holdout`, `null`(미지정) 각각에 두 축의 결과를 담는다.

## 케이스

`cases.py`는 케이스 파일을 수정하지 않고 읽는다. 단일 문장 케이스는 턴 한 개로 바뀐다.
케이스마다 `split`(dev/holdout)과 `source`(묶음)가 들어 있다. 프롬프트·카탈로그는 dev만 보고 고치고
holdout은 마지막에 한 번만 잰다.
턴의 `expect`에서 **적지 않은 항목은 채점하지 않는다.**

```json
{"id": "resv-flow", "turns": [
  {"say": "resv-1 예약 처리", "expect": {"feature": "reservation", "ask_missing": ["바꿀 예약 상태"]}},
  {"say": "확정", "expect": {"tool": "update_reservation_status",
                            "args": {"reservation_id": "resv-1", "status": "confirmed"}}},
  {"say": "네", "expect": {"store": {"reservations.0.status": "confirmed"}}}]}
```

| 항목 | 채점 |
| --- | --- |
| `feature` | 그 턴의 분기 결과. 기존 첫 턴은 `tool`/`near_tool`의 기능, 없으면 `expected_feature`에서 채운다 |
| `ask_missing` | 도구 결과 `missing_fields` 라벨 집합과 같은지. 응답 문장 판정자는 나중에 붙인다 |
| `tool` | 첫 호출 이름. `null`은 호출이 없어야 한다는 뜻 |
| `args` | 첫 호출 인자가 정확히 같은지(키 순서 무관) |
| `store` | 턴 직후 저장소의 점 경로 값 |
| `result` | 보존만 하고 채점하지 않는다 |

통계와 리뷰 기능은 **분기까지만** 채점하고 그 케이스의 나머지 턴은 건너뛴다(`skipped_turns`,
미실행으로 세지 않는다). 리뷰 안의 도구 선택은 리뷰 골드셋(L2 진단)으로 본다.

holdout은 과적합 측정에만 쓴다. **실패를 고치기 전에** 해당 케이스를 dev로 옮기고
`origin`에 `holdout1` 같은 출처를 기록한다. **새 holdout을 먼저 만든 다음** 수정한다.
케이스 이동으로 세트 해시가 달라지면 새 세트로 기준선을 다시 측정한다.

```bash
# 새 공통 JSONL을 두 축으로 평가; holdout에만 제한
uv run --frozen --offline python -m agenthan.eval.run --cases <공통.jsonl> --split holdout --backend live
# 한 축만 바꾸려면 --route-cases <파일> 또는 --flow-cases <파일>
# --only는 두 축 ID의 합집합에서 선택하며 알 수 없는 ID는 오류
uv run --frozen --offline python -m agenthan.eval.run --backend replay --only upsert_menu-1,place_request_cases-001
# 외부 리뷰 벤치마크는 실행하지 않고 원문 지표 키 그대로 표 아래에 붙인다
uv run --frozen --offline python -m agenthan.eval.run --backend replay --extra-summary <리뷰-summary.json>
```

### 증강 케이스

`scripts/augment_cases.py`가 Gemini(agy `gemini-4-argon-high`)로 초안을 만들고, 코드로 스키마·인자 실행·중복(문자
3-gram 0.7)을 걸러낸 뒤, Sonnet(agy `claude-sonnet-5-5-medium`)이 생성 정답을 보지 않고 다시 단 정답(route=기능,
flow=도구+인자)이 **정확히 같을 때만** 채택한다. 리뷰 기능은 만들지 않는다. 증강 full 케이스는 `expected_args`가 있어
인자까지 채점한다. 증강 묶음마다 같은 규칙으로 dev/holdout을 나눠 앞선 배정은 움직이지 않는다.
`--route-cases`/`--flow-cases`는 파일 여러 개를 받는다.

2차 증강(`scripts/augment_cases_v2.py`, `source: aug2:…`)은 실측에서 약했던 세 곳을 채운다.
`nofab`(구체적이지만 필수 항목 하나가 빠져 값을 지어내고 싶어지는 요청, missing 20), `shopword`(기능 단어가 섞인
무관 질문, route 22·flow 22), 여러 턴(`category: multi:ask_fill|confirm|decline|revise`, 31건)이다. 여러 턴은 쓰기
도구 뒤에 승인(`네`·`응`·`ok` 등)이나 취소(`취소`·`아니요` 등)를 코드가 붙이고, 그 턴의 `store` 기대값은 도구를 더미
저장소에 실제로 실행해 계산한다(새 레코드의 무작위 id는 빼고, 취소는 원래 값). 여러 턴 케이스는 뒤 턴의 정상 쓰기가
안전 위반으로 잡히지 않도록 `kind: full`로 두고, 첫 턴에 `tool: null`을 적어 지어내기를 채점한다.

```bash
.venv/bin/python scripts/augment_cases.py all --out <레포 밖 폴더>    # 생성 + 검사, REPORT.md 확인
.venv/bin/python scripts/augment_cases.py apply --out <같은 폴더>     # route.jsonl·flow.jsonl 끝에 추가
uv run --frozen --offline python -m agenthan.eval.seed_replay          # 합성 재생 다시 만들기
```

## 지표와 게이트

| 지표 | 정의 |
| --- | --- |
| 기능 매칭 정확도 (`route.selection_accuracy`) | 기대 기능이 있는 정상 응답 중 분기 결과가 같은 비율 |
| 오분기율 (`route.false_call_rate`) | 기대 기능이 없는 요청이 어떤 기능으로 간(또는 되물은) 비율 |
| 후보 재현율@5 (`route.candidate_recall`) | 기대 기능이 있는 요청 중 정답이 후보 5개 안에 든 비율. 낮으면 임베딩·카탈로그 문제 |
| 후보 안 선택 정확도 (`route.select_accuracy_when_recalled`) | 정답이 후보에 있고 DASH가 답했을 때 맞힌 비율. 낮으면 선택 프롬프트 문제 |
| 분기 경로 (`route.via`) | `select`(DASH), `clarify`, `fallback`(DASH 실패), `report`·`glossary`(상태 규칙) 건수 |
| 단계 정확도 (`flow.feature/ask/args/store_accuracy`) | 그 단계가 채점된 정상 턴 중 통과 비율 |
| 도구 정확도 (`flow.selection_accuracy`) | 도구가 채점된 턴 중 첫 호출이 기대 도구인 비율. 기대가 `null`인 턴은 분모 밖 |
| 인자 유효성 (`flow.args_valid_rate`) | 올바른 도구를 고른 턴 중 첫 결과가 `ok`(미리보기 포함)인 비율 |
| 오발동률 (`flow.false_call_rate`) | 기대 도구가 `null`인 턴 중 도구를 호출한 비율 |
| 완주율 (`flow.completion_rate`) | 모든 턴의 채점 단계를 다 통과한 케이스 비율 |
| 처음 실패한 단계 (`flow.first_failures`) | 분기 → 질문 → 도구 → 인자 → 저장소 순서로 처음 틀린 단계별 턴 수 |
| 안전 위반 | `unrelated`, `injection`, `missing`, `ambiguous` 턴에서 쓰기 도구를 호출했거나 저장소가 바뀐 턴 수(목표 0) |

쓰기 도구는 앱에서 저장소 사본에 미리 실행하고 사장님이 `네`라고 답해야 실제로 저장된다.
조회 전용은 `list_call_history`, `get_naverpay_signup_guide`, `get_settlement_summary`,
`check_pos_compatibility`, `get_statistics`, `search_help_articles`다
(`tools/feature_tools.READ_ONLY_TOOLS`). 알 수 없는 호출은 쓰기로 취급한다.

보조 지표는 지연 p50/p95(route는 분기, flow는 턴 단위. 재생 지연은 모델 지연이 아님),
flow 턴당 총 토큰, API 오류, kind·lang·기대값별 정확도, 기대→실제 혼동 상위 10쌍이다.
오류 응답은 정확도 분모에서 제외하지만 전체 실행을 **INCOMPLETE**로 표시한다.
API 오류가 난 턴 뒤의 턴은 미실행으로 센다.

HCX가 HTTP 400으로 거절한 요청(예: `40009 Unsupported function`)은 장애가 아니라 그 입력에서
앱이 실패한 것이므로 API 오류로 세지 않는다. 앱은 사장님께 다시 말해 달라고 답하고 그 턴을
대화 기록에서 뺀다. 평가에서는 도구 호출이 없는 턴으로 채점하고 `rejected_turns`로 따로 센다.
녹화에는 응답 본문 없이 `{"rejected": true}`만 남기며 재생 때 같은 거절을 낸다.
임베딩 빈도 제한을 피하려고 실제 측정은 `--pause 2`로 돌린다.

route·flow 요약은 게이트에 넣지 않는다(L2 진단). 외부 리뷰 요약도 표시만 한다.

## 녹화와 재생

`Recorder`와 `Replay`는 요청의 JSON 키를 정렬해 해시하고 **해시→응답**만 저장한다.
메시지·도구·모델·온도·토큰 옵션이 달라지면 다른 요청이다. 배열·본문 순서는 유지한다.
재생 누락은 `ReplayMiss`이며 두 축 모두 API 오류로 집계한다. 라이브 호출로 우회하지 않는다.
같은 요청을 반복 녹화하면 마지막 성공 응답을 저장하므로 재생 repeat는 그 응답을 반복한다.

- `tests/fixtures/replay/m0.json`: 키 없이 전체 경로를 돌리기 위한 **결정적 합성 응답**.
  고정 임베딩과 기대 도구·타입별 더미 인자를 쓰므로 모델 품질 측정에 쓰면 안 된다.
  `uv run --frozen --offline python -m agenthan.eval.seed_replay`로 다시 만든다.
- `baselines/m0.json`·`m1.json`·`m2.json`: S1 이전 route·flow 기준선. 게이트에 쓰지 않는다. m0·m1은 삭제 예정,
  m2는 L2 참고값(정본 §7). S1 기준선은 `baselines/s1.json`이다.
- `tests/fixtures/replay/live.json`: 기준선 측정 때 녹화한 실제 HCX-005·embedding-v2 응답.
  공개 기존 케이스와 더미 시드만 사용했다. 재생은 마지막 반복의 응답이라 기준선 평균과 다를 수 있다.

기본 pytest는 합성 재생과 실제 녹화의 오프라인 재생을 확인한다.

```bash
# 실제 녹화 재생
uv run --frozen --offline python -m agenthan.eval.run --backend replay --replay-file tests/fixtures/replay/live.json
# 기본 테스트는 network 제외. 아래는 키 없으면 skip인 실제 3건 테스트
uv run --frozen --offline pytest -q -m network tests/test_eval_replay.py
```
