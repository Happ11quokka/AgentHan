"""CLOVA Speech STT 어댑터 ↔ 로컬 가짜 `NestService` (spec STT 어댑터 테스트, 티켓 02).

오프라인. 실제 CLOVA는 `test_stt_clova_live.py`(opt-in).
"""

from __future__ import annotations

import asyncio

import grpc
import pytest
from call_harness import Call
from fake_nest import KEY, FakeNest, NestServer, transcription

from agenthan.voice.call.ports import PortError, SttResult
from agenthan.voice.stt.clova import ClovaSttSettings, ClovaSttStream

FRAME = bytes(3200)  # 100ms 무음
WAIT = 3.0


def run(coro):
    return asyncio.run(coro)


def settings(endpoint: str, **kw) -> ClovaSttSettings:
    return ClovaSttSettings(secret_key=KEY, endpoint=endpoint, secure=False, **kw)


async def next_result(stt: ClovaSttStream) -> SttResult:
    return await asyncio.wait_for(anext(aiter(stt.results())), WAIT)


def test_config_goes_first_with_auth_then_data_and_ep_flag_result_comes_back() -> None:
    async def go():
        async with NestServer(FakeNest(["메도빅 하나요 "])) as srv:
            stt = ClovaSttStream(settings(srv.endpoint))
            await stt.start()
            for _ in range(4):
                await stt.write(FRAME)
            await stt.write(FRAME, ep_flag=True, seq_id=1)
            result = await next_result(stt)
            await stt.close()
            return srv.servicer.connections, result

    conns, result = run(go())
    assert len(conns) == 1
    conn = conns[0]
    assert conn.metadata["authorization"] == f"Bearer {KEY}"
    assert conn.requests[0].type == 0  # CONFIG 먼저
    assert conn.configs == [{
        "transcription": {"language": "ko"},
        "semanticEpd": {"skipEmptyText": True, "useWordEpd": True, "usePeriodEpd": True,
                        "gapThreshold": 700, "durationThreshold": 10000},
    }]
    assert [len(d.data.chunk) for d in conn.data] == [3200] * 5
    assert conn.extras == [{"epFlag": False, "seqId": 0}] * 4 + [{"epFlag": True, "seqId": 1}]
    assert result == SttResult("메도빅 하나요 ", 0, 500, "endPoint", True, 1, 0.9)


def test_wrong_key_fails_start_with_auth_error_that_does_not_carry_the_key() -> None:
    async def go():
        async with NestServer(FakeNest()) as srv:
            stt = ClovaSttStream(ClovaSttSettings(secret_key="wrong-key-123",
                                                  endpoint=srv.endpoint, secure=False))
            with pytest.raises(PortError) as info:
                await stt.start()
            await stt.close()
            return info.value

    err = run(go())
    assert (err.code, err.retry) == ("stt_auth", False)
    assert "wrong-key-123" not in str(err)


def test_config_failure_status_fails_start() -> None:
    async def go():
        async with NestServer(FakeNest(config_status="Unknown key: hobidden")) as srv:
            stt = ClovaSttStream(settings(srv.endpoint))
            with pytest.raises(PortError) as info:
                await stt.start()
            await stt.close()
            return srv.servicer.connections[0], info.value

    conn, err = run(go())
    assert (err.code, err.retry) == ("stt_config", False)
    assert "hobidden" in str(err)
    assert conn.data == []  # 설정이 안 됐으면 DATA를 보내지 않는다


def test_results_are_parsed_in_order_ignoring_other_responses_and_unknown_fields() -> None:
    async def go():
        async with NestServer(FakeNest()) as srv:  # replies 없음 → epFlag 응답은 빈 글
            stt = ClovaSttStream(settings(srv.endpoint))
            await stt.start()
            conn = await srv.servicer.wait_connections(1)
            conn.push({"responseType": ["semanticEpd"], "semanticEpd": {"status": "ok"}})
            gap = transcription("아메리카노 두잔이랑 ", 0, 1600, epd="gap", confidence=0.75)
            gap["transcription"]["contextCode"] = {"x": 1}  # 문서에 없는 필드
            conn.push(gap)
            conn.push(transcription("메도빅2개", 1610, 2000, epd="durationThreshold"))
            await stt.write(FRAME, ep_flag=True, seq_id=3)
            got = [await next_result(stt) for _ in range(3)]
            await stt.close()
            return got

    assert run(go()) == [
        SttResult("아메리카노 두잔이랑 ", 0, 1600, "gap", False, 0, 0.75),
        SttResult("메도빅2개", 1610, 2000, "durationThreshold", False, 0, 0.9),
        SttResult("", 0, 100, "endPoint", True, 3, 0.9),  # epFlag 응답은 빈 글이어도 넘긴다
    ]


@pytest.mark.parametrize(
    ("status", "code", "retry"),
    [
        (grpc.StatusCode.UNAVAILABLE, "stt_disconnected", True),
        (grpc.StatusCode.INTERNAL, "stt_disconnected", True),
        (grpc.StatusCode.RESOURCE_EXHAUSTED, "stt_busy", True),
        (grpc.StatusCode.UNAUTHENTICATED, "stt_auth", False),
    ],
)
def test_broken_stream_raises_port_error_from_results(status, code, retry) -> None:
    async def go():
        async with NestServer(FakeNest()) as srv:
            stt = ClovaSttStream(settings(srv.endpoint))
            await stt.start()
            conn = await srv.servicer.wait_connections(1)
            conn.abort(status, "Lifespan expired")
            with pytest.raises(PortError) as info:
                await next_result(stt)
            await stt.close()
            return info.value

    err = run(go())
    assert (err.code, err.retry) == (code, retry)
    assert KEY not in str(err)


def test_server_closing_the_stream_unasked_is_a_disconnect() -> None:
    async def go():
        async with NestServer(FakeNest()) as srv:
            stt = ClovaSttStream(settings(srv.endpoint))
            await stt.start()
            conn = await srv.servicer.wait_connections(1)
            conn.out.put_nowait(("eof", None))
            with pytest.raises(PortError) as info:
                await next_result(stt)
            await stt.close()
            return info.value

    assert run(go()).code == "stt_disconnected"


def test_keyword_boosting_goes_into_config_and_its_status_is_recorded() -> None:
    async def go():
        async with NestServer(FakeNest()) as srv:
            stt = ClovaSttStream(settings(srv.endpoint, boost_words=("메도빅", "나폴레옹")))
            await stt.start()
            await stt.close()
            return srv.servicer.connections[0].configs, stt.boost_status

    (config,), status = run(go())
    assert config["keywordBoosting"] == {
        "boostings": [{"words": "메도빅,나폴레옹", "weight": 3.0}]
    }
    assert "useDomainBoostings" not in config  # 요청별 단어와 섞으면 요청이 실패한다
    assert config["semanticEpd"]["gapThreshold"] == 700
    assert status == "Success"


def test_a_failed_boosting_status_is_recorded_and_the_call_goes_on() -> None:
    async def go():
        async with NestServer(FakeNest(["메도빅 하나요 "], boost_status="Failure")) as srv:
            stt = ClovaSttStream(settings(srv.endpoint, boost_words=("메도빅",)))
            await stt.start()
            await stt.write(FRAME, ep_flag=True, seq_id=1)
            result = await next_result(stt)
            await stt.close()
            return stt.boost_status, result

    status, result = run(go())
    assert status == "Failure"
    assert result.text == "메도빅 하나요 "


def test_without_boost_words_there_is_no_boosting_in_config() -> None:
    async def go():
        async with NestServer(FakeNest()) as srv:
            stt = ClovaSttStream(settings(srv.endpoint))
            await stt.start()
            await stt.close()
            return srv.servicer.connections[0].configs, stt.boost_status

    (config,), status = run(go())
    assert "keywordBoosting" not in config
    assert status is None


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_after_4m30s_it_reconnects_right_after_an_ep_flag_and_keeps_one_timeline() -> None:
    clock = Clock()

    async def go():
        async with NestServer(FakeNest(["메도빅 하나요 ", "포장이요 "])) as srv:
            stt = ClovaSttStream(settings(srv.endpoint), monotonic=clock)
            await stt.start()
            for _ in range(3):
                await stt.write(FRAME)
            clock.t = 271.0
            await stt.write(FRAME)  # 시간은 넘었지만 말 중간일 수 있어 그대로
            await stt.write(FRAME, ep_flag=True, seq_id=1)  # 턴 사이 → 이 뒤에 바꾼다
            first = await next_result(stt)
            await stt.write(FRAME)
            await stt.write(FRAME)
            await stt.write(FRAME, ep_flag=True, seq_id=2)
            second = await next_result(stt)
            await stt.close()
            return srv.servicer.connections, first, second

    conns, first, second = run(go())
    assert len(conns) == 2
    old, new = conns
    assert len(old.data) == 5 and old.writing_done  # 앞 연결은 epFlag 프레임까지만, 그 뒤 닫음
    assert new.metadata["authorization"] == f"Bearer {KEY}"
    assert new.requests[0].type == 0 and len(new.configs) == 1  # 새 연결도 CONFIG 먼저
    assert len(new.data) == 3
    assert first == SttResult("메도빅 하나요 ", 0, 500, "endPoint", True, 1, 0.9)
    # 새 연결의 CLOVA 시각 0-300 → 통화 시각 500-800
    assert second == SttResult("포장이요 ", 500, 800, "endPoint", True, 2, 0.9)


def test_without_an_ep_flag_it_still_reconnects_before_the_5_minute_limit() -> None:
    clock = Clock()

    async def go():
        async with NestServer(FakeNest(["네 "])) as srv:
            stt = ClovaSttStream(settings(srv.endpoint), monotonic=clock)
            await stt.start()
            await stt.write(FRAME)
            await stt.write(FRAME)
            clock.t = 286.0
            await stt.write(FRAME)  # 이 프레임부터 새 연결
            await stt.write(FRAME, ep_flag=True, seq_id=4)
            result = await next_result(stt)
            await stt.close()
            return srv.servicer.connections, result

    conns, result = run(go())
    assert [len(c.data) for c in conns] == [2, 2]
    assert conns[0].writing_done
    assert result == SttResult("네 ", 200, 400, "endPoint", True, 4, 0.9)


def test_call_session_runs_a_turn_through_the_adapter(tmp_path) -> None:
    async def go():
        async with NestServer(FakeNest(["메도빅 하나요 "])) as srv:
            async with Call(tmp_path, stt=ClovaSttStream(settings(srv.endpoint))) as c:
                await c.start()
                await c.say()  # 말소리 → 무음 감지가 epFlag → 가짜 CLOVA가 글을 낸다
                return await c.out.wait_for("stt.final", turn=1)

    final = run(go())
    assert (final["text"], final["end_by"]) == ("메도빅 하나요", "vad")


def test_call_session_reports_auth_failure_as_error_event(tmp_path) -> None:
    async def go():
        async with NestServer(FakeNest(key="other-key")) as srv:
            async with Call(tmp_path, stt=ClovaSttStream(settings(srv.endpoint))) as c:
                await c.session.feed_event({"type": "call.start", "turn": 0})
                return await c.out.wait_for("error")

    error = run(go())
    assert (error["code"], error["retry"]) == ("stt_auth", False)
    assert KEY not in error["message"]


def test_settings_from_env_mapping_hide_the_key_and_fix_the_endpoint() -> None:
    s = ClovaSttSettings.from_env({
        "CLOVA_SPEECH_SECRET_KEY": "abc-secret",
        "CLOVA_SPEECH_GRPC_ENDPOINT": "https://clovaspeech-gw.ncloud.com",
    })
    assert s.endpoint == "clovaspeech-gw.ncloud.com:50051"
    assert s.secure
    assert "abc-secret" not in repr(s)
    assert ClovaSttSettings.from_env({"CLOVA_SPEECH_SECRET_KEY": "k"}).endpoint == (
        "clovaspeech-gw.ncloud.com:50051"
    )
    with pytest.raises(PortError) as info:
        ClovaSttSettings.from_env({})
    assert (info.value.code, info.value.retry) == ("stt_config", False)


def test_close_ends_results_and_can_be_called_twice() -> None:
    async def go():
        async with NestServer(FakeNest()) as srv:
            stt = ClovaSttStream(settings(srv.endpoint))
            await stt.start()
            await stt.write(FRAME)

            async def drain() -> list[SttResult]:
                return [r async for r in stt.results()]

            reading = asyncio.create_task(drain())
            await asyncio.sleep(0.05)
            await stt.close()
            await stt.close()
            await stt.write(FRAME)  # 닫힌 뒤 쓰기는 조용히 버린다
            return await asyncio.wait_for(reading, WAIT)

    assert run(go()) == []


class AbortFirstAfterEp(FakeNest):
    """첫 연결이 epFlag 결과를 낸 뒤 UNAVAILABLE로 끊긴다 (재연결 뒤 앞 연결의 끊김)."""

    def _on_request(self, conn, req) -> None:
        super()._on_request(conn, req)
        if conn is self.connections[0] and conn.extras and conn.extras[-1].get("epFlag"):
            conn.abort(grpc.StatusCode.UNAVAILABLE, "server going away")


def test_an_error_on_the_retired_connection_after_reconnect_does_not_end_the_call() -> None:
    # 붙잡기에 시간 제한이 생겨(리뷰 2번) 앞 연결의 오류는 로그만 남긴다 (리뷰 12번)
    clock = Clock()

    async def go():
        async with NestServer(AbortFirstAfterEp(["메도빅 하나요 ", "포장이요 "])) as srv:
            stt = ClovaSttStream(settings(srv.endpoint), monotonic=clock)
            await stt.start()
            await stt.write(FRAME)
            clock.t = 271.0
            await stt.write(FRAME, ep_flag=True, seq_id=1)  # 이 뒤 새 연결. 앞 연결은 끊긴다
            first = await next_result(stt)
            await asyncio.sleep(0.2)  # 앞 연결의 UNAVAILABLE이 도착할 틈
            await stt.write(FRAME, ep_flag=True, seq_id=2)
            second = await next_result(stt)
            await stt.close()
            return first, second

    first, second = run(go())
    assert (first.text, second.text) == ("메도빅 하나요 ", "포장이요 ")
