export function LoadFailed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="soon" role="alert">
      <strong>{message}</strong>
      <button type="button" className="ghost small" onClick={onRetry}>
        다시 불러오기
      </button>
    </div>
  );
}
