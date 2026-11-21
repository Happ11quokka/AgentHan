import { useId, useState } from "react";
import { BUSINESS_FORM_TEXT as T, BUSINESS_NAMES, businessChanges, businessProblem, type BusinessInput } from "../labels";

type Props = {
  initial: BusinessInput;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (changes: Partial<BusinessInput>) => void;
  /** [추천 글 받기]: 소개글 초안. 실패하면 던진다. */
  onDraft: () => Promise<string>;
};

/**
 * 대화 영역에 뜨는 가게 기본 정보 입력 블록(쿠폰 블록과 같은 틀). 지금 값으로 채워 열리고,
 * [미리 보기]는 고친 칸만 비서에게 넘겨 평소 승인 카드를 받는다. 저장은 승인 뒤에만.
 */
export function BusinessInfoForm({ initial, busy, onCancel, onSubmit, onDraft }: Props) {
  const [values, setValues] = useState<BusinessInput>(initial);
  const [drafting, setDrafting] = useState(false);
  const [draftNote, setDraftNote] = useState<string | null>(null);
  const id = useId();
  const set = (key: keyof BusinessInput, value: string) => setValues((v) => ({ ...v, [key]: value }));
  const changes = businessChanges(initial, values);
  const problem = businessProblem(changes);
  const locked = busy || drafting;

  const draft = async () => {
    setDrafting(true);
    setDraftNote(null);
    try {
      set("introduction", await onDraft());
      setDraftNote(T.drafted);
    } catch {
      setDraftNote(T.draftFailed);
    } finally {
      setDrafting(false);
    }
  };

  const line = (key: "name" | "address" | "phone", type = "text") => (
    <label className="field" htmlFor={`${id}-${key}`}>
      <span>{BUSINESS_NAMES[key]}</span>
      <input
        id={`${id}-${key}`}
        type={type}
        value={values[key]}
        placeholder={T.placeholders[key]}
        disabled={locked}
        onChange={(e) => set(key, e.target.value)}
      />
    </label>
  );

  return (
    <form
      className="approval coupon-form business-form"
      aria-label={T.heading}
      onSubmit={(e) => {
        e.preventDefault();
        if (!problem && !locked) onSubmit(changes);
      }}
    >
      <header>
        <strong>{T.heading}</strong>
      </header>

      <div className="form-fields">
        <p className="form-hint">{T.hint}</p>
        {line("name")}
        {line("address")}
        {line("phone", "tel")}
        <div className="field">
          <span className="field-head">
            <label htmlFor={`${id}-introduction`}>{BUSINESS_NAMES.introduction}</label>
            <button type="button" className="ghost small" disabled={locked} onClick={() => void draft()}>
              {drafting ? T.drafting : T.draft}
            </button>
          </span>
          <textarea
            id={`${id}-introduction`}
            rows={4}
            value={values.introduction}
            placeholder={T.placeholders.introduction}
            disabled={locked}
            onChange={(e) => set("introduction", e.target.value)}
          />
          <span className="field-foot">
            <span role="status">{draftNote}</span>
            <span>{values.introduction.trim().length}자</span>
          </span>
        </div>
        {problem && Object.keys(changes).length > 0 && <p className="form-problem">{problem}</p>}
      </div>

      <footer>
        <button type="button" className="ghost" disabled={busy} onClick={onCancel}>
          {T.cancel}
        </button>
        <button type="submit" className="primary" disabled={locked || Boolean(problem)}>
          {T.submit}
        </button>
      </footer>
    </form>
  );
}
