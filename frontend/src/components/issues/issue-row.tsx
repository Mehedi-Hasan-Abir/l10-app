"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCheck, ListPlus } from "lucide-react";
import { AddTodoDialog } from "@/components/issues/add-todo-dialog";
import { useIssueFocus } from "@/components/issues/issue-focus";
import { PriorityPicker } from "@/components/issues/priority-picker";
import { SolveIssueDialog } from "@/components/issues/solve-issue-dialog";
import { SourceChip } from "@/components/issues/source-chip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Issue, UserRef } from "@/lib/api/types";
import { pluralize } from "@/lib/format";
import { formatDateTime } from "@/lib/time";
import { cn } from "@/lib/utils";

export interface IssueRowProps {
  teamId: string;
  issue: Issue;
  /** Writers get priority, Add To-Do and Solve; everyone else sees a read-only row. */
  canWrite: boolean;
  eligibleOwners: UserRef[];
  /** In a meeting, the top of the list is what the team is working on. */
  isUpNext?: boolean;
  onSetPriority: (issue: Issue, priority: number | null) => void;
}

export function IssueRow({ teamId, issue, canWrite, eligibleOwners, isUpNext, onSetPriority }: IssueRowProps) {
  const solved = issue.solved_at !== null;
  const [solveOpen, setSolveOpen] = useState(false);
  const [todoOpen, setTodoOpen] = useState(false);

  const { focusedIssueId } = useIssueFocus();
  const focused = focusedIssueId === issue.id;
  const rowRef = useRef<HTMLLIElement>(null);

  useEffect(() => {
    if (focused) rowRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focused]);

  return (
    <li
      ref={rowRef}
      id={`issue-${issue.id}`}
      className={cn(
        "grid scroll-mt-24 grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-2 px-4 py-3 sm:grid-cols-[auto_minmax(0,1fr)_auto]",
        focused && "animate-flash",
        isUpNext && "bg-review/5",
      )}
    >
      <PriorityPicker
        issueTitle={issue.title}
        priority={issue.priority}
        editable={canWrite && !solved}
        onChange={(priority) => onSetPriority(issue, priority)}
      />

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <p className="font-medium leading-snug text-pretty">{issue.title}</p>
          {isUpNext ? <Badge className="bg-review text-primary-foreground">Up next</Badge> : null}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-muted-foreground">
          <SourceChip source={issue.source} />
          <span>{issue.owner.name}</span>
          <span aria-hidden>·</span>
          <time dateTime={issue.created_at}>Created {formatDateTime(issue.created_at)}</time>
          {issue.todo_count > 0 ? (
            <Badge variant="secondary" className="font-normal">
              {pluralize(issue.todo_count, "To-Do")}
            </Badge>
          ) : null}
        </div>
        {solved && issue.solved_at ? (
          <div className="mt-2 rounded-md bg-muted px-2.5 py-1.5 text-sm">
            <p className="text-xs font-medium text-muted-foreground">
              Solved <time dateTime={issue.solved_at}>{formatDateTime(issue.solved_at)}</time>
            </p>
            <p className="mt-0.5 whitespace-pre-wrap text-pretty">{issue.resolution_note}</p>
          </div>
        ) : null}
      </div>

      {canWrite && !solved ? (
        <div className="col-span-full flex gap-1.5 sm:col-span-1 sm:col-start-3 sm:row-start-1">
          <Button variant="outline" size="sm" onClick={() => setTodoOpen(true)}>
            <ListPlus data-icon="inline-start" aria-hidden />
            To-Do
          </Button>
          <Button size="sm" onClick={() => setSolveOpen(true)}>
            <CheckCheck data-icon="inline-start" aria-hidden />
            Solve
          </Button>
          <SolveIssueDialog teamId={teamId} issue={issue} open={solveOpen} onOpenChange={setSolveOpen} />
          <AddTodoDialog
            teamId={teamId}
            issue={issue}
            eligibleOwners={eligibleOwners}
            open={todoOpen}
            onOpenChange={setTodoOpen}
          />
        </div>
      ) : null}
    </li>
  );
}
