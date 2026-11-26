import { formatScore } from "@/lib/format";
import { formatDate } from "@/lib/time";
import type { AuditEvent } from "@/lib/api/types";

/** What an audit event reads like after the actor's name: "Blake Brooks" + `solved “Two key accounts at risk”`. */
export interface AuditLine {
  text: string;
  /** A second, quieter line (the resolution note of a solved issue). */
  detail: string | null;
}

type Data = AuditEvent["data"];

/** `data` is free-form JSON on the wire; read each field defensively so one odd row can't break the list. */
function str(data: Data, key: string): string | null {
  const value = data[key];
  return typeof value === "string" ? value : null;
}

function num(data: Data, key: string): number | null {
  const value = data[key];
  return typeof value === "number" ? value : null;
}

const quote = (title: string | null, fallback: string): string => (title ? `“${title}”` : fallback);

const SECTION_LABELS: Record<string, string> = { scorecard: "Scorecard", ids: "IDS", conclude: "Conclude" };
const sectionLabel = (key: string | null): string => (key ? (SECTION_LABELS[key] ?? key) : "?");

function scoreName(data: Data): string {
  return `“${str(data, "measurable_name") ?? str(data, "measurable_id") ?? "a measurable"}”`;
}

function scoreWeek(data: Data): string {
  const week = str(data, "week_start");
  return week ? ` for the week of ${formatDate(week)}` : "";
}

function issueOrigin(data: Data): string {
  const source = data.source;
  const type = source && typeof source === "object" && "type" in source ? source.type : null;
  switch (type) {
    case "measurable":
      return " from the scorecard";
    case "rock":
      return " from a Rock";
    case "todo":
      return " from an overdue To-Do";
    default:
      return "";
  }
}

function priorityText(oldValue: number | null, newValue: number | null, title: string): string {
  if (newValue === null) return `cleared the priority of ${title} (was ${oldValue ?? "none"})`;
  return oldValue === null
    ? `set the priority of ${title} to ${newValue}`
    : `changed the priority of ${title} from ${oldValue} to ${newValue}`;
}

/**
 * One human-readable line per event. `people` maps user ids to names (for the owner of a new To-Do).
 * The switch has no default on purpose: a new backend action fails type-checking here until it is worded.
 */
export function describeAuditEvent(event: AuditEvent, people: ReadonlyMap<string, string>): AuditLine {
  const { data } = event;
  const title = quote(str(data, "title"), "an item");

  switch (event.action) {
    case "score.set": {
      const old = num(data, "old");
      const was = old === null ? "" : ` (was ${formatScore(old)})`;
      return {
        text: `set ${scoreName(data)} to ${formatScore(num(data, "new"))}${scoreWeek(data)}${was}`,
        detail: null,
      };
    }
    case "score.cleared": {
      const old = num(data, "old");
      const was = old === null ? "" : ` (was ${formatScore(old)})`;
      return { text: `cleared ${scoreName(data)}${scoreWeek(data)}${was}`, detail: null };
    }
    case "issue.created":
      return { text: `created the issue ${title}${issueOrigin(data)}`, detail: null };
    case "issue.priority_changed":
      return { text: priorityText(num(data, "old"), num(data, "new"), title), detail: null };
    case "issue.solved":
      return { text: `solved ${title}`, detail: str(data, "resolution_note") };
    case "todo.created": {
      const ownerId = str(data, "owner_id");
      const owner = ownerId ? people.get(ownerId) : undefined;
      const due = str(data, "due_date");
      return {
        text: `added the To-Do ${title}` + (owner ? ` for ${owner}` : "") + (due ? `, due ${formatDate(due)}` : ""),
        detail: null,
      };
    }
    case "todo.done":
      return { text: `completed the To-Do ${title}`, detail: null };
    case "todo.reopened":
      return { text: `reopened the To-Do ${title}`, detail: null };
    case "meeting.started":
      return { text: "started the meeting", detail: null };
    case "meeting.advanced":
      return {
        text: `moved the meeting from ${sectionLabel(str(data, "from"))} to ${sectionLabel(str(data, "to"))}`,
        detail: null,
      };
    case "meeting.rated": {
      const rating = num(data, "rating");
      return { text: rating === null ? "rated the meeting" : `rated the meeting ${rating}/10`, detail: null };
    }
    case "meeting.ended":
      return { text: "ended the meeting", detail: null };
  }
}
