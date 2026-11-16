// 자막 목록 따라가기 (5단계 티켓 09). 새 글이 오면 맨 아래로 내려 준다.
// 손님이 위로 올려 예전 글을 보는 중이면(맨 아래에서 FOLLOW_SLACK_PX보다 멀면) 끌어내리지 않는다.
export const FOLLOW_SLACK_PX = 48

export function nearBottom(scrollHeight: number, scrollTop: number, clientHeight: number): boolean {
  return scrollHeight - scrollTop - clientHeight <= FOLLOW_SLACK_PX
}
