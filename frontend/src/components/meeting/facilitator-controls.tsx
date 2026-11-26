"use client";

import { ArrowRight, Flag, UserRound } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { useAdvanceMeeting, useEndMeeting } from "@/lib/api/mutations";
import type { MeetingState } from "@/lib/api/types";

export interface FacilitatorControlsProps {
  meeting: MeetingState;
  isFacilitator: boolean;
}

/**
 * "Next: IDS →" and "End meeting" are the facilitator's alone (the server enforces it too).
 * Everyone else just sees who is facilitating.
 */
export function FacilitatorControls({ meeting, isFacilitator }: FacilitatorControlsProps) {
  const advance = useAdvanceMeeting();
  const end = useEndMeeting();

  if (!isFacilitator) {
    return (
      <p className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
        <UserRound className="size-4" aria-hidden />
        Facilitated by <span className="font-medium text-foreground">{meeting.facilitator.name}</span>
      </p>
    );
  }

  const index = meeting.agenda.findIndex((a) => a.key === meeting.current_section);
  const next = meeting.agenda[index + 1];
  // One meeting command at a time: a double-click can't fire two advances (the server rejects stale ones too).
  const busy = advance.isPending || end.isPending;

  return (
    <div className="flex flex-wrap items-center gap-2 sm:justify-end">
      {next ? (
        <Button
          size="lg"
          onClick={() =>
            advance.mutate({
              teamId: meeting.team_id,
              meetingId: meeting.id,
              fromSection: meeting.current_section,
            })
          }
          disabled={busy}
        >
          {advance.isPending ? <Spinner data-icon="inline-start" /> : null}
          Next: {next.title}
          <ArrowRight data-icon="inline-end" aria-hidden />
        </Button>
      ) : null}

      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button size="lg" variant={next ? "outline" : "default"} disabled={busy}>
            <Flag data-icon="inline-start" aria-hidden />
            End meeting
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>End the meeting for everyone?</AlertDialogTitle>
            <AlertDialogDescription>
              {meeting.current_section === "conclude"
                ? "Ratings lock and everyone is taken to the meeting summary."
                : "You haven't reached Conclude yet, so ratings won't be collected. Everyone is taken to the meeting summary."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep meeting open</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => end.mutate(meeting.id)}>
              End meeting
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
