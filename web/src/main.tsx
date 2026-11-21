import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";

import { isPhonePath } from "./screens/phone/orderRoute.ts";

// 라우터 라이브러리 없이 주소 경로로 화면을 고른다.
// `/phone`(손님 통화)·`/phone/orders/<주문 번호>`(손님 주문 현황, 티켓 06)·`/owner`(전화 주문 카드)는
// Part C 화면, 그 밖은 사장님 비서 화면. 두 화면은 전역 CSS가 달라서 필요한 쪽만 불러온다.
const path = window.location.pathname.replace(/\/+$/, "") || "/";

async function screen(): Promise<ReactNode> {
  if (path === "/owner" || isPhonePath(path)) {
    document.title = path === "/owner" ? "전화 주문 카드" : "전화 주문";
    await import("./index.css");
    const { default: PhoneApp } = await import("./screens/PhoneApp.tsx");
    return <PhoneApp path={path} />;
  }
  await import("./styles.css");
  const { default: App } = await import("./App");
  return <App />;
}

void screen().then((node) => {
  createRoot(document.getElementById("root")!).render(<StrictMode>{node}</StrictMode>);
});
