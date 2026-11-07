# AgentHan

## 로컬 검증

Python 3.11과 `uv`를 사용한다. 먼저 `uv sync --frozen`으로 개발 의존성을 준비한다.

```bash
uv run --frozen --no-sync agenthan selftest
uv run --frozen --no-sync pytest -q
uv run --frozen --no-sync ruff check .
uv run --frozen --no-sync ruff format --check .
```

selftest는 모의 API를 사용하므로 실제 외부 API 호출이나 자격 증명이 필요하지 않다.

## 하네스 실행

Code geass의 저장소 검증 설정은 [`.harness/verify.toml`](.harness/verify.toml)이다.
위 네 검사를 모두 필수로 실행하며, 검증 중 의존성을 설치하거나 잠금 파일을 갱신하지 않는다.
하네스는 시작 커밋의 설정을 읽으므로 설정을 커밋하고 작업 트리가 깨끗한 상태에서 실행한다.
실행 카드는 이 저장소의 열린 GitHub Issue에 `harness` 라벨을 붙여 선택한다.
