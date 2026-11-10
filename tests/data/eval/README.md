# 공통 평가 케이스

비서 평가(`src/agenthan/eval`)가 쓰는 케이스를 평가 경로마다 파일 하나에 모은다.
만든 사람·회차는 파일을 나누지 않고 케이스의 `source`로 구분한다.

| 파일 | 평가 경로 | 케이스 | dev / holdout |
| --- | --- | --- | --- |
| `route.jsonl` | 기능 분기 (`OwnerAssistant.route()`) | 310 | 217 / 93 |
| `flow.jsonl` | 전체 흐름 (분기 → 질문 → 도구·인자 → 저장소) | 193 (237턴) | 152 / 41 |

## source

| source | 내용 | route | flow |
| --- | --- | --- | --- |
| `m0` | 처음 손으로 쓴 세트 | 64 | 61 |
| `aug:…` | 1차 증강 (`scripts/augment_cases.py`, Gemini 생성 + Sonnet 교차 검사) | 55 | 59 |
| `aug2:…` | 2차 증강 (`scripts/augment_cases_v2.py`, 값 지어내기·가게 단어 섞인 무관 질문·여러 턴 승인) | 22 | 73 |
| `review:근형` | 리뷰 기능 분기 (근형) | 169 | |

## dev / holdout

- 케이스마다 `split`이 들어 있다. 묶음마다 `cases.make_splits`(기능·종류별 7:3, ID 해시 순서)로 나눠서
  새 묶음을 붙여도 앞선 배정은 움직이지 않는다.
- 이미 실패 내용을 본 holdout 케이스는 dev로 옮기고 `origin: holdout-seen-<날짜>`를 남긴다.
- `review:근형`은 기존 116건이 dev, 나중에 새로 쓴 53건(`origin: 근형 holdout1`)이 holdout이다.

## 여기 없는 것

- 리뷰 에이전트 안 도구 선택 골드셋은 형식과 채점기가 달라 `tests/goldset/review_tools.jsonl`에 둔다
  (`agenthan.agent.review.bench`).
