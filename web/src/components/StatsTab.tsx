import {
  changeText,
  countText,
  hourText,
  METRIC_TEXT,
  NEW_KEYWORD_TEXT,
  peakText,
  PERIOD_TEXT,
  percentText,
  quietText,
  shortDay,
} from "../labels";
import type { Period, ReportView } from "../types";
import type { Loaded } from "../useLoad";
import { LoadFailed } from "./LoadFailed";

type Props = { loaded: Loaded<ReportView>; period: Period; onPeriod: (p: Period) => void };

function Bars({ values, labels, peak }: { values: number[]; labels: string[]; peak?: number | null }) {
  const max = Math.max(1, ...values);
  return (
    <div className="bars" role="img" aria-label={labels.map((l, i) => `${l} ${values[i]}`).join(", ")}>
      {values.map((v, i) => (
        <div key={i} className="bar" data-peak={i === peak} title={`${labels[i]} ${v}`}>
          <span style={{ height: `${Math.max(4, (v / max) * 100)}%` }} />
        </div>
      ))}
    </div>
  );
}

export function StatsTab({ loaded, period, onPeriod }: Props) {
  const { data, failed, reload } = loaded;
  return (
    <div className="stats">
      <div className="segmented" role="radiogroup" aria-label="기간">
        {(Object.keys(PERIOD_TEXT) as Period[]).map((p) => (
          <button key={p} type="button" role="radio" aria-checked={period === p} onClick={() => onPeriod(p)}>
            {PERIOD_TEXT[p]}
          </button>
        ))}
      </div>

      {failed ? (
        <LoadFailed message="통계를 불러오지 못했어요." onRetry={reload} />
      ) : !data || data.period !== period ? (
        <p className="loading">불러오는 중…</p>
      ) : (
        <>
          <p className="stats-range">{data.end.slice(5).replace("-", "/")}까지 {PERIOD_TEXT[period]} 기준</p>

          <ul className="cards">
            {data.cards.map((c) => (
              <li key={c.key} className="card">
                <span>{METRIC_TEXT[c.key]}</span>
                <strong>{countText(c.value)}</strong>
                <small data-trend={c.change === null ? "none" : c.change > 0 ? "up" : c.change < 0 ? "down" : "flat"}>
                  {changeText(c.change, period)}
                </small>
              </li>
            ))}
          </ul>

          {data.tip && (
            <section className="tip">
              <span>이번에 해 볼 일</span>
              <p>{data.tip}</p>
            </section>
          )}

          <section className="stats-section">
            <h3>손님이 들어온 곳</h3>
            <ul className="channels">
              {data.channels.map((c) => (
                <li key={c.name}>
                  <span className="channel-name">{c.name}</span>
                  <span className="channel-bar">
                    <span style={{ width: `${c.share_percent}%` }} />
                  </span>
                  <span className="channel-share">{percentText(c.share_percent)}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="stats-section">
            <h3>시간대별 조회</h3>
            <Bars values={data.hourly} labels={data.hourly.map((_, h) => hourText(h))} peak={data.peak_hour} />
            <div className="bars-axis" aria-hidden>
              <span>0시</span>
              <span>12시</span>
              <span>23시</span>
            </div>
            <p className="stats-note">{peakText(data.peak_hour)}</p>
            {data.quiet_hour !== null && <p className="stats-note">{quietText(data.quiet_hour)}</p>}
          </section>

          {period === "week" && (
            <section className="stats-section">
              <h3>요일별 조회</h3>
              <Bars values={data.weekday.map((d) => d.views)} labels={data.weekday.map((d) => shortDay(d.day))} />
              <div className="bars-axis days" aria-hidden>
                {data.weekday.map((d) => (
                  <span key={d.day}>{shortDay(d.day)}</span>
                ))}
              </div>
            </section>
          )}

          <section className="stats-section">
            <h3>손님이 찾은 말</h3>
            <ul className="keywords">
              {data.keywords.map((k) => (
                <li key={k.name}>
                  <span>{k.name}</span>
                  {k.new && <span className="chip" data-tone="new">{NEW_KEYWORD_TEXT}</span>}
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
