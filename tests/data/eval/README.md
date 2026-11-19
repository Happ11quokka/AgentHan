# 공통 평가 케이스

비서 평가(`src/agenthan/eval`)가 쓰는 케이스를 평가 경로마다 파일 하나에 모은다.
만든 사람·회차는 파일을 나누지 않고 케이스의 `source`로 구분한다.
구성·비율·게이트의 정본은 [`docs/eval-baseline.md`](../../../docs/eval-baseline.md)다.

## 언어

**한국어 케이스만 쓴다.** `lang`은 `ko`(보통 한국어)와 `ko_simple`(외국인 사장님처럼 짧고 서툰 한국어)만
허용한다. 외국어는 비서 코어가 아니라 앞단·뒷단 번역이 맡으므로 이 세트에 넣지 않는다.
`en`·`ru`·`mn` 케이스(route 97건, flow 40건)는 2026-11-12에 지웠고(#17), 그 밖의 `lang`은 불러올 때 오류다
(`agenthan.eval.cases.Case`). 남은 케이스의 dev/holdout은 옮기지 않았다.

| 파일 | 층 | 평가 경로 | 케이스 | 나눔 |
| --- | --- | --- | --- | --- |
| `s1.jsonl` | **L1 회귀 게이트** | 시나리오 (`agenthan.eval.scenario`, 업무 명세로 채점) | 92 | dev 80 / sealed 12 |
| `s2.jsonl` | **S2 목표 지표** (플래너 채택 근거, ADR-0006) | 시나리오 (`--suite s2`, trace·가게 상태 우선) | 48 | dev 40 / sealed 8 |
| `route.jsonl` | L2 진단 | 기능 분기 (`OwnerAssistant.route()`) | 213 | dev 149 / holdout 64 |
| `flow.jsonl` | L2 진단 | 전체 흐름 (분기 → 질문 → 도구·인자 → 저장소) | 156 (194턴) | dev 120 / holdout 36 |
| `scenario.jsonl` | S1 원본 | 기존 시나리오 30건 (`scripts/build_s1.py`가 S1으로 옮김) | 30 | 전부 dev |

`s1.jsonl`은 손으로 고치지 않는다. 원본(flow·route·scenario·리뷰 골드셋)을 고치고 `scripts/build_s1.py`로 다시 만든다.
flow의 인자는 2026-11-12 도구 스펙(`docs/tools.md`)을 따른다.

## S2 (`s2.jsonl`)

S1이 "기능 하나씩 정확히"를 재는 회귀 게이트라면, S2는 계획형 루프가 잘하는 일을 잰다(ADR-0006). 가게는 S1과 같은 더미 시드
(`today` 2026-11-10)에, 필요한 시나리오만 `setup`으로 리뷰를 더한다. 손으로 쓴 파일이고 빌드 스크립트는 없다.

| family | 개수 | 재는 것 |
| --- | --- | --- |
| `multi_step` | 12 | 한 말에 기능 2~3개 ("불만 리뷰 답글 달고 영업시간 늘려 줘") |
| `chain` | 8 | 앞 도구 결과가 다음 인자를 정함 ("리뷰에 많이 나온 메뉴 품절 걸어 줘") |
| `lexical_trap` | 10 | 단어가 다른 기능을 가리킴 ("리뷰 이벤트 메뉴 만들기", "예약 안 받는 날 영업시간") |
| `advice` | 8 | 근거 있는 조언(내 데이터 + 우리 기능으로 실행할 처방). 문장은 기준표 채점기 |
| `out_of_scope` | 5 | 정중한 거절 + 대안, 가게 도구는 부르지 않기 |
| `button_mix` | 5 | 버튼 턴 + 말 섞기. 비서에 `run_action`이 없으면 "미지원"으로 건너뜀(실패 아님) |

S1과 다른 필드(전부 S2 전용):

- `steps`: 정답 단계 `[{"feature", "reach"}]`. 기능 집합이 정답 집합이고, `reach`는 읽기 = `call`, 쓰기 = `preview`.
  범위 밖(`scope: "out"`)은 `steps`가 없고 가게 도구를 부르면 실패다.
- 턴의 `action`: B1 버튼 `{"action_id", "args", "label"}`(`say`는 label로 채워짐). id는 `review.summary`·`review.draft_reply`·
  `stats.week`·`stats.day`·`coupon.create`·`hours.edit`·`task.approve`·`task.cancel`.
- 턴의 `judge`: `{"kind": "advice"|"refusal", "evidence": {…}}`. 이 턴의 답 문장만 채점기가 본다.
- 기대 상태는 S1과 같은 `expect.final/writes/never`. 쓰기 시나리오는 미리보기 뒤 "네"(`again`으로 되풀이)로 승인한다.
- sealed 8개(family마다 1~2개)는 판정 때만 돌린다. `setup`의 `reviews[+]`는 리스트 값이면 항목을 모두 더한다.

## source

| source | 내용 | route | flow |
| --- | --- | --- | --- |
| `m0` | 처음 손으로 쓴 세트 | 64 | 61 |
| `aug:…` | 1차 증강 (`scripts/augment_cases.py`, Gemini 생성 + Sonnet 교차 검사) | 55 | 59 |
| `aug2:…` | 2차 증강 (`scripts/augment_cases_v2.py`, 값 지어내기·가게 단어 섞인 무관 질문·여러 턴 승인) | 22 | 73 |
| `review:근형` | 리뷰 기능 분기 (근형) | 169 | |

## dev / holdout (route·flow) · dev / sealed (S1)

- 케이스마다 `split`이 들어 있다. 묶음마다 `cases.make_splits`(기능·종류별 7:3, ID 해시 순서)로 나눠서
  새 묶음을 붙여도 앞선 배정은 움직이지 않는다.
- 이미 실패 내용을 본 holdout 케이스는 dev로 옮기고 `origin: holdout-seen-<날짜>`를 남긴다.
- `review:근형`은 기존 116건이 dev, 나중에 새로 쓴 53건(`origin: 근형 holdout1`)이 holdout이다.
- S1에서는 원래 holdout이던 케이스가 sealed다. 실패를 본 sealed는 dev로 옮기고 `origin: sealed-seen-<날짜>`를 남긴다
  (`scripts/build_s1.py`의 `SEEN`). 튜닝 중에는 `--split dev`만 돌린다.

## 여기 없는 것

- 리뷰 에이전트 안 도구 선택 골드셋은 형식과 채점기가 달라 `tests/goldset/review_tools.jsonl`에 둔다
  (`agenthan.agent.review.bench`).
