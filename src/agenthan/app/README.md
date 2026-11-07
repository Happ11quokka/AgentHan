# app · 통합 진입점 (예정)

FastAPI 앱 하나에 파트별 라우터를 붙인다: `/assistant`(Part A), `/phone`(Part C, WebSocket). 여기서 진짜 구현과 가짜 구현을 골라 끼운다(의존성 주입). 합칠 때 이 폴더만 고치면 되게 한다.
