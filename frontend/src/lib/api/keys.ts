import type { IssueStatusFilter } from "./types";

/**
 * Query key factory. Every key that belongs to a team starts with `["team", teamId]`, so
 * `keys.team(teamId)` invalidates everything for that team in one call (used on WebSocket reconnect).
 */
export const keys = {
  users: () => ["auth", "users"] as const,
  me: () => ["auth", "me"] as const,

  team: (teamId: string) => ["team", teamId] as const,
  teamDetail: (teamId: string) => ["team", teamId, "detail"] as const,
  scorecard: (teamId: string) => ["team", teamId, "scorecard"] as const,
  issuesAll: (teamId: string) => ["team", teamId, "issues"] as const,
  issues: (teamId: string, status: IssueStatusFilter) => ["team", teamId, "issues", status] as const,
  todos: (teamId: string) => ["team", teamId, "todos"] as const,
  rocks: (teamId: string) => ["team", teamId, "rocks"] as const,
  audit: (teamId: string) => ["team", teamId, "audit"] as const,

  meetingsAll: (teamId: string) => ["team", teamId, "meetings"] as const,
  meetings: (teamId: string, status: "active" | "ended") => ["team", teamId, "meetings", status] as const,
  meeting: (teamId: string, meetingId: string) => ["team", teamId, "meeting", meetingId] as const,
  recap: (teamId: string, meetingId: string) => ["team", teamId, "meeting", meetingId, "recap"] as const,
  summary: (teamId: string, meetingId: string) => ["team", teamId, "meeting", meetingId, "summary"] as const,
};
