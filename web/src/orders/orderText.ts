// 전화 주문 서랍의 작은 글 규칙(비서 화면은 한국어만).

/** 서랍 아래 링크: 주방 태블릿용 사장님 화면(소리·화면 꺼짐 방지)을 새 탭으로 */
export function ownerScreenLink(key: string) {
  return key ? `/owner?key=${encodeURIComponent(key)}&lang=ko` : "/owner?lang=ko";
}

export const won = (n: number) => `${n.toLocaleString("ko-KR")}원`;

/** 받은 지 얼마나 됐나: "방금", "3분 전", "1시간 12분 전" */
export function agoText(receivedAt: string, nowMs: number) {
  const min = Math.floor((nowMs - Date.parse(receivedAt)) / 60_000);
  if (Number.isNaN(min) || min < 1) return "방금";
  if (min < 60) return `${min}분 전`;
  return `${Math.floor(min / 60)}시간${min % 60 ? ` ${min % 60}분` : ""} 전`;
}
