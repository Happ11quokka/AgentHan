// 음성 끼어들기(3단계 티켓 06)를 이 브라우저에서 해도 되나.
//
// 안드로이드 크롬은 폰 제조사의 에코 제거(플랫폼 AEC)를 쓰고 통화 모드로 바꾼다(s3-barge-in Q1-1).
// 아이폰은 에코 제거를 켜면 소리가 지직거리고 끄면 에코 제거가 없어(WebKit 326286) 반이중 + 버튼이다.
// 그 밖의 브라우저(삼성 인터넷·웨일·엣지·인앱 WebView·파이어폭스·데스크톱)는 시험하지 않았다.
// 그래서 확실히 안드로이드 크롬이고 마이크에 에코 제거가 걸렸을 때만 참이다. 모르면 거짓(반이중).

export interface UaHints {
  platform?: string
  mobile?: boolean
  brands?: { brand: string }[]
}

// 크롬 UA를 흉내 내는 다른 브라우저·인앱 WebView 표시
const NOT_CHROME =
  /; wv\)|Version\/\d|SamsungBrowser|EdgA|Edg\/|OPR\/|Opera|YaBrowser|UCBrowser|MiuiBrowser|HeyTapBrowser|Vivaldi|DuckDuckGo|Whale|NAVER|KAKAOTALK|Line\/|Instagram|FBAN|FBAV|Firefox|CriOS|FxiOS|iPhone|iPad|iPod/

export function voiceBargeInCapable(
  ua: string,
  hints: UaHints | undefined,
  mic: Record<string, unknown>,
): boolean {
  const aec = mic.echoCancellation
  if (aec !== true && aec !== 'all') return false
  if (!/\bAndroid\b/.test(ua) || !/\bChrome\/\d+/.test(ua) || NOT_CHROME.test(ua)) return false
  if (hints) {
    // 크로미움 계열은 UA를 크롬과 똑같이 줄인다. 클라이언트 힌트가 있으면 그것을 믿는다
    if (hints.platform !== 'Android') return false
    if (!hints.brands?.some((b) => b.brand === 'Google Chrome')) return false
  }
  return true
}

/** 브라우저의 `navigator.userAgentData`(크로미움만 있다). 없으면 undefined. */
export function uaHints(): UaHints | undefined {
  const data = (navigator as Navigator & { userAgentData?: UaHints }).userAgentData
  return data ? { platform: data.platform, mobile: data.mobile, brands: data.brands } : undefined
}
