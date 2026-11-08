"""오프라인 말 끝 평가 (3단계 spec "평가 도구", 티켓 03). CI 밖에서 사람이 돌린다.

EoT-Bench 한국어(LiveKit, CC-BY-4.0)와 우리 녹음에서 멈춤마다 "그 시점까지의 글"과 오디오를 만들어
끝말 규칙 · Smart Turn · 합친 판정을 돌리고, 오끊김률과 평균 대기를 직전 질문 종류별로 낸다.

    uv run --with pyarrow python -m agenthan.voice.turn_eval \\
        --model <smart-turn-v3.2-cpu.onnx> \\
        --eot data/eot-bench/ko.parquet \\
        --recordings private/recordings/donghyeon/wav \\
        --manifest private/recordings/donghyeon/turn-eval.json \\
        --out private/turn-eval

- 데이터와 결과는 `data/`·`private/`에만 둔다. `--out`이 그 밖이면 멈춘다. 화면과 결과 파일에는
  id·시각·점수·라벨만 쓰고 받아 적은 글은 쓰지 않는다
- `pyarrow`는 평가 때만 필요해서 의존성에 넣지 않았다(`uv run --with pyarrow`)
- 시간 모델은 연구 노트 `s3-smart-turn.md` 9-2와 같다. 초는 멈춤 시작(마지막 말 끝)부터.
  무음 감지 말 끝 0.4초(`--vad-s`), STT 확정 글 1.0초(`--text-s`), 다시 말한 소리는 0.2초 뒤 말
  시작으로 본다. 끝남이면 확정 글 때 넘기고, 이어짐이면 max(확정 글, 상한)에 넘긴다.
  쉼 뒤 말이 넘긴 뒤에야 시작되면 오끊김이다. 쉼은 0.2~5.0초만 센다(EoT-Bench 하네스와 같음)
- 녹음 목록(`--manifest`)은 녹음마다 직전 질문·상태와 무음 감지 말 끝마다의 글을 담은 JSON이다:
  `{"A1": {"question": "...", "state": "greeting", "texts": ["말 끝 1까지의 글", ...]}}`.
  글이 빈 말 끝(잡음)은 판정이 없어 뺀다
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import wave
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from agenthan.voice.call.frames import BYTES_PER_MS
from agenthan.voice.call.turn_end import classify_turn_end, combine_turn_end, question_kind
from agenthan.voice.call.vad import SilenceDetector, VadSettings

VAD_S = 0.4
TEXT_S = 1.0
START_S = 0.2
HOLD_RANGE = (0.2, 5.0)
SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class Pause:
    """멈춤 하나. `label`: hold(말 중간 쉼) · eot(진짜 끝). `dur`: 멈춤 길이(초)."""

    source: str
    item: str
    index: int
    label: str
    dur: float
    text: str  # 그 시점까지의 글 (결과 파일에는 쓰지 않는다)
    question_kind: str
    score: float | None = None  # 말 끝(VAD_S) 시점의 Smart Turn 끝남 확률
    menu_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Policy:
    name: str
    rules: bool
    model: bool
    low: float = 0.3
    high: float = 0.7
    cap: float = 2.0
    threshold: float = 0.5  # 모델만 쓸 때 이 값 미만이면 이어짐 (원본 기본값)


POLICIES: tuple[Policy, ...] = (
    Policy("now", rules=False, model=False),  # 2단계: 확정 글이 오면 넘긴다
    Policy("rules", rules=True, model=False),
    Policy("smart_turn", rules=False, model=True),
    Policy("combined", rules=True, model=True),  # 출하 설정: 0.3/0.7, 상한 2.0초
)
SWEEP: tuple[Policy, ...] = tuple(
    Policy(f"combined {low}/{high} cap {cap}", True, True, low=low, high=high, cap=cap)
    for low, high in ((0.2, 0.8), (0.3, 0.7), (0.5, 0.5))
    for cap in (1.6, 2.0, 2.5)
)


@dataclass
class Result:
    holds: int = 0
    cut: int = 0
    waits: list[float] = field(default_factory=list)

    @property
    def cut_rate(self) -> float | None:
        return self.cut / self.holds if self.holds else None

    @property
    def mean_wait(self) -> float | None:
        return sum(self.waits) / len(self.waits) if self.waits else None


def label_for(p: Pause, policy: Policy) -> str:
    """그 정책이 이 멈춤에서 내는 판정: end · continue."""
    if not policy.rules:
        if policy.model and p.score is not None and p.score < policy.threshold:
            return "continue"
        return "end"
    verdict = classify_turn_end(p.text, state="", question_kind=p.question_kind,
                                menu_names=p.menu_names, cont_wait_s=policy.cap)
    decision = combine_turn_end(verdict, p.score if policy.model else None,
                                low=policy.low, high=policy.high, cont_wait_s=policy.cap)
    return "continue" if decision.label == "continue" else "end"


def simulate(pauses: Iterable[Pause], policy: Policy, *, vad_s: float = VAD_S,
             text_s: float = TEXT_S, start_s: float = START_S) -> Result:
    result = Result()
    for p in pauses:
        commit = None
        if p.dur >= vad_s or p.label == "eot":
            waiting = label_for(p, policy) == "continue"
            commit = max(text_s, policy.cap) if waiting else text_s
        if p.label == "eot":
            assert commit is not None
            result.waits.append(commit)
            continue
        if not HOLD_RANGE[0] <= p.dur <= HOLD_RANGE[1]:
            continue
        result.holds += 1
        if commit is not None and p.dur + start_s > commit:
            result.cut += 1
    return result


def summarize(pauses: Sequence[Pause], policies: Sequence[Policy], **timing: float) -> list[dict]:
    """정책 × (전체 + 직전 질문 종류별) 표의 줄들."""
    groups = ["all", *sorted({p.question_kind for p in pauses})]
    rows = []
    for group in groups:
        chosen = [p for p in pauses if group == "all" or p.question_kind == group]
        for policy in policies:
            r = simulate(chosen, policy, **timing)
            rows.append({"group": group, "policy": policy.name, "holds": r.holds, "cut": r.cut,
                         "cut_rate": r.cut_rate, "eots": len(r.waits),
                         "mean_wait_s": r.mean_wait})
    return rows


# ---------------------------------------------------------------- 데이터 읽기


def _pcm(wav_bytes: bytes) -> bytes:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError("16kHz mono PCM16 WAV만 받는다")
        return w.readframes(w.getnframes())


def _upto(pcm: bytes, seconds: float) -> bytes:
    """처음부터 `seconds`까지. 오디오보다 길면 뒤에 무음을 붙인다(끝 무음이 짧은 녹음)."""
    n = int(round(seconds * SAMPLE_RATE)) * 2
    return pcm[:n] + bytes(max(0, n - len(pcm)))


def eot_bench_pauses(path: Path, model, *, vad_s: float = VAD_S) -> list[Pause]:
    """EoT-Bench 줄마다 100ms 이상 멈춤. 마지막이 eot, 앞은 hold(README 규칙)."""
    import pyarrow.parquet as pq  # 평가 때만 (`uv run --with pyarrow`)

    table = pq.read_table(path, columns=["id", "audio", "silence_spans", "words", "messages"])
    pauses = []
    for row in table.to_pylist():
        pcm = _pcm(row["audio"]["bytes"])
        prompts = [m["content"] for m in row["messages"] or [] if m["role"] == "assistant"]
        prompt = prompts[-1] if prompts and prompts[-1] else None
        kind = question_kind(prompt, "") if prompt else "none"
        spans = [s for s in row["silence_spans"] if s["end"] - s["start"] >= 0.1 - 1e-6]
        for i, span in enumerate(spans):
            label = "eot" if i == len(spans) - 1 else "hold"
            dur = span["end"] - span["start"]
            text = " ".join(w["word"] for w in row["words"] if w["end"] <= span["start"] + 0.05)
            score = None
            if model is not None and (dur >= vad_s or label == "eot"):
                score = model.p_complete(_upto(pcm, span["start"] + vad_s))
            pauses.append(Pause("eot-bench", row["id"], i, label, dur, text, kind, score))
    return pauses


def recording_pauses(wav_dir: Path, manifest: dict, model, *, menu_names: tuple[str, ...] = (),
                     vad: VadSettings | None = None) -> tuple[list[Pause], list[str]]:
    """녹음마다 우리 무음 감지(100ms 프레임)로 말 끝을 찾는다. 마지막 말 끝이 eot.

    돌려주는 두 번째 값은 목록과 말 끝 수가 안 맞아 마지막 말 끝만 쓴 녹음 id다.
    """
    vad = vad or VadSettings()
    frame = 100 * BYTES_PER_MS
    pauses, mismatched = [], []
    for item, info in sorted(manifest.items()):
        pcm = _pcm((wav_dir / f"{item}.wav").read_bytes())
        detector = SilenceDetector(vad)
        ends = [detector.offset_ms for i in range(0, len(pcm) - frame + 1, frame)
                if detector.feed(pcm[i:i + frame])]
        if not ends:
            mismatched.append(item)
            continue
        texts = list(info["texts"])
        if len(texts) != len(ends):
            mismatched.append(item)
            texts = [""] * (len(ends) - 1) + texts[-1:]
        kind = question_kind(info.get("question", ""), info.get("state", ""))
        for i, (end_ms, text) in enumerate(zip(ends, texts, strict=True)):
            if not text.strip():
                continue  # 글 없는 말 끝(잡음): 세션도 판정하지 않는다
            silence_start = end_ms - vad.end_silence_ms
            later = [s for s in detector.segment_starts if s > end_ms]
            label = "hold" if later else "eot"
            until_ms = later[0] if later else len(pcm) // BYTES_PER_MS  # 다시 말함 또는 녹음 끝
            dur = (until_ms - silence_start) / 1000
            score = model.p_complete(pcm[: end_ms * BYTES_PER_MS]) if model is not None else None
            pauses.append(Pause("recordings", item, i, label, dur, text, kind, score, menu_names))
    return pauses, mismatched


# ---------------------------------------------------------------- 출력


def _fmt(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.1f}%" if pct else f"{value:.2f}"


def markdown(rows: list[dict], title: str) -> str:
    lines = [f"### {title}", "", "| 묶음 | 정책 | 쉼 | 오끊김 | 오끊김률 | 끝 | 평균 대기(초) |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['group']} | {r['policy']} | {r['holds']} | {r['cut']} | "
                     f"{_fmt(r['cut_rate'], True)} | {r['eots']} | {_fmt(r['mean_wait_s'])} |")
    return "\n".join(lines)


def _write_pauses(path: Path, pauses: list[Pause], policies: Sequence[Policy]) -> None:
    """멈춤별 라벨·점수. 글은 쓰지 않는다."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        out = csv.writer(fh)
        out.writerow(["source", "item", "index", "label", "dur", "question_kind", "score",
                      *(p.name for p in policies)])
        for p in pauses:
            out.writerow([p.source, p.item, p.index, p.label, round(p.dur, 3), p.question_kind,
                          "" if p.score is None else round(p.score, 4),
                          *(label_for(p, policy) for policy in policies)])


def _private_out(path: Path) -> Path:
    """저장소의 `data/`·`private/`(옆에 pyproject.toml이 있는 폴더) 아래만 받는다."""
    resolved = path.resolve()
    if not any(d.name in ("data", "private") and (d.parent / "pyproject.toml").is_file()
               for d in resolved.parents):
        raise SystemExit(f"--out은 저장소의 data/ 또는 private/ 아래여야 해요: {path}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="말 끝 판정 오프라인 평가 (CI 밖)")
    parser.add_argument("--model", type=Path, help="Smart Turn ONNX. 없으면 점수 없이(규칙만)")
    parser.add_argument("--eot", type=Path, help="EoT-Bench 한국어 parquet")
    parser.add_argument("--recordings", type=Path, help="녹음 WAV 폴더 (16kHz mono)")
    parser.add_argument("--manifest", type=Path, help="녹음 목록 JSON (머리 주석)")
    parser.add_argument("--out", type=Path, required=True, help="결과 폴더 (data/·private/ 아래)")
    parser.add_argument("--vad-s", type=float, default=VAD_S)
    parser.add_argument("--text-s", type=float, default=TEXT_S)
    args = parser.parse_args(argv)
    out = _private_out(args.out)

    model = None
    if args.model is not None:
        from agenthan.voice.smart_turn import load_turn_model

        model = load_turn_model(args.model)
        if model is None:
            raise SystemExit(f"모델을 열지 못했어요: {args.model}")
    timing = {"vad_s": args.vad_s, "text_s": args.text_s}
    sets: list[tuple[str, list[Pause]]] = []
    if args.eot is not None:
        sets.append(("EoT-Bench 한국어 (LiveKit, CC-BY-4.0)",
                     eot_bench_pauses(args.eot, model, vad_s=args.vad_s)))
    if args.recordings is not None and args.manifest is not None:
        from agenthan.core.fakes import FakeStoreKnowledge

        menu = tuple(m.name_ko for m in FakeStoreKnowledge.from_fixture().list_menu())
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        pauses, mismatched = recording_pauses(args.recordings, manifest, model, menu_names=menu)
        if mismatched:
            print(f"말 끝 수가 목록과 달라 마지막 말 끝만 쓴 녹음: {', '.join(mismatched)}",
                  file=sys.stderr)
        sets.append((f"우리 녹음 {len(manifest)}개", pauses))

    report = [f"시간 모델: 말 끝 {args.vad_s}초, 확정 글 {args.text_s}초, 다시 말함 +{START_S}초"]
    for title, pauses in sets:
        report.append(markdown(summarize(pauses, POLICIES, **timing), title))
        report.append(markdown([r for r in summarize(pauses, SWEEP, **timing)
                                if r["group"] == "all"], f"{title} · 대역·상한 훑기"))
        slug = "eot-bench" if title.startswith("EoT") else "recordings"
        _write_pauses(out / f"{slug}-pauses.csv", pauses, POLICIES)
    text = "\n\n".join(report) + "\n"
    (out / "summary.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
