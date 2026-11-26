"use client";

import Link from "next/link";
import { ArrowLeft, CalendarClock, CheckCheck, Gauge, ListChecks, Users } from "lucide-react";
import { InlineError } from "@/components/error-state";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useMeetingSummary } from "@/lib/api/queries";
import type { MeetingSummary as MeetingSummaryData } from "@/lib/api/types";
import { formatRating, formatScore } from "@/lib/format";
import { formatDateTime, formatDateWithWeekday, formatTime, formatWeekLabel } from "@/lib/time";

/** What attendees see once the meeting has ended: the frozen snapshot from the server. */
export function MeetingSummary({ teamId, meetingId }: { teamId: string; meetingId: string }) {
  const summary = useMeetingSummary(teamId, meetingId);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Meeting summary</h1>
          {summary.data ? (
            <p className="mt-1 text-sm text-muted-foreground">
              {formatDateTime(summary.data.started_at)} to {formatTime(summary.data.ended_at)} · reviewed the week of{" "}
              {formatWeekLabel(summary.data.scorecard_week)}
            </p>
          ) : null}
        </div>
        <Button asChild variant="outline" size="lg">
          <Link href={`/teams/${teamId}`}>
            <ArrowLeft data-icon="inline-start" aria-hidden />
            Back to team
          </Link>
        </Button>
      </div>

      {summary.isPending ? (
        <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading summary">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : summary.isError ? (
        <InlineError error={summary.error} onRetry={() => void summary.refetch()} />
      ) : (
        <SummaryBody summary={summary.data} />
      )}
    </div>
  );
}

function SummaryBody({ summary }: { summary: MeetingSummaryData }) {
  return (
    <>
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Meeting rating">
          {summary.rating === null ? (
            <span className="text-lg text-muted-foreground">No ratings</span>
          ) : (
            <>
              <span className="font-mono text-4xl font-semibold tabular-nums">{formatRating(summary.rating)}</span>
              <span className="text-sm text-muted-foreground"> / 10</span>
            </>
          )}
        </Stat>
        <Stat label="Attendees">
          <span className="font-mono text-4xl font-semibold tabular-nums">{summary.attendees.length}</span>
        </Stat>
        <Stat label="Issues solved">
          <span className="font-mono text-4xl font-semibold tabular-nums">{summary.issues_solved.length}</span>
        </Stat>
        <Stat label="To-Dos created">
          <span className="font-mono text-4xl font-semibold tabular-nums">{summary.todos_created.length}</span>
        </Stat>
      </dl>

      <div className="grid items-start gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Gauge className="size-4 text-muted-foreground" aria-hidden />
              Scorecard, week of {formatWeekLabel(summary.scorecard_week)}
            </CardTitle>
            <CardDescription>Status of each measurable for the week that was reviewed.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-col divide-y rounded-lg border">
              {summary.measurable_status.map((m) => (
                <li key={m.measurable_id} className="flex items-center justify-between gap-3 px-3 py-2">
                  <span className="min-w-0 truncate font-medium">{m.name}</span>
                  <span className="flex shrink-0 items-center gap-3">
                    <span className="font-mono text-sm tabular-nums">{formatScore(m.value)}</span>
                    <StatusBadge status={m.status} />
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <div className="flex flex-col gap-5">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CheckCheck className="size-4 text-muted-foreground" aria-hidden />
                Issues solved
              </CardTitle>
            </CardHeader>
            <CardContent>
              {summary.issues_solved.length === 0 ? (
                <p className="text-sm text-muted-foreground">No issues were solved in this meeting.</p>
              ) : (
                <ul className="flex flex-col divide-y rounded-lg border">
                  {summary.issues_solved.map((issue) => (
                    <li key={issue.id} className="px-3 py-2 font-medium text-pretty">
                      {issue.title}
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ListChecks className="size-4 text-muted-foreground" aria-hidden />
                To-Dos created
              </CardTitle>
            </CardHeader>
            <CardContent>
              {summary.todos_created.length === 0 ? (
                <p className="text-sm text-muted-foreground">No To-Dos were created in this meeting.</p>
              ) : (
                <ul className="flex flex-col divide-y rounded-lg border">
                  {summary.todos_created.map((todo) => (
                    <li key={todo.id} className="px-3 py-2">
                      <p className="font-medium text-pretty">{todo.title}</p>
                      <p className="mt-0.5 flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
                        <span>{todo.owner.name}</span>
                        <span className="inline-flex items-center gap-1">
                          <CalendarClock className="size-3" aria-hidden />
                          Due {formatDateWithWeekday(todo.due_date)}
                        </span>
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users className="size-4 text-muted-foreground" aria-hidden />
            Attendees
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-wrap gap-2">
            {summary.attendees.map((a) => (
              <li key={a.id} className="rounded-full border bg-muted/50 px-3 py-1 text-sm">
                {a.name}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3">
      <dt className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{label}</dt>
      <dd className="mt-1 flex items-baseline">{children}</dd>
    </div>
  );
}
