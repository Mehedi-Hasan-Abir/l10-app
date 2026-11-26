"use client";

import { CalendarClock, CornerDownRight, ListChecks, TriangleAlert } from "lucide-react";
import { InlineError } from "@/components/error-state";
import { CompletionBadge } from "@/components/todos/completion-badge";
import { MakeIssueButton } from "@/components/issues/make-issue-button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { useSetTodoDone } from "@/lib/api/mutations";
import { useTodos } from "@/lib/api/queries";
import type { Todo } from "@/lib/api/types";
import { formatDateTime, formatDateWithWeekday } from "@/lib/time";
import { cn } from "@/lib/utils";

export function TodosPanel({ teamId, canWrite }: { teamId: string; canWrite: boolean }) {
  const todos = useTodos(teamId);
  const setDone = useSetTodoDone(teamId);

  return (
    <Card id="todos" className="scroll-mt-20">
      <CardHeader>
        <CardTitle>To-Dos</CardTitle>
        <CardDescription>Open first by due date, then done.</CardDescription>
        {todos.data ? (
          <div className="mt-1">
            <CompletionBadge completion={todos.data.completion} lastClosedWeek={todos.data.last_closed_week} />
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="px-0">
        {todos.isPending ? (
          <div className="flex flex-col gap-2 px-4" aria-busy="true" aria-label="Loading to-dos">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : todos.isError ? (
          <div className="px-4">
            <InlineError error={todos.error} onRetry={() => void todos.refetch()} />
          </div>
        ) : todos.data.todos.length === 0 ? (
          <Empty className="mx-4 border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <ListChecks aria-hidden />
              </EmptyMedia>
              <EmptyTitle>No To-Dos</EmptyTitle>
              <EmptyDescription>To-Dos you add while solving an issue show up here, due in 7 days.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <ul className="flex max-h-[28rem] flex-col divide-y overflow-y-auto border-t">
            {todos.data.todos.map((todo) => (
              <TodoRow
                key={todo.id}
                todo={todo}
                teamId={teamId}
                canWrite={canWrite}
                onToggle={(done) => setDone.mutate({ todoId: todo.id, done })}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

interface TodoRowProps {
  todo: Todo;
  teamId: string;
  canWrite: boolean;
  onToggle: (done: boolean) => void;
}

function TodoRow({ todo, teamId, canWrite, onToggle }: TodoRowProps) {
  const checkboxId = `todo-${todo.id}`;
  return (
    <li className="flex items-start gap-3 px-4 py-2.5">
      <Checkbox
        id={checkboxId}
        checked={todo.done}
        disabled={!canWrite}
        onCheckedChange={(checked) => onToggle(checked === true)}
        aria-label={todo.done ? `Undo: mark "${todo.title}" not done` : `Mark "${todo.title}" done`}
        className="mt-0.5"
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <label
            htmlFor={checkboxId}
            className={cn("font-medium leading-snug text-pretty", todo.done && "text-muted-foreground line-through")}
          >
            {todo.title}
          </label>
          {todo.overdue ? (
            <Badge variant="outline" className="border-transparent bg-bad-soft text-bad">
              <TriangleAlert data-icon="inline-start" aria-hidden />
              Overdue
            </Badge>
          ) : null}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs text-muted-foreground">
          <span>{todo.owner.name}</span>
          <span className="inline-flex items-center gap-1">
            <CalendarClock className="size-3" aria-hidden />
            Due {formatDateWithWeekday(todo.due_date)}
          </span>
          {todo.done ? <span>{todo.done_at ? `Done ${formatDateTime(todo.done_at)}` : "Done"}</span> : null}
        </div>
        {todo.source_issue ? (
          <p className="mt-0.5 flex items-center gap-1 truncate text-xs text-muted-foreground">
            <CornerDownRight className="size-3 shrink-0" aria-hidden />
            <span className="truncate">From issue: {todo.source_issue.title}</span>
          </p>
        ) : null}
      </div>
      {canWrite && todo.overdue ? (
        <MakeIssueButton teamId={teamId} source="todo" id={todo.id} label={todo.title} />
      ) : null}
    </li>
  );
}
