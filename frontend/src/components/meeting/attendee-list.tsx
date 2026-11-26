import { RoleBadge } from "@/components/role-badge";
import type { MeetingState } from "@/lib/api/types";
import { pluralize } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Who is in the meeting. The dot shows who has the page open right now (presence), not who joined. */
export function AttendeeList({ meeting, currentUserId }: { meeting: MeetingState; currentUserId: string | undefined }) {
  const online = meeting.attendees.filter((a) => a.online).length;

  return (
    <section aria-label="Attendees" className="rounded-xl border bg-card p-2">
      <h2 className="flex items-baseline justify-between px-2 pt-1 pb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Attendees
        <span className="font-normal normal-case">{online} online</span>
      </h2>
      {meeting.attendees.length === 0 ? (
        <p className="px-2 pb-2 text-sm text-muted-foreground">Nobody has joined yet.</p>
      ) : (
        <ul className="flex flex-col">
          {meeting.attendees.map((a) => (
            <li key={a.id} className="flex items-center gap-2 rounded-lg px-2 py-1.5">
              <span
                aria-hidden
                className={cn("size-2 shrink-0 rounded-full", a.online ? "bg-ok" : "border border-muted-foreground/50")}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">
                  {a.name}
                  {a.id === currentUserId ? <span className="font-normal text-muted-foreground"> (you)</span> : null}
                </span>
                <span className="sr-only">{a.online ? "Online." : "Offline."}</span>
                {a.id === meeting.facilitator.id ? (
                  <span className="block text-xs text-muted-foreground">Facilitator</span>
                ) : null}
              </span>
              {a.role === "observer" ? <RoleBadge role={a.role} /> : null}
            </li>
          ))}
        </ul>
      )}
      <p className="sr-only">{pluralize(meeting.attendees.length, "attendee")} in this meeting.</p>
    </section>
  );
}
