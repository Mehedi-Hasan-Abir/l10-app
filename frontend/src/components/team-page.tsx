"use client";

import { CalendarDays, Eye } from "lucide-react";
import { ActivityPanel } from "@/components/audit/activity-panel";
import { IssuesPanel } from "@/components/issues/issues-panel";
import { RocksPanel } from "@/components/rocks/rocks-panel";
import { ScorecardTable } from "@/components/scorecard/scorecard-table";
import { TodosPanel } from "@/components/todos/todos-panel";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { usePrefetchTeamData } from "@/lib/api/prefetch";
import { useTeam } from "@/lib/api/queries";
import type { ClockInfo } from "@/lib/api/types";
import { deriveAffordances } from "@/lib/permissions";
import { formatBusinessClock, formatDateWithWeekday, formatWeekLabel } from "@/lib/time";

export function TeamPage({ teamId }: { teamId: string }) {
  const team = useTeam(teamId);
  // Panels mount only once permissions are known; fetch their data alongside the team request, not after it.
  usePrefetchTeamData(teamId, ["scorecard", "issues", "todos", "rocks", "audit"]);

  if (team.isPending) return <TeamPageSkeleton />;
  if (team.isError) return null; // TeamShell renders the error state

  const { canWrite, viewOnly } = deriveAffordances(team.data);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">{team.data.name}</h1>
          {viewOnly ? (
            <Badge variant="outline" className="gap-1 text-muted-foreground">
              <Eye data-icon="inline-start" aria-hidden />
              View only
            </Badge>
          ) : null}
        </div>
        <BusinessClockStrip clock={team.data.clock} />
      </div>

      <ScorecardTable teamId={teamId} canWrite={canWrite} />

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <IssuesPanel teamId={teamId} canWrite={canWrite} eligibleOwners={team.data.eligible_owners} />
        <div className="flex flex-col gap-5">
          <TodosPanel teamId={teamId} canWrite={canWrite} />
          <RocksPanel teamId={teamId} canWrite={canWrite} />
          <ActivityPanel teamId={teamId} people={[...team.data.members, ...team.data.eligible_owners]} />
        </div>
      </div>
    </div>
  );
}

/** The business clock is fixed by the server and differs from the real date, so say what "today" is. */
function BusinessClockStrip({ clock }: { clock: ClockInfo }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <p
          tabIndex={0}
          aria-label={formatBusinessClock(clock.today, clock.current_week)}
          className="inline-flex items-center gap-2 rounded-lg border bg-card px-3 py-1.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          <CalendarDays className="size-4 text-muted-foreground" aria-hidden />
          <span className="font-medium">Today: {formatDateWithWeekday(clock.today)}</span>
          <span aria-hidden className="text-muted-foreground">
            ·
          </span>
          <span className="text-muted-foreground">Week of {formatWeekLabel(clock.current_week)}</span>
        </p>
      </TooltipTrigger>
      <TooltipContent>
        The app runs on the company calendar (Central Time), which may differ from your computer&apos;s date.
      </TooltipContent>
    </Tooltip>
  );
}

function TeamPageSkeleton() {
  return (
    <div className="flex flex-col gap-5" aria-busy="true" aria-label="Loading team">
      <div className="flex items-center justify-between">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-9 w-64" />
      </div>
      <Skeleton className="h-72 w-full" />
      <div className="grid gap-5 xl:grid-cols-[7fr_5fr]">
        <Skeleton className="h-96 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    </div>
  );
}
