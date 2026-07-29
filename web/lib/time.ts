/**
 * Timestamps are stored in UTC everywhere. They are converted to US Eastern
 * only here, for display. Nothing in the pipeline should call these.
 */

const ET_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZoneName: "short",
});

/** UTC ISO-8601 string -> "Jul 29, 2026, 09:45 EDT". Returns "—" for null. */
export function formatEastern(utcIso: string | null | undefined): string {
  if (!utcIso) return "—";
  const date = new Date(utcIso);
  if (Number.isNaN(date.getTime())) return utcIso;
  return ET_FORMATTER.format(date);
}

/** Hours between a UTC timestamp and now, or null if unknown. */
export function hoursSince(utcIso: string | null | undefined, now = new Date()): number | null {
  if (!utcIso) return null;
  const date = new Date(utcIso);
  if (Number.isNaN(date.getTime())) return null;
  return (now.getTime() - date.getTime()) / 3_600_000;
}
