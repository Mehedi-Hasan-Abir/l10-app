"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { History, Play, Radio } from "lucide-react";
import { PageError } from "@/components/error-state";
import { IssuesPanel } from "@/components/issues/issues-panel";
import { Agenda } from "@/components/meeting/agenda";
import { AttendeeList } from "@/components/meeting/attendee-list";
import { ConcludePanel } from "@/components/meeting/conclude-panel";
import { FacilitatorControls } from "@/components/meeting/facilitator-controls";
import { MeetingSummary } from "@/components/meeting/meeting-summary";
import { SectionTimer } from "@/components/meeting/section-timer";
import { useStartAndOpenMeeting } from "@/components/meeting/use-start-and-open-meeting";
import { ScorecardTable } from "@/components/scorecard/scorecard-table";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useJoinMeeting } from "@/lib/api/mutations";
import { usePrefetchTeamData } from "@/lib/api/prefetch";
import { useActiveMeeting, useEndedMeetings, useMe, useMeeting, useTeam } from "@/lib/api/queries";
import type { MeetingState } from "@/lib/api/types";
import { deriveAffordances, isFacilitator } from "@/lib/permissions";
import { formatDateTime, formatWeekLabel } from "@/lib/time";

export interface MeetingPageProps {
  teamId: string;
  /** `?m=<id>`: show a specific meeting (used for the summary after one ends, and on refresh). */
  meetingIdParam: string | null;
}

/**
 * Decides which of three things the page is:
 *  - a live meeting (agenda, timer, the reused Scorecard / Issues sections, Conclude),
 *  - the summary of a meeting that has ended,
 *  - or "no meeting running".
 */
export function MeetingPage({ teamId, meetingIdParam }: MeetingPageProps) {
  const router = useRouter();
  const active = useActiveMeeting(teamId);
  const activeMeeting = active.data ?? null;
  // The Scorecard and Issues sections need these as soon as the meeting resolves; don't wait for it.
  usePrefetchTeamData(teamId, ["scorecard", "issues"]);

  // Remember which meeting this page is about, so when it ends we can still show ITS summary.
  const [trackedId, setTrackedId] = useState<string | null>(null);
  if (activeMeeting && activeMeeting.id !== trackedId) setTrackedId(activeMeeting.id);
  const meetingId = meetingIdParam ?? trackedId;

  // While the meeting is active the active-list cache already holds it (kept fresh by the socket);
  // only fetch by id for meetings that are not the active one (an ended meeting, a shared link).
  const isActiveOne = activeMeeting !== null && activeMeeting.id === meetingId;
  const byId = useMeeting(teamId, meetingId, !isActiveOne);

  const meeting: MeetingState | undefined = isActiveOne ? (activeMeeting ?? undefined) : byId.data;
  const receivedAt = isActiveOne ? active.dataUpdatedAt : byId.dataUpdatedAt;

  // Once a meeting we were watching ends, put its id in the URL so a refresh lands on the same summary.
  const endedId = meeting?.status === "ended" ? meeting.id : null;
  useEffect(() => {
    if (endedId && meetingIdParam !== endedId) router.replace(`/teams/${teamId}/meeting?m=${endedId}`);
  }, [endedId, meetingIdParam, router, teamId]);

  if (meetingId === null) {
    if (active.isPending) return <MeetingSkeleton />;
    if (active.isError) return <PageError error={active.error} onRetry={() => void active.refetch()} />;
    return <NoMeeting teamId={teamId} />;
  }

  if (!meeting) {
    if (byId.isError) return <PageError error={byId.error} onRetry={() => void byId.refetch()} />;
    return <MeetingSkeleton />;
  }

  if (meeting.status === "ended") return <MeetingSummary teamId={teamId} meetingId={meeting.id} />;
  return <LiveMeeting teamId={teamId} meeting={meeting} receivedAt={receivedAt} />;
}

function LiveMeeting({ teamId, meeting, receivedAt }: { teamId: string; meeting: MeetingState; receivedAt: number }) {
  const me = useMe().data?.user;
  const team = useTeam(teamId);
  const { canWrite, canRate } = deriveAffordances(team.data);

  // Opening the page joins the meeting. Idempotent on the server; once per meeting per page view here.
  const { mutate: joinMeeting } = useJoinMeeting(teamId);
  const joined = useRef<string | null>(null);
  useEffect(() => {
    if (joined.current === meeting.id) return;
    joined.current = meeting.id;
    joinMeeting(meeting.id);
  }, [meeting.id, joinMeeting]);

  const facilitating = isFacilitator(meeting, me);

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold">
          Level 10 meeting
          <span className="inline-flex items-center gap-1 rounded-full bg-ok-soft px-2 py-0.5 text-xs font-medium text-ok">
            <Radio className="size-3" aria-hidden />
            Live
          </span>
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {team.data?.name ? `${team.data.name} · ` : ""}started {formatDateTime(meeting.started_at)} · reviewing the
          week of {formatWeekLabel(meeting.scorecard_week)}
        </p>
      </div>

      <div className="grid items-start gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <aside className="flex flex-col gap-4 lg:sticky lg:top-20">
          <Agenda agenda={meeting.agenda} current={meeting.current_section} />
          <AttendeeList meeting={meeting} currentUserId={me?.id} />
        </aside>

        <div className="flex min-w-0 flex-col gap-5">
          <SectionTimer
            meeting={meeting}
            receivedAt={receivedAt}
            actions={<FacilitatorControls meeting={meeting} isFacilitator={facilitating} />}
          />

          {meeting.current_section === "scorecard" ? (
            <ScorecardTable teamId={teamId} canWrite={canWrite} context="meeting" reviewWeek={meeting.scorecard_week} />
          ) : null}
          {meeting.current_section === "ids" ? (
            <IssuesPanel
              teamId={teamId}
              canWrite={canWrite}
              eligibleOwners={team.data?.eligible_owners ?? []}
              context="meeting"
            />
          ) : null}
          {meeting.current_section === "conclude" ? (
            <ConcludePanel teamId={teamId} meeting={meeting} me={me} canRate={canRate} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function NoMeeting({ teamId }: { teamId: string }) {
  const team = useTeam(teamId);
  const { canFacilitate } = deriveAffordances(team.data);
  const { start, isPending } = useStartAndOpenMeeting(teamId);
  const ended = useEndedMeetings(teamId);
  const last = ended.data?.[0];

  return (
    <Empty className="mx-auto mt-10 max-w-xl border bg-card py-14">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Radio aria-hidden />
        </EmptyMedia>
        <EmptyTitle className="text-base">No meeting running</EmptyTitle>
        <EmptyDescription>
          {canFacilitate
            ? "Start the Level 10 meeting for your team. You become the facilitator, and teammates join from their own browsers."
            : "You can join as soon as a Team Member or Admin starts the meeting. This page updates on its own."}
        </EmptyDescription>
      </EmptyHeader>
      <EmptyContent>
        <div className="flex flex-wrap justify-center gap-2">
          {canFacilitate ? (
            <Button size="lg" onClick={start} disabled={isPending}>
              {isPending ? <Spinner data-icon="inline-start" /> : <Play data-icon="inline-start" aria-hidden />}
              Start meeting
            </Button>
          ) : null}
          {last ? (
            <Button asChild variant="outline" size="lg">
              <Link href={`/teams/${teamId}/meeting?m=${last.id}`}>
                <History data-icon="inline-start" aria-hidden />
                Last meeting summary
              </Link>
            </Button>
          ) : null}
        </div>
      </EmptyContent>
    </Empty>
  );
}

function MeetingSkeleton() {
  return (
    <div className="flex flex-col gap-5" aria-busy="true" aria-label="Loading meeting">
      <Skeleton className="h-8 w-64" />
      <div className="grid gap-5 lg:grid-cols-[17rem_1fr]">
        <div className="flex flex-col gap-4">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-48 w-full" />
        </div>
        <div className="flex flex-col gap-5">
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-80 w-full" />
        </div>
      </div>
    </div>
  );
}
