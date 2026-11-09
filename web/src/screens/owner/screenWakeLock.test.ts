import { describe, expect, it } from 'vitest'
import { type WakeLockLike, keepScreenAwake } from './screenWakeLock.ts'

/** visibilitychange만 흉내 내는 가짜 문서 */
class FakeDocument extends EventTarget {
  visibilityState: DocumentVisibilityState = 'visible'

  show(state: DocumentVisibilityState) {
    this.visibilityState = state
    this.dispatchEvent(new Event('visibilitychange'))
  }
}

function fakeWakeLock(fail = false) {
  const lock = {
    requests: 0,
    async request(type: 'screen') {
      expect(type).toBe('screen')
      lock.requests++
      if (fail) throw new DOMException('battery saver', 'NotAllowedError')
      return {}
    },
  }
  return lock satisfies WakeLockLike & { requests: number }
}

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('screen wake lock across tab switches (ticket 08)', () => {
  it('takes the lock right away and again whenever the tab becomes visible', async () => {
    const doc = new FakeDocument()
    const lock = fakeWakeLock()
    keepScreenAwake(doc, lock)
    expect(lock.requests).toBe(1)
    doc.show('hidden')
    expect(lock.requests).toBe(1)
    doc.show('visible')
    expect(lock.requests).toBe(2)
  })

  it('stops taking the lock after release', () => {
    const doc = new FakeDocument()
    const lock = fakeWakeLock()
    const release = keepScreenAwake(doc, lock)
    release()
    doc.show('visible')
    expect(lock.requests).toBe(1)
  })

  it('skips quietly when the browser has no wake lock', () => {
    const doc = new FakeDocument()
    expect(() => {
      keepScreenAwake(doc, undefined)
      doc.show('visible')
    }).not.toThrow()
  })

  it('skips quietly when the browser refuses the lock', async () => {
    const doc = new FakeDocument()
    const lock = fakeWakeLock(true)
    keepScreenAwake(doc, lock)
    doc.show('visible')
    await flush()
    expect(lock.requests).toBe(2)
  })
})
