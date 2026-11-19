import OwnerPage from './owner/OwnerPage.tsx'
import PhonePage from './phone/PhonePage.tsx'

// Part C 화면 둘. `main.tsx`가 주소가 `/phone`·`/owner`일 때만 이 파일을 불러온다.
export default function PhoneApp({ path }: { path: string }) {
  return path === '/owner' ? <OwnerPage /> : <PhonePage />
}
