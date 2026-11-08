"""CLOVA Voice Premium TTS 어댑터 (티켓 03). `call/ports.Tts` 구현.

문장 하나를 `POST /tts-premium/v1/tts`로 보내고, 받은 WAV의 `data` 청크만 돌려준다.
- 응답은 합성이 다 끝난 뒤 `Content-Length` 한 덩어리로 온다(실측). 흘려 받지 않는다
- 문장 나누기(150자), 동시 2개, 문장 순서, 200ms 재분할은 세션(`call/session.py`)이 한다
- 응답을 저장해 다시 쓰지 않는다(이용 조건). 매번 부른다
- 키는 오류 메시지·repr에 넣지 않는다
"""

from __future__ import annotations

import os
import re
import struct
from pathlib import Path
from typing import Any

import httpx

from agenthan.voice.call.ports import PortError
from agenthan.voice.settings import read_env_file

TTS_URL = "https://naveropenapi.apigw.ntruss.com/tts-premium/v1/tts"
SPEAKER = "nara_call"
SAMPLE_RATE = 16000
KEY_ENVS = ("CLOVA_VOICE_CLIENT_ID", "CLOVA_VOICE_CLIENT_SECRET")
# 실측 첫 바이트: 120자 0.72초, 205자 한 문장도 합성됨. 세션 문장은 150자 이하라 넉넉하다
TIMEOUT_S = 10.0


class TtsError(PortError):
    """CLOVA Voice 실패. `code`: tts_auth · tts_429 · tts_vsNN · tts_timeout · tts_bad_audio …"""


class ClovaVoiceTts:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.AsyncClient | None = None,
        speaker: str = SPEAKER,
        timeout: float = TIMEOUT_S,
    ) -> None:
        self._headers = {
            "X-NCP-APIGW-API-KEY-ID": client_id,
            "X-NCP-APIGW-API-KEY": client_secret,
        }
        # 연결을 이어 쓴다(첫 호출만 TLS 비용). 쿠키는 받을 일이 없다
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0))
        self._speaker = speaker

    @classmethod
    def from_env(cls, env_file: Path | None = Path(".env"), **kwargs: Any) -> ClovaVoiceTts:
        """환경 변수 → (없으면) `.env`의 `CLOVA_VOICE_CLIENT_ID`·`CLOVA_VOICE_CLIENT_SECRET`.

        live 테스트용이다. 서버는 `adapters`가 `VoiceSettings.env`에서 키를 꺼내 넘긴다.
        `.env`는 서버와 같은 `settings.read_env_file`로 읽는다 (리뷰 18번).
        """
        file = read_env_file(env_file)
        id_, secret = (os.environ.get(k, "").strip() or file.get(k, "") for k in KEY_ENVS)
        if not (id_ and secret):
            raise TtsError(
                f"CLOVA Voice 키가 없어요 ({', '.join(KEY_ENVS)})", code="tts_config", retry=False
            )
        return cls(id_, secret, **kwargs)

    def __repr__(self) -> str:
        return f"ClovaVoiceTts(speaker={self._speaker!r}, keys=***)"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def synth(self, text: str) -> bytes:
        form = {
            "speaker": self._speaker,
            "text": text,
            "format": "wav",
            "sampling-rate": str(SAMPLE_RATE),
        }
        try:
            response = await self._http.post(TTS_URL, headers=self._headers, data=form)
        except httpx.TimeoutException:
            raise TtsError("TTS 응답이 늦어요", code="tts_timeout") from None
        except httpx.HTTPError as err:
            raise TtsError(f"TTS 연결 오류 {type(err).__name__}", code="tts_network") from None
        if response.status_code != 200:
            raise _http_error(response)
        return wav_pcm(response.content)


# VS 코드 중 다시 부르면 될 수도 있는 것: VS26(TTS 생성 실패), VS99(내부 오류).
# 나머지 VS는 요청 파라미터 문제라 다시 불러도 같다.
_RETRYABLE_VS = frozenset({"VS26", "VS99"})
_VS_CODE = re.compile(rb"\bVS\d{2}\b")


def _http_error(response: httpx.Response) -> TtsError:
    status = response.status_code
    if status in (401, 403):
        return TtsError(
            f"TTS 인증 실패 HTTP {status}. CLOVA Voice 키를 확인하세요"
            " (새로 만든 키·Application 변경은 반영까지 몇 분 걸릴 수 있어요)",
            code="tts_auth",
            retry=False,
        )
    if status == 429:
        return TtsError("TTS 호출 한도 초과 HTTP 429", code="tts_429")
    # 오류 본문 모양은 문서에 없다. VS 코드만 찾아 쓴다.
    if match := _VS_CODE.search(response.content[:2000]):
        vs = match.group().decode()
        return TtsError(
            f"TTS 오류 {vs} HTTP {status}", code=f"tts_{vs.lower()}", retry=vs in _RETRYABLE_VS
        )
    return TtsError(f"TTS HTTP {status}", code=f"tts_http_{status}", retry=status >= 500)


def wav_pcm(data: bytes, *, rate: int = SAMPLE_RATE) -> bytes:
    """RIFF/WAVE에서 `data` 청크 본문만. PCM16 mono `rate`Hz가 아니면 `TtsError`.

    헤더 길이를 가정하지 않고 청크를 따라간다(실측은 늘 44바이트였지만 문서가 약속하지 않는다).
    `data` 크기가 본문보다 크면(흘려 쓰는 WAV의 0xFFFFFFFF 등) 받은 데까지만 쓴다.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise TtsError("TTS 응답이 WAV가 아니에요", code="tts_bad_audio")
    fmt: tuple[int, ...] | None = None
    i = 12
    while i + 8 <= len(data):
        cid = data[i : i + 4]
        (size,) = struct.unpack("<I", data[i + 4 : i + 8])
        body = data[i + 8 : i + 8 + size]
        if cid == b"fmt " and len(body) >= 16:
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            if fmt is None or (fmt[0], fmt[1], fmt[2], fmt[5]) != (1, 1, rate, 16):
                raise TtsError(f"TTS 응답 형식이 달라요 {fmt}", code="tts_bad_audio")
            return body[: len(body) - len(body) % 2]
        i += 8 + size + (size & 1)
    raise TtsError("TTS 응답에 data 청크가 없어요", code="tts_bad_audio")
