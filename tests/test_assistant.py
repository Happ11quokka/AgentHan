from agenthan.assistant import selftest


def test_selftest_passes(capsys) -> None:
    selftest()
    assert capsys.readouterr().out.startswith("PASS:")
