"use client";

import Link from "next/link";
import { ArrowRight, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useStartAndOpenMeeting } from "@/components/meeting/use-start-and-open-meeting";
import { useActiveMeeting, useTeam } from "@/lib/api/queries";
import { deriveAffordances } from "@/lib/permissions";

/**
 * Header call to action for the team's meeting:
 * live meeting -> "Join", none and you may facilitate -> "Start meeting", otherwise a quiet note.
 */
export function MeetingPill({ teamId }: { teamId: string }) {
  const team = useTeam(teamId);
  const active = useActiveMeeting(teamId);
  const { start, isPending } = useStartAndOpenMeeting(teamId);
  const { canFacilitate } = deriveAffordances(team.data);

  if (active.isPending || team.isPending) return <Skeleton className="h-8 w-36" />;
  if (active.isError) return null;

  const meeting = active.data;
  if (meeting) {
    const section = meeting.agenda.find((a) => a.key === meeting.current_section)?.title ?? "In progress";
    return (
      <Button asChild size="lg" className="gap-2">
        <Link href={`/teams/${teamId}/meeting`}>
          <span aria-hidden className="size-2 animate-pulse rounded-full bg-ok-soft" />
          <span>
            Meeting live
            <span className="font-normal opacity-80"> · {section}</span>
          </span>
          <span className="inline-flex items-center gap-1 font-semibold">
            Join
            <ArrowRight className="size-3.5" aria-hidden />
          </span>
        </Link>
      </Button>
    );
  }

  if (canFacilitate) {
    return (
      <Button size="lg" disabled={isPending} onClick={start}>
        {isPending ? <Spinner data-icon="inline-start" /> : <Play data-icon="inline-start" aria-hidden />}
        Start meeting
      </Button>
    );
  }

  return <span className="text-xs text-muted-foreground">No meeting running</span>;
}
