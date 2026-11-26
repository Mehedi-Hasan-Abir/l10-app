"use client";

import { useState } from "react";
import { Check, ChevronRight, Circle, Mountain } from "lucide-react";
import { InlineError } from "@/components/error-state";
import { MakeIssueButton } from "@/components/issues/make-issue-button";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { useRocks } from "@/lib/api/queries";
import type { Milestone, Rock } from "@/lib/api/types";
import { pluralize } from "@/lib/format";
import { formatDate } from "@/lib/time";
import { cn } from "@/lib/utils";

/** "2026-Q4" -> "Q4 2026" */
function quarterLabel(quarter: string): string {
  const [year, q] = quarter.split("-");
  return q && year ? `${q} ${year}` : quarter;
}

export function RocksPanel({ teamId, canWrite }: { teamId: string; canWrite: boolean }) {
  const rocks = useRocks(teamId);

  return (
    <Card id="rocks" className="scroll-mt-20">
      <CardHeader>
        <CardTitle>Rocks</CardTitle>
        <CardDescription>
          {rocks.data
            ? `${quarterLabel(rocks.data.quarter)} goals. Expand a rock for its milestones.`
            : "Quarterly goals."}
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        {rocks.isPending ? (
          <div className="flex flex-col gap-2 px-4" aria-busy="true" aria-label="Loading rocks">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : rocks.isError ? (
          <div className="px-4">
            <InlineError error={rocks.error} onRetry={() => void rocks.refetch()} />
          </div>
        ) : rocks.data.rocks.length === 0 ? (
          <Empty className="mx-4 border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Mountain aria-hidden />
              </EmptyMedia>
              <EmptyTitle>No Rocks this quarter</EmptyTitle>
              <EmptyDescription>
                This team has no quarterly goals for {quarterLabel(rocks.data.quarter)}.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <ul className="flex flex-col divide-y border-t">
            {rocks.data.rocks.map((rock) => (
              <RockRow key={rock.id} rock={rock} teamId={teamId} canWrite={canWrite} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function RockRow({ rock, teamId, canWrite }: { rock: Rock; teamId: string; canWrite: boolean }) {
  const [open, setOpen] = useState(false);
  const doneCount = rock.milestones.filter((m) => m.done).length;

  return (
    <li>
      <Collapsible open={open} onOpenChange={setOpen}>
        <div className="flex items-start">
          <CollapsibleTrigger
            className="group/rock flex w-full items-start gap-2 px-4 py-3 text-left outline-none hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset"
            aria-label={`${rock.title}, ${rock.progress_percent}% complete. ${open ? "Collapse" : "Expand"} milestones`}
          >
            <ChevronRight
              className={cn("mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
              aria-hidden
            />
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="font-medium leading-snug text-pretty">{rock.title}</span>
                <StatusBadge status={rock.status} />
              </span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {rock.owner.name}
                {rock.milestones.length > 0
                  ? ` · ${doneCount} of ${pluralize(rock.milestones.length, "milestone")} done`
                  : " · No milestones"}
              </span>
              <span className="mt-2 flex items-center gap-2">
                <Progress value={rock.progress_percent} className="h-1.5 flex-1" aria-hidden />
                <span className="w-9 text-right font-mono text-xs font-medium tabular-nums">
                  {rock.progress_percent}%
                </span>
              </span>
            </span>
          </CollapsibleTrigger>
          {canWrite && rock.status === "off_track" ? (
            <div className="py-2.5 pr-3">
              <MakeIssueButton teamId={teamId} source="rock" id={rock.id} label={rock.title} />
            </div>
          ) : null}
        </div>
        <CollapsibleContent>
          {rock.milestones.length === 0 ? (
            <p className="px-4 pb-3 pl-10 text-sm text-muted-foreground">
              No milestones. Status and progress come from the rock itself.
            </p>
          ) : (
            <ul className="flex flex-col gap-1 px-4 pb-3 pl-10">
              {rock.milestones.map((milestone) => (
                <MilestoneRow key={milestone.id} milestone={milestone} />
              ))}
            </ul>
          )}
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

function MilestoneRow({ milestone }: { milestone: Milestone }) {
  return (
    <li className="flex items-center gap-2 text-sm">
      {milestone.done ? (
        <Check className="size-4 shrink-0 text-ok" aria-hidden />
      ) : (
        <Circle className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      )}
      <span className={cn("min-w-0 flex-1 truncate", milestone.done && "text-muted-foreground line-through")}>
        <span className="sr-only">{milestone.done ? "Done: " : "Not done: "}</span>
        {milestone.title}
      </span>
      <span className="shrink-0 text-xs text-muted-foreground">Due {formatDate(milestone.due_date)}</span>
      {milestone.past_due ? (
        <Badge variant="outline" className="border-transparent bg-bad-soft text-bad">
          Past due
        </Badge>
      ) : null}
    </li>
  );
}
