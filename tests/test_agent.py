"""E2E 루프 테스트: 가짜 HCX가 도구를 호출하고, 결과를 받아 답변하는 흐름 (API 키 불필요)."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

from review_tools import MenuCatalog, SnapshotRepo
from review_tools.agent import ReviewToolbox, run_turn, system_prompt

ROOT = Path(__file__).resolve().parents[1]

REPO = SnapshotRepo(ROOT / "tests" / "fixtures" / "review_snapshot.jsonl")
CAT = MenuCatalog.load(ROOT / "tests" / "fixtures" / "menus.json")


class FakeHCX:
    """1차: analyze_reviews 호출 / 2차: 도구 결과의 review_count를 인용해 답변."""

    def __init__(self, args):
        self.args, self.calls = args, []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        last = kw["messages"][-1]
        if kw["messages"][0]["content"].startswith("Translate"):
            n = last["content"].count("\n") + 1
            return NS(
                choices=[
                    NS(
                        message=NS(
                            content=json.dumps({"t": [f"T{i}" for i in range(n)]}), tool_calls=None
                        )
                    )
                ]
            )
        if last["role"] == "tool":
            n = json.loads(last["content"]).get("review_count")
            msg = NS(content=f"불만 리뷰는 {n}건입니다.", tool_calls=None)
        else:
            call = NS(
                id="call_1",
                function=NS(
                    name="analyze_reviews", arguments=json.dumps(self.args, ensure_ascii=False)
                ),
            )
            msg = NS(content="", tool_calls=[call])
        return NS(choices=[NS(message=msg)])


def test_full_turn():
    tb = ReviewToolbox("연습떡집", date(2026, 11, 7), REPO, CAT)
    fake = FakeHCX(
        {"period": "all", "focus": "complaint", "store_id": "다른가게"}
    )  # 명세 밖 인자는 무시돼야 함
    logs = []
    msgs = [
        {"role": "system", "content": system_prompt("연습떡집 본점", date(2026, 11, 7), "ko")},
        {"role": "user", "content": "손님들 불만 뭐야?"},
    ]
    out = run_turn(fake, "HCX-DASH-002", msgs, tb, log=lambda *x: logs.append(x))
    name, args, result = logs[0]
    assert name == "analyze_reviews" and result["filters"]["focus"] == "complaint"
    notice, body = out.split("\n\n", 1)  # 기간 안내는 시스템이 맨 앞에 붙인다
    assert (
        notice.startswith("기간을 정하지 않으셔서")
        and "최근 30일(2026-10-08 ~ 2026-11-07)" in notice
    )
    assert body.startswith(f"불만 리뷰는 {result['review_count']}건입니다.")
    assert (
        "**대표 리뷰**" in out and f'"{result["examples"][0]["quote"]}"' in out
    )  # 원문 인용은 시스템이 붙임
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "tool", "assistant"]
    assert msgs[3]["tool_call_id"] == "call_1"
    assert fake.calls[0]["max_tokens"] == 1024 and "response_format" not in fake.calls[0]


def test_bad_period_is_reported_not_crashed():
    tb = ReviewToolbox("연습떡집", date(2026, 11, 7), REPO, CAT)
    assert tb.dispatch("analyze_reviews", {"period": "2주"})["error"] == "bad_arguments"
    assert tb.dispatch("delete_reviews", {})["error"] == "unknown_tool"


def test_owner_language_in_prompt():
    assert "русский" in system_prompt("연습떡집", date(2026, 11, 7), "ru")


class NoToolHCX(FakeHCX):
    """도구 결과가 오기 전에는 도구 없이 지어낸 답을 하는 HCX 흉내 (실제로 러시아어 질문에서 발생).
    nudge_works=True면 재요청 메시지를 받은 뒤에는 도구를 부른다."""

    def __init__(self, args, nudge_works=False):
        super().__init__(args)
        self.nudge_works = nudge_works

    def _create(self, **kw):
        assert "tool_choice" not in kw  # CLOVA가 거절하므로 절대 보내면 안 됨
        last = kw["messages"][-1]
        nudged = last["role"] == "user" and "(시스템 안내)" in last["content"]
        assert (
            sum(m["role"] == "system" for m in kw["messages"]) <= 1
        )  # CLOVA: system 메시지는 1개만
        if last["role"] == "tool" or (nudged and self.nudge_works):
            return super()._create(**kw)
        self.calls.append(kw)
        return NS(
            choices=[NS(message=NS(content="Клубничный моти – это напиток...", tool_calls=None))]
        )


def test_nudge_then_tool_call():
    tb = ReviewToolbox("연습떡집", date(2026, 11, 7), REPO, CAT)
    fake = NoToolHCX({"period": "all"}, nudge_works=True)
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "Что говорят о клубничном моти?"},
    ]
    out = run_turn(fake, "HCX-DASH-002", msgs, tb)
    body = out.split("\n\n", 1)[1]
    assert body.startswith("불만 리뷰는") and any(m["role"] == "tool" for m in msgs)


def test_system_runs_tool_when_model_never_calls():
    tb = ReviewToolbox("연습떡집", date(2026, 11, 7), REPO, CAT)
    fake = NoToolHCX({"period": "all"}, nudge_works=False)
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "Что говорят о клубничном моти?"},
    ]
    logs = []
    out = run_turn(fake, "HCX-DASH-002", msgs, tb, log=lambda *x: logs.append(x))
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert tool_msgs and json.loads(tool_msgs[0]["content"])["filters"]["menus"] == [
        "딸기모찌"
    ]  # 원문에서 메뉴를 찾음
    assert "напиток" not in out and [entry[0] for entry in logs][:2] == ["(재요청)", "(직접 실행)"]


def test_no_force_for_unrelated_question():
    tb = ReviewToolbox("연습떡집", date(2026, 11, 7), REPO, CAT)
    fake = NoToolHCX({"period": "all"})
    out = run_turn(
        fake,
        "HCX-DASH-002",
        [{"role": "system", "content": "s"}, {"role": "user", "content": "오늘 날씨 어때?"}],
        tb,
    )
    assert len(fake.calls) == 1 and "напиток" in out


def test_vague_period_is_ignored():
    tb = ReviewToolbox("연습떡집", date(2026, 11, 7), REPO, CAT)
    r = tb.dispatch(
        "analyze_reviews",
        {"period": "7d", "focus": "complaint"},
        utterance="요즘 손님들 불만 뭐야?",
    )
    assert r.get("period_expanded", {}).get("to") == "30d"  # '요즘'은 기간 표현이 아님 → 자동 확장
    r = tb.dispatch("analyze_reviews", {"period": "7d"}, utterance="이번 주 리뷰 정리해줘")
    assert "period_expanded" not in r and r["review_count"] == 0  # 분명한 기간이면 그대로
    r = tb.dispatch("analyze_reviews", {"period": "30d"}, utterance="Отзывы за месяц?")
    assert r["period"]["start"] == "2026-10-08"


def test_missing_topic_filled_from_utterance():
    tb = ReviewToolbox("연습떡집", date(2026, 11, 7), REPO, CAT)
    r = tb.dispatch(
        "analyze_reviews", {"period": "all"}, utterance="Что говорят о клубничном моти?"
    )
    assert r["filters"]["menus"] == ["딸기모찌"]


class KoreanAnswerHCX(FakeHCX):
    """러시아어 사장님에게 한국어로 답하다가, 다시 쓰라고 하면 러시아어로 쓰는 HCX 흉내."""

    def _create(self, **kw):
        last = kw["messages"][-1]
        if last["role"] == "user" and "다시 쓰세요" in last["content"]:
            return NS(
                choices=[
                    NS(
                        message=NS(
                            content='Найдено 3 отзыва. "맛이 없어요..." (невкусно)', tool_calls=None
                        )
                    )
                ]
            )
        if last["role"] == "tool":
            return NS(
                choices=[
                    NS(message=NS(content='리뷰 3건이 있습니다. "맛이 없어요..."', tool_calls=None))
                ]
            )
        return super()._create(**kw)


def test_answer_rewritten_in_owner_language():
    from review_tools.agent import _mostly_korean

    assert _mostly_korean('리뷰 3건이 있습니다. "맛이 없어요..."')
    assert not _mostly_korean(
        'Найдено 3 отзыва. "맛이 없어요..." (невкусно)'
    )  # 인용은 한국어여도 됨
    tb = ReviewToolbox("연습떡집", date(2026, 11, 7), REPO, CAT)
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "Что говорят о клубничном моти?"},
    ]
    out = run_turn(KoreanAnswerHCX({"topic": "моти"}), "HCX-005", msgs, tb, owner_lang="ru")
    notice, body = out.split("\n", 1)
    assert notice.startswith("Период не указан") and "последние 30 дн." in notice
    assert body.strip().startswith("Найдено")
    assert "**Примеры отзывов**" in out and "(T0)" in out  # 한국어 원문 + 전용 호출 번역


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


def test_analysis_answer_cleanup():
    from review_tools.agent import expand_notice, model_view, strip_confirm

    # 답글용 확인 질문이 분석 답변에 섞이던 문제 (2026-11-07 실측, 시나리오 1·2·3)
    text = (
        "총 4건의 리뷰 중 3건이 긍정적이었습니다.\n\n"
        "리뷰가 적어 경향을 말하기 어렵습니다. "
        "이대로 올려도 괜찮을까요, 아니면 수정할 사항이 있을까요?"
    )
    assert strip_confirm(text) == (
        "총 4건의 리뷰 중 3건이 긍정적이었습니다.\n\n리뷰가 적어 경향을 말하기 어렵습니다."
    )
    assert (
        strip_confirm("Three reviews mention it. Shall I post it as is?")
        == "Three reviews mention it."
    )
    assert strip_confirm("확인 질문 없는 답변입니다.") == "확인 질문 없는 답변입니다."
    # 넓힌 기간은 시스템이 정확히 말하고, 모델에게는 7d→30d 숫자를 보여 주지 않는다
    res = {
        "period": {"start": "2026-10-08", "end": "2026-11-07"},
        "period_expanded": {"from": "7d", "to": "30d"},
    }
    assert "최근 30일(2026-10-08 ~ 2026-11-07)" in expand_notice(res, "ko")
    assert "all reviews" in expand_notice(
        {**res, "period_expanded": {"from": "7d", "to": "all"}}, "en"
    )
    assert "7d" not in json.dumps(model_view("analyze_reviews", res), ensure_ascii=False)
    assert expand_notice({"period": res["period"]}, "ko") == ""


def test_dropped_period_removed_from_history():
    # 모델이 넣었다가 시스템이 버린 period가 대화 기록에 남으면 모델이 그 값을 기간으로 말한다
    tb = ReviewToolbox("연습떡집", date(2026, 11, 7), REPO, CAT)
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "최근 리뷰 어때?"}]
    run_turn(FakeHCX({"period": "7d"}), "HCX-005", msgs, tb)
    call = next(m for m in msgs if m.get("tool_calls"))["tool_calls"][0]
    assert "period" not in json.loads(call["function"]["arguments"])


def test_period_phrases_removed_when_notice_added():
    from review_tools.agent import PERIOD_PHRASE

    # 기간을 30일로 넓혔는데 본문이 '지난 7일 동안'이라고 말함 (2026-11-07 실측, 시나리오 2)
    body = "지난 7일 동안 총 4건의 리뷰 중 3건이 딸기모찌에 대한 내용입니다."
    assert PERIOD_PHRASE.sub("", body) == "총 4건의 리뷰 중 3건이 딸기모찌에 대한 내용입니다."
    assert PERIOD_PHRASE.sub("", "최근 한 달간 총 4건") == "총 4건"
    assert PERIOD_PHRASE.sub("", "In the last week, three reviews") == "three reviews"
