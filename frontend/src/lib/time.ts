/**
 * Date and time formatting. Everything renders in the company time zone, never the browser's,
 * so two people in different places read the same "Nov 9, 10:00 AM".
 *
 * Timestamps (`...Z` instants) and calendar dates (`YYYY-MM-DD`) are formatted differently on purpose:
 * a calendar date has no time zone, so it is formatted from its own year/month/day and can never
 * slip a day.
 */

export const COMPANY_TIME_ZONE = "America/Chicago";

const dateTimeFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: COMPANY_TIME_ZONE,
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

const timeFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: COMPANY_TIME_ZONE,
  hour: "numeric",
  minute: "2-digit",
});

const instantDateFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: COMPANY_TIME_ZONE,
  month: "short",
  day: "numeric",
});

// Calendar dates are anchored at UTC midnight and formatted in UTC: no shift in either direction.
const calendarShortFmt = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
const calendarWeekdayFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  weekday: "short",
  month: "short",
  day: "numeric",
});
const calendarLongFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  weekday: "long",
  year: "numeric",
  month: "long",
  day: "numeric",
});

function calendarDate(ymd: string): Date {
  return new Date(`${ymd}T00:00:00Z`);
}

/** "Nov 9, 10:00 AM" */
export function formatDateTime(iso: string): string {
  return dateTimeFmt.format(new Date(iso));
}

/** "10:00 AM" */
export function formatTime(iso: string): string {
  return timeFmt.format(new Date(iso));
}

/** "Nov 9" for an instant (company time). */
export function formatInstantDate(iso: string): string {
  return instantDateFmt.format(new Date(iso));
}

/** "Nov 9" for a calendar date. Also the week label: weeks are named by their Monday. */
export function formatDate(ymd: string): string {
  return calendarShortFmt.format(calendarDate(ymd));
}

export const formatWeekLabel = formatDate;

/** "Wed, Nov 11" */
export function formatDateWithWeekday(ymd: string): string {
  return calendarWeekdayFmt.format(calendarDate(ymd));
}

/** "Monday, November 9, 2026" */
export function formatDateLong(ymd: string): string {
  return calendarLongFmt.format(calendarDate(ymd));
}

/** "Today: Wed, Nov 11 · Week of Nov 9" */
export function formatBusinessClock(today: string, currentWeek: string): string {
  return `Today: ${formatDateWithWeekday(today)} · Week of ${formatWeekLabel(currentWeek)}`;
}

/** 0 -> "00:00", 305 -> "05:05", 3725 -> "62:05" (minutes keep counting; meetings are short). */
export function formatMmSs(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

/** 300 -> "5 min" */
export function formatMinutes(seconds: number): string {
  return `${Math.round(seconds / 60)} min`;
}
