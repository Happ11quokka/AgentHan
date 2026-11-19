import { useId, useState } from "react";
import { COUPON_FORM_TEXT as T, couponPreview, couponProblem, type CouponInput } from "../labels";

type Props = {
  initial: CouponInput;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (values: CouponInput) => void;
};

/** 대화 영역에 뜨는 쿠폰 입력 블록. [미리 보기]는 값을 비서에게 넘겨 평소 승인 카드를 받는다. */
export function CouponForm({ initial, busy, onCancel, onSubmit }: Props) {
  const [values, setValues] = useState<CouponInput>(initial);
  const id = useId();
  const set = <K extends keyof CouponInput>(key: K, value: CouponInput[K]) => setValues((v) => ({ ...v, [key]: value }));
  const problem = couponProblem(values);
  const percent = values.discount_type === "percent";

  return (
    <form
      className="approval coupon-form"
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
        <label className="field" htmlFor={`${id}-title`}>
          <span>{T.title}</span>
          <input
            id={`${id}-title`}
            type="text"
            value={values.title}
            placeholder={T.titlePlaceholder}
            disabled={busy}
            onChange={(e) => set("title", e.target.value)}
          />
        </label>

        <div className="field-row">
          <div className="field">
            <span id={`${id}-type`}>{T.type}</span>
            <div className="segmented" role="radiogroup" aria-labelledby={`${id}-type`}>
              {(["amount", "percent"] as const).map((type) => (
                <button
                  key={type}
                  type="button"
                  role="radio"
                  aria-checked={values.discount_type === type}
                  disabled={busy}
                  onClick={() => set("discount_type", type)}
                >
                  {type === "amount" ? T.amount : T.percent}
                </button>
              ))}
            </div>
          </div>
          <label className="field" htmlFor={`${id}-value`}>
            <span>{T.value}</span>
            <span className="unit-input">
              <input
                id={`${id}-value`}
                aria-label={T.value}
                type="number"
                inputMode="numeric"
                min={1}
                max={percent ? 100 : undefined}
                step={1}
                value={values.discount_value}
                disabled={busy}
                onChange={(e) => set("discount_value", e.target.value)}
              />
              <em aria-hidden>{percent ? T.percent : T.amount}</em>
            </span>
          </label>
        </div>

        <div className="field-row">
          <label className="field" htmlFor={`${id}-start`}>
            <span>{T.start}</span>
            <input id={`${id}-start`} type="date" value={values.start_date} disabled={busy} onChange={(e) => set("start_date", e.target.value)} />
          </label>
          <label className="field" htmlFor={`${id}-end`}>
            <span>{T.end}</span>
            <input id={`${id}-end`} type="date" value={values.end_date} disabled={busy} onChange={(e) => set("end_date", e.target.value)} />
          </label>
        </div>

        <p className="coupon-preview">{couponPreview(values)}</p>
        {problem && <p className="form-problem">{problem}</p>}
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
