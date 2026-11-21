import { useId, useState } from "react";
import type { OnboardingInput, ShopInfo } from "../types";

/** 짧은 업종 선택지. 없으면 [직접 입력]. 비서가 메뉴 사진 인식·추천·설명에 이 말을 그대로 쓴다. */
const CATEGORY_OPTIONS = ["카페", "디저트·베이커리", "한식", "분식", "중식", "일식", "양식", "치킨·주점"];
const CUSTOM = "직접 입력";
const MENU_ROWS = 3;

type MenuInput = { name: string; price: string };
type Props = {
  /** 이미 저장된 이름·업종(있으면 채워 둔다). */
  initial: ShopInfo;
  /** 저장. 성공하면 null, 실패하면 보일 문장. */
  onSubmit: (values: OnboardingInput) => Promise<string | null>;
};

/** "9,000", "9000원" → 9000. 양의 정수가 아니면 null. */
function parsePrice(text: string): number | null {
  const digits = text.replace(/[\s,원]/g, "");
  if (!/^\d+$/.test(digits)) return null;
  const value = Number(digits);
  return value > 0 ? value : null;
}

/** 처음 쓰는 사장님의 시작 단계(#94): 가게 이름·업종·대표 메뉴(건너뛰기 가능). 넣은 값만 보낸다. */
export function Onboarding({ initial, onSubmit }: Props) {
  const id = useId();
  const preset = CATEGORY_OPTIONS.includes(initial.category);
  const [name, setName] = useState(initial.name);
  const [choice, setChoice] = useState(preset ? initial.category : initial.category ? CUSTOM : "");
  const [custom, setCustom] = useState(preset ? "" : initial.category);
  const [menus, setMenus] = useState<MenuInput[]>(() => Array.from({ length: MENU_ROWS }, () => ({ name: "", price: "" })));
  const [problem, setProblem] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const category = (choice === CUSTOM ? custom : choice).trim();
  const setMenu = (i: number, key: keyof MenuInput, value: string) => setMenus((rows) => rows.map((r, j) => (j === i ? { ...r, [key]: value } : r)));

  const submit = async (withMenus: boolean) => {
    if (busy) return;
    const shopName = name.trim();
    if (!shopName) return setProblem("가게 이름을 넣어 주세요.");
    if (!category) return setProblem("업종을 골라 주세요.");
    const picked: OnboardingInput["menus"] = [];
    if (withMenus) {
      for (const [i, row] of menus.entries()) {
        if (!row.name.trim() && !row.price.trim()) continue;
        const price = parsePrice(row.price);
        if (!row.name.trim() || price === null) return setProblem(`메뉴 ${i + 1}의 이름과 가격(원)을 함께 넣어 주세요.`);
        picked.push({ name: row.name.trim(), price });
      }
    }
    setProblem(null);
    setFailed(null);
    setBusy(true);
    const error = await onSubmit({ name: shopName, category, menus: picked });
    setBusy(false);
    setFailed(error);
  };

  return (
    <div className="onboarding">
      <form
        className="approval onboarding-form"
        aria-label="가게 시작하기"
        onSubmit={(e) => {
          e.preventDefault();
          void submit(true);
        }}
      >
        <header>
          <strong>처음 오셨네요. 가게부터 알려 주세요</strong>
          <p>업종을 알면 비서가 메뉴 사진을 더 잘 읽고 설명도 가게에 맞게 써요.</p>
        </header>

        <div className="form-fields">
          <label className="field" htmlFor={`${id}-name`}>
            <span>가게 이름</span>
            <input id={`${id}-name`} type="text" value={name} disabled={busy} onChange={(e) => setName(e.target.value)} />
          </label>

          <div className="field">
            <span id={`${id}-category`}>업종</span>
            <div className="segmented chips" role="radiogroup" aria-labelledby={`${id}-category`}>
              {[...CATEGORY_OPTIONS, CUSTOM].map((option) => (
                <button key={option} type="button" role="radio" aria-checked={choice === option} disabled={busy} onClick={() => setChoice(option)}>
                  {option}
                </button>
              ))}
            </div>
            {choice === CUSTOM && (
              <input
                aria-label="업종 직접 입력"
                type="text"
                value={custom}
                placeholder="예: 마라탕 전문점"
                disabled={busy}
                onChange={(e) => setCustom(e.target.value)}
              />
            )}
          </div>

          <fieldset className="field onboarding-menus">
            <legend>대표 메뉴 (1~3개, 나중에 넣어도 돼요)</legend>
            {menus.map((row, i) => (
              <div className="field-row" key={i}>
                <input aria-label={`메뉴 ${i + 1} 이름`} type="text" value={row.name} disabled={busy} onChange={(e) => setMenu(i, "name", e.target.value)} />
                <input
                  aria-label={`메뉴 ${i + 1} 가격(원)`}
                  type="text"
                  inputMode="numeric"
                  value={row.price}
                  disabled={busy}
                  onChange={(e) => setMenu(i, "price", e.target.value)}
                />
              </div>
            ))}
          </fieldset>

          <p className="onboarding-hint">메뉴판 사진은 시작한 뒤 왼쪽 [메뉴 사진 올리기]로 올리면 비서가 메뉴를 읽어 드려요.</p>
          {problem && <p className="form-problem">{problem}</p>}
          {failed && (
            <p className="form-problem" role="alert">
              {failed}
            </p>
          )}
        </div>

        <footer>
          <button type="button" className="ghost" disabled={busy} onClick={() => void submit(false)}>
            메뉴는 나중에
          </button>
          <button type="submit" className="primary" disabled={busy}>
            시작하기
          </button>
        </footer>
      </form>
    </div>
  );
}
