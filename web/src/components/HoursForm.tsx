import { useState } from "react";
import { DAYS, HOURS_FORM_TEXT as T, hoursProblem, type HoursInput, type HoursRow } from "../labels";

type Props = {
  /** 지금 영업시간: 무엇을 고쳤는지 비교하는 기준. */
  initial: HoursInput;
  /** 열 때 칸에 넣을 값(거절된 뒤 다시 열 때는 고쳤던 값). 없으면 initial. */
  start?: HoursInput;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (values: HoursInput) => void;
};

/**
 * 대화 영역에 뜨는 일주일 영업시간 시간표(쿠폰·가게 정보 블록과 같은 틀). 지금 영업시간으로 채워 열리고,
 * [미리 보기]는 고친 요일만 비서에게 넘겨 평소 승인 카드를 받는다. 저장은 승인 뒤에만.
 */
export function HoursForm({ initial, start, busy, onCancel, onSubmit }: Props) {
  const [values, setValues] = useState<HoursInput>(start ?? initial);
  const set = (day: string, patch: Partial<HoursRow>) => setValues((v) => ({ ...v, [day]: { ...v[day], ...patch } }));
  const problem = hoursProblem(initial, values);
  const touched = problem !== "바꿀 요일을 고쳐 주세요.";

  return (
    <form
      className="approval coupon-form hours-form"
      aria-label={T.heading}
      onSubmit={(e) => {
        e.preventDefault();
        if (!problem && !busy) onSubmit(values);
      }}
    >
      <header>
        <strong>{T.heading}</strong>
      </header>

      <div className="form-fields">
        <p className="form-hint">{T.hint}</p>
        <ul className="hours-table">
          {DAYS.map(([day, name]) => {
            const row = values[day];
            const changed = JSON.stringify(row) !== JSON.stringify(initial[day]);
            return (
              <li key={day} data-changed={changed || undefined}>
                <span className="hours-day">{name}</span>
                <label className="hours-closed">
                  <input
                    type="checkbox"
                    aria-label={`${name} ${T.closed}`}
                    checked={row.closed}
                    disabled={busy}
                    onChange={(e) => set(day, { closed: e.target.checked })}
                  />
                  {T.closed}
                </label>
                {row.closed ? (
                  <span className="hours-off">{T.closed}</span>
                ) : (
                  <span className="hours-times">
                    <input type="time" aria-label={`${name} ${T.open}`} value={row.open} disabled={busy} onChange={(e) => set(day, { open: e.target.value })} />
                    <span aria-hidden>~</span>
                    <input type="time" aria-label={`${name} ${T.close}`} value={row.close} disabled={busy} onChange={(e) => set(day, { close: e.target.value })} />
                  </span>
                )}
              </li>
            );
          })}
        </ul>
        {problem && touched && <p className="form-problem">{problem}</p>}
      </div>

      <footer>
        <button type="button" className="ghost" disabled={busy} onClick={onCancel}>
          {T.cancel}
        </button>
        <button type="submit" className="primary" disabled={busy || Boolean(problem)}>
          {T.submit}
        </button>
      </footer>
    </form>
  );
}
