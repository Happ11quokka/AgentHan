import { describe, expect, it } from 'vitest'
import { voiceBargeInCapable } from './bargeIn.ts'

const ANDROID_CHROME =
  'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36'
const CHROME_HINTS = {
  platform: 'Android',
  mobile: true,
  brands: [{ brand: 'Not?A_Brand' }, { brand: 'Chromium' }, { brand: 'Google Chrome' }],
}
const AEC = { echoCancellation: true }

describe('voiceBargeInCapable (안드로이드 크롬만, 모르면 반이중)', () => {
  it('accepts Android Chrome with echo cancellation on', () => {
    expect(voiceBargeInCapable(ANDROID_CHROME, CHROME_HINTS, AEC)).toBe(true)
    expect(voiceBargeInCapable(ANDROID_CHROME, undefined, { echoCancellation: 'all' })).toBe(true)
  })

  it.each([
    ['iPhone Safari', 'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0 Mobile/15E148 Safari/604.1'],
    ['iPhone Chrome', 'Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/141.0 Mobile/15E148 Safari/604.1'],
    ['desktop Chrome', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36'],
    ['Samsung Internet', 'Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/27.0 Chrome/125.0.0.0 Mobile Safari/537.36'],
    ['Android WebView (KakaoTalk)', 'Mozilla/5.0 (Linux; Android 14; SM-S921N Build/UP1A; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/141.0.0.0 Mobile Safari/537.36 KAKAOTALK 10.8.0'],
    ['Android Edge', 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36 EdgA/141.0.0.0'],
    ['Android Firefox', 'Mozilla/5.0 (Android 14; Mobile; rv:141.0) Gecko/141.0 Firefox/141.0'],
    ['Naver Whale', 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Whale/4.0 Mobile Safari/537.36'],
    ['unknown', 'test'],
  ])('keeps half duplex on %s', (_name, ua) => {
    expect(voiceBargeInCapable(ua, undefined, AEC)).toBe(false)
  })

  it('trusts client hints over a Chrome-looking UA (Brave, desktop mode)', () => {
    const brave = { ...CHROME_HINTS, brands: [{ brand: 'Chromium' }, { brand: 'Brave' }] }
    expect(voiceBargeInCapable(ANDROID_CHROME, brave, AEC)).toBe(false)
    expect(voiceBargeInCapable(ANDROID_CHROME, { ...CHROME_HINTS, platform: 'Linux' }, AEC)).toBe(false)
  })

  it('keeps half duplex when echo cancellation is not applied or unknown', () => {
    expect(voiceBargeInCapable(ANDROID_CHROME, CHROME_HINTS, { echoCancellation: false })).toBe(false)
    expect(voiceBargeInCapable(ANDROID_CHROME, CHROME_HINTS, {})).toBe(false)
  })
})
