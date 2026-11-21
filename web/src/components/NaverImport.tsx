// 네이버 플레이스 불러오기(시연 컨셉). 실제 연동은 없다: 2.5초 찾는 척한 뒤 승인 창을 띄우고,
// 승인하면 이미 있는 더미 데이터를 보이기 시작한다. 메뉴는 메뉴판 사진으로 올리므로 가져오지 않는다.
import { useEffect, useRef, useState } from "react";

/** 버튼을 누르고 승인 창이 뜨기까지 기다리는 시간. */
export const NAVER_LOOKUP_MS = 2500;

/** 승인 창에 보이는 가져올 항목. 메뉴는 빠진다. */
export const NAVER_ITEMS: Array<[string, string]> = [
  ["가게 정보", "이름·업종·주소·영업시간·대표키워드"],
  ["쿠폰", "진행 중인 쿠폰"],
  ["리뷰", "방문자 리뷰와 답글"],
  ["통계", "방문·리뷰 통계"],
];

type Phase = "idle" | "looking" | "asking";

type ButtonProps = {
  onImported: () => void;
  /** 버튼 모양(기본: 알약). 왼쪽 목록에서는 [새 대화] 자리를 그대로 쓴다. */
  className?: string;
  /** 접힌 목록: N 아이콘만 보인다. */
  compact?: boolean;
};

export function NaverImportButton({ onImported, className = "naver-import", compact = false }: ButtonProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const timer = useRef<number | undefined>(undefined);
  const allow = useRef<HTMLButtonElement | null>(null);

  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    if (phase === "asking") allow.current?.focus();
  }, [phase]);

  const start = () => {
    setPhase("looking");
    timer.current = window.setTimeout(() => setPhase("asking"), NAVER_LOOKUP_MS);
  };

  return (
    <>
      <button
        type="button"
        className={className}
        onClick={start}
        disabled={phase !== "idle"}
        aria-label={compact ? "네이버 플레이스 불러오기" : undefined}
        title={compact ? "네이버 플레이스 불러오기" : undefined}
      >
        <span className="naver-n" aria-hidden>N</span>
        {!compact && (phase === "looking" ? "찾는 중…" : "네이버 플레이스 불러오기")}
      </button>
      {phase === "asking" && (
        <div className="naver-dialog-scrim" onKeyDown={(e) => e.key === "Escape" && setPhase("idle")}>
          <div className="naver-dialog" role="dialog" aria-modal="true" aria-labelledby="naver-dialog-title">
            <div className="naver-dialog-head">
              <span className="naver-n" aria-hidden>N</span>
              <div>
                <strong id="naver-dialog-title">네이버 플레이스에서 정보를 불러올까요?</strong>
                <p>사장님 비서가 네이버 스마트플레이스에 연결해 아래 정보를 가져와요.</p>
              </div>
            </div>
            <ul>
              {NAVER_ITEMS.map(([name, detail]) => (
                <li key={name}>
                  <b>{name}</b>
                  <span>{detail}</span>
                </li>
              ))}
            </ul>
            <div className="naver-dialog-actions">
              <button type="button" className="ghost" onClick={() => setPhase("idle")}>
                거절
              </button>
              <button
                ref={allow}
                type="button"
                className="primary"
                onClick={() => {
                  setPhase("idle");
                  onImported();
                }}
              >
                승인
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/** 불러오기 전 홈·리뷰·통계 탭 자리. */
export function NaverEmpty() {
  return (
    <div className="soon naver-empty">
      <strong>아직 불러온 가게 정보가 없어요</strong>
      <p>왼쪽 위 [네이버 플레이스 불러오기]를 누르면 가게 정보·리뷰·통계를 가져와요.</p>
    </div>
  );
}
