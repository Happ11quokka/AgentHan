import io

from agenthan.voice import cli


def test_cli_completes_one_order() -> None:
    stdin = io.StringIO("메도빅 하나요\n그게 다예요\n6시 반이요\n네\n")
    stdout = io.StringIO()
    code = cli.main(["--now", "2026-11-08T16:00:00+09:00"], stdin=stdin, stdout=stdout)
    out = stdout.getvalue()
    assert code == 0
    assert "AI: 네, 샘플 러시안 베이커리입니다. 주문 도와드릴게요." in out
    assert "총 7,500원" in out
    assert "status='confirmed'" in out
    assert "pickup_time='2026-11-08T18:30:00+09:00'" in out


def test_cli_exits_cleanly_on_eof() -> None:
    stdout = io.StringIO()
    code = cli.main(["--now", "2026-11-08T15:00:00+09:00"], stdin=io.StringIO("메도빅 하나요\n"),
                    stdout=stdout)
    assert code == 1
    assert "주문이 끝나지 않았어요" in stdout.getvalue()


def test_cli_hcx_without_key_exits_2(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # .env 없음
    code = cli.main(["--nlu", "hcx"], stdin=io.StringIO(""), stdout=io.StringIO())
    assert code == 2
    assert "CLOVASTUDIO_API_KEY" in capsys.readouterr().err


def test_cli_hcx_uses_hcx_nlu(monkeypatch) -> None:
    from agenthan.voice.nlu.base import Act
    from agenthan.voice.nlu.hcx import HcxNlu

    class Canned:
        def __init__(self) -> None:
            self.acts = iter([[Act("add_item", menu_text="메도빅", qty=1)], [Act("done_ordering")],
                              [Act("set_pickup_time", time_text="6시 반")]])

        def parse(self, text, ctx):
            return next(self.acts)

    monkeypatch.setattr(HcxNlu, "from_env", classmethod(lambda cls: Canned()))
    stdin = io.StringIO("메도빅 하나요\n그게 다예요\n6시 반이요\n네\n")
    stdout = io.StringIO()
    code = cli.main(["--nlu", "hcx", "--now", "2026-11-08T16:00:00+09:00"], stdin=stdin,
                    stdout=stdout)
    assert code == 0
    assert "status='confirmed'" in stdout.getvalue()
