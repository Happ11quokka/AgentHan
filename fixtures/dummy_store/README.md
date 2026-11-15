# dummy_store — 더미 러시안케이크 가게

`data/clean/`과 같은 형식의 **더미** 데이터. 레포·발표·데모에는 이 폴더만 쓴다.

| 파일 | 내용 |
|---|---|
| `menus.json` | 메뉴 32개 (`MenuItem` + `extra`: 묶음·태그·인기·선물·단맛 근거) |
| `reviews.json` | 리뷰 80개 (`Review` + `extra`) |
| `store.json`, `families.json`, `aliases.json`, `kiwi_user_dict.txt` | 가게 정보, 메뉴 묶음, 별칭 사전, Kiwi 사용자 사전 |
| `goldset.jsonl` | 검색 골드셋 60문항 (검색 규칙을 만들면서 본 질문) |
| `holdout.jsonl` | 1차 홀드아웃 14문항 (이 결과를 보고 규칙을 고쳐서 더는 독립 평가가 아님) |
| `holdout2.jsonl` | 최종 홀드아웃 15문항 (규칙을 고치지 않고 한 번만 채점) |

채점: `uv run python scripts/eval_rag.py --files goldset.jsonl holdout.jsonl holdout2.jsonl`
(실제 데이터는 `--data data/clean`, 데이터는 레포에 없음)

## 최종 정확도 (holdout2, 2026-11-16)

코드: `rag/store-knowledge` 커밋 `03c106c`, 벡터 검색 끔(키워드·규칙만). 15문항, 한 번만 채점.

| 데이터 | Recall@1 | Recall@3 | MRR |
|---|---|---|---|
| 더미 (이 폴더) | 0.800 | 0.756 | 0.833 |
| 실제 (`data/clean`, 비공개) | 0.800 | 0.767 | 0.817 |

참고(같은 코드): goldset 더미 1.000 / 실제 1.000, holdout 더미 R@1 1.000 / 실제 0.929.
goldset·holdout은 규칙을 만들 때 본 질문이라 점수가 높게 나온다. 최종 정확도는 holdout2로 본다.

채점 규칙: 정답이 빈 목록이면 결과도 비어야 1점. Recall@k = 상위 k개 중 정답 수 / min(k, 정답 수).
MRR = 1순위 정답(없으면 첫 정답)의 1/순위.
