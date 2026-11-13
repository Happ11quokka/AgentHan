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
