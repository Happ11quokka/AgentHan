/** 제목 · 걸린 초 · 막대 · 한 줄 설명. ratio는 0~1(최소 4%로 그린다). */
export function ProgressBar({ title, detail, seconds, ratio, className = "" }: { title: string; detail: string; seconds: number; ratio: number; className?: string }) {
  const percent = Math.round(Math.min(1, Math.max(0.04, ratio)) * 100);
  return (
    <div className={`menu-progress ${className}`} role="status" aria-live="polite">
      <div className="menu-progress-head">
        <strong>{title}</strong>
        <span className="menu-progress-time">{seconds.toFixed(1)}초</span>
      </div>
      <div className="menu-progress-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label={title}>
        <span style={{ width: `${percent}%` }} />
      </div>
      <p>{detail}</p>
    </div>
  );
}
