// 고른 사진을 화면에 잠깐 보여 줄 주소. 다 쓰면 forgetPhotoUrl로 풀어 준다.
export function photoUrl(file: File): string | undefined {
  try {
    return URL.createObjectURL(file);
  } catch {
    return undefined; // 미리보기를 만들 수 없는 환경(테스트 등)
  }
}

export function forgetPhotoUrl(url: string | undefined) {
  if (!url) return;
  try {
    URL.revokeObjectURL(url);
  } catch {
    // 무시
  }
}
