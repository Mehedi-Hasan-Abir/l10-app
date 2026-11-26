"use client";

import { CalendarClock, CheckCheck, ListChecks } from "lucide-react";
import { InlineError } from "@/components/error-state";
import { RatingPicker } from "@/components/meeting/rating-picker";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useRateMeeting } from "@/lib/api/mutations";
import { useMeetingRecap } from "@/lib/api/queries";
import type { MeetingState, UserRef } from "@/lib/api/types";
import { formatRating, pluralize } from "@/lib/format";
import { formatDateWithWeekday } from "@/lib/time";

export interface ConcludePanelProps {
  teamId: string;
  meeting: MeetingState;
  me: UserRef | undefined;
  /** From the team's permissions. The server still refuses ratings from anyone ineligible. */
  canRate: boolean;
}

export function ConcludePanel({ teamId, meeting, me, canRate }: ConcludePanelProps) {
  return (
    <div className="grid items-start gap-5 xl:grid-cols-2">
      <RecapCard teamId={teamId} meetingId={meeting.id} />
      <RatingsCard meeting={meeting} me={me} canRate={canRate} />
    </div>
  );
}

function RecapCard({ teamId, meetingId }: { teamId: string; meetingId: string }) {
  const recap = useMeetingRecap(teamId, meetingId);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recap</CardTitle>
        <CardDescription>What this meeting produced so far. Updates as To-Dos are added.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {recap.isPending ? (
          <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading recap">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : recap.isError ? (
          <InlineError error={recap.error} onRetry={() => void recap.refetch()} />
        ) : (
          <>
            <section aria-labelledby="recap-todos">
              <h3 id="recap-todos" className="mb-2 flex items-center gap-1.5 text-sm font-medium">
                <ListChecks className="size-4 text-muted-foreground" aria-hidden />
                New To-Dos ({recap.data.todos_created.length})
              </h3>
              {recap.data.todos_created.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No To-Dos yet. Add them from an issue while you solve it.
                </p>
              ) : (
                <ul className="flex flex-col divide-y rounded-lg border">
                  {recap.data.todos_created.map((todo) => (
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
            </section>

            <section aria-labelledby="recap-issues">
              <h3 id="recap-issues" className="mb-2 flex items-center gap-1.5 text-sm font-medium">
                <CheckCheck className="size-4 text-muted-foreground" aria-hidden />
                Issues solved ({recap.data.issues_solved.length})
              </h3>
              {recap.data.issues_solved.length === 0 ? (
                <p className="text-sm text-muted-foreground">No issues were solved in this meeting.</p>
              ) : (
                <ul className="flex flex-col divide-y rounded-lg border">
                  {recap.data.issues_solved.map((issue) => (
                    <li key={issue.id} className="px-3 py-2">
                      <p className="font-medium text-pretty">{issue.title}</p>
                      {issue.resolution_note ? (
                        <p className="mt-0.5 text-xs text-muted-foreground">{issue.resolution_note}</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function RatingsCard({ meeting, me, canRate }: { meeting: MeetingState; me: UserRef | undefined; canRate: boolean }) {
  const rate = useRateMeeting();

  const myRating = me ? (meeting.ratings.find((r) => r.user_id === me.id)?.rating ?? null) : null;
  const isAttendee = me ? meeting.attendees.some((a) => a.id === me.id) : false;
  const eligible = canRate && isAttendee && meeting.status === "active";

  const ratedIds = new Set(meeting.ratings.map((r) => r.user_id));
  // Observers never rate, so they're not "waiting" on anyone.
  const waiting = meeting.attendees.filter((a) => a.role !== "observer" && !ratedIds.has(a.id));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Rate this meeting</CardTitle>
        <CardDescription>
          1 is poor, 10 is excellent. You can change your rating until the meeting ends.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {eligible ? (
          <div className="flex flex-col gap-2">
            <RatingPicker
              value={myRating}
              disabled={rate.isPending}
              onChange={(rating) => rate.mutate({ meetingId: meeting.id, rating })}
            />
            <p className="text-sm text-muted-foreground" aria-live="polite">
              {myRating === null ? "You haven't rated yet." : `Your rating: ${myRating}`}
            </p>
          </div>
        ) : (
          <p className="rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
            {!canRate
              ? "Observers can watch the meeting but can't rate it."
              : !isAttendee
                ? "Joining the meeting…"
                : "Ratings are closed."}
          </p>
        )}

        <div>
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="text-sm font-medium">Ratings</h3>
            <p className="text-sm text-muted-foreground">
              {meeting.average_rating === null ? (
                "No ratings yet"
              ) : (
                <>
                  Average{" "}
                  <span className="font-mono text-xl font-semibold text-foreground tabular-nums">
                    {formatRating(meeting.average_rating)}
                  </span>
                  <span className="text-xs"> from {pluralize(meeting.ratings.length, "rating")}</span>
                </>
              )}
            </p>
          </div>
          <ul className="mt-2 flex flex-col divide-y rounded-lg border">
            {meeting.ratings.map((r) => (
              <li key={r.user_id} className="flex items-center justify-between px-3 py-1.5 text-sm">
                <span>
                  {r.name}
                  {r.user_id === me?.id ? <span className="text-muted-foreground"> (you)</span> : null}
                </span>
                <span className="font-mono font-semibold tabular-nums">{r.rating}</span>
              </li>
            ))}
            {waiting.map((a) => (
              <li key={a.id} className="flex items-center justify-between px-3 py-1.5 text-sm text-muted-foreground">
                <span>{a.name}</span>
                <span className="text-xs">Not rated yet</span>
              </li>
            ))}
            {meeting.ratings.length === 0 && waiting.length === 0 ? (
              <li className="px-3 py-2 text-sm text-muted-foreground">Nobody is eligible to rate.</li>
            ) : null}
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}
