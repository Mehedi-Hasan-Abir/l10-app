import type { QueryClient } from "@tanstack/react-query";
import { keys } from "./keys";
import type { MeetingState } from "./types";

/**
 * Write a MeetingState into every cache entry that can display it. Used by realtime messages and by
 * meeting mutations, so a facilitator's own click and a teammate's broadcast converge on one path.
 */
export function writeMeetingState(qc: QueryClient, meeting: MeetingState): void {
  const teamId = meeting.team_id;

  // A REST response and a broadcast can cross in flight; never let an older state overwrite a newer one.
  const cached = qc.getQueryData<MeetingState>(keys.meeting(teamId, meeting.id));
  if (cached && Date.parse(cached.server_now) > Date.parse(meeting.server_now)) return;

  qc.setQueryData(keys.meeting(teamId, meeting.id), meeting);

  if (meeting.status === "active") {
    qc.setQueryData<MeetingState[]>(keys.meetings(teamId, "active"), [meeting]);
    return;
  }

  // The meeting just ended: no longer "active", and its summary/recap are now final.
  qc.setQueryData<MeetingState[]>(keys.meetings(teamId, "active"), (current) =>
    (current ?? []).filter((m) => m.id !== meeting.id),
  );
  void qc.invalidateQueries({ queryKey: keys.meetings(teamId, "ended") });
  void qc.invalidateQueries({ queryKey: keys.summary(teamId, meeting.id) });
  void qc.invalidateQueries({ queryKey: keys.recap(teamId, meeting.id) });
  void qc.invalidateQueries({ queryKey: keys.teamDetail(teamId) });
}

/** The `snapshot` message: the current meeting, or none. */
export function writeMeetingSnapshot(qc: QueryClient, teamId: string, meeting: MeetingState | null): void {
  if (meeting) {
    writeMeetingState(qc, meeting);
  } else {
    qc.setQueryData<MeetingState[]>(keys.meetings(teamId, "active"), []);
  }
}
