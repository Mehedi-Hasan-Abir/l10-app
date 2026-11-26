/**
 * API contract types. Everything that crosses HTTP is an alias of the schema generated from the
 * backend's OpenAPI document (`npm run gen:api` → `schema.d.ts`), so a backend contract change shows up
 * as a type error here instead of a runtime surprise. Only the realtime messages (WebSocket frames,
 * which OpenAPI doesn't describe; see docs/PLAN.md section 7) are written by hand.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

/* Shared */
export type UserRef = Schemas["UserRef"];
export type Role = UserRef["role"];
export type TeamRef = Schemas["TeamRef"];
export type Status = Schemas["ScoreCell"]["status"];
export type ClockInfo = Schemas["ClockInfo"];
export type ErrorDetail = Schemas["ErrorDetail"];

/* Auth */
export type AuthUser = Schemas["UserWithTeams"];
export type MeResponse = Schemas["MeResponse"];
export type LoginResponse = Schemas["LoginResponse"];

/* Teams */
export type TeamPermissions = Schemas["Permissions"];
export type ActiveMeetingRef = Schemas["ActiveMeetingRef"];
export type Team = Schemas["TeamDetail"];

/* Scorecard */
export type Goal = Schemas["Goal"];
export type GoalType = Goal["type"];
export type ScoreCell = Schemas["ScoreCell"];
export type Measurable = Schemas["ScorecardMeasurable"];
export type Scorecard = Schemas["Scorecard"];
export type PutScoreBody = Schemas["ScoreUpdate"];
export type MakeIssueResponse = Schemas["MakeIssueResult"];

/* Issues */
export type Issue = Schemas["Issue"];
export type IssueSource = NonNullable<Issue["source"]>;
export type IssueStatusFilter = "unsolved" | "solved";
export type CreateIssueBody = Schemas["IssueCreate"];
export type PatchIssueBody = Schemas["IssueUpdate"];
export type SolveIssueBody = Schemas["IssueSolve"];
export type CreateTodoBody = Schemas["IssueTodoCreate"];

/* To-Dos */
export type Todo = Schemas["Todo"];
export type Completion = Schemas["Completion"];
export type TodosResponse = Schemas["TodoList"];
export type TodoUpdateBody = Schemas["TodoUpdate"];

/* Rocks */
export type Rock = Schemas["Rock"];
export type RockStatus = Rock["status"];
export type Milestone = Schemas["Milestone"];
export type RocksResponse = Schemas["RockList"];

/* Meetings */
export type MeetingState = Schemas["MeetingState"];
export type SectionKey = MeetingState["current_section"];
export type AgendaItem = Schemas["AgendaItem"];
export type Attendee = Schemas["Attendee"];
export type MeetingRating = Schemas["RatingEntry"];
export type MeetingSummary = Schemas["MeetingSummary"];
export type MeetingRecap = Schemas["MeetingRecap"];
export type AdvanceBody = Schemas["MeetingAdvance"];
export type RatingBody = Schemas["RatingUpdate"];

/* Audit trail (H3) */
export type AuditEvent = Schemas["AuditEvent"];
export type AuditAction = AuditEvent["action"];
export type AuditPage = Schemas["AuditPage"];

/* Realtime (docs/PLAN.md section 7): WebSocket frames are not part of the OpenAPI document. */
export type RealtimeResource = "scorecard" | "issues" | "todos" | "rocks" | "meetings";

export type RealtimeMessage =
  | { type: "snapshot"; meeting: MeetingState | null }
  | { type: "meeting"; meeting: MeetingState }
  | { type: "invalidate"; resources: RealtimeResource[] }
  | { type: "ping" };
