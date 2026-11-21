import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useElapsed } from "../useElapsed";
import { ProgressBar } from "./ProgressBar";
import { approval, DAYS, discountText, hoursText, MARK_TEXT, minOrderText, periodText, won, type RowMark } from "../labels";
import type { MenuEdit, MenuFill, MenuRow, Pending, Place, ReviewReport, Routines, RoutinesChange, SectionChanges } from "../types";
import { MENU_SORTS, sortMenu } from "../menuSort";
import { loadMenuSort, saveMenuSort, type MenuSort } from "../storage";
import { Icon } from "./Icon";
import { RoutineSettings } from "./RoutineSettings";

type Mark = RowMark | undefined;

/** 승인 대기 변경을 덮어 그린 화면과, 줄마다 표시할 강조. */
function overlay(place: Place, pending: Pending[]) {
  let view = place;
  const marks: Record<"menu" | "business_hours" | "coupon" | "keywords", Map<string, Mark>> = {
    menu: new Map(),
    business_hours: new Map(),
    coupon: new Map(),
    keywords: new Map(),
  };
  const removed: Place = { menu: [], business_hours: {}, coupon: [], keywords: [] };
  for (const p of pending) {
    if (!p.after) continue;
    for (const [section, diff] of Object.entries(p.changes ?? {}) as Array<[keyof typeof marks, SectionChanges]>) {
      if (!(section in marks)) continue; // 톡톡·주문·예약 같은 기록은 이 탭에 그리지 않는다(승인 카드가 보여 준다).
      view ={ ...view, [section]: p.after[section] };
      for (const k of diff.added) marks[section].set(k, "added");
      for (const k of diff.updated) marks[section].set(k, "updated");
      for (const k of diff.removed) marks[section].set(k, "removed");
    }
  }
  for (const m of place.menu) if (marks.menu.get(m.id) === "removed") removed.menu.push(m);
  for (const c of place.coupon) if (marks.coupon.get(c.id) === "removed") removed.coupon.push(c);
  for (const k of place.keywords ?? []) if (marks.keywords.get(k) === "removed") removed.keywords?.push(k);
  return { view, marks, removed };
}

function Row({ mark, children }: { mark: Mark; children: ReactNode }) {
  return (
    <li className="shop-row" data-mark={mark}>
      {children}
      {mark && <span className="mark">{MARK_TEXT[mark]}</span>}
    </li>
  );
}

/** 사이드카 구역 머리의 [수정]: 대화에서 그 일을 이어 가게 한다. */
function EditButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" className="icon-button section-edit" aria-label={`${label} 수정`} title="수정" onClick={onClick}>
      <Icon name="pencil" size={16} />
    </button>
  );
}

function Section({ title, count, onEdit, children }: { title: string; count?: number; onEdit?: () => void; children: ReactNode }) {
  return (
    <section className="shop-section">
      <h3>
        {title}
        {count !== undefined && <span>{count}</span>}
        {onEdit && <EditButton label={title} onClick={onEdit} />}
      </h3>
      {children}
    </section>
  );
}

/** 승인 전 변경이 있으면 위에 한 줄: 저장해야 반영된다. */
function PendingBanner({ pending }: { pending: Pending[] }) {
  if (pending.length === 0) return null;
  const waitingPhoto = pending.some((p) => p.kind === "photo" && approval(p).blocked);
  return (
    <p className="shop-banner">
      {waitingPhoto ? "사진에서 읽은 메뉴를 확인하고 있어요." : "저장 전 변경을 미리 보여 드려요."} 대화에서 저장을 눌러야 반영돼요.
    </p>
  );
}

export type OnMenuEdit = (menuId: string, edit: MenuEdit) => Promise<string | null>;
export type OnMenuDelete = (menuId: string) => Promise<string | null>;
export type OnMenuPolish = (menuId: string) => Promise<string>;

type Field = "name" | "price" | "description";

/** AI가 채운 칸 안내. 사장님이 그 칸을 고치면 사라진다. */
/** 사진에서 자동으로 채운 칸(점선 밑줄)에 손을 올리면 보이는 안내. */
const AUTO_HINT = "사진에서 읽어 자동으로 채웠어요. 맞는지 확인하고 눌러서 고쳐 주세요.";

/**
 * 메뉴 한 줄. 저장된 메뉴는 이름·설명·가격을 눌러 바로 고친다(승인 카드 없음, Enter 저장 · Esc 취소).
 * 메뉴판 사진으로 올린 메뉴는 채우는 중인 칸(filling)을 흐린 자리로, AI가 채운 칸(ai)을 배지로 보인다.
 */
function MenuItemRow({
  m,
  mark,
  oldPrice,
  onEdit,
  onDelete,
  onPolish,
}: {
  m: MenuRow;
  mark: Mark;
  oldPrice: string | null;
  onEdit?: OnMenuEdit;
  onDelete?: OnMenuDelete;
  onPolish?: OnMenuPolish;
}) {
  const [editing, setEditing] = useState<Field | null>(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const ai = new Set(m.ai ?? []);
  const filling = new Set(m.filling ?? []);
  const editable = Boolean(onEdit) && mark !== "removed";

  const begin = (field: Field) => {
    if (!editable) return;
    setError(null);
    setEditing(field);
    setValue(field === "price" ? (m.price !== null ? String(m.price) : "") : (m[field] ?? ""));
  };
  const cancel = () => {
    setEditing(null);
    setError(null);
  };
  const commit = async () => {
    if (!editing || !onEdit || saving) return;
    const text = value.trim();
    let edit: MenuEdit;
    if (editing === "price") {
      const price = Number(text.replace(/[,\s원]/g, ""));
      if (!Number.isInteger(price) || price <= 0) {
        setError("가격은 0보다 큰 숫자로 넣어 주세요.");
        return;
      }
      if (price === m.price) return cancel();
      edit = { price };
    } else {
      if (editing === "name" && !text) {
        setError("이름은 비울 수 없어요.");
        return;
      }
      if (text === (m[editing] ?? "")) return cancel();
      edit = { [editing]: text };
    }
    setSaving(true);
    const failed = await onEdit(m.id, edit);
    setSaving(false);
    if (failed) setError(failed);
    else cancel();
  };
  const remove = async () => {
    if (!onDelete) return;
    setSaving(true);
    const failed = await onDelete(m.id);
    setSaving(false);
    if (failed) setError(failed);
    setConfirmDelete(false);
  };

  const input = (field: Field, label: string) =>
    editing === field ? (
      <span className="menu-edit">
        {field === "description" ? (
          <textarea
            aria-label={label}
            value={value}
            rows={2}
            autoFocus
            disabled={saving}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") cancel();
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void commit();
              }
            }}
          />
        ) : (
          <input
            aria-label={label}
            value={value}
            inputMode={field === "price" ? "numeric" : undefined}
            autoFocus
            disabled={saving}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") cancel();
              if (e.key === "Enter" && !e.nativeEvent.isComposing) void commit();
            }}
          />
        )}
        <button type="button" className="menu-edit-save" disabled={saving} onClick={() => void commit()}>
          저장
        </button>
        <button type="button" className="menu-edit-cancel" disabled={saving} onClick={cancel}>
          취소
        </button>
      </span>
    ) : null;

  const field = (name: Field, label: string, children: ReactNode, className?: string) =>
    input(name, label) ??
    (editable ? (
      <button type="button" className={`menu-field ${className ?? ""}`} data-ai={ai.has(name) || undefined} title={ai.has(name) ? AUTO_HINT : undefined} aria-label={`${label}: 눌러서 고치기`} onClick={() => begin(name)}>
        {children}
      </button>
    ) : (
      <span className={className} data-ai={ai.has(name) || undefined} title={ai.has(name) ? AUTO_HINT : undefined}>
        {children}
      </span>
    ));

  return (
    <li className="menu-item" data-mark={mark} data-filling={filling.size > 0 || undefined}>
      <div className="menu-body">
        <div className="menu-tags">
          {mark && <span className="mark">{MARK_TEXT[mark]}</span>}
        </div>
        {field(
          "name",
          "메뉴 이름",
          <strong>
            {m.name}
            {filling.has("name") && <span className="filling-note"> · 한국어 이름 만드는 중</span>}
          </strong>,
        )}
        {filling.has("description") ? (
          <span className="skeleton-line" aria-label="설명 쓰는 중" />
        ) : (
          field(
            "description",
            "메뉴 설명",
            m.description ? <span className="menu-desc">{m.description}</span> : editable ? <span className="menu-add">+ 설명 넣기</span> : null,
          )
        )}
        <p className="menu-price">
          {oldPrice && <s>{oldPrice}</s>}
          {field("price", "가격", <span>{m.price !== null ? won(m.price) : "가격 없음"}</span>)}
        </p>
        {error && (
          <p className="menu-error" role="alert">
            {error}
          </p>
        )}
        {note && <p className="menu-note">{note}</p>}
        {onDelete && mark !== "removed" && (
          <div className="menu-delete">
            {confirmDelete ? (
              <>
                <span>이 메뉴를 지울까요?</span>
                <button type="button" className="danger" disabled={saving} onClick={() => void remove()}>
                  지우기
                </button>
                <button type="button" disabled={saving} onClick={() => setConfirmDelete(false)}>
                  취소
                </button>
              </>
            ) : (
              <>
                {onPolish && m.photo_url && !filling.has("polish") && (
                  <button
                    type="button"
                    className="menu-polish"
                    title="배달앱 메뉴 사진처럼 밝기·색·구도를 다듬고 바닥 그림자를 넣어요."
                    disabled={saving}
                    onClick={async () => setNote(await onPolish(m.id))}
                  >
                    사진 다듬기
                  </button>
                )}
                <button type="button" className="menu-delete-open" aria-label={`${m.name} 지우기`} onClick={() => setConfirmDelete(true)}>
                  지우기
                </button>
              </>
            )}
          </div>
        )}
      </div>
      {filling.has("photo") ? (
        <span className="menu-photo skeleton" aria-label="흰 배경 사진 만드는 중" />
      ) : m.photo_url ? (
        <span className="menu-photo-wrap">
          <img className="menu-photo" src={m.photo_url} alt="" />
          {ai.has("photo") && <span className="photo-note">메뉴판 사진</span>}
          {filling.has("polish") && <span className="photo-note busy">다듬는 중</span>}
        </span>
      ) : m.illustration_url ? (
        // 사진이 없을 때만: 메뉴 이름으로 고른 대표 그림. 실제 사진이 아니라는 걸 표시로 알린다.
        <span className="menu-photo-wrap">
          <img className="menu-photo illustration" src={m.illustration_url} alt="대표 그림" />
          <span className="photo-note" aria-hidden>
            대표 그림
          </span>
        </span>
      ) : (
        <span className="menu-photo empty" aria-hidden>
          {m.name.slice(0, 1)}
        </span>
      )}
    </li>
  );
}

const busyFields = (m: MenuRow) => (m.filling ?? []).filter((f) => f !== "polish");
const fillingFields = (menu: MenuRow[]) => menu.reduce((n, m) => n + busyFields(m).length, 0);

/** 서버 채우기(menu_fill.started_at)마다 이 기기 시각으로 옮긴 시작(ms). 메뉴 탭을 닫았다 열어도
 * (다른 탭에 갔다 와도) 남아서 걸린 초를 처음부터 세지 않는다. 받은 값 중 가장 이른 것을 쓴다:
 * 늦게 받은 응답이나 예전에 받은 가게 화면의 seconds는 실제보다 작다. */
const fillStarts = new Map<number, number>();
function fillStart(run: MenuFill): number {
  const guess = Date.now() - run.seconds * 1000;
  const start = Math.min(fillStarts.get(run.started_at) ?? guess, guess);
  fillStarts.set(run.started_at, start);
  return start;
}

/** 메뉴판에서 올린 메뉴의 설명·흰 배경 사진을 채우는 동안: 남은 칸 수로 진행률, 다 된 메뉴 수, 걸린 시간.
 * 시작 시각·이 일의 메뉴·칸 수는 서버(menu_fill)가 정한다. 예전 서버면 이 줄이 처음 뜬 때부터 센다. */
function FillProgress({ menu, run }: { menu: MenuRow[]; run: MenuFill | null }) {
  return fillingFields(menu) > 0 ? <FillRun menu={menu} run={run} /> : null;
}

function FillRun({ menu, run }: { menu: MenuRow[]; run: MenuFill | null }) {
  const mine = run ? menu.filter((m) => run.menu_ids.includes(m.id)) : menu;
  const remaining = fillingFields(mine);
  const busyMenus = mine.filter((m) => busyFields(m).length > 0).length;
  const [mountedAt] = useState(() => Date.now());
  // 예전 서버: 가장 많았던 남은 칸·메뉴 수(이전 렌더 값과 비교해 렌더 중에 고친다)
  const [peak, setPeak] = useState({ fields: remaining, menus: busyMenus });
  if (remaining > peak.fields || busyMenus > peak.menus)
    setPeak({ fields: Math.max(peak.fields, remaining), menus: Math.max(peak.menus, busyMenus) });
  const total = run ? { fields: Math.max(run.fields, remaining), menus: mine.length } : peak;
  const seconds = useElapsed(run ? fillStart(run) : mountedAt);
  return (
    <ProgressBar
      title="설명과 흰 배경 사진을 채우고 있어요"
      detail={`메뉴 ${total.menus - busyMenus}/${total.menus}개 완료 · 다 된 칸부터 바로 보여요`}
      seconds={seconds}
      ratio={total.fields > 0 ? 1 - remaining / total.fields : 0}
    />
  );
}

export type OnMenuReorder = (ids: string[]) => Promise<string | null>;

const REORDER_ONLY_SAVED = "순서 바꾸기는 등록순에서만 할 수 있어요.";

/**
 * [순서 바꾸기]: 저장된 메뉴를 끌어서(마우스·손가락) 또는 위·아래 버튼(키보드·화면 낭독기)으로 옮기고
 * [순서 저장]을 누르면 그 순서를 바로 저장한다(이름·가격 고치기처럼 승인 카드 없음). 전화도 이 순서로 메뉴를 알려 준다.
 */
function MenuReorder({ rows, onSave, onDone }: { rows: MenuRow[]; onSave: OnMenuReorder; onDone: () => void }) {
  const [order, setOrder] = useState(() => rows.map((m) => m.id));
  const [dragging, setDragging] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 화면 낭독기에 알릴 메뉴(옮긴 뒤 놓인 자리). 끄는 동안은 비워 둔다.
  const [moved, setMoved] = useState<string | null>(null);
  const list = useRef<HTMLUListElement>(null);
  const focusAfter = useRef<{ id: string; dir: "up" | "down" } | null>(null);
  // 고르는 사이 지운 메뉴는 빼고, 새로 올라온 메뉴는 뒤에 붙인다.
  const byId = new Map(rows.map((m) => [m.id, m]));
  const ids = [...order.filter((id) => byId.has(id)), ...rows.map((m) => m.id).filter((id) => !order.includes(id))];
  const shown = ids.map((id) => byId.get(id) as MenuRow);

  const move = (from: number, to: number) => {
    if (to < 0 || to >= ids.length || to === from) return;
    const next = [...ids];
    const [id] = next.splice(from, 1);
    next.splice(to, 0, id);
    setOrder(next);
    setError(null);
  };
  const step = (id: string, dir: "up" | "down") => {
    const from = ids.indexOf(id);
    const to = from + (dir === "up" ? -1 : 1);
    move(from, to);
    focusAfter.current = { id, dir };
    setMoved(id);
  };

  // 버튼으로 옮긴 뒤에도 같은 메뉴의 같은 버튼에 머문다(맨 위·아래에 닿아 꺼졌으면 반대 버튼).
  useLayoutEffect(() => {
    const want = focusAfter.current;
    if (!want || !list.current) return;
    focusAfter.current = null;
    const row = list.current.querySelector<HTMLElement>(`[data-id="${CSS.escape(want.id)}"]`);
    const same = row?.querySelector<HTMLButtonElement>(`[data-move="${want.dir}"]`);
    const other = row?.querySelector<HTMLButtonElement>(`[data-move="${want.dir === "up" ? "down" : "up"}"]`);
    (same && !same.disabled ? same : other)?.focus();
  });

  // 끌기: 손잡이를 누른 채 움직이면 손가락·커서가 지나간 줄 자리로 옮긴다. 줄이 옮겨져도 놓치지 않게 창에서 듣는다.
  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: PointerEvent) => {
      const items = Array.from(list.current?.children ?? []) as HTMLElement[];
      const from = items.findIndex((el) => el.dataset.id === dragging);
      if (from < 0) return;
      let to = 0;
      items.forEach((el, i) => {
        if (i === from) return;
        const box = el.getBoundingClientRect();
        if (e.clientY > box.top + box.height / 2) to += 1;
      });
      if (to === from) return;
      const next = items.map((el) => el.dataset.id as string); // 지금 그린 순서
      next.splice(from, 1);
      next.splice(to, 0, dragging);
      setOrder(next);
    };
    const onUp = () => {
      setDragging(null);
      setMoved(dragging);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [dragging]);

  const save = async () => {
    if (saving) return;
    if (ids.join("\n") === rows.map((m) => m.id).join("\n")) return onDone();
    setSaving(true);
    const failed = await onSave(ids);
    setSaving(false);
    if (failed) setError(failed);
    else onDone();
  };

  return (
    <>
      <div className="menu-toolbar reordering">
        <p className="menu-hint">끌어서 옮기거나 화살표로 순서를 바꾼 뒤 저장해 주세요.</p>
        <div className="menu-toolbar-actions">
          <button type="button" className="menu-tool" disabled={saving} onClick={onDone}>
            취소
          </button>
          <button type="button" className="menu-tool primary" disabled={saving} onClick={() => void save()}>
            {saving ? "저장 중…" : "순서 저장"}
          </button>
        </div>
      </div>
      {error && (
        <p className="menu-error" role="alert">
          {error}
        </p>
      )}
      <ul className="menu-list menu-reorder" ref={list} aria-label="메뉴 순서">
        {shown.map((m, i) => (
          <li key={m.id} className="menu-order-row" data-id={m.id} data-dragging={dragging === m.id || undefined}>
            <span
              className="menu-grip"
              aria-hidden
              title="끌어서 옮기기"
              onPointerDown={(e) => {
                if (saving || (e.pointerType === "mouse" && e.button !== 0)) return;
                e.preventDefault();
                setMoved(null);
                setDragging(m.id);
              }}
            >
              <Icon name="grip" size={18} />
            </span>
            <span className="menu-order-no" aria-hidden>
              {i + 1}
            </span>
            <span className="menu-order-name">
              <strong>{m.name}</strong>
              <small>{m.price !== null ? won(m.price) : "가격 없음"}</small>
            </span>
            <span className="menu-order-move">
              <button type="button" className="icon-button" data-move="up" aria-label={`${m.name} 위로`} disabled={saving || i === 0} onClick={() => step(m.id, "up")}>
                <Icon name="up" size={18} />
              </button>
              <button type="button" className="icon-button" data-move="down" aria-label={`${m.name} 아래로`} disabled={saving || i === shown.length - 1} onClick={() => step(m.id, "down")}>
                <Icon name="down" size={18} />
              </button>
            </span>
          </li>
        ))}
      </ul>
      <p className="sr-only" aria-live="polite">
        {moved && ids.includes(moved) ? `${byId.get(moved)?.name}, ${ids.length}개 중 ${ids.indexOf(moved) + 1}번째` : ""}
      </p>
    </>
  );
}

/** 메뉴 탭 맨 위: 보기 정렬(이 브라우저에 기억)과 [순서 바꾸기]. 순서 바꾸기는 등록순일 때만. */
function MenuToolbar({ sort, onSort, onReorder }: { sort: MenuSort; onSort: (s: MenuSort) => void; onReorder?: () => void }) {
  const hint = useId();
  const locked = sort !== "saved";
  return (
    <>
      <div className="menu-toolbar">
        <label className="menu-sort">
          <span>정렬</span>
          <select value={sort} onChange={(e) => onSort(e.target.value as MenuSort)}>
            {MENU_SORTS.map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {onReorder && (
          <button type="button" className="menu-tool" disabled={locked} aria-describedby={locked ? hint : undefined} onClick={onReorder}>
            <Icon name="grip" size={16} />
            순서 바꾸기
          </button>
        )}
      </div>
      {onReorder && locked && (
        <p className="menu-hint" id={hint}>
          {REORDER_ONLY_SAVED}
        </p>
      )}
    </>
  );
}

/** 메뉴 탭: 네이버 플레이스처럼 테두리 없는 줄(이름·설명·가격, 오른쪽 사진). 승인 전 변경은 알약 배지. */
export function MenuTab({
  place,
  pending,
  onEdit,
  onDelete,
  onPolish,
  onReorder,
  readingSince = null,
}: {
  place: Place;
  pending: Pending[];
  onEdit?: OnMenuEdit;
  onDelete?: OnMenuDelete;
  onPolish?: OnMenuPolish;
  /** [순서 바꾸기] 저장. 없으면 보기 정렬만 있다. */
  onReorder?: OnMenuReorder;
  /** 메뉴판 사진을 읽는 중이면 보낸 시각(ms). 진행 막대는 대화창에, 여기는 빈 메뉴 자리만. */
  readingSince?: number | null;
}) {
  const [sort, setSort] = useState<MenuSort>(loadMenuSort);
  const [reordering, setReordering] = useState(false);
  const { view, marks, removed } = overlay(place, pending);
  const before = new Map(place.menu.map((m) => [m.id, m]));
  const pick = (next: MenuSort) => {
    setSort(next);
    saveMenuSort(next);
  };
  const empty = view.menu.length + removed.menu.length === 0;
  return (
    <div className="shop">
      <PendingBanner pending={pending} />
      <FillProgress menu={place.menu} run={place.menu_fill ?? null} />
      <Section title="메뉴" count={view.menu.length}>
        {empty && readingSince !== null ? (
          <ul className="menu-list" aria-hidden>
            {[0, 1, 2].map((i) => (
              <li key={i} className="menu-item menu-item-ghost">
                <div className="menu-body">
                  <span className="skeleton-line" style={{ width: "40%" }} />
                  <span className="skeleton-line" />
                  <span className="skeleton-line" style={{ width: "25%" }} />
                </div>
                <span className="menu-photo skeleton" />
              </li>
            ))}
          </ul>
        ) : empty ? (
          <p className="empty">아직 메뉴가 없어요. 대화창에 메뉴판 사진을 올리면 여기에 바로 채워져요.</p>
        ) : reordering && onReorder ? (
          <MenuReorder rows={place.menu} onSave={onReorder} onDone={() => setReordering(false)} />
        ) : (
          <>
            <MenuToolbar sort={sort} onSort={pick} onReorder={onReorder && place.menu.length > 1 ? () => setReordering(true) : undefined} />
            <ul className="menu-list">
              {[...sortMenu(view.menu, sort), ...removed.menu].map((m) => {
                const mark = marks.menu.get(m.id);
                const old = mark === "updated" ? before.get(m.id) : undefined;
                const oldPrice = old && old.price !== m.price && old.price !== null ? won(old.price) : null;
                // 승인 전 변경(미리보기)에만 있는 줄은 아직 저장되지 않아 고칠 수 없다.
                const saved = before.has(m.id) && !mark;
                return (
                  <MenuItemRow
                    key={m.id}
                    m={m}
                    mark={mark}
                    oldPrice={oldPrice}
                    onEdit={saved ? onEdit : undefined}
                    onDelete={saved ? onDelete : undefined}
                    onPolish={saved ? onPolish : undefined}
                  />
                );
              })}
            </ul>
          </>
        )}
      </Section>
    </div>
  );
}

type HomeProps = {
  place: Place;
  pending: Pending[];
  report: ReviewReport | null;
  routines: Routines | null;
  onRoutines?: (change: RoutinesChange) => Promise<string | null>;
  onRunReport?: () => Promise<string | null>;
  /** 구역 머리의 [수정]. 없으면 버튼도 없다. 실행은 대화에서 한다. */
  onEdit?: (target: HomeEdit) => void;
};

export type HomeEdit = "hours" | "coupon" | "keywords" | "routines";

/** 홈 탭: 영업시간 · 쿠폰 · 대표키워드 · 루틴 설정. */
export function HomeTab({ place, pending, report, routines, onRoutines, onRunReport, onEdit }: HomeProps) {
  const { view, marks, removed } = overlay(place, pending);
  return (
    <div className="shop home">
      <PendingBanner pending={pending} />

      <Section title="영업시간" onEdit={onEdit && (() => onEdit("hours"))}>
        <ul className="hours">
          {DAYS.filter(([k]) => k in view.business_hours).map(([key, label]) => (
            <Row key={key} mark={marks.business_hours.get(key)}>
              <span className="day">{label}</span>
              <span className="time" data-closed={view.business_hours[key]?.closed}>
                {hoursText(view.business_hours[key])}
              </span>
            </Row>
          ))}
        </ul>
      </Section>

      <Section title="쿠폰" count={view.coupon.length} onEdit={onEdit && (() => onEdit("coupon"))}>
        {view.coupon.length + removed.coupon.length === 0 ? (
          <p className="empty">아직 쿠폰이 없어요.</p>
        ) : (
          <ul className="coupons">
            {[...view.coupon, ...removed.coupon].map((c) => (
              <Row key={c.id} mark={marks.coupon.get(c.id)}>
                <div className="coupon-body">
                  <strong>{c.title}</strong>
                  <small>{periodText(c)}</small>
                  {minOrderText(c) && <small>{minOrderText(c)}</small>}
                </div>
                <span className="coupon-value">{discountText(c)}</span>
              </Row>
            ))}
          </ul>
        )}
      </Section>

      {view.keywords !== undefined && (
        <Section title="대표키워드" count={view.keywords.length} onEdit={onEdit && (() => onEdit("keywords"))}>
          {view.keywords.length + (removed.keywords?.length ?? 0) === 0 ? (
            <p className="empty">아직 대표키워드가 없어요.</p>
          ) : (
            <ul className="keywords" aria-label="대표키워드">
              {[...view.keywords, ...(removed.keywords ?? [])].map((k) => (
                <Row key={k} mark={marks.keywords.get(k)}>
                  <span className="keyword">{k}</span>
                </Row>
              ))}
            </ul>
          )}
        </Section>
      )}

      {onRoutines && onRunReport && <RoutineSettings report={report} routines={routines} onChange={onRoutines} onRunReport={onRunReport} onEdit={onEdit && (() => onEdit("routines"))} />}
    </div>
  );
}
