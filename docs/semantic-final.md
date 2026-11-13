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
