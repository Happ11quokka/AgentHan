import type { FeatureCount, ReviewStats, ReviewStatsPeriod } from "../types";
import type { Loaded } from "../useLoad";
import { LoadFailed } from "./LoadFailed";

const REVIEW_PERIOD_TEXT: Array<[ReviewStatsPeriod, string]> = [
  ["day", "일간"],
  ["week", "주간"],
  ["month", "월간"],
];
const ASK_DISLIKED = "요즘 아쉬운 리뷰 정리해 줘";

const monthDay = (iso: string) => iso.slice(5).replace("-", "/");

function changeText(change: number | null) {
  if (change === null) return null;
  if (change === 0) return "지난 기간과 같아요";
  return `지난 기간보다 ${Math.abs(change)} ${change > 0 ? "늘었어요" : "줄었어요"}`;
}

function Top({ title, hint, tone, rows, empty }: { title: string; hint: string; tone: "good" | "bad"; rows: FeatureCount[]; empty: string }) {
  return (
    <section className="shop-section review-top" data-tone={tone}>
      <h3>
        <span className="review-top-face" aria-hidden />
        {title}
      </h3>
      <p className="review-top-hint">{hint}</p>
      {rows.length === 0 ? (
        <p className="empty">{empty}</p>
      ) : (
        <ol aria-label={title}>
          {rows.map((r, i) => (
            <li key={r.name}>
              <span className="review-top-rank">{i + 1}</span>
              <span className="review-top-name">
                <strong>{r.name}</strong>
                <small>{r.theme}</small>
              </span>
              <span className="review-top-count">
                <span>
                  <b>{r.count}</b>건
                </span>
                {changeText(r.change) && <small data-trend={r.change! > 0 ? "up" : r.change! < 0 ? "down" : "flat"}>{changeText(r.change)}</small>}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

type Props = {
  loaded: Loaded<ReviewStats>;
  period: ReviewStatsPeriod;
  onPeriod: (p: ReviewStatsPeriod) => void;
  /** 아쉬운 리뷰 정리를 비서에게 말로 맡긴다. */
  onAsk?: (text: string) => void;
};

/** 통계 탭 › AI 리뷰 통계: 네이버 플레이스 AI 리뷰 통계처럼 좋아요·아쉬워요 TOP3와 테마별 합계. */
export function ReviewStatsView({ loaded, period, onPeriod, onAsk }: Props) {
  const { data, failed, reload } = loaded;
  const max = Math.max(1, ...(data?.themes ?? []).map((t) => t.positive + t.negative));
  return (
    <div className="review-stats">
      <div className="stats-period">
        <p className="stats-range">{data && data.period === period ? `${monthDay(data.start)} ~ ${monthDay(data.end)} · 리뷰 ${data.review_count}개` : " "}</p>
        <div className="underline-radios" role="radiogroup" aria-label="기간">
          {REVIEW_PERIOD_TEXT.map(([key, label]) => (
            <button key={key} type="button" role="radio" aria-checked={period === key} onClick={() => onPeriod(key)}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <section className="review-stats-about" aria-label="AI 리뷰 통계란">
        <strong>AI 리뷰 통계란</strong>
        <ul>
          <li>리뷰 문장을 맛·친절 같은 특징별로 나눠, 손님이 좋아한 점과 아쉬워한 점을 세요.</li>
          <li>뜻이 담긴 문장만 세니 리뷰 수와 합계가 다를 수 있어요.</li>
        </ul>
      </section>

      {failed ? (
        <LoadFailed message="리뷰 통계를 불러오지 못했어요." onRetry={reload} />
      ) : !data || data.period !== period ? (
        <p className="loading">불러오는 중…</p>
      ) : (
        <>
          <Top title="좋아요 키워드 TOP 3" hint="손님이 특히 만족하는 점이에요. 홍보할 때 강조해 보세요." tone="good" rows={data.positive} empty="이 기간에는 좋아요로 셀 문장이 없어요." />
          <Top title="아쉬워요 키워드 TOP 3" hint="손님이 아쉬워하는 점이에요. 고칠 여지가 있는지 살펴보세요." tone="bad" rows={data.negative} empty="이 기간에는 아쉬워요로 셀 문장이 없어요." />
          {onAsk && data.negative.length > 0 && (
            <button type="button" className="ghost review-stats-ask" onClick={() => onAsk(ASK_DISLIKED)}>
              아쉬운 리뷰 비서에게 정리 맡기기
            </button>
          )}
          {data.themes.length > 0 && (
            <section className="shop-section review-themes">
              <h3>테마별 좋아요·아쉬워요</h3>
              <ul aria-label="테마별 좋아요·아쉬워요">
                {data.themes.map((t) => (
                  <li key={t.name}>
                    <span>{t.name}</span>
                    <span className="review-theme-bar" role="img" aria-label={`좋아요 ${t.positive}, 아쉬워요 ${t.negative}`}>
                      <i data-tone="good" style={{ width: `${(t.positive / max) * 100}%` }} />
                      <i data-tone="bad" style={{ width: `${(t.negative / max) * 100}%` }} />
                    </span>
                    <span className="review-theme-count">
                      {t.positive} · {t.negative}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
