import { approval, draftStatus, featureName, noteBadges, pendingLines, won } from "../labels";
import type { Pending, Place } from "../types";

type Props = {
  place: Place;
  pending: Pending;
  onAnswer: (text: string) => void;
};

/** 저장 전 변경. [저장]·[취소]는 비서에게 그 답을 말한 것과 같다. */
export function ApprovalCard({ place, pending, onAnswer }: Props) {
  const photo = pending.kind === "photo";
  const { blocked, answer, need } = approval(pending);
  const title = photo ? "사진에서 읽은 메뉴" : `${featureName(pending.feature)} 바꾸기`;

  return (
    <section className="approval" aria-label="저장 전 확인">
      <header>
        <span className="approval-tag">저장 전 확인</span>
        <strong>{title}</strong>
      </header>

      {photo ? (
        <ol className="drafts">
          {(pending.drafts ?? []).map((d, i) => {
            const status = draftStatus(d);
            return (
              <li key={d.photo_url ?? `${i}-${d.name_ko}`} className="draft">
                {d.photo_url ? <img src={d.photo_url} alt="" /> : <span className="draft-no-photo">{i + 1}</span>}
                <div className="draft-body">
                  <div className="draft-name">
                    <strong>{d.name_ko || "이름 없음"}</strong>
                    {d.name_native && <small>{d.name_native}</small>}
                  </div>
                  <div className="draft-meta">
                    {d.price !== null && <span className="draft-price">{won(d.price)}</span>}
                    <span className="chip" data-tone={status.tone}>
                      {status.text}
                    </span>
                    {noteBadges(d.notes).map((b) => (
                      <span key={b} className="chip" data-tone="note">
                        {b}
                      </span>
                    ))}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <ul className="changes">
          {pendingLines(place, pending).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}

      {need && <p className="approval-question">{need}</p>}

      <footer>
        <button type="button" className="ghost" onClick={() => onAnswer("취소")}>
          취소
        </button>
        <button type="button" className="primary" disabled={blocked} onClick={() => onAnswer(answer)}>
          저장
        </button>
      </footer>
    </section>
  );
}
