# illustration · 메뉴 대표 그림

- 담당: 임동현 (Part A 메뉴 탭에 붙는다)
- 하는 일: 사진이 없는 메뉴 자리에 보여 줄 **대표 그림**(미리 만든 SVG, 약 110장)에서 메뉴 이름에 맞는 그림을 고른다. 실행 중에 그림을 새로 그리지 않고, 고를 때 HCX도 부르지 않는다(CLOVA 임베딩만).
- 스펙: 메뉴 대표 그림(2026-11-21). 화면 우선순위는 `web/README.md`, 서버 쪽 시점은 `src/agenthan/app/README.md`.

## 구현

| 파일 | 하는 일 |
|---|---|
| `art/` | 그림 생성기(빌드 때만). 가족(`defaults`·`cakes`·`bakery`·`drinks`·`dishes`)마다 `ILLUSTRATIONS` 목록 하나. 색은 `art/style.py` `PALETTE` 이름만 |
| `catalog/<가족>.json` | 생성기가 쓰는 카탈로그(그림 id·이름·별칭·키워드·분류·레시피). 손으로 고치지 않는다 |
| `web/public/illustrations/<id>.svg` | 생성기가 쓰는 그림 파일. 화면 주소 `/illustrations/<id>.svg` |
| `library.py` | 카탈로그 읽기, 화면 주소, git 밖 실제 가게 별칭(`private_aliases`) |
| `matcher.py` | 메뉴 이름(+분류) → 그림 id(`pick_illustration`). 늘 카탈로그 id, 예외 없음 |
| `embedding_index.py`·`catalog_embeddings.json` | 그림 이름·별칭의 미리 계산한 CLOVA 임베딩(커밋함) |

그림 고르는 순서(`matcher.py`):

1. 정확한 이름: 정규화한 이름(공백·기호 제거)이 실제 가게 별칭이나 카탈로그 이름·별칭과 같으면 그 그림.
2. 키워드: 이름에 든 가장 긴 키워드의 그림. 분류 단서(분류 필드나 이름 끝말)가 있으면 같은 무리(디저트·식사·커피·차·에이드)의 키워드만 본다. "얼그레이 케이크"는 얼그레이 차, "티라미수 라떼"는 티라미수 케이크로 가지 않는다.
3. CLOVA 임베딩: 코사인 0.68 이상인 가장 가까운 그림. 같은 무리 안에서만, 분류 기본 그림(`default_*`)은 후보가 아니다.
4. 분류 기본 그림 8개(케이크·빵·요리·수프·커피·차·에이드·기타).

언제 고르나(`app/server.py`, `tools/dummy_store.py`):

- 메뉴가 들어오거나 이름이 바뀌면 저장소가 잠금 안에서 바로 고른다. 이때는 임베딩(네트워크)을 부르지 않아서 1·2·4단계만 쓴다.
- 임베딩은 잠금 밖 `server.fill_illustrations`만 부른다. 세션을 열 때, 그리고 메뉴가 바뀐 뒤(대화 턴, 메뉴판 채우기 끝, 메뉴 탭 저장, 온보딩) 백그라운드로 돈다. 분류 기본 그림이고 그 이름으로 아직 임베딩해 보지 않은 메뉴만 본다(`store.json`의 `illustration_tried`). 응답은 기다리지 않는다.
- 카탈로그·인덱스·실제 가게 별칭이 바뀌면(`catalog_version`, `store.json`의 `illustration_catalog`) 처음 세션을 열 때 기본 그림 메뉴를 다시 고른다.

## 실행

```bash
uv run python -m agenthan.illustration.art                           # SVG·카탈로그·갤러리 다시 만들기
uv run --env-file .env python -m agenthan.illustration.build_index   # 그림 임베딩 인덱스 갱신(바뀐 글만 임베딩)
uv run pytest -q tests/test_illustration_*.py tests/test_menu_illustration_api.py
```

- 그림을 더하거나 고치면 `art/<가족>.py`의 레시피만 고치고 생성기를 다시 돌린다. 이름·별칭을 고쳤으면 인덱스도 다시 만든다(병합 뒤에도). 테스트가 낡은 생성물·인덱스를 잡는다.
- 갤러리: `build/illustrations/gallery.html`(git 밖). 그림 전체와 "데모 가게 메뉴 → 고른 그림" 표. 노란 줄은 분류 기본 그림으로 떨어진 메뉴다.

## 실제 가게 별칭 (git 밖)

실제 가게 메뉴 이름은 커밋하지 않는다(PRD §0). 커밋된 카탈로그에는 "카라멜 머랭 홀케이크" 같은 묘사형 이름만 두고, 실제 가게 이름 → 그림 짝은 git 밖 파일에 둔다.

- 경로: 저장소 루트 `data/illustration_aliases.json`(`data/`는 gitignore). 환경 변수 `AGENTHAN_ILLUSTRATION_ALIASES`로 다른 경로를 줄 수 있다.
- 모양: JSON 객체 하나, `{"메뉴 이름": "그림 id", ...}`. 카탈로그에 없는 id는 무시한다. 파일이 없거나 깨졌으면 조용히 건너뛴다(실제 가게 이름은 키워드·임베딩·기본 그림으로 간다).
- 1단계(정확한 이름)에서 카탈로그보다 먼저 본다. 파일을 고치면 다음 고르기부터 읽고, 지문이 바뀌어 기본 그림 메뉴도 다시 고른다.
- `tests/test_illustration_coverage.py`는 `data/clean/menus.json`이 있으면 이 파일까지 써서 실제 가게 메뉴 31개가 모두 자기 그림을 받는지 본다(없으면 건너뜀).
- 서버: `deploy/phone/compose.yaml`이 서버 저장소의 `data/illustration_aliases.json`을 컨테이너 `/app/data/`에 읽기 전용으로 붙인다. 배포 전에 한 번 복사해 둔다(`deploy/phone/README.md` 4-1).
