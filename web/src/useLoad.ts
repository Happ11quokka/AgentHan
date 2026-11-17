// 탭이 열려 있을 때 읽기 전용 데이터를 불러온다. key가 바뀌면(대화가 바뀌거나 답이 오면) 다시 읽는다.
import { useCallback, useEffect, useState } from "react";

/** update: 다시 읽지 않고 받은 결과로 데이터 일부만 바꾼다(예: 리뷰 카드 하나). */
export type Loaded<T> = { data: T | null; failed: boolean; reload: () => void; update: (change: (data: T) => T) => void };

export function useLoad<T>(load: (() => Promise<T>) | null, key: string): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!load) return;
    let live = true;
    setFailed(false);
    load()
      .then((value) => live && setData(value))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
    // load는 key가 같으면 같은 일을 한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  const update = useCallback((change: (data: T) => T) => setData((d) => (d === null ? d : change(d))), []);
  return { data, failed, reload, update };
}
