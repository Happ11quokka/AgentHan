import { useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";

/** Pretendard Black em 박스. viewBox 높이 2443 = typo ascender 1949 + descender 494. */
const VIEW_W = 1490;
const VIEW_H = 2443;
/** 기둥은 Pretendard Black H와 같고, 가로획만 33°로 기울인다(A 27.7°와 C 40° 사이). */
const PATH =
  "M98 1949 L478 1949 L478 1240 L1012 1586.79 L1012 1949 L1394 1949 L1394 501 L1012 501 L1012 1210 L478 863.21 L478 501 L98 501 Z";

type SharedGradient = { x1: number; x2: number };

/** 재사용 가능한 H/N 마크. shared가 있으면 워드마크 전체 그라데이션의 한 구간만 그린다. */
export function BrandMark({ shared }: { shared?: SharedGradient }) {
  const gid = `agenthan-hn-${useId().replace(/:/g, "")}`;
  return (
    <svg className="brand-hn" viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} aria-hidden="true" focusable="false">
      <defs>
        <linearGradient
          id={gid}
          gradientUnits={shared ? "userSpaceOnUse" : "objectBoundingBox"}
          x1={shared ? shared.x1 : 0}
          y1={0}
          x2={shared ? shared.x2 : 1}
          y2={0}
        >
          <stop offset="0%" stopColor="#2fd17c" />
          <stop offset="45%" stopColor="#03c75a" />
          <stop offset="100%" stopColor="#009a4a" />
        </linearGradient>
      </defs>
      <path fill={`url(#${gid})`} d={PATH} />
    </svg>
  );
}

/** 펼치면 Agent+마크+an에 그라데이션이 이어지고, 접으면 마크가 3스톱 전체를 쓴다. */
export function BrandLogo({ collapsed }: { collapsed: boolean }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [paint, setPaint] = useState<{ total: number; markX: number; markW: number } | null>(null);

  useLayoutEffect(() => {
    if (collapsed) {
      setPaint(null);
      return;
    }
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const mark = el.querySelector<SVGElement>(".brand-hn");
      if (!mark) return;
      const a = el.getBoundingClientRect();
      const m = mark.getBoundingClientRect();
      if (a.width <= 0 || m.width <= 0) return;
      setPaint({ total: a.width, markX: m.left - a.left, markW: m.width });
    };
    measure();
    void document.fonts?.ready.then(measure);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [collapsed]);

  const shared = paint
    ? { x1: (-paint.markX / paint.markW) * VIEW_W, x2: ((paint.total - paint.markX) / paint.markW) * VIEW_W }
    : undefined;
  const textStyle = (x: number): CSSProperties | undefined =>
    paint
      ? ({
          "--brand-w": `${paint.total}px`,
          "--brand-x": `${-x}px`,
        } as CSSProperties)
      : undefined;

  return (
    <span className="brand-logo" aria-label="AgentHan" ref={ref}>
      {!collapsed && (
        <span className="brand-logo-text" aria-hidden="true" style={textStyle(0)}>
          Agent
        </span>
      )}
      <BrandMark shared={shared} />
      {!collapsed && (
        <span className="brand-logo-text" aria-hidden="true" style={textStyle(paint ? paint.markX + paint.markW : 0)}>
          an
        </span>
      )}
    </span>
  );
}
