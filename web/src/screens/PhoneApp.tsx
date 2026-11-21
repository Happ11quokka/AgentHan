import OwnerPage from './owner/OwnerPage.tsx'
import PhonePage from './phone/PhonePage.tsx'

// Part C 화면 둘. `main.tsx`가 주소가 `/phone`(주문 현황 `/phone/orders/<주문 번호>` 포함)·`/owner`일 때만
// 이 파일을 불러온다. 손님 화면은 주소를 스스로 읽는다(`phone/orderRoute.ts`).
export default function PhoneApp({ path }: { path: string }) {
  return path === '/owner' ? <OwnerPage /> : <PhonePage />
}
