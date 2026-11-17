// 비서 답 한 개를 말풍선 모양으로. 서버 답은 CLI·평가와 같은 글이라, 화면에 필요 없는
// 시스템 줄(더미 저장소 안내, 저장·조회 JSON, 승인 질문의 레코드 JSON)은 여기서만 숨긴다.
// 굵게(**), 목록(- 와 1.), 인용(>)만 모양으로 보이고 나머지 마크다운은 글자 그대로 둔다.
import { Fragment, type ReactNode } from "react";

const DUMMY_NOTE = /\s*\((?:더미 저장소|실제 연동 없음|실제 발송 없음)[^)]*\)/g;
// 괄호 없이 문장으로 붙는 더미 안내("… 저장했어요. 실제 발송은 하지 않았어요.").
const DUMMY_SENTENCE = /\s*실제 (?:발송|반영|연동)은 하지 않았어요\.?/g;
const SYSTEM_LINE = /^(?:더미 (?:저장 확인|조회 결과)|네 \/ 취소)/;
const JSON_LINE = /^\s*(?:[[{]|true$|false$)/;

/**
 * 보여 줄 줄만. 승인 질문 아래 레코드 줄과 저장·조회 결과 JSON을 뺀다.
 * lead: 첫 문단만. 같은 내용을 승인 카드가 보여 주고 있을 때 말풍선에서 인용·뒤따르는 질문을 뺀다.
 */
export function visibleLines(text: string, { lead = false }: { lead?: boolean } = {}): string[] {
  const lines = text
    .split("\n")
    .filter((line) => !SYSTEM_LINE.test(line.trim()) && !JSON_LINE.test(line))
    .map((line) => line.replace(DUMMY_NOTE, "").replace(DUMMY_SENTENCE, "").trimEnd());
  if (!lead) return lines;
  const start = lines.findIndex((line) => line.trim());
  if (start < 0) return lines;
  const stop = lines.findIndex((line, i) => i > start && (!line.trim() || /^\s*>/.test(line)));
  return lines.slice(start, stop < 0 ? undefined : stop);
}

function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
      <strong key={i}>{part.slice(2, -2)}</strong>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  );
}

type Block =
  | { kind: "p"; lines: string[] }
  | { kind: "quote"; lines: string[] }
  | { kind: "ul" | "ol"; items: string[] };

function blocks(lines: string[]): Block[] {
  const out: Block[] = [];
  for (const raw of lines) {
    const line = raw.trim();
    const last = out[out.length - 1];
    if (!line) {
      out.push({ kind: "p", lines: [] });
      continue;
    }
    const bullet = line.match(/^[-•]\s+(.*)$/);
    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    const quote = line.match(/^>\s?(.*)$/);
    if (bullet || numbered) {
      const kind = bullet ? "ul" : "ol";
      const item = (bullet ?? numbered)![1];
      if (last && last.kind === kind) last.items.push(item);
      else out.push({ kind, items: [item] });
    } else if (quote) {
      if (last && last.kind === "quote") last.lines.push(quote[1]);
      else out.push({ kind: "quote", lines: [quote[1]] });
    } else if (last && last.kind === "p" && last.lines.length) {
      last.lines.push(line);
    } else {
      out.push({ kind: "p", lines: [line] });
    }
  }
  return out.filter((b) => ("lines" in b ? b.lines.length : b.items.length));
}

function lineBreaks(lines: string[]) {
  return lines.map((line, i) => (
    <Fragment key={i}>
      {i > 0 && <br />}
      {inline(line)}
    </Fragment>
  ));
}

export function ReplyText({ text, lead = false }: { text: string; lead?: boolean }) {
  const shown = blocks(visibleLines(text, { lead }));
  if (!shown.length) return <p>{text.trim() ? "확인했어요." : ""}</p>;
  return (
    <>
      {shown.map((b, i) => {
        if (b.kind === "ul" || b.kind === "ol") {
          const List = b.kind;
          return (
            <List key={i}>
              {b.items.map((item, j) => (
                <li key={j}>{inline(item)}</li>
              ))}
            </List>
          );
        }
        if (b.kind === "quote") return <blockquote key={i}>{lineBreaks(b.lines)}</blockquote>;
        if (b.kind === "p") return <p key={i}>{lineBreaks(b.lines)}</p>;
        return null;
      })}
    </>
  );
}
