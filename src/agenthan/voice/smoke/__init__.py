"""배포 직후 스모크 평가 (6단계 품질). `python -m agenthan.voice.smoke`.

- S1 (`scripts.py`): 가게 데이터로 만든 대본을 실제 NLU로 엔진에 넣고 최종 Order를 본다
- S2 (`calls.py`): 가짜 휴대폰으로 서버 WS에 통화를 걸어 주문 확정까지 가는지, 말 끝 → 첫 소리
  지연이 얼마인지 본다

사용법과 호출 수는 `__main__.py` 머리 주석과 `deploy/phone/README.md`.
"""
