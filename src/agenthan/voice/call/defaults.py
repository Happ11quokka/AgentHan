"""턴 관리 기본값 (3단계 리뷰 S11). 세션 설정·서버 설정(`PHONE_*`)·끝말 규칙·평가 도구가 같이 쓴다.

값을 바꾸면 voice README와 `.env.example`의 기본값 표도 같이 고친다.
"""

from __future__ import annotations

# 이어짐 상한(초): 이어질 모양이면 마지막 말 끝부터 이만큼까지 기다린다 (grilling Q3,
# `PHONE_CONT_WAIT_S`)
CONT_WAIT_S = 2.0
# 턴 모델(Smart Turn) 끝남 확률 대역. LOW 이하면 이어짐 쪽, HIGH 이상이면 끝남 쪽, 그 사이는
# 끝말 규칙 (`PHONE_TURN_LOW`·`PHONE_TURN_HIGH`)
MODEL_LOW = 0.3
MODEL_HIGH = 0.7
# 무응답: 봇 재생 끝부터 이만큼 말이 없으면 되묻는다 (`PHONE_NO_INPUT_S`)
NO_INPUT_S = 6.0
# "잠깐만요" 뒤 첫 무응답 대기 (`PHONE_WAIT_REQUEST_S`)
WAIT_REQUEST_S = 15.0
