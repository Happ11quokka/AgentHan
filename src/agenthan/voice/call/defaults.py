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
# 무응답 안전 상한: 글 없는 소리가 마감을 계속 밀어도 처음 마감 + 이만큼에는 되묻는다.
# `NO_INPUT_S`를 바꿔도 따라 바뀌지 않게 따로 둔다 (감사 7번)
NO_INPUT_SLACK_S = 6.0
# 상한이 지나도 손님이 말하는 중이면 그 말이 끝날 때까지 미룬다(리뷰 P4). 다만 쉼 없는 말 구간이
# 이만큼 넘으면 말이 아니라 끊기지 않는 배경 소리로 보고 되묻는다 (감사 2번). 쉼 400ms 없이
# 8초를 넘는 주문 말은 드물고, 진짜 말이면 그 사이 STT 글이 와서 붙잡기가 먼저 받는다
NO_INPUT_SPEECH_MAX_S = 8.0
