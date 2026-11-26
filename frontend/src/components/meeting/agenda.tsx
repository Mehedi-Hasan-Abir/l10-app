import { Check } from "lucide-react";
import type { AgendaItem, SectionKey } from "@/lib/api/types";
import { formatMinutes } from "@/lib/time";
import { cn } from "@/lib/utils";

/** The three-section agenda: finished sections are checked, the current one is highlighted. */
export function Agenda({
  agenda,
  current,
  ended = false,
}: {
  agenda: AgendaItem[];
  current: SectionKey;
  ended?: boolean;
}) {
  const currentIndex = agenda.findIndex((a) => a.key === current);

  return (
    <nav aria-label="Meeting agenda" className="rounded-xl border bg-card p-2">
      <h2 className="px-2 pt-1 pb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Agenda</h2>
      <ol className="flex flex-col gap-1">
        {agenda.map((item, index) => {
          const done = ended || index < currentIndex;
          const active = !ended && index === currentIndex;
          return (
            <li
              key={item.key}
              aria-current={active ? "step" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-lg px-2 py-2 transition-colors",
                active && "bg-primary text-primary-foreground",
                done && "text-muted-foreground",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "grid size-5 shrink-0 place-items-center rounded-full border text-[10px]",
                  done && "border-ok bg-ok text-white",
                  active && "border-primary-foreground/60",
                  !done && !active && "border-border",
                )}
              >
                {done ? (
                  <Check className="size-3" />
                ) : active ? (
                  <span className="size-2 rounded-full bg-primary-foreground" />
                ) : null}
              </span>
              <span className="min-w-0 flex-1 text-sm font-medium">
                {item.title}
                <span className="sr-only">{done ? " (done)" : active ? " (current)" : " (upcoming)"}</span>
              </span>
              <span
                className={cn(
                  "font-mono text-xs tabular-nums",
                  active ? "text-primary-foreground/80" : "text-muted-foreground",
                )}
              >
                {formatMinutes(item.duration_seconds)}
              </span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
