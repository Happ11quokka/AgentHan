import { useEffect, useState, useSyncExternalStore } from "react";
import type { AvatarState } from "../avatarState";

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
const FADE_MS = 450;

function subscribeMotion(onChange: () => void) {
  const media = window.matchMedia?.(REDUCED_MOTION);
  media?.addEventListener("change", onChange);
  return () => media?.removeEventListener("change", onChange);
}

const reducedMotion = () => window.matchMedia?.(REDUCED_MOTION).matches ?? false;

/** 사이드카 전용. state를 직접 지정하면 다섯 상태를 앱·서버 없이도 확인할 수 있다. */
export function AgentAvatar({ state }: { state: AvatarState }) {
  const reduce = useSyncExternalStore(subscribeMotion, reducedMotion, () => true);
  const [layers, setLayers] = useState<{
    current: AvatarState;
    previous: AvatarState | null;
    entered: boolean;
  }>({ current: state, previous: null, entered: true });

  // 새 영상을 opacity 0으로 먼저 마운트하고, 직전 영상과 두 레이어만 유지한다.
  if (layers.current !== state || (reduce && (layers.previous !== null || !layers.entered))) {
    setLayers({ current: state, previous: reduce ? null : layers.current, entered: reduce });
  }

  useEffect(() => {
    if (reduce || layers.entered) return;
    let nextFrame = 0;
    const frame = requestAnimationFrame(() => {
      nextFrame = requestAnimationFrame(() => setLayers((current) => ({ ...current, entered: true })));
    });
    return () => {
      cancelAnimationFrame(frame);
      cancelAnimationFrame(nextFrame);
    };
  }, [layers.current, layers.entered, reduce]);

  useEffect(() => {
    if (reduce || !layers.previous || !layers.entered) return;
    // 페이드가 끝난 영상은 언마운트해 숨은 영상이 계속 재생되지 않게 한다.
    const timer = setTimeout(() => setLayers((current) => ({ ...current, previous: null })), FADE_MS);
    return () => clearTimeout(timer);
  }, [layers.current, layers.previous, layers.entered, reduce]);

  return (
    <div className="agent-desk" role="img" aria-label="비서 프로필" data-state={state}>
      {reduce ? (
        <img className="agent-desk-poster" src={`/avatar/${state}.png`} alt="" />
      ) : (
        [layers.previous, layers.current].map((layer) => layer && (
          <div
            key={layer}
            className="agent-desk-layer"
            data-state={layer}
            data-active={layer === layers.current ? layers.entered : !layers.entered}
            aria-hidden
          >
            <video muted loop playsInline autoPlay poster={`/avatar/${layer}.png`} preload="auto">
              <source src={`/avatar/${layer}.webm`} type="video/webm" />
              <source src={`/avatar/${layer}.mp4`} type="video/mp4" />
            </video>
          </div>
        ))
      )}
    </div>
  );
}
