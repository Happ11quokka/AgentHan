import { useEffect, useState, type RefObject } from "react";
import { forgetPhotoUrl, photoUrl } from "../photo";
import { Icon } from "./Icon";

type Props = {
  disabled: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
  dropped: File | null;
  onDroppedTaken: () => void;
  onSend: (text: string, image?: File) => void;
};

export function Composer({ disabled, inputRef, dropped, onDroppedTaken, onSend }: Props) {
  const [text, setText] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [thumb, setThumb] = useState<string | undefined>();

  useEffect(() => () => forgetPhotoUrl(thumb), [thumb]); // 바꾸거나 빼면 이전 미리보기를 푼다

  const attach = (file: File | null) => {
    setImage(file);
    setThumb(file ? photoUrl(file) : undefined);
  };

  useEffect(() => {
    if (!dropped) return;
    attach(dropped);
    onDroppedTaken();
  }, [dropped, onDroppedTaken]);

  const canSend = !disabled && (text.trim().length > 0 || image !== null);
  const submit = () => {
    if (!canSend) return;
    onSend(text.trim(), image ?? undefined);
    setText("");
    attach(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      {image && (
        <div className="attachment">
          {thumb ? <img src={thumb} alt="" /> : <Icon name="camera" />}
          <span>사진 1장</span>
          <button type="button" className="icon-button" aria-label="사진 빼기" onClick={() => attach(null)}>
            <Icon name="close" size={14} />
          </button>
        </div>
      )}
      <div className="composer-box">
        <label className="icon-button attach" title="사진 올리기">
          <Icon name="clip" />
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            aria-label="사진 올리기"
            onChange={(e) => attach(e.target.files?.[0] ?? null)}
          />
        </label>
        <textarea
          aria-label="비서에게 말하기"
          placeholder={image ? "사진과 함께 보낼 말 (없어도 돼요)" : "비서에게 말하기…"}
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button type="submit" className="send" aria-label="보내기" disabled={!canSend}>
          <Icon name="send" />
        </button>
      </div>
    </form>
  );
}
