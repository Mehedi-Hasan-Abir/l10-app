import type { MeetingState, Role, Team, UserRef } from "./api/types";

/**
 * UI affordances derived from `GET /api/teams/{id}`.permissions. This only decides what to SHOW;
 * the server enforces every rule, so a hidden button is never the security boundary.
 */
export interface Affordances {
  canWrite: boolean;
  canFacilitate: boolean;
  canRate: boolean;
  /** Read-only participant: show the "View only" badge. */
  viewOnly: boolean;
}

const READ_ONLY: Affordances = { canWrite: false, canFacilitate: false, canRate: false, viewOnly: true };

export function deriveAffordances(team: Team | undefined): Affordances {
  if (!team) return READ_ONLY;
  const { can_write, can_facilitate, can_rate } = team.permissions;
  return {
    canWrite: can_write,
    canFacilitate: can_facilitate,
    canRate: can_rate,
    viewOnly: !can_write,
  };
}

export function isFacilitator(meeting: MeetingState, user: UserRef | undefined): boolean {
  return user !== undefined && meeting.facilitator.id === user.id;
}

export const ROLE_LABEL: Record<Role, string> = {
  admin: "Admin",
  team_member: "Team Member",
  observer: "Observer",
};

export const ROLE_BLURB: Record<Role, string> = {
  admin: "Everything, in every team",
  team_member: "Edits data and runs meetings",
  observer: "Read-only; can join meetings",
};
