# fixtures — 레포에 올리는 작은 샘플

- `sample_store.json`: **더미** 가게(메뉴 4개, 리뷰 3개). `agenthan.core.fakes.FakeStoreKnowledge`가 읽는다.
- 실제 가게 데이터와 인덱스는 `data/`에만 둔다. gitignore와 pre-commit 훅이 커밋을 막는다. 공유는 **카톡으로만**.
- **레포·발표 자료·스크린샷·데모 영상에는 이 폴더의 더미 데이터만 쓴다.**
- 형식(필드 이름)은 `src/agenthan/core/contracts.py`의 `MenuItem`, `Review`를 따른다. 더미를 늘릴 때도 실제 가게 문장을 옮기지 말고 새로 지어서 쓴다.
- `review_snapshot.jsonl`, `review_menus.json`: 리뷰 도구(`src/agenthan/agent/review`)용 **더미** 가게 3곳(달빛모찌·황금반점·별빛돈가스) 리뷰 107건과 메뉴·별칭. 문장은 전부 지어낸 것이고 `scripts/make_fixture.py`로 다시 만든다.
