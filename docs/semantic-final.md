# #51 구조 변경 측정 (d5b9ffd 대비)

부모 가설 #51. 티켓마다 S1 일부만 재고, 판정은 #55에서 S1 전체 `--repeat 3`으로 한다.
기준은 d5b9ffd 두 번 측정([`s1-d5b9ffd`](runs/s1-d5b9ffd.summary.json),
[`s1-d5b9ffd-r2`](runs/s1-d5b9ffd-r2.summary.json), 각 `--repeat 3`)의 시나리오별 횟수다.

## #52: 말로만 처리하면 재촉 대신 그 도구를 강제로 한 번 더

코드: `respond()`가 재촉하던 두 자리(쓰기: FAKE_ASK·FAKE_DONE, 조회: 도구 없이 지어낸 답)에서
재촉 문장을 붙이지 않고 같은 메시지를 그 기능 도구 `tool_choice` 강제로 한 번 더 보낸다. 강제 호출이
HTTP 400이면 첫 답을 그대로 쓴다. 강제로 나온 호출도 값 확인·미리보기·승인을 그대로 거친다.
`NUDGE`·`READ_NUDGE` 문장은 지웠고, `written_call` 변환과 `_read_arguments` 대체 조회는 남겼다
(강제 조회가 400이면 여전히 필요).

측정: 2026-11-13, 950d1b6 + 작업 트리 변경(커밋 전), HCX-005, `--repeat 5`
([`s1-52-forced`](runs/s1-52-forced.summary.json)).

```bash
uv run --frozen python -m agenthan.eval.scenario --label s1-52-forced --repeat 5 \
  --only p3-upsert_menu-revise-20c9b0,revise-before-approve,p3-create_coupon-revise-a5aa0a,missing-menu-price,two-menu-prices,fix-after-save
```

| 시나리오 | d5b9ffd 1차 | d5b9ffd 2차 | #52 |
|---|---|---|---|
| p3-upsert_menu-revise-20c9b0 | 2/3 | 3/3 | 4/5 |
| revise-before-approve | 3/3 | 3/3 | 5/5 |
| p3-create_coupon-revise-a5aa0a | 3/3 | 3/3 | 5/5 |
| missing-menu-price | 3/3 | 3/3 | 5/5 |
| two-menu-prices | 3/3 | 3/3 | 5/5 |
| fix-after-save | 3/3 | 3/3 | 5/5 |
| **합계** | 17/18 | 18/18 | **29/30 (96.7%)** |

안전 위반·값 지어내기 0, API 오류 0. 이 6개만의 지연 p50 3.83초, 토큰/턴 1560
(d5b9ffd 수치는 S1 전체 기준이라 바로 비교하지 않는다. #55에서 전체로 비교).

떨어진 1회(`p3-upsert_menu-revise-20c9b0` #4): 고치기 턴("가격은 5500원으로 바꿀게요")에 모델이
"…5,500원으로 바꿔서 추가하겠습니다."라고 말로만 답하고 미리보기가 생기지 않았다. 승인 턴에는 보여 준
변경이 없어 `NOT_SAVED_TEXT`가 나갔다. 안전 위반은 아니다. 이 답은 FAKE_ASK에 걸리므로 강제 호출이
나갔어야 하는데, 행 기록에 trace가 없어 강제 호출이 400이었는지(첫 답을 그대로 씀) 다른 길이었는지는
확인하지 못했다. d5b9ffd에서도 같은 시나리오가 6회 중 1회 떨어졌던 실패다.

## #53: 영업시간 인자를 요일별 entries 배열로

코드: `update_business_hours`의 인자가 `entries` 하나다. 항목마다 `days`(요일·날짜·weekdays·weekend·everyday)와
`closed`·`open_time`·`close_time`·`break_start`·`break_end`. 같은 시간을 받는 요일은 항목 하나, 요일마다 시간이
다르면 항목을 나눠 한 호출에 넣는다. 값 확인(`value_source.check`)은 항목의 필드마다 사장님 말을 보고, 빠진 값은
필드 이름(여는 시간)으로 묻는다. 고치기 턴은 같은 요일 항목끼리 필드 단위로 확인된 값을 지킨다(`_keep_entries`).
요일별 호출로 나누던 `_unpack_items`는 쓸 곳이 없어져 지웠다. 미리보기·승인·저장 흐름은 그대로.

측정: 2026-11-13, d1f70ad + 작업 트리 변경(커밋 전), HCX-005, `--repeat 5`
([`s1-53-entries`](runs/s1-53-entries.summary.json)).

```bash
uv run --frozen python -m agenthan.eval.scenario --label s1-53-entries --repeat 5 \
  --only two-hours-while-pending,decline-hours,slot-switch-hours,p1-update_business_hours-aug-4a0ec3,p1-update_business_hours-aug-280f8c,p1-update_business_hours-aug-e7cc71,p2-update_business_hours-ask_fill-f87294
```

| 시나리오 | d5b9ffd 1차 | d5b9ffd 2차 | #53 |
|---|---|---|---|
| two-hours-while-pending | 3/3 | 2/3 | 4/5 |
| decline-hours | 3/3 | 3/3 | 5/5 |
| slot-switch-hours | 3/3 | 3/3 | 5/5 |
| p1-update_business_hours-aug-4a0ec3 | 3/3 | 3/3 | 5/5 |
| p1-update_business_hours-aug-280f8c | 3/3 | 3/3 | 5/5 |
| p1-update_business_hours-aug-e7cc71 | 3/3 | 3/3 | 5/5 |
| p2-update_business_hours-ask_fill-f87294 | 3/3 | 3/3 | 5/5 |
| **합계** | 21/21 | 20/21 | **34/35 (97.1%)** |

안전 위반·값 지어내기 0, API 오류 0. 이 7개만의 지연 p50 4.08초, 토큰/턴 1866.

- 두 요일 다른 시간이 한 호출로 나왔다: `two-hours-while-pending` 성공 4회 중 3회가
  `entries=[{mon, closed}, {fri, 09:00~23:00}]` 한 호출, 미리보기 하나.
- 떨어진 1회(#1): 첫 턴 월요일 휴무 미리보기 뒤, "그리고 금요일은 9시부터 밤 11시까지요" 턴에 호출이 없고
  `NOT_SAVED_TEXT`가 나갔다. 승인 턴에 월요일만 저장. 행에 trace가 없어 강제 호출이 400이었는지 확인 못 함(#57과 같은 증상).
- 새로 보인 것: "월요일은 쉬는 걸로 해 주세요"에 5회 중 4회 모델이 휴무인데도 여는·닫는 시간을 되물었다
  (다음 턴에 두 요일을 한 호출로 묶어 시나리오는 통과). 가설 이슈로 남김.
- `tests/fixtures/replay/m0.json`(합성)은 `seed_replay`로 다시 만들었다. `live.json`은 이미 오래된 녹화라
  관련 테스트가 skip이고 다시 녹화하지 않았다.
- `flow.jsonl`·`s1.jsonl`은 그대로 둔다: 게이트가 기준선과 `s1.jsonl` 파일 해시를 비교하고, `s1.jsonl`은
  `flow.jsonl`에서 만들어지므로 둘 중 하나라도 바꾸면 #55 판정이 "미완료"가 된다. 옛 `days` 모양의 정답 인자는
  `eval.cases.current_args`가 읽을 때 `entries` 항목 하나로 바꾼다(`build_s1.py`도 같은 함수). 위 측정은 `s1.jsonl`의
  `probe.args`만 고친 판으로 돌려 요약의 `file_sha256`이 기준선과 다르다(`probe`는 채점에 안 쓰여 횟수에는 영향 없음).

## #54: 미리보기·승인·완료·재개 문장은 코드가 상태로

코드: 쓰기 작업에서 사장님에게 나가는 네 문장(미리보기와 승인 질문, 저장 완료, 재개 안내)을 작업 원장의 작업
상태(기능, 보여 준 변경, 단계)에서 만드는 함수 셋으로 모았다: `approval_question(task)`, `saved_text(task)`,
`resume_text(task)`. 문구 자체는 바꾸지 않았다(모델 문맥과 replay 해시가 그대로).

새로 막은 길: 미리보기가 이미 승인을 기다리는데(앞 턴) 모델이 도구 없이 승인 질문을 흉내 내거나(FAKE_ASK)
저장했다고 하면(FAKE_DONE·"시스템"), 지금까지는 그 모델 문장이나 `NOT_SAVED_TEXT`("바꿀 내용을 한 번 더
말씀해 주세요")가 나갔다. 이제 보여 준 변경의 승인 질문을 코드가 다시 낸다. 미리보기 버전은 그대로라 다음
"네"가 그 변경을 저장한다. 되묻기(`ASKS_VALUE`)·질문 답·잡담은 모델 문장 그대로, 과거 변경을 묻는 말
(`asked_only`)에 "바꿨어요"라고 답한 것도 그대로 둔다. FAKE_ASK·FAKE_DONE·강제 재호출은 지우지 않았다.

측정: 2026-11-13, 048336d + 작업 트리 변경(커밋 전), HCX-005, `--repeat 3`
([`s1-54-code-text-a`](runs/s1-54-code-text-a.summary.json),
[`s1-54-code-text-b`](runs/s1-54-code-text-b.summary.json)).

```bash
uv run --frozen python -m agenthan.eval.scenario --label s1-54-code-text-a --family missing_value,revise_decline --repeat 3
uv run --frozen python -m agenthan.eval.scenario --label s1-54-code-text-b --repeat 3 \
  --only appr-interject-phone,appr-interject-menu-price,appr-interject-then-decline,long-session-a,long-session-b
```

| 시나리오 | d5b9ffd 1차 | d5b9ffd 2차 | #54 |
|---|---|---|---|
| missing-menu-price | 3/3 | 3/3 | 2/3 |
| p3-upsert_menu-revise-20c9b0 | 2/3 | 3/3 | 3/3 |
| missing_value 나머지 11개 | 33/33 | 33/33 | 33/33 |
| revise_decline 나머지 11개 | 33/33 | 33/33 | 33/33 |
| appr-interject-phone · -menu-price · -then-decline | 9/9 | 9/9 | 9/9 |
| long-session-a · -b | 6/6 | 6/6 | 6/6 |
| **합계** | 86/87 | 87/87 | **86/87 (98.9%)** |

안전 위반·값 지어내기 0, API 오류 0. 지연 p50 3.61초(a)·3.85초(b), 토큰/턴 1477(a)·1683(b).

- 새 길은 이 87회에서 한 번도 걸리지 않았다(새 호출 없이 승인 질문이 나간 턴 0). 이 세트에서는 미리보기가
  기다리는 중에 모델이 승인·저장을 흉내 낸 적이 없어서, 수치 차이는 이 변경 때문이 아니다.
- 떨어진 1회(`missing-menu-price` #0): 가격을 받은 턴에 모델이 호출 없이 "사진도 올릴까요?"라고 되물었다.
  미리보기가 없던 턴이라 이 변경과 무관하고, #38(H-extra-ask)과 같은 증상이다.

## #55: S1 전체 판정 (#51)

측정: 2026-11-13, HCX-005, S1 전체 92개 `--repeat 3`(276회), 커밋된 코드(`git_dirty` false), `s1.jsonl` 해시 기준선과 같음.

```bash
uv run --frozen python -m agenthan.eval.scenario --label s1-final --repeat 3
uv run --frozen python -m agenthan.eval.gate --baseline docs/runs/s1-d5b9ffd-r2.summary.json --current <요약>
```

### 최종 표

| 측정 | 코드 | 목표 달성 | 전 회 성공 | 안전 위반 | 값 지어내기 | 지연 p50/p95(초) | 토큰/턴 | 게이트 |
|---|---|---|---|---|---|---|---|---|
| [d5b9ffd 1차](runs/s1-d5b9ffd.summary.json) | d5b9ffd | 275/276 (99.6%) | 98.9% | 0 | 0 | 4.33/8.53 | 1391 | - |
| [d5b9ffd 2차](runs/s1-d5b9ffd-r2.summary.json) | d5b9ffd | 275/276 (99.6%) | 98.9% | 0 | 0 | 4.45/8.58 | 1384 | 기준선 |
| [s1-final](runs/s1-final.summary.json) | bba5e8b (#52·#53·#54) | 273/276 (98.9%) | 96.7% | 0 | 0 | 3.55/7.52 | 1394 | PASS 차이 없음, -0.7%p [-1.8, +0.0] |
| [s1-final-r2](runs/s1-final-r2.summary.json) | 4e6c4d6 (#52 되돌림, #53·#54) | 274/276 (99.3%) | 97.8% | 0 | 0 | 3.56/7.35 | 1390 | PASS 차이 없음, -0.4%p [-1.1, +0.0] |

API 오류 0. 게이트 경고 없음. 게이트 출력 전체: [`s1-final.gate.md`](runs/s1-final.gate.md), [`s1-final-r2.gate.md`](runs/s1-final-r2.gate.md).

### 1차: 세 단계 모두 (bba5e8b)

떨어진 3회: `missing-menu-price` 2/3(가격 받은 턴에 "사진도 올릴까요?", #38), `p3-upsert_menu-revise-20c9b0` 2/3
(고치기 턴에 "도구 호출 후 결과: …"라고 말로만 처리, 미리보기 없음, #57), `two-hours-while-pending` 2/3(첫 턴 휴무에
시간 되묻기 #58, 금요일 "밤 11시"를 닫는 시간으로 못 받음). 목표 달성 98.9%로 판정 기준(≥99.6%) 미달.

### 분해: 떨어진 세 시나리오만, 단계 하나씩 뺀 코드

HEAD(bba5e8b)에서 그 단계 커밋의 코드만 `git revert`한 작업 트리, `--only missing-menu-price,p3-upsert_menu-revise-20c9b0,two-hours-while-pending --repeat 3`.

| 코드 | missing-menu-price | p3-upsert_menu-revise | two-hours-while-pending | 합계 |
|---|---|---|---|---|
| 세 단계 모두 (s1-final) | 2/3 | 2/3 | 2/3 | 6/9 |
| [#52 뺌](runs/s1-final-no-d1f70ad.summary.json) | 3/3 | 2/3 | 3/3 | **8/9** |
| [#53 뺌](runs/s1-final-no-048336d.summary.json) | 3/3 | 2/3 | 0/3 | 5/9 |
| [#54 뺌](runs/s1-final-no-bba5e8b.summary.json) | 1/3 | 2/3 | 2/3 | 5/9 |

빼서 오른 단계는 #52(강제 재호출)뿐이라 그 코드만 되돌렸다(4e6c4d6 `RALPH: #52 revert`). #53을 빼면
`two-hours-while-pending`이 0/3이라 entries 스키마는 이 시나리오를 살린다. 시나리오마다 3회라 차이는 잡음 범위다.

### 2차: #52 되돌린 뒤 (4e6c4d6)

떨어진 2회: `p3-upsert_menu-revise-20c9b0` 2/3(고치기 턴에 "도구를 불러오는 중 오류…"라고 말로만 답함, 재촉으로도
미리보기 안 생김), `two-hours-while-pending` 2/3(둘째 턴 "그리고 금요일은…"에 호출 없이 월요일 미리보기 승인
질문이 다시 나가 금요일이 빠짐. #54 코드 승인 질문 길이 추가 요청보다 먼저 걸린 것으로 보임, #59).

### 판정: 기각

게이트는 두 번 다 "차이 없음"이고 안전 위반·값 지어내기 0, 지연 p50은 4.45초 → 3.56초로 줄었다. 그러나 목표 달성
99.3%(274/276)가 d5b9ffd 99.6%(275/276)보다 1회 낮아 #51 기준("낮지 않음")을 넘지 못했다. 남은 두 실패 시나리오는
d5b9ffd에서도 각각 6회 중 1회 떨어지던 것이다. 다음은 #56(실행 모델 HCX-007). 코드는 #53·#54가 들어간 4e6c4d6 그대로 둔다.
