# core · 공통 (세 파트가 함께 쓴다)

- `contracts.py`: 파트 사이 약속(타입·Protocol). **고칠 때는 카톡에 한 줄 공지.**
- `fakes.py`: 계약의 가짜 구현. 진짜 구현 전까지 각 파트가 이걸로 개발한다.
- 앞으로 들어올 것: 설정(.env 읽기).
- `llm.py`: CLOVA Studio HCX 공용 클라이언트 `make_client()`와 모델별 요청 옵션 `request_options()`, `tool_call_options()`. 실측 제약은 파일 맨 위.
- `embedding.py`: CLOVA 임베딩 v2(bge-m3) 호출. 429·5xx 두 번 재시도.
