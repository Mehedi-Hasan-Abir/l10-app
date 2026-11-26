const scoreFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const ratingFmt = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** Scores arrive as JSON numbers; show them without float noise ("12.5", "1,200"). */
export function formatScore(value: number | null): string {
  return value === null ? "—" : scoreFmt.format(value);
}

/** A meeting rating average, always one decimal ("8.3", "9.0"). */
export function formatRating(value: number): string {
  return ratingFmt.format(value);
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "?";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}
