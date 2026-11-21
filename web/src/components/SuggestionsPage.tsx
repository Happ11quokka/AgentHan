import type { Suggestion, Suggestions } from "../types";
import type { Loaded } from "../useLoad";
import { LoadFailed } from "./LoadFailed";

type Props = {
  loaded: Loaded<Suggestions>;
  /** 아직 치우지 않은 카드만. */
  items: Suggestion[];
  busy: boolean;
  onAct: (item: Suggestion) => void;
  onDismiss: (item: Suggestion) => void;
};

const monthDay = (iso: string) => iso.slice(5).replace("-", "/");

/** 왼쪽 [비서 제안]: 지난주 통계와 리뷰로 고른 해 볼 일. 버튼은 비서에게 말로 맡기거나 사이드카 탭을 연다. */
export function SuggestionsPage({ loaded, items, busy, onAct, onDismiss }: Props) {
  const { data, failed, reload } = loaded;
  return (
    <section className="suggest" aria-label="비서 제안">
      <header className="chat-head suggest-head">
        <strong>비서 제안</strong>
        {data && <span>{monthDay(data.end)}까지 일주일 통계와 리뷰를 보고 골랐어요</span>}
      </header>
      <div className="suggest-body">
        {failed ? (
          <LoadFailed message="제안을 불러오지 못했어요." onRetry={reload} />
        ) : !data ? (
          <p className="loading">불러오는 중…</p>
        ) : items.length === 0 ? (
          <p className="empty">지금은 해 볼 만한 일을 찾지 못했어요. 통계가 바뀌면 다시 골라 둘게요.</p>
        ) : (
          items.map((item) => (
            <article key={item.kind} className="suggest-card">
              <div className="suggest-meta">
                <span className="suggest-feature">{item.feature_name}</span>
                <span>{item.source}</span>
              </div>
              <h3>{item.title}</h3>
              <p>{item.evidence}</p>
              <div className="suggest-buttons">
                <button type="button" className="primary" disabled={busy && Boolean(item.ask)} onClick={() => onAct(item)}>
                  {item.action}
                </button>
                <button type="button" className="ghost" onClick={() => onDismiss(item)}>
                  나중에
                </button>
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
