"""3단계 티켓 02: 엔진이 그 턴에 쓸 `NluContext`를 밖에 알려 준다 (미리 해석의 키)."""

from datetime import datetime

from agenthan.core.fakes import FakeStoreKnowledge
from agenthan.voice.dialog.engine import DialogEngine
from agenthan.voice.nlu.fake import FakeNlu
from agenthan.voice.store_config import KST, StoreConfig

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=KST)


class RecordingNlu:
    def __init__(self) -> None:
        self.inner = FakeNlu()
        self.contexts = []

    def parse(self, text, ctx):
        self.contexts.append(ctx)
        return self.inner.parse(text, ctx)


def make_engine(nlu: RecordingNlu) -> DialogEngine:
    engine = DialogEngine(
        FakeStoreKnowledge.from_fixture(), nlu, StoreConfig.from_fixture(), order_id="o-test"
    )
    engine.start(NOW)
    return engine


def test_nlu_context_is_the_context_the_next_handle_passes_to_nlu() -> None:
    nlu = RecordingNlu()
    engine = make_engine(nlu)
    for text in ("메도빅 하나요", "아메리카노 두 잔이요"):
        before = engine.nlu_context()
        engine.handle(text, NOW)
        assert nlu.contexts[-1] == before


def test_nlu_context_follows_the_dialog() -> None:
    engine = make_engine(RecordingNlu())
    first = engine.nlu_context()
    engine.handle("메도빅 하나요", NOW)
    second = engine.nlu_context()
    assert first != second
    assert second.state == "ordering"
    assert second.last_question.endswith("더 필요하신 거 있으세요?")
    assert "메도빅" in second.menu_names
