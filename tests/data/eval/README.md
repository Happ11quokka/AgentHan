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
| `route.jsonl` | L2 진단 | 기능 분기 (`OwnerAssistant.route()`) | 213 | dev 149 / holdout 64 |
| `flow.jsonl` | L2 진단 | 전체 흐름 (분기 → 질문 → 도구·인자 → 저장소) | 156 (194턴) | dev 120 / holdout 36 |
| `scenario.jsonl` | S1 원본 | 기존 시나리오 30건 (`scripts/build_s1.py`가 S1으로 옮김) | 30 | 전부 dev |

`s1.jsonl`은 손으로 고치지 않는다. 원본(flow·route·scenario·리뷰 골드셋)을 고치고 `scripts/build_s1.py`로 다시 만든다.
flow의 인자는 2026-11-12 도구 스펙(`docs/tools.md`)을 따른다.

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
