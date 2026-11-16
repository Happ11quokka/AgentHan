import OwnerPage from './screens/owner/OwnerPage.tsx'
import PhonePage from './screens/phone/PhonePage.tsx'

// 라우터 라이브러리 없이 주소 경로로 화면을 고른다.
// 화면이 셋 넘게 늘면 react-router를 검토한다.
export default function App() {
  const path = window.location.pathname.replace(/\/+$/, '') || '/'

  if (path === '/phone') {
    return <PhonePage />
  }

  if (path === '/owner') {
    return <OwnerPage />
  }

  return (
    <main>
      <h1>사장님 비서 화면 자리</h1>
      <p>대화창(왼쪽)과 더미 스마트플레이스 화면(오른쪽)이 여기에 들어온다.</p>
      <p>
        전화 주문 데모는 <a href="/phone">/phone</a>
      </p>
    </main>
  )
}
