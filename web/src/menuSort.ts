// 메뉴 탭 보기 정렬. 화면에 보이는 순서만 바꾸고 저장한 순서(등록순)는 그대로다.
import type { MenuSort } from "./storage";
import type { MenuRow } from "./types";

/** 고를 수 있는 보기 정렬과 화면 이름. */
export const MENU_SORTS: Array<[MenuSort, string]> = [
  ["saved", "등록순"],
  ["name", "이름순"],
  ["price-asc", "가격 낮은순"],
  ["price-desc", "가격 높은순"],
  ["no-photo", "사진 없는 것 먼저"],
];

const names = new Intl.Collator("ko", { numeric: true });
/** 사진이 있는 메뉴: 실제 사진만(대표 그림은 사진이 아니다). 흰 배경 사진을 만드는 중이면 아직 없다. */
const hasPhoto = (m: MenuRow) => Boolean(m.photo_url) && !(m.filling ?? []).includes("photo");

/** 고른 보기 순서로 늘어놓는다. 같으면 저장한 순서. 가격 없는 메뉴는 가격 정렬에서 맨 뒤. */
export function sortMenu(rows: MenuRow[], sort: MenuSort): MenuRow[] {
  if (sort === "saved") return rows;
  const at = new Map(rows.map((m, i) => [m.id, i]));
  const price = (m: MenuRow, sign: number) => (m.price === null ? Infinity : sign * m.price);
  const key: Record<Exclude<MenuSort, "saved">, (a: MenuRow, b: MenuRow) => number> = {
    name: (a, b) => names.compare(a.name, b.name),
    "price-asc": (a, b) => price(a, 1) - price(b, 1),
    "price-desc": (a, b) => price(a, -1) - price(b, -1),
    "no-photo": (a, b) => Number(hasPhoto(a)) - Number(hasPhoto(b)),
  };
  return [...rows].sort((a, b) => key[sort](a, b) || (at.get(a.id) ?? 0) - (at.get(b.id) ?? 0));
}
