import { useState, type ReactNode } from "react";
import { DAYS, FREQUENCY_TEXT, REPORT_TEXT, reportScheduleText, ROUTINE_TEXT } from "../labels";
import type { ReviewReport, Routines, RoutinesChange } from "../types";

type Props = {
  report: ReviewReport | null;
  /** 예전 서버라 모르면 null: 스위치 없이 보고 한 줄만 보인다. */
  routines: Routines | null;
  /** 스위치·일정을 바꾼다. 성공하면 null, 실패하면 보일 문장. */
  onChange: (change: RoutinesChange) => Promise<string | null>;
  /** [지금 한 번 받아 보기]. 설정과 상관없이 보고 한 회분을 대화로 받는다. */
  onRunReport: () => Promise<string | null>;
};

const TIME = /^\d{2}:\d{2}$/;

function Switch({ label, on, onText, offText, disabled, onToggle }: { label: string; on: boolean; onText: string; offText: string; disabled: boolean; onToggle: () => void }) {
  return (
    <button type="button" role="switch" className="routine-switch" aria-label={label} aria-checked={on} disabled={disabled} onClick={onToggle}>
      <span>{on ? onText : offText}</span>
      <i aria-hidden />
    </button>
  );
}

function Choices<T extends string>({ label, items, value, disabled, onPick, className }: { label: string; items: Array<[T, string, string?]>; value: string | null; disabled: boolean; onPick: (key: T) => void; className?: string }) {
  return (
    <div className="routine-field">
      <span className="routine-field-label">{label}</span>
      <div className={className ?? "routine-chips"} role="radiogroup" aria-label={label}>
        {items.map(([key, text, name]) => (
          <button key={key} type="button" role="radio" aria-checked={value === key} aria-label={name ?? text} disabled={disabled} onClick={() => value !== key && onPick(key)}>
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}

function Routine({ title, hint, on, children }: { title: string; hint: string; on: boolean; children: ReactNode }) {
  return (
    <li className="routine" data-on={on}>
      <div className="routine-head">
        <strong>{title}</strong>
        <span>{hint}</span>
      </div>
      {children}
    </li>
  );
}

export function RoutineSettings({ report, routines, onChange, onRunReport }: Props) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const savedTime = routines?.report.time ?? "09:00";
  // 입력 중인 시각. 서버 값이 바뀌면(저장·다른 곳에서 변경) 그 값을 따른다.
  const [typed, setTyped] = useState({ saved: savedTime, value: savedTime });
  const time = typed.saved === savedTime ? typed.value : savedTime;
  const setTime = (value: string) => setTyped({ saved: savedTime, value });

  const change = async (value: RoutinesChange) => {
    if (saving) return;
    setSaving(true);
    setError(null);
    setError(await onChange(value));
    setSaving(false);
  };
  const run = async () => {
    if (running) return;
    setRunning(true);
    setRunError(null);
    setRunError(await onRunReport());
    setRunning(false);
  };

  const enabled = Boolean(report?.enabled);
  const schedule = routines?.report;
  const needsWeekday = schedule?.frequency === "weekly" || schedule?.frequency === "biweekly";
  const count = routines ? Number(enabled) + Number(routines.drafts) : null;
  const runButton = (
    <button type="button" className="ghost small" disabled={running} onClick={() => void run()}>
      {running ? REPORT_TEXT.running : REPORT_TEXT.runNow}
    </button>
  );

  return (
    <section className="shop-section routines" aria-label={ROUTINE_TEXT.section}>
      <h3>
        {ROUTINE_TEXT.section}
        {count !== null && <span>{count}</span>}
      </h3>
      <ul className="routine-list">
        {!routines ? (
          <Routine title={ROUTINE_TEXT.report} hint={report?.text ?? REPORT_TEXT.off} on={enabled}>
            {runButton}
          </Routine>
        ) : (
          <>
            <Routine title={ROUTINE_TEXT.report} hint={enabled && report?.schedule ? reportScheduleText(report.schedule) : ROUTINE_TEXT.reportOffHint} on={enabled}>
              <Switch
                label={ROUTINE_TEXT.report}
                on={enabled}
                onText={ROUTINE_TEXT.reportOn}
                offText={ROUTINE_TEXT.reportOff}
                disabled={saving}
                onToggle={() => void change({ report: { enabled: !enabled } })}
              />
              {enabled && schedule && (
                <div className="routine-schedule">
                  <Choices label={ROUTINE_TEXT.frequency} items={FREQUENCY_TEXT} value={schedule.frequency} disabled={saving} onPick={(frequency) => void change({ report: { frequency } })} />
                  {needsWeekday && (
                    <Choices
                      label={ROUTINE_TEXT.weekday}
                      className="routine-days"
                      items={DAYS.map(([key, name]) => [key, name.slice(0, 1), name])}
                      value={schedule.weekday}
                      disabled={saving}
                      onPick={(weekday) => void change({ report: { weekday } })}
                    />
                  )}
                  <label className="routine-field routine-time">
                    <span className="routine-field-label">{ROUTINE_TEXT.time}</span>
                    <input
                      type="time"
                      step={1800}
                      value={time}
                      onChange={(e) => {
                        const value = e.target.value;
                        setTime(value);
                        if (TIME.test(value) && value !== savedTime) void change({ report: { time: value } });
                      }}
                    />
                  </label>
                </div>
              )}
              {runButton}
            </Routine>
            <Routine title={ROUTINE_TEXT.drafts} hint={routines.drafts ? ROUTINE_TEXT.draftsOnHint : ROUTINE_TEXT.draftsOffHint} on={routines.drafts}>
              <Switch
                label={ROUTINE_TEXT.drafts}
                on={routines.drafts}
                onText={ROUTINE_TEXT.draftsOn}
                offText={ROUTINE_TEXT.draftsOff}
                disabled={saving}
                onToggle={() => void change({ drafts: !routines.drafts })}
              />
            </Routine>
          </>
        )}
      </ul>
      {error && (
        <p className="inbox-error" role="alert">
          {error}
        </p>
      )}
      {runError && (
        <p className="inbox-error" role="alert">
          {runError}
        </p>
      )}
    </section>
  );
}
