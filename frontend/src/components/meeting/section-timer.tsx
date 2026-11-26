"use client";

import type { ReactNode } from "react";
import { AlarmClockOff, Timer } from "lucide-react";
import type { MeetingState } from "@/lib/api/types";
import { useServerNow } from "@/lib/server-clock";
import { formatMmSs } from "@/lib/time";
import { cn } from "@/lib/utils";

export interface SectionTimerProps {
  meeting: MeetingState;
  /** Client time (ms) at which `meeting` was received; `dataUpdatedAt` of the query that holds it. */
  receivedAt: number;
  /** Facilitator controls, rendered beside the clock so "Next" sits next to the time it ends. */
  actions?: ReactNode;
}

/**
 * The big section clock. Elapsed time is derived from the server's `section_started_at` and a clock
 * offset taken from `server_now`, ticking every 250 ms. It never advances the meeting: an overrun
 * section just turns unmistakably red and keeps counting.
 */
export function SectionTimer({ meeting, receivedAt, actions }: SectionTimerProps) {
  const nowMs = useServerNow(meeting.server_now, receivedAt);
  const section = meeting.agenda.find((a) => a.key === meeting.current_section);
  const planned = section?.duration_seconds ?? 0;

  const elapsed = Math.max(0, Math.floor((nowMs - Date.parse(meeting.section_started_at)) / 1000));
  const over = planned > 0 && elapsed > planned;
  const overBy = elapsed - planned;
  const fill = planned > 0 ? Math.min(100, (elapsed / planned) * 100) : 0;

  return (
    <section
      aria-label={`${section?.title ?? "Section"} timer`}
      className={cn(
        "flex flex-col gap-4 rounded-xl border p-5 transition-colors sm:flex-row sm:items-center sm:justify-between",
        over ? "border-bad/40 bg-bad-soft" : "bg-card",
      )}
    >
      <div className="min-w-0 flex-1">
        <p className={cn("flex items-center gap-1.5 text-sm font-medium", over ? "text-bad" : "text-muted-foreground")}>
          {over ? <AlarmClockOff className="size-4" aria-hidden /> : <Timer className="size-4" aria-hidden />}
          {section?.title ?? "Section"}
          <span className="font-normal">· planned {formatMmSs(planned)}</span>
        </p>

        <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span
            role="timer"
            aria-live="off"
            aria-label={`${formatMmSs(elapsed)} elapsed of ${formatMmSs(planned)}`}
            className={cn(
              "font-mono text-6xl leading-none font-semibold tracking-tight tabular-nums",
              over && "text-bad",
            )}
          >
            {formatMmSs(elapsed)}
          </span>
          <span className="font-mono text-2xl text-muted-foreground tabular-nums">/ {formatMmSs(planned)}</span>
          {over ? (
            <span className="rounded-md bg-bad px-2 py-0.5 font-mono text-xl font-semibold text-white tabular-nums">
              +{formatMmSs(overBy)} over
            </span>
          ) : null}
        </div>

        <div aria-hidden className="mt-3 h-2 overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              "h-full rounded-full transition-[width] duration-200",
              over ? "bg-overtime w-full" : "bg-primary",
            )}
            style={over ? undefined : { width: `${fill}%` }}
          />
        </div>

        {over ? (
          <p className="mt-2 text-sm text-bad">Over time. The meeting stays here until the facilitator moves it on.</p>
        ) : null}
        {/* Announced once when the section tips over, not on every tick. */}
        <span role="status" className="sr-only">
          {over ? "This section is over its planned time." : ""}
        </span>
      </div>

      {actions ? <div className="flex shrink-0 flex-col items-stretch gap-2 sm:items-end">{actions}</div> : null}
    </section>
  );
}
