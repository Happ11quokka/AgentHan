"""Papago 실측 (4단계 티켓 04, spec "Papago 실측"). CI 밖에서 사람이 돌린다(과금, 호출 약 240번).

    uv run python -m agenthan.voice.papago_measure \\
        --env /path/to/AgentHan/.env --out /path/to/AgentHan/private/papago-measure

- 키는 `settings.read_env_file`로 읽고 03의 `papago_from_env`·`PapagoTranslator`를 그대로 쓴다.
  키 값은 화면·파일 어디에도 쓰지 않는다
- 메뉴명 보호는 우리 실제 경로와 같다: 더미 가게 메뉴(이름·별칭 → 원어 이름) + `lexicon.RU`로
  `protect.mask` → Papago → `protect.restore`(None이면 번역을 버림). 배지는 `badges.request_badges`
- 잴 것(`kind`): cold(새 클라이언트 첫 호출) · warm(클라이언트 하나로 30문장 × 3바퀴, 첫 호출 하나는
  데우기로 뺌) · concurrent(동시 3개 × 8번) · placeholder(메뉴명 든 요청 25개, 가린 것·안 가린 것) ·
  negation(부정·알레르기 20개 + ru→ko 역번역) · ru_ko(사장님 짧은 답 12개 말투) ·
  honorific(ru→ko에 `honorific:true`를 보냈을 때 2번) · shape(손님 말 흔한 모양 10개, 자막 판단)
- 원기록은 `<out>/<시각>.jsonl`, 요약 표는 `<out>/<시각>.md`. `--out`은 저장소의
  `data/`·`private/` 아래만 받는다(받은 번역문이 들어 있다)
- 이 PC에서 잰 값이다(NCP 서버 안이 아님). 결과 정리: 연구 노트 `s4-papago-measure.md`
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from agenthan.core.contracts import StoreKnowledge
from agenthan.voice.owner.badges import request_badges
from agenthan.voice.owner.lexicon import RU
from agenthan.voice.owner.papago import KEY_ENVS, PAPAGO_URL, PapagoTranslator, papago_from_env
from agenthan.voice.owner.protect import PLACEHOLDER, Masked, mask, restore
from agenthan.voice.owner.translate import TranslateError
from agenthan.voice.settings import read_env_file

# ── 문장 세트 ──────────────────────────────────────────────────────────────
# 짧은 요청사항·손님 말 30개(지연용, 대개 10~30자)
LATENCY = (
    "메도빅은 조금 덜 달게 해 주실 수 있나요?",
    "고수는 빼 주세요, 향을 잘 못 먹어서요.",
    "아이스 아메리카노 두 잔 포장해 주세요.",
    "블린에 사워크림은 따로 담아 주세요.",
    "케이크에 초 두 개만 넣어 주세요.",
    "저녁 여섯 시 반쯤 찾으러 갈게요.",
    "견과류 알레르기가 있는데 괜찮을까요?",
    "나폴레옹 케이크 한 조각 더 추가할게요.",
    "포크는 세 개 넣어 주시면 좋겠어요.",
    "생일 케이크라서 예쁘게 포장해 주세요.",
    "디카페인으로 바꿀 수 있을까요?",
    "얼음은 조금만 넣어 주세요.",
    "아이가 먹을 거라서 너무 달지 않게요.",
    "계란 알레르기가 있어서 확인 부탁드려요.",
    "블린은 연어 없이 만들어 주실 수 있어요?",
    "도착하면 전화 드릴게요.",
    "메도빅 홀케이크로 하나 주문할게요.",
    "샷 하나 추가해 주시고 뜨겁게 해 주세요.",
    "주차할 곳이 있는지 궁금해요.",
    "카드로 결제해도 되나요?",
    "우유 대신 두유로 바꿀 수 있나요?",
    "케이크 위에 축하 문구를 써 주실 수 있나요?",
    "빨대는 필요 없어요.",
    "조금 늦을 수도 있는데 괜찮을까요?",
    "땅콩이 들어간 메뉴가 있나요?",
    "포장 용기는 따로 하나씩 담아 주세요.",
    "아메리카노는 연하게 해 주세요.",
    "케이크는 냉장 보관해야 하나요?",
    "현금 영수증도 해 주세요.",
    "가게 앞에 도착하면 문자 드릴게요.",
)

# 메뉴명·별칭·재료 낱말·숫자가 든 요청사항 25개(자리표시자 보존)
PLACEHOLDER_SET = (
    "메도빅 2개는 따로 포장해 주세요",
    "메도빅은 초 3개 꽂아 주세요",
    "나폴레옹은 잘라서 주세요",
    "블린에 사워크림 많이 올려 주세요",
    "블린은 고수 빼 주세요",
    "아메리카노는 연하게 해 주세요",
    "꿀케이크 하나는 선물용으로 포장해 주세요",
    "메도비크에 견과류 들어가요?",
    "허니케이크는 상자에 담아 주세요",
    "나폴레옹케이크는 2조각으로 나눠 주세요",
    "블리니는 따뜻하게 데워 주세요",
    "러시아 팬케이크에 연어 빼 주세요",
    "메도빅이랑 나폴레옹 하나씩 따로 담아 주세요",
    "블린 2개 중 1개는 사워크림 빼 주세요",
    "메도빅 홀케이크에 초 5개 넣어 주세요",
    "나폴레옹에 우유 들어가나요?",
    "아메리카노 2잔은 얼음 적게 주세요",
    "블린은 파 많이 넣어 주세요",
    "메도빅은 6시 30분에 찾아갈게요",
    "나폴레옹 3개랑 메도빅 1개요",
    "블린에 밤 넣을 수 있어요?",
    "메도빅 포장할 때 포크 4개 주세요",
    "아메리카노는 샷 추가해 주세요",
    "블린은 고수랑 파 둘 다 빼 주세요",
    "메도빅은 생일 초 하나만 넣어 주세요",
)

# 부정·알레르기 20개 (리서치 TR "직접 재 볼 것" 4)
NEGATION_SET = (
    "사워크림 빼 주세요",
    "계란 알레르기 있어요",
    "덜 달게 해 주세요",
    "얼음 없이 주세요",
    "고수 빼 주세요",
    "견과류 알레르기가 있어서 견과류는 절대 넣지 마세요",
    "우유 못 먹어요",
    "땅콩 들어가면 안 돼요",
    "양파는 넣지 말아 주세요",
    "마늘 없이 해 주실 수 있나요?",
    "아이스 말고 따뜻하게 주세요",
    "시럽은 넣지 마세요",
    "밀가루 알러지가 있어요",
    "연어는 빼고 사워크림만 올려 주세요",
    "새우 알레르기 있는데 괜찮을까요?",
    "설탕 안 넣어도 돼요",
    "휘핑크림은 필요 없어요",
    "깨는 빼 주세요",
    "초는 안 주셔도 돼요",
    "우유 대신 두유로 해 주세요, 우유 알레르기예요",
)

# 사장님 짧은 답(ru) 12개 → ko 말투 (티켓 07 자유 입력)
RU_ANSWERS = (
    "Да, можно.",
    "Нет, нельзя.",
    "Не знаю, уточню.",
    "В медовике есть орехи.",
    "Орехов нет.",
    "Будет готово через 20 минут.",
    "Извините, блины закончились.",
    "Можно без сметаны.",
    "Подождите, пожалуйста, 10 минут.",
    "Да, приходите к 18:30.",
    "Без кинзы сделаем.",
    "К сожалению, сегодня не получится.",
)
HONORIFIC_SET = ("Да, можно.", "Будет готово через 20 минут.")

# 손님 말 흔한 모양 10개 (2·3단계 시나리오·NLU 골드셋에서 고름. 자막 판단용)
SHAPES = (
    "메도빅 하나 주세요",
    "아메리카노 두 잔이랑 블린 하나요",
    "네 맞아요",
    "아니요",
    "6시 반에 찾으러 갈게요",
    "메도빅 견과류 들어가요?",
    "사워크림 빼 주세요",
    "하나 더 추가해 주세요",
    "전화번호는 010-1234-5678이에요",
    "그걸로 주문할게요",
)

COLD_N = 8
WARM_ROUNDS = 3
CONCURRENT = 3
CONCURRENT_ROUNDS = 8
# 실측 클라이언트 시간 제한: 꼬리를 보려고 넉넉히(실제 기본 3초와 다르다)
MEASURE_TIMEOUT_S = 10.0


# ── 순수 계산 (tests/voice/test_papago_measure.py) ─────────────────────────────


def latency_summary(ms: Sequence[float]) -> dict[str, float | int | None]:
    """개수·P50·P90·최대. 백분위는 가장 가까운 순위(ceil(q·n)번째 작은 값)."""
    if not ms:
        return {"n": 0, "p50": None, "p90": None, "max": None}
    s = sorted(ms)

    def rank(q: float) -> float:
        return s[max(1, math.ceil(q * len(s))) - 1]

    return {"n": len(s), "p50": rank(0.5), "p90": rank(0.9), "max": s[-1]}


Verdict = Literal["ok", "placeholder", "number"]


def placeholder_verdict(
    raw: str, masked: Masked, numerals: Mapping[int, tuple[str, ...]]
) -> Verdict:
    """우리 실제 경로(`protect.restore`)가 번역을 쓰는지, 버린다면 왜인지.

    placeholder = 자리표시자가 사라지거나 늘거나 번호가 틀림, number = 원문 숫자와 안 맞음
    """
    if restore(raw, masked, numerals=numerals) is not None:
        return "ok"
    found = sorted(int(n) for n in PLACEHOLDER.findall(raw))
    if found != list(range(1, len(masked.fills) + 1)):
        return "placeholder"
    return "number"


_ENDING = re.compile(r"[\s.!?…~,\"')]+$")


def ko_tone(text: str) -> str:
    """마지막 문장 끝말로 말투를 가른다: 해요체 · 합쇼체 · 해라체 · 그 밖(반말·명사형·빈 글)."""
    end = _ENDING.sub("", text.strip())
    if end.endswith(("니다", "니까", "십시오", "시오")):
        return "합쇼체"
    if end.endswith("요"):
        return "해요체"
    if end.endswith("다"):
        return "해라체"
    return "그 밖"


def md_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """마크다운 표. `|`는 `\\|`로, 줄바꿈은 빈칸으로, None은 `-`로."""

    def cell(v: object) -> str:
        return "-" if v is None else str(v).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


# ── 실행부 (네트워크) ───────────────────────────────────────────────────────


def menu_names(store: StoreKnowledge) -> dict[str, str]:
    """`CardTexts`와 같은 메뉴명 표: 이름·별칭 → 원어 이름(비면 한국어)."""
    names: dict[str, str] = {}
    for item in store.list_menu():
        for ko in (item.name_ko, *item.aliases):
            names.setdefault(ko, item.name_native or item.name_ko)
    return names


class Recorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[dict[str, Any]] = []

    def add(self, kind: str, **row: Any) -> dict[str, Any]:
        rec = {"kind": kind, "at": datetime.now().astimezone().isoformat(), **row}
        self.rows.append(rec)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["kind"] == kind]


async def _timed(call: Callable[[], Awaitable[list[str]]]) -> tuple[float, str | None, str | None]:
    """(ms, 번역문, 오류 코드)."""
    start = time.perf_counter()
    try:
        out = await call()
        return (time.perf_counter() - start) * 1000, out[0], None
    except TranslateError as err:
        return (time.perf_counter() - start) * 1000, None, err.code


async def _one(
    tr: PapagoTranslator, text: str, source: str = "ko", target: str = "ru"
) -> tuple[float, str | None, str | None]:
    return await _timed(lambda: tr.translate([text], source, target))


def _translator(env: Mapping[str, str]) -> PapagoTranslator:
    tr = papago_from_env(env, timeout=MEASURE_TIMEOUT_S)
    if tr is None:
        raise SystemExit(f"{' · '.join(KEY_ENVS)}가 --env 파일에 없어요")
    return tr


async def measure(env: Mapping[str, str], rec: Recorder) -> None:
    # 더미 가게(실측 전용). 운영 코드처럼 모듈 맨 위에서 가짜 가게를 가져오지 않는다
    from agenthan.core.fakes import FakeStoreKnowledge

    names = menu_names(FakeStoreKnowledge.from_fixture())

    # 1) 첫 호출: 클라이언트를 새로 만들 때마다(DNS·TCP·TLS 포함)
    for i in range(COLD_N):
        tr = _translator(env)
        ms, out, err = await _one(tr, LATENCY[i])
        await tr.aclose()
        rec.add("cold", i=i, text=LATENCY[i], raw=out, ms=round(ms), error=err)

    tr = _translator(env)
    try:
        # 2) 이어 쓰는 클라이언트. 첫 호출(데우기)은 뺀다
        ms, out, err = await _one(tr, "안녕하세요")
        rec.add("warmup", ms=round(ms), raw=out, error=err)
        for rnd in range(1, WARM_ROUNDS + 1):
            for i, text in enumerate(LATENCY):
                ms, out, err = await _one(tr, text)
                rec.add("warm", round=rnd, i=i, text=text, chars=len(text), raw=out,
                        ms=round(ms), error=err)

        # 3) 동시 3개 (같은 클라이언트, 실제 어댑터처럼 gather)
        for rnd in range(CONCURRENT_ROUNDS):
            batch = [LATENCY[(rnd * CONCURRENT + k) % len(LATENCY)] for k in range(CONCURRENT)]
            start = time.perf_counter()
            results = await asyncio.gather(*(_one(tr, t) for t in batch))
            wall = (time.perf_counter() - start) * 1000
            for k, (text, (ms, out, err)) in enumerate(zip(batch, results, strict=True)):
                rec.add("concurrent", round=rnd, k=k, text=text, raw=out, ms=round(ms),
                        wall_ms=round(wall), error=err)

        # 4) 자리표시자: 우리 경로(가림) + 비교용(안 가림)
        for i, text in enumerate(PLACEHOLDER_SET):
            m = mask(text, names=names, terms=RU.terms)
            ms, raw, err = await _one(tr, m.text)
            _, plain, _ = await _one(tr, text)
            rec.add("placeholder", i=i, text=text, masked=m.text, fills=list(m.fills), raw=raw,
                    verdict=None if raw is None else placeholder_verdict(raw, m, RU.numerals),
                    restored=None if raw is None else restore(raw, m, numerals=RU.numerals),
                    unmasked=plain, ms=round(ms), error=err)

        # 5) 부정·알레르기: 우리 경로 결과 + 배지 + ru→ko 역번역
        for i, text in enumerate(NEGATION_SET):
            m = mask(text, names=names, terms=RU.terms)
            ms, raw, err = await _one(tr, m.text)
            restored = None if raw is None else restore(raw, m, numerals=RU.numerals)
            shown = restored or ""
            back = (await _one(tr, shown, "ru", "ko"))[1] if shown else None
            rec.add("negation", i=i, text=text, masked=m.text, raw=raw, restored=restored,
                    badges=request_badges(text), back=back, ms=round(ms), error=err)

        # 6) 사장님 짧은 답 ru → ko 말투
        for i, text in enumerate(RU_ANSWERS):
            ms, out, err = await _one(tr, text, "ru", "ko")
            rec.add("ru_ko", i=i, text=text, raw=out, tone=None if out is None else ko_tone(out),
                    ms=round(ms), error=err)

        # 7) 손님 말 흔한 모양 (자막 후보)
        for i, text in enumerate(SHAPES):
            m = mask(text, names=names, terms=RU.terms)
            ms, raw, err = await _one(tr, m.text)
            rec.add("shape", i=i, text=text, chars=len(text), masked=m.text, raw=raw,
                    restored=None if raw is None else restore(raw, m, numerals=RU.numerals),
                    ms=round(ms), error=err)
    finally:
        await tr.aclose()

    # 8) ru→ko에 honorific:true (지원 안 하는 쌍이면 무시인지 오류인지). 어댑터에 칸이 없어 직접
    headers = {"X-NCP-APIGW-API-KEY-ID": env[KEY_ENVS[0]], "X-NCP-APIGW-API-KEY": env[KEY_ENVS[1]]}
    async with httpx.AsyncClient(timeout=MEASURE_TIMEOUT_S) as http:
        for text in HONORIFIC_SET:
            body = {"source": "ru", "target": "ko", "text": text, "honorific": True}
            res = await http.post(PAPAGO_URL, headers=headers, json=body)
            try:
                data = res.json()
            except ValueError:
                data = {}
            result = (data.get("message") or {}).get("result") or {}
            rec.add("honorific", text=text, status=res.status_code,
                    raw=result.get("translatedText"),
                    error_code=(data.get("error") or {}).get("errorCode"))


def _ms(v: object) -> str:
    return "-" if v is None else f"{v:.0f}"


def summary_md(rec: Recorder) -> str:
    """원기록에서 요약 표(마크다운)를 만든다."""
    out: list[str] = []

    def lat_row(name: str, rows: list[dict[str, Any]]) -> list[object]:
        ok = [r["ms"] for r in rows if r.get("error") is None]
        s = latency_summary(ok)
        errs = len(rows) - len(ok)
        return [name, s["n"], _ms(s["p50"]), _ms(s["p90"]), _ms(s["max"]), errs]

    warm = rec.of("warm")
    lat_rows = [lat_row("cold(새 클라이언트 첫 호출)", rec.of("cold")),
                lat_row("warm 전체", warm)]
    for rnd in range(1, WARM_ROUNDS + 1):
        lat_rows.append(lat_row(f"warm {rnd}바퀴", [r for r in warm if r["round"] == rnd]))
    conc = rec.of("concurrent")
    lat_rows.append(lat_row("동시 3개(개별)", conc))
    walls = latency_summary([w for _, w in sorted({(r["round"], r["wall_ms"]) for r in conc})])
    lat_rows.append(["동시 3개(묶음 벽시계)", walls["n"], _ms(walls["p50"]), _ms(walls["p90"]),
                     _ms(walls["max"]), "-"])
    out += ["## 지연 (ms)", md_table(["구간", "n", "P50", "P90", "최대", "오류"], lat_rows), ""]

    ph = rec.of("placeholder")
    counts = {v: sum(1 for r in ph if r["verdict"] == v) for v in ("ok", "placeholder", "number")}
    out += ["## 자리표시자 보존", f"쓰임 {counts['ok']}/{len(ph)} · 자리표시자 깨짐 "
            f"{counts['placeholder']} · 숫자 불일치 {counts['number']}", "",
            md_table(["원문", "가린 글", "Papago", "판정", "보이는 번역", "안 가렸을 때"],
                     [[r["text"], r["masked"], r["raw"], r["verdict"], r["restored"],
                       r["unmasked"]] for r in ph]), ""]

    out += ["## 부정·알레르기", md_table(
        ["원문", "가린 글", "Papago", "보이는 번역", "배지", "역번역(ru→ko)"],
        [[r["text"], r["masked"], r["raw"], r["restored"], " ".join(r["badges"]), r["back"]]
         for r in rec.of("negation")]), ""]

    ru = rec.of("ru_ko")
    tones: dict[str, int] = {}
    for r in ru:
        tones[r["tone"]] = tones.get(r["tone"], 0) + 1
    out += ["## 사장님 답 ru→ko 말투", " · ".join(f"{k} {v}" for k, v in tones.items()), "",
            md_table(["ru", "ko", "말투", "ms"], [[r["text"], r["raw"], r["tone"], r["ms"]]
                                                for r in ru]), "",
            md_table(["ru(honorific:true)", "HTTP", "ko", "오류 코드"],
                     [[r["text"], r["status"], r["raw"], r["error_code"]]
                      for r in rec.of("honorific")]), ""]

    out += ["## 손님 말 흔한 모양", md_table(
        ["원문", "글자", "가린 글", "Papago", "보이는 번역", "ms"],
        [[r["text"], r["chars"], r["masked"], r["raw"], r["restored"], r["ms"]]
         for r in rec.of("shape")]), ""]
    return "\n".join(out)


def _private_out(path: Path) -> Path:
    """저장소의 `data/`·`private/`(옆에 pyproject.toml이 있는 폴더) 아래만 받는다."""
    path = path.resolve()
    if not any(d.name in ("data", "private") and (d.parent / "pyproject.toml").is_file()
               for d in (path, *path.parents)):
        raise SystemExit(f"--out은 저장소의 data/ 또는 private/ 아래여야 해요: {path}")
    return path


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Papago 실측 (CI 밖, 과금)")
    parser.add_argument("--env", type=Path, required=True, help="키가 든 .env 경로")
    parser.add_argument("--out", type=Path, required=True, help="결과 폴더 (private/ 아래)")
    args = parser.parse_args(argv)
    out = _private_out(args.out)
    out.mkdir(parents=True, exist_ok=True)
    env = read_env_file(args.env)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rec = Recorder(out / f"{stamp}.jsonl")
    asyncio.run(measure(env, rec))
    md = summary_md(rec)
    (out / f"{stamp}.md").write_text(md + "\n", encoding="utf-8")
    print(md)
    # 줄마다 한 번 + 자리표시자 비교(안 가림) + 부정 역번역
    calls = (len(rec.rows) + len(rec.of("placeholder"))
             + sum(1 for r in rec.of("negation") if r["back"]))
    print(f"\n원기록 {rec.path} · 호출 {calls}번")


if __name__ == "__main__":
    main()
