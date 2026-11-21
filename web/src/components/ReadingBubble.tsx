import { useElapsed } from "../useElapsed";
import { ProgressBar } from "./ProgressBar";

/**
 * 사진을 보내고 비서 답을 기다리는 동안 대화창의 비서 말풍선 자리(•••)에 보이는 진행 표시.
 * 메뉴판 읽기는 보통 7~11초라 끝을 모른다: 천천히 차오르다 95%에서 멈춘다.
 */
export function ReadingBubble({ since }: { since: number }) {
  const seconds = useElapsed(since);
  const ratio = 0.95 * (1 - Math.exp(-seconds / 5));
  const detail = seconds < 4 ? "메뉴 이름과 가격을 읽고 있어요" : seconds < 8 ? "음식 사진을 찾고 메뉴를 정리하고 있어요" : "거의 다 됐어요";
  return <ProgressBar className="in-chat" title="메뉴판 사진을 읽고 있어요" detail={detail} seconds={seconds} ratio={ratio} />;
}
