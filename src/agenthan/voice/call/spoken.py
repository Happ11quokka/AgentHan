"""봇이 말한 턴 하나: 문장, 보낸 소리 길이, 손님 귀에 들린 때 (3단계 티켓 05·06, 리뷰 P1).

시각은 모두 오디오 시각(올라온 오디오의 ms, `SilenceDetector.offset_ms`)이다. 브라우저 오디오와
`playback.started`가 같은 소켓으로 순서대로 오므로, 보고를 받은 순간의 오디오 시각이 손님 귀에
그 문장이 들린 시각이다(`s3-verify-barge-in` 4-4 ③). 말 끊기의 들은 만큼 기록, 음성 끼어들기,
최종 복창의 금액 문장 끝(그 전에 한 "네"는 확정이 아니다)이 이것을 쓴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SpokenTurn:
    turn: int
    texts: list[str]
    readback: bool = False  # 최종 복창 턴 (엔진 `TurnResult.readback`)
    from_ms: int = 0  # 첫 소리를 보낸 때
    ms: list[int] = field(default_factory=list)  # 보낸 문장마다 소리 길이(ms). 보낸 것만
    marks: dict[int, int] = field(default_factory=dict)  # 문장 → `playback.started`를 받은 때

    def heard(self, seq: int, played_ms: float) -> list[dict[str, Any]]:
        """문장마다 들음(heard)·일부(partial)·안 들음(unheard).

        브라우저가 `seq` 문장의 `played_ms`까지 틀고 멈췄다. 그 앞은 들음, 뒤와 보내기 전에 끊긴
        문장은 안 들음이다. CLOVA Voice는 단어 시각을 주지 않아 일부 들은 문장은 글자 비율로
        들은 글자 수(`chars`)를 어림한다(`s3-barge-in` Q2-2 4번)
        """
        out: list[dict[str, Any]] = []
        for i, text in enumerate(self.texts):
            ms = self.ms[i] if i < len(self.ms) else None
            if ms is None or i > seq:
                status, got = "unheard", 0
            elif i < seq:
                status, got = "heard", ms
            else:
                got = max(0, min(round(played_ms), ms))
                status = "unheard" if got == 0 else ("heard" if got >= ms else "partial")
            entry: dict[str, Any] = {"seq": i, "heard": status, "ms": ms, "played_ms": got}
            if status == "partial" and ms:
                entry["chars"] = round(len(text) * got / ms)
            out.append(entry)
        return out

    def starts(self) -> list[int]:
        """보낸 문장마다 손님 귀에 들리기 시작한 때. 보고가 없으면 앞 문장 끝으로 어림한다."""
        out: list[int] = []
        for s, _ in enumerate(self.ms):
            prev = self.from_ms if s == 0 else out[-1] + self.ms[s - 1]
            out.append(self.marks.get(s, prev))
        return out

    def remaining_ms(self, at_ms: int) -> float:
        """남은 봇 말 길이. 아직 안 보낸(TTS 중인) 문장이 있으면 모른다(무한대)."""
        if len(self.ms) < len(self.texts) or not self.ms:
            return math.inf
        return self.starts()[-1] + self.ms[-1] - at_ms

    def amount_end_ms(self) -> float:
        """최종 복창에서 금액(과 요청) 문장이 끝난 때 = 마지막 문장("맞으세요?")이 시작한 때.

        마지막 문장을 아직 안 보냈으면 무한대(아직 금액 문장 중이다).
        """
        last = len(self.texts) - 1
        starts = self.starts()
        return starts[last] if last < len(starts) else math.inf

    def position(self, at_ms: int) -> tuple[int, int]:
        """그때 듣던 문장과 그 문장에서 들은 ms (서버 어림. 브라우저 보고가 오면 그것을 쓴다)."""
        spot = (0, 0)
        for s, start in enumerate(self.starts()):
            if start <= at_ms:
                spot = (s, at_ms - start)
        return spot
