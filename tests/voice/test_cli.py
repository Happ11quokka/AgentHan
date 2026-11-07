import io

from agenthan.voice import cli


def test_cli_completes_one_order() -> None:
    stdin = io.StringIO("메도빅 하나요\n그게 다예요\n6시 반이요\n네\n")
    stdout = io.StringIO()
    code = cli.main(["--now", "2026-11-08T15:00:00+09:00"], stdin=stdin, stdout=stdout)
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
