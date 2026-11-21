"""계획 모드: 실제 턴·승인 경계에서 가짜 HCX로 검증한다."""

import copy
import json
from datetime import date
from itertools import repeat
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agenthan.agent.clova_chat import ClovaRequestRejected
from agenthan.agent.menu_photo import MenuDraft
from agenthan.agent.owner_assistant import OwnerAssistant
from agenthan.app.server import create_app
from agenthan.eval.fake_backend import FakeBackend
from agenthan.eval.scenario import normalize_trace
from agenthan.tools.dummy_store import SEED_PATH


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **kw: pytest.fail("오프라인 테스트의 네트워크 호출")
    )


def call(tool, **args):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": tool, "arguments": args}}],
    }


class HCX:
    def __init__(
        self,
        features,
        *responses,
        scope="store",
        clarify="",
        plans=None,
        review_action="read",
    ):
        self.features = features
        self.scope = scope
        self.clarify = clarify
        self.responses = iter(responses)
        self.scripted = list(plans) if plans is not None else None
        self.requests = []
        self.plans = []
        self.review_action = review_action

    def plan(self, model, body):
        self.plans.append((model, copy.deepcopy(body)))
        text = body["messages"][1]["content"].split('"""', 2)[1]
        if self.scripted is not None:  # 턴마다 다른 계획(원장 단계 포함)
            planned = copy.deepcopy(self.scripted.pop(0))
            for step in planned.get("steps", []):
                if step.get("goal") == step.get("feature"):
                    step["goal"] = text
                if step.get("feature") == "review":
                    step.setdefault("review_action", self.review_action)
            return {"message": {"content": json.dumps(planned)}}
        return {
            "message": {
                "content": json.dumps(
                    {
                        "scope": self.scope,
                        "steps": [
                            {
                                "feature": f,
                                "goal": text,
                                **({"review_action": self.review_action} if f == "review" else {}),
                            }
                            for f in self.features
                        ],
                        "clarify": self.clarify,
                    }
                )
            }
        }

    def chat(self, messages, *, tools, **options):
        self.requests.append(copy.deepcopy((messages, tools, options)))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def new(feature, ref=None, scope="store", review_action=None):
    step = {"kind": "new", "feature": feature, "goal": feature}
    if ref:
        step["ref"] = ref
        step["ref_basis"] = "direct"
    if feature == "review":
        step["review_action"] = review_action or ("draft" if ref else "read")
    return {"scope": scope, "steps": [step], "clarify": ""}


def on(kind, task_id, feature, goal=""):
    """열린 작업에 대한 원장 단계(continue·revise·approve·cancel) 계획."""
    return {
        "scope": "store",
        "steps": [{"kind": kind, "task_id": task_id, "feature": feature, "goal": goal or kind}],
        "clarify": "",
    }


def planner_input(hcx, index=-1):
    """계획 호출이 받은 사장님 말·열린 작업·최근 보여 준 것과 enum."""
    body = hcx.plans[index][1]
    schema = body["responseFormat"]["schema"]["properties"]["steps"]["items"]["properties"]
    return body["messages"][1]["content"], schema


def assistant(tmp_path, hcx):
    path = tmp_path / "store.json"
    path.write_bytes(SEED_PATH.read_bytes())
    return OwnerAssistant(
        path,
        agent_mode="planner",
        plan_call=hcx.plan,
        chat=hcx.chat,
        embed=FakeBackend().embed,
        select_chat=lambda messages, *, tools: call("select_feature", feature_id="none"),
    )


def chain_plan(*steps, writes=("menu",)):
    return {"scope": "store", "steps": list(steps), "write_features": list(writes), "clarify": ""}


def dependent_menu(goal="관찰에서 고른 메뉴를 품절 처리", **extra):
    return {
        "feature": "menu",
        "goal": goal,
        "needs": ["review"],
        "target_metric": "menu_mentions",
        "target_order": "max",
        **extra,
    }


def make_mention_winner(app):
    store = app.store.read()
    store["reviews"][-1]["text"] += " 블린. 블린."
    app.store.write(store)


def settings_plan(text, **values):
    planned = new("review", review_action="settings")
    planned["steps"][0]["owner_values"] = [
        {"field": k, "value": str(v).lower() if isinstance(v, bool) else v, "evidence": text}
        for k, v in values.items()
    ]
    return planned


@pytest.mark.parametrize(
    "owner_text",
    [
        "손님 글에서 자주 거론된 음식을 품절 처리해",
        "후기에 등장 횟수가 으뜸인 메뉴는 재료가 떨어졌어",
    ],
)
@pytest.mark.parametrize("omitted", ["write", "read"])
def test_b5g_missing_chain_steps_are_recovered_from_plan_state(tmp_path, owner_text, omitted):
    read = {
        "feature": "review",
        "goal": "메뉴 언급 집계",
        "review_action": "read",
        "target_metric": "menu_mentions",
    }
    write = dependent_menu()
    plans = (
        [chain_plan(read), chain_plan(read, write)] if omitted == "write" else [chain_plan(write)]
    )
    hcx = HCX(
        [],
        call("ask_review_agent", request="집계"),
        call("upsert_menu", name="블린", action="sold_out"),
        {"role": "assistant", "content": "조회"},
        plans=plans,
    )
    app = assistant(tmp_path, hcx)
    make_mention_winner(app)
    before = app.store.read()
    app.ask(owner_text)
    task = app.ledger.find_open("menu")
    assert task and task.preview
    assert task.slots["name"].source == "lookup"
    assert app.store.read() == before
    assert "menu_mentions.블린=" in str(hcx.requests[1][0])
    if omitted == "write":
        assert len(hcx.plans) == 2
        assert "menu_mentions.블린=" in hcx.plans[1][1]["messages"][1]["content"]
    app.ask("네")
    assert next(m for m in app.store.read()["menus"] if m["name"] == "블린")["sold_out"]


@pytest.mark.parametrize(
    "owner_text",
    [
        "후기에 많이 등장한 음식 금액을 500원 더 받아",
        "손님 글에서 제일 자주 거론된 메뉴를 오백 원 인상해",
    ],
)
@pytest.mark.parametrize("target", ["블린", "메도빅", "wrong_direction"])
@pytest.mark.parametrize("other_price", [6500, 8500])
def test_b5g_dependent_write_only_accepts_the_observed_winner(
    tmp_path, owner_text, target, other_price
):
    hcx = HCX(
        [],
        call("ask_review_agent", request="집계"),
        call(
            "upsert_menu",
            name="블린" if target == "wrong_direction" else target,
            price=8500 if target == "wrong_direction" else 9500 if target == "블린" else 7000,
        ),
        {"role": "assistant", "content": "조회"},
        plans=[chain_plan(dependent_menu("관찰 대상 가격 500원 인상"))],
    )
    app = assistant(tmp_path, hcx)
    make_mention_winner(app)
    data = app.store.read()
    next(m for m in data["menus"] if m["name"] == "메도빅")["price"] = other_price
    app.store.write(data)
    before = app.store.read()
    app.ask(owner_text)
    task = app.ledger.find_open("menu")
    # B5h: 방향은 실행 모델이 계산하고 사장님이 미리보기에서 확인한다(차이 금액만 코드 대조).
    assert bool(task and task.preview) == (target in ("블린", "wrong_direction"))
    assert app.store.read() == before


@pytest.mark.parametrize(
    "owner_text", ["별로인 리뷰만 알려 줘", "손님이 실망했다고 쓴 후기만 통지해 줘"]
)
def test_b5g_settings_semantics_come_from_extracted_owner_evidence(tmp_path, owner_text):
    step = new("review", review_action="settings")["steps"][0]
    step["owner_values"] = [
        {"field": "focus", "value": "complaint", "evidence": owner_text},
        {"field": "alert", "value": "negative", "evidence": owner_text},
    ]
    hcx = HCX([], plans=[chain_plan(step, writes=("review",))])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    reply = app.ask(owner_text)
    task = app.ledger.find_open("review")
    assert task and task.preview and "불만 리뷰" in reply
    box = app.review.toolbox
    sid = box.ctx["store_id"]
    assert box.settings_store.get(sid)["focus"] == "all"
    assert task.slots["focus"].source == "owner"
    app.ask("네")
    assert box.settings_store.get(sid)["focus"] == "complaint"


@pytest.mark.parametrize("yes", ["네", "좋아요"])
@pytest.mark.parametrize("count", [1, 2])
def test_b5g_yes_after_review_candidate_question_uses_recorded_proposals(
    tmp_path, monkeypatch, yes, count
):
    def delegate(request, session):
        rows = session.toolbox.ctx["repo"].get_reviews(
            session.toolbox.ctx["store_id"], None, session.toolbox.ctx["today"]
        )
        rows = [r for r in rows if not r.get("has_owner_reply")][:count]
        session.log("get_unanswered_reviews", {}, {"reviews": rows})
        return {"text": "어느 리뷰에 답글을 쓸까요?", "blocks": "\n".join(r["text"] for r in rows)}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", delegate)
    hcx = HCX([], plans=[new("review")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("아직 회신하지 않은 손님 글을 보여 주세요")
    reply = app.ask(yes)
    assert "메뉴판 사진" not in reply
    if count == 1:
        assert app.ledger.active().preview["button_review"]
        assert "답글 초안" in reply
    else:
        assert "리뷰" in reply and "선택" in reply
        assert not any(t.preview for t in app.ledger.open_tasks())


@pytest.mark.parametrize(
    "owner_text",
    ["후기 언급 횟수 1위인 음식을 품절 처리해", "손님 글에서 자주 거론된 메뉴는 재료가 떨어졌어"],
)
def test_b5g_tied_observation_requires_owner_selection(tmp_path, owner_text):
    hcx = HCX(
        [],
        call("ask_review_agent", request="집계"),
        call("upsert_menu", name="블린", action="sold_out"),
        {"role": "assistant", "content": "조회"},
        call("upsert_menu", name="블린", action="sold_out"),
        plans=[chain_plan(dependent_menu()), on("continue", "t1", "menu", "블린 선택")],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    reply = app.ask(owner_text)
    assert "같아요" in reply and "메도빅" in reply and "블린" in reply
    assert not app.ledger.active().preview and app.store.read() == before
    app.ask("블린으로 할게요")
    assert app.ledger.active().preview
    assert app.store.read() == before
    app.ask("네")
    assert next(m for m in app.store.read()["menus"] if m["name"] == "블린")["sold_out"]


@pytest.mark.parametrize(
    "owner_text",
    [
        "후기에서 가장 자주 보인 메뉴를 500원 올려 줘",
        "손님 글의 최다 언급 메뉴 금액을 오백 원 높여 줘",
    ],
)
@pytest.mark.parametrize("mentions", [1, 2])
def test_b5g_one_review_and_alias_are_enough_for_a_chain(
    tmp_path, monkeypatch, owner_text, mentions
):
    monkeypatch.setattr(
        "agenthan.agent.review_delegate.ask_review_agent",
        lambda *a: pytest.fail("집계에 리뷰 모델 불필요"),
    )
    hcx = HCX(
        [],
        call("ask_review_agent", request="집계"),
        call("upsert_menu", name="블린", price=9500),
        {"role": "assistant", "content": "조회"},
        plans=[chain_plan(dependent_menu("관찰 메뉴 가격 인상"))],
    )
    app = assistant(tmp_path, hcx)
    data = app.store.read()
    menu = next(m for m in data["menus"] if m["name"] == "블린")
    menu["aliases"] = ["둥근 팬케이크"]
    data["reviews"] = [{**data["reviews"][0], "text": "둥근 팬케이크. " * mentions}]
    app.store.write(data)
    app.ask(owner_text)
    result = app.trace["results"][0]
    assert result["menu_mentions"]["블린"] == mentions
    assert app.ledger.active().slots["name"].source == "lookup"
    assert app.ledger.active().preview


def test_b5g_with_the_store_rag_spacing_and_typos_count_once_per_review(tmp_path, monkeypatch):
    """ADR-0008: with the RAG plugged in, mentions are reviews that name the menu."""
    from agenthan.app import pilot

    monkeypatch.setattr(
        "agenthan.agent.review_delegate.ask_review_agent",
        lambda *a: pytest.fail("집계에 리뷰 모델 불필요"),
    )
    pilot.use_knowledge()
    hcx = HCX(
        [],
        call("ask_review_agent", request="집계"),
        call("upsert_menu", name="블린", price=9500),
        {"role": "assistant", "content": "조회"},
        plans=[chain_plan(dependent_menu("관찰 메뉴 가격 인상"))],
    )
    app = assistant(tmp_path, hcx)
    data = app.store.read()
    data["reviews"] = [
        {**data["reviews"][0], "review_id": "a", "text": "블 린 맛있어요. 블린 또 먹을래요"},
        {**data["reviews"][0], "review_id": "b", "text": "블린이 제일 좋아요"},
    ]
    app.store.write(data)
    app.ask("리뷰에서 제일 많이 나온 메뉴 가격 올려 줘")
    assert app.trace["results"][0]["menu_mentions"]["블린"] == 2


@pytest.mark.parametrize(
    "owner_text", ["별로인 리뷰만 알려 줘", "손님이 실망했다고 쓴 후기만 통지해 줘"]
)
def test_b5g_settings_without_real_owner_evidence_ask_and_never_save(tmp_path, owner_text):
    planned = settings_plan("사장님이 말하지 않은 문장", focus="complaint")
    hcx = HCX([], plans=[planned])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    reply = app.ask(owner_text)
    assert "리뷰 보고 대상" in reply and "알려 주세요" in reply
    assert not app.ledger.active().preview
    reply = app.ask("네")
    assert "리뷰 보고 대상" in reply
    box = app.review.toolbox
    assert box.settings_store.get(box.ctx["store_id"])["focus"] == "all"


@pytest.mark.parametrize(
    "owner_text", ["다음 분기 경영 전략을 설계해 줘", "매출을 대폭 키울 마케팅·재무 계획을 세워 줘"]
)
def test_b5g_consulting_is_outside_the_feature_scope(tmp_path, owner_text):
    hcx = HCX([], plans=[{"scope": "out", "steps": [], "clarify": ""}])
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    reply = app.ask(owner_text)
    assert "범위 밖" in reply and not app.trace["calls"]
    assert not hcx.requests and app.store.read() == before
    prompt = hcx.plans[0][1]["messages"][0]["content"]
    assert "제공 기능(통계·리뷰·영업시간·쿠폰·대표키워드 등)으로 근거를 보이고" in prompt
    assert "일반 경영·마케팅 전략 수립·재무 계획" in prompt
    assert owner_text not in prompt


@pytest.mark.parametrize(
    "owner_text",
    [
        "조회 손님은 많은데 예약이 적어요. 무엇을 바꾸면 좋을까요?",
        "우리 가게 숫자를 살펴보고 운영에서 손볼 점을 알려 줘",
    ],
)
def test_b5g_operational_advice_keeps_its_evidence_and_no_write(tmp_path, owner_text):
    hcx = HCX(
        ["statistics"],
        call("get_statistics", period="7d"),
        {"role": "assistant", "content": "조회"},
        scope="advice",
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    reply = app.ask(owner_text)
    assert "조회" in reply and app.next_actions
    assert app.store.read() == before and not any(t.preview for t in app.ledger.open_tasks())


@pytest.mark.parametrize("yes", ["네", "좋아요"])
def test_b5g_advice_proposal_does_not_approve_an_unrelated_preview(tmp_path, monkeypatch, yes):
    def delegate(request, session):
        return {"text": "답글 없는 리뷰가 있어요.", "blocks": ""}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", delegate)
    hcx = HCX(
        [],
        call("upsert_menu", name="블린", price=9500),
        {"role": "assistant", "content": "미리보기"},
        call("ask_review_agent", request="리뷰 대응 조언"),
        {"role": "assistant", "content": "조언"},
        plans=[new("menu"), new("review", scope="advice")],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("블린 가격 9500원으로 바꿔 줘")
    menu_task = app.ledger.find_open("menu")
    assert menu_task.preview
    before = app.store.read()
    app.ask("그 전에 손님 글에 어떻게 대응하면 좋을지 알려 줘")
    assert app.planner_loop.last_action["kind"] == "suggest"
    assert len(app.planner_loop.last_action["proposals"]) == 1
    reply = app.ask(yes)
    assert "답글 초안" in reply
    assert app.store.read() == before and menu_task.is_open and menu_task.preview


@pytest.mark.parametrize("yes", ["네", "좋아요"])
def test_b5g_extracted_settings_are_rechecked_at_approval(tmp_path, yes):
    owner_text = "실망스러웠다는 손님 글만 알림 받고 싶어요"
    hcx = HCX([], plans=[settings_plan(owner_text, focus="complaint")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask(owner_text)
    task = app.ledger.active()
    task.preview["owner_values"]["focus"] = ("complaint", "없는 근거")
    reply = app.ask(yes)
    assert "출처" in reply
    box = app.review.toolbox
    assert box.settings_store.get(box.ctx["store_id"])["focus"] == "all"


@pytest.mark.parametrize(
    "owner_text",
    [
        "블린을 9500원으로 바꾸고 불만 리뷰 원문도 보여 줘",
        "손님 평가를 읽고 싶어. 블린 가격은 9500원으로 고쳐 줘",
    ],
)
def test_b5g_independent_review_read_is_not_replaced_with_menu_aggregation(
    tmp_path, monkeypatch, owner_text
):
    received = []

    def delegate(request, session):
        received.append(request)
        return {"text": "실제 리뷰 조회 결과", "blocks": "리뷰 원문"}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", delegate)
    read = {"feature": "review", "goal": "불만 리뷰 원문 조회", "review_action": "read"}
    write = {"feature": "menu", "goal": "블린 9500원으로 변경"}
    hcx = HCX(
        [],
        call("ask_review_agent", request="조회"),
        call("upsert_menu", name="블린", price=9500),
        {"role": "assistant", "content": "조회"},
        plans=[chain_plan(read, write)],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    reply = app.ask(owner_text)
    assert received == ["불만 리뷰 원문 조회"]
    assert "리뷰 원문" in reply and "언급 횟수" not in reply
    assert app.ledger.find_open("menu").preview and app.store.read() == before


@pytest.mark.parametrize(
    "owner_text", ["모든 손님 글을 빠짐없이 보여 줘", "보고할 때 후기를 가리지 말아 줘"]
)
def test_b5g_settings_reverted_to_original_value_also_update_history(tmp_path, owner_text):
    first = "실망스러운 글만 보고 싶어요"
    revision = settings_plan(owner_text, focus="all")
    revision["steps"][0].update(kind="revise", task_id="t1")
    hcx = HCX([], plans=[settings_plan(first, focus="complaint"), revision])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask(first)
    app.ask(owner_text)
    task = app.ledger.active()
    assert task.preview["after"]["focus"] == task.slots["focus"].value == "all"
    app.ask("네")
    assert task.closed_values["focus"] == "all"


@pytest.mark.parametrize(
    ("edit", "price"),
    [("아니 8500원으로 해 줘", 8500), ("생각이 바뀌었어요. 8700원에 팔래요", 8700)],
)
def test_b5g_absolute_price_revision_replaces_the_previous_delta(tmp_path, edit, price):
    initial = new("menu")
    hcx = HCX(
        [],
        call("upsert_menu", name="블린", price=9500),
        call("upsert_menu", name="블린", price=price),
        {"role": "assistant", "content": "미리보기"},
        plans=[initial, on("revise", "t1", "menu", edit)],
    )
    app = assistant(tmp_path, hcx)
    app.ask("블린 가격을 500원 올려 줘")
    assert app.ledger.active().preview
    app.ask(edit)
    app.ask("네")
    assert next(m for m in app.store.read()["menus"] if m["name"] == "블린")["price"] == price


@pytest.mark.parametrize("yes", ["네", "좋아요"])
def test_b5g_review_and_playbook_proposals_are_all_considered(tmp_path, monkeypatch, yes):
    def delegate(request, session):
        rows = session.toolbox.ctx["repo"].get_reviews(
            session.toolbox.ctx["store_id"], None, session.toolbox.ctx["today"]
        )
        unanswered = [r for r in rows if not r.get("has_owner_reply")][:1]
        session.log("get_unanswered_reviews", {}, {"reviews": unanswered})
        return {"text": "리뷰 조회", "blocks": ""}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", delegate)
    hcx = HCX(
        ["statistics", "review"],
        call("get_statistics", period="7d"),
        call("ask_review_agent", request="조회"),
        {"role": "assistant", "content": "조언"},
        scope="advice",
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("가게 숫자와 손님 글을 보고 무엇을 해 볼지 알려 줘")
    proposals = app.planner_loop.last_action["proposals"]
    assert len(proposals) > 1
    assert {p["action_id"] for p in proposals} >= {p["action_id"] for p in app.next_actions}
    before = app.store.read()
    assert "선택" in app.ask(yes)
    assert app.store.read() == before and not any(t.preview for t in app.ledger.open_tasks())


@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("invented", [False, True])
def test_b5e_store_slice_values_are_lookup_sources(tmp_path, multi, invented):
    hcx = HCX(
        ["business_hours", "search_keywords"] if multi else ["business_hours"],
        call(
            "update_business_hours",
            days=["sun"],
            open_time="07:13" if invented else "09:00",
            close_time="22:00",
        ),
        call("set_representative_keywords", keywords=["블린"]),
        *[{"role": "assistant", "content": "여는 시간을 알려 주세요."}] * 4,
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask(
        "일요일은 금요일과 같은 시간으로 열고"
        + (" 대표키워드는 블린으로 해줘" if multi else " 싶어요")
    )
    task = app.ledger.find_open("business_hours")
    assert app.store.read() == before
    if invented:
        assert task.preview is None
        assert "unconfirmed_value" in str(app.trace["results"])
    else:
        assert task.preview is not None
        assert task.slots["open_time"].source == "lookup"
        app.ask("네")
        assert app.store.read()["shop"]["hours"]["sun"]["open_time"] == "09:00"


def test_b5e_clock_is_the_assistant_today_in_plan_and_act(tmp_path):
    hcx = HCX(
        ["business_hours", "coupon"],
        call("update_business_hours", days=["mon"], closed=True),
        call(
            "create_coupon",
            title="개점 할인",
            discount_type="percent",
            discount_value=12,
            start_date="2026-11-13",
            end_date="2026-11-15",
        ),
    )
    app = assistant(tmp_path, hcx)
    app.today = date(2026, 11, 12)
    app.ask("월요일 쉬고 내일부터 11월 15일까지 개점 할인 12% 쿠폰 만들어줘")
    assert "오늘: 2026-11-12 (목요일)" in hcx.plans[0][1]["messages"][1]["content"]
    assert all("오늘: 2026-11-12 (목요일)" in str(m) for m, _, _ in hcx.requests)
    assert len([t for t in app.ledger.open_tasks() if t.preview]) == 2


def test_b5e_yes_after_missing_value_gets_concrete_question_without_an_approval(tmp_path):
    hcx = HCX(
        ["menu", "business_hours"],
        call("upsert_menu", name="블린", price=9300),
        call("ask_owner", fields=["여는 시간"]),
        *[call("ask_owner", fields=["여는 시간"])] * 5,
    )
    app = assistant(tmp_path, hcx)
    app.ask("블린 9300원, 일요일은 시간을 바꾸고 싶어요")
    app.ask("네")  # 메뉴 미리보기만 승인, 시간 질문에 대한 동의는 값이 아니다.
    before = app.store.read()
    for _ in range(3):
        reply = app.ask("네")
        assert "여는 시간" in reply and "예:" in reply
        assert not any(a["action_id"] == "task.approve" for a in app.next_actions)
        assert app.store.read() == before
    assert len(hcx.requests) == 2


def test_b5e_criteria_ref_is_delegated_and_cannot_override_the_review_agent(tmp_path, monkeypatch):
    planned = new("review", ref="r02")
    planned["steps"][0]["ref_basis"] = "criteria"
    planned["steps"][0]["goal"] = "불만 내용이 담긴 리뷰에 답글을 작성해 주세요"
    received = []

    def delegate(request, session):
        received.append(request)
        session.toolbox._draft({}, review_id="r03")
        return {"text": "초안", "blocks": ""}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", delegate)
    hcx = HCX([], plans=[planned])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    before = app.store.read()
    app.ask("불만 내용이 담긴 리뷰에 답글을 작성해 주세요")
    assert received == [planned["steps"][0]["goal"]]
    assert "ref" not in app.trace["planner"]["steps"][0]
    assert app.ledger.active().slots["review_id"].value == "r03"
    assert app.store.read() == before
    app.ask("응 그대로 등록해")
    changed = [
        r["review_id"]
        for r, b in zip(app.store.read()["reviews"], before["reviews"], strict=True)
        if r != b
    ]
    assert changed == ["r03"]


def test_b5e_review_revision_and_consent_keep_the_original_id_and_expire_old_card(tmp_path):
    edit = on("revise", "t1", "review", "더 격식 있게 표현")
    edit["steps"][0]["review_action"] = "draft"
    # 승인 발화를 잘못 new로 분류해도 새 초안을 만들지 않는다.
    hcx = HCX([], plans=[new("review", ref="r03"), edit, new("review", ref="r02")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("후보 r03에 답글 써 주세요")
    task = app.ledger.active()
    old = copy.deepcopy(app.next_actions[0])
    before = app.store.read()
    calls = []

    def writer(text, sentiment, **kw):
        calls.append((text, kw))
        return "불편을 드린 점 깊이 사과드립니다. 더 세심하게 살피겠습니다."

    app.review.toolbox.writer.write_reply = writer
    reply = app.ask("더 격식 있게 표현해 주세요")
    assert task.preview["review_id"] == "r03" and "깊이 사과" in reply
    assert task.version > old["args"]["version"]
    assert calls[0][1]["edit_request"] == "더 격식 있게 표현"
    assert calls[0][1]["previous"] and app.store.read() == before
    assert "바뀌었어요" in app.run_action(old["action_id"], old["args"], old["label"])["reply"]
    app.ask("이걸로 확정해줘")
    changed = [
        r["review_id"]
        for r, b in zip(app.store.read()["reviews"], before["reviews"], strict=True)
        if r != b
    ]
    assert changed == ["r03"] and len(calls) == 1


def test_b5e_review_labels_keep_the_delegate_sentiment(tmp_path, monkeypatch):
    def delegate(request, session):
        session.log(
            "get_unanswered_reviews",
            {},
            {"reviews": [{"review_id": "r02", "sentiment": "negative", "rating": 3}]},
        )
        return {"text": "리뷰 목록", "blocks": "원문"}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", delegate)
    hcx = HCX([], plans=[new("review")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("리뷰를 보여 주세요")
    label = next(r["label"] for r in app.planner_loop.shown_view() if r["id"] == "r02")
    assert "별점 3" in label and "negative" in label


def test_b5e_review_observation_drives_a_dependent_menu_step(tmp_path, monkeypatch):
    def delegate(request, session):
        rows = session.toolbox.ctx["repo"].get_reviews(
            session.toolbox.ctx["store_id"], None, session.today
        )
        session.log("analyze_reviews", {}, {"reviews": rows})
        return {"text": "메뉴 언급을 확인했어요.", "blocks": ""}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", delegate)
    hcx = HCX(
        ["review", "menu"],
        call("ask_review_agent", request="조회"),
        call("upsert_menu", name="블린", price=9500),
        {"role": "assistant", "content": "조회 결과"},
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    before = app.store.read()
    app.ask("리뷰에서 가장 많이 언급된 메뉴를 500원 올려 주세요")
    assert "menu_mentions.블린=" in str(hcx.requests[1][0])
    task = app.ledger.active()
    assert task.slots["name"].source == task.slots["price"].source == "lookup"
    assert app.store.read() == before
    app.ask("네")
    assert next(m for m in app.store.read()["menus"] if m["name"] == "블린")["price"] == 9500
    prompt = hcx.plans[0][1]["messages"][0]["content"]
    assert "read 단계를 먼저" in prompt and "앞 단계 관찰" in prompt
    assert "기간별 주문 수·매출" in prompt and "시간대별 손님 분포" in prompt


def test_b5e_transient_retry_is_shared_by_all_models_in_a_turn(tmp_path):
    from agenthan.eval.replay import TransientChatError

    hcx = HCX(
        ["menu", "business_hours"],
        TransientChatError("temporary"),
        call("upsert_menu", name="블린", price=9500),
        TransientChatError("temporary"),
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask("블린 9500원으로 하고 월요일은 쉬어요")
    assert len(hcx.requests) == 3 and app.trace["transient_retries"] == 1
    assert app.trace["loop_budget"]["models"] == 4  # 계획 + 전송 3회
    assert app.store.read() == before


def test_b5e_rejected_single_feature_arguments_stop_without_another_model(tmp_path):
    hcx = HCX(
        ["business_hours"],
        call("update_business_hours", days=["sun"], open_time="07:13", close_time="22:00"),
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask("일요일도 금요일과 같은 시간으로 해 주세요")
    assert len(hcx.requests) == 1 and app.trace["results"][0]["error"] == "unconfirmed_value"
    assert app.store.read() == before and app.pending_action is None


def test_b5e_native_transport_uses_the_same_single_retry_budget(tmp_path, monkeypatch):
    from agenthan.core import llm

    statuses = iter([503, 200, 503])
    sent = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({"status": {"code": "20000"}, "result": {"ok": True}}).encode()

    def send(request, *, timeout):
        from urllib.error import HTTPError

        sent.append(timeout)
        if next(statuses) == 503:
            raise HTTPError("https://fake", 503, "temporary", {}, None)
        return Response()

    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "fake-key")
    monkeypatch.setattr("urllib.request.urlopen", send)
    app = assistant(tmp_path, HCX([]))
    loop = app.planner_loop
    assert loop.model(lambda: llm.chat_v3("HCX-007", {})) == {"ok": True}
    with pytest.raises(llm.ClovaV3Error):
        loop.model(lambda: llm.chat_v3("HCX-007", {}))
    assert len(sent) == 3 and all(t <= 20 for t in sent)
    assert loop.transient_retries == 1 and loop.budget.models == 3
    assert llm.call_deadline.get() is None and not llm.call_managed.get()


def test_b5e_unchanged_fields_and_incompatible_store_fields(tmp_path):
    from agenthan.agent import value_source
    from agenthan.agent.owner_assistant import tool_params

    app = assistant(tmp_path, HCX([]))
    loop = app.planner_loop
    # 여러 날을 수정할 때도 기존 필드가 모든 대상에서 같을 때만 미변경으로 인정한다.
    args = {"days": ["mon", "fri"], "closed": False, "open_time": "09:00", "close_time": "23:00"}
    sources, missing = value_source.check(
        tool_params("business_hours"),
        args,
        "월요일과 금요일 닫는 시간은 밤 11시로 해 주세요",
        app.store.read(),
        lookup=loop.lookup("business_hours", args),
    )
    assert sources["open_time"] == sources["closed"] == "lookup" and not missing
    # 다른 필드의 동일 문자열, bool을 숫자로 바꾼 값, 자료에 없는 값은 근거가 아니다.
    assert "open_time" not in loop.lookup("business_hours", {"open_time": "22:00"})
    assert "price" not in loop.lookup("menu", {"name": "블린", "price": True})
    assert "price" not in loop.lookup("menu", {"name": "블린", "price": 9123})


def test_b5e_refs_require_a_direct_selection_basis(tmp_path):
    from agenthan.agent import planner

    raw = new("review", ref="r02")
    raw["steps"][0].pop("ref_basis")
    parsed = planner.parse_ledger_plan(json.dumps(raw), {}, ["r02"])
    assert parsed.steps[0].ref is None


def test_b5e_previewless_task_never_gets_an_approval_button(tmp_path):
    from agenthan.agent.planner_loop import Batch

    app = assistant(tmp_path, HCX([]))
    task = app.ledger.start("menu")
    app.planner_loop.batch = Batch("", tasks={task.id: task.version})
    app.planner_loop.respond({}, phrase=False)
    assert not any(a["action_id"] == "task.approve" for a in app.next_actions)


def select_routes(app, *choices):
    """실제 legacy route의 후보·의도 판정은 가짜 HCX 응답으로 구동한다."""
    selected = iter(choices)
    app.embed = FakeBackend().embed
    app.select_chat = lambda messages, *, tools: call("select_feature", **next(selected))


def test_small_talk_reuses_the_legacy_help_without_rejection(tmp_path):
    hcx = HCX([], plans=[{"scope": "small_talk", "steps": [], "clarify": ""}])
    app = assistant(tmp_path, hcx)
    app.route = lambda *_: pytest.fail("잡담에 기능 선택 호출")
    reply = app.ask("안녕하세요")
    assert "도와드릴 수 있어요" in reply and "범위 밖" not in reply
    assert len(hcx.plans) == 1 and not hcx.requests and not app.trace["calls"]
    assert app.trace["scope_check"] == "none"


def test_a_plan_with_nothing_to_do_asks_instead_of_guessing_a_feature(tmp_path):
    # #92: 리뷰 요약 뒤 회의 잡담("…리뷰 쪽은 잘 받는 것 같고")에 HCX-005가 advice·빈 단계를 냈고,
    # 안전망 DASH가 '리뷰' 단어로 리뷰 요약을 다시 실행했다(실측 2026-11-21).
    hcx = HCX([], plans=[{"scope": "advice", "steps": [], "clarify": ""}])
    app = assistant(tmp_path, hcx)
    select_routes(app, {"feature_id": "review"})
    app.route = lambda *_: pytest.fail("할 일 없는 계획에 기능 선택 호출")
    reply = app.ask("어차피 저희 대회가 다 성인이에요. 그래서 리뷰 쪽은 잘 받는 것 같고")
    assert len(hcx.plans) == 1 and not hcx.requests and not app.trace["calls"]
    assert "도와드" in reply and app.trace["feature"] is None


def test_hybrid_single_read_uses_legacy_sentence(tmp_path):
    text = "이번 주 정산 예정액은 116,400원이고 입금 예정일은 2026-11-13이에요."
    hcx = HCX(
        ["settlement"],
        call("get_settlement_summary", period="this_week"),
        {"role": "assistant", "content": text},
    )
    app = assistant(tmp_path, hcx)
    reply = app.ask("이번 주 정산 얼마예요?")
    assert text in reply and "=" not in reply
    assert len(hcx.requests) == 2
    assert {t["function"]["name"] for t in hcx.requests[0][1]} == {"get_settlement_summary"}


def test_two_features_preview_then_one_approval_saves_both(tmp_path):
    hcx = HCX(
        ["menu", "business_hours"],
        call("upsert_menu", name="연습 차", price=5000),
        call("update_business_hours", days=["mon"], closed=True),
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    reply = app.ask("연습 차 5000원 등록하고 월요일은 휴무로 해 줘")
    assert app.store.read() == before
    assert "연습 차" in reply and "영업시간" in reply
    assert len([t for t in app.ledger.open_tasks() if t.preview]) == 2
    assert app.turns.get_state({"configurable": {"thread_id": "owner"}}).tasks[0].interrupts
    saved = app.ask("네")
    assert "저장" in saved
    assert next(m for m in app.store.read()["menus"] if m["name"] == "연습 차")["price"] == 5000
    assert app.store.read()["shop"]["hours"]["mon"]["closed"] is True
    assert not app.ledger.open_tasks()
    assert len(hcx.requests) == 2


def test_out_and_glossary_never_call_an_execution_model_or_tool(tmp_path):
    hcx = HCX([], scope="out")
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    assert "범위 밖" in app.ask("은행 대출 금리 알려 줘")
    assert not hcx.requests and not app.trace["calls"] and app.store.read() == before
    assert "전환율" in app.ask("전환율이 뭐예요?")
    assert len(hcx.plans) == 1 and not hcx.requests


def test_prose_only_retries_once_with_a_specific_function(tmp_path):
    hcx = HCX(
        ["menu", "business_hours"],
        {"role": "assistant", "content": "저장했어요."},
        call("upsert_menu", name="연습 차", price=5000),
        call("update_business_hours", days=["mon"], closed=True),
    )
    app = assistant(tmp_path, hcx)
    reply = app.ask("연습 차 5000원 등록하고 월요일 휴무로 해 줘")
    assert app.pending_action and "바꿀까요" in reply and "저장했어요" not in reply
    assert hcx.requests[0][2]["tool_choice"] == "auto"
    assert hcx.requests[1][2]["tool_choice"] == {
        "type": "function",
        "function": {"name": "upsert_menu"},
    }
    assert {s["function"]["name"] for s in hcx.requests[0][1]} == {
        "upsert_menu",
        "update_business_hours",
        "ask_owner",
    }
    assert len(hcx.plans) == 1


def test_same_tool_and_arguments_stop_the_turn_without_duplicate_write(tmp_path):
    response = call("upsert_menu", name="연습 차", price=5000)
    duplicate = copy.deepcopy(response["tool_calls"][0])
    duplicate["id"] = "c2"
    response["tool_calls"].append(duplicate)
    hcx = HCX(["settlement", "menu"], call("get_settlement_summary", period="this_week"), response)
    app = assistant(tmp_path, hcx)
    reply = app.ask("이번 주 정산 알려 주고 연습 차 5000원 등록해 줘")
    assert "처리 한도" not in reply and not any(
        m["name"] == "연습 차" for m in app.store.read()["menus"]
    )
    assert app.trace["loop_budget"]["tools"] == 2
    assert app.trace["budget_exhausted"]
    assert any(a["action_id"] == "task.approve" for a in app.next_actions)
    app.ask("네")
    assert len([m for m in app.store.read()["menus"] if m["name"] == "연습 차"]) == 1
    assert app.trace["loop_budget"]["tools"] == 0
    assert not app.trace["budget_exhausted"]


def test_approval_resumes_remaining_read_with_a_new_budget(tmp_path):
    hcx = HCX(
        ["menu", "settlement"],
        call("upsert_menu", name="연습 차", price=5000),
        call("get_settlement_summary", period="this_week"),
        {"role": "assistant", "content": "이번 주 정산 예정일은 2026-11-13이에요."},
    )
    app = assistant(tmp_path, hcx)
    app.ask("연습 차 5000원 등록하고 이번 주 정산 알려 줘")
    assert len(hcx.requests) == 1
    reply = app.ask("네")
    assert "저장" in reply and "2026-11-13" in reply
    assert len(hcx.plans) == 1 and len(hcx.requests) == 3
    assert app.trace["loop_budget"]["models"] == 2
    trace = normalize_trace(app.trace)
    assert [c["feature"] for c in trace["calls"]] == ["settlement"]


def test_approval_button_saves_the_shown_batch_without_model_calls(tmp_path):
    hcx = HCX(
        ["menu", "business_hours"],
        call("upsert_menu", name="연습 차", price=5000),
        call("update_business_hours", days=["mon"], closed=True),
    )
    app = assistant(tmp_path, hcx)
    app.ask("연습 차 5000원 등록하고 월요일은 휴무로 해 줘")
    action = next(a for a in app.next_actions if a["action_id"] == "task.approve")
    saved = app.run_action(action["action_id"], action["args"], action["label"])
    assert "저장" in saved["reply"] and not app.ledger.open_tasks()
    assert len(hcx.plans) == 1 and len(hcx.requests) == 2


def test_advice_uses_report_signals_and_buttons_without_writes(tmp_path):
    hcx = HCX(
        ["statistics"],  # 방법만 묻는 계획은 조회만 한다. 실행 단계는 별도 B5c 테스트로 확인한다.
        call("get_statistics", period="7d"),
        {"role": "assistant", "content": "대출을 받아 광고에 999만원 쓰세요. 쿠폰을 저장했어요."},
        scope="advice",
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    reply = app.ask("우리 가게 통계를 보고 뭘 해 볼지 추천해 줘")
    assert app.store.read() == before and app.pending_action is None
    assert "조회" in reply and "999" not in reply and "대출" not in reply
    assert app.next_actions
    assert all(
        a["action_id"] in {"coupon.create", "hours.edit", "keywords.edit", "review.draft_reply"}
        for a in app.next_actions
    )
    assert all(c["name"] != "create_coupon" for c in app.trace["calls"])


@pytest.mark.parametrize(
    "owner_text",
    [
        "이번 주 정산 들어오는 날 알려 주고 그날 하루 '정산일 감사' 쿠폰 1000원 할인으로 만들어 줘",
        "이번 주 입금 예정 날짜를 기준으로 하루짜리 '정산일 감사' 쿠폰 1000원 할인으로 만들어 줘",
    ],
)
def test_chain_uses_actual_settlement_date_as_confirmed_lookup(tmp_path, owner_text):
    hcx = HCX(
        ["settlement", "coupon"],
        call("get_settlement_summary", period="this_week"),
        call(
            "create_coupon",
            title="정산일 감사",
            discount_type="amount",
            discount_value=1000,
            start_date="2026-11-13",
            end_date="2026-11-13",
        ),
        {"role": "assistant", "content": "정산은 2026-11-13 예정이에요."},
    )
    app = assistant(tmp_path, hcx)
    reply = app.ask(owner_text)
    assert "2026-11-13" in reply and not app.store.read()["coupons"]
    second_messages = hcx.requests[1][0]
    assert len(second_messages) == 2
    assert "payout_date=2026-11-13" in second_messages[1]["content"]
    assert '"payout_date":' not in second_messages[1]["content"]
    app.ask("네")
    assert app.store.read()["coupons"][0]["start_date"] == "2026-11-13"


def test_invented_write_value_never_previews_or_saves(tmp_path):
    hcx = HCX(
        [],
        call("upsert_menu", name="연습 차", price=9999),
        call("upsert_menu", name="연습 차", price=9999),
        {"role": "assistant", "content": "가격을 알려 주세요."},
        plans=[
            {
                "scope": "store",
                "steps": [
                    {"kind": "new", "feature": "menu", "goal": "메뉴 등록"},
                    {"kind": "new", "feature": "business_hours", "goal": "영업시간"},
                ],
                "clarify": "",
            },
            on("approve", "t1", "menu"),
        ],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    reply = app.ask("연습 차 등록하고 영업시간도 바꿔 줘")
    assert "가격" in reply and app.pending_action is None
    app.ask("네")
    # 남은 단계는 "네"로 이어 가지만 지어낸 값은 여전히 미리보기에 오르지 못한다.
    assert app.store.read() == before and app.pending_action is None


def test_model_budget_stops_at_ten_and_leaves_a_continuation_button(tmp_path):
    hcx = HCX(["settlement", "statistics", "learning_support", "place_plus", "naverpay_signup"])
    hcx.responses = repeat({"role": "assistant", "content": "처리했어요."})
    app = assistant(tmp_path, hcx)
    reply = app.ask("가게 정산·통계·교육·POS·네이버페이 안내를 확인해 줘")
    assert "처리 한도" in reply
    assert app.trace["loop_budget"]["models"] == 10 and len(hcx.requests) == 9
    assert app.trace["loop_budget"]["reason"] == "model_limit"
    assert app.next_actions and not app.trace["calls"]


def test_tool_budget_never_runs_the_ninth_call(tmp_path):
    response = {"role": "assistant", "content": "", "tool_calls": []}
    for n in range(9):
        item = call("upsert_menu", name=f"차{n}", price=5000)["tool_calls"][0]
        item["id"] = f"c{n}"
        response["tool_calls"].append(item)
    hcx = HCX(["menu", "business_hours"], response)
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask(" ".join(f"차{n}" for n in range(9)) + " 모두 5000원 등록해 줘")
    assert app.trace["loop_budget"]["tools"] == 8
    assert app.trace["loop_budget"]["reason"] == "tool_limit"
    assert len(app.pending_action["actions"]) == 8 and app.store.read() == before


def test_wall_clock_stops_before_executing_a_late_model_answer(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("agenthan.agent.loop_budget.time.monotonic", lambda: clock[0])
    hcx = HCX(["menu", "business_hours"], call("upsert_menu", name="연습 차", price=5000))
    app = assistant(tmp_path, hcx)
    send = hcx.chat

    def late(*args, **kwargs):
        result = send(*args, **kwargs)
        clock[0] = 31.0
        return result

    app.chat = late
    before = app.store.read()
    assert "처리 한도" in app.ask("연습 차 5000원 등록해 줘")
    assert app.trace["loop_budget"]["reason"] == "wall_clock"
    assert not app.pending_action and app.store.read() == before


def test_rejection_retries_once_then_runs_the_next_step(tmp_path):
    hcx = HCX(
        ["menu", "business_hours"],
        ClovaRequestRejected("40009"),
        ClovaRequestRejected("40009"),
        call("update_business_hours", days=["mon"], closed=True),
    )
    app = assistant(tmp_path, hcx)
    app.ask("연습 차 5000원 등록하고 월요일 휴무로 해 줘")
    # 같은 단계는 도구를 지정해 한 번만 다시 묻고, 그래도 거절이면 건너뛴다.
    assert len(hcx.requests) == 3
    assert hcx.requests[1][2]["tool_choice"]["function"]["name"] == "upsert_menu"
    assert app.trace["rejected_retry"] is True
    assert app.pending_action["feature_id"] == "business_hours"
    assert not any(m["name"] == "연습 차" for m in app.store.read()["menus"])


def test_review_delegate_draft_joins_menu_preview_and_one_approval(tmp_path):
    hcx = HCX(
        ["review", "menu"],
        call("ask_review_agent", request="안 좋은 리뷰에 답글 써줘"),
        call("upsert_menu", name="연습 차", price=5000),
        {"role": "assistant", "content": "리뷰를 확인했어요."},
        review_action="draft",
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    before = app.store.read()
    reply = app.ask("안 좋은 리뷰에 답글 써줘. 그리고 연습 차 5000원 등록해 줘")
    assert "답글 초안" in reply and "연습 차" in reply and app.store.read() == before
    assert {t.feature for t in app.ledger.open_tasks() if t.preview} == {"review", "menu"}
    app.ask("네")
    assert sum(r.get("has_owner_reply", False) for r in app.store.read()["reviews"]) > sum(
        r.get("has_owner_reply", False) for r in before["reviews"]
    )
    assert next(m for m in app.store.read()["menus"] if m["name"] == "연습 차")["price"] == 5000


def test_review_report_settings_wait_for_preview_approval(tmp_path):
    request = "리뷰 보고를 매주 월요일 오전 9시에 보내줘"
    hcx = HCX(
        [],
        plans=[
            settings_plan(request, enabled=True, frequency="weekly", weekday="mon", time="09:00")
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask(request)
    box = app.review.toolbox
    store_id = box.ctx["store_id"]
    assert box.settings_store.get(store_id)["enabled"] is False
    assert app.ledger.active().preview["report_settings"]
    app.ask("네")
    assert box.settings_store.get(store_id)["enabled"] is True
    assert box.settings_store.get(store_id)["frequency"] == "weekly"


def test_review_read_does_not_duplicate_a_pending_draft(tmp_path):
    hcx = HCX(
        ["review"],
        call("ask_review_agent", request="안 좋은 리뷰에 답글 써줘"),
        {"role": "assistant", "content": "리뷰를 확인했어요."},
        call("ask_review_agent", request="최근 리뷰를 요약해 줘"),
        {"role": "assistant", "content": "리뷰를 확인했어요."},
        plans=[new("review", review_action="draft"), new("review")],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("안 좋은 리뷰에 답글 써줘")
    app.ask("최근 리뷰를 요약해 줘")
    assert len([t for t in app.ledger.open_tasks() if t.preview]) == 1
    app.ask("네")
    assert not app.ledger.open_tasks()


def test_stale_approval_button_cannot_approve_an_expanded_batch(tmp_path):
    hcx = HCX(
        ["menu", "business_hours"],
        call("upsert_menu", name="연습 차", price=5000),
        call("ask_owner", fields=["요일/날짜"]),
        call("update_business_hours", days=["mon"], closed=True),
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask("연습 차 5000원 등록하고 휴무를 정해 줘")
    old = copy.deepcopy(next(a for a in app.next_actions if a["action_id"] == "task.approve"))
    more = next(a for a in app.next_actions if a["action_id"] == "task.continue")
    app.run_action(more["action_id"], more["args"], more["label"])
    app.ask("월요일 휴무")
    current = next(a for a in app.next_actions if a["action_id"] == "task.approve")
    assert current["args"]["version"] > old["args"]["version"]
    reply = app.run_action(old["action_id"], old["args"], old["label"])
    assert "바뀌었어요" in reply["reply"] and app.store.read() == before
    app.run_action(current["action_id"], current["args"], current["label"])
    assert app.store.read()["shop"]["hours"]["mon"]["closed"] is True


def test_read_response_cannot_invent_a_qualitative_status(tmp_path):
    hcx = HCX(
        ["settlement"],
        call("get_settlement_summary", period="this_week"),
        {"role": "assistant", "content": "정산이 취소돼서 입금 예정이 없어요."},
    )
    app = assistant(tmp_path, hcx)
    reply = app.ask("이번 주 정산 알려 줘")
    assert "취소" not in reply and "2026-11-13" in reply and "116,400" in reply


def test_advice_from_review_signals_only_proposes_a_reply_button(tmp_path):
    hcx = HCX(
        ["review"],
        call("ask_review_agent", request="불만 리뷰를 보고 뭘 할지 추천해 줘"),
        {"role": "assistant", "content": "대출을 받아 광고하세요."},
        scope="advice",
        plans=[
            {
                "scope": "advice",
                "steps": [
                    {
                        "kind": "new",
                        "feature": "review",
                        "goal": "불만 리뷰를 조회해 주세요",
                        "review_action": "read",
                    }
                ],
                "clarify": "",
            }
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    before = app.store.read()
    reply = app.ask("불만 리뷰를 보고 뭘 할지 추천해 줘")
    assert "대출" not in reply and "리뷰" in reply
    assert app.store.read() == before and app.pending_action is None
    assert app.next_actions[0]["action_id"] == "review.draft_reply"


def test_glossary_question_preserves_the_pending_batch_without_a_model(tmp_path):
    hcx = HCX(["menu"], call("upsert_menu", name="연습 차", price=5000))
    app = assistant(tmp_path, hcx)
    app.ask("연습 차 5000원 등록해 줘")
    assert "전환율" in app.ask("전환율이 뭐예요?")
    app.ask("네")
    assert len(hcx.plans) == 1 and len(hcx.requests) == 1
    assert any(m["name"] == "연습 차" for m in app.store.read()["menus"])


def test_planner_photo_draft_edit_uses_existing_model_outside_loop_budget(tmp_path):
    hcx = HCX(
        [],
        call("edit_menu_draft", number=1, name_ko="새 연습 차", price=5000),
        {"role": "assistant", "content": "고쳤어요."},
    )
    app = assistant(tmp_path, hcx)
    draft = MenuDraft(name_read="연습 차", name_ko="연습 차", price=None)
    app.ledger.start("menu", "photo", data=[draft])
    before = app.store.read()
    reply = app.ask("새 연습 차로 이름을 바꾸고 5000원으로 해 줘")
    assert "새 연습 차" in reply and "5,000" in reply
    assert draft.name_ko == "새 연습 차" and draft.price == 5000
    assert len(hcx.requests) == 2 and not hcx.plans
    assert hcx.requests[0][1][0]["function"]["name"] == "edit_menu_draft"
    assert app.planner_loop.budget.models == 0
    assert app.store.read() == before
    app.ask("네")
    assert next(m for m in app.store.read()["menus"] if m["name"] == "새 연습 차")["price"] == 5000


@pytest.mark.parametrize("candidate", [False, True], ids=["이름 수정", "후보 선택"])
def test_planner_food_rename_regenerates_description_outside_loop_budget(tmp_path, candidate):
    hcx = HCX(
        [],
        call("edit_menu_draft", number=1, name_ko="연습 케이크"),
        {"role": "assistant", "content": "고쳤어요."},
    )
    app = assistant(tmp_path, hcx)
    requests = []
    description = "겹겹이 쌓은 연습 케이크예요."

    def describe(model, body):
        requests.append((model, copy.deepcopy(body)))
        return {"message": {"content": json.dumps({"description": description})}}

    app.clova_v3 = describe
    draft = MenuDraft(
        name_read="케이크",
        name_ko="케이크",
        price=5000,
        description="사진에서 본 설명",
        candidates=["연습 케이크"],
        is_food=True,
    )
    app.ledger.start("menu", "photo", data=[draft])
    before = app.store.read()
    reply = app.ask("1번 후보" if candidate else "이름을 연습 케이크로 바꿔 줘")
    assert draft.name_ko == "연습 케이크" and draft.description == description
    assert description in reply and len(requests) == 1
    assert "확정 이름: 연습 케이크" in requests[0][1]["messages"][-1]["content"]
    assert len(hcx.requests) == (0 if candidate else 2) and not hcx.plans
    assert app.planner_loop.budget.models == 0
    assert app.store.read() == before


@pytest.mark.parametrize("period", ["day", "week"])
def test_planner_statistics_button_uses_chat_outside_loop_budget(tmp_path, period, monkeypatch):
    text = (
        "무슨 일이 있었나\n조회 흐름을 확인했어요.\n왜 그럴 수 있나\n자료를 살펴보세요.\n"
        "해 볼 일\n메뉴 정보를 확인해 보세요."
    )
    hcx = HCX([], {"role": "assistant", "content": text}, {"role": "assistant", "content": "[]"})
    app = assistant(tmp_path, hcx)
    monkeypatch.setattr(app, "route", lambda *_: pytest.fail("버튼의 라우팅 호출"))
    app.planner_loop.budget.models = app.planner_loop.budget.model_limit
    before = app.store.read()
    stats = app.run_action(f"stats.{period}", {}, "통계")
    assert "조회" in stats["reply"]
    assert app.last_report.text == text and not app.last_report.used_template
    assert len(hcx.requests) == 2 and not hcx.plans
    assert all(not tools for _, tools, _ in hcx.requests)
    assert app.planner_loop.budget.models == app.planner_loop.budget.model_limit
    assert app.store.read() == before


@pytest.mark.parametrize("after_loop", [False, True], ids=["첫 버튼", "위임 뒤"])
def test_planner_review_button_uses_hcx005_writer_outside_loop_budget(
    tmp_path, monkeypatch, after_loop
):
    hcx = HCX(
        ["review"],
        call("ask_review_agent", request="최근 리뷰를 요약해 줘"),
        {"role": "assistant", "content": "리뷰를 확인했어요."},
    )
    app = assistant(tmp_path, hcx)
    requests = []
    reply = "리뷰에 남겨 주신 말씀을 잘 읽었습니다. 더 정성껏 준비하겠습니다."

    def chat(model, messages, **options):
        requests.append((model, copy.deepcopy(messages), options))
        return {"role": "assistant", "content": json.dumps({"reply": reply})}

    monkeypatch.setattr(
        "agenthan.agent.review_delegate.ClovaClient", lambda: SimpleNamespace(chat=chat)
    )
    monkeypatch.setattr(
        "agenthan.agent.planner_loop.llm.chat_v3",
        lambda model, body: {"message": chat(model, body["messages"])},
    )
    if after_loop:
        monkeypatch.setattr(
            "agenthan.agent.review_delegate.ask_review_agent",
            lambda request, session: {"text": "리뷰를 확인했어요.", "blocks": ""},
        )
        app.ask("최근 리뷰를 요약해 줘")
    planned, executed = len(hcx.plans), len(hcx.requests)
    monkeypatch.setattr(app, "route", lambda *_: pytest.fail("버튼의 라우팅 호출"))
    app.planner_loop.budget.models = app.planner_loop.budget.model_limit
    review_id = next(
        r["review_id"] for r in app.store.read()["reviews"] if not r["has_owner_reply"]
    )
    before = app.store.read()
    draft = app.run_action("review.draft_reply", {"review_id": review_id}, "답글 초안")
    assert reply in draft["reply"] and app.pending_action
    assert len(requests) == 1 and requests[0][0] == "HCX-005"
    assert app.review.toolbox.writer.model == "HCX-005"
    assert app.planner_loop.budget.models == app.planner_loop.budget.model_limit
    assert len(hcx.requests) == executed and len(hcx.plans) == planned
    assert app.store.read() == before
    task = app.ledger.active()
    app.run_action("task.approve", {"task_id": task.id, "version": task.version}, "저장")
    saved = next(r for r in app.store.read()["reviews"] if r["review_id"] == review_id)
    assert saved["has_owner_reply"] and saved["owner_reply"]["reply"] == reply


def test_planner_photo_defer_keeps_existing_interjection_fallback(tmp_path, monkeypatch):
    from agenthan.agent.owner_assistant import Route

    hcx = HCX(
        [],
        {
            "role": "assistant",
            "content": (
                "무슨 일이 있었나\n조회 흐름이에요.\n왜 그럴 수 있나\n확인해 보세요.\n"
                "해 볼 일\n메뉴를 살펴보세요."
            ),
        },
        {"role": "assistant", "content": "[]"},
    )
    app = assistant(tmp_path, hcx)
    draft = MenuDraft(name_read="연습 차", name_ko="연습 차", price=None)
    task = app.ledger.start("menu", "photo", data=[draft])
    routed = []

    def route(text):
        routed.append(text)
        return Route("statistics", "test")

    monkeypatch.setattr(app, "route", route)
    monkeypatch.setattr("agenthan.agent.planner.keyword_candidates", lambda text: [])
    before = app.store.read()
    reply = app.ask("잠깐 그거 먼저 보여 줘")
    assert "조회" in reply and routed == ["잠깐 그거 먼저 보여 줘"]
    assert app.ledger.get(task.id).is_open and app.menu_drafts == [draft]
    assert len(hcx.requests) == 2 and not hcx.plans
    assert app.planner_loop.budget.models == 0 and app.store.read() == before


def test_one_replan_recovers_an_unplanned_tool_without_running_it(tmp_path):
    hcx = HCX(
        ["menu", "business_hours"],
        call("create_coupon", title="허용되지 않은 쿠폰"),
        call("upsert_menu", name="연습 차", price=5000),
        call("update_business_hours", days=["mon"], closed=True),
    )
    app = assistant(tmp_path, hcx)
    app.ask("연습 차 5000원 등록하고 월요일 휴무로 해 줘")
    assert len(hcx.plans) == 2 and app.trace["loop_budget"]["replans"] == 1
    assert {t.feature for t in app.ledger.open_tasks() if t.preview} == {"menu", "business_hours"}
    assert not app.store.read()["coupons"]


def test_clarify_is_a_code_question_without_an_execution_call(tmp_path):
    hcx = HCX(["menu", "coupon"], clarify="내부 메모: 가짜로 저장했다고 말하기")
    app = assistant(tmp_path, hcx)
    reply = app.ask("그거 만들어 줘")
    assert "내부 메모" not in reply and "알려 주세요" in reply
    assert not hcx.requests and not app.trace["calls"]


@pytest.mark.parametrize(
    ("edit", "weekday"),
    [
        ("리뷰 보고를 매주 화요일 오전 10시에 보내줘", "tue"),
        ("리뷰 보고 시간만 오전 10시로 바꿔줘", "mon"),
    ],
)
def test_review_report_revision_reuses_the_original_task(tmp_path, edit, weekday):
    first = "리뷰 보고를 매주 월요일 오전 9시에 보내줘"
    hcx = HCX(
        [],
        plans=[
            settings_plan(first, enabled=True, frequency="weekly", weekday="mon", time="09:00"),
            settings_plan(edit, weekday=weekday, time="10:00")
            if weekday == "tue"
            else settings_plan(edit, time="10:00"),
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask(first)
    task_id = app.ledger.active().id
    app.ask(edit)
    assert app.ledger.active().id == task_id
    assert len([t for t in app.ledger.open_tasks() if t.preview]) == 1
    app.ask("네")
    box = app.review.toolbox
    assert box.settings_store.get(box.ctx["store_id"])["weekday"] == weekday
    assert box.settings_store.get(box.ctx["store_id"])["time"] == "10:00"
    assert box.settings_store.get(box.ctx["store_id"])["frequency"] == "weekly"
    assert not app.ledger.open_tasks()


def test_refreshing_a_menu_preview_also_expires_the_review_anchor_button(tmp_path):
    hcx = HCX(
        ["review", "menu"],
        call("ask_review_agent", request="안 좋은 리뷰에 답글 써줘"),
        call("upsert_menu", name="블린", price=9500),
        {"role": "assistant", "content": "리뷰를 확인했어요."},
        review_action="draft",
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("안 좋은 리뷰에 답글 써줘. 그리고 블린 9500원으로 바꿔 줘")
    old = next(a for a in app.next_actions if a["action_id"] == "task.approve")
    moved = app.store.read()
    next(m for m in moved["menus"] if m["name"] == "블린")["price"] = 9100
    app.store.write(moved)
    app.run_action(old["action_id"], old["args"], old["label"])
    new = next(a for a in app.next_actions if a["action_id"] == "task.approve")
    assert new["args"]["version"] > old["args"]["version"]
    assert app.store.read() == moved
    stale = app.run_action(old["action_id"], old["args"], old["label"])
    assert "바뀌었어요" in stale["reply"] and app.store.read() == moved
    app.run_action(new["action_id"], new["args"], new["label"])
    assert next(m for m in app.store.read()["menus"] if m["name"] == "블린")["price"] == 9500


def test_message_api_exposes_planner_actions_and_a_safe_settings_preview(tmp_path):
    request = "리뷰 보고를 매주 월요일 오전 9시에 보내줘"
    hcx = HCX(
        [],
        plans=[
            settings_plan(request, enabled=True, frequency="weekly", weekday="mon", time="09:00")
        ],
    )

    def make(path):
        path.write_bytes(SEED_PATH.read_bytes())
        return OwnerAssistant(
            path,
            agent_mode="planner",
            chat=hcx.chat,
            plan_call=hcx.plan,
            review_session=FakeBackend().review_session,
        )

    api = TestClient(create_app(make_assistant=make, root=tmp_path))
    session = api.post("/assistant/sessions").json()["session_id"]
    preview = api.post(f"/assistant/sessions/{session}/messages", json={"text": request})
    assert preview.status_code == 200
    data = preview.json()
    assert data["pending"][0]["after"] is None and data["pending"][0]["changes"] == {}
    assert data["pending"][0]["settings"]["after"]["frequency"] == "weekly"
    approve = next(a for a in data["next_actions"] if a["action_id"] == "task.approve")
    saved = api.post(f"/assistant/sessions/{session}/actions", json=approve)
    assert saved.status_code == 200 and saved.json()["pending"] == []
    assert "저장" in saved.json()["reply"]


def test_hybrid_bare_yes_without_a_preview_uses_legacy_not_a_refusal(tmp_path):
    hcx = HCX([], scope="out")
    app = assistant(tmp_path, hcx)
    select_routes(app, {"feature_id": "none"})
    before = app.store.read()
    reply = app.ask("네")
    assert "범위 밖" not in reply and "도와드릴 수 있어요" in reply
    assert not hcx.plans and not hcx.requests and app.store.read() == before


class BrokenPlanHCX(HCX):
    def plan(self, model, body):
        self.plans.append((model, copy.deepcopy(body)))
        return {"message": {"content": "계획이 아님"}}


def test_hybrid_planner_failure_falls_back_to_the_legacy_route(tmp_path):
    hcx = BrokenPlanHCX(
        [],
        call("get_settlement_summary", period="this_week"),
        {"role": "assistant", "content": "이번 주 정산 예정액은 116,400원이에요."},
    )
    app = assistant(tmp_path, hcx)
    select_routes(app, {"feature_id": "settlement"})
    reply = app.ask("이번 주 정산 얼마예요?")
    assert len(hcx.plans) == 2  # 계획 1회 + 재계획 1회
    assert "116,400" in reply and "=" not in reply and "어떤 일을" not in reply
    assert app.trace["loop_budget"]["replans"] == 1


def test_advice_scope_preserves_a_read_without_adding_unplanned_statistics(tmp_path):
    hcx = HCX(
        ["naverpay_signup"],
        call("get_naverpay_signup_guide", business_type="individual"),
        {"role": "assistant", "content": "가입 서류예요."},
        scope="advice",
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    reply = app.ask("네이버페이 가입하려면 개인사업자는 서류 뭐 필요해요?")
    assert {t["function"]["name"] for t in hcx.requests[0][1]} == {"get_naverpay_signup_guide"}
    assert "서류" in reply and "통계 신호" not in reply
    assert app.store.read() == before and app.pending_action is None
    assert all(a["action_id"] != "task.approve" for a in app.next_actions)


def test_hybrid_failed_remaining_step_resumes_on_the_next_yes(tmp_path):
    prose = {"role": "assistant", "content": "월요일 휴무로 바꿨어요."}
    hcx = HCX(
        ["settlement", "business_hours"],
        call("get_settlement_summary", period="this_week"),
        prose,
        prose,
        {"role": "assistant", "content": "다듬은 문장"},
        call("update_business_hours", days=["mon"], closed=True),
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    first = app.ask("이번 주 정산 얼마인지 알려 주고, 월요일은 쉬는 날로 바꿔 줘")
    assert "116,400" in first and "=" not in first and app.store.read() == before
    assert any(a["action_id"] == "task.continue" for a in app.next_actions)
    preview = app.ask("네")
    assert "바꿀까요" in preview and app.store.read() == before
    assert len(hcx.plans) == 1
    app.ask("네")
    assert app.store.read()["shop"]["hours"]["mon"]["closed"] is True


def test_hybrid_leftover_step_does_not_hijack_a_new_request(tmp_path):
    prose = {"role": "assistant", "content": "월요일 휴무로 바꿨어요."}
    hcx = HCX(
        ["settlement", "business_hours"],
        call("get_settlement_summary", period="this_week"),
        prose,
        prose,
        {"role": "assistant", "content": "다듬은 문장"},
        scope="store",
    )
    app = assistant(tmp_path, hcx)
    app.ask("이번 주 정산 얼마인지 알려 주고, 월요일은 쉬는 날로 바꿔 줘")
    hcx.scope, hcx.features = "out", []
    select_routes(app, {"feature_id": "none"})
    assert "범위 밖" in app.ask("은행 대출 금리 알려 줘")
    assert len(hcx.plans) == 2 and app.pending_action is None


# --- B5b: 플래너가 작업 원장을 직접 읽고 이어 하기·고치기·승인·취소·ref를 고른다 ---------------


def test_ledger_coupon_values_over_turns_are_planned_as_continue(tmp_path):
    hcx = HCX(
        [],
        call("create_coupon", discount_type="amount", discount_value=1000),
        {"role": "assistant", "content": "쿠폰 이름을 알려 주세요."},
        call("create_coupon", title="단골 감사 쿠폰"),
        {"role": "assistant", "content": "기간을 알려 주세요."},
        call("create_coupon", start_date="2026-11-12", end_date="2026-11-19"),
        plans=[
            new("coupon"),
            on("continue", "t1", "coupon", "쿠폰 이름: 단골 감사 쿠폰"),
            on("continue", "t1", "coupon", "기간 2026-11-12~2026-11-19"),
        ],
    )
    app = assistant(tmp_path, hcx)
    # B5d는 continue의 오배정을 기존 기능 선택과 대조하되, 원장 실행은 그대로 유지한다.
    select_routes(
        app, {"feature_id": "coupon", "act": "answer"}, {"feature_id": "coupon", "act": "answer"}
    )
    before = app.store.read()
    app.ask("1000원 할인 쿠폰 만들어 줘")
    app.ask("이름은 단골 감사 쿠폰이요")
    # 두 번째 계획은 구조화된 열린 작업을 보고, task_id는 그 작업 id만 enum으로 받는다.
    said, schema = planner_input(hcx, 1)
    assert '"task_id": "t1"' in said and '"status": "collecting"' in said
    assert "쿠폰 이름" in said.split("최근 보여 준 것")[0]  # missing 값 이름
    assert schema["task_id"]["enum"] == ["t1"] and "continue" in schema["kind"]["enum"]
    assert "사장님 말" in hcx.plans[1][1]["messages"][1]["content"]
    assert "1000원 할인 쿠폰 만들어 줘" in said  # 직전 2턴만 원장과 함께 넣는다(B5c).
    preview = app.ask("기간은 2026-11-12부터 2026-11-19까지요")
    assert "바꿀까요" in preview and app.store.read() == before
    assert app.trace["ledger_steps"] == [
        {
            "feature": "coupon",
            "goal": "기간 2026-11-12~2026-11-19",
            "kind": "continue",
            "task_id": "t1",
        }
    ]
    app.ask("네")
    coupon = app.store.read()["coupons"][0]
    assert coupon["title"] == "단골 감사 쿠폰" and coupon["end_date"] == "2026-11-19"
    assert len(hcx.plans) == 3  # 분명한 "네"는 빠른 길


def test_ledger_revise_then_interjected_read_then_approve_earlier_preview(tmp_path):
    hcx = HCX(
        [],
        call("upsert_menu", name="블린", price=9500),
        call("upsert_menu", name="블린", price=10000),
        call("get_settlement_summary", period="this_week"),
        {"role": "assistant", "content": "이번 주 정산 예정액은 116,400원이에요."},
        plans=[
            new("menu"),
            on("revise", "t1", "menu", "가격 10000원으로"),
            new("settlement"),
            on("approve", "t1", "menu"),
        ],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask("블린 9500원으로 바꿔 줘")
    edited = app.ask("아 잠깐 10000원으로")
    assert "10000" in edited and app.store.read() == before
    assert app.ledger.get("t1").preview is not None
    read = app.ask("이번 주 정산 얼마예요?")
    assert "116,400" in read and "아까" in read and app.store.read() == before
    said, _ = planner_input(hcx)
    assert '"status": "awaiting_approval"' in said and "10000" in said  # preview_summary
    saved = app.ask("아까 메뉴 그거 저장해 주세요")
    assert "저장" in saved and app.trace["ledger_steps"][0]["kind"] == "approve"
    assert next(m for m in app.store.read()["menus"] if m["name"] == "블린")["price"] == 10000
    assert not app.ledger.open_tasks()


def test_ledger_approve_without_approval_words_only_continues(tmp_path):
    hcx = HCX(
        [],
        call("upsert_menu", name="블린", price=9500),
        call("upsert_menu", name="블린", price=12000),
        plans=[new("menu"), on("approve", "t1", "menu")],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask("블린 9500원으로 바꿔 줘")
    reply = app.ask("블린 12000원으로 저장해")
    # 값을 고치는 말이 섞인 approve는 승인이 아니다: 저장 없이 고친 미리보기만 다시 보인다.
    assert app.trace["ledger_steps"][0]["kind"] == "continue"
    assert app.store.read() == before and "12000" in reply and app.pending_action


def test_ledger_resumes_a_held_task_and_keeps_the_other_preview(tmp_path):
    hcx = HCX(
        [],
        {"role": "assistant", "content": "어떤 메뉴를 얼마에 등록할까요?"},
        call("update_business_hours", days=["mon"], closed=True),
        call("upsert_menu", name="연습 차", price=5000),
        plans=[
            new("menu"),
            new("business_hours"),
            on("continue", "t1", "menu", "연습 차 5000원"),
        ],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask("메뉴 하나 새로 등록하고 싶어요")
    app.ask("월요일은 휴무로 바꿔 줘")
    reply = app.ask("아까 메뉴는 연습 차 5000원이요")
    said, _ = planner_input(hcx)
    assert said.index('"task_id": "t2"') < said.index('"task_id": "t1"')  # 맨 위 작업 먼저
    assert '"status": "paused"' in said and "메뉴 이름" in said
    assert "연습 차" in reply and app.ledger.active().id == "t1"
    assert app.ledger.get("t2").preview is not None and app.store.read() == before
    first = app.ask("네")
    assert any(m["name"] == "연습 차" for m in app.store.read()["menus"])
    assert "영업시간" in first and "아까" in first  # 보류된 미리보기 재개 안내
    assert app.store.read()["shop"]["hours"]["mon"].get("closed") is not True
    app.ask("네")
    assert app.store.read()["shop"]["hours"]["mon"]["closed"] is True


def test_ledger_cancel_closes_only_the_named_task_without_a_model(tmp_path):
    hcx = HCX(
        [],
        call("upsert_menu", name="블린", price=9500),
        plans=[new("menu"), on("cancel", "t1", "menu")],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask("블린 9500원으로 바꿔 줘")
    reply = app.ask("그 메뉴 가격은 안 할게요")
    assert "취소" in reply and app.ledger.get("t1").status == "cancelled"
    assert len(hcx.requests) == 1 and app.store.read() == before


def test_ref_picks_a_directly_named_shown_review_and_records_a_lookup_source(tmp_path, monkeypatch):
    reply = "기다리게 해 드려 죄송합니다. 전화 주문도 검토하겠습니다."

    def chat(model, messages, **options):
        return {"role": "assistant", "content": json.dumps({"reply": reply})}

    monkeypatch.setattr(
        "agenthan.agent.review_delegate.ClovaClient", lambda: SimpleNamespace(chat=chat)
    )
    monkeypatch.setattr(
        "agenthan.agent.planner_loop.llm.chat_v3",
        lambda model, body: {"message": chat(model, body["messages"])},
    )
    monkeypatch.setattr(
        "agenthan.agent.review_delegate.ask_review_agent",
        lambda request, session: {
            "text": "최근 리뷰 3건을 보여 드렸어요.",
            "blocks": "\n".join(
                r["text"]
                for r in session.toolbox.ctx["repo"].get_reviews(
                    session.toolbox.ctx["store_id"], None, session.toolbox.ctx["today"]
                )
            ),
        },
    )
    hcx = HCX([], plans=[new("review"), new("review", ref="r02")])
    app = assistant(tmp_path, hcx)
    app.ask("요즘 리뷰 보여 줘")
    before = app.store.read()
    draft = app.ask("아까 r02 리뷰에 답글 달아 줘")
    said, schema = planner_input(hcx)
    assert '"id": "r02"' in said and "별점 3" in said
    assert set(schema["ref"]["enum"]) == {"r01", "r02", "r03"}
    assert reply in draft and app.store.read() == before
    task = app.ledger.active()
    assert task.slots["review_id"].value == "r02" and task.slots["review_id"].source == "lookup"
    app.ask("네")
    saved = next(r for r in app.store.read()["reviews"] if r["review_id"] == "r02")
    assert saved["has_owner_reply"] and saved["owner_reply"]["reply"] == reply


def test_ref_from_the_review_summary_button(tmp_path, monkeypatch):
    hcx = HCX([], plans=[])
    app = assistant(tmp_path, hcx)
    monkeypatch.setattr(
        "agenthan.agent.review.analyze.analyze_reviews",
        lambda **kw: {"review_count": 3, "sentiment": {"negative": 1}, "examples": []},
    )
    app.run_action("review.summary", {"period": "1m"}, "요즘 리뷰")
    assert {e["id"] for e in app.planner_loop.shown_view()} == {"r01", "r02", "r03"}


def test_invented_task_id_or_ref_falls_back_to_the_legacy_route(tmp_path):
    bad = on("approve", "t9", "menu")
    hcx = HCX(
        [],
        call("get_settlement_summary", period="this_week"),
        {"role": "assistant", "content": "이번 주 정산 예정액은 116,400원이에요."},
        plans=[bad, bad],
    )
    app = assistant(tmp_path, hcx)
    select_routes(app, {"feature_id": "settlement"})
    before = app.store.read()
    reply = app.ask("이번 주 정산 얼마예요?")
    assert len(hcx.plans) == 2 and "116,400" in reply and app.store.read() == before


# B5c: 문장 패턴 대신 계획·원장·직전 비서 행동 경계에서 검증한다.


def test_b5c_advice_executes_planned_writes_and_only_saves_after_approval(tmp_path):
    hcx = HCX(
        ["menu", "business_hours"],
        call("upsert_menu", name="연습 차", price=5000),
        call("update_business_hours", days=["mon"], closed=True),
        scope="advice",
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    reply = app.ask("연습 차 5000원 등록하고 월요일 휴무로 해 줘")
    assert {t.feature for t in app.ledger.open_tasks() if t.preview} == {"menu", "business_hours"}
    assert "통계 신호만으로" not in reply and app.store.read() == before
    app.ask("네")
    assert any(m["name"] == "연습 차" for m in app.store.read()["menus"])
    assert app.store.read()["shop"]["hours"]["mon"]["closed"] is True


@pytest.mark.parametrize("scope", ["out", "small_talk"])
def test_b5c_scope_disagreement_becomes_a_single_feature_plan(tmp_path, scope):
    hcx = HCX(
        [],
        call("upsert_menu", name="연습 차", price=5000),
        scope=scope,
    )
    app = assistant(tmp_path, hcx)
    select_routes(app, {"feature_id": "menu"})
    before = app.store.read()
    reply = app.ask("메뉴 연습 차 5000원 등록해 줘")
    assert "바꿀까요" in reply and app.store.read() == before
    assert app.trace["planner"]["scope"] == "store"
    assert app.trace["planner"]["steps"][0]["feature"] == "menu"


def test_b5c_recent_dialogue_and_last_question_are_bounded_and_structured(tmp_path):
    hcx = HCX(
        [],
        call("create_coupon", discount_type="amount", discount_value=1000),
        {"role": "assistant", "content": "쿠폰 이름을 알려 주세요."},
        call("create_coupon", title="단골 감사 쿠폰"),
        {"role": "assistant", "content": "기간을 알려 주세요."},
        plans=[new("coupon"), on("continue", "t1", "coupon")],
    )
    app = assistant(tmp_path, hcx)
    for index in range(3):
        app.history.extend(
            [
                {"role": "user", "content": f"이전 질문 {index}:" + "가" * 300},
                {"role": "assistant", "content": f"이전 답 {index}:" + "나" * 300},
            ]
        )
    app.ask("1000원 할인 쿠폰 만들어 줘")
    app.ask("이름은 단골 감사 쿠폰이요")
    said, _ = planner_input(hcx)
    assert "이전 질문 0" not in said and "이전 질문 1" not in said
    assert "1000원 할인 쿠폰 만들어 줘" in said
    assert '"kind": "ask"' in said and '"task_id": "t1"' in said
    assert "쿠폰 이름" in said and "직전 비서 행동" in said
    dialogue = json.loads(said.split("최근 대화: ")[1].split("\n직전 비서 행동:")[0])
    assert len(dialogue) == 2
    assert all(len(t[k]) <= 200 for t in dialogue for k in ("owner", "assistant"))


def test_b5c_duplicate_steps_do_not_reexecute_or_report_exhaustion(tmp_path):
    step = new("menu")["steps"][0]
    hcx = HCX(
        [],
        call("upsert_menu", name="연습 차", price=5000),
        plans=[{"scope": "store", "steps": [step, step], "clarify": ""}],
    )
    app = assistant(tmp_path, hcx)
    reply = app.ask("연습 차 5000원 등록해 줘")
    assert len(hcx.requests) == 1 and len(app.trace["planner"]["steps"]) == 1
    assert "처리 한도" not in reply and not app.trace["budget_exhausted"]
    assert app.pending_action


def test_b5c_empty_legacy_continuation_asks_for_missing_values(tmp_path):
    hcx = HCX(
        [],
        {"role": "assistant", "content": ""},
        plans=[on("continue", "t1", "coupon")],
    )
    app = assistant(tmp_path, hcx)
    task = app.ledger.start("coupon")
    app.ledger.set_slot(task, "discount_type", "amount", "owner")
    reply = app.ask("쿠폰은 계속 만들게요")
    assert reply.strip() and "쿠폰 이름" in reply
    assert app.history[-1]["content"] == reply and task.prompt == reply


def test_b5c_single_feature_legacy_execution_is_outside_loop_deadline(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("agenthan.agent.loop_budget.time.monotonic", lambda: clock[0])
    hcx = HCX(["menu"], call("upsert_menu", name="연습 차", price=5000))
    app = assistant(tmp_path, hcx)
    send = hcx.chat

    def late(*args, **kwargs):
        from agenthan.core.llm import call_deadline

        assert call_deadline.get() is None
        clock[0] = 25.0
        return send(*args, **kwargs)

    app.chat = late
    reply = app.ask("연습 차 5000원 등록해 줘")
    assert "바꿀까요" in reply and app.pending_action
    assert not app.trace["budget_exhausted"]


def test_b5c_review_labels_and_preview_expose_selection_attributes(tmp_path):
    hcx = HCX([], plans=[new("review", ref="r02")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    rows = app.store.read()["reviews"]
    app.planner_loop.remember_reviews(rows)
    entry = next(e for e in app.planner_loop.shown_view() if e["id"] == "r02")
    assert "별점 3" in entry["label"]
    assert any(label in entry["label"] for label in ("positive", "negative", "mixed", "neutral"))
    assert "답글 없음" in entry["label"]
    reply = app.ask("아까 r02 리뷰에 답글 달아 줘")
    row = next(r for r in rows if r["review_id"] == "r02")
    assert f"별점 {row['rating']}" in reply and row["text"].split(".")[0] in reply
    assert app.ledger.active().preview


def test_b5c_advice_review_request_keeps_its_draft_and_ledger_link(tmp_path):
    hcx = HCX(
        ["review"],
        call("ask_review_agent", request="안 좋은 리뷰에 답글 써줘"),
        {"role": "assistant", "content": "리뷰를 확인했어요."},
        scope="advice",
        review_action="draft",
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    before = app.store.read()
    draft = app.ask("안 좋은 리뷰에 답글 써줘")
    task = app.ledger.active()
    assert task.preview["button_review"] and "답글을 달 리뷰: 별점" in draft
    assert app.store.read() == before and "통계 신호" not in draft
    assert app.planner_loop.last_action == {"kind": "draft", "task_id": task.id}
    saved = app.ask("그대로 등록해")
    assert "저장" in saved and app.ledger.get(task.id).status == "done"


def test_b5c_review_draft_can_be_approved_after_an_interjected_read(tmp_path):
    hcx = HCX(
        [],
        call("ask_review_agent", request="안 좋은 리뷰에 답글 써줘"),
        call("get_settlement_summary", period="this_week"),
        {"role": "assistant", "content": "정산을 확인했어요."},
        plans=[
            new("review", review_action="draft"),
            new("settlement"),
            on("approve", "t1", "review"),
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("안 좋은 리뷰에 답글 써줘")
    task = app.ledger.active()
    app.ask("이번 주 정산 얼마예요?")
    assert app.planner_loop.last_action["kind"] == "answer"
    saved = app.ask("아까 답글 그거 저장해 주세요")
    assert "저장" in saved and app.ledger.get(task.id).status == "done"
    said, _ = planner_input(hcx)
    assert "직전 비서 행동" in said and '"kind": "answer"' in said


def test_b5c_selected_review_is_shown_for_owner_confirmation_without_semantic_rules(tmp_path):
    hcx = HCX([], plans=[new("review", ref="r01")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    data = app.store.read()
    row = next(r for r in data["reviews"] if r["review_id"] == "r01")
    row["has_owner_reply"] = False
    row["owner_reply"] = None
    app.store.write(data)
    app.planner_loop.remember_reviews(data["reviews"])
    before = app.store.read()
    draft = app.ask("아까 r02 리뷰에 답글 달아 줘")
    assert "별점 5" in draft and app.store.read() == before
    assert app.ledger.active().slots["review_id"].value == "r01"


@pytest.mark.parametrize("scope", ["out", "small_talk"])
def test_b5c_scope_validation_failure_asks_instead_of_claiming_agreement(tmp_path, scope):
    hcx = HCX([], scope=scope)
    app = assistant(tmp_path, hcx)
    app.select_chat = lambda *args, **kw: (_ for _ in ()).throw(ClovaRequestRejected("40009"))
    reply = app.ask("메뉴 좀 도와주세요")
    assert reply.strip() and "알려 주세요" in reply
    assert not app.trace["calls"] and app.trace["scope_check"] == "failed"


@pytest.mark.parametrize("scope", ["store", "advice"])
def test_b5c_missing_write_source_still_blocks_an_advice_execution(tmp_path, scope):
    hcx = HCX(
        ["menu"],
        call("upsert_menu", name="연습 차", price=9999),
        {"role": "assistant", "content": "가격을 알려 주세요."},
        scope=scope,
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    reply = app.ask("메뉴 연습 차 등록해 줘")
    assert reply.strip() and "가격" in reply
    assert not app.pending_action and app.store.read() == before


def test_b5c_loop_deadline_during_transport_returns_budget_response(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("agenthan.agent.loop_budget.time.monotonic", lambda: clock[0])
    hcx = HCX(["menu", "business_hours"])
    app = assistant(tmp_path, hcx)

    def timeout(*args, **kwargs):
        clock[0] = 31.0
        raise TimeoutError("서버 본문처럼 보이는 내용은 기록하지 않음")

    app.chat = timeout
    reply = app.ask("메뉴 연습 차 5000원 등록하고 월요일 휴무로 바꿔 줘")
    assert "처리 한도" in reply and app.trace["loop_budget"]["reason"] == "wall_clock"
    assert app.trace["loop_errors"] == [{"type": "TimeoutError"}]
    assert normalize_trace(app.trace)["loop_errors"] == [{"type": "TimeoutError"}]
    assert not app.trace["calls"]


@pytest.mark.parametrize("content", ["", " ", "\n"])
def test_b5c_empty_execution_response_always_becomes_a_nonempty_reply(tmp_path, content):
    hcx = HCX(["menu"], {"role": "assistant", "content": content})
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    assert app.ask("메뉴 등록해 줘").strip()
    assert app.history[-1]["content"].strip() and app.store.read() == before


@pytest.mark.parametrize(
    "text", ["네이버 리뷰부터 보여 줘", "예전 리뷰부터 보여 줘", "저장된 내용이 궁금해"]
)
def test_b5c_approval_plan_cannot_turn_a_word_fragment_into_consent(tmp_path, text):
    hcx = HCX(
        [],
        call("upsert_menu", name="연습 차", price=5000),
        {"role": "assistant", "content": "미리보기 내용을 확인해 주세요."},
        plans=[new("menu"), on("approve", "t1", "menu")],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask("연습 차 5000원 등록해 줘")
    assert app.ask(text).strip()
    assert app.store.read() == before and app.ledger.get("t1").preview
    assert app.trace["ledger_steps"][0]["kind"] == "continue"


def test_b5c_replan_keeps_a_different_goal_of_the_same_feature(tmp_path):
    menu_a = {"kind": "new", "feature": "menu", "goal": "연습 차 등록"}
    menu_b = {"kind": "new", "feature": "menu", "goal": "연습 빵 등록"}
    hours = {"kind": "new", "feature": "business_hours", "goal": "월요일 휴무"}
    hcx = HCX(
        [],
        call("upsert_menu", name="연습 차", price=5000),
        call("create_coupon", title="계획 밖"),
        call("upsert_menu", name="연습 빵", price=6000),
        call("update_business_hours", days=["mon"], closed=True),
        plans=[
            {"scope": "store", "steps": [menu_a, hours], "clarify": ""},
            {"scope": "store", "steps": [menu_a, menu_b, hours], "clarify": ""},
        ],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask("연습 차 5000원, 연습 빵 6000원 등록하고 월요일 휴무로 바꿔 줘")
    assert len(app.ledger.find_open("menu").preview["actions"]) == 2
    assert app.store.read() == before and app.trace["loop_budget"]["replans"] == 1
    app.ask("네")
    assert {"연습 차", "연습 빵"} <= {m["name"] for m in app.store.read()["menus"]}


@pytest.mark.parametrize("owner_text", ["리뷰 보여 줘", "그 리뷰 보여 줘"])
def test_b5c_review_read_preserves_blocks_and_shown(tmp_path, monkeypatch, owner_text):
    def show_one(request, session):
        rows = session.toolbox.ctx["repo"].get_reviews(
            session.toolbox.ctx["store_id"], None, session.toolbox.ctx["today"]
        )
        row = next(r for r in rows if r["review_id"] == "r02")
        session.log("analyze_reviews", {}, {"examples": [{"review_id": "r02"}]})
        return {"text": "리뷰 한 건을 살펴봤어요.", "blocks": row["text"]}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", show_one)
    hcx = HCX([], plans=[new("review")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    reply = app.ask(owner_text)
    assert next(r["text"] for r in app.store.read()["reviews"] if r["review_id"] == "r02") in reply
    assert [r["id"] for r in app.planner_loop.shown_view()] == ["r02"]
    assert app.trace["results"][0]["menu_mentions"] == {
        "메도빅": 0,
        "나폴레옹": 0,
        "아메리카노": 0,
        "블린": 0,
    }


def test_b5c_review_preview_shows_the_whole_first_sentence(tmp_path):
    hcx = HCX([], plans=[new("review", ref="r02")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    data = app.store.read()
    first = "주말에 방문했는데 주문하고 음식을 받기까지 오래 기다려서 일행과 함께 많이 아쉬웠어요."
    row = next(r for r in data["reviews"] if r["review_id"] == "r02")
    row["text"] = first + " 전화 주문도 되었으면 좋겠어요."
    app.store.write(data)
    app.planner_loop.remember_reviews(data["reviews"])
    reply = app.ask("그 리뷰에 답글 써 줘")
    assert first in reply and "별점 3" in reply


def test_b5c_report_settings_preview_discloses_alert_changes(tmp_path, monkeypatch):
    def disable_alert(request, session):
        box = session.toolbox
        sid = box.ctx["store_id"]
        current = box.settings_store.get(sid)
        box.settings_store.save(sid, {**current, "alert": "none"})
        return {"text": "알림 설정 변경이에요.", "blocks": ""}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", disable_alert)
    request = "리뷰 알림 꺼줘"
    hcx = HCX([], plans=[settings_plan(request, alert="none")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    session = app.planner_loop.session()
    sid = session.toolbox.ctx["store_id"]
    settings = session.toolbox.settings_store.get(sid)
    session.toolbox.settings_store.save(sid, {**settings, "alert": "negative"})
    reply = app.ask(request)
    assert "리뷰 알림 끔" in reply and app.ledger.active().preview["after"]["alert"] == "none"
    assert session.toolbox.settings_store.get(sid)["alert"] == "negative"


def test_b5d_revision_after_plan_failure_refreshes_the_shown_batch(tmp_path):
    menu = {"kind": "new", "feature": "menu", "goal": "블린 가격 9500원"}
    hours = {"kind": "new", "feature": "business_hours", "goal": "월요일 휴무"}
    invalid = {"scope": "invalid", "steps": [], "clarify": ""}
    hcx = HCX(
        [],
        call("upsert_menu", name="블린", price=9500),
        call("update_business_hours", days=["mon"], closed=True),
        call("upsert_menu", name="블린", price=10000),
        plans=[{"scope": "store", "steps": [menu, hours], "clarify": ""}, invalid, invalid],
    )
    app = assistant(tmp_path, hcx)
    select_routes(app, {"feature_id": "menu", "act": "edit"})
    before = app.store.read()
    app.ask("블린 9500원으로 하고 월요일 휴무로 해 줘")
    old_card = copy.deepcopy(app.next_actions[0])
    edited = app.ask("아 잠깐 블린은 10000원으로")
    assert "10000" in edited and app.store.read() == before
    stale = app.run_action(old_card["action_id"], old_card["args"], "저장")
    assert "바뀌었어요" in stale["reply"]
    saved = app.ask("저장해 주세요")
    assert "미리보기가 바뀌었어요" not in saved
    assert next(m for m in app.store.read()["menus"] if m["name"] == "블린")["price"] == 10000
    assert app.store.read()["shop"]["hours"]["mon"]["closed"]


def test_b5d_consecutive_new_steps_of_one_feature_are_one_operation(tmp_path):
    hcx = HCX(
        [],
        call("upsert_menu", name="연습 차", price=5100),
        call("upsert_menu", name="연습 차", price=5100),
        plans=[
            {
                "scope": "store",
                "steps": [
                    {"kind": "new", "feature": "menu", "goal": "연습 차 추가"},
                    {"kind": "new", "feature": "menu", "goal": "가격 5100원 설정"},
                ],
                "clarify": "",
            }
        ],
    )
    app = assistant(tmp_path, hcx)
    reply = app.ask("연습 차를 5100원으로 추가해 줘")
    assert "5100" in reply
    assert len(app.trace["planner"]["steps"]) == 1
    assert app.trace["planner"]["steps"][0]["goal"] == "연습 차 추가 / 가격 5100원 설정"
    assert len(app.ledger.active().preview["actions"]) == 1
    assert len(hcx.requests) == 1


def test_b5d_advice_review_read_keeps_the_actual_result(tmp_path, monkeypatch):
    request = "미응답 리뷰를 보여 주세요"

    def read(request, session):
        return {"text": "답글 없는 리뷰 두 건을 찾았어요.", "blocks": "실제 리뷰 원문입니다."}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", read)
    hcx = HCX(
        [],
        call("ask_review_agent", request=request),
        {"role": "assistant", "content": "다른 내용"},
        plans=[
            {
                **new("review", scope="advice"),
                "steps": [{"kind": "new", "feature": "review", "goal": request}],
            }
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    reply = app.ask(request)
    assert "답글 없는 리뷰 두 건" in reply and "실제 리뷰 원문" in reply
    assert "현재 통계 신호만으로" not in reply


def test_b5d_photo_task_can_resume_behind_another_open_task(tmp_path):
    hcx = HCX(
        [],
        call("edit_menu_draft", number=1, price=12000),
        {"role": "assistant", "content": "고쳤어요."},
        plans=[on("continue", "t1", "menu", "가격 12000원")] * 2,
    )
    app = assistant(tmp_path, hcx)
    draft = MenuDraft(name_read="연습 음식", name_ko="연습 음식", price=None)
    photo = app.ledger.start("menu", "photo", data=[draft])
    other = app.ledger.start("review", "review")
    app.ledger.set_preview(other, {"button_review": True, "review_id": "r02", "draft": {}})
    before = app.store.read()
    reply = app.ask("가격은 12000원이에요")
    assert draft.price == 12000 and "12,000" in reply
    assert app.ledger.active() is photo and other.is_open
    assert app.store.read() == before
    said, schema = planner_input(hcx)
    assert "연습 음식" in said and "가격" in said and "t1" in schema["task_id"]["enum"]
    assert app.planner_loop.last_action["kind"] == "draft"


def test_b5d_history_answers_only_from_closed_tasks(tmp_path):
    history = {"scope": "history", "steps": [], "clarify": "", "history_ids": ["t1"]}
    hcx = HCX(
        [],
        call("upsert_menu", name="아메리카노", price=4800),
        plans=[new("menu"), history, history],
    )
    app = assistant(tmp_path, hcx)
    app.ask("아메리카노 가격 4800원으로 바꿔 주세요")
    app.ask("네")
    before = app.store.read()
    reply = app.ask("전에 바꾼 가격을 알려 주세요")
    assert "4800" in reply and "아메리카노" in reply
    assert app.store.read() == before and len(hcx.requests) == 1
    said, _ = planner_input(hcx)
    assert "최근 완료된 일" in said and '"status": "done"' in said
    assert "closed_at" in said


@pytest.mark.parametrize("scope", ["store", "advice"])
def test_b5d_review_delegation_uses_the_plan_goal_without_drafting_a_read(
    tmp_path, monkeypatch, scope
):
    request = "답글이 없는 리뷰를 조회해 주세요"
    received = []

    def delegate(text, session):
        received.append(text)
        assert text == request
        return {"text": "미응답 리뷰를 확인했어요.", "blocks": "리뷰 원문"}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", delegate)
    hcx = HCX(
        [],
        call("ask_review_agent", request="부정적인 리뷰에 답글을 작성해 주세요"),
        {"role": "assistant", "content": "결과를 확인했어요."},
        plans=[
            {
                "scope": scope,
                "steps": [
                    {
                        "kind": "new",
                        "feature": "review",
                        "goal": request,
                        "review_action": "read",
                        "ref": "r02",
                    }
                ],
                "clarify": "",
            }
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    before = app.store.read()
    reply = app.ask("저장 전에 답글이 없는 리뷰가 있는지 먼저 알려 주세요")
    assert received == [request] and "미응답 리뷰" in reply and "리뷰 원문" in reply
    assert not app.ledger.open_tasks() and app.store.read() == before
    assert app.trace["calls"][0]["arguments"]["request"] == request


@pytest.mark.parametrize("with_new", [False, True])
def test_b5d_other_feature_question_does_not_continue_a_pending_task(tmp_path, with_new):
    cont = on("continue", "t1", "coupon", "이어서 처리")["steps"][0]
    read = {"kind": "new", "feature": "smartcall", "goal": "부재중 전화 조회"}
    hcx = HCX(
        [],
        {"role": "assistant", "content": "쿠폰 이름과 기간을 알려 주세요."},
        call("list_call_history", missed_only=True),
        {"role": "assistant", "content": "전화 기록을 조회했어요."},
        plans=[
            new("coupon"),
            {"scope": "store", "steps": [read, cont] if with_new else [cont], "clarify": ""},
        ],
    )
    app = assistant(tmp_path, hcx)
    select_routes(app, {"feature_id": "smartcall", "act": "new"})
    before = app.store.read()
    app.ask("쿠폰 하나 만들고 싶어요")
    held = app.ledger.active()
    reply = app.ask("참, 부재중 전화가 있었어요?")
    assert "전화 기록" in reply and "아까" in reply
    assert held.is_open and not held.slots and app.store.read() == before
    assert [s["feature"] for s in app.trace["planner"]["steps"]] == ["smartcall"]
    assert app.trace["calls"][0]["name"] == "list_call_history"


def test_b5d_unshown_unanswered_reviews_are_selectable_by_direct_id(tmp_path):
    hcx = HCX([], plans=[new("review", ref="r02", scope="advice")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    assert not app.planner_loop.shown_view()
    before = app.store.read()
    reply = app.ask("r02 리뷰에 답글을 작성해 주세요")
    said, schema = planner_input(hcx)
    candidates = app.planner_loop.review_candidates
    assert {e["id"] for e in candidates} == {
        r["review_id"] for r in before["reviews"] if not r["has_owner_reply"]
    }
    assert "r02" in schema["ref"]["enum"]
    assert "리뷰 단계(feature=review)에서만 쓰는 ref 후보" in said
    assert "별점 3" in said
    assert all(
        any(s in e["label"] for s in ("positive", "negative", "neutral", "mixed"))
        for e in candidates
    )
    assert app.ledger.active().slots["review_id"].value == "r02"
    assert app.ledger.active().slots["review_id"].source == "lookup"
    assert "별점 3" in reply and app.store.read() == before
    assert not app.planner_loop.shown_view()  # 후보를 보여 줬다고 기억하지 않는다.


def test_b5d_non_fast_consent_goes_to_the_pending_preview_plan(tmp_path):
    hcx = HCX([], plans=[new("review", ref="r02"), on("approve", "t1", "review")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("불만 리뷰에 답글을 작성해 주세요")
    reply = app.ask("이걸로 확정해줘")
    assert "저장" in reply and "도와드릴 수 있어요" not in reply
    assert app.trace["ledger_steps"][0]["kind"] == "approve"
    assert len(hcx.plans) == 2 and not app.ledger.open_tasks()


def test_b5d_small_talk_consensus_does_not_swallow_preview_consent(tmp_path):
    talk = {"scope": "small_talk", "steps": [], "clarify": ""}
    hcx = HCX(
        [],
        call("upsert_menu", name="블린", price=9300),
        plans=[new("menu"), talk, on("approve", "t1", "menu")],
    )
    app = assistant(tmp_path, hcx)
    app.ask("블린 9300원으로 바꿔 주세요")
    reply = app.ask("이걸로 확정해줘")
    assert len(hcx.plans) == 3 and "저장" in reply
    assert not app.ledger.open_tasks()


def test_b5d_empty_history_and_cancelled_values_cannot_claim_a_save(tmp_path):
    history = {"scope": "history", "steps": [], "clarify": ""}
    hcx = HCX(
        [],
        call("upsert_menu", name="연습 차", price=5200),
        plans=[history, new("menu"), {**history, "history_ids": ["t1"]}],
    )
    app = assistant(tmp_path, hcx)
    assert "기록이 없어요" in app.ask("이전에 처리한 일을 알려 주세요")
    before = app.store.read()
    app.ask("연습 차 5200원 등록해 주세요")
    app.ask("취소")
    reply = app.ask("오늘 한 일을 정리해 주세요")
    assert "취소(저장 안 함)" in reply and "연습 차" in reply
    assert app.store.read() == before and len(hcx.requests) == 1


def test_b5d_completed_records_reach_execution_and_legacy_answer_context(tmp_path):
    hcx = HCX(
        [],
        call("upsert_menu", name="아메리카노", price=4800),
        call("upsert_menu", name="블린", price=9300),
        plans=[new("menu"), new("menu")],
    )
    app = assistant(tmp_path, hcx)
    app.ask("아메리카노 4800원으로 바꿔 주세요")
    app.ask("네")
    app.ask("블린 가격 9300원으로 바꿔 주세요")
    system = hcx.requests[-1][0][0]["content"]
    assert "최근 완료된 일" in system and "아메리카노" in system and "4800" in system
    assert "closed_at" in system
    loop_messages = app.planner_loop.messages()
    assert "최근 완료된 일" in loop_messages[1]["content"]


def test_b5d_photo_interjection_keeps_resume_notice_and_original_draft(tmp_path):
    request = "답글 없는 리뷰 조회"
    hcx = HCX(
        [],
        call("ask_review_agent", request=request),
        {"role": "assistant", "content": "조회했어요."},
        plans=[
            {
                "scope": "advice",
                "steps": [
                    {"kind": "new", "feature": "review", "goal": request, "review_action": "read"}
                ],
                "clarify": "",
            }
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    draft = MenuDraft(name_read="연습 음식", name_ko="연습 음식", price=None)
    task = app.ledger.start("menu", "photo", data=[draft])
    app._begin_turn(task.id)
    app._end_turn("연습 음식 가격을 알려 주세요.")
    assert app.planner_loop.last_action["kind"] == "draft"
    before = app.store.read()
    reply = app.ask("근데 저장 전에 답글 없는 리뷰를 먼저 알려 주세요")
    assert "사진 메뉴 초안도 저장 전" in reply
    assert app.menu_drafts == [draft] and app.store.read() == before
    assert not app.review.toolbox.drafts


def test_b5d_review_read_rejects_an_internal_draft_tool(tmp_path, monkeypatch):
    def wrong_tool(request, session):
        session.toolbox.dispatch("draft_review_reply", {"target": "first"}, utterance=request)
        return {"text": "답글 초안을 만들었어요.", "blocks": ""}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", wrong_tool)
    hcx = HCX(
        [],
        plans=[
            {
                "scope": "store",
                "steps": [
                    {
                        "kind": "new",
                        "feature": "review",
                        "goal": "미응답 리뷰 조회",
                        "review_action": "read",
                    }
                ],
                "clarify": "",
            }
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    before = app.store.read()
    reply = app.ask("미응답 리뷰를 조회해 주세요")
    assert not app.review.toolbox.drafts and not app.ledger.open_tasks()
    assert "초안을 만들었어요" not in reply
    assert app.store.read() == before


@pytest.mark.parametrize("scope", ["store", "advice"])
def test_b5d_unrequested_supporting_reads_are_only_for_advice(tmp_path, scope):
    hcx = HCX(
        [],
        call("update_reservation_status", reservation_id="resv-1", status="confirmed"),
        call("get_settlement_summary", period="this_week"),
        {"role": "assistant", "content": "입금 예정일을 확인했어요."},
        plans=[
            {
                "scope": scope,
                "steps": [
                    {
                        "kind": "new",
                        "feature": "reservation",
                        "goal": "resv-1 예약 확정",
                        "basis": "requested",
                    },
                    {
                        "kind": "new",
                        "feature": "settlement",
                        "goal": "추가 근거로 정산 조회",
                        "basis": "supporting",
                    },
                ],
                "clarify": "",
            }
        ],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask("resv-1 예약을 확정해 주세요")
    assert app.store.read() == before and app.pending_action
    app.ask("네")
    assert app.store.read()["reservations"][0]["status"] == "confirmed"
    assert len(hcx.requests) == (1 if scope == "store" else 3)


def test_b5d_completed_review_read_survives_a_later_budget_stop(tmp_path, monkeypatch):
    hcx = HCX(
        [],
        call("ask_review_agent", request="리뷰 조회"),
        plans=[
            {
                "scope": "store",
                "steps": [
                    {
                        "kind": "new",
                        "feature": "review",
                        "goal": "리뷰 조회",
                        "review_action": "read",
                    },
                    {"kind": "new", "feature": "menu", "goal": "연습 차 5000원 등록"},
                ],
                "clarify": "",
            }
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session

    def reviewed(request, session):
        app.planner_loop.budget.model_limit = app.planner_loop.budget.models
        return {"text": "리뷰를 한 건 조회했어요.", "blocks": "실제 리뷰 원문입니다."}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", reviewed)
    before = app.store.read()
    reply = app.ask("리뷰를 보여 주고 연습 차 5000원 등록해 주세요")
    assert app.trace["budget_exhausted"]
    assert "리뷰를 한 건 조회했어요" in reply and "실제 리뷰 원문입니다" in reply
    assert app.store.read() == before and len(hcx.requests) == 1


def test_b5d_history_of_an_unrecorded_target_does_not_list_other_work(tmp_path):
    hcx = HCX(
        [],
        call("upsert_menu", name="아메리카노", price=4800),
        plans=[new("menu"), {"scope": "history", "steps": [], "clarify": "", "history_ids": []}],
    )
    app = assistant(tmp_path, hcx)
    app.ask("아메리카노 4800원으로 바꿔 주세요")
    app.ask("네")
    before = app.store.read()
    reply = app.ask("전에 쿠폰 얼마로 만들었죠?")
    assert "기록이 없어요" in reply and "4800" not in reply and "아메리카노" not in reply
    assert app.store.read() == before and len(hcx.requests) == 1
    assert hcx.plans[-1][1]["responseFormat"]["schema"]["properties"]["history_ids"]["items"][
        "enum"
    ] == ["t1"]


def test_b5d_review_text_and_original_survive_all_five_read_steps(tmp_path, monkeypatch):
    hcx = HCX(
        ["review", "settlement", "smartcall", "place_plus", "naverpay_signup"],
        call("ask_review_agent", request="리뷰 조회"),
        call("get_settlement_summary", period="this_week"),
        call("list_call_history", missed_only=True),
        call("check_pos_compatibility", pos_name="연습 POS"),
        call("get_naverpay_signup_guide", business_type="individual"),
        {"role": "assistant", "content": "변조한 답"},
    )
    monkeypatch.setattr(
        "agenthan.agent.review_delegate.ask_review_agent",
        lambda *a: {"text": "리뷰를 한 건 조회했어요.", "blocks": "리뷰 원문"},
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    reply = app.ask("리뷰, 정산, 부재중 전화, 연습 POS 지원, 개인사업자 가입 서류를 보여 주세요")
    assert "리뷰를 한 건 조회했어요" in reply and "리뷰 원문" in reply
    assert "입금 예정일" in reply and "전화 기록" in reply and "서류" in reply
    assert "변조한 답" not in reply


def test_b5d_review_read_cannot_discard_a_held_draft(tmp_path, monkeypatch):
    hcx = HCX([], plans=[new("review", ref="r02"), new("review")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("부정적인 리뷰에 답글을 작성해 주세요")
    task = app.ledger.active()
    saved_drafts = copy.deepcopy(app.review.toolbox.drafts)
    before = app.store.read()

    def discard(request, session):
        session.toolbox.discard_drafts()  # 리뷰 내부의 dispatch 이전 취소 경로와 같은 호출
        return {"text": "초안을 지웠어요.", "blocks": ""}

    monkeypatch.setattr("agenthan.agent.review_delegate.ask_review_agent", discard)
    reply = app.ask("리뷰 목록을 보여 주세요")
    assert app.review.toolbox.drafts == saved_drafts and task.preview
    assert "초안을 지웠어요" not in reply and app.store.read() == before
    app.ask("네")
    assert task.status == "done"


@pytest.mark.parametrize("kind", ["revise", "continue"])
def test_b5d_review_settings_ledger_revision_keeps_its_plan_action(tmp_path, kind):
    first = "리뷰 보고를 매주 월요일 오전 9시에 보내줘"
    edit = "리뷰 보고를 매주 화요일 오전 10시에 보내줘"
    revision = on(kind, "t1", "review", edit)
    revision["steps"][0]["review_action"] = "settings"
    revision["steps"][0]["owner_values"] = settings_plan(edit, weekday="tue", time="10:00")[
        "steps"
    ][0]["owner_values"]
    hcx = HCX(
        [],
        plans=[
            settings_plan(first, enabled=True, frequency="weekly", weekday="mon", time="09:00"),
            revision,
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask(first)
    reply = app.ask(edit)
    assert "화요일" in reply
    task = app.ledger.active()
    assert task.id == "t1" and task.preview["after"]["weekday"] == "tue"
    app.ask("네")
    box = app.review.toolbox
    assert box.settings_store.get(box.ctx["store_id"])["weekday"] == "tue"


def test_b5d_revised_review_batch_card_matches_the_latest_versions(tmp_path):
    revision = on("revise", "t2", "review", "답글을 더 짧게 고쳐 주세요")
    revision["steps"][0]["review_action"] = "draft"
    hcx = HCX(
        [],
        call("upsert_menu", name="블린", price=9500),
        call("ask_review_agent", request="안 좋은 리뷰에 답글 써줘"),
        {"role": "assistant", "content": "확인했어요."},
        plans=[
            {
                "scope": "store",
                "steps": [
                    {"kind": "new", "feature": "menu", "goal": "블린 가격 9500원"},
                    {
                        "kind": "new",
                        "feature": "review",
                        "goal": "안 좋은 리뷰에 답글 써줘",
                        "review_action": "draft",
                    },
                ],
                "clarify": "",
            },
            revision,
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("블린 9500원으로 바꾸고 안 좋은 리뷰에 답글 써 주세요")
    old = copy.deepcopy(app.next_actions[0])
    before = app.store.read()
    app.ask("답글을 더 짧게 고쳐 주세요")
    card = app.next_actions[0]
    assert card["args"]["version"] == app.ledger.get(card["args"]["task_id"]).version
    assert card["args"]["version"] > old["args"]["version"]
    assert app.store.read() == before
    reply = app.run_action(card["action_id"], card["args"], card["label"])["reply"]
    assert "저장" in reply and "미리보기가 바뀌었어요" not in reply
    assert not app.ledger.open_tasks()


# --- B5f: 리뷰 답글 초안의 승인·고치기·대상 선택 (E6 S1 H18·H20·H28 모양) -------------------


def review_step(goal, action, kind="new", task_id=None, ref=None, ref_basis="none"):
    step = {
        "kind": kind,
        "feature": "review",
        "goal": goal,
        "review_action": action,
        "ref_basis": ref_basis,
    }
    if task_id:
        step["task_id"] = task_id
    if ref:
        step["ref"] = ref
    return step


def review_plan(*steps, scope="store"):
    return {"scope": scope, "steps": list(steps), "clarify": ""}


def phrased(*_):
    return {"role": "assistant", "content": "다듬은 문장"}


def changed_reviews(app, before):
    return [
        r["review_id"]
        for r, b in zip(app.store.read()["reviews"], before["reviews"], strict=True)
        if r != b
    ]


LIST_PLAN = review_plan(review_step("답글 안 단 리뷰 목록 조회", "read"), scope="advice")
LIST_CALL = call("ask_review_agent", request="답글 안 단 리뷰 목록 조회")


@pytest.mark.parametrize(
    ("ask", "goal", "approve"),
    [
        ("부정적인 리뷰에 답글 작성해줘", "부정적인 리뷰에 답글 초안 작성", "이걸로 확정해줘"),
        ("불만 있는 리뷰에 답장 좀 써 줄래", "불만 리뷰에 답장 초안 작성", "이대로 올려 주세요"),
        ("별로라고 한 리뷰에 답글 달아 줘", "낮은 별점 리뷰에 답글 달아 주기", "그걸로 진행해 줘"),
    ],
)
def test_b5f_split_read_then_draft_is_one_draft_and_consent_saves_it(tmp_path, ask, goal, approve):
    """H18: 같은 기능 read→draft 과분해는 draft 하나, 저장 동의는 review_action 없이도 승인."""
    hcx = HCX(
        [],
        LIST_CALL,
        *repeat(phrased(), 6),
        plans=[
            LIST_PLAN,
            review_plan(
                review_step(goal + "을 위한 리뷰 조회", "read"),
                review_step(goal, "draft"),
                scope="advice",
            ),
            review_plan(review_step("답글 저장", "none", "approve", "t1")),
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    before = app.store.read()
    app.ask("답글 안 단 리뷰 보여줘")
    draft = app.ask(ask)
    assert "통계 신호" not in draft and "필요한 값이나 대상" not in draft
    assert app.planner_loop.last_action["kind"] == "draft"
    assert [s.get("review_action") for s in app.trace["planner"]["steps"]] == ["draft"]
    task = app.ledger.active()
    assert task.preview["review_id"] == "r03" and app.store.read() == before
    # 끝난 초안 단계를 남겨 두면 승인 뒤 다른 리뷰 초안을 또 만든다.
    assert not any(a["action_id"] == "task.continue" for a in app.next_actions)
    saved = app.ask(approve)
    assert "저장" in saved and app.ledger.get(task.id).status == "done"
    assert changed_reviews(app, before) == ["r03"] and not app.ledger.open_tasks()


@pytest.mark.parametrize(
    ("revise", "approve"),
    [("더 짧게", "괜찮네 올려줘"), ("좀 간단하게 줄여 줘", "좋네요 등록해 주세요")],
)
@pytest.mark.parametrize("miscast", ["approve_settings", "new_draft"])
def test_b5f_revision_of_a_pending_draft_rewrites_the_same_review(
    tmp_path, revise, approve, miscast
):
    """H20: 초안 뒤 고치기가 approve+settings나 새 draft로 잘못 계획돼도 같은 리뷰 초안을 고친다."""
    wrong = (
        review_plan(review_step("답글 저장", "settings", "approve", "t1", "r03", "direct"))
        if miscast == "approve_settings"
        else review_plan(review_step("답글 초안 완성 후 저장하기", "draft"), scope="advice")
    )
    hcx = HCX(
        [],
        LIST_CALL,
        *repeat(phrased(), 6),
        plans=[
            LIST_PLAN,
            review_plan(review_step("안 좋은 리뷰에 답글 작성하기", "draft"), scope="advice"),
            wrong,
            review_plan(review_step("답글 저장", "none", "approve", "t1")),
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    before = app.store.read()
    app.ask("답글 안 단 리뷰 보여줘")
    app.ask("안 좋은 리뷰에 답글 부탁해")
    task = app.ledger.active()
    version = task.version
    calls = []

    def writer(text, sentiment, **kw):
        calls.append(kw)
        return "양이 아쉬우셨다니 죄송합니다. 다음엔 더 넉넉히 드릴게요."

    app.review.toolbox.writer.write_reply = writer
    reply = app.ask(revise)
    assert "넉넉히" in reply and app.store.read() == before
    assert task.preview["review_id"] == "r03" and task.version > version
    assert calls and revise in calls[0]["edit_request"]
    assert len([t for t in app.ledger.open_tasks() if t.feature == "review"]) == 1
    saved = app.ask(approve)
    assert "저장" in saved and app.ledger.get(task.id).status == "done"
    assert changed_reviews(app, before) == ["r03"]
    assert "넉넉히" in str(app.store.read()["reviews"])


@pytest.mark.parametrize(
    ("ask", "approve"),
    [
        ("나쁜 리뷰에 답글 써줘", "좋아 올려줘"),
        ("평이 안 좋은 리뷰에 답장 남겨 줘", "응 그대로 등록해"),
    ],
)
def test_b5f_advice_scoped_single_draft_is_executed_not_advised(tmp_path, ask, approve):
    """H28: advice로 분류된 리뷰 draft 한 단계도 실행하며 조언 신호 문구를 붙이지 않는다."""
    hcx = HCX(
        [],
        LIST_CALL,
        *repeat(phrased(), 6),
        plans=[
            LIST_PLAN,
            review_plan(review_step("낮은 별점의 리뷰에 답글 작성하기", "draft"), scope="advice"),
        ],
    )
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    before = app.store.read()
    app.ask("답글 안 단 리뷰 보여줘")
    draft = app.ask(ask)
    assert "통계 신호" not in draft and "필요한 값이나 대상" not in draft
    assert "답글을 달 리뷰: 별점 4" in draft
    # 조건 선택은 ref 없이 계획 goal 그대로 리뷰 에이전트(감정 판단)에 맡긴다(B5e).
    assert app.trace["via"] == "planner_single"
    assert app.trace["calls"][0]["arguments"]["request"] == "낮은 별점의 리뷰에 답글 작성하기"
    assert "ref" not in app.trace["planner"]["steps"][0]
    task = app.ledger.active()
    saved = app.ask(approve)
    assert "저장" in saved and app.ledger.get(task.id).status == "done"
    assert changed_reviews(app, before) == ["r03"]


def test_b5f_ledger_review_steps_do_not_need_a_review_action():
    from agenthan.agent import planner

    for action in ("none", "read", "settings"):
        content = json.dumps(review_plan(review_step("답글 저장", action, "approve", "t1")))
        plan = planner.parse_ledger_plan(content, {"t1": "review"}, [])
        assert plan.steps[0].kind == "approve" and plan.steps[0].review_action is None
    with pytest.raises(planner.PlanError):
        planner.parse_ledger_plan(
            json.dumps(review_plan(review_step("리뷰", "none"))), {"t1": "review"}, []
        )


def test_b5f_advice_signal_text_needs_advice_and_no_execution(tmp_path):
    """신호 없음 문구는 advice 조회 계획이 아무 결과도 내지 못한 경우에만 나온다."""
    hcx = HCX([], *repeat(phrased(), 6), plans=[review_plan(review_step("리뷰", "draft"))])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    loop = app.planner_loop
    loop.batch.scope = "store"
    assert loop.advice() == ""
    loop.batch.scope = "advice"
    app.trace = {"calls": [{"name": "ask_review_agent"}], "results": []}
    assert loop.advice() == ""
    app.trace = {"calls": [], "results": []}
    assert "통계 신호" in loop.advice()


def test_multi_step_plan_widens_only_the_wall_clock():
    from agenthan.agent.loop_budget import LoopBudget

    one = LoopBudget()
    one.plan_steps(1)
    assert one.seconds == 20
    two = LoopBudget()
    two.plan_steps(2)
    assert two.seconds == 30
    assert (two.tool_limit, two.model_limit, two.replan_limit) == (8, 10, 1)


# B5h: 기능 전용 계획 필드 제거, 같은 종류의 ref만 유지, 선택 필드 오류는 그 필드만 버린다.


def stray(step, **extra):
    """E8에서 본 오염: 다른 기능에 붙은 리뷰 ref와 스키마에서 빠진 옛 price_delta."""
    return {**step, "ref": "r03", "ref_basis": "direct", "price_delta": 5000, **extra}


@pytest.mark.parametrize(
    "owner_text",
    [
        "아메리카노 가격 5000원으로 올리고 매장결제도 켜 줘",
        "아메리카노는 5000원에 팔고 현장 결제 기능도 사용할게요",
    ],
)
def test_b5h_absolute_price_and_other_feature_ignore_stray_ref_and_delta(tmp_path, owner_text):
    steps = [
        stray({"feature": "menu", "goal": "아메리카노 가격 5000원"}),
        stray({"feature": "onsite_payment", "goal": "매장결제 켜기"}),
    ]
    hcx = HCX(
        [],
        call("upsert_menu", name="아메리카노", price=5000),
        call("set_onsite_payment", enabled=True),
        plans=[chain_plan(*steps, writes=("menu", "onsite_payment"))],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    reply = app.ask(owner_text)
    trace = app.trace["planner"]
    assert all("ref" not in s and "price_delta" not in s for s in trace["steps"])
    assert {"steps[0].ref", "steps[1].ref"} <= set(trace["dropped_fields"])
    assert "필요한 값이나 대상" not in reply and app.store.read() == before
    assert {t.feature for t in app.ledger.open_tasks() if t.preview} == {"menu", "onsite_payment"}
    app.ask("네")
    data = app.store.read()
    assert next(m for m in data["menus"] if m["name"] == "아메리카노")["price"] == 5000
    assert data["shop"]["onsite_payment"] is True


@pytest.mark.parametrize(
    "owner_text",
    [
        "'리뷰 이벤트 아메리카노' 메뉴를 2000원에 새로 등록해 줘",
        "새 메뉴 '리뷰 이벤트 아메리카노' 2천원으로 추가",
    ],
)
def test_b5h_new_menu_price_is_absolute_even_with_a_stale_delta(tmp_path, owner_text):
    step = stray({"feature": "menu", "goal": "새 메뉴 2000원 등록"}, price_delta=2000)
    hcx = HCX(
        [],
        call("upsert_menu", name="리뷰 이벤트 아메리카노", price=2000),
        {"role": "assistant", "content": "미리보기"},
        plans=[chain_plan(step)],
    )
    app = assistant(tmp_path, hcx)
    app.ask(owner_text)
    task = app.ledger.active()
    assert task and task.preview and task.slots["price"].value == 2000
    app.ask("네")
    added = next(m for m in app.store.read()["menus"] if m["name"] == "리뷰 이벤트 아메리카노")
    assert added["price"] == 2000


@pytest.mark.parametrize(
    "owner_text",
    [
        "블린 품절 처리하고, '품절 사과 쿠폰' 1000원 할인으로 "
        "11월 11일부터 11월 18일까지 쿠폰 만들어 줘",
        "블린은 다 떨어졌어요. 11월 11일~11월 18일 '품절 사과 쿠폰' 천원 할인 쿠폰도 발행해 주세요",
    ],
)
@pytest.mark.parametrize(
    "noise",
    [
        {"needs": ["coupon"]},  # 자기 기능을 기다림
        {"target_metric": "menu_mentions"},  # 리뷰 의존 없는 집계 기준
        {"owner_values": [{"field": "focus", "value": "all", "evidence": "쿠폰"}]},
        {"review_action": "settings", "basis": "invented"},
    ],
)
def test_b5h_one_bad_optional_field_keeps_the_rest_of_the_plan(tmp_path, owner_text, noise):
    """ms-03: 쿠폰 단계의 선택 필드 오류로 계획이 null → legacy로 빠져 쿠폰을 잃던 문제."""
    coupon = stray({"feature": "coupon", "goal": "품절 사과 쿠폰 1000원"}, price_delta=1000)
    hcx = HCX(
        [],
        call("upsert_menu", name="블린", action="sold_out"),
        call(
            "create_coupon",
            title="품절 사과 쿠폰",
            discount_type="amount",
            discount_value=1000,
            start_date="2026-11-11",
            end_date="2026-11-18",
        ),
        plans=[
            chain_plan(
                {"feature": "menu", "goal": "블린 품절"},
                {**coupon, **noise},
                writes=("menu", "coupon"),
            )
        ],
    )
    app = assistant(tmp_path, hcx)
    app.today = date(2026, 11, 10)
    before = app.store.read()
    app.ask(owner_text)
    assert len(hcx.plans) == 1 and app.trace["planner"] is not None
    assert app.trace["planner"]["dropped_fields"]
    assert {t.feature for t in app.ledger.open_tasks() if t.preview} == {"menu", "coupon"}
    assert app.store.read() == before
    app.ask("네")
    data = app.store.read()
    assert next(m for m in data["menus"] if m["name"] == "블린")["sold_out"]
    assert [c["title"] for c in data["coupons"]] == ["품절 사과 쿠폰"]


@pytest.mark.parametrize(
    ("owner_text", "price", "previews"),
    [
        ("블린 가격을 500원 올려 줘", 9500, True),
        ("블린 값 오백 원만 인상해 주세요", 9500, True),
        ("블린 가격을 500원 올려 줘", 9700, False),  # 차이가 사장님 숫자가 아님
        ("블린 값 오백 원만 인상해 주세요", 9700, False),
    ],
)
def test_b5h_relative_price_is_computed_by_act_and_checked_by_difference(
    tmp_path, owner_text, price, previews
):
    hcx = HCX(
        [],
        call("upsert_menu", name="블린", price=price),
        {"role": "assistant", "content": "가격을 알려 주세요"},
        plans=[new("menu")],
    )
    app = assistant(tmp_path, hcx)
    before = app.store.read()
    app.ask(owner_text)
    assert "현재 값에서 얼마만큼" in hcx.requests[0][0][0]["content"]
    task = app.ledger.active()
    assert bool(task and task.preview) == previews
    if previews:
        assert task.slots["price"].source == "lookup"
    assert app.store.read() == before


def _store_material(hcx):
    """The store data the model saw (single steps use the legacy prompt, chains the planner's)."""
    for messages, _, _ in hcx.requests:
        for m in messages:
            for label in ("가게 자료: ", "가게 데이터: "):
                if label in str(m.get("content")):
                    return json.loads(m["content"].split(label, 1)[1].split("\n", 1)[0])
    raise AssertionError("no store data in any model request")


def test_with_the_store_rag_the_menu_step_sees_only_the_named_menu(tmp_path):
    """ADR-0008: the model gets the menus the owner named, not the whole menu list."""
    from agenthan.app import pilot

    pilot.use_knowledge()
    hcx = HCX(["menu"], {"role": "assistant", "content": "확인할게요"})
    app = assistant(tmp_path, hcx)
    app.ask("블리니 가격 9500원으로 바꿔 줘")  # an alias of 블린

    material = _store_material(hcx)
    assert [m["name"] for m in material["menus"]] == ["블린"]
    assert "전체 4개" in material["menus_note"]


def test_with_the_store_rag_an_unnamed_menu_request_still_sees_every_menu(tmp_path):
    from agenthan.app import pilot

    pilot.use_knowledge()
    hcx = HCX(["menu"], {"role": "assistant", "content": "확인할게요"})
    app = assistant(tmp_path, hcx)
    app.ask("제일 비싼 메뉴 가격 좀 올려 줘")

    material = _store_material(hcx)
    assert len(material["menus"]) == 4 and "menus_note" not in material


def test_with_the_store_rag_a_talktalk_answer_sees_menu_facts_and_hours(tmp_path):
    from agenthan.app import pilot

    pilot.use_knowledge()
    hcx = HCX(["talktalk"], {"role": "assistant", "content": "확인할게요"})
    app = assistant(tmp_path, hcx)
    app.ask("메도빅 홀케이크 예약 문의에 답장해 줘")

    material = _store_material(hcx)
    assert material["menu_facts"][0]["name"] == "메도빅"
    assert material["shop"]["hours"]["mon"]


# ── 맥락 칩(#97): 리뷰·통계·쿠폰 뒤에 규칙 표(agent/suggest.py)의 칩이 붙는다 ──


def chip_labels(chips):
    return [c["label"] for c in chips]


def test_context_chips_after_review_summary_in_chat_and_button(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agenthan.agent.review_delegate.ask_review_agent",
        lambda request, session: {"text": "리뷰를 확인했어요.", "blocks": ""},
    )
    hcx = HCX([], plans=[new("review")])
    app = assistant(tmp_path, hcx)
    app._review_factory = FakeBackend().review_session
    app.ask("요즘 리뷰 어때?")
    labels = chip_labels(app.next_actions)
    assert labels[:2] == ["매일 리뷰 요약 받기", "불만 리뷰 답글 초안"]
    say = app.next_actions[0]
    assert say["action_id"] == "chat.say"
    assert say["args"] == {"text": "매일 리뷰 요약해서 보내 줘"}
    assert app.next_actions[1]["args"] == {"review_id": "r02"}
    assert app.trace["next_actions"] == app.next_actions

    before = app.store.read()
    button = app.run_action("review.summary", {}, "요즘 리뷰")
    ids = [c["action_id"] for c in button["next_actions"]]
    assert "chat.say" in ids and ids.count("review.draft_reply") == 1
    assert app.next_actions == button["next_actions"]
    assert app.store.read() == before

    # 리뷰 정기 보고가 이미 켜져 있으면 "매일 리뷰 요약 받기"는 뺀다.
    box = app.review.toolbox
    box.settings_store.save(box.ctx["store_id"], {"enabled": True, "frequency": "daily"})
    again = app.run_action("review.summary", {}, "요즘 리뷰")
    assert "매일 리뷰 요약 받기" not in chip_labels(again["next_actions"])


def test_context_chips_after_statistics_in_chat_and_button(tmp_path):
    hcx = HCX(
        ["statistics"],
        call("get_statistics", period="7d"),
        {"role": "assistant", "content": "조회"},
        {"role": "assistant", "content": "조회 흐름을 확인했어요."},
        {"role": "assistant", "content": "[]"},
    )
    app = assistant(tmp_path, hcx)
    app.ask("이번 주 손님 얼마나 왔어?")
    labels = chip_labels(app.next_actions)
    assert "대표 키워드 추가" in labels and "매일 리뷰 요약 받기" not in labels
    assert len(labels) == len(set(labels))
    assert sum(c["action_id"] == "coupon.create" for c in app.next_actions) == 1

    stats = app.run_action("stats.week", {}, "이번 주 손님")
    assert chip_labels(stats["next_actions"]) == [
        "대표 키워드 추가",
        "쿠폰 만들기",
        "리뷰 요약 보기",
    ]
    assert stats["next_actions"][0]["action_id"] == "keywords.edit"
    # 맥락 칩은 화면 제안일 뿐이다. 맨 "네"가 칩(쿠폰·키워드)에 대한 동의가 되지 않는다.
    before = app.store.read()
    assert "선택해 주세요" not in app.ask("네")
    assert app.store.read() == before and not app.planner_loop.last_action.get("proposals")


def test_context_chips_wait_for_the_coupon_approval_then_follow_it(tmp_path):
    hcx = HCX(
        ["coupon"],
        call(
            "create_coupon",
            title="개점 할인",
            discount_type="percent",
            discount_value=12,
            start_date="2026-11-13",
            end_date="2026-11-15",
        ),
    )
    app = assistant(tmp_path, hcx)
    app.today = date(2026, 11, 12)
    app.ask("내일부터 11월 15일까지 개점 할인 12% 쿠폰 만들어줘")
    ids = [c["action_id"] for c in app.next_actions]
    assert "task.approve" in ids
    assert not {"stats.week", "chat.say", "review.summary"} & set(ids)
    task = next(t for t in app.ledger.open_tasks() if t.preview)
    done = app.run_action("task.approve", {"task_id": task.id, "version": task.version}, "저장")
    assert app.store.read()["coupons"]
    assert chip_labels(done["next_actions"])[0] == "이번 주 통계 보기"
    assert "task.approve" not in [c["action_id"] for c in done["next_actions"]]
    assert 1 <= len(done["next_actions"]) <= 3


# ── 리뷰 정기 보고 켜고 끄기(#98): 계획기가 조회로 잡거나 goal을 바꿔 적어도 설정 미리보기로 ──


def paraphrased(goal, action):
    """실제 계획기처럼 goal을 바꿔 적고(사장님 원문이 아님) 설정 값은 비운 리뷰 단계."""
    planned = new("review", review_action=action)
    planned["steps"][0]["goal"] = goal
    return planned


def routine_app(tmp_path, *plans):
    app = assistant(tmp_path, HCX([], plans=list(plans)))
    app._review_factory = FakeBackend().review_session
    return app


def report_settings(app):
    box = app.planner_loop.session().toolbox
    return box.settings_store.get(box.ctx["store_id"])


@pytest.mark.parametrize("action", ["read", "settings"])
def test_98_daily_summary_chip_turns_the_routine_on_only_after_approval(tmp_path, action):
    app = routine_app(tmp_path, paraphrased("리뷰 요약 조회", action))
    reply = app.ask("매일 리뷰 요약해서 보내 줘")
    task = app.ledger.active()
    assert task and task.preview and task.preview["report_settings"]
    assert task.preview["after"]["enabled"] is True
    assert task.preview["after"]["frequency"] == "daily"
    assert report_settings(app)["enabled"] is False  # 승인 전에는 그대로
    assert "매일 09:00" in reply and "다음 보고" in reply
    assert [a["action_id"] for a in app.next_actions][:2] == ["task.approve", "task.cancel"]
    app.ask("네")
    settings = report_settings(app)
    assert settings["enabled"] is True and settings["frequency"] == "daily"
    # #97 칩: 켜졌으니 [매일 리뷰 요약 받기]는 빠진다
    chips = app.run_action("review.summary", {}, "요즘 리뷰")["next_actions"]
    assert "매일 리뷰 요약 받기" not in chip_labels(chips)


def test_98_weekly_request_keeps_weekday_and_time(tmp_path):
    app = routine_app(tmp_path, paraphrased("리뷰 요약", "read"))
    reply = app.ask("매주 월요일 9시에 리뷰 요약 보내 줘")
    after = app.ledger.active().preview["after"]
    assert (after["enabled"], after["frequency"], after["weekday"], after["time"]) == (
        True,
        "weekly",
        "mon",
        "09:00",
    )
    assert "매주 월요일 09:00" in reply
    app.ask("네")
    assert report_settings(app)["weekday"] == "mon"


def test_98_turning_reports_off_also_waits_for_approval(tmp_path):
    app = routine_app(tmp_path, paraphrased("리뷰 보고", "read"), paraphrased("리뷰 보고", "read"))
    box = app.planner_loop.session().toolbox
    box.settings_store.save(
        box.ctx["store_id"], {**report_settings(app), "enabled": True, "frequency": "daily"}
    )
    reply = app.ask("리뷰 보고 꺼 줘")
    assert app.ledger.active().preview["after"]["enabled"] is False
    assert "끌까요" in reply and report_settings(app)["enabled"] is True
    app.ask("취소")
    assert report_settings(app)["enabled"] is True
    app.ask("리뷰 보고 꺼 줘")
    app.ask("네")
    assert report_settings(app)["enabled"] is False
    # 꺼졌으니 [매일 리뷰 요약 받기]가 다시 보인다
    chips = app.run_action("review.summary", {}, "요즘 리뷰")["next_actions"]
    assert "매일 리뷰 요약 받기" in chip_labels(chips)


def test_98_unstated_frequency_uses_the_store_suggestion(tmp_path):
    from agenthan.agent.review.report import suggest_frequency

    app = routine_app(tmp_path, paraphrased("리뷰 보고", "settings"))
    reply = app.ask("리뷰 정기 보고 켜 줘")
    box = app.planner_loop.session().toolbox
    suggested = suggest_frequency(box.ctx["repo"], box.ctx["store_id"], app.today)["frequency"]
    task = app.ledger.active()
    assert task.preview["after"]["enabled"] is True
    assert task.preview["after"]["frequency"] == suggested
    assert "제안" in reply
    assert task.slots["frequency"].source == "lookup"
    app.ask("네")
    assert report_settings(app)["frequency"] == suggested


def test_98_already_off_says_so_without_a_preview(tmp_path):
    app = routine_app(tmp_path, paraphrased("리뷰 보고", "read"))
    reply = app.ask("리뷰 보고 꺼 줘")
    assert not any(t.preview for t in app.ledger.open_tasks())
    assert "이미 꺼져" in reply


def test_98_plain_review_summary_is_still_a_read(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agenthan.agent.review_delegate.ask_review_agent",
        lambda request, session: {"text": "리뷰를 확인했어요.", "blocks": ""},
    )
    app = routine_app(tmp_path, paraphrased("리뷰 요약", "read"))
    assert "리뷰를 확인했어요" in app.ask("최근 리뷰 요약해 줘")
    assert not any(t.preview for t in app.ledger.open_tasks())


# ── 실제 HCX 계획 모양 그대로(#98 검수): advice+draft, advice+draft+ref, store+빈 steps+clarify ──


def hcx_draft(goal, ref=None):
    step = {"kind": "new", "feature": "review", "goal": goal, "review_action": "draft"}
    if ref:
        step.update(ref=ref, ref_basis="direct")
    return step


def only_report_previews(app):
    """답글 초안 미리보기가 없고, 열린 미리보기는 보고 설정뿐이다."""
    previews = [t.preview for t in app.ledger.open_tasks() if t.preview]
    return all(p.get("report_settings") and not p.get("drafts") for p in previews)


@pytest.mark.parametrize(
    ("text", "step", "expect"),
    [
        (
            "매일 오후 2시 37분에 리뷰 요약해서 보내 줘",
            hcx_draft("매일 오후 2시 37분에 리뷰 요약해서 보내 줘"),
            ("daily", None, "14:37", "매일 14:37"),
        ),
        (
            "매일 리뷰 요약해서 보내 줘",
            hcx_draft("리뷰 요약 및 발송", ref="r01"),
            ("daily", None, None, "매일 09:00"),
        ),
        (
            "매주 월요일 9시에 리뷰 요약 보내 줘",
            hcx_draft("매주 월요일 9시에 리뷰 요약 보내 줘"),
            ("weekly", "mon", "09:00", "매주 월요일 09:00"),
        ),
    ],
)
def test_98_real_hcx_advice_draft_plans_turn_the_routine_on(tmp_path, text, step, expect):
    freq, weekday, time, said = expect
    app = routine_app(tmp_path, {"scope": "advice", "steps": [step], "clarify": ""})
    reply = app.ask(text)
    assert only_report_previews(app)
    task = app.ledger.active()
    assert task and task.preview and task.preview.get("report_settings")
    after = task.preview["after"]
    assert after["enabled"] is True and after["frequency"] == freq
    assert weekday is None or after["weekday"] == weekday
    assert time is None or after["time"] == time
    assert said in reply and "다음 보고" in reply
    assert "필요한 값" not in reply
    assert report_settings(app)["enabled"] is False  # 승인 전에는 그대로
    app.ask("네")
    settings = report_settings(app)
    assert settings["enabled"] is True and settings["frequency"] == freq
    assert time is None or settings["time"] == time


def test_98_real_hcx_empty_store_plan_turns_the_routine_off(tmp_path):
    app = routine_app(tmp_path, {"scope": "store", "steps": [], "clarify": "할 일 없음"})
    box = app.planner_loop.session().toolbox
    box.settings_store.save(
        box.ctx["store_id"], {**report_settings(app), "enabled": True, "frequency": "daily"}
    )
    reply = app.ask("리뷰 보고 꺼 줘")
    assert only_report_previews(app)
    assert "끌까요" in reply and "어떤 가게 일" not in reply
    assert app.ledger.active().preview["after"]["enabled"] is False
    assert report_settings(app)["enabled"] is True
    app.ask("네")
    assert report_settings(app)["enabled"] is False


def test_98_report_rule_keeps_other_feature_steps(tmp_path):
    from agenthan.agent import planner

    loop = routine_app(tmp_path).planner_loop
    loop.batch = SimpleNamespace(text="매일 리뷰 요약해서 보내 주고 쿠폰도 만들어 줘")
    plan = planner.Plan(
        "advice",
        (
            planner.Step("coupon", "쿠폰"),
            planner.Step("review", "리뷰 요약", review_action="draft"),
        ),
        None,
    )
    out = loop.report_steps(plan)
    assert [(s.feature, s.review_action) for s in out.steps] == [
        ("coupon", None),
        ("review", "settings"),
    ]
    assert out.scope == "store"
    out = loop.report_steps(planner.Plan("store", (planner.Step("coupon", "쿠폰"),), None))
    assert [(s.feature, s.review_action) for s in out.steps] == [
        ("coupon", None),
        ("review", "settings"),
    ]


def test_98_suggest_needs_a_clear_setting_word(tmp_path):
    from agenthan.agent import planner

    loop = routine_app(tmp_path).planner_loop
    draft = planner.Plan("advice", (planner.Step("review", "리뷰", review_action="draft"),), None)
    loop.batch = SimpleNamespace(text="리뷰 정기 보고 켜 줘")
    out = loop.report_steps(draft)
    assert out.scope == "store" and out.steps[0].review_action == "settings"
    loop.batch = SimpleNamespace(text="불만 리뷰를 보고 뭘 할지 추천해 줘")
    assert loop.report_steps(draft) is draft
    loop.batch = SimpleNamespace(text="매일 리뷰 보고 답글 써 줘")
    assert loop.report_steps(draft) is draft


@pytest.mark.parametrize("text", ["요즘 리뷰 어때?", "리뷰 요약해 줘"])
def test_98_plain_summary_words_stay_a_summary(tmp_path, monkeypatch, text):
    from agenthan.agent import planner

    monkeypatch.setattr(
        "agenthan.agent.review_delegate.ask_review_agent",
        lambda request, session: {"text": "리뷰를 확인했어요.", "blocks": ""},
    )
    app = routine_app(tmp_path, new("review", review_action="read"))
    assert "리뷰를 확인했어요" in app.ask(text)
    assert not any(t.preview for t in app.ledger.open_tasks())
    # 조언·초안으로 잡혀도 보고 설정 말이 없으면 계획을 그대로 둔다
    app.planner_loop.batch = SimpleNamespace(text=text)
    for scope, action in (("advice", "read"), ("advice", "draft"), ("store", "read")):
        plan = planner.Plan(scope, (planner.Step("review", "리뷰", review_action=action),), None)
        assert app.planner_loop.report_steps(plan) is plan
