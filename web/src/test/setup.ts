import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

// 사이드카 테스트는 네이버 플레이스를 불러온 뒤의 화면을 본다. 불러오기 자체는 NaverImport 테스트에서 본다.
beforeEach(() => {
  localStorage.setItem("agenthan.naverPlace.v1", "true");
});

afterEach(() => {
  cleanup();
  localStorage.clear();
});
