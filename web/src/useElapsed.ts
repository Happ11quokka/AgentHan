import { useEffect, useState } from "react";

/** since(ms)부터 지난 초. since가 null이면 0이고 멈춘다. 0.2초마다 다시 그린다. */
export function useElapsed(since: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === null) return;
    const timer = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(timer);
  }, [since]);
  return since === null ? 0 : Math.max(0, (now - since) / 1000);
}
