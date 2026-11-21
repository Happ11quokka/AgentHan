// 비서 말풍선: 전화 주문이 들어오거나 확인 마감이 가까우면 오른쪽 비서 얼굴에서 나온다.
// 오른쪽 칸이 보이면 그 얼굴 아래에(Sidecar 안), 칸이 닫혀 있으면 화면 오른쪽 아래에 작은 얼굴과 함께 뜬다.
// 새 주문은 잠깐 떴다 사라지고(`ARRIVED_SHOW_MS`), 마감 임박·지남은 닫을 때까지 남는다.
import type { Nudge } from "../orders/orderNudges";
import { Icon } from "./Icon";

export const ARRIVED_SHOW_MS = 8_000;

/** 띄운 말풍선. at: 띄운 시각(같은 글이 또 와도 다시 띄우고 타이머를 새로 건다) */
export type ShownNudge = Nudge & { at: number };

type Props = {
  nudge: ShownNudge;
  /** 오른쪽 칸이 닫혀 있어 화면 구석에 띄운다 */
  floating?: boolean;
  onOpen: () => void;
  onClose: () => void;
};

export function AgentNudge({ nudge, floating = false, onOpen, onClose }: Props) {
  return (
    <div className="nudge" data-floating={floating} data-kind={nudge.kind} role="status" aria-label="비서 알림">
      {floating && <img className="nudge-face" src="/avatar/attention.png" alt="" />}
      <div className="nudge-bubble" key={nudge.at}>
        <p>{nudge.text}</p>
        <div className="nudge-actions">
          <button type="button" className="nudge-open" onClick={onOpen}>
            주문 보기
          </button>
          <button type="button" className="nudge-close" aria-label="알림 닫기" onClick={onClose}>
            <Icon name="close" size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
