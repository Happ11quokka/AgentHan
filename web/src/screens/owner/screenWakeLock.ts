// 화면 꺼짐 방지 (4단계 티켓 08). 브라우저는 탭이 가려지면 Wake Lock을 스스로 푼다(MDN).
// 그래서 탭이 다시 보일 때(`visibilitychange` → visible) 다시 잡는다.
// 지원하지 않거나(오래된 브라우저·HTTP) 거절되면(절전 모드·배터리 부족) 조용히 넘어간다.

/** `navigator.wakeLock`에서 쓰는 부분만 */
export interface WakeLockLike {
  request(type: 'screen'): Promise<unknown>
}

/** `document`에서 쓰는 부분만 */
export interface VisibilitySource extends EventTarget {
  readonly visibilityState: DocumentVisibilityState
}

/**
 * 지금 한 번 잡고, 탭이 다시 보일 때마다 다시 잡는다. 돌려준 함수로 그만둔다.
 * 화면에서는 `keepScreenAwake(document, navigator.wakeLock)`로 부른다.
 */
export function keepScreenAwake(doc: VisibilitySource, lock: WakeLockLike | undefined): () => void {
  const take = () => {
    if (!lock) return
    try {
      lock.request('screen').catch(() => {
        // 절전 모드·가려진 탭 등에서 거절될 수 있다. 다음에 보일 때 다시 해 본다
      })
    } catch {
      // 바로 던지는 구현도 같은 뜻으로 넘긴다
    }
  }
  const onVisibility = () => {
    if (doc.visibilityState === 'visible') take()
  }
  take()
  doc.addEventListener('visibilitychange', onVisibility)
  return () => doc.removeEventListener('visibilitychange', onVisibility)
}
