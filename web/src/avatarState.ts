export const AVATAR_STATES = ["idle", "listening", "thinking", "speaking", "attention"] as const;
export type AvatarState = (typeof AVATAR_STATES)[number];

type AvatarSignals = {
  hasInput: boolean;
  busy: boolean;
  hasPendingApproval: boolean;
};

/** busy 동안 승인 카드는 숨겨진다. 보이는 승인 대기는 입력 내용보다 우선한다. */
export function avatarState({ hasInput, busy, hasPendingApproval }: AvatarSignals): AvatarState {
  // 현재 대화 API는 JSON 한 번으로 응답한다. 응답 단계를 알 수 없어 busy는 speaking으로만 쓴다.
  // thinking은 AgentAvatar의 state prop으로 표시할 수 있고, 별도의 대기 상태를 추측하지 않는다.
  if (busy) return "speaking";
  if (hasPendingApproval) return "attention";
  if (hasInput) return "listening";
  return "idle";
}
