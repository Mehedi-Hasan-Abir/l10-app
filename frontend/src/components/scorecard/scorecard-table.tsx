"use client";

import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { InlineError } from "@/components/error-state";
import { useIssueFocus } from "@/components/issues/issue-focus";
import { ScoreCell } from "@/components/scorecard/score-cell";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { useMakeIssue, usePutScore } from "@/lib/api/mutations";
import { useScorecard } from "@/lib/api/queries";
import type { Measurable, ScoreCell as ScoreCellData } from "@/lib/api/types";
import { formatWeekLabel } from "@/lib/time";
import { cn } from "@/lib/utils";

export interface ScorecardTableProps {
  teamId: string;
  canWrite: boolean;
  /**
   * "team": the page-level card, with its own title.
   * "meeting": the Scorecard section of a running meeting, focused on `reviewWeek`.
   */
  context?: "team" | "meeting";
  /** The week being reviewed. Defaults to the last closed week reported by the API. */
  reviewWeek?: string;
}

export function ScorecardTable({ teamId, canWrite, context = "team", reviewWeek }: ScorecardTableProps) {
  const scorecard = useScorecard(teamId);
  const putScore = usePutScore(teamId);
  const makeIssue = useMakeIssue(teamId);
  const { focusIssue } = useIssueFocus();

  const isMeeting = context === "meeting";
  const scroller = useRef<HTMLDivElement>(null);
  const scrolled = useRef(false);
  const ready = scorecard.data !== undefined;

  // 13 columns rarely fit a laptop: start at the right edge, where the current and last closed weeks are.
  useEffect(() => {
    const el = scroller.current;
    if (!el || !ready || scrolled.current) return;
    scrolled.current = true;
    el.scrollLeft = el.scrollWidth;
  }, [ready]);

  function handleSave(measurable: Measurable, cell: ScoreCellData, value: number | null) {
    putScore.mutate({
      measurableId: measurable.id,
      weekStart: cell.week_start,
      value,
      expectedVersion: cell.version,
    });
  }

  function handleMakeIssue(measurable: Measurable, cell: ScoreCellData) {
    makeIssue.mutate(
      { measurableId: measurable.id, weekStart: cell.week_start },
      {
        onSuccess: ({ issue, created }) => {
          // In a meeting the Issues panel belongs to the next section, so there is nothing to point at yet.
          const action = isMeeting ? undefined : { label: "Show", onClick: () => focusIssue(issue.id) };
          if (created) {
            toast.success("Issue created", { description: issue.title, action });
          } else {
            toast.info("Already an issue", {
              description: `${issue.title} is already open, so no duplicate was made.`,
              action,
            });
          }
          if (!isMeeting) focusIssue(issue.id);
        },
      },
    );
  }

  const data = scorecard.data;
  const highlighted = reviewWeek ?? data?.last_closed_week;

  return (
    <Card id="scorecard" className="scroll-mt-20">
      <CardHeader>
        <CardTitle>Scorecard</CardTitle>
        <CardDescription>
          {isMeeting && highlighted
            ? `Review the week of ${formatWeekLabel(highlighted)}. Turn off-track scores into Issues.`
            : "Weekly numbers for the last 13 weeks."}
        </CardDescription>
        {data ? (
          <CardAction>
            <ScorecardLegend canWrite={canWrite} />
          </CardAction>
        ) : null}
      </CardHeader>
      <CardContent className="px-0">
        {scorecard.isPending ? (
          <ScorecardSkeleton />
        ) : scorecard.isError ? (
          <div className="px-4">
            <InlineError error={scorecard.error} onRetry={() => void scorecard.refetch()} />
          </div>
        ) : data && data.measurables.length === 0 ? (
          <Empty className="mx-4 border">
            <EmptyHeader>
              <EmptyTitle>No measurables yet</EmptyTitle>
              <EmptyDescription>This team has no weekly numbers to track.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : data ? (
          <div ref={scroller} className="overflow-x-auto">
            <table className="w-full border-separate border-spacing-0 text-sm">
              <caption className="sr-only">
                Weekly scores by measurable for the last 13 weeks. Weeks are named by their Monday.
              </caption>
              <thead>
                <tr>
                  <th
                    scope="col"
                    className="sticky left-0 z-20 min-w-56 border-b bg-card px-4 py-2 text-left text-xs font-medium text-muted-foreground"
                  >
                    Measurable
                  </th>
                  {data.weeks.map((week) => {
                    const isReview = week === highlighted;
                    const isCurrent = week === data.current_week;
                    return (
                      <th
                        key={week}
                        scope="col"
                        className={cn(
                          "border-b px-1 pt-2 pb-1.5 text-center align-bottom font-normal",
                          isReview ? "min-w-36 border-x border-x-review/30 bg-review/5" : "min-w-[4.75rem]",
                        )}
                      >
                        <div className="font-mono text-xs font-medium tabular-nums">{formatWeekLabel(week)}</div>
                        <div
                          className={cn(
                            "text-[10px] leading-4 font-medium tracking-wide uppercase",
                            isReview ? "text-review" : "text-muted-foreground",
                          )}
                        >
                          {isReview ? (isMeeting ? "Reviewing" : "Last closed") : isCurrent ? "Current" : " "}
                        </div>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {data.measurables.map((m) => (
                  <tr key={m.id} className="group/row">
                    <th
                      scope="row"
                      className="sticky left-0 z-10 border-b bg-card px-4 py-1.5 text-left font-normal group-last/row:border-b-0"
                    >
                      <div className="truncate font-medium" title={m.name}>
                        {m.name}
                      </div>
                      <div className="truncate text-xs text-muted-foreground">
                        {m.owner.name} · Goal {m.goal_label}
                      </div>
                    </th>
                    {m.cells.map((cell) => {
                      const isReview = cell.week_start === highlighted;
                      return (
                        <td
                          key={cell.week_start}
                          className={cn(
                            "border-b p-1 align-middle group-last/row:border-b-0",
                            isReview && "border-x border-x-review/30 bg-review/5",
                          )}
                        >
                          <ScoreCell
                            measurable={m}
                            cell={cell}
                            canWrite={canWrite}
                            isReviewWeek={isReview}
                            makingIssue={
                              makeIssue.isPending &&
                              makeIssue.variables?.measurableId === m.id &&
                              makeIssue.variables.weekStart === cell.week_start
                            }
                            onSave={handleSave}
                            onMakeIssue={handleMakeIssue}
                            onViewIssue={isMeeting ? undefined : focusIssue}
                          />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function ScorecardLegend({ canWrite }: { canWrite: boolean }) {
  return (
    <p className="hidden text-xs text-muted-foreground md:block">
      {canWrite ? "Click a score to edit · Enter saves · Esc cancels" : "Read-only"}
    </p>
  );
}

function ScorecardSkeleton() {
  return (
    <div className="flex flex-col gap-2 px-4" aria-busy="true" aria-label="Loading scorecard">
      <Skeleton className="h-8 w-full" />
      {Array.from({ length: 5 }, (_, row) => (
        <div key={row} className="flex items-center gap-2">
          <Skeleton className="h-11 w-52 shrink-0" />
          <Skeleton className="h-11 flex-1" />
        </div>
      ))}
    </div>
  );
}
