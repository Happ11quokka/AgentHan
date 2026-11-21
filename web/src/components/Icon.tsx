// 선 아이콘 몇 개. 글자 색을 따라간다.
const PATHS = {
  plus: "M12 5v14M5 12h14",
  camera:
    "M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  star: "M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3 6.4 20.2l1.1-6.2L3 9.6l6.2-.9z",
  chart: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  ticket:
    "M3 8a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 0-2 2H5a2 2 0 0 0-2-2v-2a2 2 0 0 0 0-4zM14 6v12",
  send: "M5 12h14M13 6l6 6-6 6",
  clip: "M21 11l-8.5 8.5a5 5 0 0 1-7-7L14 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L15 7",
  close: "M6 6l12 12M18 6L6 18",
  menu: "M4 7h16M4 12h16M4 17h16",
  store: "M4 10v10h16V10M3 10l2-6h14l2 6M3 10h18M9 20v-5h6v5",
  check: "M5 12l5 5L20 7",
  phone:
    "M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z",
  receipt: "M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6M9 16h3",
  bulb: "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1 2V16h5.2v-.2c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z",
  box: "M3 9h18v11H3zM8 9V6a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v3M3 13h18M11 13v2h2v-2",
  utensils: "M7 3v8M5 3v5a2 2 0 0 0 4 0V3M7 11v10M17 21V3c-2 1-3 4-3 8h3",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  chat: "M4 5h16v11H9l-5 4zM8 10h8M8 13h5",
  bag: "M5 8h14l-1 13H6zM9 8V6a3 3 0 0 1 6 0v2",
  calendar: "M4 6h16v15H4zM4 10h16M8 3v4M16 3v4",
  plug: "M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4",
  tag: "M3 12V4h8l10 10-8 8zM7.5 8.5h.01",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-5-5",
  flag: "M5 21V4M5 4h11l-2 4 2 4H5",
  card: "M3 6h18v12H3zM3 10h18M7 15h4",
  qr: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 14h2M14 18h2M18 18h2v2",
  coin: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 9l2 6 2-5 2 5 2-6M7.5 12h9",
  pencil: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
  book: "M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 21V5M8 7h7",
  grip: "M5 9h14M5 15h14",
  up: "M6 15l6-6 6 6",
  down: "M6 9l6 6 6-6",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
